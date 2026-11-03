"""工具监控、模型调用日志和场景提示词切换。"""

from __future__ import annotations

import json
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
from utils.config_handler import agent_conf
from utils.logger_handler import logger
from utils.prompt_loader import load_report_prompts, load_system_prompts

DEFAULT_MAX_TOOL_CALLS = int(agent_conf.get("max_tool_calls", 6))
# 有状态工具会修改 runtime.context，不能复用缓存结果。
CACHE_EXCLUDED_TOOLS = {"fill_context_for_report"}


def _preview(content: Any, limit: int = 120) -> str:
    text = content_to_text(content).replace("\n", " ").strip()
    return text if len(text) <= limit else f"{text[:limit]}..."


def _tool_call_key(tool_name: str, args: Any) -> str:
    """工具名 + 规范化参数，保证相同调用得到相同 key。"""
    normalized = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    return f"{tool_name}:{normalized}"


def _tool_message(request: ToolCallRequest, content: str, status: str = "success") -> ToolMessage:
    tool_call_id = str(request.tool_call.get("id") or "")
    tool_name = str(request.tool_call.get("name") or "")
    return ToolMessage(
        content=content,
        tool_call_id=tool_call_id,
        name=tool_name,
        status=status,
    )


@wrap_tool_call
def monitor_tool(
    request: ToolCallRequest,
    handler: Callable[[ToolCallRequest], ToolMessage | Command],
) -> ToolMessage | Command:
    """记录工具耗时，并执行工具预算与重复调用缓存。"""
    tool_name = request.tool_call["name"]
    context = request.runtime.context

    # 预算按“调用尝试”计数：即使命中缓存也算一次，防止模型无限重复调用。
    max_calls = int(context.get("max_tool_calls", DEFAULT_MAX_TOOL_CALLS))
    used_calls = int(context.get("tool_calls", 0))
    if used_calls >= max_calls:
        logger.warning(
            "[tool]预算超限 tool=%s used=%d max=%d",
            tool_name,
            used_calls,
            max_calls,
        )
        return _tool_message(
            request,
            f"工具调用次数已达上限（{max_calls} 次）。"
            "请停止调用工具，基于已经获得的信息直接回答；"
            "如果信息不足，请明确说明缺少什么。",
            status="error",
        )
    context["tool_calls"] = used_calls + 1

    cache = context.setdefault("tool_cache", {})
    cache_key = _tool_call_key(tool_name, request.tool_call.get("args", {}))
    if tool_name not in CACHE_EXCLUDED_TOOLS and cache_key in cache:
        logger.info("[tool]命中缓存 tool=%s calls=%d", tool_name, used_calls + 1)
        return _tool_message(request, cache[cache_key])

    started = time.perf_counter()
    logger.info("[tool]开始调用 tool=%s args=%s", tool_name, sorted(request.tool_call["args"]))

    try:
        result = handler(request)
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.info("[tool]调用完成 tool=%s elapsed_ms=%.1f", tool_name, elapsed_ms)

        if isinstance(result, ToolMessage) and tool_name not in CACHE_EXCLUDED_TOOLS:
            # 只缓存无状态工具结果；Command / 有状态工具不能复用。
            cache[cache_key] = content_to_text(result.content)

        if tool_name == "fill_context_for_report":
            # runtime.context 是当前这次请求独有的字典，不会污染其他会话。
            context["report"] = True
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
