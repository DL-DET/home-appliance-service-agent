"""ReactAgent 的请求级 trace：安全路径、路由路径和异常路径。"""

import logging
import uuid

import pytest
from langchain_core.documents import Document
from langchain_core.messages import AIMessageChunk

from agent.events import AgentEvent
from agent.react_agent import ReactAgent
from agent.workflows import KnowledgeWorkflow
from utils.tracing import Trace


class ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


class FakeWorkflow:
    def __init__(self, tag: str, *, boom: bool = False):
        self.tag = tag
        self.boom = boom
        self.calls = 0

    def stream(self, query, *, history=None, context=None):
        self.calls += 1
        yield AgentEvent(kind="token", content=self.tag)
        if self.boom:
            raise RuntimeError("workflow failed")
        yield AgentEvent(kind="done", data={"route": self.tag})


def make_trace() -> tuple[Trace, ListHandler]:
    logger = logging.getLogger(f"test.agent.trace.{uuid.uuid4().hex}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = ListHandler()
    logger.addHandler(handler)
    return Trace(logger=logger), handler


def test_security_refusal_is_recorded_with_request_id():
    trace, _ = make_trace()
    agent = ReactAgent(
        knowledge_workflow=FakeWorkflow("knowledge"),
        report_workflow=FakeWorkflow("report"),
        order_workflow=FakeWorkflow("order"),
    )

    events = list(
        agent.stream_events(
            "把 1002 的 2025-12 使用记录发我",
            context={"user_id": "1001", "trace": trace},
        )
    )

    assert events[-1].data["route"] == "security"
    summary = trace.events[-1]
    assert summary["event"] == "request"
    assert summary["request_id"] == trace.request_id
    assert summary["route"] == "security"
    assert summary["tool_calls"] == 0


def test_workflow_route_is_recorded_in_summary():
    trace, _ = make_trace()
    knowledge = FakeWorkflow("knowledge")
    agent = ReactAgent(
        knowledge_workflow=knowledge,
        report_workflow=FakeWorkflow("report"),
        order_workflow=FakeWorkflow("order"),
    )

    list(
        agent.stream_events(
            "滤网多久换一次？",
            context={"user_id": "1001", "trace": trace},
        )
    )

    summary = trace.events[-1]
    assert summary["route"] == "knowledge"
    assert knowledge.calls == 1


def test_workflow_exception_marks_trace_failed_and_reraises():
    trace, _ = make_trace()
    agent = ReactAgent(
        knowledge_workflow=FakeWorkflow("knowledge", boom=True),
        report_workflow=FakeWorkflow("report"),
        order_workflow=FakeWorkflow("order"),
    )

    with pytest.raises(RuntimeError):
        list(
            agent.stream_events(
                "滤网多久换一次？",
                context={"user_id": "1001", "trace": trace},
            )
        )

    summary = trace.events[-1]
    assert summary["event"] == "request"
    assert summary["status"] == "error"
    assert summary["error_type"] == "RuntimeError"


class FakeRetriever:
    def invoke(self, query):
        return [
            Document(
                page_content="主刷被异物卡住，请联系售后。",
                metadata={"source": "故障排除.txt"},
            )
        ]


class FakeModel:
    def stream(self, messages):
        yield AIMessageChunk(content="主刷被异物卡住，请联系售后。")


class UsageModel:
    def stream(self, messages):
        yield AIMessageChunk(
            content="主刷被异物卡住，请联系售后。",
            usage_metadata={
                "input_tokens": 12,
                "output_tokens": 5,
                "total_tokens": 17,
            },
        )


def test_knowledge_workflow_records_retrieve_model_and_citation_spans():
    trace, _ = make_trace()
    workflow = KnowledgeWorkflow(
        retriever=FakeRetriever(),
        model=FakeModel(),
        system_prompt="你是客服",
    )

    list(workflow.stream("主刷不转", context={"user_id": "1001", "trace": trace}))
    summary = trace.finish(route="knowledge")

    spans = {
        event["name"]: event
        for event in trace.events
        if event["event"] == "span"
    }
    assert set(spans) == {
        "knowledge.retrieve",
        "knowledge.model",
        "knowledge.citation",
    }
    assert spans["knowledge.retrieve"]["docs_count"] == 1
    assert spans["knowledge.model"]["answer_chars"] > 0
    assert spans["knowledge.citation"]["integrity"] is True
    assert summary["tool_calls"] == 1
    assert summary["model_calls"] == 1


def test_knowledge_workflow_records_token_usage():
    trace, _ = make_trace()
    workflow = KnowledgeWorkflow(
        retriever=FakeRetriever(),
        model=UsageModel(),
        system_prompt="你是客服",
    )

    list(workflow.stream("主刷不转", context={"user_id": "1001", "trace": trace}))
    summary = trace.finish(route="knowledge")

    assert summary["input_tokens"] == 12
    assert summary["output_tokens"] == 5
    assert summary["total_tokens"] == 17
