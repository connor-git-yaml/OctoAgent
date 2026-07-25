"""F149 Web REST 合同的唯一 Gateway adapter seam。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Literal

from fastapi import FastAPI
from octoagent.core.models import ActionRegistryDocument
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    RootModel,
    field_validator,
)

F149_SNAPSHOT_RESOURCE_NAMES = (
    "config",
    "project_selector",
    "sessions",
    "agent_profiles",
    "worker_profiles",
    "owner_profile",
    "bootstrap_session",
    "context_continuity",
    "capability_pack",
    "skill_governance",
    "mcp_provider_catalog",
    "setup_governance",
    "delegation",
    "diagnostics",
    "retrieval_platform",
    "memory",
)

F149_REST_ENDPOINTS = (
    ("DELETE", "/api/approval-overrides/{agent_runtime_id}/{tool_name}"),
    ("DELETE", "/api/skills/{name}"),
    ("GET", "/api/approval-center/summary"),
    ("GET", "/api/approval-overrides"),
    ("GET", "/api/approvals"),
    ("GET", "/api/behavior-versions/diff"),
    ("GET", "/api/behavior-versions/files"),
    ("GET", "/api/behavior-versions/versions"),
    ("GET", "/api/behavior/compact/candidates"),
    ("GET", "/api/consolidation/candidates"),
    ("GET", "/api/control/resources/agent-profiles"),
    ("GET", "/api/control/resources/automation"),
    ("GET", "/api/control/resources/bootstrap-session"),
    ("GET", "/api/control/resources/capability-pack"),
    ("GET", "/api/control/resources/config"),
    ("GET", "/api/control/resources/context-frames"),
    ("GET", "/api/control/resources/delegation"),
    ("GET", "/api/control/resources/diagnostics"),
    ("GET", "/api/control/resources/mcp-provider-catalog"),
    ("GET", "/api/control/resources/memory"),
    ("GET", "/api/control/resources/owner-profile"),
    ("GET", "/api/control/resources/project-selector"),
    ("GET", "/api/control/resources/remote-access"),
    ("GET", "/api/control/resources/retrieval-platform"),
    ("GET", "/api/control/resources/sessions"),
    ("GET", "/api/control/resources/setup-governance"),
    ("GET", "/api/control/resources/skill-governance"),
    ("GET", "/api/control/resources/worker-profile-revisions/{profile_id}"),
    ("GET", "/api/control/resources/worker-profiles"),
    ("GET", "/api/control/snapshot"),
    ("GET", "/api/files/tasks"),
    ("GET", "/api/files/tasks/{task_id}/diff"),
    ("GET", "/api/files/tasks/{task_id}/logical-files"),
    ("GET", "/api/files/tasks/{task_id}/versions"),
    ("GET", "/api/memory/candidates"),
    ("GET", "/api/operator/inbox"),
    ("GET", "/api/ops/recovery"),
    ("GET", "/api/ops/update/status"),
    ("GET", "/api/skills"),
    ("GET", "/api/skills/{name}"),
    ("GET", "/api/tasks"),
    ("GET", "/api/tasks/{task_id}"),
    ("GET", "/api/workspace-git/blame"),
    ("GET", "/api/workspace-git/commit"),
    ("GET", "/api/workspace-git/diff"),
    ("GET", "/api/workspace-git/history"),
    ("GET", "/api/workspace-git/projects"),
    ("POST", "/api/behavior/compact/candidates/{candidate_id}/accept"),
    ("POST", "/api/behavior/compact/candidates/{candidate_id}/reject"),
    ("POST", "/api/consolidation/candidates/{candidate_id}/accept"),
    ("POST", "/api/consolidation/candidates/{candidate_id}/reject"),
    ("POST", "/api/memory/candidates/{candidate_id}/discard"),
    ("POST", "/api/memory/candidates/{candidate_id}/promote"),
    ("POST", "/api/operator/actions"),
    ("POST", "/api/ops/backup/create"),
    ("POST", "/api/ops/export/chats"),
    ("POST", "/api/ops/restart"),
    ("POST", "/api/ops/update/apply"),
    ("POST", "/api/ops/update/dry-run"),
    ("POST", "/api/ops/verify"),
    ("POST", "/api/skills"),
    ("POST", "/api/workspace-git/rollback"),
    ("POST", "/api/workspace-git/rollback/{request_id}/approve"),
    ("POST", "/api/workspace-git/rollback/{request_id}/reject"),
    ("PUT", "/api/consolidation/candidates/bulk_reject"),
    ("PUT", "/api/memory/candidates/bulk_discard"),
)


class F149RawSnapshotResource(RootModel[dict[str, JsonValue]]):
    """开放聚合中的单个命名 raw resource；不得直接进入 domain/UI。"""


class F149SnapshotResourceError(BaseModel):
    """单资源失败的稳定 envelope。"""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1)
    error_type: str = Field(min_length=1)
    message: str


class F149SnapshotEnvelope(BaseModel):
    """完整 raw snapshot envelope；内部 resource 保持命名开放边界。"""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ready", "degraded"]
    contract_version: str = Field(min_length=1)
    resources: dict[str, F149RawSnapshotResource]
    registry: ActionRegistryDocument
    degraded_sections: list[str]
    resource_errors: dict[str, F149SnapshotResourceError]
    generated_at: datetime

    @field_validator("resources")
    @classmethod
    def _require_exact_resources(
        cls,
        value: dict[str, F149RawSnapshotResource],
    ) -> dict[str, F149RawSnapshotResource]:
        if set(value) != set(F149_SNAPSHOT_RESOURCE_NAMES):
            raise ValueError("snapshot resources必须精确覆盖F149冻结集合")
        return value

    @field_validator("degraded_sections")
    @classmethod
    def _validate_degraded_sections(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("degraded_sections不能重复")
        if not set(value).issubset(F149_SNAPSHOT_RESOURCE_NAMES):
            raise ValueError("degraded_sections包含未知resource")
        return value

    @field_validator("resource_errors")
    @classmethod
    def _validate_resource_errors(
        cls,
        value: dict[str, F149SnapshotResourceError],
    ) -> dict[str, F149SnapshotResourceError]:
        if not set(value).issubset(F149_SNAPSHOT_RESOURCE_NAMES):
            raise ValueError("resource_errors包含未知resource")
        return value


class F149RemoteAccessStatusResponse(BaseModel):
    """Settings实际消费的电脑Web远程访问只读投影。"""

    model_config = ConfigDict(extra="forbid")

    state: Literal["unconfigured", "pending_verification", "ready", "fault"]
    hostname: str | None
    owner_email: str | None
    last_verified_at: datetime | None = None
    reason_code: str | None = None
    recovery_action: str | None = None
    desktop_web_url: str | None = None
    access_logout_url: str | None = None


class F149RequesterInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: str
    sender_id: str


class F149TaskDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    created_at: datetime
    updated_at: datetime
    status: str
    title: str
    alias: str = ""
    thread_id: str
    scope_id: str
    requester: F149RequesterInfo
    risk_level: str


class F149RawEventPayload(RootModel[dict[str, JsonValue]]):
    """TaskDetail中的命名 raw event boundary。"""


class F149TaskEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    task_seq: int
    ts: datetime
    type: str
    actor: str
    payload: F149RawEventPayload


class F149ArtifactPart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    mime: str
    content: str | None


class F149TaskArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    name: str
    size: int
    parts: list[F149ArtifactPart]


class F149TaskDetailResponse(BaseModel):
    """TaskDetail页面实际消费的有限response model。"""

    model_config = ConfigDict(extra="forbid")

    task: F149TaskDetail
    events: list[F149TaskEvent]
    artifacts: list[F149TaskArtifact]


def decode_f149_snapshot(payload: object) -> F149SnapshotEnvelope:
    """在raw snapshot进入F149 projection前验证完整基础envelope。"""

    return F149SnapshotEnvelope.model_validate(payload)


def export_f149_rest_openapi(app: FastAPI) -> dict[str, JsonValue]:
    """从真实FastAPI OpenAPI确定性截取F149实际消费的REST合同。"""

    source = app.openapi()
    selected_paths: dict[str, JsonValue] = {}
    for method, path in F149_REST_ENDPOINTS:
        source_path = source.get("paths", {}).get(path)
        if not isinstance(source_path, dict):
            continue
        operation = source_path.get(method.lower())
        if not isinstance(operation, dict):
            continue
        selected_paths.setdefault(path, {})[method.lower()] = operation

    artifact: dict[str, JsonValue] = {
        "openapi": str(source.get("openapi", "3.1.0")),
        "info": source.get("info", {}),
        "paths": selected_paths,
        "components": {"schemas": source.get("components", {}).get("schemas", {})},
    }
    return json.loads(json.dumps(artifact, ensure_ascii=False, sort_keys=True))
