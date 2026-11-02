"""客服 Agent 可调用的业务工具。"""

from __future__ import annotations

from functools import lru_cache

from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime

from agent.context import AgentContext
from rag.rag_service import RagSummarizeService
from services.customer_data import (
    format_usage_record,
    get_order_for_user,
    get_usage_record,
    get_user_profile,
)
from utils.logger_handler import logger


@lru_cache(maxsize=1)
def _get_rag_service() -> RagSummarizeService:
    """工具被真正调用时才加载向量库，避免模块导入依赖 API Key 和本地索引。"""
    return RagSummarizeService()


@tool(description="从家电知识库检索参考资料，返回总结和可追溯来源")
def rag_summarize(query: str) -> str:
    return _get_rag_service().rag_summarize(query)


@tool(description="根据当前登录用户查询订单状态；只能访问自己的订单")
def get_order_status(
    order_id: str,
    runtime: ToolRuntime[AgentContext],
) -> str:
    user_id = str(runtime.context.get("user_id", "")).strip()
    order = get_order_for_user(order_id, user_id)
    if order is None:
        logger.warning("[order]订单不存在或不属于当前用户")
        return "未查询到该订单。请核对订单号，或确认订单属于当前登录账号。"
    return (
        f"订单 {order.order_id} 当前状态：{order.status}；"
        f"物流进度：{order.logistics}；更新时间：{order.updated_at}。"
    )


@tool(description="获取当前登录用户的演示用户ID，以纯字符串形式返回")
def get_user_id(runtime: ToolRuntime[AgentContext]) -> str:
    return str(runtime.context.get("user_id", "")).strip()


@tool(description="获取当前登录用户所在城市，以纯字符串形式返回")
def get_user_location(runtime: ToolRuntime[AgentContext]) -> str:
    return str(runtime.context.get("city", "")).strip()


@tool(description="获取本次报告使用的月份，格式为YYYY-MM")
def get_current_month(runtime: ToolRuntime[AgentContext]) -> str:
    return str(runtime.context.get("report_month", "")).strip()


@tool(description="查询当前用户指定月份的使用记录，返回结构化JSON字符串")
def fetch_external_data(
    user_id: str,
    month: str,
    runtime: ToolRuntime[AgentContext],
) -> str:
    current_user_id = str(runtime.context.get("user_id", "")).strip()
    # 这里再次做归属校验，防止模型被提示词注入后尝试读取其他用户的数据。
    if user_id != current_user_id:
        logger.warning("[external_data]拒绝跨用户数据访问")
        return "无权查询其他用户的使用记录。"

    record = get_usage_record(user_id, month)
    if record is None:
        logger.warning("[external_data]未找到用户=%s 月份=%s 的记录", user_id, month)
        return ""
    return format_usage_record(record)


@tool(
    description=(
        "无入参。调用后由中间件把当前请求切换为报告生成提示词；"
        "仅在用户明确要求生成个人使用报告时调用"
    )
)
def fill_context_for_report() -> str:
    return "报告场景上下文已准备"


def describe_current_user(user_id: str) -> str:
    """供命令行演示和调试使用，不暴露给模型。"""
    profile = get_user_profile(user_id)
    if profile is None:
        return f"未找到用户 {user_id}"
    return (
        f"{profile.display_name}，{profile.city}，{profile.household}，"
        f"{profile.area}，在用{profile.product}"
    )
