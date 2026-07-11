"""F138 Phase E：watchdog clock DI 确定性时间测试（AC-6）。

价值锚：F103d 真跑暴露的 watchdog datetime offset-naive 比较 bug（3eabd58）
此前在 L4 不可测——时间是隐式墙钟依赖。注入固定时钟后，时间窗判断 / stall
时长计算 / cooldown 边界全部**逐值确定性可断言**（零 sleep、零墙钟抖动）。

case 列表：
- 固定时钟下 NoProgressDetector stall_duration 逐值精确（100.0 非 ≈100）
- 固定时钟下 StateMachineDriftDetector 阈值边界确定性
- RepeatedFailureDetector 遇 offset-naive 事件 ts 不炸 + stall 逐值精确
  （F103d bug 类的 L4 复现缝）
- CooldownRegistry 固定时钟边界（59s in / 61s out，零 sleep）
- WatchdogScanner._emit_drift_event 事件 ts == 注入时钟
- 全组件 clock=None 默认回退 utc_now（None 行为等价看护）
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from octoagent.core.models.enums import EventType, TaskStatus
from octoagent.gateway.services.watchdog.config import WatchdogConfig
from octoagent.gateway.services.watchdog.cooldown import CooldownRegistry
from octoagent.gateway.services.watchdog.detectors import (
    NoProgressDetector,
    RepeatedFailureDetector,
    StateMachineDriftDetector,
)
from octoagent.gateway.services.watchdog.models import utc_now
from octoagent.gateway.services.watchdog.scanner import WatchdogScanner

_FROZEN = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _frozen_clock() -> datetime:
    return _FROZEN


def _make_task(updated_ago_seconds: float) -> MagicMock:
    task = MagicMock()
    task.task_id = "task-clock-di"
    task.status = TaskStatus.RUNNING.value
    task.updated_at = _FROZEN - timedelta(seconds=updated_ago_seconds)
    return task


def _make_config() -> WatchdogConfig:
    return WatchdogConfig(scan_interval_seconds=15, no_progress_cycles=3)  # threshold=45s


async def test_no_progress_stall_duration_exact_with_frozen_clock() -> None:
    """固定时钟：无进展 stall 时长逐值 == 100.0（墙钟下只能 ≈，不可 ==）。"""
    detector = NoProgressDetector(clock=_frozen_clock)
    task = _make_task(updated_ago_seconds=999)
    store = AsyncMock()
    store.get_events_by_types_since.return_value = []  # 窗口内无进展/无豁免
    store.get_latest_event_ts.return_value = _FROZEN - timedelta(seconds=100)

    result = await detector.check(task, store, _make_config())

    assert result is not None
    assert result.stall_duration_seconds == 100.0, (
        f"AC-6: 固定时钟下 stall 必须逐值精确，实际 {result.stall_duration_seconds}"
    )
    assert result.detected_at == _FROZEN
    # 时间窗查询参数同样确定性：since_ts == frozen - threshold(45s)
    called_since = store.get_events_by_types_since.call_args_list[0].kwargs["since_ts"]
    assert called_since == _FROZEN - timedelta(seconds=45)


async def test_state_machine_threshold_boundary_deterministic() -> None:
    """固定时钟：阈值边界确定性——45s 整不告警（<=），45.5s 告警且 stall 逐值。"""
    detector = StateMachineDriftDetector(clock=_frozen_clock)
    config = _make_config()
    store = AsyncMock()

    at_threshold = await detector.check(_make_task(45.0), store, config)
    assert at_threshold is None, "AC-6: stall == threshold 不告警（<= 语义），固定时钟可测边界"

    over = await detector.check(_make_task(45.5), store, config)
    assert over is not None
    assert over.stall_duration_seconds == 45.5
    assert over.detected_at == _FROZEN


async def test_repeated_failure_offset_naive_event_ts_is_l4_catchable() -> None:
    """F103d bug 类：offset-naive 事件 ts + 固定 aware 时钟——不炸 + stall 逐值。

    baseline（无 clock DI）下这类比较 bug 只能靠真跑暴露（F103d 实证 3eabd58）；
    注入固定时钟后成为普通 L4 断言。
    """
    detector = RepeatedFailureDetector(clock=_frozen_clock)
    config = WatchdogConfig(
        scan_interval_seconds=15,
        no_progress_cycles=3,
        repeated_failure_threshold=2,
        failure_window_seconds=300,
    )

    naive_ts = (_FROZEN - timedelta(seconds=120)).replace(tzinfo=None)  # offset-naive!
    failures = []
    for _ in range(2):
        ev = MagicMock()
        ev.type = EventType.TOOL_CALL_FAILED
        ev.ts = naive_ts
        failures.append(ev)
    store = AsyncMock()
    store.get_events_by_types_since.return_value = failures

    result = await detector.check(_make_task(0), store, config)

    assert result is not None, "AC-6: offset-naive 事件 ts 不得让检测器崩溃"
    assert result.failure_count == 2
    assert result.stall_duration_seconds == 120.0, (
        "AC-6: naive ts 补 UTC 后与固定时钟相减必须逐值 120.0"
    )


async def test_cooldown_boundary_without_sleep() -> None:
    """固定时钟：cooldown 边界 59s in / 61s out——零 sleep 零抖动。"""
    drift_at = _FROZEN - timedelta(seconds=59)
    registry_in = CooldownRegistry(clock=_frozen_clock)
    registry_in.record_drift("t1", drift_at)
    assert registry_in.is_in_cooldown("t1", cooldown_seconds=60) is True

    registry_out = CooldownRegistry(clock=_frozen_clock)
    registry_out.record_drift("t1", _FROZEN - timedelta(seconds=61))
    assert registry_out.is_in_cooldown("t1", cooldown_seconds=60) is False


async def test_scanner_emit_stamps_injected_clock() -> None:
    """固定时钟：TASK_DRIFT_DETECTED 事件 ts == 注入时钟（事件时间可确定性断言）。"""
    from octoagent.gateway.services.watchdog.models import DriftResult

    store_group = MagicMock()
    store_group.event_store = AsyncMock()
    store_group.event_store.get_next_task_seq.return_value = 7

    cooldown = CooldownRegistry(clock=_frozen_clock)
    scanner = WatchdogScanner(
        store_group=store_group,
        config=_make_config(),
        cooldown_registry=cooldown,
        detectors=[],
        clock=_frozen_clock,
    )

    drift = DriftResult(
        task_id="t-emit",
        drift_type="no_progress",
        detected_at=_FROZEN,
        stall_duration_seconds=100.0,
        suggested_actions=["check_worker_logs"],
        last_progress_ts=_FROZEN - timedelta(seconds=100),
    )
    await scanner._emit_drift_event(task_id="t-emit", trace_id="tr", result=drift)

    event = store_group.event_store.append_event_committed.call_args.args[0]
    assert event.ts == _FROZEN, "AC-6: 事件时间戳必须来自注入时钟"
    # cooldown 记录同一时钟值 → 后续 is_in_cooldown 判断闭环确定性
    assert cooldown.is_in_cooldown("t-emit", cooldown_seconds=60) is True


def test_all_components_default_to_utc_now() -> None:
    """None 行为等价看护：clock 未注入时全组件回退 utc_now（== baseline 语义）。"""
    assert NoProgressDetector()._clock is utc_now
    assert StateMachineDriftDetector()._clock is utc_now
    assert RepeatedFailureDetector()._clock is utc_now
    assert CooldownRegistry()._clock is utc_now
    scanner = WatchdogScanner(
        store_group=MagicMock(),
        config=_make_config(),
        cooldown_registry=CooldownRegistry(),
        detectors=[],
    )
    assert scanner._clock is utc_now
    # 默认时钟本身 tz-aware（offset-naive 隐患的构造性排除）
    assert utc_now().tzinfo is UTC
