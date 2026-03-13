"""枚举定义 -- 对齐 spec FR-M0-DM-2, FR-M0-DM-3, FR-M0-DM-4

包含 TaskStatus 状态机、EventType、ActorType、RiskLevel、PartType 枚举，
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
