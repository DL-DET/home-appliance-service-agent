from pathlib import Path

from streamlit.testing.v1 import AppTest


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def test_app_renders_without_loading_model():
    app = AppTest.from_file(str(APP_PATH), default_timeout=20).run()

    assert not app.exception
    assert app.title[0].value == "清尘智服 · 家电客服 Agent"
    assert not any(item.key == "selected_user_id" for item in app.selectbox)
    assert any("固定演示身份" in item.value for item in app.markdown)


def test_report_month_can_change_without_identity_switching():
    app = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
    app.selectbox(key="selected_month").select("2025-11").run()

    assert not app.exception
    assert app.session_state["selected_month"] == "2025-11"
