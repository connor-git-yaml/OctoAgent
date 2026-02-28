"""US-9 Projection 重建测试 -- T070

测试内容：
1. 重建前后状态一致性
2. 多任务重建正确性
3. 空数据库重建不报错
4. 重建后事件数返回正确
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest_asyncio
from octoagent.core.models.enums import (
    ActorType,
    EventType,
    TaskStatus,
)
from octoagent.core.models.event import Event
from octoagent.core.models.payloads import (
    StateTransitionPayload,
    TaskCreatedPayload,
    UserMessagePayload,
)
from octoagent.core.models.task import RequesterInfo, Task
from octoagent.core.projection import apply_event, rebuild_all
from octoagent.core.store import create_store_group
from octoagent.core.store.transaction import append_event_and_update_task, append_event_only


@pytest_asyncio.fixture
async def store_group(tmp_path: Path):
    """创建测试用 StoreGroup"""
    sg = await create_store_group(
        str(tmp_path / "test.db"),
        str(tmp_path / "artifacts"),
    )
    yield sg
    await sg.conn.close()


class TestApplyEvent:
    """单事件应用测试"""

    def test_apply_task_created(self):
        """TASK_CREATED 事件应创建新 Task"""
        tasks = {}
        event = Event(
            event_id="EVT001",
            task_id="TSK001",
            task_seq=1,
            ts=datetime(2026, 1, 1, tzinfo=UTC),
            type=EventType.TASK_CREATED,
            actor=ActorType.SYSTEM,
            payload=TaskCreatedPayload(
                title="Test task",
                thread_id="default",
                scope_id="chat:web:default",
                channel="web",
                sender_id="owner",
            ).model_dump(),
            trace_id="trace-TSK001",
        )
        apply_event(tasks, event)

        assert "TSK001" in tasks
        assert tasks["TSK001"].status == TaskStatus.CREATED
        assert tasks["TSK001"].title == "Test task"

    def test_apply_state_transition(self):
        """STATE_TRANSITION 事件应更新状态"""
        tasks = {}
        # 先创建
        create_event = Event(
            event_id="EVT001",
            task_id="TSK001",
            task_seq=1,
            ts=datetime(2026, 1, 1, tzinfo=UTC),
            type=EventType.TASK_CREATED,
            actor=ActorType.SYSTEM,
            payload=TaskCreatedPayload(
                title="Test",
                thread_id="default",
                scope_id="chat:web:default",
                channel="web",
                sender_id="owner",
            ).model_dump(),
            trace_id="trace-TSK001",
        )
        apply_event(tasks, create_event)

        # 状态流转
        transition_event = Event(
            event_id="EVT002",
            task_id="TSK001",
            task_seq=2,
            ts=datetime(2026, 1, 1, 0, 0, 5, tzinfo=UTC),
            type=EventType.STATE_TRANSITION,
            actor=ActorType.SYSTEM,
            payload=StateTransitionPayload(
                from_status=TaskStatus.CREATED,
                to_status=TaskStatus.RUNNING,
            ).model_dump(),
            trace_id="trace-TSK001",
        )
        apply_event(tasks, transition_event)

        assert tasks["TSK001"].status == TaskStatus.RUNNING
        assert tasks["TSK001"].pointers.latest_event_id == "EVT002"

    def test_apply_other_event_updates_pointers(self):
        """非状态变更事件应更新 updated_at 和 pointers"""
        tasks = {}
        create_event = Event(
            event_id="EVT001",
            task_id="TSK001",
            task_seq=1,
            ts=datetime(2026, 1, 1, tzinfo=UTC),
            type=EventType.TASK_CREATED,
            actor=ActorType.SYSTEM,
            payload=TaskCreatedPayload(
                title="Test",
                thread_id="default",
                scope_id="chat:web:default",
                channel="web",
                sender_id="owner",
            ).model_dump(),
            trace_id="trace-TSK001",
        )
        apply_event(tasks, create_event)

        user_msg_event = Event(
            event_id="EVT002",
            task_id="TSK001",
            task_seq=2,
            ts=datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC),
            type=EventType.USER_MESSAGE,
            actor=ActorType.USER,
            payload=UserMessagePayload(
                text_preview="Hello",
                text_length=5,
            ).model_dump(),
            trace_id="trace-TSK001",
        )
        apply_event(tasks, user_msg_event)

        # 状态不变，但 pointers 更新
        assert tasks["TSK001"].status == TaskStatus.CREATED
        assert tasks["TSK001"].pointers.latest_event_id == "EVT002"


class TestRebuildAll:
    """全量重建测试"""

    async def test_rebuild_empty_db(self, store_group):
        """空数据库重建应返回 0"""
        event_count = await rebuild_all(
            store_group.conn,
            store_group.event_store,
            store_group.task_store,
        )
        assert event_count == 0

    async def test_rebuild_single_task(self, store_group):
        """单任务重建一致性"""
        conn = store_group.conn
        es = store_group.event_store
        ts = store_group.task_store

        # 创建原始任务
        now = datetime(2026, 1, 1, tzinfo=UTC)
        task = Task(
            task_id="TSK001",
            created_at=now,
            updated_at=now,
            status=TaskStatus.CREATED,
            title="Test rebuild",
            thread_id="default",
            scope_id="chat:web:default",
            requester=RequesterInfo(channel="web", sender_id="owner"),
        )
        await ts.create_task(task)
        await conn.commit()

        # 写入事件
        evt1 = Event(
            event_id="EVT001",
            task_id="TSK001",
            task_seq=1,
            ts=now,
            type=EventType.TASK_CREATED,
            actor=ActorType.SYSTEM,
            payload=TaskCreatedPayload(
                title="Test rebuild",
                thread_id="default",
                scope_id="chat:web:default",
                channel="web",
                sender_id="owner",
            ).model_dump(),
            trace_id="trace-TSK001",
        )
        await append_event_only(conn, es, evt1)

        evt2 = Event(
            event_id="EVT002",
            task_id="TSK001",
            task_seq=2,
            ts=datetime(2026, 1, 1, 0, 0, 5, tzinfo=UTC),
            type=EventType.STATE_TRANSITION,
            actor=ActorType.SYSTEM,
            payload=StateTransitionPayload(
                from_status=TaskStatus.CREATED,
                to_status=TaskStatus.RUNNING,
            ).model_dump(),
            trace_id="trace-TSK001",
        )
        await append_event_and_update_task(conn, es, ts, evt2, "RUNNING")

        evt3 = Event(
            event_id="EVT003",
            task_id="TSK001",
            task_seq=3,
            ts=datetime(2026, 1, 1, 0, 0, 10, tzinfo=UTC),
            type=EventType.STATE_TRANSITION,
            actor=ActorType.SYSTEM,
            payload=StateTransitionPayload(
                from_status=TaskStatus.RUNNING,
                to_status=TaskStatus.SUCCEEDED,
            ).model_dump(),
            trace_id="trace-TSK001",
        )
        await append_event_and_update_task(conn, es, ts, evt3, "SUCCEEDED")

        # 记录重建前的状态
        original_task = await ts.get_task("TSK001")
        assert original_task.status == TaskStatus.SUCCEEDED

        # 执行重建
        event_count = await rebuild_all(conn, es, ts)

        assert event_count == 3

        # 验证重建后状态一致
        rebuilt_task = await ts.get_task("TSK001")
        assert rebuilt_task is not None
        assert rebuilt_task.status == TaskStatus.SUCCEEDED
        assert rebuilt_task.title == "Test rebuild"
        assert rebuilt_task.pointers.latest_event_id == "EVT003"

    async def test_rebuild_multiple_tasks(self, store_group):
        """多任务重建一致性"""
        conn = store_group.conn
        es = store_group.event_store
        ts = store_group.task_store

        now = datetime(2026, 1, 1, tzinfo=UTC)

        # 创建任务 1（SUCCEEDED）
        task1 = Task(
            task_id="TSK001",
            created_at=now,
            updated_at=now,
            status=TaskStatus.CREATED,
            title="Task one",
            requester=RequesterInfo(channel="web", sender_id="owner"),
        )
        await ts.create_task(task1)
        await conn.commit()

        evt1 = Event(
            event_id="EVT001",
            task_id="TSK001",
            task_seq=1,
            ts=now,
            type=EventType.TASK_CREATED,
            actor=ActorType.SYSTEM,
            payload=TaskCreatedPayload(
                title="Task one", thread_id="default",
                scope_id="", channel="web", sender_id="owner",
            ).model_dump(),
            trace_id="trace-TSK001",
        )
        await append_event_only(conn, es, evt1)

        evt2 = Event(
            event_id="EVT002",
            task_id="TSK001",
            task_seq=2,
            ts=now,
            type=EventType.STATE_TRANSITION,
            actor=ActorType.SYSTEM,
            payload=StateTransitionPayload(
                from_status=TaskStatus.CREATED,
                to_status=TaskStatus.RUNNING,
            ).model_dump(),
            trace_id="trace-TSK001",
        )
        await append_event_and_update_task(conn, es, ts, evt2, "RUNNING")

        evt3 = Event(
            event_id="EVT003",
            task_id="TSK001",
            task_seq=3,
            ts=now,
            type=EventType.STATE_TRANSITION,
            actor=ActorType.SYSTEM,
            payload=StateTransitionPayload(
                from_status=TaskStatus.RUNNING,
                to_status=TaskStatus.SUCCEEDED,
            ).model_dump(),
            trace_id="trace-TSK001",
        )
        await append_event_and_update_task(conn, es, ts, evt3, "SUCCEEDED")

        # 创建任务 2（CANCELLED）
        task2 = Task(
            task_id="TSK002",
            created_at=now,
            updated_at=now,
            status=TaskStatus.CREATED,
            title="Task two",
            requester=RequesterInfo(channel="web", sender_id="owner"),
        )
        await ts.create_task(task2)
        await conn.commit()

        evt4 = Event(
            event_id="EVT004",
            task_id="TSK002",
            task_seq=1,
            ts=now,
            type=EventType.TASK_CREATED,
            actor=ActorType.SYSTEM,
            payload=TaskCreatedPayload(
                title="Task two", thread_id="default",
                scope_id="", channel="web", sender_id="owner",
            ).model_dump(),
            trace_id="trace-TSK002",
        )
        await append_event_only(conn, es, evt4)

        evt5 = Event(
            event_id="EVT005",
            task_id="TSK002",
            task_seq=2,
            ts=now,
            type=EventType.STATE_TRANSITION,
            actor=ActorType.SYSTEM,
            payload=StateTransitionPayload(
                from_status=TaskStatus.CREATED,
                to_status=TaskStatus.CANCELLED,
            ).model_dump(),
            trace_id="trace-TSK002",
        )
        await append_event_and_update_task(conn, es, ts, evt5, "CANCELLED")

        # 验证原始状态
        assert (await ts.get_task("TSK001")).status == TaskStatus.SUCCEEDED
        assert (await ts.get_task("TSK002")).status == TaskStatus.CANCELLED

        # 执行重建
        event_count = await rebuild_all(conn, es, ts)
        assert event_count == 5

        # 验证重建后状态一致
        rebuilt_1 = await ts.get_task("TSK001")
        rebuilt_2 = await ts.get_task("TSK002")

        assert rebuilt_1.status == TaskStatus.SUCCEEDED
        assert rebuilt_1.title == "Task one"

        assert rebuilt_2.status == TaskStatus.CANCELLED
        assert rebuilt_2.title == "Task two"

        # 验证总任务数
        all_tasks = await ts.list_tasks()
        assert len(all_tasks) == 2
