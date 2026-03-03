"""漂移检测策略 -- Feature 011 FR-009, FR-010, FR-011, FR-012

DriftDetectionStrategy: 可插拔检测器协议（Strategy 模式）
NoProgressDetector: 无进展检测（P0 核心，FR-009, FR-010）
StateMachineDriftDetector: 状态机驻留检测（P1，FR-011）
RepeatedFailureDetector: 重复失败检测（P1，FR-012）
"""

from datetime import UTC, datetime, timedelta
from typing import Protocol

import structlog

from octoagent.core.models.enums import EventType, TaskStatus, TERMINAL_STATES
from octoagent.core.models.task import Task
from octoagent.core.store.event_store import SqliteEventStore

from .config import WatchdogConfig
from .models import DriftResult

log = structlog.get_logger()

# 进展事件类型集合（FR-009 规定的 7 种）
PROGRESS_EVENT_TYPES: frozenset[EventType] = frozenset({
    EventType.MODEL_CALL_STARTED,
    EventType.MODEL_CALL_COMPLETED,
    EventType.TOOL_CALL_STARTED,
    EventType.TOOL_CALL_COMPLETED,
    EventType.TASK_HEARTBEAT,
    EventType.TASK_MILESTONE,
    EventType.CHECKPOINT_SAVED,
})


class DriftDetectionStrategy(Protocol):
    """漂移检测策略协议（Strategy 模式，T020）

    所有检测器实现此协议，WatchdogScanner 通过 Protocol 调用，
    支持 P1 阶段插入新检测器而无需修改 Scanner 主体。
    """

    async def check(
        self,
        task: Task,
        event_store: SqliteEventStore,
        config: WatchdogConfig,
    ) -> DriftResult | None:
        """检测任务是否存在漂移

        Args:
            task: 待检测任务
            event_store: EventStore 实例（只读查询）
            config: Watchdog 配置

        Returns:
            DriftResult 表示检测到漂移，None 表示正常（无漂移）
        """
        ...


class NoProgressDetector:
    """无进展检测器（FR-009, FR-010，P0 核心）

    检测策略：
    1. 查询 no_progress_threshold 时间窗口内的进展事件
    2. 若窗口内有进展事件 -> 无漂移（返回 None）
    3. 若最近事件是 MODEL_CALL_STARTED（LLM 等待期豁免）-> 返回 None
    4. 若无历史事件，使用 task.updated_at 作为时间参照
    5. 超过阈值无进展 -> 返回 DriftResult(drift_type="no_progress")

    边界情况：
    - 终态任务直接跳过（FR-013）
    - task.updated_at 作为 last_event_ts 降级（边界情况 4）
    """

    async def check(
        self,
        task: Task,
        event_store: SqliteEventStore,
        config: WatchdogConfig,
    ) -> DriftResult | None:
        """无进展漂移检测"""
        # 终态任务不检测（FR-013）
        if TaskStatus(task.status) in TERMINAL_STATES:
            return None

        threshold = config.no_progress_threshold_seconds
        now = datetime.now(UTC)
        since_ts = now - timedelta(seconds=threshold)

        # 查询时间窗口内的进展事件
        progress_events = await event_store.get_events_by_types_since(
            task_id=task.task_id,
            event_types=list(PROGRESS_EVENT_TYPES),
            since_ts=since_ts,
        )

        if progress_events:
            # 窗口内有进展事件，无漂移
            return None

        # 检查 LLM 等待期豁免（FR-010）
        # 若 MODEL_CALL_STARTED 在 no_progress_threshold 窗口内，说明 LLM 仍在推理，豁免本次检测
        model_started_events = await event_store.get_events_by_types_since(
            task_id=task.task_id,
            event_types=[EventType.MODEL_CALL_STARTED],
            since_ts=since_ts,
        )
        if model_started_events:
            # LLM 等待期内，豁免
            log.debug(
                "no_progress_detector_llm_exemption",
                task_id=task.task_id,
            )
            return None

        # 获取最近事件时间戳（用于计算 stall_duration）
        latest_ts = await event_store.get_latest_event_ts(task.task_id)
        if latest_ts is None:
            # 无历史事件，降级使用 task.updated_at（边界情况 4）
            latest_ts = task.updated_at
            if latest_ts.tzinfo is None:
                latest_ts = latest_ts.replace(tzinfo=UTC)
            log.debug(
                "no_progress_detector_fallback_to_updated_at",
                task_id=task.task_id,
            )

        stall_duration = (now - latest_ts).total_seconds()

        # 确保 stall_duration 为正（时钟偏差保护）
        if stall_duration < 0:
            stall_duration = 0.0

        return DriftResult(
            task_id=task.task_id,
            drift_type="no_progress",
            detected_at=now,
            stall_duration_seconds=stall_duration,
            last_progress_ts=latest_ts,
            suggested_actions=["check_worker_logs", "cancel_task_if_confirmed"],
        )


class StateMachineDriftDetector:
    """状态机漂移检测器（FR-011，P1）

    检测非终态任务长时间驻留而产生 state_machine_stall 漂移事件。
    阈值复用 no_progress_threshold_seconds（不引入独立配置项）。
    使用内部完整 TaskStatus 枚举（Constitution 原则 14）。
    """

    async def check(
        self,
        task: Task,
        event_store: SqliteEventStore,
        config: WatchdogConfig,
    ) -> DriftResult | None:
        """状态机驻留漂移检测"""
        task_status = TaskStatus(task.status)

        # 终态任务不检测（FR-013）
        if task_status in TERMINAL_STATES:
            return None

        # 阈值复用 no_progress_threshold_seconds
        threshold = config.no_progress_threshold_seconds
        now = datetime.now(UTC)

        updated_at = task.updated_at
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=UTC)

        stall_duration = (now - updated_at).total_seconds()

        if stall_duration <= threshold:
            # 驻留时间未超过阈值，正常
            return None

        log.debug(
            "state_machine_drift_detected",
            task_id=task.task_id,
            current_status=task.status,
            stall_duration_seconds=stall_duration,
            threshold_seconds=threshold,
        )

        return DriftResult(
            task_id=task.task_id,
            drift_type="state_machine_stall",
            detected_at=now,
            stall_duration_seconds=stall_duration,
            last_progress_ts=updated_at,
            # 使用内部完整 TaskStatus（Constitution 原则 14，FR-011）
            current_status=task.status,
            suggested_actions=["check_state_machine", "review_task_logs"],
        )


# 重复失败检测的失败事件类型集合（FR-012）
FAILURE_EVENT_TYPES: frozenset[EventType] = frozenset({
    EventType.MODEL_CALL_FAILED,
    EventType.TOOL_CALL_FAILED,
    EventType.SKILL_FAILED,
})


class RepeatedFailureDetector:
    """重复失败检测器（FR-012，P1）

    在 failure_window_seconds 时间窗口内统计失败事件数，
    超过 repeated_failure_threshold 时返回 repeated_failure 漂移。
    """

    async def check(
        self,
        task: Task,
        event_store: SqliteEventStore,
        config: WatchdogConfig,
    ) -> DriftResult | None:
        """重复失败漂移检测"""
        task_status = TaskStatus(task.status)

        # 终态任务不检测（FR-013）
        if task_status in TERMINAL_STATES:
            return None

        now = datetime.now(UTC)
        since_ts = now - timedelta(seconds=config.failure_window_seconds)

        # 查询失败事件
        failure_events = await event_store.get_events_by_types_since(
            task_id=task.task_id,
            event_types=list(FAILURE_EVENT_TYPES),
            since_ts=since_ts,
        )

        failure_count = len(failure_events)

        if failure_count < config.repeated_failure_threshold:
            if failure_count > 0:
                log.debug(
                    "repeated_failure_below_threshold",
                    task_id=task.task_id,
                    failure_count=failure_count,
                    threshold=config.repeated_failure_threshold,
                )
            return None

        # 生成失败类型列表（payload 中用于诊断，FR-012）
        failure_event_types = [e.type.value for e in failure_events]

        # 使用最早失败事件时间作为 last_progress_ts 参照
        earliest_failure_ts = min(e.ts for e in failure_events)

        stall_duration = (now - earliest_failure_ts).total_seconds()

        log.debug(
            "repeated_failure_detected",
            task_id=task.task_id,
            failure_count=failure_count,
            threshold=config.repeated_failure_threshold,
        )

        return DriftResult(
            task_id=task.task_id,
            drift_type="repeated_failure",
            detected_at=now,
            stall_duration_seconds=stall_duration,
            last_progress_ts=earliest_failure_ts,
            failure_count=failure_count,
            failure_event_types=failure_event_types,
            suggested_actions=[
                "review_failure_events",
                "check_external_dependencies",
                "cancel_task_if_confirmed",
            ],
        )
