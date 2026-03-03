"""StateMachineDriftDetector 单元测试 -- Feature 011 T033

覆盖：
- RUNNING/QUEUED/WAITING_INPUT/WAITING_APPROVAL/PAUSED 各状态驻留超阈值
- 驻留时间未超阈值（正常情况）
- 终态任务不触发
- current_status 使用内部完整 TaskStatus 枚举（FR-011）
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from octoagent.core.models.enums import ActorType, EventType, RiskLevel, TaskStatus
from octoagent.core.models.task import RequesterInfo, Task, TaskPointers
from octoagent.gateway.services.watchdog.config import WatchdogConfig
from octoagent.gateway.services.watchdog.detectors import StateMachineDriftDetector


def _make_task(task_id: str, status: TaskStatus, updated_ago_s: float) -> Task:
    """辅助函数：创建指定状态和更新时间的任务"""
    now = datetime.now(UTC)
    return Task(
        task_id=task_id,
        created_at=now - timedelta(seconds=updated_ago_s),
        updated_at=now - timedelta(seconds=updated_ago_s),
        status=status,
        title=f"Test Task {task_id}",
        thread_id="thread-001",
        scope_id="scope-001",
        requester=RequesterInfo(channel="web", sender_id="user-001"),
        risk_level=RiskLevel.LOW,
        pointers=TaskPointers(),
    )


def _make_config(threshold_s: int = 45) -> WatchdogConfig:
    """threshold = no_progress_cycles × scan_interval（3 × 15 = 45s）"""
    return WatchdogConfig(scan_interval_seconds=15, no_progress_cycles=3)


class TestStateMachineDriftDetectorNormalProgress:
    """驻留时间未超阈值的正常情况"""

    @pytest.mark.asyncio
    async def test_running_task_within_threshold_returns_none(self):
        """RUNNING 任务在阈值内不触发漂移"""
        task = _make_task("task-001", TaskStatus.RUNNING, updated_ago_s=30)
        config = _make_config()  # threshold = 45s
        event_store = AsyncMock()

        detector = StateMachineDriftDetector()
        result = await detector.check(task, event_store, config)

        assert result is None

    @pytest.mark.asyncio
    async def test_queued_task_within_threshold_returns_none(self):
        """QUEUED 任务在阈值内不触发漂移"""
        task = _make_task("task-002", TaskStatus.QUEUED, updated_ago_s=10)
        config = _make_config()

        detector = StateMachineDriftDetector()
        result = await detector.check(task, AsyncMock(), config)

        assert result is None

    @pytest.mark.asyncio
    async def test_exact_threshold_boundary_returns_none(self):
        """恰好在阈值边界（不超过），不触发漂移"""
        # stall_duration = threshold -> <= threshold，不触发
        task = _make_task("task-003", TaskStatus.RUNNING, updated_ago_s=44)
        config = _make_config()

        detector = StateMachineDriftDetector()
        result = await detector.check(task, AsyncMock(), config)

        # 44s < 45s threshold，不触发
        assert result is None


class TestStateMachineDriftDetectorStall:
    """驻留时间超过阈值触发漂移"""

    @pytest.mark.asyncio
    async def test_running_stall_over_threshold(self):
        """RUNNING 任务超过阈值触发 state_machine_stall"""
        task = _make_task("task-010", TaskStatus.RUNNING, updated_ago_s=60)
        config = _make_config()  # threshold = 45s

        detector = StateMachineDriftDetector()
        result = await detector.check(task, AsyncMock(), config)

        assert result is not None
        assert result.task_id == "task-010"
        assert result.drift_type == "state_machine_stall"
        assert result.stall_duration_seconds >= 45.0
        # current_status 使用内部完整枚举值（FR-011）
        assert result.current_status == "RUNNING"

    @pytest.mark.asyncio
    async def test_queued_stall_over_threshold(self):
        """QUEUED 任务超过阈值触发漂移"""
        task = _make_task("task-011", TaskStatus.QUEUED, updated_ago_s=90)
        config = _make_config()

        detector = StateMachineDriftDetector()
        result = await detector.check(task, AsyncMock(), config)

        assert result is not None
        assert result.drift_type == "state_machine_stall"
        assert result.current_status == "QUEUED"

    @pytest.mark.asyncio
    async def test_waiting_input_stall_over_threshold(self):
        """WAITING_INPUT 任务超过阈值触发漂移"""
        task = _make_task("task-012", TaskStatus.WAITING_INPUT, updated_ago_s=120)
        config = _make_config()

        detector = StateMachineDriftDetector()
        result = await detector.check(task, AsyncMock(), config)

        assert result is not None
        assert result.drift_type == "state_machine_stall"
        assert result.current_status == "WAITING_INPUT"

    @pytest.mark.asyncio
    async def test_waiting_approval_stall_over_threshold(self):
        """WAITING_APPROVAL 任务超过阈值触发漂移"""
        task = _make_task("task-013", TaskStatus.WAITING_APPROVAL, updated_ago_s=100)
        config = _make_config()

        detector = StateMachineDriftDetector()
        result = await detector.check(task, AsyncMock(), config)

        assert result is not None
        assert result.drift_type == "state_machine_stall"
        assert result.current_status == "WAITING_APPROVAL"

    @pytest.mark.asyncio
    async def test_paused_stall_over_threshold(self):
        """PAUSED 任务超过阈值触发漂移"""
        task = _make_task("task-014", TaskStatus.PAUSED, updated_ago_s=200)
        config = _make_config()

        detector = StateMachineDriftDetector()
        result = await detector.check(task, AsyncMock(), config)

        assert result is not None
        assert result.drift_type == "state_machine_stall"
        assert result.current_status == "PAUSED"

    @pytest.mark.asyncio
    async def test_stall_duration_calculation(self):
        """stall_duration_seconds 计算正确（大于 threshold）"""
        updated_ago_s = 120.0
        task = _make_task("task-015", TaskStatus.RUNNING, updated_ago_s=updated_ago_s)
        config = _make_config()

        detector = StateMachineDriftDetector()
        result = await detector.check(task, AsyncMock(), config)

        assert result is not None
        # 允许 2s 误差（创建 Task 对象和运行 check 的时间差）
        assert abs(result.stall_duration_seconds - updated_ago_s) < 2.0

    @pytest.mark.asyncio
    async def test_drift_result_has_required_fields(self):
        """DriftResult 包含所有必要字段"""
        task = _make_task("task-016", TaskStatus.RUNNING, updated_ago_s=60)
        config = _make_config()

        detector = StateMachineDriftDetector()
        result = await detector.check(task, AsyncMock(), config)

        assert result is not None
        assert result.task_id == "task-016"
        assert result.drift_type == "state_machine_stall"
        assert result.detected_at is not None
        assert result.stall_duration_seconds > 0
        assert result.last_progress_ts is not None
        assert len(result.suggested_actions) > 0

    @pytest.mark.asyncio
    async def test_event_store_not_queried(self):
        """StateMachineDriftDetector 不查询 EventStore（仅用 task.updated_at）"""
        task = _make_task("task-017", TaskStatus.RUNNING, updated_ago_s=60)
        config = _make_config()
        event_store = AsyncMock()

        detector = StateMachineDriftDetector()
        await detector.check(task, event_store, config)

        # 状态机漂移只用 task.updated_at，不查 EventStore
        event_store.get_events_by_types_since.assert_not_called()
        event_store.get_latest_event_ts.assert_not_called()


class TestStateMachineDriftDetectorTerminalStates:
    """终态任务不触发漂移（FR-013）"""

    @pytest.mark.asyncio
    async def test_succeeded_task_not_detected(self):
        """SUCCEEDED 终态任务不触发"""
        task = _make_task("task-020", TaskStatus.SUCCEEDED, updated_ago_s=1000)
        config = _make_config()

        detector = StateMachineDriftDetector()
        result = await detector.check(task, AsyncMock(), config)

        assert result is None

    @pytest.mark.asyncio
    async def test_failed_task_not_detected(self):
        """FAILED 终态任务不触发"""
        task = _make_task("task-021", TaskStatus.FAILED, updated_ago_s=1000)
        config = _make_config()

        detector = StateMachineDriftDetector()
        result = await detector.check(task, AsyncMock(), config)

        assert result is None

    @pytest.mark.asyncio
    async def test_cancelled_task_not_detected(self):
        """CANCELLED 终态任务不触发"""
        task = _make_task("task-022", TaskStatus.CANCELLED, updated_ago_s=1000)
        config = _make_config()

        detector = StateMachineDriftDetector()
        result = await detector.check(task, AsyncMock(), config)

        assert result is None

    @pytest.mark.asyncio
    async def test_rejected_task_not_detected(self):
        """REJECTED 终态任务不触发"""
        task = _make_task("task-023", TaskStatus.REJECTED, updated_ago_s=1000)
        config = _make_config()

        detector = StateMachineDriftDetector()
        result = await detector.check(task, AsyncMock(), config)

        assert result is None


class TestStateMachineDriftDetectorInternalTaskStatus:
    """FR-011: current_status 使用内部完整 TaskStatus 枚举，不降级为 A2A 状态"""

    @pytest.mark.asyncio
    async def test_running_status_uses_internal_enum(self):
        """RUNNING 状态的 current_status 是内部枚举值"""
        task = _make_task("task-030", TaskStatus.RUNNING, updated_ago_s=60)
        config = _make_config()

        detector = StateMachineDriftDetector()
        result = await detector.check(task, AsyncMock(), config)

        assert result is not None
        assert result.current_status == "RUNNING"
        # 不应是 A2A 映射值
        assert result.current_status != "active"

    @pytest.mark.asyncio
    async def test_waiting_approval_status_not_a2a_mapped(self):
        """WAITING_APPROVAL 的 current_status 不应被映射为 A2A 值"""
        task = _make_task("task-031", TaskStatus.WAITING_APPROVAL, updated_ago_s=60)
        config = _make_config()

        detector = StateMachineDriftDetector()
        result = await detector.check(task, AsyncMock(), config)

        assert result is not None
        assert result.current_status == "WAITING_APPROVAL"
        # 不应是 A2A 状态
        assert result.current_status != "active"
        assert result.current_status != "pending"
