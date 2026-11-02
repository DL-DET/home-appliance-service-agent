from types import SimpleNamespace

from agent.tools.agent_tools import (
    fetch_external_data,
    get_order_status,
    get_user_id,
    get_user_location,
)


def test_runtime_context_is_deterministic():
    runtime = SimpleNamespace(context={"user_id": "1001", "city": "上海"})

    assert get_user_id.func(runtime=runtime) == "1001"
    assert get_user_location.func(runtime=runtime) == "上海"


def test_tool_rejects_cross_user_usage_record():
    runtime = SimpleNamespace(context={"user_id": "1001"})

    own = fetch_external_data.func(
        user_id="1001",
        month="2025-12",
        runtime=runtime,
    )
    other = fetch_external_data.func(
        user_id="1004",
        month="2025-12",
        runtime=runtime,
    )

    assert '"user_id": "1001"' in own
    assert "无权" in other


def test_order_tool_uses_current_user_ownership():
    own_runtime = SimpleNamespace(context={"user_id": "1001"})
    other_runtime = SimpleNamespace(context={"user_id": "1002"})

    own = get_order_status.func(order_id="ORD-2025-1001", runtime=own_runtime)
    other = get_order_status.func(order_id="ORD-2025-1001", runtime=other_runtime)

    assert "已签收" in own
    assert "未查询到" in other
