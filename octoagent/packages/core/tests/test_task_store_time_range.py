"""F102 Phase B — task_store.list_tasks_in_time_range 单元测试。

覆盖 AC-T1：
- 时间窗 [start, end) 半开区间过滤
- statuses 过滤
- 空结果 / 边界值
- NaiveDatetime ValueError（spec SD-10）
- 性能验证（构造 100 条 task，单次查询 < 500ms）
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
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


async def _create_task(
    store_group: StoreGroup,
    task_id: str,
    created_at: datetime,
    status: TaskStatus = TaskStatus.SUCCEEDED,
) -> None:
    task = Task(
        task_id=task_id,
        created_at=created_at,
        updated_at=created_at,
        status=status,
        title=f"Task {task_id}",
        requester=RequesterInfo(channel="test", sender_id="user-1"),
    )
    await store_group.task_store.create_task(task)


class TestListTasksInTimeRange:
    """AC-T1 list_tasks_in_time_range 行为。"""

    @pytest.mark.asyncio
    async def test_empty_range_returns_empty_list(
        self, store_group: StoreGroup
    ) -> None:
        start = datetime(2026, 1, 1, tzinfo=UTC)
        end = datetime(2026, 1, 2, tzinfo=UTC)
        result = await store_group.task_store.list_tasks_in_time_range(start, end)
        assert result == []

    @pytest.mark.asyncio
    async def test_single_task_inside_range(self, store_group: StoreGroup) -> None:
        created = datetime(2026, 5, 24, 12, 0, tzinfo=UTC)
        await _create_task(store_group, "t-1", created)
        await store_group.conn.commit()

        start = datetime(2026, 5, 24, 0, 0, tzinfo=UTC)
        end = datetime(2026, 5, 25, 0, 0, tzinfo=UTC)
        result = await store_group.task_store.list_tasks_in_time_range(start, end)

        assert len(result) == 1
        assert result[0].task_id == "t-1"

    @pytest.mark.asyncio
    async def test_task_at_start_boundary_included(
        self, store_group: StoreGroup
    ) -> None:
        """start 是闭区间 — created_at == start 应包含。"""
        start = datetime(2026, 5, 24, 0, 0, tzinfo=UTC)
        end = datetime(2026, 5, 25, 0, 0, tzinfo=UTC)
        await _create_task(store_group, "t-start", start)
        await store_group.conn.commit()

        result = await store_group.task_store.list_tasks_in_time_range(start, end)
        assert len(result) == 1
        assert result[0].task_id == "t-start"

    @pytest.mark.asyncio
    async def test_task_at_end_boundary_excluded(
        self, store_group: StoreGroup
    ) -> None:
        """end 是开区间 — created_at == end 应排除。"""
        end = datetime(2026, 5, 25, 0, 0, tzinfo=UTC)
        await _create_task(store_group, "t-end", end)
        await store_group.conn.commit()

        start = datetime(2026, 5, 24, 0, 0, tzinfo=UTC)
        result = await store_group.task_store.list_tasks_in_time_range(start, end)
        assert result == []

    @pytest.mark.asyncio
    async def test_task_before_range_excluded(self, store_group: StoreGroup) -> None:
        await _create_task(
            store_group, "t-before", datetime(2026, 5, 23, 23, 59, tzinfo=UTC)
        )
        await store_group.conn.commit()

        start = datetime(2026, 5, 24, 0, 0, tzinfo=UTC)
        end = datetime(2026, 5, 25, 0, 0, tzinfo=UTC)
        result = await store_group.task_store.list_tasks_in_time_range(start, end)
        assert result == []

    @pytest.mark.asyncio
    async def test_task_after_range_excluded(self, store_group: StoreGroup) -> None:
        await _create_task(
            store_group, "t-after", datetime(2026, 5, 25, 0, 1, tzinfo=UTC)
        )
        await store_group.conn.commit()

        start = datetime(2026, 5, 24, 0, 0, tzinfo=UTC)
        end = datetime(2026, 5, 25, 0, 0, tzinfo=UTC)
        result = await store_group.task_store.list_tasks_in_time_range(start, end)
        assert result == []

    @pytest.mark.asyncio
    async def test_orders_by_created_at_desc(self, store_group: StoreGroup) -> None:
        await _create_task(
            store_group, "t-early", datetime(2026, 5, 24, 8, 0, tzinfo=UTC)
        )
        await _create_task(
            store_group, "t-mid", datetime(2026, 5, 24, 12, 0, tzinfo=UTC)
        )
        await _create_task(
            store_group, "t-late", datetime(2026, 5, 24, 20, 0, tzinfo=UTC)
        )
        await store_group.conn.commit()

        start = datetime(2026, 5, 24, 0, 0, tzinfo=UTC)
        end = datetime(2026, 5, 25, 0, 0, tzinfo=UTC)
        result = await store_group.task_store.list_tasks_in_time_range(start, end)

        assert [t.task_id for t in result] == ["t-late", "t-mid", "t-early"]

    @pytest.mark.asyncio
    async def test_filter_by_statuses(self, store_group: StoreGroup) -> None:
        created = datetime(2026, 5, 24, 12, 0, tzinfo=UTC)
        await _create_task(store_group, "t-failed", created, TaskStatus.FAILED)
        await _create_task(
            store_group, "t-completed", created, TaskStatus.SUCCEEDED
        )
        await _create_task(
            store_group, "t-waiting", created, TaskStatus.WAITING_APPROVAL
        )
        await store_group.conn.commit()

        start = datetime(2026, 5, 24, 0, 0, tzinfo=UTC)
        end = datetime(2026, 5, 25, 0, 0, tzinfo=UTC)
        result = await store_group.task_store.list_tasks_in_time_range(
            start, end, statuses=[TaskStatus.FAILED, TaskStatus.WAITING_APPROVAL]
        )

        ids = {t.task_id for t in result}
        assert ids == {"t-failed", "t-waiting"}

    @pytest.mark.asyncio
    async def test_empty_statuses_returns_empty(self, store_group: StoreGroup) -> None:
        created = datetime(2026, 5, 24, 12, 0, tzinfo=UTC)
        await _create_task(store_group, "t-1", created)
        await store_group.conn.commit()

        start = datetime(2026, 5, 24, 0, 0, tzinfo=UTC)
        end = datetime(2026, 5, 25, 0, 0, tzinfo=UTC)
        result = await store_group.task_store.list_tasks_in_time_range(
            start, end, statuses=[]
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_naive_start_raises_value_error(
        self, store_group: StoreGroup
    ) -> None:
        """spec SD-10 / FR-T1：NaiveDatetime 必须 raise ValueError。"""
        start = datetime(2026, 5, 24, 0, 0)  # no tzinfo
        end = datetime(2026, 5, 25, 0, 0, tzinfo=UTC)
        with pytest.raises(ValueError, match="UTC-aware"):
            await store_group.task_store.list_tasks_in_time_range(start, end)

    @pytest.mark.asyncio
    async def test_naive_end_raises_value_error(
        self, store_group: StoreGroup
    ) -> None:
        start = datetime(2026, 5, 24, 0, 0, tzinfo=UTC)
        end = datetime(2026, 5, 25, 0, 0)  # no tzinfo
        with pytest.raises(ValueError, match="UTC-aware"):
            await store_group.task_store.list_tasks_in_time_range(start, end)

    @pytest.mark.asyncio
    async def test_performance_50_tasks_under_500ms(
        self, store_group: StoreGroup
    ) -> None:
        """NFR-1 性能：task 量 = 50 时单次查询 < 500ms。"""
        base = datetime(2026, 5, 24, 0, 0, tzinfo=UTC)
        for i in range(50):
            await _create_task(
                store_group,
                f"perf-task-{i:03d}",
                base + timedelta(minutes=i * 5),
            )
        await store_group.conn.commit()

        start = base
        end = base + timedelta(days=1)

        t0 = time.perf_counter()
        result = await store_group.task_store.list_tasks_in_time_range(start, end)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        assert len(result) == 50
        assert elapsed_ms < 500, f"Query took {elapsed_ms:.1f}ms (>= 500ms threshold)"
