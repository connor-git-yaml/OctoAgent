"""F098 Phase H: 终态 callback 注册机制 + 幂等 + 生命周期单测（Codex review P2 闭环）。

测试场景：
- AC-H1: TaskService._write_state_transition 终态触发 callback
- AC-H2: callback 注册 + 终态触发集成
- AC-H5: callback 异常 → state transition 仍成功（异常隔离）
- AC-H6: 重复 register 同一 callback 触发次数仍为 1（幂等）
- AC-H7: TaskRunner.shutdown 后 callback list 不包含 self（生命周期）

注意：AC-H3（grep 验证 task_runner 不再有手动调用）由 Phase H 实施偏离归档：
保留 task_runner 多处手动 _close_subagent_session_if_needed 调用作为 fallback
（cleanup 内部已有幂等 + 非终态检测，与 callback 自动触发协同无冲突）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from octoagent.core.models import TaskStatus
from octoagent.core.store import create_store_group
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.gateway.services.task_service import TaskService


@pytest.fixture(autouse=True)
def reset_callbacks():
    """每个测试前后清空 class-level callback list（避免测试间串扰）。"""
    TaskService._terminal_state_callbacks.clear()
    yield
    TaskService._terminal_state_callbacks.clear()


# ---- AC-H6: 幂等注册 ----


@pytest.mark.asyncio
async def test_register_terminal_state_callback_idempotent():
    """AC-H6: 重复 register 同一 callback 仅生效一次（按 callback identity）。"""
    callback = AsyncMock()

    await TaskService.register_terminal_state_callback(callback)
    await TaskService.register_terminal_state_callback(callback)
    await TaskService.register_terminal_state_callback(callback)

    assert TaskService._terminal_state_callbacks.count(callback) == 1, (
        "AC-H6 闭环失败：重复 register 同一 callback 出现多次"
    )


@pytest.mark.asyncio
async def test_register_different_callbacks():
    """注册不同 callback 各自独立（不被幂等过滤）。"""
    callback_a = AsyncMock()
    callback_b = AsyncMock()

    await TaskService.register_terminal_state_callback(callback_a)
    await TaskService.register_terminal_state_callback(callback_b)

    assert callback_a in TaskService._terminal_state_callbacks
    assert callback_b in TaskService._terminal_state_callbacks
    assert len(TaskService._terminal_state_callbacks) == 2


# ---- AC-H7: 生命周期 unregister ----


@pytest.mark.asyncio
async def test_unregister_terminal_state_callback_removes_from_list():
    """AC-H7: unregister 后 callback list 不再包含。"""
    callback = AsyncMock()
    await TaskService.register_terminal_state_callback(callback)
    assert callback in TaskService._terminal_state_callbacks

    await TaskService.unregister_terminal_state_callback(callback)
    assert callback not in TaskService._terminal_state_callbacks


@pytest.mark.asyncio
async def test_unregister_unknown_callback_no_error():
    """unregister 未注册的 callback 不报错（幂等 unregister）。"""
    callback = AsyncMock()
    # 未 register 直接 unregister
    await TaskService.unregister_terminal_state_callback(callback)  # 不应 raise

    # 重复 unregister 也不报错
    await TaskService.register_terminal_state_callback(callback)
    await TaskService.unregister_terminal_state_callback(callback)
    await TaskService.unregister_terminal_state_callback(callback)  # 第二次也不应 raise


# ---- AC-H5: callback 异常隔离 ----


@pytest.mark.asyncio
async def test_invoke_callbacks_exception_isolation():
    """AC-H5: 一个 callback 异常不影响其他 callback + state transition。"""
    callback_ok = AsyncMock()
    callback_fail = AsyncMock(side_effect=RuntimeError("simulated failure"))

    await TaskService.register_terminal_state_callback(callback_fail)
    await TaskService.register_terminal_state_callback(callback_ok)

    # _invoke 应不 raise，即使 callback_fail 抛异常
    await TaskService._invoke_terminal_state_callbacks("task-test")

    # callback_fail 被调（异常被吞）
    callback_fail.assert_called_once_with("task-test")
    # callback_ok 仍被调（不受 callback_fail 异常影响）
    callback_ok.assert_called_once_with("task-test")


@pytest.mark.asyncio
async def test_invoke_callbacks_empty_list_no_error():
    """空 callback list 不报错。"""
    await TaskService._invoke_terminal_state_callbacks("task-test")  # 不应 raise


# ---- AC-H1/H2: _write_state_transition 终态触发 callback ----


@pytest.mark.asyncio
async def test_write_state_transition_invokes_callback_on_terminal(tmp_path: Path):
    """AC-H1+H2: _write_state_transition 在终态后触发所有 callback。"""
    store_group = await create_store_group(
        str(tmp_path / "h-1.db"), str(tmp_path / "art")
    )
    sse_hub = SSEHub()
    service = TaskService(store_group, sse_hub)

    callback_invoked = []

    async def my_callback(task_id: str) -> None:
        callback_invoked.append(task_id)

    await TaskService.register_terminal_state_callback(my_callback)

    # 创建 task → CREATED → RUNNING → COMPLETED（终态）
    from octoagent.core.models import NormalizedMessage

    message = NormalizedMessage(
        text="phase H test",
        idempotency_key=f"phase-h-test-{datetime.now(UTC).timestamp()}",
    )
    task_id, created = await service.create_task(message)
    assert created

    # CREATED → RUNNING
    await service._write_state_transition(
        task_id, TaskStatus.CREATED, TaskStatus.RUNNING, f"trace-{task_id}"
    )
    # RUNNING 不是终态，不触发 callback
    assert callback_invoked == []

    # RUNNING → COMPLETED（终态）
    await service._write_state_transition(
        task_id, TaskStatus.RUNNING, TaskStatus.SUCCEEDED, f"trace-{task_id}"
    )

    # AC-H1+H2: 终态触发了 callback
    assert task_id in callback_invoked, (
        "AC-H1+H2 闭环失败：终态后 callback 未被触发"
    )

    await store_group.conn.close()


@pytest.mark.asyncio
async def test_write_state_transition_callback_failure_does_not_break_transition(
    tmp_path: Path,
):
    """AC-H5 集成: callback 异常 → state transition 仍成功（事件已 commit）。"""
    store_group = await create_store_group(
        str(tmp_path / "h-2.db"), str(tmp_path / "art")
    )
    sse_hub = SSEHub()
    service = TaskService(store_group, sse_hub)

    async def failing_callback(task_id: str) -> None:
        raise RuntimeError("intentional callback failure")

    await TaskService.register_terminal_state_callback(failing_callback)

    from octoagent.core.models import NormalizedMessage

    message = NormalizedMessage(
        text="phase H exception test",
        idempotency_key=f"phase-h-exc-test-{datetime.now(UTC).timestamp()}",
    )
    task_id, created = await service.create_task(message)
    assert created

    await service._write_state_transition(
        task_id, TaskStatus.CREATED, TaskStatus.RUNNING, f"trace-{task_id}"
    )
    # 终态：callback raise 但 state transition 仍成功
    event = await service._write_state_transition(
        task_id, TaskStatus.RUNNING, TaskStatus.SUCCEEDED, f"trace-{task_id}"
    )
    assert event is not None, "AC-H5 闭环失败：callback 异常后 state transition event 缺失"

    # 验证 task 状态确实更新到 COMPLETED
    task = await service.get_task(task_id)
    assert task.status == TaskStatus.SUCCEEDED, (
        f"AC-H5 闭环失败：task 状态未更新到 COMPLETED，实际 {task.status}"
    )

    await store_group.conn.close()
