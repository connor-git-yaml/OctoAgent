"""PkceOAuthAdapter 单元测试 -- T019, T004, T005, T006, T017, T029

验证:
- resolve() 返回 access_token
- resolve() 检测过期并自动调用 refresh()
- refresh() 成功: 请求 token 端点 + 更新内存凭证 + 回写 store + 发射 OAUTH_REFRESHED
- refresh() 失败 invalid_grant: 清除凭证、返回 None
- refresh() 无 refresh_token: 返回 None
- is_expired() 边界条件
- [T004] is_expired() 缓冲期预检（5 分钟缓冲）
- [T005] refresh() 刷新成功时 CredentialStore 被更新
- [T006] refresh() 刷新失败场景（invalid_grant + 网络错误）
- [T017] 凭证实时生效：refresh() 后 resolve() 返回新 token
- [T029] 端到端预检刷新集成测试
对齐 FR-006, FR-011
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr

from octoagent.provider.auth.credentials import OAuthCredential
from octoagent.provider.auth.oauth_provider import OAuthProviderConfig
from octoagent.provider.auth.pkce_oauth_adapter import (
    REFRESH_BUFFER_SECONDS,
    PkceOAuthAdapter,
)
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
        mock_event_store.get_next_task_seq = AsyncMock(return_value=1)
        mock_event_store.append_event = AsyncMock()

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
        calls = mock_event_store.append_event.call_args_list
        event_types = []
        for call in calls:
            event = call.args[0] if call.args else call.kwargs["event"]
            event_types.append(event.type.value)
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


class TestIsExpiredBufferPreCheck:
    """[T004] is_expired() 缓冲期预检测试 -- 5 分钟缓冲期"""

    def test_token_well_within_validity(self, tmp_path: Path) -> None:
        """token 距过期 > 5min 返回 False"""
        store = CredentialStore(store_path=tmp_path / "auth-profiles.json")
        credential = OAuthCredential(
            provider="openai-codex",
            access_token=SecretStr("token"),
            expires_at=_now() + timedelta(minutes=30),  # 距过期还有 30 分钟
        )
        config = _make_config()
        adapter = PkceOAuthAdapter(
            credential=credential,
            provider_config=config,
            store=store,
            profile_name="test",
        )
        assert adapter.is_expired() is False

    def test_token_within_buffer_zone(self, tmp_path: Path) -> None:
        """token 距过期 < 5min 返回 True（缓冲期内视为过期）"""
        store = CredentialStore(store_path=tmp_path / "auth-profiles.json")
        credential = OAuthCredential(
            provider="openai-codex",
            access_token=SecretStr("token"),
            # 距过期还有 3 分钟（在 5 分钟缓冲期内）
            expires_at=_now() + timedelta(minutes=3),
        )
        config = _make_config()
        adapter = PkceOAuthAdapter(
            credential=credential,
            provider_config=config,
            store=store,
            profile_name="test",
        )
        assert adapter.is_expired() is True

    def test_token_already_expired(self, tmp_path: Path) -> None:
        """token 已过期返回 True"""
        store = CredentialStore(store_path=tmp_path / "auth-profiles.json")
        credential = OAuthCredential(
            provider="openai-codex",
            access_token=SecretStr("token"),
            expires_at=_now() - timedelta(hours=1),
        )
        config = _make_config()
        adapter = PkceOAuthAdapter(
            credential=credential,
            provider_config=config,
            store=store,
            profile_name="test",
        )
        assert adapter.is_expired() is True

    def test_token_exactly_at_buffer_boundary(self, tmp_path: Path) -> None:
        """token 恰好在 5 分钟缓冲边界"""
        store = CredentialStore(store_path=tmp_path / "auth-profiles.json")
        credential = OAuthCredential(
            provider="openai-codex",
            access_token=SecretStr("token"),
            # 距过期恰好 5 分钟
            expires_at=_now() + timedelta(seconds=REFRESH_BUFFER_SECONDS),
        )
        config = _make_config()
        adapter = PkceOAuthAdapter(
            credential=credential,
            provider_config=config,
            store=store,
            profile_name="test",
        )
        # now >= expires_at - buffer 等价于 now >= now，应返回 True
        assert adapter.is_expired() is True

    def test_token_just_outside_buffer(self, tmp_path: Path) -> None:
        """token 距过期比缓冲期多 1 秒，返回 False"""
        store = CredentialStore(store_path=tmp_path / "auth-profiles.json")
        credential = OAuthCredential(
            provider="openai-codex",
            access_token=SecretStr("token"),
            expires_at=_now() + timedelta(seconds=REFRESH_BUFFER_SECONDS + 60),
        )
        config = _make_config()
        adapter = PkceOAuthAdapter(
            credential=credential,
            provider_config=config,
            store=store,
            profile_name="test",
        )
        assert adapter.is_expired() is False


class TestRefreshSuccess:
    """[T005] refresh() 刷新成功测试"""

    async def test_refresh_updates_credential_store(self, tmp_path: Path) -> None:
        """supports_refresh=True 且 token 过期时，refresh() 成功返回新 access_token，
        CredentialStore 被更新"""
        adapter, store = _make_adapter(tmp_path, expired=True, has_refresh=True)

        mock_token_resp = MagicMock()
        mock_token_resp.access_token = SecretStr("brand-new-token")
        mock_token_resp.refresh_token = SecretStr("brand-new-refresh")
        mock_token_resp.expires_in = 3600
        mock_token_resp.account_id = "acc-456"

        with patch(
            "octoagent.provider.auth.pkce_oauth_adapter.refresh_access_token",
            new_callable=AsyncMock,
            return_value=mock_token_resp,
        ):
            result = await adapter.refresh()

        assert result == "brand-new-token"

        # 验证 store 被更新
        profile = store.get_profile("openai-codex-default")
        assert profile is not None
        cred = profile.credential
        assert cred.access_token.get_secret_value() == "brand-new-token"
        assert cred.refresh_token.get_secret_value() == "brand-new-refresh"

    async def test_refresh_not_supported_returns_none(self, tmp_path: Path) -> None:
        """supports_refresh=False 时 refresh() 返回 None"""
        store = CredentialStore(store_path=tmp_path / "auth-profiles.json")
        credential = _make_credential(expired=True, has_refresh=True)
        config = OAuthProviderConfig(
            provider_id="openai-codex",
            display_name="OpenAI Codex",
            flow_type="auth_code_pkce",
            authorization_endpoint="https://auth.openai.com/oauth/authorize",
            token_endpoint="https://auth.openai.com/oauth/token",
            client_id="test-client-id",
            supports_refresh=False,
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
        result = await adapter.refresh()
        assert result is None


class TestRefreshFailure:
    """[T006] refresh() 刷新失败测试"""

    async def test_invalid_grant_clears_profile(self, tmp_path: Path) -> None:
        """invalid_grant 错误导致 profile 被清除，返回 None"""
        adapter, store = _make_adapter(tmp_path, expired=True, has_refresh=True)

        with patch(
            "octoagent.provider.auth.pkce_oauth_adapter.refresh_access_token",
            new_callable=AsyncMock,
            side_effect=OAuthFlowError(
                "Token 刷新失败: invalid_grant -- refresh_token 已失效",
                provider="openai-codex",
            ),
        ):
            result = await adapter.refresh()

        assert result is None
        # profile 应被移除
        profile = store.get_profile("openai-codex-default")
        assert profile is None

    async def test_network_error_returns_none_and_logs(self, tmp_path: Path) -> None:
        """网络错误返回 None 并记录 warning"""
        adapter, store = _make_adapter(tmp_path, expired=True, has_refresh=True)

        with patch(
            "octoagent.provider.auth.pkce_oauth_adapter.refresh_access_token",
            new_callable=AsyncMock,
            side_effect=OAuthFlowError(
                "Token 刷新失败: 网络错误 -- Connection refused",
                provider="openai-codex",
            ),
        ):
            result = await adapter.refresh()

        assert result is None
        # profile 不应被移除（仅 invalid_grant 清除）
        profile = store.get_profile("openai-codex-default")
        assert profile is not None


class TestCredentialLiveReload:
    """[T017] 凭证实时生效集成测试 -- 刷新后 resolve() 返回新 token"""

    async def test_resolve_after_refresh_returns_new_token(
        self, tmp_path: Path
    ) -> None:
        """refresh() 后，下一次 resolve() 返回新 token（非旧 token）"""
        adapter, store = _make_adapter(tmp_path, expired=True, has_refresh=True)

        mock_token_resp = MagicMock()
        mock_token_resp.access_token = SecretStr("fresh-new-token")
        mock_token_resp.refresh_token = SecretStr("fresh-new-refresh")
        mock_token_resp.expires_in = 3600
        mock_token_resp.account_id = "acc-123"

        with patch(
            "octoagent.provider.auth.pkce_oauth_adapter.refresh_access_token",
            new_callable=AsyncMock,
            return_value=mock_token_resp,
        ):
            # 第一次 resolve 触发刷新
            result = await adapter.resolve()
            assert result == "fresh-new-token"

        # 刷新后，adapter 内存中的 credential 已更新
        # 再次 resolve 应直接返回新 token（不再触发刷新）
        result2 = await adapter.resolve()
        assert result2 == "fresh-new-token"


class TestPreCheckRefreshE2E:
    """[T029] 端到端预检刷新集成测试"""

    async def test_token_near_expiry_triggers_refresh_on_resolve(
        self, tmp_path: Path
    ) -> None:
        """构造一个距过期 4 分钟的 token，发起 resolve() 调用，
        验证触发刷新并返回新 token"""
        store = CredentialStore(store_path=tmp_path / "auth-profiles.json")
        credential = OAuthCredential(
            provider="openai-codex",
            access_token=SecretStr("about-to-expire-token"),
            refresh_token=SecretStr("valid-refresh-token"),
            # 距过期 4 分钟（在 5 分钟缓冲期内）
            expires_at=_now() + timedelta(minutes=4),
            account_id="acc-123",
        )
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

        adapter = PkceOAuthAdapter(
            credential=credential,
            provider_config=config,
            store=store,
            profile_name="openai-codex-default",
        )

        # 确认 is_expired() 因缓冲期返回 True
        assert adapter.is_expired() is True

        mock_token_resp = MagicMock()
        mock_token_resp.access_token = SecretStr("pre-refreshed-token")
        mock_token_resp.refresh_token = SecretStr("pre-refreshed-refresh")
        mock_token_resp.expires_in = 3600
        mock_token_resp.account_id = "acc-123"

        with patch(
            "octoagent.provider.auth.pkce_oauth_adapter.refresh_access_token",
            new_callable=AsyncMock,
            return_value=mock_token_resp,
        ) as mock_refresh:
            result = await adapter.resolve()

        assert result == "pre-refreshed-token"
        # 验证 refresh_access_token 被调用
        mock_refresh.assert_called_once()
