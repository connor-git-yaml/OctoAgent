"""Feature 078 Phase 4 —— /api/ops/auth/diagnostics 只读端点。

验证：
- 响应 JSON 结构符合 spec
- access_token / refresh_token / account_id 不出现在响应任何位置（脱敏验收）
- 不同 profile auth_mode 组合
- codex_cli_external_available 只对 openai-codex 有意义
- is_expired 使用 5 分钟 buffer gate
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from pydantic import SecretStr

from octoagent.gateway.routes.ops import _build_auth_diagnostics
from octoagent.provider.auth.credentials import ApiKeyCredential, OAuthCredential
from octoagent.provider.auth.profile import ProviderProfile


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _oauth_profile(
    *,
    name: str = "openai-codex-default",
    provider: str = "openai-codex",
    is_default: bool = True,
    expires_in_seconds: int = 8 * 3600,
    account_id: str | None = "acc-sensitive",
) -> ProviderProfile:
    return ProviderProfile(
        name=name,
        provider=provider,
        auth_mode="oauth",
        credential=OAuthCredential(
            provider=provider,
            access_token=SecretStr("SENSITIVE-AT-MUST-NOT-LEAK"),
            refresh_token=SecretStr("SENSITIVE-RT-MUST-NOT-LEAK"),
            expires_at=_now() + timedelta(seconds=expires_in_seconds),
            account_id=account_id,
        ),
        is_default=is_default,
        created_at=_now() - timedelta(days=30),
        updated_at=_now() - timedelta(minutes=30),
    )


def _api_key_profile() -> ProviderProfile:
    return ProviderProfile(
        name="siliconflow-default",
        provider="siliconflow",
        auth_mode="api_key",
        credential=ApiKeyCredential(
            provider="siliconflow",
            key=SecretStr("sk-TOTALLY-SECRET-KEY"),
        ),
        is_default=False,
        created_at=_now(),
        updated_at=_now(),
    )


def test_diagnostics_response_shape_for_oauth_profile() -> None:
    result = _build_auth_diagnostics(
        [_oauth_profile()],
        cli_available=False,
    )
    assert "profiles" in result
    assert len(result["profiles"]) == 1
    profile = result["profiles"][0]
    assert profile["name"] == "openai-codex-default"
    assert profile["provider"] == "openai-codex"
    assert profile["auth_mode"] == "oauth"
    assert profile["is_default"] is True
    assert isinstance(profile["expires_at"], str)
    assert isinstance(profile["expires_in_seconds"], int)
    assert profile["is_expired"] is False
    assert profile["codex_cli_external_available"] is False
    assert profile["last_refresh_at"] is not None


def test_diagnostics_marks_expired_within_buffer() -> None:
    """距过期 < 5 分钟 buffer → is_expired=True。"""
    # 3 分钟后过期 → 进入 REFRESH_BUFFER gate，应判 is_expired=True
    profile = _oauth_profile(expires_in_seconds=180)
    result = _build_auth_diagnostics([profile], cli_available=False)
    assert result["profiles"][0]["is_expired"] is True


def test_diagnostics_not_expired_well_ahead() -> None:
    profile = _oauth_profile(expires_in_seconds=8 * 3600)
    result = _build_auth_diagnostics([profile], cli_available=False)
    assert result["profiles"][0]["is_expired"] is False


def test_diagnostics_codex_cli_external_only_for_codex() -> None:
    """cli_available=True 时：仅 openai-codex profile 标记可用，其他 provider False。"""
    result = _build_auth_diagnostics(
        [
            _oauth_profile(provider="openai-codex", name="codex1"),
            _oauth_profile(provider="anthropic-claude", name="claude1"),
        ],
        cli_available=True,
    )
    entries = {p["name"]: p for p in result["profiles"]}
    assert entries["codex1"]["codex_cli_external_available"] is True
    assert entries["claude1"]["codex_cli_external_available"] is False


def test_diagnostics_api_key_profile_has_null_expiry() -> None:
    result = _build_auth_diagnostics(
        [_api_key_profile()],
        cli_available=False,
    )
    profile = result["profiles"][0]
    assert profile["auth_mode"] == "api_key"
    assert profile["expires_at"] is None
    assert profile["expires_in_seconds"] is None
    assert profile["is_expired"] is False


def test_diagnostics_never_leaks_sensitive_fields() -> None:
    """端到端脱敏验收：access_token / refresh_token / account_id 不出现在 JSON 任意位置。"""
    result = _build_auth_diagnostics(
        [
            _oauth_profile(account_id="acc-sensitive"),
            _api_key_profile(),
        ],
        cli_available=True,
    )
    blob = json.dumps(result, default=str)

    # 字面串扫描
    for sensitive in (
        "SENSITIVE-AT-MUST-NOT-LEAK",
        "SENSITIVE-RT-MUST-NOT-LEAK",
        "sk-TOTALLY-SECRET-KEY",
        "acc-sensitive",  # account_id 原值也不可出现
    ):
        assert sensitive not in blob, f"sensitive field {sensitive!r} leaked"
    # key name 字面量也不能出现（防止调用方通过 keys() 扫发现字段）
    for field in ("access_token", "refresh_token", "account_id"):
        assert field not in blob, f"field name {field!r} unexpectedly in response"


def test_diagnostics_empty_profiles_returns_empty_list() -> None:
    result = _build_auth_diagnostics([], cli_available=False)
    assert result == {"profiles": []}
