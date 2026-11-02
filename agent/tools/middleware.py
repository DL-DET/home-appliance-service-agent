"""工具监控、模型调用日志和场景提示词切换。"""

from __future__ import annotations

import time
from typing import Any, Callable

from langchain.agents import AgentState
from langchain.agents.middleware import (
    ModelRequest,
    before_model,
    dynamic_prompt,
    wrap_tool_call,
)
from langchain.tools.tool_node import ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.runtime import Runtime
from langgraph.types import Command

from agent.message_utils import content_to_text
from utils.logger_handler import logger
from utils.prompt_loader import load_report_prompts, load_system_prompts


def _preview(content: Any, limit: int = 120) -> str:
    text = content_to_text(content).replace("\n", " ").strip()
    return text if len(text) <= limit else f"{text[:limit]}..."


@wrap_tool_call
def monitor_tool(
    request: ToolCallRequest,
    handler: Callable[[ToolCallRequest], ToolMessage | Command],
) -> ToolMessage | Command:
    """记录工具耗时和结果规模，不在日志里打印完整业务数据。"""
    tool_name = request.tool_call["name"]
    started = time.perf_counter()
    logger.info("[tool]开始调用 tool=%s args=%s", tool_name, sorted(request.tool_call["args"]))

    try:
        result = handler(request)
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.info("[tool]调用完成 tool=%s elapsed_ms=%.1f", tool_name, elapsed_ms)

        if tool_name == "fill_context_for_report":
            # runtime.context 是当前这次请求独有的字典，不会污染其他会话。
            request.runtime.context["report"] = True
        return result
    except Exception:
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.exception("[tool]调用失败 tool=%s elapsed_ms=%.1f", tool_name, elapsed_ms)
        raise


@before_model
def log_before_model(state: AgentState, runtime: Runtime) -> None:
    logger.info("[model]即将调用模型，消息数=%d", len(state["messages"]))
    if state["messages"]:
        latest = state["messages"][-1]
        logger.debug(
            "[model]最新消息类型=%s 内容预览=%s",
            type(latest).__name__,
            _preview(latest.content),
        )
    return None


@dynamic_prompt
def report_prompt_switch(request: ModelRequest) -> str:
    """报告场景使用专用提示词，其余场景使用客服提示词。"""
    if request.runtime.context.get("report", False):
        return load_report_prompts()
    return load_system_prompts()
