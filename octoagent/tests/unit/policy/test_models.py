"""数据模型单元测试 -- Feature 006

覆盖:
- T004: PolicyAction 严格度排序、PolicyDecision 必填字段、ApprovalRecord 状态约束、
        PolicyProfile 默认值、Event Payload 模型序列化
- T005: TaskStatus 状态转换测试（3 条新规则）
"""

from datetime import UTC, datetime

import pytest
from octoagent.core.models.enums import (
    EventType,
    TaskStatus,
    validate_transition,
)
from octoagent.policy.models import (
    DEFAULT_PROFILE,
    PERMISSIVE_PROFILE,
    POLICY_ACTION_SEVERITY,
    STRICT_PROFILE,
    ApprovalDecision,
    ApprovalExpiredEventPayload,
    ApprovalListItem,
    ApprovalRecord,
    ApprovalRequest,
    ApprovalRequestedEventPayload,
    ApprovalResolvedEventPayload,
    ApprovalsListResponse,
    ApprovalStatus,
    ChatSendRequest,
    ChatSendResponse,
    PolicyAction,
    PolicyDecision,
    PolicyDecisionEventPayload,
    PolicyProfile,
    PolicyStep,
    SSEApprovalEvent,
)
from octoagent.tooling.models import SideEffectLevel, ToolProfile

# ============================================================
# T004: PolicyAction 严格度排序
# ============================================================


class TestPolicyAction:
    """PolicyAction 枚举测试"""

    def test_values(self) -> None:
        """三种决策值正确"""
        assert PolicyAction.ALLOW == "allow"
        assert PolicyAction.ASK == "ask"
        assert PolicyAction.DENY == "deny"

    def test_severity_ordering(self) -> None:
        """严格度递增: allow < ask < deny"""
        assert POLICY_ACTION_SEVERITY[PolicyAction.ALLOW] < POLICY_ACTION_SEVERITY[PolicyAction.ASK]
        assert POLICY_ACTION_SEVERITY[PolicyAction.ASK] < POLICY_ACTION_SEVERITY[PolicyAction.DENY]

    def test_all_actions_have_severity(self) -> None:
        """所有 PolicyAction 值都有对应的严格度"""
        for action in PolicyAction:
            assert action in POLICY_ACTION_SEVERITY


# ============================================================
# T004: PolicyDecision 必填字段
# ============================================================


class TestPolicyDecision:
    """PolicyDecision 模型测试"""

    def test_required_fields(self) -> None:
        """action 和 label 是必填字段"""
        decision = PolicyDecision(
            action=PolicyAction.ALLOW,
            label="test.layer",
        )
        assert decision.action == PolicyAction.ALLOW
        assert decision.label == "test.layer"
        assert decision.reason == ""
        assert decision.tool_name == ""
        assert decision.side_effect_level is None
        assert decision.evaluated_at is not None

    def test_label_required(self) -> None:
        """label 字段不可省略"""
        with pytest.raises(Exception):
            PolicyDecision(action=PolicyAction.ALLOW)  # type: ignore[call-arg]

    def test_full_fields(self) -> None:
        """所有字段可正确赋值"""
        now = datetime.now(UTC)
        decision = PolicyDecision(
            action=PolicyAction.DENY,
            label="global.irreversible",
            reason="不可逆操作需要审批",
            tool_name="shell_exec",
            side_effect_level=SideEffectLevel.IRREVERSIBLE,
            evaluated_at=now,
        )
        assert decision.action == PolicyAction.DENY
        assert decision.label == "global.irreversible"
        assert decision.reason == "不可逆操作需要审批"
        assert decision.tool_name == "shell_exec"
        assert decision.side_effect_level == SideEffectLevel.IRREVERSIBLE
        assert decision.evaluated_at == now

    def test_serialization(self) -> None:
        """可序列化为 dict"""
        decision = PolicyDecision(
            action=PolicyAction.ASK,
            label="global.irreversible",
        )
        data = decision.model_dump()
        assert data["action"] == "ask"
        assert data["label"] == "global.irreversible"


# ============================================================
# T004: ApprovalRecord 状态约束
# ============================================================


class TestApprovalRecord:
    """ApprovalRecord 模型测试"""

    def _make_request(self) -> ApprovalRequest:
        """创建测试用 ApprovalRequest"""
        return ApprovalRequest(
            approval_id="test-001",
            task_id="task-001",
            tool_name="shell_exec",
            tool_args_summary="command: rm -rf /tmp/***",
            risk_explanation="不可逆 shell 命令",
            policy_label="global.irreversible",
            side_effect_level=SideEffectLevel.IRREVERSIBLE,
            expires_at=datetime.now(UTC),
        )

    def test_pending_state(self) -> None:
        """PENDING 状态: decision 为 None"""
        record = ApprovalRecord(request=self._make_request())
        assert record.status == ApprovalStatus.PENDING
        assert record.decision is None
        assert record.resolved_at is None
        assert record.consumed is False

    def test_approved_state(self) -> None:
        """APPROVED 状态: decision 有值"""
        record = ApprovalRecord(
            request=self._make_request(),
            status=ApprovalStatus.APPROVED,
            decision=ApprovalDecision.ALLOW_ONCE,
            resolved_at=datetime.now(UTC),
            resolved_by="user:web",
        )
        assert record.status == ApprovalStatus.APPROVED
        assert record.decision == ApprovalDecision.ALLOW_ONCE

    def test_rejected_state(self) -> None:
        """REJECTED 状态: decision=DENY"""
        record = ApprovalRecord(
            request=self._make_request(),
            status=ApprovalStatus.REJECTED,
            decision=ApprovalDecision.DENY,
            resolved_at=datetime.now(UTC),
            resolved_by="user:web",
        )
        assert record.status == ApprovalStatus.REJECTED
        assert record.decision == ApprovalDecision.DENY

    def test_expired_state(self) -> None:
        """EXPIRED 状态: decision 为 None（自动 deny）"""
        record = ApprovalRecord(
            request=self._make_request(),
            status=ApprovalStatus.EXPIRED,
        )
        assert record.status == ApprovalStatus.EXPIRED
        assert record.decision is None


# ============================================================
# T004: PolicyProfile 默认值
# ============================================================


class TestPolicyProfile:
    """PolicyProfile 模型测试"""

    def test_default_profile(self) -> None:
        """DEFAULT_PROFILE 预期值"""
        assert DEFAULT_PROFILE.name == "default"
        assert DEFAULT_PROFILE.none_action == PolicyAction.ALLOW
        assert DEFAULT_PROFILE.reversible_action == PolicyAction.ALLOW
        assert DEFAULT_PROFILE.irreversible_action == PolicyAction.ASK
        assert DEFAULT_PROFILE.allowed_tool_profile == ToolProfile.STANDARD
        assert DEFAULT_PROFILE.approval_timeout_seconds == 120.0

    def test_strict_profile(self) -> None:
        """STRICT_PROFILE 预期值"""
        assert STRICT_PROFILE.name == "strict"
        assert STRICT_PROFILE.reversible_action == PolicyAction.ASK
        assert STRICT_PROFILE.irreversible_action == PolicyAction.ASK
        assert STRICT_PROFILE.allowed_tool_profile == ToolProfile.MINIMAL

    def test_permissive_profile(self) -> None:
        """PERMISSIVE_PROFILE 预期值"""
        assert PERMISSIVE_PROFILE.name == "permissive"
        assert PERMISSIVE_PROFILE.none_action == PolicyAction.ALLOW
        assert PERMISSIVE_PROFILE.reversible_action == PolicyAction.ALLOW
        assert PERMISSIVE_PROFILE.irreversible_action == PolicyAction.ALLOW
        assert PERMISSIVE_PROFILE.allowed_tool_profile == ToolProfile.PRIVILEGED

    def test_custom_profile(self) -> None:
        """自定义 Profile"""
        profile = PolicyProfile(
            name="custom",
            none_action=PolicyAction.ALLOW,
            reversible_action=PolicyAction.ASK,
            irreversible_action=PolicyAction.DENY,
        )
        assert profile.name == "custom"
        assert profile.reversible_action == PolicyAction.ASK
        assert profile.irreversible_action == PolicyAction.DENY


# ============================================================
# T004: Event Payload 模型序列化
# ============================================================


class TestEventPayloads:
    """事件 Payload 模型序列化测试"""

    def test_policy_decision_payload_serialization(self) -> None:
        """PolicyDecisionEventPayload 可序列化"""
        payload = PolicyDecisionEventPayload(
            action=PolicyAction.ASK,
            label="global.irreversible",
            reason="不可逆操作",
            tool_name="shell_exec",
            side_effect_level="irreversible",
            pipeline_trace=[
                {"label": "tools.profile", "action": "allow"},
                {"label": "global.irreversible", "action": "ask"},
            ],
        )
        data = payload.model_dump()
        assert data["action"] == "ask"
        assert data["label"] == "global.irreversible"
        assert len(data["pipeline_trace"]) == 2

    def test_approval_requested_payload(self) -> None:
        """ApprovalRequestedEventPayload 可序列化"""
        payload = ApprovalRequestedEventPayload(
            approval_id="test-001",
            task_id="task-001",
            tool_name="shell_exec",
            tool_args_summary="command: rm -rf /tmp/***",
            risk_explanation="不可逆操作",
            policy_label="global.irreversible",
            expires_at="2026-03-02T10:30:00Z",
        )
        data = payload.model_dump()
        assert data["approval_id"] == "test-001"
        assert data["expires_at"] == "2026-03-02T10:30:00Z"

    def test_approval_resolved_payload(self) -> None:
        """ApprovalResolvedEventPayload 可序列化"""
        payload = ApprovalResolvedEventPayload(
            approval_id="test-001",
            task_id="task-001",
            decision="allow-once",
            resolved_by="user:web",
            resolved_at="2026-03-02T10:31:00Z",
        )
        data = payload.model_dump()
        assert data["decision"] == "allow-once"

    def test_approval_expired_payload(self) -> None:
        """ApprovalExpiredEventPayload 可序列化"""
        payload = ApprovalExpiredEventPayload(
            approval_id="test-001",
            task_id="task-001",
            expired_at="2026-03-02T10:32:00Z",
        )
        data = payload.model_dump()
        assert data["auto_decision"] == "deny"
        assert data["reason"] == "approval timeout"


# ============================================================
# T004: EventType 扩展 (FR-026)
# ============================================================


class TestEventTypeExtension:
    """EventType 扩展测试"""

    def test_policy_decision_event_type(self) -> None:
        """POLICY_DECISION 事件类型存在"""
        assert EventType.POLICY_DECISION == "POLICY_DECISION"

    def test_approval_event_types(self) -> None:
        """审批事件类型存在"""
        assert EventType.APPROVAL_REQUESTED == "APPROVAL_REQUESTED"
        assert EventType.APPROVAL_APPROVED == "APPROVAL_APPROVED"
        assert EventType.APPROVAL_REJECTED == "APPROVAL_REJECTED"
        assert EventType.APPROVAL_EXPIRED == "APPROVAL_EXPIRED"

    def test_policy_config_changed_event_type(self) -> None:
        """POLICY_CONFIG_CHANGED 事件类型存在"""
        assert EventType.POLICY_CONFIG_CHANGED == "POLICY_CONFIG_CHANGED"


# ============================================================
# T004: REST API 模型
# ============================================================


class TestRESTModels:
    """REST API 响应模型测试"""

    def test_approvals_list_response(self) -> None:
        """ApprovalsListResponse 可构造"""
        resp = ApprovalsListResponse(
            approvals=[
                ApprovalListItem(
                    approval_id="test-001",
                    task_id="task-001",
                    tool_name="shell_exec",
                    tool_args_summary="command: rm -rf /tmp/***",
                    risk_explanation="不可逆操作",
                    policy_label="global.irreversible",
                    side_effect_level="irreversible",
                    remaining_seconds=95.3,
                    created_at=datetime.now(UTC),
                )
            ],
            total=1,
        )
        assert resp.total == 1
        assert len(resp.approvals) == 1

    def test_chat_send_request(self) -> None:
        """ChatSendRequest 可构造"""
        req = ChatSendRequest(message="你好")
        assert req.message == "你好"
        assert req.task_id is None

    def test_chat_send_request_min_length(self) -> None:
        """ChatSendRequest 空消息被拒绝"""
        with pytest.raises(Exception):
            ChatSendRequest(message="")

    def test_chat_send_response(self) -> None:
        """ChatSendResponse 可构造"""
        resp = ChatSendResponse(
            task_id="task-001",
            stream_url="/stream/task/task-001",
        )
        assert resp.status == "accepted"


# ============================================================
# T005: TaskStatus 状态转换测试
# ============================================================


class TestTaskStatusTransitions:
    """TaskStatus 新状态转换规则测试 (FR-013)"""

    def test_running_to_waiting_approval(self) -> None:
        """RUNNING -> WAITING_APPROVAL 合法"""
        assert validate_transition(TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL) is True

    def test_waiting_approval_to_running(self) -> None:
        """WAITING_APPROVAL -> RUNNING 合法（用户批准）"""
        assert validate_transition(TaskStatus.WAITING_APPROVAL, TaskStatus.RUNNING) is True

    def test_waiting_approval_to_rejected(self) -> None:
        """WAITING_APPROVAL -> REJECTED 合法（用户拒绝/超时）"""
        assert validate_transition(TaskStatus.WAITING_APPROVAL, TaskStatus.REJECTED) is True

    def test_waiting_approval_to_succeeded_illegal(self) -> None:
        """WAITING_APPROVAL -> SUCCEEDED 非法"""
        assert validate_transition(TaskStatus.WAITING_APPROVAL, TaskStatus.SUCCEEDED) is False

    def test_waiting_approval_to_failed_illegal(self) -> None:
        """WAITING_APPROVAL -> FAILED 非法"""
        assert validate_transition(TaskStatus.WAITING_APPROVAL, TaskStatus.FAILED) is False

    def test_waiting_approval_to_cancelled(self) -> None:
        """WAITING_APPROVAL -> CANCELLED 合法（用户主动取消）"""
        assert validate_transition(TaskStatus.WAITING_APPROVAL, TaskStatus.CANCELLED) is True

    def test_created_to_waiting_approval_illegal(self) -> None:
        """CREATED -> WAITING_APPROVAL 非法（必须先 RUNNING）"""
        assert validate_transition(TaskStatus.CREATED, TaskStatus.WAITING_APPROVAL) is False

    def test_rejected_is_terminal(self) -> None:
        """REJECTED 是终态，不可再流转"""
        assert validate_transition(TaskStatus.REJECTED, TaskStatus.RUNNING) is False
        assert validate_transition(TaskStatus.REJECTED, TaskStatus.CREATED) is False


# ============================================================
# T004: SSEApprovalEvent 模型
# ============================================================


class TestSSEApprovalEvent:
    """SSEApprovalEvent 模型测试"""

    def test_approval_requested_sse_event(self) -> None:
        """approval:requested SSE 事件可构造"""
        event = SSEApprovalEvent(
            event_type="approval:requested",
            data={
                "approval_id": "test-001",
                "tool_name": "shell_exec",
            },
        )
        assert event.event_type == "approval:requested"
        assert event.data["approval_id"] == "test-001"


# ============================================================
# T004: PolicyStep 测试
# ============================================================


class TestPolicyStep:
    """PolicyStep dataclass 测试"""

    def test_frozen(self) -> None:
        """PolicyStep 是不可变的"""
        step = PolicyStep(
            evaluator=lambda *args: PolicyDecision(action=PolicyAction.ALLOW, label="test"),
            label="test",
        )
        with pytest.raises(Exception):
            step.label = "modified"  # type: ignore[misc]

    def test_evaluator_callable(self) -> None:
        """evaluator 字段接受可调用对象"""
        step = PolicyStep(
            evaluator=lambda *args: PolicyDecision(action=PolicyAction.ALLOW, label="test"),
            label="test.layer",
        )
        assert callable(step.evaluator)
        assert step.label == "test.layer"
