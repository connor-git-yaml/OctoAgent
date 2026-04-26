"""Feature 080 Phase 1：alias → ProviderClient 路由层。

按 ``model_alias`` 查 ``octoagent.yaml`` 找到对应的 ``ProviderEntry``，构造
``ProviderRuntime`` 和 ``ProviderClient``。**支持 task scope 解析锁定**，避免
任务进行中改 alias 导致 history 跨 provider 错乱（Codex review F1 修复）。

设计要点（v2 / Codex review 后）：
- **task scope 缓存（F1 修复）**：传入 ``task_scope`` 参数时，第一次解析后
  钉死本 task 的 (provider, model)；同 task 后续 resolve 命中缓存返回相同
  结果。新 task 才重新读 yaml。
  - 用户改 alias 后**新 task** 立即生效（解决 Feature 079 P3 痛点）
  - 进行中的 task 不受影响（不会发生非幂等工具重跑 / history 协议错乱）
- ``ProviderClient`` 仍可跨 task 共享（同一 provider/transport 的 HTTP client
  / extra_headers / extra_body 都是 stateless），但 OAuth credential 由
  OAuthResolver 每次 resolve 时从 store 现读（F2 修复）

后续 Phase 2/3/4 复用此 Router，无需扩展 API。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import structlog

from .auth.oauth_provider import BUILTIN_PROVIDERS
from .auth.store import CredentialStore
from .auth_resolver import AuthResolver, OAuthResolver, StaticApiKeyResolver
from .exceptions import CredentialError, CredentialNotFoundError
from .provider_client import ProviderClient
from .provider_runtime import ProviderRuntime
from .refresh_coordinator import TokenRefreshCoordinator
from .transport import ProviderTransport

log = structlog.get_logger()


@dataclass(frozen=True)
class ResolvedAlias:
    """alias 解析结果。task scope 缓存的最小单元。"""

    client: ProviderClient
    model_name: str
    provider_id: str


class ProviderRouter:
    """alias → (ProviderClient, model_name) 路由器。

    生命周期：单 Gateway 进程内单例（在 main.py lifespan 中创建）。

    缓存层级：
    1. ``_client_cache``：``provider_id`` → ``ProviderClient``，跨 task 共享
       （client 持有 stateless runtime；OAuth credential 在 resolver 内每次
       现读 store，所以共享 client 不会导致 stale 凭证）
    2. ``_task_alias_cache``：``(task_scope, model_alias)`` → ``ResolvedAlias``，
       任务级缓存，避免任务中途改 alias 跨 provider（F1）

    缓存清理：
    - ``invalidate_task(task_scope)`` 在 task 结束时调用清理
    - ``aclose()`` 在 Gateway shutdown 时关 http_client
    """

    def __init__(
        self,
        *,
        project_root: Path,
        credential_store: CredentialStore | None = None,
        coordinator: TokenRefreshCoordinator | None = None,
        event_store: Any | None = None,
        timeout_s: float = 60.0,
    ) -> None:
        self._project_root = project_root
        self._store = credential_store or CredentialStore()
        self._coord = coordinator or TokenRefreshCoordinator()
        self._event_store = event_store
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_s, connect=10.0),
        )
        self._client_cache: dict[str, ProviderClient] = {}
        self._task_alias_cache: dict[tuple[str, str], ResolvedAlias] = {}

    async def aclose(self) -> None:
        await self._http.aclose()

    def invalidate_task(self, task_scope: str) -> None:
        """task 结束 / failed 时调用，清理本 task 的 alias 锁定。"""
        keys_to_drop = [k for k in self._task_alias_cache if k[0] == task_scope]
        for key in keys_to_drop:
            self._task_alias_cache.pop(key, None)

    def invalidate_provider_client(self, provider_id: str) -> None:
        """provider 配置发生变化（用户重新 setup）时调用，清理 client 缓存。

        OAuth credential 由 resolver 内部现读，不需要走这里；这个方法是为
        provider transport / api_base 这类**真正需要重建 client** 的场景准备。
        """
        self._client_cache.pop(provider_id, None)

    def resolve_for_alias(
        self,
        model_alias: str,
        *,
        task_scope: str | None = None,
    ) -> ResolvedAlias:
        """按 alias 解析出 (ProviderClient, model_name)。

        Args:
            model_alias: octoagent.yaml model_aliases 的 key（如 "main" / "cheap"）
            task_scope: task 范围 key（推荐用 ``f"{task_id}:{trace_id}"``）。
                同 ``task_scope`` 内同 alias 的多次 resolve 返回**钉死的相同结果**，
                避免任务中途改 yaml 触发跨 provider history 切换（F1 修复）。
                传 ``None`` 退化为每次现读（用于一次性 / 非任务上下文，如健康检查）

        Raises:
            CredentialError / CredentialNotFoundError：alias 找不到 / provider
                不存在 / OAuth profile 缺失等
        """
        # task scope 命中缓存：直接返回钉死的结果
        if task_scope is not None:
            cached = self._task_alias_cache.get((task_scope, model_alias))
            if cached is not None:
                return cached

        # 现读 yaml；这是 alias → provider 的 source of truth
        cfg = self._load_config_or_raise()
        alias_cfg = cfg.model_aliases.get(model_alias)
        if alias_cfg is None:
            raise CredentialError(
                f"model alias {model_alias!r} 未在 octoagent.yaml 中定义",
            )
        provider_cfg = cfg.get_provider(alias_cfg.provider)
        if provider_cfg is None or not provider_cfg.enabled:
            raise CredentialError(
                f"alias {model_alias!r} 的 provider {alias_cfg.provider!r} "
                f"不存在或未启用",
            )

        client = self._client_cache.get(provider_cfg.id)
        if client is None or self._client_outdated(client, provider_cfg):
            client = self._build_client(provider_cfg)
            self._client_cache[provider_cfg.id] = client

        resolved = ResolvedAlias(
            client=client,
            model_name=alias_cfg.model,
            provider_id=provider_cfg.id,
        )

        if task_scope is not None:
            self._task_alias_cache[(task_scope, model_alias)] = resolved
            log.debug(
                "router_alias_locked_to_task",
                task_scope=task_scope,
                alias=model_alias,
                provider=provider_cfg.id,
                model=alias_cfg.model,
            )

        return resolved

    # ──────────────── private helpers ────────────────

    def _load_config_or_raise(self):
        """每次现读 octoagent.yaml；任务中途改 alias 不会立刻生效（被 task scope 锁住）。"""
        # 内部 import 避免顶层循环（gateway → provider）
        from octoagent.gateway.services.config.config_wizard import load_config

        cfg = load_config(self._project_root)
        if cfg is None:
            raise CredentialError(
                f"octoagent.yaml 不可加载（project_root={self._project_root}）",
            )
        return cfg

    def _client_outdated(self, client: ProviderClient, provider_cfg: Any) -> bool:
        """检测 ProviderClient 缓存是否需要重建。

        触发重建的字段：transport / api_base / auth.kind / auth.env|profile /
        extra_headers / extra_body。
        其他字段（OAuth credential 本身）由 resolver 内部 invalidation 处理，
        不需要重建 client。
        """
        runtime = client.runtime
        if runtime.transport.value != self._provider_transport(provider_cfg):
            return True
        new_api_base = self._provider_api_base(provider_cfg)
        if runtime.api_base != new_api_base.rstrip("/"):
            return True
        # 比较 extra_headers / extra_body —— 用 sorted dict 兼容 key 顺序
        new_headers = self._provider_extra_headers(provider_cfg)
        if dict(runtime.extra_headers) != dict(new_headers):
            return True
        new_body = self._provider_extra_body(provider_cfg)
        if dict(runtime.extra_body) != dict(new_body):
            return True
        # auth resolver 类型变化也要重建
        if not self._auth_resolver_matches(runtime.auth_resolver, provider_cfg):
            return True
        return False

    def _build_client(self, provider_cfg: Any) -> ProviderClient:
        runtime = ProviderRuntime(
            provider_id=provider_cfg.id,
            transport=ProviderTransport(self._provider_transport(provider_cfg)),
            api_base=self._provider_api_base(provider_cfg).rstrip("/"),
            auth_resolver=self._build_auth_resolver(provider_cfg),
            extra_headers=dict(self._provider_extra_headers(provider_cfg)),
            extra_body=dict(self._provider_extra_body(provider_cfg)),
        )
        return ProviderClient(runtime, self._http)

    def _build_auth_resolver(self, provider_cfg: Any) -> AuthResolver:
        """按 provider_cfg.auth.kind 构造 resolver。

        Phase 1：ProviderEntry 的 auth/transport 字段尚未在 schema 落地（Phase 4
        Migration 才升 schema 到 v2），这里通过 helper 兼容读取：
        - 旧 schema：auth_type + api_key_env → 推断 auth.kind
        - 新 schema：auth: {kind, env|profile} → 直接用
        """
        auth_kind = self._provider_auth_kind(provider_cfg)
        if auth_kind == "api_key":
            env_var = self._provider_auth_env(provider_cfg)
            return StaticApiKeyResolver(env_var=env_var)
        if auth_kind == "oauth":
            profile_name = self._provider_auth_profile(provider_cfg)
            adapter_config = BUILTIN_PROVIDERS.get(provider_cfg.id)
            if adapter_config is None:
                raise CredentialError(
                    f"provider {provider_cfg.id!r} 没有 OAuth provider config "
                    f"（BUILTIN_PROVIDERS 未注册）",
                )
            return OAuthResolver(
                coordinator=self._coord,
                provider_id=provider_cfg.id,
                profile_name=profile_name,
                provider_config=adapter_config,
                credential_store=self._store,
                event_store=self._event_store,
                extra_headers_template=adapter_config.extra_api_headers,
            )
        raise CredentialError(f"unknown auth kind: {auth_kind}")

    # ──────────────── schema 兼容 helpers ────────────────
    # 这些 helper 让 Router 同时支持旧 schema（Phase 1-3）和新 schema（Phase 4 后）。
    # Phase 4 Migration 完成后，旧字段读分支将被删除。

    def _provider_transport(self, provider_cfg: Any) -> str:
        """读取 transport 字段；旧 schema 没有时按 provider id 推断。"""
        explicit = getattr(provider_cfg, "transport", None)
        if explicit:
            return str(explicit)
        # 推断规则（与 Phase 4 Migration 共用）
        provider_id = provider_cfg.id
        if provider_id == "openai-codex":
            return "openai_responses"
        if provider_id == "anthropic-claude":
            return "anthropic_messages"
        # 默认 openai_chat（覆盖 SiliconFlow / DeepSeek / OpenRouter / OpenAI 等）
        return "openai_chat"

    def _provider_api_base(self, provider_cfg: Any) -> str:
        """读 api_base；旧 schema 用 base_url 字段，按 provider id 兜底。"""
        explicit = getattr(provider_cfg, "api_base", None)
        if explicit:
            return str(explicit)
        legacy = getattr(provider_cfg, "base_url", "")
        if legacy:
            return str(legacy)
        # 兜底：按 id 给默认值
        defaults = {
            "openai-codex": "https://chatgpt.com/backend-api/codex",
            "anthropic-claude": "https://api.anthropic.com",
            "openai": "https://api.openai.com",
            "siliconflow": "https://api.siliconflow.cn/v1",
        }
        if provider_cfg.id in defaults:
            return defaults[provider_cfg.id]
        raise CredentialError(
            f"provider {provider_cfg.id!r} 缺少 api_base / base_url 配置",
        )

    def _provider_auth_kind(self, provider_cfg: Any) -> str:
        """auth.kind 字段；旧 schema 通过 auth_type 字段推断。"""
        auth_obj = getattr(provider_cfg, "auth", None)
        if auth_obj is not None:
            return str(getattr(auth_obj, "kind", ""))
        legacy_auth_type = getattr(provider_cfg, "auth_type", "")
        return str(legacy_auth_type) if legacy_auth_type else "api_key"

    def _provider_auth_env(self, provider_cfg: Any) -> str:
        """API key 模式下的 env 变量名。"""
        auth_obj = getattr(provider_cfg, "auth", None)
        if auth_obj is not None:
            env = getattr(auth_obj, "env", "")
            if env:
                return str(env)
        legacy = getattr(provider_cfg, "api_key_env", "")
        return str(legacy)

    def _provider_auth_profile(self, provider_cfg: Any) -> str:
        """OAuth 模式下的 profile 名。旧 schema 没有此字段，按 provider id
        推断默认 profile（``{id}-default``）。"""
        auth_obj = getattr(provider_cfg, "auth", None)
        if auth_obj is not None:
            profile = getattr(auth_obj, "profile", "")
            if profile:
                return str(profile)
        return f"{provider_cfg.id}-default"

    def _provider_extra_headers(self, provider_cfg: Any) -> Mapping[str, str]:
        """提取 extra_headers；旧 schema 无此字段，从 BUILTIN_PROVIDERS 兜底。"""
        explicit = getattr(provider_cfg, "extra_headers", None)
        if explicit:
            return dict(explicit)
        # 旧 schema 的 OAuth provider headers 在 BUILTIN_PROVIDERS.extra_api_headers
        builtin = BUILTIN_PROVIDERS.get(provider_cfg.id)
        if builtin is not None and builtin.extra_api_headers:
            return dict(builtin.extra_api_headers)
        return {}

    def _provider_extra_body(self, provider_cfg: Any) -> Mapping[str, Any]:
        """旧 schema 没有 extra_body；Responses API provider 默认 store=False。"""
        explicit = getattr(provider_cfg, "extra_body", None)
        if explicit:
            return dict(explicit)
        return {}

    def _auth_resolver_matches(
        self,
        resolver: AuthResolver,
        provider_cfg: Any,
    ) -> bool:
        """判断现有 resolver 是否仍能匹配 provider_cfg；用于 client 缓存失效检测。"""
        target_kind = self._provider_auth_kind(provider_cfg)
        if target_kind == "api_key":
            if not isinstance(resolver, StaticApiKeyResolver):
                return False
            return getattr(resolver, "_env_var", "") == self._provider_auth_env(
                provider_cfg
            )
        if target_kind == "oauth":
            if not isinstance(resolver, OAuthResolver):
                return False
            return resolver.profile_name == self._provider_auth_profile(provider_cfg)
        return False


__all__ = ["ProviderRouter", "ResolvedAlias"]
