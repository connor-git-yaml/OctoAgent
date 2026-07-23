"""Feature 025/026: control-plane 共享文档模型。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


class ConfigSchemaDocument(BaseModel):
    """026-A `ConfigSchemaDocument` 的最小可消费形态。"""

    model_config = ConfigDict(populate_by_name=True)

    resource_type: str = "config_schema"
    resource_id: str = "config-schema.octoagent"
    contract_version: str = "026-a"
    schema_version: str = "1"
    generated_at: datetime = Field(default_factory=_utc_now)
    schema_payload: dict[str, Any] = Field(
        default_factory=dict,
        alias="schema",
        serialization_alias="schema",
    )
    ui_hints: dict[str, Any] = Field(default_factory=dict)
    supported_surfaces: list[str] = Field(default_factory=lambda: ["cli", "web"])


class ProjectCandidate(BaseModel):
    project_id: str
    slug: str
    name: str
    is_default: bool = False
    workspace_id: str | None = None
    readiness: str = "ready"
    warnings: list[str] = Field(default_factory=list)


class ProjectSelectorDocument(BaseModel):
    """026-A `ProjectSelectorDocument` 的最小可消费形态。"""

    resource_type: str = "project_selector"
    resource_id: str = "project-selector.cli"
    contract_version: str = "026-a"
    schema_version: str = "1"
    generated_at: datetime = Field(default_factory=_utc_now)
    current_project: ProjectCandidate | None = None
    candidate_projects: list[ProjectCandidate] = Field(default_factory=list)
    readiness: str = "ready"
    warnings: list[str] = Field(default_factory=list)
    capabilities: dict[str, bool] = Field(
        default_factory=lambda: {
            "can_create": True,
            "can_select": True,
            "can_edit": True,
            "can_inspect": True,
        }
    )


class WizardNextAction(BaseModel):
    action_id: str
    title: str
    description: str
    command: str = ""
    blocking: bool = True


class WizardStepState(BaseModel):
    step_id: str
    title: str
    status: str = "pending"
    summary: str = ""


class WizardSessionDocument(BaseModel):
    """026-A `WizardSessionDocument` 的最小可消费形态。"""

    resource_type: str = "wizard_session"
    resource_id: str
    contract_version: str = "026-a"
    schema_version: str = "1"
    project_id: str
    status: str = "pending"
    current_step: str = "project"
    blocking_reason: str = ""
    next_actions: list[WizardNextAction] = Field(default_factory=list)
    step_states: list[WizardStepState] = Field(default_factory=list)
    schema_ref: str = "config-schema.octoagent"
    updated_at: datetime = Field(default_factory=_utc_now)
