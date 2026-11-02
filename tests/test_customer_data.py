from services.customer_data import (
    format_usage_record,
    get_available_months,
    get_order_for_user,
    get_usage_record,
    get_user_profile,
    get_user_profiles,
)


def test_profiles_cover_all_demo_users():
    profiles = get_user_profiles()
    assert len(profiles) == 10
    assert get_user_profile("1001").city == "上海"


def test_usage_data_is_loaded_with_csv_parser():
    assert get_available_months()[0] == "2025-12"
    record = get_usage_record("1004", "2025-12")
    assert record is not None
    assert "主刷" in record.consumables

    payload = format_usage_record(record)
    assert '"user_id": "1004"' in payload
    assert '"month": "2025-12"' in payload


def test_order_access_is_scoped_to_current_user():
    own_order = get_order_for_user("ORD-2025-1001", "1001")
    assert own_order is not None
    assert own_order.status == "已签收"

    cross_user_order = get_order_for_user("ORD-2025-1001", "1002")
    assert cross_user_order is None
