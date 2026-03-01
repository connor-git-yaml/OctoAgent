"""Handler Chain 单元测试 -- T046

覆盖:
- 单 Provider 解析（显式 profile / store / env）
- 多 Provider 优先级
- 环境变量 fallback
- 全部失败降级 echo 模式
- adapter factory 注册
- CREDENTIAL_LOADED / CREDENTIAL_FAILED 事件发射
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from octoagent.provider.auth.adapter import AuthAdapter
from octoagent.provider.auth.api_key_adapter import ApiKeyAuthAdapter
from octoagent.provider.auth.chain import HandlerChain, HandlerChainResult
from octoagent.provider.auth.codex_oauth_adapter import CodexOAuthAdapter
from octoagent.provider.auth.credentials import (
    ApiKeyCredential,
    Credential,
    OAuthCredential,
    TokenCredential,
)
from octoagent.provider.auth.profile import ProviderProfile
from octoagent.provider.auth.setup_token_adapter import SetupTokenAuthAdapter
from octoagent.provider.auth.store import CredentialStore


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _make_api_key_profile(
    name: str = "test",
    provider: str = "openai",
    key: str = "sk-test-key-123",
    is_default: bool = False,
) -> ProviderProfile:
    """创建 API Key profile 辅助函数"""
    now = _now()
    return ProviderProfile(
        name=name,
        provider=provider,
        auth_mode="api_key",
        credential=ApiKeyCredential(provider=provider, key=SecretStr(key)),
        is_default=is_default,
        created_at=now,
        updated_at=now,
    )


def _make_token_profile(
    name: str = "anthropic-setup",
    provider: str = "anthropic",
    token: str = "sk-ant-oat01-test-token",
    hours_ago: int = 1,
    is_default: bool = False,
) -> ProviderProfile:
    """创建 Token profile 辅助函数"""
    now = _now()
    acquired = now - timedelta(hours=hours_ago)
    return ProviderProfile(
        name=name,
        provider=provider,
        auth_mode="token",
        credential=TokenCredential(
            provider=provider,
            token=SecretStr(token),
            acquired_at=acquired,
            expires_at=acquired + timedelta(hours=24),
        ),
        is_default=is_default,
        created_at=now,
        updated_at=now,
    )


def _make_expired_token_profile(
    name: str = "expired",
    provider: str = "anthropic",
) -> ProviderProfile:
    """创建已过期 Token profile"""
    now = _now()
    return ProviderProfile(
        name=name,
        provider=provider,
        auth_mode="token",
        credential=TokenCredential(
            provider=provider,
            token=SecretStr("sk-ant-oat01-expired"),
            acquired_at=now - timedelta(hours=48),
            expires_at=now - timedelta(hours=24),
        ),
        is_default=False,
        created_at=now,
        updated_at=now,
    )


def _api_key_factory(credential: Credential) -> AuthAdapter:
    """API Key adapter 工厂"""
    assert isinstance(credential, ApiKeyCredential)
    return ApiKeyAuthAdapter(credential)


def _setup_token_factory(credential: Credential) -> AuthAdapter:
    """Setup Token adapter 工厂"""
    assert isinstance(credential, TokenCredential)
    return SetupTokenAuthAdapter(credential)


def _codex_oauth_factory(credential: Credential) -> AuthAdapter:
    """Codex OAuth adapter 工厂"""
    assert isinstance(credential, OAuthCredential)
    return CodexOAuthAdapter(credential)


class MockEventStore:
    """用于验证事件发射的 mock Event Store"""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def append(
        self,
        task_id: str,
        event_type: str,
        actor_type: str,
        payload: dict[str, Any],
    ) -> None:
        self.events.append(
            {
                "task_id": task_id,
                "event_type": event_type,
                "actor_type": actor_type,
                "payload": payload,
            },
        )


class TestAdapterFactoryRegistration:
    """adapter factory 注册测试"""

    def test_register_single_factory(self, tmp_path: Path) -> None:
        """注册单个 factory"""
        store = CredentialStore(store_path=tmp_path / "auth.json")
        chain = HandlerChain(store=store)
        chain.register_adapter_factory("openai", _api_key_factory)
        assert "openai" in chain._factories

    def test_register_multiple_factories(self, tmp_path: Path) -> None:
        """注册多个 factory"""
        store = CredentialStore(store_path=tmp_path / "auth.json")
        chain = HandlerChain(store=store)
        chain.register_adapter_factory("openai", _api_key_factory)
        chain.register_adapter_factory("anthropic", _setup_token_factory)
        chain.register_adapter_factory("codex", _codex_oauth_factory)
        assert len(chain._factories) == 3

    def test_overwrite_factory(self, tmp_path: Path) -> None:
        """覆盖已有 factory"""
        store = CredentialStore(store_path=tmp_path / "auth.json")
        chain = HandlerChain(store=store)
        chain.register_adapter_factory("openai", _api_key_factory)
        chain.register_adapter_factory("openai", _setup_token_factory)
        assert chain._factories["openai"] is _setup_token_factory


class TestResolveExplicitProfile:
    """显式 profile 解析测试（优先级 1）"""

    async def test_resolve_by_profile_name(self, tmp_path: Path) -> None:
        """通过 profile_name 显式解析"""
        store = CredentialStore(store_path=tmp_path / "auth.json")
        profile = _make_api_key_profile(name="my-openai", provider="openai")
        store.set_profile(profile)

        chain = HandlerChain(store=store)
        chain.register_adapter_factory("openai", _api_key_factory)

        result = await chain.resolve(profile_name="my-openai")
        assert result.provider == "openai"
        assert result.credential_value == "sk-test-key-123"
        assert result.source == "profile"
        assert result.adapter == "ApiKeyAuthAdapter"

    async def test_explicit_profile_takes_priority(self, tmp_path: Path) -> None:
        """显式 profile 优先于 store 默认"""
        store = CredentialStore(store_path=tmp_path / "auth.json")
        # 默认 profile
        default_profile = _make_api_key_profile(
            name="default-openai",
            provider="openai",
            key="sk-default-key",
            is_default=True,
        )
        store.set_profile(default_profile)
        # 显式 profile
        explicit_profile = _make_api_key_profile(
            name="explicit-openai",
            provider="openai",
            key="sk-explicit-key",
        )
        store.set_profile(explicit_profile)

        chain = HandlerChain(store=store)
        chain.register_adapter_factory("openai", _api_key_factory)

        result = await chain.resolve(
            provider="openai",
            profile_name="explicit-openai",
        )
        assert result.credential_value == "sk-explicit-key"
        assert result.source == "profile"

    async def test_nonexistent_profile_falls_through(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """不存在的 profile 名称 -> 降级到后续来源"""
        store = CredentialStore(store_path=tmp_path / "auth.json")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-env-key")

        chain = HandlerChain(store=store)
        chain.register_adapter_factory("openai", _api_key_factory)

        result = await chain.resolve(
            provider="openai",
            profile_name="nonexistent",
        )
        # 应降级到环境变量
        assert result.source == "env"
        assert result.credential_value == "sk-env-key"


class TestResolveFromStore:
    """credential store 解析测试（优先级 2）"""

    async def test_resolve_by_provider_match(self, tmp_path: Path) -> None:
        """通过 provider 匹配 store 中的 profile"""
        store = CredentialStore(store_path=tmp_path / "auth.json")
        profile = _make_api_key_profile(
            name="openrouter-default",
            provider="openrouter",
            key="sk-or-v1-mykey",
        )
        store.set_profile(profile)

        chain = HandlerChain(store=store)
        chain.register_adapter_factory("openrouter", _api_key_factory)

        result = await chain.resolve(provider="openrouter")
        assert result.provider == "openrouter"
        assert result.credential_value == "sk-or-v1-mykey"
        assert result.source == "store"

    async def test_resolve_default_profile(self, tmp_path: Path) -> None:
        """无 provider 指定时使用默认 profile"""
        store = CredentialStore(store_path=tmp_path / "auth.json")
        profile = _make_api_key_profile(
            name="default",
            provider="openai",
            key="sk-default-123",
            is_default=True,
        )
        store.set_profile(profile)

        chain = HandlerChain(store=store)
        chain.register_adapter_factory("openai", _api_key_factory)

        result = await chain.resolve()
        assert result.provider == "openai"
        assert result.credential_value == "sk-default-123"
        assert result.source == "store"

    async def test_empty_store_falls_through(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """空 store -> 降级到环境变量"""
        store = CredentialStore(store_path=tmp_path / "auth.json")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-env-fallback")

        chain = HandlerChain(store=store)
        chain.register_adapter_factory("openai", _api_key_factory)

        result = await chain.resolve(provider="openai")
        assert result.source == "env"
        assert result.credential_value == "sk-env-fallback"

    async def test_expired_credential_skipped(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """过期凭证跳过，降级到环境变量"""
        store = CredentialStore(store_path=tmp_path / "auth.json")
        profile = _make_expired_token_profile(
            name="expired-token",
            provider="anthropic",
        )
        store.set_profile(profile)

        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env-key")

        chain = HandlerChain(store=store)
        chain.register_adapter_factory("anthropic", _setup_token_factory)

        result = await chain.resolve(provider="anthropic")
        assert result.source == "env"
        assert result.credential_value == "sk-ant-env-key"


class TestResolveFromEnv:
    """环境变量 fallback 测试（优先级 3）"""

    async def test_env_fallback_openai(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """OPENAI_API_KEY 环境变量回退"""
        store = CredentialStore(store_path=tmp_path / "auth.json")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-env-openai")

        chain = HandlerChain(store=store)
        result = await chain.resolve(provider="openai")
        assert result.source == "env"
        assert result.credential_value == "sk-env-openai"
        assert result.adapter == "EnvVarAdapter"

    async def test_env_fallback_openrouter(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """OPENROUTER_API_KEY 环境变量回退"""
        store = CredentialStore(store_path=tmp_path / "auth.json")
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-env")

        chain = HandlerChain(store=store)
        result = await chain.resolve(provider="openrouter")
        assert result.source == "env"
        assert result.credential_value == "sk-or-env"

    async def test_env_custom_prefix(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """自定义环境变量前缀"""
        store = CredentialStore(store_path=tmp_path / "auth.json")
        monkeypatch.setenv("MYAPP_OPENAI_API_KEY", "sk-custom-prefix")

        chain = HandlerChain(store=store, env_prefix="MYAPP_")
        result = await chain.resolve(provider="openai")
        assert result.source == "env"
        assert result.credential_value == "sk-custom-prefix"

    async def test_unknown_provider_env(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """未知 provider 使用默认 {PROVIDER}_API_KEY 格式"""
        store = CredentialStore(store_path=tmp_path / "auth.json")
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek")

        chain = HandlerChain(store=store)
        result = await chain.resolve(provider="deepseek")
        assert result.source == "env"
        assert result.credential_value == "sk-deepseek"

    async def test_empty_env_not_matched(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """空环境变量不匹配"""
        store = CredentialStore(store_path=tmp_path / "auth.json")
        monkeypatch.setenv("OPENAI_API_KEY", "")

        chain = HandlerChain(store=store)
        result = await chain.resolve(provider="openai")
        # 空字符串视为无值，降级到 echo
        assert result.source == "default"
        assert result.provider == "echo"


class TestFallbackEchoMode:
    """全部失败降级 echo 模式测试（EC-4）"""

    async def test_all_sources_exhausted_fallback_echo(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """所有来源均无有效凭证 -> 降级到 echo 模式"""
        store = CredentialStore(store_path=tmp_path / "auth.json")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        chain = HandlerChain(store=store)
        result = await chain.resolve(provider="openai")
        assert result.provider == "echo"
        assert result.credential_value == ""
        assert result.source == "default"
        assert result.adapter == "EchoFallback"

    async def test_no_provider_no_store_fallback(
        self,
        tmp_path: Path,
    ) -> None:
        """不指定 provider 且 store 为空 -> echo 降级"""
        store = CredentialStore(store_path=tmp_path / "auth.json")
        chain = HandlerChain(store=store)
        result = await chain.resolve()
        assert result.provider == "echo"
        assert result.source == "default"

    async def test_fallback_emits_credential_failed_event(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """降级时发出 CREDENTIAL_FAILED 事件"""
        store = CredentialStore(store_path=tmp_path / "auth.json")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        event_store = MockEventStore()
        chain = HandlerChain(store=store, event_store=event_store)
        await chain.resolve(provider="openai")

        assert len(event_store.events) == 1
        event = event_store.events[0]
        assert event["event_type"] == "CREDENTIAL_FAILED"
        assert event["payload"]["provider"] == "openai"
        assert event["payload"]["reason"] == "all_handlers_exhausted"
        assert event["payload"]["fallback"] == "echo"

    async def test_no_factory_registered_fallback(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """store 有 profile 但无对应 factory -> 降级"""
        store = CredentialStore(store_path=tmp_path / "auth.json")
        profile = _make_api_key_profile(
            name="openai-profile",
            provider="openai",
        )
        store.set_profile(profile)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        chain = HandlerChain(store=store)
        # 不注册 factory
        result = await chain.resolve(provider="openai")
        assert result.provider == "echo"
        assert result.source == "default"


class TestMultiProviderPriority:
    """多 Provider 优先级测试"""

    async def test_provider_filter(self, tmp_path: Path) -> None:
        """指定 provider 只匹配对应 profile"""
        store = CredentialStore(store_path=tmp_path / "auth.json")

        openai_profile = _make_api_key_profile(
            name="openai-p",
            provider="openai",
            key="sk-openai-123",
        )
        openrouter_profile = _make_api_key_profile(
            name="openrouter-p",
            provider="openrouter",
            key="sk-or-v1-456",
        )
        store.set_profile(openai_profile)
        store.set_profile(openrouter_profile)

        chain = HandlerChain(store=store)
        chain.register_adapter_factory("openai", _api_key_factory)
        chain.register_adapter_factory("openrouter", _api_key_factory)

        result = await chain.resolve(provider="openrouter")
        assert result.provider == "openrouter"
        assert result.credential_value == "sk-or-v1-456"

    async def test_default_profile_provider_mismatch(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """默认 profile provider 不匹配请求的 provider -> 跳过"""
        store = CredentialStore(store_path=tmp_path / "auth.json")

        # 默认 profile 是 openai
        default_profile = _make_api_key_profile(
            name="default-openai",
            provider="openai",
            key="sk-openai-default",
            is_default=True,
        )
        store.set_profile(default_profile)

        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env")

        chain = HandlerChain(store=store)
        chain.register_adapter_factory("openai", _api_key_factory)

        # 请求 anthropic，默认 profile 是 openai -> 不匹配 -> env fallback
        result = await chain.resolve(provider="anthropic")
        assert result.source == "env"
        assert result.credential_value == "sk-ant-env"


class TestCredentialEvents:
    """凭证事件发射测试"""

    async def test_loaded_event_on_profile_resolve(
        self,
        tmp_path: Path,
    ) -> None:
        """成功解析时发出 CREDENTIAL_LOADED 事件"""
        store = CredentialStore(store_path=tmp_path / "auth.json")
        profile = _make_api_key_profile(
            name="test",
            provider="openai",
            is_default=True,
        )
        store.set_profile(profile)

        event_store = MockEventStore()
        chain = HandlerChain(store=store, event_store=event_store)
        chain.register_adapter_factory("openai", _api_key_factory)

        await chain.resolve(provider="openai")

        assert len(event_store.events) == 1
        event = event_store.events[0]
        assert event["event_type"] == "CREDENTIAL_LOADED"
        assert event["payload"]["provider"] == "openai"
        assert event["payload"]["credential_type"] == "api_key"
        assert event["payload"]["source"] == "store"

    async def test_no_event_store_graceful(self, tmp_path: Path) -> None:
        """event_store 为 None 时不报错"""
        store = CredentialStore(store_path=tmp_path / "auth.json")
        profile = _make_api_key_profile(
            name="test",
            provider="openai",
            is_default=True,
        )
        store.set_profile(profile)

        chain = HandlerChain(store=store, event_store=None)
        chain.register_adapter_factory("openai", _api_key_factory)

        # 不应抛出异常
        result = await chain.resolve(provider="openai")
        assert result.credential_value == "sk-test-key-123"


class TestHandlerChainResult:
    """HandlerChainResult 模型测试"""

    def test_model_creation(self) -> None:
        """模型创建"""
        result = HandlerChainResult(
            provider="openai",
            credential_value="sk-test",
            source="store",
            adapter="ApiKeyAuthAdapter",
        )
        assert result.provider == "openai"
        assert result.credential_value == "sk-test"
        assert result.source == "store"
        assert result.adapter == "ApiKeyAuthAdapter"

    def test_model_serialization(self) -> None:
        """模型序列化"""
        result = HandlerChainResult(
            provider="openai",
            credential_value="sk-test",
            source="env",
            adapter="EnvVarAdapter",
        )
        data = result.model_dump()
        assert data["source"] == "env"
        assert data["adapter"] == "EnvVarAdapter"

    def test_routing_defaults_empty(self) -> None:
        """路由字段默认值为空（不影响非 OAuth 路径）"""
        result = HandlerChainResult(
            provider="openai",
            credential_value="sk-test",
            source="store",
            adapter="ApiKeyAuthAdapter",
        )
        assert result.api_base_url is None
        assert result.extra_headers == {}

    def test_routing_fields_populated(self) -> None:
        """路由字段可显式设置"""
        result = HandlerChainResult(
            provider="openai-codex",
            credential_value="jwt-token",
            source="profile",
            adapter="PkceOAuthAdapter",
            api_base_url="https://chatgpt.com/backend-api",
            extra_headers={
                "chatgpt-account-id": "acct-123",
                "OpenAI-Beta": "responses=experimental",
            },
        )
        assert result.api_base_url == "https://chatgpt.com/backend-api"
        assert result.extra_headers["chatgpt-account-id"] == "acct-123"


class TestRoutingExtraction:
    """路由信息提取测试（003-b JWT 方案多认证隔离）"""

    async def test_api_key_no_routing(self, tmp_path: Path) -> None:
        """API Key adapter 不产生路由覆盖"""
        store = CredentialStore(store_path=tmp_path / "auth.json")
        profile = _make_api_key_profile(
            name="openai-key",
            provider="openai",
            is_default=True,
        )
        store.set_profile(profile)

        chain = HandlerChain(store=store)
        chain.register_adapter_factory("openai", _api_key_factory)

        result = await chain.resolve(provider="openai")
        assert result.api_base_url is None
        assert result.extra_headers == {}

    async def test_pkce_oauth_populates_routing(self, tmp_path: Path) -> None:
        """PkceOAuthAdapter 产生路由覆盖（api_base_url + extra_headers）"""
        from octoagent.provider.auth.oauth_provider import (
            BUILTIN_PROVIDERS,
            OAuthProviderConfig,
        )

        store = CredentialStore(store_path=tmp_path / "auth.json")
        now = _now()
        oauth_cred = OAuthCredential(
            provider="openai-codex",
            access_token=SecretStr("jwt-access-token"),
            refresh_token=SecretStr("refresh-token"),
            expires_at=now + timedelta(hours=1),
            account_id="acct-test-123",
        )
        profile = ProviderProfile(
            name="openai-codex-default",
            provider="openai-codex",
            auth_mode="oauth",
            credential=oauth_cred,
            is_default=True,
            created_at=now,
            updated_at=now,
        )
        store.set_profile(profile)

        config = BUILTIN_PROVIDERS["openai-codex"]
        chain = HandlerChain(store=store)
        chain.register_pkce_oauth_factory(
            provider="openai-codex",
            provider_config=config,
            profile_name="openai-codex-default",
        )

        result = await chain.resolve(provider="openai-codex")
        assert result.adapter == "PkceOAuthAdapter"
        assert result.credential_value == "jwt-access-token"
        # 路由覆盖
        assert result.api_base_url == "https://chatgpt.com/backend-api"
        assert result.extra_headers["chatgpt-account-id"] == "acct-test-123"
        assert result.extra_headers["OpenAI-Beta"] == "responses=experimental"
        assert result.extra_headers["originator"] == "octoagent"

    async def test_pkce_oauth_no_routing_when_no_api_base(
        self, tmp_path: Path
    ) -> None:
        """PkceOAuthAdapter 无 api_base_url 配置时不产生路由覆盖"""
        from octoagent.provider.auth.oauth_provider import OAuthProviderConfig

        store = CredentialStore(store_path=tmp_path / "auth.json")
        now = _now()
        oauth_cred = OAuthCredential(
            provider="custom-provider",
            access_token=SecretStr("custom-token"),
            refresh_token=SecretStr("custom-refresh"),
            expires_at=now + timedelta(hours=1),
        )
        profile = ProviderProfile(
            name="custom-default",
            provider="custom-provider",
            auth_mode="oauth",
            credential=oauth_cred,
            is_default=True,
            created_at=now,
            updated_at=now,
        )
        store.set_profile(profile)

        # 无 api_base_url 的自定义 Provider
        config = OAuthProviderConfig(
            provider_id="custom-provider",
            display_name="Custom Provider",
            flow_type="auth_code_pkce",
            authorization_endpoint="https://custom.com/auth",
            token_endpoint="https://custom.com/token",
            client_id="custom-client",
        )
        chain = HandlerChain(store=store)
        chain.register_pkce_oauth_factory(
            provider="custom-provider",
            provider_config=config,
            profile_name="custom-default",
        )

        result = await chain.resolve(provider="custom-provider")
        assert result.adapter == "PkceOAuthAdapter"
        assert result.api_base_url is None
        assert result.extra_headers == {}
