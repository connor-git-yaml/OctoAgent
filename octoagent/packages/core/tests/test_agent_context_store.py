"""Feature 033: AgentContextStore 持久化测试。"""

from __future__ import annotations

from pathlib import Path

from octoagent.core.models import (
    AgentProfile,
    AgentProfileScope,
    AgentRuntime,
    AgentRuntimeRole,
    AgentRuntimeStatus,
    AgentSession,
    AgentSessionKind,
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
    session_state = SessionContextState(
        session_id="thread-alpha",
        thread_id="thread-alpha",
        project_id="project-alpha",

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

        agent_profile_id=agent_profile.profile_id,
        owner_profile_id=owner_profile.owner_profile_id,
        owner_overlay_id=owner_overlay.owner_overlay_id,
        bootstrap_session_id="",
        system_blocks=[{"role": "system", "content": "Alpha Agent system context"}],
        recent_summary=session_state.rolling_summary,
        memory_hits=[{"record_id": "memory-1", "summary": "Alpha memory"}],
        source_refs=[{"ref_type": "agent_profile", "ref_id": agent_profile.profile_id}],
    )

    await store_group.agent_context_store.save_agent_profile(agent_profile)
    await store_group.agent_context_store.save_owner_profile(owner_profile)
    await store_group.agent_context_store.save_owner_overlay(owner_overlay)
    await store_group.agent_context_store.save_session_context(session_state)
    await store_group.agent_context_store.save_context_frame(context_frame)
    await store_group.conn.commit()

    stored_profile = await store_group.agent_context_store.get_agent_profile(
        agent_profile.profile_id
    )
    stored_overlay = await store_group.agent_context_store.get_owner_overlay_for_scope(
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
        agent_runtime_id="runtime-main-alpha",
        project_id="project-alpha",
        agent_profile_id="agent-profile-alpha",
        role=AgentRuntimeRole.MAIN,
        name="Alpha Agent",
        persona_summary="负责用户主会话与 worker 协调。",
    )
    session = AgentSession(
        agent_session_id="agent-session-main-alpha",
        agent_runtime_id=runtime.agent_runtime_id,
        kind=AgentSessionKind.MAIN_BOOTSTRAP,
        project_id="project-alpha",
        surface="chat",
        thread_id="thread-alpha",
        legacy_session_id="project:alpha:thread-alpha",
        last_context_frame_id="context-frame-alpha",
        last_recall_frame_id="recall-frame-alpha",
        metadata={"source": "wave1-test"},
    )
    namespace = MemoryNamespace(
        namespace_id="namespace-main-alpha",
        project_id="project-alpha",
        agent_runtime_id=runtime.agent_runtime_id,
        kind=MemoryNamespaceKind.AGENT_PRIVATE,
        name="Agent Private",
        description="Agent 私有长期记忆。",
        memory_scope_ids=["project-alpha:main"],
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
    assert stored_runtime.role == AgentRuntimeRole.MAIN
    assert stored_runtime.name == "Alpha Agent"

    assert stored_session is not None
    assert stored_session.legacy_session_id == "project:alpha:thread-alpha"
    assert stored_session.last_recall_frame_id == "recall-frame-alpha"

    assert stored_namespace is not None
    assert stored_namespace.kind == MemoryNamespaceKind.AGENT_PRIVATE
    assert stored_namespace.memory_scope_ids == ["project-alpha:main"]

    assert stored_recall is not None
    assert stored_recall.memory_namespace_ids == [namespace.namespace_id]
    assert stored_recall.memory_hits[0]["record_id"] == "memory-1"

    runtimes = await store_group.agent_context_store.list_agent_runtimes(
        project_id="project-alpha",
        role=AgentRuntimeRole.MAIN,
    )
    sessions = await store_group.agent_context_store.list_agent_sessions(
        legacy_session_id="project:alpha:thread-alpha",
    )
    namespaces = await store_group.agent_context_store.list_memory_namespaces(
        agent_runtime_id=runtime.agent_runtime_id,
        kind=MemoryNamespaceKind.AGENT_PRIVATE,
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
        model_alias="main",
        tool_profile="standard",
        default_tool_groups=["project", "filesystem"],
        selected_tools=["filesystem.read"],
        runtime_kinds=["worker", "acp_runtime"],
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


async def test_find_active_runtime_selects_active_ulid_and_skips_closed(
    tmp_path: Path,
) -> None:
    """find_active_runtime 按 (project, role, worker_profile) 命中 active ULID runtime。

    这是消除 composite-key 双写根因的关键 lookup：Path B 在 request.agent_runtime_id
    为空时应优先走这个反查复用 Path A 预建的 ULID，而不是再建一条 composite row。
    """
    store_group = await create_store_group(
        str(tmp_path / "find-active-runtime.db"),
        str(tmp_path / "artifacts"),
    )
    active_runtime = AgentRuntime(
        agent_runtime_id="runtime-01TESTACTIVE",
        project_id="project-alpha",
        worker_profile_id="worker-profile-a",
        role=AgentRuntimeRole.WORKER,
        name="Active Worker",
        status=AgentRuntimeStatus.ACTIVE,
    )
    closed_runtime = AgentRuntime(
        agent_runtime_id="runtime-01TESTCLOSED",
        project_id="project-alpha",
        worker_profile_id="worker-profile-a",
        role=AgentRuntimeRole.WORKER,
        name="Closed Worker",
        status=AgentRuntimeStatus.ARCHIVED,
    )
    other_project_runtime = AgentRuntime(
        agent_runtime_id="runtime-01TESTOTHER",
        project_id="project-beta",
        worker_profile_id="worker-profile-a",
        role=AgentRuntimeRole.WORKER,
        name="Other Project",
        status=AgentRuntimeStatus.ACTIVE,
    )
    await store_group.agent_context_store.save_agent_runtime(active_runtime)
    await store_group.agent_context_store.save_agent_runtime(closed_runtime)
    await store_group.agent_context_store.save_agent_runtime(other_project_runtime)
    await store_group.conn.commit()

    hit = await store_group.agent_context_store.find_active_runtime(
        project_id="project-alpha",
        role=AgentRuntimeRole.WORKER,
        worker_profile_id="worker-profile-a",
    )
    assert hit is not None
    assert hit.agent_runtime_id == "runtime-01TESTACTIVE"

    miss = await store_group.agent_context_store.find_active_runtime(
        project_id="project-alpha",
        role=AgentRuntimeRole.WORKER,
        worker_profile_id="worker-profile-unknown",
    )
    assert miss is None

    await store_group.conn.close()


# ────────────── Feature 082 P0：OwnerProfile 默认值 + last_synced_from_profile_at ──────────────


def test_owner_profile_default_preferred_address_is_empty() -> None:
    """Feature 082 P0：preferred_address 默认值从 "你" 改为 ""。

    避免 Profile 输出永远显示 "preferred_address: 你" 让用户误以为是脏数据；
    Agent system prompt 层 fallback 到适当称呼（如 "Owner"）。
    """
    profile = OwnerProfile(owner_profile_id="test-owner")
    assert profile.preferred_address == "", (
        f"P0 期望默认 preferred_address 为空串，实际 {profile.preferred_address!r}"
    )
    assert profile.last_synced_from_profile_at is None


def test_owner_profile_explicit_preferred_address_preserved() -> None:
    """Feature 082 P0：用户显式赋值不受默认值变更影响。"""
    profile = OwnerProfile(owner_profile_id="test-owner", preferred_address="Connor")
    assert profile.preferred_address == "Connor"


async def test_owner_profile_last_synced_field_persistence(tmp_path: Path) -> None:
    """Feature 082 P0：last_synced_from_profile_at 字段能正确写入 + 读出。"""
    from datetime import UTC, datetime

    store_group = await create_store_group(
        str(tmp_path / "p0-owner.db"),
        str(tmp_path / "p0-owner-artifacts"),
    )
    try:
        sync_time = datetime.now(tz=UTC).replace(microsecond=0)
        profile = OwnerProfile(
            owner_profile_id="owner-test",
            preferred_address="Connor",
            last_synced_from_profile_at=sync_time,
        )
        await store_group.agent_context_store.save_owner_profile(profile)
        await store_group.conn.commit()

        loaded = await store_group.agent_context_store.get_owner_profile("owner-test")
        assert loaded is not None
        assert loaded.preferred_address == "Connor"
        assert loaded.last_synced_from_profile_at is not None
        # 时区+秒一致即可（isoformat 往返）
        assert loaded.last_synced_from_profile_at.replace(microsecond=0) == sync_time

        # 用 None 重新写入
        profile.last_synced_from_profile_at = None
        await store_group.agent_context_store.save_owner_profile(profile)
        await store_group.conn.commit()
        loaded2 = await store_group.agent_context_store.get_owner_profile("owner-test")
        assert loaded2.last_synced_from_profile_at is None
    finally:
        await store_group.conn.close()


async def test_owner_profile_legacy_table_without_new_column_readable(tmp_path: Path) -> None:
    """Feature 082 P0：老库（无 last_synced_from_profile_at 列）能继续读取。

    模拟升级前已有数据库——此场景下 _migrate_legacy_tables 会 ALTER TABLE 加列；
    本测试通过 store_group 创建后已自动 migrate，验证读取路径用 row.keys() 兜底
    在迁移已发生的情况下也能正常工作（即使列存在但值为 NULL）。
    """
    store_group = await create_store_group(
        str(tmp_path / "p0-legacy.db"),
        str(tmp_path / "p0-legacy-artifacts"),
    )
    try:
        # 直接用 SQL 插入一行（模拟未经 save_owner_profile 写入的老数据，列为 NULL）
        await store_group.conn.execute(
            """
            INSERT INTO owner_profiles (
                owner_profile_id, display_name, preferred_address,
                timezone, locale, working_style,
                interaction_preferences, boundary_notes, main_session_only_fields,
                metadata, version, created_at, updated_at
            ) VALUES (
                'legacy-owner', 'Legacy', '你',
                'UTC', 'zh-CN', '',
                '[]', '[]', '[]', '{}', 1,
                '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00'
            )
            """
        )
        await store_group.conn.commit()

        loaded = await store_group.agent_context_store.get_owner_profile("legacy-owner")
        assert loaded is not None
        # 老数据 "你" 保留（不在启动时静默清洗——P4 migrate-082 显式触发）
        assert loaded.preferred_address == "你"
        # 新列在老行上为 None
        assert loaded.last_synced_from_profile_at is None
    finally:
        await store_group.conn.close()
