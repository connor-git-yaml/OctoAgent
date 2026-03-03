"""EventStore 扩展方法单元测试 -- Feature 011 T012

测试 get_latest_event_ts 和 get_events_by_types_since 的正确性，
覆盖空事件/正常查询/类型过滤/时间边界场景。
"""

from datetime import UTC, datetime, timedelta

import aiosqlite
import pytest
import pytest_asyncio

from octoagent.core.models.enums import ActorType, EventType
from octoagent.core.models.event import Event, EventCausality
from octoagent.core.store.event_store import SqliteEventStore
from octoagent.core.store.sqlite_init import init_db


def _make_event(
    task_id: str,
    event_type: EventType,
    ts: datetime,
    task_seq: int = 1,
    trace_id: str = "trace-test",
) -> Event:
    """创建测试用 Event 对象"""
    import ulid

    return Event(
        event_id=str(ulid.ULID()),
        task_id=task_id,
        task_seq=task_seq,
        ts=ts,
        type=event_type,
        schema_version=1,
        actor=ActorType.SYSTEM,
        payload={},
        trace_id=trace_id,
        span_id="",
        causality=EventCausality(),
    )


@pytest_asyncio.fixture
async def db_conn():
    """内存 SQLite 连接，已初始化 schema"""
    conn = await aiosqlite.connect(":memory:")
    await init_db(conn)
    # 插入测试任务（外键约束）
    await conn.execute(
        "INSERT INTO tasks (task_id, created_at, updated_at, status) VALUES (?, ?, ?, ?)",
        ("task-001", datetime.now(UTC).isoformat(), datetime.now(UTC).isoformat(), "RUNNING"),
    )
    await conn.commit()
    yield conn
    await conn.close()


@pytest_asyncio.fixture
async def event_store(db_conn: aiosqlite.Connection) -> SqliteEventStore:
    """已初始化的 EventStore"""
    return SqliteEventStore(db_conn)


class TestGetLatestEventTs:
    """get_latest_event_ts 方法测试"""

    @pytest.mark.asyncio
    async def test_empty_events_returns_none(self, event_store: SqliteEventStore):
        """无事件时返回 None"""
        result = await event_store.get_latest_event_ts("task-001")
        assert result is None

    @pytest.mark.asyncio
    async def test_single_event_returns_its_ts(self, event_store: SqliteEventStore):
        """单事件时返回该事件的时间戳"""
        ts = datetime(2026, 3, 3, 10, 0, 0, tzinfo=UTC)
        event = _make_event("task-001", EventType.TASK_CREATED, ts, task_seq=1)
        await event_store.append_event_committed(event)

        result = await event_store.get_latest_event_ts("task-001")
        assert result is not None
        # 比较秒级精度（ISO 序列化可能损失微秒）
        assert abs((result - ts).total_seconds()) < 1.0

    @pytest.mark.asyncio
    async def test_multiple_events_returns_latest(self, event_store: SqliteEventStore):
        """多事件时返回最晚的时间戳"""
        ts_early = datetime(2026, 3, 3, 10, 0, 0, tzinfo=UTC)
        ts_late = datetime(2026, 3, 3, 10, 5, 0, tzinfo=UTC)

        e1 = _make_event("task-001", EventType.TASK_CREATED, ts_early, task_seq=1)
        e2 = _make_event("task-001", EventType.MODEL_CALL_STARTED, ts_late, task_seq=2)
        await event_store.append_event_committed(e1)
        await event_store.append_event_committed(e2)

        result = await event_store.get_latest_event_ts("task-001")
        assert result is not None
        assert abs((result - ts_late).total_seconds()) < 1.0

    @pytest.mark.asyncio
    async def test_nonexistent_task_returns_none(self, event_store: SqliteEventStore):
        """不存在的任务返回 None"""
        result = await event_store.get_latest_event_ts("nonexistent-task")
        assert result is None


class TestGetEventsByTypesSince:
    """get_events_by_types_since 方法测试"""

    @pytest.mark.asyncio
    async def test_empty_event_types_returns_empty(self, event_store: SqliteEventStore):
        """空类型列表返回空结果"""
        result = await event_store.get_events_by_types_since(
            task_id="task-001",
            event_types=[],
            since_ts=datetime(2026, 3, 3, 10, 0, 0, tzinfo=UTC),
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_no_matching_events_returns_empty(self, event_store: SqliteEventStore):
        """无匹配事件时返回空列表"""
        ts = datetime(2026, 3, 3, 10, 0, 0, tzinfo=UTC)
        e = _make_event("task-001", EventType.TASK_CREATED, ts, task_seq=1)
        await event_store.append_event_committed(e)

        # 查询不同类型
        result = await event_store.get_events_by_types_since(
            task_id="task-001",
            event_types=[EventType.MODEL_CALL_COMPLETED],
            since_ts=ts - timedelta(minutes=1),
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_type_filter_correct(self, event_store: SqliteEventStore):
        """类型过滤正确，只返回指定类型的事件"""
        base_ts = datetime(2026, 3, 3, 10, 0, 0, tzinfo=UTC)

        e1 = _make_event("task-001", EventType.TASK_CREATED, base_ts, task_seq=1)
        e2 = _make_event("task-001", EventType.MODEL_CALL_STARTED, base_ts + timedelta(seconds=10), task_seq=2)
        e3 = _make_event("task-001", EventType.TOOL_CALL_STARTED, base_ts + timedelta(seconds=20), task_seq=3)
        for e in [e1, e2, e3]:
            await event_store.append_event_committed(e)

        result = await event_store.get_events_by_types_since(
            task_id="task-001",
            event_types=[EventType.MODEL_CALL_STARTED, EventType.TOOL_CALL_STARTED],
            since_ts=base_ts - timedelta(seconds=1),
        )
        assert len(result) == 2
        types = {r.type for r in result}
        assert EventType.MODEL_CALL_STARTED in types
        assert EventType.TOOL_CALL_STARTED in types

    @pytest.mark.asyncio
    async def test_time_boundary_since_ts(self, event_store: SqliteEventStore):
        """时间边界：只返回 since_ts 之后的事件"""
        base_ts = datetime(2026, 3, 3, 10, 0, 0, tzinfo=UTC)
        boundary = base_ts + timedelta(seconds=30)

        # 在 boundary 之前
        e1 = _make_event("task-001", EventType.MODEL_CALL_STARTED, base_ts + timedelta(seconds=10), task_seq=1)
        # 恰好在 boundary（含边界）
        e2 = _make_event("task-001", EventType.MODEL_CALL_STARTED, boundary, task_seq=2)
        # 在 boundary 之后
        e3 = _make_event("task-001", EventType.MODEL_CALL_STARTED, base_ts + timedelta(seconds=50), task_seq=3)
        for e in [e1, e2, e3]:
            await event_store.append_event_committed(e)

        result = await event_store.get_events_by_types_since(
            task_id="task-001",
            event_types=[EventType.MODEL_CALL_STARTED],
            since_ts=boundary,
        )
        # 应包含 e2（boundary）和 e3（之后）
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_multiple_types_query(self, event_store: SqliteEventStore):
        """Feature 011 新增事件类型可被查询"""
        base_ts = datetime(2026, 3, 3, 10, 0, 0, tzinfo=UTC)
        e1 = _make_event("task-001", EventType.TASK_DRIFT_DETECTED, base_ts, task_seq=1)
        e2 = _make_event("task-001", EventType.TASK_HEARTBEAT, base_ts + timedelta(seconds=5), task_seq=2)
        for e in [e1, e2]:
            await event_store.append_event_committed(e)

        result = await event_store.get_events_by_types_since(
            task_id="task-001",
            event_types=[EventType.TASK_DRIFT_DETECTED, EventType.TASK_HEARTBEAT],
            since_ts=base_ts - timedelta(seconds=1),
        )
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_ordered_by_task_seq(self, event_store: SqliteEventStore):
        """结果按 task_seq 正序排列"""
        base_ts = datetime(2026, 3, 3, 10, 0, 0, tzinfo=UTC)
        for i in range(3):
            e = _make_event(
                "task-001",
                EventType.MODEL_CALL_STARTED,
                base_ts + timedelta(seconds=i * 10),
                task_seq=i + 1,
            )
            await event_store.append_event_committed(e)

        result = await event_store.get_events_by_types_since(
            task_id="task-001",
            event_types=[EventType.MODEL_CALL_STARTED],
            since_ts=base_ts - timedelta(seconds=1),
        )
        assert len(result) == 3
        seqs = [r.task_seq for r in result]
        assert seqs == sorted(seqs)
