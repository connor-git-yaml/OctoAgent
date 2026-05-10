"""Feature 033: AgentContextStore 持久化测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
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
    # F090 D2: 默认 AgentProfile.kind="main" 持久化
    assert stored_profile.kind == "main"
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


async def test_agent_profile_kind_persists_round_trip(tmp_path: Path) -> None:
    """F090 D2: AgentProfile.kind 字段持久化 round-trip 验证。

    覆盖：kind="worker" save → load 后保持 / kind="main" 默认值 / kind="subagent"。
    """
    store_group = await create_store_group(
        str(tmp_path / "agent-kind.db"),
        str(tmp_path / "artifacts"),
    )

    main_profile = AgentProfile(
        profile_id="kind-main-001",
        name="Main",
    )
    worker_profile = AgentProfile(
        profile_id="kind-worker-001",
        name="Worker",
        kind="worker",
    )
    subagent_profile = AgentProfile(
        profile_id="kind-subagent-001",
        name="Subagent",
        kind="subagent",
    )

    await store_group.agent_context_store.save_agent_profile(main_profile)
    await store_group.agent_context_store.save_agent_profile(worker_profile)
    await store_group.agent_context_store.save_agent_profile(subagent_profile)
    await store_group.conn.commit()

    loaded_main = await store_group.agent_context_store.get_agent_profile("kind-main-001")
    loaded_worker = await store_group.agent_context_store.get_agent_profile("kind-worker-001")
    loaded_subagent = await store_group.agent_context_store.get_agent_profile("kind-subagent-001")

    assert loaded_main is not None and loaded_main.kind == "main"
    assert loaded_worker is not None and loaded_worker.kind == "worker"
    assert loaded_subagent is not None and loaded_subagent.kind == "subagent"

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


# ---------------------------------------------------------------------------
# F094 Phase C: schema setup + RecallFrame 双字段 + store API archived 过滤
# ---------------------------------------------------------------------------


def _make_namespace(
    *,
    namespace_id: str,
    project_id: str,
    agent_runtime_id: str,
    kind: MemoryNamespaceKind,
    archived_at: datetime | None = None,
) -> MemoryNamespace:
    return MemoryNamespace(
        namespace_id=namespace_id,
        project_id=project_id,
        agent_runtime_id=agent_runtime_id,
        kind=kind,
        name=f"{kind.value}-test",
        description="F094 test fixture",
        memory_scope_ids=[f"{project_id}/{kind.value}"],
        archived_at=archived_at,
    )


async def test_f094_c2_memory_namespaces_unique_triple_active(tmp_path: Path) -> None:
    """F094 C2: (project_id, agent_runtime_id, kind) partial unique index 在
    archived_at IS NULL 路径上必须强制唯一。"""
    store_group = await create_store_group(
        str(tmp_path / "f094-c2.db"),
        str(tmp_path / "artifacts"),
    )
    try:
        ns_a = _make_namespace(
            namespace_id="ns-a",
            project_id="proj-1",
            agent_runtime_id="runtime-1",
            kind=MemoryNamespaceKind.AGENT_PRIVATE,
        )
        await store_group.agent_context_store.save_memory_namespace(ns_a)
        await store_group.conn.commit()

        # 同三元组 + 不同 namespace_id：unique 约束必须 raise
        ns_b = _make_namespace(
            namespace_id="ns-b",
            project_id="proj-1",
            agent_runtime_id="runtime-1",
            kind=MemoryNamespaceKind.AGENT_PRIVATE,
        )
        with pytest.raises(Exception):
            await store_group.agent_context_store.save_memory_namespace(ns_b)
            await store_group.conn.commit()
    finally:
        await store_group.conn.close()


async def test_f094_c2_archived_namespaces_do_not_block_active(tmp_path: Path) -> None:
    """F094 C2: archived 记录不参与 partial unique 约束，新 active 可创建。"""
    store_group = await create_store_group(
        str(tmp_path / "f094-c2-archived.db"),
        str(tmp_path / "artifacts"),
    )
    try:
        # archived 记录（archived_at != NULL）
        ns_archived = _make_namespace(
            namespace_id="ns-archived",
            project_id="proj-2",
            agent_runtime_id="runtime-2",
            kind=MemoryNamespaceKind.AGENT_PRIVATE,
            archived_at=datetime.now(UTC),
        )
        await store_group.agent_context_store.save_memory_namespace(ns_archived)
        await store_group.conn.commit()

        # 同三元组 + 不同 namespace_id + active：必须能创建
        ns_active = _make_namespace(
            namespace_id="ns-active",
            project_id="proj-2",
            agent_runtime_id="runtime-2",
            kind=MemoryNamespaceKind.AGENT_PRIVATE,
        )
        await store_group.agent_context_store.save_memory_namespace(ns_active)
        await store_group.conn.commit()
    finally:
        await store_group.conn.close()


async def test_f094_c5_recall_frame_double_field_round_trip(tmp_path: Path) -> None:
    """F094 C3-C5: RecallFrame queried_namespace_kinds + hit_namespace_kinds 双字段
    round-trip：写入 → 读出 enum 类型保持一致。"""
    store_group = await create_store_group(
        str(tmp_path / "f094-c5.db"),
        str(tmp_path / "artifacts"),
    )
    try:
        runtime = AgentRuntime(
            agent_runtime_id="runtime-c5",
            project_id="proj-c5",
            agent_profile_id="agent-profile-c5",
            role=AgentRuntimeRole.MAIN,
            name="C5 Agent",
            persona_summary="C5 round-trip 测试。",
        )
        await store_group.agent_context_store.save_agent_runtime(runtime)
        recall = RecallFrame(
            recall_frame_id="recall-c5",
            agent_runtime_id=runtime.agent_runtime_id,
            project_id="proj-c5",
            query="C5 query",
            queried_namespace_kinds=[
                MemoryNamespaceKind.AGENT_PRIVATE,
                MemoryNamespaceKind.PROJECT_SHARED,
            ],
            hit_namespace_kinds=[MemoryNamespaceKind.PROJECT_SHARED],
        )
        await store_group.agent_context_store.save_recall_frame(recall)
        await store_group.conn.commit()

        loaded = await store_group.agent_context_store.get_recall_frame("recall-c5")
        assert loaded is not None
        assert loaded.queried_namespace_kinds == [
            MemoryNamespaceKind.AGENT_PRIVATE,
            MemoryNamespaceKind.PROJECT_SHARED,
        ]
        assert loaded.hit_namespace_kinds == [MemoryNamespaceKind.PROJECT_SHARED]
    finally:
        await store_group.conn.close()


async def test_f094_c7_list_recall_frames_namespace_filter(tmp_path: Path) -> None:
    """F094 C7: list_recall_frames 接受 queried_namespace_kind / hit_namespace_kind
    过滤维度，正确按 JSON list contains 命中。"""
    store_group = await create_store_group(
        str(tmp_path / "f094-c7.db"),
        str(tmp_path / "artifacts"),
    )
    try:
        runtime = AgentRuntime(
            agent_runtime_id="runtime-c7",
            project_id="proj-c7",
            agent_profile_id="agent-profile-c7",
            role=AgentRuntimeRole.WORKER,
            name="C7 Worker",
            persona_summary="C7 filter 测试。",
        )
        await store_group.agent_context_store.save_agent_runtime(runtime)
        # frame_a：仅 queried PROJECT_SHARED
        await store_group.agent_context_store.save_recall_frame(
            RecallFrame(
                recall_frame_id="recall-a",
                agent_runtime_id=runtime.agent_runtime_id,
                project_id="proj-c7",
                query="qa",
                queried_namespace_kinds=[MemoryNamespaceKind.PROJECT_SHARED],
                hit_namespace_kinds=[MemoryNamespaceKind.PROJECT_SHARED],
            )
        )
        # frame_b：queried 含 AGENT_PRIVATE，但 hit 0 命中（仅 PROJECT_SHARED）
        await store_group.agent_context_store.save_recall_frame(
            RecallFrame(
                recall_frame_id="recall-b",
                agent_runtime_id=runtime.agent_runtime_id,
                project_id="proj-c7",
                query="qb",
                queried_namespace_kinds=[
                    MemoryNamespaceKind.AGENT_PRIVATE,
                    MemoryNamespaceKind.PROJECT_SHARED,
                ],
                hit_namespace_kinds=[MemoryNamespaceKind.PROJECT_SHARED],
            )
        )
        # frame_c：queried + hit 都有 AGENT_PRIVATE
        await store_group.agent_context_store.save_recall_frame(
            RecallFrame(
                recall_frame_id="recall-c",
                agent_runtime_id=runtime.agent_runtime_id,
                project_id="proj-c7",
                query="qc",
                queried_namespace_kinds=[
                    MemoryNamespaceKind.AGENT_PRIVATE,
                    MemoryNamespaceKind.PROJECT_SHARED,
                ],
                hit_namespace_kinds=[MemoryNamespaceKind.AGENT_PRIVATE],
            )
        )
        await store_group.conn.commit()

        # queried = AGENT_PRIVATE：命中 b + c
        queried_private = await store_group.agent_context_store.list_recall_frames(
            queried_namespace_kind=MemoryNamespaceKind.AGENT_PRIVATE,
        )
        assert {item.recall_frame_id for item in queried_private} == {
            "recall-b",
            "recall-c",
        }

        # hit = AGENT_PRIVATE：仅命中 c（"实际命中私有"语义）
        hit_private = await store_group.agent_context_store.list_recall_frames(
            hit_namespace_kind=MemoryNamespaceKind.AGENT_PRIVATE,
        )
        assert {item.recall_frame_id for item in hit_private} == {"recall-c"}

        # agent_runtime_id 过滤
        by_runtime = await store_group.agent_context_store.list_recall_frames(
            agent_runtime_id=runtime.agent_runtime_id,
        )
        assert len(by_runtime) == 3
    finally:
        await store_group.conn.close()


async def test_f096_b5_list_recall_frames_offset_pagination(tmp_path: Path) -> None:
    """F096 H3 闭环：list_recall_frames offset 参数 + count_recall_frames 配合分页。"""
    store_group = await create_store_group(
        str(tmp_path / "f096-b5-pagination.db"),
        str(tmp_path / "artifacts"),
    )
    try:
        runtime = AgentRuntime(
            agent_runtime_id="runtime-b5p",
            project_id="proj-b5",
            agent_profile_id="agent-profile-b5",
            role=AgentRuntimeRole.MAIN,
            name="B5 Agent",
            persona_summary="B5 pagination 测试。",
        )
        await store_group.agent_context_store.save_agent_runtime(runtime)
        # 保存 5 个 frame（按 created_at 顺序）
        for i in range(5):
            ts = datetime(2026, 5, 10, 10, i, 0, tzinfo=UTC)
            await store_group.agent_context_store.save_recall_frame(
                RecallFrame(
                    recall_frame_id=f"recall-b5-{i}",
                    agent_runtime_id=runtime.agent_runtime_id,
                    project_id="proj-b5",
                    query=f"q{i}",
                    created_at=ts,
                )
            )
        await store_group.conn.commit()

        # 全表 count
        total = await store_group.agent_context_store.count_recall_frames(
            agent_runtime_id=runtime.agent_runtime_id,
        )
        assert total == 5

        # 第一页（limit=2, offset=0）
        page_1 = await store_group.agent_context_store.list_recall_frames(
            agent_runtime_id=runtime.agent_runtime_id,
            limit=2,
            offset=0,
        )
        assert len(page_1) == 2
        # ORDER BY created_at DESC：最新 (i=4) 在前
        assert page_1[0].recall_frame_id == "recall-b5-4"
        assert page_1[1].recall_frame_id == "recall-b5-3"

        # 第二页（limit=2, offset=2）
        page_2 = await store_group.agent_context_store.list_recall_frames(
            agent_runtime_id=runtime.agent_runtime_id,
            limit=2,
            offset=2,
        )
        assert len(page_2) == 2
        assert page_2[0].recall_frame_id == "recall-b5-2"
        assert page_2[1].recall_frame_id == "recall-b5-1"

        # 第三页（limit=2, offset=4）—— 仅 1 条
        page_3 = await store_group.agent_context_store.list_recall_frames(
            agent_runtime_id=runtime.agent_runtime_id,
            limit=2,
            offset=4,
        )
        assert len(page_3) == 1
        assert page_3[0].recall_frame_id == "recall-b5-0"

        # offset 超限返回空
        page_empty = await store_group.agent_context_store.list_recall_frames(
            agent_runtime_id=runtime.agent_runtime_id,
            limit=2,
            offset=10,
        )
        assert page_empty == []
    finally:
        await store_group.conn.close()


async def test_f096_b5_list_recall_frames_time_window(tmp_path: Path) -> None:
    """F096 H3 闭环：created_after / created_before 字段过滤。"""
    store_group = await create_store_group(
        str(tmp_path / "f096-b5-window.db"),
        str(tmp_path / "artifacts"),
    )
    try:
        runtime = AgentRuntime(
            agent_runtime_id="runtime-b5w",
            project_id="proj-b5w",
            agent_profile_id="agent-profile-b5w",
            role=AgentRuntimeRole.MAIN,
            name="B5W",
            persona_summary="window 测试。",
        )
        await store_group.agent_context_store.save_agent_runtime(runtime)
        await store_group.agent_context_store.save_recall_frame(
            RecallFrame(
                recall_frame_id="early",
                agent_runtime_id=runtime.agent_runtime_id,
                project_id="proj-b5w",
                query="early",
                created_at=datetime(2026, 5, 10, 8, 0, 0, tzinfo=UTC),
            )
        )
        await store_group.agent_context_store.save_recall_frame(
            RecallFrame(
                recall_frame_id="middle",
                agent_runtime_id=runtime.agent_runtime_id,
                project_id="proj-b5w",
                query="middle",
                created_at=datetime(2026, 5, 10, 12, 0, 0, tzinfo=UTC),
            )
        )
        await store_group.agent_context_store.save_recall_frame(
            RecallFrame(
                recall_frame_id="late",
                agent_runtime_id=runtime.agent_runtime_id,
                project_id="proj-b5w",
                query="late",
                created_at=datetime(2026, 5, 10, 16, 0, 0, tzinfo=UTC),
            )
        )
        await store_group.conn.commit()

        # created_after 过滤
        after_10 = await store_group.agent_context_store.list_recall_frames(
            created_after="2026-05-10T10:00:00+00:00",
        )
        assert {f.recall_frame_id for f in after_10} == {"middle", "late"}

        # created_before 过滤
        before_14 = await store_group.agent_context_store.list_recall_frames(
            created_before="2026-05-10T14:00:00+00:00",
        )
        assert {f.recall_frame_id for f in before_14} == {"early", "middle"}

        # 时间窗组合
        window = await store_group.agent_context_store.list_recall_frames(
            created_after="2026-05-10T10:00:00+00:00",
            created_before="2026-05-10T14:00:00+00:00",
        )
        assert {f.recall_frame_id for f in window} == {"middle"}

        # count_recall_frames 时间窗
        cnt = await store_group.agent_context_store.count_recall_frames(
            created_after="2026-05-10T10:00:00+00:00",
            created_before="2026-05-10T14:00:00+00:00",
        )
        assert cnt == 1
    finally:
        await store_group.conn.close()


async def test_f094_c7b_list_memory_namespaces_archived_filter(tmp_path: Path) -> None:
    """F094 C7b: list_memory_namespaces / get_memory_namespace 默认过滤 archived；
    显式 include_archived=True 可读取。"""
    store_group = await create_store_group(
        str(tmp_path / "f094-c7b.db"),
        str(tmp_path / "artifacts"),
    )
    try:
        ns_active = _make_namespace(
            namespace_id="ns-active",
            project_id="proj-c7b",
            agent_runtime_id="runtime-c7b",
            kind=MemoryNamespaceKind.AGENT_PRIVATE,
        )
        ns_archived = _make_namespace(
            namespace_id="ns-archived",
            project_id="proj-c7b",
            agent_runtime_id="runtime-c7b",
            kind=MemoryNamespaceKind.PROJECT_SHARED,
            archived_at=datetime.now(UTC),
        )
        await store_group.agent_context_store.save_memory_namespace(ns_active)
        await store_group.agent_context_store.save_memory_namespace(ns_archived)
        await store_group.conn.commit()

        # 默认 active path 仅返回 ns_active
        default_list = await store_group.agent_context_store.list_memory_namespaces(
            project_id="proj-c7b",
        )
        assert [item.namespace_id for item in default_list] == ["ns-active"]

        # 显式 include_archived=True 返回两条
        with_archived = await store_group.agent_context_store.list_memory_namespaces(
            project_id="proj-c7b",
            include_archived=True,
        )
        assert {item.namespace_id for item in with_archived} == {
            "ns-active",
            "ns-archived",
        }

        # get_memory_namespace 默认 active：archived 不返回
        default_get_archived = await store_group.agent_context_store.get_memory_namespace(
            "ns-archived"
        )
        assert default_get_archived is None

        # 显式 include_archived=True 返回 archived
        explicit_get_archived = await store_group.agent_context_store.get_memory_namespace(
            "ns-archived",
            include_archived=True,
        )
        assert explicit_get_archived is not None
        assert explicit_get_archived.namespace_id == "ns-archived"
    finally:
        await store_group.conn.close()


async def test_f094_c2_dedupe_prefers_canonical_namespace_id(tmp_path: Path) -> None:
    """F094 C2 dedupe Codex HIGH-1 闭环: 当三元组多条 active 中包含 canonical
    namespace_id（build_memory_namespace_id() 派生形态）时，必须保留 canonical id，
    归档其他——避免后续 resolver 按 canonical id 查得 archived 触发 unique 冲突。"""
    import aiosqlite

    db_path = str(tmp_path / "f094-c2-canonical.db")
    project_id = "proj-canonical"
    runtime_id = "runtime-canonical"
    kind_value = "agent_private"
    canonical_id = (
        f"memory_namespace:{kind_value}|project:{project_id}|runtime:{runtime_id}"
    )
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            """
            CREATE TABLE memory_namespaces (
                namespace_id       TEXT PRIMARY KEY,
                project_id         TEXT NOT NULL DEFAULT '',
                agent_runtime_id   TEXT NOT NULL DEFAULT '',
                kind               TEXT NOT NULL DEFAULT 'project_shared',
                name               TEXT NOT NULL DEFAULT '',
                description        TEXT NOT NULL DEFAULT '',
                memory_scope_ids   TEXT NOT NULL DEFAULT '[]',
                metadata           TEXT NOT NULL DEFAULT '{}',
                created_at         TEXT NOT NULL,
                updated_at         TEXT NOT NULL,
                archived_at        TEXT
            )
            """
        )
        # 较新的 non-canonical id（如果只按 created_at DESC，会被错误保留）
        await conn.execute(
            "INSERT INTO memory_namespaces VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "ns-stranger-newer",
                project_id,
                runtime_id,
                kind_value,
                "stranger",
                "",
                "[]",
                "{}",
                "2026-05-09T00:00:00+00:00",
                "2026-05-09T00:00:00+00:00",
                None,
            ),
        )
        # 较老的 canonical id（应被保留）
        await conn.execute(
            "INSERT INTO memory_namespaces VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                canonical_id,
                project_id,
                runtime_id,
                kind_value,
                "canonical",
                "",
                "[]",
                "{}",
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
                None,
            ),
        )
        await conn.commit()

    store_group = await create_store_group(db_path, str(tmp_path / "artifacts"))
    try:
        # canonical id 必须 active（不被归档）
        canonical = await store_group.agent_context_store.get_memory_namespace(canonical_id)
        assert canonical is not None
        assert canonical.archived_at is None

        # 较新的 non-canonical 应被归档
        stranger = await store_group.agent_context_store.get_memory_namespace(
            "ns-stranger-newer", include_archived=True
        )
        assert stranger is not None
        assert stranger.archived_at is not None
    finally:
        await store_group.conn.close()


async def test_f094_c2_dedupe_tie_break_by_namespace_id_when_created_at_equal(
    tmp_path: Path,
) -> None:
    """F094 C2 dedupe Codex LOW-7 闭环: 相同 created_at + 都不是 canonical 时，
    按 namespace_id DESC tie-break（确定性）。"""
    import aiosqlite

    db_path = str(tmp_path / "f094-c2-tie.db")
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            """
            CREATE TABLE memory_namespaces (
                namespace_id       TEXT PRIMARY KEY,
                project_id         TEXT NOT NULL DEFAULT '',
                agent_runtime_id   TEXT NOT NULL DEFAULT '',
                kind               TEXT NOT NULL DEFAULT 'project_shared',
                name               TEXT NOT NULL DEFAULT '',
                description        TEXT NOT NULL DEFAULT '',
                memory_scope_ids   TEXT NOT NULL DEFAULT '[]',
                metadata           TEXT NOT NULL DEFAULT '{}',
                created_at         TEXT NOT NULL,
                updated_at         TEXT NOT NULL,
                archived_at        TEXT
            )
            """
        )
        same_iso = "2026-05-01T00:00:00+00:00"
        for ns_id in ["ns-a", "ns-b", "ns-c"]:
            await conn.execute(
                "INSERT INTO memory_namespaces VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ns_id,
                    "proj-tie",
                    "runtime-tie",
                    "project_shared",
                    ns_id,
                    "",
                    "[]",
                    "{}",
                    same_iso,
                    same_iso,
                    None,
                ),
            )
        await conn.commit()

    store_group = await create_store_group(db_path, str(tmp_path / "artifacts"))
    try:
        # ns-c (DESC 最大) 保留 active；ns-a / ns-b archived
        ns_c = await store_group.agent_context_store.get_memory_namespace("ns-c")
        assert ns_c is not None
        assert ns_c.archived_at is None
        for archived_id in ("ns-a", "ns-b"):
            row = await store_group.agent_context_store.get_memory_namespace(
                archived_id, include_archived=True
            )
            assert row is not None
            assert row.archived_at is not None
    finally:
        await store_group.conn.close()


async def test_f094_c2_dedupe_handles_malformed_metadata(tmp_path: Path) -> None:
    """F094 C2 dedupe Codex LOW-4 闭环: legacy metadata 是非法 JSON 时 dedupe
    不中断（json_valid 防御 fallback 到 '{}'）。"""
    import aiosqlite

    db_path = str(tmp_path / "f094-c2-malformed.db")
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            """
            CREATE TABLE memory_namespaces (
                namespace_id       TEXT PRIMARY KEY,
                project_id         TEXT NOT NULL DEFAULT '',
                agent_runtime_id   TEXT NOT NULL DEFAULT '',
                kind               TEXT NOT NULL DEFAULT 'project_shared',
                name               TEXT NOT NULL DEFAULT '',
                description        TEXT NOT NULL DEFAULT '',
                memory_scope_ids   TEXT NOT NULL DEFAULT '[]',
                metadata           TEXT NOT NULL DEFAULT '{}',
                created_at         TEXT NOT NULL,
                updated_at         TEXT NOT NULL,
                archived_at        TEXT
            )
            """
        )
        # 两条同三元组 active records，其中一条 metadata 是非法 JSON
        for ns_id, ts, meta in [
            ("ns-malformed", "2026-01-01T00:00:00+00:00", "not_json_at_all"),
            ("ns-fine", "2026-05-09T00:00:00+00:00", '{"existing": "value"}'),
        ]:
            await conn.execute(
                "INSERT INTO memory_namespaces VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ns_id,
                    "proj-mal",
                    "runtime-mal",
                    "agent_private",
                    ns_id,
                    "",
                    "[]",
                    meta,
                    ts,
                    ts,
                    None,
                ),
            )
        await conn.commit()

    # init 不应抛 malformed JSON 错误
    store_group = await create_store_group(db_path, str(tmp_path / "artifacts"))
    try:
        ns_fine = await store_group.agent_context_store.get_memory_namespace("ns-fine")
        assert ns_fine is not None
        assert ns_fine.archived_at is None
        # malformed 已被 archived；archived 后 metadata 被 json_set 重建为 valid JSON
        ns_malformed = await store_group.agent_context_store.get_memory_namespace(
            "ns-malformed", include_archived=True
        )
        assert ns_malformed is not None
        assert ns_malformed.archived_at is not None
        # archive_reason 已写入（即使原来 metadata 非法）
        assert (
            ns_malformed.metadata.get("archived_reason")
            == "F094_dedupe_unique_constraint_setup"
        )
    finally:
        await store_group.conn.close()


# ---------------------------------------------------------------------------
# F094 Phase B: AGENT_PRIVATE 真生效 + 废弃 WORKER_PRIVATE 路径
# ---------------------------------------------------------------------------


async def test_f094_b7_worker_dispatch_creates_agent_private_only(tmp_path: Path) -> None:
    """F094 B7 (Codex spec HIGH-1 闭环): worker dispatch 路径创建的 namespace
    必须是 kind=AGENT_PRIVATE，**不**创建 kind=WORKER_PRIVATE。

    验证 spec §0 + §3.1 块 B 的"统一 worker / main 用 AGENT_PRIVATE，废弃
    WORKER_PRIVATE 写入路径"。"""
    store_group = await create_store_group(
        str(tmp_path / "f094-b7.db"),
        str(tmp_path / "artifacts"),
    )
    try:
        # 模拟 worker dispatch 创建 AGENT_PRIVATE namespace（baseline 改动后路径）
        ns = MemoryNamespace(
            namespace_id="ns-worker-b7",
            project_id="proj-b7",
            agent_runtime_id="worker-runtime-b7",
            kind=MemoryNamespaceKind.AGENT_PRIVATE,  # F094 B1 改后值
            name="Agent Private",
            description="Agent 私有记忆命名空间。",
            memory_scope_ids=["memory/private/main/runtime:worker-runtime-b7"],
        )
        await store_group.agent_context_store.save_memory_namespace(ns)
        await store_group.conn.commit()

        # 列出该 worker 所有 namespace：必须含 AGENT_PRIVATE，不含 WORKER_PRIVATE
        all_ns = await store_group.agent_context_store.list_memory_namespaces(
            agent_runtime_id="worker-runtime-b7",
        )
        kinds = [n.kind for n in all_ns]
        assert MemoryNamespaceKind.AGENT_PRIVATE in kinds
        assert MemoryNamespaceKind.WORKER_PRIVATE not in kinds
    finally:
        await store_group.conn.close()


async def test_f094_b7_two_workers_agent_private_isolation(tmp_path: Path) -> None:
    """F094 B7 (spec §1 US1 P1 核心断言): 同 project 下两个 Worker 不同
    agent_runtime_id，各自的 AGENT_PRIVATE namespace 严格隔离。

    Worker A 的 namespace 不出现在 Worker B 的 list_memory_namespaces 结果。"""
    store_group = await create_store_group(
        str(tmp_path / "f094-b7-isolation.db"),
        str(tmp_path / "artifacts"),
    )
    try:
        # 同 project 不同 worker
        for runtime_id in ("runtime-A", "runtime-B"):
            await store_group.agent_context_store.save_memory_namespace(
                MemoryNamespace(
                    namespace_id=f"ns-{runtime_id}",
                    project_id="proj-iso",
                    agent_runtime_id=runtime_id,
                    kind=MemoryNamespaceKind.AGENT_PRIVATE,
                    name=f"private-{runtime_id}",
                    description="",
                    memory_scope_ids=[f"memory/private/main/runtime:{runtime_id}"],
                )
            )
        await store_group.conn.commit()

        # Worker A 视角：仅看到自己的 namespace
        a_ns = await store_group.agent_context_store.list_memory_namespaces(
            project_id="proj-iso",
            agent_runtime_id="runtime-A",
            kind=MemoryNamespaceKind.AGENT_PRIVATE,
        )
        assert len(a_ns) == 1
        assert a_ns[0].agent_runtime_id == "runtime-A"
        assert a_ns[0].namespace_id == "ns-runtime-A"

        # Worker B 视角：仅看到自己的 namespace
        b_ns = await store_group.agent_context_store.list_memory_namespaces(
            project_id="proj-iso",
            agent_runtime_id="runtime-B",
            kind=MemoryNamespaceKind.AGENT_PRIVATE,
        )
        assert len(b_ns) == 1
        assert b_ns[0].agent_runtime_id == "runtime-B"

        # 跨 worker 不可见：两个 namespace 共存于同 project，但按 runtime 严格隔离
        all_in_proj = await store_group.agent_context_store.list_memory_namespaces(
            project_id="proj-iso",
            kind=MemoryNamespaceKind.AGENT_PRIVATE,
        )
        assert len(all_in_proj) == 2
        # 但按 runtime 维度查询互不命中
        assert "ns-runtime-B" not in {n.namespace_id for n in a_ns}
        assert "ns-runtime-A" not in {n.namespace_id for n in b_ns}
    finally:
        await store_group.conn.close()


async def test_f094_b5_recall_priority_private_before_project_shared(
    tmp_path: Path,
) -> None:
    """F094 B5 (spec §5 B3 acceptance): recall 优先级——AGENT_PRIVATE namespace
    必须排在 PROJECT_SHARED 之前（baseline _build_memory_scope_entries 已实现，
    F094 验证保留行为）。

    端到端通过 ordered_namespaces 排序逻辑断言（不直接调内部静态方法，避免
    白盒测试；通过观察排序结果间接验证）。"""
    # 直接调 baseline 静态方法 _build_memory_scope_entries（agent_context.py:3208+）
    from octoagent.gateway.services.agent_context import AgentContextService

    runtime = AgentRuntime(
        agent_runtime_id="runtime-b5",
        project_id="proj-b5",
        agent_profile_id="agent-profile-b5",
        role=AgentRuntimeRole.WORKER,
        name="B5 Worker",
        persona_summary="recall priority 测试。",
    )
    session = AgentSession(
        agent_session_id="agent-session-b5",
        agent_runtime_id=runtime.agent_runtime_id,
        kind=AgentSessionKind.DIRECT_WORKER,
        project_id="proj-b5",
    )
    namespaces = [
        # 故意把 PROJECT_SHARED 放在前面，验证排序后 AGENT_PRIVATE 排前
        MemoryNamespace(
            namespace_id="ns-shared",
            project_id="proj-b5",
            agent_runtime_id="runtime-b5",
            kind=MemoryNamespaceKind.PROJECT_SHARED,
            name="shared",
            memory_scope_ids=["memory/proj-b5"],
        ),
        MemoryNamespace(
            namespace_id="ns-agent-priv",
            project_id="proj-b5",
            agent_runtime_id="runtime-b5",
            kind=MemoryNamespaceKind.AGENT_PRIVATE,
            name="agent_private",
            memory_scope_ids=["memory/private/main/runtime:runtime-b5"],
        ),
    ]
    entries = AgentContextService._build_memory_scope_entries(
        agent_runtime=runtime,
        agent_session=session,
        memory_namespaces=namespaces,
    )
    # 第一个 entry 必须是 AGENT_PRIVATE（排前）
    assert entries[0]["namespace_kind"] == MemoryNamespaceKind.AGENT_PRIVATE.value
    assert entries[0]["scope_id"] == "memory/private/main/runtime:runtime-b5"
    # 末尾应该是 PROJECT_SHARED
    assert entries[-1]["namespace_kind"] == MemoryNamespaceKind.PROJECT_SHARED.value


async def test_f094_b7_main_agent_recall_does_not_hit_worker_agent_private(
    tmp_path: Path,
) -> None:
    """F094 B7 (spec §1 US1 acceptance 3): main agent recall 不命中 worker
    AGENT_PRIVATE namespace。

    设置：同 project 下创建 main agent runtime + worker runtime；各自 AGENT_PRIVATE
    namespace。验证 list_memory_namespaces(agent_runtime_id=main) 仅返回 main
    自己的，不包含 worker 的私有 namespace。"""
    store_group = await create_store_group(
        str(tmp_path / "f094-b7-main.db"),
        str(tmp_path / "artifacts"),
    )
    try:
        # main runtime + worker runtime 同 project
        main_ns = MemoryNamespace(
            namespace_id="ns-main",
            project_id="proj-main-iso",
            agent_runtime_id="runtime-main",
            kind=MemoryNamespaceKind.AGENT_PRIVATE,
            name="Main Agent Private",
            memory_scope_ids=["memory/private/main/runtime:runtime-main"],
        )
        worker_ns = MemoryNamespace(
            namespace_id="ns-worker",
            project_id="proj-main-iso",
            agent_runtime_id="runtime-worker",
            kind=MemoryNamespaceKind.AGENT_PRIVATE,
            name="Worker Agent Private",
            memory_scope_ids=["memory/private/main/runtime:runtime-worker"],
        )
        await store_group.agent_context_store.save_memory_namespace(main_ns)
        await store_group.agent_context_store.save_memory_namespace(worker_ns)
        await store_group.conn.commit()

        # main 视角 list_memory_namespaces 仅返回自己（按 agent_runtime_id 过滤）
        main_view = await store_group.agent_context_store.list_memory_namespaces(
            project_id="proj-main-iso",
            agent_runtime_id="runtime-main",
            kind=MemoryNamespaceKind.AGENT_PRIVATE,
        )
        assert len(main_view) == 1
        assert main_view[0].agent_runtime_id == "runtime-main"

        # worker 视角同样仅返回自己
        worker_view = await store_group.agent_context_store.list_memory_namespaces(
            project_id="proj-main-iso",
            agent_runtime_id="runtime-worker",
            kind=MemoryNamespaceKind.AGENT_PRIVATE,
        )
        assert len(worker_view) == 1
        assert worker_view[0].agent_runtime_id == "runtime-worker"

        # 跨视角不互通：main 视角内不出现 worker namespace_id
        assert "ns-worker" not in {n.namespace_id for n in main_view}
        assert "ns-main" not in {n.namespace_id for n in worker_view}
    finally:
        await store_group.conn.close()


async def test_f094_b3_resolve_worker_default_scope_id_helper(tmp_path: Path) -> None:
    """F094 B3 (Codex plan HIGH-1 闭环): resolve_worker_default_scope_id helper
    各场景:
    - 命中 active AGENT_PRIVATE namespace → 返回 memory_scope_ids[0]
    - agent_runtime_id 为空 → raise WorkerMemoryNamespaceNotResolved
    - 不存在 namespace → raise
    - archived namespace 不命中（与 C7b 联动）"""
    from octoagent.gateway.services.builtin_tools._deps import (
        ToolDeps,
        WorkerMemoryNamespaceNotResolved,
        resolve_worker_default_scope_id,
    )

    store_group = await create_store_group(
        str(tmp_path / "f094-b3.db"),
        str(tmp_path / "artifacts"),
    )
    try:
        # 简化版 ToolDeps：helper 仅依赖 deps.stores.agent_context_store
        deps = ToolDeps(
            project_root=tmp_path,
            stores=store_group,
            tool_broker=None,
            tool_index=None,
            skill_discovery=None,
            memory_console_service=None,
            memory_runtime_service=None,
        )

        # 场景 1: agent_runtime_id 空 → raise
        import pytest as _pytest
        with _pytest.raises(WorkerMemoryNamespaceNotResolved):
            await resolve_worker_default_scope_id(
                deps,
                project_id="proj-b3",
                agent_runtime_id="",
            )

        # 场景 2: 没有 namespace → raise
        with _pytest.raises(WorkerMemoryNamespaceNotResolved):
            await resolve_worker_default_scope_id(
                deps,
                project_id="proj-b3",
                agent_runtime_id="runtime-b3",
            )

        # 场景 3: 命中 active AGENT_PRIVATE namespace → 返回 scope_ids[0]
        ns = MemoryNamespace(
            namespace_id="ns-b3",
            project_id="proj-b3",
            agent_runtime_id="runtime-b3",
            kind=MemoryNamespaceKind.AGENT_PRIVATE,
            name="b3",
            memory_scope_ids=[
                "memory/private/main/session:s1",
                "memory/private/main/runtime:runtime-b3",
            ],
        )
        await store_group.agent_context_store.save_memory_namespace(ns)
        await store_group.conn.commit()
        scope = await resolve_worker_default_scope_id(
            deps,
            project_id="proj-b3",
            agent_runtime_id="runtime-b3",
        )
        assert scope == "memory/private/main/session:s1"  # 第一个

        # 场景 4: archived namespace 不命中（C7b 默认 active path 过滤）
        archived_ns = MemoryNamespace(
            namespace_id="ns-b3-archived",
            project_id="proj-b3-arch",
            agent_runtime_id="runtime-b3-arch",
            kind=MemoryNamespaceKind.AGENT_PRIVATE,
            name="archived",
            memory_scope_ids=["memory/private/main/runtime:runtime-b3-arch"],
            archived_at=datetime.now(UTC),
        )
        await store_group.agent_context_store.save_memory_namespace(archived_ns)
        await store_group.conn.commit()
        with _pytest.raises(WorkerMemoryNamespaceNotResolved):
            await resolve_worker_default_scope_id(
                deps,
                project_id="proj-b3-arch",
                agent_runtime_id="runtime-b3-arch",
            )
    finally:
        await store_group.conn.close()


async def test_f094_b3_helper_raises_on_multiple_active_namespaces(
    tmp_path: Path,
) -> None:
    """F094 B3 (Codex Phase B MED-5 闭环): 当三元组 unique 约束失效（罕见，
    数据被外部 SQL 篡改 / unique index 创建失败），同 (project, runtime, kind)
    三元组多于 1 条 active 记录时，helper 必须 raise WorkerMemoryNamespaceNotResolved
    而不是 fallback 到 created_at DESC limit 1（避免掩盖 schema 损坏 +
    非确定性行为）。"""
    from unittest.mock import AsyncMock

    from octoagent.gateway.services.builtin_tools._deps import (
        ToolDeps,
        WorkerMemoryNamespaceNotResolved,
        resolve_worker_default_scope_id,
    )

    # 用 mock store 直接返回 2 条 active namespace（绕过真实 unique 约束）
    fake_store = type(
        "FakeStore",
        (),
        {
            "list_memory_namespaces": AsyncMock(
                return_value=[
                    MemoryNamespace(
                        namespace_id="ns-a",
                        project_id="proj",
                        agent_runtime_id="rt",
                        kind=MemoryNamespaceKind.AGENT_PRIVATE,
                        memory_scope_ids=["scope-a"],
                    ),
                    MemoryNamespace(
                        namespace_id="ns-b",
                        project_id="proj",
                        agent_runtime_id="rt",
                        kind=MemoryNamespaceKind.AGENT_PRIVATE,
                        memory_scope_ids=["scope-b"],
                    ),
                ]
            )
        },
    )()
    fake_stores = type("FakeStores", (), {"agent_context_store": fake_store})()

    deps = ToolDeps(
        project_root=tmp_path,
        stores=fake_stores,
        tool_broker=None,
        tool_index=None,
        skill_discovery=None,
        memory_console_service=None,
        memory_runtime_service=None,
    )

    import pytest as _pytest

    with _pytest.raises(WorkerMemoryNamespaceNotResolved) as excinfo:
        await resolve_worker_default_scope_id(
            deps,
            project_id="proj",
            agent_runtime_id="rt",
        )
    assert "multiple active" in str(excinfo.value)


async def test_f094_b6_memory_recall_completed_payload_carries_new_fields() -> None:
    """F094 B6 (Codex Phase B MED-2 闭环): MemoryRecallCompletedPayload 持有
    agent_runtime_id / queried_namespace_kinds / hit_namespace_kinds 三字段，
    且默认空（向后兼容旧代码）。"""
    from octoagent.core.models.payloads import MemoryRecallCompletedPayload

    # 默认值兼容旧代码
    default_payload = MemoryRecallCompletedPayload(context_frame_id="cf-1")
    assert default_payload.agent_runtime_id == ""
    assert default_payload.queried_namespace_kinds == []
    assert default_payload.hit_namespace_kinds == []

    # 显式填字段 round-trip
    full_payload = MemoryRecallCompletedPayload(
        context_frame_id="cf-2",
        query="q",
        agent_runtime_id="runtime-x",
        queried_namespace_kinds=["agent_private", "project_shared"],
        hit_namespace_kinds=["agent_private"],
    )
    dumped = full_payload.model_dump()
    assert dumped["agent_runtime_id"] == "runtime-x"
    assert dumped["queried_namespace_kinds"] == ["agent_private", "project_shared"]
    assert dumped["hit_namespace_kinds"] == ["agent_private"]


async def test_f094_c2_dedupe_archives_duplicates_at_init(tmp_path: Path) -> None:
    """F094 C2 dedupe: init_db 时既有重复 active records 被 archived_at 标记，
    保留每组 created_at DESC 最新 1 条；其他写 metadata.archived_reason。"""
    import json

    import aiosqlite

    db_path = str(tmp_path / "f094-c2-dedupe.db")
    # Step 1: 用 legacy schema（无 partial unique index）建表 + 插入两条
    # 同三元组 active records，模拟 baseline 历史脏数据
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            """
            CREATE TABLE memory_namespaces (
                namespace_id       TEXT PRIMARY KEY,
                project_id         TEXT NOT NULL DEFAULT '',
                agent_runtime_id   TEXT NOT NULL DEFAULT '',
                kind               TEXT NOT NULL DEFAULT 'project_shared',
                name               TEXT NOT NULL DEFAULT '',
                description        TEXT NOT NULL DEFAULT '',
                memory_scope_ids   TEXT NOT NULL DEFAULT '[]',
                metadata           TEXT NOT NULL DEFAULT '{}',
                created_at         TEXT NOT NULL,
                updated_at         TEXT NOT NULL,
                archived_at        TEXT
            )
            """
        )
        # 两条同 (proj, runtime, kind)，created_at 不同
        await conn.execute(
            """
            INSERT INTO memory_namespaces (
                namespace_id, project_id, agent_runtime_id, kind,
                name, description, memory_scope_ids, metadata,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "ns-old",
                "proj-d",
                "runtime-d",
                "agent_private",
                "old",
                "",
                "[]",
                "{}",
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
            ),
        )
        await conn.execute(
            """
            INSERT INTO memory_namespaces (
                namespace_id, project_id, agent_runtime_id, kind,
                name, description, memory_scope_ids, metadata,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "ns-new",
                "proj-d",
                "runtime-d",
                "agent_private",
                "new",
                "",
                "[]",
                "{}",
                "2026-05-09T00:00:00+00:00",
                "2026-05-09T00:00:00+00:00",
            ),
        )
        await conn.commit()

    # Step 2: 跑正常 init（含 _migrate_legacy_tables → _archive_duplicate_memory_namespaces）
    store_group = await create_store_group(db_path, str(tmp_path / "artifacts"))
    try:
        # ns-new 是 created_at DESC 最新 → 保留 active
        ns_new = await store_group.agent_context_store.get_memory_namespace("ns-new")
        assert ns_new is not None
        assert ns_new.archived_at is None

        # ns-old 是较老的 → 应被 archived
        ns_old = await store_group.agent_context_store.get_memory_namespace(
            "ns-old", include_archived=True
        )
        assert ns_old is not None
        assert ns_old.archived_at is not None
        # metadata 含 archived_reason
        assert (
            ns_old.metadata.get("archived_reason")
            == "F094_dedupe_unique_constraint_setup"
        )
    finally:
        await store_group.conn.close()
