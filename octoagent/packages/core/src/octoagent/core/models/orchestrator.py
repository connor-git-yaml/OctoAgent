"""Orchestrator 领域模型。

Feature 008: 冻结控制平面契约，支持后续多 Worker 扩展。
"""

from enum import StrEnum
from typing import Any, Literal

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


# Feature 090 D1: 显式化派发模式枚举值（替代 metadata["single_loop_executor"] 隐式 flag）
# - "main_inline": 主 Agent 自跑（原 single_loop_executor=True 的主路径）
# - "main_delegate": 主 Agent 派给 worker（原 single_loop_executor=False / 标准 Delegation）
# - "worker_inline": worker 自跑（原 worker 端 single_loop 路径）
# - "subagent": Subagent 临时执行（F097 启用）
# - "unspecified": 显式表示尚未由 _prepare_single_loop_request 决策（默认值，兼容期）
DelegationMode = Literal[
    "unspecified",
    "main_inline",
    "main_delegate",
    "worker_inline",
    "subagent",
]


# Feature 090 D1: 显式化 recall planner 行为（替代靠 metadata["single_loop_executor"] 推断）
# - "full": 跑完整 recall planner（默认）
# - "skip": 跳过 recall planner（原 single_loop_executor 主路径下的语义）
# - "auto": 由系统按 delegation_mode 推断（保留扩展位）
RecallPlannerMode = Literal["full", "skip", "auto"]


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
    delegation_mode: DelegationMode = Field(
        default="unspecified",
        description="本次派发的执行模式（F090 D1 显式化 metadata['single_loop_executor']）",
    )
    recall_planner_mode: RecallPlannerMode = Field(
        default="full",
        description="Recall planner 行为模式（F090 D1 显式化）",
    )
    # Feature 100 H1: override flag — 强制走完整 recall planner phase，
    # 覆盖 recall_planner_mode 的默认决议结果。
    # True → 始终 full（用于 H1 完整决策环——主 Agent 自跑长 context 复杂查询时设 True）
    # False（默认）→ 按 recall_planner_mode 正常决议（行为兼容 F091 baseline）
    # 上层 producer：orchestrator._prepare_single_loop_request 接受 metadata["force_full_recall"] hint
    # 上层 producer（潜在）：chat 路由 / API 参数 / 调试工具（推 F101 / 独立 Feature）
    force_full_recall: bool = Field(
        default=False,
        description=(
            "F100：H1 完整决策环 override flag。"
            "True 强制走完整 recall planner（覆盖 recall_planner_mode）；"
            "False（默认）按 recall_planner_mode 正常决议，行为兼容 F091 baseline。"
        ),
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
