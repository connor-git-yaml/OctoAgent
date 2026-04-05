"""MemoryBackendResolver 单元测试（内建 MemU + LanceDB）。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest_asyncio
from octoagent.core.models import (
    Project,
)
from octoagent.provider.dx.project_migration import Workspace
from octoagent.core.store import create_store_group
from octoagent.memory import MemoryBackendState
from octoagent.gateway.services.memory.builtin_memu_bridge import BuiltinMemUBridge
from octoagent.gateway.services.memory.memory_backend_resolver import MemoryBackendResolver


@pytest_asyncio.fixture
async def provider_store_group(tmp_path: Path):
    store_group = await create_store_group(
        str(tmp_path / "data" / "sqlite" / "test.db"),
        tmp_path / "data" / "artifacts",
    )
    yield store_group
    await store_group.conn.close()


async def _seed_project(store_group):
    now = datetime.now(UTC)
    project = Project(
        project_id="project-alpha",
        slug="alpha",
        name="Alpha",
        is_default=True,
        created_at=now,
        updated_at=now,
    )
    await store_group.project_store.create_project(project)
    await store_group.conn.commit()
    return project, None  # workspace 概念已废弃


class TestMemoryBackendResolver:
    async def test_resolve_backend_returns_builtin_memu_bridge(
        self,
        provider_store_group,
        tmp_path: Path,
    ) -> None:
        """resolve_backend 返回 BuiltinMemUBridge（内建 MemU + LanceDB）。"""
        project, workspace = await _seed_project(provider_store_group)
        resolver = MemoryBackendResolver(tmp_path, store_group=provider_store_group)

        backend = await resolver.resolve_backend(project=project)

        assert isinstance(backend, BuiltinMemUBridge)
        assert backend.backend_id == "memu"

    async def test_resolve_local_status_returns_healthy_memu(
        self,
        provider_store_group,
        tmp_path: Path,
    ) -> None:
        """resolve_local_status 返回内建 MemU 健康状态。"""
        project, workspace = await _seed_project(provider_store_group)
        resolver = MemoryBackendResolver(tmp_path, store_group=provider_store_group)

        status = resolver.resolve_local_status(project=project)

        assert status.backend_id == "memu"
        assert status.active_backend == "memu"
        assert "内建 Memory Engine" in status.message
        assert status.project_binding == "project-alpha/project/local"

    async def test_constructor_ignores_legacy_kwargs(
        self,
        provider_store_group,
        tmp_path: Path,
    ) -> None:
        """构造函数接受遗留关键字参数但不使用它们。"""
        project, workspace = await _seed_project(provider_store_group)
        resolver = MemoryBackendResolver(
            tmp_path,
            store_group=provider_store_group,
            environ={"MEMU_API_KEY": "unused-secret"},
            client_factory=lambda: None,
        )

        backend = await resolver.resolve_backend(project=project)

        assert isinstance(backend, BuiltinMemUBridge)
