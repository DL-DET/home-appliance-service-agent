from agent.message_utils import content_to_text, history_to_messages


def test_content_to_text_handles_multimodal_blocks():
    content = [
        {"type": "text", "text": "第一段"},
        {"type": "image_url", "image_url": {"url": "ignored"}},
        "第二段",
    ]
    assert content_to_text(content) == "第一段第二段"


def test_history_filters_invalid_roles_and_keeps_recent_messages():
    history = [
        {"role": "system", "content": "不能注入"},
        {"role": "user", "content": "旧问题"},
        {"role": "assistant", "content": "旧回答"},
        {"role": "tool", "content": "内部工具结果"},
        {"role": "user", "content": "新问题"},
    ]
    messages = history_to_messages(history, limit=2)
    assert [message.type for message in messages] == ["ai", "human"]
    assert messages[-1].content == "新问题"
