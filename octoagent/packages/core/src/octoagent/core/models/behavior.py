"""Feature 049: Butler persona / clarification behavior 领域模型。"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from .capability import ToolAvailabilityExplanation


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


class BehaviorLayerKind(StrEnum):
    ROLE = "role"
    COMMUNICATION = "communication"
    SOLVING = "solving"
    TOOL_BOUNDARY = "tool_boundary"
    MEMORY_POLICY = "memory_policy"
    BOOTSTRAP = "bootstrap"


class BehaviorVisibility(StrEnum):
    SHARED = "shared"
    PRIVATE = "private"


class BehaviorWorkspaceScope(StrEnum):
    SYSTEM_SHARED = "system_shared"
    AGENT_PRIVATE = "agent_private"
    PROJECT_SHARED = "project_shared"
    PROJECT_AGENT = "project_agent"


class BehaviorEditabilityMode(StrEnum):
    DIRECT = "direct"
    PROPOSAL_REQUIRED = "proposal_required"
    READ_ONLY = "read_only"


class BehaviorReviewMode(StrEnum):
    NONE = "none"
    REVIEW_REQUIRED = "review_required"


class ClarificationAction(StrEnum):
    DIRECT = "direct"
    CLARIFY = "clarify"
    BEST_EFFORT_FALLBACK = "best_effort_fallback"
    DELEGATE_AFTER_CLARIFICATION = "delegate_after_clarification"


class AgentDecisionMode(StrEnum):
    DIRECT_ANSWER = "direct_answer"
    ASK_ONCE = "ask_once"
    BEST_EFFORT_ANSWER = "best_effort_answer"
    DELEGATE_GRAPH = "delegate_graph"


# 历史兼容别名
ButlerDecisionMode = AgentDecisionMode


class RecallPlanMode(StrEnum):
    SKIP = "skip"
    RECALL = "recall"


class BehaviorPackFile(BaseModel):
    file_id: str = Field(min_length=1)
    title: str = Field(default="")
    path_hint: str = Field(default="")
    layer: BehaviorLayerKind = BehaviorLayerKind.ROLE
    content: str = Field(default="")
    visibility: BehaviorVisibility = BehaviorVisibility.PRIVATE
    share_with_workers: bool = False
    source_kind: str = Field(default="default_template")
    budget_chars: int = Field(default=0, ge=0)
    original_char_count: int = Field(default=0, ge=0)
    effective_char_count: int = Field(default=0, ge=0)
    truncated: bool = False
    truncation_reason: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)


class BehaviorLayer(BaseModel):
    layer: BehaviorLayerKind
    content: str = Field(default="")
    source_file_ids: list[str] = Field(default_factory=list)
    original_char_count: int = Field(default=0, ge=0)
    effective_char_count: int = Field(default=0, ge=0)
    truncated_file_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class BehaviorPack(BaseModel):
    pack_id: str = Field(default="")
    profile_id: str = Field(default="")
    scope: str = Field(default="system")
    source_chain: list[str] = Field(default_factory=list)
    files: list[BehaviorPackFile] = Field(default_factory=list)
    layers: list[BehaviorLayer] = Field(default_factory=list)
    clarification_policy: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class BehaviorWorkspaceFile(BaseModel):
    file_id: str = Field(min_length=1)
    title: str = Field(default="")
    layer: BehaviorLayerKind = BehaviorLayerKind.ROLE
    visibility: BehaviorVisibility = BehaviorVisibility.PRIVATE
    share_with_workers: bool = False
    scope: BehaviorWorkspaceScope | None = None
    path: str = Field(default="")
    editable_mode: BehaviorEditabilityMode = BehaviorEditabilityMode.PROPOSAL_REQUIRED
    review_mode: BehaviorReviewMode = BehaviorReviewMode.REVIEW_REQUIRED
    content: str = Field(default="")
    source_kind: str = Field(default="default_template")
    is_advanced: bool = False
    budget_chars: int = Field(default=0, ge=0)
    original_char_count: int = Field(default=0, ge=0)
    effective_char_count: int = Field(default=0, ge=0)
    truncated: bool = False
    truncation_reason: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProjectPathManifestFile(BaseModel):
    file_id: str = Field(min_length=1)
    path: str = Field(default="")
    scope: BehaviorWorkspaceScope | None = None
    editable_mode: BehaviorEditabilityMode = BehaviorEditabilityMode.PROPOSAL_REQUIRED
    review_mode: BehaviorReviewMode = BehaviorReviewMode.REVIEW_REQUIRED
    source_kind: str = Field(default="")
    exists_on_disk: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProjectPathManifest(BaseModel):
    repository_root: str = Field(default="")
    project_root: str = Field(default="")
    project_root_source: str = Field(default="")
    project_behavior_root: str = Field(default="")
    project_workspace_root: str = Field(default="")
    project_workspace_root_source: str = Field(default="")
    workspace_id: str = Field(default="")
    workspace_slug: str = Field(default="")
    project_data_root: str = Field(default="")
    project_notes_root: str = Field(default="")
    project_artifacts_root: str = Field(default="")
    shared_behavior_root: str = Field(default="")
    agent_behavior_root: str = Field(default="")
    project_agent_behavior_root: str = Field(default="")
    secret_bindings_path: str = Field(default="")
    effective_behavior_files: list[ProjectPathManifestFile] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class StorageBoundaryHints(BaseModel):
    facts_store: str = Field(default="MemoryService")
    facts_access: str = Field(default="")
    secrets_store: str = Field(default="SecretService")
    secrets_access: str = Field(default="")
    secret_bindings_metadata_path: str = Field(default="")
    behavior_store: str = Field(default="behavior_files")
    workspace_roots: list[str] = Field(default_factory=list)
    note: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)


class BehaviorWorkspace(BaseModel):
    project_slug: str = Field(default="")
    system_dir: str = Field(default="")
    project_dir: str = Field(default="")
    agent_slug: str = Field(default="")
    shared_dir: str = Field(default="")
    agent_dir: str = Field(default="")
    project_root_dir: str = Field(default="")
    project_behavior_dir: str = Field(default="")
    project_agent_dir: str = Field(default="")
    project_workspace_dir: str = Field(default="")
    project_data_dir: str = Field(default="")
    project_notes_dir: str = Field(default="")
    project_artifacts_dir: str = Field(default="")
    secret_bindings_path: str = Field(default="")
    files: list[BehaviorWorkspaceFile] = Field(default_factory=list)
    source_chain: list[str] = Field(default_factory=list)
    path_manifest: ProjectPathManifest | None = None
    storage_boundary_hints: StorageBoundaryHints | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolUniverseHints(BaseModel):
    scope: str = Field(default="")
    tool_profile: str = Field(default="")
    resolution_mode: str = Field(default="")
    selected_tools: list[str] = Field(default_factory=list)
    discovery_entrypoints: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    mounted_tools: list[ToolAvailabilityExplanation] = Field(default_factory=list)
    blocked_tools: list[ToolAvailabilityExplanation] = Field(default_factory=list)
    note: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuntimeHintBundle(BaseModel):
    surface: str = Field(default="")
    explicit_web_search_requested: bool = False
    can_delegate_research: bool = False
    weather_query: bool = False
    current_location_hint: str = Field(default="")
    recent_location_hint: str = Field(default="")
    effective_location_hint: str = Field(default="")
    recent_clarification_category: str = Field(default="")
    recent_clarification_source_text: str = Field(default="")
    recent_worker_lane_worker_type: str = Field(default="")
    recent_worker_lane_profile_id: str = Field(default="")
    recent_worker_lane_topic: str = Field(default="")
    recent_worker_lane_summary: str = Field(default="")
    tool_universe: ToolUniverseHints | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RecallPlan(BaseModel):
    mode: RecallPlanMode = RecallPlanMode.SKIP
    query: str = Field(default="")
    rationale: str = Field(default="")
    subject_hint: str = Field(default="")
    focus_terms: list[str] = Field(default_factory=list)
    allow_vault: bool = False
    limit: int = Field(default=4, ge=1, le=8)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RecallEvidenceBundle(BaseModel):
    mode: RecallPlanMode = RecallPlanMode.SKIP
    query: str = Field(default="")
    executed: bool = False
    hit_count: int = Field(default=0, ge=0)
    delivered_hit_count: int = Field(default=0, ge=0)
    citations: list[str] = Field(default_factory=list)
    backend: str = Field(default="")
    backend_state: str = Field(default="")
    degraded_reasons: list[str] = Field(default_factory=list)
    rationale: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentDecision(BaseModel):
    mode: AgentDecisionMode = AgentDecisionMode.DIRECT_ANSWER
    category: str = Field(default="")
    rationale: str = Field(default="")
    missing_inputs: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    tool_intent: str = Field(default="")
    target_worker_type: str = Field(default="")
    target_worker_profile_id: str = Field(default="")
    delegate_objective: str = Field(default="")
    continuity_topic: str = Field(default="")
    prefer_sticky_worker: bool = False
    user_visible_boundary_note: str = Field(default="")
    reply_prompt: str = Field(default="")
    # Feature 065: DELEGATE_GRAPH 模式下的 Pipeline 标识和参数
    pipeline_id: str = Field(default="")
    pipeline_params: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


ButlerDecision = AgentDecision


class AgentLoopPlan(BaseModel):
    """Agent 预路由与 recall 统一规划结果。"""

    decision: AgentDecision = Field(default_factory=AgentDecision)
    recall_plan: RecallPlan = Field(default_factory=RecallPlan)
    metadata: dict[str, Any] = Field(default_factory=dict)


ButlerLoopPlan = AgentLoopPlan


class ClarificationDecision(BaseModel):
    action: ClarificationAction = ClarificationAction.DIRECT
    category: str = Field(default="")
    rationale: str = Field(default="")
    missing_inputs: list[str] = Field(default_factory=list)
    followup_prompt: str = Field(default="")
    fallback_hint: str = Field(default="")
    delegate_after_clarification: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class BehaviorSliceEnvelope(BaseModel):
    summary: str = Field(default="")
    shared_file_ids: list[str] = Field(default_factory=list)
    layers: list[BehaviorLayer] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class BehaviorFileChange(BaseModel):
    file_id: str = Field(min_length=1)
    summary: str = Field(default="")
    before_content: str = Field(default="")
    proposed_content: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)


class BehaviorPatchProposal(BaseModel):
    proposal_id: str = Field(min_length=1)
    profile_id: str = Field(min_length=1)
    title: str = Field(default="")
    rationale: str = Field(default="")
    review_required: bool = True
    target_file_ids: list[str] = Field(default_factory=list)
    file_changes: list[BehaviorFileChange] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)
