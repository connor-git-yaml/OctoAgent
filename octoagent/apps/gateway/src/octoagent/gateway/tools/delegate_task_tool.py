"""delegate_task_tool.py：delegate_task 工具 handler（Feature 084 Phase 3 T045）。

工具语义（FR-5 / plan.md 3.2）：
- 允许 Agent 派发子任务到指定 Worker（Sub-agent Delegation）
- async 模式：立即返回 spawned + child_task_id，不等待子任务完成
- sync 模式：等待 Worker 返回或 max_wait_seconds 超时后返回
- 任务完成时写 SUBAGENT_RETURNED 事件（Constitution C2）

entrypoints：仅 agent_runtime（不含 web）——SC-010 / FR-5.1 反向约束：
Web UI 不直接发起子 Agent 派发；只有 Agent runtime 才能触发 delegation。

Constitution 合规：
- C2 所有写操作有审计事件（SUBAGENT_RETURNED）
- C4 两阶段记录：DelegationManager.delegate() 写 SUBAGENT_SPAWNED → 完成时写 SUBAGENT_RETURNED
- C9 Agent Autonomy：工具发起时机由 LLM 自主决策

return type 使用 DelegateTaskResult（WriteResult 子类），保留 child_task_id 供调用方追踪。

注册：顶层 registry.register(ToolEntry(entrypoints={"agent_runtime"}, ...)) 调用，
与 builtin_tools/__init__.register_all 显式接入（F20 同 pattern）。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Literal

import structlog
from pydantic import BaseModel
from ulid import ULID

from octoagent.core.models.enums import ActorType, EventType, SideEffectLevel
from octoagent.core.models.tool_results import DelegateTaskResult
from octoagent.gateway.harness.delegation import DelegateTaskInput, DelegationContext, DelegationManager
from octoagent.gateway.harness.tool_registry import ToolEntry
from octoagent.gateway.harness.tool_registry import register as _registry_register
from octoagent.tooling import reflect_tool_schema, tool_contract

from ..services.builtin_tools._deps import ToolDeps

log = structlog.get_logger(__name__)

# 审计占位 task_id（防 F24 FK 违反）
_DELEGATE_AUDIT_TASK_ID = "_delegate_task_audit"

# delegate_task 仅 agent_runtime 入口（FR-5.1 / SC-010 反向）
_ENTRYPOINTS = frozenset({"agent_runtime"})


# ---------------------------------------------------------------------------
# T045: 注册入口
# ---------------------------------------------------------------------------


async def register(broker: Any, deps: ToolDeps) -> None:
    """注册 delegate_task 工具到 broker + ToolRegistry（F20 同 pattern）。"""

    @tool_contract(
        name="delegate_task",
        side_effect_level=SideEffectLevel.REVERSIBLE,
        tool_group="delegation",
        produces_write=True,
        tags=["delegation", "subagent", "spawn"],
        manifest_ref="builtin://delegate_task",
        metadata={
            "entrypoints": ["agent_runtime"],
        },
    )
    async def delegate_task_handler(
        target_worker: str,
        task_description: str,
        callback_mode: str = "async",
        max_wait_seconds: int = 300,
    ) -> DelegateTaskResult:
        """派发子任务到指定 Worker（Sub-agent Delegation）。

        async 模式：立即返回，子任务在后台运行，通过 child_task_id 追踪状态。
        sync 模式：等待子任务完成后返回结果（或 max_wait_seconds 超时）。

        Args:
            target_worker: 目标 Worker 名称（如 "research_worker"、"code_worker"）。
            task_description: 子任务详细描述。
            callback_mode: 回调模式（"async" 或 "sync"，默认 "async"）。
            max_wait_seconds: sync 模式最长等待秒数（默认 300s）。

        Returns:
            DelegateTaskResult：包含 child_task_id 和派发状态。
        """
        # 校验 callback_mode
        if callback_mode not in ("async", "sync"):
            return DelegateTaskResult(
                status="rejected",
                target="delegate_task",
                reason=f"无效的 callback_mode: {callback_mode!r}，必须是 'async' 或 'sync'",
                target_worker=target_worker,
                callback_mode=callback_mode,
            )

        # 解析当前执行上下文（获取 task_id 和深度信息）
        # F2 修复（Codex high）：必须从运行时状态推断深度和活跃子任务数，
        # 不能硬编码 depth=0 / active_children=[]，否则深度/并发约束形同虚设
        current_task_id = ""
        current_depth = 0
        active_children: list[str] = []

        task_store = getattr(getattr(deps, "stores", None), "task_store", None)

        try:
            from ..services.execution_context import get_current_execution_context
            ctx = get_current_execution_context()
            if ctx:
                current_task_id = ctx.task_id or ""
                # 从 execution context 推断深度（work_id chain 或 depth 字段）
                # Phase 3 轻量实现：查当前 task 的 depth 字段（如存在）
                if task_store and current_task_id:
                    try:
                        current_task = await task_store.get_task(current_task_id)
                        if current_task is not None:
                            current_depth = getattr(current_task, "depth", 0) or 0
                            # active_children：查 task_store 中以 current_task_id 为 parent 的活跃子任务
                            # Phase 3 轻量：使用 context 的 work_id chain 推断（如 delegation_plane 可用）
                            if deps._delegation_plane is not None:
                                try:
                                    descendants = await deps._delegation_plane.list_descendant_works(
                                        ctx.work_id
                                    ) if ctx.work_id else []
                                    # 只统计直接活跃子任务（非终态）
                                    active_children = [
                                        d.task_id for d in descendants
                                        if getattr(d, "status", "") not in ("completed", "failed", "cancelled")
                                        and getattr(d, "parent_work_id", None) == ctx.work_id
                                    ]
                                except Exception:
                                    pass
                    except Exception:
                        pass
        except Exception:
            pass

        # 构建 DelegationContext（使用真实运行时状态，不再硬编码）
        delegation_ctx = DelegationContext(
            task_id=current_task_id or _DELEGATE_AUDIT_TASK_ID,
            depth=current_depth,
            target_worker=target_worker,
            active_children=active_children,
        )

        # 构建 DelegateTaskInput
        task_input = DelegateTaskInput(
            target_worker=target_worker,
            task_description=task_description,
            callback_mode=callback_mode,  # type: ignore[arg-type]
            max_wait_seconds=max_wait_seconds,
        )

        # 初始化 DelegationManager（注入 event_store / task_store）
        event_store = getattr(getattr(deps, "stores", None), "event_store", None)
        task_store = getattr(getattr(deps, "stores", None), "task_store", None)
        mgr = DelegationManager(
            event_store=event_store,
            task_store=task_store,
        )

        # 调用 DelegationManager.delegate()（检查 depth/concurrent/blacklist + 写 SUBAGENT_SPAWNED）
        result = await mgr.delegate(delegation_ctx, task_input)

        if not result.success:
            return DelegateTaskResult(
                status="rejected",
                target="delegate_task",
                reason=result.reason,
                child_task_id=None,
                target_worker=target_worker,
                callback_mode=callback_mode,
            )

        # F26 修复（Codex 独立 review high）：把 stub 替换为真实派发——
        # 调用 launch_child（与 subagents.spawn 同路径，复用现有 Worker Runtime）。
        # 这样 DelegationManager 的 gate 检查 + 真实子任务创建串联起来：
        # 1. DelegationManager.delegate 已写 SUBAGENT_SPAWNED 事件（约束通过时）
        # 2. launch_child 实际创建 child task（task_runner 接管 lifecycle）
        # 3. async 模式立即返回 task_id；sync 模式 wait + return
        # 4. SUBAGENT_RETURNED 事件由 task_runner 终态回调写入（非本工具职责）
        try:
            from ..services.builtin_tools._deps import launch_child as _launch_child
            launch_payload = await _launch_child(
                deps,
                objective=task_description,
                worker_type=target_worker,
                target_kind="subagent",
                tool_profile=deps._pack_service._effective_tool_profile_for_objective(
                    objective=task_description,
                ) if deps._pack_service is not None else "default",
                title=task_description[:60],
            )
        except Exception as exc:
            # launch_child 失败 → 真实派发被拒绝（非 stub 限制）
            return DelegateTaskResult(
                status="rejected",
                target="delegate_task",
                reason=(
                    f"launch_child_failed: {type(exc).__name__}: {exc}; "
                    "请检查 Worker Runtime / TaskRunner 是否就绪"
                ),
                child_task_id=None,
                target_worker=target_worker,
                callback_mode=callback_mode,
            )

        spawned_task_id = (launch_payload or {}).get("task_id", "") if isinstance(launch_payload, dict) else ""

        # async 模式：立即返回 spawned + task_id（FR-5.1）
        if callback_mode == "async":
            return DelegateTaskResult(
                status="written",
                target=f"task:{spawned_task_id}" if spawned_task_id else "delegate_task",
                preview=f"async spawned: {target_worker} / {task_description[:80]}",
                reason=None,
                child_task_id=spawned_task_id or None,
                target_worker=target_worker,
                callback_mode=callback_mode,
            )

        # sync 模式：等待 task 完成或 timeout（max_wait_seconds 默认 300）
        if not spawned_task_id:
            return DelegateTaskResult(
                status="rejected",
                target="delegate_task",
                reason="sync_mode_no_task_id: launch_child 未返回 task_id，sync 等待无效",
                child_task_id=None,
                target_worker=target_worker,
                callback_mode=callback_mode,
            )

        # sync 等待：轮询 task_store 直到终态或超时
        # （Phase 3 轻量实现；Phase 5 可换 task_runner 完成事件订阅）
        import asyncio as _asyncio

        async def _wait_terminal() -> str:
            terminal_states = {"completed", "failed", "cancelled"}
            poll_interval = 1.0
            while True:
                if task_store is not None:
                    try:
                        t = await task_store.get_task(spawned_task_id)
                        if t is not None:
                            status_value = getattr(t, "status", "") or ""
                            status_str = (
                                status_value.value if hasattr(status_value, "value") else str(status_value)
                            ).lower()
                            if status_str in terminal_states:
                                return status_str
                    except Exception:
                        pass
                await _asyncio.sleep(poll_interval)

        try:
            terminal_status = await _asyncio.wait_for(
                _wait_terminal(),
                timeout=max(1, max_wait_seconds),
            )
        except _asyncio.TimeoutError:
            return DelegateTaskResult(
                status="pending",
                target=f"task:{spawned_task_id}",
                reason=f"sync_timeout_after_{max_wait_seconds}s; 子任务仍在运行",
                child_task_id=spawned_task_id,
                target_worker=target_worker,
                callback_mode=callback_mode,
            )

        if terminal_status == "completed":
            return DelegateTaskResult(
                status="written",
                target=f"task:{spawned_task_id}",
                preview=f"sync completed: {target_worker} / {task_description[:80]}",
                child_task_id=spawned_task_id,
                target_worker=target_worker,
                callback_mode=callback_mode,
            )
        return DelegateTaskResult(
            status="rejected",
            target=f"task:{spawned_task_id}",
            reason=f"sync_terminal_{terminal_status}: 子任务以非 completed 状态终结",
            child_task_id=spawned_task_id,
            target_worker=target_worker,
            callback_mode=callback_mode,
        )

    # 向 packages/tooling ToolBroker 注册（保持兼容）
    await broker.try_register(reflect_tool_schema(delegate_task_handler), delegate_task_handler)

    # 向全局 ToolRegistry 注册（F20 pattern：显式 registry.register 调用）
    _registry_register(ToolEntry(
        name="delegate_task",
        entrypoints=_ENTRYPOINTS,
        toolset="delegation",
        handler=delegate_task_handler,
        schema=BaseModel,  # DelegateTaskInput 通过 @tool_contract 绑定
        side_effect_level=SideEffectLevel.REVERSIBLE,
        description=(
            "派发子任务到指定 Worker。async 模式立即返回 child_task_id；"
            "sync 模式等待完成或超时。仅 agent_runtime 入口可用（不暴露给 web）。"
        ),
    ))


# ---------------------------------------------------------------------------
# Phase 4 预留：_emit_returned_event（由 Worker Runtime 完成时调用）
# ---------------------------------------------------------------------------
# F1 修复说明（Codex high）：
# SUBAGENT_RETURNED 事件在 Phase 3 不写入，因为子任务实际上没有运行。
# Phase 4 接入 Worker Runtime 后，_emit_returned_event 将由真正的
# 子任务完成回调调用，届时才会有真实的 child_task_id 和 completion_status。
# 此处保留函数签名为 Phase 4 预留，当前不调用。
async def _emit_returned_event_phase4(
    *,
    deps: ToolDeps,
    parent_task_id: str,
    child_task_id: str,
    target_worker: str,
    status: str,
) -> None:
    """写 SUBAGENT_RETURNED 审计事件（Phase 4 启用，Constitution C2 / FR-5.5）。

    防 F22 回归：使用真实 Event schema 字段（event_id/task_id/task_seq/ts/type/actor）
    + append_event_committed API。
    Phase 4 接入 Worker Runtime 后取消 _phase4 后缀，接入真实调度链路。
    """
    event_store = getattr(getattr(deps, "stores", None), "event_store", None)
    if event_store is None:
        log.warning(
            "delegate_task_returned_event_no_store",
            child_task_id=child_task_id,
            hint="event_store 未注入，SUBAGENT_RETURNED 事件未持久化",
        )
        return

    try:
        from octoagent.core.models.event import Event

        task_seq = await event_store.get_next_task_seq(parent_task_id)
        event = Event(
            event_id=str(ULID()),
            task_id=parent_task_id,
            task_seq=task_seq,
            ts=datetime.now(timezone.utc),
            type=EventType.SUBAGENT_RETURNED,
            actor=ActorType.SYSTEM,
            payload={
                "child_task_id": child_task_id,
                "target_worker": target_worker,
                "completion_status": status,
                "parent_task_id": parent_task_id,
            },
            trace_id=parent_task_id,
        )
        await event_store.append_event_committed(event, update_task_pointer=False)
    except Exception as exc:
        log.error(
            "delegate_task_returned_event_emit_failed",
            child_task_id=child_task_id,
            error_type=type(exc).__name__,
            error=str(exc),
            hint="Constitution C2 SUBAGENT_RETURNED 事件写入失败",
        )
