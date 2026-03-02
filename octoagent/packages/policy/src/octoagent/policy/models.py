"""数据模型与枚举 -- Feature 006 Policy Engine + Approvals

对齐 data-model.md 定义。包含：
- 枚举：PolicyAction、ApprovalDecision、ApprovalStatus
- 策略管道模型：PolicyDecision、PolicyStep、PolicyProfile
- 审批模型：ApprovalRequest、ApprovalRecord、ApprovalResolveRequest、ApprovalListItem
- 事件 Payload 模型：PolicyDecisionEventPayload 等
- 运行时模型：PendingApproval、SSEApprovalEvent
- REST API 模型：ApprovalsListResponse、ApprovalResolveResponse、ChatSendRequest、ChatSendResponse
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from octoagent.tooling.models import SideEffectLevel, ToolProfile
from pydantic import BaseModel, Field

# ============================================================
# 枚举定义 (T006)
# ============================================================


class PolicyAction(StrEnum):
    """策略决策动作

    三种决策结果，严格度递增: allow < ask < deny
    """

    ALLOW = "allow"  # 允许执行
    ASK = "ask"  # 需要用户审批
    DENY = "deny"  # 拒绝执行


# 严格度排序映射（用于"只收紧不放松"逻辑）
POLICY_ACTION_SEVERITY: dict[PolicyAction, int] = {
    PolicyAction.ALLOW: 0,
    PolicyAction.ASK: 1,
    PolicyAction.DENY: 2,
}


class ApprovalDecision(StrEnum):
    """用户的审批决策

    由用户通过 Approvals 面板或 REST API 提交。
    """

    ALLOW_ONCE = "allow-once"  # 一次性允许
    ALLOW_ALWAYS = "allow-always"  # 总是允许同类操作
    DENY = "deny"  # 拒绝


class ApprovalStatus(StrEnum):
    """审批请求状态"""

    PENDING = "pending"  # 等待用户决策
    APPROVED = "approved"  # 已批准
    REJECTED = "rejected"  # 已拒绝
    EXPIRED = "expired"  # 已过期（自动 deny）


# ============================================================
# 策略管道模型 (T009, T010)
# ============================================================


class PolicyDecision(BaseModel):
    """策略评估的决策结果

    是策略管道的核心输出物。每一层评估产生一个 PolicyDecision，
    最终取最严格的决策作为最终结果。

    对齐 FR: FR-001, FR-002, FR-005, FR-006
    """

    action: PolicyAction
    label: str = Field(
        ...,
        description=(
            "决策来源标签，标识由哪一层规则产生。"
            "格式: '<layer>.<detail>'，如 'tools.profile', 'global.irreversible'"
        ),
    )
    reason: str = Field(
        default="",
        description="决策原因说明（人类可读）",
    )
    tool_name: str = Field(
        default="",
        description="关联的工具名称",
    )
    side_effect_level: SideEffectLevel | None = Field(
        default=None,
        description="工具的副作用级别",
    )
    evaluated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="评估时间",
    )


@dataclass(frozen=True)
class PolicyStep:
    """策略管道中的一个评估层

    包含评估函数和来源标签。多个 PolicyStep 按顺序组成完整的 Policy Pipeline。

    对齐 FR: FR-001
    """

    evaluator: Callable[..., PolicyDecision]
    label: str  # 层标签前缀，如 "tools.profile", "global"


class PolicyProfile(BaseModel):
    """策略配置档案

    定义不同场景下的策略规则。M1 阶段为代码内静态配置，M2 支持动态变更。

    对齐 FR: FR-005, FR-027 (US-8)
    """

    name: str = Field(
        ...,
        description="Profile 名称，如 'default', 'strict', 'permissive'",
    )
    description: str = Field(
        default="",
        description="Profile 描述",
    )

    # === side_effect_level -> PolicyAction 映射 ===
    none_action: PolicyAction = Field(
        default=PolicyAction.ALLOW,
        description="side_effect_level=none 的默认决策",
    )
    reversible_action: PolicyAction = Field(
        default=PolicyAction.ALLOW,
        description="side_effect_level=reversible 的默认决策",
    )
    irreversible_action: PolicyAction = Field(
        default=PolicyAction.ASK,
        description="side_effect_level=irreversible 的默认决策",
    )

    # === ToolProfile 级别限制 ===
    allowed_tool_profile: ToolProfile = Field(
        default=ToolProfile.STANDARD,
        description="当前 Profile 允许的最高工具级别",
    )

    # === 超时配置 ===
    approval_timeout_seconds: float = Field(
        default=120.0,
        description="审批等待超时（秒）",
    )


# M1 预置 Profile (T010)
DEFAULT_PROFILE = PolicyProfile(
    name="default",
    description="默认策略: irreversible 需审批，其余放行",
    none_action=PolicyAction.ALLOW,
    reversible_action=PolicyAction.ALLOW,
    irreversible_action=PolicyAction.ASK,
    allowed_tool_profile=ToolProfile.STANDARD,
    approval_timeout_seconds=120.0,
)

STRICT_PROFILE = PolicyProfile(
    name="strict",
    description="严格策略: reversible 和 irreversible 都需审批",
    none_action=PolicyAction.ALLOW,
    reversible_action=PolicyAction.ASK,
    irreversible_action=PolicyAction.ASK,
    allowed_tool_profile=ToolProfile.MINIMAL,
    approval_timeout_seconds=60.0,
)

PERMISSIVE_PROFILE = PolicyProfile(
    name="permissive",
    description="宽松策略: 全部放行（仅用于测试/受信任环境）",
    none_action=PolicyAction.ALLOW,
    reversible_action=PolicyAction.ALLOW,
    irreversible_action=PolicyAction.ALLOW,
    allowed_tool_profile=ToolProfile.PRIVILEGED,
    approval_timeout_seconds=300.0,
)


# ============================================================
# 审批模型 (T011)
# ============================================================


class ApprovalRequest(BaseModel):
    """审批请求记录

    是 Two-Phase Approval 的注册产物。包含审批所需的全部上下文信息。

    对齐 FR: FR-007, FR-018
    """

    approval_id: str = Field(
        ...,
        description="唯一审批 ID（UUID v4）",
    )
    task_id: str = Field(
        ...,
        description="关联的 Task ID",
    )
    tool_name: str = Field(
        ...,
        description="触发审批的工具名称",
    )
    tool_args_summary: str = Field(
        ...,
        description="工具参数摘要（脱敏后），用于审批面板展示",
    )
    risk_explanation: str = Field(
        ...,
        description="风险说明，解释为何需要审批",
    )
    policy_label: str = Field(
        ...,
        description="触发审批的策略层 label（如 'global.irreversible'）",
    )
    side_effect_level: SideEffectLevel = Field(
        ...,
        description="工具的副作用级别",
    )
    expires_at: datetime = Field(
        ...,
        description="审批过期时间（UTC）",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="审批请求创建时间（UTC）",
    )


class ApprovalRecord(BaseModel):
    """审批完整记录（含决策结果）

    在 ApprovalRequest 基础上增加决策状态和结果信息。
    是 ApprovalManager 管理的核心实体。

    对齐 FR: FR-007, FR-008, FR-009
    """

    request: ApprovalRequest
    status: ApprovalStatus = Field(
        default=ApprovalStatus.PENDING,
        description="当前审批状态",
    )
    decision: ApprovalDecision | None = Field(
        default=None,
        description="用户的审批决策（pending 时为 None）",
    )
    resolved_at: datetime | None = Field(
        default=None,
        description="决策解决时间（UTC）",
    )
    resolved_by: str | None = Field(
        default=None,
        description="解决者标识（如 'user:web', 'system:timeout'）",
    )
    consumed: bool = Field(
        default=False,
        description="allow-once 令牌是否已消费",
    )


class ApprovalResolveRequest(BaseModel):
    """审批决策 REST API 请求体

    由 POST /api/approve/{approval_id} 接收。

    对齐 FR: FR-019
    """

    decision: ApprovalDecision = Field(
        ...,
        description="审批决策: allow-once / allow-always / deny",
    )


class ApprovalListItem(BaseModel):
    """审批列表项（API 响应）

    GET /api/approvals 返回的列表项，包含前端展示所需的全部信息。

    对齐 FR: FR-018, FR-020
    """

    approval_id: str
    task_id: str
    tool_name: str
    tool_args_summary: str
    risk_explanation: str
    policy_label: str
    side_effect_level: str
    remaining_seconds: float = Field(
        ...,
        description="剩余等待时间（秒），由服务端计算",
    )
    created_at: datetime


# ============================================================
# 事件 Payload 模型 (T012)
# ============================================================


class PolicyDecisionEventPayload(BaseModel):
    """POLICY_DECISION 事件的 payload

    对齐 FR: FR-006
    """

    action: PolicyAction
    label: str
    reason: str
    tool_name: str
    side_effect_level: str
    pipeline_trace: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Pipeline 各层评估结果链（含 label 和 action）",
    )


class ApprovalRequestedEventPayload(BaseModel):
    """APPROVAL_REQUESTED 事件的 payload

    对齐 FR: FR-007, FR-012
    """

    approval_id: str
    task_id: str
    tool_name: str
    tool_args_summary: str  # 脱敏后
    risk_explanation: str
    policy_label: str
    expires_at: str  # ISO 格式


class ApprovalResolvedEventPayload(BaseModel):
    """APPROVAL_APPROVED / APPROVAL_REJECTED 事件的 payload

    对齐 FR: FR-012
    """

    approval_id: str
    task_id: str
    decision: str  # allow-once / allow-always / deny
    resolved_by: str
    resolved_at: str  # ISO 格式


class ApprovalExpiredEventPayload(BaseModel):
    """APPROVAL_EXPIRED 事件的 payload

    对齐 FR: FR-010
    """

    approval_id: str
    task_id: str
    expired_at: str  # ISO 格式
    auto_decision: str = "deny"
    reason: str = "approval timeout"


# ============================================================
# 内部运行时模型 (T013)
# ============================================================


@dataclass
class PendingApproval:
    """ApprovalManager 内部运行时状态

    不对外暴露，仅用于管理 asyncio.Event 和定时器。
    """

    record: ApprovalRecord
    event: asyncio.Event = field(default_factory=asyncio.Event)
    timer_handle: asyncio.TimerHandle | None = None


# ============================================================
# SSE 事件模型 (T013)
# ============================================================


class SSEApprovalEvent(BaseModel):
    """SSE 推送的审批事件

    前端 EventSource 接收后按 event_type 分发到对应组件。
    """

    event_type: str = Field(
        ...,
        description=(
            "SSE event type: 'approval:requested'"
            " / 'approval:resolved' / 'approval:expired'"
        ),
    )
    data: dict[str, Any] = Field(
        ...,
        description="事件数据（JSON）",
    )


# ============================================================
# REST API 响应模型 (T014)
# ============================================================


class ApprovalsListResponse(BaseModel):
    """GET /api/approvals 响应

    对齐 contracts/policy-api.md §1.1
    """

    approvals: list[ApprovalListItem]
    total: int


class ApprovalResolveResponse(BaseModel):
    """POST /api/approve/{approval_id} 响应

    对齐 contracts/policy-api.md §1.2
    """

    success: bool
    approval_id: str | None = None
    decision: str | None = None
    message: str
    error: str | None = None
    current_status: str | None = None


class ChatSendRequest(BaseModel):
    """POST /api/chat/send 请求体

    对齐 contracts/policy-api.md §1.3
    """

    message: str = Field(..., min_length=1, max_length=10000)
    task_id: str | None = Field(
        default=None,
        description="关联的 Task ID（续对话时传入，新对话为 null）",
    )


class ChatSendResponse(BaseModel):
    """POST /api/chat/send 响应

    对齐 contracts/policy-api.md §1.3
    """

    task_id: str
    status: str = "accepted"
    stream_url: str
