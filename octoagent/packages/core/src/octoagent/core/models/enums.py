"""枚举定义 -- 对齐 spec FR-M0-DM-2, FR-M0-DM-3, FR-M0-DM-4

包含 TaskStatus 状态机、EventType、ActorType、RiskLevel、PartType、SideEffectLevel 枚举，
以及 VALID_TRANSITIONS 合法流转映射和 TERMINAL_STATES 终态集合。
"""

from enum import StrEnum


class TaskStatus(StrEnum):
    """Task 状态机 -- 对齐 spec FR-M0-DM-2"""

    # M0 活跃状态
    CREATED = "CREATED"
    RUNNING = "RUNNING"

    # M0 终态
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    # M1+ 预留状态（M0 数据模型定义但无消费者）
    QUEUED = "QUEUED"
    WAITING_INPUT = "WAITING_INPUT"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    PAUSED = "PAUSED"
    REJECTED = "REJECTED"


# 合法状态流转 -- 对齐 spec FR-M0-DM-2 + Feature 006 FR-013
VALID_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.CREATED: {TaskStatus.RUNNING, TaskStatus.CANCELLED},
    TaskStatus.RUNNING: {
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.WAITING_INPUT,
        # Feature 006: 策略决策为 ask 时进入审批等待
        TaskStatus.WAITING_APPROVAL,
    },
    # Feature 019: WAITING_INPUT 状态转换
    TaskStatus.WAITING_INPUT: {
        TaskStatus.RUNNING,
        TaskStatus.CANCELLED,
    },
    # Feature 006: WAITING_APPROVAL 状态转换 -- 对齐 FR-013
    TaskStatus.WAITING_APPROVAL: {
        TaskStatus.RUNNING,   # 用户批准后恢复执行
        TaskStatus.REJECTED,  # 用户拒绝或超时
        TaskStatus.CANCELLED,
    },
    TaskStatus.PAUSED: {
        TaskStatus.RUNNING,
        TaskStatus.CANCELLED,
    },
    # 终态不可再流转
    TaskStatus.SUCCEEDED: set(),
    TaskStatus.FAILED: set(),
    TaskStatus.CANCELLED: set(),
    TaskStatus.REJECTED: set(),
}

TERMINAL_STATES: set[TaskStatus] = {
    TaskStatus.SUCCEEDED,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
    TaskStatus.REJECTED,
}


class EventType(StrEnum):
    """事件类型 -- 对齐 spec FR-M0-DM-3"""

    TASK_CREATED = "TASK_CREATED"
    USER_MESSAGE = "USER_MESSAGE"
    MODEL_CALL_STARTED = "MODEL_CALL_STARTED"
    MODEL_CALL_COMPLETED = "MODEL_CALL_COMPLETED"
    MODEL_CALL_FAILED = "MODEL_CALL_FAILED"
    CONTEXT_COMPACTION_COMPLETED = "CONTEXT_COMPACTION_COMPLETED"
    MEMORY_RECALL_SCHEDULED = "MEMORY_RECALL_SCHEDULED"
    MEMORY_RECALL_COMPLETED = "MEMORY_RECALL_COMPLETED"
    MEMORY_RECALL_FAILED = "MEMORY_RECALL_FAILED"
    STATE_TRANSITION = "STATE_TRANSITION"
    ARTIFACT_CREATED = "ARTIFACT_CREATED"
    ERROR = "ERROR"

    # Feature 003: 凭证生命周期事件 -- 对齐 FR-012
    CREDENTIAL_LOADED = "CREDENTIAL_LOADED"
    CREDENTIAL_EXPIRED = "CREDENTIAL_EXPIRED"
    CREDENTIAL_FAILED = "CREDENTIAL_FAILED"

    # Feature 003-b: OAuth 流程事件 -- 对齐 FR-012
    OAUTH_STARTED = "OAUTH_STARTED"
    OAUTH_SUCCEEDED = "OAUTH_SUCCEEDED"
    OAUTH_FAILED = "OAUTH_FAILED"
    OAUTH_REFRESHED = "OAUTH_REFRESHED"

    # Feature 078: OAuth refresh 稳健性事件
    OAUTH_REFRESH_TRIGGERED = "OAUTH_REFRESH_TRIGGERED"
    OAUTH_REFRESH_FAILED = "OAUTH_REFRESH_FAILED"
    OAUTH_REFRESH_RECOVERED = "OAUTH_REFRESH_RECOVERED"
    OAUTH_REFRESH_EXHAUSTED = "OAUTH_REFRESH_EXHAUSTED"
    OAUTH_ADOPTED_FROM_EXTERNAL_CLI = "OAUTH_ADOPTED_FROM_EXTERNAL_CLI"

    # Feature 004: 工具调用事件 -- 对齐 FR-014
    TOOL_CALL_STARTED = "TOOL_CALL_STARTED"
    TOOL_CALL_COMPLETED = "TOOL_CALL_COMPLETED"
    TOOL_CALL_FAILED = "TOOL_CALL_FAILED"

    # Feature 005: SkillRunner 生命周期事件
    SKILL_STARTED = "SKILL_STARTED"
    SKILL_COMPLETED = "SKILL_COMPLETED"
    SKILL_FAILED = "SKILL_FAILED"

    # Feature 006: 策略决策事件 -- 对齐 FR-026
    POLICY_DECISION = "POLICY_DECISION"

    # Feature 006: 审批事件 -- 对齐 FR-026
    APPROVAL_REQUESTED = "APPROVAL_REQUESTED"
    APPROVAL_APPROVED = "APPROVAL_APPROVED"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"
    APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
    # Feature 084 Phase 3: ApprovalGate 决策事件（FR-4.4）
    APPROVAL_DECIDED = "APPROVAL_DECIDED"          # 审批决策完成（含 approved/rejected）

    # Feature 006: 策略配置变更事件 -- 对齐 FR-027
    POLICY_CONFIG_CHANGED = "POLICY_CONFIG_CHANGED"

    # Feature 008: Orchestrator 控制平面事件
    ORCH_DECISION = "ORCH_DECISION"
    WORKER_DISPATCHED = "WORKER_DISPATCHED"
    WORKER_RETURNED = "WORKER_RETURNED"
    A2A_MESSAGE_SENT = "A2A_MESSAGE_SENT"
    A2A_MESSAGE_RECEIVED = "A2A_MESSAGE_RECEIVED"

    # Feature 010: Checkpoint / Resume 生命周期事件
    CHECKPOINT_SAVED = "CHECKPOINT_SAVED"
    RESUME_STARTED = "RESUME_STARTED"
    RESUME_SUCCEEDED = "RESUME_SUCCEEDED"
    RESUME_FAILED = "RESUME_FAILED"

    # Feature 011: Watchdog + Task Journal 事件类型（FR-001）
    TASK_HEARTBEAT = "TASK_HEARTBEAT"            # Worker 心跳确认事件
    TASK_MILESTONE = "TASK_MILESTONE"            # 任务里程碑完成标记事件
    TASK_DRIFT_DETECTED = "TASK_DRIFT_DETECTED"  # 漂移检测告警事件

    # Feature 019: Execution Console / JobRunner 事件
    EXECUTION_STATUS_CHANGED = "EXECUTION_STATUS_CHANGED"
    EXECUTION_LOG = "EXECUTION_LOG"
    EXECUTION_STEP = "EXECUTION_STEP"
    EXECUTION_INPUT_REQUESTED = "EXECUTION_INPUT_REQUESTED"
    EXECUTION_INPUT_ATTACHED = "EXECUTION_INPUT_ATTACHED"
    EXECUTION_CANCEL_REQUESTED = "EXECUTION_CANCEL_REQUESTED"

    # Feature 017: Operator Inbox / Control 审计事件
    OPERATOR_ACTION_RECORDED = "OPERATOR_ACTION_RECORDED"

    # Feature 022: Backup 生命周期事件
    BACKUP_STARTED = "BACKUP_STARTED"
    BACKUP_COMPLETED = "BACKUP_COMPLETED"
    BACKUP_FAILED = "BACKUP_FAILED"

    # Feature 021: Chat Import 生命周期事件
    CHAT_IMPORT_STARTED = "CHAT_IMPORT_STARTED"
    CHAT_IMPORT_COMPLETED = "CHAT_IMPORT_COMPLETED"
    CHAT_IMPORT_FAILED = "CHAT_IMPORT_FAILED"

    # Feature 026: Control Plane 审计事件
    CONTROL_PLANE_RESOURCE_PROJECTED = "CONTROL_PLANE_RESOURCE_PROJECTED"
    CONTROL_PLANE_RESOURCE_REMOVED = "CONTROL_PLANE_RESOURCE_REMOVED"
    CONTROL_PLANE_ACTION_REQUESTED = "CONTROL_PLANE_ACTION_REQUESTED"
    CONTROL_PLANE_ACTION_COMPLETED = "CONTROL_PLANE_ACTION_COMPLETED"
    CONTROL_PLANE_ACTION_REJECTED = "CONTROL_PLANE_ACTION_REJECTED"
    CONTROL_PLANE_ACTION_DEFERRED = "CONTROL_PLANE_ACTION_DEFERRED"

    # Feature 030: capability / delegation / pipeline 事件
    TOOL_INDEX_SELECTED = "TOOL_INDEX_SELECTED"
    WORK_CREATED = "WORK_CREATED"
    WORK_STATUS_CHANGED = "WORK_STATUS_CHANGED"
    PIPELINE_RUN_UPDATED = "PIPELINE_RUN_UPDATED"
    PIPELINE_CHECKPOINT_SAVED = "PIPELINE_CHECKPOINT_SAVED"

    # Feature 062: 资源限制事件
    SKILL_USAGE_REPORT = "SKILL_USAGE_REPORT"      # Skill 执行资源消耗报告
    RESOURCE_LIMIT_HIT = "RESOURCE_LIMIT_HIT"      # 资源限制触发告警

    # Feature 064: 并行工具调用批次事件
    TOOL_BATCH_STARTED = "TOOL_BATCH_STARTED"      # 并行工具批次开始
    TOOL_BATCH_COMPLETED = "TOOL_BATCH_COMPLETED"    # 并行工具批次完成

    # Feature 064: 上下文压缩失败事件（P2-A 预留）
    CONTEXT_COMPACTION_FAILED = "CONTEXT_COMPACTION_FAILED"

    # Feature 061: 权限 Preset + Deferred Tools 事件
    PRESET_CHECK = "PRESET_CHECK"                            # Preset 权限检查
    APPROVAL_OVERRIDE_HIT = "APPROVAL_OVERRIDE_HIT"          # always 覆盖命中
    TOOL_SEARCH_EXECUTED = "TOOL_SEARCH_EXECUTED"            # tool_search 工具执行
    TOOL_PROMOTED = "TOOL_PROMOTED"                          # Deferred 工具提升为 Active
    TOOL_DEMOTED = "TOOL_DEMOTED"                            # Active 工具回退为 Deferred
    TOOL_INDEX_DEGRADED = "TOOL_INDEX_DEGRADED"              # ToolIndex 降级

    # Feature 084 Phase 2: USER.md 写入 + Observation Routine + Sub-agent Delegation 事件（FR-10）
    MEMORY_ENTRY_ADDED = "MEMORY_ENTRY_ADDED"                # user_profile.update add 成功
    MEMORY_ENTRY_REPLACED = "MEMORY_ENTRY_REPLACED"          # user_profile.update replace 成功
    MEMORY_ENTRY_REMOVED = "MEMORY_ENTRY_REMOVED"            # user_profile.update remove 成功
    MEMORY_ENTRY_BLOCKED = "MEMORY_ENTRY_BLOCKED"            # ThreatScanner 拦截写入
    OBSERVATION_OBSERVED = "OBSERVATION_OBSERVED"            # user_profile.observe 写入 candidates
    OBSERVATION_STAGE_COMPLETED = "OBSERVATION_STAGE_COMPLETED"  # Observation Routine 单阶段完成
    OBSERVATION_PROMOTED = "OBSERVATION_PROMOTED"            # 候选被用户接受并写入 USER.md
    OBSERVATION_DISCARDED = "OBSERVATION_DISCARDED"          # 候选被用户拒绝
    SUBAGENT_SPAWNED = "SUBAGENT_SPAWNED"                    # DelegationManager 派发子任务
    SUBAGENT_RETURNED = "SUBAGENT_RETURNED"                  # 子任务返回结果

    # Feature 093 Phase A: AgentSessionTurn 持久化事件（main / worker session 统一）
    AGENT_SESSION_TURN_PERSISTED = "AGENT_SESSION_TURN_PERSISTED"  # mixin 写 turn 入库后 emit

    # Feature 095 Phase D: BehaviorPack cache miss 事件
    # F095 提供 infrastructure（payload schema + helper）；实际 EventStore 接入由 F096 实现。
    # F096 BEHAVIOR_PACK_USED 通过 pack_id 引用此 LOADED 事件做行为可审计。
    BEHAVIOR_PACK_LOADED = "BEHAVIOR_PACK_LOADED"
    # F096 Phase D: 每次 LLM 决策环 emit；与 LOADED 通过 pack_id 关联。
    # LOADED 频次 = cache miss 频次（一次 worktree boot / 一次 pack mtime 变更）；
    # USED 频次 = build_task_context 调用频次（一次 dispatch 一次）。
    BEHAVIOR_PACK_USED = "BEHAVIOR_PACK_USED"


class ActorType(StrEnum):
    """操作者类型 -- 对齐 Blueprint §8.1.2"""

    USER = "user"
    KERNEL = "kernel"
    WORKER = "worker"
    TOOL = "tool"
    SYSTEM = "system"


class RiskLevel(StrEnum):
    """风险等级"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SideEffectLevel(StrEnum):
    """工具副作用等级 -- 对齐 spec FR-001, Blueprint §8.5.2

    枚举值已锁定（FR-025a），变更需经 005/006 利益方评审。
    跨 tooling/policy/skills 等多个包使用的共享类型。
    """

    NONE = "none"  # 纯读取，无副作用
    REVERSIBLE = "reversible"  # 可回滚的副作用
    IRREVERSIBLE = "irreversible"  # 不可逆操作


class PartType(StrEnum):
    """Artifact Part 类型 -- 对齐 spec FR-M0-DM-4"""

    # M0 支持
    TEXT = "text"
    FILE = "file"
    # M1+ 预留
    JSON = "json"
    IMAGE = "image"


def validate_transition(from_status: TaskStatus, to_status: TaskStatus) -> bool:
    """验证状态流转是否合法

    Args:
        from_status: 当前状态
        to_status: 目标状态

    Returns:
        True 如果流转合法，否则 False
    """
    allowed = VALID_TRANSITIONS.get(from_status, set())
    return to_status in allowed
