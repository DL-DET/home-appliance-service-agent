"""工具监控、模型调用日志和场景提示词切换。"""

from __future__ import annotations

import json
import time
from contextlib import nullcontext
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
from agent.tools.resilience import (
    CIRCUIT_EXCLUDED_TOOLS,
    DEFAULT_BACKOFF_MS,
    DEFAULT_MAX_RETRIES,
    DEFAULT_TOOL_TIMEOUT_SECONDS,
    RETRYABLE_TOOLS,
    TIMEOUT_EXCLUDED_TOOLS,
    ToolBudgetExceeded,
    call_with_retry,
    circuit_registry,
    is_transient_error,
    run_with_timeout,
)
from utils.config_handler import agent_conf
from utils.logger_handler import logger
from utils.prompt_loader import load_report_prompts, load_system_prompts
from utils.tracing import safe_preview

DEFAULT_MAX_TOOL_CALLS = int(agent_conf.get("max_tool_calls", 6))
# 有状态工具会修改 runtime.context，不能复用缓存结果。
CACHE_EXCLUDED_TOOLS = {"fill_context_for_report"}


def _preview(content: Any, limit: int = 120) -> str:
    return safe_preview(content_to_text(content), limit)


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


def _consume_budget(context: dict, request_id: str, tool_name: str) -> None:
    """每次尝试都计数；重试和缓存命中也算一次。"""
    max_calls = int(context.get("max_tool_calls", DEFAULT_MAX_TOOL_CALLS))
    used_calls = int(context.get("tool_calls", 0))
    if used_calls >= max_calls:
        logger.warning(
            "[tool]预算超限 request_id=%s tool=%s used=%d max=%d",
            request_id,
            tool_name,
            used_calls,
            max_calls,
        )
        raise ToolBudgetExceeded(max_calls)

    context["tool_calls"] = used_calls + 1
    trace = context.get("trace")
    if trace is not None:
        trace.increment("tool_calls")


def _budget_error_message(request: ToolCallRequest, exc: ToolBudgetExceeded) -> ToolMessage:
    return _tool_message(
        request,
        f"{exc}。请停止调用工具，基于已经获得的信息直接回答；"
        "如果信息不足，请明确说明缺少什么。",
        status="error",
    )


def _unavailable_message(request: ToolCallRequest) -> ToolMessage:
    return _tool_message(
        request,
        "该功能暂时不可用，请稍后重试。不要重复调用工具；"
        "基于已有信息回答，信息不足就明确说明。",
        status="error",
    )


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


@wrap_tool_call
def monitor_tool(
    request: ToolCallRequest,
    handler: Callable[[ToolCallRequest], ToolMessage | Command],
) -> ToolMessage | Command:
    """记录工具耗时，并执行预算、缓存、重试、超时和熔断。"""
    tool_name = request.tool_call["name"]
    context = request.runtime.context
    request_id = str(context.get("request_id", ""))

    cache = context.setdefault("tool_cache", {})
    cache_key = _tool_call_key(tool_name, request.tool_call.get("args", {}))
    if tool_name not in CACHE_EXCLUDED_TOOLS and cache_key in cache:
        try:
            _consume_budget(context, request_id, tool_name)
        except ToolBudgetExceeded as exc:
            return _budget_error_message(request, exc)
        logger.info(
            "[tool]命中缓存 request_id=%s tool=%s calls=%d cache_hit=true",
            request_id,
            tool_name,
            int(context.get("tool_calls", 0)),
        )
        return _tool_message(request, cache[cache_key])

    circuit = (
        None
        if tool_name in CIRCUIT_EXCLUDED_TOOLS
        else circuit_registry.get(tool_name)
    )
    if circuit is not None and not circuit.allow():
        try:
            _consume_budget(context, request_id, tool_name)
        except ToolBudgetExceeded as exc:
            return _budget_error_message(request, exc)
        logger.warning(
            "[tool]熔断打开 request_id=%s tool=%s state=%s",
            request_id,
            tool_name,
            circuit.state,
        )
        return _unavailable_message(request)

    started = time.perf_counter()
    logger.info(
        "[tool]开始调用 request_id=%s tool=%s args=%s",
        request_id,
        tool_name,
        sorted(request.tool_call["args"]),
    )

    trace = context.get("trace")
    span_context = (
        trace.span(f"tool.{tool_name}", tool=tool_name)
        if trace is not None
        else nullcontext()
    )
    max_retries = DEFAULT_MAX_RETRIES if tool_name in RETRYABLE_TOOLS else 0

    with span_context as span:
        try:
            def invoke() -> ToolMessage | Command:
                if tool_name in TIMEOUT_EXCLUDED_TOOLS:
                    return handler(request)
                return run_with_timeout(
                    lambda: handler(request),
                    DEFAULT_TOOL_TIMEOUT_SECONDS,
                )

            def before_attempt(_attempt: int) -> None:
                _consume_budget(context, request_id, tool_name)

            def on_retry(attempt: int, exc: BaseException) -> None:
                logger.warning(
                    "[tool]重试 request_id=%s tool=%s attempt=%d error_type=%s",
                    request_id,
                    tool_name,
                    attempt,
                    type(exc).__name__,
                )

            result, retry_count = call_with_retry(
                invoke,
                max_retries=max_retries,
                backoff_ms=DEFAULT_BACKOFF_MS,
                before_attempt=before_attempt,
                on_retry=on_retry,
            )
            elapsed_ms = _elapsed_ms(started)
            if span is not None:
                span.attributes["cache_hit"] = False
                span.attributes["retry_count"] = retry_count
                span.attributes["circuit_state"] = (
                    circuit.state if circuit is not None else "disabled"
                )
                if isinstance(result, ToolMessage) and result.status == "error":
                    span.status = "error"
            if circuit is not None:
                circuit.record_success()

            logger.info(
                "[tool]调用完成 request_id=%s tool=%s elapsed_ms=%.1f retry=%d",
                request_id,
                tool_name,
                elapsed_ms,
                retry_count,
            )

            if isinstance(result, ToolMessage) and tool_name not in CACHE_EXCLUDED_TOOLS:
                # 只缓存无状态工具结果；Command / 有状态工具不能复用。
                cache[cache_key] = content_to_text(result.content)

            if tool_name == "fill_context_for_report":
                # runtime.context 是当前这次请求独有的字典，不会污染其他会话。
                context["report"] = True
            return result
        except ToolBudgetExceeded as exc:
            elapsed_ms = _elapsed_ms(started)
            logger.warning(
                "[tool]预算耗尽 request_id=%s tool=%s elapsed_ms=%.1f",
                request_id,
                tool_name,
                elapsed_ms,
            )
            if span is not None:
                span.status = "error"
                span.attributes["error_type"] = "ToolBudgetExceeded"
                span.attributes["retry_count"] = 0
            if circuit is not None and circuit.state == "half_open":
                # 预算在试探前耗尽，试探没有真正发生；重新打开，避免卡在半开。
                circuit.record_failure()
            return _budget_error_message(request, exc)
        except Exception as exc:
            elapsed_ms = _elapsed_ms(started)
            if circuit is not None and (
                is_transient_error(exc) or circuit.state == "half_open"
            ):
                # 半开状态下任何失败都算试探失败，避免 half_open 永久卡住。
                circuit.record_failure()
            logger.exception(
                "[tool]调用失败 request_id=%s tool=%s elapsed_ms=%.1f "
                "error_type=%s circuit_state=%s",
                request_id,
                tool_name,
                elapsed_ms,
                type(exc).__name__,
                circuit.state if circuit is not None else "disabled",
            )
            if is_transient_error(exc):
                if span is not None:
                    span.status = "error"
                    span.attributes["error_type"] = type(exc).__name__
                    span.attributes["retry_count"] = max_retries
                    span.attributes["circuit_state"] = (
                        circuit.state if circuit is not None else "disabled"
                    )
                return _unavailable_message(request)
            raise


@before_model
def log_before_model(state: AgentState, runtime: Runtime) -> None:
    context = runtime.context or {}
    request_id = str(context.get("request_id", ""))
    trace = context.get("trace")
    if trace is not None:
        trace.increment("model_calls")

    logger.info(
        "[model]即将调用模型 request_id=%s 消息数=%d",
        request_id,
        len(state["messages"]),
    )
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
