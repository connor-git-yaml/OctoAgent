"""Control Plane 会话投影 + Context + Bootstrap 模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from ..operator_inbox import OperatorInboxItem, OperatorInboxSummary
from ._base import ControlPlaneCapability, ControlPlaneDocument, _utc_now


class SessionProjectionItem(BaseModel):
    session_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    task_id: str = Field(default="")
    parent_task_id: str = Field(default="")
    parent_work_id: str = Field(default="")
    title: str = Field(default="")
    alias: str = Field(default="")
    status: str = Field(default="")
    channel: str = Field(default="")
    requester_id: str = Field(default="")
    project_id: str = Field(default="")
    agent_profile_id: str = Field(default="")
    session_owner_profile_id: str = Field(default="")
    session_owner_name: str = Field(default="")
    turn_executor_kind: str = Field(default="")
    delegation_target_profile_id: str = Field(default="")
    runtime_kind: str = Field(default="")
    compatibility_flags: list[str] = Field(default_factory=list)
    compatibility_message: str = Field(default="")
    reset_recommended: bool = False
    lane: str = Field(default="queue")
    latest_message_summary: str = Field(default="")
    latest_event_at: datetime | None = None
    execution_summary: dict[str, Any] = Field(default_factory=dict)
    capabilities: list[ControlPlaneCapability] = Field(default_factory=list)
    detail_refs: dict[str, str] = Field(default_factory=dict)


class SessionProjectionSummary(BaseModel):
    total_sessions: int = 0
    running_sessions: int = 0
    queued_sessions: int = 0
    history_sessions: int = 0
    focused_sessions: int = 0


class SessionProjectionDocument(ControlPlaneDocument):
    resource_type: str = "session_projection"
    resource_id: str = "sessions:overview"
    focused_session_id: str = Field(default="")
    focused_thread_id: str = Field(default="")
    new_conversation_token: str = Field(default="")
    new_conversation_project_id: str = Field(default="")
    new_conversation_agent_profile_id: str = Field(default="")
    sessions: list[SessionProjectionItem] = Field(default_factory=list)
    summary: SessionProjectionSummary = Field(default_factory=SessionProjectionSummary)
    operator_summary: OperatorInboxSummary | None = None
    operator_items: list[OperatorInboxItem] = Field(default_factory=list)


class ContextSessionItem(BaseModel):
    session_id: str = Field(min_length=1)
    agent_runtime_id: str = Field(default="")
    agent_session_id: str = Field(default="")
    thread_id: str = Field(default="")
    project_id: str = Field(default="")
    rolling_summary: str = Field(default="")
    last_context_frame_id: str = Field(default="")
    last_recall_frame_id: str = Field(default="")
    updated_at: datetime | None = None


class ContextFrameItem(BaseModel):
    context_frame_id: str = Field(min_length=1)
    task_id: str = Field(default="")
    session_id: str = Field(default="")
    agent_runtime_id: str = Field(default="")
    agent_session_id: str = Field(default="")
    project_id: str = Field(default="")
    agent_profile_id: str = Field(default="")
    recall_frame_id: str = Field(default="")
    memory_namespace_ids: list[str] = Field(default_factory=list)
    recent_summary: str = Field(default="")
    memory_hit_count: int = Field(default=0, ge=0)
    memory_hits: list[dict[str, Any]] = Field(default_factory=list)
    memory_recall: dict[str, Any] = Field(default_factory=dict)
    budget: dict[str, Any] = Field(default_factory=dict)
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    degraded_reason: str = Field(default="")
    created_at: datetime | None = None


class AgentRuntimeItem(BaseModel):
    agent_runtime_id: str = Field(min_length=1)
    role: str = Field(default="")
    project_id: str = Field(default="")
    agent_profile_id: str = Field(default="")
    worker_profile_id: str = Field(default="")
    name: str = Field(default="")
    persona_summary: str = Field(default="")
    status: str = Field(default="active")
    metadata: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime | None = None


class AgentSessionContinuityItem(BaseModel):
    agent_session_id: str = Field(min_length=1)
    agent_runtime_id: str = Field(default="")
    kind: str = Field(default="")
    status: str = Field(default="active")
    project_id: str = Field(default="")
    thread_id: str = Field(default="")
    legacy_session_id: str = Field(default="")
    work_id: str = Field(default="")
    last_context_frame_id: str = Field(default="")
    last_recall_frame_id: str = Field(default="")
    updated_at: datetime | None = None


class MemoryNamespaceItem(BaseModel):
    namespace_id: str = Field(min_length=1)
    kind: str = Field(default="")
    project_id: str = Field(default="")
    agent_runtime_id: str = Field(default="")
    name: str = Field(default="")
    description: str = Field(default="")
    memory_scope_ids: list[str] = Field(default_factory=list)
    updated_at: datetime | None = None


class RecallFrameItem(BaseModel):
    recall_frame_id: str = Field(min_length=1)
    agent_runtime_id: str = Field(default="")
    agent_session_id: str = Field(default="")
    context_frame_id: str = Field(default="")
    task_id: str = Field(default="")
    project_id: str = Field(default="")
    query: str = Field(default="")
    recent_summary: str = Field(default="")
    memory_namespace_ids: list[str] = Field(default_factory=list)
    memory_hit_count: int = Field(default=0, ge=0)
    degraded_reason: str = Field(default="")
    created_at: datetime | None = None


class A2AConversationItem(BaseModel):
    a2a_conversation_id: str = Field(min_length=1)
    task_id: str = Field(default="")
    work_id: str = Field(default="")
    project_id: str = Field(default="")
    source_agent_runtime_id: str = Field(default="")
    source_agent_session_id: str = Field(default="")
    target_agent_runtime_id: str = Field(default="")
    target_agent_session_id: str = Field(default="")
    source_agent: str = Field(default="")
    target_agent: str = Field(default="")
    context_frame_id: str = Field(default="")
    request_message_id: str = Field(default="")
    latest_message_id: str = Field(default="")
    latest_message_type: str = Field(default="")
    status: str = Field(default="")
    message_count: int = Field(default=0, ge=0)
    trace_id: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime | None = None


class A2AMessageItem(BaseModel):
    a2a_message_id: str = Field(min_length=1)
    a2a_conversation_id: str = Field(min_length=1)
    message_seq: int = Field(default=1, ge=1)
    task_id: str = Field(default="")
    work_id: str = Field(default="")
    message_type: str = Field(default="")
    direction: str = Field(default="")
    protocol_message_id: str = Field(default="")
    source_agent_runtime_id: str = Field(default="")
    source_agent_session_id: str = Field(default="")
    target_agent_runtime_id: str = Field(default="")
    target_agent_session_id: str = Field(default="")
    from_agent: str = Field(default="")
    to_agent: str = Field(default="")
    idempotency_key: str = Field(default="")
    payload: dict[str, Any] = Field(default_factory=dict)
    trace: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None


class ContextContinuityDocument(ControlPlaneDocument):
    resource_type: str = "context_continuity"
    resource_id: str = "context:overview"
    active_project_id: str = Field(default="")
    sessions: list[ContextSessionItem] = Field(default_factory=list)
    frames: list[ContextFrameItem] = Field(default_factory=list)
    agent_runtimes: list[AgentRuntimeItem] = Field(default_factory=list)
    agent_sessions: list[AgentSessionContinuityItem] = Field(default_factory=list)
    memory_namespaces: list[MemoryNamespaceItem] = Field(default_factory=list)
    recall_frames: list[RecallFrameItem] = Field(default_factory=list)
    a2a_conversations: list[A2AConversationItem] = Field(default_factory=list)
    a2a_messages: list[A2AMessageItem] = Field(default_factory=list)


class OwnerProfileDocument(ControlPlaneDocument):
    resource_type: str = "owner_profile"
    resource_id: str = "owner-profile:default"
    active_project_id: str = Field(default="")
    profile: dict[str, Any] = Field(default_factory=dict)
    overlays: list[dict[str, Any]] = Field(default_factory=list)


class BootstrapSessionDocument(ControlPlaneDocument):
    resource_type: str = "bootstrap_session"
    resource_id: str = "bootstrap:current"
    active_project_id: str = Field(default="")
    session: dict[str, Any] = Field(default_factory=dict)
    resumable: bool = False
