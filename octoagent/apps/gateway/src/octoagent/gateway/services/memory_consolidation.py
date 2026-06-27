"""F127 Sleep-Time Memory Consolidation — MemoryConsolidationService（Phase B 触发编排）。

cron 深夜触发后台记忆巩固：合成 consolidation root Task+Work 对 → 经
``DelegationPlaneService.spawn_child`` 派**后台 subagent**（``callback_mode="async"``，
不阻塞主 Agent）→ subagent 在 SUBAGENT_INTERNAL session 跑巩固逻辑（发现端是 Phase C）。

Phase B 边界（spec §0.1.4）：本服务只负责"触发 + 合成 parent + spawn 编排 + 优雅 skip +
单飞 + 事件"。**subagent 内部巩固逻辑（拉窗口/LLM 识别冗余/产提议）是 Phase C**——本
Phase 的子任务 objective 是占位描述，spawn 成功即达 Phase B 验收。

设计要点（继承 F102 范式 + spec FR-A）：
- FR-A1：cron 触发（复用 AutomationSchedulerService + CronTrigger.from_crontab + F115 时区降级链）
- FR-A2：consolidation_active=False → 写 SKIPPED(disabled) 不 spawn
- FR-A3：ensure 合成 root Task+Work 对（DP-4，沿用 F102 audit-task ensure 范式，但成对）
- FR-A4：spawn rejected（CAPACITY/depth）→ 写 SKIPPED(capacity) 优雅退出，不阻塞/不报错/不抢槽
- FR-A5：并发单飞（try-lock-skip）——运行中再触发 → 写 SKIPPED(already_running) 立即 return
- FR-A6（H1）：巩固全程不向用户发起对话；用户感知仅来自 NotificationService（Phase E）
- C6：cron 注册失败 / spawn 异常不阻塞 gateway，graceful degrade
"""

from __future__ import annotations

import asyncio
import os
import zoneinfo
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

import structlog
from apscheduler.triggers.cron import CronTrigger
from octoagent.core.models.delegation import DelegationTargetKind, Work, WorkStatus
from octoagent.core.models.enums import ActorType, EventType, TaskStatus
from octoagent.core.models.event import Event
from octoagent.core.models.task import RequesterInfo
from octoagent.core.models.task import Task as TaskModel
from octoagent.memory.models import (
    ConsolidationSkippedPayload,
    ConsolidationTriggeredPayload,
)
from ulid import ULID

from .consolidation_config import ConsolidationConfig

if TYPE_CHECKING:
    from octoagent.core.store.event_store import SqliteEventStore
    from octoagent.core.store.snapshot_store import SnapshotStore
    from octoagent.core.store.task_store import SqliteTaskStore
    from octoagent.core.store.work_store import SqliteWorkStore

    from .automation_scheduler import AutomationSchedulerService
    from .delegation_plane import DelegationPlaneService


logger = structlog.get_logger(__name__)


# ============================================================
# 常量
# ============================================================

#: APScheduler 调度 job 唯一标识
CONSOLIDATION_JOB_ID: Final[str] = "_memory_consolidation"

#: 合成 consolidation root Task / Work（DP-4：spawn_child 的真父对象 + event_store FK 占位）
#: 长驻单例（沿用 F102 audit-task 永久占位范式，OQ-2 选单例）。
CONSOLIDATION_ROOT_TASK_ID: Final[str] = "_memory_consolidation_root"
CONSOLIDATION_ROOT_WORK_ID: Final[str] = "_memory_consolidation_root_work"

#: root Task 显式 thread_id（不靠默认 "default"——子 thread 命名 `{thread_id}:child:{id}`
#: 需稳定可识别，spec §0.1.4）
CONSOLIDATION_ROOT_THREAD_ID: Final[str] = "_memory_consolidation"

#: cron misfire grace（沿用 F102 范式）
CONSOLIDATION_MISFIRE_GRACE_SEC: Final[int] = 30

#: 后台巩固 subagent worker_type（受限只读型；具体 tool_profile 收窄在 Phase C/NFR-3）
CONSOLIDATION_WORKER_TYPE: Final[str] = "general"

#: spawn 标识（审计 spawned_by + idempotency_key 前缀）
CONSOLIDATION_SPAWNED_BY: Final[str] = "memory_consolidation"


class MemoryConsolidationService:
    """睡眠时记忆巩固编排服务（Phase B：cron 触发 → 后台 spawn subagent）。

    依赖注入：与 F102 DailyRoutineService 同 bootstrap 点构造，但额外持有
    ``delegation_plane``（spawn_child 入口）+ ``work_store``（合成 root Work）。

    Lifecycle：
    - ``startup()``：注册 cron job + ensure root Task+Work 占位
    - 每次 cron 触发：``_run_consolidation()``（active 检查 → 单飞 → spawn）
    - ``shutdown()``：remove cron job
    """

    def __init__(
        self,
        *,
        scheduler: AutomationSchedulerService,
        task_store: SqliteTaskStore,
        work_store: SqliteWorkStore,
        event_store: SqliteEventStore,
        snapshot_store: SnapshotStore,
        delegation_plane: DelegationPlaneService,
    ) -> None:
        self._scheduler = scheduler
        self._task_store = task_store
        self._work_store = work_store
        self._event_store = event_store
        self._snapshot_store = snapshot_store
        self._delegation_plane = delegation_plane
        self._started: bool = False
        self._cron_registered: bool = False
        # FR-A5 并发单飞标志（进程内，单 event loop 协作式）。check-then-set 在第一个
        # await 之前完成，故无 check-then-set race（与 Hermes .tick.lock try-lock-skip 等价语义）。
        self._running: bool = False

    # ============================================================
    # 时区降级链（复用 F102 范式 / F115）
    # ============================================================

    @staticmethod
    def _resolve_user_timezone(user_md_tz: str | None = None) -> str:
        """降级链 USER.md → env OCTOAGENT_USER_TIMEZONE → UTC（复用 F102/F115 语义）。"""
        if user_md_tz:
            return user_md_tz
        candidate = os.environ.get("OCTOAGENT_USER_TIMEZONE", "").strip()
        if not candidate:
            return "UTC"
        try:
            zoneinfo.ZoneInfo(candidate)
            return candidate
        except (zoneinfo.ZoneInfoNotFoundError, ValueError):
            logger.warning(
                "consolidation_invalid_user_timezone_fallback_utc", requested=candidate
            )
            return "UTC"

    # ============================================================
    # Lifecycle
    # ============================================================

    async def startup(self) -> None:
        """ensure root Task+Work 占位 + 注册 cron job（C6：失败不阻塞 gateway）。"""
        if self._started:
            logger.debug("MemoryConsolidationService.startup called again; skipping")
            return
        self._started = True

        # ensure root 占位（防 FK 违规 + spawn parent 就位）
        try:
            await self._ensure_consolidation_root()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("consolidation_root_ensure_failed")
            # 不阻塞 startup，继续尝试注册 cron（运行时再 ensure 兜底）

        config = self._read_config()
        effective_tz = self._resolve_user_timezone(config.user_timezone)

        try:
            self._register_cron(config, effective_tz)
            self._cron_registered = True
            logger.info(
                "consolidation_started",
                job_id=CONSOLIDATION_JOB_ID,
                cron_expr=config.to_crontab(),
                consolidation_active=config.consolidation_active,
                timezone=effective_tz,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception(
                "consolidation_cron_register_failed", error_type=type(exc).__name__
            )

    async def shutdown(self) -> None:
        """remove cron job。"""
        if not self._started:
            return
        self._started = False
        if self._cron_registered:
            try:
                self._scheduler._scheduler.remove_job(CONSOLIDATION_JOB_ID)
            except Exception:
                logger.exception("consolidation_cron_remove_failed")
            self._cron_registered = False
        logger.info("consolidation_shutdown")

    def _register_cron(self, config: ConsolidationConfig, user_timezone: str) -> None:
        cron_expr = config.to_crontab()
        try:
            user_tz_zoneinfo = zoneinfo.ZoneInfo(user_timezone)
        except (zoneinfo.ZoneInfoNotFoundError, ValueError):
            user_tz_zoneinfo = UTC
        self._scheduler._scheduler.add_job(
            self._run_consolidation,
            trigger=CronTrigger.from_crontab(cron_expr, timezone=user_tz_zoneinfo),
            id=CONSOLIDATION_JOB_ID,
            replace_existing=True,
            misfire_grace_time=CONSOLIDATION_MISFIRE_GRACE_SEC,
        )

    # ============================================================
    # 触发主流程（FR-A2~A6）
    # ============================================================

    async def _run_consolidation(self) -> None:
        """cron 触发回调：active 检查 → 单飞 → ensure root → spawn 后台 subagent。

        Phase B 范围：spawn 成功即返回（子 subagent 内部巩固是 Phase C）。
        """
        # FR-A5：并发单飞 try-lock-skip。check-then-set 在任何 await 之前完成 → 无 race。
        if self._running:
            await self._emit_skipped(reason="already_running")
            logger.info("consolidation_skipped_already_running")
            return
        self._running = True

        run_id = f"mcons-{ULID()}"
        trigger_ts = datetime.now(UTC)
        try:
            # FR-A3：先 ensure root Task+Work（必须在任何 emit 之前——所有
            # MEMORY_CONSOLIDATION_* 事件 task_id 都引用 root Task，events 表有
            # FOREIGN KEY(task_id) REFERENCES tasks。若延后到 active 检查之后，
            # disabled/spawn 失败路径的 SKIPPED 事件会 FK 违规被静默丢（C2 审计缺口）。
            # 幂等（startup 已 ensure，此处运行时兜底）。
            root_task, root_work = await self._ensure_consolidation_root()

            # FR-A2：active 检查
            config = self._read_config()
            if not config.consolidation_active:
                await self._emit_skipped(reason="disabled", run_id=run_id)
                logger.info("consolidation_skipped_disabled")
                return

            # FR-A4：spawn 后台 subagent
            objective = (
                "[F127 sleep-time consolidation] 后台记忆巩固占位任务（Phase B）。"
                "回顾近期 AGENT_PRIVATE 事实、识别可合并冗余的实际逻辑在 Phase C 实现。"
            )
            result = await self._delegation_plane.spawn_child(
                parent_task=root_task,
                parent_work=root_work,
                objective=objective,
                worker_type=CONSOLIDATION_WORKER_TYPE,
                target_kind=DelegationTargetKind.SUBAGENT.value,
                tool_profile="readonly",
                title="记忆巩固",
                spawned_by=CONSOLIDATION_SPAWNED_BY,
                callback_mode="async",
                emit_audit_event=False,
                audit_task_fallback=CONSOLIDATION_ROOT_TASK_ID,
            )

            if result.status == "rejected":
                # depth/capacity 不足 → 优雅 skip（不抢用户 delegate 槽）
                await self._emit_skipped(
                    reason="capacity",
                    run_id=run_id,
                    detail=result.error_code or result.reason or "",
                )
                logger.info(
                    "consolidation_skipped_capacity",
                    error_code=result.error_code,
                    reason=result.reason,
                )
                return

            # written → 派发成功
            await self._emit_triggered(
                run_id=run_id,
                trigger_ts=trigger_ts,
                child_task_id=result.task_id,
                config=config,
            )
            logger.info(
                "consolidation_triggered",
                run_id=run_id,
                child_task_id=result.task_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # spawn launch raise（task_runner 等）或意外异常 → 优雅 skip + log（C6）。
            # Phase B 不写 FAILED 事件（FAILED 留给 Phase C 巩固逻辑真失败）；spawn 编排
            # 异常按 capacity 同级优雅降级，避免单次 cron 异常阻塞后续触发。
            await self._emit_skipped(
                reason="spawn_error",
                run_id=run_id,
                detail=f"{type(exc).__name__}: {exc}",
            )
            logger.exception("consolidation_spawn_failed", run_id=run_id)
        finally:
            self._running = False

    # ============================================================
    # 合成 root Task+Work（DP-4）
    # ============================================================

    async def _ensure_consolidation_root(self) -> tuple[TaskModel, Work]:
        """ensure 系统 owned 的 consolidation root Task+Work 对（幂等，沿用 F102 ensure 范式）。

        与 F102 _ensure_audit_task 的关键区别：spawn_child 需要 **task+work 成对**真父对象
        （_launch_child_task 硬解引用 parent_task.thread_id/.task_id/.requester.*
        + parent_work.work_id；传 None 必 AttributeError，spec §0.1.1 实测）。故这里建一对。

        Returns:
            (root_task, root_work)：已持久化的真对象，作 spawn_child 父对象。
        """
        now = datetime.now(UTC)

        existing_task = await self._task_store.get_task(CONSOLIDATION_ROOT_TASK_ID)
        if existing_task is None:
            root_task = TaskModel(
                task_id=CONSOLIDATION_ROOT_TASK_ID,
                created_at=now,
                updated_at=now,
                status=TaskStatus.SUCCEEDED,  # 系统占位，避免被业务逻辑捡起
                title="F127 记忆巩固根任务占位",
                thread_id=CONSOLIDATION_ROOT_THREAD_ID,  # 显式（子 thread 命名稳定）
                scope_id="",  # 子 NormalizedMessage 继承（系统级无特定 scope）
                requester=RequesterInfo(
                    channel="system", sender_id=CONSOLIDATION_SPAWNED_BY
                ),
            )
            await self._task_store.create_task(root_task)
        else:
            root_task = existing_task

        existing_work = await self._work_store.get_work(CONSOLIDATION_ROOT_WORK_ID)
        if existing_work is None:
            root_work = Work(
                work_id=CONSOLIDATION_ROOT_WORK_ID,
                task_id=CONSOLIDATION_ROOT_TASK_ID,
                title="F127 记忆巩固根 Work",
                status=WorkStatus.CREATED,
                target_kind=DelegationTargetKind.SUBAGENT,
                created_at=now,
                updated_at=now,
            )
            await self._work_store.save_work(root_work)
        else:
            root_work = existing_work

        # 提交事务（确保 FK 引用立即可见，沿用 F102 _ensure_audit_task commit 范式）
        conn = getattr(self._task_store, "_conn", None)
        if conn is not None and hasattr(conn, "commit"):
            try:
                await conn.commit()
            except Exception:
                logger.exception("consolidation_root_commit_failed")

        return root_task, root_work

    # ============================================================
    # 配置读取（USER.md）
    # ============================================================

    def _read_config(self) -> ConsolidationConfig:
        return ConsolidationConfig.from_user_md(self._read_user_md())

    def _read_user_md(self) -> str | None:
        """读 USER.md 全文（复用 F102 SnapshotStore.get_live_state 同步范式）。"""
        get_live = getattr(self._snapshot_store, "get_live_state", None)
        if get_live is None:
            return None
        try:
            result = get_live("USER.md")
            if isinstance(result, str):
                return result
            return None
        except Exception:
            logger.exception("consolidation_read_user_md_failed")
            return None

    # ============================================================
    # 事件 emit（FR-D1）
    # ============================================================

    async def _emit_triggered(
        self,
        *,
        run_id: str,
        trigger_ts: datetime,
        child_task_id: str,
        config: ConsolidationConfig,
    ) -> None:
        payload = ConsolidationTriggeredPayload(
            run_id=run_id,
            trigger_ts=trigger_ts.isoformat(),
            child_task_id=child_task_id,
            window_days=config.consolidation_window_days,
            max_facts=config.consolidation_max_facts,
        )
        await self._safe_append_event(
            EventType.MEMORY_CONSOLIDATION_TRIGGERED, payload.model_dump()
        )

    async def _emit_skipped(
        self, *, reason: str, run_id: str = "", detail: str = ""
    ) -> None:
        payload = ConsolidationSkippedPayload(reason=reason, run_id=run_id)
        data = payload.model_dump()
        # detail 仅入 log（不进 payload schema，保持 schema 稳定 + PII 防护）
        if detail:
            logger.debug("consolidation_skip_detail", reason=reason, detail=detail)
        await self._safe_append_event(EventType.MEMORY_CONSOLIDATION_SKIPPED, data)

    def _build_event(self, event_type: EventType, payload: dict[str, Any]) -> Event:
        return Event(
            event_id=f"mcons-{ULID()}",
            task_id=CONSOLIDATION_ROOT_TASK_ID,
            task_seq=0,
            ts=datetime.now(UTC),
            type=event_type,
            actor=ActorType.SYSTEM,
            payload=payload,
            trace_id="",
        )

    async def _safe_append_event(
        self, event_type: EventType, payload: dict[str, Any]
    ) -> None:
        """append 优先 committed；C6 静默降级（沿用 F102 _safe_append_event 范式）。"""
        event = self._build_event(event_type, payload)
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
                "consolidation_event_append_failed",
                event_type=(
                    event.type.value if hasattr(event.type, "value") else str(event.type)
                ),
            )


__all__ = [
    "CONSOLIDATION_JOB_ID",
    "CONSOLIDATION_ROOT_TASK_ID",
    "CONSOLIDATION_ROOT_WORK_ID",
    "CONSOLIDATION_SPAWNED_BY",
    "MemoryConsolidationService",
]
