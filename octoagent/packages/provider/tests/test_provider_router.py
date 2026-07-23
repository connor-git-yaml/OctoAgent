"""ProviderRouter注入route、task pinning与cache invalidation合同。"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from octoagent.provider.auth.credentials import OAuthCredential
from octoagent.provider.auth.profile import ProviderProfile
from octoagent.provider.auth.store import CredentialStore
from octoagent.provider.auth_resolver import OAuthResolver, StaticApiKeyResolver
from octoagent.provider.exceptions import CredentialError
from octoagent.provider.provider_route import ProviderAuthRoute, ProviderRoute
from octoagent.provider.provider_router import ProviderRouter
from octoagent.provider.transport import ProviderTransport
from pydantic import SecretStr


def _route(
    *,
    alias: str = "main",
    provider: str = "siliconflow",
    model: str = "Qwen/Qwen3.5-14B",
    transport: str = "openai_chat",
    api_base: str = "https://api.siliconflow.cn/v1",
    auth: ProviderAuthRoute | None = None,
) -> ProviderRoute:
    return ProviderRoute(
        alias=alias,
        provider=provider,
        model=model,
        transport=transport,
        api_base=api_base,
        auth=auth or ProviderAuthRoute(kind="api_key", env="SILICONFLOW_API_KEY"),
    )


def _resolver(state: dict[str, ProviderRoute]) -> Callable[[str], ProviderRoute]:
    def resolve(alias: str) -> ProviderRoute:
        try:
            return state[alias]
        except KeyError as exc:
            raise CredentialError(f"model alias {alias!r} 未定义") from exc

    return resolve


def _seed_oauth_profile(store: CredentialStore) -> None:
    now = datetime.now(tz=UTC)
    store.set_profile(
        ProviderProfile(
            name="openai-codex-default",
            provider="openai-codex",
            auth_mode="oauth",
            credential=OAuthCredential(
                provider="openai-codex",
                access_token=SecretStr("at"),
                refresh_token=SecretStr("rt"),
                expires_at=now + timedelta(hours=1),
                account_id="acc-1",
            ),
            is_default=True,
            created_at=now,
            updated_at=now,
        )
    )


@pytest.mark.asyncio
async def test_router_alias_not_found_raises() -> None:
    router = ProviderRouter(route_resolver=_resolver({}))
    try:
        with pytest.raises(CredentialError, match="未定义"):
            router.resolve_for_alias("nonexistent")
    finally:
        await router.aclose()


@pytest.mark.asyncio
async def test_router_provider_disabled_raises() -> None:
    error = CredentialError("provider不存在或未启用")

    def reject(_alias: str) -> ProviderRoute:
        raise error

    router = ProviderRouter(route_resolver=reject)
    try:
        with pytest.raises(CredentialError, match="不存在或未启用") as caught:
            router.resolve_for_alias("main")
        assert caught.value is error
    finally:
        await router.aclose()


@pytest.mark.asyncio
async def test_router_infers_transport_from_provider_id(tmp_path: Path) -> None:
    store = CredentialStore(store_path=tmp_path / "auth-profiles.json")
    _seed_oauth_profile(store)
    state = {
        "chat": _route(alias="chat"),
        "codex": _route(
            alias="codex",
            provider="openai-codex",
            model="gpt-5.5",
            transport="openai_responses",
            api_base="https://chatgpt.com/backend-api/codex",
            auth=ProviderAuthRoute(kind="oauth", profile="openai-codex-default"),
        ),
    }
    router = ProviderRouter(route_resolver=_resolver(state), credential_store=store)
    try:
        chat = router.resolve_for_alias("chat")
        assert chat.client.runtime.transport == ProviderTransport.OPENAI_CHAT
        assert isinstance(chat.client.runtime.auth_resolver, StaticApiKeyResolver)
        codex = router.resolve_for_alias("codex")
        assert codex.client.runtime.transport == ProviderTransport.OPENAI_RESPONSES
        assert isinstance(codex.client.runtime.auth_resolver, OAuthResolver)
    finally:
        await router.aclose()


@pytest.mark.asyncio
async def test_router_task_scope_locks_alias_within_task() -> None:
    state = {"main": _route()}
    router = ProviderRouter(route_resolver=_resolver(state))
    try:
        first = router.resolve_for_alias("main", task_scope="task-A")
        state["main"] = state["main"].model_copy(update={"model": "Qwen/Qwen3.5-72B"})
        second = router.resolve_for_alias("main", task_scope="task-A")
        assert first is second
        assert second.model_name == "Qwen/Qwen3.5-14B"
    finally:
        await router.aclose()


@pytest.mark.asyncio
async def test_router_new_task_picks_up_yaml_change() -> None:
    state = {"main": _route()}
    router = ProviderRouter(route_resolver=_resolver(state))
    try:
        old_task = router.resolve_for_alias("main", task_scope="task-old")
        state["main"] = state["main"].model_copy(update={"model": "Qwen/Qwen3.5-72B"})
        new_task = router.resolve_for_alias("main", task_scope="task-new")
        assert old_task.model_name == "Qwen/Qwen3.5-14B"
        assert new_task.model_name == "Qwen/Qwen3.5-72B"
    finally:
        await router.aclose()


@pytest.mark.asyncio
async def test_router_invalidate_task_releases_lock() -> None:
    state = {"main": _route()}
    router = ProviderRouter(route_resolver=_resolver(state))
    try:
        first = router.resolve_for_alias("main", task_scope="task-X")
        router.invalidate_task("task-X")
        second = router.resolve_for_alias("main", task_scope="task-X")
        assert first is not second
        assert first.client is second.client
    finally:
        await router.aclose()


@pytest.mark.asyncio
async def test_router_resolve_without_task_scope_always_rereads() -> None:
    state = {"main": _route()}
    router = ProviderRouter(route_resolver=_resolver(state))
    try:
        first = router.resolve_for_alias("main")
        state["main"] = state["main"].model_copy(update={"model": "Qwen/Qwen3.5-72B"})
        second = router.resolve_for_alias("main")
        assert first.model_name == "Qwen/Qwen3.5-14B"
        assert second.model_name == "Qwen/Qwen3.5-72B"
    finally:
        await router.aclose()


@pytest.mark.asyncio
async def test_router_consumes_injected_route_without_gateway_loader_or_schema_getattr() -> None:
    oracle = "F151_ROUTER_ROUTE_SEAM_MISSING"
    parameters = inspect.signature(ProviderRouter).parameters
    assert "route_resolver" in parameters and "project_root" not in parameters, oracle

    state = {"main": _route(model="model-a", api_base="https://api.example.test/v1")}
    router = ProviderRouter(route_resolver=_resolver(state))
    try:
        first = router.resolve_for_alias("main", task_scope="task-a")
        state["main"] = state["main"].model_copy(update={"model": "model-b"})
        assert router.resolve_for_alias("main", task_scope="task-a") is first, oracle
        assert router.resolve_for_alias("main", task_scope="task-b").model_name == "model-b"

        old_client = first.client
        router.invalidate_provider_client("siliconflow")
        state["main"] = state["main"].model_copy(update={"api_base": "https://api.example.test/v2"})
        current = router.resolve_for_alias("main", task_scope="task-c")
        assert current.client is not old_client, oracle
        assert current.client.runtime.api_base == "https://api.example.test/v2"
    finally:
        await router.aclose()

    source = inspect.getsource(ProviderRouter)
    assert "gateway" not in source, oracle
    assert "getattr(" not in source, oracle


@pytest.mark.asyncio
async def test_router_rebuilds_inconsistent_cache_and_rejects_unsupported_auth_routes() -> None:
    state = {"main": _route()}
    router = ProviderRouter(route_resolver=_resolver(state))
    try:
        first = router.resolve_for_alias("main")
        router._client_routes.pop(first.provider_id)
        rebuilt = router.resolve_for_alias("main")
        assert rebuilt.client is not first.client
    finally:
        await router.aclose()

    oauth_route = _route(
        provider="fixture-oauth",
        auth=ProviderAuthRoute(kind="oauth", profile="fixture-default"),
    )
    router = ProviderRouter(route_resolver=_resolver({"oauth": oauth_route}))
    try:
        with pytest.raises(CredentialError, match="没有OAuth provider config"):
            router.resolve_for_alias("oauth")
    finally:
        await router.aclose()

    invalid_auth = ProviderAuthRoute.model_construct(kind="unsupported", env=None, profile=None)
    invalid_route = _route().model_copy(update={"auth": invalid_auth})
    router = ProviderRouter(route_resolver=_resolver({"invalid": invalid_route}))
    try:
        with pytest.raises(CredentialError, match="auth引用无效"):
            router.resolve_for_alias("invalid")
    finally:
        await router.aclose()
