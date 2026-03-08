"""Feature 025: Secret Store 相关模型。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from octoagent.core.models import SecretRefSourceType
from pydantic import BaseModel, Field, SecretStr


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


class SecretRef(BaseModel):
    """不含明文的 secret 引用。"""

    source_type: SecretRefSourceType
    locator: dict[str, Any] = Field(default_factory=dict)
    display_name: str = ""
    redaction_label: str = "***"
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResolvedSecretRef(BaseModel):
    """解析后的 secret material。"""

    ref: SecretRef
    value: SecretStr
    resolution_summary: str = ""


class SecretAuditReport(BaseModel):
    report_id: str
    project_id: str
    overall_status: str = "ready"
    missing_targets: list[str] = Field(default_factory=list)
    unresolved_refs: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    plaintext_risks: list[str] = Field(default_factory=list)
    reload_required: bool = False
    restart_required: bool = False
    warnings: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=_utc_now)


class SecretConfigureSummary(BaseModel):
    project_id: str
    source_default: str
    configured_targets: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)


class SecretApplyRun(BaseModel):
    run_id: str
    project_id: str
    dry_run: bool = False
    status: str = "pending"
    planned_binding_ids: list[str] = Field(default_factory=list)
    applied_binding_ids: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    materialization_summary: dict[str, Any] = Field(default_factory=dict)
    reload_required: bool = False
    error_message: str = ""
    started_at: datetime = Field(default_factory=_utc_now)
    completed_at: datetime | None = None


class RuntimeSecretMaterialization(BaseModel):
    snapshot_id: str
    project_id: str
    resolved_env_names: list[str] = Field(default_factory=list)
    resolved_targets: list[str] = Field(default_factory=list)
    delivery_mode: str = "unmanaged_manual"
    requires_restart: bool = True
    expires_at: datetime | None = None
    generated_at: datetime = Field(default_factory=_utc_now)


class SecretReloadResult(BaseModel):
    project_id: str
    overall_status: str
    summary: str
    materialization: RuntimeSecretMaterialization
    warnings: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
