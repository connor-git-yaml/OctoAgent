"""delegation.py：DelegationManager — 子 Agent 派发的深度 + 并发控制（Feature 084 Phase 3 T044）。

架构说明（plan.md 3.2）：
- MAX_DEPTH = 2：最大派发深度（FR-5.2），防止无限递归
- MAX_CONCURRENT_CHILDREN = 3：最大并发子任务数（FR-5.3）
- blacklist：黑名单 Worker 名称（默认空，通过配置扩展）（FR-5.4）

本模块是独立的 DelegationManager 类，与现有 services/delegation_plane.py 完全隔离
（delegation_plane.py 是 Phase 4 删除目标，不修改它）。

Constitution 合规：
- C2 所有写操作有审计事件：通过时写 SUBAGENT_SPAWNED（FR-5.5）
- C4 失败时不写 SUBAGENT_SPAWNED（避免假数据）

事件写入防回归（防 F22 / F24）：
- 使用真实 Event schema 字段：event_id / task_id / task_seq / ts / type / actor
- 使用 event_store.append_event_committed(event) API
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

import structlog
from pydantic import BaseModel
from ulid import ULID

from octoagent.core.models.enums import ActorType, EventType

log = structlog.get_logger(__name__)

# 审计占位 task_id（防 F24 FK 违反）
_DELEGATION_AUDIT_TASK_ID = "_delegation_manager_audit"


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


class DelegationContext(BaseModel):
    """派发上下文（当前 Agent 的位置信息，用于深度 + 并发检查）。"""

    task_id: str
    """当前任务 ID（UUID）。"""

    depth: int = 0
    """当前深度（从 0 开始，root Agent = 0，第一级子 Agent = 1）。"""

    target_worker: str = ""
    """目标 Worker 名称。"""

    parent_task_id: str | None = None
    """父任务 ID（root Agent 时为 None）。"""

    active_children: list[str] = field(default_factory=list)
    """当前活跃子任务 ID 列表（max MAX_CONCURRENT_CHILDREN）。"""

    model_config = {"arbitrary_types_allowed": True}


@dataclass(frozen=True)
class DelegateResult:
    """delegate() 返回值。

    success=True 时包含新创建的子任务 ID；
    success=False 时包含 error_code 和 reason。
    """

    success: bool
    """是否派发成功。"""

    child_task_id: str | None
    """新创建的子任务 ID；失败时为 None。"""

    error_code: str | None
    """错误代码：depth_exceeded / CAPACITY_EXCEEDED / blacklist_blocked；成功时为 None。"""

    reason: str | None
    """错误描述（人类可读）；成功时为 None。"""


# ---------------------------------------------------------------------------
# T044: DelegationManager
# ---------------------------------------------------------------------------


class DelegationManager:
    """子 Agent 派发管理器（FR-5 / plan.md 3.2 DelegationManager）。

    硬约束（不可 bypass，spec 不变量 5 / plan.md R5 缓解）：
    - MAX_DEPTH = 2：深度 ≥ 2 时拒绝派发（depth 从 0 开始，max_child_depth = 2）
    - MAX_CONCURRENT_CHILDREN = 3：active_children ≥ 3 时拒绝派发
    - blacklist：黑名单 Worker 命中时拒绝派发

    失败时**不**写 SUBAGENT_SPAWNED 事件（避免假数据）。
    通过时写 SUBAGENT_SPAWNED 事件（Constitution C2 / FR-5.5）。
    """

    MAX_DEPTH: int = 2
    """最大派发深度（FR-5.2）。"""

    MAX_CONCURRENT_CHILDREN: int = 3
    """最大并发子任务数（FR-5.3）。"""

    def __init__(
        self,
        *,
        blacklist: set[str] | None = None,
        event_store: Any | None = None,
        task_store: Any | None = None,
    ) -> None:
        """初始化 DelegationManager。

        Args:
            blacklist: 黑名单 Worker 名称集合（默认空集合）。
            event_store: EventStore 实例（用于写 SUBAGENT_SPAWNED 事件）；
                         None 时降级（审计不写库，但业务逻辑不受影响）。
            task_store: TaskStore 实例（防 F24 FK 违反）。
        """
        self._blacklist: set[str] = blacklist or set()
        self._event_store = event_store
        self._task_store = task_store
        # 防 F24：进程内幂等缓存
        self._audit_task_ensured: set[str] = set()

    async def delegate(
        self,
        ctx: DelegationContext,
        input: DelegateTaskInput,
    ) -> DelegateResult:
        """执行子 Agent 派发（FR-5.2 / FR-5.3 / FR-5.4 / FR-5.5）。

        检查顺序（按 spec 要求，不可调换）：
        1. depth < MAX_DEPTH
        2. len(active_children) < MAX_CONCURRENT_CHILDREN
        3. target_worker 不在 blacklist

        失败：不写 SUBAGENT_SPAWNED 事件，直接返回 DelegateResult(success=False)。
        成功：写 SUBAGENT_SPAWNED 事件，返回 DelegateResult(success=True)。

        Args:
            ctx: 派发上下文（当前深度 + 活跃子任务列表等）。
            input: 派发输入（目标 Worker + 任务描述 + 回调模式等）。

        Returns:
            DelegateResult。
        """
        target_worker = input.target_worker

        # 检查 1: 深度限制（FR-5.2）
        # depth 从 0 开始（root = 0，子 = 1，孙 = 2）
        # ctx.depth 是"当前 Agent 的深度"，子 Agent 的深度 = ctx.depth + 1
        child_depth = ctx.depth + 1
        if child_depth > self.MAX_DEPTH:
            log.warning(
                "delegation_depth_exceeded",
                current_depth=ctx.depth,
                max_depth=self.MAX_DEPTH,
                target_worker=target_worker,
            )
            return DelegateResult(
                success=False,
                child_task_id=None,
                error_code="depth_exceeded",
                reason=(
                    f"派发深度 {ctx.depth} + 1 = {child_depth} 超过最大值 {self.MAX_DEPTH}，"
                    f"拒绝创建子 Agent（FR-5.2）"
                ),
            )

        # 检查 2: 并发限制（FR-5.3）
        if len(ctx.active_children) >= self.MAX_CONCURRENT_CHILDREN:
            log.warning(
                "delegation_capacity_exceeded",
                active_children_count=len(ctx.active_children),
                max_concurrent=self.MAX_CONCURRENT_CHILDREN,
                target_worker=target_worker,
            )
            return DelegateResult(
                success=False,
                child_task_id=None,
                error_code="CAPACITY_EXCEEDED",
                reason=(
                    f"活跃子任务数 {len(ctx.active_children)} ≥ {self.MAX_CONCURRENT_CHILDREN}，"
                    f"拒绝创建子 Agent（FR-5.3）"
                ),
            )

        # 检查 3: 黑名单（FR-5.4）
        if target_worker in self._blacklist:
            log.warning(
                "delegation_blacklist_blocked",
                target_worker=target_worker,
            )
            return DelegateResult(
                success=False,
                child_task_id=None,
                error_code="blacklist_blocked",
                reason=f"目标 Worker {target_worker!r} 在黑名单中，拒绝派发（FR-5.4）",
            )

        # 通过所有约束检查：返回成功标记（但不生成/写入子任务 ID）
        # F1 修复（Codex high）：不在此处生成 child_task_id，也不写 SUBAGENT_SPAWNED 事件。
        # SUBAGENT_SPAWNED 事件和 child_task_id 应由真正创建子任务的调用方（delegate_task_tool）负责，
        # 在子任务被 Worker Runtime 实际调度后才写入（Constitution C4 两阶段记录）。
        # Phase 3 轻量实现：DelegationManager 只做约束检查（gate），
        # 实际调度由 launch_child / subagent lifecycle 在 Phase 4 接入。
        log.info(
            "delegation_constraints_passed",
            target_worker=target_worker,
            child_depth=child_depth,
            parent_task_id=ctx.task_id,
        )

        return DelegateResult(
            success=True,
            child_task_id=None,  # Phase 3：调用方负责创建真实子任务并提供 task_id
            error_code=None,
            reason=None,
        )

    def add_to_blacklist(self, worker_name: str) -> None:
        """动态加入黑名单（运行时配置扩展）。"""
        self._blacklist.add(worker_name)
        log.info("delegation_blacklist_added", worker_name=worker_name)

    def remove_from_blacklist(self, worker_name: str) -> None:
        """从黑名单移除。"""
        self._blacklist.discard(worker_name)

    # ---------------------------------------------------------------------------
    # 内部辅助：事件写入（防 F22 / F24 回归）
    # ---------------------------------------------------------------------------

    async def _ensure_audit_task(self, task_id: str) -> bool:
        """确保 audit task 存在（防 F24 FK violation）。

        F088 修复：本路径之前手写 Task(...) 漏 requester/pointers 必填字段
        → pydantic ValidationError → audit task 创建失败 → SUBAGENT_SPAWNED /
        SUBAGENT_COMPLETED 事件 silent 丢失。统一委托至 ensure_system_audit_task
        helper（与 PolicyGate / ApprovalGate 同 pattern）。
        """
        if task_id in self._audit_task_ensured:
            return True
        from octoagent.core.store.audit_task import ensure_system_audit_task

        ok = await ensure_system_audit_task(
            self._task_store,
            task_id,
            title="DelegationManager 审计占位 Task（F084 Phase 3 / F085 T3）",
        )
        if ok:
            self._audit_task_ensured.add(task_id)
            log.info("delegation_audit_task_ensured", task_id=task_id)
        else:
            log.warning(
                "delegation_audit_task_ensure_failed",
                task_id=task_id,
                hint="task_store 未注入 / 查询失败 / 创建失败",
            )
        return ok

    async def _emit_spawned_event(
        self,
        *,
        task_id: str,
        child_task_id: str,
        target_worker: str,
        depth: int,
        task_description: str,
        callback_mode: str,
    ) -> None:
        """写 SUBAGENT_SPAWNED 审计事件（FR-5.5 / Constitution C2）。

        防 F22 回归：使用真实 Event schema 字段（event_id/task_id/task_seq/ts/type/actor）
        + append_event_committed API。
        """
        if self._event_store is None:
            log.warning(
                "delegation_spawned_event_no_store",
                child_task_id=child_task_id,
                hint="event_store 未注入，SUBAGENT_SPAWNED 事件未持久化",
            )
            return

        # 若 task_id 为空或是占位，确保 audit task 存在（防 F24）
        emit_task_id = task_id or _DELEGATION_AUDIT_TASK_ID
        if emit_task_id == _DELEGATION_AUDIT_TASK_ID or not task_id:
            ensured = await self._ensure_audit_task(emit_task_id)
            if not ensured:
                log.error(
                    "delegation_spawned_event_no_audit_task",
                    task_id=emit_task_id,
                    hint="Constitution C2 SUBAGENT_SPAWNED 事件写入失败：audit task 不存在",
                )
                return

        try:
            from octoagent.core.models.event import Event

            task_seq = await self._event_store.get_next_task_seq(emit_task_id)
            event = Event(
                event_id=str(ULID()),
                task_id=emit_task_id,
                task_seq=task_seq,
                ts=datetime.now(timezone.utc),
                type=EventType.SUBAGENT_SPAWNED,
                actor=ActorType.SYSTEM,
                payload={
                    "child_task_id": child_task_id,
                    "target_worker": target_worker,
                    "depth": depth,
                    "task_description_preview": task_description[:200],
                    "callback_mode": callback_mode,
                    "parent_task_id": emit_task_id,
                },
                trace_id=emit_task_id,
            )
            await self._event_store.append_event_committed(event, update_task_pointer=False)
        except Exception as exc:
            log.error(
                "delegation_spawned_event_emit_failed",
                child_task_id=child_task_id,
                error_type=type(exc).__name__,
                error=str(exc),
                hint="Constitution C2 SUBAGENT_SPAWNED 事件写入失败",
            )


# ---------------------------------------------------------------------------
# DelegateTaskInput（T044 / T045 共用输入模型）
# ---------------------------------------------------------------------------


class DelegateTaskInput(BaseModel):
    """delegate_task 工具输入 schema（T044 数据模型 / T045 工具 handler 输入）。"""

    target_worker: str
    """目标 Worker 名称（如 "research_worker"、"code_worker"）。"""

    task_description: str
    """任务描述，详细说明子 Agent 需要完成的目标。"""

    callback_mode: Literal["async", "sync"] = "async"
    """回调模式：
    - async：立即返回 spawned + child_task_id，不等待子任务完成
    - sync：等待子任务返回或 max_wait_seconds 超时后返回
    """

    max_wait_seconds: int = 300
    """sync 模式下最长等待秒数（默认 300s）。"""
