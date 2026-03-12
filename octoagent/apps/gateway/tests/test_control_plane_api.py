from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient, MockTransport, Response
from octoagent.core.models import (
    AgentProfile,
    AgentProfileScope,
    BootstrapSession,
    BootstrapSessionStatus,
    ContextFrame,
    DelegationTargetKind,
    NormalizedMessage,
    OrchestratorRequest,
    OwnerOverlayScope,
    OwnerProfile,
    OwnerProfileOverlay,
    ProjectBinding,
    ProjectBindingType,
    ProjectSecretBinding,
    ProjectSelectorState,
    SecretBindingStatus,
    SecretRefSourceType,
    SecretTargetKind,
    SessionContextState,
    ToolIndexQuery,
    Work,
    WorkKind,
    WorkerProfile,
    WorkerProfileOriginKind,
    WorkerProfileRevision,
    WorkerProfileStatus,
    WorkerType,
    WorkStatus,
    Workspace,
    WorkspaceKind,
)
from octoagent.gateway.services.agent_context import build_scope_aware_session_id
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
from octoagent.provider.auth.credentials import OAuthCredential
from octoagent.provider.auth.environment import EnvironmentContext
from octoagent.provider.auth.store import CredentialStore
from octoagent.provider.dx.project_selector import ProjectSelectorService
from pydantic import SecretStr
from ulid import ULID


def _configure_control_plane_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OCTOAGENT_DB_PATH", str(tmp_path / "data" / "sqlite" / "test.db"))
    monkeypatch.setenv("OCTOAGENT_ARTIFACTS_DIR", str(tmp_path / "data" / "artifacts"))
    monkeypatch.setenv("OCTOAGENT_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("OCTOAGENT_LLM_MODE", "echo")
    monkeypatch.setenv("LOGFIRE_SEND_TO_LOGFIRE", "false")


@pytest_asyncio.fixture
async def control_plane_app(tmp_path: Path, monkeypatch):
    _configure_control_plane_env(tmp_path, monkeypatch)

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


async def _seed_context_resources(app) -> None:
    store_group = app.state.store_group
    project = await store_group.project_store.get_default_project()
    assert project is not None
    workspace = await store_group.project_store.get_primary_workspace(project.project_id)
    assert workspace is not None
    await store_group.agent_context_store.save_agent_profile(
        AgentProfile(
            profile_id="agent-profile-default",
            scope=AgentProfileScope.PROJECT,
            project_id=project.project_id,
            name="Default Agent",
            persona_summary="用于 control plane 可视化的默认 profile。",
        )
    )
    await store_group.project_store.save_project(
        project.model_copy(update={"default_agent_profile_id": "agent-profile-default"})
    )
    await store_group.agent_context_store.save_owner_profile(
        OwnerProfile(
            owner_profile_id="owner-profile-default",
            display_name="Connor",
            working_style="控制面应可见 owner profile。",
        )
    )
    await store_group.agent_context_store.save_owner_overlay(
        OwnerProfileOverlay(
            owner_overlay_id="owner-overlay-default",
            owner_profile_id="owner-profile-default",
            scope=OwnerOverlayScope.PROJECT,
            project_id=project.project_id,
            assistant_identity_overrides={"assistant_name": "Default Agent"},
        )
    )
    await store_group.agent_context_store.save_bootstrap_session(
        BootstrapSession(
            bootstrap_id="bootstrap-default",
            project_id=project.project_id,
            workspace_id=workspace.workspace_id,
            owner_profile_id="owner-profile-default",
            owner_overlay_id="owner-overlay-default",
            agent_profile_id="agent-profile-default",
            status=BootstrapSessionStatus.COMPLETED,
            current_step="done",
            answers={"assistant_identity": "Default Agent"},
        )
    )
    await store_group.agent_context_store.save_session_context(
        SessionContextState(
            session_id="thread-control-context",
            thread_id="thread-control-context",
            project_id=project.project_id,
            workspace_id=workspace.workspace_id,
            task_ids=["task-context"],
            rolling_summary="控制面可以直接看到 recent summary。",
            last_context_frame_id="context-frame-default",
        )
    )
    await store_group.agent_context_store.save_context_frame(
        ContextFrame(
            context_frame_id="context-frame-default",
            task_id="task-context",
            session_id="thread-control-context",
            project_id=project.project_id,
            workspace_id=workspace.workspace_id,
            agent_profile_id="agent-profile-default",
            owner_profile_id="owner-profile-default",
            owner_overlay_id="owner-overlay-default",
            bootstrap_session_id="bootstrap-default",
            recent_summary="控制面可以直接看到 recent summary。",
            memory_hits=[
                {
                    "record_id": "memory-1",
                    "summary": "memory visible",
                    "search_query": "project alpha next step",
                    "content_preview": "Owner confirmed project alpha still needs tests.",
                    "citation": {
                        "label": "project-alpha brief",
                        "artifact_ref": "artifact-1",
                    },
                }
            ],
            budget={
                "history_tokens": 128,
                "system_tokens": 96,
                "final_prompt_tokens": 256,
                "memory_scope_ids": ["memory/project-alpha"],
                "memory_recall": {
                    "backend_id": "sqlite",
                    "retrieval_backend": "sqlite",
                    "search_query": "project alpha next step",
                    "expanded_queries": [
                        "project alpha next step",
                        "project alpha brief",
                    ],
                    "scope_ids": ["memory/project-alpha"],
                    "hit_count": 1,
                },
            },
            source_refs=[
                {
                    "ref_type": "memory_scope",
                    "ref_id": "memory/project-alpha",
                    "label": "项目记忆 scope",
                    "metadata": {"query": "project alpha next step"},
                },
                {
                    "ref_type": "memory_record",
                    "ref_id": "memory-1",
                    "label": "project-alpha brief",
                    "metadata": {"partition": "work"},
                },
            ],
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
    await _seed_context_resources(control_plane_app)
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
            "agent_profiles",
            "worker_profiles",
            "owner_profile",
            "bootstrap_session",
            "context_continuity",
            "policy_profiles",
            "capability_pack",
            "skill_governance",
            "setup_governance",
            "delegation",
            "pipelines",
            "automation",
            "diagnostics",
            "memory",
            "imports",
        }
        assert payload["registry"]["resource_type"] == "action_registry"
        assert any(item["action_id"] == "project.select" for item in payload["registry"]["actions"])
        assert any(item["action_id"] == "setup.review" for item in payload["registry"]["actions"])
        assert any(item["action_id"] == "setup.apply" for item in payload["registry"]["actions"])
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
        assert payload["resources"]["agent_profiles"]["profiles"][0]["profile_id"] == (
            "agent-profile-default"
        )
        worker_profile = payload["resources"]["worker_profiles"]["profiles"][0]
        assert worker_profile["profile_id"] == "singleton:general"
        assert worker_profile["mode"] == "singleton"
        assert worker_profile["static_config"]["base_archetype"] == "general"
        assert "active_work_count" in worker_profile["dynamic_context"]
        assert payload["resources"]["owner_profile"]["profile"]["owner_profile_id"] == (
            "owner-profile-default"
        )
        assert payload["resources"]["bootstrap_session"]["session"]["bootstrap_id"] == (
            "bootstrap-default"
        )
        assert payload["resources"]["context_continuity"]["frames"][0]["context_frame_id"] == (
            "context-frame-default"
        )
        frame = payload["resources"]["context_continuity"]["frames"][0]
        assert frame["project_id"]
        assert frame["workspace_id"]
        assert frame["memory_hit_count"] == 1
        assert frame["memory_hits"][0]["search_query"] == "project alpha next step"
        assert frame["memory_recall"]["backend_id"] == "sqlite"
        assert frame["memory_recall"]["expanded_queries"] == [
            "project alpha next step",
            "project alpha brief",
        ]
        assert frame["budget"]["final_prompt_tokens"] == 256
        assert frame["source_refs"][0]["ref_type"] == "memory_scope"
        assert payload["resources"]["policy_profiles"]["active_profile_id"] == "default"
        assert payload["resources"]["skill_governance"]["resource_type"] == "skill_governance"
        assert payload["resources"]["setup_governance"]["resource_type"] == "setup_governance"
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

        profiles_resp = await control_plane_client.get("/api/control/resources/agent-profiles")
        worker_profiles_resp = await control_plane_client.get("/api/control/resources/worker-profiles")
        owner_resp = await control_plane_client.get("/api/control/resources/owner-profile")
        bootstrap_resp = await control_plane_client.get("/api/control/resources/bootstrap-session")
        context_resp = await control_plane_client.get("/api/control/resources/context-frames")
        policy_resp = await control_plane_client.get("/api/control/resources/policy-profiles")
        skill_resp = await control_plane_client.get("/api/control/resources/skill-governance")
        setup_resp = await control_plane_client.get("/api/control/resources/setup-governance")
        assert profiles_resp.status_code == 200
        assert worker_profiles_resp.status_code == 200
        assert owner_resp.status_code == 200
        assert bootstrap_resp.status_code == 200
        assert context_resp.status_code == 200
        context_payload = context_resp.json()
        assert context_payload["resource_type"] == "context_continuity"
        assert context_payload["frames"][0]["memory_recall"]["hit_count"] == 1
        assert context_payload["frames"][0]["source_refs"][1]["ref_id"] == "memory-1"
        assert policy_resp.status_code == 200
        assert skill_resp.status_code == 200
        assert setup_resp.status_code == 200
        worker_profiles_payload = worker_profiles_resp.json()
        assert worker_profiles_payload["resource_type"] == "worker_profiles"
        assert worker_profiles_payload["profiles"][0]["dynamic_context"]["active_project_id"]

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

    async def test_context_frames_resource_filters_workspace_before_limit(
        self,
        control_plane_app,
        control_plane_client: AsyncClient,
    ) -> None:
        await _seed_context_resources(control_plane_app)
        store_group = control_plane_app.state.store_group
        project = await store_group.project_store.get_default_project()
        assert project is not None
        primary_workspace = await store_group.project_store.get_primary_workspace(
            project.project_id
        )
        assert primary_workspace is not None
        secondary_workspace = Workspace(
            workspace_id="workspace-secondary",
            project_id=project.project_id,
            slug="secondary",
            name="Secondary",
            kind=WorkspaceKind.CHAT,
            root_path=str(control_plane_app.state.project_root / "secondary"),
        )
        await store_group.project_store.create_workspace(secondary_workspace)
        await store_group.project_store.save_selector_state(
            ProjectSelectorState(
                selector_id="selector-web",
                surface="web",
                active_project_id=project.project_id,
                active_workspace_id=secondary_workspace.workspace_id,
                source="tests",
            )
        )
        for index in range(25):
            await store_group.agent_context_store.save_context_frame(
                ContextFrame(
                    context_frame_id=f"context-frame-primary-{index}",
                    task_id=f"task-primary-{index}",
                    session_id=f"session-primary-{index}",
                    project_id=project.project_id,
                    workspace_id=primary_workspace.workspace_id,
                    agent_profile_id="agent-profile-default",
                    owner_profile_id="owner-profile-default",
                    created_at=datetime(2026, 3, 9, 10, index % 60, tzinfo=UTC),
                )
            )
        await store_group.agent_context_store.save_context_frame(
            ContextFrame(
                context_frame_id="context-frame-secondary",
                task_id="task-secondary",
                session_id="session-secondary",
                project_id=project.project_id,
                workspace_id=secondary_workspace.workspace_id,
                agent_profile_id="agent-profile-default",
                owner_profile_id="owner-profile-default",
                created_at=datetime(2026, 3, 9, 11, 0, tzinfo=UTC),
            )
        )
        await store_group.conn.commit()

        resp = await control_plane_client.get("/api/control/resources/context-frames")

        assert resp.status_code == 200
        payload = resp.json()
        assert payload["active_workspace_id"] == "workspace-secondary"
        assert [item["context_frame_id"] for item in payload["frames"]] == [
            "context-frame-secondary"
        ]

    async def test_setup_governance_surfaces_policy_skill_and_review_sections(
        self,
        control_plane_client: AsyncClient,
        seeded_memory_control_plane,
    ) -> None:
        resp = await control_plane_client.get("/api/control/resources/setup-governance")

        assert resp.status_code == 200
        payload = resp.json()
        assert payload["resource_type"] == "setup_governance"
        assert payload["project_scope"]["status"] == "ready"
        assert payload["provider_runtime"]["details"]["enabled_provider_ids"] == []
        assert payload["agent_governance"]["details"]["active_agent_profile"]["profile_id"] == (
            "agent-profile-default"
        )
        assert payload["tools_skills"]["details"]["skill_summary"]["builtin_skill_count"] >= 1
        assert "next_actions" in payload["review"]

    async def test_setup_review_returns_blocking_reasons_without_secret_leak(
        self,
        control_plane_client: AsyncClient,
        seeded_control_plane,
    ) -> None:
        resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "setup.review",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "draft": {
                        "config": {
                            "runtime": {
                                "llm_mode": "litellm",
                            },
                            "providers": [
                                {
                                    "id": "openrouter",
                                    "name": "OpenRouter",
                                    "auth_type": "api_key",
                                    "api_key_env": "OPENROUTER_API_KEY",
                                    "enabled": True,
                                }
                            ],
                            "model_aliases": {},
                            "channels": {
                                "telegram": {
                                    "enabled": True,
                                    "mode": "webhook",
                                    "bot_token_env": "TELEGRAM_BOT_TOKEN",
                                    "webhook_url": "",
                                }
                            },
                        },
                        "policy_profile_id": "permissive",
                    }
                },
            },
        )

        assert resp.status_code == 200
        result = resp.json()["result"]
        assert result["code"] == "SETUP_REVIEW_READY"
        review = result["data"]["review"]
        assert review["ready"] is False
        assert "main_alias_missing" in review["blocking_reasons"]
        assert any(
            item["risk_id"] == "telegram_webhook_url_missing"
            for item in review["channel_exposure_risks"]
        )
        serialized = json.dumps(review, ensure_ascii=False)
        assert "api_key_env" not in serialized
        assert "sk-" not in serialized

    async def test_setup_review_echo_mode_does_not_block_on_provider_or_alias(
        self,
        control_plane_client: AsyncClient,
        seeded_control_plane,
    ) -> None:
        resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "setup.review",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "draft": {
                        "config": {
                            "runtime": {
                                "llm_mode": "echo",
                            },
                            "providers": [],
                            "model_aliases": {},
                        }
                    }
                },
            },
        )

        assert resp.status_code == 200
        review = resp.json()["result"]["data"]["review"]
        assert "provider_missing" not in review["blocking_reasons"]
        assert "main_alias_missing" not in review["blocking_reasons"]
        assert any(
            item["risk_id"] == "provider_missing" and item["blocking"] is False
            for item in review["provider_runtime_risks"]
        )
        assert any(
            "体验模式" in item["summary"] for item in review["provider_runtime_risks"]
        )

    async def test_setup_review_uses_draft_aliases_for_skill_governance(
        self,
        control_plane_client: AsyncClient,
        seeded_memory_control_plane,
    ) -> None:
        resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "setup.review",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "draft": {
                        "config": {
                            "providers": [
                                {
                                    "id": "openrouter",
                                    "name": "OpenRouter",
                                    "auth_type": "api_key",
                                    "api_key_env": "OPENROUTER_API_KEY",
                                    "enabled": True,
                                }
                            ],
                            "model_aliases": {
                                "main": {
                                    "provider": "openrouter",
                                    "model": "openai/gpt-4o-mini",
                                }
                            },
                        }
                    }
                },
            },
        )

        assert resp.status_code == 200
        review = resp.json()["result"]["data"]["review"]
        assert all(
            not risk["risk_id"].startswith("skill:")
            for risk in review["tool_skill_readiness_risks"]
            if risk["blocking"]
        )

    async def test_setup_review_blocks_empty_agent_name_in_draft(
        self,
        control_plane_client: AsyncClient,
        seeded_control_plane,
    ) -> None:
        resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "setup.review",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "draft": {
                        "config": {
                            "providers": [
                                {
                                    "id": "openai",
                                    "name": "OpenAI",
                                    "auth_type": "oauth",
                                    "api_key_env": "OPENAI_API_KEY",
                                    "enabled": True,
                                }
                            ],
                            "model_aliases": {
                                "main": {
                                    "provider": "openai",
                                    "model": "gpt-4o",
                                },
                                "cheap": {
                                    "provider": "openai",
                                    "model": "gpt-4.1-mini",
                                },
                            },
                        },
                        "agent_profile": {
                            "scope": "project",
                            "name": "",
                            "persona_summary": "仍然需要明确命名。",
                            "tool_profile": "standard",
                            "model_alias": "main",
                        },
                    }
                },
            },
        )

        assert resp.status_code == 200
        review = resp.json()["result"]["data"]["review"]
        assert review["ready"] is False
        assert "agent_profile_name_missing" in review["blocking_reasons"]
        assert any(
            item["risk_id"] == "agent_profile_name_missing"
            for item in review["agent_autonomy_risks"]
        )

    async def test_setup_apply_persists_config_policy_and_agent_profile(
        self,
        control_plane_app,
        control_plane_client: AsyncClient,
        seeded_control_plane,
    ) -> None:
        skill_doc = await control_plane_app.state.control_plane_service.get_skill_governance_document()
        target_skill = next(
            item for item in skill_doc.items if item.source_kind == "builtin"
        )
        resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "setup.apply",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "draft": {
                        "config": {
                            "providers": [
                                {
                                    "id": "openai",
                                    "name": "OpenAI",
                                    "auth_type": "oauth",
                                    "api_key_env": "OPENAI_API_KEY",
                                    "enabled": True,
                                }
                            ],
                            "model_aliases": {
                                "main": {
                                    "provider": "openai",
                                    "model": "gpt-4o",
                                },
                                "cheap": {
                                    "provider": "openai",
                                    "model": "gpt-4.1-mini",
                                },
                            },
                        },
                        "policy_profile_id": "strict",
                        "skill_selection": {
                            "disabled_item_ids": [target_skill.item_id],
                        },
                        "agent_profile": {
                            "scope": "project",
                            "name": "安全优先主 Agent",
                            "persona_summary": "先审查风险，再安排 worker。",
                            "tool_profile": "minimal",
                            "model_alias": "main",
                        },
                    }
                },
            },
        )

        assert resp.status_code == 200
        result = resp.json()["result"]
        assert result["code"] == "SETUP_APPLIED"
        assert result["data"]["review"]["ready"] is True
        assert any(
            item["resource_type"] == "policy_profiles" for item in result["resource_refs"]
        )
        assert any(
            item["resource_type"] == "agent_profiles" for item in result["resource_refs"]
        )

        config_doc = await control_plane_app.state.control_plane_service.get_config_schema()
        assert config_doc.current_value["providers"][0]["id"] == "openai"
        assert config_doc.current_value["model_aliases"]["main"]["provider"] == "openai"

        default_project = (
            await control_plane_app.state.store_group.project_store.get_default_project()
        )
        assert default_project is not None
        assert default_project.metadata["policy_profile_id"] == "strict"
        assert target_skill.item_id in default_project.metadata["skill_selection"][
            "disabled_item_ids"
        ]
        assert default_project.default_agent_profile_id

        saved_profile = await control_plane_app.state.store_group.agent_context_store.get_agent_profile(
            default_project.default_agent_profile_id
        )
        assert saved_profile is not None
        assert saved_profile.name == "安全优先主 Agent"
        assert saved_profile.tool_profile == "minimal"

        refreshed_skill_doc = (
            await control_plane_app.state.control_plane_service.get_skill_governance_document()
        )
        refreshed_skill = next(
            item for item in refreshed_skill_doc.items if item.item_id == target_skill.item_id
        )
        assert refreshed_skill.selected is False
        assert refreshed_skill.selection_source == "project_override"

        capability_doc = (
            await control_plane_app.state.control_plane_service.get_capability_pack_document()
        )
        assert target_skill.item_id.removeprefix("skill:") not in {
            item.skill_id for item in capability_doc.pack.skills
        }

    async def test_skills_selection_save_persists_project_metadata(
        self,
        control_plane_app,
        control_plane_client: AsyncClient,
        seeded_control_plane,
    ) -> None:
        skill_doc = await control_plane_app.state.control_plane_service.get_skill_governance_document()
        target_skill = next(
            item for item in skill_doc.items if item.source_kind == "builtin"
        )

        resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "skills.selection.save",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "selection": {
                        "disabled_item_ids": [target_skill.item_id],
                    }
                },
            },
        )

        assert resp.status_code == 200
        result = resp.json()["result"]
        assert result["code"] == "SKILL_SELECTION_SAVED"
        assert result["data"]["selection"]["disabled_item_ids"] == [target_skill.item_id]

        default_project = (
            await control_plane_app.state.store_group.project_store.get_default_project()
        )
        assert default_project is not None
        assert target_skill.item_id in default_project.metadata["skill_selection"][
            "disabled_item_ids"
        ]

    async def test_skills_selection_filters_runtime_tool_selection(
        self,
        control_plane_app,
        control_plane_client: AsyncClient,
        seeded_control_plane,
    ) -> None:
        skill_doc = await control_plane_app.state.control_plane_service.get_skill_governance_document()
        target_skill = next(
            item for item in skill_doc.items if item.item_id == "skill:ops_triage"
        )

        save_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "skills.selection.save",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "selection": {
                        "disabled_item_ids": [target_skill.item_id],
                    }
                },
            },
        )

        assert save_resp.status_code == 200
        default_project = (
            await control_plane_app.state.store_group.project_store.get_default_project()
        )
        assert default_project is not None
        workspace = await control_plane_app.state.store_group.project_store.get_primary_workspace(
            default_project.project_id
        )
        assert workspace is not None

        selection = await control_plane_app.state.capability_pack_service.select_tools(
            ToolIndexQuery(
                query="inspect runtime health and status",
                limit=5,
                worker_type=WorkerType.OPS,
                project_id=default_project.project_id,
                workspace_id=workspace.workspace_id,
            ),
            worker_type=WorkerType.OPS,
        )

        assert selection.selected_tools
        assert "runtime.inspect" not in selection.selected_tools
        assert "tool_selection_filtered_by_skill_governance" in selection.warnings

    async def test_setup_apply_rejects_invalid_skill_selection_before_writing_config(
        self,
        control_plane_app,
        control_plane_client: AsyncClient,
        seeded_control_plane,
    ) -> None:
        skill_doc = await control_plane_app.state.control_plane_service.get_skill_governance_document()
        target_skill = next(
            item for item in skill_doc.items if item.source_kind == "builtin"
        )
        config_path = control_plane_app.state.project_root / "octoagent.yaml"
        assert config_path.exists() is False

        resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "setup.apply",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "draft": {
                        "config": {
                            "providers": [
                                {
                                    "id": "openai",
                                    "name": "OpenAI",
                                    "auth_type": "oauth",
                                    "api_key_env": "OPENAI_API_KEY",
                                    "enabled": True,
                                }
                            ],
                            "model_aliases": {
                                "main": {
                                    "provider": "openai",
                                    "model": "gpt-4o",
                                }
                            },
                        },
                        "policy_profile_id": "strict",
                        "skill_selection": {
                            "selected_item_ids": [target_skill.item_id],
                            "disabled_item_ids": [target_skill.item_id],
                        },
                    }
                },
            },
        )

        assert resp.status_code == 409
        result = resp.json()["result"]
        assert result["code"] == "SKILL_SELECTION_CONFLICT"
        assert config_path.exists() is False

        default_project = (
            await control_plane_app.state.store_group.project_store.get_default_project()
        )
        assert default_project is not None
        assert "policy_profile_id" not in default_project.metadata
        assert "skill_selection" not in default_project.metadata

    async def test_setup_apply_persists_litellm_secret_values_and_api_key_profile(
        self,
        control_plane_app,
        control_plane_client: AsyncClient,
        seeded_control_plane,
    ) -> None:
        resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "setup.apply",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "draft": {
                        "config": {
                            "runtime": {
                                "llm_mode": "litellm",
                                "litellm_proxy_url": "http://localhost:4000",
                                "master_key_env": "LITELLM_MASTER_KEY",
                            },
                            "providers": [
                                {
                                    "id": "openrouter",
                                    "name": "OpenRouter",
                                    "auth_type": "api_key",
                                    "api_key_env": "OPENROUTER_API_KEY",
                                    "enabled": True,
                                }
                            ],
                            "model_aliases": {
                                "main": {
                                    "provider": "openrouter",
                                    "model": "openrouter/auto",
                                }
                            },
                        },
                        "secret_values": {
                            "OPENROUTER_API_KEY": "sk-openrouter-value",
                            "LITELLM_MASTER_KEY": "sk-master-value",
                        },
                        "agent_profile": {
                            "scope": "project",
                            "name": "LiteLLM 主 Agent",
                            "persona_summary": "用于验证密钥落盘。",
                            "tool_profile": "standard",
                            "model_alias": "main",
                        },
                    }
                },
            },
        )

        assert resp.status_code == 200
        result = resp.json()["result"]
        assert result["code"] == "SETUP_APPLIED"
        assert result["data"]["saved_secrets"]["litellm_env_names"] == [
            "LITELLM_MASTER_KEY",
            "LITELLM_PROXY_KEY",
            "OPENROUTER_API_KEY",
        ]
        assert result["data"]["saved_secrets"]["profile_names"] == ["openrouter-default"]

        env_path = control_plane_app.state.project_root / ".env.litellm"
        assert env_path.exists()
        env_text = env_path.read_text(encoding="utf-8")
        assert "OPENROUTER_API_KEY=sk-openrouter-value" in env_text
        assert "LITELLM_MASTER_KEY=sk-master-value" in env_text
        assert "LITELLM_PROXY_KEY=sk-master-value" in env_text

        store = CredentialStore(control_plane_app.state.project_root / "auth-profiles.json")
        profile = store.get_profile("openrouter-default")
        assert profile is not None
        assert profile.provider == "openrouter"
        assert profile.auth_mode == "api_key"
        assert profile.credential.key.get_secret_value() == "sk-openrouter-value"

    async def test_setup_quick_connect_starts_proxy_and_returns_activation_summary(
        self,
        control_plane_app,
        control_plane_client: AsyncClient,
        seeded_control_plane,
        monkeypatch,
    ) -> None:
        from octoagent.gateway.services import control_plane as control_plane_module

        class FakeActivationService:
            def __init__(self, project_root: Path) -> None:
                self.project_root = project_root

            async def start_proxy(self):
                return type(
                    "Activation",
                    (),
                    {
                        "project_root": str(self.project_root),
                        "source_root": str(self.project_root / "app" / "octoagent"),
                        "compose_file": str(
                            self.project_root / "app" / "octoagent" / "docker-compose.litellm.yml"
                        ),
                        "proxy_url": "http://localhost:4000",
                        "managed_runtime": False,
                        "warnings": [],
                    },
                )()

        monkeypatch.setattr(
            control_plane_module,
            "RuntimeActivationService",
            FakeActivationService,
        )

        resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "setup.quick_connect",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "draft": {
                        "config": {
                            "runtime": {
                                "llm_mode": "litellm",
                                "litellm_proxy_url": "http://localhost:4000",
                                "master_key_env": "LITELLM_MASTER_KEY",
                            },
                            "providers": [
                                {
                                    "id": "openrouter",
                                    "name": "OpenRouter",
                                    "auth_type": "api_key",
                                    "api_key_env": "OPENROUTER_API_KEY",
                                    "enabled": True,
                                }
                            ],
                            "model_aliases": {
                                "main": {
                                    "provider": "openrouter",
                                    "model": "openrouter/auto",
                                }
                            },
                        },
                        "secret_values": {
                            "OPENROUTER_API_KEY": "sk-openrouter-value",
                            "LITELLM_MASTER_KEY": "sk-master-value",
                        },
                        "agent_profile": {
                            "scope": "project",
                            "name": "快速接入主 Agent",
                            "persona_summary": "用于验证一键连接。",
                            "tool_profile": "standard",
                            "model_alias": "main",
                        },
                    }
                },
            },
        )

        assert resp.status_code == 200
        result = resp.json()["result"]
        assert result["code"] == "SETUP_QUICK_CONNECTED"
        assert result["data"]["activation"]["proxy_url"] == "http://localhost:4000"
        assert result["data"]["activation"]["runtime_reload_mode"] == "manual_restart_required"
        assert result["data"]["review"]["ready"] is True

    async def test_provider_oauth_openai_codex_persists_profile_and_env(
        self,
        control_plane_app,
        control_plane_client: AsyncClient,
        monkeypatch,
    ) -> None:
        from octoagent.gateway.services import control_plane as control_plane_module

        async def fake_run_auth_code_pkce_flow(**_kwargs):
            return OAuthCredential(
                provider="openai-codex",
                access_token=SecretStr("oauth-access-token"),
                refresh_token=SecretStr("oauth-refresh-token"),
                expires_at=datetime(2026, 3, 20, tzinfo=UTC),
                account_id="acct-openai",
            )

        monkeypatch.setattr(
            control_plane_module,
            "detect_environment",
            lambda: EnvironmentContext(
                is_remote=False,
                can_open_browser=True,
                force_manual=False,
                detection_details="test",
            ),
        )
        monkeypatch.setattr(
            control_plane_module,
            "run_auth_code_pkce_flow",
            fake_run_auth_code_pkce_flow,
        )

        resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "provider.oauth.openai_codex",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "env_name": "OPENAI_API_KEY",
                    "profile_name": "openai-codex-default",
                },
            },
        )

        assert resp.status_code == 200
        result = resp.json()["result"]
        assert result["code"] == "OPENAI_OAUTH_CONNECTED"
        assert result["data"]["account_id"] == "acct-openai"
        assert result["data"]["env_name"] == "OPENAI_API_KEY"

        store = CredentialStore(control_plane_app.state.project_root / "auth-profiles.json")
        profile = store.get_profile("openai-codex-default")
        assert profile is not None
        assert profile.provider == "openai-codex"
        assert profile.auth_mode == "oauth"
        assert profile.credential.access_token.get_secret_value() == "oauth-access-token"

        env_path = control_plane_app.state.project_root / ".env.litellm"
        assert env_path.exists()
        assert "OPENAI_API_KEY=oauth-access-token" in env_path.read_text(encoding="utf-8")

        setup_doc = await control_plane_app.state.control_plane_service.get_setup_governance_document()
        assert setup_doc.provider_runtime.details["openai_oauth_connected"] is True
        assert (
            setup_doc.provider_runtime.details["openai_oauth_profile"]
            == "openai-codex-default"
        )

    async def test_setup_apply_rejects_blocking_review(
        self,
        control_plane_client: AsyncClient,
        seeded_control_plane,
    ) -> None:
        resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "setup.apply",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "draft": {
                        "config": {
                            "providers": [
                                {
                                    "id": "openrouter",
                                    "name": "OpenRouter",
                                    "auth_type": "api_key",
                                    "api_key_env": "OPENROUTER_API_KEY",
                                    "enabled": True,
                                }
                            ],
                            "model_aliases": {},
                        }
                    }
                },
            },
        )

        assert resp.status_code == 409
        result = resp.json()["result"]
        assert result["code"] == "SETUP_REVIEW_BLOCKED"
        assert "main_alias_missing" in result["message"]

    async def test_policy_profile_select_updates_project_metadata_and_runtime(
        self,
        control_plane_app,
        control_plane_client: AsyncClient,
    ) -> None:
        resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "policy_profile.select",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {"profile_id": "strict"},
            },
        )

        assert resp.status_code == 200
        payload = resp.json()["result"]
        assert payload["code"] == "POLICY_PROFILE_SELECTED"
        default_project = (
            await control_plane_app.state.store_group.project_store.get_default_project()
        )
        assert default_project is not None
        assert default_project.metadata["policy_profile_id"] == "strict"
        assert control_plane_app.state.policy_engine.profile.name == "strict"

        document = (
            await control_plane_app.state.control_plane_service.get_policy_profiles_document()
        ).model_dump(mode="json")
        assert document["active_profile_id"] == "strict"

    async def test_agent_profile_save_binds_selected_project_default_profile(
        self,
        control_plane_app,
        control_plane_client: AsyncClient,
    ) -> None:
        resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "agent_profile.save",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "profile": {
                        "scope": "project",
                        "name": "安全优先主 Agent",
                        "persona_summary": "更保守，适合首次使用。",
                        "tool_profile": "minimal",
                        "model_alias": "main",
                    }
                },
            },
        )

        assert resp.status_code == 200
        payload = resp.json()["result"]
        assert payload["code"] == "AGENT_PROFILE_SAVED"
        default_project = (
            await control_plane_app.state.store_group.project_store.get_default_project()
        )
        assert default_project is not None
        saved_profile_id = payload["data"]["profile_id"]
        assert default_project.default_agent_profile_id == saved_profile_id
        saved = await control_plane_app.state.store_group.agent_context_store.get_agent_profile(
            saved_profile_id
        )
        assert saved is not None
        assert saved.name == "安全优先主 Agent"

    async def test_agent_profile_save_updates_target_project_default_profile(
        self,
        control_plane_app,
        control_plane_client: AsyncClient,
    ) -> None:
        default_project = (
            await control_plane_app.state.store_group.project_store.get_default_project()
        )
        assert default_project is not None
        selector = ProjectSelectorService(
            control_plane_app.state.project_root,
            surface="web",
            store_group=control_plane_app.state.store_group,
        )
        target_project, _, _ = await selector.create_project(
            name="Project Secondary",
            slug="project-secondary",
            set_active=False,
        )

        resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "agent_profile.save",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "profile": {
                        "scope": "project",
                        "project_id": target_project.project_id,
                        "name": "Secondary Agent",
                        "tool_profile": "minimal",
                        "model_alias": "main",
                    }
                },
            },
        )

        assert resp.status_code == 200
        saved_profile_id = resp.json()["result"]["data"]["profile_id"]
        reloaded_default = await control_plane_app.state.store_group.project_store.get_project(
            default_project.project_id
        )
        reloaded_target = await control_plane_app.state.store_group.project_store.get_project(
            target_project.project_id
        )
        assert reloaded_default is not None
        assert reloaded_target is not None
        assert reloaded_default.default_agent_profile_id != saved_profile_id
        assert reloaded_target.default_agent_profile_id == saved_profile_id

    async def test_policy_engine_uses_persisted_selected_project_profile_on_restart(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        _configure_control_plane_env(tmp_path, monkeypatch)

        from octoagent.gateway.main import create_app

        app = create_app()
        async with app.router.lifespan_context(app):
            default_project = await app.state.store_group.project_store.get_default_project()
            assert default_project is not None
            selector = ProjectSelectorService(
                app.state.project_root,
                surface="web",
                store_group=app.state.store_group,
            )
            secondary_project, _, _ = await selector.create_project(
                name="Restart Secondary",
                slug="restart-secondary",
                set_active=False,
            )
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                select_resp = await client.post(
                    "/api/control/actions",
                    json={
                        "request_id": str(ULID()),
                        "action_id": "project.select",
                        "surface": "web",
                        "actor": {
                            "actor_id": "user:web",
                            "actor_label": "Owner",
                        },
                        "params": {"project_id": secondary_project.project_id},
                    },
                )
                assert select_resp.status_code == 200
                profile_resp = await client.post(
                    "/api/control/actions",
                    json={
                        "request_id": str(ULID()),
                        "action_id": "policy_profile.select",
                        "surface": "web",
                        "actor": {
                            "actor_id": "user:web",
                            "actor_label": "Owner",
                        },
                        "params": {"profile_id": "strict"},
                    },
                )
                assert profile_resp.status_code == 200

        restarted_app = create_app()
        async with restarted_app.router.lifespan_context(restarted_app):
            document = (
                await restarted_app.state.control_plane_service.get_policy_profiles_document()
            ).model_dump(mode="json")
            assert document["active_project_id"] == secondary_project.project_id
            assert document["active_profile_id"] == "strict"
            assert restarted_app.state.policy_engine.profile.name == "strict"

    async def test_config_resource_exposes_frontdoor_and_telegram_governance_hints(
        self,
        control_plane_client: AsyncClient,
        seeded_control_plane,
    ) -> None:
        resp = await control_plane_client.get("/api/control/resources/config")

        assert resp.status_code == 200
        hints = resp.json()["ui_hints"]
        assert "front_door.mode" in hints
        assert "channels.telegram.dm_policy" in hints
        assert "channels.telegram.group_policy" in hints
        assert "channels.telegram.group_allow_users" in hints

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
        assert len(pack["tools"]) >= 20
        assert {
            "subagents.spawn",
            "subagents.list",
            "subagents.kill",
            "subagents.steer",
            "work.split",
            "work.merge",
            "work.delete",
            "web.fetch",
            "browser.snapshot",
            "mcp.servers.list",
            "mcp.tools.list",
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
        assert "work.delete" in action_ids

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

    async def test_worker_review_and_apply_actions_create_governed_child_plan(
        self,
        control_plane_client: AsyncClient,
        control_plane_app,
    ) -> None:
        task_id = await _create_task(
            control_plane_app,
            text="请先调研 API，再补代码和测试",
            thread_id="thread-worker-review",
            scope_id="scope-control",
        )
        plan = await control_plane_app.state.delegation_plane_service.prepare_dispatch(
            OrchestratorRequest(
                task_id=task_id,
                trace_id=f"trace-{task_id}",
                user_text="请先调研 API，再补代码和测试",
                worker_capability="llm_generation",
                metadata={},
            )
        )

        review_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "worker.review",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "work_id": plan.work.work_id,
                    "objective": "请先调研 API，再补代码和测试",
                },
            },
        )

        assert review_resp.status_code == 200
        review_payload = review_resp.json()["result"]
        assert review_payload["code"] == "WORKER_REVIEW_READY"
        worker_plan = review_payload["data"]["plan"]
        assert worker_plan["proposal_kind"] == "split"
        assert {item["worker_type"] for item in worker_plan["assignments"]} >= {
            "research",
            "dev",
        }
        assert {item["tool_profile"] for item in worker_plan["assignments"]} >= {
            "minimal",
            "standard",
        }

        apply_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "worker.apply",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "work_id": plan.work.work_id,
                    "plan": worker_plan,
                },
            },
        )

        assert apply_resp.status_code == 200
        apply_payload = apply_resp.json()["result"]
        assert apply_payload["code"] == "WORKER_PLAN_APPLIED"
        assert len(apply_payload["data"]["child_tasks"]) >= 2

        child_works = []
        for _ in range(30):
            child_works = await control_plane_app.state.store_group.work_store.list_works(
                parent_work_id=plan.work.work_id
            )
            if len(child_works) >= 2:
                break
            await asyncio.sleep(0.05)

        assert len(child_works) >= 2
        assert {item.selected_worker_type.value for item in child_works} >= {"research", "dev"}
        assert {str(item.metadata.get("requested_tool_profile", "")) for item in child_works} >= {
            "minimal",
            "standard",
        }

    async def test_work_cancel_and_delete_actions_cascade_to_child_works(
        self,
        control_plane_client: AsyncClient,
        control_plane_app,
    ) -> None:
        parent_task_id = await _create_task(
            control_plane_app,
            text="请先暂停这项父 work",
            thread_id="thread-work-cascade-parent",
        )
        parent = await control_plane_app.state.delegation_plane_service.prepare_dispatch(
            OrchestratorRequest(
                task_id=parent_task_id,
                trace_id=f"trace-{parent_task_id}",
                user_text="请先暂停这项父 work",
                worker_capability="llm_generation",
                metadata={"delegation_pause": "approval"},
            )
        )
        child_task_id = await _create_task(
            control_plane_app,
            text="请先暂停这项 child work",
            thread_id="thread-work-cascade-child",
        )
        child = await control_plane_app.state.delegation_plane_service.prepare_dispatch(
            OrchestratorRequest(
                task_id=child_task_id,
                trace_id=f"trace-{child_task_id}",
                user_text="请先暂停这项 child work",
                worker_capability="llm_generation",
                metadata={
                    "delegation_pause": "approval",
                    "parent_work_id": parent.work.work_id,
                    "parent_task_id": parent_task_id,
                    "requested_worker_type": "research",
                    "target_kind": "subagent",
                },
            )
        )

        cancel_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "work.cancel",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {"work_id": parent.work.work_id},
            },
        )

        assert cancel_resp.status_code == 200
        cancel_payload = cancel_resp.json()["result"]
        assert cancel_payload["code"] == "WORK_CANCELLED"

        parent_after_cancel = await control_plane_app.state.store_group.work_store.get_work(
            parent.work.work_id
        )
        child_after_cancel = await control_plane_app.state.store_group.work_store.get_work(
            child.work.work_id
        )
        assert parent_after_cancel is not None
        assert child_after_cancel is not None
        assert parent_after_cancel.status.value == "cancelled"
        assert child_after_cancel.status.value == "cancelled"

        delete_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "work.delete",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {"work_id": parent.work.work_id},
            },
        )

        assert delete_resp.status_code == 200
        delete_payload = delete_resp.json()["result"]
        assert delete_payload["code"] == "WORK_DELETED"

        parent_after_delete = await control_plane_app.state.store_group.work_store.get_work(
            parent.work.work_id
        )
        child_after_delete = await control_plane_app.state.store_group.work_store.get_work(
            child.work.work_id
        )
        assert parent_after_delete is not None
        assert child_after_delete is not None
        assert parent_after_delete.status.value == "deleted"
        assert child_after_delete.status.value == "deleted"

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

    async def test_session_projection_exposes_scope_aware_session_id_and_focus_supports_session_id(
        self,
        control_plane_app,
        control_plane_client: AsyncClient,
    ) -> None:
        task_id = await _create_task(
            control_plane_app,
            text="session authority demo",
            thread_id="thread-session-authority",
            scope_id="scope-control",
        )
        task = await control_plane_app.state.store_group.task_store.get_task(task_id)
        assert task is not None
        workspace = (
            await control_plane_app.state.store_group.project_store.resolve_workspace_for_scope(
                task.scope_id
            )
        )
        assert workspace is not None
        expected_session_id = build_scope_aware_session_id(
            task,
            project_id=workspace.project_id,
            workspace_id=workspace.workspace_id,
        )

        sessions_resp = await control_plane_client.get("/api/control/resources/sessions")

        assert sessions_resp.status_code == 200
        payload = sessions_resp.json()
        session_item = next(item for item in payload["sessions"] if item["task_id"] == task_id)
        assert session_item["session_id"] == expected_session_id
        assert session_item["thread_id"] == "thread-session-authority"
        assert payload["focused_session_id"] == ""
        assert payload["focused_thread_id"] == ""

        focus_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "session.focus",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "session_id": expected_session_id,
                },
            },
        )

        assert focus_resp.status_code == 200
        focus_result = focus_resp.json()["result"]
        assert focus_result["status"] == "completed"
        assert focus_result["data"] == {
            "session_id": expected_session_id,
            "thread_id": "thread-session-authority",
        }

        focused_sessions = await control_plane_client.get("/api/control/resources/sessions")
        assert focused_sessions.status_code == 200
        focused_payload = focused_sessions.json()
        assert focused_payload["focused_session_id"] == expected_session_id
        assert focused_payload["focused_thread_id"] == "thread-session-authority"

        export_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "session.export",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "session_id": expected_session_id,
                },
            },
        )

        assert export_resp.status_code == 200
        export_result = export_resp.json()["result"]
        assert export_result["status"] == "completed"
        exported_task_ids = {item["task_id"] for item in export_result["data"]["tasks"]}
        assert task_id in exported_task_ids

    async def test_session_focus_requires_session_id_when_thread_id_is_ambiguous(
        self,
        control_plane_app,
        control_plane_client: AsyncClient,
    ) -> None:
        store_group = control_plane_app.state.store_group
        project = await store_group.project_store.get_default_project()
        assert project is not None
        workspace = await store_group.project_store.get_primary_workspace(project.project_id)
        assert workspace is not None
        await store_group.project_store.create_binding(
            ProjectBinding(
                binding_id=str(ULID()),
                project_id=project.project_id,
                workspace_id=workspace.workspace_id,
                binding_type=ProjectBindingType.SCOPE,
                binding_key="scope-control-alt",
                binding_value="scope-control-alt",
                source="tests",
                migration_run_id="scope-control-alt",
            )
        )
        await store_group.conn.commit()

        first_task_id = await _create_task(
            control_plane_app,
            text="first ambiguous session",
            thread_id="thread-ambiguous",
            scope_id="scope-control",
        )
        second_task_id = await _create_task(
            control_plane_app,
            text="second ambiguous session",
            thread_id="thread-ambiguous",
            scope_id="scope-control-alt",
        )

        sessions_resp = await control_plane_client.get("/api/control/resources/sessions")
        assert sessions_resp.status_code == 200
        payload = sessions_resp.json()
        ambiguous_sessions = [
            item for item in payload["sessions"] if item["thread_id"] == "thread-ambiguous"
        ]
        assert {item["task_id"] for item in ambiguous_sessions} == {first_task_id, second_task_id}
        assert len({item["session_id"] for item in ambiguous_sessions}) == 2

        focus_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "session.focus",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "thread_id": "thread-ambiguous",
                },
            },
        )

        assert focus_resp.status_code == 400
        focus_result = focus_resp.json()["result"]
        assert focus_result["status"] == "rejected"
        assert focus_result["code"] == "SESSION_ID_REQUIRED"

        explicit_focus_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "session.focus",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "session_id": ambiguous_sessions[0]["session_id"],
                },
            },
        )

        assert explicit_focus_resp.status_code == 200
        assert explicit_focus_resp.json()["result"]["status"] == "completed"

        export_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "session.export",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "session_id": ambiguous_sessions[0]["session_id"],
                },
            },
        )

        assert export_resp.status_code == 200
        exported_task_ids = {
            item["task_id"] for item in export_resp.json()["result"]["data"]["tasks"]
        }
        assert exported_task_ids == {ambiguous_sessions[0]["task_id"]}

    async def test_backup_create_and_restore_plan_actions_refresh_diagnostics(
        self,
        control_plane_app,
        control_plane_client: AsyncClient,
    ) -> None:
        backup_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "backup.create",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "label": "memory-center",
                },
            },
        )

        assert backup_resp.status_code == 200
        backup_result = backup_resp.json()["result"]
        assert backup_result["status"] == "completed"
        assert backup_result["code"] == "BACKUP_CREATED"
        assert backup_result["data"]["output_path"]
        assert any(
            item["resource_type"] == "diagnostics_summary"
            for item in backup_result["resource_refs"]
        )

        bundle_path = Path(backup_result["data"]["output_path"])
        assert bundle_path.exists()

        diagnostics_resp = await control_plane_client.get("/api/control/resources/diagnostics")
        assert diagnostics_resp.status_code == 200
        diagnostics_payload = diagnostics_resp.json()
        assert diagnostics_payload["recovery_summary"]["latest_backup"]["output_path"] == str(
            bundle_path
        )

        restore_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "restore.plan",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "bundle": str(bundle_path),
                    "target_root": str(control_plane_app.state.project_root / "restore-preview"),
                },
            },
        )

        assert restore_resp.status_code == 200
        restore_result = restore_resp.json()["result"]
        assert restore_result["status"] == "completed"
        assert restore_result["code"] == "RESTORE_PLAN_READY"
        assert restore_result["data"]["bundle_path"] == str(bundle_path)
        assert restore_result["data"]["compatible"] is True
        assert any(
            item["resource_type"] == "diagnostics_summary"
            for item in restore_result["resource_refs"]
        )

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

    async def test_worker_profile_create_publish_and_revision_resource(
        self,
        control_plane_app,
        control_plane_client: AsyncClient,
        seeded_memory_control_plane,
    ) -> None:
        project = await control_plane_app.state.store_group.project_store.get_default_project()
        assert project is not None
        draft = {
            "scope": "project",
            "project_id": project.project_id,
            "name": "NAS Root Agent",
            "summary": "负责 NAS 巡检与文件归档。",
            "base_archetype": "ops",
            "tool_profile": "standard",
            "default_tool_groups": ["project", "artifact"],
            "runtime_kinds": ["worker", "acp_runtime"],
            "policy_refs": ["default"],
            "tags": ["nas", "storage"],
        }

        review_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "worker_profile.review",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "draft": draft,
                },
            },
        )
        assert review_resp.status_code == 200
        review_payload = review_resp.json()["result"]["data"]["review"]
        assert review_payload["can_save"] is True
        assert review_payload["ready"] is True

        create_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "worker_profile.create",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "draft": draft,
                },
            },
        )
        assert create_resp.status_code == 200
        create_payload = create_resp.json()["result"]["data"]
        profile_id = create_payload["profile_id"]
        assert create_payload["status"] == "draft"
        assert create_payload["draft_revision"] == 1

        worker_profiles_resp = await control_plane_client.get("/api/control/resources/worker-profiles")
        assert worker_profiles_resp.status_code == 200
        profiles_payload = worker_profiles_resp.json()
        created_profile = next(
            item for item in profiles_payload["profiles"] if item["profile_id"] == profile_id
        )
        assert created_profile["status"] == "draft"
        assert created_profile["active_revision"] == 0
        assert created_profile["origin_kind"] == "custom"

        publish_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "worker_profile.publish",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "profile_id": profile_id,
                    "change_summary": "首次发布 NAS Root Agent",
                },
            },
        )
        assert publish_resp.status_code == 200
        publish_payload = publish_resp.json()["result"]["data"]
        assert publish_payload["revision"] == 1

        revisions_resp = await control_plane_client.get(
            f"/api/control/resources/worker-profile-revisions/{profile_id}"
        )
        assert revisions_resp.status_code == 200
        revisions_payload = revisions_resp.json()
        assert revisions_payload["summary"]["revision_count"] == 1
        assert revisions_payload["revisions"][0]["change_summary"] == "首次发布 NAS Root Agent"
        assert revisions_payload["revisions"][0]["snapshot_payload"]["name"] == "NAS Root Agent"

    async def test_worker_profile_spawn_and_extract_actions(
        self,
        control_plane_app,
        control_plane_client: AsyncClient,
        seeded_memory_control_plane,
    ) -> None:
        store_group = control_plane_app.state.store_group
        project = await store_group.project_store.get_default_project()
        assert project is not None
        workspace = await store_group.project_store.get_primary_workspace(project.project_id)
        assert workspace is not None

        profile = WorkerProfile(
            profile_id="worker-profile-runtime-alpha",
            scope=AgentProfileScope.PROJECT,
            project_id=project.project_id,
            name="Runtime Alpha",
            summary="用于 runtime lineage 测试。",
            base_archetype="research",
            model_alias="main",
            tool_profile="minimal",
            default_tool_groups=["project", "network"],
            selected_tools=["web.search"],
            runtime_kinds=["worker", "subagent"],
            policy_refs=["default"],
            tags=["runtime", "search"],
            status=WorkerProfileStatus.ACTIVE,
            origin_kind=WorkerProfileOriginKind.CUSTOM,
            draft_revision=1,
            active_revision=1,
        )
        await store_group.agent_context_store.save_worker_profile(profile)
        await store_group.agent_context_store.save_worker_profile_revision(
            WorkerProfileRevision(
                revision_id="worker-snapshot:worker-profile-runtime-alpha:1",
                profile_id=profile.profile_id,
                revision=1,
                change_summary="seeded runtime profile",
                snapshot_payload={
                    "profile_id": profile.profile_id,
                    "name": profile.name,
                    "selected_tools": profile.selected_tools,
                },
                created_by="tests",
            )
        )
        await store_group.conn.commit()

        spawn_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "worker.spawn_from_profile",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "profile_id": profile.profile_id,
                    "objective": "请根据这个 Root Agent 的职责检查今天的 NAS 状态。",
                },
            },
        )
        assert spawn_resp.status_code == 200
        spawn_payload = spawn_resp.json()["result"]["data"]
        task_id = spawn_payload["task_id"]
        assert spawn_payload["requested_worker_profile_version"] == 1

        events = await store_group.event_store.get_events_for_task(task_id)
        user_event = next(item for item in events if item.type.value == "USER_MESSAGE")
        assert user_event.payload["metadata"]["requested_worker_profile_id"] == profile.profile_id
        assert user_event.payload["metadata"]["effective_worker_snapshot_id"] == (
            "worker-snapshot:worker-profile-runtime-alpha:1"
        )

        runtime_task_id = await _create_task(
            control_plane_app,
            text="runtime extract source",
            thread_id="thread-runtime-profile",
            scope_id=project.project_id,
        )
        await store_group.work_store.save_work(
            Work(
                work_id="work-runtime-profile",
                task_id=runtime_task_id,
                title="调研 NAS 今日同步状态",
                kind=WorkKind.DELEGATION,
                status=WorkStatus.RUNNING,
                target_kind=DelegationTargetKind.SUBAGENT,
                selected_worker_type=WorkerType.RESEARCH,
                project_id=project.project_id,
                workspace_id=workspace.workspace_id,
                requested_worker_profile_id=profile.profile_id,
                requested_worker_profile_version=1,
                effective_worker_snapshot_id="worker-snapshot:worker-profile-runtime-alpha:1",
                selected_tools=["web.search"],
                metadata={"requested_tool_profile": "minimal"},
            )
        )
        await store_group.conn.commit()

        extract_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "worker.extract_profile_from_runtime",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "work_id": "work-runtime-profile",
                    "name": "Runtime Extracted Agent",
                },
            },
        )
        assert extract_resp.status_code == 200
        extract_payload = extract_resp.json()["result"]["data"]
        extracted_id = extract_payload["profile_id"]

        extracted = await store_group.agent_context_store.get_worker_profile(extracted_id)
        assert extracted is not None
        assert extracted.origin_kind == WorkerProfileOriginKind.EXTRACTED
        assert extracted.selected_tools == ["web.search"]
        assert extracted.metadata["source_work_id"] == "work-runtime-profile"

    async def test_worker_profiles_document_uses_latest_project_work_context(
        self,
        control_plane_app,
        control_plane_client: AsyncClient,
        seeded_memory_control_plane,
    ) -> None:
        store_group = control_plane_app.state.store_group
        project = await store_group.project_store.get_default_project()
        assert project is not None
        primary_workspace = await store_group.project_store.get_primary_workspace(project.project_id)
        assert primary_workspace is not None

        secondary_workspace = Workspace(
            workspace_id="workspace-root-agent-ops",
            project_id=project.project_id,
            slug="root-agent-ops",
            name="Root Agent Ops",
            kind=WorkspaceKind.OPS,
            root_path="/tmp/root-agent-ops",
        )
        await store_group.project_store.create_workspace(secondary_workspace)

        profile = WorkerProfile(
            profile_id="worker-profile-root-agent-project",
            scope=AgentProfileScope.PROJECT,
            project_id=project.project_id,
            name="Project Root Agent",
            summary="跨 workspace 聚合 runtime 状态。",
            base_archetype="ops",
            model_alias="main",
            tool_profile="standard",
            default_tool_groups=["runtime", "project"],
            selected_tools=["runtime.inspect"],
            runtime_kinds=["worker", "acp_runtime"],
            policy_refs=["default"],
            tags=["runtime", "ops"],
            status=WorkerProfileStatus.ACTIVE,
            origin_kind=WorkerProfileOriginKind.CUSTOM,
            draft_revision=1,
            active_revision=1,
        )
        await store_group.agent_context_store.save_worker_profile(profile)
        await store_group.agent_context_store.save_worker_profile_revision(
            WorkerProfileRevision(
                revision_id="worker-snapshot:worker-profile-root-agent-project:1",
                profile_id=profile.profile_id,
                revision=1,
                change_summary="seeded root agent profile",
                snapshot_payload={
                    "profile_id": profile.profile_id,
                    "name": profile.name,
                },
                created_by="tests",
            )
        )

        running_task_id = await _create_task(
            control_plane_app,
            text="root agent running work",
            thread_id="thread-root-running",
            scope_id=project.project_id,
        )
        failed_task_id = await _create_task(
            control_plane_app,
            text="root agent failed work",
            thread_id="thread-root-failed",
            scope_id=project.project_id,
        )
        running_ts = datetime(2026, 3, 12, 9, 10, tzinfo=UTC)
        failed_ts = datetime(2026, 3, 12, 9, 20, tzinfo=UTC)
        await store_group.work_store.save_work(
            Work(
                work_id="work-root-running",
                task_id=running_task_id,
                title="主工作区巡检",
                kind=WorkKind.DELEGATION,
                status=WorkStatus.RUNNING,
                target_kind=DelegationTargetKind.WORKER,
                selected_worker_type=WorkerType.OPS,
                project_id=project.project_id,
                workspace_id=primary_workspace.workspace_id,
                requested_worker_profile_id=profile.profile_id,
                requested_worker_profile_version=1,
                effective_worker_snapshot_id="worker-snapshot:worker-profile-root-agent-project:1",
                selected_tools=["runtime.inspect"],
                created_at=running_ts,
                updated_at=running_ts,
            )
        )
        await store_group.work_store.save_work(
            Work(
                work_id="work-root-failed",
                task_id=failed_task_id,
                title="运维工作区巡检",
                kind=WorkKind.DELEGATION,
                status=WorkStatus.FAILED,
                target_kind=DelegationTargetKind.ACP_RUNTIME,
                selected_worker_type=WorkerType.OPS,
                project_id=project.project_id,
                workspace_id=secondary_workspace.workspace_id,
                requested_worker_profile_id=profile.profile_id,
                requested_worker_profile_version=1,
                effective_worker_snapshot_id="worker-snapshot:worker-profile-root-agent-project:1",
                selected_tools=["runtime.inspect"],
                created_at=failed_ts,
                updated_at=failed_ts,
                completed_at=failed_ts,
            )
        )
        await store_group.conn.commit()

        worker_profiles_resp = await control_plane_client.get("/api/control/resources/worker-profiles")
        assert worker_profiles_resp.status_code == 200
        payload = worker_profiles_resp.json()
        target = next(
            item for item in payload["profiles"] if item["profile_id"] == profile.profile_id
        )

        assert target["dynamic_context"]["active_project_id"] == project.project_id
        assert (
            target["dynamic_context"]["active_workspace_id"]
            == secondary_workspace.workspace_id
        )
        assert target["dynamic_context"]["active_work_count"] == 1
        assert target["dynamic_context"]["running_work_count"] == 1
        assert target["dynamic_context"]["attention_work_count"] == 1
        assert target["dynamic_context"]["latest_work_id"] == "work-root-failed"
        assert target["dynamic_context"]["latest_work_status"] == "failed"
