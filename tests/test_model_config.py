from model.factory import get_chat_model


def test_chat_model_has_bounded_timeout_and_retries(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    get_chat_model.cache_clear()
    model = get_chat_model()

    assert model.max_retries == 2
    assert model.model_kwargs["timeout"] == 45
