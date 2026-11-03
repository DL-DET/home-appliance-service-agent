"""Agent 对外输出的结构化事件，供 UI 和评测共同消费。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

EventKind = Literal["token", "tool_call", "tool_result", "done"]


@dataclass(frozen=True)
class AgentEvent:
    """一次 Agent 运行中的单个事件。"""

    kind: EventKind
    content: str = ""
    name: str = ""
    call_id: str = ""
    data: dict[str, Any] | None = None
