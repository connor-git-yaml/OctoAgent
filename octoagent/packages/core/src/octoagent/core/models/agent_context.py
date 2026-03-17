"""Feature 033: Agent Profile / Bootstrap / Context Continuity 领域模型。"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


class AgentProfileScope(StrEnum):
    SYSTEM = "system"
    PROJECT = "project"


class WorkerProfileStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class WorkerProfileOriginKind(StrEnum):
    BUILTIN = "builtin"
    CUSTOM = "custom"
    CLONED = "cloned"
    EXTRACTED = "extracted"


class OwnerOverlayScope(StrEnum):
    PROJECT = "project"
    WORKSPACE = "workspace"


class BootstrapSessionStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"


class ContextRequestKind(StrEnum):
    CHAT = "chat"
    AUTOMATION = "automation"
    WORK = "work"
    PIPELINE = "pipeline"
    WORKER = "worker"
    BOOTSTRAP = "bootstrap"


class AgentRuntimeRole(StrEnum):
    BUTLER = "butler"
    WORKER = "worker"


class AgentRuntimeStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class AgentSessionKind(StrEnum):
    BUTLER_MAIN = "butler_main"
    WORKER_INTERNAL = "worker_internal"
    DIRECT_WORKER = "direct_worker"
    SUBAGENT_INTERNAL = "subagent_internal"


class AgentSessionStatus(StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"


class AgentSessionTurnKind(StrEnum):
    USER_MESSAGE = "user_message"
    ASSISTANT_MESSAGE = "assistant_message"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    CONTEXT_SUMMARY = "context_summary"


class MemoryNamespaceKind(StrEnum):
    PROJECT_SHARED = "project_shared"
    BUTLER_PRIVATE = "butler_private"
    WORKER_PRIVATE = "worker_private"


class AgentProfile(BaseModel):
    """主 Agent / automation / delegation 可消费的正式 profile。"""

    profile_id: str = Field(min_length=1)
    scope: AgentProfileScope = AgentProfileScope.SYSTEM
    project_id: str = Field(default="")
    name: str = Field(min_length=1)
    persona_summary: str = Field(default="")
    instruction_overlays: list[str] = Field(default_factory=list)
    model_alias: str = Field(default="main")
    tool_profile: str = Field(default="standard")
    policy_refs: list[str] = Field(default_factory=list)
    memory_access_policy: dict[str, Any] = Field(default_factory=dict)
    context_budget_policy: dict[str, Any] = Field(default_factory=dict)
    bootstrap_template_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)


class WorkerProfile(BaseModel):
    """Root Agent 的正式静态配置对象。"""

    profile_id: str = Field(min_length=1)
    scope: AgentProfileScope = AgentProfileScope.PROJECT
    project_id: str = Field(default="")
    name: str = Field(min_length=1)
    summary: str = Field(default="")
    base_archetype: str = Field(default="general")
    instruction_overlays: list[str] = Field(default_factory=list)
    model_alias: str = Field(default="main")
    tool_profile: str = Field(default="minimal")
    default_tool_groups: list[str] = Field(default_factory=list)
    selected_tools: list[str] = Field(default_factory=list)
    runtime_kinds: list[str] = Field(default_factory=list)
    policy_refs: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    status: WorkerProfileStatus = WorkerProfileStatus.DRAFT
    origin_kind: WorkerProfileOriginKind = WorkerProfileOriginKind.CUSTOM
    draft_revision: int = Field(default=0, ge=0)
    active_revision: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    archived_at: datetime | None = None


class WorkerProfileRevision(BaseModel):
    """Root Agent 已发布 revision。"""

    revision_id: str = Field(min_length=1)
    profile_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    change_summary: str = Field(default="")
    snapshot_payload: dict[str, Any] = Field(default_factory=dict)
    created_by: str = Field(default="")
    created_at: datetime = Field(default_factory=_utc_now)


class OwnerProfile(BaseModel):
    """Owner 全局基础身份与协作偏好。"""

    owner_profile_id: str = Field(min_length=1)
    display_name: str = Field(default="Owner")
    preferred_address: str = Field(default="你")
    timezone: str = Field(default="UTC")
    locale: str = Field(default="zh-CN")
    working_style: str = Field(default="")
    interaction_preferences: list[str] = Field(default_factory=list)
    boundary_notes: list[str] = Field(default_factory=list)
    main_session_only_fields: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)


class OwnerProfileOverlay(BaseModel):
    """Project / workspace 作用域的 owner 覆盖层。"""

    owner_overlay_id: str = Field(min_length=1)
    owner_profile_id: str = Field(min_length=1)
    scope: OwnerOverlayScope = OwnerOverlayScope.PROJECT
    project_id: str = Field(default="")
    workspace_id: str = Field(default="")
    assistant_identity_overrides: dict[str, Any] = Field(default_factory=dict)
    working_style_override: str = Field(default="")
    interaction_preferences_override: list[str] = Field(default_factory=list)
    boundary_notes_override: list[str] = Field(default_factory=list)
    bootstrap_template_ids: list[str] = Field(default_factory=list)
    main_session_only_overrides: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)


class BootstrapSession(BaseModel):
    """首启或 project-init bootstrap 状态。"""

    bootstrap_id: str = Field(min_length=1)
    project_id: str = Field(default="")
    workspace_id: str = Field(default="")
    owner_profile_id: str = Field(default="")
    owner_overlay_id: str = Field(default="")
    agent_profile_id: str = Field(default="")
    status: BootstrapSessionStatus = BootstrapSessionStatus.PENDING
    current_step: str = Field(default="owner_basics")
    steps: list[str] = Field(default_factory=list)
    answers: dict[str, Any] = Field(default_factory=dict)
    generated_profile_ids: list[str] = Field(default_factory=list)
    generated_owner_revision: int = Field(default=0, ge=0)
    blocking_reason: str = Field(default="")
    surface: str = Field(default="chat")
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    completed_at: datetime | None = None


class AgentRuntime(BaseModel):
    """Butler / Worker 的长期运行体。"""

    agent_runtime_id: str = Field(min_length=1)
    project_id: str = Field(default="")
    workspace_id: str = Field(default="")
    agent_profile_id: str = Field(default="")
    worker_profile_id: str = Field(default="")
    role: AgentRuntimeRole = AgentRuntimeRole.BUTLER
    name: str = Field(default="")
    persona_summary: str = Field(default="")
    status: AgentRuntimeStatus = AgentRuntimeStatus.ACTIVE
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    archived_at: datetime | None = None


class AgentSession(BaseModel):
    """绑定到 AgentRuntime 的正式会话对象。"""

    agent_session_id: str = Field(min_length=1)
    agent_runtime_id: str = Field(min_length=1)
    kind: AgentSessionKind = AgentSessionKind.BUTLER_MAIN
    status: AgentSessionStatus = AgentSessionStatus.ACTIVE
    project_id: str = Field(default="")
    workspace_id: str = Field(default="")
    surface: str = Field(default="chat")
    thread_id: str = Field(default="")
    legacy_session_id: str = Field(default="")
    parent_agent_session_id: str = Field(default="")
    parent_worker_runtime_id: str = Field(default="")
    """Subagent 所属 Worker 的 AgentRuntime ID（仅 SUBAGENT_INTERNAL 类型使用）。"""
    work_id: str = Field(default="")
    a2a_conversation_id: str = Field(default="")
    last_context_frame_id: str = Field(default="")
    last_recall_frame_id: str = Field(default="")
    recent_transcript: list[dict[str, str]] = Field(default_factory=list)
    rolling_summary: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    closed_at: datetime | None = None


class AgentSessionTurn(BaseModel):
    """AgentSession 的正式 turn / tool-turn 持久化记录。"""

    agent_session_turn_id: str = Field(min_length=1)
    agent_session_id: str = Field(min_length=1)
    task_id: str = Field(default="")
    turn_seq: int = Field(default=0, ge=0)
    kind: AgentSessionTurnKind = AgentSessionTurnKind.USER_MESSAGE
    role: str = Field(default="")
    tool_name: str = Field(default="")
    artifact_ref: str = Field(default="")
    summary: str = Field(default="")
    dedupe_key: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)


class MemoryNamespace(BaseModel):
    """Project shared / Butler private / Worker private 记忆命名空间。"""

    namespace_id: str = Field(min_length=1)
    project_id: str = Field(default="")
    workspace_id: str = Field(default="")
    agent_runtime_id: str = Field(default="")
    kind: MemoryNamespaceKind = MemoryNamespaceKind.PROJECT_SHARED
    name: str = Field(default="")
    description: str = Field(default="")
    memory_scope_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    archived_at: datetime | None = None


class SessionContextState(BaseModel):
    """短期上下文 durable state。"""

    session_id: str = Field(min_length=1)
    agent_runtime_id: str = Field(default="")
    agent_session_id: str = Field(default="")
    thread_id: str = Field(default="")
    project_id: str = Field(default="")
    workspace_id: str = Field(default="")
    task_ids: list[str] = Field(default_factory=list)
    recent_turn_refs: list[str] = Field(default_factory=list)
    recent_artifact_refs: list[str] = Field(default_factory=list)
    rolling_summary: str = Field(default="")
    summary_artifact_id: str = Field(default="")
    last_context_frame_id: str = Field(default="")
    last_recall_frame_id: str = Field(default="")
    updated_at: datetime = Field(default_factory=_utc_now)


class ContextSourceRef(BaseModel):
    """上下文来源引用。"""

    ref_type: str = Field(min_length=1)
    ref_id: str = Field(min_length=1)
    label: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContextResolveRequest(BaseModel):
    """统一 resolver 输入。"""

    request_id: str = Field(min_length=1)
    request_kind: ContextRequestKind
    surface: str = Field(default="chat")
    project_id: str = Field(default="")
    workspace_id: str | None = None
    task_id: str | None = None
    session_id: str | None = None
    work_id: str | None = None
    pipeline_run_id: str | None = None
    automation_run_id: str | None = None
    worker_run_id: str | None = None
    agent_runtime_id: str | None = None
    agent_session_id: str | None = None
    agent_profile_id: str | None = None
    owner_overlay_id: str | None = None
    trigger_text: str | None = None
    thread_id: str | None = None
    requester_id: str | None = None
    requester_role: str = Field(default="owner")
    input_artifact_refs: list[str] = Field(default_factory=list)
    delegation_metadata: dict[str, Any] = Field(default_factory=dict)
    runtime_metadata: dict[str, Any] = Field(default_factory=dict)


class ContextFrame(BaseModel):
    """一次真实运行所消费的上下文快照。"""

    context_frame_id: str = Field(min_length=1)
    task_id: str = Field(default="")
    session_id: str = Field(default="")
    agent_runtime_id: str = Field(default="")
    agent_session_id: str = Field(default="")
    project_id: str = Field(default="")
    workspace_id: str = Field(default="")
    agent_profile_id: str = Field(default="")
    owner_profile_id: str = Field(default="")
    owner_overlay_id: str = Field(default="")
    owner_profile_revision: int | None = None
    bootstrap_session_id: str | None = None
    recall_frame_id: str | None = None
    system_blocks: list[dict[str, Any]] = Field(default_factory=list)
    recent_summary: str = Field(default="")
    memory_namespace_ids: list[str] = Field(default_factory=list)
    memory_hits: list[dict[str, Any]] = Field(default_factory=list)
    delegation_context: dict[str, Any] = Field(default_factory=dict)
    budget: dict[str, Any] = Field(default_factory=dict)
    degraded_reason: str = Field(default="")
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utc_now)


class RecallFrame(BaseModel):
    """一次 Agent 侧召回的 durable 快照。"""

    recall_frame_id: str = Field(min_length=1)
    agent_runtime_id: str = Field(default="")
    agent_session_id: str = Field(default="")
    context_frame_id: str = Field(default="")
    task_id: str = Field(default="")
    project_id: str = Field(default="")
    workspace_id: str = Field(default="")
    query: str = Field(default="")
    recent_summary: str = Field(default="")
    memory_namespace_ids: list[str] = Field(default_factory=list)
    memory_hits: list[dict[str, Any]] = Field(default_factory=list)
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    budget: dict[str, Any] = Field(default_factory=dict)
    degraded_reason: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)


class ContextResolveResult(BaseModel):
    """统一 resolver 输出。"""

    context_frame_id: str = Field(min_length=1)
    effective_agent_profile_id: str = Field(min_length=1)
    effective_agent_runtime_id: str = Field(default="")
    effective_agent_session_id: str = Field(default="")
    effective_owner_overlay_id: str | None = None
    owner_profile_revision: int | None = None
    bootstrap_session_id: str | None = None
    recall_frame_id: str | None = None
    system_blocks: list[dict[str, Any]] = Field(default_factory=list)
    recent_summary: str = Field(default="")
    memory_hits: list[dict[str, Any]] = Field(default_factory=list)
    degraded_reason: str = Field(default="")
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
