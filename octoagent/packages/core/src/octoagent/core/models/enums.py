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


# M0 合法状态流转 -- 对齐 spec FR-M0-DM-2
VALID_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.CREATED: {TaskStatus.RUNNING, TaskStatus.CANCELLED},
    TaskStatus.RUNNING: {
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    },
    # 终态不可再流转
    TaskStatus.SUCCEEDED: set(),
    TaskStatus.FAILED: set(),
    TaskStatus.CANCELLED: set(),
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
    STATE_TRANSITION = "STATE_TRANSITION"
    ARTIFACT_CREATED = "ARTIFACT_CREATED"
    ERROR = "ERROR"


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
