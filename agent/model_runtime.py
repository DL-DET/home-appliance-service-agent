"""模型调用的总超时、首 token 超时和 token 用量。

ChatTongyi 自己的 timeout 是流式空闲超时，不是整次请求的总超时。
这里在消费流时检查总耗时；真正的网络卡死仍由客户端 idle timeout 兜底。
"""

from __future__ import annotations

import time
from typing import Any, Callable, Iterable, Iterator, TypeVar

from utils.config_handler import agent_conf

T = TypeVar("T")

DEFAULT_MODEL_TOTAL_TIMEOUT_SECONDS = float(
    agent_conf.get("model_total_timeout_seconds", 60)
)
DEFAULT_MODEL_FIRST_TOKEN_TIMEOUT_SECONDS = float(
    agent_conf.get("model_first_token_timeout_seconds", 30)
)


class ModelTimeoutError(TimeoutError):
    """模型首 token 或总耗时超过预算。"""


def stream_with_deadline(
    stream: Iterable[T],
    *,
    total_timeout_seconds: float | None = DEFAULT_MODEL_TOTAL_TIMEOUT_SECONDS,
    first_token_timeout_seconds: float | None = (
        DEFAULT_MODEL_FIRST_TOKEN_TIMEOUT_SECONDS
    ),
    clock: Callable[[], float] = time.monotonic,
) -> Iterator[T]:
    """在消费流时检查首 token 和总耗时。

    只能在一个 chunk 到达时检查，无法强杀已经阻塞的 next()；
    没有 chunk 的卡死由 ChatTongyi 的 idle timeout 负责。
    """
    started = clock()
    first = True

    for item in stream:
        elapsed = clock() - started
        if (
            first
            and first_token_timeout_seconds is not None
            and elapsed > first_token_timeout_seconds
        ):
            raise ModelTimeoutError(
                f"模型首 token 超时（{elapsed:.1f}s > "
                f"{first_token_timeout_seconds:g}s）"
            )
        if total_timeout_seconds is not None and elapsed > total_timeout_seconds:
            raise ModelTimeoutError(
                f"模型总耗时超时（{elapsed:.1f}s > {total_timeout_seconds:g}s）"
            )
        first = False
        yield item


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_usage(usage: dict[str, Any]) -> dict[str, int] | None:
    input_tokens = _to_int(usage.get("input_tokens", usage.get("prompt_tokens")))
    output_tokens = _to_int(
        usage.get("output_tokens", usage.get("completion_tokens"))
    )
    total_tokens = _to_int(usage.get("total_tokens"))

    normalized: dict[str, int] = {}
    if input_tokens is not None:
        normalized["input_tokens"] = input_tokens
    if output_tokens is not None:
        normalized["output_tokens"] = output_tokens
    if total_tokens is not None:
        normalized["total_tokens"] = total_tokens
    return normalized or None


def extract_token_usage(chunk: Any) -> dict[str, int] | None:
    """兼容 usage_metadata 和 DashScope/ChatTongyi 的 token_usage。"""
    usage = getattr(chunk, "usage_metadata", None)
    if isinstance(usage, dict):
        normalized = _normalize_usage(usage)
        if normalized:
            return normalized

    for attr in ("response_metadata", "additional_kwargs"):
        metadata = getattr(chunk, attr, None) or {}
        if not isinstance(metadata, dict):
            continue
        for key in ("token_usage", "usage"):
            usage = metadata.get(key)
            if isinstance(usage, dict):
                normalized = _normalize_usage(usage)
                if normalized:
                    return normalized
    return None


def record_usage(context: dict | None, chunk: Any) -> None:
    """把当前 chunk 的 token 用量写进本次请求的 trace。"""
    trace = (context or {}).get("trace")
    if trace is None:
        return
    usage = extract_token_usage(chunk)
    if not usage:
        return
    for key, value in usage.items():
        trace.attributes[key] = value
