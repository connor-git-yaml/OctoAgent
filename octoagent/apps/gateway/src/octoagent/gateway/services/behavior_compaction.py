"""F111 Behavior Compactor — BehaviorCompactionService（触发编排，仿 F127 Phase B）。

双触发（拍板②）：
- **cron**（compact_time，默认 03:30 深夜）：合成 compact root Task+Work 对 → 经
  ``DelegationPlaneService.spawn_child`` 派**后台 subagent**（H2 对等审计容器：
  SUBAGENT_INTERNAL session + cleanup + SUBAGENT_COMPLETED；``callback_mode="async"``
  不阻塞主 Agent，H1 守界）→ 发现端（确定性组件，F127 归档偏离同款）逐 SHARED
  eligible 文件跑 → 仅 proposals>0 通知一条 MEDIUM。
- **手动**（``run_manual``，REST trigger / CLI 消费）：前台直调发现端（不 spawn、
  **不受 compact_active 门控**——active 只门 cron，用户显式动作永远可用，spec DP-2）、
  与 cron 共享单飞、同步返回逐文件 outcome。

设计要点（照 F127 范式 + handoff 9 坑）：
- 单飞两层：进程内 ``_running`` bool（check-then-set 在第一个 await 前，无 race）+
  持久态非终态 child Work 检查（跨 tick：misfire 补跑/重启后 bool 丢失，坑补强同款）。
- ensure root **在任何 emit 之前**（events 表 FK(task_id)，坑 1 邻接问题——SKIPPED
  事件也挂 root task）。
- root Task ``channel="system"`` + ``status=SUCCEEDED``（既有通用系统任务抑制面
  task_runner/orchestrator 按 channel 过滤自动覆盖）；root Work id 进
  ``control_plane/_base.SYSTEM_INTERNAL_WORK_IDS``（+BFS 后代排除，坑 3 占位泄漏一族）。
- spawn rejected（capacity/depth）→ SKIPPED(capacity) 优雅退出不抢用户槽；spawn
  异常 → SKIPPED(spawn_error) 不崩（C6）。
- cron 范围 = SHARED eligible 3 文件（``BEHAVIOR_COMPACT_CRON_FILE_IDS`` 从
  ``COMPACT_ELIGIBLE_FILE_IDS ∩ SHARED_BEHAVIOR_FILE_IDS`` 派生，非硬编码）；
  PROJECT/KNOWLEDGE 走手动指定 project（per-project cron fan-out defer v0.2，DP-6）。
- ``llm_client`` 是**公开注入缝**（production=harness 注 ProviderRouterMessageAdapter；
  e2e 脚本化测试在 app.state 上替换为脚本 stub——spec §6 AC-11 注归档的缝位置）。
"""

from __future__ import annotations

import asyncio
import os
import zoneinfo
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

import structlog
from octoagent.core.behavior_workspace import (
    COMPACT_ELIGIBLE_FILE_IDS,
    SHARED_BEHAVIOR_FILE_IDS,
)
from octoagent.core.models.delegation import (
    WORK_TERMINAL_STATUSES,
    DelegationTargetKind,
    Work,
)
from octoagent.core.models.enums import ActorType, EventType
from octoagent.core.models.event import Event
from octoagent.core.models.payloads import (
    BehaviorCompactCompletedPayload,
    BehaviorCompactFailedPayload,
    BehaviorCompactSkippedPayload,
    BehaviorCompactTriggeredPayload,
)
from octoagent.core.models.task import Task as TaskModel
from ulid import ULID

from .behavior_compact_config import BehaviorCompactConfig
from .behavior_compact_discovery import (
    BehaviorCompactDiscoveryService,
    BehaviorCompactLLMClient,
    CompactDiscoveryOutcome,
    FileCompactOutcome,
)
from .behavior_compact_root import (
    BEHAVIOR_COMPACT_ROOT_TASK_ID,
    BEHAVIOR_COMPACT_ROOT_WORK_ID,
    BEHAVIOR_COMPACT_SPAWNED_BY,
    ensure_behavior_compact_root,
)
from .user_md_cron import read_user_md_disk_first, register_cron_job

if TYPE_CHECKING:
    from pathlib import Path

    from octoagent.core.store.behavior_compact_store import SqliteBehaviorCompactStore
    from octoagent.core.store.event_store import SqliteEventStore
    from octoagent.core.store.snapshot_store import SnapshotStore
    from octoagent.core.store.task_store import SqliteTaskStore
    from octoagent.core.store.work_store import SqliteWorkStore

    from .automation_scheduler import AutomationSchedulerService
    from .delegation_plane import DelegationPlaneService
    from .notification import NotificationService


logger = structlog.get_logger(__name__)


# ============================================================
# 常量
# ============================================================

#: APScheduler 调度 job 唯一标识
BEHAVIOR_COMPACT_JOB_ID: Final[str] = "_behavior_compact"

# root 占位常量单一事实源在 behavior_compact_root.py（Codex round5 P3：服务与
# 路由共用同一 ensure 路径），此处 re-export 保持既有 import 面。

#: cron misfire grace（沿用 F102/F127 范式）
BEHAVIOR_COMPACT_MISFIRE_GRACE_SEC: Final[int] = 30

#: 后台审计容器 subagent worker_type / tool_profile（NFR C5 最小权限，
#: "minimal" 是 _coerce_tool_profile 合法值中最受限等级——F127 Codex 抓过传未知串
#: 被静默降级成 standard 的坑，此处沿用其修复后取值）
BEHAVIOR_COMPACT_WORKER_TYPE: Final[str] = "general"
BEHAVIOR_COMPACT_TOOL_PROFILE: Final[str] = "minimal"

#: 通知类型字符串（NotificationService 用，非 core EventType 枚举成员——审计枚举
#: 事件是 BEHAVIOR_COMPACT_COMPLETED，通知是其用户面衍生物，同 F127 范式）
BEHAVIOR_COMPACT_PENDING_REVIEW_EVENT_TYPE: Final[str] = (
    "BEHAVIOR_COMPACT_PENDING_REVIEW"
)

#: cron 扫描范围 = SHARED ∩ eligible（派生自两个单一事实源，非独立硬编码；
#: 实际 = AGENTS.md / TOOLS.md / USER.md）。PROJECT/KNOWLEDGE 手动可达（DP-6）。
BEHAVIOR_COMPACT_CRON_FILE_IDS: Final[tuple[str, ...]] = tuple(
    file_id
    for file_id in COMPACT_ELIGIBLE_FILE_IDS
    if file_id in SHARED_BEHAVIOR_FILE_IDS
)


@dataclass(slots=True)
class ManualCompactResult:
    """``run_manual`` 返回（REST trigger / CLI 消费）。"""

    run_id: str
    outcomes: list[FileCompactOutcome] = field(default_factory=list)
    skipped_reason: str = ""  # "" / "already_running"
    #: Codex round3 P2：发现端内部异常时非空（BEHAVIOR_COMPACT_FAILED 已审计）——
    #: REST/CLI 必须呈现失败，不得与"真无提议"混淆成空成功。
    error: str = ""


class BehaviorCompactionService:
    """行为文件精简编排服务（cron 触发 + 手动触发）。

    Lifecycle：``startup()`` 注册 cron + ensure root 占位；cron 触发
    ``_run_compaction()``；``shutdown()`` remove cron job。
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
        compact_store: SqliteBehaviorCompactStore,
        project_root: Path,
        llm_client: BehaviorCompactLLMClient | None = None,
        notification_service: NotificationService | None = None,
    ) -> None:
        self._scheduler = scheduler
        self._task_store = task_store
        self._work_store = work_store
        self._event_store = event_store
        self._snapshot_store = snapshot_store
        self._delegation_plane = delegation_plane
        self._compact_store = compact_store
        self._project_root = project_root
        #: 公开注入缝（e2e 脚本化测试在 harness bootstrap 后替换；生产 harness 注
        #: ProviderRouterMessageAdapter；None → 发现端 fallback 0 提议不崩）
        self.llm_client = llm_client
        self._notification_service = notification_service
        self._started: bool = False
        self._cron_registered: bool = False
        # F146 件③：当前注册的 cron key (cron_expr, timezone)——tick 内比对实现热重载
        self._registered_cron_key: tuple[str, str] | None = None
        # 单飞标志（进程内，check-then-set 在第一个 await 前 → 无 race；cron 与手动共享）
        self._running: bool = False

    # ============================================================
    # 时区降级链（复用 F102/F115/F127 语义）
    # ============================================================

    @staticmethod
    def _resolve_user_timezone(user_md_tz: str | None = None) -> str:
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
                "behavior_compact_invalid_user_timezone_fallback_utc",
                requested=candidate,
            )
            return "UTC"

    # ============================================================
    # Lifecycle
    # ============================================================

    async def startup(self) -> None:
        """ensure root Task+Work 占位 + 注册 cron job（C6：失败不阻塞 gateway）。"""
        if self._started:
            logger.debug("BehaviorCompactionService.startup called again; skipping")
            return
        self._started = True

        try:
            await self._ensure_compact_root()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("behavior_compact_root_ensure_failed")

        config = self._read_config()
        effective_tz = self._resolve_user_timezone(config.user_timezone)
        try:
            self._register_cron(config, effective_tz)
            self._cron_registered = True
            logger.info(
                "behavior_compact_started",
                job_id=BEHAVIOR_COMPACT_JOB_ID,
                cron_expr=config.to_crontab(),
                compact_active=config.compact_active,
                timezone=effective_tz,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception(
                "behavior_compact_cron_register_failed", error_type=type(exc).__name__
            )

    async def shutdown(self) -> None:
        if not self._started:
            return
        self._started = False
        if self._cron_registered:
            try:
                self._scheduler._scheduler.remove_job(BEHAVIOR_COMPACT_JOB_ID)
            except Exception:
                logger.exception("behavior_compact_cron_remove_failed")
            self._cron_registered = False
        logger.info("behavior_compact_shutdown")

    def _register_cron(self, config: BehaviorCompactConfig, user_timezone: str) -> None:
        self._registered_cron_key = register_cron_job(
            self._scheduler._scheduler,
            job_id=BEHAVIOR_COMPACT_JOB_ID,
            callback=self._run_compaction,
            cron_expr=config.to_crontab(),
            timezone_name=user_timezone,
            misfire_grace_sec=BEHAVIOR_COMPACT_MISFIRE_GRACE_SEC,
        )

    def _reconcile_cron(self, config: BehaviorCompactConfig) -> None:
        """cron 时间热重载（F146 件③，闭环 Codex round5 P2 归档的 follow-up）。

        语义：改 USER.md ``compact_time`` / 时区后，**下一次已排定的 cron tick
        读盘生效**（该次仍按旧时间触发，此后按新时间）——无需重启。失败仅 log
        （#6：热重载失败不影响本次 tick 主流程，旧调度保持，下个 tick 重试）。
        """
        if not self._cron_registered:
            return
        new_key = (
            config.to_crontab(),
            self._resolve_user_timezone(config.user_timezone),
        )
        if new_key == self._registered_cron_key:
            return
        old_key = self._registered_cron_key
        try:
            self._register_cron(config, new_key[1])
            logger.info(
                "behavior_compact_cron_rescheduled",
                old_cron=old_key,
                cron_expr=new_key[0],
                timezone=new_key[1],
            )
        except Exception:
            logger.exception("behavior_compact_cron_reschedule_failed")

    # ============================================================
    # cron 触发主流程
    # ============================================================

    async def _run_compaction(self) -> None:
        """cron 回调：单飞 → ensure root → active 检查 → 跨 tick 检查 → spawn →
        发现端 → COMPLETED/FAILED → 通知。"""
        if self._running:
            await self._emit_skipped(reason="already_running")
            logger.info("behavior_compact_skipped_already_running")
            return
        self._running = True

        run_id = f"bcpt-{ULID()}"
        trigger_ts = datetime.now(UTC)
        try:
            # ensure root 必须在任何 emit 之前（events FK，F127 同款注释语义）
            root_task, root_work = await self._ensure_compact_root()

            config = self._read_config()

            # F146 件③：cron 时间热重载——改 USER.md 时间字段后下一次 tick 生效。
            # 放在 active 检查之前：disabled 服务也跟踪时间变更，重新启用时已正确。
            self._reconcile_cron(config)

            if not config.compact_active:
                await self._emit_skipped(reason="disabled", run_id=run_id)
                logger.info("behavior_compact_skipped_disabled")
                return

            # 跨 tick 单飞补强：root Work 下存在非终态 child → 上一轮审计容器仍活
            if await self._has_active_compact_child(root_work.work_id):
                await self._emit_skipped(reason="already_running", run_id=run_id)
                logger.info("behavior_compact_skipped_active_child_running")
                return

            # spawn 后台 subagent（H2 审计容器）
            objective = (
                "[F111 behavior compact] 后台行为文件精简占位任务。"
                "实际发现逻辑由确定性组件执行（BehaviorCompactDiscoveryService），"
                "本任务是 H2 对等审计容器。"
            )
            result = await self._delegation_plane.spawn_child(
                parent_task=root_task,
                parent_work=root_work,
                objective=objective,
                worker_type=BEHAVIOR_COMPACT_WORKER_TYPE,
                target_kind=DelegationTargetKind.SUBAGENT.value,
                tool_profile=BEHAVIOR_COMPACT_TOOL_PROFILE,
                title="行为规则精简",
                spawned_by=BEHAVIOR_COMPACT_SPAWNED_BY,
                callback_mode="async",
                emit_audit_event=False,
                audit_task_fallback=BEHAVIOR_COMPACT_ROOT_TASK_ID,
            )
            if result.status == "rejected":
                await self._emit_skipped(
                    reason="capacity",
                    run_id=run_id,
                    detail=result.error_code or result.reason or "",
                )
                logger.info(
                    "behavior_compact_skipped_capacity",
                    error_code=result.error_code,
                )
                return

            file_ids = list(BEHAVIOR_COMPACT_CRON_FILE_IDS)
            await self._emit_triggered(
                run_id=run_id,
                trigger="cron",
                trigger_ts=trigger_ts,
                child_task_id=result.task_id or "",
                file_ids=file_ids,
            )
            logger.info(
                "behavior_compact_triggered",
                run_id=run_id,
                child_task_id=result.task_id,
            )

            await self._run_discovery(
                run_id=run_id,
                file_ids=file_ids,
                config=config,
                # Opus 自审精化：cron 尊重同源 REJECTED（文件不变不为被拒源
                # 反复提议+通知；文件一编辑 hash 变即自然重提）
                respect_rejected=True,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._emit_skipped(
                reason="spawn_error",
                run_id=run_id,
                detail=f"{type(exc).__name__}: {exc}",
            )
            logger.exception("behavior_compact_spawn_failed", run_id=run_id)
        finally:
            self._running = False

    # ============================================================
    # 手动触发（REST trigger / CLI）
    # ============================================================

    async def run_manual(
        self,
        *,
        file_ids: list[str] | None = None,
        project_slug: str = "default",
    ) -> ManualCompactResult:
        """手动触发：前台直调发现端（不 spawn、不受 compact_active 门控，DP-2）。

        与 cron 共享单飞（并发触发 → skipped_reason="already_running"）。
        ``file_ids=None`` → 默认 SHARED eligible 集（同 cron）。
        """
        if self._running:
            return ManualCompactResult(run_id="", skipped_reason="already_running")
        self._running = True

        run_id = f"bcpt-{ULID()}"
        trigger_ts = datetime.now(UTC)
        targets = list(file_ids) if file_ids else list(BEHAVIOR_COMPACT_CRON_FILE_IDS)
        try:
            _, root_work = await self._ensure_compact_root()
            # Codex round2 P1：手动同样过持久态单飞检查（"cron/manual 共享单飞"
            # 契约的持久半边）——cron 审计 child 未终态期间（含重启后 _running 丢失
            # 场景）手动触发一律 skip，与 cron 跨 tick 语义对称。代价（child 卡住时
            # 手动被挡）与 cron 相同且可见：用户可在任务面看到/取消该 child。
            if await self._has_active_compact_child(root_work.work_id):
                return ManualCompactResult(
                    run_id="", skipped_reason="already_running"
                )
            await self._emit_triggered(
                run_id=run_id,
                trigger="manual",
                trigger_ts=trigger_ts,
                child_task_id="",
                file_ids=targets,
            )
            outcome = await self._run_discovery(
                run_id=run_id,
                file_ids=targets,
                config=self._read_config(),
                project_slug=project_slug,
                notify=False,  # 用户在场（CLI 响应含全部结果），不推通知
                respect_rejected=False,  # 用户主动=显式重新决定（spec §0.2 语义保留）
            )
            if outcome is None:
                # Codex round3 P2：发现端异常（FAILED 已审计）→ 显式失败通道，
                # 绝不折叠成"0 提议"空成功（否则故障对用户不可见、无从排障）。
                return ManualCompactResult(
                    run_id=run_id,
                    error="发现端内部异常（已记 BEHAVIOR_COMPACT_FAILED 事件，见日志）",
                )
            return ManualCompactResult(run_id=run_id, outcomes=outcome.outcomes)
        finally:
            self._running = False

    # ============================================================
    # 发现端编排（cron / manual 共用）
    # ============================================================

    async def _run_discovery(
        self,
        *,
        run_id: str,
        file_ids: list[str],
        config: BehaviorCompactConfig,
        project_slug: str = "default",
        notify: bool = True,
        respect_rejected: bool = False,
    ) -> CompactDiscoveryOutcome | None:
        """跑发现端 + 写 COMPLETED/FAILED + （cron 路径）通知。

        发现端异常 → 写 FAILED 不崩（C6）；正常 → COMPLETED（含 fallback 标记）。
        """
        discovery = BehaviorCompactDiscoveryService(
            project_root=self._project_root,
            compact_store=self._compact_store,
            event_store=self._event_store,
            llm_client=self.llm_client,
        )
        started = datetime.now(UTC)
        try:
            outcome = await discovery.discover_files(
                run_id=run_id,
                file_ids=file_ids,
                root_task_id=BEHAVIOR_COMPACT_ROOT_TASK_ID,
                project_slug=project_slug,
                respect_rejected=respect_rejected,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._emit_failed(
                run_id=run_id,
                error_type=type(exc).__name__,
                error_msg=str(exc)[:200],
            )
            logger.exception("behavior_compact_discovery_failed", run_id=run_id)
            return None

        elapsed_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
        await self._emit_completed(
            run_id=run_id,
            files_reviewed=outcome.files_reviewed,
            proposals_made=outcome.proposals_made,
            elapsed_ms=elapsed_ms,
            fallback=outcome.fallback,
        )
        if notify:
            await self._notify_pending_review(
                run_id=run_id,
                proposals_made=outcome.proposals_made,
                config=config,
            )
        logger.info(
            "behavior_compact_discovery_completed",
            run_id=run_id,
            files_reviewed=outcome.files_reviewed,
            proposals_made=outcome.proposals_made,
            fallback=outcome.fallback,
        )
        return outcome

    # ============================================================
    # 合成 root Task+Work（F127 handoff 坑 2：spawn 必需真父对）
    # ============================================================

    async def _ensure_compact_root(self) -> tuple[TaskModel, Work]:
        """ensure compact root Task+Work 对（委托单一事实源，Codex round5 P3）。"""
        return await ensure_behavior_compact_root(self._task_store, self._work_store)

    async def _has_active_compact_child(self, root_work_id: str) -> bool:
        """root Work 下是否存在非终态 child Work（跨 tick 单飞补强，F127 同款）。

        查询异常 → False 降级放行（不阻断 compact——DelegationManager capacity 兜底）。
        """
        try:
            children = await self._work_store.list_works(parent_work_id=root_work_id)
        except Exception:
            logger.exception("behavior_compact_active_child_check_failed")
            return False
        return any(child.status not in WORK_TERMINAL_STATUSES for child in children)

    # ============================================================
    # 通知（仅 proposals>0 一条 MEDIUM，F127 决策表同款）
    # ============================================================

    async def _notify_pending_review(
        self,
        *,
        run_id: str,
        proposals_made: int,
        config: BehaviorCompactConfig,
    ) -> None:
        """proposals>0 → 一条 MEDIUM"精简提议待确认"；0 提议/失败全静默。

        - quiet hours 由 NotificationService 处理（MEDIUM 受约束，深夜触发被 discard
          + NOTIFICATION_DISPATCHED(filtered=true) 审计——用户次日经 CLI/REST 主动发现）。
        - ``session_id=""`` 全局通知桶（不绑定会话）；``state_transition_event_id=run_id``
          幂等（同 run 重放不双发）。
        - H1：系统级通知非 Agent 对话。
        """
        if proposals_made <= 0:
            return
        if self._notification_service is None:
            logger.debug("behavior_compact_notify_skipped_no_service", run_id=run_id)
            return

        from .notification import NotificationPriority

        summary = f"帮你整理了行为规则：{proposals_made} 条精简提议待确认"
        try:
            await self._notification_service.notify_task_state_change(
                task_id=BEHAVIOR_COMPACT_ROOT_TASK_ID,
                event_type=BEHAVIOR_COMPACT_PENDING_REVIEW_EVENT_TYPE,
                payload={
                    "summary": summary,
                    "proposals_made": proposals_made,
                    "run_id": run_id,
                },
                priority=NotificationPriority.MEDIUM,
                state_transition_event_id=run_id,
                session_id="",
                channels=config.summary_channels,
            )
            logger.info(
                "behavior_compact_pending_review_notified",
                run_id=run_id,
                proposals_made=proposals_made,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("behavior_compact_notify_failed", run_id=run_id)

    # ============================================================
    # 配置读取（USER.md）
    # ============================================================

    def _read_config(self) -> BehaviorCompactConfig:
        return BehaviorCompactConfig.from_user_md(self._read_user_md())

    def _read_user_md(self) -> str | None:
        """读 USER.md：磁盘优先（Codex round9 P1），snapshot live state 兜底。

        F146 件①：原 F111 内联实现收敛到共享 helper（F102/F127 姊妹服务同款推广），
        语义逐级等价——盘优先 → live state 兜底 → None（C6）。
        """
        return read_user_md_disk_first(
            self._project_root, self._snapshot_store, log_prefix="behavior_compact"
        )

    # ============================================================
    # 事件 emit（运行级）
    # ============================================================

    async def _emit_triggered(
        self,
        *,
        run_id: str,
        trigger: str,
        trigger_ts: datetime,
        child_task_id: str,
        file_ids: list[str],
    ) -> None:
        payload = BehaviorCompactTriggeredPayload(
            run_id=run_id,
            trigger=trigger,
            trigger_ts=trigger_ts.isoformat(),
            child_task_id=child_task_id,
            file_ids=file_ids,
        )
        await self._safe_append_event(
            EventType.BEHAVIOR_COMPACT_TRIGGERED, payload.model_dump()
        )

    async def _emit_skipped(
        self, *, reason: str, run_id: str = "", detail: str = ""
    ) -> None:
        payload = BehaviorCompactSkippedPayload(reason=reason, run_id=run_id)
        if detail:
            logger.debug("behavior_compact_skip_detail", reason=reason, detail=detail)
        await self._safe_append_event(
            EventType.BEHAVIOR_COMPACT_SKIPPED, payload.model_dump()
        )

    async def _emit_completed(
        self,
        *,
        run_id: str,
        files_reviewed: int,
        proposals_made: int,
        elapsed_ms: int,
        fallback: bool,
    ) -> None:
        payload = BehaviorCompactCompletedPayload(
            run_id=run_id,
            files_reviewed=files_reviewed,
            proposals_made=proposals_made,
            elapsed_ms=elapsed_ms,
            fallback=fallback,
        )
        await self._safe_append_event(
            EventType.BEHAVIOR_COMPACT_COMPLETED, payload.model_dump()
        )

    async def _emit_failed(
        self, *, run_id: str, error_type: str, error_msg: str
    ) -> None:
        payload = BehaviorCompactFailedPayload(
            run_id=run_id, error_type=error_type, error_msg=error_msg
        )
        await self._safe_append_event(
            EventType.BEHAVIOR_COMPACT_FAILED, payload.model_dump()
        )

    async def _safe_append_event(
        self, event_type: EventType, payload: dict[str, Any]
    ) -> None:
        """append 优先 committed；C6 静默降级（F102/F127 同范式）。"""
        event = Event(
            event_id=f"bcpt-{ULID()}",
            task_id=BEHAVIOR_COMPACT_ROOT_TASK_ID,
            task_seq=0,
            ts=datetime.now(UTC),
            type=event_type,
            actor=ActorType.SYSTEM,
            payload=payload,
            trace_id="",
        )
        try:
            append_committed = getattr(self._event_store, "append_event_committed", None)
            if append_committed is not None:
                await append_committed(event, update_task_pointer=False)
            else:
                await self._event_store.append_event(event)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "behavior_compact_event_append_failed",
                event_type=(
                    event.type.value if hasattr(event.type, "value") else str(event.type)
                ),
            )


__all__ = [
    "BEHAVIOR_COMPACT_CRON_FILE_IDS",
    "BEHAVIOR_COMPACT_JOB_ID",
    "BEHAVIOR_COMPACT_PENDING_REVIEW_EVENT_TYPE",
    "BEHAVIOR_COMPACT_ROOT_TASK_ID",
    "BEHAVIOR_COMPACT_ROOT_WORK_ID",
    "BEHAVIOR_COMPACT_SPAWNED_BY",
    "BEHAVIOR_COMPACT_TOOL_PROFILE",
    "BehaviorCompactionService",
    "ManualCompactResult",
]
