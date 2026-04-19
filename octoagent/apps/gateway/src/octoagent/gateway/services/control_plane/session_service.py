"""SessionDomainService — session 相关的 document getter 与 action handler。

从 control_plane.py 提取的 session 领域逻辑，包括：
- get_session_projection / get_bootstrap_session_document / get_context_continuity_document
- _build_session_projection_items（两遍扫描）
- _handle_session_* 系列 action handler
- _resolve_session_* / _resolve_projected_* / _build_session_* 辅助方法
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import structlog
from octoagent.core.behavior_workspace import (
    ensure_filesystem_skeleton,
    materialize_agent_behavior_files,
    resolve_behavior_agent_slug,
)
from octoagent.core.models import (
    A2AConversationItem,
    A2AMessageItem,
    ActionRequestEnvelope,
    ActionResultEnvelope,
    AgentProfile,
    AgentProfileScope,
    AgentRuntime,
    AgentRuntimeItem,
    AgentRuntimeRole,
    AgentRuntimeStatus,
    AgentSession,
    AgentSessionContinuityItem,
    AgentSessionKind,
    AgentSessionStatus,
    BootstrapSessionDocument,
    ContextContinuityDocument,
    ContextFrameItem,
    ContextSessionItem,
    ControlPlaneCapability,
    ControlPlaneDegradedState,
    ControlPlaneState,
    ControlPlaneSupportStatus,
    ControlPlaneTargetRef,
    DelegationTargetKind,
    MemoryNamespaceItem,
    NormalizedMessage,
    Project,
    RecallFrameItem,
    SessionContextState,
    SessionProjectionDocument,
    SessionProjectionItem,
    SessionProjectionSummary,
    Task,
    TaskStatus,
    TurnExecutorKind,
    Work,
    WorkerProfile,
    WorkerProfileStatus,
)
from octoagent.core.models.agent_context import DEFAULT_PERMISSION_PRESET, resolve_permission_preset
from octoagent.provider.dx.backup_service import BackupService
from ulid import ULID

from ._base import ControlPlaneActionError, ControlPlaneContext, DomainServiceBase
from ..agent_context import build_projected_session_id, build_scope_aware_session_id
from ..connection_metadata import (
    merge_control_metadata,
    resolve_explicit_delegation_target_profile_id,
    resolve_explicit_session_owner_profile_id,
    resolve_turn_executor_kind,
)
from ..startup_bootstrap import (
    ensure_main_runtime_and_session,
    ensure_default_project_agent_profile,
)
from ..task_service import TaskService

_AUDIT_TASK_ID = "ops-control-plane"
_LEGACY_CONTEXT_POLLUTED_FLAG = "legacy_context_polluted"
_LEGACY_CONTEXT_POLLUTED_MESSAGE = (
    "这条历史会话仍沿用旧版 profile 继承语义，建议先重置 continuity，再继续新的对话。"
)
log = structlog.get_logger()


class SessionDomainService(DomainServiceBase):
    """Session 相关的 document getter 与 action handler。"""

    # ------------------------------------------------------------------
    # action_routes / document_routes
    # ------------------------------------------------------------------

    def action_routes(self) -> dict[str, Any]:
        return {
            "session.focus": self._handle_session_focus,
            "session.unfocus": self._handle_session_unfocus,
            "session.new": self._handle_session_new,
            "session.create_with_project": self._handle_session_create_with_project,
            "session.reset": self._handle_session_reset,
            "session.delete": self._handle_session_delete,
            "session.export": self._handle_session_export,
            "session.set_alias": self._handle_session_set_alias,
            "session.interrupt": self._handle_session_interrupt,
            "session.resume": self._handle_session_resume,
        }

    def document_routes(self) -> dict[str, Any]:
        return {
            "sessions": self.get_session_projection,
            "bootstrap_session": self.get_bootstrap_session_document,
            "context_continuity": self.get_context_continuity_document,
        }

    # ==================================================================
    # Document Getters
    # ==================================================================

    async def get_session_projection(self) -> SessionProjectionDocument:
        state, selected_project, _, _ = await self._resolve_selection()
        session_items = await self._build_session_projection_items()
        focused_session_id, focused_thread_id = self._resolve_projected_focus(
            state=state,
            session_items=session_items,
        )
        session_summary = self._build_session_projection_summary(
            session_items=session_items,
            focused_session_id=focused_session_id,
        )
        operator_summary = None
        operator_items = []
        if self._ctx.operator_inbox_service is not None:
            try:
                inbox = await self._ctx.operator_inbox_service.get_inbox()
            except Exception:
                inbox = None
            if inbox is not None:
                operator_summary = inbox.summary
                operator_items = inbox.items
        return SessionProjectionDocument(
            focused_session_id=focused_session_id,
            focused_thread_id=focused_thread_id,
            new_conversation_token=state.new_conversation_token,
            new_conversation_project_id=state.new_conversation_project_id,
            new_conversation_agent_profile_id=state.new_conversation_agent_profile_id,
            sessions=session_items,
            summary=session_summary,
            operator_summary=operator_summary,
            operator_items=operator_items,
            capabilities=[
                ControlPlaneCapability(
                    capability_id="session.new",
                    label="新对话",
                    action_id="session.new",
                ),
                ControlPlaneCapability(
                    capability_id="session.focus",
                    label="聚焦会话",
                    action_id="session.focus",
                ),
                ControlPlaneCapability(
                    capability_id="session.unfocus",
                    label="取消聚焦",
                    action_id="session.unfocus",
                ),
                ControlPlaneCapability(
                    capability_id="session.reset",
                    label="重置 continuity",
                    action_id="session.reset",
                ),
                ControlPlaneCapability(
                    capability_id="session.set_alias",
                    label="修改会话名称",
                    action_id="session.set_alias",
                ),
                ControlPlaneCapability(
                    capability_id="session.delete",
                    label="删除对话",
                    action_id="session.delete",
                ),
                ControlPlaneCapability(
                    capability_id="session.export",
                    label="导出会话",
                    action_id="session.export",
                ),
            ],
        )

    async def get_bootstrap_session_document(self) -> BootstrapSessionDocument:
        _, selected_project, _, _ = await self._resolve_selection()
        session = None
        if selected_project is not None:
            session = await self._stores.agent_context_store.get_latest_bootstrap_session(
                project_id=selected_project.project_id,
            )
        return BootstrapSessionDocument(
            active_project_id=selected_project.project_id if selected_project is not None else "",
            session=session.model_dump(mode="json") if session is not None else {},
            resumable=bool(session is not None and session.status.value != "completed"),
            warnings=[] if session is not None else ["当前 project 没有 bootstrap session。"],
            degraded=ControlPlaneDegradedState(
                is_degraded=session is None,
                reasons=["bootstrap_session_missing"] if session is None else [],
            ),
        )

    async def get_context_continuity_document(self) -> ContextContinuityDocument:
        _, selected_project, _, _ = await self._resolve_selection()
        active_project_id = selected_project.project_id if selected_project is not None else ""
        sessions = await self._stores.agent_context_store.list_session_contexts(
            project_id=active_project_id or None,
        )
        frames = await self._stores.agent_context_store.list_context_frames(
            project_id=active_project_id or None,
            limit=20,
        )
        agent_runtimes = await self._stores.agent_context_store.list_agent_runtimes(
            project_id=active_project_id or None,
        )
        agent_sessions = await self._stores.agent_context_store.list_agent_sessions(
            project_id=active_project_id or None,
            limit=20,
        )
        memory_namespaces = await self._stores.agent_context_store.list_memory_namespaces(
            project_id=active_project_id or None,
        )
        recall_frames = await self._stores.agent_context_store.list_recall_frames(
            project_id=active_project_id or None,
            limit=20,
        )
        a2a_conversations = await self._stores.a2a_store.list_conversations(
            project_id=active_project_id or None,
            limit=20,
        )
        a2a_messages = []
        for conversation in a2a_conversations[:5]:
            a2a_messages.extend(
                await self._stores.a2a_store.list_messages(
                    a2a_conversation_id=conversation.a2a_conversation_id,
                    limit=20,
                )
            )
        session_items = [
            ContextSessionItem(
                session_id=item.session_id,
                agent_runtime_id=item.agent_runtime_id,
                agent_session_id=item.agent_session_id,
                thread_id=item.thread_id,
                project_id=item.project_id,
                rolling_summary=item.rolling_summary,
                last_context_frame_id=item.last_context_frame_id,
                last_recall_frame_id=item.last_recall_frame_id,
                updated_at=item.updated_at,
            )
            for item in sessions
        ]
        frame_items = [
            ContextFrameItem(
                context_frame_id=item.context_frame_id,
                task_id=item.task_id,
                session_id=item.session_id,
                agent_runtime_id=item.agent_runtime_id,
                agent_session_id=item.agent_session_id,
                project_id=item.project_id,
                agent_profile_id=item.agent_profile_id,
                recall_frame_id=item.recall_frame_id or "",
                memory_namespace_ids=list(item.memory_namespace_ids),
                recent_summary=item.recent_summary,
                memory_hit_count=len(item.memory_hits),
                memory_hits=item.memory_hits,
                memory_recall=dict(item.budget.get("memory_recall", {})),
                budget=item.budget,
                source_refs=item.source_refs,
                degraded_reason=item.degraded_reason,
                created_at=item.created_at,
            )
            for item in frames
        ]
        runtime_items = [
            AgentRuntimeItem(
                agent_runtime_id=item.agent_runtime_id,
                role=item.role.value,
                project_id=item.project_id,
                agent_profile_id=item.agent_profile_id,
                worker_profile_id=item.worker_profile_id,
                name=item.name,
                persona_summary=item.persona_summary,
                status=item.status.value,
                metadata=item.metadata,
                updated_at=item.updated_at,
            )
            for item in agent_runtimes
        ]
        agent_session_items = [
            AgentSessionContinuityItem(
                agent_session_id=item.agent_session_id,
                agent_runtime_id=item.agent_runtime_id,
                kind=item.kind.value,
                status=item.status.value,
                project_id=item.project_id,
                thread_id=item.thread_id,
                legacy_session_id=item.legacy_session_id,
                work_id=item.work_id,
                last_context_frame_id=item.last_context_frame_id,
                last_recall_frame_id=item.last_recall_frame_id,
                updated_at=item.updated_at,
            )
            for item in agent_sessions
        ]
        namespace_items = [
            MemoryNamespaceItem(
                namespace_id=item.namespace_id,
                kind=item.kind.value,
                project_id=item.project_id,
                agent_runtime_id=item.agent_runtime_id,
                name=item.name,
                description=item.description,
                memory_scope_ids=list(item.memory_scope_ids),
                updated_at=item.updated_at,
            )
            for item in memory_namespaces
        ]
        recall_items = [
            RecallFrameItem(
                recall_frame_id=item.recall_frame_id,
                agent_runtime_id=item.agent_runtime_id,
                agent_session_id=item.agent_session_id,
                context_frame_id=item.context_frame_id,
                task_id=item.task_id,
                project_id=item.project_id,
                query=item.query,
                recent_summary=item.recent_summary,
                memory_namespace_ids=list(item.memory_namespace_ids),
                memory_hit_count=len(item.memory_hits),
                degraded_reason=item.degraded_reason,
                created_at=item.created_at,
            )
            for item in recall_frames
        ]
        conversation_items = [
            A2AConversationItem(
                a2a_conversation_id=item.a2a_conversation_id,
                task_id=item.task_id,
                work_id=item.work_id,
                project_id=item.project_id,
                source_agent_runtime_id=item.source_agent_runtime_id,
                source_agent_session_id=item.source_agent_session_id,
                target_agent_runtime_id=item.target_agent_runtime_id,
                target_agent_session_id=item.target_agent_session_id,
                source_agent=item.source_agent,
                target_agent=item.target_agent,
                context_frame_id=item.context_frame_id,
                request_message_id=item.request_message_id,
                latest_message_id=item.latest_message_id,
                latest_message_type=item.latest_message_type,
                status=item.status.value,
                message_count=item.message_count,
                trace_id=item.trace_id,
                metadata=item.metadata,
                updated_at=item.updated_at,
            )
            for item in a2a_conversations
        ]
        message_items = [
            A2AMessageItem(
                a2a_message_id=item.a2a_message_id,
                a2a_conversation_id=item.a2a_conversation_id,
                message_seq=item.message_seq,
                task_id=item.task_id,
                work_id=item.work_id,
                message_type=item.message_type,
                direction=item.direction.value,
                protocol_message_id=item.protocol_message_id,
                source_agent_runtime_id=item.source_agent_runtime_id,
                source_agent_session_id=item.source_agent_session_id,
                target_agent_runtime_id=item.target_agent_runtime_id,
                target_agent_session_id=item.target_agent_session_id,
                from_agent=item.from_agent,
                to_agent=item.to_agent,
                idempotency_key=item.idempotency_key,
                payload=item.payload,
                trace=item.trace,
                metadata=item.metadata,
                created_at=item.created_at,
            )
            for item in sorted(
                a2a_messages,
                key=lambda current: (
                    current.a2a_conversation_id,
                    current.message_seq,
                    current.created_at,
                ),
            )
        ]
        return ContextContinuityDocument(
            active_project_id=active_project_id,
            sessions=session_items,
            frames=frame_items,
            agent_runtimes=runtime_items,
            agent_sessions=agent_session_items,
            memory_namespaces=namespace_items,
            recall_frames=recall_items,
            a2a_conversations=conversation_items,
            a2a_messages=message_items,
            capabilities=[
                ControlPlaneCapability(
                    capability_id="context.refresh",
                    label="刷新 Context",
                    action_id="context.refresh",
                )
            ],
            warnings=[] if frame_items else ["当前作用域还没有 context frames。"],
            degraded=ControlPlaneDegradedState(
                is_degraded=not bool(frame_items),
                reasons=["context_frames_empty"] if not frame_items else [],
            ),
        )

    # ==================================================================
    # Session Projection 构建
    # ==================================================================

    async def _build_session_projection_items(
        self,
    ) -> list[SessionProjectionItem]:
        """两遍扫描构建 session 投影列表。"""
        tasks = await self._stores.task_store.list_tasks()
        works = await self._stores.work_store.list_works()
        session_states = await self._stores.agent_context_store.list_session_contexts()
        session_state_by_id = {item.session_id: item for item in session_states}
        latest_work_by_task: dict[str, Work] = {}
        for work in works:
            current = latest_work_by_task.get(work.task_id)
            if current is None or (work.updated_at or datetime.min.replace(tzinfo=UTC)) > (
                current.updated_at or datetime.min.replace(tzinfo=UTC)
            ):
                latest_work_by_task[work.task_id] = work
        grouped: dict[str, list[tuple[Task, Any]]] = defaultdict(list)
        for task in tasks:
            if task.task_id == _AUDIT_TASK_ID:
                continue
            project = await self._stores.project_store.resolve_project_for_scope(task.scope_id)
            if project is None:
                continue
            latest_metadata = await self._extract_latest_user_metadata(task.task_id)
            if str(latest_metadata.get("parent_task_id", "")).strip() or str(
                latest_metadata.get("parent_work_id", "")
            ).strip():
                continue
            session_id = self._resolve_projected_session_id_for_task(
                task=task,
                project=project,
                latest_metadata=latest_metadata,
            )
            grouped[session_id].append((task, project))

        session_items: list[SessionProjectionItem] = []
        for session_id, entries in grouped.items():
            latest, project = max(entries, key=lambda item: item[0].updated_at)
            session_state = session_state_by_id.get(session_id)
            related_agent_sessions = await self._list_related_agent_sessions_for_projection(
                session_id=session_id,
                thread_id=(session_state.thread_id if session_state is not None else "")
                or latest.thread_id,
                project_id=project.project_id,
                session_state=session_state,
            )
            session_alias = self._resolve_projected_session_alias(related_agent_sessions)
            session_runtime_kind = ""
            session_runtime_owner_profile_id = ""
            if session_state is not None and session_state.agent_session_id:
                agent_session = await self._stores.agent_context_store.get_agent_session(
                    session_state.agent_session_id
                )
                if agent_session is not None:
                    session_runtime_kind = agent_session.kind.value
                    runtime = await self._stores.agent_context_store.get_agent_runtime(
                        agent_session.agent_runtime_id
                    )
                    if runtime is not None:
                        session_runtime_owner_profile_id = str(
                            runtime.worker_profile_id or runtime.agent_profile_id or ""
                        ).strip()
            execution_summary: dict[str, Any] = {}
            latest_metadata = await self._extract_latest_user_metadata(latest.task_id)
            if self._ctx.task_runner is not None:
                session = await self._ctx.task_runner.get_execution_session(latest.task_id)
                if session is not None:
                    execution_summary = {
                        "session_id": session.session_id,
                        "state": session.state.value,
                        "interactive": session.interactive,
                        "current_step": session.current_step,
                        "runtime_kind": session.metadata.get("runtime_kind", ""),
                        "work_id": session.metadata.get("work_id", ""),
                    }
            latest_message = await self._extract_latest_user_message(latest.task_id)
            latest_work = latest_work_by_task.get(latest.task_id)
            runtime_kind = str(
                execution_summary.get(
                    "runtime_kind",
                    session_runtime_kind or latest_metadata.get("target_kind", ""),
                )
            )
            if runtime_kind == AgentSessionKind.MAIN_BOOTSTRAP.value:
                continue
            (
                session_owner_profile_id,
                turn_executor_kind,
                delegation_target_profile_id,
                session_agent_profile_id,
                compatibility_flags,
                compatibility_message,
                reset_recommended,
            ) = await self._resolve_session_projection_semantics(
                latest_metadata=latest_metadata,
                latest_work=latest_work,
                runtime_kind=runtime_kind,
                fallback_owner_profile_id=session_runtime_owner_profile_id,
            )
            if (
                turn_executor_kind == TurnExecutorKind.SELF.value
                and not delegation_target_profile_id
                and session_owner_profile_id
            ):
                owner_worker_profile = (
                    await self._stores.agent_context_store.get_worker_profile(
                        session_owner_profile_id
                    )
                )
                if owner_worker_profile is not None:
                    turn_executor_kind = TurnExecutorKind.WORKER.value
            session_owner_name = await self._resolve_profile_display_name(
                session_owner_profile_id
            )
            session_items.append(
                SessionProjectionItem(
                    session_id=session_id,
                    thread_id=(session_state.thread_id if session_state is not None else "")
                    or latest.thread_id,
                    task_id=latest.task_id,
                    parent_task_id=str(latest_metadata.get("parent_task_id", "")),
                    parent_work_id=str(latest_metadata.get("parent_work_id", "")),
                    title=latest.title,
                    alias=session_alias,
                    status=latest.status.value,
                    channel=latest.requester.channel,
                    requester_id=latest.requester.sender_id,
                    project_id=project.project_id,
                    agent_profile_id=session_agent_profile_id,
                    session_owner_profile_id=session_owner_profile_id,
                    session_owner_name=session_owner_name,
                    turn_executor_kind=turn_executor_kind,
                    delegation_target_profile_id=delegation_target_profile_id,
                    runtime_kind=runtime_kind,
                    compatibility_flags=compatibility_flags,
                    compatibility_message=compatibility_message,
                    reset_recommended=reset_recommended,
                    lane=self._session_lane_for_status(latest.status),
                    latest_message_summary=latest_message,
                    latest_event_at=latest.updated_at,
                    execution_summary=execution_summary,
                    capabilities=self._build_session_capabilities(latest),
                    detail_refs={
                        "task": f"/tasks/{latest.task_id}",
                        "task_api": f"/api/tasks/{latest.task_id}",
                        "execution_api": f"/api/tasks/{latest.task_id}/execution",
                    },
                )
            )
        # ── 第二遍：从 agent_sessions 补充没有 task 的会话 ──
        existing_session_ids = {item.session_id for item in session_items}
        # 防御性去重：按 (thread_id, project_id) 对兜底。
        # 历史背景：075-fix 之前 Path A（session_service 创建 ULID）+ Path B（agent_context
        # fallback composite key）会写出两条逻辑等价的 agent_sessions row，导致同一 project
        # 在侧栏出现两条重复。根因已由 agent_runtime/agent_session 的单写策略（find_active_runtime
        # + get_active_session_for_project）消除，此处保留仅为抵御迁移期残留 / 回归风险，
        # 正常情况下不应命中。
        existing_thread_project_pairs = {
            (item.thread_id, item.project_id)
            for item in session_items
            if item.thread_id and item.project_id
        }
        all_agent_sessions = await self._stores.agent_context_store.list_agent_sessions(
            limit=50,
        )
        for agent_sess in all_agent_sessions:
            if agent_sess.status != AgentSessionStatus.ACTIVE:
                continue
            if agent_sess.kind in {
                AgentSessionKind.WORKER_INTERNAL,
                AgentSessionKind.SUBAGENT_INTERNAL,
            }:
                continue
            if agent_sess.kind != AgentSessionKind.DIRECT_WORKER:
                continue
            projected_session_id = build_projected_session_id(
                thread_id=agent_sess.thread_id or agent_sess.agent_session_id,
                surface="web" if agent_sess.surface in ("", "chat", "web") else agent_sess.surface,
                scope_id=(
                    f"project:{agent_sess.project_id}:chat:web:{agent_sess.thread_id}"
                    if agent_sess.project_id and agent_sess.thread_id
                    else ""
                ),
                project_id=agent_sess.project_id,
            )
            if projected_session_id in existing_session_ids:
                continue
            # 二级去重：同一 (thread_id, project_id) 对已经在第一遍出现过则跳过
            session_thread = agent_sess.thread_id or agent_sess.agent_session_id
            if (session_thread, agent_sess.project_id) in existing_thread_project_pairs:
                continue
            proj = await self._stores.project_store.get_project(agent_sess.project_id)
            project_name = proj.name if proj else "未命名对话"
            agent_profile_id = ""
            runtime = await self._stores.agent_context_store.get_agent_runtime(
                agent_sess.agent_runtime_id
            )
            if runtime is not None:
                agent_profile_id = runtime.worker_profile_id or runtime.agent_profile_id
            owner_name = await self._resolve_profile_display_name(agent_profile_id)
            session_items.append(
                SessionProjectionItem(
                    session_id=projected_session_id,
                    thread_id=agent_sess.thread_id or agent_sess.agent_session_id,
                    task_id="",
                    parent_task_id="",
                    parent_work_id="",
                    title=project_name,
                    alias=agent_sess.alias,
                    status="created",
                    channel="web" if agent_sess.surface in ("chat", "web", "") else agent_sess.surface,
                    requester_id="",
                    project_id=agent_sess.project_id,
                    agent_profile_id=agent_profile_id,
                    session_owner_profile_id=agent_profile_id,
                    session_owner_name=owner_name,
                    turn_executor_kind=self._default_turn_executor_kind_for_runtime(
                        agent_sess.kind.value
                    ),
                    delegation_target_profile_id="",
                    runtime_kind=agent_sess.kind.value,
                    compatibility_flags=[],
                    compatibility_message="",
                    reset_recommended=False,
                    lane="queue",
                    latest_message_summary="",
                    latest_event_at=agent_sess.updated_at,
                    execution_summary={},
                    capabilities=[],
                    detail_refs={},
                )
            )

        session_items.sort(
            key=lambda item: item.latest_event_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )
        return session_items

    # ==================================================================
    # Session Projection 辅助
    # ==================================================================

    @staticmethod
    def _normalize_turn_executor_kind(
        value: TurnExecutorKind | str | None,
    ) -> str:
        if isinstance(value, TurnExecutorKind):
            return value.value
        normalized = str(value or "").strip().lower()
        if normalized in {item.value for item in TurnExecutorKind}:
            return normalized
        return ""

    @staticmethod
    def _default_turn_executor_kind_for_runtime(
        runtime_kind: str,
        *,
        target_kind: str = "",
    ) -> str:
        normalized_runtime = str(runtime_kind).strip().lower()
        normalized_target = str(target_kind).strip().lower()
        if normalized_runtime in {
            AgentSessionKind.WORKER_INTERNAL.value,
            AgentSessionKind.DIRECT_WORKER.value,
        }:
            return TurnExecutorKind.WORKER.value
        if normalized_runtime == AgentSessionKind.SUBAGENT_INTERNAL.value:
            return TurnExecutorKind.SUBAGENT.value
        if normalized_target == "subagent":
            return TurnExecutorKind.SUBAGENT.value
        if normalized_target == "worker":
            return TurnExecutorKind.WORKER.value
        return TurnExecutorKind.SELF.value

    async def _resolve_profile_display_name(self, profile_id: str) -> str:
        resolved = str(profile_id or "").strip()
        if not resolved:
            return ""
        worker_profile = await self._stores.agent_context_store.get_worker_profile(resolved)
        if worker_profile is not None:
            return worker_profile.name or ""
        agent_profile = await self._stores.agent_context_store.get_agent_profile(resolved)
        if agent_profile is not None:
            return agent_profile.name or ""
        return ""

    async def _is_worker_profile_id(self, profile_id: str) -> bool:
        resolved_profile_id = str(profile_id or "").strip()
        if not resolved_profile_id:
            return False
        profile = await self._stores.agent_context_store.get_worker_profile(resolved_profile_id)
        return profile is not None

    async def _resolve_session_projection_semantics(
        self,
        *,
        latest_metadata: Mapping[str, Any] | None,
        latest_work: Work | None,
        runtime_kind: str,
        fallback_owner_profile_id: str,
    ) -> tuple[str, str, str, str, list[str], str, bool]:
        explicit_owner_profile_id = resolve_explicit_session_owner_profile_id(latest_metadata)
        explicit_delegation_target_profile_id = resolve_explicit_delegation_target_profile_id(
            latest_metadata
        )
        legacy_agent_profile_id = str(
            (latest_metadata or {}).get("agent_profile_id", "")
            or (latest_work.agent_profile_id if latest_work is not None else "")
        ).strip()
        legacy_requested_worker_profile_id = str(
            (latest_metadata or {}).get("requested_worker_profile_id", "")
            or (latest_work.requested_worker_profile_id if latest_work is not None else "")
        ).strip()
        legacy_agent_is_worker = await self._is_worker_profile_id(legacy_agent_profile_id)
        legacy_requested_is_worker = await self._is_worker_profile_id(
            legacy_requested_worker_profile_id
        )

        session_owner_profile_id = (
            explicit_owner_profile_id
            or (latest_work.session_owner_profile_id if latest_work is not None else "")
            or fallback_owner_profile_id
        )
        delegation_target_profile_id = (
            explicit_delegation_target_profile_id
            or (latest_work.delegation_target_profile_id if latest_work is not None else "")
        )
        normalized_runtime_kind = str(runtime_kind or "").strip().lower()
        compatibility_flags: list[str] = []
        compatibility_message = ""
        reset_recommended = False

        if (
            not delegation_target_profile_id
            and legacy_requested_worker_profile_id
            and legacy_requested_is_worker
        ):
            if normalized_runtime_kind in {
                AgentSessionKind.WORKER_INTERNAL.value,
                AgentSessionKind.SUBAGENT_INTERNAL.value,
            } or (
                latest_work is not None
                and latest_work.target_kind in {
                    DelegationTargetKind.WORKER,
                    DelegationTargetKind.SUBAGENT,
                }
            ):
                delegation_target_profile_id = legacy_requested_worker_profile_id

        if not session_owner_profile_id:
            if normalized_runtime_kind == AgentSessionKind.DIRECT_WORKER.value:
                session_owner_profile_id = (
                    fallback_owner_profile_id
                    or legacy_agent_profile_id
                    or legacy_requested_worker_profile_id
                )
            elif legacy_agent_profile_id and not legacy_agent_is_worker:
                session_owner_profile_id = legacy_agent_profile_id
            elif legacy_requested_worker_profile_id and not legacy_requested_is_worker:
                session_owner_profile_id = legacy_requested_worker_profile_id

        turn_executor_kind = self._normalize_turn_executor_kind(
            resolve_turn_executor_kind(latest_metadata)
            or (latest_work.turn_executor_kind if latest_work is not None else None)
        )
        if not turn_executor_kind:
            turn_executor_kind = self._default_turn_executor_kind_for_runtime(
                runtime_kind,
                target_kind=latest_work.target_kind.value if latest_work is not None else "",
            )

        legacy_context_polluted = (
            normalized_runtime_kind == AgentSessionKind.MAIN_BOOTSTRAP.value
            and not explicit_owner_profile_id
            and legacy_agent_is_worker
            and not delegation_target_profile_id
        )
        if legacy_context_polluted:
            if _LEGACY_CONTEXT_POLLUTED_FLAG not in compatibility_flags:
                compatibility_flags.append(_LEGACY_CONTEXT_POLLUTED_FLAG)
            compatibility_message = _LEGACY_CONTEXT_POLLUTED_MESSAGE
            reset_recommended = True
            session_owner_profile_id = fallback_owner_profile_id or session_owner_profile_id
            turn_executor_kind = TurnExecutorKind.SELF.value

        legacy_agent_profile_id = (
            session_owner_profile_id
            or delegation_target_profile_id
            or fallback_owner_profile_id
            or legacy_agent_profile_id
        )
        return (
            session_owner_profile_id,
            turn_executor_kind,
            delegation_target_profile_id,
            legacy_agent_profile_id,
            compatibility_flags,
            compatibility_message,
            reset_recommended,
        )

    @staticmethod
    def _resolve_projected_session_id_for_task(
        *,
        task: Task,
        project: Any,
        latest_metadata: Mapping[str, Any] | None,
    ) -> str:
        metadata = latest_metadata or {}
        explicit_session_id = str(metadata.get("session_id", "")).strip()
        if explicit_session_id:
            return explicit_session_id
        return build_scope_aware_session_id(
            task,
            project_id=project.project_id,
        )

    @staticmethod
    def _session_lane_for_status(status: TaskStatus) -> str:
        if status is TaskStatus.RUNNING:
            return "running"
        if status in {
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.REJECTED,
        }:
            return "history"
        return "queue"

    def _build_session_projection_summary(
        self,
        *,
        session_items: list[SessionProjectionItem],
        focused_session_id: str,
    ) -> SessionProjectionSummary:
        running_sessions = sum(1 for item in session_items if item.lane == "running")
        history_sessions = sum(1 for item in session_items if item.lane == "history")
        queued_sessions = sum(1 for item in session_items if item.lane == "queue")
        return SessionProjectionSummary(
            total_sessions=len(session_items),
            running_sessions=running_sessions,
            queued_sessions=queued_sessions,
            history_sessions=history_sessions,
            focused_sessions=1 if focused_session_id.strip() else 0,
        )

    def _resolve_projected_focus(
        self,
        *,
        state: ControlPlaneState,
        session_items: list[SessionProjectionItem],
    ) -> tuple[str, str]:
        if not session_items:
            return "", ""
        if state.new_conversation_token.strip():
            return "", ""

        focused_session_id = state.focused_session_id.strip()
        if focused_session_id:
            focused = next(
                (item for item in session_items if item.session_id == focused_session_id),
                None,
            )
            if focused is not None:
                return focused.session_id, focused.thread_id
            return "", ""

        focused_thread_id = state.focused_thread_id.strip()
        if not focused_thread_id:
            return "", ""
        matches = [item for item in session_items if item.thread_id == focused_thread_id]
        if len(matches) != 1:
            return "", ""
        return matches[0].session_id, matches[0].thread_id

    async def _list_tasks_for_projected_session(
        self,
        *,
        session_id: str,
    ) -> list[Task]:
        tasks = await self._stores.task_store.list_tasks()
        matched: list[Task] = []
        for task in tasks:
            if task.task_id == _AUDIT_TASK_ID:
                continue
            project = await self._stores.project_store.resolve_project_for_scope(task.scope_id)
            if project is None:
                continue
            latest_metadata = await self._extract_latest_user_metadata(task.task_id)
            if (
                self._resolve_projected_session_id_for_task(
                    task=task,
                    project=project,
                    latest_metadata=latest_metadata,
                )
                == session_id
            ):
                matched.append(task)
        matched.sort(key=lambda item: item.created_at)
        return matched

    def _build_session_capabilities(self, task: Task) -> list[ControlPlaneCapability]:
        can_resume = task.status in {TaskStatus.FAILED, TaskStatus.REJECTED}
        can_interrupt = task.status in {
            TaskStatus.CREATED,
            TaskStatus.RUNNING,
            TaskStatus.WAITING_INPUT,
            TaskStatus.WAITING_APPROVAL,
            TaskStatus.PAUSED,
        }
        return [
            ControlPlaneCapability(
                capability_id="session.focus",
                label="聚焦",
                action_id="session.focus",
            ),
            ControlPlaneCapability(
                capability_id="session.export",
                label="导出",
                action_id="session.export",
            ),
            ControlPlaneCapability(
                capability_id="session.reset",
                label="重置",
                action_id="session.reset",
            ),
            ControlPlaneCapability(
                capability_id="session.interrupt",
                label="中断",
                action_id="session.interrupt",
                enabled=can_interrupt,
                support_status=(
                    ControlPlaneSupportStatus.SUPPORTED
                    if can_interrupt
                    else ControlPlaneSupportStatus.DEGRADED
                ),
            ),
            ControlPlaneCapability(
                capability_id="session.resume",
                label="恢复",
                action_id="session.resume",
                enabled=can_resume,
                support_status=(
                    ControlPlaneSupportStatus.SUPPORTED
                    if can_resume
                    else ControlPlaneSupportStatus.DEGRADED
                ),
            ),
        ]

    async def _extract_latest_user_message(self, task_id: str) -> str:
        from octoagent.core.models import EventType

        events = await self._stores.event_store.get_events_for_task(task_id)
        for event in reversed(events):
            if event.type == EventType.USER_MESSAGE:
                text = str(event.payload.get("text", "")).strip()
                if text:
                    return text
                return str(event.payload.get("text_preview", "")).strip()
        return ""

    async def _extract_latest_user_metadata(self, task_id: str) -> dict[str, Any]:
        events = await self._stores.event_store.get_events_for_task(task_id)
        return merge_control_metadata(events)

    # ==================================================================
    # Session 解析辅助
    # ==================================================================

    async def _resolve_session_projection_target(
        self,
        request: ActionRequestEnvelope,
        *,
        allow_empty: bool = False,
        use_focused_when_empty: bool = False,
    ) -> SessionProjectionItem | None:
        requested_session_id = self._param_str(request.params, "session_id")
        requested_thread_id = self._param_str(request.params, "thread_id")
        requested_task_id = self._param_str(request.params, "task_id")

        session_items = await self._build_session_projection_items()
        focused_session_id, focused_thread_id = self._resolve_projected_focus(
            state=self._ctx.state_store.load(),
            session_items=session_items,
        )
        if not requested_session_id and not requested_thread_id and not requested_task_id:
            if use_focused_when_empty and focused_session_id:
                requested_session_id = focused_session_id
            elif use_focused_when_empty and focused_thread_id:
                requested_thread_id = focused_thread_id
            elif allow_empty:
                return None
            else:
                raise ControlPlaneActionError(
                    "SESSION_ID_REQUIRED",
                    "session_id / thread_id / task_id 至少需要一个",
                )

        if requested_session_id:
            session = next(
                (item for item in session_items if item.session_id == requested_session_id),
                None,
            )
            if session is not None:
                return session
            thread_matches = [
                item for item in session_items if item.thread_id == requested_session_id
            ]
            if len(thread_matches) == 1:
                return thread_matches[0]
            if len(thread_matches) > 1:
                raise ControlPlaneActionError(
                    "SESSION_ID_REQUIRED",
                    "当前作用域存在多个同 thread_id 会话，请显式提供 session_id",
                )
            raise ControlPlaneActionError(
                "SESSION_NOT_FOUND",
                "当前作用域找不到对应的 session_id",
            )

        if requested_task_id:
            session = next(
                (item for item in session_items if item.task_id == requested_task_id), None
            )
            if session is None:
                raise ControlPlaneActionError(
                    "TASK_NOT_FOUND",
                    "当前作用域找不到对应的 task_id",
                )
            return session

        matches = [item for item in session_items if item.thread_id == requested_thread_id]
        if not matches:
            raise ControlPlaneActionError(
                "THREAD_NOT_FOUND",
                "当前作用域找不到对应的 thread_id",
            )
        if len(matches) > 1:
            raise ControlPlaneActionError(
                "SESSION_ID_REQUIRED",
                "当前作用域存在多个同 thread_id 会话，请显式提供 session_id",
            )
        return matches[0]

    async def _list_related_agent_sessions_for_projection(
        self,
        *,
        session_id: str,
        thread_id: str,
        project_id: str,
        session_state: SessionContextState | None = None,
    ) -> list[AgentSession]:
        related_sessions: list[AgentSession] = []
        seen_agent_session_ids: set[str] = set()

        def _append(item: AgentSession | None) -> None:
            if item is None or item.agent_session_id in seen_agent_session_ids:
                return
            seen_agent_session_ids.add(item.agent_session_id)
            related_sessions.append(item)

        if session_state is not None and session_state.agent_session_id:
            _append(
                await self._stores.agent_context_store.get_agent_session(
                    session_state.agent_session_id
                )
            )

        for legacy_session_id in {session_id, thread_id}:
            normalized = str(legacy_session_id).strip()
            if not normalized:
                continue
            candidates = await self._stores.agent_context_store.list_agent_sessions(
                legacy_session_id=normalized,
                project_id=project_id or None,
                limit=200,
            )
            for item in candidates:
                _append(item)
            # 旧会话的 legacy_session_id 可能为空（075 fix 之前创建的 DIRECT_WORKER），
            # 但 thread_id 可能等于 agent_session_id，尝试直接按 ID 查找。
            direct = await self._stores.agent_context_store.get_agent_session(normalized)
            if direct is not None and (not project_id or direct.project_id == project_id):
                _append(direct)

        related_sessions.sort(key=lambda item: item.updated_at, reverse=True)
        return related_sessions

    @staticmethod
    def _resolve_projected_session_alias(related_sessions: list[AgentSession]) -> str:
        for item in related_sessions:
            alias = item.alias.strip()
            if alias:
                return alias
        return ""

    async def _resolve_direct_session_worker_profile(
        self,
        profile_id: str,
    ) -> WorkerProfile | None:
        profile = await self._stores.agent_context_store.get_worker_profile(profile_id)
        if profile is None or profile.status == WorkerProfileStatus.ARCHIVED:
            return None
        return profile

    async def _ensure_existing_project_session(
        self,
        *,
        project: Project,
    ) -> AgentSession | None:
        existing_session = await self._stores.agent_context_store.get_active_session_for_project(
            project.project_id
        )
        if existing_session is not None:
            return existing_session

        if project.is_default:
            agent_profile = await ensure_default_project_agent_profile(self._stores, project)
            if agent_profile is not None:
                await ensure_main_runtime_and_session(
                    self._stores,
                    project,
                    agent_profile,
                )
                await self._stores.conn.commit()
                return await self._stores.agent_context_store.get_active_session_for_project(
                    project.project_id,
                    kind=AgentSessionKind.MAIN_BOOTSTRAP,
                )

        return None

    # ==================================================================
    # Action Handlers
    # ==================================================================

    async def _handle_session_focus(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        session = await self._resolve_session_projection_target(request)
        current_state = self._ctx.state_store.load()
        state = current_state.model_copy(
            update={
                "focused_session_id": session.session_id,
                "focused_thread_id": session.thread_id,
                "new_conversation_token": "",
                "new_conversation_project_id": "",
                "new_conversation_agent_profile_id": "",
                "updated_at": datetime.now(tz=UTC),
            }
        )
        self._ctx.state_store.save(state)
        return self._completed_result(
            request=request,
            code="SESSION_FOCUSED",
            message="已更新当前聚焦会话",
            data={
                "session_id": session.session_id,
                "thread_id": session.thread_id,
                "project_id": session.project_id,
            },
            resource_refs=[self._resource_ref("session_projection", "sessions:overview")],
            target_refs=[
                ControlPlaneTargetRef(target_type="session", target_id=session.session_id),
                ControlPlaneTargetRef(target_type="thread", target_id=session.thread_id),
            ],
        )

    async def _handle_session_unfocus(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        state = self._ctx.state_store.load()
        previous_session_id = state.focused_session_id.strip()
        previous_thread_id = state.focused_thread_id.strip()
        self._ctx.state_store.save(
            state.model_copy(
                update={
                    "focused_session_id": "",
                    "focused_thread_id": "",
                    "updated_at": datetime.now(tz=UTC),
                }
            )
        )
        target_refs: list[ControlPlaneTargetRef] = []
        if previous_session_id:
            target_refs.append(
                ControlPlaneTargetRef(target_type="session", target_id=previous_session_id)
            )
        if previous_thread_id:
            target_refs.append(
                ControlPlaneTargetRef(target_type="thread", target_id=previous_thread_id)
            )
        return self._completed_result(
            request=request,
            code="SESSION_UNFOCUSED",
            message="已取消当前聚焦会话",
            data={
                "previous_session_id": previous_session_id,
                "previous_thread_id": previous_thread_id,
            },
            resource_refs=[self._resource_ref("session_projection", "sessions:overview")],
            target_refs=target_refs,
        )

    async def _handle_session_new(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        target = await self._resolve_session_projection_target(
            request,
            allow_empty=True,
            use_focused_when_empty=True,
        )
        _, selected_project, _, _ = await self._resolve_selection()
        requested_agent_profile_id = str(
            request.params.get("agent_profile_id", "")
        ).strip()
        if requested_agent_profile_id:
            matched_profile = await self._resolve_direct_session_worker_profile(
                requested_agent_profile_id
            )
            if matched_profile is None:
                return self._rejected_result(
                    request=request,
                    code="SESSION_AGENT_PROFILE_NOT_FOUND",
                    message="指定的 Agent 当前不可用，无法作为新会话入口。",
                )
        token = str(ULID())
        state = self._ctx.state_store.load().model_copy(
            update={
                "focused_session_id": "",
                "focused_thread_id": "",
                "new_conversation_token": token,
                "new_conversation_project_id": (
                    selected_project.project_id if selected_project is not None else ""
                ),
                "new_conversation_agent_profile_id": requested_agent_profile_id,
                "updated_at": datetime.now(tz=UTC),
            }
        )
        self._ctx.state_store.save(state)
        target_refs: list[ControlPlaneTargetRef] = []
        if target is not None:
            target_refs = [
                ControlPlaneTargetRef(target_type="session", target_id=target.session_id),
                ControlPlaneTargetRef(target_type="thread", target_id=target.thread_id),
            ]
        return self._completed_result(
            request=request,
            code="SESSION_NEW_READY",
            message="已切换到新的会话起点",
            data={
                "new_conversation_token": token,
                "project_id": selected_project.project_id if selected_project is not None else "",
                "agent_profile_id": requested_agent_profile_id,
                "previous_session_id": target.session_id if target is not None else "",
                "previous_thread_id": target.thread_id if target is not None else "",
                "previous_task_id": target.task_id if target is not None else "",
            },
            resource_refs=[self._resource_ref("session_projection", "sessions:overview")],
            target_refs=target_refs,
        )

    async def _handle_session_create_with_project(
        self, request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        """创建新 Project + Session + 行为文件骨架，返回 session_id 和 conversation token。"""
        worker_profile_id = str(request.params.get("agent_profile_id", "")).strip()
        project_name = str(request.params.get("project_name", "")).strip()
        if not project_name:
            return self._rejected_result(
                request=request,
                code="SESSION_CREATE_MISSING_NAME",
                message="请为新对话输入一个名字。",
            )
        if not worker_profile_id:
            return self._rejected_result(
                request=request,
                code="SESSION_CREATE_MISSING_AGENT",
                message="请选择一个 Agent 来承接新对话。",
            )

        matched_profile = await self._resolve_direct_session_worker_profile(worker_profile_id)
        if matched_profile is None:
            return self._rejected_result(
                request=request,
                code="SESSION_AGENT_PROFILE_NOT_FOUND",
                message="指定的 Agent 当前不可用，无法作为新会话入口。",
            )

        slug = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", project_name.lower()).strip("-") or "session"
        if not re.search(r"[a-z0-9]", slug):
            slug = f"session-{str(ULID())[-6:]}"

        existing_project = await self._stores.project_store.get_project_by_slug(slug)
        if existing_project is not None:
            existing_session = await self._ensure_existing_project_session(
                project=existing_project,
            )
            if existing_session is not None:
                session_anchor = str(
                    existing_session.thread_id
                    or existing_session.legacy_session_id
                    or existing_session.agent_session_id
                ).strip()
                projected_session_id = build_projected_session_id(
                    thread_id=session_anchor,
                    surface=(
                        "web"
                        if existing_session.surface in {"", "chat", "web"}
                        else existing_session.surface
                    ),
                    scope_id=(
                        f"project:{existing_project.project_id}:chat:web:{session_anchor}"
                        if session_anchor
                        else ""
                    ),
                    project_id=existing_project.project_id,
                )
                existing_runtime = await self._stores.agent_context_store.get_agent_runtime(
                    existing_session.agent_runtime_id
                )
                existing_owner_profile_id = ""
                if existing_runtime is not None:
                    existing_owner_profile_id = str(
                        existing_runtime.worker_profile_id or existing_runtime.agent_profile_id or ""
                    ).strip()
                return self._completed_result(
                    request=request,
                    code="SESSION_OPENED_EXISTING_PROJECT",
                    message=f"已打开现有对话「{existing_project.name}」",
                    data={
                        "session_id": projected_session_id,
                        "agent_session_id": existing_session.agent_session_id,
                        "thread_id": session_anchor,
                        "project_id": existing_project.project_id,
                        "agent_profile_id": existing_owner_profile_id or worker_profile_id,
                    },
                    resource_refs=[self._resource_ref("session_projection", "sessions:overview")],
                    target_refs=[
                        ControlPlaneTargetRef(
                            target_type="session",
                            target_id=projected_session_id,
                        ),
                    ],
                )
            return self._rejected_result(
                request=request,
                code="SESSION_CREATE_DUPLICATE_NAME",
                message=f"同名项目「{project_name}」已存在，请换个名字。",
            )

        now = datetime.now(tz=UTC)
        project_id = f"project-{str(ULID())}"
        project = Project(
            project_id=project_id,
            slug=slug,
            name=project_name,
            description=f"由用户创建的会话项目：{project_name}",
            status="active",
            is_default=False,
            default_agent_profile_id=worker_profile_id,
            primary_agent_id="",
            created_at=now,
            updated_at=now,
        )
        await self._stores.project_store.create_project(project)

        ensure_filesystem_skeleton(
            self._ctx.project_root,
            project_slug=slug,
        )
        if matched_profile is not None:
            agent_slug = resolve_behavior_agent_slug(matched_profile)
            materialize_agent_behavior_files(
                self._ctx.project_root,
                agent_slug=agent_slug,
                agent_name=matched_profile.name,
                is_worker_profile=True,
            )

        agent_runtime_id = ""
        new_runtime = AgentRuntime(
            agent_runtime_id=f"runtime-{str(ULID())}",
            project_id=project_id,
            worker_profile_id=worker_profile_id,
            role=AgentRuntimeRole.WORKER,
            name=matched_profile.name,
            persona_summary=matched_profile.summary,
            status=AgentRuntimeStatus.ACTIVE,
            permission_preset=resolve_permission_preset(matched_profile),
            created_at=now,
            updated_at=now,
        )
        await self._stores.agent_context_store.save_agent_runtime(new_runtime)
        agent_runtime_id = new_runtime.agent_runtime_id
        project.primary_agent_id = agent_runtime_id
        await self._stores.project_store.set_primary_agent(project_id, agent_runtime_id)

        session_id = f"session-{str(ULID())}"
        thread_id_seed = f"thread-{str(ULID())}"
        projected_session_id = build_projected_session_id(
            thread_id=thread_id_seed,
            surface="web",
            scope_id=f"project:{project_id}:chat:web:{thread_id_seed}",
            project_id=project_id,
        )
        session = AgentSession(
            agent_session_id=session_id,
            agent_runtime_id=agent_runtime_id,
            project_id=project_id,
            kind=AgentSessionKind.DIRECT_WORKER,
            status=AgentSessionStatus.ACTIVE,
            surface="web",
            thread_id=thread_id_seed,
            legacy_session_id=thread_id_seed,
            created_at=now,
            updated_at=now,
        )
        await self._stores.agent_context_store.save_agent_session(session)

        # 同步写 session_context_states：让 Path B（/api/chat/send 触发的 task 执行）
        # 通过 projected_session_id 反查就能拿到 Path A 创建的 agent_runtime/session ids，
        # 从而避免 _ensure_agent_runtime/_ensure_agent_session 再 fallback 建一条新 row。
        existing_session_state = await self._stores.agent_context_store.get_session_context(
            projected_session_id
        )
        initial_session_state = (
            existing_session_state.model_copy(
                update={
                    "agent_runtime_id": agent_runtime_id,
                    "agent_session_id": session_id,
                    "thread_id": thread_id_seed,
                    "project_id": project_id,
                    "updated_at": now,
                }
            )
            if existing_session_state is not None
            else SessionContextState(
                session_id=projected_session_id,
                agent_runtime_id=agent_runtime_id,
                agent_session_id=session_id,
                thread_id=thread_id_seed,
                project_id=project_id,
                updated_at=now,
            )
        )
        await self._stores.agent_context_store.save_session_context(initial_session_state)

        token = str(ULID())
        state = self._ctx.state_store.load().model_copy(
            update={
                "focused_session_id": projected_session_id,
                "focused_thread_id": thread_id_seed,
                "new_conversation_token": token,
                "new_conversation_project_id": project_id,
                "new_conversation_agent_profile_id": worker_profile_id,
                "new_conversation_agent_runtime_id": agent_runtime_id,
                "new_conversation_agent_session_id": session_id,
                "new_conversation_thread_id": thread_id_seed,
                "selected_project_id": project_id,
                "selected_workspace_id": "",
                "updated_at": now,
            }
        )
        self._ctx.state_store.save(state)
        await self._sync_web_project_selector_state(
            project=project,
            source="session_create_with_project",
        )
        await self._stores.conn.commit()

        return self._completed_result(
            request=request,
            code="SESSION_CREATED_WITH_PROJECT",
            message=f"已创建对话「{project_name}」",
            data={
                "session_id": projected_session_id,
                "agent_session_id": session_id,
                "thread_id": thread_id_seed,
                "project_id": project_id,
                "new_conversation_token": token,
                "agent_profile_id": worker_profile_id,
            },
            resource_refs=[self._resource_ref("session_projection", "sessions:overview")],
            target_refs=[
                ControlPlaneTargetRef(target_type="session", target_id=projected_session_id),
            ],
        )

    async def _handle_session_reset(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        session = await self._resolve_session_projection_target(
            request,
            use_focused_when_empty=True,
        )
        now = datetime.now(tz=UTC)
        reset_context = False
        session_state = await self._stores.agent_context_store.get_session_context(session.session_id)
        if session_state is None and session.thread_id:
            session_states = await self._stores.agent_context_store.list_session_contexts(
                project_id=session.project_id or None,
            )
            session_state = next(
                (item for item in session_states if item.thread_id == session.thread_id),
                None,
            )
        if session_state is not None:
            await self._stores.agent_context_store.save_session_context(
                session_state.model_copy(
                    update={
                        "recent_turn_refs": [],
                        "recent_artifact_refs": [],
                        "rolling_summary": "",
                        "summary_artifact_id": "",
                        "last_context_frame_id": "",
                        "last_recall_frame_id": "",
                        "updated_at": now,
                    }
                )
            )
            reset_context = True

        related_sessions = await self._list_related_agent_sessions_for_projection(
            session_id=session.session_id,
            thread_id=session.thread_id,
            project_id=session.project_id,
            session_state=session_state,
        )
        reset_agent_sessions = 0
        for item in related_sessions:
            await self._stores.agent_context_store.delete_agent_session_turns(
                agent_session_id=item.agent_session_id
            )
            metadata = dict(item.metadata)
            metadata["recent_transcript"] = []
            metadata["rolling_summary"] = ""
            metadata["latest_model_reply_summary"] = ""
            metadata["latest_model_reply_preview"] = ""
            metadata["latest_compaction_summary"] = ""
            metadata["latest_compaction_summary_artifact_id"] = ""
            await self._stores.agent_context_store.save_agent_session(
                item.model_copy(
                    update={
                        "status": AgentSessionStatus.CLOSED,
                        "last_context_frame_id": "",
                        "last_recall_frame_id": "",
                        "recent_transcript": [],
                        "rolling_summary": "",
                        "metadata": metadata,
                        "updated_at": now,
                        "closed_at": now,
                    }
                )
            )
            reset_agent_sessions += 1

        token = str(ULID())
        current_state = self._ctx.state_store.load()
        state = current_state.model_copy(
            update={
                "focused_session_id": "",
                "focused_thread_id": "",
                "new_conversation_token": token,
                "new_conversation_project_id": session.project_id,
                "new_conversation_agent_profile_id": "",
                "selected_project_id": session.project_id or current_state.selected_project_id,
                "selected_workspace_id": "",
                "updated_at": now,
            }
        )
        self._ctx.state_store.save(state)
        if session.project_id:
            project = await self._stores.project_store.get_project(session.project_id)
            if project is not None:
                await self._sync_web_project_selector_state(
                    project=project,
                    source="session_reset",
                )
                await self._stores.conn.commit()

        return self._completed_result(
            request=request,
            code="SESSION_RESET",
            message="已清空该会话的 continuity，并准备新的对话起点",
            data={
                "session_id": session.session_id,
                "thread_id": session.thread_id,
                "task_id": session.task_id,
                "reset_session_context": reset_context,
                "reset_agent_session_count": reset_agent_sessions,
                "new_conversation_token": token,
                "project_id": session.project_id,
            },
            resource_refs=[self._resource_ref("session_projection", "sessions:overview")],
            target_refs=[
                ControlPlaneTargetRef(target_type="session", target_id=session.session_id),
                ControlPlaneTargetRef(target_type="thread", target_id=session.thread_id),
                ControlPlaneTargetRef(target_type="task", target_id=session.task_id),
            ],
        )

    async def _handle_session_delete(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        from octoagent.core.store.session_delete import delete_session_cascade

        session = await self._resolve_session_projection_target(request)
        if session is None:
            return self._rejected_result(
                request=request,
                code="SESSION_NOT_FOUND",
                message="找不到要删除的对话。",
            )

        session_state = await self._stores.agent_context_store.get_session_context(
            session.session_id
        )
        task_ids: list[str] = []
        if session_state is not None:
            task_ids = list(session_state.task_ids) if session_state.task_ids else []
        # 回退：session_state 可能不存在（旧会话或 DIRECT_WORKER 无 task），
        # 通过投影匹配收集关联 task（075-fix）。
        if not task_ids:
            session_tasks = await self._list_tasks_for_projected_session(
                session_id=session.session_id,
            )
            task_ids = [t.task_id for t in session_tasks]

        active_task_statuses = {"RUNNING", "WAITING_INPUT", "WAITING_APPROVAL"}
        active_tasks: list[tuple[str, str]] = []
        for tid in task_ids:
            task = await self._stores.task_store.get_task(tid)
            if task is not None and task.status.value in active_task_statuses:
                active_tasks.append((task.task_id, task.title or task.task_id))
        if active_tasks:
            task_info = "、".join(f"「{title}」" for _, title in active_tasks[:3])
            extra = f"等 {len(active_tasks)} 个" if len(active_tasks) > 3 else ""
            return self._rejected_result(
                request=request,
                code="SESSION_DELETE_BLOCKED",
                message=f"无法删除：{task_info}{extra}任务正在运行中。请先取消或等待任务完成后再删除。",
            )

        related_sessions = await self._list_related_agent_sessions_for_projection(
            session_id=session.session_id,
            thread_id=session.thread_id,
            project_id=session.project_id,
            session_state=session_state,
        )
        agent_session_ids = [s.agent_session_id for s in related_sessions]

        stats = await delete_session_cascade(
            stores=self._stores,
            session_id=session.session_id,
            task_ids=task_ids,
            agent_session_ids=agent_session_ids,
        )

        state = self._ctx.state_store.load()
        if state.focused_session_id == session.session_id:
            state = state.model_copy(
                update={
                    "focused_session_id": "",
                    "focused_thread_id": "",
                    "new_conversation_token": "",
                    "updated_at": datetime.now(tz=UTC),
                }
            )
            self._ctx.state_store.save(state)

        return self._completed_result(
            request=request,
            code="SESSION_DELETED",
            message=f"已删除对话及 {stats.get('tasks', 0)} 个任务的所有关联数据。",
            data={
                "session_id": session.session_id,
                "stats": stats,
            },
            resource_refs=[
                self._resource_ref("session_projection", "sessions:overview"),
            ],
        )

    async def _handle_session_export(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        thread_id = str(request.params.get("thread_id", "")).strip()
        session_id = str(request.params.get("session_id", "")).strip()
        task_id = str(request.params.get("task_id", "")).strip()
        since = request.params.get("since")
        until = request.params.get("until")
        task_ids: list[str] | None = None
        if session_id and not thread_id and not task_id:
            session = await self._resolve_session_projection_target(request)
            session_tasks = await self._list_tasks_for_projected_session(
                session_id=session.session_id,
            )
            task_ids = [item.task_id for item in session_tasks]
            if not task_ids and session.task_id:
                task_ids = [session.task_id]
        manifest = await BackupService(
            self._ctx.project_root,
            store_group=self._stores,
        ).export_chats(
            task_id=task_id or None,
            task_ids=task_ids,
            thread_id=thread_id or None,
            since=since,
            until=until,
        )
        return self._completed_result(
            request=request,
            code="SESSION_EXPORTED",
            message="已导出会话数据",
            data=manifest.model_dump(mode="json"),
            resource_refs=[self._resource_ref("session_projection", "sessions:overview")],
        )

    async def _handle_session_set_alias(
        self, request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        session = await self._resolve_session_projection_target(
            request,
            use_focused_when_empty=True,
        )
        session_state = await self._stores.agent_context_store.get_session_context(session.session_id)
        if session_state is None and session.thread_id:
            session_states = await self._stores.agent_context_store.list_session_contexts(
                project_id=session.project_id or None,
            )
            session_state = next(
                (item for item in session_states if item.thread_id == session.thread_id),
                None,
            )
        alias = self._param_str(request.params, "alias").strip()
        related_sessions = await self._list_related_agent_sessions_for_projection(
            session_id=session.session_id,
            thread_id=session.thread_id,
            project_id=session.project_id,
            session_state=session_state,
        )
        if not related_sessions:
            return self._rejected_result(
                request=request,
                code="SESSION_ALIAS_TARGET_NOT_FOUND",
                message="当前找不到可以改名的会话实体。",
            )
        now = datetime.now(tz=UTC)
        updated_count = 0
        for item in related_sessions:
            if item.alias == alias:
                continue
            await self._stores.agent_context_store.save_agent_session(
                item.model_copy(
                    update={
                        "alias": alias,
                        "updated_at": now,
                    }
                )
            )
            updated_count += 1
        await self._stores.conn.commit()
        message = "已恢复默认会话名称" if not alias else f"已将会话改名为「{alias}」"
        return self._completed_result(
            request=request,
            code="SESSION_ALIAS_UPDATED",
            message=message,
            data={
                "session_id": session.session_id,
                "thread_id": session.thread_id,
                "alias": alias,
                "updated_sessions": updated_count,
            },
            resource_refs=[self._resource_ref("session_projection", "sessions:overview")],
            target_refs=[
                ControlPlaneTargetRef(target_type="session", target_id=session.session_id),
            ],
        )

    async def _handle_session_interrupt(
        self, request: ActionRequestEnvelope
    ) -> ActionResultEnvelope:
        task_id = str(request.params.get("task_id", "")).strip()
        if not task_id:
            raise ControlPlaneActionError("TASK_ID_REQUIRED", "task_id 不能为空")
        existing = await self._stores.task_store.get_task(task_id)
        if existing is None:
            raise ControlPlaneActionError("TASK_NOT_FOUND", "任务不存在")
        task = None
        if self._ctx.task_runner is not None:
            cancelled = await self._ctx.task_runner.cancel_task(task_id)
            if not cancelled:
                raise ControlPlaneActionError("TASK_CANCEL_NOT_ALLOWED", "当前状态不允许取消")
            task = await self._stores.task_store.get_task(task_id)
        else:
            task = await TaskService(self._stores, self._ctx.sse_hub).cancel_task(task_id)
            if task is None:
                raise ControlPlaneActionError("TASK_NOT_FOUND", "任务不存在")
        return self._completed_result(
            request=request,
            code="TASK_CANCELLED",
            message="已取消任务",
            data={"task_id": task_id, "status": task.status.value},
            resource_refs=[self._resource_ref("session_projection", "sessions:overview")],
            target_refs=[ControlPlaneTargetRef(target_type="task", target_id=task_id)],
        )

    async def _handle_session_resume(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        task_id = str(request.params.get("task_id", "")).strip()
        if not task_id:
            raise ControlPlaneActionError("TASK_ID_REQUIRED", "task_id 不能为空")
        if self._ctx.task_runner is None:
            raise ControlPlaneActionError(
                "TASK_RUNNER_UNAVAILABLE", "当前 runtime 未启用 TaskRunner"
            )
        result = await self._ctx.task_runner.resume_task(task_id, trigger="manual")
        if not result.ok:
            raise ControlPlaneActionError("TASK_RESUME_FAILED", result.message)
        return self._completed_result(
            request=request,
            code="TASK_RESUMED",
            message=result.message,
            data=result.model_dump(mode="json"),
            resource_refs=[self._resource_ref("session_projection", "sessions:overview")],
            target_refs=[ControlPlaneTargetRef(target_type="task", target_id=task_id)],
        )
