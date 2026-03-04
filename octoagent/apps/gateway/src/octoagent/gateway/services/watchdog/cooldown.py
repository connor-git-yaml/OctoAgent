"""CooldownRegistry -- Feature 011 FR-006

Watchdog cooldown 防抖注册表，进程重启后从 EventStore 重建状态。
保证同一任务在 cooldown 窗口内不重复产生 DRIFT 事件。
"""

from datetime import UTC, datetime, timedelta

import structlog
from octoagent.core.models.enums import EventType
from octoagent.core.store.event_store import SqliteEventStore

log = structlog.get_logger()


class CooldownRegistry:
    """Watchdog cooldown 防抖注册表（spec Key Entities + FR-006）

    进程重启后通过查询 EventStore 最近 DRIFT 事件重建状态，
    保证 cooldown 跨重启一致性（边界情况 6）。

    内部以 task_id -> 最近一次 TASK_DRIFT_DETECTED 事件时间戳 维护状态。
    """

    def __init__(self) -> None:
        # task_id -> 最近一次 TASK_DRIFT_DETECTED 事件时间戳
        self._last_drift_ts: dict[str, datetime] = {}

    async def rebuild_from_store(
        self,
        event_store: SqliteEventStore,
        active_task_ids: list[str],
        cooldown_seconds: int,
    ) -> None:
        """从 EventStore 重建 cooldown 状态（进程启动时调用，FR-006）

        仅查询 cooldown 窗口内的 DRIFT 事件，窗口外的不影响当前 cooldown 判断。

        Args:
            event_store: EventStore 实例
            active_task_ids: 当前活跃任务 ID 列表
            cooldown_seconds: cooldown 时长（秒）
        """
        since_ts = datetime.now(UTC) - timedelta(seconds=cooldown_seconds)
        rebuilt_count = 0

        for task_id in active_task_ids:
            events = await event_store.get_events_by_types_since(
                task_id=task_id,
                event_types=[EventType.TASK_DRIFT_DETECTED],
                since_ts=since_ts,
            )
            if events:
                # 取最近一次 DRIFT 事件时间戳
                latest = max(e.ts for e in events)
                self._last_drift_ts[task_id] = latest
                rebuilt_count += 1

        log.info(
            "cooldown_registry_rebuilt",
            active_task_count=len(active_task_ids),
            cooldown_restored_count=rebuilt_count,
            cooldown_seconds=cooldown_seconds,
        )

    def is_in_cooldown(self, task_id: str, cooldown_seconds: int) -> bool:
        """判断任务是否在 cooldown 窗口内

        Args:
            task_id: 任务 ID
            cooldown_seconds: cooldown 时长（秒）

        Returns:
            True 表示仍在 cooldown 窗口内，不应产生新 DRIFT 事件
        """
        last_ts = self._last_drift_ts.get(task_id)
        if last_ts is None:
            return False
        elapsed = (datetime.now(UTC) - last_ts).total_seconds()
        return elapsed < cooldown_seconds

    def record_drift(self, task_id: str, ts: datetime) -> None:
        """记录最新 DRIFT 事件时间戳

        Args:
            task_id: 任务 ID
            ts: DRIFT 事件时间戳
        """
        self._last_drift_ts[task_id] = ts

    def cleanup_terminated(self, active_task_ids: set[str]) -> None:
        """移除已终止任务的 cooldown 记录，防止内存无限增长

        Args:
            active_task_ids: 当前仍活跃的任务 ID 集合
        """
        terminated = [tid for tid in self._last_drift_ts if tid not in active_task_ids]
        for tid in terminated:
            del self._last_drift_ts[tid]
        if terminated:
            log.debug("cooldown_registry_cleanup", removed_count=len(terminated))
