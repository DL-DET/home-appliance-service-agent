"""基于 LangChain/LangGraph 的流式客服 Agent。"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Any, Iterator, Literal

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from agent.context import AgentContext
from agent.message_utils import content_to_text, history_to_messages
from agent.tools.agent_tools import (
    fetch_external_data,
    fill_context_for_report,
    get_current_month,
    get_order_status,
    get_user_id,
    get_user_location,
    rag_summarize,
)
from agent.tools.middleware import (
    log_before_model,
    monitor_tool,
    report_prompt_switch,
)
from model.factory import get_chat_model
from services.customer_data import get_user_profile
from utils.logger_handler import logger
from utils.prompt_loader import load_system_prompts


EventKind = Literal["token", "tool_call", "tool_result", "done"]


@dataclass(frozen=True)
class AgentEvent:
    """供 UI 消费的结构化流事件。"""

    kind: EventKind
    content: str = ""
    name: str = ""
    call_id: str = ""
    data: dict[str, Any] | None = None


class ReactAgent:
    def __init__(self, history_limit: int = 12) -> None:
        self.history_limit = history_limit
        self.agent = create_agent(
            model=get_chat_model(),
            system_prompt=load_system_prompts(),
            tools=[
                rag_summarize,
                get_order_status,
                get_user_location,
                get_user_id,
                get_current_month,
                fetch_external_data,
                fill_context_for_report,
            ],
            middleware=[monitor_tool, log_before_model, report_prompt_switch],
            context_schema=AgentContext,
            name="home_appliance_service_agent",
        )

    def _build_messages(
        self,
        query: str,
        history: list[dict[str, Any]] | None,
    ) -> list[Any]:
        messages = history_to_messages(history, limit=self.history_limit)
        messages.append({"role": "user", "content": query})
        return messages

    def stream_events(
        self,
        query: str,
        *,
        history: list[dict[str, Any]] | None = None,
        context: AgentContext | None = None,
    ) -> Iterator[AgentEvent]:
        """同时流式输出最终答案 token 和工具执行轨迹。

        `messages` 通道负责 LLM token；`updates` 通道负责模型请求的工具调用
        以及工具返回结果。二者合并后，前端不用解析 Agent 内部状态。
        """
        run_context: AgentContext = dict(context or {})
        run_context.setdefault("user_id", "")
        run_context.setdefault("city", "")
        run_context.setdefault("report_month", "")
        # 每次新问题都从普通客服场景开始，防止上一轮报告状态泄漏到这一轮。
        run_context["report"] = False

        input_dict = {"messages": self._build_messages(query, history)}
        seen_tool_calls: set[str] = set()
        answer_parts: list[str] = []
        fallback_answer = ""

        try:
            for mode, payload in self.agent.stream(
                input_dict,
                config={"recursion_limit": 10},
                stream_mode=["messages", "updates"],
                context=run_context,
            ):
                if mode == "messages":
                    chunk, _metadata = payload
                    if isinstance(chunk, AIMessageChunk):
                        text = content_to_text(chunk.content)
                        if text:
                            answer_parts.append(text)
                            yield AgentEvent(kind="token", content=text)
                    continue

                if mode != "updates" or not isinstance(payload, dict):
                    continue

                for update in payload.values():
                    if not isinstance(update, dict):
                        continue
                    for message in update.get("messages", []):
                        if isinstance(message, AIMessage) and message.tool_calls:
                            for tool_call in message.tool_calls:
                                call_id = str(tool_call.get("id") or "")
                                if call_id and call_id in seen_tool_calls:
                                    continue
                                if call_id:
                                    seen_tool_calls.add(call_id)
                                yield AgentEvent(
                                    kind="tool_call",
                                    name=str(tool_call.get("name") or ""),
                                    call_id=call_id,
                                    data={"args": tool_call.get("args", {})},
                                )

                        if isinstance(message, ToolMessage):
                            yield AgentEvent(
                                kind="tool_result",
                                name=str(message.name or ""),
                                call_id=str(message.tool_call_id or ""),
                                content=content_to_text(message.content),
                                data={"status": message.status},
                            )

                        if (
                            isinstance(message, AIMessage)
                            and not message.tool_calls
                            and content_to_text(message.content).strip()
                        ):
                            # 某些兼容模型可能不逐 token 输出，这里保留整段兜底。
                            fallback_answer = content_to_text(message.content).strip()

            if not answer_parts and fallback_answer:
                yield AgentEvent(kind="token", content=fallback_answer)

            yield AgentEvent(
                kind="done",
                data={"tool_calls": len(seen_tool_calls)},
            )
        except Exception:
            logger.exception("[agent]执行失败 query_preview=%s", query[:80])
            raise

    def execute_stream(
        self,
        query: str,
        *,
        history: list[dict[str, Any]] | None = None,
        context: AgentContext | None = None,
    ) -> Iterator[str]:
        """兼容旧调用方式，只输出最终答案文本。"""
        for event in self.stream_events(query, history=history, context=context):
            if event.kind == "token":
                yield event.content


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="命令行调试家电客服 Agent")
    parser.add_argument("query", nargs="?", default="给我生成我的使用报告")
    parser.add_argument("--user-id", default="1001")
    parser.add_argument("--month", default="2025-12")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    profile = get_user_profile(args.user_id)
    if profile is None:
        raise SystemExit(f"未找到演示用户：{args.user_id}")

    agent = ReactAgent()
    for event in agent.stream_events(
        args.query,
        context=AgentContext(
            user_id=profile.user_id,
            city=profile.city,
            report_month=args.month,
        ),
    ):
        if event.kind == "token":
            print(event.content, end="", flush=True)
        elif event.kind == "tool_call":
            print(f"\n[调用工具] {event.name} {event.data}\n", flush=True)
        elif event.kind == "tool_result":
            print(f"[工具完成] {event.name}\n", flush=True)
