"""Feature 013 场景 C — 长时间无进展任务自动告警验收（SC-003）

测试目标：
- FR-004: 验证 WatchdogScanner.scan() 对超出阈值无进展的任务写入 TASK_DRIFT_DETECTED 事件
- 冷却机制防止重复告警（cooldown 期内重复触发不产生重复事件）
- 阈值配置覆盖机制生效（WATCHDOG_NO_PROGRESS_CYCLES=1, WATCHDOG_SCAN_INTERVAL_SECONDS=1）

独立测试命令：
    uv run pytest tests/integration/test_f013_watchdog.py -v

注：使用 watchdog_integration_app fixture（含 WatchdogScanner，不启动 APScheduler），
直接调用 scanner.scan() 而非依赖后台调度，确保测试确定性（FR-010）。
"""

import asyncio
from datetime import UTC, datetime, timedelta

from octoagent.core.models.enums import EventType, TaskStatus
from octoagent.core.models.message import NormalizedMessage
from octoagent.gateway.services.task_service import TaskService


class TestF013ScenarioC:
    """场景 C: 长时间无进展任务自动告警验收（SC-003）

    覆盖 FR-004 的三条验收场景：
    - 场景 1: 超出阈值的 RUNNING 任务触发 TASK_DRIFT_DETECTED 事件
    - 场景 2: cooldown 期内重复触发不产生重复告警
    - 场景 3: 阈值配置覆盖机制验证（WATCHDOG_NO_PROGRESS_CYCLES=1 生效）
    幂等键格式: f013-sc-c-{sequence}
    """

    async def test_watchdog_detects_stalled_task(
        self,
        watchdog_integration_app,
        watchdog_client,
    ) -> None:
        """FR-004 场景 1: 超过阈值无进展的 RUNNING 任务触发 TASK_DRIFT_DETECTED 事件。

        流程：
        1. 创建任务推进到 RUNNING
        2. 等待 1.1 秒（超过 1 秒阈值：1 cycle × 1s interval）
        3. 直接调用 scanner.scan()（不依赖 APScheduler 调度器）
        4. 断言 TASK_DRIFT_DETECTED 事件存在且 task_id 匹配
        幂等键: f013-sc-c-001
        """
        sg = watchdog_integration_app.state.store_group
        sse_hub = watchdog_integration_app.state.sse_hub
        service = TaskService(sg, sse_hub)

        msg = NormalizedMessage(
            text="f013 watchdog stalled task",
            idempotency_key="f013-sc-c-001",
        )
        task_id, _ = await service.create_task(msg)
        await service._write_state_transition(
            task_id,
            TaskStatus.CREATED,
            TaskStatus.RUNNING,
            f"trace-{task_id}",
        )

        # 等待超过 no_progress_threshold_seconds（1 cycle × 1s = 1 秒）
        await asyncio.sleep(1.1)

        # 直接触发扫描（不依赖 APScheduler，保证测试确定性）
        scanner = watchdog_integration_app.state.watchdog_scanner
        await scanner.scan()

        # 断言 TASK_DRIFT_DETECTED 事件已写入 EventStore
        since_ts = datetime.now(UTC) - timedelta(seconds=30)
        drift_events = await sg.event_store.get_events_by_types_since(
            task_id=task_id,
            event_types=[EventType.TASK_DRIFT_DETECTED],
            since_ts=since_ts,
        )
        assert len(drift_events) >= 1, (
            f"期望至少 1 条 TASK_DRIFT_DETECTED 事件，实际: {len(drift_events)}"
        )
        assert drift_events[0].task_id == task_id, (
            f"事件 task_id 不匹配：期望 {task_id}，实际 {drift_events[0].task_id}"
        )

    async def test_watchdog_cooldown_prevents_duplicate_alerts(
        self,
        watchdog_integration_app,
    ) -> None:
        """FR-004 场景 2: cooldown 期内重复触发不产生重复告警。

        在首次 scan() 触发告警后立即再次调用 scan()，
        断言 TASK_DRIFT_DETECTED 事件总数仍为 1（冷却机制已生效）。
        幂等键: f013-sc-c-002
        """
        sg = watchdog_integration_app.state.store_group
        sse_hub = watchdog_integration_app.state.sse_hub
        service = TaskService(sg, sse_hub)

        msg = NormalizedMessage(
            text="f013 watchdog cooldown test",
            idempotency_key="f013-sc-c-002",
        )
        task_id, _ = await service.create_task(msg)
        await service._write_state_transition(
            task_id,
            TaskStatus.CREATED,
            TaskStatus.RUNNING,
            f"trace-{task_id}",
        )

        # 等待超过阈值（1 秒）
        await asyncio.sleep(1.1)

        scanner = watchdog_integration_app.state.watchdog_scanner

        # 第一次扫描：应触发告警
        await scanner.scan()

        since_ts = datetime.now(UTC) - timedelta(seconds=30)
        drift_events_after_first = await sg.event_store.get_events_by_types_since(
            task_id=task_id,
            event_types=[EventType.TASK_DRIFT_DETECTED],
            since_ts=since_ts,
        )
        assert len(drift_events_after_first) == 1, (
            f"首次扫描后期望 1 条 TASK_DRIFT_DETECTED 事件，实际: {len(drift_events_after_first)}"
        )

        # 第二次扫描：cooldown 期内，不应产生重复告警
        await scanner.scan()

        drift_events_after_second = await sg.event_store.get_events_by_types_since(
            task_id=task_id,
            event_types=[EventType.TASK_DRIFT_DETECTED],
            since_ts=since_ts,
        )
        assert len(drift_events_after_second) == 1, (
            f"cooldown 期内重复扫描后事件数应仍为 1，实际: {len(drift_events_after_second)}"
        )

    async def test_watchdog_threshold_config_override(
        self,
        watchdog_integration_app,
    ) -> None:
        """FR-004 场景 3 / spec FR-010 隔离验证: 阈值配置覆盖机制已生效。

        验证 WATCHDOG_NO_PROGRESS_CYCLES=1 + WATCHDOG_SCAN_INTERVAL_SECONDS=1
        配置已通过 fixture 注入，no_progress_threshold_seconds = 1 秒。
        通过等待 1.1 秒后触发告警（无需冻结时钟），验证配置覆盖机制生效。
        幂等键: f013-sc-c-003
        """
        sg = watchdog_integration_app.state.store_group
        sse_hub = watchdog_integration_app.state.sse_hub
        service = TaskService(sg, sse_hub)

        # 验证 watchdog 配置确实已被覆盖（threshold = 1 cycle × 1s = 1 秒）
        scanner = watchdog_integration_app.state.watchdog_scanner
        actual_threshold = scanner._config.no_progress_threshold_seconds
        assert actual_threshold == 1, (
            f"阈值配置覆盖未生效，期望 1 秒，实际: {actual_threshold} 秒"
        )

        # 创建任务并等待超过阈值
        msg = NormalizedMessage(
            text="f013 watchdog threshold override",
            idempotency_key="f013-sc-c-003",
        )
        task_id, _ = await service.create_task(msg)
        await service._write_state_transition(
            task_id,
            TaskStatus.CREATED,
            TaskStatus.RUNNING,
            f"trace-{task_id}",
        )

        # 等待 1.1 秒，配置覆盖后无需等待真实默认阈值（45 秒 = 3 cycles × 15s）
        await asyncio.sleep(1.1)

        await scanner.scan()

        since_ts = datetime.now(UTC) - timedelta(seconds=30)
        drift_events = await sg.event_store.get_events_by_types_since(
            task_id=task_id,
            event_types=[EventType.TASK_DRIFT_DETECTED],
            since_ts=since_ts,
        )
        assert len(drift_events) >= 1, (
            f"阈值覆盖后期望触发 TASK_DRIFT_DETECTED，实际事件数: {len(drift_events)}"
        )
