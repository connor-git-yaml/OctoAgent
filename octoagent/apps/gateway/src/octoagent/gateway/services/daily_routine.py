"""F102 Proactive Followup — DailyRoutineService（spec §7.4 主类骨架）。

每天 ``daily_summary_time``（默认 08:30，按用户时区）由 APScheduler cron 触发，
汇总昨日 Worker 任务摘要，通过 F101 NotificationService 推送给用户。

设计要点：
- spec FR-B1 / FR-B5：cron job 注册 + audit task 占位
- spec FR-B2：9 步执行顺序（trigger → config → tasks → events → llm/fallback → notify → completed）
- spec FR-B3：LLM 路径 + deterministic fallback（Constitution C6 graceful degrade）
- spec FR-B6：CancelledError 显式 re-raise（M-1 broad-catch 教训）
- spec SD-10：时区计算严格使用 UTC-aware datetime（与 list_tasks_in_time_range 对齐）
- plan A-3：bootstrap 顺序 = _bootstrap_optional_routines 内、automation_scheduler.startup() 之后

本文件为 Phase B 骨架（仅类定义 + 公共方法签名 + DI 注入），
具体 _run_daily_summary / _generate_summary_llm / _collect_yesterday_data 实现在 Phase C / E 中完成。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import structlog

from .daily_routine_config import DailyRoutineConfig

if TYPE_CHECKING:
    from octoagent.core.store.event_store import SqliteEventStore
    from octoagent.core.store.snapshot_store import SnapshotStore
    from octoagent.core.store.task_store import SqliteTaskStore
    from octoagent.provider.router import ProviderRouter

    from .automation_scheduler import AutomationSchedulerService
    from .notification import NotificationService


logger = structlog.get_logger(__name__)


# ============================================================
# 常量（spec FR-B5 / FR-B1）
# ============================================================

#: APScheduler 调度 job 唯一标识（plan A-3）；与 event audit task_id 是两个不同概念
DAILY_ROUTINE_JOB_ID: Final[str] = "_daily_routine"

#: event_store FK 占位 task_id（spec FR-B5；与 ObservationRoutine `_observation_routine_audit` 同 pattern）
DAILY_ROUTINE_AUDIT_TASK_ID: Final[str] = "_daily_routine_audit"

#: cron misfire grace（与 automation_scheduler.py 现有约定一致，plan A-6 / CHK-2.4）
DAILY_ROUTINE_MISFIRE_GRACE_SEC: Final[int] = 30


# ============================================================
# DailyRoutineService
# ============================================================


class DailyRoutineService:
    """每日 Worker 摘要 Routine 服务（spec §7.4 主类）。

    依赖注入（spec FR-DI1）：6 个核心组件，全部由 octo_harness
    ``_bootstrap_optional_routines`` 步骤构造（plan A-3 决议）。

    Lifecycle：
    - ``startup()``：注册 cron job + ensure audit task 占位
    - 每次 cron 触发：``_run_daily_summary()`` 执行完整 9 步流程
    - ``shutdown()``：remove cron job（APScheduler 内置 awaited 任务 cancel；plan A-7）

    Args:
        scheduler: AutomationSchedulerService 实例（cron 注册）
        task_store: 查询昨日 task 列表
        event_store: 写 ROUTINE_* audit event + 查 task event 详情
        notification_service: F101 服务，推送 daily summary 通知
        snapshot_store: 读 USER.md（含 daily_summary_time / routine_active / summary_channels）
        provider_router: 调 cheap alias 做 LLM 摘要（spec FR-B3）
    """

    def __init__(
        self,
        scheduler: AutomationSchedulerService,
        task_store: SqliteTaskStore,
        event_store: SqliteEventStore,
        notification_service: NotificationService,
        snapshot_store: SnapshotStore,
        provider_router: ProviderRouter,
    ) -> None:
        self._scheduler = scheduler
        self._task_store = task_store
        self._event_store = event_store
        self._notification_service = notification_service
        self._snapshot_store = snapshot_store
        self._provider_router = provider_router
        self._started: bool = False
        self._cron_registered: bool = False

    async def startup(self) -> None:
        """注册 cron job + ensure audit task 占位（spec FR-B1 / FR-B5）。

        Phase B 骨架仅记录入口，实现细节在 Phase C 完成。
        """
        if self._started:
            logger.debug("DailyRoutineService.startup called again; skipping")
            return
        self._started = True
        logger.info(
            "DailyRoutineService.startup skeleton invoked (Phase C will fill in)"
        )

    async def shutdown(self) -> None:
        """remove cron job（spec §7.4 / plan A-7）。

        Phase B 骨架仅记录入口，实现细节在 Phase C 完成。
        """
        if not self._started:
            return
        self._started = False
        self._cron_registered = False
        logger.info(
            "DailyRoutineService.shutdown skeleton invoked (Phase C will fill in)"
        )

    async def _run_daily_summary(self) -> None:
        """cron 触发回调（spec FR-B2 9 步执行顺序）。

        Phase B 骨架仅 placeholder，实现在 Phase C 完成。
        spec FR-B6：CancelledError MUST 显式 re-raise（M-1 broad-catch 教训）。
        """
        raise NotImplementedError(
            "_run_daily_summary will be implemented in Phase C"
        )

    def _read_config(self) -> DailyRoutineConfig:
        """从 USER.md 读取 DailyRoutineConfig（spec FR-B2 步骤 2）。

        Phase B 骨架仅 placeholder，实现在 Phase C 完成。
        """
        raise NotImplementedError("_read_config will be implemented in Phase C")


__all__ = [
    "DAILY_ROUTINE_AUDIT_TASK_ID",
    "DAILY_ROUTINE_JOB_ID",
    "DAILY_ROUTINE_MISFIRE_GRACE_SEC",
    "DailyRoutineService",
]
