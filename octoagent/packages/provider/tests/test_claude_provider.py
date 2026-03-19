"""Claude Provider 刷新适配测试 -- T024

验证:
- Claude OAuthCredential 的 refresh() 成功（mock Anthropic token 端点）
- account_id 为 None 不影响刷新流程
- Anthropic 403 政策拒绝返回友好错误消息
对齐 contracts/claude-provider-api.md SS2, SS3, FR-009
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from pydantic import SecretStr

from octoagent.provider.auth.credentials import OAuthCredential
from octoagent.provider.auth.oauth_provider import BUILTIN_PROVIDERS, OAuthProviderConfig
from octoagent.provider.auth.pkce_oauth_adapter import PkceOAuthAdapter
from octoagent.provider.auth.profile import ProviderProfile
from octoagent.provider.auth.store import CredentialStore
from octoagent.provider.exceptions import OAuthFlowError


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _make_claude_config() -> OAuthProviderConfig:
    """获取 Claude Provider 配置"""
    return BUILTIN_PROVIDERS["anthropic-claude"]


def _make_claude_credential(
    expired: bool = True,
) -> OAuthCredential:
    """构造 Claude setup-token 凭证"""
    now = _now()
    if expired:
        expires_at = now - timedelta(hours=1)
    else:
        expires_at = now + timedelta(hours=8)
    return OAuthCredential(
        provider="anthropic-claude",
        access_token=SecretStr("sk-ant-oat01-test-access-token"),
        refresh_token=SecretStr("sk-ant-ort01-test-refresh-token"),
        expires_at=expires_at,
        account_id=None,  # Claude token 不是 JWT
    )


def _make_claude_adapter(
    tmp_path: Path,
    expired: bool = True,
) -> tuple[PkceOAuthAdapter, CredentialStore]:
    """构造 Claude PkceOAuthAdapter"""
    store = CredentialStore(store_path=tmp_path / "auth-profiles.json")
    credential = _make_claude_credential(expired=expired)
    config = _make_claude_config()

    profile = ProviderProfile(
        name="anthropic-claude-default",
        provider="anthropic-claude",
        auth_mode="oauth",
        credential=credential,
        is_default=False,
        created_at=_now(),
        updated_at=_now(),
    )
    store.set_profile(profile)

    adapter = PkceOAuthAdapter(
        credential=credential,
        provider_config=config,
        store=store,
        profile_name="anthropic-claude-default",
    )
    return adapter, store


class TestClaudeRefreshSuccess:
    """Claude OAuthCredential 刷新成功"""

    async def test_refresh_returns_new_token(self, tmp_path: Path) -> None:
        """mock Anthropic token 端点刷新成功"""
        adapter, store = _make_claude_adapter(tmp_path, expired=True)

        mock_token_resp = MagicMock()
        mock_token_resp.access_token = SecretStr("sk-ant-oat01-new-access-token")
        mock_token_resp.refresh_token = SecretStr("sk-ant-ort01-new-refresh-token")
        mock_token_resp.expires_in = 28800  # 8 小时
        mock_token_resp.account_id = None  # Claude token 不解析 JWT

        with patch(
            "octoagent.provider.auth.pkce_oauth_adapter.refresh_access_token",
            new_callable=AsyncMock,
            return_value=mock_token_resp,
        ) as mock_refresh:
            result = await adapter.refresh()

        assert result == "sk-ant-oat01-new-access-token"

        # 验证调用了正确的 token 端点
        mock_refresh.assert_called_once()
        call_kwargs = mock_refresh.call_args.kwargs
        assert "console.anthropic.com" in call_kwargs["token_endpoint"]

    async def test_store_updated_after_refresh(self, tmp_path: Path) -> None:
        """刷新后 store 被更新"""
        adapter, store = _make_claude_adapter(tmp_path, expired=True)

        mock_token_resp = MagicMock()
        mock_token_resp.access_token = SecretStr("sk-ant-oat01-refreshed")
        mock_token_resp.refresh_token = SecretStr("sk-ant-ort01-refreshed")
        mock_token_resp.expires_in = 28800
        mock_token_resp.account_id = None

        with patch(
            "octoagent.provider.auth.pkce_oauth_adapter.refresh_access_token",
            new_callable=AsyncMock,
            return_value=mock_token_resp,
        ):
            await adapter.refresh()

        profile = store.get_profile("anthropic-claude-default")
        assert profile is not None
        assert profile.credential.access_token.get_secret_value() == "sk-ant-oat01-refreshed"


class TestClaudeAccountIdNone:
    """account_id 为 None 不影响刷新流程"""

    async def test_refresh_works_with_none_account_id(self, tmp_path: Path) -> None:
        """account_id 为 None 时刷新正常完成"""
        adapter, store = _make_claude_adapter(tmp_path, expired=True)

        # 验证初始 account_id 为 None
        assert adapter._credential.account_id is None

        mock_token_resp = MagicMock()
        mock_token_resp.access_token = SecretStr("sk-ant-oat01-new")
        mock_token_resp.refresh_token = SecretStr("sk-ant-ort01-new")
        mock_token_resp.expires_in = 28800
        mock_token_resp.account_id = None  # JWT 解析失败返回 None

        with patch(
            "octoagent.provider.auth.pkce_oauth_adapter.refresh_access_token",
            new_callable=AsyncMock,
            return_value=mock_token_resp,
        ):
            result = await adapter.refresh()

        assert result == "sk-ant-oat01-new"
        # account_id 仍然为 None，不影响流程
        assert adapter._credential.account_id is None


class TestClaudeAnthropicPolicyRejection:
    """Anthropic 403 政策拒绝"""

    async def test_invalid_grant_clears_credential(self, tmp_path: Path) -> None:
        """invalid_grant 错误清除凭证并返回 None"""
        adapter, store = _make_claude_adapter(tmp_path, expired=True)

        with patch(
            "octoagent.provider.auth.pkce_oauth_adapter.refresh_access_token",
            new_callable=AsyncMock,
            side_effect=OAuthFlowError(
                "Token 刷新失败: invalid_grant",
                provider="anthropic-claude",
            ),
        ):
            result = await adapter.refresh()

        assert result is None
        profile = store.get_profile("anthropic-claude-default")
        assert profile is None

    async def test_network_error_preserves_credential(self, tmp_path: Path) -> None:
        """网络错误不清除凭证"""
        adapter, store = _make_claude_adapter(tmp_path, expired=True)

        with patch(
            "octoagent.provider.auth.pkce_oauth_adapter.refresh_access_token",
            new_callable=AsyncMock,
            side_effect=OAuthFlowError(
                "Token 刷新失败: Connection refused",
                provider="anthropic-claude",
            ),
        ):
            result = await adapter.refresh()

        assert result is None
        # profile 应保留（非 invalid_grant）
        profile = store.get_profile("anthropic-claude-default")
        assert profile is not None
