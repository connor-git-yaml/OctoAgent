"""F098 Phase D: dispatch service — A2A path mixin

从 orchestrator.py 拆分（line 2253-3359 共 ~889 行 A2A 路径函数）。

A2ADispatchMixin 提供 A2A 路径的所有 helper 方法，由 OrchestratorService 继承。
拆分原则：
- 编排（routing decision / approval / worker handler 注册）保留 orchestrator.py
- A2A target resolution（runtime/session ensure / message persistence / source/target 派生）挪本模块
- 行为零变更：通过继承 self.method 调用方式不变；外部 import OrchestratorService 不受影响
- F098 Phase B-1/B-2 新增的 _resolve_a2a_source_role / _resolve_target_agent_profile 一并挪入
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any

import structlog
from octoagent.core.models.agent_context import resolve_permission_preset
from octoagent.core.models import (
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
    DelegationTargetKind,
    DispatchEnvelope,
    Event,
    EventCausality,
    EventType,
    TaskStatus,
    TurnExecutorKind,
    WorkerResult,
)
# F099 Phase C: source_kinds 常量导入（_resolve_a2a_source_role 扩展）
from octoagent.core.models.source_kinds import (
    KNOWN_SOURCE_RUNTIME_KINDS,
    SOURCE_RUNTIME_KIND_AUTOMATION,
    SOURCE_RUNTIME_KIND_USER_CHANNEL,
)
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

from .agent_context import build_scope_aware_session_id
from .connection_metadata import resolve_delegation_target_profile_id
from .runtime_control import (
    RUNTIME_CONTEXT_JSON_KEY,
    RUNTIME_CONTEXT_KEY,
    encode_runtime_context,
    runtime_context_from_metadata,
)

log = structlog.get_logger()


class A2ADispatchMixin:
    """A2A 派发路径 helpers（F098 Phase D 从 orchestrator.py 拆分）。

    Mixin 类设计：
    - 由 OrchestratorService 继承（class OrchestratorService(A2ADispatchMixin):）
    - 通过 self 访问 OrchestratorService.__init__ 设置的 attribute（self._stores / self._delegation_plane 等）
    - 行为完全零变更（vs 拆分前 orchestrator.py 内方法）

    包含方法（共 14 + 1 helper = 15 个）：
    - _prepare_a2a_dispatch: A2A dispatch 准备（含 source/target runtime + session + conversation 创建）
    - _persist_a2a_terminal_message: A2A 终态消息持久化
    - _persist_a2a_message_and_event: A2A 消息 + event 持久化
    - _resolve_a2a_conversation / _resolve_a2a_conversations: 会话查询
    - _touch_a2a_agent_session: A2A session updated_at 触摸
    - _save_a2a_message: 单条 A2A 消息持久化
    - _write_a2a_message_event: A2A event 写入
    - _ensure_a2a_agent_runtime / _ensure_a2a_agent_session: A2A runtime/session 创建/复用
    - _a2a_status_from_worker_result: WorkerResult → A2AConversationStatus 映射
    - _agent_uri: agent URI 标准化
    - _resolve_a2a_source_role: F098 Phase B-1 source 派生
    - _resolve_target_agent_profile: F098 Phase B-2 target profile 解析
    - _first_non_empty: 通用 helper（仅 A2A 区域使用）
    """

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
        # F098 Phase B-1 (Codex review P1 闭环): source role / session_kind / agent_uri
        # 不再硬编码 MAIN/MAIN_BOOTSTRAP/main.agent。从 runtime_context/envelope 派生：
        # - 主 Agent dispatch → MAIN / MAIN_BOOTSTRAP / "main.agent"
        # - Worker A2A dispatch → WORKER / WORKER_INTERNAL / "worker.<source_capability>"
        source_role, source_session_kind, source_agent_uri = self._resolve_a2a_source_role(
            runtime_context=runtime_context,
            runtime_metadata=runtime_metadata,
            envelope_metadata=dict(envelope.metadata),
        )
        # F098 Phase B-1: source worker_profile_id / worker_capability 也从 source 派生
        source_worker_profile_id = ""
        source_worker_capability = ""
        if source_role is AgentRuntimeRole.WORKER:
            source_worker_profile_id = self._first_non_empty(
                str(runtime_metadata.get("source_worker_profile_id", "")),
                str(runtime_metadata.get("worker_profile_id", "")),
                str(envelope.metadata.get("source_worker_profile_id", "")),
            )
            source_worker_capability = self._first_non_empty(
                str(runtime_metadata.get("source_worker_capability", "")),
                str(runtime_metadata.get("worker_capability", "")),
                str(envelope.metadata.get("source_worker_capability", "")),
            )
        source_runtime = await self._ensure_a2a_agent_runtime(
            agent_runtime_id=source_agent_runtime_id,
            role=source_role,
            project_id=project_id,
            agent_profile_id=source_agent_profile_id,
            worker_profile_id=source_worker_profile_id,
            worker_capability=source_worker_capability,
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
            kind=source_session_kind,
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
        # F098 Phase B-2 (Codex review P2 闭环): target Worker 加载自己的 AgentProfile，
        # 不再复用 source profile（baseline bug）。通过 _delegation_plane.capability_pack
        # 解析（orchestrator 不直接持 capability_pack 引用）；fallback fail-loud。
        target_agent_profile_id = await self._resolve_target_agent_profile(
            requested_worker_profile_id=requested_worker_profile_id,
            worker_capability=worker_capability_hint,
            fallback_source_profile_id=source_agent_profile_id,
        )
        target_runtime = await self._ensure_a2a_agent_runtime(
            agent_runtime_id=str(envelope.metadata.get("target_agent_runtime_id", "")),
            role=AgentRuntimeRole.WORKER,
            project_id=project_id,
            agent_profile_id=target_agent_profile_id,
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
        # F098 Phase B-1: source_agent_uri 已在 _resolve_a2a_source_role 中派生
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
    def _agent_uri(label: str) -> str:
        normalized = "".join(
            ch if ch.isalnum() or ch in "._/-" else "-"
            for ch in label.strip().lower()
        ).strip("-")
        return f"agent://{normalized or 'unknown'}"

    def _resolve_a2a_source_role(
        self,
        *,
        runtime_context: Any,  # RuntimeControlContext | None
        runtime_metadata: dict[str, Any],
        envelope_metadata: dict[str, Any],
    ) -> tuple[AgentRuntimeRole, AgentSessionKind, str]:
        """F098 Phase B-1: 从显式 source 信号派生 A2A source role / session_kind / agent_uri。

        baseline 硬编码 source = MAIN/MAIN_BOOTSTRAP/main.agent，导致 worker→worker A2A
        被错误记录为 main→worker，audit chain 错误（AC-C3 / AC-I3 失败）。

        Phase D Codex post-review P1 修复：**不能用 RuntimeControlContext.turn_executor_kind**
        派生 source —— DelegationPlane.prepare_dispatch 会按 target 的 target_kind=worker
        把 turn_executor_kind 设为 WORKER（target 侧），主 Agent dispatch 也会被误判为
        source=worker。

        正确派生规则（仅信任显式 source 信号）：
        - envelope.metadata.source_runtime_kind == "worker"/"subagent" → WORKER 路径
          （worker→worker dispatch 时 task_runner / capability_pack 在 spawn 阶段显式注入）
        - runtime_metadata.source_runtime_kind 同上（fallback）
        - 否则 → MAIN / MAIN_BOOTSTRAP / "main.agent"（默认主 Agent dispatch，baseline 行为）

        worker→worker 解禁后扩展点：spawn 路径需在 envelope.metadata 注入
        source_runtime_kind=worker 信号才能正确派生 source。当前 baseline 不注入 →
        worker→worker dispatch 仍记录为 main→worker（行为兼容 baseline）。
        F099+ ask-back / source 泛化时一并补齐 spawn 路径注入逻辑。

        返回 (role, session_kind, agent_uri) 三元组。
        """
        # 仅信任显式 source 信号（envelope.metadata 或 runtime_metadata）
        # 不使用 turn_executor_kind（这是 target 侧字段，被 prepare_dispatch 写入 target_kind）
        source_runtime_kind = str(
            envelope_metadata.get("source_runtime_kind", "")
            or runtime_metadata.get("source_runtime_kind", "")
        ).strip().lower()

        # 派生 source role
        if source_runtime_kind in ("worker", "subagent"):
            source_capability = self._first_non_empty(
                str(envelope_metadata.get("source_worker_capability", "")),
                str(runtime_metadata.get("source_worker_capability", "")),
            )
            return (
                AgentRuntimeRole.WORKER,
                AgentSessionKind.WORKER_INTERNAL,
                self._agent_uri(
                    f"worker.{source_capability}" if source_capability else "worker.unknown"
                ),
            )

        # F099 Phase C: automation 分支（GATE_DESIGN G-2 / FR-C1）
        if source_runtime_kind == SOURCE_RUNTIME_KIND_AUTOMATION:
            source_automation_id = self._first_non_empty(
                str(envelope_metadata.get("source_automation_id", "")),
                str(runtime_metadata.get("source_automation_id", "")),
            ) or "unknown"
            return (
                AgentRuntimeRole.AUTOMATION,
                AgentSessionKind.AUTOMATION_INTERNAL,
                self._agent_uri(f"automation.{source_automation_id}"),
            )

        # F099 Phase C: user_channel 分支（GATE_DESIGN G-2 / FR-C1）
        if source_runtime_kind == SOURCE_RUNTIME_KIND_USER_CHANNEL:
            source_channel_id = self._first_non_empty(
                str(envelope_metadata.get("source_channel_id", "")),
                str(runtime_metadata.get("source_channel_id", "")),
            ) or "unknown"
            return (
                AgentRuntimeRole.USER_CHANNEL,
                AgentSessionKind.USER_CHANNEL,
                self._agent_uri(f"user.{source_channel_id}"),
            )

        # F099 Phase C: FR-C4 无效值降级（SHOULD 级别）
        # 非空且不在已知集合中的值 → 降级为 MAIN + warning log（不 raise）
        if source_runtime_kind and source_runtime_kind not in KNOWN_SOURCE_RUNTIME_KINDS:
            log.warning(
                "source_runtime_kind_unknown",
                source_runtime_kind=source_runtime_kind,
                hint="未知 source_runtime_kind 值，降级为 main 路径（AC-C3）",
                known_kinds=list(KNOWN_SOURCE_RUNTIME_KINDS),
            )
            # 降级为 MAIN（保护 audit chain 完整性，不静默丢失）

        # default: main path（regression 防护，无显式 source 信号或降级时保持 baseline）
        return (
            AgentRuntimeRole.MAIN,
            AgentSessionKind.MAIN_BOOTSTRAP,
            self._agent_uri("main.agent"),
        )

    async def _resolve_target_agent_profile(
        self,
        *,
        requested_worker_profile_id: str,
        worker_capability: str,
        fallback_source_profile_id: str,
    ) -> str:
        """F098 Phase B-2 (Codex review P2 闭环): A2A target Worker 加载自己的 AgentProfile。

        baseline bug：target_agent_profile_id = source_agent_profile_id（receiver 复用 caller profile）
        → 违反 H3-B "receiver 在自己 context 工作"。

        Final Codex review P2 修复：
        - 接入真实 capability_pack.resolve_worker_binding 入口（非 mock）
        - resolve_worker_binding 是已存在的方法（line 449），返回 _ResolvedWorkerBinding
          其 profile_id 即 target Worker 的 AgentProfile.profile_id（通过
          _sync_worker_profile_agent_profile 已同步存储）

        解析路径（fail-loud）：
        - 路径 1: 通过 capability_pack.resolve_worker_binding(requested_profile_id=...,
          fallback_worker_type=worker_capability) 解析 target Worker profile
        - 路径 2: requested_worker_profile_id 直接 store lookup（fallback 兜底）
        - 路径 3: fallback 到 source profile（warning log + 测试 fail-loud）

        返回 target Worker 的 profile_id。
        """
        # 路径 1: 通过 capability_pack.resolve_worker_binding 真实接入（生产路径）
        delegation_plane = getattr(self, "_delegation_plane", None)
        capability_pack = (
            getattr(delegation_plane, "capability_pack", None)
            if delegation_plane is not None
            else None
        )
        if capability_pack is not None:
            resolve_worker_binding = getattr(capability_pack, "resolve_worker_binding", None)
            if resolve_worker_binding is not None:
                try:
                    binding = await resolve_worker_binding(
                        requested_profile_id=requested_worker_profile_id or "",
                        fallback_worker_type=worker_capability or "general",
                    )
                    # binding.source_kind 区分：'builtin_singleton' / 'worker_profile' /
                    # 'agent_profile' / 'builtin_fallback'。前 3 种是真实 target profile，
                    # 'builtin_fallback' 是 worker_capability 派生的内置默认 profile（
                    # singleton:<worker_type> 形式），仍是独立 profile_id（不复用 source）。
                    if binding is not None and binding.profile_id:
                        return binding.profile_id
                except Exception as exc:
                    log.warning(
                        "a2a_target_profile_resolve_worker_binding_failed",
                        requested_worker_profile_id=requested_worker_profile_id,
                        worker_capability=worker_capability,
                        error=str(exc),
                    )

        # 路径 2: 直接 store lookup（fallback 兜底，用于 capability_pack 不可用场景）
        if requested_worker_profile_id:
            profile = await self._stores.agent_context_store.get_agent_profile(
                requested_worker_profile_id,
            )
            if profile is not None:
                return profile.profile_id
            log.warning(
                "a2a_target_profile_explicit_id_not_found",
                requested_worker_profile_id=requested_worker_profile_id,
            )

        # 路径 3: fallback (warning log)
        log.warning(
            "a2a_target_profile_fallback_to_source",
            requested_worker_profile_id=requested_worker_profile_id,
            worker_capability=worker_capability,
            fallback_source_profile_id=fallback_source_profile_id,
        )
        return fallback_source_profile_id
