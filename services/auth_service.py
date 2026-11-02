"""把可信登录身份解析为业务用户。"""

from __future__ import annotations

import os
from dataclasses import dataclass

from services.customer_data import get_user_profile


class IdentityError(RuntimeError):
    """登录账号没有绑定有效业务用户。"""


@dataclass(frozen=True)
class AuthIdentity:
    user_id: str
    display_name: str
    email: str
    source: str


def _identity_for_user(user_id: str, *, source: str) -> AuthIdentity:
    profile = get_user_profile(user_id)
    if profile is None:
        raise IdentityError(f"账号没有绑定有效业务用户：{user_id}")
    return AuthIdentity(
        user_id=profile.user_id,
        display_name=profile.display_name,
        email=profile.email,
        source=source,
    )


def resolve_demo_identity(configured_user_id: str | None = None) -> AuthIdentity:
    """演示模式只接受服务端环境变量，不接受浏览器提交的用户ID。"""
    user_id = (configured_user_id or os.getenv("DEMO_USER_ID", "1001")).strip()
    return _identity_for_user(user_id, source="demo")


def resolve_oidc_identity(
    email: str,
    email_to_user_id: dict[str, str],
) -> AuthIdentity:
    """OIDC 身份必须先通过服务端邮箱映射，不能直接把邮箱拼成用户ID。"""
    normalized_email = (email or "").strip().casefold()
    if not normalized_email:
        raise IdentityError("登录信息中没有可用邮箱。")

    normalized_mapping = {
        str(source_email).strip().casefold(): str(user_id).strip()
        for source_email, user_id in email_to_user_id.items()
    }
    user_id = normalized_mapping.get(normalized_email)
    if not user_id:
        raise IdentityError("当前登录账号尚未绑定家电客服业务用户。")
    return _identity_for_user(user_id, source="oidc")
