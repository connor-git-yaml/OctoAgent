"""Task model parent_task_id 字段 + TaskStore.list_child_tasks 单测。

从 apps/gateway/tests/test_subagent_executor.py 迁移（F087 followup 死代码清理后
TestTaskParentTaskId 测试与 SubagentExecutor 解耦，独立保留）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from octoagent.core.models import TaskStatus
from octoagent.core.models.task import RequesterInfo, Task
from octoagent.core.store import StoreGroup, create_store_group


@pytest_asyncio.fixture
async def store_group(tmp_path: Path) -> StoreGroup:
    db_path = str(tmp_path / "test.db")
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(exist_ok=True)
    return await create_store_group(db_path, str(artifacts_dir))


class TestTaskParentTaskId:
    """验证 Task 模型 parent_task_id 字段。"""

    async def test_task_default_parent_task_id_is_none(self) -> None:
        task = Task(
            task_id="task-001",
            created_at=datetime.now(tz=UTC),
            updated_at=datetime.now(tz=UTC),
            status=TaskStatus.CREATED,
            title="Test",
            requester=RequesterInfo(channel="test", sender_id="user-001"),
        )
        assert task.parent_task_id is None

    async def test_task_with_parent_task_id(self) -> None:
        task = Task(
            task_id="task-002",
            created_at=datetime.now(tz=UTC),
            updated_at=datetime.now(tz=UTC),
            status=TaskStatus.CREATED,
            title="Child",
            requester=RequesterInfo(channel="test", sender_id="user-001"),
            parent_task_id="task-parent-001",
        )
        assert task.parent_task_id == "task-parent-001"

    @pytest.mark.asyncio
    async def test_task_persist_parent_task_id(self, store_group: StoreGroup) -> None:
        now = datetime.now(tz=UTC)
        task = Task(
            task_id="task-persist-001",
            created_at=now,
            updated_at=now,
            status=TaskStatus.CREATED,
            title="Persisted child",
            requester=RequesterInfo(channel="test", sender_id="user-001"),
            parent_task_id="task-parent-persist",
        )
        await store_group.task_store.create_task(task)
        await store_group.conn.commit()

        loaded = await store_group.task_store.get_task("task-persist-001")
        assert loaded is not None
        assert loaded.parent_task_id == "task-parent-persist"

    @pytest.mark.asyncio
    async def test_task_persist_null_parent_task_id(self, store_group: StoreGroup) -> None:
        now = datetime.now(tz=UTC)
        task = Task(
            task_id="task-persist-002",
            created_at=now,
            updated_at=now,
            status=TaskStatus.CREATED,
            title="Top level task",
            requester=RequesterInfo(channel="test", sender_id="user-001"),
        )
        await store_group.task_store.create_task(task)
        await store_group.conn.commit()

        loaded = await store_group.task_store.get_task("task-persist-002")
        assert loaded is not None
        assert loaded.parent_task_id is None

    @pytest.mark.asyncio
    async def test_list_child_tasks(self, store_group: StoreGroup) -> None:
        now = datetime.now(tz=UTC)
        parent_id = "task-parent-list"

        parent = Task(
            task_id=parent_id,
            created_at=now,
            updated_at=now,
            status=TaskStatus.RUNNING,
            title="Parent",
            requester=RequesterInfo(channel="test", sender_id="user-001"),
        )
        await store_group.task_store.create_task(parent)

        for i in range(2):
            child = Task(
                task_id=f"task-child-{i}",
                created_at=now,
                updated_at=now,
                status=TaskStatus.CREATED,
                title=f"Child {i}",
                requester=RequesterInfo(channel="subagent", sender_id=f"subagent-{i}"),
                parent_task_id=parent_id,
            )
            await store_group.task_store.create_task(child)

        await store_group.conn.commit()

        children = await store_group.task_store.list_child_tasks(parent_id)
        assert len(children) == 2
        assert all(c.parent_task_id == parent_id for c in children)

    @pytest.mark.asyncio
    async def test_list_child_tasks_empty(self, store_group: StoreGroup) -> None:
        children = await store_group.task_store.list_child_tasks("nonexistent-parent")
        assert children == []
