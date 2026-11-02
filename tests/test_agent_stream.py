from dataclasses import dataclass

from ui.agent_stream import consume_agent_events


@dataclass
class FakeEvent:
    kind: str
    content: str = ""
    name: str = ""


class FakeStatus:
    def __init__(self):
        self.updates = []
        self.writes = []

    def update(self, *, label: str, state: str) -> None:
        self.updates.append((label, state))

    def write(self, body: str) -> None:
        self.writes.append(body)


def test_answer_is_rendered_outside_status_trace():
    status = FakeStatus()
    rendered = []
    trace = []
    events = [
        FakeEvent("tool_call", name="rag_summarize"),
        FakeEvent("tool_result", name="rag_summarize", content="检索完成"),
        FakeEvent("token", content="第一段"),
        FakeEvent("token", content="第二段"),
    ]

    answer = consume_agent_events(
        events,
        trace=trace,
        status=status,
        render_answer=rendered.append,
        get_label=lambda name: name,
        shorten=lambda text: text,
    )

    assert answer == "第一段第二段"
    assert rendered == ["第一段", "第一段第二段"]
    assert status.updates[-1] == ("执行完成 · 2 条轨迹", "complete")
