"""TaskService -- 任务创建/取消/查询业务逻辑

实现消息接收后的任务创建流程：
1. 检查 idempotency_key 去重
2. 创建 Task projection
3. 写入 TASK_CREATED + USER_MESSAGE 事件
4. 异步启动后台 LLM 处理
"""

import asyncio
from datetime import UTC, datetime

import aiosqlite
import structlog
from octoagent.core.config import (
    MESSAGE_PREVIEW_LENGTH,
)
from octoagent.core.models import (
    ActorType,
    Artifact,
    ArtifactPart,
    Event,
    EventCausality,
    EventType,
    PartType,
    RequesterInfo,
    Task,
    TaskStatus,
    TERMINAL_STATES,
    validate_transition,
)
from octoagent.core.models.message import NormalizedMessage
from octoagent.core.models.payloads import (
    ArtifactCreatedPayload,
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
    append_event_and_update_task,
    append_event_only,
    create_task_with_initial_events,
)
from ulid import ULID

log = structlog.get_logger()


class TaskService:
    """任务业务服务"""

    _task_locks: dict[str, asyncio.Lock] = {}
    _task_locks_guard = asyncio.Lock()
    _max_task_seq_retries = 3

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

    # 响应摘要截断阈值（对齐 FR-002-CL-4，沿用 M0 8KB 阈值）
    RESPONSE_SUMMARY_MAX_BYTES = 8192

    async def process_task_with_llm(
        self,
        task_id: str,
        user_text: str,
        llm_service,
        model_alias: str | None = None,
    ) -> None:
        """异步后台处理：LLM 调用 + 事件写入 + Artifact 存储

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
        try:
            # 1. STATE_TRANSITION: CREATED -> RUNNING
            await self._write_state_transition(
                task_id, TaskStatus.CREATED, TaskStatus.RUNNING, trace_id
            )

            # 2. MODEL_CALL_STARTED 事件
            request_summary = f"User asks: {user_text[:100]}"
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

            # 3. LLM 调用（返回 ModelCallResult）
            llm_result = await llm_service.call(user_text, model_alias=model_alias)

            # 4. 存储 Artifact + 写入完成事件
            artifact_id, artifact = await self._store_llm_artifact(task_id, llm_result)
            await self._write_model_call_completed(
                task_id, trace_id, llm_result, artifact_id
            )
            await self._write_artifact_created(task_id, trace_id, artifact_id, artifact)

            # 5. STATE_TRANSITION: RUNNING -> SUCCEEDED
            await self._write_state_transition(
                task_id, TaskStatus.RUNNING, TaskStatus.SUCCEEDED, trace_id
            )

        except TaskStatusConflictError:
            log.info(
                "task_state_conflict_skip_processing",
                task_id=task_id,
            )
            return
        except Exception as e:
            await self._handle_llm_failure(task_id, trace_id, effective_alias, e)

    async def _store_llm_artifact(self, task_id: str, llm_result) -> tuple[str, Artifact]:
        """存储 LLM 响应为 Artifact"""
        artifact_id = str(ULID())
        content_bytes = llm_result.content.encode("utf-8")
        artifact = Artifact(
            artifact_id=artifact_id,
            task_id=task_id,
            ts=datetime.now(UTC),
            name="llm-response",
            description="LLM 响应内容",
            parts=[ArtifactPart(type=PartType.TEXT, content=llm_result.content)],
        )
        await self._stores.artifact_store.put_artifact(artifact, content_bytes)
        await self._stores.conn.commit()
        return artifact_id, artifact

    async def _write_model_call_completed(
        self, task_id: str, trace_id: str, llm_result, artifact_id: str
    ) -> None:
        """写入 MODEL_CALL_COMPLETED 事件（含响应截断逻辑）"""
        # 响应摘要截断（对齐 FR-002-CL-4）
        response_summary = llm_result.content
        if len(response_summary.encode("utf-8")) > self.RESPONSE_SUMMARY_MAX_BYTES:
            truncated = response_summary.encode("utf-8")[
                : self.RESPONSE_SUMMARY_MAX_BYTES
            ].decode("utf-8", errors="ignore")
            response_summary = truncated + "... [truncated, see artifact]"

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

    async def _write_artifact_created(
        self, task_id: str, trace_id: str, artifact_id: str, artifact: Artifact
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
                    name="llm-response",
                    size=artifact.size,
                    part_count=len(artifact.parts),
                ).model_dump(),
                trace_id=trace_id,
            ),
        )
        if self._sse_hub:
            await self._sse_hub.broadcast(task_id, event)

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
