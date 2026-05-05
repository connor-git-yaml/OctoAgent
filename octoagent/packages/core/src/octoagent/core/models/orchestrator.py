"""Orchestrator 领域模型。

Feature 008: 冻结控制平面契约，支持后续多 Worker 扩展。
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from .enums import RiskLevel, TaskStatus


class WorkerRuntimeState(StrEnum):
    """Worker Runtime 状态。"""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"


class TurnExecutorKind(StrEnum):
    """当前轮次执行者语义。"""

    SELF = "self"
    WORKER = "worker"
    SUBAGENT = "subagent"


class RuntimeControlContext(BaseModel):
    """一次运行链路的冻结控制上下文。"""

    task_id: str = Field(description="任务 ID")
    trace_id: str = Field(default="", description="链路追踪 ID")
    contract_version: str = Field(default="1.0", description="协议版本")
    surface: str = Field(default="chat", description="入口 surface")
    scope_id: str = Field(default="", description="原始 scope_id")
    thread_id: str = Field(default="", description="线程 ID")
    session_id: str = Field(default="", description="冻结后的 durable session ID")
    project_id: str = Field(default="", description="冻结后的 project ID")
    hop_count: int = Field(default=0, ge=0, description="当前 hop")
    max_hops: int = Field(default=3, ge=1, description="最大 hop")
    worker_capability: str = Field(default="", description="当前 worker capability")
    route_reason: str = Field(default="", description="当前 route reason")
    model_alias: str = Field(default="", description="模型别名")
    tool_profile: str = Field(default="standard", description="工具权限级别")
    work_id: str = Field(default="", description="work ID")
    parent_work_id: str = Field(default="", description="父 work ID")
    pipeline_run_id: str = Field(default="", description="pipeline run ID")
    session_owner_profile_id: str = Field(default="", description="当前会话 owner profile ID")
    inherited_context_owner_profile_id: str = Field(
        default="",
        description="继承上下文的 owner profile ID",
    )
    delegation_target_profile_id: str = Field(
        default="",
        description="本轮显式 delegation target profile ID",
    )
    turn_executor_kind: TurnExecutorKind = Field(
        default=TurnExecutorKind.SELF,
        description="当前轮次执行者语义",
    )
    agent_profile_id: str = Field(
        default="",
        description="legacy effective agent profile ID（兼容字段，默认镜像 session owner）",
    )
    context_frame_id: str = Field(default="", description="继承/消费的 context frame ID")
    metadata: dict[str, Any] = Field(default_factory=dict, description="附加上下文")


class OrchestratorRequest(BaseModel):
    """控制平面入口请求。"""

    task_id: str = Field(description="任务 ID")
    trace_id: str = Field(description="链路追踪 ID")
    user_text: str = Field(description="用户输入文本")
    risk_level: RiskLevel = Field(default=RiskLevel.LOW, description="任务风险等级")

    contract_version: str = Field(default="1.0", description="协议版本")
    worker_capability: str = Field(
        default="llm_generation",
        description="目标 worker 能力标签",
    )
    route_reason: str = Field(default="", description="路由理由")
    hop_count: int = Field(default=0, ge=0, description="当前跳数")
    max_hops: int = Field(default=3, ge=1, description="最大跳数")
    model_alias: str | None = Field(default=None, description="模型别名")
    resume_from_node: str | None = Field(default=None, description="恢复起点节点 ID")
    resume_state_snapshot: dict[str, Any] | None = Field(
        default=None,
        description="恢复时注入的状态快照",
    )
    tool_profile: str = Field(default="standard", description="工具权限级别")
    runtime_context: RuntimeControlContext | None = Field(
        default=None,
        description="冻结后的运行时控制上下文",
    )
    metadata: dict[str, Any] = Field(default_factory=dict, description="扩展元数据")

    @model_validator(mode="after")
    def _validate_hops(self) -> "OrchestratorRequest":
        if self.hop_count > self.max_hops:
            raise ValueError(
                f"hop_count({self.hop_count}) cannot exceed max_hops({self.max_hops})"
            )
        return self


class DispatchEnvelope(BaseModel):
    """派发信封。"""

    dispatch_id: str = Field(description="派发 ID")
    task_id: str = Field(description="任务 ID")
    trace_id: str = Field(description="链路追踪 ID")

    contract_version: str = Field(default="1.0", description="协议版本")
    route_reason: str = Field(description="路由理由")
    worker_capability: str = Field(description="目标 worker 能力标签")
    hop_count: int = Field(ge=0, description="当前跳数")
    max_hops: int = Field(ge=1, description="最大跳数")

    user_text: str = Field(description="用户输入文本")
    model_alias: str | None = Field(default=None, description="模型别名")
    resume_from_node: str | None = Field(default=None, description="恢复起点节点 ID")
    resume_state_snapshot: dict[str, Any] | None = Field(
        default=None,
        description="恢复时注入的状态快照",
    )
    tool_profile: str = Field(default="standard", description="工具权限级别")
    runtime_context: RuntimeControlContext | None = Field(
        default=None,
        description="冻结后的运行时控制上下文",
    )
    metadata: dict[str, Any] = Field(default_factory=dict, description="扩展元数据")

    @model_validator(mode="after")
    def _validate_hops(self) -> "DispatchEnvelope":
        if self.hop_count > self.max_hops:
            raise ValueError(
                f"hop_count({self.hop_count}) cannot exceed max_hops({self.max_hops})"
            )
        return self


class WorkerResult(BaseModel):
    """Worker 回传结果。"""

    dispatch_id: str = Field(description="派发 ID")
    task_id: str = Field(description="任务 ID")
    worker_id: str = Field(description="worker 标识")
    status: TaskStatus = Field(description="执行状态")
    retryable: bool = Field(description="失败是否可重试")
    summary: str = Field(description="结果摘要")
    error_type: str | None = Field(default=None, description="错误类型")
    error_message: str | None = Field(default=None, description="错误详情")
    loop_step: int = Field(default=0, ge=0, description="执行步数")
    max_steps: int = Field(default=0, ge=0, description="最大执行步数")
    backend: str = Field(default="inline", description="执行后端")
    tool_profile: str = Field(default="standard", description="工具权限级别")


class WorkerDispatchState(BaseModel):
    """Worker Runtime 单次 dispatch 的瞬时状态计数器。

    Feature 090: 从 ``WorkerSession`` 重命名以消除与 ``AgentSession``（持久化长期会话）
    的命名歧义。本对象不持久化，生命期等于一次 dispatch。
    """

    session_id: str = Field(description="会话 ID")
    dispatch_id: str = Field(description="派发 ID")
    task_id: str = Field(description="任务 ID")
    worker_id: str = Field(description="Worker 标识")
    state: WorkerRuntimeState = Field(default=WorkerRuntimeState.PENDING, description="运行状态")

    loop_step: int = Field(default=0, ge=0, description="当前执行步数")
    max_steps: int = Field(default=3, ge=1, description="最大执行步数")
    budget_exhausted: bool = Field(default=False, description="预算是否耗尽")

    tool_profile: str = Field(default="standard", description="工具权限级别")
    backend: str = Field(default="inline", description="执行后端")

    @model_validator(mode="after")
    def _validate_loop(self) -> "WorkerDispatchState":
        if self.loop_step > self.max_steps:
            raise ValueError(
                f"loop_step({self.loop_step}) cannot exceed max_steps({self.max_steps})"
            )
        return self
