"""Orchestrator 领域模型。

Feature 008: 冻结控制平面契约，支持后续多 Worker 扩展。
"""

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

from .enums import RiskLevel


class WorkerExecutionStatus(StrEnum):
    """Worker 执行状态。"""

    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


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
    metadata: dict[str, str] = Field(default_factory=dict, description="扩展元数据")

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
    status: WorkerExecutionStatus = Field(description="执行状态")
    retryable: bool = Field(description="失败是否可重试")
    summary: str = Field(description="结果摘要")
    error_type: str | None = Field(default=None, description="错误类型")
    error_message: str | None = Field(default=None, description="错误详情")
