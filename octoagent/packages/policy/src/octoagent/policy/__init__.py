"""OctoAgent Policy -- 审批管理 + Override 持久化

Feature 070: PolicyEngine / PolicyCheckHook / Pipeline / Evaluators 已删除。
权限检查统一由 tooling.permission.check_permission() 完成。
本包仅保留 ApprovalManager + ApprovalOverrideStore + 数据模型。
"""

from __future__ import annotations

from .approval_manager import ApprovalManager
from .approval_override_store import ApprovalOverrideCache, ApprovalOverrideRepository
from .models import (
    DEFAULT_PROFILE,
    PERMISSIVE_PROFILE,
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
    SSEApprovalEvent,
)
from .privacy_ingestion_policy import (
    AuthorizedRequest,
    DeviceAuthorizationContext,
    PrivacyIngestionPolicyError,
    authorize_device_request,
    review_memory_candidate,
)

__all__ = [
    "DEFAULT_PROFILE",
    "PERMISSIVE_PROFILE",
    "STRICT_PROFILE",
    # 枚举
    "PolicyAction",
    "ApprovalDecision",
    "ApprovalStatus",
    # 审批模型
    "ApprovalRequest",
    "ApprovalRecord",
    "ApprovalResolveRequest",
    "ApprovalListItem",
    "PolicyDecision",
    "PolicyProfile",
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
    # ApprovalManager
    "ApprovalManager",
    # Override
    "ApprovalOverride",
    "ApprovalOverrideListResponse",
    "ApprovalOverrideDeleteResponse",
    "ApprovalOverrideRepository",
    "ApprovalOverrideCache",
    # F152 原生设备有限权限策略
    "AuthorizedRequest",
    "DeviceAuthorizationContext",
    "PrivacyIngestionPolicyError",
    "authorize_device_request",
    "review_memory_candidate",
]
