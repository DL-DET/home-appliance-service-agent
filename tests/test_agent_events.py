from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from agent.react_agent import ReactAgent


class FakeCompiledAgent:
    def stream(self, *_args, **_kwargs):
        yield "messages", (AIMessageChunk(content="回答"), {"langgraph_node": "model"})
        yield (
            "updates",
            {
                "model": {
                    "messages": [
                        AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "id": "call-1",
                                    "name": "rag_summarize",
                                    "args": {"query": "滤网维护"},
                                    "type": "tool_call",
                                }
                            ],
                        )
                    ]
                }
            },
        )
        yield (
            "updates",
            {
                "tools": {
                    "messages": [
                        ToolMessage(
                            content="资料内容",
                            tool_call_id="call-1",
                            name="rag_summarize",
                        )
                    ]
                }
            },
        )


def test_stream_events_separates_tokens_and_tool_trace():
    agent = object.__new__(ReactAgent)
    agent.history_limit = 12
    agent.agent = FakeCompiledAgent()

    # 用 GENERAL 问题验证 ReAct 的事件解析；知识问题现在走 KnowledgeWorkflow。
    events = list(agent.stream_events("你好"))
    assert [event.kind for event in events] == [
        "token",
        "tool_call",
        "tool_result",
        "done",
    ]
    assert events[0].content == "回答"
    assert events[1].name == "rag_summarize"
    assert events[2].content == "资料内容"
