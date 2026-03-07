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
    metadata: dict[str, str] = Field(default_factory=dict, description="渠道侧扩展元数据")


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
    loop_step: int = Field(default=0, description="执行步数")
    max_steps: int = Field(default=0, description="最大执行步数")
    backend: str = Field(default="inline", description="执行后端")
    tool_profile: str = Field(default="standard", description="工具权限级别")


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


# Feature 010: Checkpoint / Resume Payload 类型


class CheckpointSavedPayload(BaseModel):
    """CHECKPOINT_SAVED 事件 payload"""

    checkpoint_id: str = Field(description="checkpoint ID")
    node_id: str = Field(description="节点标识")
    schema_version: int = Field(default=1, description="checkpoint schema 版本")


class ResumeStartedPayload(BaseModel):
    """RESUME_STARTED 事件 payload"""

    attempt_id: str = Field(description="恢复尝试 ID")
    checkpoint_id: str | None = Field(default=None, description="checkpoint ID")
    trigger: str = Field(default="startup", description="触发来源")


class ResumeSucceededPayload(BaseModel):
    """RESUME_SUCCEEDED 事件 payload"""

    attempt_id: str = Field(description="恢复尝试 ID")
    resumed_from_node: str = Field(description="恢复起点节点")


class ResumeFailedPayload(BaseModel):
    """RESUME_FAILED 事件 payload"""

    attempt_id: str = Field(description="恢复尝试 ID")
    failure_type: str = Field(description="失败类型")
    failure_message: str = Field(description="失败信息")
    recovery_hint: str = Field(default="", description="恢复建议")


# Feature 011: Watchdog + Task Journal Payload 类型（FR-002, FR-003）


class TaskHeartbeatPayload(BaseModel):
    """TASK_HEARTBEAT 事件 payload（FR-003）

    Worker 在执行关键节点主动写入，用于 Watchdog 进度感知。
    写入时间戳由服务端 UTC 时间确定，不依赖客户端时间。
    """

    task_id: str = Field(description="任务 ID")
    trace_id: str = Field(description="关联 trace ID")
    heartbeat_ts: str = Field(description="心跳时间戳（UTC ISO 8601）")
    loop_step: int | None = Field(
        default=None,
        description="当前执行步骤编号（Free Loop 循环计数）",
    )
    note: str = Field(default="", description="心跳备注（可选摘要）")


class TaskMilestonePayload(BaseModel):
    """TASK_MILESTONE 事件 payload（FR-001）

    Worker 在完成重要阶段时主动写入，标记可观察的进展节点。
    """

    task_id: str = Field(description="任务 ID")
    trace_id: str = Field(description="关联 trace ID")
    milestone_name: str = Field(description="里程碑名称（如 'data_fetched'）")
    milestone_ts: str = Field(description="里程碑完成时间戳（UTC ISO 8601）")
    summary: str = Field(default="", description="里程碑完成摘要")
    artifact_ref: str | None = Field(
        default=None,
        description="关联产物引用（可选）",
    )


from typing import Literal  # noqa: E402

DriftType = Literal["no_progress", "state_machine_stall", "repeated_failure"]


class TaskDriftDetectedPayload(BaseModel):
    """TASK_DRIFT_DETECTED 事件 payload（FR-002, FR-019）

    Watchdog Scanner 检测到漂移时写入，payload 包含诊断摘要。
    详细诊断信息通过 artifact_ref 引用访问，不直接内联（Constitution 原则 11）。
    """

    # 必填诊断字段（FR-002）
    drift_type: DriftType = Field(
        description="漂移类型: no_progress / state_machine_stall / repeated_failure",
    )
    detected_at: str = Field(description="检测触发时间（UTC ISO 8601）")
    task_id: str = Field(description="被检测任务 ID")
    trace_id: str = Field(description="继承被检测任务的 trace_id")

    # 诊断时间字段
    last_progress_ts: str | None = Field(
        default=None,
        description="最近进展事件时间戳（UTC ISO 8601），无则为 None",
    )
    stall_duration_seconds: float = Field(
        description="卡死/驻留持续时长（秒）",
    )

    # 操作建议
    suggested_actions: list[str] = Field(
        description="可执行的建议动作列表（如 ['cancel_task', 'check_worker_logs']）",
    )

    # 详细诊断 artifact 引用（Context Hygiene，Constitution 原则 11）
    artifact_ref: str | None = Field(
        default=None,
        description="详细诊断信息的 Artifact 引用 ID，完整内容不内联于 payload",
    )

    # Logfire / OTel 预留字段（FR-021）
    # F012 接入前为空字符串占位，不填入真实 span_id
    watchdog_span_id: str = Field(
        default="",
        description="Watchdog 扫描 span_id（F012 接入前为空字符串占位）",
    )

    # 重复失败模式专属字段（drift_type == 'repeated_failure' 时有值）
    failure_count: int | None = Field(
        default=None,
        description="时间窗口内失败事件次数（重复失败模式专属）",
    )
    failure_event_types: list[str] = Field(
        default_factory=list,
        description="失败事件类型统计列表（重复失败模式专属）",
    )

    # 状态机漂移专属字段（drift_type == 'state_machine_stall' 时有值）
    current_status: str | None = Field(
        default=None,
        description="当前任务状态名称（状态机漂移模式专属，使用内部完整 TaskStatus）",
    )
