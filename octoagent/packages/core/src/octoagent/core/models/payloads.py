"""Event Payload 子类型 -- 对齐 spec FR-M0-DM-3

所有事件的结构化 payload 定义。
"""

from pydantic import BaseModel, Field

from .enums import TaskStatus


class TaskCreatedPayload(BaseModel):
    """TASK_CREATED 事件 payload"""

    title: str
    thread_id: str
    scope_id: str
    channel: str
    sender_id: str
    risk_level: str = Field(default="low", description="任务风险等级")


class UserMessagePayload(BaseModel):
    """USER_MESSAGE 事件 payload"""

    text_preview: str = Field(description="消息预览（截断到 200 字符）")
    text_length: int = Field(description="原始文本长度")
    attachment_count: int = Field(default=0)


class ModelCallStartedPayload(BaseModel):
    """MODEL_CALL_STARTED 事件 payload"""

    model_alias: str = Field(description="模型别名")
    request_summary: str = Field(description="请求摘要")
    artifact_ref: str | None = Field(default=None, description="完整请求的 Artifact 引用")


class ModelCallCompletedPayload(BaseModel):
    """MODEL_CALL_COMPLETED 事件 payload -- Feature 002 扩展

    新增字段均有默认值，确保 M0 旧事件可正常反序列化。
    """

    # M0 已有字段
    model_alias: str
    response_summary: str = Field(description="响应摘要（超过 8KB 截断）")
    duration_ms: int = Field(description="调用耗时（毫秒）")
    token_usage: dict[str, int] = Field(
        default_factory=dict,
        description="Token 用量（prompt_tokens/completion_tokens/total_tokens）",
    )
    artifact_ref: str | None = Field(default=None, description="完整响应的 Artifact 引用")

    # Feature 002 新增字段（全部有默认值，M0 向后兼容）
    model_name: str = Field(default="", description="实际调用的模型名称")
    provider: str = Field(default="", description="实际 provider 名称")
    cost_usd: float = Field(default=0.0, description="本次调用的 USD 成本")
    cost_unavailable: bool = Field(
        default=False,
        description="成本数据是否不可用",
    )
    is_fallback: bool = Field(default=False, description="是否为降级调用")


class ModelCallFailedPayload(BaseModel):
    """MODEL_CALL_FAILED 事件 payload -- Feature 002 扩展"""

    # M0 已有字段
    model_alias: str
    error_type: str
    error_message: str
    duration_ms: int

    # Feature 002 新增字段（全部有默认值，M0 向后兼容）
    model_name: str = Field(default="", description="尝试调用的模型名称")
    provider: str = Field(default="", description="尝试使用的 provider")
    is_fallback: bool = Field(default=False, description="失败时是否已在降级模式")


class StateTransitionPayload(BaseModel):
    """STATE_TRANSITION 事件 payload"""

    from_status: TaskStatus
    to_status: TaskStatus
    reason: str = Field(default="")


class ArtifactCreatedPayload(BaseModel):
    """ARTIFACT_CREATED 事件 payload"""

    artifact_id: str
    name: str
    size: int
    part_count: int


class ErrorPayload(BaseModel):
    """ERROR 事件 payload"""

    error_type: str = Field(description="错误分类：model/tool/system/business")
    error_message: str
    recoverable: bool = Field(default=False)
    recovery_hint: str = Field(default="")


# Feature 008: Orchestrator 控制平面 Payload 类型


class OrchestratorDecisionPayload(BaseModel):
    """ORCH_DECISION 事件 payload。"""

    contract_version: str = Field(description="派发协议版本")
    route_reason: str = Field(description="路由理由")
    worker_capability: str = Field(description="目标 worker 能力")
    hop_count: int = Field(description="当前跳数")
    max_hops: int = Field(description="最大跳数")
    gate_decision: str = Field(description="门禁决策: allow/deny")
    gate_reason: str = Field(default="", description="门禁决策说明")


class WorkerDispatchedPayload(BaseModel):
    """WORKER_DISPATCHED 事件 payload。"""

    dispatch_id: str = Field(description="派发 ID")
    worker_id: str = Field(description="worker 标识")
    worker_capability: str = Field(description="worker 能力")
    contract_version: str = Field(description="派发协议版本")


class WorkerReturnedPayload(BaseModel):
    """WORKER_RETURNED 事件 payload。"""

    dispatch_id: str = Field(description="派发 ID")
    worker_id: str = Field(description="worker 标识")
    status: str = Field(description="worker 返回状态")
    retryable: bool = Field(description="失败是否可重试")
    summary: str = Field(description="执行摘要")
    error_type: str = Field(default="", description="错误类型")
    error_message: str = Field(default="", description="错误信息")


# Feature 004: 工具调用 Payload 类型 -- 对齐 FR-014


class ToolCallStartedPayload(BaseModel):
    """TOOL_CALL_STARTED 事件 payload -- 对齐 spec FR-014"""

    tool_name: str = Field(description="工具名称")
    tool_group: str = Field(description="工具分组")
    side_effect_level: str = Field(description="副作用等级")
    args_summary: str = Field(description="参数摘要（脱敏后）")
    timeout_seconds: float | None = Field(
        default=None,
        description="声明式超时",
    )


class ToolCallCompletedPayload(BaseModel):
    """TOOL_CALL_COMPLETED 事件 payload -- 对齐 spec FR-014"""

    tool_name: str = Field(description="工具名称")
    duration_ms: int = Field(description="执行耗时（毫秒）")
    output_summary: str = Field(description="输出摘要（脱敏后）")
    truncated: bool = Field(
        default=False,
        description="输出是否被裁切",
    )
    artifact_ref: str | None = Field(
        default=None,
        description="完整输出的 Artifact 引用",
    )


class ToolCallFailedPayload(BaseModel):
    """TOOL_CALL_FAILED 事件 payload -- 对齐 spec FR-014"""

    tool_name: str = Field(description="工具名称")
    duration_ms: int = Field(description="执行耗时（毫秒）")
    error_type: str = Field(
        description="错误分类（timeout / exception / rejection / hook_failure）"
    )
    error_message: str = Field(description="错误信息（脱敏后）")
    recoverable: bool = Field(
        default=False,
        description="是否可恢复",
    )
    recovery_hint: str = Field(
        default="",
        description="恢复建议",
    )
