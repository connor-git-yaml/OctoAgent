"""Control Plane Delegation/Pipeline + Work 投影模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from ..capability import ToolAvailabilityExplanation
from ..pipeline import PipelineReplayFrame
from ._base import ControlPlaneCapability, ControlPlaneDocument


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
    agent_profile_id: str = Field(default="")
    session_owner_profile_id: str = Field(default="")
    turn_executor_kind: str = Field(default="")
    delegation_target_profile_id: str = Field(default="")
    requested_worker_profile_id: str = Field(default="")
    requested_worker_profile_version: int = Field(default=0, ge=0)
    effective_worker_snapshot_id: str = Field(default="")
    tool_resolution_mode: str = Field(default="")
    mounted_tools: list[ToolAvailabilityExplanation] = Field(default_factory=list)
    blocked_tools: list[ToolAvailabilityExplanation] = Field(default_factory=list)
    tool_resolution_warnings: list[str] = Field(default_factory=list)
    child_work_ids: list[str] = Field(default_factory=list)
    child_work_count: int = Field(default=0, ge=0)
    merge_ready: bool = False
    a2a_conversation_id: str = Field(default="")
    butler_agent_session_id: str = Field(default="")
    worker_agent_session_id: str = Field(default="")
    a2a_message_count: int = Field(default=0, ge=0)
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
