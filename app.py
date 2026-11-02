"""家电客服 Agent 的 Streamlit 工作台。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import streamlit as st
from dotenv import load_dotenv

from services.auth_service import (
    AuthIdentity,
    IdentityError,
    resolve_demo_identity,
    resolve_oidc_identity,
)
from services.customer_data import (
    get_available_months,
    get_usage_record,
    get_user_profile,
    normalize_newlines,
)
from ui.agent_stream import consume_agent_events
from utils.config_handler import auth_conf


st.set_page_config(
    page_title="清尘智服 · 家电客服 Agent",
    page_icon=":material/support_agent:",
    layout="wide",
)


TOOL_LABELS = {
    "rag_summarize": "检索家电知识库",
    "get_order_status": "查询订单状态",
    "get_user_id": "读取当前用户",
    "get_user_location": "读取用户城市",
    "get_current_month": "读取报告月份",
    "fetch_external_data": "读取月度使用记录",
    "fill_context_for_report": "切换报告场景",
}

SUGGESTIONS = {
    ":material/description: 生成我的使用报告": "给我生成我的月度使用报告",
    ":material/build: 主刷不转怎么排查": "扫地机器人主刷不转怎么排查？",
    ":material/pets: 养宠家庭怎么选购": "养宠家庭选购扫拖一体机应该重点看什么？",
    ":material/route: 频繁避障失败怎么办": "机器人经常避障失败、被电线卡住怎么办？",
}


def _load_local_secrets() -> None:
    """本地读 .env，部署环境优先使用 Streamlit secrets。"""
    # 只读取项目根目录，避免开发机上级目录中的同名 Key 被意外继承。
    load_dotenv(Path(__file__).with_name(".env"))
    if os.getenv("DASHSCOPE_API_KEY", "").strip():
        return
    try:
        secret = st.secrets.get("DASHSCOPE_API_KEY", "")
    except Exception:
        return
    if secret:
        os.environ["DASHSCOPE_API_KEY"] = str(secret)


def _api_key_configured() -> bool:
    return bool(os.getenv("DASHSCOPE_API_KEY", "").strip())


def _oidc_configured() -> bool:
    """只有项目 secrets 中配置了 [auth] 才启用真实 OIDC 登录。"""
    try:
        return bool(st.secrets.get("auth"))
    except Exception:
        return False


def _require_identity() -> AuthIdentity:
    """可信登录态是业务用户ID的唯一来源。"""
    if not _oidc_configured():
        try:
            return resolve_demo_identity()
        except IdentityError as exc:
            st.error(str(exc), icon=":material/person_off:")
            st.stop()

    if not bool(getattr(st.user, "is_logged_in", False)):
        st.title("登录家电客服工作台", icon=":material/lock:")
        st.caption("使用组织配置的单点登录账号访问个人订单和使用报告。")
        if st.button(
            "使用单点登录",
            icon=":material/login:",
            type="primary",
        ):
            provider = os.getenv("OIDC_PROVIDER", "").strip()
            if provider:
                st.login(provider)
            else:
                st.login()
        st.stop()

    email = str(getattr(st.user, "email", "") or "")
    try:
        return resolve_oidc_identity(
            email,
            auth_conf["oidc_email_to_user_id"],
        )
    except IdentityError as exc:
        st.error(str(exc), icon=":material/person_off:")
        if st.button("退出登录", icon=":material/logout:"):
            st.logout()
        st.stop()


@st.cache_resource(show_spinner=False)
def _get_agent():
    # 延迟导入，保证没有 API Key 时页面仍能打开并显示清晰提示。
    from agent.react_agent import ReactAgent

    return ReactAgent()


def _reset_conversation() -> None:
    st.session_state.messages = []
    st.session_state.pending_prompt = None
    # 更换 key 可以安全清空 st.pills 的选择，避免旧选择在 rerun 时再次提交。
    st.session_state.suggestion_epoch += 1


def _queue_suggestion(widget_key: str) -> None:
    """快捷问题只写入待处理队列，由下一次 rerun 统一提交。"""
    selected = st.session_state.get(widget_key)
    if not selected:
        return
    st.session_state.pending_prompt = SUGGESTIONS[selected]
    # 回调中更换下次渲染的 widget key，旧选择不会残留并重复触发。
    st.session_state.suggestion_epoch += 1


def _shorten(text: str, limit: int = 140) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else f"{compact[:limit]}..."


def _render_trace(trace: list[dict[str, Any]]) -> None:
    if not trace:
        return
    with st.expander(f"执行轨迹 · {len(trace)} 条", icon=":material/account_tree:"):
        for item in trace:
            label = item.get("label", item.get("name", "步骤"))
            if item.get("kind") == "tool_result":
                st.markdown(f"- **{label}**：{item.get('summary') or '已完成'}")
            else:
                st.markdown(f"- 调用 **{label}**")


def _process_prompt(prompt: str, user_id: str, city: str, report_month: str) -> None:
    st.session_state.is_running = True
    prior_history = list(st.session_state.messages)
    st.session_state.messages.append({"role": "user", "content": prompt})

    try:
        with st.chat_message("user", avatar=":material/person:"):
            st.markdown(prompt)

        with st.chat_message("assistant", avatar=":material/support_agent:"):
            trace: list[dict[str, Any]] = []
            status = st.status(":shimmer[正在分析问题]", type="compact")
            # 这个占位符位于 status 之后，回答不会被折叠进执行轨迹。
            answer_slot = st.empty()

            with status:
                try:
                    events = _get_agent().stream_events(
                        prompt,
                        history=prior_history,
                        context={
                            "user_id": user_id,
                            "city": city,
                            "report_month": report_month,
                        },
                    )
                    answer = consume_agent_events(
                        events,
                        trace=trace,
                        status=status,
                        render_answer=answer_slot.markdown,
                        get_label=lambda name: TOOL_LABELS.get(
                            name,
                            name or "未知工具",
                        ),
                        shorten=_shorten,
                    )
                except Exception as exc:
                    status.update(label="执行失败", state="error")
                    answer = "本次请求没有成功完成，请稍后重试。"
                    answer_slot.error(
                        "Agent 执行失败。请检查 DASHSCOPE_API_KEY、网络连接和日志后再试。"
                        f"\n\n错误摘要：{_shorten(str(exc), 180)}",
                        icon=":material/error:",
                    )

            if not answer:
                answer = "没有生成有效回答，请换一种问法后重试。"
                answer_slot.markdown(answer)

            st.session_state.messages.append(
                {
                    "role": "assistant",
                    "content": answer,
                    "trace": trace,
                }
            )
    finally:
        st.session_state.is_running = False


_load_local_secrets()
identity = _require_identity()
profile = get_user_profile(identity.user_id)
if profile is None:
    st.error("登录身份没有对应的用户画像。", icon=":material/person_off:")
    st.stop()

st.session_state.setdefault("messages", [])
st.session_state.setdefault("is_running", False)
st.session_state.setdefault("pending_prompt", None)
st.session_state.setdefault("suggestion_epoch", 0)

months = get_available_months()

with st.sidebar:
    st.header("当前身份", icon=":material/person:")
    if identity.source == "oidc":
        st.badge("OIDC 已登录", icon=":material/verified_user:", color="green")
        st.markdown(f"**{identity.display_name}**")
        st.caption(identity.email)
        if st.button("退出登录", icon=":material/logout:"):
            st.logout()
    else:
        st.badge("固定演示身份", icon=":material/science:", color="orange")
        st.markdown(f"**{identity.display_name}**")
        st.caption("演示用户由服务端 DEMO_USER_ID 决定，页面不能切换")

    selected_month = st.selectbox(
        "报告月份",
        options=list(months),
        on_change=_reset_conversation,
        key="selected_month",
    )
    record = get_usage_record(identity.user_id, selected_month)

    with st.container(border=True, gap="small"):
        st.markdown(f"**{profile.display_name}**")
        st.caption(f"{profile.city} · {profile.area} · {profile.household}")
        st.write(f"在用产品：{profile.product}")
        if record:
            st.caption(f"{selected_month} 使用记录")
            st.markdown(normalize_newlines(record.efficiency).replace("\n", "  \n"))
            st.markdown(normalize_newlines(record.consumables).replace("\n", "  \n"))

    st.space("small")
    if _api_key_configured():
        st.badge("模型已配置", icon=":material/check_circle:", color="green")
    else:
        st.warning(
            "未配置 DASHSCOPE_API_KEY。复制 .env.example 为 .env 并填写 Key 后重启应用。",
            icon=":material/key:",
        )

    with st.container(horizontal=True):
        st.button(
            "清空会话",
            icon=":material/delete_sweep:",
            on_click=_reset_conversation,
        )

    if st.session_state.messages:
        export_text = json.dumps(
            {
                "user_id": identity.user_id,
                "report_month": selected_month,
                "messages": st.session_state.messages,
            },
            ensure_ascii=False,
            indent=2,
        )
        st.download_button(
            "导出会话",
            data=export_text.encode("utf-8"),
            file_name=f"conversation_{identity.user_id}_{selected_month}.json",
            mime="application/json",
            icon=":material/download:",
        )

    st.caption("演示数据范围：2025-01 至 2025-12")


st.title("清尘智服 · 家电客服 Agent", icon=":material/support_agent:")
st.caption("知识库问答 · 订单查询 · 用户隔离 · 执行轨迹 · 月度使用报告")

with st.container(border=True, gap="small"):
    st.subheader("当前服务对象", icon=":material/assignment_ind:")
    with st.container(horizontal=True):
        st.badge(profile.city, icon=":material/location_on:", color="blue")
        st.badge(profile.product, icon=":material/precision_manufacturing:", color="gray")
        st.badge(f"数据月份 {selected_month}", icon=":material/calendar_month:", color="green")
    st.caption(
        "可直接尝试知识库检索、故障排查或生成月度使用报告。"
        "报告生成会先读取当前用户与月份，再查询对应记录。"
    )

suggestion_key = f"suggestion_{st.session_state.suggestion_epoch}"
st.pills(
    "快捷问题",
    options=list(SUGGESTIONS),
    label_visibility="collapsed",
    key=suggestion_key,
    on_change=_queue_suggestion,
    args=(suggestion_key,),
    disabled=st.session_state.is_running,
)

for message in st.session_state.messages:
    role = message["role"]
    avatar = ":material/person:" if role == "user" else ":material/support_agent:"
    with st.chat_message(role, avatar=avatar):
        st.markdown(message["content"])
        if role == "assistant":
            _render_trace(message.get("trace", []))

input_prompt = st.chat_input(
    "询问产品使用、故障排查、订单状态，或生成月度使用报告",
    submit_mode="disable",
)

# 队列只在本次 rerun 消费一次，避免模型生成期间重复提交。
queued_prompt = st.session_state.pop("pending_prompt", None)
prompt = input_prompt or queued_prompt

if prompt:
    if not _api_key_configured():
        st.error(
            "缺少 DASHSCOPE_API_KEY，暂时无法调用模型。请先完成 .env 配置。",
            icon=":material/key_off:",
        )
    else:
        _process_prompt(
            prompt,
            user_id=identity.user_id,
            city=profile.city,
            report_month=selected_month,
        )
