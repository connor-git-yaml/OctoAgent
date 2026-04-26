"""Feature 080 Phase 1：AuthResolver 单元测试。

覆盖：
- StaticApiKeyResolver：env 有值 / env 缺失 / force_refresh 重读 env
- OAuthResolver：每次 resolve 从 store 现读 profile（F2）
- OAuthResolver：force_refresh 走 PkceOAuthAdapter.refresh(force_refresh=True)
- OAuthResolver：profile 切账号后 resolve 立即用新 credential（F2 关键回归）
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import SecretStr

from octoagent.provider.auth.credentials import OAuthCredential
from octoagent.provider.auth.oauth_provider import OAuthProviderConfig
from octoagent.provider.auth.profile import ProviderProfile
from octoagent.provider.auth.store import CredentialStore
from octoagent.provider.auth_resolver import (
    OAuthResolver,
    ResolvedAuth,
    StaticApiKeyResolver,
)
from octoagent.provider.exceptions import (
    CredentialExpiredError,
    CredentialNotFoundError,
)
from octoagent.provider.refresh_coordinator import TokenRefreshCoordinator


# ─────────────────────── StaticApiKeyResolver ───────────────────────


@pytest.mark.asyncio
async def test_static_api_key_resolver_env_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_TEST_KEY", "sk-abc")
    resolver = StaticApiKeyResolver(env_var="MY_TEST_KEY")
    auth = await resolver.resolve()
    assert isinstance(auth, ResolvedAuth)
    assert auth.bearer_token == "sk-abc"
    assert auth.extra_headers == {}


@pytest.mark.asyncio
async def test_static_api_key_resolver_env_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MY_TEST_KEY", raising=False)
    resolver = StaticApiKeyResolver(env_var="MY_TEST_KEY")
    with pytest.raises(CredentialNotFoundError):
        await resolver.resolve()


@pytest.mark.asyncio
async def test_static_api_key_resolver_force_refresh_rereads_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """force_refresh 必须重读 env，让用户运行期改 .env 后下次 401 retry 立即生效。"""
    monkeypatch.setenv("MY_KEY", "old-token")
    resolver = StaticApiKeyResolver(env_var="MY_KEY")
    first = await resolver.resolve()
    assert first.bearer_token == "old-token"

    monkeypatch.setenv("MY_KEY", "new-token")
    refreshed = await resolver.force_refresh()
    assert refreshed is not None
    assert refreshed.bearer_token == "new-token"


@pytest.mark.asyncio
async def test_static_api_key_resolver_force_refresh_empty_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MY_KEY", raising=False)
    resolver = StaticApiKeyResolver(env_var="MY_KEY")
    assert await resolver.force_refresh() is None


@pytest.mark.asyncio
async def test_static_api_key_resolver_extra_headers_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ProviderClient 期待 resolver 把额外 headers 一起 resolve（如 anthropic-version）。"""
    monkeypatch.setenv("MY_KEY", "x")
    resolver = StaticApiKeyResolver(
        env_var="MY_KEY",
        extra_headers={"anthropic-version": "2023-06-01"},
    )
    auth = await resolver.resolve()
    assert auth.extra_headers == {"anthropic-version": "2023-06-01"}


# ────────────────────── OAuthResolver F2 回归 ──────────────────────


def _make_provider_config() -> OAuthProviderConfig:
    return OAuthProviderConfig(
        provider_id="openai-codex",
        display_name="OpenAI Codex",
        flow_type="auth_code_pkce",
        authorization_endpoint="https://auth.openai.com/oauth/authorize",
        token_endpoint="https://auth.openai.com/oauth/token",
        client_id="test-client",
        supports_refresh=True,
        extra_api_headers={"chatgpt-account-id": "{account_id}"},
    )


def _seed_profile(
    store: CredentialStore,
    *,
    name: str = "openai-codex-default",
    access_token: str = "at-original",
    account_id: str = "acc-original",
    expires_at: datetime | None = None,
) -> ProviderProfile:
    now = datetime.now(tz=UTC)
    profile = ProviderProfile(
        name=name,
        provider="openai-codex",
        auth_mode="oauth",
        credential=OAuthCredential(
            provider="openai-codex",
            access_token=SecretStr(access_token),
            refresh_token=SecretStr("rt"),
            expires_at=expires_at or (now + timedelta(hours=1)),
            account_id=account_id,
        ),
        is_default=True,
        created_at=now,
        updated_at=now,
    )
    store.set_profile(profile)
    return profile


@pytest.mark.asyncio
async def test_oauth_resolver_resolves_from_current_store_state(tmp_path: Path) -> None:
    store = CredentialStore(store_path=tmp_path / "auth-profiles.json")
    _seed_profile(store, access_token="at-1", account_id="acc-1")
    resolver = OAuthResolver(
        coordinator=TokenRefreshCoordinator(),
        provider_id="openai-codex",
        profile_name="openai-codex-default",
        provider_config=_make_provider_config(),
        credential_store=store,
        extra_headers_template={"chatgpt-account-id": "{account_id}"},
    )

    auth = await resolver.resolve()
    assert auth.bearer_token == "at-1"
    assert auth.extra_headers["chatgpt-account-id"] == "acc-1"


@pytest.mark.asyncio
async def test_oauth_resolver_picks_up_account_switch_without_restart(
    tmp_path: Path,
) -> None:
    """F2 关键回归：用户重新走 OAuth / 切账号 → store 里 profile 被替换 →
    OAuthResolver.resolve() 立即返回新 credential（不持有任何 stale 快照）。
    """
    store = CredentialStore(store_path=tmp_path / "auth-profiles.json")
    _seed_profile(store, access_token="at-old", account_id="acc-old")
    resolver = OAuthResolver(
        coordinator=TokenRefreshCoordinator(),
        provider_id="openai-codex",
        profile_name="openai-codex-default",
        provider_config=_make_provider_config(),
        credential_store=store,
        extra_headers_template={"chatgpt-account-id": "{account_id}"},
    )

    first = await resolver.resolve()
    assert first.bearer_token == "at-old"
    assert first.extra_headers["chatgpt-account-id"] == "acc-old"

    # 模拟用户在 Settings 页重新走 OAuth：profile 被替换为新 credential
    _seed_profile(store, access_token="at-NEW", account_id="acc-NEW")

    second = await resolver.resolve()
    assert second.bearer_token == "at-NEW"
    assert second.extra_headers["chatgpt-account-id"] == "acc-NEW"


@pytest.mark.asyncio
async def test_oauth_resolver_profile_missing_raises_not_found(tmp_path: Path) -> None:
    store = CredentialStore(store_path=tmp_path / "auth-profiles.json")
    resolver = OAuthResolver(
        coordinator=TokenRefreshCoordinator(),
        provider_id="openai-codex",
        profile_name="non-existent",
        provider_config=_make_provider_config(),
        credential_store=store,
    )
    with pytest.raises(CredentialNotFoundError):
        await resolver.resolve()


@pytest.mark.asyncio
async def test_oauth_resolver_force_refresh_calls_adapter_with_force(
    tmp_path: Path,
) -> None:
    store = CredentialStore(store_path=tmp_path / "auth-profiles.json")
    _seed_profile(store, access_token="at-current", account_id="acc-x")
    resolver = OAuthResolver(
        coordinator=TokenRefreshCoordinator(),
        provider_id="openai-codex",
        profile_name="openai-codex-default",
        provider_config=_make_provider_config(),
        credential_store=store,
    )

    # PkceOAuthAdapter.resolve(force_refresh=True) 内部走 refresh()。
    # 这里 mock adapter.resolve 验证 force kwarg 透传。
    with patch(
        "octoagent.provider.auth.pkce_oauth_adapter.PkceOAuthAdapter.resolve",
        new_callable=AsyncMock,
        return_value="at-after-refresh",
    ) as mock_resolve:
        result = await resolver.force_refresh()

    assert result is not None
    assert result.bearer_token == "at-after-refresh"
    # 调用方收到了 force=True 透传给 adapter.resolve
    mock_resolve.assert_called_once_with(force_refresh=True)


@pytest.mark.asyncio
async def test_oauth_resolver_force_refresh_returns_none_on_failure(
    tmp_path: Path,
) -> None:
    store = CredentialStore(store_path=tmp_path / "auth-profiles.json")
    _seed_profile(store)
    resolver = OAuthResolver(
        coordinator=TokenRefreshCoordinator(),
        provider_id="openai-codex",
        profile_name="openai-codex-default",
        provider_config=_make_provider_config(),
        credential_store=store,
    )

    with patch(
        "octoagent.provider.auth.pkce_oauth_adapter.PkceOAuthAdapter.resolve",
        new_callable=AsyncMock,
        side_effect=CredentialExpiredError("refresh failed"),
    ):
        result = await resolver.force_refresh()

    # F3 行为约束：force_refresh 失败返回 None（让 ProviderClient 抛回原 401）
    # 而不是 raise，因为调用方已经在 401 retry 路径上。
    assert result is None
