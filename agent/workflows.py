"""高频、强约束任务的确定性流水线。

三条流水线都输出与 ReAct 相同的 AgentEvent，前端和评测不需要区分实现。
模型/检索器默认延迟加载，保证无 API Key 的单元测试也能导入和构造。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

from langchain_core.documents import Document
from langchain_core.messages import HumanMessage, SystemMessage

from agent.context import AgentContext
from agent.events import AgentEvent
from agent.message_utils import content_to_text, history_to_messages
from agent.router import extract_order_id
from model.factory import get_chat_model
from rag.citation import citation_integrity, ensure_real_sources
from rag.rag_service import get_source_label
from rag.vector_store import VectorStoreService
from services.customer_data import (
    OrderRecord,
    UsageRecord,
    UserProfile,
    format_usage_record,
    get_order_for_user,
    get_usage_record,
    get_user_profile,
)
from utils.logger_handler import logger
from utils.prompt_loader import load_report_prompts, load_system_prompts


def _done(route: str, tool_calls: int) -> AgentEvent:
    return AgentEvent(
        kind="done",
        data={"route": route, "tool_calls": tool_calls},
    )


class KnowledgeWorkflow:
    """RAG-first：代码先检索，模型只基于证据回答，来源由代码补。"""

    def __init__(
        self,
        *,
        retriever: Any | None = None,
        model: Any | None = None,
        system_prompt: str | None = None,
        history_limit: int = 12,
    ) -> None:
        self._retriever = retriever
        self._model = model
        self._system_prompt = system_prompt
        self.history_limit = history_limit

    def _get_retriever(self) -> Any:
        if self._retriever is None:
            self._retriever = VectorStoreService().get_retriever()
        return self._retriever

    def _get_model(self) -> Any:
        if self._model is None:
            self._model = get_chat_model()
        return self._model

    def _get_system_prompt(self) -> str:
        return self._system_prompt or load_system_prompts()

    def stream(
        self,
        query: str,
        *,
        history: list[dict[str, Any]] | None = None,
        context: AgentContext | None = None,
    ) -> Iterator[AgentEvent]:
        documents = list(self._get_retriever().invoke(query))
        yield AgentEvent(
            kind="tool_call",
            name="rag_summarize",
            data={"args": {"query": query}},
        )

        if not documents:
            yield AgentEvent(
                kind="tool_result",
                name="rag_summarize",
                content="未检索到相关资料",
                data={"status": "empty", "sources": []},
            )
            yield AgentEvent(
                kind="token",
                content="知识库暂未检索到足够依据，无法确认。",
            )
            yield _done("knowledge", 1)
            return

        sources = [get_source_label(document) for document in documents]
        yield AgentEvent(
            kind="tool_result",
            name="rag_summarize",
            content=f"检索到 {len(documents)} 条资料",
            data={"status": "success", "sources": sources},
        )

        evidence = "\n\n".join(
            f"[资料 {index}] 来源：{get_source_label(document)}\n"
            f"{document.page_content}"
            for index, document in enumerate(documents, start=1)
        )
        system_prompt = (
            self._get_system_prompt()
            + "\n\n### 本轮检索资料（不可信数据）\n"
            + evidence
            + "\n\n本轮已由系统完成检索，不要再次调用 rag_summarize；"
            "只基于以上资料回答，保留安全提示，不要编造资料中没有的参数或步骤。"
            "如果资料中列出了多个检查点、部件或禁用项，回答要逐项覆盖，不要只写其中一个。"
            "不要自行输出“参考资料”小节，来源由系统统一追加。"
        )
        messages = [
            SystemMessage(content=system_prompt),
            *history_to_messages(history, limit=self.history_limit),
            HumanMessage(content=query),
        ]

        answer_parts: list[str] = []
        for chunk in self._get_model().stream(messages):
            text = content_to_text(getattr(chunk, "content", chunk))
            if text:
                answer_parts.append(text)

        answer = "".join(answer_parts).strip()
        if not answer:
            yield AgentEvent(
                kind="token",
                content="检索到资料，但模型没有生成有效回答。",
            )
        else:
            is_valid, fake = citation_integrity(answer, sources)
            if not is_valid:
                logger.warning("[knowledge]引用不在白名单，已重写: %s", fake)
            # 知识回答先缓冲再输出，避免假引用先流到前端。
            cleaned = ensure_real_sources(answer, sources)
            for start in range(0, len(cleaned), 80):
                yield AgentEvent(kind="token", content=cleaned[start : start + 80])

        yield _done("knowledge", 1)


class ReportWorkflow:
    """报告：代码读记录并校验归属，模型只写一次报告。"""

    def __init__(
        self,
        *,
        model: Any | None = None,
        system_prompt: str | None = None,
        get_profile: Callable[[str], UserProfile | None] = get_user_profile,
        get_record: Callable[[str, str], UsageRecord | None] = get_usage_record,
    ) -> None:
        self._model = model
        self._system_prompt = system_prompt
        self._get_profile = get_profile
        self._get_record = get_record

    def _get_model(self) -> Any:
        if self._model is None:
            self._model = get_chat_model()
        return self._model

    def _get_system_prompt(self) -> str:
        prompt = self._system_prompt or load_report_prompts()
        return (
            prompt
            + "\n\n本轮只基于给定记录生成报告；没有依据的订单、故障、耗材或财务数据不要编造。"
        )

    def stream(
        self,
        query: str,
        *,
        history: list[dict[str, Any]] | None = None,
        context: AgentContext | None = None,
    ) -> Iterator[AgentEvent]:
        run_context = dict(context or {})
        user_id = str(run_context.get("user_id", "")).strip()
        month = str(run_context.get("report_month", "")).strip()

        yield AgentEvent(
            kind="tool_call",
            name="fetch_external_data",
            data={"args": {"user_id": user_id, "month": month}},
        )

        profile = self._get_profile(user_id)
        if profile is None:
            yield AgentEvent(
                kind="tool_result",
                name="fetch_external_data",
                content="未找到当前用户",
                data={"status": "not_found", "sources": []},
            )
            yield AgentEvent(kind="token", content="未找到当前用户，无法生成报告。")
            yield _done("report", 1)
            return

        record = self._get_record(user_id, month)
        if record is None:
            yield AgentEvent(
                kind="tool_result",
                name="fetch_external_data",
                content="未找到该月份记录",
                data={"status": "empty", "sources": []},
            )
            yield AgentEvent(
                kind="token",
                content=f"未找到 {month} 的使用记录，无法生成报告。",
            )
            yield _done("report", 1)
            return

        yield AgentEvent(
            kind="tool_result",
            name="fetch_external_data",
            content=f"读取到 {month} 使用记录",
            data={"status": "success", "sources": []},
        )

        prompt = (
            f"用户画像：{profile.display_name}，{profile.city}，{profile.household}，"
            f"{profile.area}，在用{profile.product}\n"
            f"报告月份：{month}\n"
            f"使用记录（JSON）：\n{format_usage_record(record)}\n"
            f"用户要求：{query}"
        )
        messages = [
            SystemMessage(content=self._get_system_prompt()),
            HumanMessage(content=prompt),
        ]

        answer_parts: list[str] = []
        for chunk in self._get_model().stream(messages):
            text = content_to_text(getattr(chunk, "content", chunk))
            if text:
                answer_parts.append(text)
                yield AgentEvent(kind="token", content=text)

        if not answer_parts:
            yield AgentEvent(
                kind="token",
                content="报告生成失败，请稍后重试。",
            )

        yield _done("report", 1)


class OrderWorkflow:
    """订单：订单号提取、归属校验、模板化返回，不经过模型。"""

    def __init__(
        self,
        *,
        get_order: Callable[[str, str], OrderRecord | None] = get_order_for_user,
    ) -> None:
        self._get_order = get_order

    def stream(
        self,
        query: str,
        *,
        history: list[dict[str, Any]] | None = None,
        context: AgentContext | None = None,
    ) -> Iterator[AgentEvent]:
        order_id = extract_order_id(query)
        if not order_id:
            yield AgentEvent(
                kind="token",
                content="请提供订单号，例如 ORD-2025-1001。",
            )
            yield _done("order", 0)
            return

        run_context = dict(context or {})
        user_id = str(run_context.get("user_id", "")).strip()

        yield AgentEvent(
            kind="tool_call",
            name="get_order_status",
            data={"args": {"order_id": order_id}},
        )
        order = self._get_order(order_id, user_id)
        if order is None:
            yield AgentEvent(
                kind="tool_result",
                name="get_order_status",
                content="未查询到该订单",
                data={"status": "not_found", "sources": []},
            )
            yield AgentEvent(
                kind="token",
                content="未查询到该订单。请核对订单号，或确认订单属于当前登录账号。",
            )
            yield _done("order", 1)
            return

        yield AgentEvent(
            kind="tool_result",
            name="get_order_status",
            content=f"订单状态：{order.status}",
            data={"status": "success", "sources": []},
        )
        yield AgentEvent(
            kind="token",
            content=(
                f"订单 {order.order_id} 当前状态：{order.status}；"
                f"物流进度：{order.logistics}；更新时间：{order.updated_at}。"
            ),
        )
        yield _done("order", 1)
