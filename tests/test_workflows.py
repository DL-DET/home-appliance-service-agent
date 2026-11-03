"""三条确定性流水线的测试：全部使用假模型/假检索器，不需要 API Key。"""

from langchain_core.documents import Document
from langchain_core.messages import AIMessageChunk

from agent.workflows import KnowledgeWorkflow, OrderWorkflow, ReportWorkflow
from services.customer_data import OrderRecord, UsageRecord, UserProfile


class FakeModel:
    def __init__(self, chunks: list[str]):
        self.chunks = chunks
        self.messages = None

    def stream(self, messages):
        self.messages = messages
        for chunk in self.chunks:
            yield AIMessageChunk(content=chunk)


class FakeRetriever:
    def __init__(self, documents: list[Document]):
        self.documents = documents

    def invoke(self, query: str):
        return self.documents


def _profile() -> UserProfile:
    return UserProfile(
        user_id="1001",
        display_name="演示用户1001",
        city="上海",
        household="独居",
        area="65㎡公寓",
        product="扫拖一体机",
        email="demo1001@example.com",
    )


def _record() -> UsageRecord:
    return UsageRecord(
        user_id="1001",
        month="2025-12",
        feature="65㎡公寓 | 单身 | 木地板",
        efficiency="使用率:90%",
        consumables="主刷寿命:剩余30天",
        comparison="优于85%同面积用户",
    )


def test_knowledge_workflow_uses_rag_first_and_adds_sources():
    documents = [
        Document(
            page_content="主刷被异物卡住，请联系售后。",
            metadata={"source": "故障排除.txt"},
        )
    ]
    model = FakeModel(["主刷被异物卡住，请联系售后。"])
    workflow = KnowledgeWorkflow(
        retriever=FakeRetriever(documents),
        model=model,
        system_prompt="你是客服",
    )

    events = list(workflow.stream("主刷不转", context={"user_id": "1001"}))

    assert events[0].kind == "tool_call"
    assert events[0].name == "rag_summarize"
    tool_result = next(event for event in events if event.kind == "tool_result")
    assert tool_result.data["sources"] == ["故障排除.txt"]
    assert any(event.kind == "token" and "主刷" in event.content for event in events)
    assert any("参考资料" in event.content for event in events)
    assert events[-1].kind == "done"
    assert events[-1].data["route"] == "knowledge"
    assert "本轮检索资料" in model.messages[0].content


def test_knowledge_workflow_without_documents_refuses():
    model = FakeModel(["不应该调用"])
    workflow = KnowledgeWorkflow(
        retriever=FakeRetriever([]),
        model=model,
        system_prompt="你是客服",
    )

    events = list(workflow.stream("没有资料的问题", context={"user_id": "1001"}))

    assert any(
        event.kind == "token" and "无法确认" in event.content for event in events
    )
    assert events[-1].kind == "done"
    assert model.messages is None


def test_report_workflow_reads_record_and_generates_once():
    model = FakeModel(["# 小丁家电服务与使用报告\n使用率90%，主刷剩余30天。"])
    workflow = ReportWorkflow(
        model=model,
        system_prompt="你是报告助手",
        get_profile=lambda _: _profile(),
        get_record=lambda *_: _record(),
    )

    events = list(
        workflow.stream(
            "生成我的月度报告",
            context={"user_id": "1001", "report_month": "2025-12"},
        )
    )

    assert events[0].kind == "tool_call"
    assert events[0].name == "fetch_external_data"
    assert any(event.kind == "token" and "90%" in event.content for event in events)
    assert events[-1].kind == "done"
    assert events[-1].data["route"] == "report"
    assert len(model.messages) == 2


def test_report_workflow_without_record_refuses():
    model = FakeModel(["不应该调用"])
    workflow = ReportWorkflow(
        model=model,
        system_prompt="你是报告助手",
        get_profile=lambda _: _profile(),
        get_record=lambda *_: None,
    )

    events = list(
        workflow.stream(
            "生成 2024-01 的报告",
            context={"user_id": "1001", "report_month": "2024-01"},
        )
    )

    assert any(event.kind == "token" and "未找到" in event.content for event in events)
    assert events[-1].kind == "done"
    assert model.messages is None


def test_order_workflow_returns_templated_status():
    order = OrderRecord(
        order_id="ORD-2025-1001",
        user_id="1001",
        status="已签收",
        logistics="已放至用户指定驿站",
        updated_at="2025-12-18 14:20",
    )
    workflow = OrderWorkflow(get_order=lambda *_: order)

    events = list(
        workflow.stream(
            "ORD-2025-1001 这个订单到哪里了？",
            context={"user_id": "1001"},
        )
    )

    assert events[0].kind == "tool_call"
    assert events[0].name == "get_order_status"
    assert any(
        event.kind == "token" and "已签收" in event.content and "驿站" in event.content
        for event in events
    )
    assert events[-1].kind == "done"


def test_order_workflow_not_found():
    workflow = OrderWorkflow(get_order=lambda *_: None)

    events = list(
        workflow.stream(
            "ORD-2025-9999 到哪了",
            context={"user_id": "1001"},
        )
    )

    assert any(
        event.kind == "token" and "未查询到" in event.content for event in events
    )
    assert events[-1].kind == "done"


def test_order_workflow_without_order_id_asks_for_id():
    workflow = OrderWorkflow(get_order=lambda *_: (_ for _ in ()).throw(AssertionError))

    events = list(workflow.stream("我的订单到哪了", context={"user_id": "1001"}))

    assert any(
        event.kind == "token" and "请提供订单号" in event.content for event in events
    )
    assert all(event.kind != "tool_call" for event in events)
