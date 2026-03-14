"""TaskService -- 任务创建/取消/查询业务逻辑

实现消息接收后的任务创建流程：
1. 检查 idempotency_key 去重
2. 创建 Task projection
3. 写入 TASK_CREATED + USER_MESSAGE 事件
4. 异步启动后台 LLM 处理
"""

import asyncio
import hashlib
import inspect
import json
import re
from datetime import UTC, datetime
from typing import Any

import aiosqlite
import structlog
from octoagent.core.config import (
    MESSAGE_PREVIEW_LENGTH,
)
from octoagent.core.models import (
    PIPELINE_NODES,
    TERMINAL_STATES,
    ActorType,
    Artifact,
    ArtifactPart,
    CheckpointSnapshot,
    CheckpointStatus,
    Event,
    EventCausality,
    EventType,
    MemoryNamespaceKind,
    PartType,
    RecallPlan,
    RecallPlanMode,
    RequesterInfo,
    RuntimeControlContext,
    Task,
    TaskStatus,
    validate_transition,
)
from octoagent.core.models.message import NormalizedMessage
from octoagent.core.models.payloads import (
    ArtifactCreatedPayload,
    CheckpointSavedPayload,
    ContextCompactionCompletedPayload,
    MemoryRecallCompletedPayload,
    MemoryRecallFailedPayload,
    MemoryRecallScheduledPayload,
    ModelCallCompletedPayload,
    ModelCallFailedPayload,
    ModelCallStartedPayload,
    StateTransitionPayload,
    TaskCreatedPayload,
    UserMessagePayload,
)
from octoagent.core.store import StoreGroup
from octoagent.core.store.transaction import (
    TaskStatusConflictError,
    append_event_and_save_checkpoint,
    append_event_and_update_task,
    append_event_only,
    create_task_with_initial_events,
)
from octoagent.memory import (
    EvidenceRef,
    MemoryMaintenanceCommand,
    MemoryMaintenanceCommandKind,
    MemoryPartition,
    init_memory_db,
)
from ulid import ULID

from .agent_context import (
    AgentContextService,
    build_default_memory_recall_hook_options,
    effective_memory_access_policy,
    memory_recall_max_hits,
    memory_recall_per_scope_limit,
)
from .connection_metadata import (
    input_metadata_from_payload,
    merge_control_metadata,
    normalize_control_metadata,
    normalize_input_metadata,
)
from .context_compaction import CompiledTaskContext, ContextCompactionService
from .execution_context import bind_execution_context
from .runtime_control import runtime_context_from_metadata

log = structlog.get_logger()


class TaskService:
    """任务业务服务"""

    _task_locks: dict[str, asyncio.Lock] = {}
    _task_locks_guard = asyncio.Lock()
    _max_task_seq_retries = 3
    _pipeline_nodes = PIPELINE_NODES

    def __init__(self, store_group: StoreGroup, sse_hub=None) -> None:
        self._stores = store_group
        self._sse_hub = sse_hub
        self._context_compaction = ContextCompactionService(store_group)
        self._agent_context = AgentContextService(store_group)

    async def create_task(self, message: NormalizedMessage) -> tuple[str, bool]:
        """创建任务（消息接收入口）

        Args:
            message: 标准化消息

        Returns:
            (task_id, created) -- created=True 表示新创建，False 表示 idempotency 命中
        """
        # 检查幂等键
        existing_task_id = await self._stores.event_store.check_idempotency_key(
            message.idempotency_key
        )
        if existing_task_id:
            return existing_task_id, False

        # 生成 ID
        now = datetime.now(UTC)
        task_id = str(ULID())
        trace_id = f"trace-{task_id}"
        scope_id = message.scope_id or f"chat:{message.channel}:{message.thread_id}"

        # 先构建 Task 与初始事件，后续单事务提交（避免 task 与 events 分离落盘）
        task = Task(
            task_id=task_id,
            created_at=now,
            updated_at=now,
            status=TaskStatus.CREATED,
            title=message.text[:100],  # 标题截断到 100 字符
            thread_id=message.thread_id,
            scope_id=scope_id,
            requester=RequesterInfo(
                channel=message.channel,
                sender_id=message.sender_id,
            ),
        )

        # TASK_CREATED 事件
        event_1_id = str(ULID())
        event_1 = Event(
            event_id=event_1_id,
            task_id=task_id,
            task_seq=1,
            ts=now,
            type=EventType.TASK_CREATED,
            actor=ActorType.SYSTEM,
            payload=TaskCreatedPayload(
                title=task.title,
                thread_id=task.thread_id,
                scope_id=scope_id,
                channel=message.channel,
                sender_id=message.sender_id,
                risk_level=task.risk_level.value,
            ).model_dump(),
            trace_id=trace_id,
            causality=EventCausality(idempotency_key=message.idempotency_key),
        )

        # USER_MESSAGE 事件
        text_preview = message.text[:MESSAGE_PREVIEW_LENGTH]
        input_metadata = normalize_input_metadata(message.metadata)
        control_metadata = normalize_control_metadata(message.control_metadata)
        event_2_id = str(ULID())
        event_2 = Event(
            event_id=event_2_id,
            task_id=task_id,
            task_seq=2,
            ts=now,
            type=EventType.USER_MESSAGE,
            actor=ActorType.USER,
            payload=UserMessagePayload(
                text_preview=text_preview,
                text_length=len(message.text),
                text=message.text,
                attachment_count=len(message.attachments),
                metadata=input_metadata,
                control_metadata=control_metadata,
            ).model_dump(),
            trace_id=trace_id,
        )

        # 单事务写入 task + 两条初始事件
        try:
            await create_task_with_initial_events(
                self._stores.conn,
                self._stores.task_store,
                self._stores.event_store,
                task,
                [event_1, event_2],
            )
        except aiosqlite.IntegrityError as e:
            if self._is_idempotency_conflict(e):
                # 并发重复请求：回查幂等键并返回已存在 task_id，避免 500
                existing_task_id = await self._stores.event_store.check_idempotency_key(
                    message.idempotency_key
                )
                if existing_task_id:
                    return existing_task_id, False
            raise

        # 广播 TASK_CREATED 事件
        if self._sse_hub:
            await self._sse_hub.broadcast(task_id, event_1)

        # 广播 USER_MESSAGE 事件
        if self._sse_hub:
            await self._sse_hub.broadcast(task_id, event_2)

        return task_id, True

    async def append_user_message(
        self,
        task_id: str,
        text: str,
        *,
        sender_id: str = "owner",
        attachment_count: int = 0,
        metadata: dict[str, str] | None = None,
        control_metadata: dict[str, Any] | None = None,
    ) -> Event:
        """向已有任务追加 USER_MESSAGE 事件。"""
        task = await self._stores.task_store.get_task(task_id)
        if task is None:
            raise ValueError(f"Task not found: {task_id}")

        trace_id = f"trace-{task_id}"
        text_preview = text[:MESSAGE_PREVIEW_LENGTH]
        input_metadata = normalize_input_metadata(metadata)
        trusted_control = normalize_control_metadata(control_metadata)
        event = await self._append_event_only_with_retry(
            task_id=task_id,
            event_builder=lambda seq: Event(
                event_id=str(ULID()),
                task_id=task_id,
                task_seq=seq,
                ts=datetime.now(UTC),
                type=EventType.USER_MESSAGE,
                actor=ActorType.USER,
                payload=UserMessagePayload(
                    text_preview=text_preview,
                    text_length=len(text),
                    text=text,
                    attachment_count=attachment_count,
                    metadata=input_metadata,
                    control_metadata=trusted_control,
                ).model_dump(),
                trace_id=trace_id,
                causality=EventCausality(idempotency_key=f"chat-{task_id}-{ULID()}"),
            ),
        )
        if self._sse_hub:
            await self._sse_hub.broadcast(task_id, event)
        return event

    async def append_structured_event(
        self,
        *,
        task_id: str,
        event_type: EventType,
        actor: ActorType,
        payload: dict[str, Any],
        trace_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> Event:
        """追加任意结构化事件并广播。"""
        event = await self._append_event_only_with_retry(
            task_id=task_id,
            event_builder=lambda seq: Event(
                event_id=str(ULID()),
                task_id=task_id,
                task_seq=seq,
                ts=datetime.now(UTC),
                type=event_type,
                actor=actor,
                payload=payload,
                trace_id=trace_id or f"trace-{task_id}",
                causality=EventCausality(idempotency_key=idempotency_key),
            ),
        )
        if self._sse_hub:
            await self._sse_hub.broadcast(task_id, event)
        return event

    async def ensure_task_running(
        self,
        task_id: str,
        *,
        trace_id: str | None = None,
    ) -> None:
        """确保任务进入 RUNNING，供 orchestrator 级长链路复用。"""
        await self._prepare_task_for_processing(
            task_id,
            trace_id or f"trace-{task_id}",
        )

    async def create_text_artifact(
        self,
        *,
        task_id: str,
        name: str,
        description: str,
        content: str,
        artifact_id: str | None = None,
        allow_existing: bool = False,
        trace_id: str | None = None,
        emit_event: bool = True,
        session_id: str | None = None,
        source: str = "",
    ) -> Artifact:
        """创建文本 Artifact 并写入 ARTIFACT_CREATED 事件。"""
        if artifact_id and allow_existing:
            existing = await self._stores.artifact_store.get_artifact(artifact_id)
            if existing is not None:
                return existing

        artifact = Artifact(
            artifact_id=artifact_id or str(ULID()),
            task_id=task_id,
            ts=datetime.now(UTC),
            name=name,
            description=description,
            parts=[ArtifactPart(type=PartType.TEXT, content=content)],
        )
        try:
            await self._stores.artifact_store.put_artifact(artifact, content.encode("utf-8"))
            await self._stores.conn.commit()
        except aiosqlite.IntegrityError:
            if artifact_id and allow_existing:
                existing = await self._stores.artifact_store.get_artifact(artifact_id)
                if existing is not None:
                    return existing
            raise
        if emit_event:
            await self._write_artifact_created(
                task_id=task_id,
                trace_id=trace_id or f"trace-{task_id}",
                artifact_id=artifact.artifact_id,
                artifact=artifact,
                session_id=session_id,
                source=source,
            )
        return artifact

    async def record_auxiliary_model_call(
        self,
        *,
        task_id: str,
        trace_id: str,
        llm_service,
        prompt_or_messages: str | list[dict[str, str]],
        request_summary: str,
        model_alias: str | None,
        dispatch_metadata: dict[str, Any] | None = None,
        worker_capability: str | None = None,
        tool_profile: str | None = None,
        request_artifact_name: str,
        request_artifact_description: str,
        request_artifact_source: str,
        response_artifact_name: str,
        response_artifact_description: str,
        response_artifact_source: str,
    ):
        prompt_snapshot = self._render_prompt_snapshot(prompt_or_messages)
        request_artifact = await self.create_text_artifact(
            task_id=task_id,
            name=request_artifact_name,
            description=request_artifact_description,
            content=prompt_snapshot,
            trace_id=trace_id,
            source=request_artifact_source,
        )
        effective_alias = model_alias or "main"
        model_call_idempotency_key = (
            f"{task_id}:aux_llm_call:{request_artifact_name}:{effective_alias}:{ULID()}"
        )
        started_event = await self._append_event_only_with_retry(
            task_id=task_id,
            event_builder=lambda seq: Event(
                event_id=str(ULID()),
                task_id=task_id,
                task_seq=seq,
                ts=datetime.now(UTC),
                type=EventType.MODEL_CALL_STARTED,
                actor=ActorType.SYSTEM,
                payload=ModelCallStartedPayload(
                    model_alias=effective_alias,
                    request_summary=request_summary,
                    artifact_ref=request_artifact.artifact_id,
                ).model_dump(),
                trace_id=trace_id,
                causality=EventCausality(idempotency_key=model_call_idempotency_key),
            ),
        )
        if self._sse_hub:
            await self._sse_hub.broadcast(task_id, started_event)

        llm_result = await self._call_llm_service(
            llm_service=llm_service,
            prompt_or_messages=prompt_or_messages,
            model_alias=effective_alias,
            task_id=task_id,
            trace_id=trace_id,
            dispatch_metadata=dispatch_metadata or {},
            worker_capability=worker_capability,
            tool_profile=tool_profile,
        )
        response_artifact = await self.create_text_artifact(
            task_id=task_id,
            name=response_artifact_name,
            description=response_artifact_description,
            content=llm_result.content,
            trace_id=trace_id,
            emit_event=False,
            source=response_artifact_source,
        )
        await self._write_model_call_completed(
            task_id,
            trace_id,
            llm_result,
            response_artifact.artifact_id,
        )
        await self._write_artifact_created(
            task_id=task_id,
            trace_id=trace_id,
            artifact_id=response_artifact.artifact_id,
            artifact=response_artifact,
            source=response_artifact_source,
        )
        return llm_result, request_artifact, response_artifact

    # 响应摘要截断阈值（对齐 FR-002-CL-4，沿用 M0 8KB 阈值）
    RESPONSE_SUMMARY_MAX_BYTES = 8192

    async def process_task_with_llm(
        self,
        task_id: str,
        user_text: str,
        llm_service,
        model_alias: str | None = None,
        resume_from_node: str | None = None,
        resume_state_snapshot: dict[str, Any] | None = None,
        execution_context=None,
        dispatch_metadata: dict[str, Any] | None = None,
        worker_capability: str | None = None,
        tool_profile: str | None = None,
        runtime_context: RuntimeControlContext | None = None,
    ) -> None:
        """异步后台处理：LLM 调用 + 事件写入 + Artifact 存储 + Checkpoint

        流程：
        1. STATE_TRANSITION: CREATED -> RUNNING
        2. MODEL_CALL_STARTED 事件
        3. LLM 调用（通过 LLMService -> FallbackManager）
        4. MODEL_CALL_COMPLETED 事件 + Artifact 写入
        5. ARTIFACT_CREATED 事件
        6. STATE_TRANSITION: RUNNING -> SUCCEEDED

        Feature 002 变更:
        - LLM 调用返回 ModelCallResult（替代 LLMResponse）
        - MODEL_CALL_COMPLETED payload 填充 cost/provider/is_fallback 新字段
        - 响应超过 8KB 截断为 response_summary + Artifact 引用
        """
        trace_id = f"trace-{task_id}"
        effective_alias = model_alias or "main"
        llm_call_idempotency_key = self._derive_llm_call_idempotency_key(
            task_id=task_id,
            model_alias=effective_alias,
            resume_from_node=resume_from_node,
            resume_state_snapshot=resume_state_snapshot,
        )
        artifact_id = (
            str(resume_state_snapshot.get("artifact_id"))
            if resume_state_snapshot and resume_state_snapshot.get("artifact_id")
            else None
        )
        request_artifact_id = (
            str(resume_state_snapshot.get("request_artifact_id"))
            if resume_state_snapshot and resume_state_snapshot.get("request_artifact_id")
            else ""
        )
        delayed_recall_request_artifact_id = (
            str(resume_state_snapshot.get("delayed_recall_request_artifact_id"))
            if resume_state_snapshot
            and resume_state_snapshot.get("delayed_recall_request_artifact_id")
            else ""
        )
        model_call_started_idempotency_key = f"{llm_call_idempotency_key}:model_call_started"
        compaction_idempotency_key = f"{llm_call_idempotency_key}:context_compaction"
        compiled_context: CompiledTaskContext | None = None
        try:
            # 1. STATE_TRANSITION: CREATED -> RUNNING（恢复路径按 checkpoint 起点跳过）
            if self._should_execute_node("state_running", resume_from_node):
                if resume_from_node is None:
                    # 常规路径保留“续对话可重复执行”语义
                    await self._prepare_task_for_processing(task_id, trace_id)
                else:
                    try:
                        await self._write_state_transition(
                            task_id, TaskStatus.CREATED, TaskStatus.RUNNING, trace_id
                        )
                    except TaskStatusConflictError:
                        # 恢复路径下任务大概率已是 RUNNING，允许跳过冲突
                        current = await self.get_task(task_id)
                        if current is None or current.status != TaskStatus.RUNNING:
                            raise

                await self._write_checkpoint(
                    task_id=task_id,
                    node_id="state_running",
                    trace_id=trace_id,
                    state_snapshot={
                        "next_node": "model_call_started",
                        "model_alias": effective_alias,
                        "llm_call_idempotency_key": llm_call_idempotency_key,
                    },
                )

            compiled_context = await self._build_task_context(
                task_id=task_id,
                fallback_user_text=user_text,
                llm_service=llm_service,
                trace_id=trace_id,
                model_alias=effective_alias,
                dispatch_metadata=dispatch_metadata or {},
                worker_capability=worker_capability,
                tool_profile=tool_profile,
                runtime_context=runtime_context,
            )
            llm_dispatch_metadata = self._build_llm_dispatch_metadata(
                dispatch_metadata=dispatch_metadata or {},
                compiled_context=compiled_context,
                runtime_context=runtime_context,
            )
            request_summary = compiled_context.request_summary
            if self._should_execute_node("model_call_started", resume_from_node):
                request_artifact_id = await self._store_request_snapshot_artifact(
                    task_id=task_id,
                    compiled=compiled_context,
                    llm_call_idempotency_key=llm_call_idempotency_key,
                    trace_id=trace_id,
                    session_id=(
                        execution_context.session_id if execution_context is not None else None
                    ),
                )
                if compiled_context.compacted:
                    await self._record_context_compaction_once(
                        task_id=task_id,
                        trace_id=trace_id,
                        compiled=compiled_context,
                        llm_call_idempotency_key=llm_call_idempotency_key,
                        compaction_idempotency_key=compaction_idempotency_key,
                        request_artifact_id=request_artifact_id,
                        session_id=(
                            execution_context.session_id if execution_context is not None else None
                        ),
                        worker_capability=worker_capability,
                    )
                delayed_recall_request_artifact_id = await self._record_delayed_recall_once(
                    task_id=task_id,
                    trace_id=trace_id,
                    context_frame_id=compiled_context.context_frame_id,
                    llm_call_idempotency_key=llm_call_idempotency_key,
                    request_artifact_id=request_artifact_id,
                    session_id=(
                        execution_context.session_id if execution_context is not None else None
                    ),
                )
                started_event = await self._append_event_only_with_retry(
                    task_id=task_id,
                    event_builder=lambda seq: Event(
                        event_id=str(ULID()),
                        task_id=task_id,
                        task_seq=seq,
                        ts=datetime.now(UTC),
                        type=EventType.MODEL_CALL_STARTED,
                        actor=ActorType.SYSTEM,
                        payload=ModelCallStartedPayload(
                            model_alias=effective_alias,
                            request_summary=request_summary,
                            artifact_ref=request_artifact_id or None,
                        ).model_dump(),
                        trace_id=trace_id,
                        causality=EventCausality(
                            idempotency_key=model_call_started_idempotency_key
                        ),
                    ),
                )
                if self._sse_hub:
                    await self._sse_hub.broadcast(task_id, started_event)

                await self._write_checkpoint(
                    task_id=task_id,
                    node_id="model_call_started",
                    trace_id=trace_id,
                    state_snapshot={
                        "next_node": "response_persisted",
                        "request_summary": request_summary,
                        "model_alias": effective_alias,
                        "llm_call_idempotency_key": llm_call_idempotency_key,
                        "request_artifact_id": request_artifact_id,
                        "delayed_recall_request_artifact_id": (delayed_recall_request_artifact_id),
                    },
                )

            # 3. LLM 调用（返回 ModelCallResult）
            if self._should_execute_node("response_persisted", resume_from_node):
                first_record = await self._stores.side_effect_ledger_store.try_record(
                    task_id=task_id,
                    step_key=f"llm_call:{llm_call_idempotency_key}",
                    idempotency_key=llm_call_idempotency_key,
                    effect_type="tool_call",
                )
                if first_record:
                    with bind_execution_context(execution_context):
                        llm_result = await self._call_llm_service(
                            llm_service=llm_service,
                            prompt_or_messages=(
                                compiled_context.messages
                                if compiled_context is not None
                                else user_text
                            ),
                            model_alias=effective_alias,
                            task_id=task_id,
                            trace_id=trace_id,
                            dispatch_metadata=llm_dispatch_metadata,
                            worker_capability=worker_capability,
                            tool_profile=tool_profile,
                        )

                    # 4. 存储 Artifact + 写入完成事件
                    artifact_id, artifact = await self._store_llm_artifact(
                        task_id,
                        llm_result,
                        session_id=(
                            execution_context.session_id if execution_context is not None else None
                        ),
                    )
                    await self._stores.side_effect_ledger_store.set_result_ref(
                        llm_call_idempotency_key, artifact_id
                    )
                    await self._write_model_call_completed(
                        task_id, trace_id, llm_result, artifact_id
                    )
                    await self._write_artifact_created(
                        task_id,
                        trace_id,
                        artifact_id,
                        artifact,
                        session_id=(
                            execution_context.session_id if execution_context is not None else None
                        ),
                        source="llm-response",
                    )
                    if compiled_context is not None and compiled_context.context_frame_id:
                        try:
                            await self._agent_context.record_response_context(
                                task_id=task_id,
                                context_frame_id=compiled_context.context_frame_id,
                                request_artifact_id=request_artifact_id,
                                response_artifact_id=artifact_id,
                                latest_user_text=compiled_context.latest_user_text,
                                model_response=llm_result.content,
                                recent_summary=compiled_context.recent_summary,
                            )
                        except Exception as exc:
                            log.warning(
                                "agent_context_response_update_degraded",
                                task_id=task_id,
                                error_type=type(exc).__name__,
                                error=str(exc),
                            )
                    if delayed_recall_request_artifact_id:
                        await self._materialize_delayed_recall_once(
                            task_id=task_id,
                            trace_id=trace_id,
                            context_frame_id=compiled_context.context_frame_id,
                            llm_call_idempotency_key=llm_call_idempotency_key,
                            request_artifact_id=delayed_recall_request_artifact_id,
                            session_id=(
                                execution_context.session_id
                                if execution_context is not None
                                else None
                            ),
                        )
                else:
                    reused_artifact_id = await self._resolve_reused_artifact_id(
                        llm_call_idempotency_key
                    )
                    if reused_artifact_id is None:
                        raise RuntimeError("检测到重复副作用幂等键，但缺少可复用结果引用")
                    artifact_id = reused_artifact_id
                    await self._write_model_call_reused_events(
                        task_id=task_id,
                        trace_id=trace_id,
                        model_alias=effective_alias,
                        artifact_id=artifact_id,
                    )
                    log.info(
                        "resume_reused_llm_result",
                        task_id=task_id,
                        artifact_id=artifact_id,
                    )

                await self._write_checkpoint(
                    task_id=task_id,
                    node_id="response_persisted",
                    trace_id=trace_id,
                    state_snapshot={
                        "next_node": "task_succeeded",
                        "artifact_id": artifact_id,
                        "llm_call_idempotency_key": llm_call_idempotency_key,
                    },
                )

            # 5. STATE_TRANSITION: RUNNING -> SUCCEEDED
            if self._should_execute_node("task_succeeded", resume_from_node):
                await self._write_state_transition(
                    task_id, TaskStatus.RUNNING, TaskStatus.SUCCEEDED, trace_id
                )
                await self._write_checkpoint(
                    task_id=task_id,
                    node_id="task_succeeded",
                    trace_id=trace_id,
                    state_snapshot={
                        "next_node": None,
                        "artifact_id": artifact_id,
                    },
                )

        except TaskStatusConflictError:
            log.info(
                "task_state_conflict_skip_processing",
                task_id=task_id,
            )
            return
        except Exception as e:
            await self._handle_llm_failure(task_id, trace_id, effective_alias, e)

    async def _call_llm_service(
        self,
        *,
        llm_service,
        prompt_or_messages: str | list[dict[str, str]],
        model_alias: str | None,
        task_id: str,
        trace_id: str,
        dispatch_metadata: dict[str, Any],
        worker_capability: str | None,
        tool_profile: str | None,
    ):
        call_fn = llm_service.call
        kwargs: dict[str, Any] = {"model_alias": model_alias}
        extra_kwargs = {
            "task_id": task_id,
            "trace_id": trace_id,
            "metadata": dispatch_metadata,
            "worker_capability": worker_capability,
            "tool_profile": tool_profile,
        }

        try:
            signature = inspect.signature(call_fn)
        except (TypeError, ValueError):
            signature = None

        accepts_var_kwargs = False
        accepted_names: set[str] = set()
        if signature is not None:
            accepted_names = set(signature.parameters.keys())
            accepts_var_kwargs = any(
                parameter.kind == inspect.Parameter.VAR_KEYWORD
                for parameter in signature.parameters.values()
            )

        for key, value in extra_kwargs.items():
            if value is None:
                continue
            if accepts_var_kwargs or key in accepted_names:
                kwargs[key] = value

        return await call_fn(prompt_or_messages, **kwargs)

    @staticmethod
    def _build_llm_dispatch_metadata(
        *,
        dispatch_metadata: dict[str, Any],
        compiled_context: CompiledTaskContext | None,
        runtime_context: RuntimeControlContext | None,
    ) -> dict[str, Any]:
        merged = dict(dispatch_metadata)
        if compiled_context is not None:
            if compiled_context.effective_agent_runtime_id and not str(
                merged.get("agent_runtime_id", "")
            ).strip():
                merged["agent_runtime_id"] = compiled_context.effective_agent_runtime_id
            if compiled_context.effective_agent_session_id and not str(
                merged.get("agent_session_id", "")
            ).strip():
                merged["agent_session_id"] = compiled_context.effective_agent_session_id
            if compiled_context.context_frame_id and not str(
                merged.get("context_frame_id", "")
            ).strip():
                merged["context_frame_id"] = compiled_context.context_frame_id
            if compiled_context.recall_frame_id and not str(
                merged.get("recall_frame_id", "")
            ).strip():
                merged["recall_frame_id"] = compiled_context.recall_frame_id
            if compiled_context.memory_namespace_ids and "memory_namespace_ids" not in merged:
                merged["memory_namespace_ids"] = list(compiled_context.memory_namespace_ids)
        if runtime_context is not None and runtime_context.work_id and not str(
            merged.get("work_id", "")
        ).strip():
            merged["work_id"] = runtime_context.work_id
        return merged

    @staticmethod
    def _render_prompt_snapshot(prompt_or_messages: str | list[dict[str, str]]) -> str:
        if isinstance(prompt_or_messages, str):
            return prompt_or_messages
        return json.dumps(prompt_or_messages, ensure_ascii=False, indent=2)

    @staticmethod
    def _supports_recall_planning(llm_service) -> bool:
        return bool(getattr(llm_service, "supports_recall_planning_phase", False))

    @staticmethod
    def _build_memory_recall_planner_messages(
        *,
        planning_context,
    ) -> list[dict[str, str]]:
        transcript_lines = [
            f"{str(item.get('role', '')).strip()}: {str(item.get('content', '')).strip()}"
            for item in planning_context.transcript_entries[-6:]
            if str(item.get("content", "")).strip()
        ]
        transcript_block = "\n".join(transcript_lines) or "N/A"
        summary = planning_context.recent_summary.strip() or "N/A"
        project_name = planning_context.project.name if planning_context.project is not None else "N/A"
        workspace_name = (
            planning_context.workspace.name if planning_context.workspace is not None else "N/A"
        )
        return [
            {
                "role": "system",
                "content": (
                    "你是 OctoAgent 的 Memory Recall Planner。"
                    "你的唯一任务是判断：在正式回答前，是否值得先做一次 memory recall。"
                    "只输出 JSON，不要输出解释性正文。\n"
                    "JSON schema: "
                    "{\"mode\":\"skip|recall\",\"query\":\"...\",\"rationale\":\"...\","
                    "\"subject_hint\":\"...\",\"focus_terms\":[\"...\"],"
                    "\"allow_vault\":false,\"limit\":4}\n"
                    "规则：\n"
                    "- 如果当前问题明显依赖长期事实、约束、历史承诺、用户偏好、project continuity 或多轮上下文，再用 mode=recall。\n"
                    "- 如果 recent summary / recent transcript 已足够，或当前问题与长期记忆无关，用 mode=skip。\n"
                    "- query 要比原始用户问题更适合检索；focus_terms 只保留最关键的 1-5 个词。\n"
                    "- allow_vault 默认 false；只有在确实需要敏感长期事实时才设 true。"
                ),
            },
            {
                "role": "user",
                "content": (
                    "RecallPlanningContext:\n"
                    f"request_kind: {planning_context.request.request_kind.value}\n"
                    f"project: {project_name}\n"
                    f"workspace: {workspace_name}\n"
                    f"agent_profile_id: {planning_context.agent_profile.profile_id}\n"
                    f"agent_runtime_role: {planning_context.agent_runtime.role.value}\n"
                    f"query: {planning_context.query}\n"
                    f"prefetch_mode: {planning_context.prefetch_mode}\n"
                    f"memory_scope_ids: {', '.join(planning_context.memory_scope_ids) or 'N/A'}\n"
                    f"recent_summary: {summary}\n"
                    f"recent_transcript:\n{transcript_block}"
                ),
            },
        ]

    @staticmethod
    def _parse_memory_recall_plan_response(content: str) -> RecallPlan | None:
        candidates = [content.strip()]
        fenced = re.findall(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", content, flags=re.IGNORECASE)
        candidates.extend(item.strip() for item in fenced if item.strip())
        for candidate in candidates:
            if not candidate:
                continue
            try:
                payload = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            try:
                plan = RecallPlan.model_validate(payload)
            except Exception:
                continue
            if plan.mode is RecallPlanMode.RECALL and not plan.query.strip():
                return plan.model_copy(update={"mode": RecallPlanMode.SKIP})
            return plan
        return None

    @staticmethod
    def _parse_precomputed_recall_plan(
        dispatch_metadata: dict[str, Any],
    ) -> RecallPlan | None:
        raw = dispatch_metadata.get("precomputed_recall_plan")
        if isinstance(raw, RecallPlan):
            plan = raw
        elif isinstance(raw, dict):
            try:
                plan = RecallPlan.model_validate(raw)
            except Exception:
                return None
        else:
            return None
        if plan.mode is RecallPlanMode.RECALL and not plan.query.strip():
            return plan.model_copy(update={"mode": RecallPlanMode.SKIP})
        metadata = dict(plan.metadata)
        request_ref = str(dispatch_metadata.get("precomputed_recall_plan_request_artifact_ref", "")).strip()
        response_ref = str(
            dispatch_metadata.get("precomputed_recall_plan_response_artifact_ref", "")
        ).strip()
        plan_source = str(dispatch_metadata.get("precomputed_recall_plan_source", "")).strip()
        return plan.model_copy(
            update={
                "metadata": {
                    **metadata,
                    "plan_source": plan_source or str(metadata.get("plan_source", "")).strip(),
                    "request_artifact_ref": request_ref or str(
                        metadata.get("request_artifact_ref", "")
                    ).strip(),
                    "response_artifact_ref": response_ref or str(
                        metadata.get("response_artifact_ref", "")
                    ).strip(),
                }
            }
        )

    @staticmethod
    def _metadata_flag(metadata: dict[str, Any], key: str) -> bool:
        value = metadata.get(key)
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

    async def _build_memory_recall_plan(
        self,
        *,
        task_id: str,
        trace_id: str,
        model_alias: str | None,
        llm_service,
        compiled: CompiledTaskContext,
        dispatch_metadata: dict[str, Any],
        worker_capability: str | None,
        tool_profile: str | None,
        runtime_context: RuntimeControlContext | None,
    ) -> RecallPlan | None:
        precomputed_plan = self._parse_precomputed_recall_plan(dispatch_metadata)
        if precomputed_plan is not None:
            return precomputed_plan
        if self._metadata_flag(dispatch_metadata, "single_loop_executor"):
            return None
        if not self._supports_recall_planning(llm_service):
            return None
        task = await self._stores.task_store.get_task(task_id)
        if task is None:
            return None
        planning_context = await self._agent_context.build_recall_planning_context(
            task=task,
            compiled=compiled,
            dispatch_metadata=dispatch_metadata,
            worker_capability=worker_capability,
            runtime_context=runtime_context,
        )
        if (
            planning_context.prefetch_mode
            not in {"agent_led_hint_first", "hint_first"}
            or not planning_context.planner_enabled
            or not planning_context.memory_scope_ids
            or not planning_context.query.strip()
        ):
            return None
        llm_result, request_artifact, response_artifact = await self.record_auxiliary_model_call(
            task_id=task_id,
            trace_id=trace_id,
            llm_service=llm_service,
            prompt_or_messages=self._build_memory_recall_planner_messages(
                planning_context=planning_context
            ),
            request_summary=f"Memory recall plan: {planning_context.query[:80]}",
            model_alias=model_alias or "main",
            dispatch_metadata={
                **dispatch_metadata,
                "decision_phase": "memory_recall_planning",
                "decision_task_id": task_id,
            },
            worker_capability=worker_capability,
            tool_profile=tool_profile,
            request_artifact_name="memory-recall-plan-request",
            request_artifact_description="Memory recall planner 请求",
            request_artifact_source="memory-recall-plan-request",
            response_artifact_name="memory-recall-plan-response",
            response_artifact_description="Memory recall planner 响应",
            response_artifact_source="memory-recall-plan-response",
        )
        parsed = self._parse_memory_recall_plan_response(llm_result.content)
        if parsed is None:
            return RecallPlan(
                mode=RecallPlanMode.SKIP,
                rationale="memory_recall_plan_parse_failed",
                metadata={
                    "plan_source": "parse_failed",
                    "request_artifact_ref": request_artifact.artifact_id,
                    "response_artifact_ref": response_artifact.artifact_id,
                },
            )
        return parsed.model_copy(
            update={
                "metadata": {
                    **dict(parsed.metadata),
                    "plan_source": "model",
                    "request_artifact_ref": request_artifact.artifact_id,
                    "response_artifact_ref": response_artifact.artifact_id,
                }
            }
        )

    async def _build_task_context(
        self,
        *,
        task_id: str,
        fallback_user_text: str,
        llm_service,
        trace_id: str,
        model_alias: str | None,
        dispatch_metadata: dict[str, Any],
        worker_capability: str | None,
        tool_profile: str | None,
        runtime_context: RuntimeControlContext | None = None,
    ) -> CompiledTaskContext:
        resolved_runtime_context = runtime_context or runtime_context_from_metadata(
            dispatch_metadata
        )
        compiled = await self._context_compaction.build_context(
            task_id=task_id,
            fallback_user_text=fallback_user_text,
            llm_service=llm_service,
            dispatch_metadata=dispatch_metadata,
            worker_capability=worker_capability,
            tool_profile=tool_profile,
        )
        task = await self._stores.task_store.get_task(task_id)
        if task is None:
            return compiled
        recall_plan = await self._build_memory_recall_plan(
            task_id=task_id,
            trace_id=trace_id,
            model_alias=model_alias,
            llm_service=llm_service,
            compiled=compiled,
            dispatch_metadata=dispatch_metadata,
            worker_capability=worker_capability,
            tool_profile=tool_profile,
            runtime_context=resolved_runtime_context,
        )
        try:
            return await self._agent_context.build_task_context(
                task=task,
                compiled=compiled,
                dispatch_metadata=dispatch_metadata,
                worker_capability=worker_capability,
                runtime_context=resolved_runtime_context,
                recall_plan=recall_plan,
            )
        except Exception as exc:
            log.warning(
                "agent_context_resolve_degraded",
                task_id=task_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return compiled

    async def _store_request_snapshot_artifact(
        self,
        *,
        task_id: str,
        compiled: CompiledTaskContext,
        llm_call_idempotency_key: str,
        trace_id: str,
        session_id: str | None,
    ) -> str:
        artifact = await self.create_text_artifact(
            task_id=task_id,
            name="llm-request-context",
            description="主模型请求上下文快照",
            content=compiled.snapshot_text,
            artifact_id=self._derive_artifact_id(
                "ctxreq",
                task_id,
                llm_call_idempotency_key,
            ),
            allow_existing=True,
            trace_id=trace_id,
            emit_event=False,
            session_id=session_id,
            source="llm-request-context",
        )
        return artifact.artifact_id

    async def _record_context_compaction_once(
        self,
        *,
        task_id: str,
        trace_id: str,
        compiled: CompiledTaskContext,
        llm_call_idempotency_key: str,
        compaction_idempotency_key: str,
        request_artifact_id: str,
        session_id: str | None,
        worker_capability: str | None,
    ) -> None:
        step_key = f"context_compaction:{llm_call_idempotency_key}"
        first_record = await self._stores.side_effect_ledger_store.try_record(
            task_id=task_id,
            step_key=step_key,
            idempotency_key=compaction_idempotency_key,
            effect_type="context_compaction",
        )
        if first_record:
            event = await self._record_context_compaction(
                task_id=task_id,
                trace_id=trace_id,
                compiled=compiled,
                compaction_idempotency_key=compaction_idempotency_key,
                request_artifact_id=request_artifact_id,
                session_id=session_id,
                worker_capability=worker_capability,
            )
            await self._stores.side_effect_ledger_store.set_result_ref(
                compaction_idempotency_key,
                event.event_id,
            )
            return

        entry = await self._stores.side_effect_ledger_store.get_entry(compaction_idempotency_key)
        if entry is not None and entry.result_ref:
            return

        existing_event = await self._find_event_by_idempotency_key(
            task_id,
            f"{compaction_idempotency_key}:event",
        )
        if existing_event is not None:
            await self._stores.side_effect_ledger_store.set_result_ref(
                compaction_idempotency_key,
                existing_event.event_id,
            )
            return

        event = await self._record_context_compaction(
            task_id=task_id,
            trace_id=trace_id,
            compiled=compiled,
            compaction_idempotency_key=compaction_idempotency_key,
            request_artifact_id=request_artifact_id,
            session_id=session_id,
            worker_capability=worker_capability,
        )
        await self._stores.side_effect_ledger_store.set_result_ref(
            compaction_idempotency_key,
            event.event_id,
        )

    async def _record_context_compaction(
        self,
        *,
        task_id: str,
        trace_id: str,
        compiled: CompiledTaskContext,
        compaction_idempotency_key: str,
        request_artifact_id: str,
        session_id: str | None,
        worker_capability: str | None,
    ) -> Event:
        if not compiled.compacted or not compiled.summary_text:
            raise ValueError("仅在 compaction 成功时记录上下文压缩事件")

        summary_artifact = await self.create_text_artifact(
            task_id=task_id,
            name="context-compaction-summary",
            description="小模型生成的历史压缩摘要",
            content=compiled.summary_text,
            artifact_id=self._derive_artifact_id(
                "ctxsum",
                task_id,
                compaction_idempotency_key,
            ),
            allow_existing=True,
            trace_id=trace_id,
            emit_event=False,
            session_id=session_id,
            source="context-compaction-summary",
        )

        memory_flush_run_id = await self._persist_compaction_flush(
            task_id=task_id,
            context_frame_id=compiled.context_frame_id,
            summary_text=compiled.summary_text,
            summary_artifact_id=summary_artifact.artifact_id,
            request_artifact_id=request_artifact_id,
            flush_idempotency_key=f"{compaction_idempotency_key}:flush",
            worker_capability=worker_capability,
            compressed_turn_count=compiled.compressed_turn_count,
        )
        if compiled.context_frame_id:
            try:
                await self._agent_context.record_compaction_context(
                    task_id=task_id,
                    context_frame_id=compiled.context_frame_id,
                    summary_text=compiled.summary_text,
                    summary_artifact_id=summary_artifact.artifact_id,
                    compacted_messages=compiled.messages,
                )
            except Exception as exc:
                log.warning(
                    "agent_context_compaction_update_degraded",
                    task_id=task_id,
                    context_frame_id=compiled.context_frame_id,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )

        event = await self._append_event_only_with_retry(
            task_id=task_id,
            event_builder=lambda seq: Event(
                event_id=str(ULID()),
                task_id=task_id,
                task_seq=seq,
                ts=datetime.now(UTC),
                type=EventType.CONTEXT_COMPACTION_COMPLETED,
                actor=ActorType.SYSTEM,
                payload=ContextCompactionCompletedPayload(
                    model_alias=compiled.summary_model_alias or "summarizer",
                    input_tokens_before=compiled.raw_tokens,
                    input_tokens_after=compiled.final_tokens,
                    compressed_turn_count=compiled.compressed_turn_count,
                    kept_turn_count=compiled.kept_turn_count,
                    summary_artifact_ref=summary_artifact.artifact_id,
                    request_artifact_ref=request_artifact_id or None,
                    memory_flush_run_id=memory_flush_run_id or None,
                    reason=compiled.compaction_reason,
                ).model_dump(),
                trace_id=trace_id,
                causality=EventCausality(idempotency_key=f"{compaction_idempotency_key}:event"),
            ),
        )
        if self._sse_hub:
            await self._sse_hub.broadcast(task_id, event)
        return event

    async def _record_delayed_recall_once(
        self,
        *,
        task_id: str,
        trace_id: str,
        context_frame_id: str,
        llm_call_idempotency_key: str,
        request_artifact_id: str,
        session_id: str | None,
    ) -> str:
        if not context_frame_id:
            return ""

        frame = await self._stores.agent_context_store.get_context_frame(context_frame_id)
        if frame is None:
            return ""
        plan = self._build_delayed_recall_plan(frame)
        if not plan["enabled"]:
            return ""

        delayed_recall_idempotency_key = f"{llm_call_idempotency_key}:delayed_recall_schedule"
        first_record = await self._stores.side_effect_ledger_store.try_record(
            task_id=task_id,
            step_key=f"delayed_recall_schedule:{llm_call_idempotency_key}",
            idempotency_key=delayed_recall_idempotency_key,
            effect_type="memory_recall",
        )
        if not first_record:
            entry = await self._stores.side_effect_ledger_store.get_entry(
                delayed_recall_idempotency_key
            )
            if entry is not None and entry.result_ref:
                return entry.result_ref

        delayed_recall_request_artifact_id = await self._store_delayed_recall_artifact(
            task_id=task_id,
            trace_id=trace_id,
            artifact_id=self._derive_artifact_id(
                "recallreq",
                task_id,
                llm_call_idempotency_key,
            ),
            name="delayed-recall-request",
            description="Delayed recall durable request carrier",
            content=json.dumps(
                {
                    "task_id": task_id,
                    "context_frame_id": context_frame_id,
                    "request_artifact_ref": request_artifact_id,
                    "scheduled_at": datetime.now(UTC).isoformat(),
                    **plan,
                },
                ensure_ascii=False,
                indent=2,
            ),
            session_id=session_id,
            source="delayed-recall-request",
        )
        event = await self._append_event_only_with_retry(
            task_id=task_id,
            event_builder=lambda seq: Event(
                event_id=str(ULID()),
                task_id=task_id,
                task_seq=seq,
                ts=datetime.now(UTC),
                type=EventType.MEMORY_RECALL_SCHEDULED,
                actor=ActorType.SYSTEM,
                payload=MemoryRecallScheduledPayload(
                    context_frame_id=context_frame_id,
                    query=str(plan["query"]),
                    scope_ids=list(plan["scope_ids"]),
                    request_artifact_ref=delayed_recall_request_artifact_id,
                    initial_hit_count=int(plan["initial_hit_count"]),
                    delivered_hit_count=int(plan["delivered_hit_count"]),
                    schedule_reason=str(plan["schedule_reason"]),
                    degraded_reasons=list(plan["degraded_reasons"]),
                ).model_dump(),
                trace_id=trace_id,
                causality=EventCausality(idempotency_key=f"{delayed_recall_idempotency_key}:event"),
            ),
        )
        if self._sse_hub:
            await self._sse_hub.broadcast(task_id, event)
        await self._stores.side_effect_ledger_store.set_result_ref(
            delayed_recall_idempotency_key,
            delayed_recall_request_artifact_id,
        )
        await self._agent_context.record_delayed_recall_state(
            context_frame_id=context_frame_id,
            status="scheduled",
            request_artifact_id=delayed_recall_request_artifact_id,
            schedule_reason=str(plan["schedule_reason"]),
        )
        return delayed_recall_request_artifact_id

    async def _materialize_delayed_recall_once(
        self,
        *,
        task_id: str,
        trace_id: str,
        context_frame_id: str,
        llm_call_idempotency_key: str,
        request_artifact_id: str,
        session_id: str | None,
    ) -> str:
        if not context_frame_id or not request_artifact_id:
            return ""

        delayed_recall_idempotency_key = f"{llm_call_idempotency_key}:delayed_recall_materialize"
        first_record = await self._stores.side_effect_ledger_store.try_record(
            task_id=task_id,
            step_key=f"delayed_recall_materialize:{llm_call_idempotency_key}",
            idempotency_key=delayed_recall_idempotency_key,
            effect_type="memory_recall",
        )
        if not first_record:
            entry = await self._stores.side_effect_ledger_store.get_entry(
                delayed_recall_idempotency_key
            )
            if entry is not None and entry.result_ref:
                return entry.result_ref

        frame = await self._stores.agent_context_store.get_context_frame(context_frame_id)
        if frame is None:
            return ""
        plan = self._build_delayed_recall_plan(frame)
        if not plan["query"] or not plan["scope_ids"]:
            return ""

        task = await self.get_task(task_id)
        if task is None:
            return ""

        try:
            await init_memory_db(self._stores.conn)
            project, workspace = await self._agent_context.resolve_project_scope(
                task=task,
                surface=task.requester.channel,
            )
            memory_service = await self._agent_context.get_memory_service(
                project=project,
                workspace=workspace,
            )
            agent_profile = await self._stores.agent_context_store.get_agent_profile(
                frame.agent_profile_id
            )
            policy = effective_memory_access_policy(agent_profile)
            recall = await memory_service.recall_memory(
                scope_ids=list(plan["scope_ids"]),
                query=str(plan["query"]),
                policy=policy,
                per_scope_limit=max(
                    memory_recall_per_scope_limit(agent_profile, default=4),
                    int(plan["delivered_hit_count"]) or 0,
                ),
                max_hits=max(
                    memory_recall_max_hits(agent_profile, default=8),
                    int(plan["initial_hit_count"]) or 0,
                ),
                hook_options=build_default_memory_recall_hook_options(
                    agent_profile=agent_profile
                ),
            )
            result_artifact_id = await self._store_delayed_recall_artifact(
                task_id=task_id,
                trace_id=trace_id,
                artifact_id=self._derive_artifact_id(
                    "recallres",
                    task_id,
                    llm_call_idempotency_key,
                ),
                name="delayed-recall-result",
                description="Delayed recall materialized result",
                content=json.dumps(
                    {
                        "task_id": task_id,
                        "context_frame_id": context_frame_id,
                        "request_artifact_ref": request_artifact_id,
                        "materialized_at": datetime.now(UTC).isoformat(),
                        "schedule_reason": str(plan["schedule_reason"]),
                        "recall": recall.model_dump(mode="json"),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                session_id=session_id,
                source="delayed-recall-result",
            )
            event = await self._append_event_only_with_retry(
                task_id=task_id,
                event_builder=lambda seq: Event(
                    event_id=str(ULID()),
                    task_id=task_id,
                    task_seq=seq,
                    ts=datetime.now(UTC),
                    type=EventType.MEMORY_RECALL_COMPLETED,
                    actor=ActorType.SYSTEM,
                    payload=MemoryRecallCompletedPayload(
                        context_frame_id=context_frame_id,
                        query=recall.query,
                        scope_ids=list(recall.scope_ids),
                        request_artifact_ref=request_artifact_id,
                        result_artifact_ref=result_artifact_id,
                        hit_count=len(recall.hits),
                        backend=(
                            recall.backend_status.active_backend
                            if recall.backend_status is not None
                            else ""
                        ),
                        backend_state=(
                            recall.backend_status.state.value
                            if recall.backend_status is not None
                            else ""
                        ),
                        degraded_reasons=list(recall.degraded_reasons),
                    ).model_dump(),
                    trace_id=trace_id,
                    causality=EventCausality(
                        idempotency_key=f"{delayed_recall_idempotency_key}:event"
                    ),
                ),
            )
            if self._sse_hub:
                await self._sse_hub.broadcast(task_id, event)
            await self._stores.side_effect_ledger_store.set_result_ref(
                delayed_recall_idempotency_key,
                result_artifact_id,
            )
            await self._agent_context.record_delayed_recall_state(
                context_frame_id=context_frame_id,
                status="completed",
                request_artifact_id=request_artifact_id,
                result_artifact_id=result_artifact_id,
                schedule_reason=str(plan["schedule_reason"]),
                recall=recall,
            )
            return result_artifact_id
        except Exception as exc:
            error_type = type(exc).__name__
            error_message = str(exc)
            event = await self._append_event_only_with_retry(
                task_id=task_id,
                event_builder=lambda seq, error_type=error_type, error_message=error_message: Event(
                    event_id=str(ULID()),
                    task_id=task_id,
                    task_seq=seq,
                    ts=datetime.now(UTC),
                    type=EventType.MEMORY_RECALL_FAILED,
                    actor=ActorType.SYSTEM,
                    payload=MemoryRecallFailedPayload(
                        context_frame_id=context_frame_id,
                        query=str(plan["query"]),
                        scope_ids=list(plan["scope_ids"]),
                        request_artifact_ref=request_artifact_id,
                        error_type=error_type,
                        error_message=error_message,
                        degraded_reasons=list(plan["degraded_reasons"]),
                    ).model_dump(),
                    trace_id=trace_id,
                    causality=EventCausality(
                        idempotency_key=f"{delayed_recall_idempotency_key}:failed"
                    ),
                ),
            )
            if self._sse_hub:
                await self._sse_hub.broadcast(task_id, event)
            await self._agent_context.record_delayed_recall_state(
                context_frame_id=context_frame_id,
                status="failed",
                request_artifact_id=request_artifact_id,
                schedule_reason=str(plan["schedule_reason"]),
                error_summary=str(exc),
            )
            log.warning(
                "delayed_recall_materialize_degraded",
                task_id=task_id,
                context_frame_id=context_frame_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return ""

    async def _store_delayed_recall_artifact(
        self,
        *,
        task_id: str,
        trace_id: str,
        artifact_id: str,
        name: str,
        description: str,
        content: str,
        session_id: str | None,
        source: str,
    ) -> str:
        artifact = await self.create_text_artifact(
            task_id=task_id,
            name=name,
            description=description,
            content=content,
            artifact_id=artifact_id,
            allow_existing=True,
            trace_id=trace_id,
            emit_event=False,
            session_id=session_id,
            source=source,
        )
        return artifact.artifact_id

    @staticmethod
    def _build_delayed_recall_plan(frame) -> dict[str, Any]:
        memory_recall = dict(frame.budget.get("memory_recall", {}))
        prefetch_mode = str(memory_recall.get("prefetch_mode", "")).strip().lower()
        if prefetch_mode in {"hint_first", "agent_led_hint_first"} and not bool(
            memory_recall.get("agent_led_recall_executed", False)
        ):
            return {
                "enabled": False,
                "query": str(memory_recall.get("query", "")).strip(),
                "scope_ids": [
                    str(item).strip()
                    for item in memory_recall.get(
                        "scope_ids",
                        frame.budget.get("memory_scope_ids", []),
                    )
                    if str(item).strip()
                ],
                "initial_hit_count": 0,
                "delivered_hit_count": 0,
                "degraded_reasons": [],
                "schedule_reason": "",
                "backend": str(memory_recall.get("backend", "")),
                "backend_state": str(memory_recall.get("backend_state", "")).strip().lower(),
                "pending_replay_count": int(
                    memory_recall.get("pending_replay_count", 0) or 0
                ),
            }
        query = str(memory_recall.get("query", "")).strip()
        scope_ids = [
            str(item).strip()
            for item in memory_recall.get("scope_ids", frame.budget.get("memory_scope_ids", []))
            if str(item).strip()
        ]
        initial_hit_count = max(
            int(memory_recall.get("hit_count", 0) or 0),
            len(frame.memory_hits),
        )
        delivered_hit_count = max(
            int(memory_recall.get("delivered_hit_count", 0) or 0),
            len(frame.memory_hits),
        )
        degraded_reasons = [
            str(item).strip()
            for item in memory_recall.get("degraded_reasons", [])
            if str(item).strip()
        ]
        schedule_reasons: list[str] = []
        if initial_hit_count > delivered_hit_count:
            schedule_reasons.append("prompt_budget_trimmed")
        backend_state = str(memory_recall.get("backend_state", "")).strip().lower()
        if backend_state and backend_state != "healthy":
            schedule_reasons.append(f"memory_backend_{backend_state}")
        pending_replay_count = int(memory_recall.get("pending_replay_count", 0) or 0)
        if pending_replay_count > 0:
            schedule_reasons.append("memory_sync_backlog")
        schedule_reasons.extend(
            item
            for item in degraded_reasons
            if item == "prompt_budget_trimmed" or item.startswith("memory_")
        )
        schedule_reason = "; ".join(dict.fromkeys(item for item in schedule_reasons if item))
        return {
            "enabled": bool(query and scope_ids and schedule_reason),
            "query": query,
            "scope_ids": scope_ids,
            "initial_hit_count": initial_hit_count,
            "delivered_hit_count": delivered_hit_count,
            "degraded_reasons": degraded_reasons,
            "schedule_reason": schedule_reason,
            "backend": str(memory_recall.get("backend", "")),
            "backend_state": backend_state,
            "pending_replay_count": pending_replay_count,
        }

    async def _persist_compaction_flush(
        self,
        *,
        task_id: str,
        context_frame_id: str,
        summary_text: str,
        summary_artifact_id: str,
        request_artifact_id: str,
        flush_idempotency_key: str,
        worker_capability: str | None,
        compressed_turn_count: int,
    ) -> str:
        task = await self.get_task(task_id)
        if task is None:
            return ""

        try:
            await init_memory_db(self._stores.conn)
            project, workspace = await self._agent_context.resolve_project_scope(
                task=task,
                surface=task.requester.channel,
            )
            flush_scope_id, flush_scope_metadata = await self._resolve_compaction_flush_scope(
                task=task,
                context_frame_id=context_frame_id,
            )
            if not flush_scope_id:
                return ""
            memory_service = await self._agent_context.get_memory_service(
                project=project,
                workspace=workspace,
            )
            run = await memory_service.run_memory_maintenance(
                MemoryMaintenanceCommand(
                    command_id=str(ULID()),
                    kind=MemoryMaintenanceCommandKind.FLUSH,
                    scope_id=flush_scope_id,
                    partition=MemoryPartition.WORK,
                    reason="context compaction flush",
                    requested_by=f"context_compaction:{worker_capability or 'main'}",
                    idempotency_key=flush_idempotency_key,
                    summary=summary_text,
                    evidence_refs=[
                        EvidenceRef(
                            ref_id=summary_artifact_id,
                            ref_type="artifact",
                            snippet=summary_text[:120],
                        ),
                        EvidenceRef(
                            ref_id=request_artifact_id,
                            ref_type="artifact",
                            snippet="llm request context snapshot",
                        ),
                    ],
                    metadata={
                        "source": "context_compaction",
                        "task_id": task_id,
                        "compressed_turn_count": compressed_turn_count,
                        **flush_scope_metadata,
                    },
                )
            )
            return run.run_id
        except Exception as exc:
            log.warning(
                "context_compaction_flush_degraded",
                task_id=task_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return ""

    async def _resolve_compaction_flush_scope(
        self,
        *,
        task,
        context_frame_id: str,
    ) -> tuple[str, dict[str, str]]:
        if context_frame_id:
            frame = await self._stores.agent_context_store.get_context_frame(context_frame_id)
            if frame is not None:
                namespaces = []
                for namespace_id in frame.memory_namespace_ids:
                    namespace = await self._stores.agent_context_store.get_memory_namespace(
                        namespace_id
                    )
                    if namespace is not None:
                        namespaces.append(namespace)
                private_namespace = next(
                    (
                        item
                        for item in namespaces
                        if item.kind
                        in {
                            MemoryNamespaceKind.BUTLER_PRIVATE,
                            MemoryNamespaceKind.WORKER_PRIVATE,
                        }
                        and item.memory_scope_ids
                    ),
                    None,
                )
                if private_namespace is not None:
                    return private_namespace.memory_scope_ids[0], {
                        "memory_namespace_id": private_namespace.namespace_id,
                        "memory_namespace_kind": private_namespace.kind.value,
                        "memory_scope_id": private_namespace.memory_scope_ids[0],
                    }
                project_namespace = next(
                    (
                        item
                        for item in namespaces
                        if item.kind is MemoryNamespaceKind.PROJECT_SHARED
                        and item.memory_scope_ids
                    ),
                    None,
                )
                if project_namespace is not None:
                    return project_namespace.memory_scope_ids[0], {
                        "memory_namespace_id": project_namespace.namespace_id,
                        "memory_namespace_kind": project_namespace.kind.value,
                        "memory_scope_id": project_namespace.memory_scope_ids[0],
                    }
        if task.scope_id:
            return task.scope_id, {
                "memory_namespace_id": "",
                "memory_namespace_kind": "legacy_task_scope",
                "memory_scope_id": task.scope_id,
            }
        return "", {}

    def _truncate_response_summary(self, text: str, suffix: str = "see artifact") -> str:
        """截断超出字节限制的响应摘要（UTF-8 字节数）"""
        if len(text.encode("utf-8")) > self.RESPONSE_SUMMARY_MAX_BYTES:
            truncated = text.encode("utf-8")[: self.RESPONSE_SUMMARY_MAX_BYTES].decode(
                "utf-8", errors="ignore"
            )
            return truncated + f"... [truncated, {suffix}]"
        return text

    async def _store_llm_artifact(
        self,
        task_id: str,
        llm_result,
        *,
        session_id: str | None = None,
    ) -> tuple[str, Artifact]:
        """存储 LLM 响应为 Artifact"""
        artifact = await self.create_text_artifact(
            task_id=task_id,
            name="llm-response",
            description="LLM 响应内容",
            content=llm_result.content,
            trace_id=f"trace-{task_id}",
            emit_event=False,
            session_id=session_id,
            source="llm-response",
        )
        return artifact.artifact_id, artifact

    async def _write_model_call_completed(
        self, task_id: str, trace_id: str, llm_result, artifact_id: str
    ) -> None:
        """写入 MODEL_CALL_COMPLETED 事件（含响应截断逻辑）"""
        # 响应摘要截断（对齐 FR-002-CL-4）
        response_summary = self._truncate_response_summary(llm_result.content, "see artifact")

        event = await self._append_event_only_with_retry(
            task_id=task_id,
            event_builder=lambda seq: Event(
                event_id=str(ULID()),
                task_id=task_id,
                task_seq=seq,
                ts=datetime.now(UTC),
                type=EventType.MODEL_CALL_COMPLETED,
                actor=ActorType.SYSTEM,
                payload=ModelCallCompletedPayload(
                    model_alias=llm_result.model_alias,
                    model_name=llm_result.model_name,
                    provider=llm_result.provider,
                    response_summary=response_summary,
                    duration_ms=llm_result.duration_ms,
                    token_usage={
                        "prompt_tokens": llm_result.token_usage.prompt_tokens,
                        "completion_tokens": llm_result.token_usage.completion_tokens,
                        "total_tokens": llm_result.token_usage.total_tokens,
                    },
                    cost_usd=llm_result.cost_usd,
                    cost_unavailable=llm_result.cost_unavailable,
                    is_fallback=llm_result.is_fallback,
                    artifact_ref=artifact_id,
                ).model_dump(),
                trace_id=trace_id,
            ),
        )
        if self._sse_hub:
            await self._sse_hub.broadcast(task_id, event)

    async def _resolve_reused_artifact_id(self, idempotency_key: str) -> str | None:
        """根据幂等键查找可复用的 artifact 引用"""
        entry = await self._stores.side_effect_ledger_store.get_entry(idempotency_key)
        if entry is None or not entry.result_ref:
            return None
        artifact = await self._stores.artifact_store.get_artifact(entry.result_ref)
        if artifact is None:
            return None
        return artifact.artifact_id

    async def _write_model_call_reused_events(
        self,
        task_id: str,
        trace_id: str,
        model_alias: str,
        artifact_id: str,
    ) -> None:
        """恢复场景复用已存在 artifact，补写必要事件（若缺失）。"""
        existing_events = await self._stores.event_store.get_events_for_task(task_id)
        has_model_completed = any(
            e.type == EventType.MODEL_CALL_COMPLETED
            and e.payload.get("artifact_ref") == artifact_id
            for e in existing_events
        )
        has_artifact_created = any(
            e.type == EventType.ARTIFACT_CREATED and e.payload.get("artifact_id") == artifact_id
            for e in existing_events
        )
        if has_model_completed and has_artifact_created:
            return

        artifact = await self._stores.artifact_store.get_artifact(artifact_id)
        if artifact is None:
            raise RuntimeError(f"复用 artifact 不存在: {artifact_id}")

        if not has_model_completed:
            content = await self._stores.artifact_store.get_artifact_content(artifact_id)
            summary = "Reused prior LLM result"
            if content is not None:
                text = content.decode("utf-8", errors="ignore")
                summary = self._truncate_response_summary(text, "reused artifact")

            event = await self._append_event_only_with_retry(
                task_id=task_id,
                event_builder=lambda seq: Event(
                    event_id=str(ULID()),
                    task_id=task_id,
                    task_seq=seq,
                    ts=datetime.now(UTC),
                    type=EventType.MODEL_CALL_COMPLETED,
                    actor=ActorType.SYSTEM,
                    payload=ModelCallCompletedPayload(
                        model_alias=model_alias,
                        model_name="",
                        provider="",
                        response_summary=summary,
                        duration_ms=0,
                        token_usage={},
                        cost_usd=0.0,
                        cost_unavailable=True,
                        is_fallback=False,
                        artifact_ref=artifact_id,
                    ).model_dump(),
                    trace_id=trace_id,
                ),
            )
            if self._sse_hub:
                await self._sse_hub.broadcast(task_id, event)

        if not has_artifact_created:
            await self._write_artifact_created(
                task_id=task_id,
                trace_id=trace_id,
                artifact_id=artifact_id,
                artifact=artifact,
            )

    async def _write_artifact_created(
        self,
        task_id: str,
        trace_id: str,
        artifact_id: str,
        artifact: Artifact,
        *,
        session_id: str | None = None,
        source: str = "",
    ) -> None:
        """写入 ARTIFACT_CREATED 事件"""
        event = await self._append_event_only_with_retry(
            task_id=task_id,
            event_builder=lambda seq: Event(
                event_id=str(ULID()),
                task_id=task_id,
                task_seq=seq,
                ts=datetime.now(UTC),
                type=EventType.ARTIFACT_CREATED,
                actor=ActorType.SYSTEM,
                payload=ArtifactCreatedPayload(
                    artifact_id=artifact_id,
                    name=artifact.name,
                    size=artifact.size,
                    part_count=len(artifact.parts),
                    session_id=session_id,
                    source=source,
                ).model_dump(),
                trace_id=trace_id,
            ),
        )
        if self._sse_hub:
            await self._sse_hub.broadcast(task_id, event)

    def _should_execute_node(self, node_id: str, resume_from_node: str | None) -> bool:
        """根据恢复起点判断当前节点是否需要执行"""
        if resume_from_node is None:
            return True
        if resume_from_node not in self._pipeline_nodes:
            return True
        return self._pipeline_nodes.index(node_id) > self._pipeline_nodes.index(resume_from_node)

    def _derive_llm_call_idempotency_key(
        self,
        task_id: str,
        model_alias: str,
        resume_from_node: str | None,
        resume_state_snapshot: dict[str, Any] | None,
    ) -> str:
        """生成副作用幂等键。

        常规执行为每次处理生成唯一 key，避免续对话场景误复用历史结果；
        恢复执行优先复用 checkpoint 中落盘 key，兼容旧快照回退到确定性 key。
        """
        if resume_from_node is None:
            return f"{task_id}:llm_call:{model_alias}:{ULID()}"

        if resume_state_snapshot and resume_state_snapshot.get("llm_call_idempotency_key"):
            return str(resume_state_snapshot["llm_call_idempotency_key"])

        return f"{task_id}:llm_call:{model_alias}"

    async def _write_checkpoint(
        self,
        task_id: str,
        node_id: str,
        trace_id: str,
        state_snapshot: dict[str, Any],
    ) -> str:
        """写入 CHECKPOINT_SAVED 事件与 checkpoint（同事务）"""

        event, checkpoint = await self._append_event_and_checkpoint_with_retry(
            task_id=task_id,
            builder=lambda seq: self._build_checkpoint_pair(
                task_id=task_id,
                task_seq=seq,
                node_id=node_id,
                trace_id=trace_id,
                state_snapshot=state_snapshot,
            ),
        )

        if self._sse_hub:
            await self._sse_hub.broadcast(task_id, event)
        return checkpoint.checkpoint_id

    def _build_checkpoint_pair(
        self,
        task_id: str,
        task_seq: int,
        node_id: str,
        trace_id: str,
        state_snapshot: dict[str, Any],
    ) -> tuple[Event, CheckpointSnapshot]:
        """构建 checkpoint 事件与 snapshot 配对对象"""
        now = datetime.now(UTC)
        checkpoint_id = str(ULID())
        checkpoint = CheckpointSnapshot(
            checkpoint_id=checkpoint_id,
            task_id=task_id,
            node_id=node_id,
            status=CheckpointStatus.SUCCESS,
            schema_version=1,
            state_snapshot=state_snapshot,
            created_at=now,
            updated_at=now,
        )
        event = Event(
            event_id=str(ULID()),
            task_id=task_id,
            task_seq=task_seq,
            ts=now,
            type=EventType.CHECKPOINT_SAVED,
            actor=ActorType.SYSTEM,
            payload=CheckpointSavedPayload(
                checkpoint_id=checkpoint_id,
                node_id=node_id,
                schema_version=1,
            ).model_dump(),
            trace_id=trace_id,
        )
        return event, checkpoint

    async def _append_event_and_checkpoint_with_retry(self, task_id: str, builder):
        """写入 checkpoint 相关事件并在 task_seq 冲突时重试。"""
        lock = await self._get_task_lock(task_id)
        async with lock:
            for attempt in range(1, self._max_task_seq_retries + 1):
                seq = await self._stores.event_store.get_next_task_seq(task_id)
                event, checkpoint = builder(seq)
                try:
                    await append_event_and_save_checkpoint(
                        self._stores.conn,
                        self._stores.event_store,
                        self._stores.task_store,
                        self._stores.checkpoint_store,
                        event,
                        checkpoint,
                    )
                    return event, checkpoint
                except aiosqlite.IntegrityError as e:
                    if self._is_task_seq_conflict(e) and attempt < self._max_task_seq_retries:
                        log.warning(
                            "task_seq_conflict_retry",
                            task_id=task_id,
                            attempt=attempt,
                        )
                        continue
                    raise
        raise RuntimeError("failed to append checkpoint event after retries")

    async def _handle_llm_failure(
        self, task_id: str, trace_id: str, model_alias: str, error: Exception
    ) -> None:
        """处理 LLM 调用失败：写入 FAILED 事件并推进到 FAILED 状态"""
        log.error(
            "llm_processing_failed",
            task_id=task_id,
            error_type=type(error).__name__,
        )
        raw_error_message = str(error).strip()
        error_message = raw_error_message[:400] if raw_error_message else "LLM 调用失败，请查看服务端日志"
        try:
            failed_event = await self._append_event_only_with_retry(
                task_id=task_id,
                event_builder=lambda seq: Event(
                    event_id=str(ULID()),
                    task_id=task_id,
                    task_seq=seq,
                    ts=datetime.now(UTC),
                    type=EventType.MODEL_CALL_FAILED,
                    actor=ActorType.SYSTEM,
                    payload=ModelCallFailedPayload(
                        model_alias=model_alias,
                        model_name="",
                        provider="",
                        error_type=type(error).__name__,
                        error_message=error_message,
                        duration_ms=0,
                        is_fallback=False,
                    ).model_dump(),
                    trace_id=trace_id,
                ),
            )
            if self._sse_hub:
                await self._sse_hub.broadcast(task_id, failed_event)

            try:
                await self._write_state_transition(
                    task_id, TaskStatus.RUNNING, TaskStatus.FAILED, trace_id
                )
            except TaskStatusConflictError:
                log.warning(
                    "skip_failure_transition_due_state_conflict",
                    task_id=task_id,
                )
        except Exception as inner_e:
            log.error(
                "failed_to_record_failure",
                task_id=task_id,
                error_type=type(inner_e).__name__,
            )
            await self._force_mark_failed_without_event(task_id)

    async def _prepare_task_for_processing(self, task_id: str, trace_id: str) -> None:
        task = await self._stores.task_store.get_task(task_id)
        if task is None:
            raise RuntimeError(f"Task not found: {task_id}")
        if task.status == TaskStatus.RUNNING:
            return
        if task.status in {TaskStatus.CANCELLED, TaskStatus.REJECTED}:
            raise TaskStatusConflictError(
                f"Task {task_id} is not runnable from status {task.status}"
            )
        await self._write_state_transition(
            task_id=task_id,
            from_status=task.status,
            to_status=TaskStatus.RUNNING,
            trace_id=trace_id,
            reason="llm_processing_start",
        )

    async def cancel_task(self, task_id: str) -> Task | None:
        """取消任务

        Returns:
            更新后的 Task，如果任务不存在返回 None

        Raises:
            ValueError: 任务已在终态
        """
        task = await self._stores.task_store.get_task(task_id)
        if task is None:
            return None

        if task.status in TERMINAL_STATES:
            raise ValueError(f"Task is already in terminal state: {task.status}")

        # 验证流转合法性
        if not validate_transition(task.status, TaskStatus.CANCELLED):
            raise ValueError(f"Cannot transition from {task.status} to CANCELLED")

        trace_id = f"trace-{task_id}"
        await self._write_state_transition(
            task_id, task.status, TaskStatus.CANCELLED, trace_id, reason="用户取消"
        )

        return await self._stores.task_store.get_task(task_id)

    async def _write_state_transition(
        self,
        task_id: str,
        from_status: TaskStatus,
        to_status: TaskStatus,
        trace_id: str,
        reason: str = "",
    ) -> Event:
        """写入 STATE_TRANSITION 事件并更新 Task projection"""
        event = await self._append_event_and_update_task_with_retry(
            task_id=task_id,
            new_status=to_status.value,
            expected_status=from_status.value,
            event_builder=lambda seq: Event(
                event_id=str(ULID()),
                task_id=task_id,
                task_seq=seq,
                ts=datetime.now(UTC),
                type=EventType.STATE_TRANSITION,
                actor=ActorType.SYSTEM,
                payload=StateTransitionPayload(
                    from_status=from_status,
                    to_status=to_status,
                    reason=reason,
                ).model_dump(),
                trace_id=trace_id,
            ),
        )
        if self._sse_hub:
            await self._sse_hub.broadcast(task_id, event)
        return event

    async def get_task(self, task_id: str) -> Task | None:
        """查询任务详情"""
        return await self._stores.task_store.get_task(task_id)

    async def get_latest_user_metadata(self, task_id: str) -> dict[str, Any]:
        """读取当前任务累计生效的 trusted control metadata。"""
        events = await self._stores.event_store.get_events_for_task(task_id)
        return merge_control_metadata(events)

    async def get_latest_input_metadata(self, task_id: str) -> dict[str, str]:
        """读取最近一条 USER_MESSAGE 的 input metadata。"""
        events = await self._stores.event_store.get_events_for_task(task_id)
        for event in reversed(events):
            if event.type != EventType.USER_MESSAGE:
                continue
            return input_metadata_from_payload(event.payload)
        return {}

    async def list_tasks(self, status: str | None = None) -> list[Task]:
        """查询任务列表"""
        return await self._stores.task_store.list_tasks(status)

    @classmethod
    async def _get_task_lock(cls, task_id: str) -> asyncio.Lock:
        """获取 task 级别锁，序列化同一任务的事件写入。"""
        async with cls._task_locks_guard:
            lock = cls._task_locks.get(task_id)
            if lock is None:
                lock = asyncio.Lock()
                cls._task_locks[task_id] = lock
            return lock

    @staticmethod
    def _is_task_seq_conflict(error: Exception) -> bool:
        if not isinstance(error, aiosqlite.IntegrityError):
            return False
        text = str(error)
        return "idx_events_task_seq" in text or "events.task_id, events.task_seq" in text

    @staticmethod
    def _is_idempotency_conflict(error: Exception) -> bool:
        if not isinstance(error, aiosqlite.IntegrityError):
            return False
        text = str(error)
        return "idx_events_idempotency_key" in text or "events.idempotency_key" in text

    async def _find_event_by_idempotency_key(
        self,
        task_id: str,
        idempotency_key: str,
    ) -> Event | None:
        events = await self._stores.event_store.get_events_for_task(task_id)
        for event in reversed(events):
            if event.causality.idempotency_key == idempotency_key:
                return event
        return None

    async def _append_event_only_with_retry(self, task_id: str, event_builder):
        """写事件并在 task_seq 冲突时重试。"""
        lock = await self._get_task_lock(task_id)
        async with lock:
            for attempt in range(1, self._max_task_seq_retries + 1):
                seq = await self._stores.event_store.get_next_task_seq(task_id)
                event = event_builder(seq)
                try:
                    await append_event_only(
                        self._stores.conn,
                        self._stores.event_store,
                        event,
                    )
                    return event
                except aiosqlite.IntegrityError as e:
                    if self._is_idempotency_conflict(e):
                        idem_key = event.causality.idempotency_key
                        if idem_key:
                            existing = await self._find_event_by_idempotency_key(
                                task_id,
                                idem_key,
                            )
                            if existing is not None:
                                return existing
                    if self._is_task_seq_conflict(e) and attempt < self._max_task_seq_retries:
                        log.warning(
                            "task_seq_conflict_retry",
                            task_id=task_id,
                            attempt=attempt,
                        )
                        continue
                    raise

        raise RuntimeError("failed to append event after retries")

    @staticmethod
    def _derive_artifact_id(prefix: str, *parts: str) -> str:
        seed = "|".join(parts)
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
        return f"{prefix}-{digest}"

    async def _append_event_and_update_task_with_retry(
        self,
        task_id: str,
        new_status: str,
        expected_status: str | None,
        event_builder,
    ) -> Event:
        """写事件并更新 projection，在 task_seq 冲突时重试。"""
        lock = await self._get_task_lock(task_id)
        cleanup_lock = False
        result_event: Event | None = None
        async with lock:
            for attempt in range(1, self._max_task_seq_retries + 1):
                seq = await self._stores.event_store.get_next_task_seq(task_id)
                event = event_builder(seq)
                try:
                    await append_event_and_update_task(
                        self._stores.conn,
                        self._stores.event_store,
                        self._stores.task_store,
                        event,
                        new_status,
                        expected_status,
                    )
                    try:
                        cleanup_lock = TaskStatus(new_status) in TERMINAL_STATES
                    except ValueError:
                        cleanup_lock = False
                    result_event = event
                    break
                except aiosqlite.IntegrityError as e:
                    if self._is_task_seq_conflict(e) and attempt < self._max_task_seq_retries:
                        log.warning(
                            "task_seq_conflict_retry",
                            task_id=task_id,
                            attempt=attempt,
                        )
                        continue
                    raise
            else:
                raise RuntimeError("failed to append state transition after retries")
        if cleanup_lock:
            await self._cleanup_task_lock(task_id)

        if result_event is None:
            raise RuntimeError("failed to append state transition after retries")
        return result_event

    @classmethod
    async def _cleanup_task_lock(cls, task_id: str) -> None:
        """任务终态后清理 lock，避免全局字典无限增长。"""
        async with cls._task_locks_guard:
            lock = cls._task_locks.get(task_id)
            if lock is not None and not lock.locked():
                cls._task_locks.pop(task_id, None)

    async def _force_mark_failed_without_event(self, task_id: str) -> None:
        """兜底：失败事件落盘再次失败时，至少将任务推进到 FAILED，避免卡 RUNNING。"""
        task = await self._stores.task_store.get_task(task_id)
        if task is None or task.status in TERMINAL_STATES:
            return
        if not validate_transition(task.status, TaskStatus.FAILED):
            return

        try:
            await self._stores.task_store.update_task_status(
                task_id=task_id,
                status=TaskStatus.FAILED.value,
                updated_at=datetime.now(UTC).isoformat(),
                latest_event_id=task.pointers.latest_event_id or "",
            )
            await self._stores.conn.commit()
            log.warning(
                "task_force_failed_without_event",
                task_id=task_id,
            )
            await self._cleanup_task_lock(task_id)
        except Exception as e:
            await self._stores.conn.rollback()
            log.error(
                "task_force_failed_update_error",
                task_id=task_id,
                error_type=type(e).__name__,
            )

    async def mark_running_task_failed_for_recovery(
        self,
        task_id: str,
        reason: str,
    ) -> None:
        """恢复流程使用：将卡在 RUNNING 的任务推进到 FAILED。"""
        task = await self._stores.task_store.get_task(task_id)
        if task is None:
            return
        if task.status != TaskStatus.RUNNING:
            return
        await self._handle_llm_failure(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            model_alias="main",
            error=RuntimeError(reason),
        )

    async def mark_running_task_cancelled_for_runtime(
        self,
        task_id: str,
        reason: str,
    ) -> None:
        """运行时取消流程使用：将任务推进到 CANCELLED。"""
        task = await self._stores.task_store.get_task(task_id)
        if task is None or task.status in TERMINAL_STATES:
            return

        try:
            if task.status == TaskStatus.CREATED:
                await self._write_state_transition(
                    task_id=task_id,
                    from_status=TaskStatus.CREATED,
                    to_status=TaskStatus.RUNNING,
                    trace_id=f"trace-{task_id}",
                    reason="runtime_cancel_bootstrap",
                )
                task = await self._stores.task_store.get_task(task_id)
                if task is None:
                    return

            if task.status == TaskStatus.RUNNING:
                await self._write_state_transition(
                    task_id=task_id,
                    from_status=TaskStatus.RUNNING,
                    to_status=TaskStatus.CANCELLED,
                    trace_id=f"trace-{task_id}",
                    reason=reason,
                )
                return

            if task.status == TaskStatus.WAITING_INPUT:
                await self._write_state_transition(
                    task_id=task_id,
                    from_status=TaskStatus.WAITING_INPUT,
                    to_status=TaskStatus.CANCELLED,
                    trace_id=f"trace-{task_id}",
                    reason=reason,
                )
                return

            if task.status == TaskStatus.WAITING_APPROVAL:
                await self._write_state_transition(
                    task_id=task_id,
                    from_status=TaskStatus.WAITING_APPROVAL,
                    to_status=TaskStatus.CANCELLED,
                    trace_id=f"trace-{task_id}",
                    reason=reason,
                )
                return

            if task.status == TaskStatus.PAUSED:
                await self._write_state_transition(
                    task_id=task_id,
                    from_status=TaskStatus.PAUSED,
                    to_status=TaskStatus.CANCELLED,
                    trace_id=f"trace-{task_id}",
                    reason=reason,
                )
        except TaskStatusConflictError:
            log.info("task_cancel_conflict_skip", task_id=task_id)
