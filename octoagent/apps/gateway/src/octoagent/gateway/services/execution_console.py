"""Execution console service for Feature 019."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

import structlog
from octoagent.core.models import (
    ActorType,
    Artifact,
    EventType,
    ExecutionBackend,
    ExecutionConsoleSession,
    ExecutionEventKind,
    ExecutionSessionState,
    ExecutionStreamEvent,
    HumanInputPolicy,
    TaskStatus,
)
from octoagent.core.models.payloads import (
    ExecutionCancelRequestedPayload,
    ExecutionInputAttachedPayload,
    ExecutionInputRequestedPayload,
    ExecutionLogPayload,
    ExecutionStatusChangedPayload,
    ExecutionStepPayload,
)
from octoagent.policy.models import ApprovalDecision, ApprovalRequest, ApprovalStatus
from octoagent.tooling.models import SideEffectLevel
from ulid import ULID

from .task_service import TaskService

log = structlog.get_logger()


class ExecutionInputError(RuntimeError):
    """Execution input 基础异常。"""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        approval_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.approval_id = approval_id


@dataclass
class PendingInputRequest:
    """当前 live input request。"""

    request_id: str
    prompt: str
    approval_id: str | None
    queue: asyncio.Queue[str]


@dataclass
class LiveExecutionState:
    """进程内 live execution state。"""

    session: ExecutionConsoleSession
    log_index: int = 0
    current_request: PendingInputRequest | None = None


@dataclass(frozen=True)
class AttachInputResult:
    """attach_input 执行结果。"""

    task_id: str
    session_id: str
    request_id: str
    artifact_id: str
    delivered_live: bool
    approval_id: str | None = None


class ExecutionConsoleA2ANotifier(Protocol):
    """Execution console -> A2A durable 同步接口。"""

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
        """记录 worker -> butler 的 WAITING_INPUT update。"""

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
        """记录 butler -> worker 的 resume update。"""


class ExecutionConsoleService:
    """Execution 控制台服务。"""

    def __init__(
        self,
        store_group,
        sse_hub,
        *,
        approval_manager=None,
        a2a_notifier: ExecutionConsoleA2ANotifier | None = None,
    ) -> None:
        self._stores = store_group
        self._sse_hub = sse_hub
        self._approval_manager = approval_manager
        self._a2a_notifier = a2a_notifier
        self._live_sessions: dict[str, LiveExecutionState] = {}

    def bind_a2a_notifier(self, notifier: ExecutionConsoleA2ANotifier | None) -> None:
        """延迟绑定 A2A notifier，避免构造期循环依赖。"""
        self._a2a_notifier = notifier

    async def register_session(
        self,
        *,
        task_id: str,
        session_id: str,
        backend_job_id: str,
        interactive: bool,
        input_policy: HumanInputPolicy,
        backend: ExecutionBackend = ExecutionBackend.DOCKER,
        worker_id: str = "",
        metadata: dict[str, str] | None = None,
        message: str = "",
    ) -> ExecutionConsoleSession:
        """注册或覆盖当前 task 的 execution session。"""
        now = datetime.now(UTC)
        session = ExecutionConsoleSession(
            session_id=session_id,
            task_id=task_id,
            backend=backend,
            backend_job_id=backend_job_id,
            state=ExecutionSessionState.RUNNING,
            interactive=interactive,
            input_policy=input_policy,
            started_at=now,
            updated_at=now,
            live=True,
            can_attach_input=False,
            can_cancel=True,
            metadata={"worker_id": worker_id, **(metadata or {})},
        )
        self._live_sessions[task_id] = LiveExecutionState(session=session)
        await self._append_status_event(
            task_id=task_id,
            session=session,
            status=ExecutionSessionState.RUNNING,
            message=message or "execution session started",
        )
        return session

    async def mark_status(
        self,
        *,
        task_id: str,
        session_id: str,
        status: ExecutionSessionState,
        message: str = "",
    ) -> None:
        """更新 execution session 状态并写事件。"""
        session = await self._get_or_project_session(task_id)
        if session is None or session.session_id != session_id:
            return
        session.state = status
        session.updated_at = datetime.now(UTC)
        session.live = status not in {
            ExecutionSessionState.SUCCEEDED,
            ExecutionSessionState.FAILED,
            ExecutionSessionState.CANCELLED,
        }
        session.can_cancel = session.live
        if not session.live:
            session.finished_at = session.updated_at
            session.can_attach_input = False
        await self._append_status_event(
            task_id=task_id,
            session=session,
            status=status,
            message=message,
        )
        if not session.live:
            self._live_sessions.pop(task_id, None)

    async def emit_log(
        self,
        *,
        task_id: str,
        session_id: str,
        stream: str,
        chunk: str,
    ) -> None:
        """写 execution log 事件。"""
        state = self._live_sessions.get(task_id)
        if state is None or state.session.session_id != session_id:
            return
        state.log_index += 1
        service = TaskService(self._stores, self._sse_hub)
        await service.append_structured_event(
            task_id=task_id,
            event_type=EventType.EXECUTION_LOG,
            actor=ActorType.WORKER,
            payload=ExecutionLogPayload(
                session_id=session_id,
                stream=stream,
                chunk=chunk,
                chunk_index=state.log_index,
            ).model_dump(mode="json"),
        )

    async def emit_step(
        self,
        *,
        task_id: str,
        session_id: str,
        step_name: str,
        summary: str = "",
    ) -> None:
        """写 execution step 事件。"""
        state = self._live_sessions.get(task_id)
        if state is None or state.session.session_id != session_id:
            return
        state.session.current_step = step_name
        state.session.updated_at = datetime.now(UTC)
        if summary:
            state.session.metadata["step_summary"] = summary
        service = TaskService(self._stores, self._sse_hub)
        await service.append_structured_event(
            task_id=task_id,
            event_type=EventType.EXECUTION_STEP,
            actor=ActorType.WORKER,
            payload=ExecutionStepPayload(
                session_id=session_id,
                step_name=step_name,
                summary=summary,
            ).model_dump(mode="json"),
        )

    async def request_input(
        self,
        *,
        task_id: str,
        session_id: str,
        prompt: str,
        actor: str,
        approval_required: bool = False,
    ) -> str:
        """请求人工输入并等待 attach_input。"""
        state = self._live_sessions.get(task_id)
        if state is None or state.session.session_id != session_id:
            raise ExecutionInputError(
                "execution session is not live",
                code="SESSION_NOT_LIVE",
            )

        approval_id = None
        if approval_required or await self._task_requires_approval(task_id):
            approval_id = await self._register_input_approval(
                task_id=task_id,
                prompt=prompt,
                actor=actor,
            )

        request = PendingInputRequest(
            request_id=str(ULID()),
            prompt=prompt,
            approval_id=approval_id,
            queue=asyncio.Queue(maxsize=1),
        )
        state.current_request = request
        state.session.state = ExecutionSessionState.WAITING_INPUT
        state.session.requested_input = prompt
        state.session.pending_approval_id = approval_id
        state.session.updated_at = datetime.now(UTC)
        state.session.can_attach_input = True
        state.session.can_cancel = True

        service = TaskService(self._stores, self._sse_hub)
        task = await service.get_task(task_id)
        if task is not None and task.status == TaskStatus.RUNNING:
            await service._write_state_transition(
                task_id=task_id,
                from_status=TaskStatus.RUNNING,
                to_status=TaskStatus.WAITING_INPUT,
                trace_id=f"trace-{task_id}",
                reason="execution_console_input_requested",
            )
        await self._stores.task_job_store.mark_waiting_input(task_id)
        await service.append_structured_event(
            task_id=task_id,
            event_type=EventType.EXECUTION_INPUT_REQUESTED,
            actor=ActorType.WORKER,
            payload=ExecutionInputRequestedPayload(
                session_id=session_id,
                prompt=prompt,
                request_id=request.request_id,
                approval_id=approval_id,
            ).model_dump(mode="json"),
        )
        await self._append_status_event(
            task_id=task_id,
            session=state.session,
            status=ExecutionSessionState.WAITING_INPUT,
            message="waiting for human input",
        )
        if self._a2a_notifier is not None:
            try:
                await self._a2a_notifier.record_waiting_input(
                    task_id=task_id,
                    session_id=session_id,
                    prompt=prompt,
                    request_id=request.request_id,
                    approval_id=approval_id,
                    worker_id=str(state.session.metadata.get("worker_id", "")),
                    work_id=str(state.session.metadata.get("work_id", "")),
                )
            except Exception as exc:  # pragma: no cover - A2A 审计失败不阻塞人工接管
                log.warning(
                    "execution_console_a2a_waiting_input_failed",
                    task_id=task_id,
                    session_id=session_id,
                    error_type=type(exc).__name__,
                )

        try:
            return await request.queue.get()
        finally:
            state.current_request = None
            state.session.requested_input = None
            state.session.pending_approval_id = None
            state.session.can_attach_input = False

    async def attach_input(
        self,
        *,
        task_id: str,
        text: str,
        actor: str,
        approval_id: str | None = None,
    ) -> AttachInputResult:
        """接纳人工输入；live waiter 存在时直接投递，否则走恢复路径。"""
        task = await self._stores.task_store.get_task(task_id)
        if task is None:
            raise ExecutionInputError("task not found", code="TASK_NOT_FOUND")
        if task.status != TaskStatus.WAITING_INPUT:
            raise ExecutionInputError(
                "task is not waiting for human input",
                code="TASK_NOT_WAITING_INPUT",
            )

        session, pending_request = await self._resolve_pending_request(task_id)
        if pending_request is None:
            raise ExecutionInputError(
                "no pending input request found",
                code="INPUT_REQUEST_NOT_FOUND",
            )

        await self._ensure_input_approval(
            pending_request.approval_id,
            approval_id,
        )

        preview = text[:80]
        service = TaskService(self._stores, self._sse_hub)
        artifact = await service.create_text_artifact(
            task_id=task_id,
            name="human-input",
            description=f"执行控制台输入: {pending_request.request_id}",
            content=text,
            trace_id=f"trace-{task_id}",
            session_id=session.session_id,
            source="human-input",
        )
        await service.append_structured_event(
            task_id=task_id,
            event_type=EventType.EXECUTION_INPUT_ATTACHED,
            actor=ActorType.USER,
            payload=ExecutionInputAttachedPayload(
                session_id=session.session_id,
                request_id=pending_request.request_id,
                actor=actor,
                preview=preview,
                text_length=len(text),
                approval_id=pending_request.approval_id,
                artifact_id=artifact.artifact_id,
                attached_at=datetime.now(UTC),
            ).model_dump(mode="json"),
        )
        await service._write_state_transition(
            task_id=task_id,
            from_status=TaskStatus.WAITING_INPUT,
            to_status=TaskStatus.RUNNING,
            trace_id=f"trace-{task_id}",
            reason="execution_console_input_attached",
        )
        session.state = ExecutionSessionState.RUNNING
        session.updated_at = datetime.now(UTC)
        session.latest_artifact_id = artifact.artifact_id
        session.pending_approval_id = None
        session.requested_input = None
        session.can_attach_input = False
        await self._append_status_event(
            task_id=task_id,
            session=session,
            status=ExecutionSessionState.RUNNING,
            message="human input attached",
        )

        delivered_live = False
        live_state = self._live_sessions.get(task_id)
        if (
            live_state is not None
            and live_state.current_request is not None
            and live_state.current_request.request_id == pending_request.request_id
        ):
            await self._stores.task_job_store.mark_running_from_waiting_input(task_id)
            live_state.session.state = ExecutionSessionState.RUNNING
            live_state.session.requested_input = None
            live_state.session.pending_approval_id = None
            live_state.session.latest_artifact_id = artifact.artifact_id
            live_state.session.updated_at = datetime.now(UTC)
            live_state.session.can_attach_input = False
            live_state.session.can_cancel = True
            await live_state.current_request.queue.put(text)
            delivered_live = True

        if self._a2a_notifier is not None:
            try:
                await self._a2a_notifier.record_input_attached(
                    task_id=task_id,
                    session_id=session.session_id,
                    request_id=pending_request.request_id,
                    artifact_id=artifact.artifact_id,
                    actor=actor,
                    worker_id=str(session.metadata.get("worker_id", "")),
                    work_id=str(session.metadata.get("work_id", "")),
                )
            except Exception as exc:  # pragma: no cover - A2A 审计失败不影响恢复
                log.warning(
                    "execution_console_a2a_input_attached_failed",
                    task_id=task_id,
                    session_id=session.session_id,
                    error_type=type(exc).__name__,
                )

        return AttachInputResult(
            task_id=task_id,
            session_id=session.session_id,
            request_id=pending_request.request_id,
            artifact_id=artifact.artifact_id,
            delivered_live=delivered_live,
            approval_id=pending_request.approval_id,
        )

    async def record_cancel_request(
        self,
        *,
        task_id: str,
        actor: str,
        reason: str,
    ) -> None:
        """写 execution cancel requested 事件。"""
        session = await self.get_session(task_id)
        if session is None:
            return
        service = TaskService(self._stores, self._sse_hub)
        await service.append_structured_event(
            task_id=task_id,
            event_type=EventType.EXECUTION_CANCEL_REQUESTED,
            actor=ActorType.USER,
            payload=ExecutionCancelRequestedPayload(
                session_id=session.session_id,
                actor=actor,
                reason=reason,
            ).model_dump(mode="json"),
        )

    async def get_session(self, task_id: str) -> ExecutionConsoleSession | None:
        """查询当前 execution session 视图。"""
        live_state = self._live_sessions.get(task_id)
        if live_state is not None:
            session = live_state.session.model_copy(deep=True)
            session.latest_artifact_id = await self._latest_artifact_id(task_id)
            session.live = True
            session.can_attach_input = live_state.current_request is not None
            session.can_cancel = True
            return session

        return await self._project_session(task_id)

    async def list_execution_events(
        self,
        task_id: str,
        *,
        session_id: str | None = None,
    ) -> list[ExecutionStreamEvent]:
        """返回当前 execution session 的统一事件视图。"""
        active_session_id = session_id
        if active_session_id is None:
            session = await self.get_session(task_id)
            if session is None:
                return []
            active_session_id = session.session_id

        if not active_session_id:
            return []

        events = await self._stores.event_store.get_events_for_task(task_id)
        result: list[ExecutionStreamEvent] = []
        for event in events:
            stream_event = self._to_stream_event(event)
            if stream_event is None:
                continue
            if stream_event.session_id != active_session_id:
                continue
            result.append(stream_event)
        return result

    async def collect_artifacts(self, task_id: str) -> list[Artifact]:
        """查询任务 artifacts。"""
        return await self._stores.artifact_store.list_artifacts_for_task(task_id)

    async def load_text_artifact(self, artifact_id: str) -> str | None:
        """读取文本 artifact 内容。"""
        content = await self._stores.artifact_store.get_artifact_content(artifact_id)
        if content is None:
            return None
        return content.decode("utf-8", errors="replace")

    async def _append_status_event(
        self,
        *,
        task_id: str,
        session: ExecutionConsoleSession,
        status: ExecutionSessionState,
        message: str,
    ) -> None:
        service = TaskService(self._stores, self._sse_hub)
        await service.append_structured_event(
            task_id=task_id,
            event_type=EventType.EXECUTION_STATUS_CHANGED,
            actor=ActorType.WORKER,
            payload=ExecutionStatusChangedPayload(
                session_id=session.session_id,
                backend=session.backend.value,
                backend_job_id=session.backend_job_id,
                status=status,
                interactive=session.interactive,
                input_policy=session.input_policy.value,
                runtime_dir=session.metadata.get("runtime_dir", ""),
                container_name=session.metadata.get("container_name", ""),
                message=message,
                metadata=session.metadata,
            ).model_dump(mode="json"),
        )

    async def _project_session(self, task_id: str) -> ExecutionConsoleSession | None:
        task = await self._stores.task_store.get_task(task_id)
        if task is None:
            return None
        events = await self._stores.event_store.get_events_for_task(task_id)
        latest_status_event = None
        latest_status_payload = None
        latest_session_id = ""
        for event in reversed(events):
            if event.type != EventType.EXECUTION_STATUS_CHANGED:
                continue
            payload = ExecutionStatusChangedPayload.model_validate(event.payload)
            latest_status_event = event
            latest_status_payload = payload
            latest_session_id = payload.session_id
            break

        if latest_status_payload is None or latest_status_event is None:
            return None

        started_at = latest_status_event.ts
        step_payload = None
        latest_requested = None
        latest_attached_request_id = None
        for event in events:
            stream_event = self._to_stream_event(event)
            if stream_event is None or stream_event.session_id != latest_session_id:
                continue
            if (
                event.type == EventType.EXECUTION_STATUS_CHANGED
                and stream_event.status == ExecutionSessionState.RUNNING
            ):
                started_at = event.ts
                break
        for event in reversed(events):
            stream_event = self._to_stream_event(event)
            if stream_event is None or stream_event.session_id != latest_session_id:
                continue
            if step_payload is None and event.type == EventType.EXECUTION_STEP:
                step_payload = ExecutionStepPayload.model_validate(event.payload)
            if (
                latest_attached_request_id is None
                and event.type == EventType.EXECUTION_INPUT_ATTACHED
            ):
                latest_attached_request_id = ExecutionInputAttachedPayload.model_validate(
                    event.payload
                ).request_id
            if latest_requested is None and event.type == EventType.EXECUTION_INPUT_REQUESTED:
                latest_requested = ExecutionInputRequestedPayload.model_validate(event.payload)
            if (
                step_payload is not None
                and latest_requested is not None
                and latest_attached_request_id is not None
            ):
                break
        pending_request = None
        if (
            latest_requested is not None
            and latest_requested.request_id != latest_attached_request_id
            and task.status == TaskStatus.WAITING_INPUT
        ):
            pending_request = latest_requested

        updated_at = task.updated_at
        metadata = dict(latest_status_payload.metadata)
        if latest_status_payload.runtime_dir:
            metadata.setdefault("runtime_dir", latest_status_payload.runtime_dir)
        if latest_status_payload.container_name:
            metadata.setdefault("container_name", latest_status_payload.container_name)
        session = ExecutionConsoleSession(
            session_id=latest_session_id,
            task_id=task_id,
            backend=ExecutionBackend(latest_status_payload.backend),
            backend_job_id=latest_status_payload.backend_job_id,
            state=self._map_task_to_execution_state(task.status),
            interactive=latest_status_payload.interactive,
            input_policy=HumanInputPolicy(latest_status_payload.input_policy),
            current_step=step_payload.step_name if step_payload else "",
            requested_input=(
                pending_request.prompt
                if pending_request is not None
                else None
            ),
            pending_approval_id=(
                pending_request.approval_id
                if pending_request is not None and pending_request.approval_id
                else None
            ),
            latest_artifact_id=await self._latest_artifact_id(task_id, latest_session_id),
            latest_event_seq=max(
                (
                    stream_event.seq
                    for event in events
                    if (stream_event := self._to_stream_event(event)) is not None
                    and stream_event.session_id == latest_session_id
                ),
                default=0,
            ),
            started_at=started_at,
            updated_at=updated_at,
            finished_at=updated_at if task.status in {
                TaskStatus.SUCCEEDED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
                TaskStatus.REJECTED,
            } else None,
            live=False,
            can_attach_input=task.status == TaskStatus.WAITING_INPUT,
            can_cancel=task.status not in {
                TaskStatus.SUCCEEDED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
                TaskStatus.REJECTED,
            },
            metadata=metadata,
        )
        return session

    async def _resolve_pending_request(
        self,
        task_id: str,
    ) -> tuple[ExecutionConsoleSession, PendingInputRequest | None]:
        live_state = self._live_sessions.get(task_id)
        if live_state is not None and live_state.current_request is not None:
            return live_state.session, live_state.current_request

        session = await self.get_session(task_id)
        if session is None:
            raise ExecutionInputError("execution session not found", code="SESSION_NOT_FOUND")

        events = await self._stores.event_store.get_events_for_task(task_id)
        latest_requested = None
        latest_attached_request_id = None
        for event in reversed(events):
            if (
                latest_attached_request_id is None
                and event.type == EventType.EXECUTION_INPUT_ATTACHED
            ):
                latest_attached_request_id = str(event.payload.get("request_id", ""))
            if latest_requested is None and event.type == EventType.EXECUTION_INPUT_REQUESTED:
                latest_requested = event.payload
            if latest_attached_request_id is not None and latest_requested is not None:
                break

        if latest_requested is None:
            return session, None

        request_id = str(latest_requested.get("request_id", ""))
        if latest_attached_request_id == request_id:
            return session, None

        return session, PendingInputRequest(
            request_id=request_id,
            prompt=str(latest_requested.get("prompt", "")),
            approval_id=(
                str(latest_requested.get("approval_id"))
                if latest_requested.get("approval_id")
                else None
            ),
            queue=asyncio.Queue(maxsize=1),
        )

    async def _get_or_project_session(self, task_id: str) -> ExecutionConsoleSession | None:
        live_state = self._live_sessions.get(task_id)
        if live_state is not None:
            return live_state.session
        return await self._project_session(task_id)

    def _to_stream_event(self, event) -> ExecutionStreamEvent | None:
        if event.type == EventType.EXECUTION_STATUS_CHANGED:
            payload = ExecutionStatusChangedPayload.model_validate(event.payload)
            return ExecutionStreamEvent(
                session_id=payload.session_id,
                task_id=event.task_id,
                event_id=event.event_id,
                seq=event.task_seq,
                kind=ExecutionEventKind.STATUS,
                message=payload.message,
                status=payload.status,
                ts=event.ts,
                final=payload.status
                in {
                    ExecutionSessionState.SUCCEEDED,
                    ExecutionSessionState.FAILED,
                    ExecutionSessionState.CANCELLED,
                },
                metadata=payload.metadata,
            )
        if event.type == EventType.EXECUTION_LOG:
            payload = ExecutionLogPayload.model_validate(event.payload)
            return ExecutionStreamEvent(
                session_id=payload.session_id,
                task_id=event.task_id,
                event_id=event.event_id,
                seq=event.task_seq,
                kind=ExecutionEventKind.STDOUT
                if payload.stream == "stdout"
                else ExecutionEventKind.STDERR,
                message=payload.chunk,
                stream=payload.stream,
                ts=event.ts,
            )
        if event.type == EventType.EXECUTION_STEP:
            payload = ExecutionStepPayload.model_validate(event.payload)
            return ExecutionStreamEvent(
                session_id=payload.session_id,
                task_id=event.task_id,
                event_id=event.event_id,
                seq=event.task_seq,
                kind=ExecutionEventKind.STEP,
                message=payload.summary or payload.step_name,
                ts=event.ts,
            )
        if event.type == EventType.EXECUTION_INPUT_REQUESTED:
            payload = ExecutionInputRequestedPayload.model_validate(event.payload)
            metadata = {"request_id": payload.request_id}
            if payload.approval_id:
                metadata["approval_id"] = payload.approval_id
            return ExecutionStreamEvent(
                session_id=payload.session_id,
                task_id=event.task_id,
                event_id=event.event_id,
                seq=event.task_seq,
                kind=ExecutionEventKind.INPUT_REQUESTED,
                message=payload.prompt,
                ts=event.ts,
                metadata=metadata,
            )
        if event.type == EventType.EXECUTION_INPUT_ATTACHED:
            payload = ExecutionInputAttachedPayload.model_validate(event.payload)
            metadata = {"request_id": payload.request_id, "actor": payload.actor}
            if payload.approval_id:
                metadata["approval_id"] = payload.approval_id
            if payload.artifact_id:
                metadata["artifact_id"] = payload.artifact_id
            return ExecutionStreamEvent(
                session_id=payload.session_id,
                task_id=event.task_id,
                event_id=event.event_id,
                seq=event.task_seq,
                kind=ExecutionEventKind.INPUT_ATTACHED,
                message=payload.preview,
                artifact_id=payload.artifact_id,
                ts=event.ts,
                metadata=metadata,
            )
        if event.type == EventType.ARTIFACT_CREATED:
            payload = event.payload
            session_id = payload.get("session_id")
            if not session_id:
                return None
            return ExecutionStreamEvent(
                session_id=str(session_id),
                task_id=event.task_id,
                event_id=event.event_id,
                seq=event.task_seq,
                kind=ExecutionEventKind.ARTIFACT,
                message=str(payload.get("name", "")),
                artifact_id=str(payload.get("artifact_id", "")),
                ts=event.ts,
                metadata={"source": str(payload.get("source", ""))},
            )
        return None

    async def _task_requires_approval(self, task_id: str) -> bool:
        task = await self._stores.task_store.get_task(task_id)
        return task is not None and task.risk_level.value == "high"

    async def _register_input_approval(
        self,
        *,
        task_id: str,
        prompt: str,
        actor: str,
    ) -> str | None:
        if self._approval_manager is None:
            return None
        approval_id = f"execution-input-{ULID()}"
        timeout_s = float(getattr(self._approval_manager, "_default_timeout_s", 120.0))
        expires_at = datetime.now(UTC) + timedelta(seconds=timeout_s)
        await self._approval_manager.register(
            ApprovalRequest(
                approval_id=approval_id,
                task_id=task_id,
                tool_name="jobrunner.attach_input",
                tool_args_summary=prompt[:120],
                risk_explanation=f"{actor} 请求向长任务补充人工输入",
                policy_label="execution.input",
                side_effect_level=SideEffectLevel.IRREVERSIBLE,
                expires_at=expires_at,
            )
        )
        return approval_id

    async def _ensure_input_approval(
        self,
        required_approval_id: str | None,
        provided_approval_id: str | None,
    ) -> None:
        if required_approval_id is None:
            return
        if provided_approval_id != required_approval_id:
            raise ExecutionInputError(
                "approval is required before attaching input",
                code="INPUT_APPROVAL_REQUIRED",
                approval_id=required_approval_id,
            )
        if self._approval_manager is None:
            raise ExecutionInputError(
                "approval manager unavailable",
                code="INPUT_APPROVAL_REQUIRED",
                approval_id=required_approval_id,
            )
        record = self._approval_manager.get_approval(required_approval_id)
        if record is None or record.status != ApprovalStatus.APPROVED:
            raise ExecutionInputError(
                "approval is required before attaching input",
                code="INPUT_APPROVAL_REQUIRED",
                approval_id=required_approval_id,
            )
        if record.decision == ApprovalDecision.ALLOW_ONCE:
            consumed = self._approval_manager.consume_allow_once(required_approval_id)
            if not consumed:
                raise ExecutionInputError(
                    "approval token has already been consumed",
                    code="INPUT_APPROVAL_REQUIRED",
                    approval_id=required_approval_id,
                )

    async def _latest_artifact_id(
        self,
        task_id: str,
        session_id: str | None = None,
    ) -> str | None:
        if session_id:
            events = await self._stores.event_store.get_events_for_task(task_id)
            for event in reversed(events):
                if event.type != EventType.ARTIFACT_CREATED:
                    continue
                payload = event.payload
                if str(payload.get("session_id", "")) != session_id:
                    continue
                artifact_id = payload.get("artifact_id")
                if artifact_id:
                    return str(artifact_id)

        artifacts = await self._stores.artifact_store.list_artifacts_for_task(task_id)
        if not artifacts:
            return None
        return artifacts[-1].artifact_id

    @staticmethod
    def _map_task_to_execution_state(task_status: TaskStatus) -> ExecutionSessionState:
        if task_status == TaskStatus.WAITING_INPUT:
            return ExecutionSessionState.WAITING_INPUT
        if task_status == TaskStatus.SUCCEEDED:
            return ExecutionSessionState.SUCCEEDED
        if task_status == TaskStatus.FAILED or task_status == TaskStatus.REJECTED:
            return ExecutionSessionState.FAILED
        if task_status == TaskStatus.CANCELLED:
            return ExecutionSessionState.CANCELLED
        return ExecutionSessionState.RUNNING
