from model.factory import chat_model


def test_chat_model_has_bounded_timeout_and_retries():
    assert chat_model.max_retries == 2
    assert chat_model.model_kwargs["timeout"] == 45
