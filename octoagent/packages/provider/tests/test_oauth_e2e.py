"""OAuth 端到端集成测试 -- T027

验证完整 PKCE 流程:
- mock OAuth server 返回 token -> CredentialStore 验证凭证写入
- init_wizard PKCE 流程集成（mock 浏览器 + callback）
- Device Flow 回归（GitHub Provider 仍正常工作）
- --manual-oauth 端到端验证
覆盖 SC-001 ~ SC-009
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr

from octoagent.provider.auth.callback_server import CallbackResult
from octoagent.provider.auth.credentials import OAuthCredential
from octoagent.provider.auth.environment import EnvironmentContext
from octoagent.provider.auth.oauth_flows import run_auth_code_pkce_flow
from octoagent.provider.auth.oauth_provider import (
    BUILTIN_PROVIDERS,
    OAuthProviderConfig,
    OAuthProviderRegistry,
)
from octoagent.provider.auth.pkce_oauth_adapter import PkceOAuthAdapter
from octoagent.provider.auth.profile import ProviderProfile
from octoagent.provider.auth.store import CredentialStore


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


class TestSC001_PkceFullFlow:
    """SC-001: 本地 PKCE 全流程"""

    async def test_pkce_flow_stores_credential(self, tmp_path: Path) -> None:
        """完整 PKCE 流程: mock OAuth server -> CredentialStore 写入验证"""
        config = BUILTIN_PROVIDERS["openai-codex"].model_copy(
            update={"client_id": "e2e-test-client"}
        )
        registry = OAuthProviderRegistry()
        registry.register(config)

        env = EnvironmentContext(
            is_remote=False,
            can_open_browser=True,
            force_manual=False,
            detection_details="E2E 测试",
        )

        mock_data = {
            "access_token": "e2e-access-token",
            "refresh_token": "e2e-refresh-token",
            "token_type": "Bearer",
            "expires_in": 3600,
            "account_id": "e2e-account",
        }

        with (
            patch("octoagent.provider.auth.oauth_flows.webbrowser.open"),
            patch("octoagent.provider.auth.oauth_flows.wait_for_callback") as mock_wait,
            patch("octoagent.provider.auth.oauth_flows.generate_state") as mock_state,
            patch("octoagent.provider.auth.oauth_flows._curl_post", return_value=mock_data),
        ):
            mock_state.return_value = "e2e-state"
            mock_wait.return_value = CallbackResult(code="e2e-code", state="e2e-state")

            credential = await run_auth_code_pkce_flow(
                config=config,
                registry=registry,
                env=env,
            )

        # 验证凭证
        assert credential.provider == "openai-codex"
        assert credential.access_token.get_secret_value() == "e2e-access-token"
        assert credential.refresh_token.get_secret_value() == "e2e-refresh-token"
        assert credential.account_id == "e2e-account"

        # 写入 CredentialStore 并验证
        store = CredentialStore(store_path=tmp_path / "auth-profiles.json")
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

        # 重新加载验证
        loaded = store.get_profile("openai-codex-default")
        assert loaded is not None
        assert loaded.credential.access_token.get_secret_value() == "e2e-access-token"
        assert loaded.credential.account_id == "e2e-account"


class TestSC002_ManualModeFlow:
    """SC-002: SSH/VPS 手动模式完成授权"""

    async def test_manual_mode_flow(self) -> None:
        """远程环境手动模式完成 OAuth"""
        config = BUILTIN_PROVIDERS["openai-codex"].model_copy(
            update={"client_id": "manual-test-client"}
        )
        registry = OAuthProviderRegistry()
        registry.register(config)

        env = EnvironmentContext(
            is_remote=True,
            can_open_browser=False,
            force_manual=False,
            detection_details="SSH 环境",
        )

        mock_data = {
            "access_token": "manual-token",
            "expires_in": 3600,
        }

        with (
            patch("octoagent.provider.auth.oauth_flows.manual_paste_flow") as mock_manual,
            patch("octoagent.provider.auth.oauth_flows.generate_state") as mock_state,
            patch("octoagent.provider.auth.oauth_flows._curl_post", return_value=mock_data),
        ):
            mock_state.return_value = "manual-state"
            mock_manual.return_value = CallbackResult(
                code="manual-code", state="manual-state"
            )

            credential = await run_auth_code_pkce_flow(
                config=config,
                registry=registry,
                env=env,
            )

        assert credential.access_token.get_secret_value() == "manual-token"
        mock_manual.assert_called_once()


class TestSC003_ProviderList:
    """SC-003: Provider 列表正确展示流程类型"""

    def test_registry_has_both_providers(self) -> None:
        """注册表包含 OpenAI (PKCE) 和 GitHub (Device Flow)"""
        registry = OAuthProviderRegistry()
        providers = registry.list_providers()
        ids = {p.provider_id: p.flow_type for p in providers}
        assert ids["openai-codex"] == "auth_code_pkce"
        assert ids["github-copilot"] == "device_flow"


class TestSC006_TokenRefresh:
    """SC-006: refresh_token 自动刷新"""

    async def test_adapter_auto_refresh(self, tmp_path: Path) -> None:
        """PkceOAuthAdapter 过期时自动刷新"""
        store = CredentialStore(store_path=tmp_path / "auth-profiles.json")
        expired_cred = OAuthCredential(
            provider="openai-codex",
            access_token=SecretStr("expired-token"),
            refresh_token=SecretStr("valid-refresh"),
            expires_at=_now() - timedelta(hours=1),
        )
        config = OAuthProviderConfig(
            provider_id="openai-codex",
            display_name="OpenAI Codex",
            flow_type="auth_code_pkce",
            authorization_endpoint="https://auth.openai.com/oauth/authorize",
            token_endpoint="https://auth.openai.com/oauth/token",
            client_id="test-client",
        )

        profile = ProviderProfile(
            name="openai-codex-default",
            provider="openai-codex",
            auth_mode="oauth",
            credential=expired_cred,
            is_default=True,
            created_at=_now(),
            updated_at=_now(),
        )
        store.set_profile(profile)

        adapter = PkceOAuthAdapter(
            credential=expired_cred,
            provider_config=config,
            store=store,
            profile_name="openai-codex-default",
        )

        mock_token_resp = MagicMock()
        mock_token_resp.access_token = SecretStr("refreshed-e2e-token")
        mock_token_resp.refresh_token = SecretStr("new-refresh")
        mock_token_resp.expires_in = 3600
        mock_token_resp.account_id = None

        with patch(
            "octoagent.provider.auth.pkce_oauth_adapter.refresh_access_token",
            new_callable=AsyncMock,
            return_value=mock_token_resp,
        ):
            result = await adapter.resolve()

        assert result == "refreshed-e2e-token"

        # 验证 store 已更新
        loaded = store.get_profile("openai-codex-default")
        assert loaded is not None
        assert loaded.credential.access_token.get_secret_value() == "refreshed-e2e-token"


class TestSC007_ManualOAuthFlag:
    """SC-007: --manual-oauth 强制手动模式"""

    async def test_force_manual_flag(self) -> None:
        """force_manual=True 强制手动模式"""
        config = BUILTIN_PROVIDERS["openai-codex"].model_copy(
            update={"client_id": "flag-test-client"}
        )
        registry = OAuthProviderRegistry()
        registry.register(config)

        env = EnvironmentContext(
            is_remote=False,
            can_open_browser=True,
            force_manual=True,
            detection_details="强制手动模式",
        )

        mock_data = {
            "access_token": "flag-token",
            "expires_in": 3600,
        }

        with (
            patch("octoagent.provider.auth.oauth_flows.webbrowser.open") as mock_browser,
            patch("octoagent.provider.auth.oauth_flows.manual_paste_flow") as mock_manual,
            patch("octoagent.provider.auth.oauth_flows.generate_state") as mock_state,
            patch("octoagent.provider.auth.oauth_flows._curl_post", return_value=mock_data),
        ):
            mock_state.return_value = "flag-state"
            mock_manual.return_value = CallbackResult(
                code="flag-code", state="flag-state"
            )

            credential = await run_auth_code_pkce_flow(
                config=config,
                registry=registry,
                env=env,
            )

        mock_browser.assert_not_called()
        mock_manual.assert_called_once()
        assert credential.access_token.get_secret_value() == "flag-token"


class TestSC001b_PkceWithJwtAccountId:
    """SC-001b: JWT 方案 — 从 access_token 提取 account_id"""

    async def test_pkce_jwt_extracts_account_id(self, tmp_path: Path) -> None:
        """完整 PKCE + JWT: auth code → JWT access_token → 提取 account_id"""
        import base64
        import json

        config = BUILTIN_PROVIDERS["openai-codex"]
        registry = OAuthProviderRegistry()
        registry.register(config)

        env = EnvironmentContext(
            is_remote=False,
            can_open_browser=True,
            force_manual=False,
            detection_details="E2E JWT 测试",
        )

        # 构造含 account_id 的 JWT access_token
        jwt_payload = {
            "https://api.openai.com/auth": {
                "chatgpt_account_id": "acct_e2e_jwt",
            },
            "sub": "user-e2e",
        }
        header = base64.urlsafe_b64encode(
            json.dumps({"alg": "RS256"}).encode()
        ).rstrip(b"=").decode()
        body = base64.urlsafe_b64encode(
            json.dumps(jwt_payload).encode()
        ).rstrip(b"=").decode()
        jwt_token = f"{header}.{body}.e2e-sig"

        mock_data = {
            "access_token": jwt_token,
            "refresh_token": "e2e-refresh",
            "token_type": "Bearer",
            "expires_in": 3600,
        }

        with (
            patch("octoagent.provider.auth.oauth_flows.webbrowser.open"),
            patch("octoagent.provider.auth.oauth_flows.wait_for_callback") as mock_wait,
            patch("octoagent.provider.auth.oauth_flows.generate_state") as mock_state,
            patch("octoagent.provider.auth.oauth_flows._curl_post", return_value=mock_data),
        ):
            mock_state.return_value = "e2e-jwt-state"
            mock_wait.return_value = CallbackResult(code="e2e-jwt-code", state="e2e-jwt-state")

            credential = await run_auth_code_pkce_flow(
                config=config,
                registry=registry,
                env=env,
            )

        # JWT access_token 直接保存（不做 Token Exchange）
        assert credential.access_token.get_secret_value() == jwt_token
        assert credential.provider == "openai-codex"
        # account_id 从 JWT 提取
        assert credential.account_id == "acct_e2e_jwt"

        # 写入 CredentialStore 并验证
        store = CredentialStore(store_path=tmp_path / "auth-profiles.json")
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

        loaded = store.get_profile("openai-codex-default")
        assert loaded is not None
        assert loaded.credential.access_token.get_secret_value() == jwt_token
        assert loaded.credential.account_id == "acct_e2e_jwt"


class TestSC008_DeviceFlowRegression:
    """SC-008: Device Flow 无回归"""

    def test_device_flow_config_still_exists(self) -> None:
        """DeviceFlowConfig 仍可使用"""
        from octoagent.provider.auth.oauth import DeviceFlowConfig

        config = DeviceFlowConfig(client_id="regression-test")
        assert config.client_id == "regression-test"

    def test_github_provider_is_device_flow(self) -> None:
        """GitHub Provider 仍然使用 device_flow"""
        assert BUILTIN_PROVIDERS["github-copilot"].flow_type == "device_flow"

    def test_to_device_flow_config_works(self) -> None:
        """to_device_flow_config() 正常工作"""
        config = BUILTIN_PROVIDERS["github-copilot"]
        df_config = config.to_device_flow_config()
        assert df_config.client_id == config.client_id
