"""OctoAgent Policy Engine -- 策略管道 + 审批管理 + Two-Phase Gate

Feature 006: Policy Engine + Approvals + Chat UI
提供多层策略评估管道、Two-Phase Approval 流程、PolicyCheckHook 适配器。
"""

from __future__ import annotations

# ApprovalManager
from .approval_manager import ApprovalManager

# Feature 061: ApprovalOverride 持久化 + 缓存
from .approval_override_store import ApprovalOverrideCache, ApprovalOverrideRepository

# 评估器
from .evaluators.global_rule import global_rule
from .evaluators.profile_filter import profile_filter

# 枚举
from .models import (
    DEFAULT_PROFILE,
    PERMISSIVE_PROFILE,
    POLICY_ACTION_SEVERITY,
    STRICT_PROFILE,
    ApprovalDecision,
    ApprovalExpiredEventPayload,
    ApprovalListItem,
    ApprovalOverride,
    ApprovalOverrideDeleteResponse,
    ApprovalOverrideListResponse,
    ApprovalRecord,
    ApprovalRequest,
    ApprovalRequestedEventPayload,
    ApprovalResolvedEventPayload,
    ApprovalResolveRequest,
    ApprovalResolveResponse,
    ApprovalsListResponse,
    ApprovalStatus,
    ChatSendRequest,
    ChatSendResponse,
    PendingApproval,
    PolicyAction,
    PolicyDecision,
    PolicyDecisionEventPayload,
    PolicyProfile,
    PolicyStep,
    SSEApprovalEvent,
)

# Pipeline
from .pipeline import evaluate_pipeline

# PolicyCheckHook
from .policy_check_hook import PolicyCheckHook

# PolicyEngine 门面类
from .policy_engine import PolicyEngine

__all__ = [
    # 枚举
    "PolicyAction",
    "ApprovalDecision",
    "ApprovalStatus",
    "POLICY_ACTION_SEVERITY",
    # 策略管道模型
    "PolicyDecision",
    "PolicyStep",
    "PolicyProfile",
    "DEFAULT_PROFILE",
    "STRICT_PROFILE",
    "PERMISSIVE_PROFILE",
    # 审批模型
    "ApprovalRequest",
    "ApprovalRecord",
    "ApprovalResolveRequest",
    "ApprovalListItem",
    # 事件 Payload
    "PolicyDecisionEventPayload",
    "ApprovalRequestedEventPayload",
    "ApprovalResolvedEventPayload",
    "ApprovalExpiredEventPayload",
    # 运行时模型
    "PendingApproval",
    "SSEApprovalEvent",
    # REST API 模型
    "ApprovalsListResponse",
    "ApprovalResolveResponse",
    "ChatSendRequest",
    "ChatSendResponse",
    # Pipeline
    "evaluate_pipeline",
    # 评估器
    "profile_filter",
    "global_rule",
    # ApprovalManager
    "ApprovalManager",
    # Feature 061: ApprovalOverride
    "ApprovalOverride",
    "ApprovalOverrideListResponse",
    "ApprovalOverrideDeleteResponse",
    "ApprovalOverrideRepository",
    "ApprovalOverrideCache",
    # PolicyCheckHook
    "PolicyCheckHook",
    # PolicyEngine 门面类
    "PolicyEngine",
]
