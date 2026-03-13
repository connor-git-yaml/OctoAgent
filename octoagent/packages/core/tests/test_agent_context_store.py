"""Feature 033: AgentContextStore 持久化测试。"""

from __future__ import annotations

from pathlib import Path

from octoagent.core.models import (
    AgentProfile,
    AgentProfileScope,
    AgentRuntime,
    AgentRuntimeRole,
    AgentSession,
    AgentSessionKind,
    BootstrapSession,
    BootstrapSessionStatus,
    ContextFrame,
    MemoryNamespace,
    MemoryNamespaceKind,
    OwnerOverlayScope,
    OwnerProfile,
    OwnerProfileOverlay,
    RecallFrame,
    SessionContextState,
    WorkerProfile,
    WorkerProfileOriginKind,
    WorkerProfileRevision,
    WorkerProfileStatus,
)
from octoagent.core.store import create_store_group


async def test_agent_context_store_roundtrip(tmp_path: Path) -> None:
    store_group = await create_store_group(
        str(tmp_path / "agent-context.db"),
        str(tmp_path / "artifacts"),
    )
    agent_profile = AgentProfile(
        profile_id="agent-profile-alpha",
        scope=AgentProfileScope.PROJECT,
        project_id="project-alpha",
        name="Alpha Agent",
        persona_summary="负责 Alpha 项目的连续协作。",
        instruction_overlays=["保持 project 上下文连续性。"],
    )
    owner_profile = OwnerProfile(
        owner_profile_id="owner-profile-default",
        display_name="Connor",
        working_style="偏好直接结论。",
    )
    owner_overlay = OwnerProfileOverlay(
        owner_overlay_id="owner-overlay-alpha",
        owner_profile_id=owner_profile.owner_profile_id,
        scope=OwnerOverlayScope.PROJECT,
        project_id="project-alpha",
        assistant_identity_overrides={"assistant_name": "Alpha Agent"},
        interaction_preferences_override=["回答前先对齐 project 事实。"],
    )
    bootstrap = BootstrapSession(
        bootstrap_id="bootstrap-alpha",
        project_id="project-alpha",
        owner_profile_id=owner_profile.owner_profile_id,
        owner_overlay_id=owner_overlay.owner_overlay_id,
        agent_profile_id=agent_profile.profile_id,
        status=BootstrapSessionStatus.COMPLETED,
        current_step="done",
        answers={"assistant_identity": "Alpha Agent", "tone": "direct"},
    )
    session_state = SessionContextState(
        session_id="thread-alpha",
        thread_id="thread-alpha",
        project_id="project-alpha",
        workspace_id="workspace-alpha",
        task_ids=["task-1"],
        recent_turn_refs=["task-1"],
        recent_artifact_refs=["artifact-1"],
        rolling_summary="已经确认 Alpha 项目的主要约束。",
        last_context_frame_id="context-frame-alpha",
    )
    context_frame = ContextFrame(
        context_frame_id="context-frame-alpha",
        task_id="task-1",
        session_id="thread-alpha",
        project_id="project-alpha",
        workspace_id="workspace-alpha",
        agent_profile_id=agent_profile.profile_id,
        owner_profile_id=owner_profile.owner_profile_id,
        owner_overlay_id=owner_overlay.owner_overlay_id,
        bootstrap_session_id=bootstrap.bootstrap_id,
        system_blocks=[{"role": "system", "content": "Alpha Agent system context"}],
        recent_summary=session_state.rolling_summary,
        memory_hits=[{"record_id": "memory-1", "summary": "Alpha memory"}],
        source_refs=[{"ref_type": "agent_profile", "ref_id": agent_profile.profile_id}],
    )

    await store_group.agent_context_store.save_agent_profile(agent_profile)
    await store_group.agent_context_store.save_owner_profile(owner_profile)
    await store_group.agent_context_store.save_owner_overlay(owner_overlay)
    await store_group.agent_context_store.save_bootstrap_session(bootstrap)
    await store_group.agent_context_store.save_session_context(session_state)
    await store_group.agent_context_store.save_context_frame(context_frame)
    await store_group.conn.commit()

    stored_profile = await store_group.agent_context_store.get_agent_profile(
        agent_profile.profile_id
    )
    stored_overlay = await store_group.agent_context_store.get_owner_overlay_for_scope(
        project_id="project-alpha"
    )
    stored_bootstrap = await store_group.agent_context_store.get_latest_bootstrap_session(
        project_id="project-alpha"
    )
    stored_session = await store_group.agent_context_store.get_session_context("thread-alpha")
    stored_frame = await store_group.agent_context_store.get_context_frame(
        "context-frame-alpha"
    )

    assert stored_profile is not None
    assert stored_profile.persona_summary == "负责 Alpha 项目的连续协作。"
    assert stored_overlay is not None
    assert stored_overlay.assistant_identity_overrides["assistant_name"] == "Alpha Agent"
    assert stored_bootstrap is not None
    assert stored_bootstrap.answers["tone"] == "direct"
    assert stored_session is not None
    assert stored_session.rolling_summary == "已经确认 Alpha 项目的主要约束。"
    assert stored_frame is not None
    assert stored_frame.memory_hits[0]["record_id"] == "memory-1"

    frames = await store_group.agent_context_store.list_context_frames(
        project_id="project-alpha",
        limit=5,
    )
    assert [item.context_frame_id for item in frames] == ["context-frame-alpha"]

    await store_group.conn.close()


async def test_agent_runtime_namespace_and_recall_roundtrip(tmp_path: Path) -> None:
    store_group = await create_store_group(
        str(tmp_path / "agent-runtime.db"),
        str(tmp_path / "artifacts"),
    )
    runtime = AgentRuntime(
        agent_runtime_id="runtime-butler-alpha",
        project_id="project-alpha",
        agent_profile_id="agent-profile-alpha",
        role=AgentRuntimeRole.BUTLER,
        name="Alpha Butler",
        persona_summary="负责用户主会话与 worker 协调。",
    )
    session = AgentSession(
        agent_session_id="agent-session-butler-alpha",
        agent_runtime_id=runtime.agent_runtime_id,
        kind=AgentSessionKind.BUTLER_MAIN,
        project_id="project-alpha",
        surface="chat",
        thread_id="thread-alpha",
        legacy_session_id="project:alpha:thread-alpha",
        last_context_frame_id="context-frame-alpha",
        last_recall_frame_id="recall-frame-alpha",
        metadata={"source": "wave1-test"},
    )
    namespace = MemoryNamespace(
        namespace_id="namespace-butler-alpha",
        project_id="project-alpha",
        agent_runtime_id=runtime.agent_runtime_id,
        kind=MemoryNamespaceKind.BUTLER_PRIVATE,
        name="Butler Private",
        description="Butler 私有长期记忆。",
        memory_scope_ids=["project-alpha:butler"],
    )
    recall = RecallFrame(
        recall_frame_id="recall-frame-alpha",
        agent_runtime_id=runtime.agent_runtime_id,
        agent_session_id=session.agent_session_id,
        context_frame_id="context-frame-alpha",
        task_id="task-alpha",
        project_id="project-alpha",
        query="深圳今天天气怎么样",
        recent_summary="用户最近在问实时天气与出行建议。",
        memory_namespace_ids=[namespace.namespace_id],
        memory_hits=[{"record_id": "memory-1", "summary": "用户当前常驻深圳"}],
        source_refs=[{"ref_type": "memory_namespace", "ref_id": namespace.namespace_id}],
        budget={"max_hits": 5},
        metadata={"phase": "wave1"},
    )

    await store_group.agent_context_store.save_agent_runtime(runtime)
    await store_group.agent_context_store.save_agent_session(session)
    await store_group.agent_context_store.save_memory_namespace(namespace)
    await store_group.agent_context_store.save_recall_frame(recall)
    await store_group.conn.commit()

    stored_runtime = await store_group.agent_context_store.get_agent_runtime(
        runtime.agent_runtime_id
    )
    stored_session = await store_group.agent_context_store.get_agent_session(
        session.agent_session_id
    )
    stored_namespace = await store_group.agent_context_store.get_memory_namespace(
        namespace.namespace_id
    )
    stored_recall = await store_group.agent_context_store.get_recall_frame(
        recall.recall_frame_id
    )

    assert stored_runtime is not None
    assert stored_runtime.role == AgentRuntimeRole.BUTLER
    assert stored_runtime.name == "Alpha Butler"

    assert stored_session is not None
    assert stored_session.legacy_session_id == "project:alpha:thread-alpha"
    assert stored_session.last_recall_frame_id == "recall-frame-alpha"

    assert stored_namespace is not None
    assert stored_namespace.kind == MemoryNamespaceKind.BUTLER_PRIVATE
    assert stored_namespace.memory_scope_ids == ["project-alpha:butler"]

    assert stored_recall is not None
    assert stored_recall.memory_namespace_ids == [namespace.namespace_id]
    assert stored_recall.memory_hits[0]["record_id"] == "memory-1"

    runtimes = await store_group.agent_context_store.list_agent_runtimes(
        project_id="project-alpha",
        role=AgentRuntimeRole.BUTLER,
    )
    sessions = await store_group.agent_context_store.list_agent_sessions(
        legacy_session_id="project:alpha:thread-alpha",
    )
    namespaces = await store_group.agent_context_store.list_memory_namespaces(
        agent_runtime_id=runtime.agent_runtime_id,
        kind=MemoryNamespaceKind.BUTLER_PRIVATE,
    )
    recalls = await store_group.agent_context_store.list_recall_frames(
        agent_session_id=session.agent_session_id,
    )

    assert [item.agent_runtime_id for item in runtimes] == [runtime.agent_runtime_id]
    assert [item.agent_session_id for item in sessions] == [session.agent_session_id]
    assert [item.namespace_id for item in namespaces] == [namespace.namespace_id]
    assert [item.recall_frame_id for item in recalls] == [recall.recall_frame_id]

    await store_group.conn.close()


async def test_worker_profile_and_revision_roundtrip(tmp_path: Path) -> None:
    store_group = await create_store_group(
        str(tmp_path / "worker-profile.db"),
        str(tmp_path / "artifacts"),
    )
    profile = WorkerProfile(
        profile_id="worker-profile-alpha",
        scope=AgentProfileScope.PROJECT,
        project_id="project-alpha",
        name="NAS Root Agent",
        summary="负责 NAS 巡检与文件整理。",
        base_archetype="ops",
        model_alias="main",
        tool_profile="standard",
        default_tool_groups=["project", "filesystem"],
        selected_tools=["filesystem.read"],
        runtime_kinds=["worker", "acp_runtime"],
        policy_refs=["default"],
        tags=["nas", "storage"],
        status=WorkerProfileStatus.ACTIVE,
        origin_kind=WorkerProfileOriginKind.CUSTOM,
        draft_revision=1,
        active_revision=1,
    )
    revision = WorkerProfileRevision(
        revision_id="worker-snapshot:worker-profile-alpha:1",
        profile_id=profile.profile_id,
        revision=1,
        change_summary="首次发布",
        snapshot_payload={
            "profile_id": profile.profile_id,
            "name": profile.name,
            "selected_tools": profile.selected_tools,
        },
        created_by="tests",
    )

    await store_group.agent_context_store.save_worker_profile(profile)
    await store_group.agent_context_store.save_worker_profile_revision(revision)
    await store_group.conn.commit()

    stored_profile = await store_group.agent_context_store.get_worker_profile(profile.profile_id)
    stored_revisions = await store_group.agent_context_store.list_worker_profile_revisions(
        profile.profile_id
    )

    assert stored_profile is not None
    assert stored_profile.name == "NAS Root Agent"
    assert stored_profile.selected_tools == ["filesystem.read"]
    assert stored_profile.origin_kind == WorkerProfileOriginKind.CUSTOM
    assert len(stored_revisions) == 1
    assert stored_revisions[0].change_summary == "首次发布"
    assert stored_revisions[0].snapshot_payload["selected_tools"] == ["filesystem.read"]

    await store_group.conn.close()
