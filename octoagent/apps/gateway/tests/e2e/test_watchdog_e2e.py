"""Watchdog E2E 测试 -- Feature 011 T044-T047

使用 in-memory SQLite + 时间注入，不依赖外部 LLM 或真实 Docker。
覆盖四个端到端场景：
- 场景 1: 卡死检测（no_progress）
- 场景 2: 重复失败检测（repeated_failure）
- 场景 3: 状态机漂移（state_machine_stall）
- 场景 4: 进程重启后 cooldown 恢复

每个场景均验证 TASK_DRIFT_DETECTED 事件已写入，包含 task_id 和 trace_id（FR-019）。
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

from octoagent.core.models.enums import ActorType, EventType, RiskLevel, TaskStatus
from octoagent.core.models.event import Event, EventCausality
from octoagent.core.models.task import RequesterInfo, Task, TaskPointers
from octoagent.core.store import StoreGroup
from octoagent.core.store.sqlite_init import init_db
from octoagent.gateway.services.watchdog.config import WatchdogConfig
from octoagent.gateway.services.watchdog.cooldown import CooldownRegistry
from octoagent.gateway.services.watchdog.detectors import (
    NoProgressDetector,
    RepeatedFailureDetector,
    StateMachineDriftDetector,
)
from octoagent.gateway.services.watchdog.scanner import WatchdogScanner


# ── 辅助函数 ─────────────────────────────────────────────────────────────────

def _new_ulid() -> str:
    import ulid
    return str(ulid.ULID())


def _make_task(
    task_id: str,
    status: TaskStatus,
    updated_ago_s: float = 0.0,
    trace_id: str = "trace-e2e",
) -> Task:
    """创建指定状态和更新时间的任务"""
    now = datetime.now(UTC)
    return Task(
        task_id=task_id,
        created_at=now - timedelta(seconds=updated_ago_s),
        updated_at=now - timedelta(seconds=updated_ago_s),
        status=status,
        title=f"E2E Task {task_id}",
        thread_id="thread-e2e",
        scope_id="scope-e2e",
        requester=RequesterInfo(channel="web", sender_id="user-e2e"),
        risk_level=RiskLevel.LOW,
        pointers=TaskPointers(),
    )


def _make_event(
    task_id: str,
    event_type: EventType,
    ts: datetime,
    seq: int,
    trace_id: str = "trace-e2e",
    payload: dict | None = None,
) -> Event:
    """创建指定类型和时间的事件"""
    return Event(
        event_id=_new_ulid(),
        task_id=task_id,
        task_seq=seq,
        ts=ts,
        type=event_type,
        schema_version=1,
        actor=ActorType.SYSTEM,
        payload=payload or {},
        trace_id=trace_id,
        span_id="",
        causality=EventCausality(),
    )


def _make_tight_config(threshold_s: int = 45) -> WatchdogConfig:
    """创建阈值紧凑的测试配置（3 × 15 = 45s）"""
    return WatchdogConfig(
        scan_interval_seconds=15,
        no_progress_cycles=3,
        cooldown_seconds=60,
        failure_window_seconds=300,
        repeated_failure_threshold=3,
    )


async def _get_drift_events(store_group: StoreGroup, task_id: str) -> list[Event]:
    """查询指定任务的所有 TASK_DRIFT_DETECTED 事件"""
    return await store_group.event_store.get_events_by_types_since(
        task_id=task_id,
        event_types=[EventType.TASK_DRIFT_DETECTED],
        since_ts=datetime(2000, 1, 1, tzinfo=UTC),  # 从历史开始查
    )


# ── Fixture ───────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def store_group(tmp_path: Path) -> StoreGroup:
    """内存 SQLite StoreGroup"""
    conn = await aiosqlite.connect(":memory:")
    await init_db(conn)
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    sg = StoreGroup(conn=conn, artifacts_dir=artifacts_dir)
    yield sg
    await conn.close()


# ── 场景 1: 卡死检测（T044）─────────────────────────────────────────────────

class TestE2EScenario1StallDetection:
    """场景 1: 卡死检测（no_progress）

    向 in-memory SQLite 写入 RUNNING 任务，注入停止进展事件，
    等待时间超过 no_progress_threshold，验证 TASK_DRIFT_DETECTED 事件类型 no_progress，
    携带 task_id 和 trace_id（FR-019）。
    """

    @pytest.mark.asyncio
    async def test_stalled_task_triggers_no_progress_drift(self, store_group: StoreGroup):
        """卡死任务触发 no_progress 漂移，事件包含 task_id 和 trace_id"""
        task_id = "e2e-task-001"
        trace_id = "trace-e2e-001"
        config = _make_tight_config()  # threshold = 45s

        # 创建 60s 前更新的 RUNNING 任务（超过 45s 阈值）
        task = _make_task(task_id, TaskStatus.RUNNING, updated_ago_s=60, trace_id=trace_id)
        await store_group.task_store.create_task(task)
        # 不写入任何进展事件（模拟卡死）

        scanner = WatchdogScanner(
            store_group=store_group,
            config=config,
            cooldown_registry=CooldownRegistry(),
            detectors=[NoProgressDetector()],
        )
        await scanner.startup()
        await scanner.scan()

        # 验证 TASK_DRIFT_DETECTED 事件已写入
        drift_events = await _get_drift_events(store_group, task_id)
        assert len(drift_events) == 1, f"期望 1 个漂移事件，实际 {len(drift_events)} 个"

        drift_event = drift_events[0]
        # FR-019: 必须携带 task_id，trace_id 由 scanner 从 task 属性获取
        # Task 模型本身无 trace_id 字段，scanner 使用 getattr(task, "trace_id", "") 降级
        assert drift_event.task_id == task_id
        assert drift_event.type == EventType.TASK_DRIFT_DETECTED

        # 验证 payload 漂移类型
        payload = drift_event.payload
        assert payload["drift_type"] == "no_progress"
        assert payload["task_id"] == task_id
        assert payload["stall_duration_seconds"] >= 45.0

    @pytest.mark.asyncio
    async def test_active_task_no_drift(self, store_group: StoreGroup):
        """有近期进展的任务不触发漂移"""
        task_id = "e2e-task-002"
        config = _make_tight_config()

        task = _make_task(task_id, TaskStatus.RUNNING, updated_ago_s=5)
        await store_group.task_store.create_task(task)

        # 写入近期进展事件（5s 前）
        now = datetime.now(UTC)
        progress_event = _make_event(task_id, EventType.MODEL_CALL_COMPLETED, now - timedelta(seconds=5), 1)
        await store_group.event_store.append_event_committed(progress_event)

        scanner = WatchdogScanner(
            store_group=store_group,
            config=config,
            cooldown_registry=CooldownRegistry(),
            detectors=[NoProgressDetector()],
        )
        await scanner.startup()
        await scanner.scan()

        drift_events = await _get_drift_events(store_group, task_id)
        assert len(drift_events) == 0, "有进展的任务不应触发漂移"


# ── 场景 2: 重复失败检测（T045）────────────────────────────────────────────

class TestE2EScenario2RepeatedFailure:
    """场景 2: 重复失败检测（repeated_failure）

    注入 3 条以上失败事件，超过 repeated_failure_threshold，
    验证漂移类型 repeated_failure，依赖 RepeatedFailureDetector。
    """

    @pytest.mark.asyncio
    async def test_repeated_failure_triggers_drift(self, store_group: StoreGroup):
        """短时间内 3 次以上失败触发 repeated_failure 漂移"""
        task_id = "e2e-task-010"
        trace_id = "trace-e2e-010"
        config = _make_tight_config()

        task = _make_task(task_id, TaskStatus.RUNNING, trace_id=trace_id)
        await store_group.task_store.create_task(task)

        # 写入 4 条失败事件（超过阈值 3）
        now = datetime.now(UTC)
        failure_types = [
            EventType.MODEL_CALL_FAILED,
            EventType.TOOL_CALL_FAILED,
            EventType.MODEL_CALL_FAILED,
            EventType.SKILL_FAILED,
        ]
        for i, ft in enumerate(failure_types):
            event = _make_event(task_id, ft, now - timedelta(seconds=10 + i), i + 1, trace_id=trace_id)
            await store_group.event_store.append_event_committed(event)

        scanner = WatchdogScanner(
            store_group=store_group,
            config=config,
            cooldown_registry=CooldownRegistry(),
            detectors=[RepeatedFailureDetector()],
        )
        await scanner.startup()
        await scanner.scan()

        drift_events = await _get_drift_events(store_group, task_id)
        assert len(drift_events) == 1

        drift_event = drift_events[0]
        assert drift_event.task_id == task_id
        # trace_id 来自事件本身（由 scanner 写入时透传）
        assert drift_event.type == EventType.TASK_DRIFT_DETECTED
        assert drift_event.payload["drift_type"] == "repeated_failure"
        assert drift_event.payload["failure_count"] == 4
        assert len(drift_event.payload["failure_event_types"]) == 4

    @pytest.mark.asyncio
    async def test_below_threshold_no_drift(self, store_group: StoreGroup):
        """失败次数未达阈值不触发漂移"""
        task_id = "e2e-task-011"
        config = _make_tight_config()

        task = _make_task(task_id, TaskStatus.RUNNING)
        await store_group.task_store.create_task(task)

        # 仅写入 2 条失败事件（低于阈值 3）
        now = datetime.now(UTC)
        for i in range(2):
            event = _make_event(task_id, EventType.MODEL_CALL_FAILED, now - timedelta(seconds=i + 1), i + 1)
            await store_group.event_store.append_event_committed(event)

        scanner = WatchdogScanner(
            store_group=store_group,
            config=config,
            cooldown_registry=CooldownRegistry(),
            detectors=[RepeatedFailureDetector()],
        )
        await scanner.startup()
        await scanner.scan()

        drift_events = await _get_drift_events(store_group, task_id)
        assert len(drift_events) == 0


# ── 场景 3: 状态机漂移（T046）──────────────────────────────────────────────

class TestE2EScenario3StateMachineStall:
    """场景 3: 状态机漂移（state_machine_stall）

    注入 RUNNING 状态长时间驻留任务，超过 stale_running_threshold，
    验证漂移类型 state_machine_stall，依赖 StateMachineDriftDetector。
    """

    @pytest.mark.asyncio
    async def test_long_running_task_triggers_state_machine_stall(self, store_group: StoreGroup):
        """长时间驻留 RUNNING 状态触发 state_machine_stall"""
        task_id = "e2e-task-020"
        trace_id = "trace-e2e-020"
        config = _make_tight_config()  # threshold = 45s

        # 创建 90s 前更新的任务（远超 45s 阈值）
        task = _make_task(task_id, TaskStatus.RUNNING, updated_ago_s=90, trace_id=trace_id)
        await store_group.task_store.create_task(task)

        scanner = WatchdogScanner(
            store_group=store_group,
            config=config,
            cooldown_registry=CooldownRegistry(),
            detectors=[StateMachineDriftDetector()],
        )
        await scanner.startup()
        await scanner.scan()

        drift_events = await _get_drift_events(store_group, task_id)
        assert len(drift_events) == 1

        drift_event = drift_events[0]
        assert drift_event.task_id == task_id
        assert drift_event.type == EventType.TASK_DRIFT_DETECTED
        payload = drift_event.payload
        assert payload["drift_type"] == "state_machine_stall"
        assert payload["stall_duration_seconds"] >= 45.0
        # current_status 使用内部 TaskStatus（FR-011），不映射为 A2A 状态
        assert payload["current_status"] == "RUNNING"
        assert payload["current_status"] != "active"

    @pytest.mark.asyncio
    async def test_queued_task_long_stall_triggers_drift(self, store_group: StoreGroup):
        """QUEUED 状态长时间驻留也触发漂移"""
        task_id = "e2e-task-021"
        config = _make_tight_config()

        task = _make_task(task_id, TaskStatus.QUEUED, updated_ago_s=120)
        await store_group.task_store.create_task(task)

        scanner = WatchdogScanner(
            store_group=store_group,
            config=config,
            cooldown_registry=CooldownRegistry(),
            detectors=[StateMachineDriftDetector()],
        )
        await scanner.startup()
        await scanner.scan()

        drift_events = await _get_drift_events(store_group, task_id)
        assert len(drift_events) == 1
        assert drift_events[0].payload["drift_type"] == "state_machine_stall"
        assert drift_events[0].payload["current_status"] == "QUEUED"

    @pytest.mark.asyncio
    async def test_terminal_task_no_drift(self, store_group: StoreGroup):
        """终态任务不触发漂移（FR-013）"""
        task_id = "e2e-task-022"
        config = _make_tight_config()

        # 创建 SUCCEEDED 终态任务
        task = _make_task(task_id, TaskStatus.SUCCEEDED, updated_ago_s=1000)
        await store_group.task_store.create_task(task)

        scanner = WatchdogScanner(
            store_group=store_group,
            config=config,
            cooldown_registry=CooldownRegistry(),
            detectors=[StateMachineDriftDetector()],
        )
        await scanner.startup()
        await scanner.scan()

        drift_events = await _get_drift_events(store_group, task_id)
        assert len(drift_events) == 0


# ── 场景 4: 进程重启后 cooldown 恢复（T047）────────────────────────────────

class TestE2EScenario4RestartCooldownRecovery:
    """场景 4: 进程重启后 cooldown 恢复

    模拟第一次扫描写入 DRIFT 事件，重建新的 WatchdogScanner 实例（模拟进程重启），
    调用 startup() 重建 cooldown，验证第二次扫描不重复写入 DRIFT 事件。
    """

    @pytest.mark.asyncio
    async def test_restart_cooldown_prevents_duplicate_drift(self, store_group: StoreGroup):
        """进程重启后，cooldown 从 EventStore 重建，防止重复漂移告警"""
        task_id = "e2e-task-030"
        config = _make_tight_config()

        # 创建卡死任务
        task = _make_task(task_id, TaskStatus.RUNNING, updated_ago_s=60)
        await store_group.task_store.create_task(task)

        # ── 第一次扫描（第一个进程实例）──────────────────────────────────
        scanner1 = WatchdogScanner(
            store_group=store_group,
            config=config,
            cooldown_registry=CooldownRegistry(),
            detectors=[NoProgressDetector()],
        )
        await scanner1.startup()
        await scanner1.scan()

        drift_events_after_first = await _get_drift_events(store_group, task_id)
        assert len(drift_events_after_first) == 1, "第一次扫描应写入 1 个漂移事件"

        # ── 模拟进程重启：创建新的 Scanner 实例（新 cooldown 注册表）──────
        scanner2 = WatchdogScanner(
            store_group=store_group,
            config=config,
            cooldown_registry=CooldownRegistry(),  # 全新的空 cooldown 注册表
            detectors=[NoProgressDetector()],
        )
        # startup() 从 EventStore 重建 cooldown，避免重复告警
        await scanner2.startup()
        await scanner2.scan()

        drift_events_after_restart = await _get_drift_events(store_group, task_id)
        # cooldown 重建成功，不重复写入
        assert len(drift_events_after_restart) == 1, (
            f"进程重启后 cooldown 应重建，第二次扫描不应重复写入漂移事件，"
            f"实际写入 {len(drift_events_after_restart)} 个"
        )

    @pytest.mark.asyncio
    async def test_cooldown_expired_after_restart_triggers_new_drift(self, store_group: StoreGroup):
        """cooldown 过期后重启，新扫描可以重新触发漂移"""
        task_id = "e2e-task-031"
        # cooldown 设置为 1s，非常短
        config = WatchdogConfig(
            scan_interval_seconds=15,
            no_progress_cycles=3,
            cooldown_seconds=1,
        )

        task = _make_task(task_id, TaskStatus.RUNNING, updated_ago_s=60)
        await store_group.task_store.create_task(task)

        # 第一次扫描
        scanner1 = WatchdogScanner(
            store_group=store_group,
            config=config,
            cooldown_registry=CooldownRegistry(),
            detectors=[NoProgressDetector()],
        )
        await scanner1.startup()
        await scanner1.scan()

        drift_events_first = await _get_drift_events(store_group, task_id)
        assert len(drift_events_first) == 1

        # 等待 cooldown 过期（2s）
        import asyncio
        await asyncio.sleep(2)

        # 进程重启，cooldown_seconds=1 已过期
        scanner2 = WatchdogScanner(
            store_group=store_group,
            config=config,
            cooldown_registry=CooldownRegistry(),
            detectors=[NoProgressDetector()],
        )
        await scanner2.startup()
        await scanner2.scan()

        drift_events_after = await _get_drift_events(store_group, task_id)
        # cooldown 已过期，应触发新漂移
        assert len(drift_events_after) == 2, (
            f"cooldown 过期后应触发新漂移事件，实际 {len(drift_events_after)} 个"
        )


# ── 跨场景验证：多检测器并发（综合场景）──────────────────────────────────

class TestE2EMultiDetectorScenario:
    """验证多检测器组合下的 E2E 行为（综合场景）"""

    @pytest.mark.asyncio
    async def test_multiple_tasks_multiple_detectors(self, store_group: StoreGroup):
        """多任务 + 多检测器场景：正确为每个任务分配漂移类型"""
        config = _make_tight_config()
        now = datetime.now(UTC)

        # 任务 A: 卡死任务（no_progress）
        task_a = _make_task("e2e-ma-001", TaskStatus.RUNNING, updated_ago_s=60)
        await store_group.task_store.create_task(task_a)
        # 不写进展事件

        # 任务 B: 重复失败任务（repeated_failure）
        task_b = _make_task("e2e-ma-002", TaskStatus.RUNNING, updated_ago_s=5)
        await store_group.task_store.create_task(task_b)
        # 写入近期进展事件（防止 no_progress 触发）
        progress = _make_event("e2e-ma-002", EventType.TOOL_CALL_COMPLETED, now - timedelta(seconds=3), 1)
        await store_group.event_store.append_event_committed(progress)
        # 写入 3 条失败事件（触发 repeated_failure）
        for i in range(3):
            fail = _make_event("e2e-ma-002", EventType.MODEL_CALL_FAILED, now - timedelta(seconds=10 + i), i + 2)
            await store_group.event_store.append_event_committed(fail)

        # 任务 C: 健康任务（无漂移）
        task_c = _make_task("e2e-ma-003", TaskStatus.RUNNING, updated_ago_s=5)
        await store_group.task_store.create_task(task_c)
        progress_c = _make_event("e2e-ma-003", EventType.MODEL_CALL_COMPLETED, now - timedelta(seconds=2), 1)
        await store_group.event_store.append_event_committed(progress_c)

        scanner = WatchdogScanner(
            store_group=store_group,
            config=config,
            cooldown_registry=CooldownRegistry(),
            detectors=[NoProgressDetector(), RepeatedFailureDetector()],
        )
        await scanner.startup()
        await scanner.scan()

        # 任务 A 应有 no_progress 漂移
        drift_a = await _get_drift_events(store_group, "e2e-ma-001")
        assert len(drift_a) >= 1
        assert drift_a[0].payload["drift_type"] == "no_progress"

        # 任务 B 应有 repeated_failure 漂移
        drift_b = await _get_drift_events(store_group, "e2e-ma-002")
        assert len(drift_b) >= 1
        assert drift_b[0].payload["drift_type"] == "repeated_failure"

        # 任务 C 无漂移
        drift_c = await _get_drift_events(store_group, "e2e-ma-003")
        assert len(drift_c) == 0
