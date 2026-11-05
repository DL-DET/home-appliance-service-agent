"""轻量请求追踪：request_id、span、JSONL 日志和敏感信息脱敏。

不引入 OpenTelemetry，先把“一次请求发生了什么”记录清楚：
- 每个请求一个 request_id
- 每个阶段一个 span，记录耗时、状态和错误类型
- 日志写成 JSONL，方便按 request_id 过滤
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterator

from utils.path_tool import get_abs_path


TRACE_LOG_DIR = get_abs_path("logs")
os.makedirs(TRACE_LOG_DIR, exist_ok=True)

_ORDER_ID_PATTERN = re.compile(r"ORD-\d{4}-\d{4}", re.IGNORECASE)
_EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w.-]+")


def new_request_id() -> str:
    """生成可读、可排序的请求 ID。"""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"req_{stamp}_{uuid.uuid4().hex[:8]}"


def redact(value: Any, *, keep: int = 4) -> str:
    """只保留末尾少量字符，避免日志泄露完整订单号、用户 ID 或邮箱。"""
    text = str(value or "").strip()
    if not text:
        return ""
    if "@" in text:
        local, _, domain = text.partition("@")
        return f"{local[:1]}***@{domain}"
    if len(text) <= keep:
        return "***"
    return f"***{text[-keep:]}"


def preview(value: Any, limit: int = 80) -> str:
    """压缩空白并截断，避免日志被长文本刷屏。"""
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else f"{text[:limit]}..."


def safe_preview(value: Any, limit: int = 80) -> str:
    """日志用的查询预览：先脱敏订单号和邮箱，再截断。"""
    text = preview(value, limit)
    text = _ORDER_ID_PATTERN.sub("ORD-****", text)
    text = _EMAIL_PATTERN.sub("***@***", text)
    return text


@dataclass
class Span:
    trace_id: str
    name: str
    parent_id: str | None = None
    span_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    started_at: float = field(default_factory=time.perf_counter)
    duration_ms: float = 0.0
    status: str = "ok"
    error_type: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)

    def finish(self, status: str | None = None, error_type: str = "") -> None:
        self.duration_ms = round((time.perf_counter() - self.started_at) * 1000, 1)
        self.status = status or self.status or "ok"
        self.error_type = error_type or self.error_type

    def as_dict(self) -> dict[str, Any]:
        payload = dict(self.attributes)
        payload.update(
            {
                "event": "span",
                "request_id": self.trace_id,
                "span_id": self.span_id,
                "parent_id": self.parent_id,
                "name": self.name,
                "duration_ms": self.duration_ms,
                "status": self.status,
                "error_type": self.error_type,
            }
        )
        return payload


class Trace:
    """一次请求的 trace；由 Agent 创建，工作流和工具只追加 span。"""

    def __init__(
        self,
        request_id: str | None = None,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self.request_id = request_id or new_request_id()
        self.started_at = time.perf_counter()
        self.events: list[dict[str, Any]] = []
        self.attributes: dict[str, Any] = {}
        self.spans: list[Span] = []
        self.status = "ok"
        self.error_type = ""
        self._logger = logger or _get_trace_logger()
        self._stack: list[Span] = []

    @contextmanager
    def span(self, name: str, **attributes: Any) -> Iterator[Span]:
        """记录一个阶段的耗时；异常会标记 span 和整次请求失败。"""
        parent = self._stack[-1] if self._stack else None
        span = Span(
            trace_id=self.request_id,
            name=name,
            parent_id=parent.span_id if parent else None,
            attributes=dict(attributes),
        )
        self.spans.append(span)
        self._stack.append(span)
        try:
            yield span
        except Exception as exc:
            span.finish("error", type(exc).__name__)
            self.mark_error(type(exc).__name__)
            raise
        else:
            span.finish(span.status)
        finally:
            self._stack.pop()
            self._emit(span.as_dict())

    def increment(self, key: str, amount: int = 1) -> None:
        self.attributes[key] = int(self.attributes.get(key, 0)) + amount

    def mark_error(self, error_type: str) -> None:
        self.status = "error"
        self.error_type = error_type

    def finish(self, route: str = "", **attributes: Any) -> dict[str, Any]:
        """输出一行请求摘要；即使中途失败也会被 finally 调用。"""
        total_ms = round((time.perf_counter() - self.started_at) * 1000, 1)
        payload = {
            **self.attributes,
            **attributes,
            "event": "request",
            "request_id": self.request_id,
            "route": route,
            "status": self.status,
            "error_type": self.error_type,
            "total_ms": total_ms,
            "span_count": len(self.spans),
        }
        self._emit(payload)
        return payload

    def _emit(self, payload: dict[str, Any]) -> None:
        self.events.append(payload)
        if self._logger is not None:
            self._logger.info(json.dumps(payload, ensure_ascii=False, default=str))


_TRACE_LOGGER: logging.Logger | None = None


def _get_trace_logger() -> logging.Logger:
    """独立的 JSONL logger，避免和现有文本日志混在一起。"""
    global _TRACE_LOGGER
    if _TRACE_LOGGER is not None:
        return _TRACE_LOGGER

    logger = logging.getLogger("trace")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        log_file = os.path.join(
            TRACE_LOG_DIR,
            f"trace_{datetime.now().strftime('%Y%m%d')}.jsonl",
        )
        handler = logging.FileHandler(log_file, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    _TRACE_LOGGER = logger
    return logger
