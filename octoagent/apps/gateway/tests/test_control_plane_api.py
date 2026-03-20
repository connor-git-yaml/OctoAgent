from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from octoagent.core.models import (
    A2AConversation,
    A2AConversationStatus,
    A2AMessageDirection,
    A2AMessageRecord,
    AgentProfile,
    AgentProfileScope,
    AgentRuntime,
    AgentRuntimeRole,
    AgentSession,
    AgentSessionKind,
    AgentSessionTurn,
    AgentSessionTurnKind,
    BootstrapSession,
    BootstrapSessionStatus,
    ContextFrame,
    DelegationTargetKind,
    DynamicToolSelection,
    EffectiveToolUniverse,
    MemoryNamespace,
    MemoryNamespaceKind,
    NormalizedMessage,
    OrchestratorRequest,
    OwnerOverlayScope,
    OwnerProfile,
    OwnerProfileOverlay,
    ProjectBinding,
    ProjectBindingType,
    ProjectSelectorState,
    RecallFrame,
    SessionContextState,
    ToolAvailabilityExplanation,
    ToolIndexQuery,
    Work,
    WorkerProfile,
    WorkerProfileOriginKind,
    WorkerProfileRevision,
    WorkerProfileStatus,
    WorkKind,
    Workspace,
    WorkspaceKind,
    WorkStatus,
)
from octoagent.gateway.services.agent_context import (
    build_projected_session_id,
    build_scope_aware_session_id,
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
from octoagent.provider.auth.credentials import OAuthCredential
from octoagent.provider.auth.environment import EnvironmentContext
from octoagent.provider.auth.store import CredentialStore
from octoagent.provider.dx.config_schema import (
    MemoryConfig,
    ModelAlias,
    OctoAgentConfig,
    ProviderEntry,
)
from octoagent.provider.dx.config_wizard import save_config
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


async def _seed_context_resources(app) -> None:
    store_group = app.state.store_group
    project = await store_group.project_store.get_default_project()
    assert project is not None
    workspace = await store_group.project_store.get_primary_workspace(project.project_id)
    assert workspace is not None
    tasks = await store_group.task_store.list_tasks()
    seeded_task_id = (
        tasks[0].task_id
        if tasks
        else await _create_task(
            app,
            text="control plane context seed",
            thread_id="thread-control-context",
            scope_id="scope-control",
        )
    )
    runtime = AgentRuntime(
        agent_runtime_id="runtime-butler-default",
        project_id=project.project_id,
        workspace_id=workspace.workspace_id,
        agent_profile_id="agent-profile-default",
        role=AgentRuntimeRole.BUTLER,
        name="Default Agent",
        persona_summary="用于 control plane 可视化的默认 runtime。",
    )
    agent_session = AgentSession(
        agent_session_id="agent-session-butler-default",
        agent_runtime_id=runtime.agent_runtime_id,
        kind=AgentSessionKind.BUTLER_MAIN,
        project_id=project.project_id,
        workspace_id=workspace.workspace_id,
        surface="web",
        thread_id="thread-control-context",
        legacy_session_id="thread-control-context",
        last_context_frame_id="context-frame-default",
        last_recall_frame_id="recall-frame-default",
    )
    project_namespace = MemoryNamespace(
        namespace_id="memory-namespace-project-default",
        project_id=project.project_id,
        workspace_id=workspace.workspace_id,
        agent_runtime_id=runtime.agent_runtime_id,
        kind=MemoryNamespaceKind.PROJECT_SHARED,
        name="Project Shared",
        description="控制面 project shared namespace。",
        memory_scope_ids=["memory/project-alpha"],
    )
    private_namespace = MemoryNamespace(
        namespace_id="memory-namespace-butler-default",
        project_id=project.project_id,
        workspace_id=workspace.workspace_id,
        agent_runtime_id=runtime.agent_runtime_id,
        kind=MemoryNamespaceKind.BUTLER_PRIVATE,
        name="Butler Private",
        description="控制面 butler private namespace。",
    )
    await store_group.agent_context_store.save_agent_profile(
        AgentProfile(
            profile_id="agent-profile-default",
            scope=AgentProfileScope.PROJECT,
            project_id=project.project_id,
            name="Default Agent",
            persona_summary="用于 control plane 可视化的默认 profile。",
            bootstrap_template_ids=[
                "behavior:system:AGENTS.md",
                "behavior:system:USER.md",
                "behavior:system:TOOLS.md",
                "behavior:system:BOOTSTRAP.md",
                "behavior:agent:IDENTITY.md",
                "behavior:agent:SOUL.md",
                "behavior:agent:HEARTBEAT.md",
                "behavior:project:PROJECT.md",
                "behavior:project:KNOWLEDGE.md",
                "behavior:project:USER.md",
                "behavior:project:TOOLS.md",
                "behavior:project:instructions/README.md",
            ],
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
            metadata={
                "questionnaire": {
                    "owner_identity": {
                        "route": "memory",
                        "target": "OwnerProfile + Memory",
                        "summary": "用户怎么称呼自己、长期个人事实。",
                    },
                    "assistant_identity": {
                        "route": "behavior",
                        "target": "IDENTITY.md",
                        "summary": "默认会话 Agent 的身份与名称。",
                    },
                    "secret_routing": {
                        "route": "secrets",
                        "target": "projects/default-project/project.secret-bindings.json",
                        "summary": "敏感值进入 project secret bindings。",
                    },
                }
            },
        )
    )
    await store_group.agent_context_store.save_agent_runtime(runtime)
    await store_group.agent_context_store.save_agent_session(agent_session)
    await store_group.agent_context_store.save_memory_namespace(project_namespace)
    await store_group.agent_context_store.save_memory_namespace(private_namespace)
    await store_group.agent_context_store.save_session_context(
        SessionContextState(
            session_id="thread-control-context",
            agent_runtime_id=runtime.agent_runtime_id,
            agent_session_id=agent_session.agent_session_id,
            thread_id="thread-control-context",
            project_id=project.project_id,
            workspace_id=workspace.workspace_id,
            task_ids=[seeded_task_id],
            rolling_summary="控制面可以直接看到 recent summary。",
            last_context_frame_id="context-frame-default",
            last_recall_frame_id="recall-frame-default",
        )
    )
    await store_group.agent_context_store.save_recall_frame(
        RecallFrame(
            recall_frame_id="recall-frame-default",
            agent_runtime_id=runtime.agent_runtime_id,
            agent_session_id=agent_session.agent_session_id,
            context_frame_id="context-frame-default",
            task_id=seeded_task_id,
            project_id=project.project_id,
            workspace_id=workspace.workspace_id,
            query="project alpha next step",
            recent_summary="控制面可以直接看到 recent summary。",
            memory_namespace_ids=[
                project_namespace.namespace_id,
                private_namespace.namespace_id,
            ],
            memory_hits=[
                {
                    "record_id": "memory-1",
                    "summary": "memory visible",
                    "search_query": "project alpha next step",
                }
            ],
            source_refs=[
                {
                    "ref_type": "memory_namespace",
                    "ref_id": project_namespace.namespace_id,
                    "label": "project_shared",
                }
            ],
            budget={
                "memory_recall": {
                    "backend_id": "sqlite",
                    "retrieval_backend": "sqlite",
                    "scope_ids": ["memory/project-alpha"],
                    "hit_count": 1,
                }
            },
        )
    )
    await store_group.agent_context_store.save_context_frame(
        ContextFrame(
            context_frame_id="context-frame-default",
            task_id=seeded_task_id,
            session_id="thread-control-context",
            agent_runtime_id=runtime.agent_runtime_id,
            agent_session_id=agent_session.agent_session_id,
            project_id=project.project_id,
            workspace_id=workspace.workspace_id,
            agent_profile_id="agent-profile-default",
            owner_profile_id="owner-profile-default",
            owner_overlay_id="owner-overlay-default",
            bootstrap_session_id="bootstrap-default",
            recall_frame_id="recall-frame-default",
            memory_namespace_ids=[
                project_namespace.namespace_id,
                private_namespace.namespace_id,
            ],
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
    await store_group.a2a_store.save_conversation(
        A2AConversation(
            a2a_conversation_id="work-weather-default",
            task_id=seeded_task_id,
            work_id="work-weather-default",
            project_id=project.project_id,
            workspace_id=workspace.workspace_id,
            source_agent_runtime_id=runtime.agent_runtime_id,
            source_agent_session_id=agent_session.agent_session_id,
            target_agent_runtime_id="runtime-worker-research-default",
            target_agent_session_id="agent-session-worker-research-default",
            source_agent="agent://butler.main",
            target_agent="agent://worker.llm.research",
            context_frame_id="context-frame-default",
            request_message_id="a2a-message-task-default",
            latest_message_id="a2a-message-result-default",
            latest_message_type="RESULT",
            status=A2AConversationStatus.COMPLETED,
            message_count=2,
            trace_id="trace-task-context",
            metadata={"worker_capability": "research"},
        )
    )
    await store_group.a2a_store.save_message(
        A2AMessageRecord(
            a2a_message_id="a2a-message-task-default",
            a2a_conversation_id="work-weather-default",
            message_seq=1,
            task_id=seeded_task_id,
            work_id="work-weather-default",
            project_id=project.project_id,
            workspace_id=workspace.workspace_id,
            source_agent_runtime_id=runtime.agent_runtime_id,
            source_agent_session_id=agent_session.agent_session_id,
            target_agent_runtime_id="runtime-worker-research-default",
            target_agent_session_id="agent-session-worker-research-default",
            direction=A2AMessageDirection.OUTBOUND,
            message_type="TASK",
            protocol_message_id="dispatch-weather-default",
            from_agent="agent://butler.main",
            to_agent="agent://worker.llm.research",
            idempotency_key=f"{seeded_task_id}:dispatch-weather-default:task",
            payload={"user_text": "深圳今天天气怎么样？"},
            trace={"trace_id": "trace-task-context"},
            metadata={"route_reason": "freshness"},
            raw_message={"type": "TASK"},
        )
    )
    await store_group.a2a_store.save_message(
        A2AMessageRecord(
            a2a_message_id="a2a-message-result-default",
            a2a_conversation_id="work-weather-default",
            message_seq=2,
            task_id=seeded_task_id,
            work_id="work-weather-default",
            project_id=project.project_id,
            workspace_id=workspace.workspace_id,
            source_agent_runtime_id="runtime-worker-research-default",
            source_agent_session_id="agent-session-worker-research-default",
            target_agent_runtime_id=runtime.agent_runtime_id,
            target_agent_session_id=agent_session.agent_session_id,
            direction=A2AMessageDirection.INBOUND,
            message_type="RESULT",
            protocol_message_id="dispatch-weather-default-result",
            from_agent="agent://worker.llm.research",
            to_agent="agent://butler.main",
            idempotency_key=f"{seeded_task_id}:dispatch-weather-default:result",
            payload={"summary": "深圳晴，21C。"},
            trace={"trace_id": "trace-task-context"},
            metadata={"backend": "inline"},
            raw_message={"type": "RESULT"},
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
        assert payload["status"] == "ready"
        assert payload["contract_version"] == "1.0.0"
        assert payload["degraded_sections"] == []
        assert payload["resource_errors"] == {}
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
            "mcp_provider_catalog",
            "setup_governance",
            "delegation",
            "pipelines",
            "automation",
            "diagnostics",
            "retrieval_platform",
            "memory",
            "imports",
        }
        assert payload["registry"]["resource_type"] == "action_registry"
        assert any(item["action_id"] == "project.select" for item in payload["registry"]["actions"])
        assert any(item["action_id"] == "setup.review" for item in payload["registry"]["actions"])
        assert any(item["action_id"] == "setup.apply" for item in payload["registry"]["actions"])
        assert any(item["action_id"] == "memory.query" for item in payload["registry"]["actions"])
        assert any(
            item["action_id"] == "retrieval.index.start" for item in payload["registry"]["actions"]
        )
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
        assert payload["resources"]["memory"]["retrieval_profile"]["engine_label"]
        assert payload["resources"]["memory"]["retrieval_profile"]["bindings"]
        assert "index_health" in payload["resources"]["memory"]
        assert payload["resources"]["retrieval_platform"]["resource_type"] == "retrieval_platform"
        assert payload["resources"]["imports"]["resource_type"] == "import_workbench"
        # agent profile 的 ID 格式改为 agent-profile-{project_id}，默认项目 ID 为 project-default
        assert payload["resources"]["agent_profiles"]["profiles"][0]["profile_id"] == (
            "agent-profile-project-default"
        )
        assert (
            payload["resources"]["agent_profiles"]["profiles"][0]["bootstrap_template_ids"][0]
            == "behavior:system:AGENTS.md"
        )
        # source_chain 非空，包含至少一个 filesystem 或 default_behavior_templates 来源
        source_chain = payload["resources"]["agent_profiles"]["profiles"][0]["behavior_system"][
            "source_chain"
        ]
        assert len(source_chain) >= 1
        # 应包含 filesystem 路径或 default_behavior_templates
        assert any("filesystem:" in s or s == "default_behavior_templates" for s in source_chain)
        assert (
            "direct_answer"
            in payload["resources"]["agent_profiles"]["profiles"][0]["behavior_system"][
                "decision_modes"
            ]
        )
        assert (
            "effective_location_hint"
            in payload["resources"]["agent_profiles"]["profiles"][0]["behavior_system"][
                "runtime_hint_fields"
            ]
        )
        assert (
            payload["resources"]["agent_profiles"]["profiles"][0]["behavior_system"][
                "bootstrap_templates"
            ]["shared"][0]
            == "behavior:system:AGENTS.md"
        )
        assert (
            payload["resources"]["agent_profiles"]["profiles"][0]["behavior_system"][
                "bootstrap_routes"
            ]["assistant_identity"]["target"]
            == "IDENTITY.md"
        )
        worker_profile = payload["resources"]["worker_profiles"]["profiles"][0]
        assert worker_profile["profile_id"] == "singleton:general"
        assert worker_profile["mode"] == "singleton"
        assert "active_work_count" in worker_profile["dynamic_context"]
        assert payload["resources"]["owner_profile"]["profile"]["owner_profile_id"] == (
            "owner-profile-default"
        )
        assert payload["resources"]["bootstrap_session"]["session"]["bootstrap_id"] == (
            "bootstrap-default"
        )
        assert (
            payload["resources"]["bootstrap_session"]["session"]["metadata"]["questionnaire"][
                "secret_routing"
            ]["route"]
            == "secrets"
        )
        assert payload["resources"]["context_continuity"]["frames"][0]["context_frame_id"] == (
            "context-frame-default"
        )
        assert (
            payload["resources"]["context_continuity"]["agent_runtimes"][0]["agent_runtime_id"]
            == "runtime-butler-default"
        )
        assert (
            payload["resources"]["context_continuity"]["agent_sessions"][0]["agent_session_id"]
            == "agent-session-butler-default"
        )
        assert {
            item["kind"] for item in payload["resources"]["context_continuity"]["memory_namespaces"]
        } == {"project_shared", "butler_private"}
        assert (
            payload["resources"]["context_continuity"]["recall_frames"][0]["recall_frame_id"]
            == "recall-frame-default"
        )
        assert (
            payload["resources"]["context_continuity"]["a2a_conversations"][0][
                "a2a_conversation_id"
            ]
            == "work-weather-default"
        )
        assert (
            payload["resources"]["context_continuity"]["a2a_messages"][0]["a2a_message_id"]
            == "a2a-message-task-default"
        )
        frame = payload["resources"]["context_continuity"]["frames"][0]
        assert frame["project_id"]
        assert frame["workspace_id"]
        assert frame["agent_runtime_id"] == "runtime-butler-default"
        assert frame["agent_session_id"] == "agent-session-butler-default"
        assert frame["recall_frame_id"] == "recall-frame-default"
        assert set(frame["memory_namespace_ids"]) == {
            "memory-namespace-project-default",
            "memory-namespace-butler-default",
        }
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
        worker_profiles_resp = await control_plane_client.get(
            "/api/control/resources/worker-profiles"
        )
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
        assert context_payload["sessions"][0]["agent_runtime_id"] == "runtime-butler-default"
        assert context_payload["sessions"][0]["last_recall_frame_id"] == "recall-frame-default"
        assert context_payload["frames"][0]["memory_recall"]["hit_count"] == 1
        assert context_payload["recall_frames"][0]["agent_session_id"] == (
            "agent-session-butler-default"
        )
        assert context_payload["a2a_conversations"][0]["target_agent"] == (
            "agent://worker.llm.research"
        )
        assert context_payload["a2a_messages"][1]["message_type"] == "RESULT"
        assert context_payload["frames"][0]["source_refs"][1]["ref_id"] == "memory-1"
        assert policy_resp.status_code == 200
        assert skill_resp.status_code == 200
        assert setup_resp.status_code == 200
        worker_profiles_payload = worker_profiles_resp.json()
        assert worker_profiles_payload["resource_type"] == "worker_profiles"
        assert worker_profiles_payload["profiles"][0]["dynamic_context"]["active_project_id"]

    async def test_snapshot_exposes_butler_owned_freshness_runtime_truth(
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

        task_id = await _create_task(
            control_plane_app,
            text="深圳今天天气怎么样？",
            thread_id="thread-freshness-parent",
            scope_id=project.project_id,
        )
        await store_group.work_store.save_work(
            Work(
                work_id="work-freshness-parent",
                task_id=task_id,
                title="深圳今天天气怎么样？",
                kind=WorkKind.DELEGATION,
                status=WorkStatus.SUCCEEDED,
                target_kind=DelegationTargetKind.WORKER,
                selected_worker_type="general",
                route_reason="delegation_strategy=butler_owned_freshness",
                owner_id="butler.main",
                project_id=project.project_id,
                workspace_id=workspace.workspace_id,
                selected_tools=[],
                metadata={
                    "delegation_strategy": "butler_owned_freshness",
                    "final_speaker": "butler",
                    "research_child_task_id": "task-freshness-child",
                    "research_child_thread_id": "thread-freshness-parent:freshness:abc123",
                    "research_child_work_id": "work-freshness-child",
                    "research_child_status": "SUCCEEDED",
                    "research_worker_status": "SUCCEEDED",
                    "research_worker_id": "worker.llm.research",
                    "research_route_reason": "worker_type=research | fallback=single_worker",
                    "research_tool_profile": "standard",
                    "research_a2a_conversation_id": "a2a-freshness-child",
                    "research_butler_agent_session_id": "agent-session-butler-default",
                    "research_worker_agent_session_id": "agent-session-worker-research-child",
                    "research_a2a_message_count": 2,
                    "research_result_artifact_ref": "artifact-freshness-result",
                    "research_handoff_artifact_ref": "artifact-freshness-handoff",
                },
            )
        )
        await store_group.conn.commit()

        resp = await control_plane_client.get("/api/control/snapshot")

        assert resp.status_code == 200
        payload = resp.json()
        work = next(
            item
            for item in payload["resources"]["delegation"]["works"]
            if item["work_id"] == "work-freshness-parent"
        )
        runtime_summary = work["runtime_summary"]
        assert runtime_summary["delegation_strategy"] == "butler_owned_freshness"
        assert runtime_summary["final_speaker"] == "butler"
        assert runtime_summary["research_child_task_id"] == "task-freshness-child"
        assert runtime_summary["research_tool_profile"] == "standard"
        assert runtime_summary["research_a2a_conversation_id"] == "a2a-freshness-child"
        assert runtime_summary["research_butler_agent_session_id"] == (
            "agent-session-butler-default"
        )
        assert runtime_summary["research_worker_agent_session_id"] == (
            "agent-session-worker-research-child"
        )
        assert runtime_summary["research_a2a_message_count"] == 2

    async def test_snapshot_returns_partial_degraded_payload_when_section_fails(
        self,
        control_plane_client: AsyncClient,
        seeded_memory_control_plane,
        monkeypatch,
    ) -> None:
        async def broken_memory_console():
            raise RuntimeError("memory backend offline")

        monkeypatch.setattr(
            seeded_memory_control_plane.state.control_plane_service,
            "get_memory_console",
            broken_memory_console,
        )

        resp = await control_plane_client.get("/api/control/snapshot")

        assert resp.status_code == 200
        payload = resp.json()
        assert payload["status"] == "degraded"
        assert "memory" in payload["degraded_sections"]
        assert payload["resource_errors"]["memory"]["code"] == "SNAPSHOT_SECTION_UNAVAILABLE"
        assert payload["resources"]["memory"]["resource_type"] == "memory_unavailable"
        assert payload["resources"]["memory"]["degraded"]["is_degraded"] is True

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
        assert any("体验模式" in item["summary"] for item in review["provider_runtime_risks"])

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

    async def test_setup_review_blocks_invalid_memory_alias_in_draft(
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
                            "runtime": {
                                "llm_mode": "echo",
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
                            "memory": {
                                "embedding_model_alias": "mem-embed",
                            },
                        }
                    }
                },
            },
        )

        assert resp.status_code == 200
        review = resp.json()["result"]["data"]["review"]
        assert any(
            item["risk_id"] == "memory_alias_missing:memory.embedding_model_alias"
            and item["blocking"] is True
            for item in review["provider_runtime_risks"]
        )
        assert any(
            "memory.embedding_model_alias" in item["summary"]
            for item in review["provider_runtime_risks"]
        )

    async def test_setup_review_accepts_valid_memory_alias_in_draft(
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
                            "runtime": {
                                "llm_mode": "echo",
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
                                },
                                "mem-embed": {
                                    "provider": "openrouter",
                                    "model": "openrouter/qwen/qwen3-embedding-8b",
                                },
                            },
                            "memory": {
                                "embedding_model_alias": "mem-embed",
                            },
                        }
                    }
                },
            },
        )

        assert resp.status_code == 200
        review = resp.json()["result"]["data"]["review"]
        assert all(
            not item["risk_id"].startswith("memory_alias_missing:")
            and not item["risk_id"].startswith("memory_alias_provider_unavailable:")
            for item in review["provider_runtime_risks"]
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
        skill_doc = (
            await control_plane_app.state.control_plane_service.get_skill_governance_document()
        )
        target_skill = next(item for item in skill_doc.items if item.source_kind == "builtin")
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
        assert any(item["resource_type"] == "policy_profiles" for item in result["resource_refs"])
        assert any(item["resource_type"] == "agent_profiles" for item in result["resource_refs"])

        config_doc = await control_plane_app.state.control_plane_service.get_config_schema()
        assert config_doc.current_value["providers"][0]["id"] == "openai"
        assert config_doc.current_value["model_aliases"]["main"]["provider"] == "openai"

        default_project = (
            await control_plane_app.state.store_group.project_store.get_default_project()
        )
        assert default_project is not None
        assert default_project.metadata["policy_profile_id"] == "strict"
        assert (
            target_skill.item_id in default_project.metadata["skill_selection"]["disabled_item_ids"]
        )
        assert default_project.default_agent_profile_id

        saved_profile = (
            await control_plane_app.state.store_group.agent_context_store.get_agent_profile(
                default_project.default_agent_profile_id
            )
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
        skill_doc = (
            await control_plane_app.state.control_plane_service.get_skill_governance_document()
        )
        target_skill = next(item for item in skill_doc.items if item.source_kind == "builtin")

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
        assert (
            target_skill.item_id in default_project.metadata["skill_selection"]["disabled_item_ids"]
        )

    async def test_skills_selection_filters_runtime_tool_selection(
        self,
        control_plane_app,
        control_plane_client: AsyncClient,
        seeded_control_plane,
    ) -> None:
        skill_doc = (
            await control_plane_app.state.control_plane_service.get_skill_governance_document()
        )
        # Feature 057: 使用 SkillDiscovery 发现的第一个 skill 作为测试目标
        skill_items = [item for item in skill_doc.items if item.source_kind != "mcp"]
        assert skill_items, "至少需要一个 skill governance item"
        target_skill = skill_items[0]

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
                worker_type="ops",
                project_id=default_project.project_id,
                workspace_id=workspace.workspace_id,
            ),
            worker_type="ops",
        )

        assert selection.selected_tools

    async def test_agent_profile_capability_selection_overrides_project_default(
        self,
        control_plane_app,
        control_plane_client: AsyncClient,
        seeded_control_plane,
    ) -> None:
        skill_doc = (
            await control_plane_app.state.control_plane_service.get_skill_governance_document()
        )
        # Feature 057: 使用 SkillDiscovery 发现的第一个 skill 作为测试目标
        skill_items = [item for item in skill_doc.items if item.source_kind != "mcp"]
        assert skill_items, "至少需要一个 skill governance item"
        target_skill = skill_items[0]

        disable_resp = await control_plane_client.post(
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
        assert disable_resp.status_code == 200

        save_resp = await control_plane_client.post(
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
                        "name": "运行排障主 Agent",
                        "persona_summary": "优先诊断运行健康。",
                        "tool_profile": "standard",
                        "model_alias": "main",
                        "metadata": {
                            "capability_provider_selection": {
                                "selected_item_ids": [target_skill.item_id],
                            }
                        },
                    }
                },
            },
        )
        assert save_resp.status_code == 200
        profile_id = save_resp.json()["result"]["data"]["profile_id"]

        default_project = (
            await control_plane_app.state.store_group.project_store.get_default_project()
        )
        assert default_project is not None
        workspace = await control_plane_app.state.store_group.project_store.get_primary_workspace(
            default_project.project_id
        )
        assert workspace is not None

        base_pack = await control_plane_app.state.capability_pack_service.get_pack(
            project_id=default_project.project_id,
            workspace_id=workspace.workspace_id,
        )
        overridden_pack = await control_plane_app.state.capability_pack_service.get_pack(
            project_id=default_project.project_id,
            workspace_id=workspace.workspace_id,
            profile_id=profile_id,
        )

        # Feature 057: 验证 capability selection 能正确过滤/恢复 skill
        target_name = target_skill.item_id.replace("skill:", "")
        assert target_name not in {item.skill_id for item in base_pack.skills}
        assert target_name in {item.skill_id for item in overridden_pack.skills}

    async def test_provider_catalog_actions_save_and_delete_custom_entries(
        self,
        control_plane_app,
        control_plane_client: AsyncClient,
        seeded_control_plane,
    ) -> None:
        mcp_save_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "mcp_provider.save",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "provider": {
                        "provider_id": "custom-mcp",
                        "command": "/bin/echo",
                        "args": ["mcp"],
                    }
                },
            },
        )
        assert mcp_save_resp.status_code == 200

        mcp_catalog_resp = await control_plane_client.get(
            "/api/control/resources/mcp-provider-catalog"
        )
        assert mcp_catalog_resp.status_code == 200
        mcp_items = mcp_catalog_resp.json()["items"]
        custom_mcp = next(item for item in mcp_items if item["provider_id"] == "custom-mcp")
        assert custom_mcp["enabled"] is True
        assert custom_mcp["mount_policy"] == "auto_readonly"

        mcp_delete_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "mcp_provider.delete",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "provider_id": "custom-mcp",
                },
            },
        )
        assert mcp_delete_resp.status_code == 200

    async def test_setup_apply_rejects_invalid_skill_selection_before_writing_config(
        self,
        control_plane_app,
        control_plane_client: AsyncClient,
        seeded_control_plane,
    ) -> None:
        skill_doc = (
            await control_plane_app.state.control_plane_service.get_skill_governance_document()
        )
        target_skill = next(item for item in skill_doc.items if item.source_kind == "builtin")
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

        setup_doc = (
            await control_plane_app.state.control_plane_service.get_setup_governance_document()
        )
        assert setup_doc.provider_runtime.details["openai_oauth_connected"] is True
        assert setup_doc.provider_runtime.details["openai_oauth_profile"] == "openai-codex-default"

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
        payload = resp.json()["result"]
        assert payload["code"] == "AGENT_PROFILE_SAVED"
        saved_profile_id = payload["data"]["profile_id"]

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

    async def test_agent_profile_save_rejects_unknown_model_alias(
        self,
        control_plane_app,
        control_plane_client: AsyncClient,
    ) -> None:
        default_project = (
            await control_plane_app.state.store_group.project_store.get_default_project()
        )
        assert default_project is not None
        original_default_profile_id = default_project.default_agent_profile_id

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
                        "name": "错误 alias Agent",
                        "tool_profile": "minimal",
                        "model_alias": "reasoning",
                    }
                },
            },
        )

        assert resp.status_code == 400
        payload = resp.json()["result"]
        assert payload["code"] == "AGENT_PROFILE_MODEL_ALIAS_INVALID"
        assert "模型别名 'reasoning' 不存在" in payload["message"]
        reloaded_default = await control_plane_app.state.store_group.project_store.get_project(
            default_project.project_id
        )
        assert reloaded_default is not None
        assert reloaded_default.default_agent_profile_id == original_default_profile_id
        agent_profiles = await control_plane_app.state.store_group.agent_context_store.list_agent_profiles()
        assert all(profile.name != "错误 alias Agent" for profile in agent_profiles)

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
        assert "memory.reasoning_model_alias" in hints
        assert "memory.expand_model_alias" in hints
        assert "memory.embedding_model_alias" in hints
        assert "memory.rerank_model_alias" in hints
        assert "channels.telegram.dm_policy" in hints
        assert "channels.telegram.group_policy" in hints
        assert "channels.telegram.group_allow_users" in hints

    async def test_retrieval_platform_keeps_old_embedding_active_until_cancelled_generation_is_resolved(
        self,
        control_plane_client: AsyncClient,
        seeded_control_plane,
    ) -> None:
        baseline = await control_plane_client.get("/api/control/resources/retrieval-platform")
        assert baseline.status_code == 200
        baseline_memory = next(
            item for item in baseline.json()["corpora"] if item["corpus_kind"] == "memory"
        )
        # 无任何 active generation 时，active_profile_target 回落为 desired_profile.target（即 engine-default）
        assert baseline_memory["active_profile_target"] == "engine-default"

        save_config(
            OctoAgentConfig(
                updated_at="2026-03-15T12:00:00Z",
                providers=[
                    ProviderEntry(
                        id="openrouter",
                        name="OpenRouter",
                        auth_type="api_key",
                        api_key_env="OPENROUTER_API_KEY",
                        enabled=True,
                    )
                ],
                model_aliases={
                    "main": ModelAlias(provider="openrouter", model="openrouter/auto"),
                    "knowledge-embed": ModelAlias(
                        provider="openrouter",
                        model="openai/text-embedding-3-small",
                    ),
                },
                memory=MemoryConfig(embedding_model_alias="knowledge-embed"),
            ),
            seeded_control_plane.state.project_root,
        )

        pending_resp = await control_plane_client.get("/api/control/resources/retrieval-platform")
        assert pending_resp.status_code == 200
        pending_payload = pending_resp.json()
        pending_memory = next(
            item for item in pending_payload["corpora"] if item["corpus_kind"] == "memory"
        )
        assert pending_memory["active_profile_target"] == "engine-default"
        assert pending_memory["desired_profile_target"] == "knowledge-embed"
        assert pending_memory["pending_generation_id"]

        cancel_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": "req-retrieval-cancel",
                "action_id": "retrieval.index.cancel",
                "params": {"generation_id": pending_memory["pending_generation_id"]},
                "surface": "web",
                "actor": {"actor_id": "owner:test", "actor_label": "Owner"},
            },
        )
        assert cancel_resp.status_code == 200
        assert cancel_resp.json()["result"]["code"] == "RETRIEVAL_BUILD_CANCELLED"

        after_cancel = await control_plane_client.get("/api/control/resources/retrieval-platform")
        assert after_cancel.status_code == 200
        cancelled_memory = next(
            item for item in after_cancel.json()["corpora"] if item["corpus_kind"] == "memory"
        )
        assert cancelled_memory["active_profile_target"] == "engine-default"
        assert cancelled_memory["pending_generation_id"] == ""
        assert cancelled_memory["state"] == "migration_deferred"

        restart_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": "req-retrieval-restart",
                "action_id": "retrieval.index.start",
                "params": {},
                "surface": "web",
                "actor": {"actor_id": "owner:test", "actor_label": "Owner"},
            },
        )
        assert restart_resp.status_code == 200
        assert restart_resp.json()["result"]["code"] == "RETRIEVAL_BUILD_STARTED"

        after_restart = await control_plane_client.get("/api/control/resources/retrieval-platform")
        assert after_restart.status_code == 200
        restarted_memory = next(
            item for item in after_restart.json()["corpora"] if item["corpus_kind"] == "memory"
        )
        assert restarted_memory["active_profile_target"] == "engine-default"
        assert restarted_memory["desired_profile_target"] == "knowledge-embed"
        assert restarted_memory["pending_generation_id"]
        assert restarted_memory["state"] in {"migration_running", "migration_pending"}

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
        assert {item.selected_worker_type for item in child_works} == {"research"}
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
        assert {item["tool_profile"] for item in worker_plan["assignments"]} == {
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
        assert {item.selected_worker_type for item in child_works} >= {"research", "dev"}
        assert {str(item.metadata.get("requested_tool_profile", "")) for item in child_works} == {
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
        assert memory_payload["retrieval_profile"]["engine_label"]
        assert memory_payload["retrieval_profile"]["bindings"]
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
        # memory.bridge.reconnect 已被移除，不再断言此 action
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
            "project_id": workspace.project_id,
            "workspace_id": workspace.workspace_id,
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

    async def test_session_new_uses_server_side_token_to_suppress_restore(
        self,
        control_plane_app,
        control_plane_client: AsyncClient,
    ) -> None:
        task_id = await _create_task(
            control_plane_app,
            text="start a fresh chat lifecycle",
            thread_id="thread-session-new",
        )
        task = await control_plane_app.state.store_group.task_store.get_task(task_id)
        assert task is not None
        workspace = (
            await control_plane_app.state.store_group.project_store.resolve_workspace_for_scope(
                task.scope_id
            )
        )
        assert workspace is not None
        session_id = build_scope_aware_session_id(
            task,
            project_id=workspace.project_id,
            workspace_id=workspace.workspace_id,
        )

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
                    "session_id": session_id,
                },
            },
        )
        assert focus_resp.status_code == 200

        new_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "session.new",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "task_id": task_id,
                },
            },
        )

        assert new_resp.status_code == 200
        new_result = new_resp.json()["result"]
        assert new_result["code"] == "SESSION_NEW_READY"
        assert new_result["data"]["previous_session_id"] == session_id
        assert new_result["data"]["previous_task_id"] == task_id
        assert new_result["data"]["new_conversation_token"]
        assert new_result["data"]["project_id"] == workspace.project_id
        assert new_result["data"]["workspace_id"] == workspace.workspace_id

        sessions_resp = await control_plane_client.get("/api/control/resources/sessions")
        assert sessions_resp.status_code == 200
        payload = sessions_resp.json()
        assert payload["focused_session_id"] == ""
        assert payload["focused_thread_id"] == ""
        assert payload["new_conversation_token"] == new_result["data"]["new_conversation_token"]
        assert payload["new_conversation_project_id"] == workspace.project_id
        assert payload["new_conversation_workspace_id"] == workspace.workspace_id
        assert payload["new_conversation_agent_profile_id"] == ""

    async def test_session_new_can_prepare_explicit_agent_session_entry(
        self,
        control_plane_app,
        control_plane_client: AsyncClient,
    ) -> None:
        profile_id = "worker-profile-direct-research"
        await control_plane_app.state.store_group.agent_context_store.save_worker_profile(
            WorkerProfile(
                profile_id=profile_id,
                project_id="",
                name="研究员小 A",
                summary="direct research root",
                model_alias="cheap",
                status=WorkerProfileStatus.ACTIVE,
            )
        )
        await control_plane_app.state.store_group.conn.commit()

        resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "session.new",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "agent_profile_id": profile_id,
                },
            },
        )

        assert resp.status_code == 200
        result = resp.json()["result"]
        assert result["code"] == "SESSION_NEW_READY"
        assert result["data"]["agent_profile_id"] == profile_id

        sessions_resp = await control_plane_client.get("/api/control/resources/sessions")
        assert sessions_resp.status_code == 200
        payload = sessions_resp.json()
        assert payload["new_conversation_agent_profile_id"] == profile_id

    async def test_session_create_with_project_returns_projected_session_id_and_thread_seed(
        self,
        control_plane_app,
        control_plane_client: AsyncClient,
    ) -> None:
        profile_id = "worker-profile-fin-direct"
        await control_plane_app.state.store_group.agent_context_store.save_worker_profile(
            WorkerProfile(
                profile_id=profile_id,
                project_id="",
                name="研究员小 A",
                summary="finance direct session",
                model_alias="cheap",
                status=WorkerProfileStatus.ACTIVE,
            )
        )
        await control_plane_app.state.store_group.conn.commit()

        resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "session.create_with_project",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "agent_profile_id": profile_id,
                    "project_name": "fin",
                },
            },
        )

        assert resp.status_code == 200
        result = resp.json()["result"]
        assert result["code"] == "SESSION_CREATED_WITH_PROJECT"
        projected_session_id = result["data"]["session_id"]
        agent_session_id = result["data"]["agent_session_id"]
        thread_id = result["data"]["thread_id"]
        project_id = result["data"]["project_id"]
        workspace_id = result["data"]["workspace_id"]
        assert projected_session_id == build_projected_session_id(
            thread_id=thread_id,
            surface="web",
            scope_id=f"workspace:{workspace_id}:chat:web:{thread_id}",
            project_id=project_id,
            workspace_id=workspace_id,
        )
        agent_session = (
            await control_plane_app.state.store_group.agent_context_store.get_agent_session(
                agent_session_id
            )
        )
        assert agent_session is not None
        assert agent_session.kind is AgentSessionKind.DIRECT_WORKER
        runtime = await control_plane_app.state.store_group.agent_context_store.get_agent_runtime(
            agent_session.agent_runtime_id
        )
        assert runtime is not None
        assert runtime.role is AgentRuntimeRole.WORKER
        assert runtime.worker_profile_id == profile_id

        sessions_resp = await control_plane_client.get("/api/control/resources/sessions")
        assert sessions_resp.status_code == 200
        payload = sessions_resp.json()
        item = next(
            (entry for entry in payload["sessions"] if entry["session_id"] == projected_session_id),
            None,
        )
        assert item is not None
        assert item["thread_id"] == thread_id
        assert item["task_id"] == ""
        assert item["agent_profile_id"] == profile_id
        assert item["session_owner_profile_id"] == profile_id
        assert item["turn_executor_kind"] == "worker"
        assert item["delegation_target_profile_id"] == ""
        assert item["runtime_kind"] == AgentSessionKind.DIRECT_WORKER.value

        internal_runtime = AgentRuntime(
            agent_runtime_id="runtime-internal-worker",
            project_id=project_id,
            workspace_id=workspace_id,
            worker_profile_id=profile_id,
            role=AgentRuntimeRole.WORKER,
            name="internal worker",
        )
        await control_plane_app.state.store_group.agent_context_store.save_agent_runtime(
            internal_runtime
        )
        await control_plane_app.state.store_group.agent_context_store.save_agent_session(
            AgentSession(
                agent_session_id="session-worker-internal",
                agent_runtime_id=internal_runtime.agent_runtime_id,
                kind=AgentSessionKind.WORKER_INTERNAL,
                project_id=project_id,
                workspace_id=workspace_id,
                surface="web",
                thread_id="thread-worker-internal",
                legacy_session_id="thread-worker-internal",
            )
        )
        await control_plane_app.state.store_group.conn.commit()

        sessions_resp = await control_plane_client.get("/api/control/resources/sessions")
        assert sessions_resp.status_code == 200
        payload = sessions_resp.json()
        assert all(
            entry["session_id"] != "session-worker-internal"
            and entry["runtime_kind"] != AgentSessionKind.WORKER_INTERNAL.value
            for entry in payload["sessions"]
        )

    async def test_direct_session_first_message_recovers_owner_profile_from_session_anchor(
        self,
        control_plane_app,
        control_plane_client: AsyncClient,
    ) -> None:
        profile_id = "worker-profile-finance-anchor"
        await control_plane_app.state.store_group.agent_context_store.save_worker_profile(
            WorkerProfile(
                profile_id=profile_id,
                scope=AgentProfileScope.PROJECT,
                project_id="",
                name="研究员小 A",
                summary="finance direct session",
                model_alias="cheap",
                status=WorkerProfileStatus.ACTIVE,
            )
        )
        await control_plane_app.state.store_group.conn.commit()

        create_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "session.create_with_project",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "agent_profile_id": profile_id,
                    "project_name": "fin-owner-anchor",
                },
            },
        )
        assert create_resp.status_code == 200
        create_payload = create_resp.json()["result"]["data"]
        projected_session_id = create_payload["session_id"]
        thread_id = create_payload["thread_id"]
        workspace_id = create_payload["workspace_id"]

        send_resp = await control_plane_client.post(
            "/api/chat/send",
            json={
                "message": "你好，直接会话第一条",
                "session_id": projected_session_id,
            },
        )
        assert send_resp.status_code == 200
        task_id = send_resp.json()["task_id"]

        await asyncio.sleep(0.6)

        task = await control_plane_app.state.store_group.task_store.get_task(task_id)
        assert task is not None
        assert task.thread_id == thread_id
        assert task.scope_id == f"workspace:{workspace_id}:chat:web:{thread_id}"

        events = await control_plane_app.state.store_group.event_store.get_events_for_task(task_id)
        user_events = [event for event in events if event.type.value == "USER_MESSAGE"]
        assert user_events
        metadata = user_events[-1].payload["control_metadata"]
        assert metadata["session_owner_profile_id"] == profile_id
        assert metadata["agent_profile_id"] == profile_id
        assert metadata["session_id"] == projected_session_id
        assert metadata["thread_id"] == thread_id
        assert not metadata.get("requested_worker_profile_id")

        sessions_resp = await control_plane_client.get("/api/control/resources/sessions")
        assert sessions_resp.status_code == 200
        payload = sessions_resp.json()
        item = next(
            (entry for entry in payload["sessions"] if entry["task_id"] == task_id),
            None,
        )
        assert item is not None
        assert item["session_owner_profile_id"] == profile_id
        assert item["delegation_target_profile_id"] == ""
        assert item["turn_executor_kind"] == "worker"

    async def test_direct_session_continue_message_preserves_owner_without_delegation_target(
        self,
        control_plane_app,
        control_plane_client: AsyncClient,
    ) -> None:
        profile_id = "worker-profile-finance-continue"
        await control_plane_app.state.store_group.agent_context_store.save_worker_profile(
            WorkerProfile(
                profile_id=profile_id,
                scope=AgentProfileScope.PROJECT,
                project_id="",
                name="研究员小 A",
                summary="finance direct session",
                model_alias="cheap",
                status=WorkerProfileStatus.ACTIVE,
            )
        )
        await control_plane_app.state.store_group.conn.commit()

        create_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "session.create_with_project",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "agent_profile_id": profile_id,
                    "project_name": "finance-owner-continue",
                },
            },
        )
        assert create_resp.status_code == 200
        projected_session_id = create_resp.json()["result"]["data"]["session_id"]

        first_send = await control_plane_client.post(
            "/api/chat/send",
            json={
                "message": "第一条 direct 会话消息",
                "session_id": projected_session_id,
            },
        )
        assert first_send.status_code == 200
        task_id = first_send.json()["task_id"]
        await asyncio.sleep(0.6)

        second_send = await control_plane_client.post(
            "/api/chat/send",
            json={
                "message": "继续这条直聊会话",
                "task_id": task_id,
            },
        )
        assert second_send.status_code == 200
        assert second_send.json()["task_id"] == task_id
        await asyncio.sleep(0.6)

        events = await control_plane_app.state.store_group.event_store.get_events_for_task(task_id)
        user_events = [event for event in events if event.type.value == "USER_MESSAGE"]
        assert len(user_events) >= 2
        latest_metadata = user_events[-1].payload["control_metadata"]
        assert latest_metadata["session_owner_profile_id"] == profile_id
        assert latest_metadata["agent_profile_id"] == profile_id
        assert not latest_metadata.get("requested_worker_profile_id")

    async def test_session_projection_reports_delegated_worker_execution(
        self,
        control_plane_app,
        control_plane_client: AsyncClient,
    ) -> None:
        store_group = control_plane_app.state.store_group
        project = await store_group.project_store.get_default_project()
        assert project is not None
        workspace = await store_group.project_store.get_primary_workspace(project.project_id)
        assert workspace is not None

        worker_profile_id = "worker-profile-finance-delegated"
        await store_group.agent_context_store.save_worker_profile(
            WorkerProfile(
                profile_id=worker_profile_id,
                scope=AgentProfileScope.PROJECT,
                project_id=project.project_id,
                name="金融研究员",
                summary="delegated worker projection",
                model_alias="cheap",
                status=WorkerProfileStatus.ACTIVE,
            )
        )

        thread_id = "thread-delegated-finance"
        task_id = await _create_task(
            control_plane_app,
            text="请交给金融研究员处理",
            thread_id=thread_id,
            scope_id=f"workspace:{workspace.workspace_id}:chat:web:{thread_id}",
        )
        task_service = TaskService(store_group, control_plane_app.state.sse_hub)
        await task_service.append_user_message(
            task_id=task_id,
            text="继续当前主会话",
            control_metadata={
                "session_owner_profile_id": "agent-profile-default",
                "agent_profile_id": "agent-profile-default",
            },
        )
        task = await store_group.task_store.get_task(task_id)
        assert task is not None
        projected_session_id = build_scope_aware_session_id(
            task,
            project_id=project.project_id,
            workspace_id=workspace.workspace_id,
        )
        await store_group.agent_context_store.save_agent_runtime(
            AgentRuntime(
                agent_runtime_id="runtime-delegated-main",
                project_id=project.project_id,
                workspace_id=workspace.workspace_id,
                agent_profile_id="agent-profile-default",
                role=AgentRuntimeRole.BUTLER,
                name="Delegated Main Runtime",
            )
        )
        await store_group.agent_context_store.save_agent_session(
            AgentSession(
                agent_session_id="agent-session-delegated-main",
                agent_runtime_id="runtime-delegated-main",
                kind=AgentSessionKind.BUTLER_MAIN,
                project_id=project.project_id,
                workspace_id=workspace.workspace_id,
                thread_id=thread_id,
                legacy_session_id=thread_id,
            )
        )
        await store_group.agent_context_store.save_session_context(
            SessionContextState(
                session_id=projected_session_id,
                agent_runtime_id="runtime-delegated-main",
                agent_session_id="agent-session-delegated-main",
                thread_id=thread_id,
                project_id=project.project_id,
                workspace_id=workspace.workspace_id,
                task_ids=[task_id],
            )
        )
        await store_group.work_store.save_work(
            Work(
                work_id="work-delegated-finance",
                task_id=task_id,
                title="金融研究 delegated work",
                kind=WorkKind.DELEGATION,
                status=WorkStatus.RUNNING,
                target_kind=DelegationTargetKind.WORKER,
                selected_worker_type="finance",
                project_id=project.project_id,
                workspace_id=workspace.workspace_id,
                session_owner_profile_id="agent-profile-default",
                delegation_target_profile_id=worker_profile_id,
                turn_executor_kind="worker",
                agent_profile_id="agent-profile-default",
                requested_worker_profile_id=worker_profile_id,
            )
        )
        await store_group.conn.commit()

        sessions_resp = await control_plane_client.get("/api/control/resources/sessions")
        assert sessions_resp.status_code == 200
        payload = sessions_resp.json()
        item = next(
            (entry for entry in payload["sessions"] if entry["task_id"] == task_id),
            None,
        )
        assert item is not None
        assert item["session_owner_profile_id"] == "agent-profile-default"
        assert item["turn_executor_kind"] == "worker"
        assert item["delegation_target_profile_id"] == worker_profile_id
        assert item["compatibility_flags"] == []
        assert item["reset_recommended"] is False

    async def test_legacy_butler_session_with_worker_profile_pollution_is_marked_for_reset(
        self,
        control_plane_app,
        control_plane_client: AsyncClient,
    ) -> None:
        store_group = control_plane_app.state.store_group
        project = await store_group.project_store.get_default_project()
        assert project is not None
        workspace = await store_group.project_store.get_primary_workspace(project.project_id)
        assert workspace is not None

        worker_profile_id = "worker-profile-legacy-finance"
        await store_group.agent_context_store.save_worker_profile(
            WorkerProfile(
                profile_id=worker_profile_id,
                scope=AgentProfileScope.PROJECT,
                project_id=project.project_id,
                name="旧版金融研究员",
                summary="legacy polluted worker profile",
                model_alias="cheap",
                status=WorkerProfileStatus.ACTIVE,
            )
        )

        thread_id = "thread-legacy-polluted"
        task_id = await _create_task(
            control_plane_app,
            text="legacy polluted session",
            thread_id=thread_id,
            scope_id=f"workspace:{workspace.workspace_id}:chat:web:{thread_id}",
        )
        task_service = TaskService(store_group, control_plane_app.state.sse_hub)
        await task_service.append_user_message(
            task_id=task_id,
            text="继续旧会话",
            control_metadata={"agent_profile_id": worker_profile_id},
        )
        task = await store_group.task_store.get_task(task_id)
        assert task is not None
        projected_session_id = build_scope_aware_session_id(
            task,
            project_id=project.project_id,
            workspace_id=workspace.workspace_id,
        )
        await store_group.agent_context_store.save_agent_runtime(
            AgentRuntime(
                agent_runtime_id="runtime-legacy-polluted",
                project_id=project.project_id,
                workspace_id=workspace.workspace_id,
                agent_profile_id="agent-profile-default",
                role=AgentRuntimeRole.BUTLER,
                name="Legacy Polluted Runtime",
            )
        )
        await store_group.agent_context_store.save_agent_session(
            AgentSession(
                agent_session_id="agent-session-legacy-polluted",
                agent_runtime_id="runtime-legacy-polluted",
                kind=AgentSessionKind.BUTLER_MAIN,
                project_id=project.project_id,
                workspace_id=workspace.workspace_id,
                thread_id=thread_id,
                legacy_session_id=thread_id,
            )
        )
        await store_group.agent_context_store.save_session_context(
            SessionContextState(
                session_id=projected_session_id,
                agent_runtime_id="runtime-legacy-polluted",
                agent_session_id="agent-session-legacy-polluted",
                thread_id=thread_id,
                project_id=project.project_id,
                workspace_id=workspace.workspace_id,
                task_ids=[task_id],
            )
        )
        await store_group.conn.commit()

        sessions_resp = await control_plane_client.get("/api/control/resources/sessions")
        assert sessions_resp.status_code == 200
        payload = sessions_resp.json()
        item = next(
            (entry for entry in payload["sessions"] if entry["task_id"] == task_id),
            None,
        )
        assert item is not None
        assert item["session_owner_profile_id"] == "agent-profile-default"
        assert item["delegation_target_profile_id"] == ""
        assert item["turn_executor_kind"] == "self"
        assert item["compatibility_flags"] == ["legacy_context_polluted"]
        assert item["compatibility_message"]
        assert item["reset_recommended"] is True

    async def test_session_projection_exposes_lane_summary_and_unfocus(
        self,
        control_plane_app,
        control_plane_client: AsyncClient,
    ) -> None:
        task_id = await _create_task(
            control_plane_app,
            text="review current running session",
            thread_id="thread-session-unfocus",
        )
        task = await control_plane_app.state.store_group.task_store.get_task(task_id)
        assert task is not None
        workspace = (
            await control_plane_app.state.store_group.project_store.resolve_workspace_for_scope(
                task.scope_id
            )
        )
        assert workspace is not None
        session_id = build_scope_aware_session_id(
            task,
            project_id=workspace.project_id,
            workspace_id=workspace.workspace_id,
        )

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
                    "session_id": session_id,
                },
            },
        )
        assert focus_resp.status_code == 200

        sessions_resp = await control_plane_client.get("/api/control/resources/sessions")
        assert sessions_resp.status_code == 200
        payload = sessions_resp.json()
        assert payload["summary"]["total_sessions"] >= 1
        assert payload["summary"]["queued_sessions"] + payload["summary"]["running_sessions"] >= 1
        assert payload["summary"]["focused_sessions"] == 1
        assert any(item["capability_id"] == "session.unfocus" for item in payload["capabilities"])
        focused = next(item for item in payload["sessions"] if item["session_id"] == session_id)
        assert focused["lane"] in {"queue", "running"}

        selector_resp = await control_plane_client.get("/api/control/resources/project-selector")
        assert selector_resp.status_code == 200
        selector_payload = selector_resp.json()
        assert selector_payload["current_project_id"] == focused["project_id"]
        assert selector_payload["current_workspace_id"] == focused["workspace_id"]

        unfocus_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "session.unfocus",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {},
            },
        )
        assert unfocus_resp.status_code == 200
        unfocus_result = unfocus_resp.json()["result"]
        assert unfocus_result["code"] == "SESSION_UNFOCUSED"
        assert unfocus_result["data"]["previous_session_id"] == session_id

        after_resp = await control_plane_client.get("/api/control/resources/sessions")
        assert after_resp.status_code == 200
        after_payload = after_resp.json()
        assert after_payload["focused_session_id"] == ""
        assert after_payload["summary"]["focused_sessions"] == 0

    async def test_session_reset_clears_continuity_and_closes_agent_sessions(
        self,
        control_plane_app,
        control_plane_client: AsyncClient,
    ) -> None:
        store_group = control_plane_app.state.store_group
        project = await store_group.project_store.get_default_project()
        assert project is not None
        workspace = await store_group.project_store.get_primary_workspace(project.project_id)
        assert workspace is not None
        task_id = await _create_task(
            control_plane_app,
            text="legacy continuity reset",
            thread_id="thread-reset-legacy",
            scope_id="scope-control",
        )
        await store_group.agent_context_store.save_agent_runtime(
            AgentRuntime(
                agent_runtime_id="runtime-reset-legacy",
                project_id=project.project_id,
                workspace_id=workspace.workspace_id,
                agent_profile_id="agent-profile-default",
                role=AgentRuntimeRole.BUTLER,
                name="Reset Legacy Runtime",
            )
        )
        await store_group.agent_context_store.save_agent_session(
            AgentSession(
                agent_session_id="agent-session-reset-legacy",
                agent_runtime_id="runtime-reset-legacy",
                kind=AgentSessionKind.BUTLER_MAIN,
                project_id=project.project_id,
                workspace_id=workspace.workspace_id,
                thread_id="thread-reset-legacy",
                legacy_session_id="thread-reset-legacy",
                recent_transcript=[
                    {
                        "role": "user",
                        "content": "旧上下文还挂在 legacy session id 上。",
                        "task_id": task_id,
                    },
                    {
                        "role": "assistant",
                        "content": "需要把 continuity 清掉。",
                        "task_id": task_id,
                    },
                ],
                rolling_summary="需要清空旧 continuity。",
                metadata={
                    "recent_transcript": [
                        {
                            "role": "user",
                            "content": "旧上下文还挂在 legacy session id 上。",
                            "task_id": task_id,
                        },
                        {
                            "role": "assistant",
                            "content": "需要把 continuity 清掉。",
                            "task_id": task_id,
                        },
                    ],
                    "latest_model_reply_summary": "需要把 continuity 清掉。",
                },
            )
        )
        await store_group.agent_context_store.save_agent_session_turn(
            AgentSessionTurn(
                agent_session_turn_id="agent-session-turn-reset-legacy",
                agent_session_id="agent-session-reset-legacy",
                task_id=task_id,
                turn_seq=1,
                kind=AgentSessionTurnKind.TOOL_RESULT,
                role="tool",
                tool_name="web.search",
                summary="旧 continuity 里还包含一条 tool result。",
            )
        )
        await store_group.agent_context_store.save_session_context(
            SessionContextState(
                session_id="thread-reset-legacy",
                agent_runtime_id="runtime-reset-legacy",
                agent_session_id="agent-session-reset-legacy",
                thread_id="thread-reset-legacy",
                project_id=project.project_id,
                workspace_id=workspace.workspace_id,
                task_ids=[task_id],
                recent_turn_refs=[task_id],
                recent_artifact_refs=["artifact-reset-legacy"],
                rolling_summary="旧 continuity 还没有被清空。",
                summary_artifact_id="artifact-reset-legacy-summary",
            )
        )
        await store_group.conn.commit()

        reset_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "session.reset",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "session_id": "thread-reset-legacy",
                },
            },
        )

        assert reset_resp.status_code == 200
        reset_result = reset_resp.json()["result"]
        assert reset_result["code"] == "SESSION_RESET"
        assert reset_result["data"]["thread_id"] == "thread-reset-legacy"
        assert reset_result["data"]["reset_session_context"] is True
        assert reset_result["data"]["reset_agent_session_count"] >= 1
        assert reset_result["data"]["new_conversation_token"]
        assert reset_result["data"]["project_id"] == project.project_id
        assert reset_result["data"]["workspace_id"] == workspace.workspace_id

        session_state = await store_group.agent_context_store.get_session_context(
            "thread-reset-legacy"
        )
        assert session_state is not None
        assert session_state.rolling_summary == ""
        assert session_state.recent_turn_refs == []
        assert session_state.recent_artifact_refs == []
        assert session_state.summary_artifact_id == ""

        agent_session = await store_group.agent_context_store.get_agent_session(
            "agent-session-reset-legacy"
        )
        assert agent_session is not None
        assert agent_session.status.value == "closed"
        assert agent_session.recent_transcript == []
        assert agent_session.rolling_summary == ""
        assert agent_session.closed_at is not None
        session_turns = await store_group.agent_context_store.list_agent_session_turns(
            agent_session_id="agent-session-reset-legacy",
            limit=20,
        )
        assert session_turns == []

        sessions_resp = await control_plane_client.get("/api/control/resources/sessions")
        assert sessions_resp.status_code == 200
        payload = sessions_resp.json()
        assert payload["focused_session_id"] == ""
        assert payload["focused_thread_id"] == ""
        assert payload["new_conversation_token"] == reset_result["data"]["new_conversation_token"]
        assert payload["new_conversation_project_id"] == project.project_id
        assert payload["new_conversation_workspace_id"] == workspace.workspace_id

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
        # 系统内置作业（如 system:memory-consolidate）可能存在，
        # 此处仅验证用户创建的 broken-job 未被保存
        user_jobs = [
            j
            for j in automation_resp.json()["jobs"]
            if not j["job"]["job_id"].startswith("system:")
        ]
        assert user_jobs == []

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
            "tool_profile": "standard",
            "default_tool_groups": ["project", "artifact"],
            "runtime_kinds": ["worker", "acp_runtime"],
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

        worker_profiles_resp = await control_plane_client.get(
            "/api/control/resources/worker-profiles"
        )
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
        mirrored = await control_plane_app.state.store_group.agent_context_store.get_agent_profile(
            profile_id
        )
        assert mirrored is not None
        assert mirrored.metadata["worker_profile_id"] == profile_id

        revisions_resp = await control_plane_client.get(
            f"/api/control/resources/worker-profile-revisions/{profile_id}"
        )
        assert revisions_resp.status_code == 200
        revisions_payload = revisions_resp.json()
        assert revisions_payload["summary"]["revision_count"] == 1
        assert revisions_payload["revisions"][0]["change_summary"] == "首次发布 NAS Root Agent"
        assert revisions_payload["revisions"][0]["snapshot_payload"]["name"] == "NAS Root Agent"

        bind_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "worker_profile.bind_default",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "profile_id": profile_id,
                },
            },
        )
        assert bind_resp.status_code == 200
        project = await control_plane_app.state.store_group.project_store.get_default_project()
        assert project is not None
        assert project.default_agent_profile_id == profile_id
        worker_profiles_resp = await control_plane_client.get(
            "/api/control/resources/worker-profiles"
        )
        assert worker_profiles_resp.status_code == 200
        summary = worker_profiles_resp.json()["summary"]
        assert summary["default_profile_id"] == profile_id

    async def test_worker_profile_review_reports_unknown_model_alias(
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
            "name": "错误 alias Root Agent",
            "summary": "故意用不存在的 alias。",
            "model_alias": "reasoning",
            "tool_profile": "standard",
            "default_tool_groups": ["project"],
            "runtime_kinds": ["worker"],
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
        assert review_payload["can_save"] is False
        assert review_payload["ready"] is False
        assert any("model_alias 必须引用已存在的模型别名" in item for item in review_payload["save_errors"])

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
            model_alias="main",
            tool_profile="minimal",
            default_tool_groups=["project", "network"],
            selected_tools=["web.search"],
            runtime_kinds=["worker", "subagent"],
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
        assert user_event.payload["control_metadata"]["requested_worker_profile_id"] == (
            profile.profile_id
        )
        assert user_event.payload["control_metadata"]["effective_worker_snapshot_id"] == (
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
                selected_worker_type="research",
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
        primary_workspace = await store_group.project_store.get_primary_workspace(
            project.project_id
        )
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
            model_alias="main",
            tool_profile="standard",
            default_tool_groups=["runtime", "project"],
            selected_tools=["runtime.inspect"],
            runtime_kinds=["worker", "acp_runtime"],
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
                selected_worker_type="ops",
                project_id=project.project_id,
                workspace_id=primary_workspace.workspace_id,
                requested_worker_profile_id=profile.profile_id,
                requested_worker_profile_version=1,
                effective_worker_snapshot_id="worker-snapshot:worker-profile-root-agent-project:1",
                selected_tools=["runtime.inspect"],
                created_at=running_ts,
                updated_at=running_ts,
                metadata={
                    "tool_selection": DynamicToolSelection(
                        selection_id="selection-running",
                        query=ToolIndexQuery(query="巡检", limit=6),
                        selected_tools=["runtime.inspect"],
                        resolution_mode="profile_first_core",
                        effective_tool_universe=EffectiveToolUniverse(
                            profile_id=profile.profile_id,
                            profile_revision=1,
                            worker_type="ops",
                            tool_profile="standard",
                            resolution_mode="profile_first_core",
                            selected_tools=["runtime.inspect"],
                            discovery_entrypoints=["workers.review"],
                        ),
                        mounted_tools=[
                            ToolAvailabilityExplanation(
                                tool_name="runtime.inspect",
                                status="mounted",
                                source_kind="profile_selected",
                                tool_group="runtime",
                                tool_profile="minimal",
                            )
                        ],
                    ).model_dump(mode="json")
                },
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
                selected_worker_type="ops",
                project_id=project.project_id,
                workspace_id=secondary_workspace.workspace_id,
                requested_worker_profile_id=profile.profile_id,
                requested_worker_profile_version=1,
                effective_worker_snapshot_id="worker-snapshot:worker-profile-root-agent-project:1",
                selected_tools=["runtime.inspect"],
                created_at=failed_ts,
                updated_at=failed_ts,
                completed_at=failed_ts,
                metadata={
                    "tool_selection": DynamicToolSelection(
                        selection_id="selection-failed",
                        query=ToolIndexQuery(query="巡检", limit=6),
                        selected_tools=["runtime.inspect", "workers.review"],
                        warnings=["profile_first_tool_unavailable"],
                        resolution_mode="profile_first_core",
                        effective_tool_universe=EffectiveToolUniverse(
                            profile_id=profile.profile_id,
                            profile_revision=1,
                            worker_type="ops",
                            tool_profile="standard",
                            resolution_mode="profile_first_core",
                            selected_tools=["runtime.inspect", "workers.review"],
                            discovery_entrypoints=["workers.review", "mcp.tools.list"],
                            warnings=["profile_first_tool_unavailable"],
                        ),
                        mounted_tools=[
                            ToolAvailabilityExplanation(
                                tool_name="runtime.inspect",
                                status="mounted",
                                source_kind="profile_selected",
                                tool_group="runtime",
                                tool_profile="minimal",
                            )
                        ],
                        blocked_tools=[
                            ToolAvailabilityExplanation(
                                tool_name="subagents.spawn",
                                status="unavailable",
                                source_kind="profile_first_core",
                                tool_group="delegation",
                                tool_profile="standard",
                                reason_code="task_runner_unbound",
                            )
                        ],
                    ).model_dump(mode="json")
                },
            )
        )
        await store_group.conn.commit()

        worker_profiles_resp = await control_plane_client.get(
            "/api/control/resources/worker-profiles"
        )
        assert worker_profiles_resp.status_code == 200
        payload = worker_profiles_resp.json()
        target = next(
            item for item in payload["profiles"] if item["profile_id"] == profile.profile_id
        )

        assert target["dynamic_context"]["active_project_id"] == project.project_id
        assert target["dynamic_context"]["active_workspace_id"] == secondary_workspace.workspace_id
        assert target["dynamic_context"]["active_work_count"] == 1
        assert target["dynamic_context"]["running_work_count"] == 1
        assert target["dynamic_context"]["attention_work_count"] == 1
        assert target["dynamic_context"]["latest_work_id"] == "work-root-failed"
        assert target["dynamic_context"]["latest_work_status"] == "failed"
        assert target["dynamic_context"]["current_tool_resolution_mode"] == "profile_first_core"
        assert target["dynamic_context"]["current_blocked_tools"][0]["tool_name"] == (
            "subagents.spawn"
        )
        assert target["dynamic_context"]["current_discovery_entrypoints"] == [
            "workers.review",
            "mcp.tools.list",
        ]

    async def test_worker_profiles_document_includes_owner_self_worker_runs(
        self,
        control_plane_app,
        control_plane_client: AsyncClient,
    ) -> None:
        store_group = control_plane_app.state.store_group
        project = await store_group.project_store.get_default_project()
        assert project is not None
        workspace = await store_group.project_store.get_primary_workspace(project.project_id)
        assert workspace is not None

        profile = WorkerProfile(
            profile_id="worker-profile-owner-self-dashboard",
            scope=AgentProfileScope.PROJECT,
            project_id=project.project_id,
            name="Owner Self Worker",
            summary="owner-self worker should appear in behavior center",
            model_alias="cheap",
            tool_profile="standard",
            default_tool_groups=["filesystem", "project"],
            selected_tools=["filesystem.read_text"],
            runtime_kinds=["worker", "subagent"],
            status=WorkerProfileStatus.ACTIVE,
            origin_kind=WorkerProfileOriginKind.CUSTOM,
            draft_revision=1,
            active_revision=1,
        )
        await store_group.agent_context_store.save_worker_profile(profile)

        task_id = await _create_task(
            control_plane_app,
            text="owner-self worker dashboard binding",
            thread_id="thread-owner-self-dashboard",
            scope_id=project.project_id,
        )
        await store_group.work_store.save_work(
            Work(
                work_id="work-owner-self-dashboard",
                task_id=task_id,
                title="Owner-self worker run",
                kind=WorkKind.DELEGATION,
                status=WorkStatus.RUNNING,
                target_kind=DelegationTargetKind.WORKER,
                selected_worker_type="general",
                project_id=project.project_id,
                workspace_id=workspace.workspace_id,
                session_owner_profile_id=profile.profile_id,
                delegation_target_profile_id="",
                turn_executor_kind="worker",
                agent_profile_id=profile.profile_id,
                requested_worker_profile_id="",
                selected_tools=["filesystem.read_text"],
            )
        )
        await store_group.conn.commit()

        worker_profiles_resp = await control_plane_client.get("/api/control/resources/worker-profiles")
        assert worker_profiles_resp.status_code == 200
        payload = worker_profiles_resp.json()
        target = next(
            item for item in payload["profiles"] if item["profile_id"] == profile.profile_id
        )
        assert target["dynamic_context"]["active_work_count"] == 1
        assert target["dynamic_context"]["running_work_count"] == 1
        assert target["dynamic_context"]["latest_work_id"] == "work-owner-self-dashboard"
        assert target["dynamic_context"]["latest_work_status"] == "running"
