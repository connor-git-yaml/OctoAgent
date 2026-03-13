"""Adapters between current gateway/core models and A2A-Lite contract."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from octoagent.core.models import (
    Artifact,
    DispatchEnvelope,
    TaskStatus,
    WorkerExecutionStatus,
    WorkerResult,
    WorkerSession,
)

from .mappers import A2AArtifactMapper, A2AStateMapper
from .models import (
    A2ACancelPayload,
    A2AErrorPayload,
    A2AHeartbeatPayload,
    A2AMessage,
    A2AMessageMetadata,
    A2AMessageType,
    A2AResultPayload,
    A2ATaskPayload,
    A2ATaskState,
    A2ATraceContext,
    A2AUpdatePayload,
)


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def build_task_message(
    envelope: DispatchEnvelope,
    *,
    context_id: str,
    from_agent: str = "agent://kernel",
    to_agent: str | None = None,
    idempotency_key: str | None = None,
    timestamp_ms: int | None = None,
) -> A2AMessage:
    receiver = to_agent or f"agent://{envelope.worker_capability}"
    return A2AMessage(
        schema_version="0.1",
        message_id=envelope.dispatch_id,
        task_id=envelope.task_id,
        context_id=context_id,
        from_agent=from_agent,
        to_agent=receiver,
        type=A2AMessageType.TASK,
        idempotency_key=idempotency_key or f"{envelope.task_id}:{envelope.dispatch_id}:task",
        timestamp_ms=timestamp_ms or _now_ms(),
        payload=A2ATaskPayload(
            user_text=envelope.user_text,
            metadata=envelope.metadata,
            resume_from_node=envelope.resume_from_node,
            resume_state_snapshot=envelope.resume_state_snapshot,
        ),
        trace=A2ATraceContext(trace_id=envelope.trace_id),
        metadata=A2AMessageMetadata(
            hop_count=envelope.hop_count,
            max_hops=envelope.max_hops,
            route_reason=envelope.route_reason,
            worker_capability=envelope.worker_capability,
            tool_profile=envelope.tool_profile,
            model_alias=envelope.model_alias,
        ),
    )


def dispatch_envelope_from_task_message(message: A2AMessage) -> DispatchEnvelope:
    if message.type != A2AMessageType.TASK:
        raise TypeError("dispatch_envelope_from_task_message requires TASK message")
    payload = message.payload
    if not isinstance(payload, A2ATaskPayload):
        raise TypeError("TASK message payload must be A2ATaskPayload")
    worker_capability = message.metadata.worker_capability or _capability_from_agent_uri(
        message.to_agent
    )
    return DispatchEnvelope(
        dispatch_id=message.message_id,
        task_id=message.task_id,
        trace_id=message.trace.trace_id,
        contract_version=message.schema_version,
        route_reason=message.metadata.route_reason or "a2a-task-message",
        worker_capability=worker_capability,
        hop_count=message.metadata.hop_count,
        max_hops=message.metadata.max_hops,
        user_text=payload.user_text,
        model_alias=message.metadata.model_alias,
        resume_from_node=payload.resume_from_node,
        resume_state_snapshot=payload.resume_state_snapshot,
        tool_profile=message.metadata.tool_profile or "standard",
        metadata=payload.metadata,
    )


def build_result_message(
    result: WorkerResult,
    *,
    context_id: str,
    trace_id: str,
    from_agent: str | None = None,
    to_agent: str = "agent://kernel",
    artifacts: Iterable[Artifact] = (),
    idempotency_key: str | None = None,
    timestamp_ms: int | None = None,
) -> A2AMessage:
    state = _worker_status_to_a2a_state(result.status)
    mapped_artifacts = [A2AArtifactMapper.to_a2a(artifact) for artifact in artifacts]
    return A2AMessage(
        schema_version="0.1",
        message_id=f"{result.dispatch_id}-result",
        task_id=result.task_id,
        context_id=context_id,
        from_agent=from_agent or f"agent://{result.worker_id}",
        to_agent=to_agent,
        type=A2AMessageType.RESULT,
        idempotency_key=idempotency_key or f"{result.task_id}:{result.dispatch_id}:result",
        timestamp_ms=timestamp_ms or _now_ms(),
        payload=A2AResultPayload(
            state=state,
            worker_id=result.worker_id,
            summary=result.summary,
            artifacts=mapped_artifacts,
            retryable=result.retryable,
            backend=result.backend,
            tool_profile=result.tool_profile,
        ),
        trace=A2ATraceContext(trace_id=trace_id),
        metadata=A2AMessageMetadata(
            final=True,
            retryable=result.retryable,
            backend=result.backend,
            loop_step=result.loop_step,
            max_steps=result.max_steps,
        ),
    )


def build_update_message(
    *,
    task_id: str,
    context_id: str,
    trace_id: str,
    from_agent: str,
    to_agent: str,
    state: TaskStatus = TaskStatus.RUNNING,
    summary: str = "",
    requested_input: str | None = None,
    idempotency_key: str,
    message_id: str | None = None,
    timestamp_ms: int | None = None,
    backend: str | None = None,
    loop_step: int | None = None,
    max_steps: int | None = None,
) -> A2AMessage:
    return A2AMessage(
        schema_version="0.1",
        message_id=message_id or f"{task_id}-update-{_now_ms()}",
        task_id=task_id,
        context_id=context_id,
        from_agent=from_agent,
        to_agent=to_agent,
        type=A2AMessageType.UPDATE,
        idempotency_key=idempotency_key,
        timestamp_ms=timestamp_ms or _now_ms(),
        payload=A2AUpdatePayload(
            state=A2AStateMapper.to_a2a(state),
            summary=summary,
            requested_input=requested_input,
        ),
        trace=A2ATraceContext(trace_id=trace_id),
        metadata=A2AMessageMetadata(
            internal_status=state,
            backend=backend,
            loop_step=loop_step,
            max_steps=max_steps,
            final=False,
        ),
    )


def build_error_message(
    result: WorkerResult,
    *,
    context_id: str,
    trace_id: str,
    from_agent: str | None = None,
    to_agent: str = "agent://kernel",
    idempotency_key: str | None = None,
    timestamp_ms: int | None = None,
) -> A2AMessage:
    error_state = (
        A2ATaskState.CANCELED
        if result.status == WorkerExecutionStatus.CANCELLED
        else A2ATaskState.FAILED
    )
    return A2AMessage(
        schema_version="0.1",
        message_id=f"{result.dispatch_id}-error",
        task_id=result.task_id,
        context_id=context_id,
        from_agent=from_agent or f"agent://{result.worker_id}",
        to_agent=to_agent,
        type=A2AMessageType.ERROR,
        idempotency_key=idempotency_key or f"{result.task_id}:{result.dispatch_id}:error",
        timestamp_ms=timestamp_ms or _now_ms(),
        payload=A2AErrorPayload(
            state=error_state,
            error_type=result.error_type or "WorkerRuntimeError",
            error_message=result.error_message or result.summary,
            retryable=result.retryable,
        ),
        trace=A2ATraceContext(trace_id=trace_id),
        metadata=A2AMessageMetadata(
            final=error_state in {A2ATaskState.CANCELED, A2ATaskState.FAILED},
            retryable=result.retryable,
            backend=result.backend,
        ),
    )


def build_cancel_message(
    *,
    task_id: str,
    context_id: str,
    trace_id: str,
    from_agent: str = "agent://kernel",
    to_agent: str,
    reason: str,
    idempotency_key: str,
    timestamp_ms: int | None = None,
) -> A2AMessage:
    return A2AMessage(
        schema_version="0.1",
        message_id=f"{task_id}-cancel",
        task_id=task_id,
        context_id=context_id,
        from_agent=from_agent,
        to_agent=to_agent,
        type=A2AMessageType.CANCEL,
        idempotency_key=idempotency_key,
        timestamp_ms=timestamp_ms or _now_ms(),
        payload=A2ACancelPayload(reason=reason),
        trace=A2ATraceContext(trace_id=trace_id),
        metadata=A2AMessageMetadata(final=False),
    )


def build_heartbeat_message(
    session: WorkerSession,
    *,
    context_id: str,
    trace_id: str,
    from_agent: str | None = None,
    to_agent: str = "agent://kernel",
    state: TaskStatus = TaskStatus.RUNNING,
    summary: str = "",
    idempotency_key: str | None = None,
    timestamp_ms: int | None = None,
) -> A2AMessage:
    message_root = session.dispatch_id or session.task_id or "worker"
    return A2AMessage(
        schema_version="0.1",
        message_id=f"{message_root}-heartbeat-{session.loop_step}",
        task_id=session.task_id,
        context_id=context_id,
        from_agent=from_agent or f"agent://{session.worker_id}",
        to_agent=to_agent,
        type=A2AMessageType.HEARTBEAT,
        idempotency_key=(
            idempotency_key
            or f"{session.task_id}:{message_root}:heartbeat:{session.loop_step}"
        ),
        timestamp_ms=timestamp_ms or _now_ms(),
        payload=A2AHeartbeatPayload(
            state=A2AStateMapper.to_a2a(state),
            worker_id=session.worker_id,
            loop_step=session.loop_step,
            max_steps=session.max_steps,
            summary=summary,
            backend=session.backend,
        ),
        trace=A2ATraceContext(trace_id=trace_id),
        metadata=A2AMessageMetadata(
            backend=session.backend,
            internal_status=state,
            loop_step=session.loop_step,
            max_steps=session.max_steps,
            final=False,
        ),
    )


def _worker_status_to_a2a_state(status: WorkerExecutionStatus) -> str:
    if status == WorkerExecutionStatus.SUCCEEDED:
        return A2ATaskState.COMPLETED
    if status == WorkerExecutionStatus.CANCELLED:
        return A2ATaskState.CANCELED
    return A2ATaskState.FAILED


def _capability_from_agent_uri(agent_uri: str) -> str:
    if agent_uri.startswith("agent://"):
        return agent_uri[len("agent://") :]
    return agent_uri
