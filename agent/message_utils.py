"""处理 LangChain 消息的纯函数，便于单测和复用。"""

from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage


def content_to_text(content: Any) -> str:
    """把字符串或多段内容块统一转换成纯文本。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts)
    return str(content)


def history_to_messages(
    history: list[dict[str, Any]] | None,
    *,
    limit: int = 12,
) -> list[BaseMessage]:
    """只保留最近若干条有效对话，避免前端字段直接污染 Agent 状态。"""
    if not history:
        return []

    messages: list[BaseMessage] = []
    for item in history:
        role = item.get("role")
        content = content_to_text(item.get("content")).strip()
        if not content:
            continue
        if role == "user":
            messages.append(HumanMessage(content=content))
        elif role == "assistant":
            messages.append(AIMessage(content=content))
    # 先过滤无效角色再截断，避免一条 tool 消息占用有效历史名额。
    return messages[-limit:]
