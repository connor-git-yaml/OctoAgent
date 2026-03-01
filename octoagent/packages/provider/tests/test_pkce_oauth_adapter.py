"""PkceOAuthAdapter 单元测试 -- T019

验证:
- resolve() 返回 access_token
- resolve() 检测过期并自动调用 refresh()
- refresh() 成功: 请求 token 端点 + 更新内存凭证 + 回写 store + 发射 OAUTH_REFRESHED
- refresh() 失败 invalid_grant: 清除凭证、返回 None
- refresh() 无 refresh_token: 返回 None
- is_expired() 边界条件
对齐 FR-006
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr

from octoagent.provider.auth.credentials import OAuthCredential
from octoagent.provider.auth.oauth_provider import OAuthProviderConfig
from octoagent.provider.auth.pkce_oauth_adapter import PkceOAuthAdapter
from octoagent.provider.auth.profile import ProviderProfile
from octoagent.provider.auth.store import CredentialStore
from octoagent.provider.exceptions import CredentialExpiredError, OAuthFlowError


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _make_config() -> OAuthProviderConfig:
    return OAuthProviderConfig(
        provider_id="openai-codex",
        display_name="OpenAI Codex",
        flow_type="auth_code_pkce",
        authorization_endpoint="https://auth.openai.com/oauth/authorize",
        token_endpoint="https://auth.openai.com/oauth/token",
        client_id="test-client-id",
        supports_refresh=True,
    )


def _make_credential(
    expired: bool = False,
    has_refresh: bool = True,
) -> OAuthCredential:
    now = _now()
    if expired:
        expires_at = now - timedelta(hours=1)
    else:
        expires_at = now + timedelta(hours=1)
    return OAuthCredential(
        provider="openai-codex",
        access_token=SecretStr("test-access-token"),
        refresh_token=SecretStr("test-refresh-token" if has_refresh else ""),
        expires_at=expires_at,
        account_id="acc-123",
    )


def _make_adapter(
    tmp_path: Path,
    expired: bool = False,
    has_refresh: bool = True,
) -> tuple[PkceOAuthAdapter, CredentialStore]:
    store = CredentialStore(store_path=tmp_path / "auth-profiles.json")
    credential = _make_credential(expired=expired, has_refresh=has_refresh)
    config = _make_config()

    # 创建 profile
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


class TestResolve:
    """resolve() 行为"""

    async def test_returns_access_token(self, tmp_path: Path) -> None:
        """正常情况返回 access_token"""
        adapter, _ = _make_adapter(tmp_path, expired=False)
        result = await adapter.resolve()
        assert result == "test-access-token"

    async def test_expired_with_refresh_auto_refreshes(self, tmp_path: Path) -> None:
        """过期时自动刷新"""
        adapter, store = _make_adapter(tmp_path, expired=True, has_refresh=True)

        mock_token_resp = MagicMock()
        mock_token_resp.access_token = SecretStr("new-access-token")
        mock_token_resp.refresh_token = SecretStr("new-refresh-token")
        mock_token_resp.expires_in = 3600
        mock_token_resp.account_id = "acc-123"

        with patch(
            "octoagent.provider.auth.pkce_oauth_adapter.refresh_access_token",
            new_callable=AsyncMock,
            return_value=mock_token_resp,
        ):
            result = await adapter.resolve()
            assert result == "new-access-token"

    async def test_expired_no_refresh_raises(self, tmp_path: Path) -> None:
        """过期且无 refresh_token 抛出 CredentialExpiredError"""
        adapter, _ = _make_adapter(tmp_path, expired=True, has_refresh=False)
        with pytest.raises(CredentialExpiredError, match="过期"):
            await adapter.resolve()


class TestRefresh:
    """refresh() 行为"""

    async def test_success_updates_store(self, tmp_path: Path) -> None:
        """刷新成功后回写 store"""
        adapter, store = _make_adapter(tmp_path, expired=True, has_refresh=True)

        mock_token_resp = MagicMock()
        mock_token_resp.access_token = SecretStr("refreshed-token")
        mock_token_resp.refresh_token = SecretStr("refreshed-refresh")
        mock_token_resp.expires_in = 7200
        mock_token_resp.account_id = None

        with patch(
            "octoagent.provider.auth.pkce_oauth_adapter.refresh_access_token",
            new_callable=AsyncMock,
            return_value=mock_token_resp,
        ):
            result = await adapter.refresh()

        assert result == "refreshed-token"

        # 验证 store 已更新
        profile = store.get_profile("openai-codex-default")
        assert profile is not None
        assert profile.credential.access_token.get_secret_value() == "refreshed-token"

    async def test_success_emits_event(self, tmp_path: Path) -> None:
        """刷新成功发射 OAUTH_REFRESHED 事件"""
        store = CredentialStore(store_path=tmp_path / "auth-profiles.json")
        credential = _make_credential(expired=True, has_refresh=True)
        config = _make_config()

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

        mock_event_store = AsyncMock()
        mock_event_store.append = AsyncMock()

        adapter = PkceOAuthAdapter(
            credential=credential,
            provider_config=config,
            store=store,
            profile_name="openai-codex-default",
            event_store=mock_event_store,
        )

        mock_token_resp = MagicMock()
        mock_token_resp.access_token = SecretStr("new-token")
        mock_token_resp.refresh_token = SecretStr("new-refresh")
        mock_token_resp.expires_in = 3600
        mock_token_resp.account_id = None

        with patch(
            "octoagent.provider.auth.pkce_oauth_adapter.refresh_access_token",
            new_callable=AsyncMock,
            return_value=mock_token_resp,
        ):
            await adapter.refresh()

        # 验证事件被发射
        calls = mock_event_store.append.call_args_list
        event_types = [
            c.kwargs.get("event_type", c[0][1] if len(c[0]) > 1 else None)
            for c in calls
        ]
        assert "OAUTH_REFRESHED" in event_types

    async def test_invalid_grant_clears_credential(self, tmp_path: Path) -> None:
        """invalid_grant 错误清除凭证"""
        adapter, store = _make_adapter(tmp_path, expired=True, has_refresh=True)

        with patch(
            "octoagent.provider.auth.pkce_oauth_adapter.refresh_access_token",
            new_callable=AsyncMock,
            side_effect=OAuthFlowError("Token 刷新失败: invalid_grant", provider=""),
        ):
            result = await adapter.refresh()

        assert result is None
        # 验证 profile 已被移除
        profile = store.get_profile("openai-codex-default")
        assert profile is None

    async def test_no_refresh_token_returns_none(self, tmp_path: Path) -> None:
        """无 refresh_token 返回 None"""
        adapter, _ = _make_adapter(tmp_path, expired=True, has_refresh=False)
        result = await adapter.refresh()
        assert result is None


class TestIsExpired:
    """is_expired() 边界条件"""

    def test_not_expired(self, tmp_path: Path) -> None:
        """未过期"""
        adapter, _ = _make_adapter(tmp_path, expired=False)
        assert adapter.is_expired() is False

    def test_expired(self, tmp_path: Path) -> None:
        """已过期"""
        adapter, _ = _make_adapter(tmp_path, expired=True)
        assert adapter.is_expired() is True

    def test_exactly_at_boundary(self, tmp_path: Path) -> None:
        """刚好在过期边界"""
        store = CredentialStore(store_path=tmp_path / "auth-profiles.json")
        credential = OAuthCredential(
            provider="openai-codex",
            access_token=SecretStr("token"),
            expires_at=_now(),  # 刚好是当前时间
        )
        config = _make_config()
        adapter = PkceOAuthAdapter(
            credential=credential,
            provider_config=config,
            store=store,
            profile_name="test",
        )
        # 当前时间 >= expires_at，应判定为过期
        assert adapter.is_expired() is True
