"""Feature 030: Work / Delegation 领域模型。"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from .capability import RuntimeKind
from .orchestrator import TurnExecutorKind


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


class WorkKind(StrEnum):
    """Work 业务类型。"""

    TASK = "task"
    DELEGATION = "delegation"
    PIPELINE = "pipeline"
    RECOVERY = "recovery"


class WorkStatus(StrEnum):
    """Work 生命周期。"""

    CREATED = "created"
    ASSIGNED = "assigned"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"
    WAITING_APPROVAL = "waiting_approval"
    PAUSED = "paused"
    MERGED = "merged"
    ESCALATED = "escalated"
    TIMED_OUT = "timed_out"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DELETED = "deleted"


# ============================================================
# Work 状态转换规则
# ============================================================

VALID_WORK_TRANSITIONS: dict[WorkStatus, set[WorkStatus]] = {
    # 活跃状态
    WorkStatus.CREATED: {
        WorkStatus.ASSIGNED, WorkStatus.RUNNING,
        WorkStatus.FAILED, WorkStatus.CANCELLED, WorkStatus.MERGED, WorkStatus.DELETED,
    },
    WorkStatus.ASSIGNED: {
        WorkStatus.RUNNING, WorkStatus.CANCELLED,
        WorkStatus.MERGED, WorkStatus.DELETED, WorkStatus.FAILED,
        WorkStatus.ESCALATED, WorkStatus.TIMED_OUT,
    },
    WorkStatus.RUNNING: {
        WorkStatus.CREATED,  # pipeline SUCCEEDED → 就绪待 dispatch
        WorkStatus.SUCCEEDED, WorkStatus.FAILED, WorkStatus.CANCELLED,
        WorkStatus.WAITING_INPUT, WorkStatus.WAITING_APPROVAL,
        WorkStatus.PAUSED, WorkStatus.TIMED_OUT, WorkStatus.ESCALATED,
    },
    WorkStatus.WAITING_INPUT: {
        WorkStatus.RUNNING, WorkStatus.CANCELLED, WorkStatus.TIMED_OUT,
    },
    WorkStatus.WAITING_APPROVAL: {
        WorkStatus.RUNNING, WorkStatus.CANCELLED, WorkStatus.TIMED_OUT,
    },
    WorkStatus.PAUSED: {WorkStatus.RUNNING, WorkStatus.CANCELLED},
    # 可 retry 终态（允许 → CREATED 重新派发，→ DELETED 清理）
    WorkStatus.FAILED: {WorkStatus.CREATED, WorkStatus.DELETED},
    WorkStatus.CANCELLED: {WorkStatus.CREATED, WorkStatus.DELETED},
    WorkStatus.ESCALATED: {WorkStatus.CREATED, WorkStatus.DELETED},
    WorkStatus.TIMED_OUT: {WorkStatus.CREATED, WorkStatus.DELETED},
    # 纯终态（仅允许 → DELETED 清理）
    WorkStatus.SUCCEEDED: {WorkStatus.DELETED},
    WorkStatus.MERGED: {WorkStatus.DELETED},
    # DELETED 是最终态
    WorkStatus.DELETED: set(),
}

WORK_TERMINAL_STATUSES: frozenset[WorkStatus] = frozenset({
    WorkStatus.SUCCEEDED, WorkStatus.FAILED, WorkStatus.CANCELLED,
    WorkStatus.MERGED, WorkStatus.ESCALATED, WorkStatus.TIMED_OUT, WorkStatus.DELETED,
})
# ESCALATED 纳入终态：升级意味着当前执行结束，交给更高层处理。
# 如需 retry，走 ESCALATED → CREATED 路径。


def validate_work_transition(from_status: WorkStatus, to_status: WorkStatus) -> bool:
    """验证 Work 状态流转是否合法。

    对齐 TaskStatus 的 validate_transition() 模式。
    """
    return to_status in VALID_WORK_TRANSITIONS.get(from_status, set())


class WorkTransitionError(ValueError):
    """非法 Work 状态转换。

    继承 ValueError 保持向后兼容，同时允许调用方按需 catch 更精确的异常。
    """

    def __init__(self, work_id: str, from_status: WorkStatus, to_status: WorkStatus, context: str = ""):
        self.work_id = work_id
        self.from_status = from_status
        self.to_status = to_status
        msg = f"非法 Work 状态转换: {work_id} {from_status.value} → {to_status.value}"
        if context:
            msg += f" ({context})"
        super().__init__(msg)


class DelegationTargetKind(StrEnum):
    """统一委派目标类型。"""

    WORKER = "worker"
    SUBAGENT = "subagent"
    ACP_RUNTIME = "acp_runtime"
    GRAPH_AGENT = "graph_agent"
    FALLBACK = "fallback"


class Work(BaseModel):
    """主 Agent 的 delegation unit。"""

    work_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    parent_work_id: str | None = None
    title: str = Field(default="")
    kind: WorkKind = WorkKind.DELEGATION
    status: WorkStatus = WorkStatus.CREATED
    target_kind: DelegationTargetKind = DelegationTargetKind.WORKER
    owner_id: str = Field(default="")
    requested_capability: str = Field(default="")
    selected_worker_type: str = "general"
    route_reason: str = Field(default="")
    project_id: str = Field(default="")
    session_owner_profile_id: str = Field(default="")
    inherited_context_owner_profile_id: str = Field(default="")
    delegation_target_profile_id: str = Field(default="")
    turn_executor_kind: TurnExecutorKind = Field(default=TurnExecutorKind.WORKER)
    agent_profile_id: str = Field(default="")
    requested_worker_profile_id: str = Field(default="")
    requested_worker_profile_version: int = Field(default=0, ge=0)
    effective_worker_snapshot_id: str = Field(default="")
    context_frame_id: str = Field(default="")
    tool_selection_id: str = Field(default="")
    selected_tools: list[str] = Field(default_factory=list)
    pipeline_run_id: str = Field(default="")
    delegation_id: str = Field(default="")
    runtime_id: str = Field(default="")
    retry_count: int = Field(default=0, ge=0)
    escalation_count: int = Field(default=0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    completed_at: datetime | None = None


class DelegationEnvelope(BaseModel):
    """统一 delegation envelope。"""

    delegation_id: str = Field(min_length=1)
    work_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    target_kind: DelegationTargetKind
    runtime_kind: RuntimeKind = RuntimeKind.WORKER
    requested_capability: str = Field(default="")
    payload: dict[str, Any] = Field(default_factory=dict)
    route_reason: str = Field(default="")
    selected_worker_type: str = "general"
    bootstrap_context: list[dict[str, Any]] = Field(default_factory=list)
    selected_tools: list[str] = Field(default_factory=list)
    project_id: str = Field(default="")
    timeout_seconds: float | None = Field(default=None, ge=0.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DelegationResult(BaseModel):
    """统一 delegation 结果。"""

    delegation_id: str = Field(min_length=1)
    work_id: str = Field(min_length=1)
    status: WorkStatus
    summary: str = Field(default="")
    retryable: bool = False
    runtime_id: str = Field(default="")
    target_kind: DelegationTargetKind
    worker_type: str = "general"
    route_reason: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)
