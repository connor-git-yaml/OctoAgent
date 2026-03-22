"""Feature 059 + Feature 064: Subagent 生命周期管理。

Worker 按需创建/销毁临时 Subagent。
Subagent 共享 Worker 的 Project，用完回收。

Feature 064 P1 扩展:
- SubagentExecutor: 独立执行循环（asyncio.Task）
- spawn_subagent: 创建 Child Task + A2AConversation + 独立 SkillRunner
- kill_subagent: 发送 A2A CANCEL + 流转 Child Task 终态

Feature 064 P3 代码质量优化:
- SubagentSpawnParams / SubagentSpawnContext 配置对象分层
- SubagentOutcome StrEnum 替代裸字符串
- emit_task_event 提取到 core 层
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol

import structlog
from octoagent.core.models.agent_context import DEFAULT_PERMISSION_PRESET
from octoagent.core.event_helpers import emit_task_event
from octoagent.core.models import (
    A2AConversation,
    A2AConversationStatus,
    A2AMessageDirection,
    A2AMessageRecord,
    ActorType,
    AgentRuntime,
    AgentRuntimeRole,
    AgentRuntimeStatus,
    AgentSession,
    AgentSessionKind,
    AgentSessionStatus,
    Event,
    EventType,
    TaskStatus,
)
from octoagent.core.models.task import RequesterInfo, Task, TaskPointers
from octoagent.core.store import StoreGroup
from octoagent.skills.manifest import SkillManifest
from octoagent.skills.models import (
    SkillExecutionContext,
    SkillRunResult,
    SkillRunStatus,
    UsageLimits,
)
from octoagent.skills.runner import SkillRunner
from octoagent.tooling.protocols import EventStoreProtocol, ToolBrokerProtocol
from ulid import ULID

if TYPE_CHECKING:
    from octoagent.skills.protocols import (
        ApprovalBridgeProtocol,
        StructuredModelClientProtocol,
    )

log = structlog.get_logger(__name__)


# ============================================================
# SubagentOutcome StrEnum — 替代裸字符串 (Feature 064 P3 优化 2)
# ============================================================


class SubagentOutcome(StrEnum):
    """Subagent 执行结果状态枚举。"""

    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


# ============================================================
# 配置对象分层 (Feature 064 P3 优化 1)
# ============================================================


@dataclass
class SubagentSpawnParams:
    """Subagent 业务层参数。"""

    task_description: str = ""
    permission_preset: str = DEFAULT_PERMISSION_PRESET
    model_alias: str | None = None
    usage_limits: dict[str, Any] | None = None  # max_steps, max_duration_seconds 等
    name: str = ""
    persona_summary: str = ""


@dataclass
class SubagentSpawnContext:
    """Subagent 调用者上下文（由编排层提供）。"""

    parent_task_id: str = ""
    project_id: str = ""  # 从 parent_runtime 推导，调用方可不填
    workspace_id: str = ""  # 从 parent_runtime 推导，调用方可不填
    model_client: Any = None  # StructuredModelClientProtocol 或兼容 Protocol
    tool_broker: Any = None  # ToolBrokerProtocol
    event_store: Any = None  # EventStoreProtocol
    parent_manifest: Any = None  # SkillManifest
    approval_bridge: Any | None = None
    result_callback: SubagentResultCallback | None = None


# ============================================================
# 回调协议：SubagentExecutor 完成后通知 Orchestrator
# ============================================================


class SubagentResultCallback(Protocol):
    """SubagentExecutor 完成后的回调接口。"""

    async def __call__(
        self,
        *,
        parent_task_id: str,
        child_task_id: str,
        subagent_name: str,
        status: SubagentOutcome,
        summary: str,
        artifact_count: int,
    ) -> None: ...


# ============================================================
# SubagentExecutor -- 独立执行循环（Feature 064 P1-A 核心）
# ============================================================


class SubagentExecutor:
    """管理单个 Subagent 的独立执行循环。

    在独立的 asyncio.Task 中运行 SkillRunner，不阻塞父 Worker 主循环。
    支持心跳上报、优雅取消、异常退出自动清理。
    """

    def __init__(
        self,
        *,
        child_task: Task,
        skill_runner: SkillRunner,
        manifest: SkillManifest,
        execution_context: SkillExecutionContext,
        a2a_conversation_id: str,
        parent_agent_uri: str,
        subagent_agent_uri: str,
        event_store: EventStoreProtocol,
        store_group: StoreGroup,
        heartbeat_interval: int = 5,
        result_callback: SubagentResultCallback | None = None,
    ) -> None:
        self._child_task = child_task
        self._runner = skill_runner
        self._manifest = manifest
        self._context = execution_context
        self._a2a_conversation_id = a2a_conversation_id
        self._parent_agent_uri = parent_agent_uri
        self._subagent_agent_uri = subagent_agent_uri
        self._event_store = event_store
        self._store_group = store_group
        self._heartbeat_interval = heartbeat_interval
        self._result_callback = result_callback

        self._cancel_event = asyncio.Event()
        self._asyncio_task: asyncio.Task | None = None

    @property
    def child_task_id(self) -> str:
        return self._child_task.task_id

    @property
    def is_running(self) -> bool:
        return self._asyncio_task is not None and not self._asyncio_task.done()

    async def start(self) -> None:
        """启动独立 asyncio.Task 执行循环。"""
        self._asyncio_task = asyncio.create_task(
            self._run_loop(),
            name=f"subagent-{self._child_task.task_id}",
        )

    async def cancel(self) -> None:
        """优雅取消。设置取消标志并 cancel asyncio.Task。"""
        self._cancel_event.set()
        if self._asyncio_task and not self._asyncio_task.done():
            self._asyncio_task.cancel()

    async def wait(self) -> None:
        """等待执行完成（用于测试）。"""
        if self._asyncio_task:
            try:
                await self._asyncio_task
            except (asyncio.CancelledError, Exception):
                pass

    # ----- 主循环 -----

    async def _run_loop(self) -> None:
        """独立执行循环，含心跳上报和优雅终止。"""
        try:
            # 流转 Child Task 到 RUNNING
            await self._transition_task(TaskStatus.RUNNING)

            # 执行 SkillRunner
            task_description = self._context.metadata.get("task_description", "")
            result = await self._runner.run(
                manifest=self._manifest,
                execution_context=self._context,
                skill_input={},
                prompt=task_description,
            )

            # 根据结果发送 A2A 消息并流转 Task
            if result.status == SkillRunStatus.SUCCEEDED:
                summary = self._extract_summary(result)
                await self._send_a2a_result(result, summary)
                await self._transition_task(TaskStatus.SUCCEEDED)
                await self._notify_parent(
                    status=SubagentOutcome.SUCCEEDED,
                    summary=summary,
                    artifact_count=0,
                )
            else:
                error_msg = result.error_message or "Subagent execution failed"
                await self._send_a2a_error(error_msg)
                await self._transition_task(TaskStatus.FAILED)
                await self._notify_parent(
                    status=SubagentOutcome.FAILED,
                    summary=f"Error: {error_msg}",
                    artifact_count=0,
                )

        except asyncio.CancelledError:
            await self._send_a2a_cancel_response()
            await self._transition_task(TaskStatus.CANCELLED)
            await self._notify_parent(
                status=SubagentOutcome.CANCELLED,
                summary="Subagent was cancelled",
                artifact_count=0,
            )
            raise

        except Exception as exc:
            log.error(
                "subagent_executor_error",
                child_task_id=self._child_task.task_id,
                error=str(exc),
                exc_info=True,
            )
            await self._send_a2a_error(str(exc))
            await self._transition_task(TaskStatus.FAILED)
            await self._notify_parent(
                status=SubagentOutcome.FAILED,
                summary=f"Unexpected error: {exc}",
                artifact_count=0,
            )

        finally:
            await self._cleanup()

    # ----- A2A 消息辅助 -----

    async def _send_a2a_result(self, result: SkillRunResult, summary: str) -> None:
        """发送 A2A RESULT 消息。"""
        await self._record_a2a_message(
            message_type="RESULT",
            direction=A2AMessageDirection.OUTBOUND,
            payload={
                "state": "completed",
                "summary": summary,
                "status": result.status.value,
                "steps": result.steps,
                "duration_ms": result.duration_ms,
            },
        )

    async def _send_a2a_error(self, error_message: str) -> None:
        """发送 A2A ERROR 消息。"""
        await self._record_a2a_message(
            message_type="ERROR",
            direction=A2AMessageDirection.OUTBOUND,
            payload={
                "state": "failed",
                "error_type": "execution_error",
                "error_message": error_message,
            },
        )

    async def _send_a2a_cancel_response(self) -> None:
        """发送 A2A CANCEL 响应消息。"""
        await self._record_a2a_message(
            message_type="CANCEL",
            direction=A2AMessageDirection.OUTBOUND,
            payload={
                "reason": "cancelled_by_parent",
            },
        )

    async def _record_a2a_message(
        self,
        *,
        message_type: str,
        direction: A2AMessageDirection,
        payload: dict[str, Any],
    ) -> None:
        """记录 A2A 消息到 A2A Store 并写入审计事件。"""
        try:
            message_id = f"a2a-msg-{ULID()}"

            def build_message(message_seq: int) -> A2AMessageRecord:
                return A2AMessageRecord(
                    a2a_message_id=message_id,
                    a2a_conversation_id=self._a2a_conversation_id,
                    message_seq=message_seq,
                    task_id=self._child_task.task_id,
                    work_id="",
                    project_id=self._child_task.scope_id,
                    workspace_id="",
                    source_agent_runtime_id=self._context.agent_runtime_id,
                    source_agent_session_id=self._context.agent_session_id,
                    target_agent_runtime_id="",
                    target_agent_session_id="",
                    direction=direction,
                    message_type=message_type,
                    protocol_message_id=message_id,
                    from_agent=self._subagent_agent_uri,
                    to_agent=self._parent_agent_uri,
                    idempotency_key=f"{self._child_task.task_id}:{message_id}",
                    payload=payload,
                    trace={},
                    metadata={},
                    raw_message={},
                    created_at=datetime.now(tz=UTC),
                )

            await self._store_group.a2a_store.append_message(
                self._a2a_conversation_id, build_message
            )
            await self._store_group.conn.commit()

            # 写入 A2A_MESSAGE_SENT 审计事件到 Child Task
            await self._emit_event(
                EventType.A2A_MESSAGE_SENT,
                {
                    "message_type": message_type,
                    "a2a_conversation_id": self._a2a_conversation_id,
                    "from_agent": self._subagent_agent_uri,
                    "to_agent": self._parent_agent_uri,
                },
            )
        except Exception:
            log.warning(
                "subagent_a2a_message_failed",
                child_task_id=self._child_task.task_id,
                message_type=message_type,
                exc_info=True,
            )

    # ----- 状态流转 -----

    async def _transition_task(self, new_status: TaskStatus) -> None:
        """流转 Child Task 状态。"""
        try:
            now = datetime.now(tz=UTC)
            event_id = f"evt-{ULID()}"

            await self._store_group.task_store.update_task_status(
                self._child_task.task_id,
                new_status.value,
                now.isoformat(),
                event_id,
            )

            # 发射 STATE_TRANSITION 事件
            await self._emit_event(
                EventType.STATE_TRANSITION,
                {
                    "from_status": self._child_task.status.value,
                    "to_status": new_status.value,
                    "reason": "subagent_lifecycle",
                },
            )
            self._child_task.status = new_status

            await self._store_group.conn.commit()
        except Exception:
            log.warning(
                "subagent_task_transition_failed",
                child_task_id=self._child_task.task_id,
                new_status=new_status.value,
                exc_info=True,
            )

    # ----- 事件发射 -----

    async def _emit_event(self, event_type: EventType, payload: dict[str, Any]) -> None:
        """发射事件到 Child Task 的事件流。

        Feature 064 P3: 委托给 core 层 emit_task_event()，消除重复逻辑。
        """
        try:
            await emit_task_event(
                self._event_store,
                task_id=self._child_task.task_id,
                event_type=event_type,
                payload=payload,
                actor=ActorType.WORKER,
                trace_id=self._context.trace_id,
            )
        except Exception:
            log.warning(
                "subagent_event_emit_failed",
                child_task_id=self._child_task.task_id,
                event_type=event_type.value,
                exc_info=True,
            )

    # ----- 通知父 Worker -----

    async def _notify_parent(
        self,
        *,
        status: SubagentOutcome,
        summary: str,
        artifact_count: int,
    ) -> None:
        """通知父 Worker Subagent 结果（通过回调）。"""
        if self._result_callback is None:
            return
        try:
            await self._result_callback(
                parent_task_id=self._context.parent_task_id or "",
                child_task_id=self._child_task.task_id,
                subagent_name=self._manifest.description or self._manifest.skill_id,
                status=status,
                summary=summary,
                artifact_count=artifact_count,
            )
        except Exception:
            log.warning(
                "subagent_result_callback_failed",
                child_task_id=self._child_task.task_id,
                exc_info=True,
            )

    # ----- 辅助 -----

    @staticmethod
    def _extract_summary(result: SkillRunResult) -> str:
        """从 SkillRunResult 提取摘要。"""
        if result.output and result.output.content:
            content = result.output.content
            return content[:500] if len(content) > 500 else content
        return f"Completed in {result.steps} steps, {result.duration_ms}ms"

    async def _cleanup(self) -> None:
        """清理 Subagent 资源：关闭 Session、归档 Runtime。"""
        try:
            await kill_subagent(
                store_group=self._store_group,
                subagent_runtime_id=self._context.agent_runtime_id,
            )
        except Exception:
            log.warning(
                "subagent_cleanup_failed",
                child_task_id=self._child_task.task_id,
                runtime_id=self._context.agent_runtime_id,
                exc_info=True,
            )


# ============================================================
async def spawn_subagent(
    *,
    store_group: StoreGroup,
    parent_worker_runtime_id: str,
    params: SubagentSpawnParams,
    ctx: SubagentSpawnContext,
) -> tuple[AgentRuntime, AgentSession] | tuple[AgentRuntime, AgentSession, SubagentExecutor]:
    """为指定 Worker 创建一个临时 Subagent Runtime + Session。

    当 ctx 中提供 model_client/tool_broker/event_store/parent_manifest 时，
    同时创建 Child Task + A2AConversation + SubagentExecutor（独立执行循环）。
    """
    parent_runtime = await store_group.agent_context_store.get_agent_runtime(
        parent_worker_runtime_id
    )
    if parent_runtime is None:
        raise ValueError(f"Parent worker runtime 不存在: {parent_worker_runtime_id}")

    now = datetime.now(tz=UTC)
    runtime_id = f"subagent-{str(ULID())}"
    effective_name = params.name or f"Subagent of {parent_runtime.name or parent_worker_runtime_id}"

    runtime = AgentRuntime(
        agent_runtime_id=runtime_id,
        project_id=parent_runtime.project_id,
        workspace_id="",
        agent_profile_id=parent_runtime.agent_profile_id,
        worker_profile_id=parent_runtime.worker_profile_id,
        role=AgentRuntimeRole.WORKER,
        name=effective_name,
        persona_summary=params.persona_summary,
        status=AgentRuntimeStatus.ACTIVE,
        permission_preset=parent_runtime.permission_preset,
        metadata={
            "is_subagent": True,
            "parent_worker_runtime_id": parent_worker_runtime_id,
            "parent_task_id": ctx.parent_task_id,
            "task_description": params.task_description,
        },
        created_at=now,
        updated_at=now,
    )
    await store_group.agent_context_store.save_agent_runtime(runtime)

    session_id = f"session-subagent-{str(ULID())}"
    session = AgentSession(
        agent_session_id=session_id,
        agent_runtime_id=runtime_id,
        kind=AgentSessionKind.SUBAGENT_INTERNAL,
        status=AgentSessionStatus.ACTIVE,
        project_id=parent_runtime.project_id,
        workspace_id="",
        parent_worker_runtime_id=parent_worker_runtime_id,
        created_at=now,
        updated_at=now,
    )
    await store_group.agent_context_store.save_agent_session(session)

    await store_group.conn.commit()

    log.info(
        "subagent_spawned",
        subagent_runtime_id=runtime_id,
        parent_worker_runtime_id=parent_worker_runtime_id,
        project_id=parent_runtime.project_id,
        session_id=session_id,
        has_executor=bool(
            ctx.model_client and ctx.tool_broker and ctx.event_store and ctx.parent_manifest
        ),
    )

    if (
        ctx.model_client
        and ctx.tool_broker
        and ctx.event_store
        and ctx.parent_manifest
        and ctx.parent_task_id
    ):
        executor = await _create_subagent_executor(
            store_group=store_group,
            parent_runtime=parent_runtime,
            runtime=runtime,
            session=session,
            parent_task_id=ctx.parent_task_id,
            task_description=params.task_description,
            permission_preset=params.permission_preset,
            usage_limits=params.usage_limits,
            model_client=ctx.model_client,
            tool_broker=ctx.tool_broker,
            event_store=ctx.event_store,
            parent_manifest=ctx.parent_manifest,
            approval_bridge=ctx.approval_bridge,
            result_callback=ctx.result_callback,
        )
        return runtime, session, executor

    return runtime, session


async def _create_subagent_executor(
    *,
    store_group: StoreGroup,
    parent_runtime: AgentRuntime,
    runtime: AgentRuntime,
    session: AgentSession,
    parent_task_id: str,
    task_description: str,
    permission_preset: str,
    usage_limits: dict[str, Any] | None,
    model_client: StructuredModelClientProtocol,
    tool_broker: ToolBrokerProtocol,
    event_store: EventStoreProtocol,
    parent_manifest: SkillManifest,
    approval_bridge: ApprovalBridgeProtocol | None,
    result_callback: SubagentResultCallback | None,
) -> SubagentExecutor:
    """内部辅助：创建 Child Task + A2AConversation + SubagentExecutor。"""
    now = datetime.now(tz=UTC)
    child_task_id = f"task-{ULID()}"
    trace_id = f"trace-{ULID()}"

    # 1. 创建 Child Task
    child_task = Task(
        task_id=child_task_id,
        created_at=now,
        updated_at=now,
        status=TaskStatus.CREATED,
        title=(
            task_description[:200]
            if task_description
            else f"Subagent task ({runtime.agent_runtime_id})"
        ),
        thread_id=parent_task_id,  # 关联父 Task 的 thread
        scope_id=parent_runtime.project_id,
        requester=RequesterInfo(channel="subagent", sender_id=runtime.agent_runtime_id),
        pointers=TaskPointers(),
        trace_id=trace_id,
        parent_task_id=parent_task_id,
    )
    await store_group.task_store.create_task(child_task)

    # 发射 TASK_CREATED 事件
    task_created_event = Event(
        event_id=f"evt-{ULID()}",
        task_id=child_task_id,
        task_seq=1,
        ts=now,
        type=EventType.TASK_CREATED,
        actor=ActorType.SYSTEM,
        payload={
            "parent_task_id": parent_task_id,
            "parent_worker_runtime_id": parent_runtime.agent_runtime_id,
            "subagent_runtime_id": runtime.agent_runtime_id,
            "task_description": task_description[:500],
        },
        trace_id=trace_id,
    )
    await event_store.append_event(task_created_event)

    # 2. 创建 A2AConversation
    parent_agent_uri = f"agent://workers/{parent_runtime.agent_runtime_id}"
    subagent_agent_uri = (
        f"agent://workers/{parent_runtime.agent_runtime_id}"
        f"/subagents/{runtime.agent_runtime_id}"
    )
    conversation_id = f"a2a-conv-{ULID()}"

    conversation = A2AConversation(
        a2a_conversation_id=conversation_id,
        task_id=child_task_id,
        work_id="",
        project_id=parent_runtime.project_id,
        workspace_id="",
        source_agent_runtime_id=parent_runtime.agent_runtime_id,
        source_agent_session_id="",
        target_agent_runtime_id=runtime.agent_runtime_id,
        target_agent_session_id=session.agent_session_id,
        source_agent=parent_agent_uri,
        target_agent=subagent_agent_uri,
        status=A2AConversationStatus.ACTIVE,
        trace_id=trace_id,
        metadata={
            "is_subagent_conversation": True,
            "parent_task_id": parent_task_id,
        },
        created_at=now,
        updated_at=now,
    )
    await store_group.a2a_store.save_conversation(conversation)

    # 3. 发送 A2A TASK 消息
    task_message_id = f"a2a-msg-{ULID()}"

    def build_task_msg(message_seq: int) -> A2AMessageRecord:
        return A2AMessageRecord(
            a2a_message_id=task_message_id,
            a2a_conversation_id=conversation_id,
            message_seq=message_seq,
            task_id=child_task_id,
            work_id="",
            project_id=parent_runtime.project_id,
            workspace_id="",
            source_agent_runtime_id=parent_runtime.agent_runtime_id,
            source_agent_session_id="",
            target_agent_runtime_id=runtime.agent_runtime_id,
            target_agent_session_id=session.agent_session_id,
            direction=A2AMessageDirection.OUTBOUND,
            message_type="TASK",
            protocol_message_id=task_message_id,
            from_agent=parent_agent_uri,
            to_agent=subagent_agent_uri,
            idempotency_key=f"{child_task_id}:{task_message_id}:task",
            payload={
                "user_text": task_description,
                "metadata": {"parent_task_id": parent_task_id},
            },
            trace={"trace_id": trace_id},
            metadata={},
            raw_message={},
            created_at=now,
        )

    await store_group.a2a_store.append_message(conversation_id, build_task_msg)

    await store_group.conn.commit()

    # 4. 衍生 SkillManifest（C-05）
    subagent_manifest = parent_manifest.model_copy(
        update={
            "skill_id": f"subagent-{runtime.agent_runtime_id}",
            "description": task_description[:500] if task_description else "Subagent task",
        }
    )

    # 5. 创建独立 UsageLimits
    limits_kwargs: dict[str, Any] = {
        "max_steps": 30,
        "max_duration_seconds": 1800.0,
    }
    if usage_limits:
        limits_kwargs.update(usage_limits)
    subagent_limits = UsageLimits(**limits_kwargs)

    # 6. 创建 SkillExecutionContext
    subagent_context = SkillExecutionContext(
        task_id=child_task_id,
        trace_id=trace_id,
        caller="subagent",
        agent_runtime_id=runtime.agent_runtime_id,
        agent_session_id=session.agent_session_id,
        permission_preset=permission_preset,
        conversation_messages=[],
        metadata={
            "task_description": task_description,
            "parent_task_id": parent_task_id,
            "parent_worker_runtime_id": parent_runtime.agent_runtime_id,
            "is_subagent": True,
        },
        usage_limits=subagent_limits,
        parent_task_id=parent_task_id,
    )

    # 7. 创建独立 SkillRunner（共享 ToolBroker，独立 model_client）
    subagent_runner = SkillRunner(
        model_client=model_client,
        tool_broker=tool_broker,
        event_store=event_store,
        approval_bridge=approval_bridge,
    )

    # 8. 创建 SubagentExecutor
    executor = SubagentExecutor(
        child_task=child_task,
        skill_runner=subagent_runner,
        manifest=subagent_manifest,
        execution_context=subagent_context,
        a2a_conversation_id=conversation_id,
        parent_agent_uri=parent_agent_uri,
        subagent_agent_uri=subagent_agent_uri,
        event_store=event_store,
        store_group=store_group,
        heartbeat_interval=parent_manifest.heartbeat_interval_steps,
        result_callback=result_callback,
    )

    # 启动独立执行循环
    await executor.start()

    log.info(
        "subagent_executor_started",
        child_task_id=child_task_id,
        parent_task_id=parent_task_id,
        subagent_runtime_id=runtime.agent_runtime_id,
        a2a_conversation_id=conversation_id,
    )

    return executor


# ============================================================
# kill_subagent() — Feature 059 原始 + Feature 064 P1 扩展
# ============================================================


async def kill_subagent(
    *,
    store_group: StoreGroup,
    subagent_runtime_id: str,
    # Feature 064 P1 新增参数
    executor: SubagentExecutor | None = None,
    event_store: EventStoreProtocol | None = None,
) -> bool:
    """关闭 Subagent 的 Session 并归档 Runtime。

    Feature 064 P1 扩展：如果提供 executor，发送 A2A CANCEL 并优雅终止执行循环。

    返回 True 表示成功清理，False 表示 runtime 不存在。
    """
    runtime = await store_group.agent_context_store.get_agent_runtime(subagent_runtime_id)
    if runtime is None:
        return False

    # Feature 064 P1: 优雅取消 SubagentExecutor
    if executor and executor.is_running:
        await executor.cancel()
        # 等待短时间让 cancel 流程完成
        try:
            await asyncio.wait_for(executor.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            log.warning(
                "subagent_cancel_timeout",
                subagent_runtime_id=subagent_runtime_id,
            )

    now = datetime.now(tz=UTC)

    # 关闭所有关联的 subagent session
    subagent_sessions = await store_group.agent_context_store.list_subagent_sessions(
        runtime.metadata.get("parent_worker_runtime_id", subagent_runtime_id),
        status=AgentSessionStatus.ACTIVE,
    )
    for s in subagent_sessions:
        if s.agent_runtime_id == subagent_runtime_id:
            await store_group.agent_context_store.save_agent_session(
                s.model_copy(
                    update={
                        "status": AgentSessionStatus.CLOSED,
                        "closed_at": now,
                        "updated_at": now,
                    }
                )
            )

    # 归档 runtime
    await store_group.agent_context_store.save_agent_runtime(
        runtime.model_copy(
            update={
                "status": AgentRuntimeStatus.ARCHIVED,
                "archived_at": now,
                "updated_at": now,
            }
        )
    )

    await store_group.conn.commit()

    log.info(
        "subagent_killed",
        subagent_runtime_id=subagent_runtime_id,
        parent_worker_runtime_id=runtime.metadata.get("parent_worker_runtime_id"),
    )

    return True


# ============================================================
# list_active_subagents() — 保持不变
# ============================================================


async def list_active_subagents(
    *,
    store_group: StoreGroup,
    parent_worker_runtime_id: str,
) -> list[AgentRuntime]:
    """列出指定 Worker 的所有活跃 Subagent。"""
    sessions = await store_group.agent_context_store.list_subagent_sessions(
        parent_worker_runtime_id,
        status=AgentSessionStatus.ACTIVE,
    )
    if not sessions:
        return []

    runtime_ids = {s.agent_runtime_id for s in sessions}
    runtimes: list[AgentRuntime] = []
    for runtime_id in runtime_ids:
        rt = await store_group.agent_context_store.get_agent_runtime(runtime_id)
        if rt is not None and rt.status == AgentRuntimeStatus.ACTIVE:
            runtimes.append(rt)

    return runtimes
