"""MemoryBackendResolver 单元测试。"""

from __future__ import annotations

import sys
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
from octoagent.memory import MemoryAccessPolicy, MemoryBackendState, MemoryLayer, MemoryPartition
from octoagent.provider.dx.config_schema import MemoryConfig, OctoAgentConfig
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
    async def test_uses_yaml_local_only_mode_when_no_project_binding_exists(
        self,
        provider_store_group,
        tmp_path: Path,
    ) -> None:
        project, workspace = await _seed_project(provider_store_group)
        (tmp_path / "octoagent.yaml").write_text(
            OctoAgentConfig(
                updated_at="2026-03-11",
                memory=MemoryConfig(backend_mode="local_only"),
            ).to_yaml(),
            encoding="utf-8",
        )
        resolver = MemoryBackendResolver(tmp_path, store_group=provider_store_group)

        backend = await resolver.resolve_backend(project=project, workspace=workspace)
        status = await backend.get_status()

        assert status.backend_id == "memu"
        assert status.state.value == "healthy"
        assert status.active_backend == "sqlite-metadata"
        assert status.project_binding == "project-alpha/workspace-primary/octoagent.yaml"
        assert "本地 Memory 模式" in status.message

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

    async def test_uses_yaml_memu_bridge_when_project_binding_missing(
        self,
        provider_store_group,
        tmp_path: Path,
    ) -> None:
        project, workspace = await _seed_project(provider_store_group)
        (tmp_path / "octoagent.yaml").write_text(
            OctoAgentConfig(
                updated_at="2026-03-11",
                memory=MemoryConfig(
                    backend_mode="memu",
                    bridge_url="https://yaml.memu.test",
                    bridge_api_key_env="MEMU_API_KEY",
                    bridge_timeout_seconds=8.0,
                    bridge_search_path="/memory/query",
                ),
            ).to_yaml(),
            encoding="utf-8",
        )

        seen_hosts: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_hosts.append(request.url.host or "")
            assert request.headers["Authorization"] == "Bearer yaml-secret"
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
            environ={"MEMU_API_KEY": "yaml-secret"},
            client_factory=lambda: httpx.AsyncClient(transport=transport),
        )

        backend = await resolver.resolve_backend(project=project, workspace=workspace)
        status = await backend.get_status()

        assert status.state.value == "healthy"
        assert status.project_binding == "project-alpha/workspace-primary/octoagent.yaml"
        assert seen_hosts == ["yaml.memu.test"]

    async def test_uses_yaml_memu_command_bridge_when_configured(
        self,
        provider_store_group,
        tmp_path: Path,
    ) -> None:
        project, workspace = await _seed_project(provider_store_group)
        script_path = tmp_path / "memu_bridge.py"
        script_path.write_text(
            """
import json
import os
import sys

action = sys.argv[-1]
payload = json.loads(sys.stdin.read() or "{}")

if action == "health":
    print(json.dumps({
        "status": {
            "backend_id": "memu",
            "state": "healthy",
            "active_backend": "memu",
            "project_binding": os.environ.get("OCTOAGENT_BRIDGE_BINDING", ""),
            "index_health": {"transport": "command"},
        }
    }))
elif action == "query":
    print(json.dumps({
        "items": [
            {
                "record_id": "memu-command-hit",
                "layer": "sor",
                "scope_id": payload["scope_id"],
                "partition": "work",
                "summary": payload.get("query", ""),
                "created_at": "2026-03-14T00:00:00+00:00",
            }
        ]
    }))
else:
    print(json.dumps({"result": {}}))
""".strip(),
            encoding="utf-8",
        )
        (tmp_path / "octoagent.yaml").write_text(
            OctoAgentConfig(
                updated_at="2026-03-11",
                memory=MemoryConfig(
                    backend_mode="memu",
                    bridge_transport="command",
                    bridge_command=f"{sys.executable} {script_path}",
                    bridge_command_cwd=str(tmp_path),
                    bridge_command_timeout_seconds=4.0,
                ),
            ).to_yaml(),
            encoding="utf-8",
        )
        resolver = MemoryBackendResolver(tmp_path, store_group=provider_store_group)

        backend = await resolver.resolve_backend(project=project, workspace=workspace)
        status = await backend.get_status()
        hits = await backend.search(
            "memory/project-alpha",
            query="running",
            policy=MemoryAccessPolicy(),
        )

        assert status.state is MemoryBackendState.HEALTHY
        assert status.project_binding == "project-alpha/workspace-primary/octoagent.yaml"
        assert status.index_health["transport"] == "command"
        assert hits[0].record_id == "memu-command-hit"
        assert hits[0].layer is MemoryLayer.SOR
        assert hits[0].partition is MemoryPartition.WORK
