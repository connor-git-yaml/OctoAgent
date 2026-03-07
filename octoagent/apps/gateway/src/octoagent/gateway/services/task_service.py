"""TaskService -- 任务创建/取消/查询业务逻辑

实现消息接收后的任务创建流程：
1. 检查 idempotency_key 去重
2. 创建 Task projection
3. 写入 TASK_CREATED + USER_MESSAGE 事件
4. 异步启动后台 LLM 处理
"""

import asyncio
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
    PartType,
    RequesterInfo,
    Task,
    TaskStatus,
    validate_transition,
)
from octoagent.core.models.message import NormalizedMessage
from octoagent.core.models.payloads import (
    ArtifactCreatedPayload,
    CheckpointSavedPayload,
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
from ulid import ULID

from .execution_context import bind_execution_context

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
                attachment_count=len(message.attachments),
                metadata=message.metadata,
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
    ) -> Event:
        """向已有任务追加 USER_MESSAGE 事件。"""
        task = await self._stores.task_store.get_task(task_id)
        if task is None:
            raise ValueError(f"Task not found: {task_id}")

        trace_id = f"trace-{task_id}"
        text_preview = text[:MESSAGE_PREVIEW_LENGTH]
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
                    attachment_count=attachment_count,
                    metadata=metadata or {},
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

    async def create_text_artifact(
        self,
        *,
        task_id: str,
        name: str,
        description: str,
        content: str,
        trace_id: str | None = None,
        emit_event: bool = True,
        session_id: str | None = None,
        source: str = "",
    ) -> Artifact:
        """创建文本 Artifact 并写入 ARTIFACT_CREATED 事件。"""
        artifact = Artifact(
            artifact_id=str(ULID()),
            task_id=task_id,
            ts=datetime.now(UTC),
            name=name,
            description=description,
            parts=[ArtifactPart(type=PartType.TEXT, content=content)],
        )
        await self._stores.artifact_store.put_artifact(artifact, content.encode("utf-8"))
        await self._stores.conn.commit()
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

            # 2. MODEL_CALL_STARTED 事件
            request_summary = f"User asks: {user_text[:100]}"
            if self._should_execute_node("model_call_started", resume_from_node):
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
                        ).model_dump(),
                        trace_id=trace_id,
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
                        llm_result = await llm_service.call(user_text, model_alias=model_alias)

                    # 4. 存储 Artifact + 写入完成事件
                    artifact_id, artifact = await self._store_llm_artifact(
                        task_id,
                        llm_result,
                        session_id=(
                            execution_context.session_id
                            if execution_context is not None
                            else None
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
                            execution_context.session_id
                            if execution_context is not None
                            else None
                        ),
                        source="llm-response",
                    )
                else:
                    reused_artifact_id = await self._resolve_reused_artifact_id(
                        llm_call_idempotency_key
                    )
                    if reused_artifact_id is None:
                        raise RuntimeError(
                            "检测到重复副作用幂等键，但缺少可复用结果引用"
                        )
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
            e.type == EventType.ARTIFACT_CREATED
            and e.payload.get("artifact_id") == artifact_id
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
        return self._pipeline_nodes.index(node_id) > self._pipeline_nodes.index(
            resume_from_node
        )

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
                        error_message="LLM 调用失败，请查看服务端日志",
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
            raise ValueError(
                f"Cannot transition from {task.status} to CANCELLED"
            )

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
                    if self._is_task_seq_conflict(e) and attempt < self._max_task_seq_retries:
                        log.warning(
                            "task_seq_conflict_retry",
                            task_id=task_id,
                            attempt=attempt,
                        )
                        continue
                    raise

        raise RuntimeError("failed to append event after retries")

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
