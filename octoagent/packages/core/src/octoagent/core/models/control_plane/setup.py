"""Control Plane Setup/Config/Wizard + MCP + Diagnostics 模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ._base import ControlPlaneDocument, ControlPlaneResourceRef


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
    warnings: list[str] = Field(default_factory=list)


class ProjectSelectorDocument(ControlPlaneDocument):
    resource_type: str = "project_selector"
    resource_id: str = "project:selector"
    current_project_id: str = Field(default="")
    default_project_id: str = Field(default="")
    fallback_reason: str = Field(default="")
    switch_allowed: bool = False
    available_projects: list[ProjectOption] = Field(default_factory=list)


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


class SetupGovernanceDocument(ControlPlaneDocument):
    resource_type: str = "setup_governance"
    resource_id: str = "setup:governance"
    active_project_id: str = Field(default="")
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


class McpProviderItem(BaseModel):
    provider_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    description: str = Field(default="")
    editable: bool = True
    removable: bool = True
    enabled: bool = True
    status: str = Field(default="unconfigured")
    command: str = Field(default="")
    args: list[str] = Field(default_factory=list)
    cwd: str = Field(default="")
    env: dict[str, str] = Field(default_factory=dict)
    mount_policy: str = Field(default="auto_readonly")
    tool_count: int = Field(default=0, ge=0)
    selection_item_id: str = Field(default="")
    install_hint: str = Field(default="")
    error: str = Field(default="")
    warnings: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)
    # Feature 058: 安装信息展示字段
    install_source: str = Field(default="")
    install_version: str = Field(default="")
    install_path: str = Field(default="")
    installed_at: str = Field(default="")


class McpProviderCatalogDocument(ControlPlaneDocument):
    resource_type: str = "mcp_provider_catalog"
    resource_id: str = "mcp-providers:catalog"
    active_project_id: str = Field(default="")
    items: list[McpProviderItem] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)


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
