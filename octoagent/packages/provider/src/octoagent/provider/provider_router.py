"""ProviderRoute → ProviderClient 路由层。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from .auth.oauth_provider import BUILTIN_PROVIDERS
from .auth.store import CredentialStore
from .auth_resolver import AuthResolver, OAuthResolver, StaticApiKeyResolver
from .exceptions import CredentialError
from .provider_client import ProviderClient
from .provider_route import ProviderRoute
from .provider_runtime import ProviderRuntime
from .refresh_coordinator import TokenRefreshCoordinator
from .transport import ProviderTransport

log = structlog.get_logger()

RouteResolver = Callable[[str], ProviderRoute]


@dataclass(frozen=True)
class ResolvedAlias:
    """task scope缓存的最小解析结果。"""

    client: ProviderClient
    model_name: str
    provider_id: str


class ProviderRouter:
    """只消费注入ProviderRoute的Provider client路由器。"""

    def __init__(
        self,
        *,
        route_resolver: RouteResolver,
        credential_store: CredentialStore | None = None,
        coordinator: TokenRefreshCoordinator | None = None,
        event_store: Any | None = None,
        timeout_s: float = 60.0,
    ) -> None:
        self._route_resolver = route_resolver
        self._store = credential_store or CredentialStore()
        self._coord = coordinator or TokenRefreshCoordinator()
        self._event_store = event_store
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(timeout_s, connect=10.0))
        self._client_cache: dict[str, ProviderClient] = {}
        self._client_routes: dict[str, ProviderRoute] = {}
        self._task_alias_cache: dict[tuple[str, str], ResolvedAlias] = {}

    async def aclose(self) -> None:
        await self._http.aclose()

    def invalidate_task(self, task_scope: str) -> None:
        keys_to_drop = [key for key in self._task_alias_cache if key[0] == task_scope]
        for key in keys_to_drop:
            self._task_alias_cache.pop(key, None)

    def invalidate_provider_client(self, provider_id: str) -> None:
        """只逐出client；不关闭共享HTTP，也不清理已pinned task。"""

        self._client_cache.pop(provider_id, None)
        self._client_routes.pop(provider_id, None)

    def resolve_for_alias(
        self,
        model_alias: str,
        *,
        task_scope: str | None = None,
    ) -> ResolvedAlias:
        if task_scope is not None:
            cached = self._task_alias_cache.get((task_scope, model_alias))
            if cached is not None:
                return cached

        route = self._route_resolver(model_alias)
        client = self._client_cache.get(route.provider)
        if client is None or self._client_outdated(route):
            client = self._build_client(route)
            self._client_cache[route.provider] = client
            self._client_routes[route.provider] = route
        resolved = ResolvedAlias(
            client=client,
            model_name=route.model,
            provider_id=route.provider,
        )
        if task_scope is not None:
            self._task_alias_cache[(task_scope, model_alias)] = resolved
            log.debug(
                "router_alias_locked_to_task",
                task_scope=task_scope,
                alias=model_alias,
                provider=route.provider,
                model=route.model,
            )
        return resolved

    def _client_outdated(self, route: ProviderRoute) -> bool:
        previous = self._client_routes.get(route.provider)
        if previous is None:
            return True
        return (
            previous.transport != route.transport
            or previous.api_base.rstrip("/") != route.api_base.rstrip("/")
            or previous.auth != route.auth
        )

    def _build_client(self, route: ProviderRoute) -> ProviderClient:
        runtime = ProviderRuntime(
            provider_id=route.provider,
            transport=ProviderTransport(route.transport),
            api_base=route.api_base.rstrip("/"),
            auth_resolver=self._build_auth_resolver(route),
        )
        return ProviderClient(runtime, self._http)

    def _build_auth_resolver(self, route: ProviderRoute) -> AuthResolver:
        auth = route.auth
        if auth.kind == "api_key" and auth.env:
            return StaticApiKeyResolver(env_var=auth.env)
        if auth.kind == "oauth" and auth.profile:
            adapter_config = BUILTIN_PROVIDERS.get(route.provider)
            if adapter_config is None:
                raise CredentialError(f"provider {route.provider!r}没有OAuth provider config")
            return OAuthResolver(
                coordinator=self._coord,
                provider_id=route.provider,
                profile_name=auth.profile,
                provider_config=adapter_config,
                credential_store=self._store,
                event_store=self._event_store,
                extra_headers_template=adapter_config.extra_api_headers,
            )
        raise CredentialError(f"provider {route.provider!r}的auth引用无效")


__all__ = ["ProviderRouter", "ResolvedAlias", "RouteResolver"]
