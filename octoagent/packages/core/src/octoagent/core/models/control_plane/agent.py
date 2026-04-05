"""Control Plane Agent/Worker Profile + Policy + Capability 模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from ..agent_context import WorkerProfileOriginKind, WorkerProfileStatus
from ..capability import BundledCapabilityPack, ToolAvailabilityExplanation
from ._base import ControlPlaneCapability, ControlPlaneDocument


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
    bootstrap_template_ids: list[str] = Field(default_factory=list)
    behavior_system: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    resource_limits: dict[str, Any] = Field(default_factory=dict, description="资源限制覆盖")
    updated_at: datetime | None = None


class AgentProfilesDocument(ControlPlaneDocument):
    resource_type: str = "agent_profiles"
    resource_id: str = "agent-profiles:overview"
    active_project_id: str = Field(default="")
    profiles: list[AgentProfileItem] = Field(default_factory=list)


class WorkerProfileStaticConfig(BaseModel):
    summary: str = Field(default="")
    model_alias: str = Field(default="main")
    tool_profile: str = Field(default="minimal")
    default_tool_groups: list[str] = Field(default_factory=list)
    selected_tools: list[str] = Field(default_factory=list)
    runtime_kinds: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    resource_limits: dict[str, Any] = Field(default_factory=dict, description="资源限制覆盖")


class WorkerProfileDynamicContext(BaseModel):
    active_project_id: str = Field(default="")
    active_work_count: int = Field(default=0, ge=0)
    running_work_count: int = Field(default=0, ge=0)
    attention_work_count: int = Field(default=0, ge=0)
    latest_work_id: str = Field(default="")
    latest_task_id: str = Field(default="")
    latest_work_title: str = Field(default="")
    latest_work_status: str = Field(default="")
    latest_target_kind: str = Field(default="")
    current_selected_tools: list[str] = Field(default_factory=list)
    current_tool_resolution_mode: str = Field(default="")
    current_tool_warnings: list[str] = Field(default_factory=list)
    current_mounted_tools: list[ToolAvailabilityExplanation] = Field(default_factory=list)
    current_blocked_tools: list[ToolAvailabilityExplanation] = Field(default_factory=list)
    current_discovery_entrypoints: list[str] = Field(default_factory=list)
    updated_at: datetime | None = None


class WorkerProfileViewItem(BaseModel):
    profile_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    scope: str = Field(default="system")
    project_id: str = Field(default="")
    mode: str = Field(default="singleton")
    origin_kind: WorkerProfileOriginKind = WorkerProfileOriginKind.BUILTIN
    status: WorkerProfileStatus = WorkerProfileStatus.ACTIVE
    active_revision: int = Field(default=0, ge=0)
    draft_revision: int = Field(default=0, ge=0)
    effective_snapshot_id: str = Field(default="")
    editable: bool = False
    summary: str = Field(default="")
    static_config: WorkerProfileStaticConfig = Field(default_factory=WorkerProfileStaticConfig)
    dynamic_context: WorkerProfileDynamicContext = Field(
        default_factory=WorkerProfileDynamicContext
    )
    is_default_for_project: bool = Field(default=False)
    behavior_system: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    capabilities: list[ControlPlaneCapability] = Field(default_factory=list)


class WorkerProfilesDocument(ControlPlaneDocument):
    resource_type: str = "worker_profiles"
    resource_id: str = "worker-profiles:overview"
    active_project_id: str = Field(default="")
    profiles: list[WorkerProfileViewItem] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)


class WorkerProfileRevisionItem(BaseModel):
    revision_id: str = Field(min_length=1)
    profile_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    change_summary: str = Field(default="")
    created_by: str = Field(default="")
    created_at: datetime | None = None
    snapshot_payload: dict[str, Any] = Field(default_factory=dict)


class WorkerProfileRevisionsDocument(ControlPlaneDocument):
    resource_type: str = "worker_profile_revisions"
    resource_id: str = "worker-profile-revisions:overview"
    profile_id: str = Field(default="")
    revisions: list[WorkerProfileRevisionItem] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)


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
    active_profile_id: str = Field(default="")
    profiles: list[PolicyProfileItem] = Field(default_factory=list)


class SkillGovernanceItem(BaseModel):
    """Skill / MCP 治理条目。"""

    item_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    source_kind: str = Field(default="builtin")
    scope: str = Field(default="project")
    enabled_by_default: bool = False
    selected: bool = False
    selection_source: str = Field(default="default")
    availability: str = Field(default="available")
    trust_level: str = Field(default="trusted")
    blocking: bool = False
    required_secrets: list[str] = Field(default_factory=list)
    missing_requirements: list[str] = Field(default_factory=list)
    install_hint: str = Field(default="")
    details: dict[str, Any] = Field(default_factory=dict)


class SkillGovernanceDocument(ControlPlaneDocument):
    """Skill / MCP 治理文档。"""

    resource_type: str = "skill_governance"
    resource_id: str = "skills:governance"
    active_project_id: str = Field(default="")
    items: list[SkillGovernanceItem] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)


class CapabilityPackDocument(ControlPlaneDocument):
    resource_type: str = "capability_pack"
    resource_id: str = "capability:bundled"
    pack: BundledCapabilityPack = Field(default_factory=BundledCapabilityPack)
    selected_project_id: str = Field(default="")
