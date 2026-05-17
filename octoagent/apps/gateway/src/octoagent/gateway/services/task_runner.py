"""TaskRunner -- 后台任务调度与恢复

将 LLM 处理任务持久化到 task_jobs 表，支持：
1) 启动时恢复 queued/running 任务
2) 超时监控
3) 避免路由层直接 fire-and-forget
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import structlog
from octoagent.core.models import (
    TERMINAL_STATES,
    ActorType,
    AgentSessionStatus,
    DispatchEnvelope,
    Event,
    EventCausality,
    EventType,
    ExecutionConsoleSession,
    ExecutionSessionState,
    NormalizedMessage,
    ResumeFailureType,
    ResumeResult,
    SubagentCompletedPayload,
    SubagentDelegation,
    TaskStatus,
)
from octoagent.core.store import StoreGroup
from ulid import ULID

from .execution_console import (
    AttachInputResult,
    ExecutionConsoleService,
    ExecutionInputError,
)
from .orchestrator import OrchestratorService
from .resume_engine import ResumeEngine
from .task_service import TaskService
from .worker_runtime import WorkerCancellationRegistry, WorkerRuntimeConfig

log = structlog.get_logger()

_DEFERRED_TASK_STATUSES: dict[TaskStatus, str] = {
    TaskStatus.WAITING_INPUT: "WAITING_INPUT",
    TaskStatus.WAITING_APPROVAL: "WAITING_APPROVAL",
    TaskStatus.PAUSED: "PAUSED",
}
_DEFERRED_JOB_STATUSES = set(_DEFERRED_TASK_STATUSES.values())
_TERMINAL_JOB_STATUSES = {
    TaskStatus.SUCCEEDED.value,
    TaskStatus.FAILED.value,
    TaskStatus.REJECTED.value,
    TaskStatus.CANCELLED.value,
}


@dataclass
class RunningJob:
    task: asyncio.Task[None]
    started_at: datetime


class TaskRunner:
    """后台任务执行器（带持久化恢复）"""

    def __init__(
        self,
        store_group: StoreGroup,
        sse_hub,
        llm_service,
        approval_manager=None,
        timeout_seconds: float = 14400.0,  # 4 小时，需大于 Worker max_execution
        monitor_interval_seconds: float = 5.0,
        completion_notifier: Callable[[str], Awaitable[None]] | None = None,
        worker_runtime_config: WorkerRuntimeConfig | None = None,
        docker_available_checker=None,
        delegation_plane=None,
        project_root: Path | None = None,
        approval_timeout_seconds: float = 300.0,  # F101 Phase B FR-C3b：审批超时（默认 300s）
    ) -> None:
        self._stores = store_group
        self._sse_hub = sse_hub
        self._llm_service = llm_service
        self._timeout_seconds = timeout_seconds
        self._monitor_interval_seconds = monitor_interval_seconds
        self._approval_timeout_seconds = approval_timeout_seconds  # FR-C3b
        self._completion_notifier = completion_notifier
        self._running_jobs: dict[str, RunningJob] = {}
        self._monitor_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._cancellation_registry = WorkerCancellationRegistry()
        # F101 Phase B HIGH-04 v4：保存 approval_manager 引用，供 startup_recovery 调用
        # expire_dead_approval，让过期审批在用户再次 approve 时返回 409/410 而非假成功。
        self._approval_manager = approval_manager
        self._execution_console = ExecutionConsoleService(
            store_group=store_group,
            sse_hub=sse_hub,
            approval_manager=approval_manager,
        )
        self._orchestrator = OrchestratorService(
            store_group=store_group,
            sse_hub=sse_hub,
            llm_service=llm_service,
            approval_manager=approval_manager,
            delegation_plane=delegation_plane,
            worker_runtime_config=worker_runtime_config,
            docker_available_checker=docker_available_checker,
            cancellation_registry=self._cancellation_registry,
            execution_console=self._execution_console,
            project_root=project_root,
        )
        if delegation_plane is not None:
            delegation_plane.bind_dispatch_scheduler(self.schedule_dispatch_envelope)
        self._resume_engine = ResumeEngine(store_group)

    @property
    def execution_console(self) -> ExecutionConsoleService:
        return self._execution_console

    async def startup(self) -> None:
        """启动恢复：清理 orphan running + 处理 WAITING_APPROVAL + 拉起 queued。

        F098 Phase H: 注册 subagent session cleanup 为 task 终态 callback。
        所有终态路径（mark_failed / mark_cancelled / dispatch exception / shutdown 等）
        通过 task_service._write_state_transition 自动触发，不再依赖 task_runner 手动调用。

        F101 Phase B HIGH-04：启动时同时扫描 WAITING_APPROVAL job，按 timeout policy 推 FAILED。
        （原仅扫 RUNNING job，WAITING_APPROVAL job 重启后 ApprovalGate._pending_handles 全丢，
        任务永远 hang——违反 FR-C6 对称性承诺 + Constitution 1 Durability First）
        """
        # F098 Phase H: 注册 cleanup callback 到 TaskService class-level callback list
        # 幂等：重复 startup 多次注册仅生效一次（按 callback identity 检测）。
        await TaskService.register_terminal_state_callback(
            self._close_subagent_session_if_needed,
        )

        await self._recover_orphan_running_jobs()
        await self._recover_orphan_waiting_approval_jobs()  # HIGH-04: 新增
        await self._dispatch_queued_jobs()
        if self._monitor_task is None or self._monitor_task.done():
            self._monitor_task = asyncio.create_task(self._monitor_loop())

    async def shutdown(self) -> None:
        """停止监控并取消在途任务。

        F098 Phase H: 注销 cleanup callback 防泄漏。
        Final Codex review P2 修复：注销 callback 挪到 shutdown 末尾——
        否则 mark_running_task_failed_for_recovery 触发的终态 transition 不会
        触发 cleanup callback（callback 已被注销），运行中的 subagent session
        在进程停止时会保持 ACTIVE。
        """

        if self._monitor_task is not None:
            self._monitor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._monitor_task
            self._monitor_task = None

        async with self._lock:
            running = list(self._running_jobs.items())
            self._running_jobs.clear()

        task_service = TaskService(self._stores, self._sse_hub)
        for task_id, running_job in running:
            self._cancellation_registry.cancel(task_id)
            running_job.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await running_job.task
            task = await self._stores.task_store.get_task(task_id)
            deferred_job_status = (
                _DEFERRED_TASK_STATUSES.get(task.status) if task is not None else None
            )
            if deferred_job_status is not None:
                await self._stores.task_job_store.mark_deferred(task_id, deferred_job_status)
            else:
                await self._stores.task_job_store.mark_failed(
                    task_id,
                    "runner_shutdown_cancelled",
                )
                await task_service.mark_running_task_failed_for_recovery(
                    task_id,
                    reason="实例重启或停止时取消了当前执行，请重新发起这条请求。",
                )
                await self._mark_execution_terminal(
                    task_id=task_id,
                    status=ExecutionSessionState.FAILED,
                    message="runner shutdown cancelled execution",
                )
            self._cancellation_registry.clear(task_id)

        # F098 Phase H + Final Codex P2 修复：所有终态迁移完成后才注销 callback
        # 确保 shutdown 路径下的 mark_running_task_failed_for_recovery 仍能触发
        # cleanup callback，subagent session 不会保持 ACTIVE。
        await TaskService.unregister_terminal_state_callback(
            self._close_subagent_session_if_needed,
        )

    async def enqueue(self, task_id: str, user_text: str, model_alias: str | None = None) -> None:
        """入队并尝试启动执行"""
        created = await self._stores.task_job_store.create_job(
            task_id=task_id,
            user_text=user_text,
            model_alias=model_alias,
        )
        if not created:
            return
        await self._start_job(task_id)

    async def launch_child_task(
        self,
        message: NormalizedMessage,
        *,
        model_alias: str | None = None,
    ) -> tuple[str, bool]:
        """创建并启动 child task。"""
        service = TaskService(self._stores, self._sse_hub)
        task_id, created = await service.create_task(message)
        # F097 Phase B-1（Codex P1-2 闭环）：在 create_task 后、enqueue 前
        # emit SubagentDelegation USER_MESSAGE event。消除 race —— child runtime
        # 启动前已有完整 delegation 可读，_ensure_agent_session 能拿到 SubagentDelegation。
        # P1-1 闭环：USER_MESSAGE event 同时保留 target_kind="subagent" 让
        # merge_control_metadata 取最新 USER_MESSAGE 时仍能读到 turn-scoped 信号，
        # _ensure_agent_session 走第 4 路 SUBAGENT_INTERNAL。
        if created:
            await self._emit_subagent_delegation_init_if_needed(task_id, message)
        if created:
            await self.enqueue(task_id, message.text, model_alias=model_alias)
        return task_id, created

    async def _emit_subagent_delegation_init_if_needed(
        self, task_id: str, message: NormalizedMessage
    ) -> None:
        """F097 Phase B-1: 若 message 含 __subagent_delegation_init__ raw fields，
        emit SubagentDelegation USER_MESSAGE event（保留 target_kind 等 turn-scoped 信号）。
        异常隔离：失败 log warn 不阻断 spawn 主流程。
        """
        raw = message.control_metadata.get("__subagent_delegation_init__")
        if not raw or not isinstance(raw, dict):
            return
        target_kind = str(message.control_metadata.get("target_kind", "")).strip().lower()
        if target_kind != "subagent":
            return
        try:
            # P2-6 闭环：caller_agent_runtime_id 无值时用 "<unknown>" 而不是 task_id 伪造
            # （SubagentDelegation 字段 min_length=1 不允许空字符串）
            caller_runtime = raw.get("caller_agent_runtime_id", "") or "<unknown>"
            caller_proj = raw.get("caller_project_id", "") or "<unknown>"
            # TF.1: 查询 caller 的 AGENT_PRIVATE namespace IDs，填入 caller_memory_namespace_ids。
            # α 语义（OD-1 锁定）：subagent 直接复用 caller 的 AGENT_PRIVATE namespace ID，
            # 不创建新的 namespace row。仅 caller_agent_runtime_id 有效时查询，
            # 失败时 caller_memory_namespace_ids = []，log warn 不阻断。
            caller_memory_namespace_ids: list[str] = []
            if caller_runtime != "<unknown>":
                try:
                    from octoagent.core.models import MemoryNamespaceKind

                    caller_namespaces = (
                        await self._stores.agent_context_store.list_memory_namespaces(
                            agent_runtime_id=caller_runtime,
                            kind=MemoryNamespaceKind.AGENT_PRIVATE,
                        )
                    )
                    caller_memory_namespace_ids = [
                        ns.namespace_id for ns in caller_namespaces
                    ]
                    if caller_memory_namespace_ids:
                        log.debug(
                            "subagent_delegation_caller_namespaces_found",
                            caller_agent_runtime_id=caller_runtime,
                            namespace_ids=caller_memory_namespace_ids,
                        )
                    else:
                        log.warning(
                            "subagent_delegation_caller_namespaces_empty",
                            caller_agent_runtime_id=caller_runtime,
                        )
                except Exception as ns_exc:
                    log.warning(
                        "subagent_delegation_caller_namespaces_lookup_failed",
                        caller_agent_runtime_id=caller_runtime,
                        error=str(ns_exc),
                    )
            delegation = SubagentDelegation(
                delegation_id=raw["delegation_id"],
                parent_task_id=raw["parent_task_id"],
                parent_work_id=raw["parent_work_id"],
                child_task_id=task_id,
                child_agent_session_id=None,
                caller_agent_runtime_id=caller_runtime,
                caller_project_id=caller_proj,
                caller_memory_namespace_ids=caller_memory_namespace_ids,
                spawned_by=raw["spawned_by"],
                created_at=datetime.now(tz=UTC),
            )
            next_seq = await self._stores.event_store.get_next_task_seq(task_id)
            # F098 Phase E: 改用 ControlMetadataUpdatedPayload + EventType.CONTROL_METADATA_UPDATED
            # 替代 USER_MESSAGE 复用承载（F097 P1-1 known issue 修复）。
            # 历史 baseline：用 USER_MESSAGE marker text "[subagent delegation metadata]"
            # 污染 ContextCompactionService._load_conversation_turns 的对话历史读取。
            # F098 修复：CONTROL_METADATA_UPDATED 仅含 control_metadata + source 字段，
            # 不含 text，consumer（context_compaction / chat / telegram 等）天然不受影响。
            from octoagent.core.models.payloads import (
                ControlMetadataUpdatedPayload as _ControlMetadataUpdatedPayload,
            )
            from .connection_metadata import normalize_control_metadata as _normalize

            # F097 Phase D Round 2 修复保留：merge_control_metadata 取最新 USER_MESSAGE +
            # CONTROL_METADATA_UPDATED 的 TURN_SCOPED 字段（如 requested_worker_type /
            # tool_profile）。本 emit 是任务最新 control 事件，必须包含原 message 的
            # normalize 后的所有 control_metadata 字段（除 raw key 外），否则下游 dispatch
            # 会丢失 worker_type/tool_profile 等。
            preserved_control = _normalize(message.control_metadata)
            preserved_control["subagent_delegation"] = delegation.model_dump(mode="json")
            # P1-1 闭环：target_kind / spawned_by 在 normalize 后已保留，无需重复写

            delegation_event = Event(
                event_id=str(ULID()),
                task_id=task_id,
                task_seq=next_seq,
                ts=datetime.now(tz=UTC),
                type=EventType.CONTROL_METADATA_UPDATED,
                actor=ActorType.SYSTEM,
                payload=_ControlMetadataUpdatedPayload(
                    control_metadata=preserved_control,
                    source="subagent_delegation_init",
                ).model_dump(),
                trace_id=f"trace-{task_id}",
                causality=EventCausality(
                    idempotency_key=f"subagent_delegation_init:{delegation.delegation_id}"
                ),
            )
            await self._stores.event_store.append_event_committed(
                delegation_event, update_task_pointer=False
            )
        except Exception as exc:
            log.warning(
                "subagent_delegation_init_failed",
                task_id=task_id,
                error=str(exc),
            )

    async def resume_task(self, task_id: str, trigger: str = "manual") -> ResumeResult:
        """手动触发恢复并在成功时启动执行。"""
        job = await self._stores.task_job_store.get_job(task_id)
        if job is None:
            return ResumeResult(
                ok=False,
                task_id=task_id,
                failure_type=ResumeFailureType.DEPENDENCY_MISSING,
                message="task_jobs 中不存在可恢复任务记录",
            )

        resume_result = await self._resume_engine.try_resume(task_id, trigger=trigger)
        if not resume_result.ok:
            return resume_result

        if job.status == "QUEUED":
            marked = await self._stores.task_job_store.mark_running(task_id)
            if not marked:
                return ResumeResult(
                    ok=False,
                    task_id=task_id,
                    failure_type=ResumeFailureType.LEASE_CONFLICT,
                    message="任务未能切换到 RUNNING，可能被其他执行器接管",
                )

        self._cancellation_registry.ensure(task_id)
        await self._spawn_job(
            task_id=task_id,
            user_text=job.user_text,
            model_alias=job.model_alias,
            resume_from_node=resume_result.resumed_from_node,
            resume_state_snapshot=resume_result.state_snapshot,
        )
        return resume_result

    async def _dispatch_queued_jobs(self) -> None:
        jobs = await self._stores.task_job_store.list_jobs(["QUEUED"])
        if jobs:
            await asyncio.gather(*[self._start_job(j.task_id) for j in jobs])

    async def _recover_orphan_running_jobs(self) -> None:
        jobs = await self._stores.task_job_store.list_jobs(["RUNNING"])
        if not jobs:
            return

        service = TaskService(self._stores, self._sse_hub)
        await asyncio.gather(*[self._recover_one_orphan_job(j, service) for j in jobs])

    async def _get_approval_requested_created_at(self, task_id: str) -> datetime | None:
        """F101 Phase B HIGH-04 v3：从 event_store 读取 APPROVAL_REQUESTED 事件的 created_at。

        用于 startup_recovery 精确计算 elapsed_since_approval_request。
        若 event_store 不支持 get_events_for_task 或无相关事件，返回 None（fallback 到 task.updated_at）。
        """
        try:
            from octoagent.core.models.enums import EventType as _EventType
            _getter = getattr(self._stores.event_store, "get_events_for_task", None)
            if not callable(_getter):
                return None
            events = await _getter(task_id)
            # 取最新的 APPROVAL_REQUESTED 事件（task_seq 最大）
            approval_events = [
                e for e in events
                if getattr(e, "type", None) == _EventType.APPROVAL_REQUESTED
            ]
            if not approval_events:
                return None
            latest = max(approval_events, key=lambda e: getattr(e, "task_seq", 0))
            ts = getattr(latest, "ts", None)
            if ts is None:
                return None
            from datetime import timezone as _tz
            return ts.replace(tzinfo=_tz.utc) if ts.tzinfo is None else ts
        except Exception:
            return None

    async def _recover_orphan_waiting_approval_jobs(self) -> None:
        """F101 Phase B HIGH-04 v3：启动时处理 WAITING_APPROVAL job，按剩余 timeout 判断策略。

        gateway 重启时 ApprovalGate._pending_handles（in-memory）全丢，
        WAITING_APPROVAL job 无法被唤醒。

        v3 修复（HIGH-04 PARTIAL → CLOSED）：
        1. 从 event_store 读取 APPROVAL_REQUESTED 事件 created_at，计算 elapsed
        2. 若 elapsed < approval_timeout → 重启 monitor（让 monitor 在剩余时间后推 FAILED）
           原理：把 task 状态更新为 WAITING_APPROVAL（mark_waiting_approval），然后
           把 task 重新加入 _running_jobs 字典，让 monitor 按剩余时间检查。
           实现细节：用 _fake_running_job 占位（started_at 设为 approval_requested_at），
           monitor _monitor_loop_step 的 approval_timeout 检查会在剩余时间后推 FAILED。
        3. 若 elapsed >= approval_timeout → 推 FAILED + reason "timeout_after_<sec>s"（与 monitor 路径统一）
        4. 无法确定 elapsed（event_store 无 APPROVAL_REQUESTED 事件）→
           推 FAILED + reason "gateway_restart_approval_lost"（fallback）
        """
        jobs = await self._stores.task_job_store.list_jobs(["WAITING_APPROVAL"])
        if not jobs:
            return

        service = TaskService(self._stores, self._sse_hub)
        _now = datetime.now(UTC)

        for job in jobs:
            try:
                task = await service.get_task(job.task_id)
                if task is None:
                    log.warning(
                        "recover_waiting_approval_task_not_found",
                        task_id=job.task_id,
                    )
                    await self._stores.task_job_store.mark_failed(
                        job.task_id,
                        "task_missing_for_approval_recovery",
                    )
                    continue

                # 从 event_store 读取审批发起时间（APPROVAL_REQUESTED 事件 created_at）
                approval_requested_at = await self._get_approval_requested_created_at(job.task_id)

                # fallback：若无 APPROVAL_REQUESTED 事件，用 task.updated_at
                if approval_requested_at is None:
                    from datetime import timezone as _tz
                    approval_requested_at_candidate = (
                        task.updated_at.replace(tzinfo=_tz.utc)
                        if task.updated_at.tzinfo is None
                        else task.updated_at
                    )
                    # 保守估计：没有精确时间戳，无法安全重启 monitor，推 FAILED
                    _elapsed = (_now - approval_requested_at_candidate).total_seconds()
                    if _elapsed >= self._approval_timeout_seconds:
                        _reason = f"timeout_after_{int(self._approval_timeout_seconds)}s"
                    else:
                        # 无法确定精确 elapsed（event_store 无 APPROVAL_REQUESTED 事件）
                        _reason = "gateway_restart_approval_lost"
                    log.warning(
                        "recover_waiting_approval_no_event_fallback",
                        task_id=job.task_id,
                        reason=_reason,
                        elapsed_approx=_elapsed,
                    )
                    await self._recover_waiting_approval_push_failed(service, job.task_id, _reason)
                    continue

                # 计算精确 elapsed
                _elapsed = (_now - approval_requested_at).total_seconds()

                if _elapsed < self._approval_timeout_seconds:
                    # 未超时：重启 monitor 等待剩余时间
                    # 把任务重新加入 _running_jobs，用 approval_requested_at 作为"started_at"
                    # monitor 的 approval_timeout 检查：
                    #   task.updated_at 从 APPROVAL_REQUESTED 时间算，
                    #   threshold = now - approval_timeout_seconds
                    #   若 task.updated_at < threshold → 超时推 FAILED
                    # 通过 mark_waiting_approval 确保 task_jobs 表状态一致
                    await self._stores.task_job_store.mark_waiting_approval(job.task_id)

                    # 注册到 _running_jobs，让 monitor 继续跟踪
                    import asyncio as _asyncio
                    _placeholder_task = _asyncio.create_task(_asyncio.sleep(999_999))
                    self._running_jobs[job.task_id] = RunningJob(
                        task=_placeholder_task,
                        started_at=approval_requested_at,
                    )
                    _remaining = self._approval_timeout_seconds - _elapsed
                    log.info(
                        "recover_waiting_approval_restart_monitor",
                        task_id=job.task_id,
                        elapsed_s=round(_elapsed, 1),
                        remaining_s=round(_remaining, 1),
                        approval_timeout_s=self._approval_timeout_seconds,
                        hint="monitor 将在剩余时间后推 FAILED（HIGH-04 v3 重启 monitor）",
                    )
                    # HIGH-04 v4：即使 task 尚未超时，ApprovalGate handle 已丢失（重启后无法恢复）。
                    # 必须立即 expire ApprovalManager entry，防止用户在剩余时间内 approve 收到假成功。
                    # monitor 仍会在剩余时间后推 FAILED，此处只确保 ApprovalManager 侧正确反映 dead 状态。
                    await self._expire_approval_manager_entry(job.task_id)
                else:
                    # 已超时：推 FAILED + reason "timeout_after_<sec>s"（与 monitor 路径统一）
                    _reason = f"timeout_after_{int(self._approval_timeout_seconds)}s"
                    log.warning(
                        "recover_waiting_approval_timeout_expired",
                        task_id=job.task_id,
                        elapsed_s=round(_elapsed, 1),
                        approval_timeout_s=self._approval_timeout_seconds,
                        reason=_reason,
                    )
                    await self._recover_waiting_approval_push_failed(service, job.task_id, _reason)

            except Exception as _exc:
                log.warning(
                    "recover_waiting_approval_error",
                    task_id=job.task_id,
                    error=str(_exc),
                )

    async def _expire_approval_manager_entry(self, task_id: str) -> None:
        """F101 Phase B HIGH-04 v4：通知 ApprovalManager 将对应审批标记为 EXPIRED。

        gateway 重启后 ApprovalGate._pending_handles 全丢，但 ApprovalManager 可能已
        从 event store 恢复了 PENDING 条目。若不显式 expire，用户在 300-600s 窗口内
        approve 时 approval_manager.resolve() 会返回 True（假成功）但 task 无法恢复执行。

        此方法在 startup_recovery 中对每个已超时或 fallback 的 WAITING_APPROVAL job 调用，
        确保后续 resolve 尝试收到 409（或 410 EXPIRED）而非假成功。

        task_id 用于匹配 approval_id（escalate_permission 以 task_id 为 approval_id 注册）。
        """
        if self._approval_manager is None:
            return
        try:
            # escalate_permission 以 handle.handle_id 注册（handle_id 由 ApprovalGate 生成），
            # 但 ApprovalManager 通过 task_id 关联——approval_id 可能不等于 task_id。
            # 最精确的方法：遍历 pending approvals，找 task_id 匹配的条目。
            pending_list = self._approval_manager.get_pending_approvals()
            for _rec in pending_list:
                if _rec.request.task_id == task_id:
                    expired = await self._approval_manager.expire_dead_approval(_rec.request.approval_id)
                    if expired:
                        log.info(
                            "recover_waiting_approval_expired_approval_manager",
                            task_id=task_id,
                            approval_id=_rec.request.approval_id,
                            hint="HIGH-04 v4：startup_recovery 显式 expire dead approval",
                        )
        except Exception as _exc:
            log.warning(
                "recover_waiting_approval_expire_approval_manager_error",
                task_id=task_id,
                error=str(_exc),
                hint="HIGH-04 v4：expire_dead_approval 失败，用户 approve 可能返回假成功",
            )

    async def _recover_waiting_approval_push_failed(
        self,
        service: "TaskService",
        task_id: str,
        reason: str,
    ) -> None:
        """HIGH-04 v4 辅助：CAS 状态转移 WAITING_APPROVAL → FAILED 并通知。

        同时调用 _expire_approval_manager_entry，确保 ApprovalManager 中对应 dead approval
        被标记为 EXPIRED，防止用户后续 approve 时收到假成功响应（HIGH-04 v4 修复）。
        """
        try:
            await service._write_state_transition(
                task_id=task_id,
                from_status=TaskStatus.WAITING_APPROVAL,
                to_status=TaskStatus.FAILED,
                trace_id=f"trace-{task_id}",
                reason=reason,
            )
        except Exception as _cas_exc:
            log.warning(
                "recover_waiting_approval_cas_failed",
                task_id=task_id,
                error=str(_cas_exc),
            )
            return

        await self._stores.task_job_store.mark_failed(
            task_id,
            f"approval_recovery_{reason}",
        )
        await self._mark_execution_terminal(
            task_id=task_id,
            status=ExecutionSessionState.FAILED,
            message=f"gateway restart: {reason}",
        )
        # HIGH-04 v4：expire ApprovalManager 对应 pending entry，
        # 确保用户后续 approve 时收到 409/410 而非假成功
        await self._expire_approval_manager_entry(task_id)
        await self._notify_completion(task_id)

    async def _recover_one_orphan_job(self, job, service: TaskService) -> None:
        task = await service.get_task(job.task_id)
        if task is None:
            await self._stores.task_job_store.mark_failed(
                job.task_id,
                "task_missing_for_recovery",
            )
            return
        if task.status == TaskStatus.SUCCEEDED:
            await self._stores.task_job_store.mark_succeeded(job.task_id)
            await self._notify_completion(job.task_id)
            return
        if task.status == TaskStatus.WAITING_INPUT:
            await self._stores.task_job_store.mark_waiting_input(job.task_id)
            return
        if task.status == TaskStatus.WAITING_APPROVAL:
            await self._stores.task_job_store.mark_waiting_approval(job.task_id)
            return
        if task.status == TaskStatus.PAUSED:
            await self._stores.task_job_store.mark_paused(job.task_id)
            return
        if task.status in TERMINAL_STATES:
            await self._stores.task_job_store.mark_failed(
                job.task_id,
                f"task_terminal_status_{task.status}",
            )
            await self._notify_completion(job.task_id)
            return

        # task 仍在 CREATED 说明 dispatch 可能在 A2A 准备阶段崩溃了，直接推进到 FAILED。
        # CREATED 不具备 resume 条件，直接标记失败可以避免无限卡住。
        if task.status == TaskStatus.CREATED:
            log.warning(
                "recover_orphan_created_task",
                task_id=job.task_id,
                reason="job 为 RUNNING 但 task 仍为 CREATED，dispatch 可能异常中断",
            )
            await self._stores.task_job_store.mark_failed(
                job.task_id,
                "orphan_created_task_dispatch_failed",
            )
            await self._orchestrator._ensure_task_failed(
                job.task_id,
                f"trace-{job.task_id}",
                "任务在调度阶段意外中断，网关重启后自动清理。请重新发起请求。",
            )
            await self._notify_completion(job.task_id)
            return

        resume_result = await self._resume_engine.try_resume(job.task_id, trigger="startup")
        if resume_result.ok:
            self._cancellation_registry.ensure(job.task_id)
            # F101 Phase B FR-C6：startup_recovery 路径补充 is_caller_worker_signal 读取，
            # 与 attach_input 路径（task_runner.py:613-631）对称（N-H1 PARTIAL 修复）。
            # WorkerRuntime 首次 dispatch 时写入该信号（CONTROL_METADATA_UPDATED 事件）；
            # resume 路径如无信号，WorkerRuntime 仍回退到无条件 True（worker 子任务路径）。
            _startup_state_snapshot: dict = dict(resume_result.state_snapshot or {})
            try:
                _task_svc_startup = TaskService(self._stores, self._sse_hub)
                _latest_meta_startup = await _task_svc_startup.get_latest_user_metadata(job.task_id)
                if _latest_meta_startup.get("is_caller_worker_signal") == "1":
                    _startup_state_snapshot["is_caller_worker_signal"] = "1"
            except Exception:
                log.warning(
                    "startup_recovery_is_caller_worker_signal_read_failed",
                    task_id=job.task_id,
                )
            await self._spawn_job(
                task_id=job.task_id,
                user_text=job.user_text,
                model_alias=job.model_alias,
                resume_from_node=resume_result.resumed_from_node,
                resume_state_snapshot=_startup_state_snapshot if _startup_state_snapshot else resume_result.state_snapshot,
            )
            return

        await self._stores.task_job_store.mark_failed(
            job.task_id,
            f"gateway_resume_failed:{resume_result.failure_type or 'unknown'}",
        )
        await service.mark_running_task_failed_for_recovery(
            job.task_id,
            reason=f"网关恢复失败: {resume_result.message}",
        )
        await self._notify_completion(job.task_id)

    async def _start_job(self, task_id: str) -> None:
        async with self._lock:
            if task_id in self._running_jobs:
                return

        marked = await self._stores.task_job_store.mark_running(task_id)
        if not marked:
            return
        self._cancellation_registry.ensure(task_id)

        job = await self._stores.task_job_store.get_job(task_id)
        if job is None:
            await self._stores.task_job_store.mark_failed(task_id, "job_missing_after_mark_running")
            return

        await self._spawn_job(
            task_id=job.task_id,
            user_text=job.user_text,
            model_alias=job.model_alias,
        )

    async def _spawn_job(
        self,
        task_id: str,
        user_text: str,
        model_alias: str | None,
        resume_from_node: str | None = None,
        resume_state_snapshot: dict[str, Any] | None = None,
        dispatch_envelope: DispatchEnvelope | None = None,
    ) -> None:
        task = asyncio.create_task(
            self._run_job(
                task_id=task_id,
                user_text=user_text,
                model_alias=model_alias,
                resume_from_node=resume_from_node,
                resume_state_snapshot=resume_state_snapshot,
                dispatch_envelope=dispatch_envelope,
            )
        )
        async with self._lock:
            self._running_jobs[task_id] = RunningJob(
                task=task,
                started_at=datetime.now(UTC),
            )
        task.add_done_callback(lambda t, tid=task_id: asyncio.create_task(self._on_done(tid)))

    async def _on_done(self, task_id: str) -> None:
        async with self._lock:
            self._running_jobs.pop(task_id, None)
        self._cancellation_registry.clear(task_id)

    async def cancel_task(self, task_id: str) -> bool:
        """通知运行中任务取消。"""
        await self._execution_console.record_cancel_request(
            task_id=task_id,
            actor="user:web",
            reason="用户取消",
        )
        try:
            await self._orchestrator.record_cancel(
                task_id=task_id,
                reason="用户取消",
                actor="user:web",
            )
        except Exception as exc:  # pragma: no cover - 取消不应因 A2A 审计失败而阻塞
            log.warning(
                "task_runner_a2a_cancel_failed",
                task_id=task_id,
                error_type=type(exc).__name__,
            )
        self._cancellation_registry.cancel(task_id)

        async with self._lock:
            running = self._running_jobs.get(task_id)
        service = TaskService(self._stores, self._sse_hub)
        if running is None:
            job = await self._stores.task_job_store.get_job(task_id)
            if job is not None and job.status in _DEFERRED_JOB_STATUSES:
                await service.mark_running_task_cancelled_for_runtime(
                    task_id,
                    reason="用户取消",
                )
                await self._stores.task_job_store.mark_cancelled(task_id)
                await self._mark_execution_terminal(
                    task_id=task_id,
                    status=ExecutionSessionState.CANCELLED,
                    message="用户取消",
                )
                await self._notify_completion(task_id)
                return True
            return False

        running.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await running.task
        await service.mark_running_task_cancelled_for_runtime(
            task_id,
            reason="用户取消",
        )
        await self._stores.task_job_store.mark_cancelled(task_id)
        await self._mark_execution_terminal(
            task_id=task_id,
            status=ExecutionSessionState.CANCELLED,
            message="用户取消",
        )
        await self._notify_completion(task_id)
        return True

    async def get_execution_session(self, task_id: str) -> ExecutionConsoleSession | None:
        """查询 execution session。"""
        return await self._execution_console.get_session(task_id)

    async def collect_artifacts(self, task_id: str):
        """查询 execution artifacts。"""
        return await self._execution_console.collect_artifacts(task_id)

    async def attach_input(
        self,
        task_id: str,
        text: str,
        *,
        actor: str = "user:web",
        approval_id: str | None = None,
    ) -> AttachInputResult:
        """提交人工输入；若 live waiter 不存在则自动恢复执行。"""
        result = await self._execution_console.attach_input(
            task_id=task_id,
            text=text,
            actor=actor,
            approval_id=approval_id,
        )
        if result.delivered_live:
            async with self._lock:
                running = self._running_jobs.get(task_id)
                if running is not None:
                    running.started_at = datetime.now(UTC)
            return result

        job = await self._stores.task_job_store.get_job(task_id)
        if job is None:
            raise ExecutionInputError(
                "task job missing for input resume",
                code="TASK_JOB_MISSING",
            )

        async with self._lock:
            if task_id in self._running_jobs:
                return result

        await self._stores.task_job_store.mark_running_from_waiting_input(task_id)
        self._cancellation_registry.ensure(task_id)

        # F099 N-H1 修复：从持久化 latest_user_metadata 读取 is_caller_worker_signal，
        # 附加到 resume_state_snapshot，WorkerRuntime 据此重建 is_caller_worker=True。
        # WorkerRuntime 首次 dispatch 时写入该信号（CONTROL_METADATA_UPDATED 事件）；
        # resume 路径如无信号，WorkerRuntime 仍回退到无条件 True（worker 子任务路径）。
        _resume_snapshot: dict = {
            "execution_session_id": result.session_id,
            "human_input_artifact_id": result.artifact_id,
            "input_request_id": result.request_id,
        }
        try:
            _task_svc = TaskService(self._stores, self._sse_hub)
            _latest_meta = await _task_svc.get_latest_user_metadata(task_id)
            if _latest_meta.get("is_caller_worker_signal") == "1":
                _resume_snapshot["is_caller_worker_signal"] = "1"
        except Exception:
            log.warning(
                "attach_input_resume_is_caller_worker_signal_read_failed",
                task_id=task_id,
            )

        await self._spawn_job(
            task_id=task_id,
            user_text=job.user_text,
            model_alias=job.model_alias,
            resume_from_node="state_running",
            resume_state_snapshot=_resume_snapshot,
        )
        return result

    async def schedule_dispatch_envelope(self, envelope: DispatchEnvelope) -> bool:
        """为预构建 dispatch envelope 重新排队并异步执行。"""
        task_id = envelope.task_id
        async with self._lock:
            if task_id in self._running_jobs:
                return False

        job = await self._stores.task_job_store.get_job(task_id)
        if job is None or job.status in _TERMINAL_JOB_STATUSES:
            created = await self._stores.task_job_store.create_job(
                task_id,
                envelope.user_text,
                envelope.model_alias,
            )
            if not created:
                return False
            marked = await self._stores.task_job_store.mark_running(task_id)
        elif job.status in _DEFERRED_JOB_STATUSES:
            marked = await self._stores.task_job_store.mark_running_from_deferred(task_id)
        elif job.status == "QUEUED":
            marked = await self._stores.task_job_store.mark_running(task_id)
        elif job.status == "RUNNING":
            return False
        else:
            return False

        if not marked:
            return False

        self._cancellation_registry.ensure(task_id)
        await self._spawn_job(
            task_id=task_id,
            user_text=envelope.user_text,
            model_alias=envelope.model_alias,
            dispatch_envelope=envelope,
        )
        return True

    async def _run_job(
        self,
        task_id: str,
        user_text: str,
        model_alias: str | None,
        resume_from_node: str | None = None,
        resume_state_snapshot: dict[str, Any] | None = None,
        dispatch_envelope: DispatchEnvelope | None = None,
    ) -> None:
        service = TaskService(self._stores, self._sse_hub)
        try:
            if dispatch_envelope is None:
                metadata = await service.get_latest_user_metadata(task_id)
                result = await self._orchestrator.dispatch(
                    task_id=task_id,
                    user_text=user_text,
                    model_alias=model_alias,
                    resume_from_node=resume_from_node,
                    resume_state_snapshot=resume_state_snapshot,
                    tool_profile=str(metadata.get("tool_profile", "standard")).strip() or "standard",
                    metadata=metadata,
                )
            else:
                result = await self._orchestrator.dispatch_prepared(dispatch_envelope)
        except Exception as exc:
            # 防御性兜底：dispatch 内部任何未捕获异常都不能让 job 永远停在 RUNNING。
            # 记录完整异常后把 task 和 job 都标记为 FAILED，确保前端和 drift detector 能看到终态。
            log.error(
                "run_job_dispatch_exception",
                task_id=task_id,
                error_type=type(exc).__name__,
                error=str(exc),
                exc_info=True,
            )
            error_summary = f"dispatch_exception:{type(exc).__name__}:{str(exc)[:200]}"
            try:
                await self._stores.task_job_store.mark_failed(task_id, error_summary)
            except Exception:
                log.warning("run_job_mark_failed_fallback", task_id=task_id, exc_info=True)
            try:
                await self._orchestrator._ensure_task_failed(
                    task_id, f"trace-{task_id}", error_summary,
                )
            except Exception:
                log.warning("run_job_mark_task_failed_fallback", task_id=task_id, exc_info=True)
            # F097 Phase B-4 P2-4 闭环：dispatch exception 路径原本无 _notify_completion。
            # 这里显式调用 subagent cleanup（cleanup 内部有幂等 + try-except 隔离）。
            await self._close_subagent_session_if_needed(task_id)
            return

        task = await service.get_task(task_id)
        if task is None:
            await self._stores.task_job_store.mark_failed(task_id, "task_missing_after_processing")
            return
        if task.status == TaskStatus.SUCCEEDED:
            await self._stores.task_job_store.mark_succeeded(task_id)
            await self._notify_completion(task_id)
            return
        deferred_job_status = _DEFERRED_TASK_STATUSES.get(task.status)
        if deferred_job_status is not None:
            await self._stores.task_job_store.mark_deferred(task_id, deferred_job_status)
            return
        if task.status == TaskStatus.CANCELLED or result.status == TaskStatus.CANCELLED:
            await self._stores.task_job_store.mark_cancelled(task_id)
            await self._notify_completion(task_id)
            return
        # F101 Phase B N-M-02 v3：approval timeout 去重 check。
        # 场景：approval timeout CAS 成功后 monitor 已推 FAILED，
        # 但 worker _run_job 还在等 wait_for_decision → escalate_permission 返回后
        # _run_job 走到此处，task 已是 FAILED 终态（monitor 已处理）。
        # 此时不再调 mark_failed + notify（避免 double-notify）。
        # 检查：若 task 已是 TERMINAL_STATES（FAILED/CANCELLED 等），直接 return（幂等保护）。
        if task.status in TERMINAL_STATES:
            # 检查 task_jobs 表是否也已是终态（若是则跳过重复 mark_failed + notify）
            try:
                _job_for_check = await self._stores.task_job_store.get_job(task_id)
                if _job_for_check is not None and _job_for_check.status in _TERMINAL_JOB_STATUSES:
                    # task 和 job 都已是终态——monitor 已处理，跳过 double-notify
                    log.debug(
                        "run_job_skip_terminal_already_handled",
                        task_id=task_id,
                        task_status=task.status.value,
                        job_status=_job_for_check.status,
                        hint="N-M-02 v3：approval timeout 后 monitor 已推终态，跳过 double-notify",
                    )
                    return
            except Exception:
                pass  # 查询失败时不阻断，继续原有终态处理逻辑

            await self._stores.task_job_store.mark_failed(
                task_id,
                f"task_terminal_status_{task.status}",
            )
            await self._notify_completion(task_id)
            return
        await self._stores.task_job_store.mark_failed(
            task_id,
            f"task_left_non_terminal_status_{task.status}",
        )
        # F097 Phase B-4 P2-4: task 处于非终态时也触发 cleanup（幂等保护，cleanup 内部
        # 会检查 task 是否确实终态，不会对非终态 task 的 delegation session 做错误关闭）
        await self._close_subagent_session_if_needed(task_id)

    async def _monitor_loop_step(self) -> None:
        """单次监控循环体（供测试直接调用）。

        _monitor_loop 调用此方法，测试可以直接调用 _monitor_loop_step
        而无需等待整个 while True + sleep 循环。

        三个独立超时路径：
        1. 全局 job timeout（timeout_seconds）：started_at 超过阈值 → 取消 task
        2. F101 Phase B FR-C3 approval timeout（approval_timeout_seconds）：
           task 处于 WAITING_APPROVAL 且 updated_at 超过 approval_timeout_seconds →
           强制 FAILED（与 job timeout 无关，所有 running_jobs 中的 task 都要检查）
        3. F101 Phase B HIGH-02 v4：额外扫数据库 WAITING_APPROVAL task（不在 _running_jobs 的）：
           wait_for_decision timeout 返回后，escalate_permission_handler 返回，task 离开
           _running_jobs（done callback 移除），但 task 仍是 WAITING_APPROVAL。
           monitor 只扫 _running_jobs 会遗漏这些 task → task 永远 hang。
           修复：扫数据库 task_job_store["WAITING_APPROVAL"]，合并到检查集合。
        """
        threshold = datetime.now(UTC) - timedelta(seconds=self._timeout_seconds)
        _approval_threshold = (
            datetime.now(UTC)
            - timedelta(seconds=self._approval_timeout_seconds)
        )

        # 收集所有 running job 的 task_id，同时区分超时类型
        all_running_ids: list[str] = []
        timed_out_ids: list[str] = []
        async with self._lock:
            running_ids_set: set[str] = set(self._running_jobs.keys())
            for task_id, running in self._running_jobs.items():
                all_running_ids.append(task_id)
                if running.started_at < threshold:
                    timed_out_ids.append(task_id)

        # HIGH-02 v4：从数据库补充 WAITING_APPROVAL jobs（不在 _running_jobs 的 orphan task）。
        # 场景：wait_for_decision timeout → escalate_permission 返回 → done callback 移除 _running_jobs
        # → 下次 monitor tick _running_jobs 无此 task_id，但数据库 task_job 仍是 WAITING_APPROVAL。
        # 这类 orphan task 需要 monitor 通过数据库查询兜底推 FAILED。
        try:
            _db_waiting_approval_jobs = await self._stores.task_job_store.list_jobs(["WAITING_APPROVAL"])
            for _wa_job in _db_waiting_approval_jobs:
                if _wa_job.task_id not in running_ids_set:
                    # 不在 _running_jobs，但数据库 task_job 是 WAITING_APPROVAL → orphan
                    all_running_ids.append(_wa_job.task_id)
                    # 不加入 timed_out_ids：让 approval_timeout 路径判断是否超时
        except Exception as _db_exc:
            log.warning(
                "monitor_loop_db_waiting_approval_scan_error",
                error=str(_db_exc),
                hint="HIGH-02 v4：数据库扫描失败，仅扫 _running_jobs（降级）",
            )

        if not all_running_ids:
            return

        service = TaskService(self._stores, self._sse_hub)

        # F101 Phase B FR-C3 + HIGH-02 v4：先检查 WAITING_APPROVAL 超时（approval_timeout_seconds）
        # 此检查覆盖：
        #   A) _running_jobs 中的 WAITING_APPROVAL task（正常路径）
        #   B) 数据库中 orphan WAITING_APPROVAL task（HIGH-02 v4 新增路径）
        for task_id in all_running_ids:
            task = await service.get_task(task_id)
            if task is None or task.status != TaskStatus.WAITING_APPROVAL:
                continue
            # 计算 task.updated_at 是否超出 approval_timeout_seconds
            if task.updated_at.tzinfo is None:
                from datetime import timezone as _tz
                _task_updated = task.updated_at.replace(tzinfo=_tz.utc)
            else:
                _task_updated = task.updated_at
            if _task_updated >= _approval_threshold:
                # 还未超出 approval_timeout_seconds，继续等待
                continue
            # approval_timeout_seconds 已超出：强制 FAILED 终态（H2：task_runner owner）
            _reason = f"user_inaction_{int(self._approval_timeout_seconds)}s"
            log.warning(
                "task_runner_approval_timeout",
                task_id=task_id,
                approval_timeout_seconds=self._approval_timeout_seconds,
                reason=_reason,
            )
            # H2 决议：直接写 WAITING_APPROVAL → FAILED 状态转移（CAS 语义）。
            # F101 Phase B HIGH-03 修复：先做 CAS 状态转移，只有 CAS 成功后才执行 side effects。
            # CAS 失败（task 已不在 WAITING_APPROVAL）→ abort 整个 FAILED 路径，不调 mark_failed / notify。
            # 原因：先写 task_job_store.mark_failed 后做 CAS 时，CAS 失败会导致：
            #   task 表=RUNNING、job 表=FAILED、通知已发——三者状态分裂（Durability First 违反）。
            _cas_succeeded = False
            try:
                await service._write_state_transition(
                    task_id=task_id,
                    from_status=TaskStatus.WAITING_APPROVAL,
                    to_status=TaskStatus.FAILED,
                    trace_id=f"trace-{task_id}",
                    reason=_reason,
                )
                _cas_succeeded = True
            except Exception as _transition_exc:
                log.warning(
                    "task_runner_approval_timeout_transition_failed_abort",
                    task_id=task_id,
                    error=str(_transition_exc),
                    hint="CAS 失败（task 已不在 WAITING_APPROVAL），abort FAILED 路径，不 emit side effects",
                )
            if not _cas_succeeded:
                continue  # CAS 失败，跳过后续 side effects，处理下一个 task_id
            # CAS 成功后才调 mark_failed + side effects（防止状态分裂）
            await self._stores.task_job_store.mark_failed(
                task_id,
                f"approval_timeout_{int(self._approval_timeout_seconds)}s",
            )
            await self._mark_execution_terminal(
                task_id=task_id,
                status=ExecutionSessionState.FAILED,
                message=f"approval timeout: {_reason}",
            )
            await self._notify_completion(task_id)

        # 全局 job timeout 路径：只处理 started_at 超出 timeout_seconds 的 task
        if not timed_out_ids:
            return

        for task_id in timed_out_ids:
            task = await service.get_task(task_id)
            if task is not None and task.status in {
                TaskStatus.WAITING_INPUT,
                TaskStatus.PAUSED,
                TaskStatus.WAITING_APPROVAL,  # WAITING_APPROVAL 已在上方处理（或尚未超时）
            }:
                continue
            async with self._lock:
                running = self._running_jobs.get(task_id)
            if running is None:
                continue

            self._cancellation_registry.cancel(task_id)
            running.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await running.task

            await self._stores.task_job_store.mark_failed(
                task_id,
                f"job_timeout_after_{int(self._timeout_seconds)}s",
            )
            await service.mark_running_task_failed_for_recovery(
                task_id,
                reason=f"后台任务超时（>{int(self._timeout_seconds)}s）",
            )
            await self._mark_execution_terminal(
                task_id=task_id,
                status=ExecutionSessionState.FAILED,
                message="worker runtime timeout",
            )
            await self._notify_completion(task_id)
            log.warning("task_runner_job_timeout", task_id=task_id)

    async def _monitor_loop(self) -> None:
        while True:
            await asyncio.sleep(self._monitor_interval_seconds)
            await self._monitor_loop_step()

    async def _notify_completion(self, task_id: str) -> None:
        # F097 Phase E: 在通知前先执行 subagent session cleanup
        # cleanup 内部已有 try-except 隔离，异常不影响主流程
        await self._close_subagent_session_if_needed(task_id)
        if self._completion_notifier is None:
            return
        try:
            await self._completion_notifier(task_id)
        except Exception as exc:  # pragma: no cover - 通知链路异常不能影响主流程
            log.warning(
                "task_runner_completion_notifier_failed",
                task_id=task_id,
                error_type=type(exc).__name__,
            )

    async def _close_subagent_session_if_needed(self, task_id: str) -> None:
        """F097 Phase E: subagent 完成后真清理 SUBAGENT_INTERNAL session + emit SUBAGENT_COMPLETED。

        幂等：对已 CLOSED session 或 delegation.closed_at 已设置时重复调用不报错。
        保留 RecallFrame：不清理 RecallFrame 历史（H1 audit 优先）。
        异常隔离：cleanup 失败 log warn 不影响主流程（AC-E1 异常隔离要求）。
        非 subagent task：无 subagent_delegation metadata 时立即 return（对 main / worker 零影响）。
        """
        try:
            # 1. 通过 TaskService 读取 control_metadata，取出 subagent_delegation
            service = TaskService(self._stores, self._sse_hub)
            control_metadata = await service.get_latest_user_metadata(task_id)
            raw_delegation = control_metadata.get("subagent_delegation")
            if not raw_delegation:
                return  # 非 subagent task，直接 return

            # 2. 反序列化为 SubagentDelegation
            if isinstance(raw_delegation, str):
                delegation = SubagentDelegation.model_validate_json(raw_delegation)
            else:
                delegation = SubagentDelegation.model_validate(raw_delegation)

            # 3. spawn 失败场景：child_agent_session_id 为 None 则 return
            if delegation.child_agent_session_id is None:
                return

            # 4. 幂等：若 delegation.closed_at 已设置，return
            if delegation.closed_at is not None:
                return

            # 5. 查 Task 获取终态时间戳
            task = await service.get_task(task_id)
            # F097 Phase B-4 (Codex P2-5 闭环): 验证 task 真终态——非终态调用直接 return。
            # _notify_completion 正常路径触发时 task 已是终态；但 dispatch exception /
            # shutdown 兜底等路径若在 task 非终态时调用 cleanup，会用错误的"current 状态"
            # 写 SUBAGENT_COMPLETED 并提前关闭未真正终结的 subagent session。
            if task is None:
                return
            if task.status not in TERMINAL_STATES:
                # task 还非终态（RUNNING / CREATED / PAUSED 等），不应触发 cleanup
                log.debug(
                    "subagent_session_cleanup_skipped_non_terminal",
                    task_id=task_id,
                    task_status=str(task.status),
                )
                return
            terminal_at = task.updated_at if task.updated_at else datetime.now(tz=UTC)
            terminal_status_str = (
                task.status.value
                if hasattr(task.status, "value")
                else str(task.status)
            )

            # 6. F097 Phase E (Codex P1-2 闭环) + Phase B-4 (Codex P2-4 缓解):
            # 用 EventStore.check_idempotency_key 防止重复 emit。重复触发 cleanup
            # （_notify_completion 多次 / 进程重启）时，若 SUBAGENT_COMPLETED 事件已写入
            # 则跳过 emit 但**仍尝试 close session**（P2-4 缓解：避免事件 OK + session
            # 因 first cleanup 失败留在 ACTIVE 永久状态）。
            idempotency_key = f"subagent_completed:{delegation.delegation_id}"
            existing_task = await self._stores.event_store.check_idempotency_key(
                idempotency_key
            )
            should_emit_event = existing_task is None
            if not should_emit_event:
                log.info(
                    "subagent_session_cleanup_event_already_emitted",
                    task_id=task_id,
                    delegation_id=delegation.delegation_id,
                    existing_event_task=existing_task,
                )
                # 不 return：继续走到 step 8 尝试 close session（P2-4 缓解）

            # F098 Phase G (P2-3 修复，OD-3 选 A) + Final Codex review P2 修复：
            # event + session 同一事务，且保留 append_event_committed 的 task_seq 冲突重试机制。
            #
            # 原 F097 Phase B-4 用 append_event_committed (commit 1) + save_agent_session +
            # conn.commit() (commit 2) 是 2 次 commit。F098 Phase G 初版改用 append_event
            # (pending) 失去了 task_seq 重试 + per-task lock（Codex review P2 抓到）。
            #
            # Final 修复方案：
            # - 用 append_event_committed(update_task_pointer=False)：保留 task_seq 重试 +
            #   per-task lock（防并发）；event 在 append_event_committed 内已 commit。
            # - session.save 走自己的 save_agent_session + 单独 commit。
            # - 仍是 2 次 commit，但与 F097 baseline 相比已颠倒顺序（event 先 commit，
            #   session 后 commit）：失败模式优化为"event 存在但 session 未 close"
            #   （audit chain 优先，与 F097 Phase B-4 设计一致）。
            # - rollback 路径仅对 session.save 失败有效（event 已独立 commit 不可回滚）；
            #   session.save 失败时下次 cleanup 重试，idempotency_key 守护 event 短路 emit，
            #   重新尝试关 session。
            #
            # 真正的 single-transaction atomic 需要 EventStore API 演化（append_event_pending
            # API + 显式 task lock 暴露）—— 推迟到 F107 Capability Layer Refactor。
            # F098 Phase G 当前实施：保留 F097 Phase B-4 的"颠倒顺序 + idempotency 守护"
            # 设计 + 加 try/except 包裹（异常路径更明确）。

            parent_task_id = delegation.parent_task_id
            session = await self._stores.agent_context_store.get_agent_session(
                delegation.child_agent_session_id
            )
            session_needs_close = (
                session is not None and session.status != AgentSessionStatus.CLOSED
            )

            if should_emit_event:
                payload = SubagentCompletedPayload(
                    delegation_id=delegation.delegation_id,
                    child_task_id=delegation.child_task_id,
                    terminal_status=terminal_status_str,
                    closed_at=terminal_at,
                    parent_task_id=parent_task_id,
                    child_agent_session_id=delegation.child_agent_session_id,
                )
                event_seq = await self._stores.event_store.get_next_task_seq(parent_task_id)
                completed_event = Event(
                    event_id=str(ULID()),
                    task_id=parent_task_id,
                    task_seq=event_seq,
                    ts=terminal_at,
                    type=EventType.SUBAGENT_COMPLETED,
                    actor=ActorType.SYSTEM,
                    payload=payload.model_dump(mode="json"),
                    trace_id=f"trace-{parent_task_id}",
                    causality=EventCausality(idempotency_key=idempotency_key),
                )
                # Final Codex review P2 修复：保留 append_event_committed 的
                # task_seq 冲突重试 + per-task lock（防并发同 parent task 竞态）
                await self._stores.event_store.append_event_committed(
                    completed_event, update_task_pointer=False
                )

            if session_needs_close:
                try:
                    updated_session = session.model_copy(
                        update={
                            "status": AgentSessionStatus.CLOSED,
                            "closed_at": terminal_at,
                        }
                    )
                    await self._stores.agent_context_store.save_agent_session(updated_session)
                    await self._stores.conn.commit()
                except Exception:
                    try:
                        await self._stores.conn.rollback()
                    except Exception as rollback_exc:
                        log.error(
                            "subagent_cleanup_session_rollback_failed",
                            task_id=task_id,
                            error=str(rollback_exc),
                        )
                    raise

            log.info(
                "subagent_session_cleanup_completed",
                task_id=task_id,
                delegation_id=delegation.delegation_id,
                child_agent_session_id=delegation.child_agent_session_id,
                terminal_status=terminal_status_str,
            )
        except Exception as cleanup_exc:
            log.warning(
                "subagent_session_cleanup_failed",
                task_id=task_id,
                error=str(cleanup_exc),
            )

    async def _mark_execution_terminal(
        self,
        *,
        task_id: str,
        status: ExecutionSessionState,
        message: str,
    ) -> None:
        session = await self._execution_console.get_session(task_id)
        if session is None:
            return
        if session.live is False:
            events = await self._execution_console.list_execution_events(
                task_id,
                session_id=session.session_id,
            )
            latest_status = next(
                (event for event in reversed(events) if event.kind.value == "status"),
                None,
            )
            if latest_status is not None and latest_status.final:
                return
        await self._execution_console.mark_status(
            task_id=task_id,
            session_id=session.session_id,
            status=status,
            message=message,
        )
