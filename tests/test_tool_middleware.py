"""工具预算与重复调用缓存的单元测试。"""

from types import SimpleNamespace

from langchain.tools.tool_node import ToolCallRequest
from langchain_core.messages import ToolMessage

from agent.tools.middleware import monitor_tool


def make_request(
    context: dict,
    *,
    name: str = "demo",
    args: dict | None = None,
    call_id: str = "call-1",
) -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": name, "args": args or {}, "id": call_id},
        tool=None,
        state=None,
        runtime=SimpleNamespace(context=context),
    )


def run_tool(request: ToolCallRequest, handler):
    return monitor_tool.wrap_tool_call(request, handler)


def make_handler(counter: list[int]):
    def handler(request: ToolCallRequest) -> ToolMessage:
        counter[0] += 1
        return ToolMessage(
            content=f"result-{counter[0]}",
            tool_call_id=str(request.tool_call["id"]),
            name=str(request.tool_call["name"]),
        )

    return handler


def test_allows_tool_calls_under_budget():
    context = {"max_tool_calls": 2}
    counter = [0]
    handler = make_handler(counter)

    first = run_tool(make_request(context, args={"q": "a"}, call_id="c1"), handler)
    second = run_tool(make_request(context, args={"q": "b"}, call_id="c2"), handler)

    assert first.status == "success"
    assert second.status == "success"
    assert counter[0] == 2
    assert context["tool_calls"] == 2


def test_blocks_tool_call_over_budget():
    context = {"max_tool_calls": 1}
    counter = [0]
    handler = make_handler(counter)

    run_tool(make_request(context, args={"q": "a"}, call_id="c1"), handler)
    blocked = run_tool(make_request(context, args={"q": "b"}, call_id="c2"), handler)

    assert blocked.status == "error"
    assert "上限" in str(blocked.content)
    assert counter[0] == 1
    assert context["tool_calls"] == 1


def test_duplicate_call_uses_cache():
    context = {"max_tool_calls": 6}
    counter = [0]
    handler = make_handler(counter)

    first = run_tool(make_request(context, args={"q": "same"}, call_id="c1"), handler)
    second = run_tool(make_request(context, args={"q": "same"}, call_id="c2"), handler)

    assert counter[0] == 1
    assert second.content == first.content
    # 缓存命中不执行工具，但仍然消耗一次调用预算。
    assert context["tool_calls"] == 2


def test_different_args_are_not_cached():
    context = {"max_tool_calls": 6}
    counter = [0]
    handler = make_handler(counter)

    run_tool(make_request(context, args={"q": "a"}, call_id="c1"), handler)
    run_tool(make_request(context, args={"q": "b"}, call_id="c2"), handler)

    assert counter[0] == 2


def test_cache_is_isolated_between_requests():
    counter = [0]
    handler = make_handler(counter)
    first_context = {"max_tool_calls": 6}
    second_context = {"max_tool_calls": 6}

    run_tool(make_request(first_context, args={"q": "same"}, call_id="c1"), handler)
    run_tool(make_request(second_context, args={"q": "same"}, call_id="c2"), handler)

    assert counter[0] == 2
    assert len(first_context["tool_cache"]) == 1
    assert len(second_context["tool_cache"]) == 1
    assert list(first_context["tool_cache"].values()) == ["result-1"]
    assert list(second_context["tool_cache"].values()) == ["result-2"]


def test_report_tool_still_switches_prompt_context():
    context = {"max_tool_calls": 6}
    counter = [0]
    handler = make_handler(counter)

    run_tool(make_request(context, name="fill_context_for_report", call_id="c1"), handler)
    run_tool(make_request(context, name="fill_context_for_report", call_id="c2"), handler)

    assert context["report"] is True
    assert counter[0] == 2
    assert context["tool_cache"] == {}
