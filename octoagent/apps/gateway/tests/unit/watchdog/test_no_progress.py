"""NoProgressDetector 单元测试 -- Feature 011 T022

覆盖以下场景：
- 正常进展任务（窗口内有进展事件）-> 返回 None
- 超过阈值无进展 -> 返回 DriftResult(drift_type="no_progress")
- MODEL_CALL_STARTED 豁免窗口内 -> 返回 None
- 无历史事件降级使用 task.updated_at -> 正确计算 stall_duration_seconds
- 终态任务 -> 不触发
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from octoagent.core.models.enums import EventType, TaskStatus
from octoagent.gateway.services.watchdog.config import WatchdogConfig
from octoagent.gateway.services.watchdog.detectors import NoProgressDetector


def _make_task(status: TaskStatus = TaskStatus.RUNNING, updated_ago_seconds: float = 0.0) -> MagicMock:
    """创建测试用 Task mock"""
    task = MagicMock()
    task.task_id = "task-001"
    task.status = status.value
    task.updated_at = datetime.now(UTC) - timedelta(seconds=updated_ago_seconds)
    return task


def _make_event(event_type: EventType, ts: datetime) -> MagicMock:
    """创建测试用 Event mock"""
    event = MagicMock()
    event.type = event_type
    event.ts = ts
    return event


def _make_config(
    scan_interval: int = 15,
    no_progress_cycles: int = 3,
) -> WatchdogConfig:
    """创建测试用 WatchdogConfig（threshold = cycles × interval = 45s by default）"""
    return WatchdogConfig(
        scan_interval_seconds=scan_interval,
        no_progress_cycles=no_progress_cycles,
    )


class TestNoProgressDetectorNormalProgress:
    """正常进展任务：应返回 None"""

    @pytest.mark.asyncio
    async def test_recent_progress_event_returns_none(self):
        """时间窗口内有进展事件，返回 None"""
        detector = NoProgressDetector()
        task = _make_task(TaskStatus.RUNNING)
        config = _make_config()  # threshold=45s

        # 模拟 10 秒前有进展事件
        recent_event = _make_event(
            EventType.MODEL_CALL_COMPLETED,
            datetime.now(UTC) - timedelta(seconds=10),
        )
        mock_event_store = AsyncMock()
        mock_event_store.get_events_by_types_since.return_value = [recent_event]

        result = await detector.check(task, mock_event_store, config)
        assert result is None

    @pytest.mark.asyncio
    async def test_multiple_progress_events_returns_none(self):
        """多个进展事件，返回 None"""
        detector = NoProgressDetector()
        task = _make_task(TaskStatus.RUNNING)
        config = _make_config()

        events = [
            _make_event(EventType.TOOL_CALL_STARTED, datetime.now(UTC) - timedelta(seconds=5)),
            _make_event(EventType.TOOL_CALL_COMPLETED, datetime.now(UTC) - timedelta(seconds=2)),
        ]
        mock_event_store = AsyncMock()
        mock_event_store.get_events_by_types_since.return_value = events

        result = await detector.check(task, mock_event_store, config)
        assert result is None


class TestNoProgressDetectorDrift:
    """超过阈值无进展：应返回 DriftResult"""

    @pytest.mark.asyncio
    async def test_no_progress_over_threshold_returns_drift(self):
        """超过 no_progress_threshold 无进展，返回 DriftResult"""
        detector = NoProgressDetector()
        task = _make_task(TaskStatus.RUNNING)
        config = _make_config()  # threshold=45s

        # 无进展事件，也无 MODEL_CALL_STARTED
        mock_event_store = AsyncMock()
        mock_event_store.get_events_by_types_since.return_value = []
        # 最近事件在 60 秒前
        mock_event_store.get_latest_event_ts.return_value = datetime.now(UTC) - timedelta(seconds=60)

        result = await detector.check(task, mock_event_store, config)

        assert result is not None
        assert result.drift_type == "no_progress"
        assert result.task_id == "task-001"
        assert result.stall_duration_seconds > 0
        assert "check_worker_logs" in result.suggested_actions

    @pytest.mark.asyncio
    async def test_drift_result_contains_last_progress_ts(self):
        """DriftResult 包含 last_progress_ts 字段"""
        detector = NoProgressDetector()
        task = _make_task(TaskStatus.RUNNING)
        config = _make_config()

        last_ts = datetime.now(UTC) - timedelta(seconds=60)
        mock_event_store = AsyncMock()
        mock_event_store.get_events_by_types_since.return_value = []
        mock_event_store.get_latest_event_ts.return_value = last_ts

        result = await detector.check(task, mock_event_store, config)

        assert result is not None
        assert result.last_progress_ts is not None
        assert abs((result.last_progress_ts - last_ts).total_seconds()) < 1.0


class TestNoProgressDetectorLLMExemption:
    """LLM 等待期豁免（FR-010）"""

    @pytest.mark.asyncio
    async def test_model_call_started_in_window_exempts(self):
        """MODEL_CALL_STARTED 在窗口内，豁免检测，返回 None"""
        detector = NoProgressDetector()
        task = _make_task(TaskStatus.RUNNING)
        config = _make_config()

        # 第一次调用（PROGRESS_EVENT_TYPES）：返回空（无进展）
        # 第二次调用（MODEL_CALL_STARTED）：返回有事件（LLM 等待期）
        model_started_event = _make_event(
            EventType.MODEL_CALL_STARTED,
            datetime.now(UTC) - timedelta(seconds=20),
        )

        call_count = 0

        async def side_effect(task_id, event_types, since_ts):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # 第一次：PROGRESS_EVENT_TYPES 查询，无进展
                return []
            else:
                # 第二次：MODEL_CALL_STARTED 查询，有事件
                return [model_started_event]

        mock_event_store = AsyncMock()
        mock_event_store.get_events_by_types_since.side_effect = side_effect

        result = await detector.check(task, mock_event_store, config)
        # LLM 等待期内，豁免
        assert result is None

    @pytest.mark.asyncio
    async def test_model_call_started_outside_window_does_not_exempt(self):
        """MODEL_CALL_STARTED 不在窗口内（两次查询均返回空），不豁免"""
        detector = NoProgressDetector()
        task = _make_task(TaskStatus.RUNNING)
        config = _make_config()

        # 两次查询均返回空
        mock_event_store = AsyncMock()
        mock_event_store.get_events_by_types_since.return_value = []
        mock_event_store.get_latest_event_ts.return_value = datetime.now(UTC) - timedelta(seconds=60)

        result = await detector.check(task, mock_event_store, config)
        # 不豁免，应返回漂移
        assert result is not None
        assert result.drift_type == "no_progress"


class TestNoProgressDetectorFallback:
    """无历史事件降级使用 task.updated_at（边界情况 4）"""

    @pytest.mark.asyncio
    async def test_no_events_falls_back_to_updated_at(self):
        """无历史事件时，使用 task.updated_at 计算 stall_duration"""
        detector = NoProgressDetector()
        # 任务 60 秒前更新
        task = _make_task(TaskStatus.RUNNING, updated_ago_seconds=60.0)
        config = _make_config()  # threshold=45s

        mock_event_store = AsyncMock()
        mock_event_store.get_events_by_types_since.return_value = []
        mock_event_store.get_latest_event_ts.return_value = None  # 无历史事件

        result = await detector.check(task, mock_event_store, config)

        assert result is not None
        assert result.drift_type == "no_progress"
        # stall_duration 应约为 60 秒（使用 updated_at）
        assert result.stall_duration_seconds >= 55.0  # 允许 5s 执行误差


class TestNoProgressDetectorTerminalStates:
    """终态任务不触发检测（FR-013）"""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("terminal_status", [
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.REJECTED,
    ])
    async def test_terminal_task_not_detected(self, terminal_status: TaskStatus):
        """终态任务直接跳过，返回 None"""
        detector = NoProgressDetector()
        task = _make_task(terminal_status)
        config = _make_config()
        mock_event_store = AsyncMock()

        result = await detector.check(task, mock_event_store, config)

        assert result is None
        # 终态任务不应查询 EventStore
        mock_event_store.get_events_by_types_since.assert_not_called()
