"""F097 Phase E: _close_subagent_session_if_needed 单测。

覆盖：
- AC-E1: succeeded / failed / cancelled 三态触发 cleanup → session.CLOSED + delegation.closed_at 同步
- AC-E2: 幂等（重复调用不报错，closed_at 保持首次值）
- AC-E3: RecallFrame 保留（不删除，list_recall_frames 仍返回数据）
- AC-EVENT-1: SUBAGENT_COMPLETED 事件写入 EventStore，payload 字段正确
- 非 subagent task: 无 subagent_delegation 时 return noop
- child_agent_session_id=None: spawn 失败场景 return
- 异常隔离: 内部异常 log warn 不向上传播
- Phase B 兼容: session 不存在时（Phase B 完成前）cleanup 静默跳过
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from octoagent.core.models import (
    AgentSessionStatus,
    EventType,
    NormalizedMessage,
    SubagentDelegation,
)
from octoagent.core.models.agent_context import (
    AgentRuntime,
    AgentRuntimeRole,
    AgentSession,
    AgentSessionKind,
)
from octoagent.core.models.enums import ActorType, TaskStatus
from octoagent.core.store import create_store_group
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.gateway.services.task_runner import TaskRunner
from octoagent.gateway.services.task_service import TaskService


# ---------------------------------------------------------------------------
# 辅助工具
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 5, 10, 12, 0, 0, tzinfo=UTC)
_DELEGATION_ID = "01J0000000000000000000DELA"
_PARENT_TASK_ID_PLACEHOLDER = ""  # 将从创建的任务中填充
_CHILD_TASK_ID = "01J0000000000000000000CHLD"
_CHILD_SESSION_ID = "session-subagent-e2e-001"
_CALLER_RUNTIME_ID = "runtime-caller-001"
_CALLER_PROJECT_ID = "proj-caller-001"
_PARENT_WORK_ID = "work-parent-001"
_SPAWNED_BY = "delegate_task"


def _make_delegation(
    *,
    parent_task_id: str,
    child_agent_session_id: str | None = _CHILD_SESSION_ID,
    closed_at: datetime | None = None,
) -> SubagentDelegation:
    """构造测试用 SubagentDelegation。"""
    return SubagentDelegation(
        delegation_id=_DELEGATION_ID,
        parent_task_id=parent_task_id,
        parent_work_id=_PARENT_WORK_ID,
        child_task_id=_CHILD_TASK_ID,
        child_agent_session_id=child_agent_session_id,
        caller_agent_runtime_id=_CALLER_RUNTIME_ID,
        caller_project_id=_CALLER_PROJECT_ID,
        spawned_by=_SPAWNED_BY,
        created_at=_NOW,
        closed_at=closed_at,
    )


async def _create_parent_task(store_group, sse_hub: SSEHub) -> str:
    """创建父任务并返回 task_id（用于 EventStore parent_task_id）。"""
    service = TaskService(store_group, sse_hub)
    msg = NormalizedMessage(
        text="parent task for subagent cleanup test",
        idempotency_key=f"parent-{datetime.now().timestamp()}",
    )
    task_id, _ = await service.create_task(msg)
    return task_id


async def _create_runner(store_group, sse_hub: SSEHub) -> TaskRunner:
    """创建最小 TaskRunner（无 LLM service，不用于执行任务）。"""
    return TaskRunner(
        store_group=store_group,
        sse_hub=sse_hub,
        llm_service=None,
        timeout_seconds=60,
    )


async def _create_subagent_session(store_group, *, agent_session_id: str, agent_runtime_id: str) -> AgentSession:
    """在 store 中创建一个 SUBAGENT_INTERNAL AgentSession（Phase B 完成后才会真实存在，测试用）。"""
    # 首先创建 AgentRuntime（agent_session 需要 FK 关联）
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


async def _get_subagent_completed_events(store_group, task_id: str) -> list:
    """从 EventStore 查询 SUBAGENT_COMPLETED 事件列表。"""
    events = await store_group.event_store.get_events_for_task(task_id)
    return [e for e in events if e.type is EventType.SUBAGENT_COMPLETED]


# ---------------------------------------------------------------------------
# TE.5.1: AC-E1 — succeeded 终态触发 cleanup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_on_succeeded_closes_session(tmp_path: Path) -> None:
    """AC-E1: child 进入 succeeded 终态 → SUBAGENT_INTERNAL session status=CLOSED + closed_at 填充。"""
    store_group = await create_store_group(str(tmp_path / "e-01.db"), str(tmp_path / "art"))
    sse_hub = SSEHub()

    parent_task_id = await _create_parent_task(store_group, sse_hub)
    runner = await _create_runner(store_group, sse_hub)

    delegation = _make_delegation(parent_task_id=parent_task_id)
    await _create_subagent_session(
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
            "status": TaskStatus.SUCCEEDED,
            "updated_at": _NOW,
        })(),
    ):
        await runner._close_subagent_session_if_needed(parent_task_id)

    session = await store_group.agent_context_store.get_agent_session(_CHILD_SESSION_ID)
    assert session is not None
    assert session.status == AgentSessionStatus.CLOSED
    assert session.closed_at is not None

    await store_group.conn.close()


# ---------------------------------------------------------------------------
# TE.5.2: AC-E1 — failed 终态触发 cleanup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_on_failed_closes_session(tmp_path: Path) -> None:
    """AC-E1: child 进入 failed 终态 → session status=CLOSED。"""
    store_group = await create_store_group(str(tmp_path / "e-02.db"), str(tmp_path / "art"))
    sse_hub = SSEHub()

    parent_task_id = await _create_parent_task(store_group, sse_hub)
    runner = await _create_runner(store_group, sse_hub)

    delegation = _make_delegation(parent_task_id=parent_task_id)
    await _create_subagent_session(
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
            "status": TaskStatus.FAILED,
            "updated_at": _NOW,
        })(),
    ):
        await runner._close_subagent_session_if_needed(parent_task_id)

    session = await store_group.agent_context_store.get_agent_session(_CHILD_SESSION_ID)
    assert session is not None
    assert session.status == AgentSessionStatus.CLOSED

    await store_group.conn.close()


# ---------------------------------------------------------------------------
# TE.5.3: AC-E1 — cancelled 终态触发 cleanup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_on_cancelled_closes_session(tmp_path: Path) -> None:
    """AC-E1: child 进入 cancelled 终态 → session status=CLOSED。"""
    store_group = await create_store_group(str(tmp_path / "e-03.db"), str(tmp_path / "art"))
    sse_hub = SSEHub()

    parent_task_id = await _create_parent_task(store_group, sse_hub)
    runner = await _create_runner(store_group, sse_hub)

    delegation = _make_delegation(parent_task_id=parent_task_id)
    await _create_subagent_session(
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
            "status": TaskStatus.CANCELLED,
            "updated_at": _NOW,
        })(),
    ):
        await runner._close_subagent_session_if_needed(parent_task_id)

    session = await store_group.agent_context_store.get_agent_session(_CHILD_SESSION_ID)
    assert session is not None
    assert session.status == AgentSessionStatus.CLOSED

    await store_group.conn.close()


# ---------------------------------------------------------------------------
# TE.5.4: AC-E2 — 幂等：重复调用不报错，closed_at 保持首次值
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_idempotent_already_closed_session(tmp_path: Path) -> None:
    """AC-E2: 对已 CLOSED session 重复调用不报错，closed_at 保持首次值。"""
    store_group = await create_store_group(str(tmp_path / "e-04.db"), str(tmp_path / "art"))
    sse_hub = SSEHub()

    parent_task_id = await _create_parent_task(store_group, sse_hub)
    runner = await _create_runner(store_group, sse_hub)

    # 第一次 cleanup 时 delegation.closed_at=None（触发真实 cleanup）
    delegation = _make_delegation(parent_task_id=parent_task_id)
    await _create_subagent_session(
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
            "status": TaskStatus.SUCCEEDED,
            "updated_at": _NOW,
        })(),
    ):
        await runner._close_subagent_session_if_needed(parent_task_id)

    session_after_first = await store_group.agent_context_store.get_agent_session(_CHILD_SESSION_ID)
    assert session_after_first is not None
    assert session_after_first.status == AgentSessionStatus.CLOSED
    first_closed_at = session_after_first.closed_at

    # 第二次 cleanup：delegation.closed_at 已设置（幂等保护 step 4 触发 early return）
    later_time = datetime(2026, 5, 10, 14, 0, 0, tzinfo=UTC)
    delegation_closed = _make_delegation(parent_task_id=parent_task_id, closed_at=_NOW)
    with patch(
        "octoagent.gateway.services.task_runner.TaskService.get_latest_user_metadata",
        new_callable=AsyncMock,
        return_value={"subagent_delegation": delegation_closed.model_dump_json()},
    ), patch(
        "octoagent.gateway.services.task_runner.TaskService.get_task",
        new_callable=AsyncMock,
        return_value=type("Task", (), {
            "status": TaskStatus.SUCCEEDED,
            "updated_at": later_time,
        })(),
    ):
        # 第二次调用不应 raise
        await runner._close_subagent_session_if_needed(parent_task_id)

    session_after_second = await store_group.agent_context_store.get_agent_session(_CHILD_SESSION_ID)
    assert session_after_second is not None
    # session 不应被第二次调用修改（幂等保证）
    assert session_after_second.status == AgentSessionStatus.CLOSED
    # closed_at 应与第一次一致（delegation.closed_at 已设置，early return 在 session save 之前）
    assert session_after_second.closed_at == first_closed_at

    await store_group.conn.close()


# ---------------------------------------------------------------------------
# TE.5.5: AC-E3 — RecallFrame 保留（不删除）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_preserves_recall_frames(tmp_path: Path) -> None:
    """AC-E3: session 关闭后 RecallFrame 记录仍然存在，不被删除。

    当前 Phase E 实现：cleanup 只操作 AgentSession，不触碰 RecallFrame 表。
    此测试验证 cleanup 后仍可（通过 agent_runtime_id）检索到已有 RecallFrame。
    由于 Phase B/F 完成前没有真实 Subagent RecallFrame，
    此测试创建一个普通 RecallFrame（agent_runtime_id=caller）验证不被删除。
    """
    from octoagent.core.models import AgentSession as AS
    from octoagent.core.models.agent_context import RecallFrame

    store_group = await create_store_group(str(tmp_path / "e-05.db"), str(tmp_path / "art"))
    sse_hub = SSEHub()

    parent_task_id = await _create_parent_task(store_group, sse_hub)
    runner = await _create_runner(store_group, sse_hub)

    await _create_subagent_session(
        store_group,
        agent_session_id=_CHILD_SESSION_ID,
        agent_runtime_id=_CALLER_RUNTIME_ID,
    )

    # 创建一个 RecallFrame（验证 cleanup 不删除）
    recall_frame = RecallFrame(
        recall_frame_id="recall-phase-e-001",
        task_id=parent_task_id,
        agent_runtime_id=_CALLER_RUNTIME_ID,
        query="test recall query",
        context_frame_id="cf-001",
    )
    await store_group.agent_context_store.save_recall_frame(recall_frame)
    await store_group.conn.commit()

    delegation = _make_delegation(parent_task_id=parent_task_id)

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

    # 验证 RecallFrame 仍然存在（通过 agent_runtime_id 过滤）
    frames = await store_group.agent_context_store.list_recall_frames(
        agent_runtime_id=_CALLER_RUNTIME_ID
    )
    assert len(frames) >= 1, "cleanup 后 RecallFrame 不应被删除（AC-E3）"
    frame_ids = [f.recall_frame_id for f in frames]
    assert "recall-phase-e-001" in frame_ids

    await store_group.conn.close()


# ---------------------------------------------------------------------------
# TE.5.6: 非 subagent task — 无 subagent_delegation 时 return noop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_noop_for_non_subagent_task(tmp_path: Path) -> None:
    """非 subagent task：get_latest_user_metadata 不含 subagent_delegation → cleanup return noop。"""
    store_group = await create_store_group(str(tmp_path / "e-06.db"), str(tmp_path / "art"))
    sse_hub = SSEHub()
    runner = await _create_runner(store_group, sse_hub)

    with patch(
        "octoagent.gateway.services.task_runner.TaskService.get_latest_user_metadata",
        new_callable=AsyncMock,
        return_value={"tool_profile": "standard"},  # 普通任务，无 subagent_delegation
    ):
        # 不应 raise，不应 emit 任何事件
        await runner._close_subagent_session_if_needed("task-non-subagent-001")

    await store_group.conn.close()


# ---------------------------------------------------------------------------
# TE.5.7: child_agent_session_id=None — spawn 失败场景 cleanup return
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_skips_when_no_child_session_id(tmp_path: Path) -> None:
    """child_agent_session_id=None: spawn 失败场景，cleanup 直接 return（不尝试查 session）。"""
    store_group = await create_store_group(str(tmp_path / "e-07.db"), str(tmp_path / "art"))
    sse_hub = SSEHub()

    parent_task_id = await _create_parent_task(store_group, sse_hub)
    runner = await _create_runner(store_group, sse_hub)

    # child_agent_session_id=None 表示 spawn 失败
    delegation = _make_delegation(parent_task_id=parent_task_id, child_agent_session_id=None)

    with patch(
        "octoagent.gateway.services.task_runner.TaskService.get_latest_user_metadata",
        new_callable=AsyncMock,
        return_value={"subagent_delegation": delegation.model_dump_json()},
    ):
        # 不应 raise，也不调用 get_agent_session
        await runner._close_subagent_session_if_needed(parent_task_id)

    await store_group.conn.close()


# ---------------------------------------------------------------------------
# TE.5.8: AC-EVENT-1 — SUBAGENT_COMPLETED 事件写入 EventStore，payload 字段正确
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subagent_completed_event_emitted(tmp_path: Path) -> None:
    """AC-EVENT-1: cleanup 后 EventStore 中含 SUBAGENT_COMPLETED 事件，payload 字段正确。"""
    store_group = await create_store_group(str(tmp_path / "e-08.db"), str(tmp_path / "art"))
    sse_hub = SSEHub()

    parent_task_id = await _create_parent_task(store_group, sse_hub)
    runner = await _create_runner(store_group, sse_hub)

    delegation = _make_delegation(parent_task_id=parent_task_id)
    await _create_subagent_session(
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
            "status": TaskStatus.SUCCEEDED,
            "updated_at": _NOW,
        })(),
    ):
        await runner._close_subagent_session_if_needed(parent_task_id)

    # 验证 SUBAGENT_COMPLETED 事件已写入 parent_task_id 的事件流
    events = await _get_subagent_completed_events(store_group, parent_task_id)
    assert len(events) == 1, f"应有 1 条 SUBAGENT_COMPLETED 事件，实际: {len(events)}"

    evt = events[0]
    payload = evt.payload
    assert payload["delegation_id"] == _DELEGATION_ID
    assert payload["child_task_id"] == _CHILD_TASK_ID
    assert payload["terminal_status"] == "SUCCEEDED"  # TaskStatus.SUCCEEDED.value = "SUCCEEDED"（大写）
    assert payload["parent_task_id"] == parent_task_id
    assert payload["child_agent_session_id"] == _CHILD_SESSION_ID

    await store_group.conn.close()


# ---------------------------------------------------------------------------
# TE.5.9: 异常隔离 — 内部异常 log warn 不向上传播
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_exception_does_not_propagate(tmp_path: Path) -> None:
    """异常隔离: store 层异常不向上传播（cleanup 内 try-except 隔离）。"""
    store_group = await create_store_group(str(tmp_path / "e-09.db"), str(tmp_path / "art"))
    sse_hub = SSEHub()
    runner = await _create_runner(store_group, sse_hub)

    # 注入异常到 get_latest_user_metadata
    with patch(
        "octoagent.gateway.services.task_runner.TaskService.get_latest_user_metadata",
        new_callable=AsyncMock,
        side_effect=RuntimeError("store 层模拟错误"),
    ):
        # 不应 raise，异常应被 try-except 捕获并 log warn
        await runner._close_subagent_session_if_needed("task-exception-001")

    await store_group.conn.close()


# ---------------------------------------------------------------------------
# TE.5.10: Phase B 兼容 — session 不存在时（Phase B 完成前）cleanup 静默跳过
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_skips_when_session_not_found(tmp_path: Path) -> None:
    """Phase B 兼容: SUBAGENT_INTERNAL session 当前不存在（Phase B 完成前），cleanup 静默跳过 session save。

    仍然 emit SUBAGENT_COMPLETED 事件（因为 delegation 有效、delegation.closed_at=None）。
    但不 raise，也不尝试 model_copy 一个 None session。
    """
    store_group = await create_store_group(str(tmp_path / "e-10.db"), str(tmp_path / "art"))
    sse_hub = SSEHub()

    parent_task_id = await _create_parent_task(store_group, sse_hub)
    runner = await _create_runner(store_group, sse_hub)

    # child_agent_session_id 填写了 ID，但实际 store 中没有这个 session（Phase B 前）
    delegation = _make_delegation(parent_task_id=parent_task_id, child_agent_session_id="session-not-exist-phase-b")

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
        # 不应 raise（session=None 时 if 判断 False，跳过 save + commit）
        await runner._close_subagent_session_if_needed(parent_task_id)

    # session 不存在，应静默跳过（不应抛异常）
    session = await store_group.agent_context_store.get_agent_session("session-not-exist-phase-b")
    assert session is None  # 确认没有被意外创建

    await store_group.conn.close()


# ---------------------------------------------------------------------------
# Codex P1-1 闭环：normalize_control_metadata 保留 subagent_delegation key
# ---------------------------------------------------------------------------


def test_normalize_control_metadata_preserves_subagent_delegation():
    """Codex P1-1 闭环：subagent_delegation 必须在 TASK_SCOPED_CONTROL_KEYS 白名单中。

    若白名单缺失，normalize 会丢弃 subagent_delegation key，cleanup 在真实 spawn 路径
    永远 return noop（永远拿不到 delegation 数据）。
    此测试守护：normalize 必须保留 subagent_delegation 不变。
    """
    from octoagent.gateway.services.connection_metadata import (
        TASK_SCOPED_CONTROL_KEYS,
        normalize_control_metadata,
    )

    # 1. 白名单包含 subagent_delegation
    assert "subagent_delegation" in TASK_SCOPED_CONTROL_KEYS, (
        "subagent_delegation 必须在 TASK_SCOPED_CONTROL_KEYS 白名单中（Codex P1-1）"
    )

    # 2. normalize 保留 dict value 不变（结构化 SubagentDelegation 序列化结果）
    delegation_dict = {
        "delegation_id": "01J0000000000000000000DELA",
        "parent_task_id": "task-parent-1",
        "parent_work_id": "work-parent-1",
        "child_task_id": "task-child-1",
        "child_agent_session_id": "session-child-1",
        "caller_agent_runtime_id": "runtime-caller-1",
        "caller_project_id": "proj-caller-1",
        "caller_memory_namespace_ids": [],
        "spawned_by": "delegate_task",
        "target_kind": "subagent",
        "created_at": "2026-05-10T12:00:00+00:00",
        "closed_at": None,
    }
    raw = {"subagent_delegation": delegation_dict, "tool_profile": "standard"}
    normalized = normalize_control_metadata(raw)

    assert "subagent_delegation" in normalized, "subagent_delegation 必须被 normalize 保留"
    assert normalized["subagent_delegation"] == delegation_dict, (
        "normalize 必须原样保留结构化 dict value（不展开/不修改）"
    )
    # 同时 tool_profile 仍被正常 normalize（regression）
    assert normalized["tool_profile"] == "standard"


def test_normalize_control_metadata_preserves_subagent_delegation_json_string():
    """Codex P1-1 闭环：normalize 也应保留 SubagentDelegation 的 JSON 字符串形式。

    cleanup 路径同时支持 dict（model_validate）和 string（model_validate_json）反序列化，
    所以 normalize 必须对两种 value 形态都不破坏。
    """
    from octoagent.gateway.services.connection_metadata import normalize_control_metadata

    # JSON string 形式（spawn 路径若选择直接 dump_json 写入）
    json_str = '{"delegation_id":"d","child_task_id":"c"}'
    raw = {"subagent_delegation": json_str}
    normalized = normalize_control_metadata(raw)

    assert "subagent_delegation" in normalized
    # str value 会被 strip()（line 112-113 of connection_metadata.py），但内容不变
    assert normalized["subagent_delegation"] == json_str.strip()


# ---------------------------------------------------------------------------
# Codex P1-2 闭环：EventStore.check_idempotency_key 防重复 emit SUBAGENT_COMPLETED
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_idempotent_via_event_store_check(tmp_path: Path) -> None:
    """Codex P1-2 闭环：重复触发 cleanup 时通过 EventStore.check_idempotency_key 短路。

    场景：进程重启后再次进入终态分支，delegation.closed_at 仍是 None（未持久化），
    传统的 step 4 (delegation.closed_at != None) 幂等检查失效。新增 step 6 用
    EventStore.check_idempotency_key 防止重复 emit SUBAGENT_COMPLETED 事件。

    此测试验证：
    - 第一次 cleanup 后 EventStore 写入 SUBAGENT_COMPLETED 事件 + idempotency_key
    - 第二次 cleanup 同样 delegation（closed_at=None）应通过 idempotency_key 短路
    - 不重复 emit 事件
    """
    store_group = await create_store_group(
        str(tmp_path / "p1-2-idempotency.db"), str(tmp_path / "art")
    )
    sse_hub = SSEHub()

    parent_task_id = await _create_parent_task(store_group, sse_hub)
    runner = await _create_runner(store_group, sse_hub)

    # 模拟 spawn 路径：创建 SUBAGENT_INTERNAL session
    delegation = _make_delegation(parent_task_id=parent_task_id)
    await _create_subagent_session(
        store_group,
        agent_session_id=_CHILD_SESSION_ID,
        agent_runtime_id=_CALLER_RUNTIME_ID,
    )

    # 模拟传入相同的 delegation（closed_at 始终是 None — 模拟未持久化场景）
    metadata_return = {"subagent_delegation": delegation.model_dump_json()}
    task_return = type("Task", (), {
        "status": TaskStatus.SUCCEEDED,
        "updated_at": _NOW,
    })()

    # 第一次 cleanup
    with patch(
        "octoagent.gateway.services.task_runner.TaskService.get_latest_user_metadata",
        new_callable=AsyncMock,
        return_value=metadata_return,
    ), patch(
        "octoagent.gateway.services.task_runner.TaskService.get_task",
        new_callable=AsyncMock,
        return_value=task_return,
    ):
        await runner._close_subagent_session_if_needed(parent_task_id)

    events_first = await _get_subagent_completed_events(store_group, parent_task_id)
    assert len(events_first) == 1, "首次 cleanup 应 emit 1 个 SUBAGENT_COMPLETED 事件"
    first_event = events_first[0]
    expected_key = f"subagent_completed:{delegation.delegation_id}"
    assert first_event.causality.idempotency_key == expected_key, (
        f"事件 idempotency_key 应为 {expected_key!r}"
    )

    # 第二次 cleanup（同样 delegation，closed_at 仍是 None — 模拟进程重启或 _notify_completion 多次调用）
    with patch(
        "octoagent.gateway.services.task_runner.TaskService.get_latest_user_metadata",
        new_callable=AsyncMock,
        return_value=metadata_return,
    ), patch(
        "octoagent.gateway.services.task_runner.TaskService.get_task",
        new_callable=AsyncMock,
        return_value=task_return,
    ):
        await runner._close_subagent_session_if_needed(parent_task_id)

    # 验证：第二次 cleanup 通过 EventStore.check_idempotency_key 短路，不重复 emit
    events_second = await _get_subagent_completed_events(store_group, parent_task_id)
    assert len(events_second) == 1, (
        "第二次 cleanup 应通过 EventStore idempotency_key 短路，"
        f"不重复 emit；实际事件数：{len(events_second)}"
    )
    # 同一事件还在
    assert events_second[0].event_id == first_event.event_id

    await store_group.conn.close()
