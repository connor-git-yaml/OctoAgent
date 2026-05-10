"""F097 Phase F: Memory α 共享引用单测 + 集成测。

覆盖（任务 TF.3/TF.4 要求至少 6-8 个测试）：

1. AC-F1 (α 语义): target_kind=subagent + caller 有 AGENT_PRIVATE namespace
   → _ensure_memory_namespaces 不创建新 AGENT_PRIVATE，复用 caller namespace ID
2. AC-F1 regression: target_kind=worker → 仍创建独立 AGENT_PRIVATE（F094 行为保留）
3. AC-F1 regression: target_kind 为空（main 路径）→ 仍走独立 AGENT_PRIVATE（baseline 行为）
4. AC-F2: spawn 时 SubagentDelegation.caller_memory_namespace_ids 被真实填充
   （值来自 caller AGENT_PRIVATE namespace ID 集合）
5. AC-F3 集成测: subagent 的 memory namespace IDs 等于 caller 的 AGENT_PRIVATE namespace IDs
6. 降级行为: caller_memory_namespace_ids 为空时 subagent 不创建 AGENT_PRIVATE，log warn 不报错
7. TF.1 <unknown> caller: caller_agent_runtime_id="<unknown>" 时跳过 namespace 查询
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from octoagent.core.models import (
    ContextRequestKind,
    ContextResolveRequest,
    MemoryNamespace,
    MemoryNamespaceKind,
    NormalizedMessage,
    SubagentDelegation,
    TaskStatus,
)
from octoagent.core.models.agent_context import (
    AgentRuntime,
    AgentRuntimeRole,
    AgentSession,
    AgentSessionKind,
)
from octoagent.core.store import create_store_group
from octoagent.gateway.services.agent_context import AgentContextService
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.gateway.services.task_runner import TaskRunner
from octoagent.gateway.services.task_service import TaskService


# ---------------------------------------------------------------------------
# 辅助工具
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 5, 10, 12, 0, 0, tzinfo=UTC)
_DELEGATION_ID = "01J0000000000000000000DELF"
_CALLER_RUNTIME_ID = "runtime-caller-f-001"
_CALLER_PROJECT_ID = "proj-caller-f-001"
_CALLER_NS_ID = "ns-caller-agent-private-f-001"
_PARENT_WORK_ID = "work-parent-f-001"
_SPAWNED_BY = "delegate_task"


def _make_delegation(
    *,
    parent_task_id: str,
    child_task_id: str = "child-task-f-001",
    caller_memory_namespace_ids: list[str] | None = None,
    caller_agent_runtime_id: str = _CALLER_RUNTIME_ID,
) -> SubagentDelegation:
    """构造测试用 SubagentDelegation。"""
    return SubagentDelegation(
        delegation_id=_DELEGATION_ID,
        parent_task_id=parent_task_id,
        parent_work_id=_PARENT_WORK_ID,
        child_task_id=child_task_id,
        child_agent_session_id=None,
        caller_agent_runtime_id=caller_agent_runtime_id,
        caller_project_id=_CALLER_PROJECT_ID,
        caller_memory_namespace_ids=caller_memory_namespace_ids or [],
        spawned_by=_SPAWNED_BY,
        created_at=_NOW,
    )


async def _create_agent_runtime(
    store_group,
    *,
    runtime_id: str,
    role: AgentRuntimeRole = AgentRuntimeRole.WORKER,
) -> AgentRuntime:
    """在 store 中创建并保存 AgentRuntime。"""
    runtime = AgentRuntime(
        agent_runtime_id=runtime_id,
        role=role,
    )
    await store_group.agent_context_store.save_agent_runtime(runtime)
    await store_group.conn.commit()
    return runtime


async def _create_agent_session(
    store_group,
    *,
    session_id: str,
    runtime_id: str,
    kind: AgentSessionKind = AgentSessionKind.WORKER_INTERNAL,
) -> AgentSession:
    """在 store 中创建并保存 AgentSession。"""
    session = AgentSession(
        agent_session_id=session_id,
        agent_runtime_id=runtime_id,
        kind=kind,
    )
    await store_group.agent_context_store.save_agent_session(session)
    await store_group.conn.commit()
    return session


async def _create_memory_namespace(
    store_group,
    *,
    namespace_id: str,
    agent_runtime_id: str,
    kind: MemoryNamespaceKind = MemoryNamespaceKind.AGENT_PRIVATE,
    project_id: str = "proj-f-001",
) -> MemoryNamespace:
    """在 store 中创建并保存 MemoryNamespace。"""
    ns = MemoryNamespace(
        namespace_id=namespace_id,
        project_id=project_id,
        agent_runtime_id=agent_runtime_id,
        kind=kind,
        name="Agent Private",
        description="Test namespace",
        memory_scope_ids=[],
        metadata={"source": "test"},
    )
    await store_group.agent_context_store.save_memory_namespace(ns)
    await store_group.conn.commit()
    return ns


async def _create_task_with_delegation(
    store_group,
    sse_hub: SSEHub,
    *,
    delegation: SubagentDelegation,
) -> str:
    """创建子任务并写入 SubagentDelegation 到 control_metadata。"""
    service = TaskService(store_group, sse_hub)
    msg = NormalizedMessage(
        text="subagent child task for Phase F test",
        idempotency_key=f"child-f-{datetime.now().timestamp()}",
        control_metadata={"target_kind": "subagent"},
    )
    task_id, _ = await service.create_task(msg)
    # 模拟 Phase B _emit_subagent_delegation_init_if_needed 写入 SubagentDelegation
    await service.append_user_message(
        task_id=task_id,
        text="",
        metadata={},
        control_metadata={
            "target_kind": "subagent",
            "subagent_delegation": delegation.model_dump(mode="json"),
        },
    )
    return task_id


# ---------------------------------------------------------------------------
# TF.3.1: AC-F1 (α 语义) — subagent 复用 caller AGENT_PRIVATE namespace ID
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_memory_namespaces_subagent_alpha_shared(tmp_path: Path) -> None:
    """AC-F1: target_kind=subagent + caller 有 AGENT_PRIVATE namespace
    → _ensure_memory_namespaces 不创建新 namespace，复用 caller namespace ID。
    """
    store_group = await create_store_group(str(tmp_path / "f-01.db"), str(tmp_path / "art"))

    # 创建 caller 的 AGENT_PRIVATE namespace（在 store 中）
    caller_runtime = await _create_agent_runtime(
        store_group, runtime_id=_CALLER_RUNTIME_ID
    )
    caller_ns = await _create_memory_namespace(
        store_group,
        namespace_id=_CALLER_NS_ID,
        agent_runtime_id=_CALLER_RUNTIME_ID,
        kind=MemoryNamespaceKind.AGENT_PRIVATE,
    )

    # 创建 subagent 的 AgentRuntime 和 session
    subagent_runtime = await _create_agent_runtime(
        store_group, runtime_id="runtime-subagent-f01"
    )
    subagent_session = await _create_agent_session(
        store_group,
        session_id="session-subagent-f01",
        runtime_id="runtime-subagent-f01",
        kind=AgentSessionKind.SUBAGENT_INTERNAL,
    )

    # 构造含 caller namespace ID 的 SubagentDelegation
    delegation = _make_delegation(
        parent_task_id="task-parent-f01",
        child_task_id="task-child-f01",
        caller_memory_namespace_ids=[_CALLER_NS_ID],
    )

    svc = AgentContextService(store_group, project_root=tmp_path)

    # 调用 _ensure_memory_namespaces，传入 delegation（α 路径）
    namespaces = await svc._ensure_memory_namespaces(
        project=None,
        agent_runtime=subagent_runtime,
        agent_session=subagent_session,
        project_memory_scope_ids=[],
        _subagent_delegation=delegation,
    )

    # 验证：subagent 获得的 namespace ID = caller 的 AGENT_PRIVATE namespace ID
    ns_ids = [ns.namespace_id for ns in namespaces]
    assert _CALLER_NS_ID in ns_ids, (
        f"期望 caller namespace {_CALLER_NS_ID} 在 subagent namespaces 中，实际: {ns_ids}"
    )

    # 验证：subagent 没有创建属于自己 runtime 的 AGENT_PRIVATE namespace
    subagent_ns = await store_group.agent_context_store.list_memory_namespaces(
        agent_runtime_id="runtime-subagent-f01",
        kind=MemoryNamespaceKind.AGENT_PRIVATE,
    )
    assert len(subagent_ns) == 0, (
        f"subagent 不应创建新的 AGENT_PRIVATE namespace，实际找到: {len(subagent_ns)}"
    )

    await store_group.conn.close()


# ---------------------------------------------------------------------------
# TF.3.2: AC-F1 regression — worker 路径独立 AGENT_PRIVATE（不走 α 路径）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_memory_namespaces_worker_creates_own_namespace(tmp_path: Path) -> None:
    """AC-F1 regression: target_kind=worker → 仍创建独立 AGENT_PRIVATE namespace（F094 行为保留）。
    关键：_subagent_delegation=None 时不走 α 路径。
    """
    store_group = await create_store_group(str(tmp_path / "f-02.db"), str(tmp_path / "art"))

    worker_runtime = await _create_agent_runtime(
        store_group, runtime_id="runtime-worker-f02"
    )
    worker_session = await _create_agent_session(
        store_group,
        session_id="session-worker-f02",
        runtime_id="runtime-worker-f02",
        kind=AgentSessionKind.WORKER_INTERNAL,
    )

    svc = AgentContextService(store_group, project_root=tmp_path)

    # Worker 路径：_subagent_delegation=None（F094 行为）
    namespaces = await svc._ensure_memory_namespaces(
        project=None,
        agent_runtime=worker_runtime,
        agent_session=worker_session,
        project_memory_scope_ids=[],
        _subagent_delegation=None,  # 不传 delegation
    )

    # 验证：worker 创建了自己独立的 AGENT_PRIVATE namespace
    worker_private_ns = [
        ns for ns in namespaces if ns.kind is MemoryNamespaceKind.AGENT_PRIVATE
    ]
    assert len(worker_private_ns) == 1, (
        f"Worker 路径应创建独立 AGENT_PRIVATE namespace，实际: {len(worker_private_ns)}"
    )
    assert worker_private_ns[0].agent_runtime_id == "runtime-worker-f02", (
        f"namespace 的 agent_runtime_id 应为 worker runtime，实际: {worker_private_ns[0].agent_runtime_id}"
    )

    await store_group.conn.close()


# ---------------------------------------------------------------------------
# TF.3.3: AC-F1 regression — main 路径（delegation=None）独立 AGENT_PRIVATE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_memory_namespaces_main_creates_own_namespace(tmp_path: Path) -> None:
    """AC-F1 regression: main 路径（_subagent_delegation=None）仍创建独立 AGENT_PRIVATE（baseline 行为）。"""
    store_group = await create_store_group(str(tmp_path / "f-03.db"), str(tmp_path / "art"))

    main_runtime = await _create_agent_runtime(
        store_group,
        runtime_id="runtime-main-f03",
        role=AgentRuntimeRole.MAIN,
    )
    main_session = await _create_agent_session(
        store_group,
        session_id="session-main-f03",
        runtime_id="runtime-main-f03",
        kind=AgentSessionKind.MAIN_BOOTSTRAP,
    )

    svc = AgentContextService(store_group, project_root=tmp_path)

    # main 路径：不传 _subagent_delegation
    namespaces = await svc._ensure_memory_namespaces(
        project=None,
        agent_runtime=main_runtime,
        agent_session=main_session,
        project_memory_scope_ids=[],
        _subagent_delegation=None,
    )

    main_private_ns = [
        ns for ns in namespaces if ns.kind is MemoryNamespaceKind.AGENT_PRIVATE
    ]
    assert len(main_private_ns) == 1, (
        f"main 路径应创建独立 AGENT_PRIVATE namespace，实际: {len(main_private_ns)}"
    )

    await store_group.conn.close()


# ---------------------------------------------------------------------------
# TF.3.4: 降级行为 — caller_memory_namespace_ids 为空时不报错
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_memory_namespaces_subagent_empty_caller_ids(tmp_path: Path) -> None:
    """降级行为: caller_memory_namespace_ids 为空时 subagent 不创建 AGENT_PRIVATE，不报错。"""
    store_group = await create_store_group(str(tmp_path / "f-04.db"), str(tmp_path / "art"))

    subagent_runtime = await _create_agent_runtime(
        store_group, runtime_id="runtime-subagent-f04"
    )
    subagent_session = await _create_agent_session(
        store_group,
        session_id="session-subagent-f04",
        runtime_id="runtime-subagent-f04",
        kind=AgentSessionKind.SUBAGENT_INTERNAL,
    )

    # 空 caller_memory_namespace_ids（降级场景）
    delegation = _make_delegation(
        parent_task_id="task-parent-f04",
        caller_memory_namespace_ids=[],
    )

    svc = AgentContextService(store_group, project_root=tmp_path)

    # 应不报错
    namespaces = await svc._ensure_memory_namespaces(
        project=None,
        agent_runtime=subagent_runtime,
        agent_session=subagent_session,
        project_memory_scope_ids=[],
        _subagent_delegation=delegation,
    )

    # 降级路径：subagent 没有 AGENT_PRIVATE namespace（只有可能的 PROJECT_SHARED）
    subagent_private_ns = [
        ns for ns in namespaces if ns.kind is MemoryNamespaceKind.AGENT_PRIVATE
    ]
    assert len(subagent_private_ns) == 0, (
        f"空 caller_ids 时 subagent 不应有 AGENT_PRIVATE namespace，实际: {len(subagent_private_ns)}"
    )

    # 验证 subagent 没有在 store 中创建新的 AGENT_PRIVATE namespace row
    stored_private = await store_group.agent_context_store.list_memory_namespaces(
        agent_runtime_id="runtime-subagent-f04",
        kind=MemoryNamespaceKind.AGENT_PRIVATE,
    )
    assert len(stored_private) == 0, (
        f"降级路径不应在 store 中创建新 AGENT_PRIVATE namespace，实际: {len(stored_private)}"
    )

    await store_group.conn.close()


# ---------------------------------------------------------------------------
# TF.4.1: AC-F2 — spawn 时 caller_memory_namespace_ids 被填充（TaskRunner 路径）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_fills_caller_memory_namespace_ids(tmp_path: Path) -> None:
    """AC-F2: spawn 时 SubagentDelegation.caller_memory_namespace_ids 真实填充。
    caller 有 AGENT_PRIVATE namespace → caller_memory_namespace_ids 非空。
    """
    store_group = await create_store_group(str(tmp_path / "f-05.db"), str(tmp_path / "art"))
    sse_hub = SSEHub()

    # 创建 caller 的 AGENT_PRIVATE namespace
    caller_runtime = await _create_agent_runtime(
        store_group, runtime_id=_CALLER_RUNTIME_ID
    )
    caller_ns = await _create_memory_namespace(
        store_group,
        namespace_id=_CALLER_NS_ID,
        agent_runtime_id=_CALLER_RUNTIME_ID,
        kind=MemoryNamespaceKind.AGENT_PRIVATE,
    )

    # 创建父任务
    parent_svc = TaskService(store_group, sse_hub)
    parent_msg = NormalizedMessage(
        text="parent task",
        idempotency_key="parent-f05",
    )
    parent_task_id, _ = await parent_svc.create_task(parent_msg)

    # 构造 spawn NormalizedMessage（含 __subagent_delegation_init__ raw fields）
    from ulid import ULID

    delegation_id = str(ULID())
    child_msg = NormalizedMessage(
        text="subagent child",
        idempotency_key=f"child-f05-{delegation_id}",
        control_metadata={
            "target_kind": "subagent",
            "spawned_by": _SPAWNED_BY,
            "__subagent_delegation_init__": {
                "delegation_id": delegation_id,
                "parent_task_id": parent_task_id,
                "parent_work_id": "work-parent-f05",
                "spawned_by": _SPAWNED_BY,
                "caller_agent_runtime_id": _CALLER_RUNTIME_ID,
                "caller_project_id": _CALLER_PROJECT_ID,
            },
        },
    )

    # 创建 TaskRunner 并调用 launch_child_task
    runner = TaskRunner(
        store_group=store_group,
        sse_hub=sse_hub,
        llm_service=None,
        timeout_seconds=60,
    )
    child_task_id, created = await runner.launch_child_task(child_msg)
    assert created, "子任务应被新创建"

    # 读取子任务 events，验证 SubagentDelegation.caller_memory_namespace_ids 被填充
    from octoagent.gateway.services.connection_metadata import merge_control_metadata

    events = await store_group.event_store.get_events_for_task(child_task_id)
    control = merge_control_metadata(events)
    raw_del = control.get("subagent_delegation")
    assert raw_del is not None, "subagent_delegation 应写入 control_metadata"

    if isinstance(raw_del, str):
        import json
        raw_del = json.loads(raw_del)

    caller_ns_ids = raw_del.get("caller_memory_namespace_ids", [])
    assert _CALLER_NS_ID in caller_ns_ids, (
        f"caller_memory_namespace_ids 应包含 {_CALLER_NS_ID}，实际: {caller_ns_ids}"
    )

    await store_group.conn.close()


# ---------------------------------------------------------------------------
# TF.4.2: AC-F2 — caller 无 namespace 时 caller_memory_namespace_ids = []
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_caller_without_namespace_gets_empty_ids(tmp_path: Path) -> None:
    """AC-F2 降级: caller 无 AGENT_PRIVATE namespace → caller_memory_namespace_ids = []。"""
    store_group = await create_store_group(str(tmp_path / "f-06.db"), str(tmp_path / "art"))
    sse_hub = SSEHub()

    # caller runtime 存在但没有 namespace
    caller_runtime = await _create_agent_runtime(
        store_group, runtime_id="runtime-caller-f06-no-ns"
    )

    parent_svc = TaskService(store_group, sse_hub)
    parent_msg = NormalizedMessage(
        text="parent task",
        idempotency_key="parent-f06",
    )
    parent_task_id, _ = await parent_svc.create_task(parent_msg)

    from ulid import ULID

    delegation_id = str(ULID())
    child_msg = NormalizedMessage(
        text="subagent child",
        idempotency_key=f"child-f06-{delegation_id}",
        control_metadata={
            "target_kind": "subagent",
            "spawned_by": _SPAWNED_BY,
            "__subagent_delegation_init__": {
                "delegation_id": delegation_id,
                "parent_task_id": parent_task_id,
                "parent_work_id": "work-parent-f06",
                "spawned_by": _SPAWNED_BY,
                "caller_agent_runtime_id": "runtime-caller-f06-no-ns",
                "caller_project_id": _CALLER_PROJECT_ID,
            },
        },
    )

    runner = TaskRunner(
        store_group=store_group,
        sse_hub=sse_hub,
        llm_service=None,
        timeout_seconds=60,
    )
    child_task_id, created = await runner.launch_child_task(child_msg)
    assert created

    from octoagent.gateway.services.connection_metadata import merge_control_metadata
    import json

    events = await store_group.event_store.get_events_for_task(child_task_id)
    control = merge_control_metadata(events)
    raw_del = control.get("subagent_delegation")
    assert raw_del is not None

    if isinstance(raw_del, str):
        raw_del = json.loads(raw_del)

    caller_ns_ids = raw_del.get("caller_memory_namespace_ids", ["has_values"])
    assert caller_ns_ids == [], (
        f"无 namespace 时 caller_memory_namespace_ids 应为 []，实际: {caller_ns_ids}"
    )

    await store_group.conn.close()


# ---------------------------------------------------------------------------
# TF.4.3: AC-F2 — caller_agent_runtime_id="<unknown>" 跳过 namespace 查询
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_unknown_caller_skips_namespace_query(tmp_path: Path) -> None:
    """TF.1 <unknown>: caller_agent_runtime_id='<unknown>' 时跳过 namespace 查询，
    caller_memory_namespace_ids = []，不报错。
    """
    store_group = await create_store_group(str(tmp_path / "f-07.db"), str(tmp_path / "art"))
    sse_hub = SSEHub()

    parent_svc = TaskService(store_group, sse_hub)
    parent_msg = NormalizedMessage(
        text="parent task",
        idempotency_key="parent-f07",
    )
    parent_task_id, _ = await parent_svc.create_task(parent_msg)

    from ulid import ULID

    delegation_id = str(ULID())
    child_msg = NormalizedMessage(
        text="subagent child",
        idempotency_key=f"child-f07-{delegation_id}",
        control_metadata={
            "target_kind": "subagent",
            "spawned_by": _SPAWNED_BY,
            "__subagent_delegation_init__": {
                "delegation_id": delegation_id,
                "parent_task_id": parent_task_id,
                "parent_work_id": "work-parent-f07",
                "spawned_by": _SPAWNED_BY,
                # caller_agent_runtime_id 为空 → 最终用 "<unknown>"
                "caller_agent_runtime_id": "",
                "caller_project_id": _CALLER_PROJECT_ID,
            },
        },
    )

    runner = TaskRunner(
        store_group=store_group,
        sse_hub=sse_hub,
        llm_service=None,
        timeout_seconds=60,
    )
    child_task_id, created = await runner.launch_child_task(child_msg)
    assert created

    from octoagent.gateway.services.connection_metadata import merge_control_metadata
    import json

    events = await store_group.event_store.get_events_for_task(child_task_id)
    control = merge_control_metadata(events)
    raw_del = control.get("subagent_delegation")
    assert raw_del is not None

    if isinstance(raw_del, str):
        raw_del = json.loads(raw_del)

    # <unknown> caller → 跳过查询 → 空列表
    caller_ns_ids = raw_del.get("caller_memory_namespace_ids", ["has_values"])
    assert caller_ns_ids == [], (
        f"<unknown> caller 时 caller_memory_namespace_ids 应为 []，实际: {caller_ns_ids}"
    )

    await store_group.conn.close()


# ---------------------------------------------------------------------------
# TF.4.4: AC-F3 集成测 — namespace ID 一致性（α 语义）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subagent_memory_namespace_ids_match_caller(tmp_path: Path) -> None:
    """AC-F3 集成测: subagent 通过 _ensure_memory_namespaces 获得的 namespace IDs
    等于 caller 的 AGENT_PRIVATE namespace IDs（α 语义端到端验证）。
    """
    store_group = await create_store_group(str(tmp_path / "f-08.db"), str(tmp_path / "art"))

    # 创建 caller 的 AGENT_PRIVATE namespace
    await _create_agent_runtime(store_group, runtime_id=_CALLER_RUNTIME_ID)
    caller_ns = await _create_memory_namespace(
        store_group,
        namespace_id=_CALLER_NS_ID,
        agent_runtime_id=_CALLER_RUNTIME_ID,
        kind=MemoryNamespaceKind.AGENT_PRIVATE,
    )

    # 创建 subagent runtime 和 session
    subagent_runtime = await _create_agent_runtime(
        store_group, runtime_id="runtime-subagent-f08"
    )
    subagent_session = await _create_agent_session(
        store_group,
        session_id="session-subagent-f08",
        runtime_id="runtime-subagent-f08",
        kind=AgentSessionKind.SUBAGENT_INTERNAL,
    )

    # 构造含 caller namespace ID 的 delegation
    delegation = _make_delegation(
        parent_task_id="task-parent-f08",
        caller_memory_namespace_ids=[_CALLER_NS_ID],
    )

    svc = AgentContextService(store_group, project_root=tmp_path)
    namespaces = await svc._ensure_memory_namespaces(
        project=None,
        agent_runtime=subagent_runtime,
        agent_session=subagent_session,
        project_memory_scope_ids=[],
        _subagent_delegation=delegation,
    )

    # 核心断言：subagent 获得的 private namespace ID = caller 的 namespace ID
    private_ns = [ns for ns in namespaces if ns.kind is MemoryNamespaceKind.AGENT_PRIVATE]
    assert len(private_ns) == 1, f"subagent 应有 1 个 AGENT_PRIVATE namespace，实际: {len(private_ns)}"
    assert private_ns[0].namespace_id == _CALLER_NS_ID, (
        f"subagent namespace ID 应等于 caller 的 {_CALLER_NS_ID}，实际: {private_ns[0].namespace_id}"
    )
    assert private_ns[0].agent_runtime_id == _CALLER_RUNTIME_ID, (
        f"namespace 归属 agent_runtime_id 应为 caller ({_CALLER_RUNTIME_ID})，"
        f"实际: {private_ns[0].agent_runtime_id}"
    )

    await store_group.conn.close()


# ---------------------------------------------------------------------------
# Codex Phase F 闭环测试（P2-1 fail-closed + P2-2 端到端守护）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_p2_1_subagent_session_no_delegation_fails_closed(tmp_path: Path) -> None:
    """Codex Phase F P2-1 闭环：SUBAGENT_INTERNAL session 但 SubagentDelegation
    无法读取（degraded 场景）时必须 fail-closed —— 不创建独立 AGENT_PRIVATE namespace。

    防止 delegation lookup 失败时 fall through 到 main/worker 路径创建违反 AC-F1 的
    独立 AGENT_PRIVATE namespace。
    """
    store_group = await create_store_group(
        str(tmp_path / "p2-1-fail-closed.db"), str(tmp_path / "art")
    )

    # 创建 SUBAGENT_INTERNAL session 但不提供 SubagentDelegation
    subagent_runtime = await _create_agent_runtime(
        store_group, runtime_id="runtime-subagent-p21"
    )
    subagent_session = await _create_agent_session(
        store_group,
        session_id="session-subagent-p21",
        runtime_id="runtime-subagent-p21",
        kind=AgentSessionKind.SUBAGENT_INTERNAL,
    )

    svc = AgentContextService(store_group, project_root=tmp_path)
    namespaces = await svc._ensure_memory_namespaces(
        project=None,
        agent_runtime=subagent_runtime,
        agent_session=subagent_session,
        project_memory_scope_ids=[],
        _subagent_delegation=None,  # 模拟 delegation lookup 失败
    )

    # 核心断言：fail-closed 不应有 AGENT_PRIVATE namespace 被创建
    private_ns = [ns for ns in namespaces if ns.kind is MemoryNamespaceKind.AGENT_PRIVATE]
    assert len(private_ns) == 0, (
        f"P2-1 闭环失败：SUBAGENT_INTERNAL + delegation=None 应 fail-closed "
        f"不创建 AGENT_PRIVATE，实际有 {len(private_ns)} 个"
    )

    # 验证 namespaces 中不含为 subagent runtime 创建的新 namespace row
    all_subagent_ns = await store_group.agent_context_store.list_memory_namespaces(
        agent_runtime_id="runtime-subagent-p21"
    )
    subagent_private = [
        ns for ns in all_subagent_ns if ns.kind is MemoryNamespaceKind.AGENT_PRIVATE
    ]
    assert len(subagent_private) == 0, (
        f"P2-1 闭环失败：fail-closed 路径不应在 store 中创建 subagent AGENT_PRIVATE，"
        f"实际有 {len(subagent_private)} 条"
    )

    await store_group.conn.close()


@pytest.mark.asyncio
async def test_p2_2_subagent_memory_write_uses_caller_scope(tmp_path: Path) -> None:
    """Codex Phase F P2-2 闭环：subagent memory.write 默认路径（不传 scope_id）
    通过 SubagentDelegation 找到 caller AGENT_PRIVATE scope 并写入；
    caller 在 spawn 之后能 list 到该写入（namespace 共享语义）。

    端到端验证 α 语义在真实 memory.write 路径下生效（不只是 namespace_id 一致性）。
    """
    store_group = await create_store_group(
        str(tmp_path / "p2-2-e2e.db"), str(tmp_path / "art")
    )

    # 创建 caller AGENT_PRIVATE namespace 含 scope_id
    caller_runtime = await _create_agent_runtime(
        store_group, runtime_id="runtime-caller-p22"
    )
    caller_ns = await _create_memory_namespace(
        store_group,
        namespace_id=f"ns-private-caller-p22",
        agent_runtime_id="runtime-caller-p22",
        kind=MemoryNamespaceKind.AGENT_PRIVATE,
    )
    # 注入 scope_ids（caller_ns 创建时通常有 memory_scope_ids）
    caller_ns_with_scope = caller_ns.model_copy(
        update={
            "memory_scope_ids": ["memory/private/runtime-caller-p22"]
        }
    )
    await store_group.agent_context_store.save_memory_namespace(caller_ns_with_scope)
    await store_group.conn.commit()

    # 模拟 spawn：构造含 caller_memory_namespace_ids 的 SubagentDelegation
    delegation = _make_delegation(
        parent_task_id="task-parent-p22",
        caller_agent_runtime_id="runtime-caller-p22",
        caller_memory_namespace_ids=[caller_ns_with_scope.namespace_id],
    )

    # 创建 subagent SUBAGENT_INTERNAL session
    subagent_runtime = await _create_agent_runtime(
        store_group, runtime_id="runtime-subagent-p22"
    )
    subagent_session = await _create_agent_session(
        store_group,
        session_id="session-subagent-p22",
        runtime_id="runtime-subagent-p22",
        kind=AgentSessionKind.SUBAGENT_INTERNAL,
    )

    # 执行 _ensure_memory_namespaces 应返回 caller 共享 namespace（α 路径）
    svc = AgentContextService(store_group, project_root=tmp_path)
    namespaces = await svc._ensure_memory_namespaces(
        project=None,
        agent_runtime=subagent_runtime,
        agent_session=subagent_session,
        project_memory_scope_ids=[],
        _subagent_delegation=delegation,
    )

    # 验证 1: subagent 拿到的 private namespace 是 caller 的
    private_ns = [
        ns for ns in namespaces if ns.kind is MemoryNamespaceKind.AGENT_PRIVATE
    ]
    assert len(private_ns) == 1, (
        f"端到端 α 语义失败：subagent 应获得 1 个 caller AGENT_PRIVATE，实际 {len(private_ns)}"
    )
    assert private_ns[0].namespace_id == caller_ns_with_scope.namespace_id

    # 验证 2: subagent 拿到的 namespace memory_scope_ids 与 caller 一致
    # （memory.write 默认路径会用此 scope_id 写入）
    assert private_ns[0].memory_scope_ids == ["memory/private/runtime-caller-p22"], (
        f"caller scope_ids 未传递到 subagent，实际 {private_ns[0].memory_scope_ids}"
    )

    # 验证 3: caller 在 spawn 后通过 list_memory_namespaces 仍能访问同一 namespace
    # （α 语义关键：caller 与 subagent 共享同一 namespace row，subagent 写入对 caller 可见）
    caller_listed = await store_group.agent_context_store.list_memory_namespaces(
        agent_runtime_id="runtime-caller-p22",
        kind=MemoryNamespaceKind.AGENT_PRIVATE,
    )
    assert len(caller_listed) == 1
    assert caller_listed[0].namespace_id == caller_ns_with_scope.namespace_id
    # 关键：subagent 共享的 namespace 与 caller list 出来的是同一 namespace_id
    assert private_ns[0].namespace_id == caller_listed[0].namespace_id

    await store_group.conn.close()
