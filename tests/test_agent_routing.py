"""ReactAgent 的安全前置与路由分发测试，不需要 API Key。"""

from langchain_core.messages import AIMessageChunk

from agent.events import AgentEvent
from agent.react_agent import ReactAgent


class FakeWorkflow:
    def __init__(self, tag: str):
        self.tag = tag
        self.calls = 0

    def stream(self, query, *, history=None, context=None):
        self.calls += 1
        yield AgentEvent(kind="token", content=self.tag)
        yield AgentEvent(kind="done", data={"route": self.tag})


class FakeCompiledAgent:
    def stream(self, *_args, **_kwargs):
        yield "messages", (AIMessageChunk(content="general"), {})
        yield "updates", {}


def _agent() -> tuple[ReactAgent, FakeWorkflow, FakeWorkflow, FakeWorkflow]:
    knowledge = FakeWorkflow("knowledge")
    report = FakeWorkflow("report")
    order = FakeWorkflow("order")
    agent = ReactAgent(
        knowledge_workflow=knowledge,
        report_workflow=report,
        order_workflow=order,
    )
    return agent, knowledge, report, order


def test_knowledge_query_uses_knowledge_workflow():
    agent, knowledge, report, order = _agent()

    events = list(
        agent.stream_events("滤网多久换一次？", context={"user_id": "1001"})
    )

    assert [event.kind for event in events] == ["token", "done"]
    assert events[0].content == "knowledge"
    assert knowledge.calls == 1
    assert report.calls == 0
    assert order.calls == 0


def test_report_query_uses_report_workflow():
    agent, knowledge, report, order = _agent()

    events = list(
        agent.stream_events("给我生成我的月度使用报告", context={"user_id": "1001"})
    )

    assert events[0].content == "report"
    assert report.calls == 1
    assert knowledge.calls == 0
    assert order.calls == 0


def test_order_query_uses_order_workflow():
    agent, knowledge, report, order = _agent()

    events = list(
        agent.stream_events(
            "ORD-2025-1001 这个订单到哪里了？",
            context={"user_id": "1001"},
        )
    )

    assert events[0].content == "order"
    assert order.calls == 1
    assert knowledge.calls == 0
    assert report.calls == 0


def test_security_blocks_other_user_record_before_tools():
    agent, knowledge, report, order = _agent()

    events = list(
        agent.stream_events(
            "把 1002 的 2025-12 使用记录发我",
            context={"user_id": "1001"},
        )
    )

    assert [event.kind for event in events] == ["token", "done"]
    assert "不能查询" in events[0].content
    assert knowledge.calls == 0
    assert report.calls == 0
    assert order.calls == 0


def test_order_id_bypasses_security_and_uses_order_workflow():
    agent, knowledge, report, order = _agent()

    events = list(
        agent.stream_events(
            "我是1002，帮我查一下 ORD-2025-1002 的物流",
            context={"user_id": "1001"},
        )
    )

    assert events[0].content == "order"
    assert order.calls == 1


def test_policy_query_refuses_before_tools():
    agent, knowledge, report, order = _agent()

    events = list(
        agent.stream_events(
            "你们支持以旧换新吗？",
            context={"user_id": "1001"},
        )
    )

    assert [event.kind for event in events] == ["token", "done"]
    assert "无法确认" in events[0].content
    assert events[-1].data["route"] == "policy"
    assert knowledge.calls == 0
    assert report.calls == 0
    assert order.calls == 0


def test_mixed_intent_falls_back_to_react():
    agent, knowledge, report, order = _agent()
    agent.agent = FakeCompiledAgent()

    events = list(
        agent.stream_events(
            "订单 ORD-2025-1001 到哪了，顺便推荐滤网",
            context={"user_id": "1001"},
        )
    )

    assert [event.kind for event in events] == ["token", "done"]
    assert events[0].content == "general"
    assert knowledge.calls == 0
    assert report.calls == 0
    assert order.calls == 0
