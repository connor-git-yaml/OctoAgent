"""RepeatedFailureDetector 单元测试 -- Feature 011 T037

覆盖：
- 失败次数达阈值触发漂移
- 失败次数未达阈值不触发
- 不同失败类型组合（MODEL_CALL_FAILED / TOOL_CALL_FAILED / SKILL_FAILED）
- 时间窗口边界（窗口外的失败不计入）
- 终态任务不触发（FR-013）
- failure_count 和 failure_event_types 字段正确性
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import aiosqlite
import pytest
import pytest_asyncio

from octoagent.core.models.enums import ActorType, EventType, RiskLevel, TaskStatus
from octoagent.core.models.event import Event, EventCausality
from octoagent.core.models.task import RequesterInfo, Task, TaskPointers
from octoagent.core.store import StoreGroup
from octoagent.core.store.sqlite_init import init_db
from octoagent.gateway.services.watchdog.config import WatchdogConfig
from octoagent.gateway.services.watchdog.detectors import (
    FAILURE_EVENT_TYPES,
    RepeatedFailureDetector,
)


def _make_task(task_id: str, status: TaskStatus, updated_ago_s: float = 0.0) -> Task:
    """辅助函数：创建任务"""
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


def _make_config(
    failure_window_seconds: int = 300,
    repeated_failure_threshold: int = 3,
) -> WatchdogConfig:
    """辅助函数：创建 WatchdogConfig"""
    return WatchdogConfig(
        failure_window_seconds=failure_window_seconds,
        repeated_failure_threshold=repeated_failure_threshold,
    )


@pytest_asyncio.fixture
async def store_group(tmp_path: Path) -> StoreGroup:
    """内存 SQLite StoreGroup（含外键约束，需先创建 task 再写 event）"""
    conn = await aiosqlite.connect(":memory:")
    await init_db(conn)
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    sg = StoreGroup(conn=conn, artifacts_dir=artifacts_dir)
    yield sg
    await conn.close()


def _make_failure_event(
    task_id: str,
    event_type: EventType,
    ts: datetime,
    seq: int,
) -> Event:
    """辅助函数：创建失败事件"""
    import ulid
    return Event(
        event_id=str(ulid.ULID()),
        task_id=task_id,
        task_seq=seq,
        ts=ts,
        type=event_type,
        schema_version=1,
        actor=ActorType.SYSTEM,
        payload={},
        trace_id="trace-test",
        span_id="",
        causality=EventCausality(),
    )


class TestRepeatedFailureDetectorBelowThreshold:
    """失败次数未达阈值，不触发漂移"""

    @pytest.mark.asyncio
    async def test_no_failures_returns_none(self, store_group: StoreGroup):
        """无失败事件不触发"""
        task = _make_task("task-001", TaskStatus.RUNNING)
        await store_group.task_store.create_task(task)
        config = _make_config()

        detector = RepeatedFailureDetector()
        result = await detector.check(task, store_group.event_store, config)

        assert result is None

    @pytest.mark.asyncio
    async def test_one_failure_below_threshold_returns_none(self, store_group: StoreGroup):
        """1 次失败（阈值 3）不触发"""
        task = _make_task("task-002", TaskStatus.RUNNING)
        await store_group.task_store.create_task(task)
        config = _make_config(repeated_failure_threshold=3)

        now = datetime.now(UTC)
        event = _make_failure_event("task-002", EventType.MODEL_CALL_FAILED, now - timedelta(seconds=10), 1)
        await store_group.event_store.append_event_committed(event)

        detector = RepeatedFailureDetector()
        result = await detector.check(task, store_group.event_store, config)

        assert result is None

    @pytest.mark.asyncio
    async def test_two_failures_below_threshold_returns_none(self, store_group: StoreGroup):
        """2 次失败（阈值 3）不触发"""
        task = _make_task("task-003", TaskStatus.RUNNING)
        await store_group.task_store.create_task(task)
        config = _make_config(repeated_failure_threshold=3)

        now = datetime.now(UTC)
        for i, event_type in enumerate([EventType.MODEL_CALL_FAILED, EventType.TOOL_CALL_FAILED]):
            event = _make_failure_event("task-003", event_type, now - timedelta(seconds=10 + i), i + 1)
            await store_group.event_store.append_event_committed(event)

        detector = RepeatedFailureDetector()
        result = await detector.check(task, store_group.event_store, config)

        assert result is None


class TestRepeatedFailureDetectorAtThreshold:
    """失败次数达到或超过阈值，触发漂移"""

    @pytest.mark.asyncio
    async def test_exactly_threshold_failures_triggers_drift(self, store_group: StoreGroup):
        """恰好达到阈值（3 次）触发漂移"""
        task = _make_task("task-010", TaskStatus.RUNNING)
        await store_group.task_store.create_task(task)
        config = _make_config(repeated_failure_threshold=3)

        now = datetime.now(UTC)
        for i in range(3):
            event = _make_failure_event("task-010", EventType.MODEL_CALL_FAILED, now - timedelta(seconds=10 + i), i + 1)
            await store_group.event_store.append_event_committed(event)

        detector = RepeatedFailureDetector()
        result = await detector.check(task, store_group.event_store, config)

        assert result is not None
        assert result.drift_type == "repeated_failure"
        assert result.failure_count == 3

    @pytest.mark.asyncio
    async def test_above_threshold_triggers_drift(self, store_group: StoreGroup):
        """超过阈值（5 次，阈值 3）触发漂移"""
        task = _make_task("task-011", TaskStatus.RUNNING)
        await store_group.task_store.create_task(task)
        config = _make_config(repeated_failure_threshold=3)

        now = datetime.now(UTC)
        for i in range(5):
            event = _make_failure_event("task-011", EventType.TOOL_CALL_FAILED, now - timedelta(seconds=10 + i), i + 1)
            await store_group.event_store.append_event_committed(event)

        detector = RepeatedFailureDetector()
        result = await detector.check(task, store_group.event_store, config)

        assert result is not None
        assert result.failure_count == 5

    @pytest.mark.asyncio
    async def test_drift_result_has_required_fields(self, store_group: StoreGroup):
        """DriftResult 包含所有必要字段"""
        task = _make_task("task-012", TaskStatus.RUNNING)
        await store_group.task_store.create_task(task)
        config = _make_config(repeated_failure_threshold=3)

        now = datetime.now(UTC)
        for i in range(3):
            event = _make_failure_event("task-012", EventType.MODEL_CALL_FAILED, now - timedelta(seconds=10 + i), i + 1)
            await store_group.event_store.append_event_committed(event)

        detector = RepeatedFailureDetector()
        result = await detector.check(task, store_group.event_store, config)

        assert result is not None
        assert result.task_id == "task-012"
        assert result.drift_type == "repeated_failure"
        assert result.detected_at is not None
        assert result.stall_duration_seconds >= 0
        assert result.failure_count == 3
        assert len(result.failure_event_types) == 3
        assert len(result.suggested_actions) > 0


class TestRepeatedFailureDetectorEventTypes:
    """不同失败类型组合"""

    @pytest.mark.asyncio
    async def test_model_call_failed_counts(self, store_group: StoreGroup):
        """MODEL_CALL_FAILED 事件被计入失败统计"""
        task = _make_task("task-020", TaskStatus.RUNNING)
        await store_group.task_store.create_task(task)
        config = _make_config(repeated_failure_threshold=3)

        now = datetime.now(UTC)
        for i in range(3):
            event = _make_failure_event("task-020", EventType.MODEL_CALL_FAILED, now - timedelta(seconds=i + 1), i + 1)
            await store_group.event_store.append_event_committed(event)

        detector = RepeatedFailureDetector()
        result = await detector.check(task, store_group.event_store, config)

        assert result is not None
        assert all(t == "MODEL_CALL_FAILED" for t in result.failure_event_types)

    @pytest.mark.asyncio
    async def test_tool_call_failed_counts(self, store_group: StoreGroup):
        """TOOL_CALL_FAILED 事件被计入失败统计"""
        task = _make_task("task-021", TaskStatus.RUNNING)
        await store_group.task_store.create_task(task)
        config = _make_config(repeated_failure_threshold=3)

        now = datetime.now(UTC)
        for i in range(3):
            event = _make_failure_event("task-021", EventType.TOOL_CALL_FAILED, now - timedelta(seconds=i + 1), i + 1)
            await store_group.event_store.append_event_committed(event)

        detector = RepeatedFailureDetector()
        result = await detector.check(task, store_group.event_store, config)

        assert result is not None
        assert all(t == "TOOL_CALL_FAILED" for t in result.failure_event_types)

    @pytest.mark.asyncio
    async def test_skill_failed_counts(self, store_group: StoreGroup):
        """SKILL_FAILED 事件被计入失败统计"""
        task = _make_task("task-022", TaskStatus.RUNNING)
        await store_group.task_store.create_task(task)
        config = _make_config(repeated_failure_threshold=3)

        now = datetime.now(UTC)
        for i in range(3):
            event = _make_failure_event("task-022", EventType.SKILL_FAILED, now - timedelta(seconds=i + 1), i + 1)
            await store_group.event_store.append_event_committed(event)

        detector = RepeatedFailureDetector()
        result = await detector.check(task, store_group.event_store, config)

        assert result is not None
        assert all(t == "SKILL_FAILED" for t in result.failure_event_types)

    @pytest.mark.asyncio
    async def test_mixed_failure_types_combined_count(self, store_group: StoreGroup):
        """混合失败类型累计计入（MODEL + TOOL + SKILL 各一次，阈值 3）"""
        task = _make_task("task-023", TaskStatus.RUNNING)
        await store_group.task_store.create_task(task)
        config = _make_config(repeated_failure_threshold=3)

        now = datetime.now(UTC)
        failure_types = [
            EventType.MODEL_CALL_FAILED,
            EventType.TOOL_CALL_FAILED,
            EventType.SKILL_FAILED,
        ]
        for i, ft in enumerate(failure_types):
            event = _make_failure_event("task-023", ft, now - timedelta(seconds=i + 1), i + 1)
            await store_group.event_store.append_event_committed(event)

        detector = RepeatedFailureDetector()
        result = await detector.check(task, store_group.event_store, config)

        assert result is not None
        assert result.failure_count == 3
        # 三种不同失败类型都应出现
        assert "MODEL_CALL_FAILED" in result.failure_event_types
        assert "TOOL_CALL_FAILED" in result.failure_event_types
        assert "SKILL_FAILED" in result.failure_event_types


class TestRepeatedFailureDetectorTimeWindow:
    """时间窗口边界测试"""

    @pytest.mark.asyncio
    async def test_failures_outside_window_not_counted(self, store_group: StoreGroup):
        """窗口外的失败事件不计入"""
        task = _make_task("task-030", TaskStatus.RUNNING)
        await store_group.task_store.create_task(task)
        config = _make_config(failure_window_seconds=60, repeated_failure_threshold=3)

        now = datetime.now(UTC)
        # 写入 3 条窗口外的失败事件（70s 前，超过 60s 窗口）
        for i in range(3):
            event = _make_failure_event(
                "task-030", EventType.MODEL_CALL_FAILED,
                now - timedelta(seconds=70 + i), i + 1,
            )
            await store_group.event_store.append_event_committed(event)

        detector = RepeatedFailureDetector()
        result = await detector.check(task, store_group.event_store, config)

        # 窗口外，不触发
        assert result is None

    @pytest.mark.asyncio
    async def test_partial_failures_in_window(self, store_group: StoreGroup):
        """部分失败在窗口内，部分在窗口外 — 只统计窗口内的"""
        task = _make_task("task-031", TaskStatus.RUNNING)
        await store_group.task_store.create_task(task)
        config = _make_config(failure_window_seconds=60, repeated_failure_threshold=3)

        now = datetime.now(UTC)
        # 2 条在窗口内（30s 前）
        for i in range(2):
            event = _make_failure_event(
                "task-031", EventType.MODEL_CALL_FAILED,
                now - timedelta(seconds=30 + i), i + 1,
            )
            await store_group.event_store.append_event_committed(event)

        # 2 条在窗口外（90s 前）
        for i in range(2):
            event = _make_failure_event(
                "task-031", EventType.TOOL_CALL_FAILED,
                now - timedelta(seconds=90 + i), i + 3,
            )
            await store_group.event_store.append_event_committed(event)

        detector = RepeatedFailureDetector()
        result = await detector.check(task, store_group.event_store, config)

        # 窗口内只有 2 条，未达阈值 3
        assert result is None

    @pytest.mark.asyncio
    async def test_exactly_at_window_boundary(self, store_group: StoreGroup):
        """时间窗口内边界：恰好在 failure_window_seconds 之内的事件被计入"""
        task = _make_task("task-032", TaskStatus.RUNNING)
        await store_group.task_store.create_task(task)
        config = _make_config(failure_window_seconds=60, repeated_failure_threshold=3)

        now = datetime.now(UTC)
        # 3 条在窗口刚刚内（55s 前）
        for i in range(3):
            event = _make_failure_event(
                "task-032", EventType.MODEL_CALL_FAILED,
                now - timedelta(seconds=55 + i * 0.1), i + 1,
            )
            await store_group.event_store.append_event_committed(event)

        detector = RepeatedFailureDetector()
        result = await detector.check(task, store_group.event_store, config)

        assert result is not None
        assert result.failure_count == 3


class TestRepeatedFailureDetectorTerminalStates:
    """终态任务不触发（FR-013）"""

    @pytest.mark.asyncio
    async def test_succeeded_task_not_detected(self):
        """SUCCEEDED 终态任务不触发"""
        task = _make_task("task-040", TaskStatus.SUCCEEDED)
        config = _make_config()
        event_store = AsyncMock()

        detector = RepeatedFailureDetector()
        result = await detector.check(task, event_store, config)

        assert result is None

    @pytest.mark.asyncio
    async def test_failed_task_not_detected(self):
        """FAILED 终态任务不触发"""
        task = _make_task("task-041", TaskStatus.FAILED)
        config = _make_config()
        event_store = AsyncMock()

        detector = RepeatedFailureDetector()
        result = await detector.check(task, event_store, config)

        assert result is None

    @pytest.mark.asyncio
    async def test_cancelled_task_not_detected(self):
        """CANCELLED 终态任务不触发"""
        task = _make_task("task-042", TaskStatus.CANCELLED)
        config = _make_config()
        event_store = AsyncMock()

        detector = RepeatedFailureDetector()
        result = await detector.check(task, event_store, config)

        assert result is None

    @pytest.mark.asyncio
    async def test_rejected_task_not_detected(self):
        """REJECTED 终态任务不触发"""
        task = _make_task("task-043", TaskStatus.REJECTED)
        config = _make_config()
        event_store = AsyncMock()

        detector = RepeatedFailureDetector()
        result = await detector.check(task, event_store, config)

        assert result is None


class TestRepeatedFailureDetectorConstants:
    """FAILURE_EVENT_TYPES 常量验证（FR-012）"""

    def test_failure_event_types_contains_required_types(self):
        """FAILURE_EVENT_TYPES 包含 MODEL_CALL_FAILED/TOOL_CALL_FAILED/SKILL_FAILED"""
        assert EventType.MODEL_CALL_FAILED in FAILURE_EVENT_TYPES
        assert EventType.TOOL_CALL_FAILED in FAILURE_EVENT_TYPES
        assert EventType.SKILL_FAILED in FAILURE_EVENT_TYPES

    def test_failure_event_types_has_exactly_three_types(self):
        """FAILURE_EVENT_TYPES 恰好包含 3 种失败类型"""
        assert len(FAILURE_EVENT_TYPES) == 3
