from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient, MockTransport, Response
from octoagent.core.models import (
    NormalizedMessage,
    OrchestratorRequest,
    ProjectBinding,
    ProjectBindingType,
    ProjectSecretBinding,
    SecretBindingStatus,
    SecretRefSourceType,
    SecretTargetKind,
)
from octoagent.gateway.services.task_service import TaskService
from octoagent.memory import (
    EvidenceRef,
    MemoryIngestBatch,
    MemoryIngestItem,
    MemoryPartition,
    MemoryService,
    SqliteMemoryStore,
    WriteAction,
)
from octoagent.provider.dx.project_selector import ProjectSelectorService
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


async def _create_task(
    app,
    *,
    text: str,
    thread_id: str = "thread-control",
    scope_id: str = "scope-control",
) -> str:
    task_service = TaskService(app.state.store_group, app.state.sse_hub)
    task_id, created = await task_service.create_task(
        NormalizedMessage(
            channel="web",
            thread_id=thread_id,
            scope_id=scope_id,
            sender_id="owner",
            sender_name="Owner",
            text=text,
            idempotency_key=f"control:{thread_id}:{text}",
        )
    )
    assert created is True
    return task_id


async def _create_project_with_scope_binding(
    app,
    *,
    name: str,
    slug: str,
    scope_id: str,
):
    selector = ProjectSelectorService(
        app.state.project_root,
        surface="web",
        store_group=app.state.store_group,
    )
    project, _, _ = await selector.create_project(
        name=name,
        slug=slug,
        set_active=False,
    )
    workspace = await app.state.store_group.project_store.get_primary_workspace(project.project_id)
    assert workspace is not None
    await app.state.store_group.project_store.create_binding(
        ProjectBinding(
            binding_id=str(ULID()),
            project_id=project.project_id,
            workspace_id=workspace.workspace_id,
            binding_type=ProjectBindingType.SCOPE,
            binding_key=scope_id,
            binding_value=scope_id,
            source="tests",
            migration_run_id=f"scope-binding-{slug}",
        )
    )
    await app.state.store_group.conn.commit()
    return project, workspace


def _write_wechat_export(path: Path, media_root: Path) -> None:
    media_root.mkdir(parents=True, exist_ok=True)
    (media_root / "alpha.txt").write_text("alpha attachment", encoding="utf-8")
    payload = {
        "account": {"label": "Connor"},
        "conversations": [
            {
                "conversation_key": "team-alpha",
                "label": "Team Alpha",
                "messages": [
                    {
                        "id": "wx-1",
                        "cursor": "cursor-1",
                        "sender_id": "alice",
                        "sender_name": "Alice",
                        "timestamp": datetime.now(tz=UTC).isoformat(),
                        "text": "wechat import from control plane",
                        "attachments": [
                            {
                                "path": "alpha.txt",
                                "filename": "alpha.txt",
                                "mime": "text/plain",
                            }
                        ],
                    }
                ],
            }
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


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
    await memory_service.ingest_memory_batch(
        MemoryIngestBatch(
            ingest_id="seeded-memory-ingest",
            scope_id=scope_id,
            partition=MemoryPartition.WORK,
            idempotency_key="seeded-memory-ingest",
            items=[
                MemoryIngestItem(
                    item_id="seeded-derived",
                    modality="text",
                    artifact_ref="artifact-derived-1",
                    metadata={
                        "text": "Connor owns project alpha and plans more tests.",
                        "subject_key": "work.project-alpha.status",
                        "entity": "Connor",
                        "relation": "owns",
                        "relation_target": "project-alpha",
                        "tom_summary": "Owner 认为项目还需要补测试。",
                    },
                )
            ],
        )
    )
    return {
        "project_id": project.project_id,
        "workspace_id": workspace.workspace_id,
        "scope_id": scope_id,
        "subject_key": "profile.user.health.note",
        "vault_id": vault_commit.vault_id,
    }


async def _seed_memu_bridge(app, *, base_url: str = "https://workspace.memu.test") -> None:
    store_group = app.state.store_group
    project = await store_group.project_store.get_default_project()
    assert project is not None
    workspace = await store_group.project_store.get_primary_workspace(project.project_id)
    assert workspace is not None
    await store_group.project_store.create_binding(
        ProjectBinding(
            binding_id=str(ULID()),
            project_id=project.project_id,
            workspace_id=workspace.workspace_id,
            binding_type=ProjectBindingType.MEMORY_BRIDGE,
            binding_key="memu.primary",
            binding_value=base_url,
            source="tests",
            migration_run_id="memory-bridge-test",
            metadata={"api_key_target_key": "memory.memu.api_key"},
        )
    )
    await store_group.project_store.save_secret_binding(
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
    await store_group.conn.commit()


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
    async def test_snapshot_returns_control_plane_resources_and_registry(
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
            "capability_pack",
            "delegation",
            "pipelines",
            "automation",
            "diagnostics",
            "memory",
            "imports",
        }
        assert payload["registry"]["resource_type"] == "action_registry"
        assert any(item["action_id"] == "project.select" for item in payload["registry"]["actions"])
        assert any(item["action_id"] == "memory.query" for item in payload["registry"]["actions"])
        assert any(item["action_id"] == "work.cancel" for item in payload["registry"]["actions"])
        assert any(
            item["action_id"] == "pipeline.resume" for item in payload["registry"]["actions"]
        )
        assert any(
            item["action_id"] == "import.source.detect" for item in payload["registry"]["actions"]
        )
        assert "schema" in payload["resources"]["config"]
        assert "schema_payload" not in payload["resources"]["config"]
        assert payload["resources"]["memory"]["resource_type"] == "memory_console"
        assert payload["resources"]["memory"]["backend_id"]
        assert payload["resources"]["memory"]["retrieval_backend"]
        assert "index_health" in payload["resources"]["memory"]
        assert payload["resources"]["imports"]["resource_type"] == "import_workbench"
        sessions = payload["resources"]["sessions"]["sessions"]
        assert len(sessions) == 1
        assert sessions[0]["latest_message_summary"] == "control plane hello"
        capability_pack = payload["resources"]["capability_pack"]
        assert capability_pack["resource_type"] == "capability_pack"
        assert capability_pack["pack"]["tools"]
        assert payload["resources"]["delegation"]["works"] == []
        assert payload["resources"]["pipelines"]["runs"] == []

        config_resp = await control_plane_client.get("/api/control/resources/config")
        config_payload = config_resp.json()
        assert "schema" in config_payload
        assert "schema_payload" not in config_payload

    async def test_capability_refresh_action_invokes_refresh(
        self,
        control_plane_app,
        control_plane_client: AsyncClient,
        monkeypatch,
    ) -> None:
        calls: list[str] = []

        async def fake_refresh():
            calls.append("refresh")
            return await control_plane_app.state.capability_pack_service.get_pack()

        monkeypatch.setattr(
            control_plane_app.state.capability_pack_service,
            "refresh",
            fake_refresh,
        )

        resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "capability.refresh",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {},
            },
        )

        assert resp.status_code == 200
        payload = resp.json()["result"]
        assert payload["code"] == "CAPABILITY_REFRESHED"
        assert calls == ["refresh"]
        assert payload["resource_refs"] == [
            {
                "resource_type": "capability_pack",
                "resource_id": "capability:bundled",
                "schema_version": 1,
            }
        ]

    async def test_snapshot_exposes_builtin_tool_catalog_and_work_split_merge_actions(
        self,
        control_plane_client: AsyncClient,
        seeded_control_plane,
    ) -> None:
        resp = await control_plane_client.get("/api/control/snapshot")

        assert resp.status_code == 200
        payload = resp.json()
        pack = payload["resources"]["capability_pack"]["pack"]
        tool_names = {item["tool_name"] for item in pack["tools"]}
        assert len(pack["tools"]) >= 15
        assert {
            "subagents.spawn",
            "work.split",
            "work.merge",
            "web.fetch",
            "web.search",
            "browser.status",
            "memory.search",
        }.issubset(tool_names)

        spawn_tool = next(item for item in pack["tools"] if item["tool_name"] == "subagents.spawn")
        assert spawn_tool["availability"] == "available"
        assert "agent_runtime" in spawn_tool["entrypoints"]

        action_ids = {item["action_id"] for item in payload["registry"]["actions"]}
        assert "work.split" in action_ids
        assert "work.merge" in action_ids

    async def test_pipeline_resource_is_explicitly_marked_as_delegation_projection(
        self,
        control_plane_client: AsyncClient,
        seeded_control_plane,
    ) -> None:
        resp = await control_plane_client.get("/api/control/resources/pipelines")

        assert resp.status_code == 200
        payload = resp.json()
        assert payload["resource_type"] == "skill_pipeline"
        assert payload["degraded"]["is_degraded"] is True
        assert "graph_runtime_projection_unavailable" in payload["degraded"]["reasons"]
        assert payload["summary"]["source"] == "delegation_plane_pipeline_runs"
        assert payload["summary"]["graph_runtime_projection"] == "unavailable"
        assert any("delegation preflight" in item for item in payload["warnings"])

    async def test_work_split_and_merge_actions_create_child_work_lifecycle(
        self,
        control_plane_client: AsyncClient,
        control_plane_app,
    ) -> None:
        task_id = await _create_task(
            control_plane_app,
            text="请把当前工作拆分给两个 child workers",
            thread_id="thread-work-split",
            scope_id="scope-control",
        )
        plan = await control_plane_app.state.delegation_plane_service.prepare_dispatch(
            OrchestratorRequest(
                task_id=task_id,
                trace_id=f"trace-{task_id}",
                user_text="请把当前工作拆分给两个 child workers",
                worker_capability="llm_generation",
                metadata={},
            )
        )

        split_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "work.split",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "work_id": plan.work.work_id,
                    "objectives": ["先调研当前实现", "再补关键测试"],
                    "worker_type": "research",
                    "target_kind": "subagent",
                },
            },
        )

        assert split_resp.status_code == 200
        split_payload = split_resp.json()["result"]
        assert split_payload["code"] == "WORK_SPLIT_ACCEPTED"
        assert len(split_payload["data"]["child_tasks"]) == 2

        child_works = []
        for _ in range(30):
            child_works = await control_plane_app.state.store_group.work_store.list_works(
                parent_work_id=plan.work.work_id
            )
            if len(child_works) >= 2:
                break
            await asyncio.sleep(0.05)

        assert len(child_works) == 2
        assert {item.parent_work_id for item in child_works} == {plan.work.work_id}
        assert {item.selected_worker_type.value for item in child_works} == {"research"}
        assert {item.target_kind.value for item in child_works} == {"subagent"}

        for _ in range(30):
            child_works = await control_plane_app.state.store_group.work_store.list_works(
                parent_work_id=plan.work.work_id
            )
            if all(
                item.status.value in {"succeeded", "failed", "cancelled", "merged"}
                for item in child_works
            ):
                break
            await asyncio.sleep(0.05)

        merge_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "work.merge",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "work_id": plan.work.work_id,
                    "summary": "child works merged after completion",
                },
            },
        )

        assert merge_resp.status_code == 200
        merge_payload = merge_resp.json()["result"]
        assert merge_payload["code"] == "WORK_MERGED"
        merged = await control_plane_app.state.store_group.work_store.get_work(plan.work.work_id)
        assert merged is not None
        assert merged.status.value == "merged"

    async def test_import_workbench_detect_preview_run_and_inspect(
        self,
        control_plane_client: AsyncClient,
        control_plane_app,
    ) -> None:
        export_path = control_plane_app.state.project_root / "wechat-export.json"
        media_root = control_plane_app.state.project_root / "wechat-media"
        _write_wechat_export(export_path, media_root)

        detect_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "import.source.detect",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "source_type": "wechat",
                    "input_path": str(export_path),
                    "media_root": str(media_root),
                    "format_hint": "json",
                },
            },
        )

        assert detect_resp.status_code == 200
        detect_payload = detect_resp.json()["result"]
        assert detect_payload["code"] == "IMPORT_SOURCE_DETECTED"
        source_id = detect_payload["data"]["source_id"]

        mapping_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "import.mapping.save",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "source_id": source_id,
                },
            },
        )

        assert mapping_resp.status_code == 200
        mapping_id = mapping_resp.json()["result"]["data"]["mapping_id"]

        preview_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "import.preview",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "source_id": source_id,
                    "mapping_id": mapping_id,
                },
            },
        )

        assert preview_resp.status_code == 200
        preview_payload = preview_resp.json()["result"]["data"]
        assert preview_payload["status"] == "ready_to_run"
        assert preview_payload["summary"]["attachment_count"] == 1

        run_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "import.run",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "source_id": source_id,
                    "mapping_id": mapping_id,
                },
            },
        )

        assert run_resp.status_code == 200
        run_payload = run_resp.json()["result"]["data"]
        run_id = run_payload["resource_id"]
        assert run_payload["status"] == "completed"
        assert run_payload["summary"]["imported_count"] == 1
        assert run_payload["summary"]["attachment_artifact_count"] == 1

        workbench_resp = await control_plane_client.get("/api/control/resources/import-workbench")
        assert workbench_resp.status_code == 200
        workbench_payload = workbench_resp.json()
        assert workbench_payload["resource_type"] == "import_workbench"
        assert any(item["source_id"] == source_id for item in workbench_payload["sources"])
        assert any(item["resource_id"] == run_id for item in workbench_payload["recent_runs"])

        source_resp = await control_plane_client.get(
            f"/api/control/resources/import-sources/{source_id}"
        )
        assert source_resp.status_code == 200
        assert source_resp.json()["source_type"] == "wechat"

        report_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "import.report.inspect",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "run_id": run_id,
                },
            },
        )

        assert report_resp.status_code == 200
        assert report_resp.json()["result"]["data"]["resource_id"] == run_id

        run_detail_resp = await control_plane_client.get(
            f"/api/control/resources/import-runs/{run_id}"
        )
        assert run_detail_resp.status_code == 200
        assert run_detail_resp.json()["status"] == "completed"

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
        assert memory_payload["backend_id"]
        assert memory_payload["retrieval_backend"]
        assert "backend_diagnostics" in memory_payload["advanced_refs"]
        assert any(
            item["subject_key"] == "work.project-alpha.status" for item in memory_payload["records"]
        )
        assert any(item["layer"] == "derived" for item in memory_payload["records"])

        history_resp = await control_plane_client.get(
            "/api/control/resources/memory-subjects/work.project-alpha.status",
            params={"scope_id": seeded["scope_id"]},
        )
        assert history_resp.status_code == 200
        history_payload = history_resp.json()
        assert history_payload["current_record"]["subject_key"] == "work.project-alpha.status"
        assert "retrieval_backend" in history_payload

        proposal_resp = await control_plane_client.get("/api/control/resources/memory-proposals")
        assert proposal_resp.status_code == 200
        assert proposal_resp.json()["items"]

        diagnostics_resp = await control_plane_client.get("/api/control/resources/diagnostics")
        diagnostics_payload = diagnostics_resp.json()
        assert any(item["subsystem_id"] == "memory" for item in diagnostics_payload["subsystems"])
        assert "memory" in diagnostics_payload["deep_refs"]

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

    async def test_memory_maintenance_actions_are_registered_and_runnable(
        self,
        control_plane_client: AsyncClient,
        seeded_memory_control_plane,
    ) -> None:
        seeded = seeded_memory_control_plane.state.seeded_memory

        snapshot_resp = await control_plane_client.get("/api/control/snapshot")
        actions = snapshot_resp.json()["registry"]["actions"]
        assert any(item["action_id"] == "memory.flush" for item in actions)
        assert any(item["action_id"] == "memory.reindex" for item in actions)
        assert any(item["action_id"] == "memory.bridge.reconnect" for item in actions)
        assert any(item["action_id"] == "memory.sync.resume" for item in actions)

        flush_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "memory.flush",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "project_id": seeded["project_id"],
                    "workspace_id": seeded["workspace_id"],
                    "scope_id": seeded["scope_id"],
                    "partition": "work",
                    "summary": "最近对话主要集中在 project alpha 测试与交付。",
                    "evidence_refs": [{"ref_id": "artifact-flush-1", "ref_type": "artifact"}],
                },
            },
        )
        assert flush_resp.status_code == 200
        flush_result = flush_resp.json()["result"]
        assert flush_result["code"] == "MEMORY_FLUSH_COMPLETED"
        assert flush_result["data"]["run_id"]
        assert flush_result["data"]["status"] in {"completed", "degraded"}
        resource_types = {item["resource_type"] for item in flush_result["resource_refs"]}
        assert {"memory_console", "diagnostics_summary"} <= resource_types

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

    async def test_memory_resources_use_project_scoped_memu_bridge_status(
        self,
        control_plane_client: AsyncClient,
        seeded_memory_control_plane,
        monkeypatch,
    ) -> None:
        seen_hosts: list[str] = []

        def handler(request):
            seen_hosts.append(request.url.host or "")
            assert request.headers["Authorization"] == "Bearer memu-secret"
            return Response(
                200,
                json={
                    "status": {
                        "backend_id": "memu",
                        "state": "healthy",
                        "active_backend": "memu",
                        "index_health": {"documents": 24},
                        "last_ingest_at": "2026-03-08T05:00:00+00:00",
                        "last_maintenance_at": "2026-03-08T05:10:00+00:00",
                    }
                },
            )

        monkeypatch.setenv("MEMU_API_KEY", "memu-secret")
        monkeypatch.setattr(
            "octoagent.memory.backends.http_bridge.httpx.AsyncClient",
            lambda *args, **kwargs: AsyncClient(transport=MockTransport(handler)),
        )
        await _seed_memu_bridge(seeded_memory_control_plane)

        memory_resp = await control_plane_client.get("/api/control/resources/memory")
        diagnostics_resp = await control_plane_client.get("/api/control/resources/diagnostics")

        assert memory_resp.status_code == 200
        memory_payload = memory_resp.json()
        assert memory_payload["backend_id"] == "memu"
        assert memory_payload["retrieval_backend"] == "memu"
        assert memory_payload["index_health"]["documents"] == 24
        assert memory_payload["index_health"]["project_binding"].endswith("/memu.primary")
        assert memory_payload["index_health"]["last_ingest_at"] == "2026-03-08T05:00:00+00:00"
        assert memory_payload["index_health"]["last_maintenance_at"] == "2026-03-08T05:10:00+00:00"

        assert diagnostics_resp.status_code == 200
        diagnostics_payload = diagnostics_resp.json()
        memory_subsystem = next(
            item for item in diagnostics_payload["subsystems"] if item["subsystem_id"] == "memory"
        )
        assert memory_subsystem["status"] == "healthy"
        assert any(
            item.startswith("project_binding=") and item.endswith("/memu.primary")
            for item in memory_subsystem["warnings"]
        )
        assert "last_ingest_at=2026-03-08T05:00:00+00:00" in memory_subsystem["warnings"]
        assert "last_maintenance_at=2026-03-08T05:10:00+00:00" in memory_subsystem["warnings"]
        assert len(seen_hosts) >= 2
        assert set(seen_hosts) == {"workspace.memu.test"}

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

    async def test_session_projection_scopes_items_to_selected_project(
        self,
        control_plane_app,
        control_plane_client: AsyncClient,
    ) -> None:
        beta_project, _ = await _create_project_with_scope_binding(
            control_plane_app,
            name="Beta",
            slug="beta",
            scope_id="chat:web:thread-beta",
        )

        default_task_id = await _create_task(
            control_plane_app,
            text="default session",
            thread_id="thread-default",
            scope_id="scope-control",
        )
        beta_task_id = await _create_task(
            control_plane_app,
            text="beta session",
            thread_id="thread-beta",
            scope_id="chat:web:thread-beta",
        )

        default_sessions = await control_plane_client.get("/api/control/resources/sessions")
        assert default_sessions.status_code == 200
        default_task_ids = {item["task_id"] for item in default_sessions.json()["sessions"]}
        assert default_task_id in default_task_ids
        assert beta_task_id not in default_task_ids

        select_beta = await control_plane_client.post(
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
                    "project_id": beta_project.project_id,
                },
            },
        )
        assert select_beta.status_code == 200

        beta_sessions = await control_plane_client.get("/api/control/resources/sessions")
        assert beta_sessions.status_code == 200
        beta_task_ids = {item["task_id"] for item in beta_sessions.json()["sessions"]}
        assert beta_task_id in beta_task_ids
        assert default_task_id not in beta_task_ids

    async def test_raw_task_routes_scope_items_to_selected_project(
        self,
        control_plane_app,
        control_plane_client: AsyncClient,
    ) -> None:
        beta_project, _ = await _create_project_with_scope_binding(
            control_plane_app,
            name="Beta Tasks",
            slug="beta-tasks",
            scope_id="chat:web:thread-beta-tasks",
        )

        default_task_id = await _create_task(
            control_plane_app,
            text="default raw task",
            thread_id="thread-default-raw",
            scope_id="scope-default-raw",
        )
        beta_task_id = await _create_task(
            control_plane_app,
            text="beta raw task",
            thread_id="thread-beta-tasks",
            scope_id="chat:web:thread-beta-tasks",
        )

        select_beta = await control_plane_client.post(
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
                    "project_id": beta_project.project_id,
                },
            },
        )
        assert select_beta.status_code == 200

        list_resp = await control_plane_client.get("/api/tasks")
        assert list_resp.status_code == 200
        listed_task_ids = {item["task_id"] for item in list_resp.json()["tasks"]}
        assert beta_task_id in listed_task_ids
        assert default_task_id not in listed_task_ids

        detail_resp = await control_plane_client.get(f"/api/tasks/{default_task_id}")
        assert detail_resp.status_code == 403
        assert detail_resp.json()["error"]["code"] == "TASK_SCOPE_NOT_ALLOWED"

        checkpoint_resp = await control_plane_client.get(
            f"/api/tasks/{default_task_id}/checkpoints"
        )
        assert checkpoint_resp.status_code == 403
        assert checkpoint_resp.json()["error"]["code"] == "TASK_SCOPE_NOT_ALLOWED"

        stream_resp = await control_plane_client.get(f"/api/stream/task/{default_task_id}")
        assert stream_resp.status_code == 403
        assert stream_resp.json()["error"]["code"] == "TASK_SCOPE_NOT_ALLOWED"

        execution_resp = await control_plane_client.get(f"/api/tasks/{default_task_id}/execution")
        assert execution_resp.status_code == 403
        assert execution_resp.json()["error"]["code"] == "TASK_SCOPE_NOT_ALLOWED"

        execution_events_resp = await control_plane_client.get(
            f"/api/tasks/{default_task_id}/execution/events"
        )
        assert execution_events_resp.status_code == 403
        assert execution_events_resp.json()["error"]["code"] == "TASK_SCOPE_NOT_ALLOWED"

        execution_input_resp = await control_plane_client.post(
            f"/api/tasks/{default_task_id}/execution/input",
            json={"text": "cross-project input"},
        )
        assert execution_input_resp.status_code == 403
        assert execution_input_resp.json()["error"]["code"] == "TASK_SCOPE_NOT_ALLOWED"

        resume_resp = await control_plane_client.post(f"/api/tasks/{default_task_id}/resume")
        assert resume_resp.status_code == 403
        assert resume_resp.json()["error"]["code"] == "TASK_SCOPE_NOT_ALLOWED"

        cancel_resp = await control_plane_client.post(f"/api/tasks/{default_task_id}/cancel")
        assert cancel_resp.status_code == 403
        assert cancel_resp.json()["error"]["code"] == "TASK_SCOPE_NOT_ALLOWED"

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

    async def test_automation_projection_and_actions_scope_to_selected_project(
        self,
        control_plane_app,
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
                    "name": "default-only-job",
                    "action_id": "diagnostics.refresh",
                    "schedule_kind": "interval",
                    "schedule_expr": "3600",
                    "enabled": True,
                },
            },
        )
        assert create_resp.status_code == 200
        job_id = create_resp.json()["result"]["data"]["job_id"]

        selector = ProjectSelectorService(
            control_plane_app.state.project_root,
            surface="web",
            store_group=control_plane_app.state.store_group,
        )
        beta_project, _, _ = await selector.create_project(
            name="Beta Jobs",
            slug="beta-jobs",
            set_active=False,
        )

        select_beta = await control_plane_client.post(
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
                    "project_id": beta_project.project_id,
                },
            },
        )
        assert select_beta.status_code == 200

        automation_resp = await control_plane_client.get("/api/control/resources/automation")
        assert automation_resp.status_code == 200
        assert all(item["job"]["job_id"] != job_id for item in automation_resp.json()["jobs"])

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
        assert run_resp.status_code == 403
        assert run_resp.json()["result"]["code"] == "PROJECT_SCOPE_NOT_ALLOWED"

    async def test_import_details_and_actions_reject_cross_project_access(
        self,
        control_plane_app,
        control_plane_client: AsyncClient,
        tmp_path: Path,
    ) -> None:
        export_path = tmp_path / "wechat-export.json"
        media_root = tmp_path / "wechat-media"
        _write_wechat_export(export_path, media_root)

        detect_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "import.source.detect",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "source_type": "wechat",
                    "input_path": str(export_path),
                    "media_root": str(media_root),
                    "format_hint": "json",
                },
            },
        )
        assert detect_resp.status_code == 200
        source_id = detect_resp.json()["result"]["data"]["source_id"]

        mapping_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "import.mapping.save",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "source_id": source_id,
                },
            },
        )
        assert mapping_resp.status_code == 200
        mapping_id = mapping_resp.json()["result"]["data"]["mapping_id"]

        preview_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "import.preview",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "source_id": source_id,
                    "mapping_id": mapping_id,
                },
            },
        )
        assert preview_resp.status_code == 200
        run_id = preview_resp.json()["result"]["data"]["resource_id"]

        selector = ProjectSelectorService(
            control_plane_app.state.project_root,
            surface="web",
            store_group=control_plane_app.state.store_group,
        )
        beta_project, _, _ = await selector.create_project(
            name="Beta Import Access",
            slug="beta-import-access",
            set_active=False,
        )

        select_beta = await control_plane_client.post(
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
                    "project_id": beta_project.project_id,
                },
            },
        )
        assert select_beta.status_code == 200

        source_resp = await control_plane_client.get(
            f"/api/control/resources/import-sources/{source_id}"
        )
        assert source_resp.status_code == 403
        assert source_resp.json()["error"]["code"] == "IMPORT_SOURCE_NOT_ALLOWED"

        run_detail_resp = await control_plane_client.get(
            f"/api/control/resources/import-runs/{run_id}"
        )
        assert run_detail_resp.status_code == 403
        assert run_detail_resp.json()["error"]["code"] == "IMPORT_REPORT_NOT_ALLOWED"

        rerun_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "import.run",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "source_id": source_id,
                    "mapping_id": mapping_id,
                },
            },
        )
        assert rerun_resp.status_code == 403
        assert rerun_resp.json()["result"]["code"] == "IMPORT_SOURCE_NOT_ALLOWED"

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
