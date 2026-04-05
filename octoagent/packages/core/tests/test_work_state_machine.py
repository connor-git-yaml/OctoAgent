"""Work 状态机形式化约束测试（对齐 TaskStatus 的 test_state_machine.py 模式）。

覆盖：
1. 所有合法转换通过
2. 典型非法转换被拒绝
3. 纯终态不可再流转
4. 可 retry 终态仅允许 → CREATED
5. WORK_TERMINAL_STATUSES 与 VALID_WORK_TRANSITIONS 一致性
"""

import pytest

from octoagent.core.models.delegation import (
    VALID_WORK_TRANSITIONS,
    WORK_TERMINAL_STATUSES,
    WorkStatus,
    validate_work_transition,
)


# ============================================================
# 合法转换参数化
# ============================================================

_VALID_CASES: list[tuple[WorkStatus, WorkStatus]] = [
    # CREATED →
    (WorkStatus.CREATED, WorkStatus.ASSIGNED),
    (WorkStatus.CREATED, WorkStatus.RUNNING),
    (WorkStatus.CREATED, WorkStatus.FAILED),
    (WorkStatus.CREATED, WorkStatus.CANCELLED),
    (WorkStatus.CREATED, WorkStatus.DELETED),
    # ASSIGNED →
    (WorkStatus.ASSIGNED, WorkStatus.RUNNING),
    (WorkStatus.ASSIGNED, WorkStatus.CANCELLED),
    (WorkStatus.ASSIGNED, WorkStatus.MERGED),
    (WorkStatus.ASSIGNED, WorkStatus.DELETED),
    (WorkStatus.ASSIGNED, WorkStatus.FAILED),
    (WorkStatus.ASSIGNED, WorkStatus.ESCALATED),
    (WorkStatus.ASSIGNED, WorkStatus.TIMED_OUT),
    # RUNNING →
    (WorkStatus.RUNNING, WorkStatus.SUCCEEDED),
    (WorkStatus.RUNNING, WorkStatus.FAILED),
    (WorkStatus.RUNNING, WorkStatus.CANCELLED),
    (WorkStatus.RUNNING, WorkStatus.WAITING_INPUT),
    (WorkStatus.RUNNING, WorkStatus.WAITING_APPROVAL),
    (WorkStatus.RUNNING, WorkStatus.PAUSED),
    (WorkStatus.RUNNING, WorkStatus.TIMED_OUT),
    (WorkStatus.RUNNING, WorkStatus.ESCALATED),
    # WAITING_INPUT →
    (WorkStatus.WAITING_INPUT, WorkStatus.RUNNING),
    (WorkStatus.WAITING_INPUT, WorkStatus.CANCELLED),
    (WorkStatus.WAITING_INPUT, WorkStatus.TIMED_OUT),
    # WAITING_APPROVAL →
    (WorkStatus.WAITING_APPROVAL, WorkStatus.RUNNING),
    (WorkStatus.WAITING_APPROVAL, WorkStatus.CANCELLED),
    (WorkStatus.WAITING_APPROVAL, WorkStatus.TIMED_OUT),
    # PAUSED →
    (WorkStatus.PAUSED, WorkStatus.RUNNING),
    (WorkStatus.PAUSED, WorkStatus.CANCELLED),
    # retry 场景
    (WorkStatus.FAILED, WorkStatus.CREATED),
    (WorkStatus.CANCELLED, WorkStatus.CREATED),
    (WorkStatus.ESCALATED, WorkStatus.CREATED),
    (WorkStatus.TIMED_OUT, WorkStatus.CREATED),
    # delete 场景（所有终态 → DELETED）
    (WorkStatus.FAILED, WorkStatus.DELETED),
    (WorkStatus.CANCELLED, WorkStatus.DELETED),
    (WorkStatus.ESCALATED, WorkStatus.DELETED),
    (WorkStatus.TIMED_OUT, WorkStatus.DELETED),
    (WorkStatus.SUCCEEDED, WorkStatus.DELETED),
    (WorkStatus.MERGED, WorkStatus.DELETED),
    # merge 场景
    (WorkStatus.CREATED, WorkStatus.MERGED),
]


class TestValidWorkTransitions:

    @pytest.mark.parametrize("from_status,to_status", _VALID_CASES)
    def test_valid_transition(self, from_status: WorkStatus, to_status: WorkStatus):
        assert validate_work_transition(from_status, to_status) is True


# ============================================================
# 非法转换参数化
# ============================================================

_INVALID_CASES: list[tuple[WorkStatus, WorkStatus]] = [
    # 纯终态不可再流转
    (WorkStatus.SUCCEEDED, WorkStatus.RUNNING),
    (WorkStatus.SUCCEEDED, WorkStatus.CREATED),
    (WorkStatus.MERGED, WorkStatus.RUNNING),
    (WorkStatus.DELETED, WorkStatus.CREATED),
    # 跳跃式非法转换
    (WorkStatus.CREATED, WorkStatus.SUCCEEDED),
    (WorkStatus.ASSIGNED, WorkStatus.SUCCEEDED),
    (WorkStatus.ASSIGNED, WorkStatus.WAITING_INPUT),
    # 同态转换（禁止）
    (WorkStatus.RUNNING, WorkStatus.RUNNING),
    (WorkStatus.CREATED, WorkStatus.CREATED),
    # 可 retry 终态只能 → CREATED
    (WorkStatus.FAILED, WorkStatus.RUNNING),
    (WorkStatus.CANCELLED, WorkStatus.RUNNING),
    (WorkStatus.ESCALATED, WorkStatus.RUNNING),
]


class TestInvalidWorkTransitions:

    @pytest.mark.parametrize("from_status,to_status", _INVALID_CASES)
    def test_invalid_transition(self, from_status: WorkStatus, to_status: WorkStatus):
        assert validate_work_transition(from_status, to_status) is False


# ============================================================
# 终态与转换表一致性
# ============================================================

class TestWorkTerminalStates:

    def test_deleted_is_absolute_terminal(self):
        """DELETED 是绝对终态，不可再流转。"""
        assert VALID_WORK_TRANSITIONS.get(WorkStatus.DELETED) == set()

    def test_terminal_states_allow_only_deleted(self):
        """非 DELETED 终态仅允许 → DELETED（清理）。"""
        for status in {WorkStatus.SUCCEEDED, WorkStatus.MERGED}:
            allowed = VALID_WORK_TRANSITIONS.get(status, set())
            assert allowed == {WorkStatus.DELETED}, (
                f"{status} 应仅允许 → DELETED，实际: {allowed}"
            )

    def test_retryable_terminals_only_allow_created_or_deleted(self):
        """可 retry 终态仅允许 → CREATED (retry) 或 → DELETED (清理)。"""
        retryable = {WorkStatus.FAILED, WorkStatus.CANCELLED, WorkStatus.ESCALATED, WorkStatus.TIMED_OUT}
        for status in retryable:
            allowed = VALID_WORK_TRANSITIONS.get(status, set())
            assert allowed == {WorkStatus.CREATED, WorkStatus.DELETED}, (
                f"{status} 应仅允许 → CREATED/DELETED，实际: {allowed}"
            )

    def test_all_statuses_covered_in_transitions(self):
        """每个 WorkStatus 值都在 VALID_WORK_TRANSITIONS 中有定义。"""
        for status in WorkStatus:
            assert status in VALID_WORK_TRANSITIONS, (
                f"{status} 未在 VALID_WORK_TRANSITIONS 中定义"
            )

    def test_terminal_statuses_consistency(self):
        """WORK_TERMINAL_STATUSES 中的状态仅允许 CREATED (retry) 或 DELETED (清理)。"""
        for status in WORK_TERMINAL_STATUSES:
            allowed = VALID_WORK_TRANSITIONS.get(status, set())
            assert allowed <= {WorkStatus.CREATED, WorkStatus.DELETED}, (
                f"{status} 在 WORK_TERMINAL_STATUSES 中但转换表允许非 retry/delete 目标: {allowed}"
            )

    def test_escalated_in_terminal(self):
        """ESCALATED 在终态集中——升级意味着当前执行结束。"""
        assert WorkStatus.ESCALATED in WORK_TERMINAL_STATUSES
