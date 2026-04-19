"""Orchestrator 控制平面服务（Feature 008）。

最小职责：
1. 请求封装与高风险 gate
2. 单 worker 路由与派发
3. 控制平面事件写入（ORCH_DECISION/WORKER_DISPATCHED/WORKER_RETURNED）
4. worker 结果回传与失败分类
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import structlog
from octoagent.core.models.agent_context import resolve_permission_preset
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
    AgentDecision,
    AgentDecisionMode,
    DelegationResult,
    DelegationTargetKind,
    DispatchEnvelope,
    Event,
    EventCausality,
    EventType,
    ExecutionBackend,
    ExecutionSessionState,
    HumanInputPolicy,
    OrchestratorDecisionPayload,
    OrchestratorRequest,
    RecallPlan,
    RiskLevel,
    SessionContextState,
    TaskHeartbeatPayload,
    TaskStatus,
    ToolIndexQuery,
    ToolIndexSelectedPayload,
    TurnExecutorKind,
    Work,
    WorkerDispatchedPayload,
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
from octoagent.provider import ModelCallResult, TokenUsage
from ulid import ULID

from .agent_context import (
    _SESSION_TRANSCRIPT_LIMIT_DEFAULT,
    _dynamic_transcript_limit,
    AgentContextService,
    build_scope_aware_session_id,
)
from .agent_decision import (
    _is_trivial_direct_answer,
    build_runtime_hint_bundle,
    decide_agent_routing,
    render_behavior_system_block,
    render_runtime_hint_block,
)
from .connection_metadata import (
    resolve_delegation_target_profile_id,
    resolve_session_owner_profile_id,
)
from .execution_console import ExecutionConsoleService
from .execution_context import ExecutionRuntimeContext
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


class _InlineReplyLLMService:
    """把 orchestrator 的确定性收口回复接入标准 Task LLM 主链。"""

    def __init__(
        self,
        content: str,
        *,
        model_alias: str = "inline-reply",
        provider: str = "inline",
    ) -> None:
        self._content = content
        self._model_alias = model_alias
        self._provider = provider

    async def call(
        self,
        prompt_or_messages,
        model_alias: str | None = None,
        *,
        task_id: str | None = None,
        trace_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        worker_capability: str | None = None,
        tool_profile: str | None = None,
    ) -> ModelCallResult:
        del prompt_or_messages, task_id, trace_id, metadata, worker_capability, tool_profile
        resolved_alias = (model_alias or self._model_alias).strip() or self._model_alias
        return ModelCallResult(
            content=self._content,
            model_alias=resolved_alias,
            model_name="agent-inline",
            provider=self._provider,
            duration_ms=1,
            token_usage=TokenUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
            cost_usd=0.0,
            cost_unavailable=False,
            is_fallback=False,
            fallback_reason="",
        )


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


@dataclass(frozen=True)
class _RecentWorkerLaneCandidate:
    """主 Agent 最近可复用的 specialist worker lane。"""

    worker_type: str
    requested_worker_profile_id: str
    topic: str
    summary: str
    source_task_id: str
    source_work_id: str


@dataclass(frozen=True)
class _OwnerSelfWorkerExecutionChoice:
    """会话 owner 自执行 worker 路径。"""

    profile_id: str
    profile_name: str
    worker_type: str
    model_alias: str
    tool_profile: str
    source_kind: str


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
        project_root: Path | None = None,
        notification_service: Any | None = None,
    ) -> None:
        self._stores = store_group
        self._sse_hub = sse_hub
        self._llm_service = llm_service
        _env_root = os.environ.get("OCTOAGENT_PROJECT_ROOT", "").strip()
        self._project_root = (
            project_root or (Path(_env_root) if _env_root else Path.cwd())
        ).resolve()
        self._policy_gate = policy_gate or OrchestratorPolicyGate(approval_manager=approval_manager)
        self._router = router or SingleWorkerRouter()
        self._delegation_plane = delegation_plane
        self._execution_console = execution_console or ExecutionConsoleService(
            store_group=store_group,
            sse_hub=sse_hub,
            approval_manager=approval_manager,
        )
        if hasattr(self._execution_console, "bind_a2a_notifier"):
            self._execution_console.bind_a2a_notifier(self)

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
                execution_console=self._execution_console,
                a2a_observer=self,
            ),
        ]
        self._workers: dict[str, OrchestratorWorker] = {
            worker.capability: worker for worker in default_workers
        }
        if workers:
            self._workers.update(workers)

        # Feature 064 P1-B: Subagent 结果注入队列
        # key = parent_task_id, value = asyncio.Queue 存放结果摘要
        self._subagent_result_queues: dict[str, asyncio.Queue] = {}

        # Feature 064 P2-B: 通知服务（可选注入，不注入时不影响现有行为）
        self._notification_service = notification_service

    # ----- Feature 064 P2-B: 通知服务 -----

    async def _notify_state_change(
        self,
        *,
        task_id: str,
        from_status: str,
        to_status: str,
        reason: str = "",
    ) -> None:
        """Task 状态变更时调用 NotificationService（FR-064-32）。

        降级安全：通知失败仅记录日志，不影响 Task 执行（Constitution #6）。
        """
        if self._notification_service is None:
            return
        try:
            # 获取 Task 信息用于通知 payload
            task = await self._stores.task_store.get_task(task_id)
            task_title = ""
            if task is not None:
                task_title = task.title or task_id

            payload = {
                "task_id": task_id,
                "task_title": task_title,
                "from_status": from_status,
                "to_status": to_status,
                "reason": reason,
            }

            await self._notification_service.notify_task_state_change(
                task_id=task_id,
                event_type=f"STATE_TRANSITION:{to_status}",
                payload=payload,
            )
        except Exception:
            log.warning(
                "notification_state_change_failed",
                task_id=task_id,
                to_status=to_status,
                exc_info=True,
            )

    # ----- Feature 064 P1-B: SubagentResultQueue -----

    async def enqueue_subagent_result(
        self,
        *,
        parent_task_id: str,
        child_task_id: str,
        subagent_name: str,
        status: str,
        summary: str,
        artifact_count: int,
    ) -> None:
        """Subagent 完成后将结果放入队列，父 Worker 在下一步 generate() 前消费。

        同时写入 A2A_MESSAGE_RECEIVED 事件到父 Task（事件冒泡 FR-064-22）。
        """
        if not parent_task_id:
            return

        # 写入 A2A_MESSAGE_RECEIVED 事件到父 Task
        try:
            now = datetime.now(UTC)
            task_seq = await self._stores.event_store.get_next_task_seq(parent_task_id)
            event = Event(
                event_id=f"evt-{ULID()}",
                task_id=parent_task_id,
                task_seq=task_seq,
                ts=now,
                type=EventType.A2A_MESSAGE_RECEIVED,
                actor=ActorType.SYSTEM,
                payload={
                    "source": "subagent",
                    "child_task_id": child_task_id,
                    "subagent_name": subagent_name,
                    "status": status,
                    "summary": summary[:500] if summary else "",
                    "artifact_count": artifact_count,
                },
                trace_id="",
            )
            await self._stores.event_store.append_event(event)
            await self._stores.conn.commit()

            # SSE 双路广播到父 Task 订阅者
            if self._sse_hub:
                await self._sse_hub.broadcast(parent_task_id, event)
        except Exception:
            log.warning(
                "subagent_result_event_failed",
                parent_task_id=parent_task_id,
                child_task_id=child_task_id,
                exc_info=True,
            )

        # 将结果放入 Queue 供父 Worker SkillRunner 消费
        if parent_task_id not in self._subagent_result_queues:
            self._subagent_result_queues[parent_task_id] = asyncio.Queue()

        result_message = (
            f"[Subagent Result] Subagent '{subagent_name}' "
            f"(task: {child_task_id}) completed:\n"
            f"Status: {status}\n"
            f"Summary: {summary}\n"
            f"Artifacts: {artifact_count} items"
        )
        self._subagent_result_queues[parent_task_id].put_nowait(result_message)

    def drain_subagent_results(self, parent_task_id: str) -> list[str]:
        """消费父 Task 的所有待处理 Subagent 结果。

        由 SkillRunner（或 LiteLLMSkillClient）在 generate() 前调用。
        返回按到达顺序排列的结果消息列表。
        """
        queue = self._subagent_result_queues.get(parent_task_id)
        if queue is None or queue.empty():
            return []

        results: list[str] = []
        while not queue.empty():
            try:
                results.append(queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        # 清理空 Queue，防止无界增长
        if queue.empty():
            self._subagent_result_queues.pop(parent_task_id, None)

        return results

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
                status=TaskStatus.FAILED,
                retryable=False,
                summary="dispatch_blocked_by_policy_gate",
                error_type="PolicyGateDenied",
                error_message=gate_decision.reason,
            )

        request = await self._normalize_requested_worker_lens(request)
        owner_self_worker = await self._resolve_owner_self_worker_execution_choice(request)
        if owner_self_worker is not None:
            return await self._dispatch_owner_self_worker_execution(
                request=request,
                gate_decision=gate_decision,
                choice=owner_self_worker,
            )
        request = await self._prepare_single_loop_request(request)
        routing_decision, request_metadata_updates = await self._resolve_routing_decision(request)
        if request_metadata_updates:
            request = request.model_copy(
                update={
                    "metadata": {
                        **dict(request.metadata),
                        **request_metadata_updates,
                    }
                }
            )

        # Feature 065: DELEGATE_GRAPH 路由分支
        if routing_decision is not None and routing_decision.mode is AgentDecisionMode.DELEGATE_GRAPH:
            graph_result = await self._dispatch_delegate_graph(
                request=request,
                gate_decision=gate_decision,
                decision=routing_decision,
            )
            if graph_result is not None:
                return graph_result

        delegated_request = await self._build_delegation_request(
            request=request,
            decision=routing_decision,
        )
        if delegated_request is not None:
            request = delegated_request
            routing_decision = None

        if routing_decision is not None:
            return await self._dispatch_inline_decision(
                request=request,
                gate_decision=gate_decision,
                decision=routing_decision,
            )

        # Phase 1 (Feature 064): Direct Execution
        # routing_decision is None 且无委派请求时，走 Direct Execution Loop
        # 而非 Delegation Plane -> Worker Dispatch
        if self._should_direct_execute(request):
            return await self._dispatch_direct_execution(
                request=request,
                gate_decision=gate_decision,
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
                        status=TaskStatus.FAILED,
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
                status=TaskStatus.FAILED,
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

    async def _normalize_requested_worker_lens(
        self,
        request: OrchestratorRequest,
    ) -> OrchestratorRequest:
        metadata = dict(request.metadata)
        session_owner_profile_id = resolve_session_owner_profile_id(metadata)
        delegation_target_profile_id = resolve_delegation_target_profile_id(metadata)
        explicit_requested_worker_type = str(
            metadata.get("requested_worker_type", "")
        ).strip()
        canonical_worker_type = self._canonical_requested_worker_type(metadata)
        if explicit_requested_worker_type and canonical_worker_type:
            return request
        if canonical_worker_type:
            requested_profile_id = delegation_target_profile_id
            return request.model_copy(
                update={
                    "metadata": {
                        **metadata,
                        "requested_worker_type": canonical_worker_type,
                        **(
                            {"requested_worker_profile_id": requested_profile_id}
                            if requested_profile_id
                            else {}
                        ),
                        "requested_worker_type_source": (
                            "delegation_target_profile_id"
                            if requested_profile_id
                            else (
                                "session_owner_profile_id"
                                if session_owner_profile_id.startswith("singleton:")
                                else "canonical_worker_lens"
                            )
                        ),
                    }
                }
            )
        requested_profile_id = delegation_target_profile_id
        if not requested_profile_id:
            return request
        if self._delegation_plane is None:
            return request
        resolved_worker_type = (
            await self._delegation_plane.capability_pack.resolve_worker_type_for_profile(
                requested_profile_id
            )
        )
        if resolved_worker_type is None:
            return request
        return request.model_copy(
            update={
                "metadata": {
                    **metadata,
                    "requested_worker_type": resolved_worker_type,
                    "requested_worker_profile_id": requested_profile_id,
                    "requested_worker_type_source": "delegation_target_profile_id",
                }
            }
        )

    async def _prepare_single_loop_request(
        self,
        request: OrchestratorRequest,
    ) -> OrchestratorRequest:
        if not self._is_single_loop_main_eligible(request):
            return request
        metadata = dict(request.metadata)
        # 如果 capability pack 已刷新（如 MCP 安装完成），需要重新解析工具列表
        pack_rev = (
            self._delegation_plane.capability_pack.pack_revision
            if self._delegation_plane is not None
            else 0
        )
        stored_rev = metadata.get("_pack_revision", 0)
        if self._metadata_flag(metadata, "single_loop_executor") and pack_rev == stored_rev:
            return request
        worker_type = self._resolve_single_loop_worker_type(request)
        selection = await self._resolve_single_loop_tool_selection(
            request,
            worker_type=worker_type,
        )
        if selection is not None:
            await TaskService(self._stores, self._sse_hub, project_root=self._project_root).append_structured_event(
                task_id=request.task_id,
                event_type=EventType.TOOL_INDEX_SELECTED,
                actor=ActorType.KERNEL,
                payload=ToolIndexSelectedPayload(
                    selection_id=selection.selection_id,
                    backend=selection.backend,
                    is_fallback=selection.is_fallback,
                    query=selection.query.query,
                    selected_tools=selection.selected_tools,
                    hit_count=len(selection.hits),
                    warnings=selection.warnings,
                ).model_dump(mode="json"),
                trace_id=request.trace_id,
                idempotency_key=f"single-loop-tool-index:{selection.selection_id}",
            )
        selected_tools = list(selection.selected_tools) if selection is not None else []
        route_reason = self._join_route_reason(
            request.route_reason,
            f"single_loop_executor=main_{worker_type}",
        )
        if selection is not None:
            route_reason = self._join_route_reason(
                route_reason,
                f"tool_resolution={selection.resolution_mode}",
            )
        recommended_tools = (
            list(selection.recommended_tools)
            if selection is not None and selection.recommended_tools
            else list(selected_tools)
        )
        updated_metadata = {
            **metadata,
            "single_loop_executor": True,
            "single_loop_executor_mode": f"main_{worker_type}",
            "selected_worker_type": worker_type,
            "selected_tools": selected_tools,
            "recommended_tools": recommended_tools,
            "selected_tools_json": json.dumps(recommended_tools, ensure_ascii=False),
            "tool_selection": (
                selection.model_dump(mode="json") if selection is not None else {}
            ),
            "agent_execution_mode": "single_loop",
            "_pack_revision": pack_rev,
        }
        if selection is not None and selection.effective_tool_universe is not None:
            if selection.effective_tool_universe.profile_id:
                updated_metadata.setdefault(
                    "agent_profile_id",
                    selection.effective_tool_universe.profile_id,
                )
        return request.model_copy(
            update={
                "route_reason": route_reason,
                "metadata": updated_metadata,
            }
        )

    def _is_single_loop_main_eligible(self, request: OrchestratorRequest) -> bool:
        if not bool(getattr(self._llm_service, "supports_single_loop_executor", False)):
            return False
        if request.worker_capability not in {"", "llm_generation"}:
            return False
        metadata = request.metadata
        if str(metadata.get("parent_task_id", "")).strip():
            return False
        if str(metadata.get("spawned_by", "")).strip():
            return False
        # 用户明确指定了非 singleton 的自定义 Worker profile 时，不走 single loop
        # 主 Agent 路径，让请求走 Delegation Plane 由 Worker 自己的 persona 处理。
        # singleton:xxx 格式的内置 profile 仍然允许走 single loop。
        requested_profile_id = resolve_delegation_target_profile_id(metadata)
        if requested_profile_id and not requested_profile_id.startswith("singleton:"):
            return False
        requested_worker_type = self._canonical_requested_worker_type(metadata)
        return requested_worker_type in {"", "general", "research", "dev", "ops"}

    @staticmethod
    def _resolve_single_loop_worker_type(request: OrchestratorRequest) -> str:
        requested_worker_type = OrchestratorService._canonical_requested_worker_type(
            request.metadata
        )
        wt = requested_worker_type or "general"
        if wt in {"general", "ops", "research", "dev"}:
            return wt
        return "general"

    @staticmethod
    def _canonical_requested_worker_type(metadata: dict[str, Any]) -> str:
        requested_worker_type = str(metadata.get("requested_worker_type", "")).strip().lower()
        if requested_worker_type:
            return requested_worker_type
        for key in ("delegation_target_profile_id", "requested_worker_profile_id"):
            profile_id = str(metadata.get(key, "")).strip().lower()
            if not profile_id.startswith("singleton:"):
                continue
            lane = profile_id.split(":", 1)[1].strip()
            if lane in {"general", "ops", "research", "dev"}:
                return lane
        return ""

    async def _resolve_single_loop_tool_selection(
        self,
        request: OrchestratorRequest,
        *,
        worker_type: str = "general",
        requested_profile_id: str = "",
        tool_profile_override: str = "",
    ):
        if self._delegation_plane is None:
            return None
        task = await self._stores.task_store.get_task(request.task_id)
        if task is None:
            return None
        agent_context_service = AgentContextService(
            self._stores,
            project_root=self._project_root,
        )
        project, workspace = await agent_context_service._resolve_project_scope(
            task=task,
            surface=task.requester.channel or "chat",
        )
        resolved_profile_id = requested_profile_id or resolve_delegation_target_profile_id(
            request.metadata
        )
        if worker_type == "general" and not requested_profile_id:
            agent_profile, _ = await agent_context_service._resolve_agent_profile(
                project=project,
                requested_profile_id="",
            )
            resolved_profile_id = agent_profile.profile_id
        try:
            return await self._delegation_plane.capability_pack.resolve_profile_first_tools(
                ToolIndexQuery(
                    query=request.user_text.strip() or "general request",
                    limit=12,
                    tool_groups=[],
                    worker_type=worker_type,
                    tool_profile=(
                        str(tool_profile_override).strip()
                        or str(request.tool_profile).strip()
                        or "standard"
                    ),
                    project_id=project.project_id if project is not None else "",
                ),
                worker_type=worker_type,
                requested_profile_id=resolved_profile_id,
            )
        except Exception:
            return None

    async def _resolve_owner_self_worker_execution_choice(
        self,
        request: OrchestratorRequest,
    ) -> _OwnerSelfWorkerExecutionChoice | None:
        if self._delegation_plane is None:
            return None
        if request.worker_capability not in {"", "llm_generation"}:
            return None
        metadata = request.metadata
        if str(metadata.get("parent_task_id", "")).strip():
            return None
        if str(metadata.get("spawned_by", "")).strip():
            return None
        if str(metadata.get("requested_worker_type", "")).strip():
            return None
        if str(metadata.get("target_kind", "")).strip():
            return None
        if resolve_delegation_target_profile_id(metadata):
            return None
        owner_profile_id = resolve_session_owner_profile_id(metadata)
        if not owner_profile_id:
            return None
        binding = await self._delegation_plane.capability_pack.resolve_worker_binding(
            requested_profile_id=owner_profile_id,
            fallback_worker_type="general",
        )
        if binding.source_kind not in {"builtin_singleton", "worker_profile"}:
            return None
        return _OwnerSelfWorkerExecutionChoice(
            profile_id=binding.profile_id,
            profile_name=binding.profile_name,
            worker_type=binding.worker_type,
            model_alias=binding.model_alias,
            tool_profile=binding.tool_profile,
            source_kind=binding.source_kind,
        )

    @staticmethod
    def _metadata_flag(metadata: dict[str, Any], key: str) -> bool:
        value = metadata.get(key)
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

    async def _resolve_routing_decision(
        self,
        request: OrchestratorRequest,
    ) -> tuple[AgentDecision | None, dict[str, Any]]:
        if self._metadata_flag(request.metadata, "single_loop_executor"):
            return None, {}
        if not self._is_routing_decision_eligible(request):
            return None, {}
        hints = await self._build_request_runtime_hints(request)

        # Feature 067: 将 Pipeline 列表传入规则决策层，支持 trigger_hint 匹配
        pipeline_items = None
        graph_tool = self._resolve_graph_pipeline_tool()
        if graph_tool is not None:
            try:
                registry = getattr(graph_tool, "_registry", None)
                if registry is not None:
                    pipeline_items = registry.list_items()
            except Exception:
                pass

        decision = decide_agent_routing(
            request.user_text,
            runtime_hints=hints,
            pipeline_items=pipeline_items,
        )

        # Phase 1 (Feature 064): 跳过 model decision preflight
        # 天气/位置等规则决策保留，其余直接返回 None -> 进入主 Agent Direct Execution
        if decision.mode is AgentDecisionMode.DIRECT_ANSWER:
            return None, {}
        return decision, {}

    async def _dispatch_inline_decision(
        self,
        *,
        request: OrchestratorRequest,
        gate_decision: OrchestratorPolicyDecision,
        decision: AgentDecision,
    ) -> WorkerResult:
        route_reason = f"agent_decision:{decision.mode.value}:{decision.category or 'general'}"
        await self._write_orch_decision_event(
            request=request,
            route_reason=route_reason,
            gate_decision=gate_decision,
        )
        task_service = TaskService(self._stores, self._sse_hub, project_root=self._project_root)
        await task_service.ensure_task_running(
            request.task_id,
            trace_id=request.trace_id,
        )
        clarification_metadata = {
            **dict(request.metadata),
            "final_speaker": "main",
            "agent_decision_mode": decision.mode.value,
            "clarification_category": decision.category,
            "clarification_needed": decision.metadata.get(
                "clarification_needed",
                decision.category,
            ),
            "clarification_source_text": request.user_text,
            "clarification_rationale": decision.rationale,
            "clarification_missing_inputs": list(decision.missing_inputs),
            "clarification_missing_inputs_json": json.dumps(
                decision.missing_inputs,
                ensure_ascii=False,
            ),
            "agent_boundary_note": decision.user_visible_boundary_note,
            **self._build_decision_trace_metadata(decision),
        }
        await task_service.process_task_with_llm(
            task_id=request.task_id,
            user_text=request.user_text,
            llm_service=_InlineReplyLLMService(decision.reply_prompt),
            model_alias=request.model_alias,
            execution_context=None,
            dispatch_metadata=clarification_metadata,
            worker_capability="llm_generation",
            tool_profile="minimal",
            runtime_context=request.runtime_context,
        )
        task_after = await self._stores.task_store.get_task(request.task_id)
        summary_prefix = (
            "agent_best_effort"
            if decision.mode is AgentDecisionMode.BEST_EFFORT_ANSWER
            else "agent_clarification"
        )
        return self._agent_worker_result(
            request=request,
            task_status=task_after.status if task_after is not None else TaskStatus.FAILED,
            success_summary=f"{summary_prefix}:{decision.category or 'general'}",
            dispatch_prefix="agent-clarification",
        )

    async def _dispatch_direct_execution(
        self,
        *,
        request: OrchestratorRequest,
        gate_decision: OrchestratorPolicyDecision,
    ) -> WorkerResult:
        """主 Agent 直接执行路径：使用主 LLM 直接处理用户请求。

        Phase 1 (Feature 064): 跳过主 Agent Decision Preflight，复用
        ``TaskService.process_task_with_llm()`` 的 Event Sourcing 链路，
        与 Worker 执行路径保持一致的事件粒度。

        Args:
            request: 经过 Policy Gate 和 prepare 阶段的编排请求。
            gate_decision: Policy Gate 评估结果。

        Returns:
            WorkerResult: 主 Agent 直接执行的结果，状态映射与 Worker 路径一致。
        """
        is_trivial = _is_trivial_direct_answer(request.user_text)
        route_reason = (
            "agent_direct_execution:trivial"
            if is_trivial
            else "agent_direct_execution:standard"
        )

        # Phase 2 (Feature 064): 解析工具集，注入 tool_selection 到 metadata
        # 使 LLMService.call() → _try_call_with_tools() → SkillRunner 多轮循环自动生效
        worker_type = "general"
        selection = await self._resolve_single_loop_tool_selection(
            request, worker_type=worker_type,
        )
        selected_tools = list(selection.selected_tools) if selection is not None else []
        if selection is not None:
            route_reason = self._join_route_reason(
                route_reason,
                f"tool_resolution={selection.resolution_mode}",
            )
            task_service = TaskService(self._stores, self._sse_hub, project_root=self._project_root)
            await task_service.append_structured_event(
                task_id=request.task_id,
                event_type=EventType.TOOL_INDEX_SELECTED,
                actor=ActorType.KERNEL,
                payload=ToolIndexSelectedPayload(
                    selection_id=selection.selection_id,
                    backend=selection.backend,
                    is_fallback=selection.is_fallback,
                    query=selection.query.query,
                    selected_tools=selection.selected_tools,
                    hit_count=len(selection.hits),
                    warnings=selection.warnings,
                ).model_dump(mode="json"),
                trace_id=request.trace_id,
                idempotency_key=f"agent-direct-tool-index:{selection.selection_id}",
            )

        await self._write_orch_decision_event(
            request=request,
            route_reason=route_reason,
            gate_decision=gate_decision,
        )

        agent_metadata = {
            **dict(request.metadata),
            "final_speaker": "main",
            "agent_execution_mode": "direct",
            "agent_is_trivial": is_trivial,
            "selected_tools": selected_tools,
            "tool_selection": (
                selection.model_dump(mode="json") if selection is not None else {}
            ),
        }
        if selection is not None and selection.effective_tool_universe is not None:
            if selection.effective_tool_universe.profile_id:
                agent_metadata.setdefault(
                    "agent_profile_id",
                    selection.effective_tool_universe.profile_id,
                )

        task_service = TaskService(self._stores, self._sse_hub, project_root=self._project_root)
        await task_service.ensure_task_running(
            request.task_id,
            trace_id=request.trace_id,
        )

        # 主 Agent 直接执行：LLMService.call() 内部自动判断
        # 有 tool_selection → _try_call_with_tools() → SkillRunner 多轮循环
        # 无 tool_selection → FallbackManager 单次调用
        await task_service.process_task_with_llm(
            task_id=request.task_id,
            user_text=request.user_text,
            llm_service=self._llm_service,
            model_alias=request.model_alias or "main",
            execution_context=None,
            dispatch_metadata=agent_metadata,
            worker_capability="llm_generation",
            tool_profile="standard",
            runtime_context=request.runtime_context,
        )

        task_after = await self._stores.task_store.get_task(request.task_id)
        return self._agent_worker_result(
            request=request,
            task_status=(
                task_after.status if task_after is not None else TaskStatus.FAILED
            ),
            success_summary=f"agent_direct:{('trivial' if is_trivial else 'standard')}",
            dispatch_prefix="agent-direct",
        )

    async def _dispatch_owner_self_worker_execution(
        self,
        *,
        request: OrchestratorRequest,
        gate_decision: OrchestratorPolicyDecision,
        choice: _OwnerSelfWorkerExecutionChoice,
    ) -> WorkerResult:
        route_reason = f"owner_self_worker_execution:{choice.worker_type}"
        selection = await self._resolve_single_loop_tool_selection(
            request,
            worker_type=choice.worker_type,
            requested_profile_id=choice.profile_id,
            tool_profile_override=choice.tool_profile,
        )
        selected_tools = list(selection.selected_tools) if selection is not None else []
        if selection is not None:
            route_reason = self._join_route_reason(
                route_reason,
                f"tool_resolution={selection.resolution_mode}",
            )
            task_service = TaskService(self._stores, self._sse_hub, project_root=self._project_root)
            await task_service.append_structured_event(
                task_id=request.task_id,
                event_type=EventType.TOOL_INDEX_SELECTED,
                actor=ActorType.KERNEL,
                payload=ToolIndexSelectedPayload(
                    selection_id=selection.selection_id,
                    backend=selection.backend,
                    is_fallback=selection.is_fallback,
                    query=selection.query.query,
                    selected_tools=selection.selected_tools,
                    hit_count=len(selection.hits),
                    warnings=selection.warnings,
                ).model_dump(mode="json"),
                trace_id=request.trace_id,
                idempotency_key=f"owner-self-tool-index:{selection.selection_id}",
            )

        await self._write_orch_decision_event(
            request=request,
            route_reason=route_reason,
            gate_decision=gate_decision,
        )

        task_service = TaskService(self._stores, self._sse_hub, project_root=self._project_root)
        await task_service.ensure_task_running(
            request.task_id,
            trace_id=request.trace_id,
        )
        owner_metadata = {
            **dict(request.metadata),
            "final_speaker": "session_owner",
            "owner_execution_mode": "worker_self",
            "turn_executor_kind": TurnExecutorKind.WORKER.value,
            "session_owner_profile_id": choice.profile_id,
            "inherited_context_owner_profile_id": str(
                request.metadata.get("inherited_context_owner_profile_id", "")
            ).strip(),
            "agent_profile_id": choice.profile_id,
            "selected_worker_type": choice.worker_type,
            "selected_tools": selected_tools,
            "tool_selection": (
                selection.model_dump(mode="json") if selection is not None else {}
            ),
            "requested_worker_profile_id": "",
            "delegation_target_profile_id": "",
        }
        execution_context = await self._register_owner_self_execution_session(
            request=request,
            choice=choice,
        )
        await task_service.process_task_with_llm(
            task_id=request.task_id,
            user_text=request.user_text,
            llm_service=self._llm_service,
            model_alias=request.model_alias or choice.model_alias or "main",
            execution_context=execution_context,
            dispatch_metadata=owner_metadata,
            worker_capability="llm_generation",
            tool_profile=choice.tool_profile or "standard",
            runtime_context=request.runtime_context,
        )
        task_after = await self._stores.task_store.get_task(request.task_id)
        task_status = task_after.status if task_after is not None else TaskStatus.FAILED
        await self._mark_owner_self_execution_terminal(
            task_id=request.task_id,
            task_status=task_status,
            execution_context=execution_context,
        )
        return self._owner_self_worker_result(
            request=request,
            task_status=task_status,
            worker_id=choice.profile_id or f"worker:{choice.worker_type}",
            tool_profile=choice.tool_profile or "standard",
            success_summary=f"owner_self_worker:{choice.worker_type}",
            dispatch_prefix="owner-self-worker",
        )

    async def _register_owner_self_execution_session(
        self,
        *,
        request: OrchestratorRequest,
        choice: _OwnerSelfWorkerExecutionChoice,
    ) -> ExecutionRuntimeContext:
        worker_id = choice.profile_id or f"worker:{choice.worker_type}"
        session_id = str(ULID())
        runtime_kind = DelegationTargetKind.WORKER.value
        await self._execution_console.register_session(
            task_id=request.task_id,
            session_id=session_id,
            backend_job_id=session_id,
            backend=ExecutionBackend.INLINE,
            interactive=True,
            input_policy=HumanInputPolicy.EXPLICIT_REQUEST_ONLY,
            worker_id=worker_id,
            metadata={
                "work_id": str(request.metadata.get("work_id", "")),
                "runtime_kind": runtime_kind,
                "selected_worker_type": choice.worker_type,
                "parent_work_id": str(request.metadata.get("parent_work_id", "")),
                "parent_task_id": str(request.metadata.get("parent_task_id", "")),
            },
            message="owner-self worker selected inline backend",
        )
        return ExecutionRuntimeContext(
            task_id=request.task_id,
            trace_id=request.trace_id,
            session_id=session_id,
            worker_id=worker_id,
            backend=ExecutionBackend.INLINE.value,
            console=self._execution_console,
            work_id=str(request.metadata.get("work_id", "")),
            runtime_kind=runtime_kind,
            agent_session_id=str(request.metadata.get("agent_session_id", "")),
            runtime_context=request.runtime_context,
        )

    async def _mark_owner_self_execution_terminal(
        self,
        *,
        task_id: str,
        task_status: TaskStatus,
        execution_context: ExecutionRuntimeContext,
    ) -> None:
        execution_status: ExecutionSessionState | None = None
        if task_status == TaskStatus.SUCCEEDED:
            execution_status = ExecutionSessionState.SUCCEEDED
        elif task_status == TaskStatus.CANCELLED:
            execution_status = ExecutionSessionState.CANCELLED
        elif task_status in TERMINAL_STATES:
            execution_status = ExecutionSessionState.FAILED

        if execution_status is None:
            return
        await self._execution_console.mark_status(
            task_id=task_id,
            session_id=execution_context.session_id,
            status=execution_status,
            message=f"owner-self worker finished with task status={task_status.value}",
        )

    def _is_routing_decision_eligible(self, request: OrchestratorRequest) -> bool:
        if request.worker_capability not in {"", "llm_generation"}:
            return False
        metadata = request.metadata
        if str(metadata.get("parent_task_id", "")).strip():
            return False
        if str(metadata.get("spawned_by", "")).strip():
            return False
        # 如果用户明确指定了非 singleton 的自定义 Worker profile，
        # 即使 worker_type 是 general，也应该路由到该 Worker 而非被主 Agent 拦截。
        # singleton:xxx 格式的内置 delegation target 仍然走主 Agent 决策路径。
        requested_profile_id = resolve_delegation_target_profile_id(metadata)
        if requested_profile_id and not requested_profile_id.startswith("singleton:"):
            return False
        requested_worker_type = self._canonical_requested_worker_type(metadata)
        return not requested_worker_type or requested_worker_type == "general"

    def _should_direct_execute(self, request: OrchestratorRequest) -> bool:
        """判断请求是否应走主 Agent Direct Execution 路径。

        Phase 1 (Feature 064): 主 Agent 直接执行条件：
        1. LLMService 支持 tool calling（supports_single_loop_executor）
        2. 请求满足主 Agent 决策资格（非子任务、非 spawned 等）
        """
        if not bool(getattr(self._llm_service, "supports_single_loop_executor", False)):
            return False
        return self._is_routing_decision_eligible(request)

    async def _build_request_runtime_hints(
        self,
        request: OrchestratorRequest,
    ):
        latest_category = ""
        latest_source_text = ""
        works = await self._stores.work_store.list_works(task_id=request.task_id)
        if works:
            latest_work = works[0]
            latest_metadata = latest_work.metadata
            latest_category = (
                str(latest_metadata.get("clarification_category", "")).strip()
                or str(latest_metadata.get("clarification_needed", "")).strip()
            )
            latest_source_text = str(
                latest_metadata.get("clarification_source_text", "")
            ).strip()

        recent_worker_lane = await self._resolve_recent_worker_lane(task_id=request.task_id)

        return build_runtime_hint_bundle(
            user_text=request.user_text,
            can_delegate_research=self._delegation_plane is not None,
            recent_clarification_category=latest_category,
            recent_clarification_source_text=latest_source_text,
            recent_worker_lane_worker_type=(
                recent_worker_lane.worker_type if recent_worker_lane is not None else ""
            ),
            recent_worker_lane_profile_id=(
                recent_worker_lane.requested_worker_profile_id
                if recent_worker_lane is not None
                else ""
            ),
            recent_worker_lane_topic=(
                recent_worker_lane.topic if recent_worker_lane is not None else ""
            ),
            recent_worker_lane_summary=(
                recent_worker_lane.summary if recent_worker_lane is not None else ""
            ),
            metadata={"task_id": request.task_id},
        )

    async def _resolve_recent_worker_lane(
        self,
        *,
        task_id: str,
    ) -> _RecentWorkerLaneCandidate | None:
        session_state = await self._load_main_session_state(task_id=task_id)
        recent_task_ids: list[str] = []
        if session_state is not None:
            for ref_task_id in [*session_state.recent_turn_refs, task_id]:
                normalized = str(ref_task_id).strip()
                if normalized and normalized not in recent_task_ids:
                    recent_task_ids.append(normalized)
        if not recent_task_ids:
            recent_task_ids = [task_id]

        latest_candidate: tuple[datetime, _RecentWorkerLaneCandidate] | None = None
        for ref_task_id in recent_task_ids[-6:]:
            works = await self._stores.work_store.list_works(task_id=ref_task_id)
            for work in works:
                if work.selected_worker_type == "general":
                    continue
                requested_profile_id = str(work.requested_worker_profile_id or "").strip()
                topic = (
                    str(work.metadata.get("delegate_continuity_topic", "")).strip()
                    or str(work.metadata.get("agent_decision_category", "")).strip()
                    or str(work.metadata.get("agent_decision_tool_intent", "")).strip()
                    or work.title.strip()
                )
                summary = (
                    str(work.metadata.get("agent_delegate_objective", "")).strip()
                    or str(work.metadata.get("result_summary", "")).strip()
                    or work.title.strip()
                )
                candidate = _RecentWorkerLaneCandidate(
                    worker_type=work.selected_worker_type,
                    requested_worker_profile_id=requested_profile_id,
                    topic=topic,
                    summary=summary,
                    source_task_id=work.task_id,
                    source_work_id=work.work_id,
                )
                sort_key = work.updated_at or work.created_at
                if latest_candidate is None or sort_key > latest_candidate[0]:
                    latest_candidate = (sort_key, candidate)
        return None if latest_candidate is None else latest_candidate[1]

    @staticmethod
    def _build_decision_trace_metadata(decision: AgentDecision) -> dict[str, str]:
        metadata = dict(decision.metadata)
        trace_metadata: dict[str, str] = {}
        decision_source = str(metadata.get("decision_source", "")).strip()
        if decision_source:
            trace_metadata["agent_decision_source"] = decision_source
        resolution_status = str(metadata.get("decision_model_resolution_status", "")).strip()
        if resolution_status:
            trace_metadata["agent_decision_model_resolution_status"] = resolution_status
        fallback_reason = str(metadata.get("decision_fallback_reason", "")).strip()
        if fallback_reason:
            trace_metadata["agent_decision_fallback_reason"] = fallback_reason
        request_ref = str(metadata.get("decision_request_artifact_ref", "")).strip()
        if request_ref:
            trace_metadata["agent_decision_request_artifact_ref"] = request_ref
        response_ref = str(metadata.get("decision_response_artifact_ref", "")).strip()
        if response_ref:
            trace_metadata["agent_decision_response_artifact_ref"] = response_ref
        return trace_metadata

    @staticmethod
    def _annotate_compatibility_fallback_decision(
        decision: AgentDecision,
        *,
        model_resolution_status: str,
    ) -> AgentDecision:
        if decision.mode is AgentDecisionMode.DIRECT_ANSWER:
            return decision
        return decision.model_copy(
            update={
                "metadata": {
                    **dict(decision.metadata),
                    "decision_model_resolution_status": model_resolution_status,
                }
            }
        )

    async def _build_delegation_request(
        self,
        *,
        request: OrchestratorRequest,
        decision: AgentDecision | None,
    ) -> OrchestratorRequest | None:
        # DELEGATE_RESEARCH 已删除，当前无需委派重写
        del request, decision
        return None

    # ------------------------------------------------------------------
    # Feature 065: DELEGATE_GRAPH 路由分支
    # ------------------------------------------------------------------
    async def _dispatch_delegate_graph(
        self,
        *,
        request: OrchestratorRequest,
        gate_decision: "OrchestratorPolicyDecision",
        decision: AgentDecision,
    ) -> WorkerResult | None:
        """Feature 065: DELEGATE_GRAPH 路由。

        尝试通过 GraphPipelineTool 启动指定 Pipeline。
        成功时返回 WorkerResult（Pipeline 已后台启动）。
        失败时直接返回 None。
        """
        pipeline_id = str(decision.pipeline_id).strip()
        if not pipeline_id:
            log.warning(
                "delegate_graph_missing_pipeline_id",
                rationale=decision.rationale,
            )
            return None

        graph_tool = self._resolve_graph_pipeline_tool()
        if graph_tool is None:
            log.warning(
                "delegate_graph_tool_unavailable",
                pipeline_id=pipeline_id,
            )
            return None

        try:
            result_text = await graph_tool.execute(
                action="start",
                pipeline_id=pipeline_id,
                params=dict(decision.pipeline_params) if decision.pipeline_params else {},
                task_id=request.task_id,
            )
        except Exception as exc:
            log.warning(
                "delegate_graph_start_failed",
                pipeline_id=pipeline_id,
                error=str(exc),
            )
            return None

        if result_text.startswith("Error:"):
            log.warning(
                "delegate_graph_start_error",
                pipeline_id=pipeline_id,
                error=result_text,
            )
            return None

        log.info(
            "delegate_graph_started",
            pipeline_id=pipeline_id,
            task_id=request.task_id,
            result=result_text[:200],
        )

        await self._write_orch_decision_event(
            request=request,
            route_reason=f"delegate_graph:{pipeline_id}",
            gate_decision=gate_decision,
        )

        return WorkerResult(
            dispatch_id=f"graph:{pipeline_id}",
            task_id=request.task_id,
            worker_id="main.graph_pipeline",
            status=TaskStatus.SUCCEEDED,
            retryable=False,
            summary=f"pipeline_started:{pipeline_id}",
            extra_metadata={"pipeline_start_result": result_text[:500]},
        )

    def _resolve_graph_pipeline_tool(self) -> Any | None:
        """尝试获取 GraphPipelineTool 实例。"""
        return getattr(self, "_graph_pipeline_tool", None)

    async def _compose_delegate_objective(
        self,
        *,
        request: OrchestratorRequest,
        decision: AgentDecision,
        worker_type: str,
        continuity_topic: str,
        requested_worker_profile_id: str,
        recent_lane: _RecentWorkerLaneCandidate | None,
    ) -> str:
        recent_conversation = await self._build_main_recent_conversation_block(
            task_id=request.task_id
        )
        worker_label = worker_type
        continuity_lines: list[str] = []
        if continuity_topic:
            continuity_lines.append(f"- continuity_topic: {continuity_topic}")
        if requested_worker_profile_id:
            continuity_lines.append(
                f"- preferred_worker_profile_id: {requested_worker_profile_id}"
            )
        if recent_lane is not None and recent_lane.worker_type == worker_type:
            continuity_lines.append(
                f"- recent_lane_summary: {recent_lane.summary or recent_lane.source_work_id}"
            )
            continuity_lines.append(f"- recent_lane_task_id: {recent_lane.source_task_id}")
            continuity_lines.append(f"- recent_lane_work_id: {recent_lane.source_work_id}")
        continuity_block = "\n".join(continuity_lines) or "- continuity_topic: none"

        missing_inputs = "\n".join(f"- {item}" for item in decision.missing_inputs) or "- none"
        assumptions = "\n".join(f"- {item}" for item in decision.assumptions) or "- none"
        allowed_tools = (
            str(request.metadata.get("selected_tools_json", "")).strip()
            or json.dumps(request.metadata.get("selected_tools", []), ensure_ascii=False)
            or "[]"
        )
        objective = str(decision.delegate_objective).strip() or request.user_text.strip()
        return (
            f"MainAgentDelegation for {worker_label}\n"
            "目标说明：\n"
            f"- objective: {objective}\n"
            f"- rationale: {decision.rationale or 'N/A'}\n"
            f"- original_user_request: {request.user_text.strip() or 'N/A'}\n"
            "已知前提：\n"
            f"{assumptions}\n"
            "仍缺信息：\n"
            f"{missing_inputs}\n"
            "连续性要求：\n"
            f"{continuity_block}\n"
            "工具契约：\n"
            f"- tool_intent: {decision.tool_intent or 'N/A'}\n"
            f"- requested_tool_profile: {request.tool_profile or 'standard'}\n"
            f"- selected_tools_json: {allowed_tools}\n"
            "返回契约：\n"
            "- 先给可直接给主 Agent 收口的结论摘要。\n"
            "- 如果调用了外部工具，说明来源、关键参数和限制。\n"
            "- 如果仍缺关键事实，明确告诉主 Agent 还差什么。\n"
            f"{recent_conversation}"
        )

    def _main_target_kind_for_worker_type(self, worker_type: str) -> str:
        del worker_type
        if self._delegation_plane is None:
            return DelegationTargetKind.WORKER.value
        return "subagent"

    async def _build_main_recent_conversation_block(self, *, task_id: str) -> str:
        recent_user_messages: list[str] = []
        recent_tool_lines: list[str] = []
        session_rolling_summary = ""
        latest_model_summary = ""
        latest_model_preview = ""
        session_state = await self._load_main_session_state(task_id=task_id)
        agent_session = await self._load_main_agent_session(task_id=task_id)
        recent_task_ids: list[str] = []
        conversation_source = "task_event_fallback"
        if session_state is not None:
            session_rolling_summary = session_state.rolling_summary.strip()
        elif agent_session is not None:
            session_rolling_summary = agent_session.rolling_summary.strip()
        if session_state is not None:
            for ref_task_id in [*session_state.recent_turn_refs, task_id]:
                ref_task_id = str(ref_task_id).strip()
                if ref_task_id and ref_task_id not in recent_task_ids:
                    recent_task_ids.append(ref_task_id)
        transcript_entries: list[dict[str, str]] = []
        if agent_session is not None:
            replay = await AgentContextService(
                self._stores,
                project_root=self._project_root,
            ).build_agent_session_replay_projection(
                agent_session=agent_session
            )
            transcript_entries = list(replay.transcript_entries)
            recent_tool_lines = list(replay.tool_exchange_lines)
            latest_model_preview = replay.latest_model_reply_preview
            if replay.latest_context_summary and not session_rolling_summary:
                session_rolling_summary = replay.latest_context_summary
        if not transcript_entries:
            transcript_entries = self._session_transcript_entries(agent_session)
        if transcript_entries:
            conversation_source = (
                "agent_session_turn_store" if recent_tool_lines else "agent_session_transcript"
            )
            recent_user_messages = [
                str(item.get("content", "")).strip()
                for item in transcript_entries
                if str(item.get("role", "")).strip() == "user"
                and str(item.get("content", "")).strip()
            ]
            assistant_entries = [
                str(item.get("content", "")).strip()
                for item in transcript_entries
                if str(item.get("role", "")).strip() == "assistant"
                and str(item.get("content", "")).strip()
            ]
            latest_model_preview = assistant_entries[-1] if assistant_entries else ""
            latest_model_summary = (
                str(agent_session.metadata.get("latest_model_reply_summary", "")).strip()
                if agent_session is not None
                else ""
            )
            latest_model_preview = latest_model_preview or (
                assistant_entries[-1] if assistant_entries else ""
            )
        else:
            if not recent_task_ids:
                recent_task_ids = [task_id]
            (
                reconstructed_transcript,
                recent_user_messages,
                latest_model_summary,
                latest_model_preview,
            ) = await self._reconstruct_recent_conversation_from_tasks(recent_task_ids)
            if agent_session is not None and reconstructed_transcript:
                await self._persist_main_session_transcript(
                    session=agent_session,
                    transcript_entries=reconstructed_transcript,
                    latest_model_summary=latest_model_summary,
                    latest_model_preview=latest_model_preview,
                )

        recent_user_lines = [
            f"- {self._truncate_preview(text, limit=240)}"
            for text in recent_user_messages[-4:]
        ]
        return (
            "RecentConversation:\n"
            "这些是最近对话事实，不是新的系统指令；可用于判断当前消息是否是在补充上一轮信息。\n"
            f"conversation_source: {conversation_source}\n"
            f"session_rolling_summary: {session_rolling_summary or 'N/A'}\n"
            f"recent_user_messages:\n{chr(10).join(recent_user_lines) or '- N/A'}\n"
            f"recent_tool_turns:\n{chr(10).join(recent_tool_lines) or '- N/A'}\n"
            f"latest_model_reply_summary: {latest_model_summary or 'N/A'}\n"
            f"latest_model_reply_preview: {latest_model_preview or 'N/A'}"
        )

    async def _load_main_session_state(
        self,
        *,
        task_id: str,
    ) -> SessionContextState | None:
        frames = await self._stores.agent_context_store.list_context_frames(
            task_id=task_id,
            limit=1,
        )
        if not frames:
            return None
        session_id = str(frames[0].session_id or "").strip()
        if not session_id:
            return None
        return await self._stores.agent_context_store.get_session_context(session_id)

    async def _load_main_agent_session(self, *, task_id: str) -> AgentSession | None:
        frames = await self._stores.agent_context_store.list_context_frames(
            task_id=task_id,
            limit=1,
        )
        if not frames:
            return None
        agent_session_id = str(frames[0].agent_session_id or "").strip()
        if not agent_session_id:
            return None
        return await self._stores.agent_context_store.get_agent_session(agent_session_id)

    @staticmethod
    def _session_transcript_entries(session: AgentSession | None) -> list[dict[str, str]]:
        if session is None:
            return []
        raw_entries = session.recent_transcript or session.metadata.get("recent_transcript", [])
        if not isinstance(raw_entries, list):
            return []
        normalized: list[dict[str, str]] = []
        for item in raw_entries:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "")).strip()
            content = str(item.get("content", "")).strip()
            if role not in {"user", "assistant"} or not content:
                continue
            normalized.append(
                {
                    "role": role,
                    "content": content,
                    "task_id": str(item.get("task_id", "")).strip(),
                }
            )
        return normalized[-_dynamic_transcript_limit():]

    async def _persist_main_session_transcript(
        self,
        *,
        session: AgentSession,
        transcript_entries: list[dict[str, str]],
        latest_model_summary: str,
        latest_model_preview: str,
    ) -> None:
        normalized = transcript_entries[-_dynamic_transcript_limit():]
        metadata = {
            **session.metadata,
            "recent_transcript": normalized,
            "latest_model_reply_summary": latest_model_summary,
            "latest_model_reply_preview": latest_model_preview,
        }
        await self._stores.agent_context_store.save_agent_session(
            session.model_copy(
                update={
                    "recent_transcript": normalized,
                    "metadata": metadata,
                    "updated_at": datetime.now(UTC),
                }
            )
        )

    async def _reconstruct_recent_conversation_from_tasks(
        self,
        recent_task_ids: list[str],
    ) -> tuple[list[dict[str, str]], list[str], str, str]:
        transcript_entries: list[dict[str, str]] = []
        recent_user_messages: list[str] = []
        latest_model_summary = ""
        latest_model_preview = ""
        for ref_task_id in recent_task_ids[-6:]:
            events = await self._stores.event_store.get_events_for_task(ref_task_id)
            for event in events:
                if event.type is EventType.USER_MESSAGE:
                    text = str(event.payload.get("text", "") or "").strip()
                    if not text:
                        text = str(event.payload.get("text_preview", "") or "").strip()
                    if not text:
                        continue
                    recent_user_messages.append(text)
                    transcript_entries.append(
                        {
                            "role": "user",
                            "content": self._truncate_preview(text, limit=320),
                            "task_id": ref_task_id,
                        }
                    )
                    continue
                if event.type is not EventType.MODEL_CALL_COMPLETED:
                    continue
                artifact_ref = str(event.payload.get("artifact_ref", "") or "").strip()
                if not artifact_ref:
                    continue
                artifact = await self._stores.artifact_store.get_artifact(artifact_ref)
                if artifact is None or artifact.name != "llm-response":
                    continue
                latest_model_summary = str(
                    event.payload.get("response_summary", "") or ""
                ).strip()
                payload = await self._stores.artifact_store.get_artifact_content(artifact_ref)
                preview = (
                    self._truncate_preview(payload.decode("utf-8", errors="ignore"))
                    if payload is not None
                    else latest_model_summary
                )
                if not preview:
                    continue
                latest_model_preview = preview
                transcript_entries.append(
                    {
                        "role": "assistant",
                        "content": preview,
                        "task_id": ref_task_id,
                    }
                )
        return (
            transcript_entries[-_dynamic_transcript_limit():],
            recent_user_messages,
            latest_model_summary,
            latest_model_preview,
        )

    async def _resolve_session_response_artifact_id(self, artifact_refs: list[str]) -> str:
        for artifact_id in reversed(artifact_refs):
            artifact = await self._stores.artifact_store.get_artifact(artifact_id)
            if artifact is None:
                continue
            if artifact.name == "llm-response":
                return artifact.artifact_id
        return ""

    def _agent_worker_result(
        self,
        *,
        request: OrchestratorRequest,
        task_status: TaskStatus,
        success_summary: str = "agent_inline_completed",
        dispatch_prefix: str = "agent-inline",
    ) -> WorkerResult:
        if task_status == TaskStatus.SUCCEEDED:
            return WorkerResult(
                dispatch_id=f"{dispatch_prefix}:{request.task_id}",
                task_id=request.task_id,
                worker_id="main.agent",
                status=TaskStatus.SUCCEEDED,
                retryable=False,
                summary=success_summary,
                backend="inline",
                tool_profile="minimal",
            )
        if task_status == TaskStatus.CANCELLED:
            return WorkerResult(
                dispatch_id=f"{dispatch_prefix}:{request.task_id}",
                task_id=request.task_id,
                worker_id="main.agent",
                status=TaskStatus.CANCELLED,
                retryable=False,
                summary=f"{dispatch_prefix}_cancelled",
                backend="inline",
                tool_profile="minimal",
            )
        return WorkerResult(
            dispatch_id=f"{dispatch_prefix}:{request.task_id}",
            task_id=request.task_id,
            worker_id="main.agent",
            status=TaskStatus.FAILED,
            retryable=True,
            summary=f"{dispatch_prefix}_terminal:{task_status.value}",
            error_type="AgentInlineExecutionFailed",
            error_message=f"task status={task_status.value}",
            backend="inline",
            tool_profile="minimal",
        )

    def _owner_self_worker_result(
        self,
        *,
        request: OrchestratorRequest,
        task_status: TaskStatus,
        worker_id: str,
        tool_profile: str,
        success_summary: str,
        dispatch_prefix: str,
    ) -> WorkerResult:
        if task_status == TaskStatus.SUCCEEDED:
            return WorkerResult(
                dispatch_id=f"{dispatch_prefix}:{request.task_id}",
                task_id=request.task_id,
                worker_id=worker_id,
                status=TaskStatus.SUCCEEDED,
                retryable=False,
                summary=success_summary,
                backend="inline",
                tool_profile=tool_profile,
            )
        if task_status == TaskStatus.CANCELLED:
            return WorkerResult(
                dispatch_id=f"{dispatch_prefix}:{request.task_id}",
                task_id=request.task_id,
                worker_id=worker_id,
                status=TaskStatus.CANCELLED,
                retryable=False,
                summary="owner_self_worker_cancelled",
                backend="inline",
                tool_profile=tool_profile,
            )
        return WorkerResult(
            dispatch_id=f"{dispatch_prefix}:{request.task_id}",
            task_id=request.task_id,
            worker_id=worker_id,
            status=TaskStatus.FAILED,
            retryable=True,
            summary=f"owner_self_worker_terminal:{task_status.value}",
            error_type="OwnerSelfWorkerExecutionFailed",
            error_message=f"task status={task_status.value}",
            backend="inline",
            tool_profile=tool_profile,
        )

    def _join_route_reason(self, *parts: str) -> str:
        normalized = [part.strip() for part in parts if part and part.strip()]
        return " | ".join(dict.fromkeys(normalized))

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
                status=TaskStatus.FAILED,
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
        # 获取 target agent 显示名称（用于前端泳道标题）
        _agent_name = ""
        if a2a_conversation and a2a_conversation.target_agent_runtime_id:
            try:
                _rt = await self._stores.agent_context_store.get_agent_runtime(
                    a2a_conversation.target_agent_runtime_id
                )
                if _rt is not None:
                    _agent_name = _rt.name
            except Exception:
                pass
        await self._write_worker_dispatched_event(
            envelope, worker.worker_id, agent_name=_agent_name
        )

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
                status=TaskStatus.FAILED,
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
                        TaskStatus.SUCCEEDED: WorkStatus.SUCCEEDED,
                        TaskStatus.FAILED: WorkStatus.FAILED,
                        TaskStatus.CANCELLED: WorkStatus.CANCELLED,
                    }.get(result.status, WorkStatus.FAILED),
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

        if result.status == TaskStatus.FAILED:
            await self._ensure_task_failed(task_id, trace_id, result.summary)

        # Feature 064 P2-B: Worker 完成后的终态通知
        # 注意：_ensure_task_failed 内部已有通知调用，这里处理 SUCCEEDED/CANCELLED 情况
        if result.status == TaskStatus.SUCCEEDED:
            await self._notify_state_change(
                task_id=task_id,
                from_status=TaskStatus.RUNNING.value,
                to_status=TaskStatus.SUCCEEDED.value,
                reason=result.summary,
            )
        elif result.status == TaskStatus.CANCELLED:
            await self._notify_state_change(
                task_id=task_id,
                from_status=TaskStatus.RUNNING.value,
                to_status=TaskStatus.CANCELLED.value,
                reason=result.summary,
            )

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
            role=AgentRuntimeRole.MAIN,
            project_id=project_id,
            agent_profile_id=source_agent_profile_id,
            worker_profile_id="",
            worker_capability="",
        )
        legacy_session_id = self._first_non_empty(
            runtime_context.session_id if runtime_context is not None else "",
            build_scope_aware_session_id(
                task,
                project_id=project_id,
            )
            if task is not None
            else "",
            envelope.task_id,
        )
        source_session = await self._ensure_a2a_agent_session(
            agent_session_id=source_agent_session_id,
            agent_runtime=source_runtime,
            kind=AgentSessionKind.MAIN_BOOTSTRAP,
            project_id=project_id,
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
        source_agent_uri = self._agent_uri("main.agent")
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
        if result.status == TaskStatus.SUCCEEDED:
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
        agent_profile_id: str,
        worker_profile_id: str,
        worker_capability: str,
    ) -> AgentRuntime:
        runtime_id = agent_runtime_id.strip()
        existing: AgentRuntime | None = None
        if runtime_id:
            existing = await self._stores.agent_context_store.get_agent_runtime(runtime_id)
        if existing is None:
            existing = await self._stores.agent_context_store.find_active_runtime(
                project_id=project_id,
                role=role,
                worker_profile_id=worker_profile_id,
                agent_profile_id=agent_profile_id,
            )
        if existing is not None:
            return existing
        runtime_id = f"runtime-{ULID()}"
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
        if role is AgentRuntimeRole.MAIN:
            name = agent_profile.name if agent_profile is not None else "Main Agent"
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
            agent_profile_id=agent_profile_id,
            worker_profile_id=worker_profile_id,
            role=role,
            name=name,
            persona_summary=persona_summary,
            permission_preset=resolve_permission_preset(worker_profile, agent_profile),
            metadata={
                "created_by": "orchestrator.wave2",
                "worker_capability": worker_capability,
            },
        )
        try:
            await self._stores.agent_context_store.save_agent_runtime(runtime)
        except sqlite3.IntegrityError:
            refreshed = await self._stores.agent_context_store.find_active_runtime(
                project_id=project_id,
                role=role,
                worker_profile_id=worker_profile_id,
                agent_profile_id=agent_profile_id,
            )
            if refreshed is not None:
                return refreshed
            raise
        return runtime

    async def _ensure_a2a_agent_session(
        self,
        *,
        agent_session_id: str,
        agent_runtime: AgentRuntime,
        kind: AgentSessionKind,
        project_id: str,
        surface: str,
        thread_id: str,
        legacy_session_id: str,
        work_id: str,
        task_id: str,
        a2a_conversation_id: str,
        parent_agent_session_id: str,
    ) -> AgentSession:
        session_id = agent_session_id.strip()
        existing: AgentSession | None = None
        if session_id:
            existing = await self._stores.agent_context_store.get_agent_session(session_id)
            if existing is not None:
                return existing
        # 按 (project, kind, runtime/work) 反查已有 active session，避免 composite-key 双写
        if kind is AgentSessionKind.MAIN_BOOTSTRAP and project_id:
            active_for_project = (
                await self._stores.agent_context_store.get_active_session_for_project(
                    project_id, kind=AgentSessionKind.MAIN_BOOTSTRAP
                )
            )
            if active_for_project is not None:
                return active_for_project
        elif kind is AgentSessionKind.DIRECT_WORKER and project_id:
            active_for_project = (
                await self._stores.agent_context_store.get_active_session_for_project(
                    project_id, kind=AgentSessionKind.DIRECT_WORKER
                )
            )
            if active_for_project is not None:
                return active_for_project
        elif kind is AgentSessionKind.WORKER_INTERNAL and work_id:
            # 同一 task / work 的多次 a2a dispatch（重启 / 重试）必须复用同一
            # WORKER_INTERNAL session，否则 worker_runtime 的 execution session
            # 也跟着翻新（worker_runtime.session_id 优先用 envelope.metadata.agent_session_id）。
            candidates = await self._stores.agent_context_store.list_agent_sessions(
                agent_runtime_id=agent_runtime.agent_runtime_id,
                project_id=project_id or None,
                kind=AgentSessionKind.WORKER_INTERNAL,
                limit=20,
            )
            for candidate in candidates:
                if candidate.work_id and candidate.work_id == work_id:
                    return candidate
        if not session_id:
            session_id = f"session-{ULID()}"
        session = AgentSession(
            agent_session_id=session_id,
            agent_runtime_id=agent_runtime.agent_runtime_id,
            kind=kind,
            project_id=project_id,
            surface=surface or "chat",
            thread_id=thread_id,
            legacy_session_id=legacy_session_id,
            parent_agent_session_id=parent_agent_session_id,
            work_id=work_id,
            a2a_conversation_id=a2a_conversation_id,
            metadata={"created_by": "orchestrator.wave2"},
        )
        try:
            await self._stores.agent_context_store.save_agent_session(session)
        except sqlite3.IntegrityError:
            # 并发 race：partial unique index 拒绝同 project 同 kind 第二条 active session。
            if kind in (AgentSessionKind.MAIN_BOOTSTRAP, AgentSessionKind.DIRECT_WORKER) and project_id:
                refreshed = await self._stores.agent_context_store.get_active_session_for_project(
                    project_id, kind=kind,
                )
                if refreshed is not None:
                    return refreshed
            raise
        return session

    @staticmethod
    def _a2a_status_from_worker_result(result: WorkerResult) -> A2AConversationStatus:
        if result.status == TaskStatus.SUCCEEDED:
            return A2AConversationStatus.COMPLETED
        if result.status == TaskStatus.CANCELLED:
            return A2AConversationStatus.CANCELLED
        return A2AConversationStatus.FAILED

    @staticmethod
    def _first_non_empty(*values: object) -> str:
        for value in values:
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    @staticmethod
    def _truncate_preview(text: str, *, limit: int = 600) -> str:
        normalized = " ".join(text.split())
        if len(normalized) <= limit:
            return normalized
        return f"{normalized[:limit].rstrip()}..."

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
        agent_name: str = "",
    ) -> None:
        payload = WorkerDispatchedPayload(
            dispatch_id=envelope.dispatch_id,
            worker_id=worker_id,
            worker_capability=envelope.worker_capability,
            contract_version=envelope.contract_version,
            agent_name=agent_name,
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

        service = TaskService(self._stores, self._sse_hub, project_root=self._project_root)
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
                # Feature 064 P2-B: 终态通知
                await self._notify_state_change(
                    task_id=task_id,
                    from_status=TaskStatus.RUNNING.value,
                    to_status=TaskStatus.FAILED.value,
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

        service = TaskService(self._stores, self._sse_hub, project_root=self._project_root)
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
                # Feature 064 P2-B: 终态通知
                await self._notify_state_change(
                    task_id=task_id,
                    from_status=TaskStatus.WAITING_APPROVAL.value,
                    to_status=TaskStatus.REJECTED.value,
                    reason=reason,
                )
                return

            if task.status not in TERMINAL_STATES:
                prev_status = task.status.value
                await service._write_state_transition(
                    task_id=task_id,
                    from_status=task.status,
                    to_status=TaskStatus.REJECTED,
                    trace_id=trace_id,
                    reason=reason,
                )
                # Feature 064 P2-B: 终态通知
                await self._notify_state_change(
                    task_id=task_id,
                    from_status=prev_status,
                    to_status=TaskStatus.REJECTED.value,
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

        service = TaskService(self._stores, self._sse_hub, project_root=self._project_root)
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
                # Feature 064 P2-B: WAITING_APPROVAL 状态通知
                if target == TaskStatus.WAITING_APPROVAL:
                    await self._notify_state_change(
                        task_id=task_id,
                        from_status=TaskStatus.RUNNING.value,
                        to_status=TaskStatus.WAITING_APPROVAL.value,
                        reason=reason,
                    )
        except Exception as exc:  # pragma: no cover - 防御性兜底
            log.warning(
                "orchestrator_force_waiting_error",
                task_id=task_id,
                reason=reason,
                error_type=type(exc).__name__,
            )
