"""TaskStore 扩展方法单元测试 -- Feature 011 T014

测试 list_tasks_by_statuses 的正确性，
覆盖多状态过滤/空结果/向下兼容验证场景。
"""

from datetime import UTC, datetime

import aiosqlite
import pytest
import pytest_asyncio

from octoagent.core.models.enums import RiskLevel, TaskStatus
from octoagent.core.models.task import RequesterInfo, Task, TaskPointers
from octoagent.core.store.sqlite_init import init_db
from octoagent.core.store.task_store import SqliteTaskStore


def _make_task(task_id: str, status: TaskStatus) -> Task:
    """创建测试用 Task 对象"""
    now = datetime.now(UTC)
    return Task(
        task_id=task_id,
        created_at=now,
        updated_at=now,
        status=status,
        title=f"Test Task {task_id}",
        thread_id="thread-test",
        scope_id="scope-test",
        requester=RequesterInfo(channel="web", sender_id="user-001"),
        risk_level=RiskLevel.LOW,
        pointers=TaskPointers(),
    )


@pytest_asyncio.fixture
async def db_conn():
    """内存 SQLite 连接，已初始化 schema"""
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await init_db(conn)
    yield conn
    await conn.close()


@pytest_asyncio.fixture
async def task_store(db_conn: aiosqlite.Connection) -> SqliteTaskStore:
    """已初始化的 TaskStore"""
    return SqliteTaskStore(db_conn)


class TestListTasksByStatuses:
    """list_tasks_by_statuses 方法测试"""

    @pytest.mark.asyncio
    async def test_empty_statuses_returns_empty(self, task_store: SqliteTaskStore):
        """空状态列表返回空结果"""
        result = await task_store.list_tasks_by_statuses([])
        assert result == []

    @pytest.mark.asyncio
    async def test_single_status_filter(self, task_store: SqliteTaskStore):
        """单状态过滤正确"""
        t1 = _make_task("task-001", TaskStatus.RUNNING)
        t2 = _make_task("task-002", TaskStatus.FAILED)
        t3 = _make_task("task-003", TaskStatus.RUNNING)

        for t in [t1, t2, t3]:
            await task_store.create_task(t)
        await task_store._conn.commit()

        result = await task_store.list_tasks_by_statuses([TaskStatus.RUNNING])
        assert len(result) == 2
        ids = {r.task_id for r in result}
        assert "task-001" in ids
        assert "task-003" in ids
        assert "task-002" not in ids

    @pytest.mark.asyncio
    async def test_multi_status_filter(self, task_store: SqliteTaskStore):
        """多状态过滤（单次 IN 查询）"""
        t1 = _make_task("task-001", TaskStatus.RUNNING)
        t2 = _make_task("task-002", TaskStatus.WAITING_APPROVAL)
        t3 = _make_task("task-003", TaskStatus.SUCCEEDED)
        t4 = _make_task("task-004", TaskStatus.CREATED)

        for t in [t1, t2, t3, t4]:
            await task_store.create_task(t)
        await task_store._conn.commit()

        result = await task_store.list_tasks_by_statuses(
            [TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL, TaskStatus.CREATED]
        )
        assert len(result) == 3
        ids = {r.task_id for r in result}
        assert "task-001" in ids
        assert "task-002" in ids
        assert "task-004" in ids
        assert "task-003" not in ids  # SUCCEEDED 是终态，不应包含

    @pytest.mark.asyncio
    async def test_no_matching_tasks_returns_empty(self, task_store: SqliteTaskStore):
        """无匹配任务返回空列表"""
        t1 = _make_task("task-001", TaskStatus.SUCCEEDED)
        await task_store.create_task(t1)
        await task_store._conn.commit()

        result = await task_store.list_tasks_by_statuses([TaskStatus.RUNNING])
        assert result == []

    @pytest.mark.asyncio
    async def test_existing_list_tasks_still_works(self, task_store: SqliteTaskStore):
        """向下兼容：原 list_tasks 接口不受影响"""
        t1 = _make_task("task-001", TaskStatus.RUNNING)
        t2 = _make_task("task-002", TaskStatus.FAILED)
        for t in [t1, t2]:
            await task_store.create_task(t)
        await task_store._conn.commit()

        # 原接口按状态过滤
        running_tasks = await task_store.list_tasks(status="RUNNING")
        assert len(running_tasks) == 1
        assert running_tasks[0].task_id == "task-001"

        # 原接口不传状态返回全部
        all_tasks = await task_store.list_tasks()
        assert len(all_tasks) == 2

    @pytest.mark.asyncio
    async def test_all_non_terminal_statuses(self, task_store: SqliteTaskStore):
        """覆盖 Watchdog 关心的所有非终态状态"""
        non_terminal = [
            TaskStatus.CREATED,
            TaskStatus.RUNNING,
            TaskStatus.QUEUED,
            TaskStatus.WAITING_INPUT,
            TaskStatus.WAITING_APPROVAL,
            TaskStatus.PAUSED,
        ]
        for i, status in enumerate(non_terminal):
            t = _make_task(f"task-{i:03d}", status)
            await task_store.create_task(t)
        await task_store._conn.commit()

        result = await task_store.list_tasks_by_statuses(non_terminal)
        assert len(result) == len(non_terminal)
