"""idempotency_key 唯一约束测试 -- T035

测试内容：
1. idempotency_key 存在时返回关联的 task_id
2. idempotency_key 不存在时返回 None
3. 重复 idempotency_key 数据库层报错
"""

from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio
from octoagent.core.models import (
    ActorType,
    Event,
    EventCausality,
    EventType,
    RequesterInfo,
    Task,
)
from octoagent.core.store.event_store import SqliteEventStore
from octoagent.core.store.sqlite_init import init_db
from octoagent.core.store.task_store import SqliteTaskStore


@pytest_asyncio.fixture
async def db(tmp_path: Path):
    conn = await aiosqlite.connect(str(tmp_path / "test.db"))
    await init_db(conn)
    yield conn
    await conn.close()


@pytest_asyncio.fixture
async def stores(db):
    task_store = SqliteTaskStore(db)
    event_store = SqliteEventStore(db)

    now = datetime.now(UTC)
    task = Task(
        task_id="01JTEST_IDEM_0000000000001",
        created_at=now,
        updated_at=now,
        title="幂等测试",
        requester=RequesterInfo(channel="web", sender_id="owner"),
    )
    await task_store.create_task(task)
    await db.commit()
    return event_store, db


class TestIdempotency:
    """idempotency_key 唯一约束测试"""

    async def test_check_nonexistent_key_returns_none(self, stores):
        """不存在的 key 返回 None"""
        event_store, _ = stores
        result = await event_store.check_idempotency_key("nonexistent-key")
        assert result is None

    async def test_check_existing_key_returns_task_id(self, stores):
        """存在的 key 返回关联的 task_id"""
        event_store, conn = stores
        now = datetime.now(UTC)

        event = Event(
            event_id="01JEVT_IDEM_0000000000001",
            task_id="01JTEST_IDEM_0000000000001",
            task_seq=1,
            ts=now,
            type=EventType.TASK_CREATED,
            actor=ActorType.SYSTEM,
            trace_id="trace-idem",
            causality=EventCausality(idempotency_key="msg-unique-001"),
        )
        await event_store.append_event(event)
        await conn.commit()

        result = await event_store.check_idempotency_key("msg-unique-001")
        assert result == "01JTEST_IDEM_0000000000001"

    async def test_duplicate_idempotency_key_raises_error(self, stores):
        """重复 idempotency_key 数据库层报错"""
        event_store, conn = stores
        now = datetime.now(UTC)

        event_1 = Event(
            event_id="01JEVT_IDEM_0000000000010",
            task_id="01JTEST_IDEM_0000000000001",
            task_seq=1,
            ts=now,
            type=EventType.TASK_CREATED,
            actor=ActorType.SYSTEM,
            trace_id="trace-idem-dup",
            causality=EventCausality(idempotency_key="msg-dup-key"),
        )
        await event_store.append_event(event_1)
        await conn.commit()

        event_2 = Event(
            event_id="01JEVT_IDEM_0000000000011",
            task_id="01JTEST_IDEM_0000000000001",
            task_seq=2,
            ts=now,
            type=EventType.USER_MESSAGE,
            actor=ActorType.USER,
            trace_id="trace-idem-dup",
            causality=EventCausality(idempotency_key="msg-dup-key"),  # 重复 key
        )
        with pytest.raises(Exception):  # IntegrityError
            await event_store.append_event(event_2)
            await conn.commit()

    async def test_null_idempotency_key_allowed_multiple(self, stores):
        """NULL idempotency_key 允许多条（UNIQUE WHERE NOT NULL）"""
        event_store, conn = stores
        now = datetime.now(UTC)

        for i in range(3):
            event = Event(
                event_id=f"01JEVT_NULL_{i:020d}",
                task_id="01JTEST_IDEM_0000000000001",
                task_seq=i + 1,
                ts=now,
                type=EventType.STATE_TRANSITION,
                actor=ActorType.SYSTEM,
                trace_id="trace-null",
                # idempotency_key 默认为 None
            )
            await event_store.append_event(event)
            await conn.commit()

        # 应该成功写入 3 条无 idempotency_key 的事件
        events = await event_store.get_events_for_task("01JTEST_IDEM_0000000000001")
        assert len(events) == 3
