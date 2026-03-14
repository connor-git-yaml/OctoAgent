"""Feature 030: capability pack / ToolIndex 领域模型。"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


class WorkerType(StrEnum):
    """内建 worker 类型。"""

    GENERAL = "general"
    OPS = "ops"
    RESEARCH = "research"
    DEV = "dev"


class RuntimeKind(StrEnum):
    """委派目标 runtime 分类。"""

    WORKER = "worker"
    SUBAGENT = "subagent"
    ACP_RUNTIME = "acp_runtime"
    GRAPH_AGENT = "graph_agent"


class BuiltinToolAvailabilityStatus(StrEnum):
    """Built-in tool 可用性状态。"""

    AVAILABLE = "available"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    INSTALL_REQUIRED = "install_required"


class BundledToolDefinition(BaseModel):
    """内建工具定义。"""

    tool_name: str = Field(min_length=1)
    label: str = Field(default="")
    description: str = Field(default="")
    tool_group: str = Field(default="")
    tool_profile: str = Field(default="standard")
    tags: list[str] = Field(default_factory=list)
    worker_types: list[WorkerType] = Field(default_factory=list)
    manifest_ref: str = Field(default="")
    availability: BuiltinToolAvailabilityStatus = BuiltinToolAvailabilityStatus.AVAILABLE
    availability_reason: str = Field(default="")
    install_hint: str = Field(default="")
    entrypoints: list[str] = Field(default_factory=list)
    runtime_kinds: list[RuntimeKind] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class BundledSkillDefinition(BaseModel):
    """内建 skill 定义。"""

    skill_id: str = Field(min_length=1)
    label: str = Field(default="")
    description: str = Field(default="")
    model_alias: str = Field(default="main")
    permission_mode: str = Field(default="restrict")
    worker_types: list[WorkerType] = Field(default_factory=list)
    tools_allowed: list[str] = Field(default_factory=list)
    pipeline_templates: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkerBootstrapFile(BaseModel):
    """Worker bootstrap 文件。"""

    file_id: str = Field(min_length=1)
    path_hint: str = Field(default="")
    content: str = Field(default="")
    applies_to_worker_types: list[WorkerType] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkerCapabilityProfile(BaseModel):
    """Worker 能力档案。"""

    worker_type: WorkerType
    capabilities: list[str] = Field(default_factory=list)
    default_model_alias: str = Field(default="main")
    default_tool_profile: str = Field(default="standard")
    default_tool_groups: list[str] = Field(default_factory=list)
    bootstrap_file_ids: list[str] = Field(default_factory=list)
    runtime_kinds: list[RuntimeKind] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class BundledCapabilityPack(BaseModel):
    """030 的 bundled capability pack。"""

    pack_id: str = Field(default="bundled:default")
    version: str = Field(default="1.0.0")
    skills: list[BundledSkillDefinition] = Field(default_factory=list)
    tools: list[BundledToolDefinition] = Field(default_factory=list)
    worker_profiles: list[WorkerCapabilityProfile] = Field(default_factory=list)
    bootstrap_files: list[WorkerBootstrapFile] = Field(default_factory=list)
    fallback_toolset: list[str] = Field(default_factory=list)
    degraded_reason: str = Field(default="")
    generated_at: datetime = Field(default_factory=_utc_now)


class ToolIndexQuery(BaseModel):
    """ToolIndex 查询。"""

    query: str = Field(min_length=1)
    limit: int = Field(default=5, ge=1, le=50)
    tool_groups: list[str] = Field(default_factory=list)
    worker_type: WorkerType | None = None
    tool_profile: str = Field(default="")
    tags: list[str] = Field(default_factory=list)
    project_id: str = Field(default="")
    workspace_id: str = Field(default="")


class ToolIndexHit(BaseModel):
    """ToolIndex 命中记录。"""

    tool_name: str = Field(min_length=1)
    score: float = Field(default=0.0, ge=0.0)
    match_reason: str = Field(default="")
    matched_filters: list[str] = Field(default_factory=list)
    tool_group: str = Field(default="")
    tool_profile: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolAvailabilityExplanation(BaseModel):
    """单个工具在当前运行里的可用性说明。"""

    tool_name: str = Field(min_length=1)
    status: str = Field(default="mounted")
    source_kind: str = Field(default="profile_selected")
    tool_group: str = Field(default="")
    tool_profile: str = Field(default="")
    reason_code: str = Field(default="")
    summary: str = Field(default="")
    recommended_action: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)


class EffectiveToolUniverse(BaseModel):
    """当前运行实际挂载的稳定工具宇宙。"""

    profile_id: str = Field(default="")
    profile_revision: int = Field(default=0, ge=0)
    worker_type: str = Field(default="")
    tool_profile: str = Field(default="")
    resolution_mode: str = Field(default="tool_index")
    selected_tools: list[str] = Field(default_factory=list)
    recommended_tools: list[str] = Field(default_factory=list)
    discovery_entrypoints: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class DynamicToolSelection(BaseModel):
    """动态工具选择结果。"""

    selection_id: str = Field(min_length=1)
    query: ToolIndexQuery
    selected_tools: list[str] = Field(default_factory=list)
    recommended_tools: list[str] = Field(default_factory=list)
    hits: list[ToolIndexHit] = Field(default_factory=list)
    backend: str = Field(default="in_memory")
    is_fallback: bool = False
    warnings: list[str] = Field(default_factory=list)
    resolution_mode: str = Field(default="tool_index")
    effective_tool_universe: EffectiveToolUniverse | None = None
    mounted_tools: list[ToolAvailabilityExplanation] = Field(default_factory=list)
    blocked_tools: list[ToolAvailabilityExplanation] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utc_now)
