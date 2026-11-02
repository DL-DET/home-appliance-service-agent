import pytest

from services.auth_service import (
    IdentityError,
    resolve_demo_identity,
    resolve_oidc_identity,
)


def test_demo_identity_comes_from_server_configuration():
    identity = resolve_demo_identity("1003")

    assert identity.user_id == "1003"
    assert identity.source == "demo"
    assert identity.email == "demo1003@example.com"


def test_oidc_email_mapping_is_case_insensitive():
    identity = resolve_oidc_identity(
        "DEMO1001@EXAMPLE.COM",
        {"demo1001@example.com": "1001"},
    )

    assert identity.user_id == "1001"
    assert identity.source == "oidc"


def test_unknown_oidc_email_is_rejected():
    with pytest.raises(IdentityError):
        resolve_oidc_identity(
            "unknown@example.com",
            {"demo1001@example.com": "1001"},
        )


def test_invalid_demo_user_is_rejected():
    with pytest.raises(IdentityError):
        resolve_demo_identity("9999")
