"""Checkpoint/Resume 领域模型 -- 对齐 Feature 010 FR-001/FR-011"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class CheckpointStatus(StrEnum):
    """Checkpoint 状态机"""

    CREATED = "created"
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"


class ResumeFailureType(StrEnum):
    """恢复失败分类"""

    SNAPSHOT_CORRUPT = "snapshot_corrupt"
    VERSION_MISMATCH = "version_mismatch"
    LEASE_CONFLICT = "lease_conflict"
    DEPENDENCY_MISSING = "dependency_missing"
    TERMINAL_TASK = "terminal_task"
    UNKNOWN = "unknown"


class CheckpointSnapshot(BaseModel):
    """节点级快照"""

    checkpoint_id: str = Field(description="checkpoint 唯一标识（ULID）")
    task_id: str = Field(description="任务 ID")
    node_id: str = Field(description="节点标识")
    status: CheckpointStatus = Field(description="checkpoint 状态")
    schema_version: int = Field(default=1, description="快照 schema 版本")
    state_snapshot: dict[str, Any] = Field(
        default_factory=dict,
        description="可恢复状态快照",
    )
    side_effect_cursor: str | None = Field(default=None, description="副作用游标")
    created_at: datetime = Field(description="创建时间")
    updated_at: datetime = Field(description="更新时间")


class ResumeAttempt(BaseModel):
    """恢复尝试记录（审计视角）"""

    attempt_id: str = Field(description="恢复尝试 ID（ULID）")
    task_id: str = Field(description="任务 ID")
    checkpoint_id: str | None = Field(default=None, description="使用的 checkpoint ID")
    trigger: str = Field(default="startup", description="触发来源：startup/manual/retry")
    status: str = Field(description="started/succeeded/failed")
    failure_type: ResumeFailureType | None = Field(default=None, description="失败类型")
    failure_message: str = Field(default="", description="失败信息")
    started_at: datetime = Field(description="开始时间")
    finished_at: datetime | None = Field(default=None, description="结束时间")


class ResumeResult(BaseModel):
    """恢复执行结果"""

    ok: bool = Field(description="是否恢复成功")
    task_id: str = Field(description="任务 ID")
    checkpoint_id: str | None = Field(default=None, description="使用的 checkpoint")
    resumed_from_node: str | None = Field(default=None, description="恢复基线节点")
    failure_type: ResumeFailureType | None = Field(default=None, description="失败类型")
    message: str = Field(description="结果说明")
    state_snapshot: dict[str, Any] | None = Field(default=None, description="快照内容")


class SideEffectLedgerEntry(BaseModel):
    """副作用幂等账本记录"""

    ledger_id: str = Field(description="账本记录 ID（ULID）")
    task_id: str = Field(description="任务 ID")
    step_key: str = Field(description="步骤键（node_id/tool_call_id）")
    idempotency_key: str = Field(description="幂等键")
    effect_type: str = Field(description="副作用类型")
    result_ref: str | None = Field(default=None, description="结果引用（artifact_id）")
    created_at: datetime = Field(description="创建时间")
