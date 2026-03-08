"""Feature 030: Skill Pipeline 领域模型。"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


class PipelineNodeType(StrEnum):
    """Pipeline 节点类型。"""

    SKILL = "skill"
    TOOL = "tool"
    TRANSFORM = "transform"
    GATE = "gate"
    DELEGATION = "delegation"


class PipelineRunStatus(StrEnum):
    """Pipeline run 状态。"""

    CREATED = "created"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"
    WAITING_APPROVAL = "waiting_approval"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SkillPipelineNode(BaseModel):
    """Deterministic pipeline 节点定义。"""

    node_id: str = Field(min_length=1)
    label: str = Field(default="")
    node_type: PipelineNodeType
    handler_id: str = Field(min_length=1)
    next_node_id: str | None = None
    retry_limit: int = Field(default=0, ge=0, le=20)
    timeout_seconds: float | None = Field(default=None, ge=0.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SkillPipelineDefinition(BaseModel):
    """Pipeline definition。"""

    pipeline_id: str = Field(min_length=1)
    label: str = Field(default="")
    version: str = Field(default="1.0.0")
    entry_node_id: str = Field(min_length=1)
    nodes: list[SkillPipelineNode] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_entry_node(self) -> SkillPipelineDefinition:
        node_ids = {node.node_id for node in self.nodes}
        if self.entry_node_id not in node_ids:
            raise ValueError(f"entry_node_id 不存在: {self.entry_node_id}")
        return self

    def get_node(self, node_id: str) -> SkillPipelineNode:
        for node in self.nodes:
            if node.node_id == node_id:
                return node
        raise KeyError(node_id)


class SkillPipelineRun(BaseModel):
    """Pipeline run durable state。"""

    run_id: str = Field(min_length=1)
    pipeline_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    work_id: str = Field(min_length=1)
    status: PipelineRunStatus = PipelineRunStatus.CREATED
    current_node_id: str = Field(default="")
    pause_reason: str = Field(default="")
    retry_cursor: dict[str, int] = Field(default_factory=dict)
    state_snapshot: dict[str, Any] = Field(default_factory=dict)
    input_request: dict[str, Any] = Field(default_factory=dict)
    approval_request: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    completed_at: datetime | None = None


class PipelineCheckpoint(BaseModel):
    """Pipeline 节点 checkpoint。"""

    checkpoint_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    node_id: str = Field(min_length=1)
    status: PipelineRunStatus
    state_snapshot: dict[str, Any] = Field(default_factory=dict)
    side_effect_cursor: str | None = None
    replay_summary: str = Field(default="")
    retry_count: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)


class PipelineReplayFrame(BaseModel):
    """回放视图帧。"""

    frame_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    node_id: str = Field(min_length=1)
    status: PipelineRunStatus
    summary: str = Field(default="")
    checkpoint_id: str = Field(default="")
    ts: datetime = Field(default_factory=_utc_now)
