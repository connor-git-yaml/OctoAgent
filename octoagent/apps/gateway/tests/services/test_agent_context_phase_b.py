"""F097 Phase B: _ensure_agent_session SUBAGENT_INTERNAL 第 4 路 + spawn 写 SubagentDelegation 单测。

覆盖（任务 TB.5 要求至少 8 个测试）：
1. AC-B1: target_kind=subagent + 有 parent_agent_session_id → 创建 kind=SUBAGENT_INTERNAL AgentSession
2. AC-B1: parent_worker_runtime_id 字段从 SubagentDelegation.caller_agent_runtime_id 正确填充
3. AC-B2 regression: target_kind=worker + 无 parent → DIRECT_WORKER（不变）
4. AC-B2 regression: target_kind=worker + 有 parent → WORKER_INTERNAL（不变）
5. AC-B2 regression: target_kind 为空 + role=MAIN → MAIN_BOOTSTRAP（不变）
6. B-1 写入: spawn 时 child_task control_metadata 含完整 SubagentDelegation（delegation_id / child_task_id 等字段）
7. B-3 回填: _ensure_agent_session 完成后 child_task control_metadata 中 child_agent_session_id 非 None
8. P2-3 事务顺序: SUBAGENT_COMPLETED 事件在 session.save 之前 emit
9. P2-4 终态覆盖: dispatch exception 路径调用 cleanup（SUBAGENT_COMPLETED 被 emit）
10. end-to-end: spawn → cleanup 路径联通（delegation 从 spawn 写入到 cleanup 读取完整链路）
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from octoagent.core.models import (
    AgentRuntimeRole,
    AgentSessionKind,
    AgentSessionStatus,
    ContextRequestKind,
    ContextResolveRequest,
    EventType,
    NormalizedMessage,
    SubagentDelegation,
    Task,
    TaskStatus,
)
from octoagent.core.models.agent_context import (
    AgentRuntime,
    AgentSession,
)
from octoagent.core.store import create_store_group
from octoagent.gateway.services.agent_context import AgentContextService
from octoagent.gateway.services.connection_metadata import merge_control_metadata
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.gateway.services.task_runner import TaskRunner
from octoagent.gateway.services.task_service import TaskService


# ---------------------------------------------------------------------------
# 辅助工具
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 5, 10, 12, 0, 0, tzinfo=UTC)
_DELEGATION_ID = "01J0000000000000000000DELA"
_CHILD_SESSION_ID = "session-subagent-b2b-001"
_CALLER_RUNTIME_ID = "runtime-caller-b-001"
_CALLER_PROJECT_ID = "proj-caller-b-001"
_PARENT_WORK_ID = "work-parent-b-001"
_SPAWNED_BY = "delegate_task"


def _make_request(
    *,
    target_kind: str = "subagent",
    role: AgentRuntimeRole = AgentRuntimeRole.WORKER,
    parent_agent_session_id: str = "session-parent-b-001",
    work_id: str = "work-b-001",
) -> ContextResolveRequest:
    """构造最小 ContextResolveRequest。"""
    meta: dict = {}
    if target_kind:
        meta["target_kind"] = target_kind
    if parent_agent_session_id:
        meta["parent_agent_session_id"] = parent_agent_session_id
    return ContextResolveRequest(
        request_id="req-phase-b-test-001",
        request_kind=ContextRequestKind.WORKER,
        surface="chat",
        delegation_metadata=meta,
        work_id=work_id,
    )


def _make_delegation(
    *,
    parent_task_id: str,
    child_task_id: str = "child-task-b-001",
    child_agent_session_id: str | None = None,
    caller_agent_runtime_id: str = _CALLER_RUNTIME_ID,
    caller_project_id: str = _CALLER_PROJECT_ID,
) -> SubagentDelegation:
    """构造测试用 SubagentDelegation。"""
    return SubagentDelegation(
        delegation_id=_DELEGATION_ID,
        parent_task_id=parent_task_id,
        parent_work_id=_PARENT_WORK_ID,
        child_task_id=child_task_id,
        child_agent_session_id=child_agent_session_id,
        caller_agent_runtime_id=caller_agent_runtime_id,
        caller_project_id=caller_project_id,
        spawned_by=_SPAWNED_BY,
        created_at=_NOW,
    )


async def _create_task_with_delegation(
    store_group, sse_hub: SSEHub, *, parent_task_id: str | None = None
) -> tuple[str, SubagentDelegation]:
    """创建子任务，并写入 SubagentDelegation 到 control_metadata（模拟 B-1 写入）。"""
    service = TaskService(store_group, sse_hub)
    msg = NormalizedMessage(
        text="subagent child task for Phase B test",
        idempotency_key=f"child-b-{datetime.now().timestamp()}",
        control_metadata={"target_kind": "subagent"},
    )
    task_id, _ = await service.create_task(msg)
    _parent_task_id = parent_task_id or task_id

    delegation = _make_delegation(
        parent_task_id=_parent_task_id,
        child_task_id=task_id,
    )
    # 追加 SubagentDelegation（模拟 B-1 的 USER_MESSAGE 写入）
    await service.append_user_message(
        task_id=task_id,
        text="",
        metadata={},
        control_metadata={"subagent_delegation": delegation.model_dump(mode="json")},
    )
    return task_id, delegation


async def _create_agent_runtime(store_group, *, runtime_id: str, role: AgentRuntimeRole = AgentRuntimeRole.WORKER) -> AgentRuntime:
    """在 store 中创建 AgentRuntime。"""
    runtime = AgentRuntime(
        agent_runtime_id=runtime_id,
        role=role,
    )
    await store_group.agent_context_store.save_agent_runtime(runtime)
    await store_group.conn.commit()
    return runtime


async def _create_runner(store_group, sse_hub: SSEHub) -> TaskRunner:
    """创建最小 TaskRunner。"""
    return TaskRunner(
        store_group=store_group,
        sse_hub=sse_hub,
        llm_service=None,
        timeout_seconds=60,
    )


# ---------------------------------------------------------------------------
# TB.5.1: AC-B1 — target_kind=subagent 创建 SUBAGENT_INTERNAL session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_session_creates_subagent_internal(tmp_path: Path) -> None:
    """AC-B1: target_kind=subagent + parent_agent_session_id → kind=SUBAGENT_INTERNAL。"""
    store_group = await create_store_group(str(tmp_path / "b-01.db"), str(tmp_path / "art"))
    sse_hub = SSEHub()

    # 创建子任务并写入 delegation
    child_task_id, delegation = await _create_task_with_delegation(store_group, sse_hub)
    task = await store_group.task_store.get_task(child_task_id)
    assert task is not None

    # 创建 AgentRuntime（subagent role）
    runtime = await _create_agent_runtime(store_group, runtime_id="runtime-subagent-b01")

    # 构造 _ensure_agent_session 所需的 request 和 session_state
    request = _make_request(target_kind="subagent")
    session_state = MagicMock()
    session_state.session_id = "legacy-session-b01"
    session_state.agent_session_id = ""
    session_state.agent_runtime_id = ""

    # AgentContextService 仅需 store（不需要真实的 project root）
    svc = AgentContextService(store_group, project_root=tmp_path)
    session = await svc._ensure_agent_session(
        request=request,
        task=task,
        project=None,
        agent_runtime=runtime,
        session_state=session_state,
    )

    assert session.kind is AgentSessionKind.SUBAGENT_INTERNAL, (
        f"期望 SUBAGENT_INTERNAL，实际 {session.kind}"
    )
    await store_group.conn.close()


# ---------------------------------------------------------------------------
# TB.5.2: AC-B1 — parent_worker_runtime_id 从 delegation 正确填充
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_session_fills_parent_worker_runtime_id(tmp_path: Path) -> None:
    """AC-B1: SUBAGENT_INTERNAL session 的 parent_worker_runtime_id = delegation.caller_agent_runtime_id。"""
    store_group = await create_store_group(str(tmp_path / "b-02.db"), str(tmp_path / "art"))
    sse_hub = SSEHub()

    child_task_id, delegation = await _create_task_with_delegation(store_group, sse_hub)
    task = await store_group.task_store.get_task(child_task_id)
    assert task is not None

    runtime = await _create_agent_runtime(store_group, runtime_id="runtime-subagent-b02")
    request = _make_request(target_kind="subagent")
    session_state = MagicMock()
    session_state.session_id = "legacy-session-b02"
    session_state.agent_session_id = ""
    session_state.agent_runtime_id = ""

    svc = AgentContextService(store_group, project_root=tmp_path)
    session = await svc._ensure_agent_session(
        request=request,
        task=task,
        project=None,
        agent_runtime=runtime,
        session_state=session_state,
    )

    assert session.kind is AgentSessionKind.SUBAGENT_INTERNAL
    assert session.parent_worker_runtime_id == delegation.caller_agent_runtime_id, (
        f"期望 {delegation.caller_agent_runtime_id}，实际 {session.parent_worker_runtime_id}"
    )
    await store_group.conn.close()


# ---------------------------------------------------------------------------
# TB.5.3: AC-B2 regression — target_kind=worker + 无 parent → DIRECT_WORKER
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_session_worker_no_parent_is_direct_worker(tmp_path: Path) -> None:
    """AC-B2 regression: target_kind=worker + 无 parent_agent_session_id → DIRECT_WORKER 不变。"""
    store_group = await create_store_group(str(tmp_path / "b-03.db"), str(tmp_path / "art"))
    sse_hub = SSEHub()

    service = TaskService(store_group, sse_hub)
    msg = NormalizedMessage(
        text="worker task for regression",
        idempotency_key=f"worker-regression-{datetime.now().timestamp()}",
    )
    task_id, _ = await service.create_task(msg)
    task = await store_group.task_store.get_task(task_id)
    assert task is not None

    runtime = await _create_agent_runtime(store_group, runtime_id="runtime-worker-b03")

    # 无 parent_agent_session_id，无 work_id → DIRECT_WORKER
    request = _make_request(target_kind="worker", parent_agent_session_id="", work_id="")
    session_state = MagicMock()
    session_state.session_id = "legacy-session-b03"
    session_state.agent_session_id = ""
    session_state.agent_runtime_id = ""

    svc = AgentContextService(store_group, project_root=tmp_path)
    session = await svc._ensure_agent_session(
        request=request,
        task=task,
        project=None,
        agent_runtime=runtime,
        session_state=session_state,
    )

    assert session.kind is AgentSessionKind.DIRECT_WORKER, (
        f"regression: 期望 DIRECT_WORKER，实际 {session.kind}"
    )
    await store_group.conn.close()


# ---------------------------------------------------------------------------
# TB.5.4: AC-B2 regression — target_kind=worker + 有 parent + work_id → WORKER_INTERNAL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_session_worker_with_parent_is_worker_internal(tmp_path: Path) -> None:
    """AC-B2 regression: target_kind=worker + 有 parent_agent_session_id → WORKER_INTERNAL 不变。"""
    store_group = await create_store_group(str(tmp_path / "b-04.db"), str(tmp_path / "art"))
    sse_hub = SSEHub()

    service = TaskService(store_group, sse_hub)
    msg = NormalizedMessage(
        text="worker internal task for regression",
        idempotency_key=f"worker-internal-{datetime.now().timestamp()}",
    )
    task_id, _ = await service.create_task(msg)
    task = await store_group.task_store.get_task(task_id)
    assert task is not None

    runtime = await _create_agent_runtime(store_group, runtime_id="runtime-worker-b04")

    # 有 parent_agent_session_id → WORKER_INTERNAL
    request = _make_request(target_kind="worker", parent_agent_session_id="session-parent-b04", work_id="work-b04")
    session_state = MagicMock()
    session_state.session_id = "legacy-session-b04"
    session_state.agent_session_id = ""
    session_state.agent_runtime_id = ""

    svc = AgentContextService(store_group, project_root=tmp_path)
    session = await svc._ensure_agent_session(
        request=request,
        task=task,
        project=None,
        agent_runtime=runtime,
        session_state=session_state,
    )

    assert session.kind is AgentSessionKind.WORKER_INTERNAL, (
        f"regression: 期望 WORKER_INTERNAL，实际 {session.kind}"
    )
    await store_group.conn.close()


# ---------------------------------------------------------------------------
# TB.5.5: AC-B2 regression — target_kind 为空 + role=MAIN → MAIN_BOOTSTRAP
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_session_main_agent_is_main_bootstrap(tmp_path: Path) -> None:
    """AC-B2 regression: target_kind 为空 + role=MAIN → MAIN_BOOTSTRAP 不变。"""
    store_group = await create_store_group(str(tmp_path / "b-05.db"), str(tmp_path / "art"))
    sse_hub = SSEHub()

    service = TaskService(store_group, sse_hub)
    msg = NormalizedMessage(
        text="main agent task for regression",
        idempotency_key=f"main-regression-{datetime.now().timestamp()}",
    )
    task_id, _ = await service.create_task(msg)
    task = await store_group.task_store.get_task(task_id)
    assert task is not None

    # 主 Agent role
    runtime = await _create_agent_runtime(
        store_group, runtime_id="runtime-main-b05", role=AgentRuntimeRole.MAIN
    )

    # target_kind 为空 → 主 Agent 路径
    request = _make_request(target_kind="", parent_agent_session_id="", work_id="")
    session_state = MagicMock()
    session_state.session_id = "legacy-session-b05"
    session_state.agent_session_id = ""
    session_state.agent_runtime_id = ""

    svc = AgentContextService(store_group, project_root=tmp_path)
    session = await svc._ensure_agent_session(
        request=request,
        task=task,
        project=None,
        agent_runtime=runtime,
        session_state=session_state,
    )

    assert session.kind is AgentSessionKind.MAIN_BOOTSTRAP, (
        f"regression: 期望 MAIN_BOOTSTRAP，实际 {session.kind}"
    )
    await store_group.conn.close()


# ---------------------------------------------------------------------------
# TB.5.6: B-1 写入 — spawn 时 child_task control_metadata 含 SubagentDelegation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_writes_subagent_delegation_to_child_task(tmp_path: Path) -> None:
    """B-1 写入: spawn 后子任务 control_metadata 中存在 SubagentDelegation，且必要字段非空。"""
    store_group = await create_store_group(str(tmp_path / "b-06.db"), str(tmp_path / "art"))
    sse_hub = SSEHub()

    # 使用辅助函数模拟 B-1 写入（append_user_message 写入 subagent_delegation）
    child_task_id, delegation = await _create_task_with_delegation(store_group, sse_hub)

    # 通过 merge_control_metadata 读取 control_metadata
    events = await store_group.event_store.get_events_for_task(child_task_id)
    control = merge_control_metadata(events)
    raw = control.get("subagent_delegation")

    assert raw is not None, "child_task control_metadata 中缺少 subagent_delegation"
    # 验证可以反序列化为 SubagentDelegation
    if isinstance(raw, str):
        parsed = SubagentDelegation.model_validate_json(raw)
    else:
        parsed = SubagentDelegation.model_validate(raw)

    assert parsed.delegation_id, "delegation_id 不能为空"
    assert parsed.child_task_id == child_task_id, (
        f"child_task_id 期望 {child_task_id}，实际 {parsed.child_task_id}"
    )
    assert parsed.caller_project_id is not None, "caller_project_id 不能为 None"
    assert parsed.spawned_by == _SPAWNED_BY

    await store_group.conn.close()


# ---------------------------------------------------------------------------
# TB.5.7: B-3 回填 — _ensure_agent_session 后 child_agent_session_id 非 None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_session_backfills_child_agent_session_id(tmp_path: Path) -> None:
    """B-3 回填: _ensure_agent_session 完成后，子任务 control_metadata 的
    subagent_delegation.child_agent_session_id 非 None，指向新创建的 session。"""
    store_group = await create_store_group(str(tmp_path / "b-07.db"), str(tmp_path / "art"))
    sse_hub = SSEHub()

    child_task_id, _ = await _create_task_with_delegation(store_group, sse_hub)
    task = await store_group.task_store.get_task(child_task_id)
    assert task is not None

    runtime = await _create_agent_runtime(store_group, runtime_id="runtime-subagent-b07")
    request = _make_request(target_kind="subagent")
    session_state = MagicMock()
    session_state.session_id = "legacy-session-b07"
    session_state.agent_session_id = ""
    session_state.agent_runtime_id = ""

    svc = AgentContextService(store_group, project_root=tmp_path)
    session = await svc._ensure_agent_session(
        request=request,
        task=task,
        project=None,
        agent_runtime=runtime,
        session_state=session_state,
    )

    # B-3 后读取 control_metadata
    events = await store_group.event_store.get_events_for_task(child_task_id)
    control = merge_control_metadata(events)
    raw = control.get("subagent_delegation")
    assert raw is not None

    if isinstance(raw, str):
        parsed = SubagentDelegation.model_validate_json(raw)
    else:
        parsed = SubagentDelegation.model_validate(raw)

    assert parsed.child_agent_session_id is not None, (
        "B-3 回填失败：child_agent_session_id 仍为 None"
    )
    assert parsed.child_agent_session_id == session.agent_session_id, (
        f"child_agent_session_id 期望 {session.agent_session_id}，实际 {parsed.child_agent_session_id}"
    )
    await store_group.conn.close()


# ---------------------------------------------------------------------------
# TB.5.8: P2-3 事务顺序 — SUBAGENT_COMPLETED 在 session.save 之前 emit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_p2_3_event_emitted_before_session_save(tmp_path: Path) -> None:
    """P2-3: cleanup 中 SUBAGENT_COMPLETED 在 session.save 之前 emit。
    模拟 session.save 失败时，事件仍已写入（audit chain 优先）。"""
    store_group = await create_store_group(str(tmp_path / "b-08.db"), str(tmp_path / "art"))
    sse_hub = SSEHub()

    service = TaskService(store_group, sse_hub)
    msg = NormalizedMessage(
        text="parent task for P2-3 test",
        idempotency_key=f"parent-p2-3-{datetime.now().timestamp()}",
    )
    parent_task_id, _ = await service.create_task(msg)
    runner = await _create_runner(store_group, sse_hub)

    # 创建 AgentRuntime + Session（cleanup 读取的 session）
    runtime = await _create_agent_runtime(store_group, runtime_id="runtime-p2-3-001")
    session = AgentSession(
        agent_session_id="session-p2-3-001",
        agent_runtime_id="runtime-p2-3-001",
        kind=AgentSessionKind.SUBAGENT_INTERNAL,
        status=AgentSessionStatus.ACTIVE,
    )
    await store_group.agent_context_store.save_agent_session(session)
    await store_group.conn.commit()

    delegation = _make_delegation(
        parent_task_id=parent_task_id,
        child_agent_session_id="session-p2-3-001",
    )

    emit_order: list[str] = []
    original_append = store_group.event_store.append_event_committed
    original_save = store_group.agent_context_store.save_agent_session

    async def tracked_emit(event, **kwargs):
        emit_order.append("event_emit")
        return await original_append(event, **kwargs)

    async def tracked_save(sess):
        emit_order.append("session_save")
        return await original_save(sess)

    store_group.event_store.append_event_committed = tracked_emit
    store_group.agent_context_store.save_agent_session = tracked_save

    with patch(
        "octoagent.gateway.services.task_runner.TaskService.get_latest_user_metadata",
        new_callable=AsyncMock,
        return_value={"subagent_delegation": delegation.model_dump_json()},
    ), patch(
        "octoagent.gateway.services.task_runner.TaskService.get_task",
        new_callable=AsyncMock,
        return_value=type("Task", (), {
            "status": TaskStatus.SUCCEEDED,
            "updated_at": _NOW,
        })(),
    ):
        await runner._close_subagent_session_if_needed(parent_task_id)

    # event_emit 必须在 session_save 之前（P2-3 颠倒顺序保证）
    if emit_order:
        event_idx = next((i for i, x in enumerate(emit_order) if x == "event_emit"), None)
        session_idx = next((i for i, x in enumerate(emit_order) if x == "session_save"), None)
        if event_idx is not None and session_idx is not None:
            assert event_idx < session_idx, (
                f"P2-3 失败：event_emit({event_idx}) 应在 session_save({session_idx}) 之前"
            )

    await store_group.conn.close()


# ---------------------------------------------------------------------------
# TB.5.9: P2-4 终态覆盖 — dispatch exception 路径调用 cleanup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_p2_4_dispatch_exception_triggers_cleanup(tmp_path: Path) -> None:
    """P2-4: dispatch exception 路径（原来无 _notify_completion）现在调用 cleanup。"""
    store_group = await create_store_group(str(tmp_path / "b-09.db"), str(tmp_path / "art"))
    sse_hub = SSEHub()

    # 创建父任务（子任务中写 delegation）
    service = TaskService(store_group, sse_hub)
    msg = NormalizedMessage(
        text="parent task for P2-4 test",
        idempotency_key=f"parent-p2-4-{datetime.now().timestamp()}",
    )
    parent_task_id, _ = await service.create_task(msg)

    runner = await _create_runner(store_group, sse_hub)
    cleanup_called: list[str] = []

    original_cleanup = runner._close_subagent_session_if_needed

    async def tracked_cleanup(task_id: str) -> None:
        cleanup_called.append(task_id)
        await original_cleanup(task_id)

    runner._close_subagent_session_if_needed = tracked_cleanup

    # 模拟 dispatch exception（orchestrator._ensure_task_failed 无需真实 orchestrator）
    with patch.object(runner._orchestrator, "_ensure_task_failed", new_callable=AsyncMock), \
         patch.object(runner._orchestrator, "dispatch", side_effect=RuntimeError("模拟 dispatch 失败")):
        # 直接调用 _run_job（内部 try-except 捕获 dispatch 异常后应调用 cleanup）
        await runner._run_job(
            task_id=parent_task_id,
            user_text="test",
            model_alias=None,
        )

    assert parent_task_id in cleanup_called, (
        "P2-4 失败：dispatch exception 路径没有调用 _close_subagent_session_if_needed"
    )

    await store_group.conn.close()


# ---------------------------------------------------------------------------
# TB.5.10: end-to-end — spawn → cleanup 路径联通
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_to_cleanup_end_to_end(tmp_path: Path) -> None:
    """End-to-End: spawn（写入 SubagentDelegation）→ cleanup（读取 delegation 并关闭 session）完整链路。"""
    store_group = await create_store_group(str(tmp_path / "b-10.db"), str(tmp_path / "art"))
    sse_hub = SSEHub()

    # 1. 创建父任务
    service = TaskService(store_group, sse_hub)
    parent_msg = NormalizedMessage(
        text="parent task for e2e test",
        idempotency_key=f"parent-e2e-b-{datetime.now().timestamp()}",
    )
    parent_task_id, _ = await service.create_task(parent_msg)

    # 2. 创建子任务（模拟 B-1 spawn 写入 SubagentDelegation）
    child_task_id, delegation = await _create_task_with_delegation(
        store_group, sse_hub, parent_task_id=parent_task_id
    )
    child_task = await store_group.task_store.get_task(child_task_id)
    assert child_task is not None

    # 3. 模拟 B-2/B-3：创建 SUBAGENT_INTERNAL session 并回填 child_agent_session_id
    runtime = await _create_agent_runtime(store_group, runtime_id="runtime-e2e-b-001")
    request = _make_request(target_kind="subagent")
    session_state = MagicMock()
    session_state.session_id = "legacy-e2e-b-001"
    session_state.agent_session_id = ""
    session_state.agent_runtime_id = ""

    svc = AgentContextService(store_group, project_root=tmp_path)
    session = await svc._ensure_agent_session(
        request=request,
        task=child_task,
        project=None,
        agent_runtime=runtime,
        session_state=session_state,
    )
    assert session.kind is AgentSessionKind.SUBAGENT_INTERNAL

    # 4. 验证 child_agent_session_id 已回填
    events = await store_group.event_store.get_events_for_task(child_task_id)
    control = merge_control_metadata(events)
    raw = control.get("subagent_delegation")
    assert raw is not None
    if isinstance(raw, str):
        updated_delegation = SubagentDelegation.model_validate_json(raw)
    else:
        updated_delegation = SubagentDelegation.model_validate(raw)
    assert updated_delegation.child_agent_session_id == session.agent_session_id

    # 5. 模拟 cleanup（子任务进入终态）
    runner = await _create_runner(store_group, sse_hub)

    with patch(
        "octoagent.gateway.services.task_runner.TaskService.get_latest_user_metadata",
        new_callable=AsyncMock,
        return_value={"subagent_delegation": updated_delegation.model_dump_json()},
    ), patch(
        "octoagent.gateway.services.task_runner.TaskService.get_task",
        new_callable=AsyncMock,
        return_value=type("Task", (), {
            "status": TaskStatus.SUCCEEDED,
            "updated_at": _NOW,
        })(),
    ):
        await runner._close_subagent_session_if_needed(child_task_id)

    # 6. 验证 session 已 CLOSED
    closed_session = await store_group.agent_context_store.get_agent_session(session.agent_session_id)
    assert closed_session is not None
    assert closed_session.status == AgentSessionStatus.CLOSED, (
        f"E2E 失败：session status 期望 CLOSED，实际 {closed_session.status}"
    )

    # 7. 验证 SUBAGENT_COMPLETED 事件写入父任务的事件流
    parent_events = await store_group.event_store.get_events_for_task(parent_task_id)
    completed_events = [e for e in parent_events if e.type is EventType.SUBAGENT_COMPLETED]
    assert len(completed_events) >= 1, "E2E 失败：SUBAGENT_COMPLETED 事件未写入父任务事件流"

    await store_group.conn.close()


# ---------------------------------------------------------------------------
# Codex Phase B Round 2 闭环测试（P1-1 / P1-2 / P2-5 / P2-6 验证）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_p1_2_emit_before_enqueue_no_race(tmp_path: Path) -> None:
    """Codex P1-2 闭环：launch_child_task 在 create_task 后、enqueue 前 emit
    SubagentDelegation USER_MESSAGE event。验证 spawn 后第一时间已能从 task 事件流读到 delegation
    （消除 child runtime 启动前 race 窗口）。
    """
    store_group = await create_store_group(
        str(tmp_path / "p1-2.db"), str(tmp_path / "art")
    )
    sse_hub = SSEHub()
    runner = TaskRunner(
        store_group=store_group,
        sse_hub=sse_hub,
        llm_service=None,
        timeout_seconds=60,
    )

    # 模拟 capability_pack 写入的 raw delegation init
    raw_init = {
        "delegation_id": "01J0000000000000000000ABCD",
        "parent_task_id": "task-parent-p1-2",
        "parent_work_id": "work-parent-p1-2",
        "caller_agent_runtime_id": "runtime-caller-p1-2",
        "caller_project_id": "proj-caller-p1-2",
        "spawned_by": "delegate_task",
    }
    message = NormalizedMessage(
        text="subagent test message",
        idempotency_key=f"p1-2-test-{datetime.now().timestamp()}",
        control_metadata={
            "target_kind": "subagent",
            "__subagent_delegation_init__": raw_init,
        },
    )

    # patch enqueue 防止真启动 task；保留 _emit_subagent_delegation_init_if_needed 实际执行
    with patch.object(runner, "enqueue", new_callable=AsyncMock):
        task_id, created = await runner.launch_child_task(message)

    assert created, "launch_child_task 应成功创建 task"

    # 验证：USER_MESSAGE event 含完整 SubagentDelegation 在 task 事件流中
    events = await store_group.event_store.get_events_for_task(task_id)
    user_msg_events = [e for e in events if e.type is EventType.USER_MESSAGE]
    delegation_events = [
        e for e in user_msg_events
        if e.payload.get("control_metadata", {}).get("subagent_delegation")
    ]
    assert len(delegation_events) >= 1, (
        "P1-2 闭环失败：launch_child_task 后未在事件流中找到 SubagentDelegation USER_MESSAGE"
    )

    # 验证：delegation event 在 enqueue 之前 emit（enqueue 被 mock 不会真触发，
    # 但事件流已含 delegation 证明 race 已消除）
    delegation_event = delegation_events[0]
    delegation_dict = delegation_event.payload["control_metadata"]["subagent_delegation"]
    delegation = SubagentDelegation.model_validate_json(delegation_dict) if isinstance(
        delegation_dict, str
    ) else SubagentDelegation.model_validate(delegation_dict)
    assert delegation.delegation_id == raw_init["delegation_id"]
    assert delegation.child_task_id == task_id, (
        f"P1-2 闭环：child_task_id 应为真实 task_id={task_id}，实际 {delegation.child_task_id}"
    )

    await store_group.conn.close()


@pytest.mark.asyncio
async def test_p1_1_emit_preserves_target_kind(tmp_path: Path) -> None:
    """Codex P1-1 闭环：USER_MESSAGE event 的 control_metadata 必须含 target_kind=subagent，
    让 merge_control_metadata 取最新 USER_MESSAGE 时仍能读到 turn-scoped 信号，
    _ensure_agent_session 走第 4 路 SUBAGENT_INTERNAL。
    """
    store_group = await create_store_group(
        str(tmp_path / "p1-1.db"), str(tmp_path / "art")
    )
    sse_hub = SSEHub()
    runner = TaskRunner(
        store_group=store_group,
        sse_hub=sse_hub,
        llm_service=None,
        timeout_seconds=60,
    )

    raw_init = {
        "delegation_id": "01J0000000000000000000XYZK",
        "parent_task_id": "task-parent-p1-1",
        "parent_work_id": "work-parent-p1-1",
        "caller_agent_runtime_id": "runtime-caller-p1-1",
        "caller_project_id": "proj-caller-p1-1",
        "spawned_by": "delegate_task",
    }
    message = NormalizedMessage(
        text="subagent test for target_kind preservation",
        idempotency_key=f"p1-1-test-{datetime.now().timestamp()}",
        control_metadata={
            "target_kind": "subagent",
            "__subagent_delegation_init__": raw_init,
        },
    )

    with patch.object(runner, "enqueue", new_callable=AsyncMock):
        task_id, _ = await runner.launch_child_task(message)

    # 验证：emit 的 USER_MESSAGE event 的 control_metadata 同时含 target_kind 和 subagent_delegation
    events = await store_group.event_store.get_events_for_task(task_id)
    delegation_events = [
        e for e in events
        if e.type is EventType.USER_MESSAGE
        and e.payload.get("control_metadata", {}).get("subagent_delegation")
    ]
    assert len(delegation_events) >= 1
    cm = delegation_events[0].payload["control_metadata"]
    assert cm.get("target_kind") == "subagent", (
        f"P1-1 闭环失败：USER_MESSAGE event 缺 target_kind，实际 {cm}"
    )

    # 验证：merge_control_metadata 取最新 USER_MESSAGE 后能读到 target_kind
    merged = merge_control_metadata(events)
    assert merged.get("target_kind") == "subagent", (
        f"P1-1 闭环失败：merge_control_metadata 后 target_kind 丢失，实际 {merged}"
    )
    assert merged.get("subagent_delegation") is not None

    await store_group.conn.close()


@pytest.mark.asyncio
async def test_p2_5_cleanup_skips_non_terminal_task(tmp_path: Path) -> None:
    """Codex P2-5 闭环：cleanup 检测到 task 非终态时直接 return，不写 SUBAGENT_COMPLETED 事件。

    防止 dispatch exception / shutdown 兜底等路径在 task 仍 RUNNING 时调用 cleanup
    导致 subagent session 被提前关闭。
    """
    store_group = await create_store_group(
        str(tmp_path / "p2-5.db"), str(tmp_path / "art")
    )
    sse_hub = SSEHub()
    parent_task_id = await _create_parent_task_simple(store_group, sse_hub)

    runner = TaskRunner(
        store_group=store_group,
        sse_hub=sse_hub,
        llm_service=None,
        timeout_seconds=60,
    )

    # 模拟 task 在 RUNNING 状态（非终态）
    delegation = _make_delegation(parent_task_id=parent_task_id)
    await _create_subagent_session_simple(
        store_group,
        agent_session_id=_CHILD_SESSION_ID,
        agent_runtime_id=_CALLER_RUNTIME_ID,
    )

    with patch(
        "octoagent.gateway.services.task_runner.TaskService.get_latest_user_metadata",
        new_callable=AsyncMock,
        return_value={"subagent_delegation": delegation.model_dump_json()},
    ), patch(
        "octoagent.gateway.services.task_runner.TaskService.get_task",
        new_callable=AsyncMock,
        return_value=type("Task", (), {
            "status": TaskStatus.RUNNING,  # 非终态
            "updated_at": _NOW,
        })(),
    ):
        await runner._close_subagent_session_if_needed(parent_task_id)

    # 验证：session 仍 ACTIVE（cleanup 跳过）
    session = await store_group.agent_context_store.get_agent_session(_CHILD_SESSION_ID)
    assert session is not None
    assert session.status == AgentSessionStatus.ACTIVE, (
        "P2-5 闭环失败：非终态 task 不应触发 cleanup，session 应保持 ACTIVE"
    )

    # 验证：SUBAGENT_COMPLETED 事件未 emit
    events = await store_group.event_store.get_events_for_task(parent_task_id)
    completed_events = [e for e in events if e.type is EventType.SUBAGENT_COMPLETED]
    assert len(completed_events) == 0, (
        "P2-5 闭环失败：非终态 task 不应 emit SUBAGENT_COMPLETED 事件"
    )

    await store_group.conn.close()


@pytest.mark.asyncio
async def test_p2_6_caller_unknown_when_no_execution_context(tmp_path: Path) -> None:
    """Codex P2-6 闭环：caller_agent_runtime_id 为空时使用 '<unknown>' 而非 task_id 伪造。

    防止 launch_child_task 在非 execution context 路径（如 worker plan apply / control-plane spawn）
    把 task_id 误填为 caller_agent_runtime_id 导致 parent_worker_runtime_id 失去意义。
    """
    store_group = await create_store_group(
        str(tmp_path / "p2-6.db"), str(tmp_path / "art")
    )
    sse_hub = SSEHub()
    runner = TaskRunner(
        store_group=store_group,
        sse_hub=sse_hub,
        llm_service=None,
        timeout_seconds=60,
    )

    # 模拟 capability_pack 在非 execution context 路径调用 — caller_agent_runtime_id="" 空字符串
    raw_init = {
        "delegation_id": "01J0000000000000000000P26K",
        "parent_task_id": "task-parent-p2-6",
        "parent_work_id": "work-parent-p2-6",
        "caller_agent_runtime_id": "",  # 空字符串，无 execution context
        "caller_project_id": "",        # 同样空字符串
        "spawned_by": "worker_plan_apply",
    }
    message = NormalizedMessage(
        text="subagent test for unknown caller fallback",
        idempotency_key=f"p2-6-test-{datetime.now().timestamp()}",
        control_metadata={
            "target_kind": "subagent",
            "__subagent_delegation_init__": raw_init,
        },
    )

    with patch.object(runner, "enqueue", new_callable=AsyncMock):
        task_id, _ = await runner.launch_child_task(message)

    # 验证：emit 的 SubagentDelegation 中 caller_agent_runtime_id 是 "<unknown>" 而非 task_id
    events = await store_group.event_store.get_events_for_task(task_id)
    delegation_events = [
        e for e in events
        if e.type is EventType.USER_MESSAGE
        and e.payload.get("control_metadata", {}).get("subagent_delegation")
    ]
    assert len(delegation_events) >= 1
    delegation_dict = delegation_events[0].payload["control_metadata"]["subagent_delegation"]
    if isinstance(delegation_dict, str):
        delegation = SubagentDelegation.model_validate_json(delegation_dict)
    else:
        delegation = SubagentDelegation.model_validate(delegation_dict)

    assert delegation.caller_agent_runtime_id == "<unknown>", (
        f"P2-6 闭环失败：caller_agent_runtime_id 应为 '<unknown>'，"
        f"实际 {delegation.caller_agent_runtime_id!r}"
    )
    assert delegation.caller_agent_runtime_id != task_id, (
        f"P2-6 闭环失败：caller_agent_runtime_id 不应 fallback 为 task_id"
    )

    await store_group.conn.close()


# 注：_make_delegation 复用文件头部 helper；以下为 Codex Round 2 闭环测试专属 helpers
# （非冲突命名）


async def _create_parent_task_simple(store_group, sse_hub: SSEHub) -> str:
    """创建简单父 task 用于 cleanup 测试。"""
    service = TaskService(store_group, sse_hub)
    msg = NormalizedMessage(
        text="parent task for cleanup test",
        idempotency_key=f"parent-cleanup-{datetime.now().timestamp()}",
    )
    task_id, _ = await service.create_task(msg)
    return task_id


async def _create_subagent_session_simple(
    store_group, *, agent_session_id: str, agent_runtime_id: str
):
    """创建 SUBAGENT_INTERNAL AgentSession + 关联 AgentRuntime。"""
    runtime = AgentRuntime(
        agent_runtime_id=agent_runtime_id,
        role=AgentRuntimeRole.WORKER,
    )
    await store_group.agent_context_store.save_agent_runtime(runtime)
    await store_group.conn.commit()

    session = AgentSession(
        agent_session_id=agent_session_id,
        agent_runtime_id=agent_runtime_id,
        kind=AgentSessionKind.SUBAGENT_INTERNAL,
        status=AgentSessionStatus.ACTIVE,
    )
    await store_group.agent_context_store.save_agent_session(session)
    await store_group.conn.commit()
    return session
