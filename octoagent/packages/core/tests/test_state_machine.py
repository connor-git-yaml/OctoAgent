"""状态机流转单元测试 -- T036

测试内容：
1. 合法流转通过
2. 非法流转抛出异常
3. 终态不可再流转
"""

import pytest
from octoagent.core.models.enums import (
    TERMINAL_STATES,
    VALID_TRANSITIONS,
    TaskStatus,
    validate_transition,
)


class TestStateMachineTransitions:
    """状态机流转验证"""

    # 所有合法流转
    @pytest.mark.parametrize(
        "from_status,to_status",
        [
            (TaskStatus.CREATED, TaskStatus.RUNNING),
            (TaskStatus.CREATED, TaskStatus.CANCELLED),
            (TaskStatus.RUNNING, TaskStatus.SUCCEEDED),
            (TaskStatus.RUNNING, TaskStatus.FAILED),
            (TaskStatus.RUNNING, TaskStatus.CANCELLED),
        ],
    )
    def test_valid_transition(self, from_status: TaskStatus, to_status: TaskStatus):
        """合法流转应通过验证"""
        assert validate_transition(from_status, to_status) is True

    # 所有非法流转
    @pytest.mark.parametrize(
        "from_status,to_status",
        [
            (TaskStatus.CREATED, TaskStatus.SUCCEEDED),
            (TaskStatus.CREATED, TaskStatus.FAILED),
            (TaskStatus.CREATED, TaskStatus.CREATED),
            (TaskStatus.RUNNING, TaskStatus.CREATED),
            (TaskStatus.RUNNING, TaskStatus.RUNNING),
        ],
    )
    def test_invalid_transition(self, from_status: TaskStatus, to_status: TaskStatus):
        """非法流转应被拒绝"""
        assert validate_transition(from_status, to_status) is False

    def test_all_terminal_states_cannot_transition(self):
        """所有终态都不能再流转"""
        for terminal in TERMINAL_STATES:
            if terminal not in VALID_TRANSITIONS:
                continue  # 跳过 M1+ 预留状态
            for target in TaskStatus:
                assert validate_transition(terminal, target) is False, (
                    f"终态 {terminal} 不应能流转到 {target}"
                )

    def test_terminal_states_have_empty_transitions(self):
        """终态的合法流转集合为空"""
        for status in [TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED]:
            assert VALID_TRANSITIONS[status] == set()

    def test_valid_transitions_completeness(self):
        """VALID_TRANSITIONS 覆盖所有 M0 活跃状态"""
        m0_states = {
            TaskStatus.CREATED,
            TaskStatus.RUNNING,
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }
        for state in m0_states:
            assert state in VALID_TRANSITIONS, f"{state} 未在 VALID_TRANSITIONS 中定义"
