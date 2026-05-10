"""Feature 030: Work / Delegation 领域模型。"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from .capability import RuntimeKind
from .enums import TaskStatus
from .orchestrator import TurnExecutorKind, WorkerRuntimeState


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


# ============================================================
# F091: 跨枚举状态映射函数
#
# 设计原则：
# - Work > Task ⊃ ExecutorState（嵌套关系，但每个枚举语义独立保留）
# - 各枚举的终态集合（TERMINAL_STATES / WORK_TERMINAL_STATUSES）就近定义不动
# - 通过 module-level dict + 函数提供单向映射；下层 → 上层是单射安全
#   （worker→work, task→work），上层 → 下层（work→task）需限制安全范围
# - 沿用 pipeline_tool.py 已有的 _PIPELINE_TO_TASK_STATUS / _PIPELINE_TO_WORK_STATUS pattern
# - 映射函数本身不修改运行时行为；仅供后续 Feature（F092 / F093）按需引用
#
# 安全约束（Codex H1 / M1 / M2 / M3 闭环）：
# - WorkStatus.MERGED / ESCALATED / DELETED 是"前置状态依赖"状态：
#   * MERGED 可从 CREATED/ASSIGNED/RUNNING 任意进入（未执行 vs 执行后合并语义不同）
#   * ESCALATED 是"可 retry 终态"（升级处理后可能 retry → 不应压扁为 FAILED）
#   * DELETED 可从任意终态进入（保留前置终态信息）
#   → 这 3 个状态在 work_status_to_task_status() 中显式 raise ValueError，
#     强制调用方根据 previous_status 自行决议
# - WorkStatus.ASSIGNED → TaskStatus.RUNNING（不映射到 QUEUED；后者是 M1+ 预留死状态无出边）
# - WorkerRuntimeState.PENDING → 直接路径 RUNNING / 组合路径 PENDING→ASSIGNED→RUNNING 一致
# ============================================================


# WorkStatus 子集：投影到 TaskStatus 时需要前置状态上下文（不可单步映射）
WORK_STATUSES_REQUIRING_CONTEXT: frozenset[WorkStatus] = frozenset({
    WorkStatus.MERGED,
    WorkStatus.ESCALATED,
    WorkStatus.DELETED,
})


TASK_TO_WORK_STATUS: dict[TaskStatus, WorkStatus] = {
    TaskStatus.CREATED: WorkStatus.CREATED,
    TaskStatus.QUEUED: WorkStatus.ASSIGNED,
    TaskStatus.RUNNING: WorkStatus.RUNNING,
    TaskStatus.WAITING_INPUT: WorkStatus.WAITING_INPUT,
    TaskStatus.WAITING_APPROVAL: WorkStatus.WAITING_APPROVAL,
    TaskStatus.PAUSED: WorkStatus.PAUSED,
    TaskStatus.SUCCEEDED: WorkStatus.SUCCEEDED,
    TaskStatus.FAILED: WorkStatus.FAILED,
    TaskStatus.CANCELLED: WorkStatus.CANCELLED,
    # task rejection（用户拒绝审批）从 work 角度看是失败
    TaskStatus.REJECTED: WorkStatus.FAILED,
}


# WorkStatus → TaskStatus 安全子集（不含 MERGED / ESCALATED / DELETED；这 3 个由
# work_status_to_task_status() 函数显式 raise）。
# ASSIGNED → RUNNING（避免 task QUEUED 死状态）。
WORK_TO_TASK_STATUS: dict[WorkStatus, TaskStatus] = {
    WorkStatus.CREATED: TaskStatus.CREATED,
    WorkStatus.ASSIGNED: TaskStatus.RUNNING,
    WorkStatus.RUNNING: TaskStatus.RUNNING,
    WorkStatus.WAITING_INPUT: TaskStatus.WAITING_INPUT,
    WorkStatus.WAITING_APPROVAL: TaskStatus.WAITING_APPROVAL,
    WorkStatus.PAUSED: TaskStatus.PAUSED,
    WorkStatus.SUCCEEDED: TaskStatus.SUCCEEDED,
    WorkStatus.FAILED: TaskStatus.FAILED,
    WorkStatus.CANCELLED: TaskStatus.CANCELLED,
    # work timeout 从 task 角度看是失败
    WorkStatus.TIMED_OUT: TaskStatus.FAILED,
    # MERGED / ESCALATED / DELETED：刻意不放——见 work_status_to_task_status()
}


WORKER_TO_WORK_STATUS: dict[WorkerRuntimeState, WorkStatus] = {
    WorkerRuntimeState.PENDING: WorkStatus.ASSIGNED,
    WorkerRuntimeState.RUNNING: WorkStatus.RUNNING,
    WorkerRuntimeState.SUCCEEDED: WorkStatus.SUCCEEDED,
    WorkerRuntimeState.FAILED: WorkStatus.FAILED,
    WorkerRuntimeState.CANCELLED: WorkStatus.CANCELLED,
    WorkerRuntimeState.TIMED_OUT: WorkStatus.TIMED_OUT,
}


# WorkerRuntimeState → TaskStatus 与"组合路径 worker→work→task"必须一致：
# - PENDING：直接 RUNNING / 组合 PENDING→ASSIGNED→RUNNING（一致，因 ASSIGNED→RUNNING）
# - RUNNING：直接 RUNNING / 组合 RUNNING→RUNNING→RUNNING（一致）
# - SUCCEEDED/FAILED/CANCELLED：双射等价
# - TIMED_OUT：直接 FAILED / 组合 TIMED_OUT→TIMED_OUT→FAILED（一致）
WORKER_TO_TASK_STATUS: dict[WorkerRuntimeState, TaskStatus] = {
    WorkerRuntimeState.PENDING: TaskStatus.RUNNING,
    WorkerRuntimeState.RUNNING: TaskStatus.RUNNING,
    WorkerRuntimeState.SUCCEEDED: TaskStatus.SUCCEEDED,
    WorkerRuntimeState.FAILED: TaskStatus.FAILED,
    WorkerRuntimeState.CANCELLED: TaskStatus.CANCELLED,
    WorkerRuntimeState.TIMED_OUT: TaskStatus.FAILED,
}


def task_status_to_work_status(status: TaskStatus) -> WorkStatus:
    """TaskStatus → WorkStatus（task 视角抬升到 work 视角；单射）。"""
    return TASK_TO_WORK_STATUS[status]


def work_status_to_task_status(status: WorkStatus) -> TaskStatus:
    """WorkStatus → TaskStatus（work 视角降到 task 视角；多对一）。

    安全约束：MERGED / ESCALATED / DELETED 这 3 个状态是"前置依赖"状态，
    无法在不知道 previous_status 的情况下决定 task outcome：
    - MERGED 可从未执行（CREATED/ASSIGNED）合并 vs 执行后（RUNNING）合并；语义不同
    - ESCALATED 是 work 可 retry 终态（保留 → CREATED 路径），压扁为 task FAILED 会丢失 retry 语义
    - DELETED 可从任意终态（SUCCEEDED/FAILED/CANCELLED）进入；强行映射会覆盖原终态

    调用方应根据自身上下文（如 previous_status / deletion_reason）显式决议。
    """
    if status in WORK_STATUSES_REQUIRING_CONTEXT:
        raise ValueError(
            f"WorkStatus.{status.name} 投影到 TaskStatus 需要前置状态上下文。"
            f"该状态不在 WORK_TO_TASK_STATUS 单步映射中——调用方应根据 previous_status 自行决议。"
        )
    return WORK_TO_TASK_STATUS[status]


def worker_state_to_work_status(state: WorkerRuntimeState) -> WorkStatus:
    """WorkerRuntimeState → WorkStatus（单射）。"""
    return WORKER_TO_WORK_STATUS[state]


def worker_state_to_task_status(state: WorkerRuntimeState) -> TaskStatus:
    """WorkerRuntimeState → TaskStatus（直接路径与组合路径一致）。

    保证：worker_state_to_task_status(s) ==
          work_status_to_task_status(worker_state_to_work_status(s))
    （TIMED_OUT 是唯一通过 WorkStatus.TIMED_OUT → TaskStatus.FAILED 的中转）
    """
    return WORKER_TO_TASK_STATUS[state]


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


class SubagentDelegation(BaseModel):
    """F097：H3-A 临时 Subagent 委托的结构化载体。

    生命周期从 spawn（created_at 填充）到 close（closed_at 填充）。
    持久化路径：child_task.metadata["subagent_delegation"]（JSON 序列化，无独立 SQL 表）。
    F098 扩展点：WorkerDelegation 为独立 model（target_kind=WORKER），两者共有字段命名
    遵循可派生惯例（delegation_id / parent_task_id / created_at 等），未来可提取 BaseDelegation。
    """

    delegation_id: str = Field(..., min_length=1)
    """ULID 生成的唯一委托 ID（min_length=1，与 Work/DelegationEnvelope 一致 - Codex P2-2）。"""

    parent_task_id: str = Field(..., min_length=1)
    """发起委托的父任务 ID。"""

    parent_work_id: str = Field(..., min_length=1)
    """父任务对应的 work ID。"""

    child_task_id: str = Field(..., min_length=1)
    """被委托的子任务 ID。"""

    child_agent_session_id: str | None = None
    """Subagent 的 SUBAGENT_INTERNAL AgentSession ID（GATE_DESIGN C-1）。
    spawn 失败或 session 尚未创建时为 None；cleanup hook 通过此字段直接定位。"""

    caller_agent_runtime_id: str = Field(..., min_length=1)
    """调用方 Agent 的 runtime ID，用于 Memory / Context 共享。"""

    caller_project_id: str = Field(..., min_length=1)
    """调用方 Project ID，用于 audit 和过滤。"""

    caller_memory_namespace_ids: list[str] = Field(default_factory=list)
    """共享的 Memory namespace ID 集合（OD-1 α 语义：直接复用 caller 的 AGENT_PRIVATE namespace）。"""

    spawned_by: str = Field(..., min_length=1)
    """spawn 来源工具名称，如 delegate_task / subagents.spawn。"""

    target_kind: Literal[DelegationTargetKind.SUBAGENT] = DelegationTargetKind.SUBAGENT
    """委托目标类型，固定为 SUBAGENT（Codex P2-1：Literal 防止反序列化时接受非 SUBAGENT 值）。
    F098 WorkerDelegation 将作为独立 model（Literal[WORKER]），与本 model 边界互斥。"""

    created_at: datetime
    """委托创建时间（UTC）。"""

    closed_at: datetime | None = None
    """委托关闭时间（UTC）。None 表示委托仍活跃。"""
