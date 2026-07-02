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
- FR-E（Phase E）：**仅当** proposals > 0 才发一条 MEDIUM"待确认"通知（引导用户去审批）；
  0 提议 / 失败 / skip 全部静默（事件已审计）。与 finding-E 的关系见 _notify_pending_review。
- C6：cron 注册失败 / spawn 异常不阻塞 gateway，graceful degrade
"""

from __future__ import annotations

import asyncio
import os
import zoneinfo
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final, Protocol

import structlog
from apscheduler.triggers.cron import CronTrigger
from octoagent.core.models.delegation import (
    WORK_TERMINAL_STATUSES,
    DelegationTargetKind,
    Work,
    WorkStatus,
)
from octoagent.core.models.enums import ActorType, EventType, TaskStatus
from octoagent.core.models.event import Event
from octoagent.core.models.task import RequesterInfo
from octoagent.core.models.task import Task as TaskModel
from octoagent.memory.models import (
    ConsolidationCompletedPayload,
    ConsolidationFailedPayload,
    ConsolidationSkippedPayload,
    ConsolidationTriggeredPayload,
)
from ulid import ULID

from .consolidation_config import ConsolidationConfig

if TYPE_CHECKING:
    from octoagent.core.store.agent_context_store import SqliteAgentContextStore
    from octoagent.core.store.event_store import SqliteEventStore
    from octoagent.core.store.snapshot_store import SnapshotStore
    from octoagent.core.store.task_store import SqliteTaskStore
    from octoagent.core.store.work_store import SqliteWorkStore

    from .automation_scheduler import AutomationSchedulerService
    from .consolidation_discovery import DiscoveryOutcome
    from .delegation_plane import DelegationPlaneService
    from .notification import NotificationService


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

#: 后台巩固 subagent tool_profile（NFR-3 / C5 最小权限）。**必须是 capability_pack
#: ._coerce_tool_profile 支持的合法值之一 {minimal, standard, privileged}**——传未知串
#: （如曾经的 "readonly"）会被静默降级成默认 "standard"，反而把标准工具面给了后台巩固
#: subagent，破坏只读/人审安全边界（Codex review 抓出）。"minimal" 是最受限等级
#: （_PROFILE_LEVELS[minimal]=0 < standard < privileged），符合 spec NFR-3"只需 memory
#: 读 + 提议写，不需 terminal/web"的受限要求。
CONSOLIDATION_TOOL_PROFILE: Final[str] = "minimal"

#: spawn 标识（审计 spawned_by + idempotency_key 前缀）
CONSOLIDATION_SPAWNED_BY: Final[str] = "memory_consolidation"

#: Phase E 通知 event_type（FR-E1）。是 NotificationService 的通知类型字符串（同 F102
#: "ROUTINE_DAILY_SUMMARY" 范式），**不是** core EventType 枚举成员——审计枚举事件是
#: MEMORY_CONSOLIDATION_COMPLETED，通知是其用户面衍生物。
CONSOLIDATION_PENDING_REVIEW_EVENT_TYPE: Final[str] = (
    "MEMORY_CONSOLIDATION_PENDING_REVIEW"
)


class ConsolidationDiscoveryRunner(Protocol):
    """发现端 runner 回调契约（注入式，harness 提供真实现，测试可省略）。

    给定主 Agent AGENT_PRIVATE scope + run/root_task 上下文，跑发现端（拉窗口 → LLM
    识别冗余 → 产 PENDING 候选）并返回 ``DiscoveryOutcome``。**本回调内绝不 commit 既有
    事实合并**（C4，发现端只提议）。异常应由调用方（_run_consolidation）捕获降级。
    """

    async def __call__(
        self,
        *,
        run_id: str,
        scope_id: str,
        root_task_id: str,
        window_days: int,
        max_facts: int,
    ) -> DiscoveryOutcome: ...


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
        agent_context_store: SqliteAgentContextStore | None = None,
        discovery_runner: ConsolidationDiscoveryRunner | None = None,
        notification_service: NotificationService | None = None,
    ) -> None:
        self._scheduler = scheduler
        self._task_store = task_store
        self._work_store = work_store
        self._event_store = event_store
        self._snapshot_store = snapshot_store
        self._delegation_plane = delegation_plane
        # finding-1：定位主 Agent MAIN runtime 以注入其 AGENT_PRIVATE namespace scope
        # 给后台巩固 subagent（cron 无执行上下文，不能靠 exec_ctx 派生 caller）。
        # None 时降级（subagent 拿不到目标记忆，写 warning，不阻断 spawn——graceful）。
        self._agent_context_store = agent_context_store
        # Phase C 发现端 runner（注入式回调：给定 scope/run/root_task → 跑发现端产候选）。
        # None 时跳过发现端——退回 Phase B 纯 spawn 编排行为（trigger 测试无 runner 即此路径）。
        # 拆成回调而非直接持 MemoryService/store，是为了：①保 trigger 测试零改动（无 runner =
        # Phase B 行为）；②发现端的 MemoryService 需按 scope 解析（memory_runtime_service），
        # 在 harness 构造期注入工厂比在本服务里组装更干净（避免本服务持一堆 memory 子 store）。
        self._discovery_runner = discovery_runner
        # Phase E：巩固完成"待确认"通知（FR-E）。None 时静默跳过（C6 降级——通知不可用
        # 不影响巩固主流程，用户仍可经 Web 候选列表主动发现，FR-C6）。
        self._notification_service = notification_service
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

            # FR-A5 跨 tick 单飞补强（Codex review）：进程内 _running bool 只覆盖本次
            # orchestration tick——spawn(callback_mode="async") 返回后 finally 即复位
            # _running，但巩固 subagent 仍在后台跑。若上一次巩固 child 未达终态时本次又触发
            # （misfire 补跑 / 跨日 / 进程重启后 _running 丢失但 child 仍活），仅靠 bool 拦不住，
            # 会并行起多个 subagent 处理同一批记忆（浪费 + 重复提议）。补一道**持久态**前置检查：
            # root Work 下存在非终态 child Work → 视为"巩固进行中"，写 SKIPPED 跳过。只读检查、
            # 不改 async 模型，与 spec FR-A5 try-check-skip 语义一致（DelegationManager capacity 仍
            # 是兜底硬限）。查询失败时降级放行（不阻断巩固，与 list_descendant 容错一致）。
            if await self._has_active_consolidation_child(root_work.work_id):
                await self._emit_skipped(reason="already_running", run_id=run_id)
                logger.info("consolidation_skipped_active_child_running")
                return

            # finding-1：解析主 Agent MAIN runtime，注入其身份让巩固 subagent 经 α 共享
            # 语义读到主 Agent AGENT_PRIVATE 记忆（它要回顾/合并的目标）。找不到时降级
            # （subagent 拿不到记忆，task_runner 会写 namespaces_empty warning，不阻断）。
            main_runtime_id, main_project_id = await self._resolve_main_agent_runtime()
            extra_control_metadata: dict[str, Any] = {}
            if main_runtime_id:
                extra_control_metadata["synthetic_caller_agent_runtime_id"] = (
                    main_runtime_id
                )
                if main_project_id:
                    extra_control_metadata["synthetic_caller_project_id"] = (
                        main_project_id
                    )

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
                tool_profile=CONSOLIDATION_TOOL_PROFILE,
                title="记忆巩固",
                spawned_by=CONSOLIDATION_SPAWNED_BY,
                callback_mode="async",
                emit_audit_event=False,
                audit_task_fallback=CONSOLIDATION_ROOT_TASK_ID,
                extra_control_metadata=extra_control_metadata or None,
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

            # Phase C：spawn 成功后跑发现端（拉窗口 → LLM 识别冗余 → 产 PENDING 候选）。
            # spawn 提供 H2 对等审计容器（SUBAGENT_INTERNAL session + cleanup +
            # SUBAGENT_COMPLETED）+ finding-1 注入的 caller scope；发现端做确定性结构化工作。
            # discovery_runner 为 None（trigger 测试）→ 跳过，退回 Phase B 纯 spawn 行为。
            # 发现端异常 → 写 FAILED 不崩（C6），不阻塞 finally 复位 _running。
            await self._run_discovery(
                run_id=run_id,
                trigger_ts=trigger_ts,
                child_task_id=result.task_id or "",
                config=config,
                main_runtime_id=main_runtime_id,
                main_project_id=main_project_id,
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

    async def _has_active_consolidation_child(self, root_work_id: str) -> bool:
        """root Work 下是否存在非终态 child Work（FR-A5 跨 tick 单飞补强）。

        进程内 ``_running`` bool 只覆盖本次 orchestration tick；spawn(async) 返回后
        subagent 仍在后台跑，跨 tick（misfire 补跑 / 跨日 / 进程重启 _running 丢失）只靠
        bool 拦不住并行巩固。本检查用**持久态** child Work 状态判定"巩固进行中"。

        Args:
            root_work_id: 巩固 root Work id（child 的 parent_work_id）。

        Returns:
            True：存在至少一个非终态（非 WORK_TERMINAL_STATUSES）child Work → 应跳过。
            False：无 child 或全部终态 → 可派新一轮。查询异常时返回 False（降级放行，
            不阻断巩固——DelegationManager capacity 仍是兜底硬限）。
        """
        try:
            children = await self._work_store.list_works(
                parent_work_id=root_work_id
            )
        except Exception:
            logger.exception("consolidation_active_child_check_failed")
            return False
        return any(
            child.status not in WORK_TERMINAL_STATUSES for child in children
        )

    # ============================================================
    # 主 Agent runtime/scope 定位（finding-1：注入巩固目标记忆 scope）
    # ============================================================

    async def _resolve_main_agent_runtime(self) -> tuple[str, str]:
        """定位最近活跃的主 Agent MAIN runtime（id, project_id）。

        finding-1 根因：cron 后台合成 spawn **没有当前执行上下文**——``_launch_child_task``
        从 ``get_current_execution_context()`` 取不到 caller_agent_runtime_id → 下游
        ``task_runner`` 把 caller 当 ``<unknown>`` 跳过 AGENT_PRIVATE namespace 查询 →
        巩固 subagent **读不到它要合并的目标记忆**（α 共享语义 fail-closed）。

        修复策略：F127 v0.1 巩固范围 = 主 Agent AGENT_PRIVATE（spec §约束，跨 project/
        Worker 私有留后续）。这里查最近活跃的 MAIN runtime（``list_agent_runtimes`` 按
        updated_at DESC，无需 project_id——系统级 cron 不绑定单一 project），把它的
        runtime_id 经 ``synthetic_caller_agent_runtime_id`` 注入，让 task_runner 用现有
        namespace 查询路径解析其 AGENT_PRIVATE namespace（**零并行路径**，复用 α 语义）。

        Returns:
            (agent_runtime_id, project_id)：找不到时返回 ("", "")（调用方降级，不阻断）。
        """
        if self._agent_context_store is None:
            return "", ""
        try:
            from octoagent.core.models.agent_context import (
                AgentRuntimeRole,
                AgentRuntimeStatus,
            )

            runtimes = await self._agent_context_store.list_agent_runtimes(
                role=AgentRuntimeRole.MAIN,
            )
        except Exception:
            logger.exception("consolidation_main_runtime_lookup_failed")
            return "", ""
        # list_agent_runtimes 已按 updated_at DESC 排序；优先 active，无 active 退最近一条。
        active = [
            r
            for r in runtimes
            if getattr(r, "status", None) == AgentRuntimeStatus.ACTIVE
        ]
        chosen = active[0] if active else (runtimes[0] if runtimes else None)
        if chosen is None:
            logger.warning("consolidation_no_main_runtime_found")
            return "", ""
        return chosen.agent_runtime_id, chosen.project_id

    async def _resolve_main_agent_scope_id(
        self, *, main_runtime_id: str, main_project_id: str
    ) -> str:
        """把主 Agent MAIN runtime → AGENT_PRIVATE namespace 首个 scope_id（发现端拉窗口用）。

        复用 ``resolve_worker_default_scope_id`` 同逻辑（按 (project_id, agent_runtime_id,
        AGENT_PRIVATE) 三元组查 namespace 取 memory_scope_ids[0]），但不依赖 ToolDeps——
        本服务直接调 ``agent_context_store.list_memory_namespaces``。找不到返回 ""（调用方
        降级跳过发现端，不阻断）。
        """
        if self._agent_context_store is None or not main_runtime_id:
            return ""
        try:
            from octoagent.core.models.agent_context import MemoryNamespaceKind

            namespaces = await self._agent_context_store.list_memory_namespaces(
                project_id=main_project_id or None,
                agent_runtime_id=main_runtime_id,
                kind=MemoryNamespaceKind.AGENT_PRIVATE,
            )
        except Exception:
            logger.exception("consolidation_scope_resolve_failed")
            return ""
        if not namespaces:
            logger.warning(
                "consolidation_no_agent_private_namespace",
                runtime_id=main_runtime_id,
            )
            return ""
        scope_ids = getattr(namespaces[0], "memory_scope_ids", None) or []
        return scope_ids[0] if scope_ids else ""

    # ============================================================
    # Phase C 发现端编排（spawn 成功后跑发现端 + 写 run 审计）
    # ============================================================

    async def _run_discovery(
        self,
        *,
        run_id: str,
        trigger_ts: datetime,
        child_task_id: str,
        config: ConsolidationConfig,
        main_runtime_id: str,
        main_project_id: str,
    ) -> None:
        """跑发现端（拉窗口 → LLM 识别冗余 → 产 PENDING 候选）+ 写 COMPLETED/FAILED。

        - discovery_runner 为 None → 跳过（Phase B 纯 spawn 行为，trigger 测试路径）。
        - scope 解析失败 → 跳过发现端（写 COMPLETED proposals=0 fallback，不算 FAILED——
          没记忆可整理是正常空运行）。
        - 发现端异常 → 写 FAILED（C6 不崩，不阻塞 finally 复位 _running）。

        **C4**：发现端只产 PENDING 候选——既有事实 MERGE 绝不在此 commit（Phase D 人审）。
        """
        if self._discovery_runner is None:
            return  # Phase B 行为：无 runner 不跑发现端

        scope_id = await self._resolve_main_agent_scope_id(
            main_runtime_id=main_runtime_id, main_project_id=main_project_id
        )
        if not scope_id:
            # 无 scope（无主 Agent 记忆 namespace）→ 空运行 COMPLETED（非 FAILED）
            await self._emit_completed(
                run_id=run_id,
                facts_reviewed=0,
                proposals_made=0,
                elapsed_ms=0,
                fallback=True,
            )
            logger.info("consolidation_discovery_skipped_no_scope", run_id=run_id)
            return

        started = datetime.now(UTC)
        try:
            outcome: DiscoveryOutcome = await self._discovery_runner(
                run_id=run_id,
                scope_id=scope_id,
                root_task_id=CONSOLIDATION_ROOT_TASK_ID,
                window_days=config.consolidation_window_days,
                max_facts=config.consolidation_max_facts,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._emit_failed(
                run_id=run_id,
                error_type=type(exc).__name__,
                error_msg=str(exc)[:200],
            )
            logger.exception("consolidation_discovery_failed", run_id=run_id)
            return

        elapsed_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
        await self._emit_completed(
            run_id=run_id,
            facts_reviewed=outcome.facts_reviewed,
            proposals_made=outcome.proposals_made,
            elapsed_ms=elapsed_ms,
            fallback=outcome.fallback,
        )
        # Phase E（FR-E1/E2）：审计事件先落盘，通知 best-effort 在后——仅当有待审提议才发
        # （引导用户去审批）；0 提议不发（无噪声）。
        await self._notify_pending_review(
            run_id=run_id,
            facts_reviewed=outcome.facts_reviewed,
            proposals_made=outcome.proposals_made,
            config=config,
        )
        logger.info(
            "consolidation_discovery_completed",
            run_id=run_id,
            facts_reviewed=outcome.facts_reviewed,
            proposals_made=outcome.proposals_made,
            fallback=outcome.fallback,
        )

    # ============================================================
    # Phase E 通知（FR-E：仅"有提议待确认"这一种情况发）
    # ============================================================

    async def _notify_pending_review(
        self,
        *,
        run_id: str,
        facts_reviewed: int,
        proposals_made: int,
        config: ConsolidationConfig,
    ) -> None:
        """巩固产出待审提议 → 发一条 MEDIUM"整理了记忆，N 条合并建议待确认"通知。

        **发/不发的完整决策表（FR-E1/E2 + finding-E 调和）**：
        - proposals_made > 0 → 发**一条** MEDIUM（引导用户去候选列表审批，有行动价值）
        - proposals_made == 0（事实已干净 / fallback 空运行）→ **不发**（无行动价值，纯噪声）
        - 巩固 FAILED / SKIPPED → **不发**（调用点只在 COMPLETED 成功路径；失败静默，
          事件已审计，Constitution C2/C8 可观测不靠推送）
        - notification_service 未注入 → 静默跳过（C6 降级）

        **与 finding-E（round4）的关系——两者互补不冲突**：finding-E 压掉的是
        ``channel=="system"`` 后台 Task 的**通用**任务完成/失败/状态变更推送
        （TaskRunner._notify_completion / audit_worker_error / orchestrator
        ._notify_state_change）——那些是"后台任务刷存在感"，用户看不到对应任务还收推送，
        自相矛盾。本方法是**专用直调** ``notify_task_state_change``（同 F102 daily routine
        summary 范式——finding-E commit 已显式注明该路径不受抑制），只在"有提议等用户拍板"
        时发，是用户**必须知道**的待办引导。绝不恢复通用路径的抑制。

        **H1**：这是系统级通知（NotificationService 渠道推送），不是 Agent 对话——
        无 session / 无对话通道，用户感知是"系统帮我整理了记忆"。

        **quiet hours（FR-E3）**：MEDIUM 受 quiet hours 约束，由 NotificationService
        自身处理（quiet 内 discard + NOTIFICATION_DISPATCHED(filtered=true) 审计，
        F101 H4 契约）——深夜 03:00 触发的通知若在用户 quiet hours 内会被丢弃，用户
        仍可经 Web 候选列表红点主动发现（FR-C6）。本方法不重复实现时段判断。

        **幂等（FR-E4）**：``state_transition_event_id=run_id`` → notification_id =
        sha256(root_task:event_type:run_id)[:16]——同一 run 重放不双发，不同 run 各发一条。
        """
        if proposals_made <= 0:
            return  # FR-E2：无提议不噪声
        if self._notification_service is None:
            logger.debug("consolidation_notify_skipped_no_service", run_id=run_id)
            return

        from .notification import NotificationPriority

        summary = (
            f"帮你整理了记忆：回顾 {facts_reviewed} 条近期事实，"
            f"{proposals_made} 条合并建议待确认"
        )
        try:
            await self._notification_service.notify_task_state_change(
                task_id=CONSOLIDATION_ROOT_TASK_ID,
                event_type=CONSOLIDATION_PENDING_REVIEW_EVENT_TYPE,
                # payload 无敏感原文（FR-D1 PII 惯例）：计数 + run_id 引用，合并内容
                # 用户去候选列表看（payload 会进 NOTIFICATION_DISPATCHED 审计事件）。
                payload={
                    "summary": summary,
                    "facts_reviewed": facts_reviewed,
                    "proposals_made": proposals_made,
                    "run_id": run_id,
                },
                priority=NotificationPriority.MEDIUM,
                state_transition_event_id=run_id,
                session_id=None,
                channels=config.summary_channels,
            )
            logger.info(
                "consolidation_pending_review_notified",
                run_id=run_id,
                proposals_made=proposals_made,
                channels=sorted(config.summary_channels),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            # C6：通知失败不影响巩固运行结论（COMPLETED 已落盘）
            logger.exception("consolidation_notify_failed", run_id=run_id)

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

    async def _emit_completed(
        self,
        *,
        run_id: str,
        facts_reviewed: int,
        proposals_made: int,
        elapsed_ms: int,
        fallback: bool,
    ) -> None:
        payload = ConsolidationCompletedPayload(
            run_id=run_id,
            facts_reviewed=facts_reviewed,
            proposals_made=proposals_made,
            elapsed_ms=elapsed_ms,
            fallback=fallback,
        )
        await self._safe_append_event(
            EventType.MEMORY_CONSOLIDATION_COMPLETED, payload.model_dump()
        )

    async def _emit_failed(
        self, *, run_id: str, error_type: str, error_msg: str
    ) -> None:
        payload = ConsolidationFailedPayload(
            run_id=run_id, error_type=error_type, error_msg=error_msg
        )
        await self._safe_append_event(
            EventType.MEMORY_CONSOLIDATION_FAILED, payload.model_dump()
        )

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
    "CONSOLIDATION_PENDING_REVIEW_EVENT_TYPE",
    "CONSOLIDATION_ROOT_TASK_ID",
    "CONSOLIDATION_ROOT_WORK_ID",
    "CONSOLIDATION_SPAWNED_BY",
    "CONSOLIDATION_TOOL_PROFILE",
    "MemoryConsolidationService",
]
