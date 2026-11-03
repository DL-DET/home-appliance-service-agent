"""Agent 单次运行使用的上下文定义。"""

from typing import TypedDict


class AgentContext(TypedDict, total=False):
    """一次请求内只读或受控修改的运行时信息。"""

    user_id: str
    city: str
    report_month: str
    # 只有报告场景的中间件会把它改成 True，用于动态切换系统提示词。
    report: bool
    # 工具预算与缓存只属于本次请求，请求结束即销毁，不会跨会话泄漏。
    max_tool_calls: int
    tool_calls: int
    tool_cache: dict[str, str]
