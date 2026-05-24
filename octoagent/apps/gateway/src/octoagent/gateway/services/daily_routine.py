"""F102 Proactive Followup — DailyRoutineService（spec §7.4 主类，Phase C 完整实现）。

每天 ``daily_summary_time``（默认 08:30，按用户时区）由 APScheduler cron 触发，
汇总昨日 Worker 任务摘要，通过 F101 NotificationService 推送给用户。

设计要点：
- spec FR-B1 / FR-B5：cron job 注册 + audit task 占位
- spec FR-B2：9 步执行顺序（trigger → config → tasks → events → llm/fallback → notify → completed）
- spec FR-B3：LLM 路径（cheap alias）+ deterministic fallback（Constitution C6 graceful degrade）
- spec FR-B6：CancelledError 显式 re-raise（M-1 broad-catch 教训）
- spec SD-10：时区计算严格使用 UTC-aware datetime（与 list_tasks_in_time_range 对齐）
- plan A-3：bootstrap 顺序 = _bootstrap_optional_routines 内、automation_scheduler.startup() 之后

Phase C 实现范围：完整 9 步执行流程 + cron 注册 + bootstrap 集成 + audit task 占位。
Phase E 范围：LLM prompt 模板细化 + token budget 截断 + priority 决策细节。
"""

from __future__ import annotations

import asyncio
import time as _time
import zoneinfo
from datetime import UTC, datetime, time, timedelta
from typing import TYPE_CHECKING, Any, Final

import structlog
from apscheduler.triggers.cron import CronTrigger
from octoagent.core.models import TaskStatus
from octoagent.core.models.enums import ActorType, EventType
from octoagent.core.models.event import Event
from ulid import ULID

from .daily_routine_config import (
    DailyRoutineConfig,
    RoutineCompletedPayload,
    RoutineFailedPayload,
    RoutineSkippedPayload,
    RoutineTriggeredPayload,
)
from .notification import NotificationPriority

if TYPE_CHECKING:
    from octoagent.core.store.event_store import SqliteEventStore
    from octoagent.core.store.snapshot_store import SnapshotStore
    from octoagent.core.store.task_store import SqliteTaskStore
    from octoagent.core.models.task import Task

    from .automation_scheduler import AutomationSchedulerService
    from .notification import NotificationService


logger = structlog.get_logger(__name__)


# ============================================================
# 常量
# ============================================================

#: APScheduler 调度 job 唯一标识（plan A-3）
DAILY_ROUTINE_JOB_ID: Final[str] = "_daily_routine"

#: event_store FK 占位 task_id（spec FR-B5；与 ObservationRoutine pattern 一致）
DAILY_ROUTINE_AUDIT_TASK_ID: Final[str] = "_daily_routine_audit"

#: cron misfire grace（plan A-6 / CHK-2.4）
DAILY_ROUTINE_MISFIRE_GRACE_SEC: Final[int] = 30

#: spec SD-7 校正：实际 TaskStatus 集合中表示"需关注"的状态（去掉 spec 写的
#: "escalated"——TaskStatus 无此值，那是 worker_service WorkItem.status）
ATTENTION_TASK_STATUSES: Final[frozenset[TaskStatus]] = frozenset({
    TaskStatus.WAITING_INPUT,
    TaskStatus.WAITING_APPROVAL,
    TaskStatus.PAUSED,
    TaskStatus.FAILED,
})

#: spec SD-9 LLM input token budget（粗估值，中文 1 字符 ≈ 1.5 token，简化按字符数算）
#: 3000 token ≈ 2000 中文字符；超限时优先保留 failed + attention task 详情
LLM_INPUT_CHAR_BUDGET: Final[int] = 2000

#: LLM output token budget（spec SD-9）
LLM_OUTPUT_TOKEN_BUDGET: Final[int] = 512


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
    - ``shutdown()``：remove cron job（APScheduler 内置 awaited 任务 cancel）
    """

    def __init__(
        self,
        scheduler: AutomationSchedulerService,
        task_store: SqliteTaskStore,
        event_store: SqliteEventStore,
        notification_service: NotificationService,
        snapshot_store: SnapshotStore,
        provider_router: Any,
    ) -> None:
        self._scheduler = scheduler
        self._task_store = task_store
        self._event_store = event_store
        self._notification_service = notification_service
        self._snapshot_store = snapshot_store
        self._provider_router = provider_router
        self._started: bool = False
        self._cron_registered: bool = False
        self._user_timezone: str = "UTC"  # 由 startup() 从 USER.md 解析后更新

    async def startup(self) -> None:
        """注册 cron job + ensure audit task 占位（spec FR-B1 / FR-B5）。"""
        if self._started:
            logger.debug("DailyRoutineService.startup called again; skipping")
            return
        self._started = True

        # FR-B5：ensure audit task 占位（防 FK 违规）
        try:
            await self._ensure_audit_task()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("daily_routine_audit_task_ensure_failed")
            # 不阻塞 startup，继续尝试注册 cron

        # 读 USER.md 获取 daily_summary_time + user timezone（NFR-3）
        config = self._read_config()

        # FR-B1：注册 cron job
        try:
            self._register_cron(config)
            self._cron_registered = True
            logger.info(
                "daily_routine_started",
                job_id=DAILY_ROUTINE_JOB_ID,
                cron_expr=config.to_crontab(),
                routine_active=config.routine_active,
                timezone=self._user_timezone,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # spec FR-B1 异常兜底（Constitution C6）：cron 注册失败不阻塞 gateway 启动
            logger.exception(
                "daily_routine_cron_register_failed",
                error_type=type(exc).__name__,
            )
            await self._emit_routine_failed(
                error_type="cron_register_failed",
                error_msg=f"{type(exc).__name__}: {exc}",
            )

    async def shutdown(self) -> None:
        """remove cron job（spec §7.4 / plan A-7）。"""
        if not self._started:
            return
        self._started = False

        if self._cron_registered:
            try:
                self._scheduler._scheduler.remove_job(DAILY_ROUTINE_JOB_ID)
            except Exception:
                logger.exception("daily_routine_cron_remove_failed")
            self._cron_registered = False
        logger.info("daily_routine_shutdown")

    # ============================================================
    # FR-B2 主路径（9 步）
    # ============================================================

    async def _run_daily_summary(self) -> None:
        """cron 触发回调，执行 spec FR-B2 完整 9 步。"""
        run_started_ts = datetime.now(UTC)
        trigger_event_id = await self._emit_routine_triggered(run_started_ts)
        t_start = _time.perf_counter()

        try:
            # Step 2：读 config
            config = self._read_config()

            # Step 3：routine_active=False → skipped
            if not config.routine_active:
                await self._emit_routine_skipped(reason="routine_disabled")
                logger.info("daily_routine_skipped_disabled")
                return

            # Step 4：计算昨日 UTC 时间窗 + 查询 task 列表
            yesterday_start_utc, yesterday_end_utc, yesterday_date_str = (
                self._compute_yesterday_range_utc(run_started_ts)
            )
            tasks = await self._task_store.list_tasks_in_time_range(
                yesterday_start_utc, yesterday_end_utc
            )

            # Step 5：空数据（SD-8）直接写 ROUTINE_COMPLETED 不推送
            if not tasks:
                elapsed_ms = int((_time.perf_counter() - t_start) * 1000)
                await self._emit_routine_completed(
                    date=yesterday_date_str,
                    worker_count=0,
                    failed_count=0,
                    attention_count=0,
                    elapsed_ms=elapsed_ms,
                    llm_elapsed_ms=None,
                    fallback=False,
                    summary_length=0,
                    channels=None,
                )
                logger.info("daily_routine_empty_no_push", date=yesterday_date_str)
                return

            # Step 6-7：汇总指标
            worker_count = len(tasks)
            failed_count = sum(1 for t in tasks if t.status == TaskStatus.FAILED)
            attention_count = sum(
                1 for t in tasks if t.status in ATTENTION_TASK_STATUSES
            )

            # Step 8：摘要生成（LLM 或 fallback）
            summary_text, used_fallback, llm_elapsed_ms = await self._generate_summary(
                tasks, yesterday_date_str, worker_count, failed_count, attention_count
            )

            # Step 9a：推送通知（FR-B4 priority 决策 + FR-B7 调用样板）
            priority = (
                NotificationPriority.MEDIUM
                if attention_count > 0
                else NotificationPriority.LOW
            )
            await self._notification_service.notify_task_state_change(
                task_id=DAILY_ROUTINE_AUDIT_TASK_ID,
                event_type="ROUTINE_DAILY_SUMMARY",
                payload={
                    "summary": summary_text,
                    "worker_count": worker_count,
                    "failed_count": failed_count,
                    "attention_count": attention_count,
                    "date": yesterday_date_str,
                    "fallback": used_fallback,
                },
                priority=priority,
                session_id=None,
                state_transition_event_id=trigger_event_id,
                channels=config.summary_channels,
            )

            # Step 9b：写 ROUTINE_COMPLETED
            elapsed_ms = int((_time.perf_counter() - t_start) * 1000)
            await self._emit_routine_completed(
                date=yesterday_date_str,
                worker_count=worker_count,
                failed_count=failed_count,
                attention_count=attention_count,
                elapsed_ms=elapsed_ms,
                llm_elapsed_ms=llm_elapsed_ms,
                fallback=used_fallback,
                summary_length=len(summary_text),
                channels=sorted(config.summary_channels),
            )
            logger.info(
                "daily_routine_completed",
                date=yesterday_date_str,
                worker_count=worker_count,
                failed_count=failed_count,
                attention_count=attention_count,
                elapsed_ms=elapsed_ms,
                fallback=used_fallback,
            )

        except asyncio.CancelledError:
            # spec FR-B6：CancelledError MUST 显式 re-raise（M-1 broad-catch 教训）
            raise
        except Exception as exc:
            await self._emit_routine_failed(
                error_type=type(exc).__name__,
                error_msg=str(exc),
            )
            logger.exception(
                "daily_routine_failed",
                error_type=type(exc).__name__,
            )

    # ============================================================
    # 摘要生成（Phase C 提供基础结构，Phase E 细化 LLM 路径）
    # ============================================================

    async def _generate_summary(
        self,
        tasks: list[Task],
        date_str: str,
        worker_count: int,
        failed_count: int,
        attention_count: int,
    ) -> tuple[str, bool, int | None]:
        """生成摘要文本。

        优先走 LLM 路径（cheap alias），失败 fallback 到 deterministic 模板（spec FR-B3）。

        Returns:
            (summary_text, used_fallback, llm_elapsed_ms_or_None)
        """
        # Phase C 实施：先 fallback，Phase E 接入 LLM
        # 当前实现尝试 LLM，失败时无声 fallback——Phase E 完善 prompt + token budget
        llm_elapsed_ms: int | None = None
        try:
            t0 = _time.perf_counter()
            text = await self._generate_summary_llm(
                tasks, date_str, worker_count, failed_count, attention_count
            )
            llm_elapsed_ms = int((_time.perf_counter() - t0) * 1000)
            if text and text.strip():
                return text, False, llm_elapsed_ms
            # 空响应也走 fallback
            logger.warning("daily_routine_llm_empty_response_fallback")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "daily_routine_llm_failed_fallback",
                error_type=type(exc).__name__,
                error=str(exc),
            )

        fallback_text = self._generate_summary_fallback(
            tasks, date_str, worker_count, failed_count, attention_count
        )
        return fallback_text, True, None

    async def _generate_summary_llm(
        self,
        tasks: list[Task],
        date_str: str,
        worker_count: int,
        failed_count: int,
        attention_count: int,
    ) -> str:
        """LLM 路径（spec FR-B3 + SD-9 token budget 截断）。

        SD-9 input budget：≤ 2000 中文字符（粗估 3000 token）；超限时**优先保留**
        attention/failed task 的 status + title，其余 task 仅 title。max_tokens=512。
        """
        prompt = self._build_summary_prompt(
            tasks, date_str, worker_count, failed_count, attention_count
        )
        result = await self._provider_router.complete(
            model_alias="cheap",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=LLM_OUTPUT_TOKEN_BUDGET,
        )
        if isinstance(result, str):
            return result.strip()
        # 容错：result 可能是 dict 或 ProviderResponse 对象
        text_attr = getattr(result, "text", None)
        if isinstance(text_attr, str):
            return text_attr.strip()
        if isinstance(result, dict):
            content = result.get("content") or result.get("text") or ""
            if isinstance(content, str):
                return content.strip()
        return ""

    def _build_summary_prompt(
        self,
        tasks: list[Task],
        date_str: str,
        worker_count: int,
        failed_count: int,
        attention_count: int,
    ) -> str:
        """SD-9 LLM prompt 模板（含 token budget 截断策略）。

        优先级：
        1. attention task 详情（failed / waiting_input / waiting_approval / paused）
           — 这些是用户最需要看到的，先 enumerate
        2. 其余 task title（如 budget 不够则只列前 N 个）
        3. 任意剩余 task 用"以及 N 个其他完成任务"概括

        budget 检查：拼装时实时计算字符数，超限时停止详情、回退到 title-only。
        """
        # 1) 头部 + 总览（固定开销 ~150 char）
        header_lines = [
            "你是 OctoAgent 的 daily summary 助手。请用中文为用户生成简短摘要：",
            "",
            f"日期：{date_str}（昨日）",
            f"任务总数：{worker_count}",
            f"失败任务数：{failed_count}",
            f"待关注任务数：{attention_count}（含 failed / waiting_input / waiting_approval / paused）",
            "",
        ]

        attention_tasks = [t for t in tasks if t.status in ATTENTION_TASK_STATUSES]
        succeeded_tasks = [
            t for t in tasks if t.status not in ATTENTION_TASK_STATUSES
        ]

        # 2) 优先展示 attention task 详情
        body_lines: list[str] = []
        used_chars = sum(len(line) for line in header_lines)

        if attention_tasks:
            body_lines.append("[待关注 / 失败任务]")
            for task in attention_tasks:
                entry = f"- [{task.status.value}] {task.title}"
                if used_chars + len(entry) > LLM_INPUT_CHAR_BUDGET:
                    body_lines.append(
                        f"... 还有 {len(attention_tasks) - (len(body_lines) - 1)} 个待关注任务未列出"
                    )
                    break
                body_lines.append(entry)
                used_chars += len(entry)

        # 3) 完成任务（title-only，留空间）
        if succeeded_tasks and used_chars < LLM_INPUT_CHAR_BUDGET - 200:
            body_lines.append("")
            body_lines.append("[完成任务（仅 title）]")
            shown = 0
            for task in succeeded_tasks:
                entry = f"- {task.title}"
                if used_chars + len(entry) > LLM_INPUT_CHAR_BUDGET:
                    remaining = len(succeeded_tasks) - shown
                    if remaining > 0:
                        body_lines.append(f"... 以及 {remaining} 个其他完成任务")
                    break
                body_lines.append(entry)
                used_chars += len(entry)
                shown += 1

        # 4) 摘要要求（约 100 char 固定）
        tail_lines = [
            "",
            "摘要要求：",
            "- 3-5 句话",
            "- 开门见山，先说重点（失败 / 待关注），再说总体进展",
            "- 不要列举具体 task title，重点说趋势和需用户关注的事项",
        ]

        return "\n".join(header_lines + body_lines + tail_lines)

    def _generate_summary_fallback(
        self,
        tasks: list[Task],
        date_str: str,
        worker_count: int,
        failed_count: int,
        attention_count: int,
    ) -> str:
        """deterministic fallback 模板（spec FR-B3）。

        LLM 不可用时自动产出结构化摘要。不依赖任何外部服务，1s 内完成（NFR-2）。
        """
        lines = [
            f"昨日 Worker 摘要（{date_str}）：",
            f"- 完成任务：{worker_count} 个",
            f"- 失败任务：{failed_count} 个",
            f"- 待关注：{attention_count} 个",
        ]
        if failed_count > 0:
            lines.append("")
            lines.append("失败任务摘要：")
            failed_tasks = [t for t in tasks if t.status == TaskStatus.FAILED]
            for task in failed_tasks[:5]:
                lines.append(f"- {task.title}（失败）")
            if len(failed_tasks) > 5:
                lines.append(f"- ……以及 {len(failed_tasks) - 5} 个其他失败任务")
        return "\n".join(lines)

    # ============================================================
    # 辅助方法
    # ============================================================

    def _read_config(self) -> DailyRoutineConfig:
        """从 USER.md 读取 DailyRoutineConfig（spec FR-B2 步骤 2）。"""
        user_md_content = self._read_user_md()
        return DailyRoutineConfig.from_user_md(user_md_content)

    def _read_user_md(self) -> str | None:
        """读取 USER.md 全文（兼容同步 / 异步 SnapshotStore）。"""
        get_live = getattr(self._snapshot_store, "get_live_state", None)
        if get_live is None:
            return None
        try:
            result = get_live("USER.md")
            # SnapshotStore.get_live_state 是同步方法（F101 实测）
            if isinstance(result, str):
                return result
            return None
        except Exception:
            logger.exception("daily_routine_read_user_md_failed")
            return None

    def _compute_yesterday_range_utc(
        self, now_utc: datetime
    ) -> tuple[datetime, datetime, str]:
        """按用户本地时区计算"昨日"窗口，转为 UTC datetime（spec SD-10）。

        Returns:
            (yesterday_start_utc, yesterday_end_utc, yesterday_date_str "YYYY-MM-DD")
        """
        try:
            user_tz = zoneinfo.ZoneInfo(self._user_timezone)
        except (zoneinfo.ZoneInfoNotFoundError, ValueError):
            logger.warning(
                "daily_routine_user_timezone_invalid_fallback_utc",
                user_timezone=self._user_timezone,
            )
            user_tz = UTC

        now_local = now_utc.astimezone(user_tz)
        # 昨日 = [yesterday_00:00 local, today_00:00 local)
        today_local_midnight = now_local.replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        yesterday_local_midnight = today_local_midnight - timedelta(days=1)

        yesterday_start_utc = yesterday_local_midnight.astimezone(UTC)
        yesterday_end_utc = today_local_midnight.astimezone(UTC)
        yesterday_date_str = yesterday_local_midnight.strftime("%Y-%m-%d")

        return yesterday_start_utc, yesterday_end_utc, yesterday_date_str

    async def _ensure_audit_task(self) -> None:
        """确保 _daily_routine_audit task 占位存在（spec FR-B5）。

        ObservationRoutine pattern：当 audit task 不存在时插入，存在则 no-op。
        """
        existing = await self._task_store.get_task(DAILY_ROUTINE_AUDIT_TASK_ID)
        if existing is not None:
            return

        from octoagent.core.models.task import RequesterInfo, Task as TaskModel

        now_utc = datetime.now(UTC)
        audit_task = TaskModel(
            task_id=DAILY_ROUTINE_AUDIT_TASK_ID,
            created_at=now_utc,
            updated_at=now_utc,
            status=TaskStatus.SUCCEEDED,  # 系统占位 task，标记为已完成态避免被业务逻辑捡起
            title="F102 Daily Routine 审计占位",
            requester=RequesterInfo(channel="system", sender_id="daily_routine"),
        )
        await self._task_store.create_task(audit_task)
        # 提交事务（确保 FK 引用立即可见）
        conn = getattr(self._task_store, "_conn", None)
        if conn is not None and hasattr(conn, "commit"):
            try:
                await conn.commit()
            except Exception:
                logger.exception("daily_routine_audit_task_commit_failed")

    def _register_cron(self, config: DailyRoutineConfig) -> None:
        """向 AutomationSchedulerService 注册 cron job（spec FR-B1）。"""
        cron_expr = config.to_crontab()
        try:
            user_tz_zoneinfo = zoneinfo.ZoneInfo(self._user_timezone)
        except (zoneinfo.ZoneInfoNotFoundError, ValueError):
            user_tz_zoneinfo = UTC

        self._scheduler._scheduler.add_job(
            self._run_daily_summary,
            trigger=CronTrigger.from_crontab(cron_expr, timezone=user_tz_zoneinfo),
            id=DAILY_ROUTINE_JOB_ID,
            replace_existing=True,
            misfire_grace_time=DAILY_ROUTINE_MISFIRE_GRACE_SEC,
        )

    # ============================================================
    # Event emit helpers (spec FR-E1/E2/E3)
    # ============================================================

    async def _emit_routine_triggered(self, trigger_ts: datetime) -> str:
        """写 ROUTINE_TRIGGERED 事件，返回 event_id（用于 notify state_transition_event_id）。"""
        payload = RoutineTriggeredPayload(trigger_ts=trigger_ts.isoformat())
        event = self._build_routine_event(EventType.ROUTINE_TRIGGERED, payload.model_dump())
        await self._safe_append_event(event)
        return event.event_id

    async def _emit_routine_completed(
        self,
        *,
        date: str,
        worker_count: int,
        failed_count: int,
        attention_count: int,
        elapsed_ms: int,
        llm_elapsed_ms: int | None,
        fallback: bool,
        summary_length: int,
        channels: list[str] | None,
    ) -> None:
        payload = RoutineCompletedPayload(
            date=date,
            worker_count=worker_count,
            failed_count=failed_count,
            attention_count=attention_count,
            elapsed_ms=elapsed_ms,
            llm_elapsed_ms=llm_elapsed_ms,
            fallback=fallback,
            summary_length=summary_length,
            channels=channels,
        )
        event = self._build_routine_event(EventType.ROUTINE_COMPLETED, payload.model_dump())
        await self._safe_append_event(event)

    async def _emit_routine_failed(self, *, error_type: str, error_msg: str) -> None:
        payload = RoutineFailedPayload(error_type=error_type, error_msg=error_msg)
        event = self._build_routine_event(EventType.ROUTINE_FAILED, payload.model_dump())
        await self._safe_append_event(event)

    async def _emit_routine_skipped(self, *, reason: str) -> None:
        payload = RoutineSkippedPayload(reason=reason)
        event = self._build_routine_event(EventType.ROUTINE_SKIPPED, payload.model_dump())
        await self._safe_append_event(event)

    def _build_routine_event(self, event_type: EventType, payload: dict[str, Any]) -> Event:
        return Event(
            event_id=f"routine-{ULID()}",
            task_id=DAILY_ROUTINE_AUDIT_TASK_ID,
            task_seq=0,
            ts=datetime.now(UTC),
            type=event_type,
            actor=ActorType.SYSTEM,
            payload=payload,
            trace_id="",
        )

    async def _safe_append_event(self, event: Event) -> None:
        """append_event_committed 优先；Constitution C6 静默降级。"""
        try:
            append_committed = getattr(
                self._event_store, "append_event_committed", None
            )
            if append_committed is not None:
                await append_committed(event)
            else:
                await self._event_store.append_event(event)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "daily_routine_event_append_failed",
                event_type=event.type.value if hasattr(event.type, "value") else str(event.type),
            )


__all__ = [
    "ATTENTION_TASK_STATUSES",
    "DAILY_ROUTINE_AUDIT_TASK_ID",
    "DAILY_ROUTINE_JOB_ID",
    "DAILY_ROUTINE_MISFIRE_GRACE_SEC",
    "DailyRoutineService",
]
