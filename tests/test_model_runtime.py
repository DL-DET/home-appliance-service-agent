"""模型运行时测试：超时、首 token 超时和 token 用量，不调用真实模型。"""

import logging
import uuid
from types import SimpleNamespace

import pytest

from agent.model_runtime import (
    ModelTimeoutError,
    extract_token_usage,
    record_usage,
    stream_with_deadline,
)
from utils.tracing import Trace


class ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def make_trace() -> Trace:
    logger = logging.getLogger(f"test.model.{uuid.uuid4().hex}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(ListHandler())
    return Trace(logger=logger)


def test_extract_usage_from_usage_metadata():
    chunk = SimpleNamespace(
        usage_metadata={
            "input_tokens": 10,
            "output_tokens": 5,
            "total_tokens": 15,
        }
    )

    assert extract_token_usage(chunk) == {
        "input_tokens": 10,
        "output_tokens": 5,
        "total_tokens": 15,
    }


def test_extract_usage_from_response_metadata():
    chunk = SimpleNamespace(
        response_metadata={
            "token_usage": {
                "input_tokens": 7,
                "output_tokens": 3,
                "total_tokens": 10,
            }
        }
    )

    assert extract_token_usage(chunk) == {
        "input_tokens": 7,
        "output_tokens": 3,
        "total_tokens": 10,
    }


def test_extract_usage_supports_prompt_and_completion_keys():
    chunk = SimpleNamespace(
        response_metadata={
            "usage": {
                "prompt_tokens": 4,
                "completion_tokens": 2,
                "total_tokens": 6,
            }
        }
    )

    assert extract_token_usage(chunk) == {
        "input_tokens": 4,
        "output_tokens": 2,
        "total_tokens": 6,
    }


def test_extract_usage_returns_none_without_metadata():
    assert extract_token_usage(SimpleNamespace()) is None


def test_record_usage_updates_trace_attributes():
    trace = make_trace()
    chunk = SimpleNamespace(
        usage_metadata={
            "input_tokens": 11,
            "output_tokens": 6,
            "total_tokens": 17,
        }
    )

    record_usage({"trace": trace}, chunk)

    assert trace.attributes["input_tokens"] == 11
    assert trace.attributes["output_tokens"] == 6
    assert trace.attributes["total_tokens"] == 17


def test_stream_with_deadline_yields_within_budget():
    times = [0.0, 1.0, 2.0]

    def clock():
        return times.pop(0)

    def stream():
        yield "a"
        yield "b"

    assert list(
        stream_with_deadline(
            stream(),
            total_timeout_seconds=60,
            first_token_timeout_seconds=30,
            clock=clock,
        )
    ) == ["a", "b"]


def test_stream_with_deadline_raises_on_first_token_timeout():
    times = [0.0, 31.0]

    def clock():
        return times.pop(0)

    def stream():
        yield "late"

    with pytest.raises(ModelTimeoutError, match="首 token"):
        list(
            stream_with_deadline(
                stream(),
                total_timeout_seconds=60,
                first_token_timeout_seconds=30,
                clock=clock,
            )
        )


def test_stream_with_deadline_raises_on_total_timeout():
    times = [0.0, 1.0, 61.0]

    def clock():
        return times.pop(0)

    def stream():
        yield "a"
        yield "b"

    with pytest.raises(ModelTimeoutError, match="总耗时"):
        list(
            stream_with_deadline(
                stream(),
                total_timeout_seconds=60,
                first_token_timeout_seconds=30,
                clock=clock,
            )
        )
