"""OctoAgent Core Domain Models -- 公共类型导出

所有公共模型类型从此入口导入。
"""

from .artifact import Artifact, ArtifactPart
from .checkpoint import (
    PIPELINE_NODES,
    CheckpointSnapshot,
    CheckpointStatus,
    ResumeAttempt,
    ResumeFailureType,
    ResumeResult,
    SideEffectLedgerEntry,
)
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
    WorkerRuntimeState,
    WorkerSession,
)
from .payloads import (
    ArtifactCreatedPayload,
    CheckpointSavedPayload,
    ErrorPayload,
    ModelCallCompletedPayload,
    ModelCallFailedPayload,
    ModelCallStartedPayload,
    OrchestratorDecisionPayload,
    ResumeFailedPayload,
    ResumeStartedPayload,
    ResumeSucceededPayload,
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
    # Checkpoint
    "PIPELINE_NODES",
    "CheckpointStatus",
    "ResumeFailureType",
    "CheckpointSnapshot",
    "ResumeAttempt",
    "ResumeResult",
    "SideEffectLedgerEntry",
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
    # Feature 010: Checkpoint / Resume Payloads
    "CheckpointSavedPayload",
    "ResumeStartedPayload",
    "ResumeSucceededPayload",
    "ResumeFailedPayload",
    # Feature 008: Orchestrator Models
    "OrchestratorRequest",
    "DispatchEnvelope",
    "WorkerResult",
    "WorkerExecutionStatus",
    "WorkerRuntimeState",
    "WorkerSession",
]
