"""AuthAdapter 接口一致性 Contract 测试 -- T053 + T024

验证四种 Adapter 均正确实现 ABC 接口:
- resolve() 返回 str
- refresh() 返回 str | None
- is_expired() 返回 bool
- 均为 AuthAdapter 子类

T024: 新增 PkceOAuthAdapter 接口契约验证
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import SecretStr

from octoagent.provider.auth.adapter import AuthAdapter
from octoagent.provider.auth.api_key_adapter import ApiKeyAuthAdapter
from octoagent.provider.auth.codex_oauth_adapter import CodexOAuthAdapter
from octoagent.provider.auth.credentials import (
    ApiKeyCredential,
    OAuthCredential,
    TokenCredential,
)
from octoagent.provider.auth.oauth_provider import OAuthProviderConfig
from octoagent.provider.auth.pkce_oauth_adapter import PkceOAuthAdapter
from octoagent.provider.auth.setup_token_adapter import SetupTokenAuthAdapter
from octoagent.provider.auth.store import CredentialStore


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _make_api_key_adapter() -> ApiKeyAuthAdapter:
    """创建 API Key adapter"""
    return ApiKeyAuthAdapter(
        credential=ApiKeyCredential(
            provider="openai",
            key=SecretStr("sk-test-contract"),
        ),
    )


def _make_setup_token_adapter() -> SetupTokenAuthAdapter:
    """创建 Setup Token adapter"""
    now = _now()
    return SetupTokenAuthAdapter(
        credential=TokenCredential(
            provider="anthropic",
            token=SecretStr("sk-ant-oat01-contract-test"),
            acquired_at=now,
            expires_at=now + timedelta(hours=24),
        ),
    )


def _make_codex_oauth_adapter() -> CodexOAuthAdapter:
    """创建 Codex OAuth adapter"""
    now = _now()
    return CodexOAuthAdapter(
        credential=OAuthCredential(
            provider="openai-codex",
            access_token=SecretStr("oauth-access-token-contract"),
            expires_at=now + timedelta(hours=1),
        ),
    )


def _make_pkce_oauth_adapter(tmp_path: Path) -> PkceOAuthAdapter:
    """创建 PKCE OAuth adapter (T024)"""
    now = _now()
    store = CredentialStore(store_path=tmp_path / "contract-auth.json")
    return PkceOAuthAdapter(
        credential=OAuthCredential(
            provider="openai-codex",
            access_token=SecretStr("pkce-access-token-contract"),
            expires_at=now + timedelta(hours=1),
        ),
        provider_config=OAuthProviderConfig(
            provider_id="openai-codex",
            display_name="OpenAI Codex",
            flow_type="auth_code_pkce",
            authorization_endpoint="https://auth.openai.com/oauth/authorize",
            token_endpoint="https://auth.openai.com/oauth/token",
            client_id="test-client",
        ),
        store=store,
        profile_name="openai-codex-default",
    )


class TestAdapterSubclass:
    """三种 Adapter 均为 AuthAdapter 子类"""

    def test_api_key_is_auth_adapter(self) -> None:
        adapter = _make_api_key_adapter()
        assert isinstance(adapter, AuthAdapter)

    def test_setup_token_is_auth_adapter(self) -> None:
        adapter = _make_setup_token_adapter()
        assert isinstance(adapter, AuthAdapter)

    def test_codex_oauth_is_auth_adapter(self) -> None:
        adapter = _make_codex_oauth_adapter()
        assert isinstance(adapter, AuthAdapter)

    def test_pkce_oauth_is_auth_adapter(self, tmp_path: Path) -> None:
        """T024: PkceOAuthAdapter 是 AuthAdapter 子类"""
        adapter = _make_pkce_oauth_adapter(tmp_path)
        assert isinstance(adapter, AuthAdapter)


class TestResolveContract:
    """resolve() 返回 str"""

    async def test_api_key_resolve_returns_str(self) -> None:
        adapter = _make_api_key_adapter()
        result = await adapter.resolve()
        assert isinstance(result, str)
        assert len(result) > 0

    async def test_setup_token_resolve_returns_str(self) -> None:
        adapter = _make_setup_token_adapter()
        result = await adapter.resolve()
        assert isinstance(result, str)
        assert len(result) > 0

    async def test_codex_oauth_resolve_returns_str(self) -> None:
        adapter = _make_codex_oauth_adapter()
        result = await adapter.resolve()
        assert isinstance(result, str)
        assert len(result) > 0

    async def test_pkce_oauth_resolve_returns_str(self, tmp_path: Path) -> None:
        """T024: PkceOAuthAdapter.resolve() 返回 str"""
        adapter = _make_pkce_oauth_adapter(tmp_path)
        result = await adapter.resolve()
        assert isinstance(result, str)
        assert len(result) > 0


class TestRefreshContract:
    """refresh() 返回 str | None"""

    async def test_api_key_refresh_returns_none(self) -> None:
        adapter = _make_api_key_adapter()
        result = await adapter.refresh()
        assert result is None

    async def test_setup_token_refresh_returns_none(self) -> None:
        adapter = _make_setup_token_adapter()
        result = await adapter.refresh()
        assert result is None

    async def test_codex_oauth_refresh_returns_none(self) -> None:
        """M1 阶段返回 None"""
        adapter = _make_codex_oauth_adapter()
        result = await adapter.refresh()
        assert result is None

    async def test_pkce_oauth_refresh_returns_str_or_none(self, tmp_path: Path) -> None:
        """T024: PkceOAuthAdapter.refresh() 返回 str | None"""
        adapter = _make_pkce_oauth_adapter(tmp_path)
        # 未过期时返回 None（因为没有 refresh token 需要使用）
        # 此处验证方法存在且返回正确类型
        result = await adapter.refresh()
        assert result is None or isinstance(result, str)


class TestIsExpiredContract:
    """is_expired() 返回 bool"""

    def test_api_key_is_expired_returns_bool(self) -> None:
        adapter = _make_api_key_adapter()
        result = adapter.is_expired()
        assert isinstance(result, bool)
        # API Key 永不过期
        assert result is False

    def test_setup_token_is_expired_returns_bool(self) -> None:
        adapter = _make_setup_token_adapter()
        result = adapter.is_expired()
        assert isinstance(result, bool)
        # 新创建的 token 未过期
        assert result is False

    def test_codex_oauth_is_expired_returns_bool(self) -> None:
        adapter = _make_codex_oauth_adapter()
        result = adapter.is_expired()
        assert isinstance(result, bool)
        # 新创建的 oauth token 未过期
        assert result is False

    def test_pkce_oauth_is_expired_returns_bool(self, tmp_path: Path) -> None:
        """T024: PkceOAuthAdapter.is_expired() 返回 bool"""
        adapter = _make_pkce_oauth_adapter(tmp_path)
        result = adapter.is_expired()
        assert isinstance(result, bool)
        assert result is False


class TestMethodSignatures:
    """方法签名验证：确保所有 adapter 有三个必须方法"""

    @pytest.mark.parametrize(
        "adapter_factory",
        [
            _make_api_key_adapter,
            _make_setup_token_adapter,
            _make_codex_oauth_adapter,
        ],
        ids=["ApiKeyAuthAdapter", "SetupTokenAuthAdapter", "CodexOAuthAdapter"],
    )
    def test_has_required_methods(
        self,
        adapter_factory: callable,
    ) -> None:
        """每个 adapter 都有 resolve, refresh, is_expired 方法"""
        adapter = adapter_factory()
        assert hasattr(adapter, "resolve")
        assert callable(adapter.resolve)
        assert hasattr(adapter, "refresh")
        assert callable(adapter.refresh)
        assert hasattr(adapter, "is_expired")
        assert callable(adapter.is_expired)

    def test_pkce_oauth_has_required_methods(self, tmp_path: Path) -> None:
        """T024: PkceOAuthAdapter 有 resolve, refresh, is_expired 方法"""
        adapter = _make_pkce_oauth_adapter(tmp_path)
        assert hasattr(adapter, "resolve")
        assert callable(adapter.resolve)
        assert hasattr(adapter, "refresh")
        assert callable(adapter.refresh)
        assert hasattr(adapter, "is_expired")
        assert callable(adapter.is_expired)
