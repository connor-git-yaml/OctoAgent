"""进程重启持久性测试 -- T052

测试内容：
1. 创建任务 → 关闭 DB 连接 → 重新打开 → 验证数据完整
2. WAL 模式验证
"""

from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
from octoagent.core.models import (
    ActorType,
    Event,
    EventCausality,
    EventType,
    RequesterInfo,
    Task,
    TaskStatus,
)
from octoagent.core.store.event_store import SqliteEventStore
from octoagent.core.store.sqlite_init import init_db, verify_wal_mode
from octoagent.core.store.task_store import SqliteTaskStore


class TestDurability:
    """US-5: 进程重启后任务不丢失"""

    async def test_data_survives_restart(self, tmp_path: Path):
        """创建任务 → 关闭 → 重新打开 → 数据完整"""
        db_path = str(tmp_path / "durability.db")
        now = datetime.now(UTC)

        # 第一次连接：创建数据
        conn1 = await aiosqlite.connect(db_path)
        await init_db(conn1)
        task_store = SqliteTaskStore(conn1)
        event_store = SqliteEventStore(conn1)

        # 创建任务
        task = Task(
            task_id="01JTEST_DUR_00000000000001",
            created_at=now,
            updated_at=now,
            title="持久性测试任务",
            requester=RequesterInfo(channel="web", sender_id="owner"),
        )
        await task_store.create_task(task)
        await conn1.commit()

        # 写入事件
        event = Event(
            event_id="01JEVT_DUR_00000000000001",
            task_id="01JTEST_DUR_00000000000001",
            task_seq=1,
            ts=now,
            type=EventType.TASK_CREATED,
            actor=ActorType.SYSTEM,
            trace_id="trace-dur",
            causality=EventCausality(idempotency_key="dur-key-001"),
        )
        await event_store.append_event(event)
        await conn1.commit()

        # 关闭连接（模拟进程终止）
        await conn1.close()

        # 第二次连接：验证数据
        conn2 = await aiosqlite.connect(db_path)
        await init_db(conn2)
        task_store2 = SqliteTaskStore(conn2)
        event_store2 = SqliteEventStore(conn2)

        # 验证任务
        restored_task = await task_store2.get_task("01JTEST_DUR_00000000000001")
        assert restored_task is not None
        assert restored_task.title == "持久性测试任务"
        assert restored_task.status == TaskStatus.CREATED

        # 验证事件
        events = await event_store2.get_events_for_task("01JTEST_DUR_00000000000001")
        assert len(events) == 1
        assert events[0].type == EventType.TASK_CREATED

        # 验证幂等键
        existing = await event_store2.check_idempotency_key("dur-key-001")
        assert existing == "01JTEST_DUR_00000000000001"

        await conn2.close()

    async def test_wal_mode_enabled(self, tmp_path: Path):
        """WAL 模式正确启用"""
        db_path = str(tmp_path / "wal_test.db")
        conn = await aiosqlite.connect(db_path)
        await init_db(conn)

        is_wal = await verify_wal_mode(conn)
        assert is_wal is True

        await conn.close()

    async def test_multiple_tasks_survive_restart(self, tmp_path: Path):
        """多个任务在重启后均完整"""
        db_path = str(tmp_path / "multi_dur.db")
        now = datetime.now(UTC)

        # 创建多个任务
        conn1 = await aiosqlite.connect(db_path)
        await init_db(conn1)
        task_store = SqliteTaskStore(conn1)

        for i in range(5):
            task = Task(
                task_id=f"01JTEST_MULTI_{i:018d}",
                created_at=now,
                updated_at=now,
                title=f"任务 {i}",
                requester=RequesterInfo(channel="web", sender_id="owner"),
            )
            await task_store.create_task(task)
        await conn1.commit()
        await conn1.close()

        # 重新打开并验证
        conn2 = await aiosqlite.connect(db_path)
        await init_db(conn2)
        task_store2 = SqliteTaskStore(conn2)
        all_tasks = await task_store2.list_tasks()
        assert len(all_tasks) == 5

        await conn2.close()
