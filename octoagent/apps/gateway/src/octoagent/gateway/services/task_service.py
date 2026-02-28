"""TaskService -- 任务创建/取消/查询业务逻辑

实现消息接收后的任务创建流程：
1. 检查 idempotency_key 去重
2. 创建 Task projection
3. 写入 TASK_CREATED + USER_MESSAGE 事件
4. 异步启动后台 LLM 处理
"""

from datetime import UTC, datetime

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
from octoagent.core.store.transaction import append_event_and_update_task, append_event_only
from ulid import ULID

log = structlog.get_logger()


class TaskService:
    """任务业务服务"""

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

        # 创建 Task projection
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
        await self._stores.task_store.create_task(task)
        await self._stores.conn.commit()

        # 写入 TASK_CREATED 事件
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
        await append_event_only(
            self._stores.conn,
            self._stores.event_store,
            event_1,
        )

        # 广播 TASK_CREATED 事件
        if self._sse_hub:
            await self._sse_hub.broadcast(task_id, event_1)

        # 写入 USER_MESSAGE 事件
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
        await append_event_only(
            self._stores.conn,
            self._stores.event_store,
            event_2,
        )

        # 广播 USER_MESSAGE 事件
        if self._sse_hub:
            await self._sse_hub.broadcast(task_id, event_2)

        return task_id, True

    async def process_task_with_llm(
        self,
        task_id: str,
        user_text: str,
        llm_service,
    ) -> None:
        """异步后台处理：LLM 调用 + 事件写入 + Artifact 存储

        流程：
        1. STATE_TRANSITION: CREATED -> RUNNING
        2. MODEL_CALL_STARTED 事件
        3. LLM 调用（Echo/Mock）
        4. MODEL_CALL_COMPLETED 事件 + Artifact 写入
        5. ARTIFACT_CREATED 事件
        6. STATE_TRANSITION: RUNNING -> SUCCEEDED
        """
        trace_id = f"trace-{task_id}"
        try:
            # 1. STATE_TRANSITION: CREATED -> RUNNING
            await self._write_state_transition(
                task_id, TaskStatus.CREATED, TaskStatus.RUNNING, trace_id
            )

            # 2. MODEL_CALL_STARTED 事件
            now = datetime.now(UTC)
            seq = await self._stores.event_store.get_next_task_seq(task_id)
            request_summary = f"User asks: {user_text[:100]}"
            started_event = Event(
                event_id=str(ULID()),
                task_id=task_id,
                task_seq=seq,
                ts=now,
                type=EventType.MODEL_CALL_STARTED,
                actor=ActorType.SYSTEM,
                payload=ModelCallStartedPayload(
                    model_alias="echo",
                    request_summary=request_summary,
                ).model_dump(),
                trace_id=trace_id,
            )
            await append_event_only(
                self._stores.conn, self._stores.event_store, started_event
            )
            if self._sse_hub:
                await self._sse_hub.broadcast(task_id, started_event)

            # 3. LLM 调用
            llm_response = await llm_service.call(user_text)

            # 4. 存储 Artifact（LLM 响应）
            artifact_id = str(ULID())
            content_bytes = llm_response.content.encode("utf-8")
            artifact = Artifact(
                artifact_id=artifact_id,
                task_id=task_id,
                ts=datetime.now(UTC),
                name="llm-response",
                description="LLM 响应内容",
                parts=[ArtifactPart(type=PartType.TEXT, content=llm_response.content)],
            )
            await self._stores.artifact_store.put_artifact(artifact, content_bytes)
            await self._stores.conn.commit()

            # 5. MODEL_CALL_COMPLETED 事件
            now = datetime.now(UTC)
            seq = await self._stores.event_store.get_next_task_seq(task_id)
            completed_event = Event(
                event_id=str(ULID()),
                task_id=task_id,
                task_seq=seq,
                ts=now,
                type=EventType.MODEL_CALL_COMPLETED,
                actor=ActorType.SYSTEM,
                payload=ModelCallCompletedPayload(
                    model_alias=llm_response.model_alias,
                    response_summary=llm_response.content[:200],
                    duration_ms=llm_response.duration_ms,
                    token_usage=llm_response.token_usage,
                    artifact_ref=artifact_id,
                ).model_dump(),
                trace_id=trace_id,
            )
            await append_event_only(
                self._stores.conn, self._stores.event_store, completed_event
            )
            if self._sse_hub:
                await self._sse_hub.broadcast(task_id, completed_event)

            # 6. ARTIFACT_CREATED 事件
            now = datetime.now(UTC)
            seq = await self._stores.event_store.get_next_task_seq(task_id)
            artifact_event = Event(
                event_id=str(ULID()),
                task_id=task_id,
                task_seq=seq,
                ts=now,
                type=EventType.ARTIFACT_CREATED,
                actor=ActorType.SYSTEM,
                payload=ArtifactCreatedPayload(
                    artifact_id=artifact_id,
                    name="llm-response",
                    size=artifact.size,
                    part_count=len(artifact.parts),
                ).model_dump(),
                trace_id=trace_id,
            )
            await append_event_only(
                self._stores.conn, self._stores.event_store, artifact_event
            )
            if self._sse_hub:
                await self._sse_hub.broadcast(task_id, artifact_event)

            # 7. STATE_TRANSITION: RUNNING -> SUCCEEDED
            await self._write_state_transition(
                task_id, TaskStatus.RUNNING, TaskStatus.SUCCEEDED, trace_id
            )

        except Exception as e:
            # LLM 调用失败，写入 MODEL_CALL_FAILED 事件并推进到 FAILED
            log.error("llm_processing_failed", task_id=task_id, error=str(e))
            try:
                now = datetime.now(UTC)
                seq = await self._stores.event_store.get_next_task_seq(task_id)
                failed_event = Event(
                    event_id=str(ULID()),
                    task_id=task_id,
                    task_seq=seq,
                    ts=now,
                    type=EventType.MODEL_CALL_FAILED,
                    actor=ActorType.SYSTEM,
                    payload=ModelCallFailedPayload(
                        model_alias="echo",
                        error_type="model",
                        error_message=str(e),
                        duration_ms=0,
                    ).model_dump(),
                    trace_id=trace_id,
                )
                await append_event_only(
                    self._stores.conn, self._stores.event_store, failed_event
                )
                if self._sse_hub:
                    await self._sse_hub.broadcast(task_id, failed_event)

                await self._write_state_transition(
                    task_id, TaskStatus.RUNNING, TaskStatus.FAILED, trace_id
                )
            except Exception as inner_e:
                log.error(
                    "failed_to_record_failure",
                    task_id=task_id,
                    error=str(inner_e),
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

        from octoagent.core.models.enums import TERMINAL_STATES

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
        now = datetime.now(UTC)
        seq = await self._stores.event_store.get_next_task_seq(task_id)
        event = Event(
            event_id=str(ULID()),
            task_id=task_id,
            task_seq=seq,
            ts=now,
            type=EventType.STATE_TRANSITION,
            actor=ActorType.SYSTEM,
            payload=StateTransitionPayload(
                from_status=from_status,
                to_status=to_status,
                reason=reason,
            ).model_dump(),
            trace_id=trace_id,
        )
        await append_event_and_update_task(
            self._stores.conn,
            self._stores.event_store,
            self._stores.task_store,
            event,
            to_status.value,
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
