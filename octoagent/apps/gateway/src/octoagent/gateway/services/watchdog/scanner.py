"""WatchdogScanner -- Feature 011 FR-004~FR-008

APScheduler job 主体：周期扫描活跃任务，运行漂移检测策略，
写入 TASK_DRIFT_DETECTED 事件，全程结构化日志记录。

核心约束：
- 不直接执行取消/暂停（Constitution 原则 4）
- 扫描失败记录 warning 不抛出（FR-007）
- 全链路透传 task_id + trace_id（FR-019, FR-020）
"""

import time
from datetime import UTC, datetime

import structlog

from octoagent.core.models.enums import ActorType, EventType, TaskStatus, TERMINAL_STATES
from octoagent.core.models.event import Event, EventCausality
from octoagent.core.models.payloads import TaskDriftDetectedPayload
from octoagent.core.store import StoreGroup

from .config import WatchdogConfig
from .cooldown import CooldownRegistry
from .detectors import DriftDetectionStrategy
from .models import NON_TERMINAL_STATUSES, DriftResult

log = structlog.get_logger()


def _new_event_id() -> str:
    """生成新的 ULID 格式 event_id"""
    import ulid
    return str(ulid.ULID())


class WatchdogScanner:
    """Watchdog 核心扫描器（FR-004 ~ FR-008）

    职责：
    1. 从 TaskStore 获取活跃任务列表（单次原子查询，spec WARNING 3）
    2. 对每个任务运行注册的检测策略（Strategy 模式可插拔）
    3. cooldown 防抖检查（FR-006）
    4. 向 EventStore 写入 TASK_DRIFT_DETECTED 事件（FR-002, FR-019）
    5. 结构化日志记录扫描元数据（FR-008）

    硬约束：
    - 绝不直接调用 task cancel/pause（Constitution 原则 4）
    - 扫描失败 try/except 全包裹，记录 warning，等待下次重试（FR-007）
    """

    def __init__(
        self,
        store_group: StoreGroup,
        config: WatchdogConfig,
        cooldown_registry: CooldownRegistry,
        detectors: list[DriftDetectionStrategy],
    ) -> None:
        self._store_group = store_group
        self._config = config
        self._cooldown = cooldown_registry
        self._detectors = detectors

    async def startup(self) -> None:
        """进程启动：重建 cooldown 注册表（FR-006 跨重启一致性）

        查询 EventStore 中 cooldown 窗口内的 TASK_DRIFT_DETECTED 事件，
        重建 cooldown 状态，避免进程重启后连续告警轰炸（边界情况 6）。
        """
        log.info("watchdog_scanner_startup", config={
            "scan_interval_seconds": self._config.scan_interval_seconds,
            "no_progress_threshold_seconds": self._config.no_progress_threshold_seconds,
            "cooldown_seconds": self._config.cooldown_seconds,
        })

        try:
            # 获取所有活跃任务 ID，用于重建 cooldown 注册表
            active_tasks = await self._store_group.task_store.list_tasks_by_statuses(
                NON_TERMINAL_STATUSES
            )
            active_task_ids = [t.task_id for t in active_tasks]

            await self._cooldown.rebuild_from_store(
                event_store=self._store_group.event_store,
                active_task_ids=active_task_ids,
                cooldown_seconds=self._config.cooldown_seconds,
            )
            log.info("watchdog_scanner_startup_complete", active_task_count=len(active_task_ids))
        except Exception as exc:
            # startup 失败不应阻止进程启动，记录 warning
            log.warning(
                "watchdog_scanner_startup_failed",
                error_type=type(exc).__name__,
                error=str(exc),
            )

    async def scan(self) -> None:
        """单次扫描周期（APScheduler 每 scan_interval_seconds 调用）

        全程 try/except 包裹，任何异常均记录 warning 不抛出（FR-007），
        保证扫描失败不影响主任务执行。
        """
        scan_start = time.monotonic()
        drift_count = 0
        active_count = 0

        try:
            # 获取活跃任务（单次原子查询，FR-013 保证终态不包含）
            active_tasks = await self._store_group.task_store.list_tasks_by_statuses(
                NON_TERMINAL_STATUSES
            )
            active_count = len(active_tasks)

            for task in active_tasks:
                # 额外防御：跳过终态任务（理论上 list_tasks_by_statuses 已过滤）
                if TaskStatus(task.status) in TERMINAL_STATES:
                    continue

                # FR-008/FR-020: structlog 绑定 task_id 上下文，全链路透传
                task_log = log.bind(task_id=task.task_id)

                # 对每个任务运行所有检测策略
                for detector in self._detectors:
                    try:
                        result = await detector.check(
                            task=task,
                            event_store=self._store_group.event_store,
                            config=self._config,
                        )
                    except Exception as det_exc:
                        # 单个检测器异常不影响其他任务/检测器（FR-007）
                        task_log.warning(
                            "watchdog_detector_error",
                            detector=type(detector).__name__,
                            error_type=type(det_exc).__name__,
                            error=str(det_exc),
                        )
                        continue

                    if result is None:
                        continue

                    # 检查 cooldown 防抖（FR-006）
                    if self._cooldown.is_in_cooldown(task.task_id, self._config.cooldown_seconds):
                        task_log.debug(
                            "watchdog_drift_cooldown_skipped",
                            drift_type=result.drift_type,
                        )
                        continue

                    # 写入 DRIFT 事件（FR-002, FR-019）
                    try:
                        # FR-020: 全链路透传 task_id/trace_id（Feature 011 已在 Task 模型中添加 trace_id）
                        task_trace_id = task.trace_id
                        await self._emit_drift_event(
                            task_id=task.task_id,
                            trace_id=task_trace_id,
                            result=result,
                        )
                        drift_count += 1
                    except Exception as emit_exc:
                        log.warning(
                            "watchdog_emit_drift_event_failed",
                            task_id=task.task_id,
                            drift_type=result.drift_type,
                            error_type=type(emit_exc).__name__,
                            error=str(emit_exc),
                        )

        except Exception as exc:
            # 扫描整体失败，记录 warning，下次重试（FR-007）
            log.warning(
                "watchdog_scan_failed",
                error_type=type(exc).__name__,
                error=str(exc),
            )

        # 结构化日志记录扫描元数据（FR-008）
        scan_duration_ms = (time.monotonic() - scan_start) * 1000
        log.info(
            "watchdog_scan_completed",
            active_task_count=active_count,
            drift_detected_count=drift_count,
            scan_duration_ms=round(scan_duration_ms, 2),
        )

    async def _emit_drift_event(
        self,
        task_id: str,
        trace_id: str,
        result: DriftResult,
    ) -> None:
        """写入 TASK_DRIFT_DETECTED 事件（FR-002, FR-019）

        构建 TaskDriftDetectedPayload，写入 EventStore，
        更新 CooldownRegistry 防止重复告警（FR-006）。
        """
        now = datetime.now(UTC)
        detected_at_iso = result.detected_at.isoformat()
        last_progress_iso = result.last_progress_ts.isoformat() if result.last_progress_ts else None

        payload = TaskDriftDetectedPayload(
            drift_type=result.drift_type,
            detected_at=detected_at_iso,
            task_id=task_id,
            trace_id=trace_id,
            last_progress_ts=last_progress_iso,
            stall_duration_seconds=result.stall_duration_seconds,
            suggested_actions=result.suggested_actions,
            # FR-021: F012 接入前 watchdog_span_id 为空字符串占位
            watchdog_span_id="",
            failure_count=result.failure_count,
            failure_event_types=result.failure_event_types,
            current_status=result.current_status,
        )

        # 获取下一个 task_seq
        next_seq = await self._store_group.event_store.get_next_task_seq(task_id)

        event = Event(
            event_id=_new_event_id(),
            task_id=task_id,
            task_seq=next_seq,
            ts=now,
            type=EventType.TASK_DRIFT_DETECTED,
            schema_version=1,
            actor=ActorType.SYSTEM,
            payload=payload.model_dump(),
            trace_id=trace_id,
            span_id="",  # FR-021 预留，F012 接入时填充
            causality=EventCausality(),
        )

        await self._store_group.event_store.append_event_committed(event)

        # 更新 cooldown 注册表（FR-006）
        self._cooldown.record_drift(task_id, now)

        log.info(
            "watchdog_drift_event_emitted",
            task_id=task_id,
            trace_id=trace_id,
            drift_type=result.drift_type,
            stall_duration_seconds=result.stall_duration_seconds,
        )
