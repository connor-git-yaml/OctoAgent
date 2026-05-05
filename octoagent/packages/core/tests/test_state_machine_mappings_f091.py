"""F091 跨枚举状态映射函数单测。

设计约束（Codex review 闭环 H1/M1/M2/M3/L2）：
- WorkStatus.MERGED / ESCALATED / DELETED 为"前置依赖"状态，work_status_to_task_status() 必 raise
- WorkStatus.ASSIGNED → TaskStatus.RUNNING（避免 QUEUED 死状态）
- WorkerRuntimeState.PENDING 直接路径 / 组合路径必须一致

覆盖：
- 4 个映射 dict 完整性（每个源枚举值都被映射，必要的"刻意排除"显式记录）
- 4 个映射函数 round-trip 与显式 edge case
- 终态 → 终态保持
- raise 行为契约
- 直接 vs 组合路径一致性
"""

import pytest

from octoagent.core.models import (
    TASK_TO_WORK_STATUS,
    TERMINAL_STATES,
    WORK_STATUSES_REQUIRING_CONTEXT,
    WORK_TERMINAL_STATUSES,
    WORK_TO_TASK_STATUS,
    WORKER_TO_TASK_STATUS,
    WORKER_TO_WORK_STATUS,
    TaskStatus,
    WorkerRuntimeState,
    WorkStatus,
    task_status_to_work_status,
    work_status_to_task_status,
    worker_state_to_task_status,
    worker_state_to_work_status,
)


# ============================================================
# 完整性：每个源枚举值必须被映射（除显式排除）
# ============================================================


class TestMappingCompleteness:
    """映射 dict 必须覆盖源枚举的全部值（防止漏值）。"""

    def test_task_to_work_covers_all_task_status(self) -> None:
        assert set(TASK_TO_WORK_STATUS.keys()) == set(TaskStatus), (
            f"TASK_TO_WORK_STATUS 漏值：{set(TaskStatus) - set(TASK_TO_WORK_STATUS.keys())}"
        )

    def test_work_to_task_covers_safe_subset_only(self) -> None:
        """work_to_task 安全子集 = WorkStatus - WORK_STATUSES_REQUIRING_CONTEXT。

        MERGED / ESCALATED / DELETED 必须刻意不在 dict 中——用 raise 引导调用方处理。
        """
        expected = set(WorkStatus) - WORK_STATUSES_REQUIRING_CONTEXT
        assert set(WORK_TO_TASK_STATUS.keys()) == expected, (
            f"WORK_TO_TASK_STATUS 与安全子集不符："
            f"漏 {expected - set(WORK_TO_TASK_STATUS.keys())} / "
            f"多 {set(WORK_TO_TASK_STATUS.keys()) - expected}"
        )

    def test_worker_to_work_covers_all_worker_state(self) -> None:
        assert set(WORKER_TO_WORK_STATUS.keys()) == set(WorkerRuntimeState), (
            f"WORKER_TO_WORK_STATUS 漏值：{set(WorkerRuntimeState) - set(WORKER_TO_WORK_STATUS.keys())}"
        )

    def test_worker_to_task_covers_all_worker_state(self) -> None:
        assert set(WORKER_TO_TASK_STATUS.keys()) == set(WorkerRuntimeState), (
            f"WORKER_TO_TASK_STATUS 漏值：{set(WorkerRuntimeState) - set(WORKER_TO_TASK_STATUS.keys())}"
        )

    def test_work_statuses_requiring_context_is_documented(self) -> None:
        """WORK_STATUSES_REQUIRING_CONTEXT 必须显式包含 3 个高风险状态。"""
        assert WorkStatus.MERGED in WORK_STATUSES_REQUIRING_CONTEXT
        assert WorkStatus.ESCALATED in WORK_STATUSES_REQUIRING_CONTEXT
        assert WorkStatus.DELETED in WORK_STATUSES_REQUIRING_CONTEXT
        # 当且仅当这 3 个；防止后人误加
        assert WORK_STATUSES_REQUIRING_CONTEXT == frozenset({
            WorkStatus.MERGED,
            WorkStatus.ESCALATED,
            WorkStatus.DELETED,
        })


# ============================================================
# raise 行为契约（H1 / M3 闭环）
# ============================================================


class TestUnsafeMappingRaisesValueError:
    """work_status_to_task_status 对前置依赖状态必 raise。"""

    @pytest.mark.parametrize("status", list(WORK_STATUSES_REQUIRING_CONTEXT))
    def test_raises_for_merged_escalated_deleted(self, status: WorkStatus) -> None:
        with pytest.raises(ValueError, match="需要前置状态上下文"):
            work_status_to_task_status(status)

    def test_raise_message_mentions_status_name(self) -> None:
        """错误信息应明确说明哪个状态不可投影。"""
        with pytest.raises(ValueError, match=r"WorkStatus\.MERGED"):
            work_status_to_task_status(WorkStatus.MERGED)


# ============================================================
# 终态保持：源终态映射到目标终态
# ============================================================


class TestTerminalPreservation:
    """终态 → 终态：状态机的关键不变量。"""

    @pytest.mark.parametrize("status", list(TERMINAL_STATES))
    def test_task_terminal_maps_to_work_terminal(self, status: TaskStatus) -> None:
        mapped = task_status_to_work_status(status)
        assert mapped in WORK_TERMINAL_STATUSES, (
            f"TaskStatus.{status.name} (终态) → WorkStatus.{mapped.name} (非终态)"
        )

    @pytest.mark.parametrize(
        "status",
        sorted(WORK_TERMINAL_STATUSES - WORK_STATUSES_REQUIRING_CONTEXT, key=lambda s: s.name),
    )
    def test_work_terminal_safe_subset_maps_to_task_terminal(
        self, status: WorkStatus
    ) -> None:
        """除前置依赖状态外，work 终态都映射到 task 终态。"""
        mapped = work_status_to_task_status(status)
        assert mapped in TERMINAL_STATES, (
            f"WorkStatus.{status.name} (终态) → TaskStatus.{mapped.name} (非终态)"
        )

    @pytest.mark.parametrize(
        "state",
        [
            WorkerRuntimeState.SUCCEEDED,
            WorkerRuntimeState.FAILED,
            WorkerRuntimeState.CANCELLED,
            WorkerRuntimeState.TIMED_OUT,
        ],
    )
    def test_worker_terminal_maps_to_work_terminal(self, state: WorkerRuntimeState) -> None:
        mapped = worker_state_to_work_status(state)
        assert mapped in WORK_TERMINAL_STATUSES, (
            f"WorkerRuntimeState.{state.name} (终态) → WorkStatus.{mapped.name} (非终态)"
        )

    @pytest.mark.parametrize(
        "state",
        [
            WorkerRuntimeState.SUCCEEDED,
            WorkerRuntimeState.FAILED,
            WorkerRuntimeState.CANCELLED,
            WorkerRuntimeState.TIMED_OUT,
        ],
    )
    def test_worker_terminal_maps_to_task_terminal(self, state: WorkerRuntimeState) -> None:
        mapped = worker_state_to_task_status(state)
        assert mapped in TERMINAL_STATES, (
            f"WorkerRuntimeState.{state.name} (终态) → TaskStatus.{mapped.name} (非终态)"
        )


# ============================================================
# 显式 edge case：多对一与语义化映射
# ============================================================


class TestExplicitMappings:
    """关键映射的语义合约（变更时必触发测试失败）。"""

    def test_task_rejected_to_work_failed(self) -> None:
        """task rejection（用户拒绝审批）→ work failed。"""
        assert task_status_to_work_status(TaskStatus.REJECTED) is WorkStatus.FAILED

    def test_task_queued_to_work_assigned(self) -> None:
        assert task_status_to_work_status(TaskStatus.QUEUED) is WorkStatus.ASSIGNED

    def test_work_assigned_to_task_running(self) -> None:
        """ASSIGNED → RUNNING（避免 task QUEUED 死状态，Codex M2 闭环）。"""
        assert work_status_to_task_status(WorkStatus.ASSIGNED) is TaskStatus.RUNNING

    def test_work_timeout_to_task_failed(self) -> None:
        """work timeout → task failed。"""
        assert work_status_to_task_status(WorkStatus.TIMED_OUT) is TaskStatus.FAILED

    def test_worker_pending_to_task_running(self) -> None:
        """worker PENDING（已派发未跑）→ task RUNNING（用户视角不区分）。"""
        assert worker_state_to_task_status(WorkerRuntimeState.PENDING) is TaskStatus.RUNNING

    def test_worker_pending_to_work_assigned(self) -> None:
        """worker PENDING → work ASSIGNED（more granular than task）。"""
        assert worker_state_to_work_status(WorkerRuntimeState.PENDING) is WorkStatus.ASSIGNED

    def test_worker_timeout_to_task_failed(self) -> None:
        """worker timeout → task failed。"""
        assert worker_state_to_task_status(WorkerRuntimeState.TIMED_OUT) is TaskStatus.FAILED

    def test_worker_timeout_to_work_timeout(self) -> None:
        """worker timeout → work TIMED_OUT（保留语义）。"""
        assert worker_state_to_work_status(WorkerRuntimeState.TIMED_OUT) is WorkStatus.TIMED_OUT


# ============================================================
# 直接 vs 组合路径一致性（M1 闭环）
#
# worker_state_to_task_status(s) 必须等价于
#   work_status_to_task_status(worker_state_to_work_status(s))
# 因为下层 → 上层 → 下层 路径的语义应自洽。
# ============================================================


class TestDirectVsComposedPathConsistency:
    """直接路径 (worker → task) 与组合路径 (worker → work → task) 必须等价。

    这是 Codex M1 finding 闭环：之前 PENDING 直接 → RUNNING 但组合 → ASSIGNED → QUEUED
    不一致；现在 ASSIGNED → RUNNING 修复后，PENDING 路径自洽。
    """

    @pytest.mark.parametrize("state", list(WorkerRuntimeState))
    def test_all_worker_states_consistent_paths(self, state: WorkerRuntimeState) -> None:
        direct = worker_state_to_task_status(state)
        composed = work_status_to_task_status(worker_state_to_work_status(state))
        assert direct is composed, (
            f"WorkerRuntimeState.{state.name}: 直接 → {direct.name}, "
            f"组合 (经 {worker_state_to_work_status(state).name}) → {composed.name}"
        )


# ============================================================
# Round-trip 一致性：核心活跃状态保持等价
# ============================================================


class TestRoundTripConsistency:
    """task→work→task 在单射状态保持等价。

    多对一状态（QUEUED↔ASSIGNED 双射安全；ASSIGNED 在 work 又对应到 RUNNING 在 task）
    需要单独验证。
    """

    @pytest.mark.parametrize(
        "status",
        [
            TaskStatus.CREATED,
            TaskStatus.RUNNING,
            TaskStatus.WAITING_INPUT,
            TaskStatus.WAITING_APPROVAL,
            TaskStatus.PAUSED,
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        ],
    )
    def test_task_round_trip_preserves_value(self, status: TaskStatus) -> None:
        """单射 TaskStatus 值，task→work→task 必保持原值。"""
        round_trip = work_status_to_task_status(task_status_to_work_status(status))
        assert round_trip is status, (
            f"TaskStatus.{status.name} round-trip → TaskStatus.{round_trip.name}"
        )

    def test_task_queued_round_trip_collapses_to_running(self) -> None:
        """QUEUED → ASSIGNED → RUNNING（M2 修复后语义：QUEUED 是 M1+ 预留死状态，
        投影 task→work→task 故意压扁到活跃 RUNNING）。"""
        round_trip = work_status_to_task_status(task_status_to_work_status(TaskStatus.QUEUED))
        assert round_trip is TaskStatus.RUNNING

    def test_task_rejected_round_trip_collapses_to_failed(self) -> None:
        """REJECTED → FAILED → FAILED（用户拒审 = work failed = task failed）。"""
        round_trip = work_status_to_task_status(task_status_to_work_status(TaskStatus.REJECTED))
        assert round_trip is TaskStatus.FAILED


# ============================================================
# 多对一映射记录（信息有损但语义一致）
# ============================================================


class TestNonInjectiveMappings:
    """显式记录哪些映射是多对一（信息有损）。"""

    def test_two_work_states_collapse_to_task_running(self) -> None:
        """ASSIGNED / RUNNING 两态都映射到 task RUNNING（M2 修复：避免 QUEUED 死状态）。"""
        assert work_status_to_task_status(WorkStatus.ASSIGNED) is TaskStatus.RUNNING
        assert work_status_to_task_status(WorkStatus.RUNNING) is TaskStatus.RUNNING

    def test_two_work_states_collapse_to_task_failed(self) -> None:
        """TIMED_OUT / FAILED 两态都映射到 task FAILED。"""
        assert work_status_to_task_status(WorkStatus.TIMED_OUT) is TaskStatus.FAILED
        assert work_status_to_task_status(WorkStatus.FAILED) is TaskStatus.FAILED

    def test_two_worker_states_collapse_to_task_running(self) -> None:
        """worker PENDING / RUNNING 两态都映射到 task RUNNING。"""
        assert worker_state_to_task_status(WorkerRuntimeState.PENDING) is TaskStatus.RUNNING
        assert worker_state_to_task_status(WorkerRuntimeState.RUNNING) is TaskStatus.RUNNING

    def test_two_worker_states_collapse_to_task_failed(self) -> None:
        """worker FAILED / TIMED_OUT 两态都映射到 task FAILED。"""
        assert worker_state_to_task_status(WorkerRuntimeState.FAILED) is TaskStatus.FAILED
        assert worker_state_to_task_status(WorkerRuntimeState.TIMED_OUT) is TaskStatus.FAILED
