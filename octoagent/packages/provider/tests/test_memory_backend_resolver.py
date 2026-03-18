"""MemoryBackendResolver 单元测试（内建 MemU + LanceDB）。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest_asyncio
from octoagent.core.models import (
    Project,
    Workspace,
)
from octoagent.core.store import create_store_group
from octoagent.memory import MemoryBackendState
from octoagent.provider.dx.builtin_memu_bridge import BuiltinMemUBridge
from octoagent.provider.dx.memory_backend_resolver import MemoryBackendResolver


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
    workspace = Workspace(
        workspace_id="workspace-primary",
        project_id=project.project_id,
        slug="primary",
        name="Primary",
        root_path="/tmp/project-alpha",
        created_at=now,
        updated_at=now,
    )
    await store_group.project_store.create_project(project)
    await store_group.project_store.create_workspace(workspace)
    await store_group.conn.commit()
    return project, workspace


class TestMemoryBackendResolver:
    async def test_resolve_backend_returns_builtin_memu_bridge(
        self,
        provider_store_group,
        tmp_path: Path,
    ) -> None:
        """resolve_backend 返回 BuiltinMemUBridge（内建 MemU + LanceDB）。"""
        project, workspace = await _seed_project(provider_store_group)
        resolver = MemoryBackendResolver(tmp_path, store_group=provider_store_group)

        backend = await resolver.resolve_backend(project=project, workspace=workspace)

        assert isinstance(backend, BuiltinMemUBridge)
        assert backend.backend_id == "memu"

    async def test_resolve_backend_without_workspace(
        self,
        provider_store_group,
        tmp_path: Path,
    ) -> None:
        """不传 workspace 也能正常返回 BuiltinMemUBridge。"""
        project, _ = await _seed_project(provider_store_group)
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

        status = resolver.resolve_local_status(project=project, workspace=workspace)

        assert status.backend_id == "memu"
        assert status.active_backend == "memu"
        assert "内建 Memory Engine" in status.message
        assert status.project_binding == "project-alpha/workspace-primary/local"

    async def test_resolve_local_status_without_workspace(
        self,
        provider_store_group,
        tmp_path: Path,
    ) -> None:
        """不传 workspace 时 binding ref 使用 'project' 占位。"""
        project, _ = await _seed_project(provider_store_group)
        resolver = MemoryBackendResolver(tmp_path, store_group=provider_store_group)

        status = resolver.resolve_local_status(project=project)

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

        backend = await resolver.resolve_backend(project=project, workspace=workspace)

        assert isinstance(backend, BuiltinMemUBridge)
