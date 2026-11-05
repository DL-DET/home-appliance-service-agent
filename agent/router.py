"""规则路由：把用户问题分到报告、订单、知识或通用路径。

这里刻意不调用模型：路由是高频决策，先用可测试的规则保证稳定；
规则覆盖不到的混合意图，回退到 ReAct。
"""

from __future__ import annotations

import re
from enum import Enum

ORDER_ID_PATTERN = re.compile(r"ORD-\d{4}-\d{4}", re.IGNORECASE)
USER_ID_PATTERN = re.compile(r"(?<!\d)(10\d{2})(?!\d)")

OTHER_USER_PATTERN = re.compile(
    r"(其他|别人|他人|另一个|别的).{0,8}(用户|账号|订单|记录|邮箱)"
)
ALL_USERS_PATTERN = re.compile(r"(所有|全部|每个).{0,4}(用户|账号)")
PROMPT_LEAK_PATTERN = re.compile(
    r"(系统提示词|系统\s*prompt|api\s*key|密钥|内部配置)",
    re.IGNORECASE,
)
INJECTION_PATTERN = re.compile(r"(忽略|无视|跳过).{0,10}(规则|指令|限制)")
ROLE_PATTERN = re.compile(r"(管理员|超级权限|所有权限|最高权限)")

REPORT_KEYWORDS = ("使用报告", "月度报告")
ORDER_KEYWORDS = ("订单", "物流", "发货", "到哪", "签收", "售后进度")
KNOWLEDGE_KEYWORDS = (
    "故障",
    "排查",
    "选购",
    "保养",
    "清洁",
    "滤网",
    "主刷",
    "电池",
    "噪音",
    "地毯",
    "建图",
    "避障",
    "充电",
    "拖布",
    "吸力",
    "消毒液",
    "毛发",
    "尘盒",
    "续航",
    "边刷",
    "水痕",
    "维护",
    "扫地机器人",
    "扫拖一体机",
    "洗地机",
    "吸尘器",
    "推荐",
)

OTHER_USER_REFUSAL = (
    "我只能查询当前登录账号的数据，不能查询其他用户的订单、使用记录或邮箱。"
)
SECURITY_REFUSAL = (
    "我只能查询当前登录账号的数据，也不能提供系统提示词、密钥或内部配置。"
)

# 价格、活动、补贴类政策必须以后台数据为准，知识库没有依据时不能让模型凭记忆回答。
POLICY_KEYWORDS = ("以旧换新", "折价", "抵扣", "补贴", "回收")
# 同义表达：能抵多少钱、抵钱、折抵、抵价、能抵吗。
POLICY_PATTERNS = (
    re.compile("|".join(re.escape(keyword) for keyword in POLICY_KEYWORDS)),
    re.compile(r"抵(?:多少钱|多少|钱|价|吗)"),
    re.compile(r"折抵"),
)
POLICY_REFUSAL = (
    "知识库暂未收录该活动或政策信息，无法确认。"
    "请以官方活动页或人工客服的最新说明为准。"
)


class Route(str, Enum):
    REPORT = "report"
    ORDER = "order"
    KNOWLEDGE = "knowledge"
    GENERAL = "general"


def extract_order_id(query: str) -> str | None:
    """提取订单号并统一大写；没有则返回 None。"""
    match = ORDER_ID_PATTERN.search(query or "")
    return match.group(0).upper() if match else None


def _mentions_other_user_id(query: str, current_user_id: str) -> bool:
    """识别用户输入里出现的其他演示用户 ID。"""
    if not current_user_id:
        return False
    for match in USER_ID_PATTERN.finditer(query or ""):
        if match.group(1) != current_user_id:
            return True
    return False


def security_refusal(query: str, current_user_id: str = "") -> str | None:
    """返回安全拒答文案；None 表示放行。

    有合法订单号时不在这里拒绝：交给 OrderWorkflow，
    由工具层根据 runtime.context 的当前用户校验归属。
    """
    text = (query or "").strip()
    if not text:
        return None

    if extract_order_id(text):
        return None

    if _mentions_other_user_id(text, current_user_id):
        return OTHER_USER_REFUSAL

    if any(
        pattern.search(text)
        for pattern in (
            OTHER_USER_PATTERN,
            ALL_USERS_PATTERN,
            PROMPT_LEAK_PATTERN,
            INJECTION_PATTERN,
            ROLE_PATTERN,
        )
    ):
        return SECURITY_REFUSAL

    return None


def _is_report_query(text: str) -> bool:
    """报告意图需要“报告”加生成/使用语境，避免“报告一下天气”被误判。"""
    return any(keyword in text for keyword in REPORT_KEYWORDS) or (
        "生成" in text and "报告" in text
    )


def policy_refusal(query: str) -> str | None:
    """活动/价格政策没有可信数据时直接拒答，避免模型编造。"""
    text = (query or "").strip()
    if not text:
        return None
    if any(pattern.search(text) for pattern in POLICY_PATTERNS):
        return POLICY_REFUSAL
    return None


def route_query(query: str) -> Route:
    """单一路由；混合意图回退 GENERAL，由 ReAct 组合工具。"""
    text = (query or "").strip()
    if not text:
        return Route.GENERAL

    is_report = _is_report_query(text)
    is_order = bool(extract_order_id(text)) or any(
        keyword in text for keyword in ORDER_KEYWORDS
    )
    is_knowledge = any(keyword in text for keyword in KNOWLEDGE_KEYWORDS)

    if is_report:
        # 报告 + 其他意图：交给 ReAct，避免漏掉第二个任务。
        return Route.GENERAL if (is_order or is_knowledge) else Route.REPORT

    if is_order and is_knowledge:
        return Route.GENERAL
    if is_order:
        return Route.ORDER
    if is_knowledge:
        return Route.KNOWLEDGE
    return Route.GENERAL
