"""Feature 080 Phase 1：统一凭证解析层。

把"OAuth"和"API Key"两种凭证形态统一到 ``AuthResolver`` 接口下。每次 LLM 调用
前 ``resolve()`` 出当前 token + 动态 headers；401 时调用 ``force_refresh()`` 重试。

设计参考：
- Pydantic AI 的 ``httpx.Auth`` hook 模式（``async_auth_flow`` / ``_refresh_token``）
- Hermes Agent 的凭证池接口（不引入轮换逻辑，单 profile 即可）

与 Feature 078 的关系：``OAuthResolver`` **完整复用** ``PkceOAuthAdapter`` +
``TokenRefreshCoordinator`` —— Feature 078 的所有 OAuth 逻辑（reused recovery /
store reload / preemptive buffer / emit_oauth_event 埋点）零浪费迁移到新链路。
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import structlog

from .auth.pkce_oauth_adapter import PkceOAuthAdapter
from .exceptions import CredentialError, CredentialExpiredError, CredentialNotFoundError
from .refresh_coordinator import TokenRefreshCoordinator

log = structlog.get_logger()


@dataclass(frozen=True)
class ResolvedAuth:
    """LLM 调用前 resolve 出来的"现役凭证"，含 bearer token + 动态 headers。

    ``extra_headers`` 会在请求时与 provider 的静态 ``extra_headers``（如
    ``OpenAI-Beta``）合并；动态值（OAuth 的 ``chatgpt-account-id``）由
    AuthResolver 填充，以反映 refresh 后可能变化的身份字段。
    """

    bearer_token: str
    extra_headers: dict[str, str] = field(default_factory=dict)


class AuthResolver(Protocol):
    """凭证解析器协议。每次 LLM 调用前 ``resolve()``；401 后 ``force_refresh()``。

    实现需要满足：
    - ``resolve()``：preemptive 检查 + 必要时刷新（OAuth）/ 直接读 env (API key)
    - ``force_refresh()``：跳过 preemptive gate 强制刷新；失败返回 ``None``
      让上层回落到原始 401（不抛，避免吞掉真实的 unauthorized 信号）
    """

    async def resolve(self) -> ResolvedAuth: ...

    async def force_refresh(self) -> ResolvedAuth | None: ...


class StaticApiKeyResolver:
    """静态 API key 凭证解析器（SiliconFlow / DeepSeek / OpenRouter / OpenAI raw key 等）。

    凭证从环境变量读，与 Feature 078 的 ``os.environ[api_key_env] = token``
    同步契约一致；force_refresh 重读 env 是为了支持用户运行期改 ``.env`` 后
    通过下次 401 retry 自动生效。
    """

    def __init__(
        self,
        env_var: str,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        if not env_var:
            raise CredentialError("StaticApiKeyResolver 需要非空 env_var")
        self._env_var = env_var
        self._extra_headers = dict(extra_headers or {})

    async def resolve(self) -> ResolvedAuth:
        token = os.environ.get(self._env_var, "").strip()
        if not token:
            raise CredentialNotFoundError(
                f"环境变量 {self._env_var} 为空或未设置",
            )
        return ResolvedAuth(
            bearer_token=token,
            extra_headers=dict(self._extra_headers),
        )

    async def force_refresh(self) -> ResolvedAuth | None:
        # API key 没有 refresh 概念；重读 env 容许用户运行期改 .env 后下次 401 retry
        # 拿到新值。读到空就返回 None，让上层回落到原始 401。
        token = os.environ.get(self._env_var, "").strip()
        if not token:
            log.warning(
                "static_api_key_force_refresh_empty",
                env_var=self._env_var,
            )
            return None
        return ResolvedAuth(
            bearer_token=token,
            extra_headers=dict(self._extra_headers),
        )


class OAuthResolver:
    """OAuth 凭证解析器（ChatGPT Pro Codex / Anthropic Claude OAuth 等）。

    F2 修复：**每次 resolve 时从 CredentialStore 重新加载 profile**，构造一次性
    PkceOAuthAdapter 用于本次调用。避免长生命周期持有 credential 快照导致用户
    重新走 OAuth / 切账号后仍用旧 access_token + 旧 account_id 的 tenant 混淆。

    适配器构造成本可忽略（纯 Python dataclass + reference 持有；store.get_profile
    本身命中 filelock + json.loads，~1ms/次，单次 LLM 调用相比可忽略）。

    复用 Feature 078 的：
    - ``is_expired()`` 5 分钟 preemptive buffer
    - ``refresh()`` 含 reused recovery / store reload / emit 全套
    - ``TokenRefreshCoordinator`` 的 per-provider 串行化锁
    """

    def __init__(
        self,
        *,
        coordinator: TokenRefreshCoordinator,
        provider_id: str,
        profile_name: str,
        provider_config: Any,  # OAuthProviderConfig，用 Any 避免循环导入
        credential_store: Any,  # CredentialStore，同上
        event_store: Any | None = None,
        extra_headers_template: dict[str, str] | None = None,
    ) -> None:
        self._coord = coordinator
        self._provider_id = provider_id
        self._profile_name = profile_name
        self._provider_config = provider_config
        self._store = credential_store
        self._event_store = event_store
        self._tmpl = dict(extra_headers_template or {})

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def profile_name(self) -> str:
        return self._profile_name

    async def resolve(self) -> ResolvedAuth:
        return await self._resolve(force=False)

    async def force_refresh(self) -> ResolvedAuth | None:
        try:
            return await self._resolve(force=True)
        except (CredentialExpiredError, CredentialNotFoundError) as exc:
            log.warning(
                "oauth_resolver_force_refresh_failed",
                provider_id=self._provider_id,
                error_type=type(exc).__name__,
            )
            return None

    async def _resolve(self, *, force: bool) -> ResolvedAuth:
        # F2 修复：每次 resolve 都从 store 现读 profile 构造 fresh adapter
        # —— 不持有任何 credential 快照。
        adapter = self._build_fresh_adapter()
        if adapter is None:
            raise CredentialNotFoundError(
                f"profile {self._profile_name!r} 在 auth-profiles.json 中不存在",
            )

        async def _refresh_fn() -> str | None:
            try:
                return await adapter.resolve(force_refresh=force)
            except (CredentialExpiredError, CredentialNotFoundError) as exc:
                log.warning(
                    "oauth_resolver_adapter_resolve_failed",
                    provider_id=self._provider_id,
                    profile_name=self._profile_name,
                    force=force,
                    error_type=type(exc).__name__,
                )
                return None

        token = await self._coord.refresh_if_needed(
            provider_id=self._provider_id,
            refresh_fn=_refresh_fn,
        )
        if not token:
            raise CredentialExpiredError(
                f"OAuth resolve 失败：provider={self._provider_id} force={force}",
            )

        # 取 refresh 后的 adapter._credential.account_id（adapter.refresh() 会更新
        # 内存态；如果走 reused recovery / store reload 路径，account_id 也会刷新）
        account_id = (adapter.credential.account_id or "")
        rendered_headers = {
            k: v.replace("{account_id}", account_id) for k, v in self._tmpl.items()
        }
        return ResolvedAuth(
            bearer_token=token,
            extra_headers=rendered_headers,
        )

    def _build_fresh_adapter(self) -> "PkceOAuthAdapter | None":
        """从 store 读最新 profile 构造一次性 adapter。

        避免长生命周期持有 credential：用户重新授权 / 切账号 / 手动改
        auth-profiles.json 后下次 resolve 立即生效，不需要重启 Gateway。
        """
        # 内部 import 避免顶层循环（auth.pkce_oauth_adapter → auth_resolver）
        from .auth.credentials import OAuthCredential

        profile = self._store.get_profile(self._profile_name)
        if profile is None:
            return None
        if not isinstance(profile.credential, OAuthCredential):
            log.warning(
                "oauth_resolver_profile_not_oauth_kind",
                profile_name=self._profile_name,
                actual_kind=type(profile.credential).__name__,
            )
            return None
        return PkceOAuthAdapter(
            credential=profile.credential,
            provider_config=self._provider_config,
            store=self._store,
            profile_name=self._profile_name,
            event_store=self._event_store,
        )


# 类型别名：让上层代码可以把 resolver 当 callable 传递（不强制实例化）
AuthResolverFactory = Callable[[], Awaitable[AuthResolver]]


__all__ = [
    "AuthResolver",
    "AuthResolverFactory",
    "OAuthResolver",
    "ResolvedAuth",
    "StaticApiKeyResolver",
]
