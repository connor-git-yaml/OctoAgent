"""MemoryBackendResolver 单元测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest_asyncio
from octoagent.core.models import (
    Project,
    ProjectBinding,
    ProjectBindingType,
    ProjectSecretBinding,
    SecretBindingStatus,
    SecretRefSourceType,
    SecretTargetKind,
    Workspace,
)
from octoagent.core.store import create_store_group
from octoagent.provider.dx.memory_backend_resolver import MemoryBackendResolver
from ulid import ULID


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
    async def test_returns_unavailable_backend_when_bridge_not_configured(
        self,
        provider_store_group,
        tmp_path: Path,
    ) -> None:
        project, workspace = await _seed_project(provider_store_group)
        resolver = MemoryBackendResolver(tmp_path, store_group=provider_store_group)

        backend = await resolver.resolve_backend(project=project, workspace=workspace)
        status = await backend.get_status()

        assert status.backend_id == "memu"
        assert status.state.value == "unavailable"
        assert status.failure_code == "MEMU_NOT_CONFIGURED"
        assert status.project_binding.endswith("/memu.primary")

    async def test_prefers_workspace_binding_and_resolves_memory_secret(
        self,
        provider_store_group,
        tmp_path: Path,
    ) -> None:
        project, workspace = await _seed_project(provider_store_group)
        await provider_store_group.project_store.create_binding(
            ProjectBinding(
                binding_id=str(ULID()),
                project_id=project.project_id,
                workspace_id=None,
                binding_type=ProjectBindingType.MEMORY_BRIDGE,
                binding_key="memu.project",
                binding_value="https://project.memu.test",
                source="tests",
                migration_run_id="memory-bridge-test",
            )
        )
        await provider_store_group.project_store.create_binding(
            ProjectBinding(
                binding_id=str(ULID()),
                project_id=project.project_id,
                workspace_id=workspace.workspace_id,
                binding_type=ProjectBindingType.MEMORY_BRIDGE,
                binding_key="memu.primary",
                binding_value="https://workspace.memu.test",
                source="tests",
                migration_run_id="memory-bridge-test",
                metadata={"api_key_target_key": "memory.memu.api_key"},
            )
        )
        await provider_store_group.project_store.save_secret_binding(
            ProjectSecretBinding(
                binding_id=str(ULID()),
                project_id=project.project_id,
                workspace_id=workspace.workspace_id,
                target_kind=SecretTargetKind.MEMORY,
                target_key="memory.memu.api_key",
                env_name="MEMU_API_KEY",
                ref_source_type=SecretRefSourceType.ENV,
                ref_locator={"env_name": "MEMU_API_KEY"},
                display_name="MemU API Key",
                status=SecretBindingStatus.APPLIED,
            )
        )
        await provider_store_group.conn.commit()

        seen_hosts: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_hosts.append(request.url.host or "")
            assert request.headers["Authorization"] == "Bearer memu-secret"
            return httpx.Response(
                200,
                json={
                    "status": {
                        "backend_id": "memu",
                        "state": "healthy",
                        "active_backend": "memu",
                    }
                },
            )

        transport = httpx.MockTransport(handler)
        resolver = MemoryBackendResolver(
            tmp_path,
            store_group=provider_store_group,
            environ={"MEMU_API_KEY": "memu-secret"},
            client_factory=lambda: httpx.AsyncClient(transport=transport),
        )

        backend = await resolver.resolve_backend(project=project, workspace=workspace)
        status = await backend.get_status()

        assert status.state.value == "healthy"
        assert status.project_binding == "project-alpha/workspace-primary/memu.primary"
        assert seen_hosts == ["workspace.memu.test"]
