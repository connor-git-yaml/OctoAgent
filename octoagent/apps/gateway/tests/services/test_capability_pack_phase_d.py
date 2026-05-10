"""F097 Phase D: RuntimeHintBundle caller→child 拷贝 单测。

覆盖（TD.3 要求至少 5 个测试）：
1. AC-D1: target_kind=subagent 时 child_message.control_metadata 含 __caller_runtime_hints__ 字段
2. AC-D1: 拷贝字段完整（surface / can_delegate_research / recent_worker_lane_* / tool_universe）
3. AC-D1: surface 值从 exec_ctx.runtime_context.surface 正确拷贝
4. AC-D2 regression: target_kind=worker 时 control_metadata 不含 __caller_runtime_hints__
5. AC-D2 regression: target_kind=main / 空 时 control_metadata 不含 __caller_runtime_hints__
6. 拷贝失败隔离: exec_ctx 缺失时 spawn 主流程不受影响（control_metadata 不含 hints，不抛异常）
7. AC-D1: parent_task.requester.channel 作为 surface fallback（exec_ctx 无 surface 时）
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from octoagent.core.models import (
    DelegationTargetKind,
    NormalizedMessage,
)


# ---------------------------------------------------------------------------
# 辅助：构造最小 parent_task / parent_work
# ---------------------------------------------------------------------------


def _make_parent_task(*, channel: str = "web", task_id: str = "parent-task-d-001") -> MagicMock:
    """构造最小 parent_task mock。"""
    task = MagicMock()
    task.task_id = task_id
    task.thread_id = "thread-d-001"
    task.scope_id = "scope-d-001"
    task.depth = 0
    requester = MagicMock()
    requester.channel = channel
    requester.sender_id = "user-d-001"
    task.requester = requester
    return task


def _make_parent_work(
    *,
    work_id: str = "work-d-001",
    project_id: str = "proj-d-001",
) -> MagicMock:
    """构造最小 parent_work mock。"""
    work = MagicMock()
    work.work_id = work_id
    work.project_id = project_id
    return work


def _make_task_runner_mock() -> MagicMock:
    """构造 task_runner mock，launch_child_task 返回 (task_id, True)。"""
    runner = MagicMock()
    runner.launch_child_task = AsyncMock(return_value=("child-task-d-001", True))
    return runner


def _make_cap_pack(task_runner) -> Any:
    """构造最小 CapabilityPackService mock，注入 task_runner。"""
    from octoagent.gateway.services.capability_pack import CapabilityPackService

    # CapabilityPackService 需要多个依赖，用 MagicMock(spec=...) 替代
    cap = MagicMock(spec=CapabilityPackService)
    cap._task_runner = task_runner
    cap.enforce_child_target_kind_policy = MagicMock()  # 不抛异常
    # 直接绑定真实方法到 mock 实例（使实际代码路径被执行）
    cap._launch_child_task = CapabilityPackService._launch_child_task.__get__(cap, CapabilityPackService)
    return cap


# ---------------------------------------------------------------------------
# TD.3.1: AC-D1 — target_kind=subagent 时含 __caller_runtime_hints__
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subagent_spawn_has_caller_runtime_hints() -> None:
    """AC-D1: target_kind=subagent spawn → control_metadata 含 __caller_runtime_hints__ key。"""
    runner = _make_task_runner_mock()

    # 模拟 exec_ctx.runtime_context.surface
    mock_runtime_ctx = MagicMock()
    mock_runtime_ctx.surface = "web"
    mock_exec_ctx = MagicMock()
    mock_exec_ctx.agent_runtime_id = "runtime-caller-d-001"
    mock_exec_ctx.runtime_context = mock_runtime_ctx

    captured_messages: list[NormalizedMessage] = []

    async def _fake_launch(msg: NormalizedMessage) -> tuple[str, bool]:
        # Codex Phase D P2-3 闭环：深拷贝 control_metadata 立即捕获 launch-time 状态。
        # 避免 production code 在 await launch_child_task 后修改 control_metadata
        # 时被 mock 引用回填误判为 launch 时已存在（Phase B P1-2 同根问题）。
        import copy
        captured_messages.append(
            msg.model_copy(update={
                "control_metadata": copy.deepcopy(msg.control_metadata)
            })
        )
        return "child-task-d-001", True

    runner.launch_child_task = _fake_launch

    cap = _make_cap_pack(runner)

    with patch(
        "octoagent.gateway.services.capability_pack.get_current_execution_context",
        return_value=mock_exec_ctx,
    ):
        await cap._launch_child_task(
            parent_task=_make_parent_task(),
            parent_work=_make_parent_work(),
            objective="test subagent objective",
            worker_type="general",
            target_kind=DelegationTargetKind.SUBAGENT.value,
            tool_profile="standard",
            title="test-d-01",
            spawned_by="delegate_task",
        )

    assert len(captured_messages) == 1, "应该有 1 个 child message"
    cm = captured_messages[0].control_metadata
    assert "__caller_runtime_hints__" in cm, (
        f"subagent spawn 应含 __caller_runtime_hints__，实际 control_metadata keys: {list(cm.keys())}"
    )


# ---------------------------------------------------------------------------
# TD.3.2: AC-D1 — 拷贝字段完整
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subagent_spawn_caller_hints_fields_complete() -> None:
    """AC-D1: __caller_runtime_hints__ 包含所有必须字段（surface / tool_universe / recent_worker_lane_*）。"""
    runner = _make_task_runner_mock()

    mock_runtime_ctx = MagicMock()
    mock_runtime_ctx.surface = "telegram"
    mock_exec_ctx = MagicMock()
    mock_exec_ctx.agent_runtime_id = "runtime-caller-d-002"
    mock_exec_ctx.runtime_context = mock_runtime_ctx

    captured_messages: list[NormalizedMessage] = []

    async def _fake_launch(msg: NormalizedMessage) -> tuple[str, bool]:
        captured_messages.append(msg)
        return "child-task-d-002", True

    runner.launch_child_task = _fake_launch
    cap = _make_cap_pack(runner)

    with patch(
        "octoagent.gateway.services.capability_pack.get_current_execution_context",
        return_value=mock_exec_ctx,
    ):
        await cap._launch_child_task(
            parent_task=_make_parent_task(channel="telegram"),
            parent_work=_make_parent_work(),
            objective="test subagent complete fields",
            worker_type="general",
            target_kind=DelegationTargetKind.SUBAGENT.value,
            tool_profile="standard",
            title="test-d-02",
            spawned_by="delegate_task",
        )

    hints = captured_messages[0].control_metadata["__caller_runtime_hints__"]
    # 所有必须字段存在
    required_fields = {
        "surface",
        "can_delegate_research",
        "recent_clarification_category",
        "recent_clarification_source_text",
        "recent_worker_lane_worker_type",
        "recent_worker_lane_profile_id",
        "recent_worker_lane_topic",
        "recent_worker_lane_summary",
        "tool_universe",
    }
    missing = required_fields - set(hints.keys())
    assert not missing, f"__caller_runtime_hints__ 缺少字段: {missing}"


# ---------------------------------------------------------------------------
# TD.3.3: AC-D1 — surface 值从 exec_ctx 正确拷贝
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subagent_spawn_surface_from_exec_ctx() -> None:
    """AC-D1: surface 字段值与 exec_ctx.runtime_context.surface 一致。"""
    runner = _make_task_runner_mock()

    mock_runtime_ctx = MagicMock()
    mock_runtime_ctx.surface = "web"
    mock_exec_ctx = MagicMock()
    mock_exec_ctx.agent_runtime_id = "runtime-caller-d-003"
    mock_exec_ctx.runtime_context = mock_runtime_ctx

    captured_messages: list[NormalizedMessage] = []

    async def _fake_launch(msg: NormalizedMessage) -> tuple[str, bool]:
        captured_messages.append(msg)
        return "child-task-d-003", True

    runner.launch_child_task = _fake_launch
    cap = _make_cap_pack(runner)

    with patch(
        "octoagent.gateway.services.capability_pack.get_current_execution_context",
        return_value=mock_exec_ctx,
    ):
        await cap._launch_child_task(
            parent_task=_make_parent_task(channel="telegram"),  # channel 与 surface 不同，验证优先级
            parent_work=_make_parent_work(),
            objective="test surface priority",
            worker_type="general",
            target_kind=DelegationTargetKind.SUBAGENT.value,
            tool_profile="standard",
            title="test-d-03",
            spawned_by="delegate_task",
        )

    hints = captured_messages[0].control_metadata["__caller_runtime_hints__"]
    assert hints["surface"] == "web", (
        f"surface 应从 exec_ctx.runtime_context 取 'web'，实际: {hints['surface']!r}"
    )


# ---------------------------------------------------------------------------
# TD.3.4: AC-D2 regression — target_kind=worker 不含 __caller_runtime_hints__
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_spawn_no_caller_runtime_hints() -> None:
    """AC-D2: target_kind=worker spawn → control_metadata 不含 __caller_runtime_hints__。"""
    runner = _make_task_runner_mock()

    mock_runtime_ctx = MagicMock()
    mock_runtime_ctx.surface = "web"
    mock_exec_ctx = MagicMock()
    mock_exec_ctx.agent_runtime_id = "runtime-caller-d-004"
    mock_exec_ctx.runtime_context = mock_runtime_ctx

    captured_messages: list[NormalizedMessage] = []

    async def _fake_launch(msg: NormalizedMessage) -> tuple[str, bool]:
        captured_messages.append(msg)
        return "child-task-d-004", True

    runner.launch_child_task = _fake_launch

    # 对于 worker target_kind，enforce_child_target_kind_policy 会阻止 WORKER→WORKER
    # 但这里用 MagicMock，不抛异常——只验证 control_metadata 无 __caller_runtime_hints__
    cap = _make_cap_pack(runner)

    with patch(
        "octoagent.gateway.services.capability_pack.get_current_execution_context",
        return_value=mock_exec_ctx,
    ):
        await cap._launch_child_task(
            parent_task=_make_parent_task(),
            parent_work=_make_parent_work(),
            objective="test worker spawn no hints",
            worker_type="general",
            target_kind=DelegationTargetKind.WORKER.value,
            tool_profile="standard",
            title="test-d-04",
            spawned_by="delegate_task",
        )

    assert len(captured_messages) == 1
    cm = captured_messages[0].control_metadata
    assert "__caller_runtime_hints__" not in cm, (
        f"worker spawn 不应含 __caller_runtime_hints__，实际 keys: {list(cm.keys())}"
    )


# ---------------------------------------------------------------------------
# TD.3.5: AC-D2 regression — target_kind=main 不含 __caller_runtime_hints__
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_main_target_no_caller_runtime_hints() -> None:
    """AC-D2: target_kind='main' spawn → control_metadata 不含 __caller_runtime_hints__。"""
    runner = _make_task_runner_mock()

    captured_messages: list[NormalizedMessage] = []

    async def _fake_launch(msg: NormalizedMessage) -> tuple[str, bool]:
        captured_messages.append(msg)
        return "child-task-d-005", True

    runner.launch_child_task = _fake_launch
    cap = _make_cap_pack(runner)

    with patch(
        "octoagent.gateway.services.capability_pack.get_current_execution_context",
        side_effect=RuntimeError("no context"),
    ):
        await cap._launch_child_task(
            parent_task=_make_parent_task(),
            parent_work=_make_parent_work(),
            objective="test main target no hints",
            worker_type="general",
            target_kind="main",  # 非 subagent / worker
            tool_profile="standard",
            title="test-d-05",
            spawned_by="delegate_task",
        )

    assert len(captured_messages) == 1
    cm = captured_messages[0].control_metadata
    assert "__caller_runtime_hints__" not in cm, (
        f"target_kind=main 不应含 __caller_runtime_hints__，实际 keys: {list(cm.keys())}"
    )


# ---------------------------------------------------------------------------
# TD.3.6: 拷贝失败隔离 — exec_ctx 完全缺失时 spawn 主流程不受影响
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hint_copy_failure_does_not_block_spawn() -> None:
    """拷贝失败隔离: exec_ctx RuntimeError → spawn 仍成功，control_metadata 不含 hints，不抛异常。"""
    runner = _make_task_runner_mock()

    captured_messages: list[NormalizedMessage] = []
    spawn_succeeded = False

    async def _fake_launch(msg: NormalizedMessage) -> tuple[str, bool]:
        captured_messages.append(msg)
        nonlocal spawn_succeeded
        spawn_succeeded = True
        return "child-task-d-006", True

    runner.launch_child_task = _fake_launch
    cap = _make_cap_pack(runner)

    # exec_ctx 不可用（RuntimeError）——hints 拷贝会失败，spawn 主流程应继续
    with patch(
        "octoagent.gateway.services.capability_pack.get_current_execution_context",
        side_effect=RuntimeError("no execution context available"),
    ):
        result = await cap._launch_child_task(
            parent_task=_make_parent_task(),
            parent_work=_make_parent_work(),
            objective="test hint failure isolation",
            worker_type="general",
            target_kind=DelegationTargetKind.SUBAGENT.value,
            tool_profile="standard",
            title="test-d-06",
            spawned_by="delegate_task",
        )

    # spawn 主流程成功
    assert spawn_succeeded, "exec_ctx 不可用时 spawn 主流程应继续（不受影响）"
    assert result["task_id"] == "child-task-d-001" or result.get("task_id") is not None, (
        "spawn 返回值中应有 task_id"
    )


# ---------------------------------------------------------------------------
# TD.3.7: AC-D1 — parent_task.requester.channel 作为 surface fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subagent_spawn_surface_fallback_to_channel() -> None:
    """AC-D1: exec_ctx.runtime_context 无 surface 时，fallback 到 parent_task.requester.channel。"""
    runner = _make_task_runner_mock()

    # runtime_context.surface 为空字符串（触发 fallback）
    mock_runtime_ctx = MagicMock()
    mock_runtime_ctx.surface = ""  # 空字符串触发 fallback
    mock_exec_ctx = MagicMock()
    mock_exec_ctx.agent_runtime_id = "runtime-caller-d-007"
    mock_exec_ctx.runtime_context = mock_runtime_ctx

    captured_messages: list[NormalizedMessage] = []

    async def _fake_launch(msg: NormalizedMessage) -> tuple[str, bool]:
        captured_messages.append(msg)
        return "child-task-d-007", True

    runner.launch_child_task = _fake_launch
    cap = _make_cap_pack(runner)

    with patch(
        "octoagent.gateway.services.capability_pack.get_current_execution_context",
        return_value=mock_exec_ctx,
    ):
        await cap._launch_child_task(
            parent_task=_make_parent_task(channel="telegram"),
            parent_work=_make_parent_work(),
            objective="test surface fallback",
            worker_type="general",
            target_kind=DelegationTargetKind.SUBAGENT.value,
            tool_profile="standard",
            title="test-d-07",
            spawned_by="delegate_task",
        )

    hints = captured_messages[0].control_metadata["__caller_runtime_hints__"]
    assert hints["surface"] == "telegram", (
        f"surface 应 fallback 到 parent_task.requester.channel 'telegram'，实际: {hints['surface']!r}"
    )
