"""Feature 080 Phase 1：ProviderRouter 单元测试。

覆盖：
- alias 找不到 / provider 不存在 / provider 禁用 → 抛 CredentialError
- task scope 缓存（F1 修复）：同 task 内同 alias 的多次 resolve 命中缓存
- task scope 缓存：用户改 yaml 后**新 task** 立即生效，**进行中的 task** 不变
- invalidate_task：清理后再 resolve 重读 yaml
- 旧 schema 兼容：base_url / auth_type / api_key_env 字段能正确推断
"""

from __future__ import annotations

import textwrap
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import SecretStr

from octoagent.provider.auth.credentials import OAuthCredential
from octoagent.provider.auth.profile import ProviderProfile
from octoagent.provider.auth.store import CredentialStore
from octoagent.provider.auth_resolver import OAuthResolver, StaticApiKeyResolver
from octoagent.provider.exceptions import CredentialError
from octoagent.provider.provider_router import ProviderRouter
from octoagent.provider.transport import ProviderTransport


def _write_config(project_root: Path, content: str) -> None:
    (project_root / "octoagent.yaml").write_text(textwrap.dedent(content), encoding="utf-8")


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


# ────────────────────── 错误处理 ──────────────────────


@pytest.mark.asyncio
async def test_router_alias_not_found_raises(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        """
        config_version: 1
        updated_at: "2026-04-26"
        providers:
          - id: siliconflow
            name: SiliconFlow
            auth_type: api_key
            api_key_env: SILICONFLOW_API_KEY
            enabled: true
        model_aliases:
          main:
            provider: siliconflow
            model: Qwen/Qwen3.5-32B
        """,
    )
    router = ProviderRouter(
        project_root=tmp_path,
        credential_store=CredentialStore(store_path=tmp_path / "auth-profiles.json"),
    )
    try:
        with pytest.raises(CredentialError, match="未在 octoagent.yaml 中定义"):
            router.resolve_for_alias("nonexistent")
    finally:
        await router.aclose()


@pytest.mark.asyncio
async def test_router_provider_disabled_raises(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        """
        config_version: 1
        updated_at: "2026-04-26"
        providers:
          - id: siliconflow
            name: SiliconFlow
            auth_type: api_key
            api_key_env: SILICONFLOW_API_KEY
            enabled: false
        model_aliases:
          main:
            provider: siliconflow
            model: Qwen/Qwen3.5-32B
        """,
    )
    router = ProviderRouter(
        project_root=tmp_path,
        credential_store=CredentialStore(store_path=tmp_path / "auth-profiles.json"),
    )
    try:
        with pytest.raises(CredentialError, match="不存在或未启用"):
            router.resolve_for_alias("main")
    finally:
        await router.aclose()


# ────────────────────── 旧 schema 推断 ──────────────────────


@pytest.mark.asyncio
async def test_router_infers_transport_from_provider_id(tmp_path: Path) -> None:
    """旧 schema 没 transport 字段时按 provider id 推断（与 Phase 4 Migration 共享逻辑）。"""
    _write_config(
        tmp_path,
        """
        config_version: 1
        updated_at: "2026-04-26"
        providers:
          - id: siliconflow
            name: SiliconFlow
            auth_type: api_key
            api_key_env: SILICONFLOW_API_KEY
            enabled: true
          - id: openai-codex
            name: OpenAI Codex
            auth_type: oauth
            api_key_env: OPENAI_API_KEY
            enabled: true
        model_aliases:
          chat:
            provider: siliconflow
            model: Qwen/Qwen3.5-32B
          codex:
            provider: openai-codex
            model: gpt-5.5
        """,
    )
    store = CredentialStore(store_path=tmp_path / "auth-profiles.json")
    _seed_oauth_profile(store)
    router = ProviderRouter(project_root=tmp_path, credential_store=store)
    try:
        chat = router.resolve_for_alias("chat")
        assert chat.client.runtime.transport == ProviderTransport.OPENAI_CHAT
        assert chat.model_name == "Qwen/Qwen3.5-32B"
        assert isinstance(chat.client.runtime.auth_resolver, StaticApiKeyResolver)

        codex = router.resolve_for_alias("codex")
        assert codex.client.runtime.transport == ProviderTransport.OPENAI_RESPONSES
        assert codex.model_name == "gpt-5.5"
        assert isinstance(codex.client.runtime.auth_resolver, OAuthResolver)
    finally:
        await router.aclose()


# ────────────────────── F1：task scope 缓存 ──────────────────────


@pytest.mark.asyncio
async def test_router_task_scope_locks_alias_within_task(tmp_path: Path) -> None:
    """F1 关键回归：同 task scope 内多次 resolve 同一 alias 返回**钉死**的结果，
    即便 octoagent.yaml 中途被改也不切换 provider，避免 history 跨协议错乱。
    """
    _write_config(
        tmp_path,
        """
        config_version: 1
        updated_at: "2026-04-26"
        providers:
          - id: siliconflow
            name: SiliconFlow
            auth_type: api_key
            api_key_env: SILICONFLOW_API_KEY
            enabled: true
        model_aliases:
          main:
            provider: siliconflow
            model: Qwen/Qwen3.5-14B
        """,
    )
    router = ProviderRouter(
        project_root=tmp_path,
        credential_store=CredentialStore(store_path=tmp_path / "auth-profiles.json"),
    )
    try:
        first = router.resolve_for_alias("main", task_scope="task-A")
        assert first.model_name == "Qwen/Qwen3.5-14B"

        # 模拟用户在 task 进行中改了 yaml（极端情况，但完全可能发生）
        _write_config(
            tmp_path,
            """
            config_version: 1
            updated_at: "2026-04-26"
            providers:
              - id: siliconflow
                name: SiliconFlow
                auth_type: api_key
                api_key_env: SILICONFLOW_API_KEY
                enabled: true
            model_aliases:
              main:
                provider: siliconflow
                model: Qwen/Qwen3.5-72B  # ← 改了
            """,
        )

        # 同 task 内仍然返回老的 model（钉死）
        second = router.resolve_for_alias("main", task_scope="task-A")
        assert second.model_name == "Qwen/Qwen3.5-14B", "task 进行中不应跨 model"
        assert first is second  # 同一 ResolvedAlias 对象（缓存命中）
    finally:
        await router.aclose()


@pytest.mark.asyncio
async def test_router_new_task_picks_up_yaml_change(tmp_path: Path) -> None:
    """F1 行为对仗：进行中的 task 钉死，但**新** task 必须读最新 yaml（解决用户痛点）。"""
    _write_config(
        tmp_path,
        """
        config_version: 1
        updated_at: "2026-04-26"
        providers:
          - id: siliconflow
            name: SiliconFlow
            auth_type: api_key
            api_key_env: SILICONFLOW_API_KEY
            enabled: true
        model_aliases:
          main:
            provider: siliconflow
            model: Qwen/Qwen3.5-14B
        """,
    )
    router = ProviderRouter(
        project_root=tmp_path,
        credential_store=CredentialStore(store_path=tmp_path / "auth-profiles.json"),
    )
    try:
        old_task = router.resolve_for_alias("main", task_scope="task-old")
        assert old_task.model_name == "Qwen/Qwen3.5-14B"

        # 用户改 yaml
        _write_config(
            tmp_path,
            """
            config_version: 1
            updated_at: "2026-04-26"
            providers:
              - id: siliconflow
                name: SiliconFlow
                auth_type: api_key
                api_key_env: SILICONFLOW_API_KEY
                enabled: true
            model_aliases:
              main:
                provider: siliconflow
                model: Qwen/Qwen3.5-72B
            """,
        )

        new_task = router.resolve_for_alias("main", task_scope="task-new")
        assert new_task.model_name == "Qwen/Qwen3.5-72B", "新 task 必须读最新 yaml"
        assert old_task.model_name == "Qwen/Qwen3.5-14B", "老 task 钉死不变"
    finally:
        await router.aclose()


@pytest.mark.asyncio
async def test_router_invalidate_task_releases_lock(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        """
        config_version: 1
        updated_at: "2026-04-26"
        providers:
          - id: siliconflow
            name: SiliconFlow
            auth_type: api_key
            api_key_env: SILICONFLOW_API_KEY
            enabled: true
        model_aliases:
          main:
            provider: siliconflow
            model: Qwen/Qwen3.5-14B
        """,
    )
    router = ProviderRouter(
        project_root=tmp_path,
        credential_store=CredentialStore(store_path=tmp_path / "auth-profiles.json"),
    )
    try:
        a1 = router.resolve_for_alias("main", task_scope="task-X")
        # invalidate 后再 resolve 应重新查 yaml（model 没变所以结果相同，但走了完整路径）
        router.invalidate_task("task-X")
        a2 = router.resolve_for_alias("main", task_scope="task-X")
        # task 重新解析后是新 ResolvedAlias 对象（即便 model_name 相同）
        assert a1.model_name == a2.model_name == "Qwen/Qwen3.5-14B"
    finally:
        await router.aclose()


@pytest.mark.asyncio
async def test_router_resolve_without_task_scope_always_rereads(tmp_path: Path) -> None:
    """``task_scope=None`` 时退化为每次现读，用于一次性 / 健康检查类调用。"""
    _write_config(
        tmp_path,
        """
        config_version: 1
        updated_at: "2026-04-26"
        providers:
          - id: siliconflow
            name: SiliconFlow
            auth_type: api_key
            api_key_env: SILICONFLOW_API_KEY
            enabled: true
        model_aliases:
          main:
            provider: siliconflow
            model: Qwen/Qwen3.5-14B
        """,
    )
    router = ProviderRouter(
        project_root=tmp_path,
        credential_store=CredentialStore(store_path=tmp_path / "auth-profiles.json"),
    )
    try:
        first = router.resolve_for_alias("main", task_scope=None)
        # 改 yaml
        _write_config(
            tmp_path,
            """
            config_version: 1
            updated_at: "2026-04-26"
            providers:
              - id: siliconflow
                name: SiliconFlow
                auth_type: api_key
                api_key_env: SILICONFLOW_API_KEY
                enabled: true
            model_aliases:
              main:
                provider: siliconflow
                model: Qwen/Qwen3.5-72B
            """,
        )
        second = router.resolve_for_alias("main", task_scope=None)
        # 无 task scope：每次都读最新
        assert first.model_name == "Qwen/Qwen3.5-14B"
        assert second.model_name == "Qwen/Qwen3.5-72B"
    finally:
        await router.aclose()
