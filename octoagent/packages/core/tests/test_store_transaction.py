"""事务一致性单元测试 -- T033

测试内容：
1. 事件写入 + projection 更新原子性
2. 回滚验证（事务失败时事件和 projection 都不写入）
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
    TaskStatus,
)
from octoagent.core.models.payloads import StateTransitionPayload
from octoagent.core.store.event_store import SqliteEventStore
from octoagent.core.store.sqlite_init import init_db
from octoagent.core.store.task_store import SqliteTaskStore
from octoagent.core.store.transaction import append_event_and_update_task


@pytest_asyncio.fixture
async def db(tmp_path: Path):
    """提供已初始化的测试数据库"""
    conn = await aiosqlite.connect(str(tmp_path / "test.db"))
    await init_db(conn)
    yield conn
    await conn.close()


@pytest_asyncio.fixture
async def stores(db):
    """提供 TaskStore 和 EventStore 实例"""
    return SqliteTaskStore(db), SqliteEventStore(db), db


class TestTransactionAtomicity:
    """事务一致性测试"""

    async def test_event_and_projection_atomic_success(self, stores):
        """事件写入和 projection 更新在同一事务内成功"""
        task_store, event_store, conn = stores
        now = datetime.now(UTC)

        # 创建任务
        task = Task(
            task_id="01JTEST000000000000000001",
            created_at=now,
            updated_at=now,
            title="事务测试",
            requester=RequesterInfo(channel="web", sender_id="owner"),
        )
        await task_store.create_task(task)
        await conn.commit()

        # 通过事务更新状态
        event = Event(
            event_id="01JEVT000000000000000001",
            task_id="01JTEST000000000000000001",
            task_seq=1,
            ts=now,
            type=EventType.STATE_TRANSITION,
            actor=ActorType.SYSTEM,
            payload=StateTransitionPayload(
                from_status=TaskStatus.CREATED,
                to_status=TaskStatus.RUNNING,
            ).model_dump(),
            trace_id="trace-001",
        )
        await append_event_and_update_task(
            conn, event_store, task_store, event, "RUNNING"
        )

        # 验证事件已写入
        events = await event_store.get_events_for_task("01JTEST000000000000000001")
        assert len(events) == 1

        # 验证 task 状态已更新
        updated_task = await task_store.get_task("01JTEST000000000000000001")
        assert updated_task is not None
        assert updated_task.status == TaskStatus.RUNNING

    async def test_event_and_projection_atomic_rollback(self, stores):
        """事务失败时事件和 projection 都回滚"""
        task_store, event_store, conn = stores
        now = datetime.now(UTC)

        # 创建任务
        task = Task(
            task_id="01JTEST000000000000000002",
            created_at=now,
            updated_at=now,
            title="回滚测试",
            requester=RequesterInfo(channel="web", sender_id="owner"),
        )
        await task_store.create_task(task)
        await conn.commit()

        # 写入第一个事件（成功）
        event_1 = Event(
            event_id="01JEVT000000000000000010",
            task_id="01JTEST000000000000000002",
            task_seq=1,
            ts=now,
            type=EventType.STATE_TRANSITION,
            actor=ActorType.SYSTEM,
            trace_id="trace-002",
        )
        await append_event_and_update_task(
            conn, event_store, task_store, event_1, "RUNNING"
        )

        # 尝试写入重复 event_id（应该失败并回滚）
        event_dup = Event(
            event_id="01JEVT000000000000000010",  # 重复 ID
            task_id="01JTEST000000000000000002",
            task_seq=2,
            ts=now,
            type=EventType.STATE_TRANSITION,
            actor=ActorType.SYSTEM,
            trace_id="trace-002",
        )
        with pytest.raises(Exception):
            await append_event_and_update_task(
                conn, event_store, task_store, event_dup, "SUCCEEDED"
            )

        # 验证状态仍然是 RUNNING（回滚后状态未变）
        current_task = await task_store.get_task("01JTEST000000000000000002")
        assert current_task is not None
        assert current_task.status == TaskStatus.RUNNING

    async def test_append_without_status_keeps_original_status(self, stores):
        """new_status=None 时不应将状态写成空字符串"""
        task_store, event_store, conn = stores
        now = datetime.now(UTC)

        task = Task(
            task_id="01JTEST000000000000000003",
            created_at=now,
            updated_at=now,
            title="状态保持测试",
            requester=RequesterInfo(channel="web", sender_id="owner"),
        )
        await task_store.create_task(task)
        await conn.commit()

        event = Event(
            event_id="01JEVT000000000000000099",
            task_id="01JTEST000000000000000003",
            task_seq=1,
            ts=now,
            type=EventType.USER_MESSAGE,
            actor=ActorType.USER,
            trace_id="trace-003",
        )
        await append_event_and_update_task(
            conn,
            event_store,
            task_store,
            event,
            new_status=None,
        )

        updated_task = await task_store.get_task("01JTEST000000000000000003")
        assert updated_task is not None
        assert updated_task.status == TaskStatus.CREATED
