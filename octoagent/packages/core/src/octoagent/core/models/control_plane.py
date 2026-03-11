"""Feature 026: Control Plane canonical models。"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .capability import BundledCapabilityPack
from .operator_inbox import OperatorInboxItem, OperatorInboxSummary
from .pipeline import PipelineReplayFrame


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


class ControlPlaneSurface(StrEnum):
    WEB = "web"
    TELEGRAM = "telegram"
    CLI = "cli"
    SYSTEM = "system"


class ControlPlaneSupportStatus(StrEnum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    HIDDEN = "hidden"
    DEGRADED = "degraded"


class ControlPlaneActionStatus(StrEnum):
    COMPLETED = "completed"
    REJECTED = "rejected"
    DEFERRED = "deferred"


class ControlPlaneEventType(StrEnum):
    RESOURCE_PROJECTED = "control.resource.projected"
    RESOURCE_REMOVED = "control.resource.removed"
    ACTION_REQUESTED = "control.action.requested"
    ACTION_COMPLETED = "control.action.completed"
    ACTION_REJECTED = "control.action.rejected"
    ACTION_DEFERRED = "control.action.deferred"


class ControlPlaneActor(BaseModel):
    actor_id: str = Field(min_length=1)
    actor_label: str = Field(default="")


class ControlPlaneResourceRef(BaseModel):
    resource_type: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    schema_version: int = Field(default=1, ge=1)


class ControlPlaneTargetRef(BaseModel):
    target_type: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    label: str = Field(default="")


class ControlPlaneDegradedState(BaseModel):
    is_degraded: bool = False
    reasons: list[str] = Field(default_factory=list)
    unavailable_sections: list[str] = Field(default_factory=list)


class ControlPlaneCapability(BaseModel):
    capability_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    action_id: str = Field(default="")
    enabled: bool = True
    support_status: ControlPlaneSupportStatus = ControlPlaneSupportStatus.SUPPORTED
    reason: str = Field(default="")


class ControlPlaneDocument(BaseModel):
    contract_version: str = Field(default="1.0.0")
    resource_type: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    schema_version: int = Field(default=1, ge=1)
    generated_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    status: str = Field(default="ready")
    degraded: ControlPlaneDegradedState = Field(default_factory=ControlPlaneDegradedState)
    warnings: list[str] = Field(default_factory=list)
    capabilities: list[ControlPlaneCapability] = Field(default_factory=list)
    refs: dict[str, str] = Field(default_factory=dict)


class WizardStepDocument(BaseModel):
    step_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    status: str = Field(min_length=1)
    summary: str = Field(default="")
    actions: list[dict[str, Any]] = Field(default_factory=list)
    detail_ref: str | None = None


class WizardSessionDocument(ControlPlaneDocument):
    resource_type: str = "wizard_session"
    resource_id: str = "wizard:default"
    session_version: int = 1
    current_step: str = Field(default="")
    resumable: bool = True
    blocking_reason: str = Field(default="")
    steps: list[WizardStepDocument] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    next_actions: list[dict[str, Any]] = Field(default_factory=list)


class ConfigFieldHint(BaseModel):
    field_path: str = Field(min_length=1)
    section: str = Field(default="general")
    label: str = Field(default="")
    description: str = Field(default="")
    widget: str = Field(default="text")
    placeholder: str = Field(default="")
    help_text: str = Field(default="")
    sensitive: bool = False
    multiline: bool = False
    order: int = 100


class ConfigSchemaDocument(ControlPlaneDocument):
    model_config = ConfigDict(populate_by_name=True)

    resource_type: str = "config_schema"
    resource_id: str = "config:octoagent"
    schema_payload: dict[str, Any] = Field(
        default_factory=dict,
        alias="schema",
        serialization_alias="schema",
    )
    ui_hints: dict[str, ConfigFieldHint] = Field(default_factory=dict)
    current_value: dict[str, Any] = Field(default_factory=dict)
    validation_rules: list[str] = Field(default_factory=list)
    bridge_refs: list[dict[str, Any]] = Field(default_factory=list)
    secret_refs_only: bool = True


class ProjectOption(BaseModel):
    project_id: str = Field(min_length=1)
    slug: str = Field(min_length=1)
    name: str = Field(min_length=1)
    is_default: bool = False
    status: str = Field(default="active")
    workspace_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class WorkspaceOption(BaseModel):
    workspace_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    slug: str = Field(min_length=1)
    name: str = Field(min_length=1)
    kind: str = Field(default="primary")
    root_path: str = Field(default="")


class ProjectSelectorDocument(ControlPlaneDocument):
    resource_type: str = "project_selector"
    resource_id: str = "project:selector"
    current_project_id: str = Field(default="")
    current_workspace_id: str = Field(default="")
    default_project_id: str = Field(default="")
    fallback_reason: str = Field(default="")
    switch_allowed: bool = False
    available_projects: list[ProjectOption] = Field(default_factory=list)
    available_workspaces: list[WorkspaceOption] = Field(default_factory=list)


class SessionProjectionItem(BaseModel):
    session_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    task_id: str = Field(default="")
    parent_task_id: str = Field(default="")
    parent_work_id: str = Field(default="")
    title: str = Field(default="")
    status: str = Field(default="")
    channel: str = Field(default="")
    requester_id: str = Field(default="")
    project_id: str = Field(default="")
    workspace_id: str = Field(default="")
    runtime_kind: str = Field(default="")
    latest_message_summary: str = Field(default="")
    latest_event_at: datetime | None = None
    execution_summary: dict[str, Any] = Field(default_factory=dict)
    capabilities: list[ControlPlaneCapability] = Field(default_factory=list)
    detail_refs: dict[str, str] = Field(default_factory=dict)


class SessionProjectionDocument(ControlPlaneDocument):
    resource_type: str = "session_projection"
    resource_id: str = "sessions:overview"
    focused_session_id: str = Field(default="")
    focused_thread_id: str = Field(default="")
    sessions: list[SessionProjectionItem] = Field(default_factory=list)
    operator_summary: OperatorInboxSummary | None = None
    operator_items: list[OperatorInboxItem] = Field(default_factory=list)


class AgentProfileItem(BaseModel):
    profile_id: str = Field(min_length=1)
    scope: str = Field(default="system")
    project_id: str = Field(default="")
    name: str = Field(min_length=1)
    persona_summary: str = Field(default="")
    model_alias: str = Field(default="main")
    tool_profile: str = Field(default="standard")
    memory_access_policy: dict[str, Any] = Field(default_factory=dict)
    context_budget_policy: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime | None = None


class AgentProfilesDocument(ControlPlaneDocument):
    resource_type: str = "agent_profiles"
    resource_id: str = "agent-profiles:overview"
    active_project_id: str = Field(default="")
    active_workspace_id: str = Field(default="")
    profiles: list[AgentProfileItem] = Field(default_factory=list)


class OwnerProfileDocument(ControlPlaneDocument):
    resource_type: str = "owner_profile"
    resource_id: str = "owner-profile:default"
    active_project_id: str = Field(default="")
    active_workspace_id: str = Field(default="")
    profile: dict[str, Any] = Field(default_factory=dict)
    overlays: list[dict[str, Any]] = Field(default_factory=list)


class BootstrapSessionDocument(ControlPlaneDocument):
    resource_type: str = "bootstrap_session"
    resource_id: str = "bootstrap:current"
    active_project_id: str = Field(default="")
    active_workspace_id: str = Field(default="")
    session: dict[str, Any] = Field(default_factory=dict)
    resumable: bool = False


class ContextSessionItem(BaseModel):
    session_id: str = Field(min_length=1)
    thread_id: str = Field(default="")
    project_id: str = Field(default="")
    workspace_id: str = Field(default="")
    rolling_summary: str = Field(default="")
    last_context_frame_id: str = Field(default="")
    updated_at: datetime | None = None


class ContextFrameItem(BaseModel):
    context_frame_id: str = Field(min_length=1)
    task_id: str = Field(default="")
    session_id: str = Field(default="")
    project_id: str = Field(default="")
    workspace_id: str = Field(default="")
    agent_profile_id: str = Field(default="")
    recent_summary: str = Field(default="")
    memory_hit_count: int = Field(default=0, ge=0)
    memory_hits: list[dict[str, Any]] = Field(default_factory=list)
    memory_recall: dict[str, Any] = Field(default_factory=dict)
    budget: dict[str, Any] = Field(default_factory=dict)
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    degraded_reason: str = Field(default="")
    created_at: datetime | None = None


class ContextContinuityDocument(ControlPlaneDocument):
    resource_type: str = "context_continuity"
    resource_id: str = "context:overview"
    active_project_id: str = Field(default="")
    active_workspace_id: str = Field(default="")
    sessions: list[ContextSessionItem] = Field(default_factory=list)
    frames: list[ContextFrameItem] = Field(default_factory=list)


class SetupRiskItem(BaseModel):
    risk_id: str = Field(min_length=1)
    severity: str = Field(default="info")
    title: str = Field(min_length=1)
    summary: str = Field(default="")
    blocking: bool = False
    recommended_action: str = Field(default="")
    source_ref: ControlPlaneResourceRef | None = None


class SetupGovernanceSection(BaseModel):
    section_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    status: str = Field(default="ready")
    summary: str = Field(default="")
    warnings: list[str] = Field(default_factory=list)
    blocking_reasons: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)
    source_refs: list[ControlPlaneResourceRef] = Field(default_factory=list)


class SetupReviewSummary(BaseModel):
    ready: bool = False
    risk_level: str = Field(default="info")
    warnings: list[str] = Field(default_factory=list)
    blocking_reasons: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    provider_runtime_risks: list[SetupRiskItem] = Field(default_factory=list)
    channel_exposure_risks: list[SetupRiskItem] = Field(default_factory=list)
    agent_autonomy_risks: list[SetupRiskItem] = Field(default_factory=list)
    tool_skill_readiness_risks: list[SetupRiskItem] = Field(default_factory=list)
    secret_binding_risks: list[SetupRiskItem] = Field(default_factory=list)


class PolicyProfileItem(BaseModel):
    profile_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    description: str = Field(default="")
    allowed_tool_profile: str = Field(default="standard")
    approval_policy: str = Field(default="")
    risk_level: str = Field(default="info")
    recommended_for: list[str] = Field(default_factory=list)
    is_active: bool = False


class PolicyProfilesDocument(ControlPlaneDocument):
    resource_type: str = "policy_profiles"
    resource_id: str = "policy:profiles"
    active_project_id: str = Field(default="")
    active_workspace_id: str = Field(default="")
    active_profile_id: str = Field(default="")
    profiles: list[PolicyProfileItem] = Field(default_factory=list)


class SkillGovernanceItem(BaseModel):
    item_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    source_kind: str = Field(default="builtin")
    scope: str = Field(default="project")
    enabled_by_default: bool = False
    availability: str = Field(default="available")
    trust_level: str = Field(default="trusted")
    blocking: bool = False
    required_secrets: list[str] = Field(default_factory=list)
    missing_requirements: list[str] = Field(default_factory=list)
    install_hint: str = Field(default="")
    details: dict[str, Any] = Field(default_factory=dict)


class SkillGovernanceDocument(ControlPlaneDocument):
    resource_type: str = "skill_governance"
    resource_id: str = "skills:governance"
    active_project_id: str = Field(default="")
    active_workspace_id: str = Field(default="")
    items: list[SkillGovernanceItem] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)


class SetupGovernanceDocument(ControlPlaneDocument):
    resource_type: str = "setup_governance"
    resource_id: str = "setup:governance"
    active_project_id: str = Field(default="")
    active_workspace_id: str = Field(default="")
    project_scope: SetupGovernanceSection = Field(
        default_factory=lambda: SetupGovernanceSection(
            section_id="project_scope",
            label="Project Scope",
        )
    )
    provider_runtime: SetupGovernanceSection = Field(
        default_factory=lambda: SetupGovernanceSection(
            section_id="provider_runtime",
            label="Provider Runtime",
        )
    )
    channel_access: SetupGovernanceSection = Field(
        default_factory=lambda: SetupGovernanceSection(
            section_id="channel_access",
            label="Channel Access",
        )
    )
    agent_governance: SetupGovernanceSection = Field(
        default_factory=lambda: SetupGovernanceSection(
            section_id="agent_governance",
            label="Agent Governance",
        )
    )
    tools_skills: SetupGovernanceSection = Field(
        default_factory=lambda: SetupGovernanceSection(
            section_id="tools_skills",
            label="Tools & Skills",
        )
    )
    review: SetupReviewSummary = Field(default_factory=SetupReviewSummary)


class CapabilityPackDocument(ControlPlaneDocument):
    resource_type: str = "capability_pack"
    resource_id: str = "capability:bundled"
    pack: BundledCapabilityPack = Field(default_factory=BundledCapabilityPack)
    selected_project_id: str = Field(default="")
    selected_workspace_id: str = Field(default="")


class WorkProjectionItem(BaseModel):
    work_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    parent_work_id: str = Field(default="")
    title: str = Field(default="")
    status: str = Field(default="")
    target_kind: str = Field(default="")
    selected_worker_type: str = Field(default="")
    route_reason: str = Field(default="")
    owner_id: str = Field(default="")
    selected_tools: list[str] = Field(default_factory=list)
    pipeline_run_id: str = Field(default="")
    runtime_id: str = Field(default="")
    project_id: str = Field(default="")
    workspace_id: str = Field(default="")
    child_work_ids: list[str] = Field(default_factory=list)
    child_work_count: int = Field(default=0, ge=0)
    merge_ready: bool = False
    runtime_summary: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime | None = None
    capabilities: list[ControlPlaneCapability] = Field(default_factory=list)


class DelegationPlaneDocument(ControlPlaneDocument):
    resource_type: str = "delegation_plane"
    resource_id: str = "delegation:overview"
    works: list[WorkProjectionItem] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)


class PipelineRunItem(BaseModel):
    run_id: str = Field(min_length=1)
    pipeline_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    work_id: str = Field(min_length=1)
    status: str = Field(default="")
    current_node_id: str = Field(default="")
    pause_reason: str = Field(default="")
    retry_cursor: dict[str, int] = Field(default_factory=dict)
    updated_at: datetime | None = None
    replay_frames: list[PipelineReplayFrame] = Field(default_factory=list)


class SkillPipelineDocument(ControlPlaneDocument):
    resource_type: str = "skill_pipeline"
    resource_id: str = "pipeline:overview"
    runs: list[PipelineRunItem] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)


class AutomationScheduleKind(StrEnum):
    INTERVAL = "interval"
    CRON = "cron"
    ONCE = "once"


class AutomationJobStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    RUNNING = "running"
    FAILED = "failed"
    DEGRADED = "degraded"


class AutomationJob(BaseModel):
    job_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    action_id: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)
    project_id: str = Field(default="")
    workspace_id: str = Field(default="")
    agent_profile_id: str = Field(default="")
    context_frame_id: str = Field(default="")
    schedule_kind: AutomationScheduleKind = AutomationScheduleKind.INTERVAL
    schedule_expr: str = Field(min_length=1)
    timezone: str = Field(default="UTC")
    enabled: bool = True
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)


class AutomationJobRun(BaseModel):
    run_id: str = Field(min_length=1)
    job_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
    status: str = Field(min_length=1)
    started_at: datetime = Field(default_factory=_utc_now)
    completed_at: datetime | None = None
    summary: str = Field(default="")
    result_code: str = Field(default="")
    resource_refs: list[ControlPlaneResourceRef] = Field(default_factory=list)


class AutomationJobItem(BaseModel):
    job: AutomationJob
    status: AutomationJobStatus = AutomationJobStatus.ACTIVE
    next_run_at: datetime | None = None
    last_run: AutomationJobRun | None = None
    supported_actions: list[str] = Field(default_factory=list)
    degraded_reason: str = Field(default="")


class AutomationJobDocument(ControlPlaneDocument):
    resource_type: str = "automation_job"
    resource_id: str = "automation:jobs"
    jobs: list[AutomationJobItem] = Field(default_factory=list)
    run_history_cursor: str = Field(default="")


class DiagnosticsSubsystemStatus(BaseModel):
    subsystem_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    status: str = Field(min_length=1)
    summary: str = Field(default="")
    detail_ref: str = Field(default="")
    warnings: list[str] = Field(default_factory=list)


class DiagnosticsFailureSummary(BaseModel):
    source: str = Field(min_length=1)
    message: str = Field(min_length=1)
    occurred_at: datetime | None = None


class DiagnosticsSummaryDocument(ControlPlaneDocument):
    resource_type: str = "diagnostics_summary"
    resource_id: str = "diagnostics:runtime"
    overall_status: str = Field(default="ready")
    subsystems: list[DiagnosticsSubsystemStatus] = Field(default_factory=list)
    recent_failures: list[DiagnosticsFailureSummary] = Field(default_factory=list)
    runtime_snapshot: dict[str, Any] = Field(default_factory=dict)
    recovery_summary: dict[str, Any] = Field(default_factory=dict)
    update_summary: dict[str, Any] = Field(default_factory=dict)
    channel_summary: dict[str, Any] = Field(default_factory=dict)
    deep_refs: dict[str, str] = Field(default_factory=dict)


class MemoryConsoleFilter(BaseModel):
    project_id: str = Field(default="")
    workspace_id: str = Field(default="")
    scope_id: str = Field(default="")
    partition: str = Field(default="")
    layer: str = Field(default="")
    query: str = Field(default="")
    include_history: bool = False
    include_vault_refs: bool = False
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str = Field(default="")


class MemoryRecordProjection(BaseModel):
    record_id: str = Field(min_length=1)
    layer: str = Field(min_length=1)
    project_id: str = Field(default="")
    workspace_id: str = Field(default="")
    scope_id: str = Field(min_length=1)
    partition: str = Field(min_length=1)
    subject_key: str = Field(default="")
    summary: str = Field(default="")
    status: str = Field(default="")
    version: int | None = None
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime | None = None
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    derived_refs: list[str] = Field(default_factory=list)
    proposal_refs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    requires_vault_authorization: bool = False
    retrieval_backend: str = Field(default="")


class MemoryConsoleSummary(BaseModel):
    scope_count: int = 0
    fragment_count: int = 0
    sor_current_count: int = 0
    sor_history_count: int = 0
    vault_ref_count: int = 0
    proposal_count: int = 0
    pending_replay_count: int = 0


class MemoryConsoleDocument(ControlPlaneDocument):
    resource_type: str = "memory_console"
    resource_id: str = "memory:overview"
    active_project_id: str = Field(default="")
    active_workspace_id: str = Field(default="")
    backend_id: str = Field(default="")
    retrieval_backend: str = Field(default="")
    backend_state: str = Field(default="")
    index_health: dict[str, Any] = Field(default_factory=dict)
    filters: MemoryConsoleFilter = Field(default_factory=MemoryConsoleFilter)
    summary: MemoryConsoleSummary = Field(default_factory=MemoryConsoleSummary)
    records: list[MemoryRecordProjection] = Field(default_factory=list)
    available_scopes: list[str] = Field(default_factory=list)
    available_partitions: list[str] = Field(default_factory=list)
    available_layers: list[str] = Field(default_factory=list)
    advanced_refs: dict[str, str] = Field(default_factory=dict)


class MemorySubjectHistoryDocument(ControlPlaneDocument):
    resource_type: str = "memory_subject_history"
    resource_id: str = "memory-subject:overview"
    active_project_id: str = Field(default="")
    active_workspace_id: str = Field(default="")
    retrieval_backend: str = Field(default="")
    backend_state: str = Field(default="")
    index_health: dict[str, Any] = Field(default_factory=dict)
    scope_id: str = Field(default="")
    subject_key: str = Field(default="")
    current_record: MemoryRecordProjection | None = None
    history: list[MemoryRecordProjection] = Field(default_factory=list)
    latest_proposal_refs: list[str] = Field(default_factory=list)


class MemoryProposalSummary(BaseModel):
    pending: int = 0
    validated: int = 0
    rejected: int = 0
    committed: int = 0


class MemoryProposalAuditItem(BaseModel):
    proposal_id: str = Field(min_length=1)
    scope_id: str = Field(min_length=1)
    partition: str = Field(min_length=1)
    action: str = Field(min_length=1)
    subject_key: str = Field(default="")
    status: str = Field(min_length=1)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    rationale: str = Field(default="")
    is_sensitive: bool = False
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utc_now)
    validated_at: datetime | None = None
    committed_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryProposalAuditDocument(ControlPlaneDocument):
    resource_type: str = "memory_proposal_audit"
    resource_id: str = "memory-proposals:overview"
    active_project_id: str = Field(default="")
    active_workspace_id: str = Field(default="")
    retrieval_backend: str = Field(default="")
    backend_state: str = Field(default="")
    summary: MemoryProposalSummary = Field(default_factory=MemoryProposalSummary)
    items: list[MemoryProposalAuditItem] = Field(default_factory=list)


class VaultAccessRequestItem(BaseModel):
    request_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    workspace_id: str = Field(default="")
    scope_id: str = Field(min_length=1)
    partition: str = Field(default="")
    subject_key: str = Field(default="")
    reason: str = Field(default="")
    requester_actor_id: str = Field(min_length=1)
    requester_actor_label: str = Field(default="")
    status: str = Field(min_length=1)
    decision: str = Field(default="")
    requested_at: datetime = Field(default_factory=_utc_now)
    resolved_at: datetime | None = None
    resolver_actor_id: str = Field(default="")
    resolver_actor_label: str = Field(default="")


class VaultAccessGrantItem(BaseModel):
    grant_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    workspace_id: str = Field(default="")
    scope_id: str = Field(min_length=1)
    partition: str = Field(default="")
    subject_key: str = Field(default="")
    granted_to_actor_id: str = Field(min_length=1)
    granted_to_actor_label: str = Field(default="")
    granted_by_actor_id: str = Field(min_length=1)
    granted_by_actor_label: str = Field(default="")
    granted_at: datetime = Field(default_factory=_utc_now)
    expires_at: datetime | None = None
    status: str = Field(min_length=1)


class VaultRetrievalAuditItem(BaseModel):
    retrieval_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    workspace_id: str = Field(default="")
    scope_id: str = Field(min_length=1)
    partition: str = Field(default="")
    subject_key: str = Field(default="")
    query: str = Field(default="")
    grant_id: str = Field(default="")
    actor_id: str = Field(min_length=1)
    actor_label: str = Field(default="")
    authorized: bool = False
    reason_code: str = Field(min_length=1)
    result_count: int = Field(default=0, ge=0)
    retrieved_vault_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utc_now)


class VaultAuthorizationDocument(ControlPlaneDocument):
    resource_type: str = "vault_authorization"
    resource_id: str = "vault:authorization"
    active_project_id: str = Field(default="")
    active_workspace_id: str = Field(default="")
    retrieval_backend: str = Field(default="")
    backend_state: str = Field(default="")
    active_requests: list[VaultAccessRequestItem] = Field(default_factory=list)
    active_grants: list[VaultAccessGrantItem] = Field(default_factory=list)
    recent_retrievals: list[VaultRetrievalAuditItem] = Field(default_factory=list)


class ActionDefinition(BaseModel):
    action_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    description: str = Field(default="")
    category: str = Field(default="general")
    supported_surfaces: list[ControlPlaneSurface] = Field(default_factory=list)
    surface_aliases: dict[str, list[str]] = Field(default_factory=dict)
    support_status_by_surface: dict[str, ControlPlaneSupportStatus] = Field(default_factory=dict)
    params_schema: dict[str, Any] = Field(default_factory=dict)
    result_schema: dict[str, Any] = Field(default_factory=dict)
    risk_hint: str = Field(default="low")
    approval_hint: str = Field(default="none")
    idempotency_hint: str = Field(default="")
    resource_targets: list[str] = Field(default_factory=list)


class ActionRegistryDocument(ControlPlaneDocument):
    resource_type: str = "action_registry"
    resource_id: str = "actions:registry"
    actions: list[ActionDefinition] = Field(default_factory=list)


class ActionRequestEnvelope(BaseModel):
    contract_version: str = Field(default="1.0.0")
    request_id: str = Field(min_length=1)
    action_id: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)
    surface: ControlPlaneSurface = ControlPlaneSurface.WEB
    actor: ControlPlaneActor
    requested_at: datetime = Field(default_factory=_utc_now)
    idempotency_key: str = Field(default="")
    context: dict[str, Any] = Field(default_factory=dict)


class ActionResultEnvelope(BaseModel):
    contract_version: str = Field(default="1.0.0")
    request_id: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
    action_id: str = Field(min_length=1)
    status: ControlPlaneActionStatus
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    data: dict[str, Any] = Field(default_factory=dict)
    resource_refs: list[ControlPlaneResourceRef] = Field(default_factory=list)
    target_refs: list[ControlPlaneTargetRef] = Field(default_factory=list)
    handled_at: datetime = Field(default_factory=_utc_now)
    audit_event_id: str | None = None


class ControlPlaneEvent(BaseModel):
    contract_version: str = Field(default="1.0.0")
    event_id: str = Field(default="")
    event_type: ControlPlaneEventType
    request_id: str = Field(default="")
    correlation_id: str = Field(default="")
    causation_id: str = Field(default="")
    actor: ControlPlaneActor
    surface: ControlPlaneSurface
    occurred_at: datetime = Field(default_factory=_utc_now)
    payload_summary: str = Field(default="")
    resource_ref: ControlPlaneResourceRef | None = None
    resource_refs: list[ControlPlaneResourceRef] = Field(default_factory=list)
    target_refs: list[ControlPlaneTargetRef] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ControlPlaneState(BaseModel):
    selected_project_id: str = Field(default="")
    selected_workspace_id: str = Field(default="")
    focused_session_id: str = Field(default="")
    focused_thread_id: str = Field(default="")
    updated_at: datetime = Field(default_factory=_utc_now)
