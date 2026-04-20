"""PkceOAuthAdapter refresh_token_reused recovery -- Feature 078 Phase 3

场景：
- refresh 抛 invalid_grant，但 store 磁盘有更新的 refresh_token
  （并发场景：另一个 actor 刚刷新过）
- adapter 应从 store reload 后用新 refresh_token 再试 1 次
- 仍失败 → 原 fallback（remove_profile）
- store 磁盘无变化 → 不重试，直接 fallback
- 超时不等同 invalid_grant，保留 profile
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import SecretStr

from octoagent.provider.auth.credentials import OAuthCredential
from octoagent.provider.auth.oauth_flows import OAuthTokenResponse
from octoagent.provider.auth.oauth_provider import OAuthProviderConfig
from octoagent.provider.auth.pkce_oauth_adapter import PkceOAuthAdapter
from octoagent.provider.auth.profile import ProviderProfile
from octoagent.provider.auth.store import CredentialStore
from octoagent.provider.exceptions import OAuthFlowError, OAuthRefreshTimeoutError


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _config() -> OAuthProviderConfig:
    return OAuthProviderConfig(
        provider_id="openai-codex",
        display_name="OpenAI Codex",
        flow_type="auth_code_pkce",
        authorization_endpoint="https://auth.openai.com/oauth/authorize",
        token_endpoint="https://auth.openai.com/oauth/token",
        client_id="test-client-id",
        supports_refresh=True,
    )


def _make_expired_cred(refresh_token_value: str) -> OAuthCredential:
    return OAuthCredential(
        provider="openai-codex",
        access_token=SecretStr("old-at"),
        refresh_token=SecretStr(refresh_token_value),
        expires_at=_now() - timedelta(hours=1),
        account_id="acc-1",
    )


def _make_store_with_profile(
    tmp_path: Path, refresh_token_in_store: str
) -> tuple[CredentialStore, PkceOAuthAdapter]:
    store = CredentialStore(store_path=tmp_path / "auth-profiles.json")
    # store 里的 profile 是"最新的"（模拟另一个 actor 已刷新过）
    store_cred = OAuthCredential(
        provider="openai-codex",
        access_token=SecretStr("stored-at"),
        refresh_token=SecretStr(refresh_token_in_store),
        expires_at=_now() + timedelta(hours=1),
        account_id="acc-1",
    )
    profile = ProviderProfile(
        name="openai-codex-default",
        provider="openai-codex",
        auth_mode="oauth",
        credential=store_cred,
        is_default=True,
        created_at=_now(),
        updated_at=_now(),
    )
    store.set_profile(profile)

    # adapter 内存态是"旧的"refresh_token（模拟被 drive-by cached 的 adapter 实例）
    adapter_cred = _make_expired_cred("stale-refresh-token")
    adapter = PkceOAuthAdapter(
        credential=adapter_cred,
        provider_config=_config(),
        store=store,
        profile_name="openai-codex-default",
    )
    return store, adapter


@pytest.mark.asyncio
async def test_reused_recovery_succeeds_when_store_has_newer_refresh_token(
    tmp_path: Path,
) -> None:
    """store 磁盘上 refresh_token 比内存新 → invalid_grant 后 reload 重试成功。"""
    store, adapter = _make_store_with_profile(
        tmp_path, refresh_token_in_store="fresher-refresh-token"
    )

    call_log: list[str] = []

    async def _fake_refresh(
        token_endpoint: str,
        refresh_token: str,
        client_id: str,
        **kwargs,
    ) -> OAuthTokenResponse:
        call_log.append(refresh_token)
        if refresh_token == "stale-refresh-token":
            # 第一次用内存中的旧 token → invalid_grant
            raise OAuthFlowError("refresh failed: invalid_grant")
        # 第二次用 store reload 回来的新 token → 成功
        return OAuthTokenResponse(
            access_token=SecretStr("recovered-at"),
            refresh_token=SecretStr("even-newer-rt"),
            token_type="Bearer",
            expires_in=28800,
            account_id="acc-1",
        )

    with patch(
        "octoagent.provider.auth.pkce_oauth_adapter.refresh_access_token",
        side_effect=_fake_refresh,
    ):
        result = await adapter.refresh()

    assert result == "recovered-at"
    # 调了 2 次：stale → invalid_grant，fresh → success
    assert call_log == ["stale-refresh-token", "fresher-refresh-token"]


@pytest.mark.asyncio
async def test_reused_recovery_skipped_when_store_has_same_refresh_token(
    tmp_path: Path,
) -> None:
    """store 磁盘 refresh_token 与内存一致 → 不重试，返回 None。

    Codex adversarial review F1 之后：adapter 不再在 invalid_grant 时删 profile，
    把清理延迟到 callback 层的完整 waterfall 结束后，给 CLI adopt 留救援机会。
    """
    store, adapter = _make_store_with_profile(
        tmp_path, refresh_token_in_store="stale-refresh-token"
    )

    call_log: list[str] = []

    async def _fake_refresh(
        token_endpoint: str,
        refresh_token: str,
        client_id: str,
        **kwargs,
    ) -> OAuthTokenResponse:
        call_log.append(refresh_token)
        raise OAuthFlowError("refresh failed: invalid_grant")

    assert store.get_profile("openai-codex-default") is not None

    with patch(
        "octoagent.provider.auth.pkce_oauth_adapter.refresh_access_token",
        side_effect=_fake_refresh,
    ):
        result = await adapter.refresh()

    assert result is None
    # 仅调了 1 次（不重试）
    assert call_log == ["stale-refresh-token"]
    # 新契约：adapter 自身不删 profile，交给 callback 层在 CLI adopt 也失败后清理
    assert store.get_profile("openai-codex-default") is not None


@pytest.mark.asyncio
async def test_reused_recovery_gives_up_when_retry_also_fails(tmp_path: Path) -> None:
    """store 有新值但第二次 refresh 也失败 → adapter 仍不清 profile。

    Codex adversarial review F1 之后：invalid_grant 路径不再由 adapter 直接删 profile，
    profile 清理由上层 callback 在整条 waterfall（refresh + CLI adopt）全部失败后负责。
    """
    store, adapter = _make_store_with_profile(
        tmp_path, refresh_token_in_store="also-bad-rt"
    )

    call_log: list[str] = []

    async def _fake_refresh(
        token_endpoint: str,
        refresh_token: str,
        client_id: str,
        **kwargs,
    ) -> OAuthTokenResponse:
        call_log.append(refresh_token)
        raise OAuthFlowError("refresh failed: invalid_grant")

    with patch(
        "octoagent.provider.auth.pkce_oauth_adapter.refresh_access_token",
        side_effect=_fake_refresh,
    ):
        result = await adapter.refresh()

    assert result is None
    # 调了 2 次：第 1 次 stale，第 2 次 also-bad
    assert call_log == ["stale-refresh-token", "also-bad-rt"]
    # 新契约：adapter 不负责清理 profile（留给 callback 判断）
    assert store.get_profile("openai-codex-default") is not None


@pytest.mark.asyncio
async def test_timeout_preserves_profile(tmp_path: Path) -> None:
    """OAuthRefreshTimeoutError 不触发 remove_profile（保留给下次）。"""
    store = CredentialStore(store_path=tmp_path / "auth-profiles.json")
    cred = _make_expired_cred("some-rt")
    profile = ProviderProfile(
        name="openai-codex-default",
        provider="openai-codex",
        auth_mode="oauth",
        credential=cred,
        is_default=True,
        created_at=_now(),
        updated_at=_now(),
    )
    store.set_profile(profile)
    adapter = PkceOAuthAdapter(
        credential=cred,
        provider_config=_config(),
        store=store,
        profile_name="openai-codex-default",
    )

    async def _fake_timeout(*args, **kwargs):
        raise OAuthRefreshTimeoutError("Token 刷新超时（15s）", provider="openai-codex")

    with patch(
        "octoagent.provider.auth.pkce_oauth_adapter.refresh_access_token",
        side_effect=_fake_timeout,
    ):
        result = await adapter.refresh()

    assert result is None
    # profile 仍在（超时不能等同 invalid_grant 去清凭证）
    assert store.get_profile("openai-codex-default") is not None
