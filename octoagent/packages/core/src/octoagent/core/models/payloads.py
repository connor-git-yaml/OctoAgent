"""Event Payload 子类型 -- 对齐 spec FR-M0-DM-3

所有事件的结构化 payload 定义。
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from .enums import TaskStatus
from .execution import ExecutionSessionState


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
    text: str = Field(default="", description="完整消息文本，供续对话和上下文压缩使用")
    attachment_count: int = Field(default=0)
    metadata: dict[str, str] = Field(default_factory=dict, description="渠道侧输入元数据")
    control_metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="受控运行元数据，仅 trusted control envelope 可写入",
    )


class ControlMetadataUpdatedPayload(BaseModel):
    """F098 Phase E: CONTROL_METADATA_UPDATED 事件 payload。

    F097 P1-1 known issue 修复：USER_MESSAGE 事件被多 consumer（context_compaction /
    chat / telegram 等）当作用户输入；F098 引入独立 event type，仅承载 control_metadata
    不含 text，避免污染对话历史。

    与 UserMessagePayload 区别：
    - UserMessagePayload 含 text / text_preview / text_length / attachment_count
    - ControlMetadataUpdatedPayload 仅承载 control_metadata + source 字段（emit 来源）

    `merge_control_metadata` 合并 USER_MESSAGE + CONTROL_METADATA_UPDATED 两类事件，
    历史 USER_MESSAGE 含 control_metadata 的 task 仍可读（向后兼容）。
    """

    control_metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="受控运行元数据，仅 trusted control envelope 可写入",
    )
    source: str = Field(
        default="",
        description=(
            "Emit 来源标识，便于审计与诊断。候选值（未强制 enum）："
            " 'subagent_delegation_init'（task_runner._emit_subagent_delegation_init_if_needed）"
            " / 'subagent_delegation_session_backfill'（agent_context._ensure_agent_session B-3）"
            " / 后续 Feature 可扩展"
        ),
    )


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


class ContextCompactionCompletedPayload(BaseModel):
    """上下文压缩完成事件 payload。"""

    model_alias: str = Field(default="summarizer")
    input_tokens_before: int = Field(default=0, ge=0)
    input_tokens_after: int = Field(default=0, ge=0)
    compressed_turn_count: int = Field(default=0, ge=0)
    kept_turn_count: int = Field(default=0, ge=0)
    summary_artifact_ref: str | None = Field(default=None)
    request_artifact_ref: str | None = Field(default=None)
    memory_flush_run_id: str | None = Field(default=None)
    reason: str = Field(default="")
    # Feature 060: fallback 链审计字段
    fallback_used: bool = Field(default=False, description="是否触发了 fallback")
    fallback_chain: list[str] = Field(
        default_factory=list, description="实际走过的 alias 链"
    )
    # Feature 060 Phase 2: 两阶段压缩审计字段
    compaction_phases: list[dict[str, Any]] = Field(
        default_factory=list, description="两阶段压缩执行详情"
    )
    # Feature 060 Phase 3: 三层压缩审计字段
    layers: list[dict[str, Any]] = Field(
        default_factory=list, description="各压缩层级审计信息"
    )
    compaction_version: str = Field(
        default="", description="压缩版本: v1(扁平) | v2(三层)"
    )


class MemoryRecallScheduledPayload(BaseModel):
    """delayed recall 已进入 durable carrier 的事件 payload。"""

    context_frame_id: str = Field(default="")
    query: str = Field(default="")
    scope_ids: list[str] = Field(default_factory=list)
    request_artifact_ref: str | None = Field(default=None)
    initial_hit_count: int = Field(default=0, ge=0)
    delivered_hit_count: int = Field(default=0, ge=0)
    schedule_reason: str = Field(default="")
    degraded_reasons: list[str] = Field(default_factory=list)


class MemoryRecallCompletedPayload(BaseModel):
    """delayed recall materialize 完成事件 payload。

    F094 B6: 加 worker / agent 维度审计字段。
    - agent_runtime_id: 触发 recall 的 runtime
    - queried_namespace_kinds: 本次 recall 查询了哪些 namespace kind（去重，
      与 RecallFrame.queried_namespace_kinds 派生路径相同）
    - hit_namespace_kinds: 本次 recall 实际有 hit 命中的 namespace kind（与
      RecallFrame.hit_namespace_kinds 一致）
    F096 audit endpoint 可订阅这些字段做"曾查过私有"vs"实际命中私有"两类查询。
    """

    context_frame_id: str = Field(default="")
    query: str = Field(default="")
    scope_ids: list[str] = Field(default_factory=list)
    request_artifact_ref: str | None = Field(default=None)
    result_artifact_ref: str | None = Field(default=None)
    hit_count: int = Field(default=0, ge=0)
    backend: str = Field(default="")
    backend_state: str = Field(default="")
    degraded_reasons: list[str] = Field(default_factory=list)
    # F094 B6: worker / agent 维度（默认空 list 保持向后兼容）
    agent_runtime_id: str = Field(default="")
    queried_namespace_kinds: list[str] = Field(default_factory=list)
    hit_namespace_kinds: list[str] = Field(default_factory=list)


class MemoryRecallFailedPayload(BaseModel):
    """delayed recall materialize 失败事件 payload。"""

    context_frame_id: str = Field(default="")
    query: str = Field(default="")
    scope_ids: list[str] = Field(default_factory=list)
    request_artifact_ref: str | None = Field(default=None)
    error_type: str = Field(default="")
    error_message: str = Field(default="")
    degraded_reasons: list[str] = Field(default_factory=list)


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
    session_id: str | None = Field(default=None, description="来源 execution session")
    source: str = Field(default="", description="artifact 来源分类")


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
    agent_name: str = Field(default="", description="Agent 显示名称（前端泳道标题用）")


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


class A2AMessageAuditPayload(BaseModel):
    """A2A_MESSAGE_SENT / A2A_MESSAGE_RECEIVED 审计 payload。"""

    a2a_conversation_id: str = Field(description="A2A conversation ID")
    a2a_message_id: str = Field(description="durable A2A message ID")
    protocol_message_id: str = Field(description="协议 message_id")
    message_type: str = Field(description="A2A message type")
    from_agent: str = Field(description="发送方 agent URI")
    to_agent: str = Field(description="接收方 agent URI")
    source_agent_runtime_id: str = Field(default="", description="源 runtime")
    source_agent_session_id: str = Field(default="", description="源 session")
    target_agent_runtime_id: str = Field(default="", description="目标 runtime")
    target_agent_session_id: str = Field(default="", description="目标 session")
    work_id: str = Field(default="", description="关联 work ID")
    direction: str = Field(default="", description="message 方向")


# Feature 004: 工具调用 Payload 类型 -- 对齐 FR-014


class ToolCallStartedPayload(BaseModel):
    """TOOL_CALL_STARTED 事件 payload -- 对齐 spec FR-014"""

    tool_name: str = Field(description="工具名称")
    tool_group: str = Field(description="工具分组")
    side_effect_level: str = Field(description="副作用等级")
    args_summary: str = Field(description="参数摘要（脱敏后）")
    agent_runtime_id: str = Field(default="", description="当前 agent runtime ID")
    agent_session_id: str = Field(default="", description="当前 agent session ID")
    work_id: str = Field(default="", description="当前 work ID")
    timeout_seconds: float | None = Field(
        default=None,
        description="声明式超时",
    )


class ToolCallCompletedPayload(BaseModel):
    """TOOL_CALL_COMPLETED 事件 payload -- 对齐 spec FR-014"""

    tool_name: str = Field(description="工具名称")
    duration_ms: int = Field(description="执行耗时（毫秒）")
    output_summary: str = Field(description="输出摘要（脱敏后）")
    agent_runtime_id: str = Field(default="", description="当前 agent runtime ID")
    agent_session_id: str = Field(default="", description="当前 agent session ID")
    work_id: str = Field(default="", description="当前 work ID")
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
    agent_runtime_id: str = Field(default="", description="当前 agent runtime ID")
    agent_session_id: str = Field(default="", description="当前 agent session ID")
    work_id: str = Field(default="", description="当前 work ID")
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


# Feature 022: Backup 生命周期 Payload 类型


class BackupLifecyclePayload(BaseModel):
    """BACKUP_* 生命周期事件 payload。"""

    bundle_id: str = Field(description="backup bundle ID")
    output_path: str = Field(description="bundle 输出路径")
    scope_summary: list[str] = Field(default_factory=list, description="包含的 scope 摘要")
    status: str = Field(description="started/completed/failed")
    message: str = Field(default="", description="补充说明")


class ChatImportLifecyclePayload(BaseModel):
    """CHAT_IMPORT_* 生命周期事件 payload。"""

    batch_id: str = Field(description="导入批次 ID")
    source_id: str = Field(description="导入源 ID")
    scope_id: str = Field(description="目标 chat scope")
    imported_count: int = Field(default=0, description="本次新增导入消息数")
    duplicate_count: int = Field(default=0, description="本次命中的重复消息数")
    window_count: int = Field(default=0, description="生成的窗口数量")
    report_id: str | None = Field(default=None, description="对应 ImportReport ID")
    message: str = Field(default="", description="补充说明")


class ControlPlaneAuditPayload(BaseModel):
    """CONTROL_PLANE_* 事件 payload。"""

    event_type: str = Field(description="control-plane 事件类型")
    contract_version: str = Field(default="1.0.0", description="contract 版本")
    request_id: str = Field(default="", description="动作请求 ID")
    correlation_id: str = Field(default="", description="异步关联 ID")
    causation_id: str = Field(default="", description="因果链 ID")
    actor_id: str = Field(default="", description="操作者 ID")
    actor_label: str = Field(default="", description="操作者标签")
    surface: str = Field(default="system", description="触发表面")
    payload_summary: str = Field(default="", description="摘要")
    resource_ref: dict[str, object] | None = Field(default=None)
    resource_refs: list[dict[str, object]] = Field(default_factory=list)
    target_refs: list[dict[str, object]] = Field(default_factory=list)
    metadata: dict[str, object] = Field(default_factory=dict)


class ToolIndexSelectedPayload(BaseModel):
    """Feature 030: ToolIndex 命中事件 payload。"""

    selection_id: str = Field(min_length=1)
    backend: str = Field(default="in_memory")
    is_fallback: bool = False
    query: str = Field(default="")
    selected_tools: list[str] = Field(default_factory=list)
    hit_count: int = Field(default=0, ge=0)
    warnings: list[str] = Field(default_factory=list)


class WorkLifecyclePayload(BaseModel):
    """Feature 030: Work 生命周期 payload。"""

    work_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    parent_work_id: str | None = None
    status: str = Field(min_length=1)
    target_kind: str = Field(default="")
    requested_capability: str = Field(default="")
    selected_worker_type: str = Field(default="")
    route_reason: str = Field(default="")
    selected_tools: list[str] = Field(default_factory=list)
    pipeline_run_id: str = Field(default="")
    owner_id: str = Field(default="")
    metadata: dict[str, object] = Field(default_factory=dict)


class PipelineRunUpdatedPayload(BaseModel):
    """Feature 030: Pipeline run 状态变更 payload。"""

    run_id: str = Field(min_length=1)
    pipeline_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    work_id: str = Field(min_length=1)
    status: str = Field(min_length=1)
    current_node_id: str = Field(default="")
    pause_reason: str = Field(default="")
    retry_count: int = Field(default=0, ge=0)
    summary: str = Field(default="")
    metadata: dict[str, object] = Field(default_factory=dict)


class PipelineCheckpointSavedPayload(BaseModel):
    """Feature 030: Pipeline checkpoint 持久化 payload。"""

    checkpoint_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    node_id: str = Field(min_length=1)
    status: str = Field(min_length=1)
    retry_count: int = Field(default=0, ge=0)
    replay_summary: str = Field(default="")


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


# Feature 017: Operator Inbox / Mobile Controls Payload 类型


class OperatorActionAuditPayload(BaseModel):
    """OPERATOR_ACTION_RECORDED 事件 payload。"""

    item_id: str = Field(description="被操作的 inbox item 标识")
    item_kind: str = Field(description="item 类型")
    action_kind: str = Field(description="动作类型")
    source: str = Field(description="动作来源渠道")
    actor_id: str = Field(description="操作者标识")
    actor_label: str = Field(default="", description="操作者展示名称")
    target_ref: str = Field(default="", description="目标对象引用")
    outcome: str = Field(description="动作结果")
    message: str = Field(default="", description="结果描述")
    note: str = Field(default="", description="用户备注")
    result_task_id: str | None = Field(default=None, description="retry 生成的新任务 ID")
    handled_at: datetime = Field(description="动作处理时间")


# Feature 019: Execution Console / JobRunner Payload 类型


class ExecutionStatusChangedPayload(BaseModel):
    """EXECUTION_STATUS_CHANGED 事件 payload。"""

    session_id: str = Field(description="execution session ID")
    backend: str = Field(description="execution backend")
    backend_job_id: str = Field(description="backend job ID")
    status: ExecutionSessionState = Field(description="new execution status")
    interactive: bool = Field(default=False)
    input_policy: str = Field(default="explicit-request-only")
    runtime_dir: str = Field(default="", description="host runtime dir for recovery")
    container_name: str = Field(default="", description="docker container name")
    message: str = Field(default="", description="status summary")
    metadata: dict[str, str] = Field(default_factory=dict)


class ExecutionLogPayload(BaseModel):
    """EXECUTION_LOG 事件 payload。"""

    session_id: str = Field(description="execution session ID")
    stream: str = Field(description="stdout/stderr")
    chunk: str = Field(description="log chunk")
    chunk_index: int = Field(default=0, ge=0)


class ExecutionStepPayload(BaseModel):
    """EXECUTION_STEP 事件 payload。"""

    session_id: str = Field(description="execution session ID")
    step_name: str = Field(description="current step name")
    summary: str = Field(default="", description="step summary")


class ExecutionInputRequestedPayload(BaseModel):
    """EXECUTION_INPUT_REQUESTED 事件 payload。"""

    session_id: str = Field(description="execution session ID")
    prompt: str = Field(description="human input prompt")
    request_id: str = Field(description="input request ID")
    approval_id: str | None = Field(default=None, description="optional approval ID")


class ExecutionInputAttachedPayload(BaseModel):
    """EXECUTION_INPUT_ATTACHED 事件 payload。"""

    session_id: str = Field(description="execution session ID")
    request_id: str = Field(description="input request ID")
    actor: str = Field(description="input actor")
    preview: str = Field(description="sanitized input preview")
    text_length: int = Field(description="full input length")
    approval_id: str | None = Field(default=None)
    artifact_id: str | None = Field(default=None)
    attached_at: datetime = Field(description="attached timestamp")


class ExecutionCancelRequestedPayload(BaseModel):
    """EXECUTION_CANCEL_REQUESTED 事件 payload。"""

    session_id: str = Field(description="execution session ID")
    actor: str = Field(description="cancel actor")
    reason: str = Field(default="", description="cancel reason")


# Feature 097 Phase E: Subagent 完成事件 payload


class SubagentCompletedPayload(BaseModel):
    """SUBAGENT_COMPLETED 事件 payload。

    Subagent 子任务进入终态时由 cleanup hook 写入。
    覆盖 AC-EVENT-1（Constitution C2 Everything is an Event）：
    SUBAGENT_INTERNAL session CLOSED 状态迁移通过此事件记录。
    """

    delegation_id: str = Field(..., min_length=1, description="委托 ID（SubagentDelegation.delegation_id）")
    child_task_id: str = Field(..., min_length=1, description="被委托子任务 ID")
    terminal_status: str = Field(description="终态：succeeded / failed / cancelled")
    closed_at: datetime = Field(description="终态时间戳（UTC）")
    parent_task_id: str = Field(default="", description="父任务 ID，用于 audit 关联")
    child_agent_session_id: str | None = Field(default=None, description="Subagent SUBAGENT_INTERNAL session ID（None 表示 spawn 失败场景）")
