from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from octoagent.core.models import NormalizedMessage, ProjectBinding, ProjectBindingType
from octoagent.gateway.services.task_service import TaskService
from octoagent.memory import (
    EvidenceRef,
    MemoryPartition,
    MemoryService,
    SqliteMemoryStore,
    WriteAction,
)
from ulid import ULID


@pytest_asyncio.fixture
async def control_plane_app(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OCTOAGENT_DB_PATH", str(tmp_path / "data" / "sqlite" / "test.db"))
    monkeypatch.setenv("OCTOAGENT_ARTIFACTS_DIR", str(tmp_path / "data" / "artifacts"))
    monkeypatch.setenv("OCTOAGENT_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("OCTOAGENT_LLM_MODE", "echo")
    monkeypatch.setenv("LOGFIRE_SEND_TO_LOGFIRE", "false")

    from octoagent.gateway.main import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        yield app


@pytest_asyncio.fixture
async def control_plane_client(control_plane_app) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=control_plane_app),
        base_url="http://test",
    ) as client:
        yield client


async def _create_task(app, *, text: str, thread_id: str = "thread-control") -> str:
    task_service = TaskService(app.state.store_group, app.state.sse_hub)
    task_id, created = await task_service.create_task(
        NormalizedMessage(
            channel="web",
            thread_id=thread_id,
            scope_id="scope-control",
            sender_id="owner",
            sender_name="Owner",
            text=text,
            idempotency_key=f"control:{thread_id}:{text}",
        )
    )
    assert created is True
    return task_id


async def _seed_memory(app) -> dict[str, str]:
    store_group = app.state.store_group
    project = await store_group.project_store.get_default_project()
    assert project is not None
    workspace = await store_group.project_store.get_primary_workspace(project.project_id)
    assert workspace is not None
    scope_id = "memory/project-alpha"
    await store_group.project_store.create_binding(
        ProjectBinding(
            binding_id=str(ULID()),
            project_id=project.project_id,
            workspace_id=workspace.workspace_id,
            binding_type=ProjectBindingType.MEMORY_SCOPE,
            binding_key=scope_id,
            binding_value=scope_id,
            source="tests",
            migration_run_id="memory-test",
        )
    )
    memory_service = MemoryService(
        store_group.conn,
        store=SqliteMemoryStore(store_group.conn),
    )
    summary_proposal = await memory_service.propose_write(
        scope_id=scope_id,
        partition=MemoryPartition.WORK,
        action=WriteAction.ADD,
        subject_key="work.project-alpha.status",
        content="running",
        rationale="project alpha running",
        confidence=0.9,
        evidence_refs=[EvidenceRef(ref_id="artifact-1", ref_type="artifact")],
        metadata={"source": "tests"},
    )
    await memory_service.validate_proposal(summary_proposal.proposal_id)
    await memory_service.commit_memory(summary_proposal.proposal_id)

    vault_proposal = await memory_service.propose_write(
        scope_id=scope_id,
        partition=MemoryPartition.HEALTH,
        action=WriteAction.ADD,
        subject_key="profile.user.health.note",
        content="sensitive raw record",
        rationale="health note updated",
        confidence=0.95,
        evidence_refs=[EvidenceRef(ref_id="artifact-2", ref_type="artifact")],
        metadata={"source": "tests"},
    )
    await memory_service.validate_proposal(vault_proposal.proposal_id)
    vault_commit = await memory_service.commit_memory(vault_proposal.proposal_id)
    assert vault_commit.vault_id is not None
    return {
        "project_id": project.project_id,
        "workspace_id": workspace.workspace_id,
        "scope_id": scope_id,
        "subject_key": "profile.user.health.note",
        "vault_id": vault_commit.vault_id,
    }


@pytest_asyncio.fixture
async def seeded_control_plane(control_plane_app):
    await _create_task(control_plane_app, text="control plane hello")
    return control_plane_app


@pytest_asyncio.fixture
async def seeded_memory_control_plane(control_plane_app):
    await _create_task(control_plane_app, text="control plane hello")
    control_plane_app.state.seeded_memory = await _seed_memory(control_plane_app)
    return control_plane_app


class TestControlPlaneApi:
    async def test_snapshot_returns_six_resources_and_registry(
        self,
        control_plane_client: AsyncClient,
        seeded_memory_control_plane,
    ) -> None:
        resp = await control_plane_client.get("/api/control/snapshot")

        assert resp.status_code == 200
        payload = resp.json()
        assert payload["contract_version"] == "1.0.0"
        assert set(payload["resources"].keys()) == {
            "wizard",
            "config",
            "project_selector",
            "sessions",
            "automation",
            "diagnostics",
            "memory",
        }
        assert payload["registry"]["resource_type"] == "action_registry"
        assert any(item["action_id"] == "project.select" for item in payload["registry"]["actions"])
        assert any(item["action_id"] == "memory.query" for item in payload["registry"]["actions"])
        assert "schema" in payload["resources"]["config"]
        assert "schema_payload" not in payload["resources"]["config"]
        assert payload["resources"]["memory"]["resource_type"] == "memory_console"
        sessions = payload["resources"]["sessions"]["sessions"]
        assert len(sessions) == 1
        assert sessions[0]["latest_message_summary"] == "control plane hello"

        config_resp = await control_plane_client.get("/api/control/resources/config")
        config_payload = config_resp.json()
        assert "schema" in config_payload
        assert "schema_payload" not in config_payload

    async def test_memory_resources_and_vault_authorization_flow(
        self,
        control_plane_client: AsyncClient,
        seeded_memory_control_plane,
    ) -> None:
        seeded = seeded_memory_control_plane.state.seeded_memory

        memory_resp = await control_plane_client.get("/api/control/resources/memory")
        assert memory_resp.status_code == 200
        memory_payload = memory_resp.json()
        assert memory_payload["resource_type"] == "memory_console"
        assert any(
            item["subject_key"] == "work.project-alpha.status"
            for item in memory_payload["records"]
        )

        history_resp = await control_plane_client.get(
            "/api/control/resources/memory-subjects/work.project-alpha.status",
            params={"scope_id": seeded["scope_id"]},
        )
        assert history_resp.status_code == 200
        history_payload = history_resp.json()
        assert history_payload["current_record"]["subject_key"] == "work.project-alpha.status"

        proposal_resp = await control_plane_client.get("/api/control/resources/memory-proposals")
        assert proposal_resp.status_code == 200
        assert proposal_resp.json()["items"]

        request_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "vault.access.request",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "project_id": seeded["project_id"],
                    "workspace_id": seeded["workspace_id"],
                    "scope_id": seeded["scope_id"],
                    "partition": "health",
                    "subject_key": seeded["subject_key"],
                    "reason": "排障需要查看敏感摘要",
                },
            },
        )
        assert request_resp.status_code == 200
        access_request_id = request_resp.json()["result"]["data"]["request_id"]

        resolve_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "vault.access.resolve",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "request_id": access_request_id,
                    "decision": "approve",
                    "expires_in_seconds": 3600,
                },
            },
        )
        assert resolve_resp.status_code == 200
        grant_id = resolve_resp.json()["result"]["data"]["grant_id"]

        retrieve_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "vault.retrieve",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "project_id": seeded["project_id"],
                    "workspace_id": seeded["workspace_id"],
                    "scope_id": seeded["scope_id"],
                    "partition": "health",
                    "subject_key": seeded["subject_key"],
                    "grant_id": grant_id,
                },
            },
        )
        assert retrieve_resp.status_code == 200
        retrieve_payload = retrieve_resp.json()["result"]
        assert retrieve_payload["code"] == "VAULT_RETRIEVE_AUTHORIZED"
        assert retrieve_payload["data"]["results"][0]["vault_id"] == seeded["vault_id"]

        authorization_resp = await control_plane_client.get(
            "/api/control/resources/vault-authorization"
        )
        assert authorization_resp.status_code == 200
        authorization_payload = authorization_resp.json()
        assert authorization_payload["active_grants"]
        assert authorization_payload["recent_retrievals"]

    async def test_memory_export_inspect_and_restore_verify(
        self,
        control_plane_client: AsyncClient,
        seeded_memory_control_plane,
    ) -> None:
        seeded = seeded_memory_control_plane.state.seeded_memory
        snapshot_path = seeded_memory_control_plane.state.project_root / "memory-snapshot.json"
        snapshot_path.write_text(
            json.dumps(
                {
                    "scope_ids": [seeded["scope_id"]],
                    "records": [
                        {
                            "layer": "sor",
                            "status": "current",
                            "scope_id": seeded["scope_id"],
                            "subject_key": "profile.user.health.note",
                        }
                    ],
                    "grants": [],
                }
            ),
            encoding="utf-8",
        )

        export_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "memory.export.inspect",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "project_id": seeded["project_id"],
                    "workspace_id": seeded["workspace_id"],
                    "scope_ids": [seeded["scope_id"]],
                    "include_vault_refs": True,
                },
            },
        )
        assert export_resp.status_code == 200
        assert export_resp.json()["result"]["code"] == "MEMORY_EXPORT_INSPECTION_READY"

        verify_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "memory.restore.verify",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "project_id": seeded["project_id"],
                    "snapshot_ref": "memory-snapshot.json",
                },
            },
        )
        assert verify_resp.status_code == 409
        verify_payload = verify_resp.json()["result"]
        assert verify_payload["code"] == "MEMORY_RESTORE_VERIFICATION_BLOCKED"
        assert verify_payload["data"] == {}

    async def test_session_projection_excludes_control_plane_audit_task(
        self,
        control_plane_client: AsyncClient,
        seeded_control_plane,
    ) -> None:
        events_resp = await control_plane_client.get("/api/control/events")
        assert events_resp.status_code == 200

        sessions_resp = await control_plane_client.get("/api/control/resources/sessions")
        assert sessions_resp.status_code == 200
        payload = sessions_resp.json()
        session_items = payload["sessions"]
        assert len(session_items) == 1
        assert all(item["task_id"] != "ops-control-plane" for item in session_items)
        assert all(item["title"] != "Control Plane Audit" for item in session_items)

    async def test_project_select_action_emits_control_plane_events(
        self,
        control_plane_app,
        control_plane_client: AsyncClient,
    ) -> None:
        default_project = (
            await control_plane_app.state.store_group.project_store.get_default_project()
        )
        assert default_project is not None

        resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "project.select",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "project_id": default_project.project_id,
                },
            },
        )

        assert resp.status_code == 200
        payload = resp.json()
        assert payload["contract_version"] == "1.0.0"
        assert payload["result"]["code"] == "PROJECT_SELECTED"
        assert payload["result"]["data"]["project_id"] == default_project.project_id

        selector_resp = await control_plane_client.get("/api/control/resources/project-selector")
        selector = selector_resp.json()
        assert selector["current_project_id"] == default_project.project_id

        events_resp = await control_plane_client.get("/api/control/events")
        events = events_resp.json()["events"]
        assert any(
            event["event_type"] == "control.action.requested"
            and event["metadata"]["action_id"] == "project.select"
            for event in events
        )
        assert any(
            event["event_type"] == "control.action.completed"
            and event["metadata"]["code"] == "PROJECT_SELECTED"
            for event in events
        )
        assert any(
            event["event_type"] == "control.resource.projected"
            and event["resource_ref"]["resource_type"] == "project_selector"
            for event in events
        )

        after_resp = await control_plane_client.get(
            "/api/control/events",
            params={"after": events[0]["event_id"], "limit": 1},
        )
        assert after_resp.status_code == 200
        after_events = after_resp.json()["events"]
        assert len(after_events) == 1
        assert after_events[0]["event_id"] == events[1]["event_id"]

    async def test_automation_create_and_run_updates_projection(
        self,
        control_plane_app,
        control_plane_client: AsyncClient,
    ) -> None:
        default_project = (
            await control_plane_app.state.store_group.project_store.get_default_project()
        )
        assert default_project is not None
        default_workspace = (
            await control_plane_app.state.store_group.project_store.get_primary_workspace(
                default_project.project_id
            )
        )
        assert default_workspace is not None

        create_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "automation.create",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "name": "diag-check",
                    "action_id": "diagnostics.refresh",
                    "schedule_kind": "interval",
                    "schedule_expr": "3600",
                    "enabled": True,
                },
            },
        )

        assert create_resp.status_code == 200
        job_id = create_resp.json()["result"]["data"]["job_id"]

        run_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "automation.run",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "job_id": job_id,
                },
            },
        )

        assert run_resp.status_code == 202
        await asyncio.sleep(0.2)

        automation_resp = await control_plane_client.get("/api/control/resources/automation")
        payload = automation_resp.json()
        job_item = next(item for item in payload["jobs"] if item["job"]["job_id"] == job_id)
        assert job_item["job"]["action_id"] == "diagnostics.refresh"
        assert job_item["job"]["project_id"] == default_project.project_id
        assert job_item["job"]["workspace_id"] == default_workspace.workspace_id
        assert job_item["last_run"] is not None
        assert job_item["last_run"]["status"] in {"succeeded", "deferred"}

    async def test_automation_create_rejects_unknown_target_action(
        self,
        control_plane_client: AsyncClient,
    ) -> None:
        create_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "automation.create",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "name": "broken-job",
                    "action_id": "diagnostics.refresh.typo",
                    "schedule_kind": "interval",
                    "schedule_expr": "3600",
                    "enabled": True,
                },
            },
        )

        assert create_resp.status_code == 400
        payload = create_resp.json()["result"]
        assert payload["code"] == "AUTOMATION_ACTION_INVALID"

        automation_resp = await control_plane_client.get("/api/control/resources/automation")
        assert automation_resp.status_code == 200
        assert automation_resp.json()["jobs"] == []

    async def test_automation_create_rejects_invalid_schedule_kind(
        self,
        control_plane_client: AsyncClient,
    ) -> None:
        create_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "automation.create",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "name": "invalid-kind-job",
                    "action_id": "diagnostics.refresh",
                    "schedule_kind": "weeklyish",
                    "schedule_expr": "3600",
                    "enabled": True,
                },
            },
        )

        assert create_resp.status_code == 400
        payload = create_resp.json()["result"]
        assert payload["code"] == "SCHEDULE_KIND_INVALID"
