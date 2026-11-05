"""轻量 trace 的单元测试：不依赖模型、不写真实日志文件。"""

import json
import logging
import uuid

import pytest

from utils.tracing import Trace, new_request_id, preview, redact, safe_preview


class ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def make_trace(request_id: str = "req_test") -> tuple[Trace, ListHandler]:
    logger = logging.getLogger(f"test.trace.{uuid.uuid4().hex}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = ListHandler()
    logger.addHandler(handler)
    return Trace(request_id=request_id, logger=logger), handler


def test_new_request_id_is_unique_and_readable():
    first = new_request_id()
    second = new_request_id()

    assert first.startswith("req_")
    assert first != second


def test_span_nesting_and_request_summary():
    trace, handler = make_trace("req_demo")

    with trace.span("request"):
        with trace.span("knowledge.retrieve", docs_count=2):
            pass

    summary = trace.finish(route="knowledge", tool_calls=1, model_calls=1)

    assert summary["request_id"] == "req_demo"
    assert summary["route"] == "knowledge"
    assert summary["status"] == "ok"
    assert summary["span_count"] == 2
    assert summary["tool_calls"] == 1
    assert summary["model_calls"] == 1

    spans = {
        event["name"]: event
        for event in trace.events
        if event["event"] == "span"
    }
    assert set(spans) == {"request", "knowledge.retrieve"}
    assert spans["knowledge.retrieve"]["parent_id"] == spans["request"]["span_id"]
    assert spans["knowledge.retrieve"]["docs_count"] == 2

    # 写入日志的是合法 JSON，方便后续按 request_id 过滤。
    assert json.loads(handler.messages[-1])["event"] == "request"


def test_span_records_exception_and_marks_request_failed():
    trace, _ = make_trace("req_error")

    with pytest.raises(ValueError):
        with trace.span("model.call"):
            raise ValueError("boom")

    summary = trace.finish(route="knowledge")

    assert trace.status == "error"
    assert trace.error_type == "ValueError"
    assert summary["status"] == "error"
    assert summary["error_type"] == "ValueError"

    span = next(event for event in trace.events if event["event"] == "span")
    assert span["status"] == "error"
    assert span["error_type"] == "ValueError"


def test_span_can_mark_empty_result_without_exception():
    trace, _ = make_trace("req_empty")

    with trace.span("knowledge.retrieve") as span:
        span.status = "empty"
        span.attributes["docs_count"] = 0

    summary = trace.finish(route="knowledge")

    assert summary["status"] == "ok"
    span = next(event for event in trace.events if event["event"] == "span")
    assert span["status"] == "empty"
    assert span["docs_count"] == 0


def test_increment_counts_tool_and_model_calls():
    trace, _ = make_trace("req_count")

    trace.increment("tool_calls")
    trace.increment("tool_calls")
    trace.increment("model_calls")

    summary = trace.finish(route="general")

    assert summary["tool_calls"] == 2
    assert summary["model_calls"] == 1


def test_redact_hides_sensitive_values():
    assert redact("") == ""
    assert redact("demo1001@example.com") == "d***@example.com"
    assert redact("ORD-2025-1001") == "***1001"
    assert redact("1001") == "***"


def test_preview_compacts_and_truncates():
    assert preview("a\n b   c") == "a b c"
    assert preview("x" * 20, limit=10) == "xxxxxxxxxx..."


def test_safe_preview_redacts_order_id_and_email():
    text = "订单 ORD-2025-1001，邮箱 demo1001@example.com"

    assert safe_preview(text) == "订单 ORD-****，邮箱 ***@***"
