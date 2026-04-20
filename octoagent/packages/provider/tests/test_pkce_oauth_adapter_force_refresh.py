"""PkceOAuthAdapter.resolve(force_refresh=...) -- Feature 078 Phase 1

验证：
- resolve(force_refresh=True) 即使未过期也调用 refresh()（Bug B 修复）
- resolve(force_refresh=False) 在未过期时不调用 refresh()（回归既有行为）
- resolve(force_refresh=True) 且 refresh 返回 None 时抛 CredentialExpiredError
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import SecretStr

from octoagent.provider.auth.credentials import OAuthCredential
from octoagent.provider.auth.oauth_provider import OAuthProviderConfig
from octoagent.provider.auth.pkce_oauth_adapter import PkceOAuthAdapter
from octoagent.provider.auth.profile import ProviderProfile
from octoagent.provider.auth.store import CredentialStore
from octoagent.provider.exceptions import CredentialExpiredError


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _make_adapter(tmp_path: Path) -> tuple[PkceOAuthAdapter, CredentialStore]:
    store = CredentialStore(store_path=tmp_path / "auth-profiles.json")
    credential = OAuthCredential(
        provider="openai-codex",
        access_token=SecretStr("current-access-token"),
        refresh_token=SecretStr("current-refresh-token"),
        # 远期未过期（距 is_expired buffer 还有几十分钟）
        expires_at=_now() + timedelta(hours=1),
        account_id="acc-123",
    )
    config = OAuthProviderConfig(
        provider_id="openai-codex",
        display_name="OpenAI Codex",
        flow_type="auth_code_pkce",
        authorization_endpoint="https://auth.openai.com/oauth/authorize",
        token_endpoint="https://auth.openai.com/oauth/token",
        client_id="test-client-id",
        supports_refresh=True,
    )
    profile = ProviderProfile(
        name="openai-codex-default",
        provider="openai-codex",
        auth_mode="oauth",
        credential=credential,
        is_default=True,
        created_at=_now(),
        updated_at=_now(),
    )
    store.set_profile(profile)
    adapter = PkceOAuthAdapter(
        credential=credential,
        provider_config=config,
        store=store,
        profile_name="openai-codex-default",
    )
    return adapter, store


@pytest.mark.asyncio
async def test_force_refresh_true_triggers_refresh_even_when_not_expired(
    tmp_path: Path,
) -> None:
    """force_refresh=True：未过期也必须刷新（修复 Bug B）。"""
    adapter, _ = _make_adapter(tmp_path)
    assert adapter.is_expired() is False  # 前置：token 确实未过期

    with patch.object(adapter, "refresh", new=AsyncMock(return_value="new-token")) as m:
        token = await adapter.resolve(force_refresh=True)
    assert token == "new-token"
    m.assert_awaited_once()


@pytest.mark.asyncio
async def test_force_refresh_false_skips_refresh_when_not_expired(
    tmp_path: Path,
) -> None:
    """force_refresh=False（默认）：未过期时不刷新（回归既有行为）。"""
    adapter, _ = _make_adapter(tmp_path)
    assert adapter.is_expired() is False

    with patch.object(adapter, "refresh", new=AsyncMock(return_value="new-token")) as m:
        token = await adapter.resolve(force_refresh=False)
    assert token == "current-access-token"
    m.assert_not_awaited()


@pytest.mark.asyncio
async def test_force_refresh_true_raises_when_refresh_fails(tmp_path: Path) -> None:
    """force_refresh=True 且 refresh 返回 None → CredentialExpiredError。

    语义约束：调用方明确要求刷新（可能是 401 重试路径），如果连 refresh 都失败
    就必须让上层知道，不能悄悄回落到过期 token。
    """
    adapter, _ = _make_adapter(tmp_path)
    with patch.object(adapter, "refresh", new=AsyncMock(return_value=None)):
        with pytest.raises(CredentialExpiredError):
            await adapter.resolve(force_refresh=True)


@pytest.mark.asyncio
async def test_default_arg_preserves_legacy_signature(tmp_path: Path) -> None:
    """旧调用方 ``await adapter.resolve()`` 仍然工作（向后兼容）。"""
    adapter, _ = _make_adapter(tmp_path)
    token = await adapter.resolve()
    assert token == "current-access-token"
