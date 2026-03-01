"""Handler Chain -- 对齐 contracts/auth-adapter-api.md SS3, FR-010

Chain of Responsibility 模式的凭证解析链。
解析优先级: 显式 profile > credential store > 环境变量 > 默认值。
所有 handler 均无有效凭证时发出 CREDENTIAL_FAILED 事件 + 降级到 echo 模式（EC-4）。

003-b: 支持 PkceOAuthAdapter 注册，factory 通过闭包捕获
OAuthProviderConfig + CredentialStore + profile_name。
注册示例见 register_pkce_oauth_factory() 便捷函数。
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import TYPE_CHECKING, Literal

import structlog
from octoagent.core.models.enums import EventType
from pydantic import BaseModel, Field

from .adapter import AuthAdapter
from .credentials import Credential
from .events import EventStoreProtocol, emit_credential_event
from .store import CredentialStore

if TYPE_CHECKING:
    from .oauth_provider import OAuthProviderConfig

log = structlog.get_logger()

# Provider -> 环境变量名映射
_ENV_KEY_MAP: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


class HandlerChainResult(BaseModel):
    """Handler Chain 解析结果"""

    provider: str = Field(description="匹配的 Provider")
    credential_value: str = Field(description="解析到的凭证值")
    source: Literal["profile", "store", "env", "default"] = Field(
        description="凭证来源",
    )
    adapter: str = Field(description="匹配的 AuthAdapter 类名")

    # 路由覆盖（003-b JWT 方案）
    # JWT OAuth 路径需要绕过 Proxy，直接调用 Provider API
    # 非 OAuth 路径保持 None / {}，调用方使用默认 Proxy 路由
    api_base_url: str | None = Field(
        default=None,
        description="LLM API base URL 覆盖；None 表示使用调用方默认值（如 Proxy URL）",
    )
    extra_headers: dict[str, str] = Field(
        default_factory=dict,
        description="LLM API 调用附加 headers（如 chatgpt-account-id）",
    )


class HandlerChain:
    """处理器链 -- 按优先级解析凭证

    解析优先级: 显式 profile > credential store > 环境变量 > 默认值
    """

    def __init__(
        self,
        store: CredentialStore,
        env_prefix: str = "",
        event_store: EventStoreProtocol | None = None,
    ) -> None:
        """初始化

        Args:
            store: Credential Store 实例
            env_prefix: 环境变量前缀（如空字符串则使用标准命名）
            event_store: Event Store 实例（用于发射凭证事件）
        """
        self._store = store
        self._env_prefix = env_prefix
        self._event_store = event_store
        # provider -> adapter factory 映射
        self._factories: dict[str, Callable[[Credential], AuthAdapter]] = {}

    def register_adapter_factory(
        self,
        provider: str,
        factory: Callable[[Credential], AuthAdapter],
    ) -> None:
        """注册 adapter 工厂函数

        Args:
            provider: Provider 标识
            factory: 给定 Credential 返回对应 AuthAdapter 的工厂函数
        """
        self._factories[provider] = factory
        log.debug("adapter_factory_registered", provider=provider)

    async def resolve(
        self,
        provider: str | None = None,
        profile_name: str | None = None,
    ) -> HandlerChainResult:
        """解析凭证

        解析链:
        1. 如果指定 profile_name -> 从 store 获取指定 profile
        2. 如果指定 provider -> 从 store 获取该 provider 的默认/匹配 profile
        3. 尝试从环境变量解析（如 OPENAI_API_KEY、OPENROUTER_API_KEY）
        4. 所有来源均无有效凭证 -> 发出 CREDENTIAL_FAILED 事件 + 降级到 echo 模式

        Args:
            provider: 目标 Provider 标识
            profile_name: 显式指定的 profile 名称

        Returns:
            HandlerChainResult

        Raises:
            CredentialNotFoundError: 所有来源均无有效凭证且无法降级
        """
        # 优先级 1: 显式 profile
        if profile_name is not None:
            result = await self._try_profile(profile_name, source="profile")
            if result is not None:
                return result

        # 优先级 2: credential store（按 provider 匹配或 default profile）
        result = await self._try_store(provider)
        if result is not None:
            return result

        # 优先级 3: 环境变量
        if provider is not None:
            result = self._try_env(provider)
            if result is not None:
                return result

        # 优先级 4: 全部失败 -> 降级
        return await self._handle_fallback(provider)

    async def _try_profile(
        self,
        profile_name: str,
        source: Literal["profile", "store"] = "profile",
    ) -> HandlerChainResult | None:
        """尝试从指定 profile 解析凭证"""
        profile = self._store.get_profile(profile_name)
        if profile is None:
            log.debug("profile_not_found", profile_name=profile_name)
            return None

        adapter = self._create_adapter(profile.provider, profile.credential)
        if adapter is None:
            log.debug(
                "no_adapter_factory",
                provider=profile.provider,
                profile_name=profile_name,
            )
            return None

        try:
            if adapter.is_expired():
                # 尝试刷新
                refreshed = await adapter.refresh()
                if refreshed is not None:
                    await self._emit_loaded(
                        profile.provider,
                        profile.credential.type,
                        source,
                    )
                    return HandlerChainResult(
                        provider=profile.provider,
                        credential_value=refreshed,
                        source=source,
                        adapter=type(adapter).__name__,
                        **self._extract_routing(adapter),
                    )
                log.debug("credential_expired_no_refresh", provider=profile.provider)
                return None

            value = await adapter.resolve()
            await self._emit_loaded(
                profile.provider,
                profile.credential.type,
                source,
            )
            return HandlerChainResult(
                provider=profile.provider,
                credential_value=value,
                source=source,
                adapter=type(adapter).__name__,
                **self._extract_routing(adapter),
            )
        except Exception as exc:
            log.debug(
                "profile_resolve_failed",
                profile_name=profile_name,
                error=str(exc),
            )
            return None

    async def _try_store(
        self,
        provider: str | None,
    ) -> HandlerChainResult | None:
        """尝试从 credential store 解析凭证"""
        if provider is not None:
            # 按 provider 查找匹配的 profile
            profiles = self._store.list_profiles()
            for profile in profiles:
                if profile.provider == provider:
                    result = await self._try_profile(
                        profile.name,
                        source="store",
                    )
                    if result is not None:
                        return result

        # 尝试默认 profile
        default_profile = self._store.get_default_profile()
        if default_profile is not None and (
            provider is None or default_profile.provider == provider
        ):
            return await self._try_profile(
                default_profile.name,
                source="store",
            )

        return None

    def _try_env(self, provider: str) -> HandlerChainResult | None:
        """尝试从环境变量解析凭证"""
        # 确定环境变量名
        env_key = self._get_env_key(provider)
        value = os.environ.get(env_key)
        if value:
            log.debug("credential_from_env", provider=provider, env_key=env_key)
            return HandlerChainResult(
                provider=provider,
                credential_value=value,
                source="env",
                adapter="EnvVarAdapter",
            )
        return None

    async def _handle_fallback(
        self,
        provider: str | None,
    ) -> HandlerChainResult:
        """处理全部凭证均无效的降级逻辑

        发出 CREDENTIAL_FAILED 事件，返回 echo 模式默认值。
        """
        provider_str = provider or "unknown"

        # 发出 CREDENTIAL_FAILED 事件（EC-4）
        await emit_credential_event(
            event_store=self._event_store,
            event_type=EventType.CREDENTIAL_FAILED,
            provider=provider_str,
            credential_type="none",
            extra={"reason": "all_handlers_exhausted", "fallback": "echo"},
        )

        log.warning(
            "handler_chain_fallback_echo",
            provider=provider_str,
            reason="所有凭证来源均无有效凭证，降级到 echo 模式",
        )

        # 返回 echo 模式默认值
        return HandlerChainResult(
            provider="echo",
            credential_value="",
            source="default",
            adapter="EchoFallback",
        )

    @staticmethod
    def _extract_routing(adapter: AuthAdapter) -> dict:
        """从 adapter 提取路由覆盖信息（003-b JWT 方案）

        仅当 adapter 提供 get_api_base_url() / get_extra_headers() 时填充。
        非 OAuth adapter 返回空 dict，不影响 HandlerChainResult 默认值。
        """
        routing: dict = {}
        if hasattr(adapter, "get_api_base_url"):
            api_base = adapter.get_api_base_url()
            if api_base is not None:
                routing["api_base_url"] = api_base
        if hasattr(adapter, "get_extra_headers"):
            headers = adapter.get_extra_headers()
            if headers:
                routing["extra_headers"] = headers
        return routing

    def _create_adapter(
        self,
        provider: str,
        credential: Credential,
    ) -> AuthAdapter | None:
        """根据 provider 和 credential 创建 adapter"""
        factory = self._factories.get(provider)
        if factory is None:
            return None
        try:
            return factory(credential)
        except Exception as exc:
            log.warning(
                "adapter_factory_error",
                provider=provider,
                error=str(exc),
            )
            return None

    def _get_env_key(self, provider: str) -> str:
        """获取 provider 对应的环境变量名"""
        if self._env_prefix:
            return f"{self._env_prefix}{provider.upper()}_API_KEY"
        return _ENV_KEY_MAP.get(
            provider.lower(),
            f"{provider.upper()}_API_KEY",
        )

    async def _emit_loaded(
        self,
        provider: str,
        credential_type: str,
        source: str,
    ) -> None:
        """发射 CREDENTIAL_LOADED 事件"""
        await emit_credential_event(
            event_store=self._event_store,
            event_type=EventType.CREDENTIAL_LOADED,
            provider=provider,
            credential_type=credential_type,
            extra={"source": source},
        )

    def register_pkce_oauth_factory(
        self,
        provider: str,
        provider_config: OAuthProviderConfig,
        profile_name: str,
    ) -> None:
        """注册 PkceOAuthAdapter factory（003-b 便捷方法）

        factory 通过闭包捕获 OAuthProviderConfig + CredentialStore + profile_name。

        Args:
            provider: Provider canonical_id（如 "openai-codex"）
            provider_config: OAuthProviderConfig 实例
            profile_name: Profile 名称（用于 store 更新）
        """
        from .pkce_oauth_adapter import PkceOAuthAdapter

        store = self._store
        event_store = self._event_store

        def _factory(cred: Credential) -> AuthAdapter:
            from .credentials import OAuthCredential

            if not isinstance(cred, OAuthCredential):
                raise TypeError(f"PkceOAuthAdapter 需要 OAuthCredential，收到 {type(cred)}")
            return PkceOAuthAdapter(
                credential=cred,
                provider_config=provider_config,
                store=store,
                profile_name=profile_name,
                event_store=event_store,
            )

        self.register_adapter_factory(provider, _factory)
