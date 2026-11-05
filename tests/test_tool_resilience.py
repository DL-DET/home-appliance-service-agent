"""工具韧性测试：假异常、假时钟、假 sleep，不访问网络。"""

import logging
import time
import uuid

import pytest
from langchain.tools.tool_node import ToolCallRequest
from langchain_core.messages import ToolMessage

from agent.tools import middleware
from agent.tools.resilience import (
    CircuitBreaker,
    CircuitBreakerRegistry,
    call_with_retry,
    is_transient_error,
    run_with_timeout,
)
from utils.tracing import Trace


class ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


@pytest.fixture(autouse=True)
def isolate_circuit(monkeypatch):
    registry = CircuitBreakerRegistry(
        threshold=1,
        cooldown_seconds=30,
        clock=time.monotonic,
    )
    monkeypatch.setattr(middleware, "circuit_registry", registry)
    monkeypatch.setattr(middleware, "DEFAULT_BACKOFF_MS", 0)
    return registry


def make_request(
    context: dict,
    *,
    name: str = "get_order_status",
    args: dict | None = None,
    call_id: str = "call-1",
) -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": name, "args": args or {}, "id": call_id},
        tool=None,
        state=None,
        runtime=SimpleNamespace(context=context),
    )


def run_tool(request: ToolCallRequest, handler):
    return middleware.monitor_tool.wrap_tool_call(request, handler)


class SimpleNamespace:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def test_transient_error_classification():
    class ReadTimeout(Exception):
        pass

    assert is_transient_error(TimeoutError("x"))
    assert is_transient_error(ConnectionError("x"))
    assert is_transient_error(ReadTimeout("x"))
    assert not is_transient_error(ValueError("x"))
    assert not is_transient_error(KeyError("x"))


def test_call_with_retry_retries_transient_error_then_succeeds():
    calls: list[int] = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise TimeoutError("fake timeout")
        return "ok"

    result, retries = call_with_retry(
        flaky,
        max_retries=2,
        backoff_ms=0,
        sleep=lambda _: None,
    )

    assert result == "ok"
    assert retries == 2
    assert len(calls) == 3


def test_call_with_retry_stops_after_max_retries():
    calls: list[int] = []

    def always_timeout():
        calls.append(1)
        raise TimeoutError("fake timeout")

    with pytest.raises(TimeoutError):
        call_with_retry(
            always_timeout,
            max_retries=2,
            backoff_ms=0,
            sleep=lambda _: None,
        )

    assert len(calls) == 3


def test_call_with_retry_does_not_retry_non_transient_error():
    calls: list[int] = []

    def broken():
        calls.append(1)
        raise ValueError("bad args")

    with pytest.raises(ValueError):
        call_with_retry(
            broken,
            max_retries=2,
            backoff_ms=0,
            sleep=lambda _: None,
        )

    assert len(calls) == 1


def test_circuit_breaker_opens_and_recovers_after_cooldown():
    now = [0.0]
    breaker = CircuitBreaker(
        "demo",
        threshold=2,
        cooldown_seconds=10,
        clock=lambda: now[0],
    )

    assert breaker.allow()
    breaker.record_failure()
    assert breaker.allow()
    breaker.record_failure()

    assert not breaker.allow()
    assert breaker.state == "open"

    now[0] = 10
    assert breaker.allow()
    assert breaker.state == "half_open"

    breaker.record_success()
    assert breaker.state == "closed"
    assert breaker.allow()


def test_circuit_breaker_half_open_failure_reopens():
    now = [0.0]
    breaker = CircuitBreaker(
        "demo",
        threshold=1,
        cooldown_seconds=10,
        clock=lambda: now[0],
    )

    breaker.record_failure()
    assert not breaker.allow()

    now[0] = 10
    assert breaker.allow()
    breaker.record_failure()

    assert not breaker.allow()
    assert breaker.state == "open"


def test_run_with_timeout_raises_timeout_error():
    def slow():
        time.sleep(0.1)
        return "late"

    with pytest.raises(TimeoutError):
        run_with_timeout(slow, timeout_seconds=0.01)


def test_transient_tool_failure_returns_error_message_after_retries():
    context = {"max_tool_calls": 10}
    calls: list[int] = []

    def handler(_request):
        calls.append(1)
        raise TimeoutError("fake timeout")

    result = run_tool(make_request(context), handler)

    assert result.status == "error"
    assert "暂时不可用" in str(result.content)
    assert len(calls) == 3
    assert context["tool_calls"] == 3


def test_stateful_tool_is_not_retried():
    context = {"max_tool_calls": 10}
    calls: list[int] = []

    def handler(_request):
        calls.append(1)
        raise TimeoutError("fake timeout")

    result = run_tool(
        make_request(context, name="fill_context_for_report"),
        handler,
    )

    assert result.status == "error"
    assert len(calls) == 1
    assert context["tool_calls"] == 1


def test_not_found_result_is_not_retried():
    context = {"max_tool_calls": 10}
    calls: list[int] = []

    def handler(request):
        calls.append(1)
        return ToolMessage(
            content="未查询到该订单",
            tool_call_id=str(request.tool_call["id"]),
            name=str(request.tool_call["name"]),
        )

    result = run_tool(make_request(context), handler)

    assert result.content == "未查询到该订单"
    assert len(calls) == 1


def test_open_circuit_skips_handler_and_returns_error_message():
    context = {"max_tool_calls": 20}
    calls: list[int] = []

    def handler(_request):
        calls.append(1)
        raise TimeoutError("fake timeout")

    first = run_tool(make_request(context, call_id="c1"), handler)
    assert first.status == "error"
    assert len(calls) == 3

    second = run_tool(make_request(context, call_id="c2"), handler)
    assert second.status == "error"
    assert "暂时不可用" in str(second.content)
    assert len(calls) == 3


def test_retry_attempts_count_toward_tool_budget():
    context = {"max_tool_calls": 1}
    calls: list[int] = []

    def handler(_request):
        calls.append(1)
        raise TimeoutError("fake timeout")

    result = run_tool(make_request(context), handler)

    assert result.status == "error"
    assert "上限" in str(result.content)
    assert len(calls) == 1
    assert context["tool_calls"] == 1


def test_trace_records_retry_count_and_circuit_state():
    logger = logging.getLogger(f"test.resilience.{uuid.uuid4().hex}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(ListHandler())
    trace = Trace(logger=logger)
    context = {"max_tool_calls": 10, "trace": trace}
    calls: list[int] = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            raise TimeoutError("fake timeout")
        return ToolMessage(
            content="ok",
            tool_call_id=str(request.tool_call["id"]),
            name=str(request.tool_call["name"]),
        )

    result = run_tool(make_request(context), handler)

    assert result.status == "success"
    spans = [
        event
        for event in trace.events
        if event["event"] == "span" and event["name"] == "tool.get_order_status"
    ]
    assert spans[-1]["retry_count"] == 1
    assert spans[-1]["circuit_state"] == "closed"


def test_half_open_probe_non_transient_failure_reopens(monkeypatch):
    now = [0.0]
    registry = CircuitBreakerRegistry(
        threshold=1,
        cooldown_seconds=10,
        clock=lambda: now[0],
    )
    monkeypatch.setattr(middleware, "circuit_registry", registry)
    context = {"max_tool_calls": 10}

    def timeout_handler(_request):
        raise TimeoutError("fake timeout")

    run_tool(
        make_request(context, name="custom_tool", call_id="c1"),
        timeout_handler,
    )
    assert registry.get("custom_tool").state == "open"

    now[0] = 10

    def bad_handler(_request):
        raise ValueError("bad args")

    with pytest.raises(ValueError):
        run_tool(
            make_request(context, name="custom_tool", call_id="c2"),
            bad_handler,
        )

    # 试探失败后必须重新打开，不能永久停在 half_open。
    assert registry.get("custom_tool").state == "open"
