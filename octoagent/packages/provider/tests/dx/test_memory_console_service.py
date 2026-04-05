from __future__ import annotations

import json
from pathlib import Path

import pytest
from octoagent.core.models import Project, ProjectBinding, ProjectBindingType
from octoagent.core.store import create_store_group
from octoagent.memory import (
    EvidenceRef,
    MemoryPartition,
    MemoryService,
    SqliteMemoryStore,
    VaultAccessDecision,
    WriteAction,
    init_memory_db,
)
from octoagent.gateway.services.memory.memory_console_service import MemoryConsoleService
from ulid import ULID


def _db_path(project_root: Path) -> Path:
    return project_root / "data" / "sqlite" / "octoagent.db"


def _artifacts_dir(project_root: Path) -> Path:
    return project_root / "data" / "artifacts"


async def _create_project(
    store_group,
    *,
    project_id: str,
    workspace_id: str,
    scope_id: str | None = None,
) -> tuple[Project, str]:
    project = Project(
        project_id=project_id,
        slug=project_id.replace("project-", ""),
        name=project_id,
        is_default=project_id == "project-default",
    )
    await store_group.project_store.create_project(project)
    if scope_id:
        await store_group.project_store.create_binding(
            ProjectBinding(
                binding_id=str(ULID()),
                project_id=project_id,
                binding_type=ProjectBindingType.MEMORY_SCOPE,
                binding_key=scope_id,
                binding_value=scope_id,
                source="tests",
                migration_run_id="memory-console-tests",
            )
        )
    await store_group.conn.commit()
    return project, workspace_id


async def _seed_bound_memory(
    store_group,
    *,
    scope_id: str,
) -> None:
    memory = MemoryService(store_group.conn, store=SqliteMemoryStore(store_group.conn))
    import_proposal = await memory.propose_write(
        scope_id=scope_id,
        partition=MemoryPartition.WORK,
        action=WriteAction.ADD,
        subject_key="work.import.subject",
        content="imported content",
        rationale="import proposal",
        confidence=0.9,
        evidence_refs=[EvidenceRef(ref_id="artifact-import", ref_type="artifact")],
        metadata={"source": "import"},
    )
    worker_proposal = await memory.propose_write(
        scope_id=scope_id,
        partition=MemoryPartition.WORK,
        action=WriteAction.ADD,
        subject_key="work.worker.subject",
        content="worker content",
        rationale="worker proposal",
        confidence=0.9,
        evidence_refs=[EvidenceRef(ref_id="artifact-worker", ref_type="artifact")],
        metadata={"source": "worker"},
    )
    health_proposal = await memory.propose_write(
        scope_id=scope_id,
        partition=MemoryPartition.HEALTH,
        action=WriteAction.ADD,
        subject_key="profile.user.health.note",
        content="sensitive raw record",
        rationale="health note updated",
        confidence=0.95,
        evidence_refs=[EvidenceRef(ref_id="artifact-health", ref_type="artifact")],
        metadata={"source": "worker"},
    )
    await memory.validate_proposal(import_proposal.proposal_id)
    await memory.validate_proposal(worker_proposal.proposal_id)
    await memory.validate_proposal(health_proposal.proposal_id)
    await memory.commit_memory(health_proposal.proposal_id)
    await store_group.conn.commit()


@pytest.mark.asyncio
async def test_explicit_grant_id_must_belong_to_actor(tmp_path: Path) -> None:
    store_group = await create_store_group(str(_db_path(tmp_path)), _artifacts_dir(tmp_path))
    try:
        await init_memory_db(store_group.conn)
        project, workspace = await _create_project(
            store_group,
            project_id="project-default",
            workspace_id="workspace-default",
            scope_id="memory/project-alpha",
        )
        await _seed_bound_memory(store_group, scope_id="memory/project-alpha")
        memory = MemoryService(store_group.conn, store=SqliteMemoryStore(store_group.conn))
        request = await memory.create_vault_access_request(
            project_id=project.project_id,

            scope_id="memory/project-alpha",
            partition=MemoryPartition.HEALTH,
            subject_key="profile.user.health.note",
            requester_actor_id="user:owner",
            requester_actor_label="Owner",
            reason="排障",
        )
        _, grant = await memory.resolve_vault_access_request(
            request.request_id,
            decision=VaultAccessDecision.APPROVE,
            granted_by_actor_id="user:owner",
            granted_by_actor_label="Owner",
        )
        assert grant is not None

        service = MemoryConsoleService(tmp_path, store_group=store_group)
        code, payload, decision = await service.retrieve_vault(
            actor_id="user:intruder",
            actor_label="Intruder",
            active_project_id=project.project_id,

            project_id=project.project_id,

            scope_id="memory/project-alpha",
            partition="health",
            subject_key="profile.user.health.note",
            grant_id=grant.grant_id,
        )

        assert code == "VAULT_AUTHORIZATION_NOT_ALLOWED"
        assert payload == {}
        assert decision.allowed is False
    finally:
        await store_group.conn.close()


@pytest.mark.asyncio
async def test_empty_scope_binding_does_not_leak_proposals(tmp_path: Path) -> None:
    store_group = await create_store_group(str(_db_path(tmp_path)), _artifacts_dir(tmp_path))
    try:
        await init_memory_db(store_group.conn)
        await _create_project(
            store_group,
            project_id="project-default",
            workspace_id="workspace-default",
            scope_id="memory/project-alpha",
        )
        empty_project, empty_workspace = await _create_project(
            store_group,
            project_id="project-empty",
            workspace_id="workspace-empty",
        )
        await _seed_bound_memory(store_group, scope_id="memory/project-alpha")

        service = MemoryConsoleService(tmp_path, store_group=store_group)
        proposal_audit = await service.get_proposal_audit(
            active_project_id=empty_project.project_id,

            project_id=empty_project.project_id,

        )
        overview = await service.get_memory_console(
            project_id=empty_project.project_id,

        )

        assert proposal_audit.items == []
        assert overview.summary.proposal_count == 0
        # scope binding 改动后不再生成 "没有可用的 memory scope" 警告
        # 空 project 的 proposal_audit 应该没有泄漏的 proposals
    finally:
        await store_group.conn.close()


@pytest.mark.asyncio
async def test_export_inspect_rejects_unbound_scope_ids(tmp_path: Path) -> None:
    store_group = await create_store_group(str(_db_path(tmp_path)), _artifacts_dir(tmp_path))
    try:
        await init_memory_db(store_group.conn)
        project, workspace = await _create_project(
            store_group,
            project_id="project-default",
            workspace_id="workspace-default",
            scope_id="memory/project-alpha",
        )
        await _seed_bound_memory(store_group, scope_id="memory/project-alpha")

        service = MemoryConsoleService(tmp_path, store_group=store_group)
        code, payload, decision = await service.inspect_export(
            active_project_id=project.project_id,

            project_id=project.project_id,

            scope_ids=["memory/project-alpha", "memory/orphan-scope"],
            include_vault_refs=True,
        )

        assert code == "MEMORY_EXPORT_INSPECTION_NOT_ALLOWED"
        assert payload == {}
        assert decision.allowed is False
        assert decision.reason_code == "MEMORY_PERMISSION_SCOPE_UNBOUND"
    finally:
        await store_group.conn.close()


@pytest.mark.asyncio
async def test_restore_verify_uses_snapshot_scope_ids_for_conflicts(tmp_path: Path) -> None:
    store_group = await create_store_group(str(_db_path(tmp_path)), _artifacts_dir(tmp_path))
    try:
        await init_memory_db(store_group.conn)
        project, workspace = await _create_project(
            store_group,
            project_id="project-default",
            workspace_id="workspace-default",
            scope_id="memory/project-alpha",
        )
        snapshot_path = tmp_path / "memory-snapshot.json"
        snapshot_path.write_text(
            json.dumps(
                {
                    "scope_ids": ["memory/orphan-scope"],
                    "records": [
                        {
                            "layer": "sor",
                            "status": "current",
                            "scope_id": "memory/orphan-scope",
                            "subject_key": "profile.external.subject",
                        }
                    ],
                    "grants": [],
                }
            ),
            encoding="utf-8",
        )

        service = MemoryConsoleService(tmp_path, store_group=store_group)
        code, payload, decision = await service.verify_restore(
            actor_id="user:web",
            active_project_id=project.project_id,

            project_id=project.project_id,

            snapshot_ref=str(snapshot_path),
        )

        assert code == "MEMORY_RESTORE_VERIFICATION_BLOCKED"
        assert decision.allowed is True
        assert "scope 未绑定到当前 project: memory/orphan-scope" in payload["scope_conflicts"]
    finally:
        await store_group.conn.close()


@pytest.mark.asyncio
async def test_proposal_audit_filters_by_source(tmp_path: Path) -> None:
    store_group = await create_store_group(str(_db_path(tmp_path)), _artifacts_dir(tmp_path))
    try:
        await init_memory_db(store_group.conn)
        project, workspace = await _create_project(
            store_group,
            project_id="project-default",
            workspace_id="workspace-default",
            scope_id="memory/project-alpha",
        )
        await _seed_bound_memory(store_group, scope_id="memory/project-alpha")

        service = MemoryConsoleService(tmp_path, store_group=store_group)
        audit = await service.get_proposal_audit(
            active_project_id=project.project_id,

            project_id=project.project_id,

            source="import",
        )

        assert len(audit.items) == 1
        assert audit.items[0].metadata["source"] == "import"
    finally:
        await store_group.conn.close()
