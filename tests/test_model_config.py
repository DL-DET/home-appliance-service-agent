from langchain_openai import ChatOpenAI

from model.factory import get_chat_model


def test_chat_model_uses_compatible_endpoint_with_bounded_timeout(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    get_chat_model.cache_clear()
    model = get_chat_model()

    assert isinstance(model, ChatOpenAI)
    assert model.model_name == "qwen3.7-plus"
    assert model.openai_api_base == (
        "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    assert model.max_retries == 2
    assert model.request_timeout == 45.0
    assert model.streaming is True
    assert model.stream_usage is True
