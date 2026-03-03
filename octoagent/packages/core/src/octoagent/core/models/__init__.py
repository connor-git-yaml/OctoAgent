"""OctoAgent Core Domain Models -- 公共类型导出

所有公共模型类型从此入口导入。
"""

from .artifact import Artifact, ArtifactPart
from .enums import (
    TERMINAL_STATES,
    VALID_TRANSITIONS,
    ActorType,
    EventType,
    PartType,
    RiskLevel,
    TaskStatus,
    validate_transition,
)
from .event import Event, EventCausality
from .message import MessageAttachment, NormalizedMessage
from .orchestrator import (
    DispatchEnvelope,
    OrchestratorRequest,
    WorkerExecutionStatus,
    WorkerResult,
)
from .payloads import (
    ArtifactCreatedPayload,
    ErrorPayload,
    ModelCallCompletedPayload,
    ModelCallFailedPayload,
    ModelCallStartedPayload,
    OrchestratorDecisionPayload,
    StateTransitionPayload,
    TaskCreatedPayload,
    ToolCallCompletedPayload,
    ToolCallFailedPayload,
    ToolCallStartedPayload,
    UserMessagePayload,
    WorkerDispatchedPayload,
    WorkerReturnedPayload,
)
from .task import RequesterInfo, Task, TaskPointers

__all__ = [
    # 枚举
    "TaskStatus",
    "EventType",
    "ActorType",
    "RiskLevel",
    "PartType",
    # 状态机
    "VALID_TRANSITIONS",
    "TERMINAL_STATES",
    "validate_transition",
    # Task
    "Task",
    "RequesterInfo",
    "TaskPointers",
    # Event
    "Event",
    "EventCausality",
    # Artifact
    "Artifact",
    "ArtifactPart",
    # Message
    "NormalizedMessage",
    "MessageAttachment",
    # Payloads
    "TaskCreatedPayload",
    "UserMessagePayload",
    "ModelCallStartedPayload",
    "ModelCallCompletedPayload",
    "ModelCallFailedPayload",
    "StateTransitionPayload",
    "ArtifactCreatedPayload",
    "ErrorPayload",
    # Feature 008: Orchestrator Payloads
    "OrchestratorDecisionPayload",
    "WorkerDispatchedPayload",
    "WorkerReturnedPayload",
    # Feature 004: 工具调用 Payloads
    "ToolCallStartedPayload",
    "ToolCallCompletedPayload",
    "ToolCallFailedPayload",
    # Feature 008: Orchestrator Models
    "OrchestratorRequest",
    "DispatchEnvelope",
    "WorkerResult",
    "WorkerExecutionStatus",
]
