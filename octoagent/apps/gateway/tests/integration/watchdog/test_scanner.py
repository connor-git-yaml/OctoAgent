"""WatchdogScanner 集成测试 -- Feature 011 T024

使用 in-memory SQLite，覆盖：
- 单次扫描检测到漂移并写入 TASK_DRIFT_DETECTED 事件
- cooldown 防抖：第二次扫描不重复写入事件
- 扫描失败（模拟 Store 异常）-> 记录 warning、不抛出、下次扫描可恢复
- 终态任务被跳过
- 进程重启（新实例）后 startup() 重建 cooldown 注册表
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

from octoagent.core.models.enums import ActorType, EventType, TaskStatus
from octoagent.core.models.event import Event, EventCausality
from octoagent.core.models.task import Task, RequesterInfo, TaskPointers
from octoagent.core.models.enums import RiskLevel
from octoagent.core.store import StoreGroup, SqliteEventStore, SqliteTaskStore
from octoagent.core.store.sqlite_init import init_db
from octoagent.gateway.services.watchdog.config import WatchdogConfig
from octoagent.gateway.services.watchdog.cooldown import CooldownRegistry
from octoagent.gateway.services.watchdog.detectors import NoProgressDetector
from octoagent.gateway.services.watchdog.scanner import WatchdogScanner


def _make_event_id() -> str:
    import ulid
    return str(ulid.ULID())


def _make_task_obj(task_id: str, status: TaskStatus) -> Task:
    now = datetime.now(UTC)
    return Task(
        task_id=task_id,
        created_at=now,
        updated_at=now,
        status=status,
        title=f"Test Task {task_id}",
        thread_id="thread-001",
        scope_id="scope-001",
        requester=RequesterInfo(channel="web", sender_id="user-001"),
        risk_level=RiskLevel.LOW,
        pointers=TaskPointers(),
    )


@pytest_asyncio.fixture
async def db_conn(tmp_path: Path):
    """in-memory SQLite 连接"""
    conn = await aiosqlite.connect(":memory:")
    await init_db(conn)
    yield conn
    await conn.close()


@pytest_asyncio.fixture
async def store_group(db_conn: aiosqlite.Connection, tmp_path: Path) -> StoreGroup:
    """基于 in-memory SQLite 的 StoreGroup"""
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    return StoreGroup(conn=db_conn, artifacts_dir=artifacts_dir)


def _make_config(
    scan_interval: int = 15,
    no_progress_cycles: int = 3,
    cooldown_seconds: int = 60,
) -> WatchdogConfig:
    return WatchdogConfig(
        scan_interval_seconds=scan_interval,
        no_progress_cycles=no_progress_cycles,
        cooldown_seconds=cooldown_seconds,
    )


async def _create_stalled_task(store_group: StoreGroup, task_id: str) -> Task:
    """创建一个卡死任务：RUNNING 状态，无进展事件"""
    task = _make_task_obj(task_id, TaskStatus.RUNNING)
    # 让 updated_at 足够旧（超过阈值 45s）
    task = task.model_copy(update={"updated_at": datetime.now(UTC) - timedelta(seconds=60)})
    await store_group.task_store.create_task(task)
    await store_group.conn.commit()
    return task


class TestWatchdogScannerDriftDetection:
    """漂移检测基础测试"""

    @pytest.mark.asyncio
    async def test_scan_detects_stalled_task_and_emits_drift_event(
        self, store_group: StoreGroup
    ):
        """单次扫描检测到漂移并写入 TASK_DRIFT_DETECTED 事件"""
        task = await _create_stalled_task(store_group, "task-001")
        config = _make_config()
        cooldown = CooldownRegistry()
        scanner = WatchdogScanner(
            store_group=store_group,
            config=config,
            cooldown_registry=cooldown,
            detectors=[NoProgressDetector()],
        )

        await scanner.startup()
        await scanner.scan()

        # 验证 TASK_DRIFT_DETECTED 事件已写入
        events = await store_group.event_store.get_events_for_task("task-001")
        drift_events = [e for e in events if e.type == EventType.TASK_DRIFT_DETECTED]
        assert len(drift_events) == 1

        drift_event = drift_events[0]
        payload = drift_event.payload
        assert payload["drift_type"] == "no_progress"
        assert payload["task_id"] == "task-001"

    @pytest.mark.asyncio
    async def test_terminal_task_skipped(self, store_group: StoreGroup):
        """终态任务被跳过，不写入 DRIFT 事件"""
        # 创建一个 SUCCEEDED 状态的任务
        task = _make_task_obj("task-terminal", TaskStatus.SUCCEEDED)
        await store_group.task_store.create_task(task)
        await store_group.conn.commit()

        config = _make_config()
        cooldown = CooldownRegistry()
        scanner = WatchdogScanner(
            store_group=store_group,
            config=config,
            cooldown_registry=cooldown,
            detectors=[NoProgressDetector()],
        )

        await scanner.scan()

        # 终态任务不产生 DRIFT 事件（也没有任何事件，因为我们没有插入）
        events = await store_group.event_store.get_events_for_task("task-terminal")
        drift_events = [e for e in events if e.type == EventType.TASK_DRIFT_DETECTED]
        assert len(drift_events) == 0


class TestWatchdogScannerCooldown:
    """cooldown 防抖测试"""

    @pytest.mark.asyncio
    async def test_second_scan_does_not_duplicate_drift_event(
        self, store_group: StoreGroup
    ):
        """cooldown 防抖：第二次扫描不重复写入 DRIFT 事件"""
        task = await _create_stalled_task(store_group, "task-001")
        config = _make_config(cooldown_seconds=60)
        cooldown = CooldownRegistry()
        scanner = WatchdogScanner(
            store_group=store_group,
            config=config,
            cooldown_registry=cooldown,
            detectors=[NoProgressDetector()],
        )

        await scanner.startup()
        # 第一次扫描：写入 DRIFT 事件
        await scanner.scan()
        # 第二次扫描：cooldown 内，不重复写入
        await scanner.scan()

        events = await store_group.event_store.get_events_for_task("task-001")
        drift_events = [e for e in events if e.type == EventType.TASK_DRIFT_DETECTED]
        assert len(drift_events) == 1  # 只有一次

    @pytest.mark.asyncio
    async def test_startup_rebuilds_cooldown_prevents_duplicate(
        self, store_group: StoreGroup
    ):
        """进程重启后 startup() 重建 cooldown，防止重复写入（FR-006）"""
        task = await _create_stalled_task(store_group, "task-001")
        config = _make_config(cooldown_seconds=60)

        # 第一个 Scanner 实例：扫描并写入 DRIFT 事件
        cooldown1 = CooldownRegistry()
        scanner1 = WatchdogScanner(
            store_group=store_group,
            config=config,
            cooldown_registry=cooldown1,
            detectors=[NoProgressDetector()],
        )
        await scanner1.startup()
        await scanner1.scan()

        # 验证第一次写入了 DRIFT 事件
        events_after_first = await store_group.event_store.get_events_for_task("task-001")
        drift_events_after_first = [
            e for e in events_after_first if e.type == EventType.TASK_DRIFT_DETECTED
        ]
        assert len(drift_events_after_first) == 1

        # 第二个 Scanner 实例（模拟进程重启）：startup 重建 cooldown
        cooldown2 = CooldownRegistry()
        scanner2 = WatchdogScanner(
            store_group=store_group,
            config=config,
            cooldown_registry=cooldown2,
            detectors=[NoProgressDetector()],
        )
        await scanner2.startup()  # 应从 EventStore 重建 cooldown
        await scanner2.scan()  # cooldown 内，不再写入

        # 仍然只有一个 DRIFT 事件
        events_after_second = await store_group.event_store.get_events_for_task("task-001")
        drift_events_after_second = [
            e for e in events_after_second if e.type == EventType.TASK_DRIFT_DETECTED
        ]
        assert len(drift_events_after_second) == 1  # 重建 cooldown 防止了重复写入


class TestWatchdogScannerResilience:
    """扫描弹性：失败不抛出（FR-007）"""

    @pytest.mark.asyncio
    async def test_scan_failure_does_not_raise(self, store_group: StoreGroup):
        """扫描失败记录 warning，不抛出异常（FR-007）"""
        from unittest.mock import AsyncMock, patch

        config = _make_config()
        cooldown = CooldownRegistry()
        scanner = WatchdogScanner(
            store_group=store_group,
            config=config,
            cooldown_registry=cooldown,
            detectors=[NoProgressDetector()],
        )

        # 模拟 TaskStore 抛出异常
        with patch.object(
            store_group.task_store,
            "list_tasks_by_statuses",
            side_effect=RuntimeError("DB connection failed"),
        ):
            # 不应抛出异常
            await scanner.scan()

    @pytest.mark.asyncio
    async def test_scan_recovers_after_failure(self, store_group: StoreGroup):
        """扫描失败后，下次扫描可正常恢复"""
        task = await _create_stalled_task(store_group, "task-001")
        config = _make_config()
        cooldown = CooldownRegistry()
        scanner = WatchdogScanner(
            store_group=store_group,
            config=config,
            cooldown_registry=cooldown,
            detectors=[NoProgressDetector()],
        )

        from unittest.mock import AsyncMock, patch

        # 第一次扫描失败
        with patch.object(
            store_group.task_store,
            "list_tasks_by_statuses",
            side_effect=RuntimeError("DB connection failed"),
        ):
            await scanner.scan()  # 失败但不抛出

        # 第二次扫描正常恢复
        await scanner.scan()

        events = await store_group.event_store.get_events_for_task("task-001")
        drift_events = [e for e in events if e.type == EventType.TASK_DRIFT_DETECTED]
        assert len(drift_events) == 1  # 正常恢复后检测到漂移
