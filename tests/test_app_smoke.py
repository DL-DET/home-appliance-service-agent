from pathlib import Path

from streamlit.testing.v1 import AppTest


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def test_app_renders_without_loading_model():
    app = AppTest.from_file(str(APP_PATH), default_timeout=20).run()

    assert not app.exception
    assert app.title[0].value == "清尘智服 · 家电客服 Agent"


def test_switching_demo_user_does_not_crash():
    app = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
    app.selectbox(key="selected_user_id").select("1003").run()

    assert not app.exception
    assert app.session_state["selected_user_id"] == "1003"
