"""F098 Phase G: atomic 事务边界单测（P2-3 修复，OD-3 选 A）。

baseline 行为：
- F097 Phase B-4：append_event_committed (commit 1) + save_agent_session + conn.commit() (commit 2)
- 仍是 2 次 commit；event commit 后 session save 失败 → audit 事件存在但 session 永久 ACTIVE

F098 Phase G 修复：
- append_event (pending) + save_agent_session (pending) + 单一 conn.commit() = atomic
- 任一步失败 → conn.rollback 全部回滚
- idempotency_key 守护重试不重复

测试场景：
- AC-G1: cleanup 成功路径 → event + session 同事务（单 commit）
- AC-G2: idempotency_key 守护重复 cleanup 不重复 emit event
- AC-G3: cleanup 失败模式（atomic rollback）—— 通过模拟 save_agent_session 抛异常验证 rollback
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from octoagent.core.models import EventType, SubagentDelegation
from octoagent.core.models.agent_context import AgentSession, AgentSessionKind, AgentSessionStatus
from octoagent.core.store import create_store_group
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.gateway.services.task_runner import TaskRunner


_NOW = datetime(2026, 5, 10, 12, 0, 0, tzinfo=UTC)


async def _setup_completed_subagent_task(tmp_path: Path) -> tuple:
    """构造一个完整的 subagent task + delegation 状态（已完成，等待 cleanup）。"""
    from octoagent.core.models import (
        ActorType,
        Event,
        EventCausality,
        NormalizedMessage,
        TaskStatus,
    )
    from octoagent.core.models.payloads import (
        ControlMetadataUpdatedPayload,
        StateTransitionPayload,
    )
    from ulid import ULID

    store_group = await create_store_group(
        str(tmp_path / "g-test.db"), str(tmp_path / "art")
    )
    sse_hub = SSEHub()

    # 创建 parent task
    from octoagent.gateway.services.task_service import TaskService

    service = TaskService(store_group, sse_hub)
    parent_msg = NormalizedMessage(
        text="parent task g",
        idempotency_key=f"phase-g-parent-{datetime.now(UTC).timestamp()}",
    )
    parent_task_id, _ = await service.create_task(parent_msg)

    # 创建 child subagent task
    child_msg = NormalizedMessage(
        text="child subagent g",
        idempotency_key=f"phase-g-child-{datetime.now(UTC).timestamp()}",
    )
    child_task_id, _ = await service.create_task(child_msg)

    # 推进 child 到终态（让 cleanup 可触发）
    await service._write_state_transition(
        child_task_id, TaskStatus.CREATED, TaskStatus.RUNNING, f"trace-{child_task_id}"
    )
    await service._write_state_transition(
        child_task_id, TaskStatus.RUNNING, TaskStatus.SUCCEEDED, f"trace-{child_task_id}"
    )

    # 创建 ephemeral subagent session
    child_session = AgentSession(
        agent_session_id="session-subagent-g-001",
        agent_runtime_id="runtime-subagent-g-001",
        kind=AgentSessionKind.SUBAGENT_INTERNAL,
        status=AgentSessionStatus.ACTIVE,
        project_id="proj-g",
        thread_id="thread-g",
        legacy_session_id="legacy-g",
        created_at=_NOW,
        updated_at=_NOW,
    )
    await store_group.agent_context_store.save_agent_session(child_session)
    await store_group.conn.commit()

    # 写入 SubagentDelegation 到 child_task control_metadata（CONTROL_METADATA_UPDATED）
    delegation = SubagentDelegation(
        delegation_id="01J0G00000000000000000DELG",
        parent_task_id=parent_task_id,
        parent_work_id="work-parent-g",
        child_task_id=child_task_id,
        child_agent_session_id=child_session.agent_session_id,
        caller_agent_runtime_id="caller-runtime-g",
        caller_project_id="proj-g",
        caller_memory_namespace_ids=[],
        spawned_by="delegate_task",
        created_at=_NOW,
    )
    next_seq = await store_group.event_store.get_next_task_seq(child_task_id)
    delegation_event = Event(
        event_id=str(ULID()),
        task_id=child_task_id,
        task_seq=next_seq,
        ts=_NOW,
        type=EventType.CONTROL_METADATA_UPDATED,
        actor=ActorType.SYSTEM,
        payload=ControlMetadataUpdatedPayload(
            control_metadata={"subagent_delegation": delegation.model_dump(mode="json")},
            source="subagent_delegation_init",
        ).model_dump(),
        trace_id=f"trace-{child_task_id}",
        causality=EventCausality(),
    )
    await store_group.event_store.append_event_committed(delegation_event, update_task_pointer=False)

    runner = TaskRunner(
        store_group=store_group,
        sse_hub=sse_hub,
        llm_service=None,
        timeout_seconds=60,
    )
    return store_group, runner, parent_task_id, child_task_id, delegation


# ---- AC-G1: cleanup 成功 → event + session 同事务 ----


@pytest.mark.asyncio
async def test_cleanup_emits_event_and_closes_session_atomic(tmp_path: Path):
    """AC-G1: cleanup 成功路径 → SUBAGENT_COMPLETED event 和 AgentSession CLOSED 同事务。"""
    store_group, runner, parent_task_id, child_task_id, delegation = (
        await _setup_completed_subagent_task(tmp_path)
    )
    try:
        await runner._close_subagent_session_if_needed(child_task_id)

        # 验证 SUBAGENT_COMPLETED 事件 emit
        events = await store_group.event_store.get_events_for_task(parent_task_id)
        completed_events = [e for e in events if e.type is EventType.SUBAGENT_COMPLETED]
        assert len(completed_events) >= 1, "AC-G1 失败：SUBAGENT_COMPLETED 事件未 emit"

        # 验证 session 状态为 CLOSED
        session = await store_group.agent_context_store.get_agent_session(
            delegation.child_agent_session_id
        )
        assert session is not None
        assert session.status == AgentSessionStatus.CLOSED, (
            f"AC-G1 失败：session 状态未更新到 CLOSED，实际 {session.status}"
        )
    finally:
        await store_group.conn.close()


# ---- AC-G2: idempotency 守护 ----


@pytest.mark.asyncio
async def test_cleanup_idempotent_via_idempotency_key(tmp_path: Path):
    """AC-G2: 重复 cleanup 触发不重复 emit event（idempotency_key 守护）。"""
    store_group, runner, parent_task_id, child_task_id, delegation = (
        await _setup_completed_subagent_task(tmp_path)
    )
    try:
        # 首次 cleanup
        await runner._close_subagent_session_if_needed(child_task_id)
        events_after_first = await store_group.event_store.get_events_for_task(parent_task_id)
        completed_count_first = sum(
            1 for e in events_after_first if e.type is EventType.SUBAGENT_COMPLETED
        )

        # 重复 cleanup
        await runner._close_subagent_session_if_needed(child_task_id)
        events_after_second = await store_group.event_store.get_events_for_task(parent_task_id)
        completed_count_second = sum(
            1 for e in events_after_second if e.type is EventType.SUBAGENT_COMPLETED
        )

        # 第二次不重复 emit
        assert completed_count_second == completed_count_first, (
            f"AC-G2 失败：重复 cleanup 后 SUBAGENT_COMPLETED 数量增加 "
            f"({completed_count_first} → {completed_count_second})"
        )
    finally:
        await store_group.conn.close()


# ---- AC-G3: rollback on failure ----


@pytest.mark.asyncio
async def test_cleanup_rollback_on_session_save_failure(tmp_path: Path):
    """AC-G3: session.save 失败 → conn.rollback 全部撤销（event + session 一致性）。"""
    store_group, runner, parent_task_id, child_task_id, delegation = (
        await _setup_completed_subagent_task(tmp_path)
    )
    try:
        # mock save_agent_session 抛异常
        original_save = store_group.agent_context_store.save_agent_session

        async def failing_save(*args, **kwargs):
            raise RuntimeError("simulated save failure for atomic test")

        with patch.object(
            store_group.agent_context_store,
            "save_agent_session",
            side_effect=failing_save,
        ):
            # cleanup 应触发异常并被 task_runner outer try/except 捕获 + log warn 不 raise
            # （F097/F098 Phase G 异常隔离要求）
            await runner._close_subagent_session_if_needed(child_task_id)

        # rollback 后：SUBAGENT_COMPLETED 事件应不存在（atomic 全部回滚）
        events = await store_group.event_store.get_events_for_task(parent_task_id)
        completed_events = [e for e in events if e.type is EventType.SUBAGENT_COMPLETED]
        # 注意：cleanup 内 outer try/except log warn 但 atomic rollback 应已撤销 event
        assert len(completed_events) == 0, (
            f"AC-G3 失败：rollback 后 SUBAGENT_COMPLETED 事件仍存在 "
            f"（atomic 事务未生效；count={len(completed_events)}）"
        )

        # session 状态保持 ACTIVE（save 失败 + rollback）
        session = await store_group.agent_context_store.get_agent_session(
            delegation.child_agent_session_id
        )
        assert session is not None
        assert session.status == AgentSessionStatus.ACTIVE, (
            f"AC-G3 失败：rollback 后 session 状态变化（atomic 事务未生效；"
            f"实际 {session.status}）"
        )
    finally:
        await store_group.conn.close()
