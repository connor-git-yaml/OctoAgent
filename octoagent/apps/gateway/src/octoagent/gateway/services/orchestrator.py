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
    A2AConversation,
    A2AConversationStatus,
    A2AMessageAuditPayload,
    A2AMessageDirection,
    A2AMessageRecord,
    ActorType,
    AgentRuntime,
    AgentRuntimeRole,
    AgentSession,
    AgentSessionKind,
    DelegationResult,
    DelegationTargetKind,
    DispatchEnvelope,
    Event,
    EventCausality,
    EventType,
    OrchestratorDecisionPayload,
    OrchestratorRequest,
    RiskLevel,
    TaskHeartbeatPayload,
    TaskStatus,
    WorkerDispatchedPayload,
    WorkerExecutionStatus,
    WorkerResult,
    WorkerReturnedPayload,
    WorkerSession,
    WorkStatus,
)
from octoagent.core.store import StoreGroup
from octoagent.policy.models import ApprovalDecision, ApprovalStatus
from octoagent.protocol import (
    build_cancel_message,
    build_error_message,
    build_heartbeat_message,
    build_result_message,
    build_task_message,
    build_update_message,
    dispatch_envelope_from_task_message,
)
from octoagent.protocol.models import A2AMessage
from ulid import ULID

from .agent_context import (
    build_agent_runtime_id,
    build_agent_session_id,
    build_scope_aware_session_id,
)
from .runtime_control import (
    RUNTIME_CONTEXT_JSON_KEY,
    RUNTIME_CONTEXT_KEY,
    encode_runtime_context,
    runtime_context_from_metadata,
)
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
            str(request.metadata.get("approval_id", "")).strip()
            or str(request.metadata.get("approval_token", "")).strip()
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
        a2a_observer=None,
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
            a2a_observer=a2a_observer,
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
        if execution_console is not None and hasattr(execution_console, "bind_a2a_notifier"):
            execution_console.bind_a2a_notifier(self)

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
                a2a_observer=self,
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
                a2a_observer=self,
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
                a2a_observer=self,
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
                a2a_observer=self,
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
        metadata: dict[str, Any] | None = None,
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
        envelope, a2a_conversation = await self._prepare_a2a_dispatch(
            envelope,
            work_id=work_id,
            worker_id=worker.worker_id if worker is not None else "",
        )
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
            a2a_conversation = await self._persist_a2a_terminal_message(
                conversation=a2a_conversation,
                envelope=envelope,
                result=result,
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
                        metadata={
                            "a2a_conversation_id": a2a_conversation.a2a_conversation_id,
                            "source_agent_runtime_id": a2a_conversation.source_agent_runtime_id,
                            "source_agent_session_id": a2a_conversation.source_agent_session_id,
                            "target_agent_runtime_id": a2a_conversation.target_agent_runtime_id,
                            "target_agent_session_id": a2a_conversation.target_agent_session_id,
                            "a2a_message_count": a2a_conversation.message_count,
                        },
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

        a2a_conversation = await self._persist_a2a_terminal_message(
            conversation=a2a_conversation,
            envelope=envelope,
            result=result,
        )
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
                        "a2a_conversation_id": a2a_conversation.a2a_conversation_id,
                        "source_agent_runtime_id": a2a_conversation.source_agent_runtime_id,
                        "source_agent_session_id": a2a_conversation.source_agent_session_id,
                        "target_agent_runtime_id": a2a_conversation.target_agent_runtime_id,
                        "target_agent_session_id": a2a_conversation.target_agent_session_id,
                        "a2a_message_count": a2a_conversation.message_count,
                    },
                ),
            )

        if result.status == WorkerExecutionStatus.FAILED:
            await self._ensure_task_failed(task_id, trace_id, result.summary)

        return result

    async def _prepare_a2a_dispatch(
        self,
        envelope: DispatchEnvelope,
        *,
        work_id: str,
        worker_id: str,
    ) -> tuple[DispatchEnvelope, A2AConversation]:
        runtime_context = envelope.runtime_context or runtime_context_from_metadata(
            envelope.metadata
        )
        runtime_metadata = dict(runtime_context.metadata) if runtime_context is not None else {}
        task = await self._stores.task_store.get_task(envelope.task_id)
        context_frame_id = self._first_non_empty(
            runtime_context.context_frame_id if runtime_context is not None else "",
            str(envelope.metadata.get("context_frame_id", "")),
        )
        source_frame = (
            await self._stores.agent_context_store.get_context_frame(context_frame_id)
            if context_frame_id
            else None
        )
        project_id = self._first_non_empty(
            runtime_context.project_id if runtime_context is not None else "",
            source_frame.project_id if source_frame is not None else "",
            str(envelope.metadata.get("project_id", "")),
        )
        workspace_id = self._first_non_empty(
            runtime_context.workspace_id if runtime_context is not None else "",
            source_frame.workspace_id if source_frame is not None else "",
            str(envelope.metadata.get("workspace_id", "")),
        )
        source_agent_profile_id = self._first_non_empty(
            runtime_context.agent_profile_id if runtime_context is not None else "",
            source_frame.agent_profile_id if source_frame is not None else "",
            str(envelope.metadata.get("agent_profile_id", "")),
        )
        source_agent_runtime_id = self._first_non_empty(
            source_frame.agent_runtime_id if source_frame is not None else "",
            str(runtime_metadata.get("source_agent_runtime_id", "")),
            str(runtime_metadata.get("agent_runtime_id", "")),
            str(envelope.metadata.get("source_agent_runtime_id", "")),
            str(envelope.metadata.get("agent_runtime_id", "")),
        )
        source_agent_session_id = self._first_non_empty(
            source_frame.agent_session_id if source_frame is not None else "",
            str(runtime_metadata.get("source_agent_session_id", "")),
            str(runtime_metadata.get("agent_session_id", "")),
            str(envelope.metadata.get("source_agent_session_id", "")),
            str(envelope.metadata.get("agent_session_id", "")),
        )
        requested_worker_profile_id = str(
            envelope.metadata.get("requested_worker_profile_id", "")
        ).strip()
        worker_capability_hint = self._first_non_empty(
            str(envelope.metadata.get("selected_worker_type", "")),
            str(envelope.metadata.get("worker_capability", "")),
            envelope.worker_capability,
        )
        source_runtime = await self._ensure_a2a_agent_runtime(
            agent_runtime_id=source_agent_runtime_id,
            role=AgentRuntimeRole.BUTLER,
            project_id=project_id,
            workspace_id=workspace_id,
            agent_profile_id=source_agent_profile_id,
            worker_profile_id="",
            worker_capability="",
        )
        legacy_session_id = self._first_non_empty(
            runtime_context.session_id if runtime_context is not None else "",
            build_scope_aware_session_id(
                task,
                project_id=project_id,
                workspace_id=workspace_id,
            )
            if task is not None
            else "",
            envelope.task_id,
        )
        source_session = await self._ensure_a2a_agent_session(
            agent_session_id=source_agent_session_id,
            agent_runtime=source_runtime,
            kind=AgentSessionKind.BUTLER_MAIN,
            project_id=project_id,
            workspace_id=workspace_id,
            surface=(
                runtime_context.surface
                if runtime_context is not None and runtime_context.surface
                else (task.requester.channel if task is not None else "chat")
            ),
            thread_id=task.thread_id if task is not None else "",
            legacy_session_id=legacy_session_id,
            work_id="",
            task_id=envelope.task_id,
            a2a_conversation_id="",
            parent_agent_session_id="",
        )
        target_runtime = await self._ensure_a2a_agent_runtime(
            agent_runtime_id=str(envelope.metadata.get("target_agent_runtime_id", "")),
            role=AgentRuntimeRole.WORKER,
            project_id=project_id,
            workspace_id=workspace_id,
            agent_profile_id=source_agent_profile_id,
            worker_profile_id=requested_worker_profile_id,
            worker_capability=worker_capability_hint,
        )
        conversation_id = self._first_non_empty(
            str(envelope.metadata.get("a2a_conversation_id", "")),
            work_id,
            f"a2a|task:{envelope.task_id}|dispatch:{envelope.dispatch_id}",
        )
        target_session = await self._ensure_a2a_agent_session(
            agent_session_id=str(envelope.metadata.get("target_agent_session_id", "")),
            agent_runtime=target_runtime,
            kind=AgentSessionKind.WORKER_INTERNAL,
            project_id=project_id,
            workspace_id=workspace_id,
            surface=(
                runtime_context.surface
                if runtime_context is not None and runtime_context.surface
                else (task.requester.channel if task is not None else "chat")
            ),
            thread_id=task.thread_id if task is not None else "",
            legacy_session_id=legacy_session_id,
            work_id=work_id or envelope.task_id,
            task_id=envelope.task_id,
            a2a_conversation_id=conversation_id,
            parent_agent_session_id=source_session.agent_session_id,
        )
        source_agent_uri = self._agent_uri("butler.main")
        target_agent_uri = self._agent_uri(worker_id or f"worker.{envelope.worker_capability}")
        existing_conversation = await self._stores.a2a_store.get_conversation(conversation_id)
        conversation = (
            existing_conversation
            if existing_conversation is not None
            else A2AConversation(
                a2a_conversation_id=conversation_id,
                task_id=envelope.task_id,
                work_id=work_id,
                project_id=project_id,
                workspace_id=workspace_id,
                source_agent_runtime_id=source_runtime.agent_runtime_id,
                source_agent_session_id=source_session.agent_session_id,
                target_agent_runtime_id=target_runtime.agent_runtime_id,
                target_agent_session_id=target_session.agent_session_id,
                source_agent=source_agent_uri,
                target_agent=target_agent_uri,
                context_frame_id=context_frame_id,
                trace_id=envelope.trace_id,
            )
        )
        message_metadata = {
            **dict(envelope.metadata),
            "a2a_conversation_id": conversation_id,
            "a2a_context_id": conversation_id,
            "source_agent_runtime_id": source_runtime.agent_runtime_id,
            "source_agent_session_id": source_session.agent_session_id,
            "target_agent_runtime_id": target_runtime.agent_runtime_id,
            "target_agent_session_id": target_session.agent_session_id,
            "agent_runtime_id": target_runtime.agent_runtime_id,
            "agent_session_id": target_session.agent_session_id,
            "parent_agent_session_id": source_session.agent_session_id,
            "context_frame_id": context_frame_id,
            "requested_worker_profile_id": requested_worker_profile_id,
        }
        updated_runtime_context = (
            runtime_context.model_copy(
                update={
                    "metadata": {
                        **runtime_metadata,
                        "a2a_conversation_id": conversation_id,
                        "source_agent_runtime_id": source_runtime.agent_runtime_id,
                        "source_agent_session_id": source_session.agent_session_id,
                        "target_agent_runtime_id": target_runtime.agent_runtime_id,
                        "target_agent_session_id": target_session.agent_session_id,
                        "agent_runtime_id": target_runtime.agent_runtime_id,
                        "agent_session_id": target_session.agent_session_id,
                        "parent_agent_session_id": source_session.agent_session_id,
                    }
                }
            )
            if runtime_context is not None
            else None
        )
        if updated_runtime_context is not None:
            message_metadata[RUNTIME_CONTEXT_KEY] = updated_runtime_context.model_dump(mode="json")
            message_metadata[RUNTIME_CONTEXT_JSON_KEY] = encode_runtime_context(
                updated_runtime_context
            )
        outbound_envelope = envelope.model_copy(
            update={
                "runtime_context": updated_runtime_context,
                "metadata": message_metadata,
            }
        )
        message = build_task_message(
            outbound_envelope,
            context_id=conversation_id,
            from_agent=source_agent_uri,
            to_agent=target_agent_uri,
        )
        conversation = conversation.model_copy(
            update={
                "task_id": envelope.task_id,
                "work_id": work_id,
                "project_id": project_id,
                "workspace_id": workspace_id,
                "source_agent_runtime_id": source_runtime.agent_runtime_id,
                "source_agent_session_id": source_session.agent_session_id,
                "target_agent_runtime_id": target_runtime.agent_runtime_id,
                "target_agent_session_id": target_session.agent_session_id,
                "source_agent": source_agent_uri,
                "target_agent": target_agent_uri,
                "context_frame_id": context_frame_id,
                "status": A2AConversationStatus.ACTIVE,
                "trace_id": envelope.trace_id,
                "metadata": {
                    **conversation.metadata,
                    "worker_capability": envelope.worker_capability,
                    "worker_id": worker_id,
                    "requested_worker_profile_id": requested_worker_profile_id,
                },
                "updated_at": datetime.now(UTC),
                "completed_at": None,
            }
        )
        await self._stores.a2a_store.save_conversation(conversation)
        message_record = await self._save_a2a_message(
            conversation=conversation,
            message=message,
            direction=A2AMessageDirection.OUTBOUND,
            source_agent_runtime_id=source_runtime.agent_runtime_id,
            source_agent_session_id=source_session.agent_session_id,
            target_agent_runtime_id=target_runtime.agent_runtime_id,
            target_agent_session_id=target_session.agent_session_id,
            from_agent=source_agent_uri,
            to_agent=target_agent_uri,
        )
        conversation = conversation.model_copy(
            update={
                "request_message_id": message_record.a2a_message_id,
                "latest_message_id": message_record.a2a_message_id,
                "latest_message_type": message.type.value,
                "message_count": message_record.message_seq,
                "updated_at": datetime.now(UTC),
            }
        )
        await self._stores.a2a_store.save_conversation(conversation)
        await self._stores.agent_context_store.save_agent_session(
            target_session.model_copy(
                update={
                    "a2a_conversation_id": conversation_id,
                    "updated_at": datetime.now(UTC),
                }
            )
        )
        await self._write_a2a_message_event(
            task_id=envelope.task_id,
            trace_id=envelope.trace_id,
            event_type=EventType.A2A_MESSAGE_SENT,
            record=message_record,
        )
        restored = dispatch_envelope_from_task_message(message)
        return restored.model_copy(
            update={
                "runtime_context": updated_runtime_context,
                "metadata": {
                    **restored.metadata,
                    **message_metadata,
                    "a2a_message_id": message.message_id,
                    "a2a_to_agent": message.to_agent,
                },
            }
        ), conversation

    async def _persist_a2a_terminal_message(
        self,
        *,
        conversation: A2AConversation,
        envelope: DispatchEnvelope,
        result: WorkerResult,
    ) -> A2AConversation:
        if result.status == WorkerExecutionStatus.SUCCEEDED:
            message = build_result_message(
                result,
                context_id=conversation.a2a_conversation_id,
                trace_id=envelope.trace_id,
                from_agent=conversation.target_agent,
                to_agent=conversation.source_agent,
            )
        else:
            message = build_error_message(
                result,
                context_id=conversation.a2a_conversation_id,
                trace_id=envelope.trace_id,
                from_agent=conversation.target_agent,
                to_agent=conversation.source_agent,
            )
        return await self._persist_a2a_message_and_event(
            task_id=result.task_id,
            trace_id=envelope.trace_id,
            conversation=conversation,
            message=message,
            direction=A2AMessageDirection.INBOUND,
            source_agent_runtime_id=conversation.target_agent_runtime_id,
            source_agent_session_id=conversation.target_agent_session_id,
            target_agent_runtime_id=conversation.source_agent_runtime_id,
            target_agent_session_id=conversation.source_agent_session_id,
            from_agent=message.from_agent,
            to_agent=message.to_agent,
            event_type=EventType.A2A_MESSAGE_RECEIVED,
            status=self._a2a_status_from_worker_result(result),
            completed=True,
            metadata_updates={
                "worker_id": result.worker_id,
                "backend": result.backend,
                "tool_profile": result.tool_profile,
            },
        )

    async def record_worker_heartbeat(
        self,
        *,
        envelope: DispatchEnvelope,
        session: WorkerSession,
        summary: str = "",
        state: TaskStatus = TaskStatus.RUNNING,
    ) -> None:
        conversation = await self._resolve_a2a_conversation(
            task_id=envelope.task_id,
            conversation_id=str(envelope.metadata.get("a2a_conversation_id", "")),
            work_id=str(envelope.metadata.get("work_id", "")),
        )
        if conversation is None or conversation.status in {
            A2AConversationStatus.COMPLETED,
            A2AConversationStatus.FAILED,
            A2AConversationStatus.CANCELLED,
        }:
            return
        message = build_heartbeat_message(
            session,
            context_id=conversation.a2a_conversation_id,
            trace_id=envelope.trace_id,
            from_agent=conversation.target_agent,
            to_agent=conversation.source_agent,
            state=state,
            summary=summary,
        )
        await self._persist_a2a_message_and_event(
            task_id=envelope.task_id,
            trace_id=envelope.trace_id,
            conversation=conversation,
            message=message,
            direction=A2AMessageDirection.INBOUND,
            source_agent_runtime_id=conversation.target_agent_runtime_id,
            source_agent_session_id=conversation.target_agent_session_id,
            target_agent_runtime_id=conversation.source_agent_runtime_id,
            target_agent_session_id=conversation.source_agent_session_id,
            from_agent=message.from_agent,
            to_agent=message.to_agent,
            event_type=EventType.A2A_MESSAGE_RECEIVED,
            status=A2AConversationStatus.ACTIVE,
            metadata_updates={
                "worker_id": session.worker_id,
                "backend": session.backend,
                "loop_step": session.loop_step,
                "max_steps": session.max_steps,
                "last_heartbeat_summary": summary,
            },
        )
        await self._append_control_event(
            task_id=envelope.task_id,
            trace_id=envelope.trace_id,
            event_type=EventType.TASK_HEARTBEAT,
            payload=TaskHeartbeatPayload(
                task_id=envelope.task_id,
                trace_id=envelope.trace_id,
                heartbeat_ts=datetime.now(UTC).isoformat(),
                loop_step=session.loop_step,
                note=summary,
            ).model_dump(),
        )

    async def record_waiting_input(
        self,
        *,
        task_id: str,
        session_id: str,
        prompt: str,
        request_id: str,
        approval_id: str | None,
        worker_id: str,
        work_id: str = "",
    ) -> None:
        conversation = await self._resolve_a2a_conversation(
            task_id=task_id,
            work_id=work_id,
        )
        if conversation is None:
            return
        message = build_update_message(
            task_id=task_id,
            context_id=conversation.a2a_conversation_id,
            trace_id=f"trace-{task_id}",
            from_agent=conversation.target_agent,
            to_agent=conversation.source_agent,
            state=TaskStatus.WAITING_INPUT,
            summary="worker requested human input",
            requested_input=prompt,
            idempotency_key=f"{task_id}:{request_id}:update:waiting_input",
            message_id=f"{task_id}-input-request-{request_id}",
        )
        await self._persist_a2a_message_and_event(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            conversation=conversation,
            message=message,
            direction=A2AMessageDirection.INBOUND,
            source_agent_runtime_id=conversation.target_agent_runtime_id,
            source_agent_session_id=conversation.target_agent_session_id,
            target_agent_runtime_id=conversation.source_agent_runtime_id,
            target_agent_session_id=conversation.source_agent_session_id,
            from_agent=message.from_agent,
            to_agent=message.to_agent,
            event_type=EventType.A2A_MESSAGE_RECEIVED,
            status=A2AConversationStatus.WAITING_INPUT,
            metadata_updates={
                "worker_id": worker_id,
                "waiting_input_request_id": request_id,
                "waiting_input_prompt": prompt,
                "waiting_input_approval_id": approval_id or "",
                "waiting_input_session_id": session_id,
            },
        )

    async def record_input_attached(
        self,
        *,
        task_id: str,
        session_id: str,
        request_id: str,
        artifact_id: str,
        actor: str,
        worker_id: str,
        work_id: str = "",
    ) -> None:
        conversation = await self._resolve_a2a_conversation(
            task_id=task_id,
            work_id=work_id,
        )
        if conversation is None:
            return
        message = build_update_message(
            task_id=task_id,
            context_id=conversation.a2a_conversation_id,
            trace_id=f"trace-{task_id}",
            from_agent=conversation.source_agent,
            to_agent=conversation.target_agent,
            state=TaskStatus.RUNNING,
            summary="human input attached; resume worker execution",
            idempotency_key=f"{task_id}:{request_id}:update:resume",
            message_id=f"{task_id}-input-attached-{request_id}",
        )
        await self._persist_a2a_message_and_event(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            conversation=conversation,
            message=message,
            direction=A2AMessageDirection.OUTBOUND,
            source_agent_runtime_id=conversation.source_agent_runtime_id,
            source_agent_session_id=conversation.source_agent_session_id,
            target_agent_runtime_id=conversation.target_agent_runtime_id,
            target_agent_session_id=conversation.target_agent_session_id,
            from_agent=message.from_agent,
            to_agent=message.to_agent,
            event_type=EventType.A2A_MESSAGE_SENT,
            status=A2AConversationStatus.ACTIVE,
            metadata_updates={
                "worker_id": worker_id,
                "waiting_input_request_id": request_id,
                "last_input_artifact_id": artifact_id,
                "last_input_actor": actor,
                "last_input_session_id": session_id,
            },
        )

    async def record_cancel(
        self,
        *,
        task_id: str,
        reason: str,
        actor: str = "user:web",
        work_id: str = "",
    ) -> None:
        conversations = await self._resolve_a2a_conversations(
            task_id=task_id,
            work_id=work_id,
        )
        if not conversations:
            return
        for index, conversation in enumerate(conversations, start=1):
            message = build_cancel_message(
                task_id=task_id,
                context_id=conversation.a2a_conversation_id,
                trace_id=f"trace-{task_id}",
                from_agent=conversation.source_agent,
                to_agent=conversation.target_agent,
                reason=reason,
                idempotency_key=f"{task_id}:cancel:{index}:{actor}",
            )
            await self._persist_a2a_message_and_event(
                task_id=task_id,
                trace_id=f"trace-{task_id}",
                conversation=conversation,
                message=message,
                direction=A2AMessageDirection.OUTBOUND,
                source_agent_runtime_id=conversation.source_agent_runtime_id,
                source_agent_session_id=conversation.source_agent_session_id,
                target_agent_runtime_id=conversation.target_agent_runtime_id,
                target_agent_session_id=conversation.target_agent_session_id,
                from_agent=message.from_agent,
                to_agent=message.to_agent,
                event_type=EventType.A2A_MESSAGE_SENT,
                status=A2AConversationStatus.CANCELLED,
                completed=True,
                metadata_updates={
                    "last_cancel_actor": actor,
                    "last_cancel_reason": reason,
                },
            )

    async def _persist_a2a_message_and_event(
        self,
        *,
        task_id: str,
        trace_id: str,
        conversation: A2AConversation,
        message: A2AMessage,
        direction: A2AMessageDirection,
        source_agent_runtime_id: str,
        source_agent_session_id: str,
        target_agent_runtime_id: str,
        target_agent_session_id: str,
        from_agent: str,
        to_agent: str,
        event_type: EventType,
        status: A2AConversationStatus | None = None,
        completed: bool = False,
        metadata_updates: dict[str, Any] | None = None,
    ) -> A2AConversation:
        message_record = await self._save_a2a_message(
            conversation=conversation,
            message=message,
            direction=direction,
            source_agent_runtime_id=source_agent_runtime_id,
            source_agent_session_id=source_agent_session_id,
            target_agent_runtime_id=target_agent_runtime_id,
            target_agent_session_id=target_agent_session_id,
            from_agent=from_agent,
            to_agent=to_agent,
        )
        now = datetime.now(UTC)
        update_fields: dict[str, Any] = {
            "latest_message_id": message_record.a2a_message_id,
            "latest_message_type": message.type.value,
            "message_count": message_record.message_seq,
            "updated_at": now,
            "metadata": {
                **conversation.metadata,
                **(metadata_updates or {}),
            },
        }
        if status is not None:
            update_fields["status"] = status
            if status in {
                A2AConversationStatus.ACTIVE,
                A2AConversationStatus.WAITING_INPUT,
            } and not completed:
                update_fields["completed_at"] = None
        if completed:
            update_fields["completed_at"] = now
        updated_conversation = conversation.model_copy(update=update_fields)
        await self._stores.a2a_store.save_conversation(updated_conversation)
        await self._touch_a2a_agent_session(
            agent_session_id=source_agent_session_id,
            a2a_conversation_id=updated_conversation.a2a_conversation_id,
            record=message_record,
            peer_agent_session_id=target_agent_session_id,
        )
        await self._touch_a2a_agent_session(
            agent_session_id=target_agent_session_id,
            a2a_conversation_id=updated_conversation.a2a_conversation_id,
            record=message_record,
            peer_agent_session_id=source_agent_session_id,
        )
        await self._write_a2a_message_event(
            task_id=task_id,
            trace_id=trace_id,
            event_type=event_type,
            record=message_record,
        )
        return updated_conversation

    async def _resolve_a2a_conversation(
        self,
        *,
        task_id: str,
        conversation_id: str = "",
        work_id: str = "",
    ) -> A2AConversation | None:
        conversations = await self._resolve_a2a_conversations(
            task_id=task_id,
            conversation_id=conversation_id,
            work_id=work_id,
        )
        if not conversations:
            return None
        return conversations[0]

    async def _resolve_a2a_conversations(
        self,
        *,
        task_id: str,
        conversation_id: str = "",
        work_id: str = "",
    ) -> list[A2AConversation]:
        if conversation_id.strip():
            conversation = await self._stores.a2a_store.get_conversation(conversation_id.strip())
            if conversation is not None:
                return [conversation]
        if work_id.strip():
            conversation = await self._stores.a2a_store.get_conversation_for_work(work_id.strip())
            if conversation is not None:
                return [conversation]
        conversations = await self._stores.a2a_store.list_conversations(
            task_id=task_id,
            limit=None,
        )
        if not conversations:
            return []
        active = [
            item
            for item in conversations
            if item.status
            in {
                A2AConversationStatus.ACTIVE,
                A2AConversationStatus.WAITING_INPUT,
            }
        ]
        return active or [conversations[0]]

    async def _touch_a2a_agent_session(
        self,
        *,
        agent_session_id: str,
        a2a_conversation_id: str,
        record: A2AMessageRecord,
        peer_agent_session_id: str,
    ) -> None:
        if not agent_session_id:
            return
        session = await self._stores.agent_context_store.get_agent_session(agent_session_id)
        if session is None:
            return
        await self._stores.agent_context_store.save_agent_session(
            session.model_copy(
                update={
                    "a2a_conversation_id": a2a_conversation_id,
                    "metadata": {
                        **session.metadata,
                        "last_a2a_message_id": record.a2a_message_id,
                        "last_a2a_message_type": record.message_type,
                        "last_a2a_direction": record.direction.value,
                        "last_a2a_protocol_message_id": record.protocol_message_id,
                        "peer_agent_session_id": peer_agent_session_id,
                    },
                    "updated_at": datetime.now(UTC),
                }
            )
        )

    async def _save_a2a_message(
        self,
        *,
        conversation: A2AConversation,
        message: A2AMessage,
        direction: A2AMessageDirection,
        source_agent_runtime_id: str,
        source_agent_session_id: str,
        target_agent_runtime_id: str,
        target_agent_session_id: str,
        from_agent: str,
        to_agent: str,
    ) -> A2AMessageRecord:
        return await self._stores.a2a_store.append_message(
            conversation.a2a_conversation_id,
            lambda message_seq: A2AMessageRecord(
                a2a_message_id=str(ULID()),
                a2a_conversation_id=conversation.a2a_conversation_id,
                message_seq=message_seq,
                task_id=conversation.task_id,
                work_id=conversation.work_id,
                project_id=conversation.project_id,
                workspace_id=conversation.workspace_id,
                source_agent_runtime_id=source_agent_runtime_id,
                source_agent_session_id=source_agent_session_id,
                target_agent_runtime_id=target_agent_runtime_id,
                target_agent_session_id=target_agent_session_id,
                direction=direction,
                message_type=message.type.value,
                protocol_message_id=message.message_id,
                from_agent=from_agent,
                to_agent=to_agent,
                idempotency_key=message.idempotency_key,
                payload=message.payload.model_dump(mode="json"),
                trace=message.trace.model_dump(mode="json"),
                metadata=message.metadata.model_dump(mode="json"),
                raw_message=message.model_dump(mode="json"),
                created_at=datetime.now(UTC),
            ),
        )

    async def _write_a2a_message_event(
        self,
        *,
        task_id: str,
        trace_id: str,
        event_type: EventType,
        record: A2AMessageRecord,
    ) -> None:
        payload = A2AMessageAuditPayload(
            a2a_conversation_id=record.a2a_conversation_id,
            a2a_message_id=record.a2a_message_id,
            protocol_message_id=record.protocol_message_id,
            message_type=record.message_type,
            from_agent=record.from_agent,
            to_agent=record.to_agent,
            source_agent_runtime_id=record.source_agent_runtime_id,
            source_agent_session_id=record.source_agent_session_id,
            target_agent_runtime_id=record.target_agent_runtime_id,
            target_agent_session_id=record.target_agent_session_id,
            work_id=record.work_id,
            direction=record.direction.value,
        )
        await self._append_control_event(
            task_id=task_id,
            trace_id=trace_id,
            event_type=event_type,
            payload=payload.model_dump(),
        )

    async def _ensure_a2a_agent_runtime(
        self,
        *,
        agent_runtime_id: str,
        role: AgentRuntimeRole,
        project_id: str,
        workspace_id: str,
        agent_profile_id: str,
        worker_profile_id: str,
        worker_capability: str,
    ) -> AgentRuntime:
        runtime_id = agent_runtime_id.strip() or build_agent_runtime_id(
            role=role,
            project_id=project_id,
            workspace_id=workspace_id,
            agent_profile_id=agent_profile_id,
            worker_profile_id=worker_profile_id,
            worker_capability=worker_capability,
        )
        existing = await self._stores.agent_context_store.get_agent_runtime(runtime_id)
        if existing is not None:
            return existing
        agent_profile = (
            await self._stores.agent_context_store.get_agent_profile(agent_profile_id)
            if agent_profile_id
            else None
        )
        worker_profile = (
            await self._stores.agent_context_store.get_worker_profile(worker_profile_id)
            if worker_profile_id
            else None
        )
        if role is AgentRuntimeRole.BUTLER:
            name = agent_profile.name if agent_profile is not None else "Butler"
            persona_summary = (
                agent_profile.persona_summary if agent_profile is not None else "用户主会话协调者。"
            )
        else:
            name = (
                worker_profile.name
                if worker_profile is not None
                else f"{worker_capability or 'general'} worker"
            )
            persona_summary = (
                worker_profile.summary
                if worker_profile is not None
                else f"执行 {worker_capability or 'general'} delegation。"
            )
        runtime = AgentRuntime(
            agent_runtime_id=runtime_id,
            project_id=project_id,
            workspace_id=workspace_id,
            agent_profile_id=agent_profile_id,
            worker_profile_id=worker_profile_id,
            role=role,
            name=name,
            persona_summary=persona_summary,
            metadata={
                "created_by": "orchestrator.wave2",
                "worker_capability": worker_capability,
            },
        )
        await self._stores.agent_context_store.save_agent_runtime(runtime)
        return runtime

    async def _ensure_a2a_agent_session(
        self,
        *,
        agent_session_id: str,
        agent_runtime: AgentRuntime,
        kind: AgentSessionKind,
        project_id: str,
        workspace_id: str,
        surface: str,
        thread_id: str,
        legacy_session_id: str,
        work_id: str,
        task_id: str,
        a2a_conversation_id: str,
        parent_agent_session_id: str,
    ) -> AgentSession:
        session_id = agent_session_id.strip() or build_agent_session_id(
            agent_runtime_id=agent_runtime.agent_runtime_id,
            kind=kind,
            legacy_session_id=legacy_session_id,
            work_id=work_id,
            task_id=task_id,
        )
        existing = await self._stores.agent_context_store.get_agent_session(session_id)
        if existing is not None:
            return existing
        session = AgentSession(
            agent_session_id=session_id,
            agent_runtime_id=agent_runtime.agent_runtime_id,
            kind=kind,
            project_id=project_id,
            workspace_id=workspace_id,
            surface=surface or "chat",
            thread_id=thread_id,
            legacy_session_id=legacy_session_id,
            parent_agent_session_id=parent_agent_session_id,
            work_id=work_id,
            a2a_conversation_id=a2a_conversation_id,
            metadata={"created_by": "orchestrator.wave2"},
        )
        await self._stores.agent_context_store.save_agent_session(session)
        return session

    @staticmethod
    def _a2a_status_from_worker_result(result: WorkerResult) -> A2AConversationStatus:
        if result.status == WorkerExecutionStatus.SUCCEEDED:
            return A2AConversationStatus.COMPLETED
        if result.status == WorkerExecutionStatus.CANCELLED:
            return A2AConversationStatus.CANCELLED
        return A2AConversationStatus.FAILED

    @staticmethod
    def _first_non_empty(*values: object) -> str:
        for value in values:
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    @staticmethod
    def _agent_uri(label: str) -> str:
        normalized = "".join(
            ch if ch.isalnum() or ch in "._/-" else "-"
            for ch in label.strip().lower()
        ).strip("-")
        return f"agent://{normalized or 'unknown'}"

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
