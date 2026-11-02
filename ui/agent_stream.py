"""把 Agent 事件流转换为界面状态，不直接依赖 Streamlit。"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any, Protocol


class AgentEventLike(Protocol):
    kind: str
    content: str
    name: str


class StatusLike(Protocol):
    def update(self, *, label: str, state: str) -> None: ...

    def write(self, body: str) -> None: ...


def consume_agent_events(
    events: Iterable[AgentEventLike],
    *,
    trace: list[dict[str, Any]],
    status: StatusLike,
    render_answer: Callable[[str], None],
    get_label: Callable[[str], str],
    shorten: Callable[[str], str],
) -> str:
    """分别更新工具轨迹和回答区域，避免回答被折叠进 status 容器。"""
    answer_parts: list[str] = []

    for event in events:
        if event.kind == "token":
            answer_parts.append(event.content)
            render_answer("".join(answer_parts))
        elif event.kind == "tool_call":
            label = get_label(event.name)
            status.update(label=f":shimmer[正在{label}]", state="running")
            trace.append(
                {
                    "kind": "tool_call",
                    "name": event.name,
                    "label": label,
                }
            )
        elif event.kind == "tool_result":
            label = get_label(event.name)
            summary = shorten(event.content) or "已完成"
            status.write(f"**{label}**：{summary}")
            trace.append(
                {
                    "kind": "tool_result",
                    "name": event.name,
                    "label": label,
                    "summary": summary,
                }
            )

    answer = "".join(answer_parts).strip()
    status.update(label=f"执行完成 · {len(trace)} 条轨迹", state="complete")
    return answer
