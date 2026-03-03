"""TaskJournalService 单元测试 -- Feature 011 T030

覆盖：
- 四分组分类逻辑
- task_status 使用内部 TaskStatus 不降级为 A2A
- drift_summary 摘要字段
- drift_artifact_id 引用字段
- 空数据库场景
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

from octoagent.core.models.enums import ActorType, EventType, RiskLevel, TaskStatus
from octoagent.core.models.event import Event, EventCausality
from octoagent.core.models.task import RequesterInfo, Task, TaskPointers
from octoagent.core.store import StoreGroup
from octoagent.core.store.sqlite_init import init_db
from octoagent.gateway.services.task_journal import TaskJournalService
from octoagent.gateway.services.watchdog.config import WatchdogConfig


def _make_event_id() -> str:
    import ulid
    return str(ulid.ULID())


def _make_task(task_id: str, status: TaskStatus, updated_ago_s: float = 0.0) -> Task:
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


def _make_event_obj(
    task_id: str,
    event_type: EventType,
    ts: datetime,
    task_seq: int,
    payload: dict | None = None,
) -> Event:
    return Event(
        event_id=_make_event_id(),
        task_id=task_id,
        task_seq=task_seq,
        ts=ts,
        type=event_type,
        schema_version=1,
        actor=ActorType.SYSTEM,
        payload=payload or {},
        trace_id="trace-test",
        span_id="",
        causality=EventCausality(),
    )


@pytest_asyncio.fixture
async def db_conn():
    conn = await aiosqlite.connect(":memory:")
    await init_db(conn)
    yield conn
    await conn.close()


@pytest_asyncio.fixture
async def store_group(db_conn: aiosqlite.Connection, tmp_path: Path) -> StoreGroup:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    return StoreGroup(conn=db_conn, artifacts_dir=artifacts_dir)


def _make_config(threshold_s: int = 45) -> WatchdogConfig:
    """threshold = no_progress_cycles × scan_interval"""
    # 3 cycles × 15s = 45s
    return WatchdogConfig(scan_interval_seconds=15, no_progress_cycles=3)


class TestTaskJournalServiceEmpty:
    """空数据库场景"""

    @pytest.mark.asyncio
    async def test_empty_db_returns_zero_counts(self, store_group: StoreGroup):
        """无任务时返回全零统计"""
        service = TaskJournalService(store_group=store_group)
        response = await service.get_journal(config=_make_config())

        assert response.summary.total == 0
        assert response.summary.running == 0
        assert response.summary.stalled == 0
        assert response.summary.drifted == 0
        assert response.summary.waiting_approval == 0
        assert response.groups.running == []
        assert response.groups.stalled == []
        assert response.groups.drifted == []
        assert response.groups.waiting_approval == []


class TestTaskJournalServiceGrouping:
    """四分组分类逻辑测试"""

    @pytest.mark.asyncio
    async def test_running_task_with_recent_progress(self, store_group: StoreGroup):
        """有近期进展事件的任务归为 running"""
        task = _make_task("task-001", TaskStatus.RUNNING, updated_ago_s=0)
        await store_group.task_store.create_task(task)

        # 写入近期进展事件（10 秒前）
        recent_event = _make_event_obj(
            "task-001",
            EventType.MODEL_CALL_COMPLETED,
            datetime.now(UTC) - timedelta(seconds=10),
            task_seq=1,
        )
        await store_group.event_store.append_event_committed(recent_event)

        service = TaskJournalService(store_group=store_group)
        response = await service.get_journal(config=_make_config())

        assert response.summary.running == 1
        assert response.summary.stalled == 0
        assert len(response.groups.running) == 1
        assert response.groups.running[0]["task_id"] == "task-001"

    @pytest.mark.asyncio
    async def test_stalled_task_no_progress_no_drift_event(self, store_group: StoreGroup):
        """超过阈值无进展、无 DRIFT 事件 -> stalled"""
        # 创建 60 秒前更新的任务（超过 45s 阈值）
        task = _make_task("task-002", TaskStatus.RUNNING, updated_ago_s=60)
        await store_group.task_store.create_task(task)
        # 不写入任何进展事件，也不写入 DRIFT 事件

        service = TaskJournalService(store_group=store_group)
        response = await service.get_journal(config=_make_config())

        assert response.summary.stalled == 1
        assert response.summary.running == 0
        assert len(response.groups.stalled) == 1
        assert response.groups.stalled[0]["task_id"] == "task-002"
        assert response.groups.stalled[0]["journal_state"] == "stalled"

    @pytest.mark.asyncio
    async def test_drifted_task_with_drift_event_no_progress(self, store_group: StoreGroup):
        """有 DRIFT 事件且仍无进展 -> drifted"""
        task = _make_task("task-003", TaskStatus.RUNNING, updated_ago_s=90)
        await store_group.task_store.create_task(task)

        # 写入 DRIFT 事件（20 秒前）
        drift_payload = {
            "drift_type": "no_progress",
            "stall_duration_seconds": 75.0,
            "task_id": "task-003",
            "trace_id": "trace-003",
            "detected_at": (datetime.now(UTC) - timedelta(seconds=20)).isoformat(),
            "suggested_actions": ["check_worker_logs", "cancel_task_if_confirmed"],
        }
        drift_event = _make_event_obj(
            "task-003",
            EventType.TASK_DRIFT_DETECTED,
            datetime.now(UTC) - timedelta(seconds=20),
            task_seq=1,
            payload=drift_payload,
        )
        await store_group.event_store.append_event_committed(drift_event)

        service = TaskJournalService(store_group=store_group)
        response = await service.get_journal(config=_make_config())

        assert response.summary.drifted == 1
        assert len(response.groups.drifted) == 1
        entry = response.groups.drifted[0]
        assert entry["task_id"] == "task-003"
        assert entry["journal_state"] == "drifted"
        assert entry["drift_summary"] is not None
        assert entry["drift_summary"]["drift_type"] == "no_progress"
        assert entry["drift_summary"]["stall_duration_seconds"] == 75.0

    @pytest.mark.asyncio
    async def test_waiting_approval_task(self, store_group: StoreGroup):
        """WAITING_APPROVAL 任务始终独立归组"""
        task = _make_task("task-004", TaskStatus.WAITING_APPROVAL, updated_ago_s=0)
        await store_group.task_store.create_task(task)

        service = TaskJournalService(store_group=store_group)
        response = await service.get_journal(config=_make_config())

        assert response.summary.waiting_approval == 1
        assert len(response.groups.waiting_approval) == 1
        entry = response.groups.waiting_approval[0]
        assert entry["task_id"] == "task-004"
        assert entry["journal_state"] == "waiting_approval"

    @pytest.mark.asyncio
    async def test_task_status_uses_internal_enum_not_a2a(self, store_group: StoreGroup):
        """FR-015: task_status 使用内部 TaskStatus 值，不映射为 A2A 状态（Constitution 原则 14）"""
        task = _make_task("task-005", TaskStatus.WAITING_APPROVAL)
        await store_group.task_store.create_task(task)

        service = TaskJournalService(store_group=store_group)
        response = await service.get_journal(config=_make_config())

        entry = response.groups.waiting_approval[0]
        # 应使用内部完整枚举值 "WAITING_APPROVAL"，而不是 A2A 状态 "active"/"pending"
        assert entry["task_status"] == "WAITING_APPROVAL"
        assert entry["task_status"] != "active"
        assert entry["task_status"] != "pending"

    @pytest.mark.asyncio
    async def test_drifted_task_recovered_becomes_running(self, store_group: StoreGroup):
        """有 DRIFT 事件但已恢复进展 -> running（US2 验收场景 3）"""
        task = _make_task("task-006", TaskStatus.RUNNING, updated_ago_s=10)
        await store_group.task_store.create_task(task)

        # 写入旧 DRIFT 事件（已触发过漂移）
        drift_event = _make_event_obj(
            "task-006",
            EventType.TASK_DRIFT_DETECTED,
            datetime.now(UTC) - timedelta(seconds=200),
            task_seq=1,
            payload={
                "drift_type": "no_progress",
                "stall_duration_seconds": 60.0,
                "task_id": "task-006",
                "trace_id": "trace-006",
                "detected_at": (datetime.now(UTC) - timedelta(seconds=200)).isoformat(),
                "suggested_actions": [],
            },
        )
        await store_group.event_store.append_event_committed(drift_event)

        # 写入近期进展事件（已恢复）
        recent_event = _make_event_obj(
            "task-006",
            EventType.MODEL_CALL_COMPLETED,
            datetime.now(UTC) - timedelta(seconds=5),
            task_seq=2,
        )
        await store_group.event_store.append_event_committed(recent_event)

        service = TaskJournalService(store_group=store_group)
        response = await service.get_journal(config=_make_config())

        # 已恢复，应归为 running
        assert response.summary.running == 1
        assert response.summary.drifted == 0

    @pytest.mark.asyncio
    async def test_summary_counts_match_groups(self, store_group: StoreGroup):
        """summary 计数与 groups 列表长度一致"""
        tasks_data = [
            ("t1", TaskStatus.RUNNING, 0.0),          # running（有近期进展）
            ("t2", TaskStatus.RUNNING, 90.0),          # stalled（超阈值无进展）
            ("t3", TaskStatus.WAITING_APPROVAL, 0.0),  # waiting_approval
        ]
        for task_id, status, ago in tasks_data:
            t = _make_task(task_id, status, updated_ago_s=ago)
            await store_group.task_store.create_task(t)

        # 为 t1 写入近期进展事件
        await store_group.event_store.append_event_committed(
            _make_event_obj("t1", EventType.TOOL_CALL_COMPLETED, datetime.now(UTC) - timedelta(seconds=5), 1)
        )

        service = TaskJournalService(store_group=store_group)
        response = await service.get_journal(config=_make_config())

        assert response.summary.total == 3
        assert response.summary.running == len(response.groups.running)
        assert response.summary.stalled == len(response.groups.stalled)
        assert response.summary.drifted == len(response.groups.drifted)
        assert response.summary.waiting_approval == len(response.groups.waiting_approval)
