"""Orchestrator 控制平面服务（Feature 008）。

最小职责：
1. 请求封装与高风险 gate
2. 单 worker 路由与派发
3. 控制平面事件写入（ORCH_DECISION/WORKER_DISPATCHED/WORKER_RETURNED）
4. worker 结果回传与失败分类
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import structlog
from octoagent.core.models import (
    TERMINAL_STATES,
    ActorType,
    DelegationResult,
    DelegationTargetKind,
    DispatchEnvelope,
    Event,
    EventCausality,
    EventType,
    OrchestratorDecisionPayload,
    OrchestratorRequest,
    RiskLevel,
    TaskStatus,
    WorkerDispatchedPayload,
    WorkerExecutionStatus,
    WorkerResult,
    WorkerReturnedPayload,
    WorkStatus,
)
from octoagent.core.store import StoreGroup
from octoagent.policy.models import ApprovalDecision, ApprovalStatus
from ulid import ULID

from .task_service import TaskService
from .worker_runtime import (
    WorkerCancellationRegistry,
    WorkerRuntime,
    WorkerRuntimeConfig,
)

log = structlog.get_logger()


class OrchestratorRoutingError(RuntimeError):
    """路由阶段异常。"""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class OrchestratorPolicyDecision:
    """控制平面 gate 决策。"""

    allow: bool
    reason: str


class OrchestratorApprovalManager(Protocol):
    """审批管理器最小接口。"""

    def get_approval(self, approval_id: str):
        """查询审批记录。"""

    def consume_allow_once(self, approval_id: str) -> bool:
        """消费一次性审批令牌。"""


class OrchestratorWorker(Protocol):
    """Orchestrator worker 协议。"""

    @property
    def worker_id(self) -> str:
        """worker 唯一标识。"""

    @property
    def capability(self) -> str:
        """worker 能力标签。"""

    async def handle(self, envelope: DispatchEnvelope) -> WorkerResult:
        """执行派发信封并返回结果。"""


class OrchestratorPolicyGate:
    """控制平面高风险 gate。

    本 gate 仅处理派发入口风险，不替代 Feature 006 的工具级审批链路。
    """

    def __init__(self, approval_manager: OrchestratorApprovalManager | None = None) -> None:
        self._approval_manager = approval_manager

    def evaluate(self, request: OrchestratorRequest) -> OrchestratorPolicyDecision:
        if request.risk_level != RiskLevel.HIGH:
            return OrchestratorPolicyDecision(allow=True, reason="risk_not_high")

        approval_id = (
            request.metadata.get("approval_id", "").strip()
            or request.metadata.get("approval_token", "").strip()
        )
        if not approval_id:
            return OrchestratorPolicyDecision(
                allow=False,
                reason="high_risk_requires_approval_id",
            )
        if self._approval_manager is None:
            return OrchestratorPolicyDecision(
                allow=False,
                reason="high_risk_approval_manager_unavailable",
            )
        approval = self._approval_manager.get_approval(approval_id)
        if approval is None:
            return OrchestratorPolicyDecision(
                allow=False,
                reason="high_risk_approval_not_found",
            )
        if approval.request.task_id != request.task_id:
            return OrchestratorPolicyDecision(
                allow=False,
                reason="high_risk_approval_task_mismatch",
            )
        if approval.status == ApprovalStatus.PENDING:
            return OrchestratorPolicyDecision(
                allow=False,
                reason="high_risk_approval_pending",
            )
        if approval.status == ApprovalStatus.REJECTED:
            return OrchestratorPolicyDecision(
                allow=False,
                reason="high_risk_approval_rejected",
            )
        if approval.status == ApprovalStatus.EXPIRED:
            return OrchestratorPolicyDecision(
                allow=False,
                reason="high_risk_approval_expired",
            )
        if approval.status != ApprovalStatus.APPROVED:
            return OrchestratorPolicyDecision(
                allow=False,
                reason=f"high_risk_approval_status_invalid:{approval.status}",
            )
        if approval.decision == ApprovalDecision.ALLOW_ONCE:
            consumed = self._approval_manager.consume_allow_once(approval_id)
            if not consumed:
                return OrchestratorPolicyDecision(
                    allow=False,
                    reason="high_risk_approval_allow_once_consumed",
                )
            return OrchestratorPolicyDecision(
                allow=True,
                reason="high_risk_approval_allow_once",
            )
        if approval.decision == ApprovalDecision.ALLOW_ALWAYS:
            return OrchestratorPolicyDecision(
                allow=True,
                reason="high_risk_approval_allow_always",
            )
        return OrchestratorPolicyDecision(
            allow=False,
            reason="high_risk_approval_decision_invalid",
        )


class SingleWorkerRouter:
    """单 worker 路由器（rule-based）。"""

    def route(self, request: OrchestratorRequest) -> DispatchEnvelope:
        next_hop = request.hop_count + 1
        if next_hop > request.max_hops:
            raise OrchestratorRoutingError(
                f"hop_count_exceeded: next={next_hop}, max={request.max_hops}",
                retryable=False,
            )

        if not request.worker_capability.strip():
            raise OrchestratorRoutingError(
                "worker_capability_empty",
                retryable=False,
            )

        return DispatchEnvelope(
            dispatch_id=str(ULID()),
            task_id=request.task_id,
            trace_id=request.trace_id,
            contract_version=request.contract_version,
            route_reason=request.route_reason or "single_worker_default",
            worker_capability=request.worker_capability,
            hop_count=next_hop,
            max_hops=request.max_hops,
            user_text=request.user_text,
            model_alias=request.model_alias,
            resume_from_node=request.resume_from_node,
            resume_state_snapshot=request.resume_state_snapshot,
            tool_profile=request.tool_profile,
            runtime_context=request.runtime_context,
            metadata=request.metadata,
        )


class LLMWorkerAdapter:
    """默认 LLM worker 适配器。"""

    def __init__(
        self,
        store_group: StoreGroup,
        sse_hub,
        llm_service,
        *,
        worker_id: str = "worker.llm.default",
        capability: str = "llm_generation",
        runtime_config: WorkerRuntimeConfig | None = None,
        docker_available_checker: Callable[[], bool] | None = None,
        cancellation_registry: WorkerCancellationRegistry | None = None,
        execution_console=None,
    ) -> None:
        self._stores = store_group
        self._sse_hub = sse_hub
        self._worker_id = worker_id
        self._capability = capability
        self._runtime = WorkerRuntime(
            store_group=store_group,
            sse_hub=sse_hub,
            llm_service=llm_service,
            config=runtime_config,
            docker_available_checker=docker_available_checker,
            cancellation_registry=cancellation_registry,
            execution_console=execution_console,
        )

    @property
    def worker_id(self) -> str:
        return self._worker_id

    @property
    def capability(self) -> str:
        return self._capability

    async def handle(self, envelope: DispatchEnvelope) -> WorkerResult:
        return await self._runtime.run(envelope, worker_id=self.worker_id)


class OrchestratorService:
    """Orchestrator 控制平面入口。"""

    def __init__(
        self,
        store_group: StoreGroup,
        sse_hub,
        llm_service,
        approval_manager: OrchestratorApprovalManager | None = None,
        *,
        policy_gate: OrchestratorPolicyGate | None = None,
        router: SingleWorkerRouter | None = None,
        workers: dict[str, OrchestratorWorker] | None = None,
        delegation_plane=None,
        worker_runtime_config: WorkerRuntimeConfig | None = None,
        docker_available_checker: Callable[[], bool] | None = None,
        cancellation_registry: WorkerCancellationRegistry | None = None,
        execution_console=None,
    ) -> None:
        self._stores = store_group
        self._sse_hub = sse_hub
        self._policy_gate = policy_gate or OrchestratorPolicyGate(approval_manager=approval_manager)
        self._router = router or SingleWorkerRouter()
        self._delegation_plane = delegation_plane

        default_workers = [
            LLMWorkerAdapter(
                store_group,
                sse_hub,
                llm_service,
                worker_id="worker.llm.default",
                capability="llm_generation",
                runtime_config=worker_runtime_config,
                docker_available_checker=docker_available_checker,
                cancellation_registry=cancellation_registry,
                execution_console=execution_console,
            ),
            LLMWorkerAdapter(
                store_group,
                sse_hub,
                llm_service,
                worker_id="worker.llm.ops",
                capability="ops",
                runtime_config=worker_runtime_config,
                docker_available_checker=docker_available_checker,
                cancellation_registry=cancellation_registry,
                execution_console=execution_console,
            ),
            LLMWorkerAdapter(
                store_group,
                sse_hub,
                llm_service,
                worker_id="worker.llm.research",
                capability="research",
                runtime_config=worker_runtime_config,
                docker_available_checker=docker_available_checker,
                cancellation_registry=cancellation_registry,
                execution_console=execution_console,
            ),
            LLMWorkerAdapter(
                store_group,
                sse_hub,
                llm_service,
                worker_id="worker.llm.dev",
                capability="dev",
                runtime_config=worker_runtime_config,
                docker_available_checker=docker_available_checker,
                cancellation_registry=cancellation_registry,
                execution_console=execution_console,
            ),
        ]
        self._workers: dict[str, OrchestratorWorker] = {
            worker.capability: worker for worker in default_workers
        }
        if workers:
            self._workers.update(workers)

    async def dispatch_prepared(self, envelope: DispatchEnvelope) -> WorkerResult:
        """执行已完成 preflight 的 dispatch envelope。"""
        task = await self._stores.task_store.get_task(envelope.task_id)
        risk_level = task.risk_level if task is not None else RiskLevel.LOW
        request = OrchestratorRequest(
            task_id=envelope.task_id,
            trace_id=envelope.trace_id,
            user_text=envelope.user_text,
            model_alias=envelope.model_alias,
            resume_from_node=envelope.resume_from_node,
            resume_state_snapshot=envelope.resume_state_snapshot,
            worker_capability=envelope.worker_capability,
            contract_version=envelope.contract_version,
            route_reason=envelope.route_reason,
            hop_count=envelope.hop_count,
            max_hops=envelope.max_hops,
            tool_profile=envelope.tool_profile,
            runtime_context=envelope.runtime_context,
            risk_level=risk_level,
            metadata=dict(envelope.metadata),
        )
        return await self._dispatch_envelope(
            request=request,
            envelope=envelope,
            gate_decision=OrchestratorPolicyDecision(
                allow=True,
                reason="delegation_pipeline_ready",
            ),
            work_id=envelope.metadata.get("work_id", ""),
        )

    async def dispatch(
        self,
        task_id: str,
        user_text: str,
        model_alias: str | None = None,
        resume_from_node: str | None = None,
        resume_state_snapshot: dict[str, Any] | None = None,
        *,
        worker_capability: str = "llm_generation",
        contract_version: str = "1.0",
        hop_count: int = 0,
        max_hops: int = 3,
        tool_profile: str = "standard",
        metadata: dict[str, str] | None = None,
    ) -> WorkerResult:
        trace_id = f"trace-{task_id}"
        task = await self._stores.task_store.get_task(task_id)
        risk_level = task.risk_level if task is not None else RiskLevel.LOW

        request = OrchestratorRequest(
            task_id=task_id,
            trace_id=trace_id,
            user_text=user_text,
            model_alias=model_alias,
            resume_from_node=resume_from_node,
            resume_state_snapshot=resume_state_snapshot,
            worker_capability=worker_capability,
            contract_version=contract_version,
            hop_count=hop_count,
            max_hops=max_hops,
            tool_profile=tool_profile,
            runtime_context=None,
            risk_level=risk_level,
            metadata=metadata or {},
        )

        gate_decision = self._policy_gate.evaluate(request)
        if not gate_decision.allow:
            await self._write_orch_decision_event(
                request=request,
                route_reason="policy_gate_denied",
                gate_decision=gate_decision,
            )
            await self._ensure_task_rejected(task_id, trace_id, gate_decision.reason)
            return WorkerResult(
                dispatch_id="policy-gate-denied",
                task_id=task_id,
                worker_id="orchestrator.policy_gate",
                status=WorkerExecutionStatus.FAILED,
                retryable=False,
                summary="dispatch_blocked_by_policy_gate",
                error_type="PolicyGateDenied",
                error_message=gate_decision.reason,
            )

        try:
            if self._delegation_plane is not None:
                plan = await self._delegation_plane.prepare_dispatch(request)
                if plan.dispatch_envelope is None:
                    await self._write_orch_decision_event(
                        request=request,
                        route_reason=plan.work.route_reason or "delegation_pipeline_deferred",
                        gate_decision=gate_decision,
                    )
                    await self._ensure_task_waiting(
                        task_id=task_id,
                        trace_id=trace_id,
                        status=plan.pipeline_status.value,
                        reason=plan.deferred_reason or plan.pipeline_status.value,
                    )
                    return WorkerResult(
                        dispatch_id=f"delegation-{plan.work.work_id}",
                        task_id=task_id,
                        worker_id="orchestrator.delegation",
                        status=WorkerExecutionStatus.FAILED,
                        retryable=True,
                        summary=f"delegation_deferred:{plan.pipeline_status.value}",
                        error_type="DelegationDeferred",
                        error_message=plan.deferred_reason or plan.pipeline_status.value,
                    )
                envelope = plan.dispatch_envelope
                work_id = plan.work.work_id
            else:
                envelope = self._router.route(request)
                work_id = ""
        except OrchestratorRoutingError as exc:
            await self._write_orch_decision_event(
                request=request,
                route_reason="routing_error",
                gate_decision=gate_decision,
            )
            await self._ensure_task_failed(task_id, trace_id, str(exc))
            return WorkerResult(
                dispatch_id="routing-error",
                task_id=task_id,
                worker_id="orchestrator.router",
                status=WorkerExecutionStatus.FAILED,
                retryable=exc.retryable,
                summary="dispatch_routing_failed",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )

        return await self._dispatch_envelope(
            request=request,
            envelope=envelope,
            gate_decision=gate_decision,
            work_id=work_id,
        )

    async def _dispatch_envelope(
        self,
        *,
        request: OrchestratorRequest,
        envelope: DispatchEnvelope,
        gate_decision: OrchestratorPolicyDecision,
        work_id: str,
    ) -> WorkerResult:
        task_id = envelope.task_id
        trace_id = request.trace_id
        await self._write_orch_decision_event(
            request=request,
            route_reason=envelope.route_reason,
            gate_decision=gate_decision,
        )

        worker = self._workers.get(envelope.worker_capability)
        if worker is None:
            summary = f"worker_not_found_for_capability:{envelope.worker_capability}"
            result = WorkerResult(
                dispatch_id=envelope.dispatch_id,
                task_id=envelope.task_id,
                worker_id="orchestrator.registry",
                status=WorkerExecutionStatus.FAILED,
                retryable=False,
                summary=summary,
                error_type="WorkerNotFound",
                error_message=summary,
            )
            await self._write_worker_returned_event(result)
            await self._ensure_task_failed(task_id, trace_id, summary)
            if self._delegation_plane is not None and work_id:
                await self._delegation_plane.complete_work(
                    work_id=work_id,
                    result=DelegationResult(
                        delegation_id=envelope.dispatch_id,
                        work_id=work_id,
                        status=WorkStatus.FAILED,
                        summary=summary,
                        retryable=False,
                        runtime_id="orchestrator.registry",
                        target_kind=DelegationTargetKind.FALLBACK,
                        route_reason=envelope.route_reason,
                    ),
                )
            return result

        if self._delegation_plane is not None and work_id:
            await self._delegation_plane.mark_dispatched(
                work_id=work_id,
                worker_id=worker.worker_id,
                dispatch_id=envelope.dispatch_id,
            )
        await self._write_worker_dispatched_event(envelope, worker.worker_id)

        try:
            result = await worker.handle(envelope)
        except Exception as exc:  # pragma: no cover - 防御性兜底
            log.error(
                "orchestrator_worker_handle_exception",
                task_id=task_id,
                worker_id=worker.worker_id,
                error_type=type(exc).__name__,
                error=str(exc),
                exc_info=True,
            )
            result = WorkerResult(
                dispatch_id=envelope.dispatch_id,
                task_id=envelope.task_id,
                worker_id=worker.worker_id,
                status=WorkerExecutionStatus.FAILED,
                retryable=True,
                summary="worker_runtime_exception",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            await self._ensure_task_failed(task_id, trace_id, result.summary)

        await self._write_worker_returned_event(result)
        if self._delegation_plane is not None and work_id:
            await self._delegation_plane.complete_work(
                work_id=work_id,
                result=DelegationResult(
                    delegation_id=envelope.dispatch_id,
                    work_id=work_id,
                    status={
                        WorkerExecutionStatus.SUCCEEDED: WorkStatus.SUCCEEDED,
                        WorkerExecutionStatus.FAILED: WorkStatus.FAILED,
                        WorkerExecutionStatus.CANCELLED: WorkStatus.CANCELLED,
                    }[result.status],
                    summary=result.summary,
                    retryable=result.retryable,
                    runtime_id=result.worker_id,
                    target_kind=DelegationTargetKind(
                        envelope.metadata.get("target_kind", DelegationTargetKind.FALLBACK.value)
                    ),
                    route_reason=envelope.route_reason,
                    metadata={
                        "backend": result.backend,
                        "tool_profile": result.tool_profile,
                    },
                ),
            )

        if result.status == WorkerExecutionStatus.FAILED:
            await self._ensure_task_failed(task_id, trace_id, result.summary)

        return result

    async def _write_orch_decision_event(
        self,
        *,
        request: OrchestratorRequest,
        route_reason: str,
        gate_decision: OrchestratorPolicyDecision,
    ) -> None:
        payload = OrchestratorDecisionPayload(
            contract_version=request.contract_version,
            route_reason=route_reason,
            worker_capability=request.worker_capability,
            hop_count=request.hop_count,
            max_hops=request.max_hops,
            gate_decision="allow" if gate_decision.allow else "deny",
            gate_reason=gate_decision.reason,
        )
        await self._append_control_event(
            task_id=request.task_id,
            trace_id=request.trace_id,
            event_type=EventType.ORCH_DECISION,
            payload=payload.model_dump(),
        )

    async def _write_worker_dispatched_event(
        self,
        envelope: DispatchEnvelope,
        worker_id: str,
    ) -> None:
        payload = WorkerDispatchedPayload(
            dispatch_id=envelope.dispatch_id,
            worker_id=worker_id,
            worker_capability=envelope.worker_capability,
            contract_version=envelope.contract_version,
        )
        await self._append_control_event(
            task_id=envelope.task_id,
            trace_id=envelope.trace_id,
            event_type=EventType.WORKER_DISPATCHED,
            payload=payload.model_dump(),
        )

    async def _write_worker_returned_event(self, result: WorkerResult) -> None:
        payload = WorkerReturnedPayload(
            dispatch_id=result.dispatch_id,
            worker_id=result.worker_id,
            status=result.status.value,
            retryable=result.retryable,
            summary=result.summary,
            error_type=result.error_type or "",
            error_message=result.error_message or "",
            loop_step=result.loop_step,
            max_steps=result.max_steps,
            backend=result.backend,
            tool_profile=result.tool_profile,
        )
        await self._append_control_event(
            task_id=result.task_id,
            trace_id=f"trace-{result.task_id}",
            event_type=EventType.WORKER_RETURNED,
            payload=payload.model_dump(),
        )

    async def _append_control_event(
        self,
        *,
        task_id: str,
        trace_id: str,
        event_type: EventType,
        payload: dict[str, object],
    ) -> None:
        event = Event(
            event_id=str(ULID()),
            task_id=task_id,
            task_seq=await self._stores.event_store.get_next_task_seq(task_id),
            ts=datetime.now(UTC),
            type=event_type,
            actor=ActorType.KERNEL,
            payload=payload,
            trace_id=trace_id,
            causality=EventCausality(),
        )
        stored = await self._stores.event_store.append_event_committed(event)
        if self._sse_hub:
            await self._sse_hub.broadcast(task_id, stored)

    async def _ensure_task_failed(self, task_id: str, trace_id: str, reason: str) -> None:
        task = await self._stores.task_store.get_task(task_id)
        if task is None or task.status in TERMINAL_STATES:
            return

        service = TaskService(self._stores, self._sse_hub)
        try:
            if task.status == TaskStatus.CREATED:
                await service._write_state_transition(
                    task_id=task_id,
                    from_status=TaskStatus.CREATED,
                    to_status=TaskStatus.RUNNING,
                    trace_id=trace_id,
                    reason="orchestrator_bootstrap",
                )
                task = await self._stores.task_store.get_task(task_id)
                if task is None:
                    return

            if task.status == TaskStatus.RUNNING:
                await service._write_state_transition(
                    task_id=task_id,
                    from_status=TaskStatus.RUNNING,
                    to_status=TaskStatus.FAILED,
                    trace_id=trace_id,
                    reason=reason,
                )
        except Exception as exc:  # pragma: no cover - 防御性兜底
            log.warning(
                "orchestrator_force_fail_error",
                task_id=task_id,
                reason=reason,
                error_type=type(exc).__name__,
            )

    async def _ensure_task_rejected(self, task_id: str, trace_id: str, reason: str) -> None:
        task = await self._stores.task_store.get_task(task_id)
        if task is None or task.status in TERMINAL_STATES:
            return

        service = TaskService(self._stores, self._sse_hub)
        try:
            if task.status == TaskStatus.CREATED:
                await service._write_state_transition(
                    task_id=task_id,
                    from_status=TaskStatus.CREATED,
                    to_status=TaskStatus.RUNNING,
                    trace_id=trace_id,
                    reason="orchestrator_bootstrap",
                )
                task = await self._stores.task_store.get_task(task_id)
                if task is None:
                    return

            if task.status == TaskStatus.RUNNING:
                await service._write_state_transition(
                    task_id=task_id,
                    from_status=TaskStatus.RUNNING,
                    to_status=TaskStatus.WAITING_APPROVAL,
                    trace_id=trace_id,
                    reason="policy_gate_requires_approval",
                )
                task = await self._stores.task_store.get_task(task_id)
                if task is None:
                    return

            if task.status == TaskStatus.WAITING_APPROVAL:
                await service._write_state_transition(
                    task_id=task_id,
                    from_status=TaskStatus.WAITING_APPROVAL,
                    to_status=TaskStatus.REJECTED,
                    trace_id=trace_id,
                    reason=reason,
                )
                return

            if task.status not in TERMINAL_STATES:
                await service._write_state_transition(
                    task_id=task_id,
                    from_status=task.status,
                    to_status=TaskStatus.REJECTED,
                    trace_id=trace_id,
                    reason=reason,
                )
        except Exception as exc:  # pragma: no cover - 防御性兜底
            log.warning(
                "orchestrator_force_reject_error",
                task_id=task_id,
                reason=reason,
                error_type=type(exc).__name__,
            )

    async def _ensure_task_waiting(
        self,
        *,
        task_id: str,
        trace_id: str,
        status: str,
        reason: str,
    ) -> None:
        task = await self._stores.task_store.get_task(task_id)
        if task is None or task.status in TERMINAL_STATES:
            return
        target = {
            "waiting_input": TaskStatus.WAITING_INPUT,
            "waiting_approval": TaskStatus.WAITING_APPROVAL,
            "paused": TaskStatus.PAUSED,
        }.get(status)
        if target is None:
            return

        service = TaskService(self._stores, self._sse_hub)
        try:
            if task.status == TaskStatus.CREATED:
                await service._write_state_transition(
                    task_id=task_id,
                    from_status=TaskStatus.CREATED,
                    to_status=TaskStatus.RUNNING,
                    trace_id=trace_id,
                    reason="orchestrator_bootstrap",
                )
                task = await self._stores.task_store.get_task(task_id)
                if task is None:
                    return
            if task.status == TaskStatus.RUNNING:
                await service._write_state_transition(
                    task_id=task_id,
                    from_status=TaskStatus.RUNNING,
                    to_status=target,
                    trace_id=trace_id,
                    reason=reason,
                )
        except Exception as exc:  # pragma: no cover - 防御性兜底
            log.warning(
                "orchestrator_force_waiting_error",
                task_id=task_id,
                reason=reason,
                error_type=type(exc).__name__,
            )
