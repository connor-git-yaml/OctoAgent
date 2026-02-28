"""task_seq 单调递增测试 -- T034

测试内容：
1. 同一 task 内 task_seq 严格单调递增
2. 重复 task_seq 数据库层报错
"""

from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio
from octoagent.core.models import (
    ActorType,
    Event,
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

    # 创建测试任务
    now = datetime.now(UTC)
    task = Task(
        task_id="01JTEST_SEQ_00000000000001",
        created_at=now,
        updated_at=now,
        title="seq 测试",
        requester=RequesterInfo(channel="web", sender_id="owner"),
    )
    await task_store.create_task(task)
    await db.commit()
    return event_store, db


class TestEventSeq:
    """task_seq 单调递增测试"""

    async def test_get_next_task_seq_starts_at_1(self, stores):
        """新任务的第一个 task_seq 为 1"""
        event_store, _ = stores
        seq = await event_store.get_next_task_seq("01JTEST_SEQ_00000000000001")
        assert seq == 1

    async def test_task_seq_increments(self, stores):
        """task_seq 严格递增"""
        event_store, conn = stores
        now = datetime.now(UTC)

        for i in range(1, 4):
            event = Event(
                event_id=f"01JEVT_SEQ_{i:020d}",
                task_id="01JTEST_SEQ_00000000000001",
                task_seq=i,
                ts=now,
                type=EventType.TASK_CREATED,
                actor=ActorType.SYSTEM,
                trace_id="trace-seq",
            )
            await event_store.append_event(event)
            await conn.commit()

        # 下一个应该是 4
        seq = await event_store.get_next_task_seq("01JTEST_SEQ_00000000000001")
        assert seq == 4

    async def test_duplicate_task_seq_raises_error(self, stores):
        """重复 task_seq 数据库层报错（UNIQUE INDEX 约束）"""
        event_store, conn = stores
        now = datetime.now(UTC)

        # 写入 task_seq=1
        event_1 = Event(
            event_id="01JEVT_DUP_00000000000001",
            task_id="01JTEST_SEQ_00000000000001",
            task_seq=1,
            ts=now,
            type=EventType.TASK_CREATED,
            actor=ActorType.SYSTEM,
            trace_id="trace-dup",
        )
        await event_store.append_event(event_1)
        await conn.commit()

        # 再次写入 task_seq=1（应该失败）
        event_dup = Event(
            event_id="01JEVT_DUP_00000000000002",
            task_id="01JTEST_SEQ_00000000000001",
            task_seq=1,  # 重复！
            ts=now,
            type=EventType.USER_MESSAGE,
            actor=ActorType.USER,
            trace_id="trace-dup",
        )
        with pytest.raises(Exception):  # IntegrityError
            await event_store.append_event(event_dup)
            await conn.commit()
