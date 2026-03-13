"""Orchestrator 控制平面服务（Feature 008）。

最小职责：
1. 请求封装与高风险 gate
2. 单 worker 路由与派发
3. 控制平面事件写入（ORCH_DECISION/WORKER_DISPATCHED/WORKER_RETURNED）
4. worker 结果回传与失败分类
"""

from __future__ import annotations

import hashlib
import json
import re
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
    Work,
    WorkerDispatchedPayload,
    WorkerExecutionStatus,
    WorkerResult,
    WorkerReturnedPayload,
    WorkerSession,
    WorkerType,
    WorkStatus,
)
from octoagent.core.models.message import NormalizedMessage
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
    build_agent_runtime_id,
    build_agent_session_id,
    build_scope_aware_session_id,
)
from .capability_pack import CapabilityPackService
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

_WEATHER_QUERY_TOKENS = ("天气", "weather", "气温", "温度", "下雨", "降雨", "体感", "穿衣")
_WEATHER_LOCATION_STOPWORDS = (
    "今天",
    "今日",
    "现在",
    "此刻",
    "帮我",
    "请帮我",
    "请问",
    "查一下",
    "查查",
    "看一下",
    "看下",
    "问下",
    "问一问",
    "想知道",
    "想问",
    "一下",
    "会不会",
    "怎么样",
    "咋样",
    "如何",
    "这里",
    "这边",
    "本地",
    "当地",
    "我这里",
)
_LOCATION_SUFFIX_PATTERN = re.compile(
    r"[\u4e00-\u9fff]{1,10}(?:省|市|区|县|州|盟|旗|镇|乡|村|岛|湾|港)"
)
_EN_LOCATION_PATTERN = re.compile(
    r"\b(?:in|at|for)\s+[A-Za-z][A-Za-z .'-]{1,40}",
    re.IGNORECASE,
)
_DIRECT_LOCATION_TOKENS = {
    "北京",
    "上海",
    "天津",
    "重庆",
    "深圳",
    "广州",
    "杭州",
    "苏州",
    "南京",
    "武汉",
    "成都",
    "西安",
    "长沙",
    "青岛",
    "厦门",
    "宁波",
    "无锡",
    "香港",
    "澳门",
    "台北",
    "东京",
    "首尔",
    "伦敦",
    "巴黎",
    "纽约",
    "洛杉矶",
    "旧金山",
    "新加坡",
}


class _InlineReplyLLMService:
    """把 orchestrator 的确定性收口回复接入标准 Task LLM 主链。"""

    def __init__(
        self,
        content: str,
        *,
        model_alias: str = "butler-inline",
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
            model_name="butler-inline",
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
class ButlerFreshnessOutcome:
    """Butler -> Research freshness 委派结果。"""

    child_task_id: str
    child_thread_id: str
    child_work_id: str
    child_status: TaskStatus
    child_worker_status: WorkerExecutionStatus
    child_worker_id: str
    child_route_reason: str
    child_tool_profile: str
    child_a2a_conversation_id: str
    child_source_agent_session_id: str
    child_target_agent_session_id: str
    child_a2a_message_count: int
    result_artifact_id: str
    result_text: str
    result_summary: str
    error_summary: str


@dataclass(frozen=True)
class ButlerFreshnessFollowup:
    """Butler 天气补问后的续写恢复上下文。"""

    source_work_id: str
    original_user_text: str


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
        self._llm_service = llm_service
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

        freshness_request = await self._resolve_butler_owned_freshness_request(request)
        if freshness_request is not None:
            return await self._dispatch_butler_owned_freshness(
                request=freshness_request,
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

    def _should_use_butler_owned_freshness(self, request: OrchestratorRequest) -> bool:
        if self._delegation_plane is None:
            return False
        if request.worker_capability not in {"", "llm_generation"}:
            return False
        metadata = request.metadata
        if str(metadata.get("parent_task_id", "")).strip():
            return False
        if str(metadata.get("spawned_by", "")).strip():
            return False
        requested_worker_type = str(metadata.get("requested_worker_type", "")).strip().lower()
        if requested_worker_type and requested_worker_type != "general":
            return False
        return CapabilityPackService._requires_standard_web_access(
            request.user_text,
            worker_type=WorkerType.RESEARCH,
        )

    async def _resolve_butler_owned_freshness_request(
        self,
        request: OrchestratorRequest,
    ) -> OrchestratorRequest | None:
        if self._should_use_butler_owned_freshness(request):
            return request
        followup = await self._match_weather_location_followup(request)
        if followup is None:
            return None
        return request.model_copy(
            update={
                "user_text": self._build_weather_location_followup_query(
                    location_text=request.user_text,
                    original_user_text=followup.original_user_text,
                ),
                "metadata": {
                    **dict(request.metadata),
                    "freshness_followup_mode": "weather_location",
                    "freshness_followup_source_work_id": followup.source_work_id,
                    "freshness_followup_original_question": followup.original_user_text,
                    "freshness_followup_location_text": request.user_text,
                },
            }
        )

    async def _match_weather_location_followup(
        self,
        request: OrchestratorRequest,
    ) -> ButlerFreshnessFollowup | None:
        if self._delegation_plane is None:
            return None
        if request.worker_capability not in {"", "llm_generation"}:
            return None
        metadata = request.metadata
        if str(metadata.get("parent_task_id", "")).strip():
            return None
        if str(metadata.get("spawned_by", "")).strip():
            return None
        requested_worker_type = str(metadata.get("requested_worker_type", "")).strip().lower()
        if requested_worker_type and requested_worker_type != "general":
            return None
        if CapabilityPackService._requires_standard_web_access(
            request.user_text,
            worker_type=WorkerType.RESEARCH,
        ):
            return None
        if not self._contains_explicit_location(request.user_text):
            return None
        works = await self._stores.work_store.list_works(task_id=request.task_id)
        if not works:
            return None
        latest_work = works[0]
        latest_metadata = latest_work.metadata
        if str(latest_metadata.get("delegation_strategy", "")).strip() != "butler_owned_freshness":
            return None
        if str(latest_metadata.get("clarification_needed", "")).strip() != "weather_location":
            return None
        if str(latest_metadata.get("freshness_resolution", "")).strip() != "location_required":
            return None
        original_user_text = (
            str(latest_metadata.get("clarification_source_text", "")).strip()
            or latest_work.title.strip()
        )
        if not original_user_text:
            return None
        return ButlerFreshnessFollowup(
            source_work_id=latest_work.work_id,
            original_user_text=original_user_text,
        )

    async def _dispatch_butler_owned_freshness(
        self,
        *,
        request: OrchestratorRequest,
        gate_decision: OrchestratorPolicyDecision,
    ) -> WorkerResult:
        if self._delegation_plane is None:
            raise RuntimeError("delegation plane is required for butler freshness dispatch")

        task = await self._stores.task_store.get_task(request.task_id)
        if task is None:
            raise RuntimeError(f"task not found for butler freshness dispatch: {request.task_id}")

        parent_request = request.model_copy(
            update={
                "tool_profile": "minimal",
                "metadata": {
                    **dict(request.metadata),
                    "requested_worker_type": "general",
                    "target_kind": DelegationTargetKind.WORKER.value,
                    "delegation_strategy": "butler_owned_freshness",
                },
            }
        )
        plan = await self._delegation_plane.prepare_dispatch(parent_request)
        route_reason = self._join_route_reason(
            plan.work.route_reason or "worker_type=general",
            "delegation_strategy=butler_owned_freshness",
        )
        if plan.dispatch_envelope is None:
            await self._write_orch_decision_event(
                request=parent_request,
                route_reason=route_reason,
                gate_decision=gate_decision,
            )
            await self._ensure_task_waiting(
                task_id=request.task_id,
                trace_id=request.trace_id,
                status=plan.pipeline_status.value,
                reason=plan.deferred_reason or plan.pipeline_status.value,
            )
            return WorkerResult(
                dispatch_id=f"butler-freshness-{plan.work.work_id}",
                task_id=request.task_id,
                worker_id="butler.main",
                status=WorkerExecutionStatus.FAILED,
                retryable=True,
                summary=f"delegation_deferred:{plan.pipeline_status.value}",
                error_type="DelegationDeferred",
                error_message=plan.deferred_reason or plan.pipeline_status.value,
            )

        parent_envelope = plan.dispatch_envelope.model_copy(
            update={
                "route_reason": route_reason,
                "tool_profile": "minimal",
                "metadata": {
                    **dict(plan.dispatch_envelope.metadata),
                    "delegation_strategy": "butler_owned_freshness",
                    "selected_worker_type": "general",
                    "target_kind": DelegationTargetKind.WORKER.value,
                },
            }
        )
        await self._write_orch_decision_event(
            request=parent_request,
            route_reason=route_reason,
            gate_decision=gate_decision,
        )
        await self._delegation_plane.mark_dispatched(
            work_id=plan.work.work_id,
            worker_id="butler.main",
            dispatch_id=f"butler-freshness:{plan.work.work_id}",
        )

        task_service = TaskService(self._stores, self._sse_hub)
        await task_service.ensure_task_running(
            request.task_id,
            trace_id=request.trace_id,
        )
        if self._requires_freshness_location_clarification(request.user_text):
            clarification_metadata = {
                "delegation_strategy": "butler_owned_freshness",
                "final_speaker": "butler",
                "freshness_resolution": "location_required",
                "clarification_needed": "weather_location",
                "clarification_source_text": request.user_text,
                "selected_worker_type": "general",
                "selected_tools": [],
                "selected_tools_json": "[]",
            }
            await task_service.process_task_with_llm(
                task_id=request.task_id,
                user_text=request.user_text,
                llm_service=_InlineReplyLLMService(
                    self._build_missing_location_reply(request.user_text)
                ),
                model_alias=request.model_alias,
                execution_context=None,
                dispatch_metadata={
                    **dict(parent_envelope.metadata),
                    **clarification_metadata,
                },
                worker_capability="llm_generation",
                tool_profile="minimal",
                runtime_context=parent_envelope.runtime_context,
            )
            await self._patch_work_metadata(
                plan.work.work_id,
                metadata_updates=clarification_metadata,
            )
            task_after = await self._stores.task_store.get_task(request.task_id)
            result = self._butler_worker_result(
                request=request,
                task_status=task_after.status if task_after is not None else TaskStatus.FAILED,
                success_summary="butler_freshness_location_clarified",
            )
            await self._delegation_plane.complete_work(
                work_id=plan.work.work_id,
                result=DelegationResult(
                    delegation_id=result.dispatch_id,
                    work_id=plan.work.work_id,
                    status=self._work_status_from_task_status(
                        task_after.status if task_after else TaskStatus.FAILED
                    ),
                    summary=result.summary,
                    retryable=result.retryable,
                    runtime_id="butler.main",
                    target_kind=DelegationTargetKind.WORKER,
                    route_reason=route_reason,
                    metadata=clarification_metadata,
                ),
            )
            return result

        outcome = await self._delegate_freshness_to_research(
            parent_task=task,
            parent_request=parent_request,
            parent_work=plan.work,
            parent_envelope=parent_envelope,
        )
        followup_metadata = self._extract_freshness_followup_metadata(request.metadata)

        handoff_artifact = await task_service.create_text_artifact(
            task_id=request.task_id,
            name="butler-research-handoff",
            description="Research worker 向 Butler 回传的 freshness handoff",
            content=self._build_freshness_handoff_artifact_content(
                user_text=request.user_text,
                outcome=outcome,
            ),
            trace_id=request.trace_id,
            source="freshness-research-handoff",
        )

        resolution_metadata: dict[str, Any] = {}
        synthesis_service = self._llm_service
        synthesis_summary = "butler_freshness_synthesized"
        if self._is_backend_unavailable_outcome(outcome):
            resolution_metadata = {
                "freshness_resolution": "backend_unavailable",
                "freshness_degraded_reason": outcome.error_summary,
                "final_speaker": "butler",
            }
            synthesis_service = _InlineReplyLLMService(
                self._build_backend_unavailable_reply(
                    user_text=request.user_text,
                    outcome=outcome,
                )
            )
            synthesis_summary = "butler_freshness_backend_explained"

        await self._patch_work_metadata(
            plan.work.work_id,
            metadata_updates={
                "delegation_strategy": "butler_owned_freshness",
                "final_speaker": "butler",
                "research_child_task_id": outcome.child_task_id,
                "research_child_thread_id": outcome.child_thread_id,
                "research_child_work_id": outcome.child_work_id,
                "research_child_status": outcome.child_status.value,
                "research_worker_status": outcome.child_worker_status.value,
                "research_worker_id": outcome.child_worker_id,
                "research_route_reason": outcome.child_route_reason,
                "research_tool_profile": outcome.child_tool_profile,
                "research_a2a_conversation_id": outcome.child_a2a_conversation_id,
                "research_butler_agent_session_id": outcome.child_source_agent_session_id,
                "research_worker_agent_session_id": outcome.child_target_agent_session_id,
                "research_a2a_message_count": outcome.child_a2a_message_count,
                "research_result_artifact_ref": outcome.result_artifact_id,
                "research_result_summary": outcome.result_summary,
                "research_error_summary": outcome.error_summary,
                "research_handoff_artifact_ref": handoff_artifact.artifact_id,
                **followup_metadata,
                **resolution_metadata,
            },
        )

        synthesis_metadata = {
            **dict(parent_envelope.metadata),
            "delegation_strategy": "butler_owned_freshness",
            "freshness_delegate_mode": "research",
            "selected_worker_type": "general",
            "selected_tools": [],
            "selected_tools_json": "[]",
            "research_child_task_id": outcome.child_task_id,
            "research_child_work_id": outcome.child_work_id,
            "research_child_status": outcome.child_status.value,
            "research_worker_status": outcome.child_worker_status.value,
            "research_worker_id": outcome.child_worker_id,
            "research_route_reason": outcome.child_route_reason,
            "research_tool_profile": outcome.child_tool_profile,
            "research_a2a_conversation_id": outcome.child_a2a_conversation_id,
            "research_butler_agent_session_id": outcome.child_source_agent_session_id,
            "research_worker_agent_session_id": outcome.child_target_agent_session_id,
            "research_a2a_message_count": outcome.child_a2a_message_count,
            "research_result_artifact_ref": outcome.result_artifact_id,
            "research_result_summary": outcome.result_summary,
            "research_result_text": outcome.result_text,
            "research_error_summary": outcome.error_summary,
            "research_handoff_artifact_ref": handoff_artifact.artifact_id,
            **followup_metadata,
            **resolution_metadata,
        }
        await task_service.process_task_with_llm(
            task_id=request.task_id,
            user_text=request.user_text,
            llm_service=synthesis_service,
            model_alias=request.model_alias,
            execution_context=None,
            dispatch_metadata=synthesis_metadata,
            worker_capability="llm_generation",
            tool_profile="minimal",
            runtime_context=parent_envelope.runtime_context,
        )

        task_after = await self._stores.task_store.get_task(request.task_id)
        result = self._butler_worker_result(
            request=request,
            task_status=task_after.status if task_after is not None else TaskStatus.FAILED,
            success_summary=synthesis_summary,
        )
        await self._delegation_plane.complete_work(
            work_id=plan.work.work_id,
            result=DelegationResult(
                delegation_id=result.dispatch_id,
                work_id=plan.work.work_id,
                status=self._work_status_from_task_status(task_after.status if task_after else TaskStatus.FAILED),
                summary=result.summary,
                retryable=result.retryable,
                runtime_id="butler.main",
                target_kind=DelegationTargetKind.WORKER,
                route_reason=route_reason,
                metadata={
                    "delegation_strategy": "butler_owned_freshness",
                    "final_speaker": "butler",
                    "research_child_task_id": outcome.child_task_id,
                    "research_child_thread_id": outcome.child_thread_id,
                    "research_child_work_id": outcome.child_work_id,
                    "research_child_status": outcome.child_status.value,
                    "research_worker_status": outcome.child_worker_status.value,
                    "research_worker_id": outcome.child_worker_id,
                    "research_route_reason": outcome.child_route_reason,
                    "research_tool_profile": outcome.child_tool_profile,
                    "research_a2a_conversation_id": outcome.child_a2a_conversation_id,
                    "research_butler_agent_session_id": outcome.child_source_agent_session_id,
                    "research_worker_agent_session_id": outcome.child_target_agent_session_id,
                    "research_a2a_message_count": outcome.child_a2a_message_count,
                    "research_result_artifact_ref": outcome.result_artifact_id,
                    "research_result_summary": outcome.result_summary,
                    "research_error_summary": outcome.error_summary,
                    "research_handoff_artifact_ref": handoff_artifact.artifact_id,
                    **followup_metadata,
                    **resolution_metadata,
                },
            ),
        )
        return result

    async def _delegate_freshness_to_research(
        self,
        *,
        parent_task,
        parent_request: OrchestratorRequest,
        parent_work: Work,
        parent_envelope: DispatchEnvelope,
    ) -> ButlerFreshnessOutcome:
        latest_user_event_id = await self._latest_user_event_id(parent_task.task_id)
        parent_runtime_context = parent_envelope.runtime_context or runtime_context_from_metadata(
            parent_envelope.metadata
        )
        parent_agent_runtime_id = build_agent_runtime_id(
            role=AgentRuntimeRole.BUTLER,
            project_id=parent_work.project_id,
            workspace_id=parent_work.workspace_id,
            agent_profile_id=parent_work.agent_profile_id,
            worker_profile_id="",
            worker_capability="",
        )
        legacy_session_id = self._first_non_empty(
            parent_runtime_context.session_id if parent_runtime_context is not None else "",
            build_scope_aware_session_id(
                parent_task,
                project_id=parent_work.project_id,
                workspace_id=parent_work.workspace_id,
            ),
            parent_task.task_id,
        )
        parent_agent_session_id = build_agent_session_id(
            agent_runtime_id=parent_agent_runtime_id,
            kind=AgentSessionKind.BUTLER_MAIN,
            legacy_session_id=legacy_session_id,
            work_id="",
            task_id=parent_task.task_id,
        )
        objective = self._build_freshness_delegate_objective(parent_request.user_text)
        child_suffix = latest_user_event_id or hashlib.sha1(
            parent_request.user_text.encode("utf-8")
        ).hexdigest()[:12]
        child_thread_suffix = child_suffix[:12]
        child_thread_id = f"{parent_task.thread_id}:freshness:{child_thread_suffix}"
        child_message = NormalizedMessage(
            channel=parent_task.requester.channel,
            thread_id=child_thread_id,
            scope_id=parent_task.scope_id,
            sender_id=parent_task.requester.sender_id,
            sender_name=parent_task.requester.sender_id or "owner",
            text=objective,
            control_metadata={
                "parent_task_id": parent_task.task_id,
                "parent_work_id": parent_work.work_id,
                "requested_worker_type": "research",
                "target_kind": DelegationTargetKind.SUBAGENT.value,
                "tool_profile": "standard",
                "project_id": parent_work.project_id,
                "workspace_id": parent_work.workspace_id,
                "agent_profile_id": parent_work.agent_profile_id,
                "spawned_by": "butler_freshness_delegate",
                "child_title": "freshness-research",
                "source_agent_runtime_id": parent_agent_runtime_id,
                "source_agent_session_id": parent_agent_session_id,
            },
            idempotency_key=f"butler_freshness_delegate:{parent_task.task_id}:{child_suffix}",
        )
        task_service = TaskService(self._stores, self._sse_hub)
        child_task_id, created = await task_service.create_task(child_message)

        child_task = await self._stores.task_store.get_task(child_task_id)
        if child_task is not None and child_task.status in TERMINAL_STATES:
            await self._finalize_internal_task_job(child_task_id, child_task.status)
            return await self._build_freshness_outcome(
                child_task_id=child_task_id,
                child_thread_id=child_thread_id,
                child_worker_result=None,
                parent_work_id=parent_work.work_id,
            )

        await self._stores.task_job_store.create_job(
            child_task_id,
            objective,
            parent_request.model_alias,
        )
        job = await self._stores.task_job_store.get_job(child_task_id)
        if job is not None and job.status == "QUEUED":
            await self._stores.task_job_store.mark_running(child_task_id)

        child_result = await self.dispatch(
            task_id=child_task_id,
            user_text=objective,
            model_alias=parent_request.model_alias,
            worker_capability="llm_generation",
            tool_profile="standard",
            metadata={
                **dict(child_message.control_metadata),
                "project_id": parent_work.project_id,
                "workspace_id": parent_work.workspace_id,
                "agent_profile_id": parent_work.agent_profile_id,
                "source_agent_runtime_id": parent_agent_runtime_id,
                "source_agent_session_id": parent_agent_session_id,
            },
        )
        child_task = await self._stores.task_store.get_task(child_task_id)
        await self._finalize_internal_task_job(
            child_task_id,
            child_task.status if child_task is not None else TaskStatus.FAILED,
        )
        return await self._build_freshness_outcome(
            child_task_id=child_task_id,
            child_thread_id=child_thread_id,
            child_worker_result=child_result,
            parent_work_id=parent_work.work_id,
        )

    async def _build_freshness_outcome(
        self,
        *,
        child_task_id: str,
        child_thread_id: str,
        child_worker_result: WorkerResult | None,
        parent_work_id: str,
    ) -> ButlerFreshnessOutcome:
        child_task = await self._stores.task_store.get_task(child_task_id)
        child_status = child_task.status if child_task is not None else TaskStatus.FAILED
        child_work = await self._find_primary_work(
            task_id=child_task_id,
            parent_work_id=parent_work_id,
        )
        latest_output = await self._latest_model_output(child_task_id)
        latest_failure = await self._latest_model_failure(child_task_id)
        worker_status = (
            child_worker_result.status
            if child_worker_result is not None
            else self._worker_status_from_task_status(child_status)
        )
        child_route_reason = child_work.route_reason if child_work is not None else ""
        child_tool_profile = "standard"
        child_a2a_conversation_id = ""
        child_worker_id = child_worker_result.worker_id if child_worker_result is not None else ""
        child_source_agent_session_id = ""
        child_target_agent_session_id = ""
        child_a2a_message_count = 0
        if child_work is not None:
            child_tool_profile = str(child_work.metadata.get("requested_tool_profile", "")).strip() or str(
                child_work.metadata.get("tool_profile", "")
            ).strip() or "standard"
            child_a2a_conversation_id = str(
                child_work.metadata.get("a2a_conversation_id", "")
            ).strip()
            child_source_agent_session_id = str(
                child_work.metadata.get("source_agent_session_id", "")
            ).strip()
            child_target_agent_session_id = str(
                child_work.metadata.get("target_agent_session_id", "")
            ).strip()
            child_a2a_message_count = int(child_work.metadata.get("a2a_message_count", 0) or 0)
            if not child_worker_id:
                child_worker_id = str(child_work.runtime_id or "").strip()
        error_summary = ""
        if child_status != TaskStatus.SUCCEEDED:
            error_summary = self._first_non_empty(
                latest_failure["error_message"],
                child_worker_result.error_message if child_worker_result is not None else "",
                latest_output["summary"],
                f"research child task ended with {child_status.value}",
            )
        return ButlerFreshnessOutcome(
            child_task_id=child_task_id,
            child_thread_id=child_thread_id,
            child_work_id=child_work.work_id if child_work is not None else "",
            child_status=child_status,
            child_worker_status=worker_status,
            child_worker_id=child_worker_id,
            child_route_reason=child_route_reason,
            child_tool_profile=child_tool_profile,
            child_a2a_conversation_id=child_a2a_conversation_id,
            child_source_agent_session_id=child_source_agent_session_id,
            child_target_agent_session_id=child_target_agent_session_id,
            child_a2a_message_count=child_a2a_message_count,
            result_artifact_id=latest_output["artifact_id"],
            result_text=latest_output["content"],
            result_summary=latest_output["summary"],
            error_summary=error_summary,
        )

    async def _finalize_internal_task_job(
        self,
        child_task_id: str,
        status: TaskStatus,
    ) -> None:
        if status == TaskStatus.SUCCEEDED:
            await self._stores.task_job_store.mark_succeeded(child_task_id)
            return
        if status == TaskStatus.CANCELLED:
            await self._stores.task_job_store.mark_cancelled(child_task_id)
            return
        if status == TaskStatus.WAITING_INPUT:
            await self._stores.task_job_store.mark_waiting_input(child_task_id)
            return
        if status == TaskStatus.WAITING_APPROVAL:
            await self._stores.task_job_store.mark_waiting_approval(child_task_id)
            return
        if status == TaskStatus.PAUSED:
            await self._stores.task_job_store.mark_paused(child_task_id)
            return
        await self._stores.task_job_store.mark_failed(
            child_task_id,
            f"freshness_child_terminal_status:{status.value}",
        )

    async def _latest_user_event_id(self, task_id: str) -> str:
        events = await self._stores.event_store.get_events_for_task(task_id)
        for event in reversed(events):
            if event.type is EventType.USER_MESSAGE:
                return event.event_id
        return ""

    async def _latest_model_output(self, task_id: str) -> dict[str, str]:
        events = await self._stores.event_store.get_events_for_task(task_id)
        for event in reversed(events):
            if event.type is not EventType.MODEL_CALL_COMPLETED:
                continue
            artifact_id = str(event.payload.get("artifact_ref", "") or "").strip()
            summary = str(event.payload.get("response_summary", "") or "").strip()
            content = ""
            if artifact_id:
                payload = await self._stores.artifact_store.get_artifact_content(artifact_id)
                if payload is not None:
                    content = payload.decode("utf-8", errors="ignore")
            return {
                "artifact_id": artifact_id,
                "summary": summary,
                "content": content,
            }
        return {"artifact_id": "", "summary": "", "content": ""}

    async def _latest_model_failure(self, task_id: str) -> dict[str, str]:
        events = await self._stores.event_store.get_events_for_task(task_id)
        for event in reversed(events):
            if event.type is not EventType.MODEL_CALL_FAILED:
                continue
            return {
                "error_type": str(event.payload.get("error_type", "") or "").strip(),
                "error_message": str(event.payload.get("error_message", "") or "").strip(),
            }
        return {"error_type": "", "error_message": ""}

    async def _find_primary_work(
        self,
        *,
        task_id: str,
        parent_work_id: str,
    ) -> Work | None:
        works = await self._stores.work_store.list_works(task_id=task_id)
        if not works:
            return None
        if parent_work_id:
            for work in works:
                if (work.parent_work_id or "") == parent_work_id:
                    return work
        return works[-1]

    async def _patch_work_metadata(
        self,
        work_id: str,
        *,
        metadata_updates: dict[str, Any],
    ) -> None:
        work = await self._stores.work_store.get_work(work_id)
        if work is None:
            return
        await self._stores.work_store.save_work(
            work.model_copy(
                update={
                    "metadata": {
                        **work.metadata,
                        **metadata_updates,
                    },
                    "updated_at": datetime.now(tz=UTC),
                }
            )
        )
        await self._stores.conn.commit()

    def _build_freshness_delegate_objective(self, user_text: str) -> str:
        objective = user_text.strip() or "请处理这次 freshness 查询"
        return (
            "Butler 委派给你的 research 子任务如下。\n"
            "请把它当作一次需要实时/外部事实核验的查询，"
            "优先使用受治理 web.search / web.fetch / browser.* 收集证据；"
            "如果缺关键参数，就明确指出缺什么；"
            "最终输出请直接给出结论、关键证据、来源限制与不确定性，不要输出执行计划。\n\n"
            f"原始用户问题：{objective}"
        )

    @classmethod
    def _requires_freshness_location_clarification(cls, user_text: str) -> bool:
        normalized = user_text.strip()
        if not normalized:
            return False
        lowered = normalized.lower()
        if not any(token in lowered for token in _WEATHER_QUERY_TOKENS):
            return False
        return not cls._contains_explicit_location(normalized)

    @classmethod
    def _contains_explicit_location(cls, user_text: str) -> bool:
        normalized = user_text.strip()
        if not normalized:
            return False
        lowered = normalized.lower()
        if _LOCATION_SUFFIX_PATTERN.search(normalized) or _EN_LOCATION_PATTERN.search(normalized):
            return True
        if any(token in normalized for token in _DIRECT_LOCATION_TOKENS):
            return True

        weather_anchor = min(
            (
                lowered.find(token)
                for token in _WEATHER_QUERY_TOKENS
                if lowered.find(token) >= 0
            ),
            default=-1,
        )
        prefix = normalized if weather_anchor < 0 else normalized[:weather_anchor]
        for token in _WEATHER_LOCATION_STOPWORDS:
            prefix = prefix.replace(token, "")
        prefix = re.sub(r"[，,。？！!?:：\s]", "", prefix)
        candidates = re.findall(r"[\u4e00-\u9fff]{2,8}", prefix)
        return any(candidate not in _WEATHER_LOCATION_STOPWORDS for candidate in candidates)

    @staticmethod
    def _build_missing_location_reply(user_text: str) -> str:
        question = user_text.strip() or "这条天气问题"
        return (
            f"我可以继续帮你查实时天气，但这条问题还缺少**城市 / 区县**信息：{question}\n\n"
            "你可以直接回我一个城市 / 区县，例如：`深圳`、`北京朝阳区`；"
            "也可以一次性把完整问题发成 `深圳今天天气怎么样`。\n"
            "你一补充位置，我就按 Butler -> Research Worker 的受治理链路继续查，不会把它当成“系统没有实时能力”。"
        )

    @classmethod
    def _build_weather_location_followup_query(
        cls,
        *,
        location_text: str,
        original_user_text: str,
    ) -> str:
        location = location_text.strip()
        original = original_user_text.strip() or "今天天气怎么样？"
        if not location:
            return original
        if cls._contains_explicit_location(original):
            return original
        normalized_original = re.sub(r"^[，,。？！!?:：\s]+", "", original)
        return f"{location}，{normalized_original}"

    @staticmethod
    def _extract_freshness_followup_metadata(metadata: dict[str, Any]) -> dict[str, str]:
        keys = (
            "freshness_followup_mode",
            "freshness_followup_source_work_id",
            "freshness_followup_original_question",
            "freshness_followup_location_text",
        )
        extracted: dict[str, str] = {}
        for key in keys:
            value = str(metadata.get(key, "")).strip()
            if value:
                extracted[key] = value
        return extracted

    @staticmethod
    def _is_backend_unavailable_outcome(outcome: ButlerFreshnessOutcome) -> bool:
        if outcome.child_status == TaskStatus.SUCCEEDED:
            return False
        normalized = f"{outcome.error_summary}\n{outcome.result_summary}\n{outcome.result_text}".lower()
        backend_tokens = (
            "web search failed",
            "web fetch failed",
            "browser_controller_missing",
            "desktop_session_unavailable",
            "browser backend",
            "network is unreachable",
            "name or service not known",
            "nodename nor servname",
            "connection refused",
            "connecterror",
            "readtimeout",
            "connecttimeout",
            "dns",
            "tls",
            "ssl",
            "temporary failure in name resolution",
            "httpx",
            "duckduckgo",
            "tool backend unavailable",
        )
        return any(token in normalized for token in backend_tokens)

    @staticmethod
    def _build_backend_unavailable_reply(
        *,
        user_text: str,
        outcome: ButlerFreshnessOutcome,
    ) -> str:
        reason = outcome.error_summary.strip() or "当前外部取证后端暂时不可用"
        question = user_text.strip() or "这条实时查询"
        return (
            f"我本来已经把这条实时问题转给内部 Research Worker 去联网取证了，但当前用于外部核验的 **web/browser 后端暂时不可用**，所以这次没法可靠查完：{question}\n\n"
            f"当前限制：{reason}\n\n"
            "这属于**当前工具后端 / 运行环境限制**，不代表系统整体没有实时查询能力。\n"
            "你可以稍后重试，或者直接把网页链接 / 截图 / 更明确的查询对象发给我，我先基于你提供的材料继续处理。"
        )

    def _build_freshness_handoff_artifact_content(
        self,
        *,
        user_text: str,
        outcome: ButlerFreshnessOutcome,
    ) -> str:
        payload = {
            "mode": "butler_owned_freshness",
            "user_text": user_text,
            "child_task_id": outcome.child_task_id,
            "child_thread_id": outcome.child_thread_id,
            "child_work_id": outcome.child_work_id,
            "child_status": outcome.child_status.value,
            "child_worker_status": outcome.child_worker_status.value,
            "child_worker_id": outcome.child_worker_id,
            "child_route_reason": outcome.child_route_reason,
            "child_tool_profile": outcome.child_tool_profile,
            "child_a2a_conversation_id": outcome.child_a2a_conversation_id,
            "result_artifact_id": outcome.result_artifact_id,
            "result_summary": outcome.result_summary,
            "result_text": outcome.result_text,
            "error_summary": outcome.error_summary,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _butler_worker_result(
        self,
        *,
        request: OrchestratorRequest,
        task_status: TaskStatus,
        success_summary: str = "butler_freshness_synthesized",
    ) -> WorkerResult:
        if task_status == TaskStatus.SUCCEEDED:
            return WorkerResult(
                dispatch_id=f"butler-freshness:{request.task_id}",
                task_id=request.task_id,
                worker_id="butler.main",
                status=WorkerExecutionStatus.SUCCEEDED,
                retryable=False,
                summary=success_summary,
                backend="inline",
                tool_profile="minimal",
            )
        if task_status == TaskStatus.CANCELLED:
            return WorkerResult(
                dispatch_id=f"butler-freshness:{request.task_id}",
                task_id=request.task_id,
                worker_id="butler.main",
                status=WorkerExecutionStatus.CANCELLED,
                retryable=False,
                summary="butler_freshness_cancelled",
                backend="inline",
                tool_profile="minimal",
            )
        return WorkerResult(
            dispatch_id=f"butler-freshness:{request.task_id}",
            task_id=request.task_id,
            worker_id="butler.main",
            status=WorkerExecutionStatus.FAILED,
            retryable=True,
            summary=f"butler_freshness_terminal:{task_status.value}",
            error_type="ButlerFreshnessSynthesisFailed",
            error_message=f"task status={task_status.value}",
            backend="inline",
            tool_profile="minimal",
        )

    def _join_route_reason(self, *parts: str) -> str:
        normalized = [part.strip() for part in parts if part and part.strip()]
        return " | ".join(dict.fromkeys(normalized))

    @staticmethod
    def _worker_status_from_task_status(task_status: TaskStatus) -> WorkerExecutionStatus:
        mapping = {
            TaskStatus.SUCCEEDED: WorkerExecutionStatus.SUCCEEDED,
            TaskStatus.CANCELLED: WorkerExecutionStatus.CANCELLED,
        }
        return mapping.get(task_status, WorkerExecutionStatus.FAILED)

    @staticmethod
    def _work_status_from_task_status(task_status: TaskStatus) -> WorkStatus:
        mapping = {
            TaskStatus.SUCCEEDED: WorkStatus.SUCCEEDED,
            TaskStatus.CANCELLED: WorkStatus.CANCELLED,
        }
        return mapping.get(task_status, WorkStatus.FAILED)

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
