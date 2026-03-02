"""完整审批流程集成测试 -- T051, T052, T056, T057, T058

覆盖:
- T051 [US7] 事件全链路: allow/ask->approve/ask->deny/超时过期事件链 (FR-006, FR-012)
- T052 [US8] 策略配置变更事件 (FR-027)
- T056 [e2e] 完整审批流程端到端
- T057 Task 状态机流转
- T058 启动恢复

注: 由于 Event Store 需要完整的 SQLite 实例，
这些测试使用 mock Event Store 验证事件生成逻辑。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from octoagent.policy.approval_manager import ApprovalManager
from octoagent.policy.models import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalStatus,
    DEFAULT_PROFILE,
    PolicyAction,
    PolicyDecision,
    PolicyProfile,
    PolicyStep,
    STRICT_PROFILE,
)
from octoagent.policy.pipeline import evaluate_pipeline
from octoagent.policy.policy_check_hook import PolicyCheckHook
from octoagent.policy.policy_engine import PolicyEngine
from octoagent.tooling.models import (
    ExecutionContext,
    SideEffectLevel,
    ToolMeta,
    ToolProfile,
)


# ============================================================
# Mock Event Store（记录事件调用）
# ============================================================


class MockEventStore:
    """Mock Event Store，记录所有写入的事件"""

    def __init__(self) -> None:
        self.events: list[Any] = []
        self._seq = 0

    async def append_event(self, event: Any) -> None:
        self.events.append(event)

    async def get_next_task_seq(self, task_id: str) -> int:
        self._seq += 1
        return self._seq


class MockSSEBroadcaster:
    """Mock SSE 广播器"""

    def __init__(self) -> None:
        self.events: list[dict] = []

    async def broadcast(
        self,
        event_type: str,
        data: dict[str, Any],
        task_id: str | None = None,
    ) -> None:
        self.events.append({
            "event_type": event_type,
            "data": data,
            "task_id": task_id,
        })


# ============================================================
# 辅助函数
# ============================================================


def _make_tool_meta(
    name: str = "shell_exec",
    side_effect: SideEffectLevel = SideEffectLevel.IRREVERSIBLE,
    profile: ToolProfile = ToolProfile.STANDARD,
) -> ToolMeta:
    return ToolMeta(
        name=name,
        description="测试工具",
        parameters_json_schema={"type": "object"},
        side_effect_level=side_effect,
        tool_profile=profile,
        tool_group="test",
    )


def _make_context(task_id: str = "task-001") -> ExecutionContext:
    return ExecutionContext(task_id=task_id, trace_id="trace-001")


def _make_request(
    approval_id: str = "test-001",
    task_id: str = "task-001",
    tool_name: str = "shell_exec",
    timeout_s: float = 120.0,
) -> ApprovalRequest:
    now = datetime.now(timezone.utc)
    return ApprovalRequest(
        approval_id=approval_id,
        task_id=task_id,
        tool_name=tool_name,
        tool_args_summary="command: rm -rf /tmp/***",
        risk_explanation="不可逆 shell 命令",
        policy_label="global.irreversible",
        side_effect_level=SideEffectLevel.IRREVERSIBLE,
        expires_at=now + timedelta(seconds=timeout_s),
    )


# ============================================================
# T051: 事件全链路集成测试
# ============================================================


class TestAllowPathEventChain:
    """allow 路径事件链: POLICY_DECISION(allow)"""

    async def test_allow_produces_no_approval_events(self) -> None:
        """allow 路径不产生审批事件"""
        broadcaster = MockSSEBroadcaster()
        manager = ApprovalManager(sse_broadcaster=broadcaster)

        # 使用 allow 步骤
        def allow_eval(tool_meta, params, context):
            return PolicyDecision(
                action=PolicyAction.ALLOW,
                label="test.allow",
                reason="直接放行",
            )

        hook = PolicyCheckHook(
            steps=[PolicyStep(evaluator=allow_eval, label="test")],
            approval_manager=manager,
        )

        result = await hook.before_execute(
            tool_meta=_make_tool_meta(side_effect=SideEffectLevel.NONE),
            args={},
            context=_make_context(),
        )

        assert result.proceed is True
        # 无审批事件（allow 不需要审批）
        assert len(broadcaster.events) == 0


class TestAskApproveEventChain:
    """ask -> approve 事件链: POLICY_DECISION + APPROVAL_REQUESTED + APPROVAL_APPROVED"""

    async def test_ask_approve_full_chain(self) -> None:
        """ask -> approve 产生完整事件链"""
        broadcaster = MockSSEBroadcaster()
        manager = ApprovalManager(sse_broadcaster=broadcaster)

        def ask_eval(tool_meta, params, context):
            return PolicyDecision(
                action=PolicyAction.ASK,
                label="test.ask",
                reason="需审批",
            )

        hook = PolicyCheckHook(
            steps=[PolicyStep(evaluator=ask_eval, label="test")],
            approval_manager=manager,
        )

        async def approve_later():
            await asyncio.sleep(0.05)
            pending = manager.get_pending_approvals()
            if pending:
                await manager.resolve(
                    pending[0].request.approval_id,
                    ApprovalDecision.ALLOW_ONCE,
                )

        task = asyncio.create_task(approve_later())
        result = await hook.before_execute(
            tool_meta=_make_tool_meta(),
            args={"command": "test"},
            context=_make_context(),
        )
        await task

        assert result.proceed is True

        # 验证 SSE 事件链
        assert len(broadcaster.events) >= 2
        event_types = [e["event_type"] for e in broadcaster.events]
        assert "approval:requested" in event_types
        assert "approval:resolved" in event_types


class TestAskDenyEventChain:
    """ask -> deny 事件链"""

    async def test_ask_deny_full_chain(self) -> None:
        """ask -> deny 产生拒绝事件链"""
        broadcaster = MockSSEBroadcaster()
        manager = ApprovalManager(sse_broadcaster=broadcaster)

        def ask_eval(tool_meta, params, context):
            return PolicyDecision(
                action=PolicyAction.ASK,
                label="test.ask",
                reason="需审批",
            )

        hook = PolicyCheckHook(
            steps=[PolicyStep(evaluator=ask_eval, label="test")],
            approval_manager=manager,
        )

        async def deny_later():
            await asyncio.sleep(0.05)
            pending = manager.get_pending_approvals()
            if pending:
                await manager.resolve(
                    pending[0].request.approval_id,
                    ApprovalDecision.DENY,
                )

        task = asyncio.create_task(deny_later())
        result = await hook.before_execute(
            tool_meta=_make_tool_meta(),
            args={},
            context=_make_context(),
        )
        await task

        assert result.proceed is False

        # 验证事件链包含 resolved
        event_types = [e["event_type"] for e in broadcaster.events]
        assert "approval:requested" in event_types
        assert "approval:resolved" in event_types


# ============================================================
# T052: 策略配置变更事件测试
# ============================================================


class TestPolicyConfigChangedEvent:
    """FR-027: 策略配置变更事件"""

    async def test_update_profile_writes_event(self) -> None:
        """update_profile() 写入 POLICY_CONFIG_CHANGED 事件"""
        event_store = MockEventStore()
        engine = PolicyEngine(
            profile=DEFAULT_PROFILE,
            event_store=event_store,
        )

        await engine.update_profile(STRICT_PROFILE)

        # 验证事件写入
        assert len(event_store.events) >= 1
        last_event = event_store.events[-1]
        assert last_event.type == "POLICY_CONFIG_CHANGED"

        # 验证 payload 包含差异
        payload = last_event.payload
        assert "old_profile" in payload
        assert "new_profile" in payload
        assert "diff" in payload
        assert payload["old_profile"]["name"] == "default"
        assert payload["new_profile"]["name"] == "strict"

    async def test_update_profile_twice_uses_incremental_task_seq(self) -> None:
        """连续更新配置时 task_seq 应递增，避免唯一键冲突。"""
        event_store = MockEventStore()
        engine = PolicyEngine(
            profile=DEFAULT_PROFILE,
            event_store=event_store,
        )

        await engine.update_profile(STRICT_PROFILE)
        await engine.update_profile(DEFAULT_PROFILE)

        assert len(event_store.events) >= 2
        last_two = event_store.events[-2:]
        assert [event.task_seq for event in last_two] == [1, 2]

    async def test_update_profile_diff_calculation(self) -> None:
        """配置差异计算正确"""
        event_store = MockEventStore()
        engine = PolicyEngine(
            profile=DEFAULT_PROFILE,
            event_store=event_store,
        )

        await engine.update_profile(STRICT_PROFILE)

        payload = event_store.events[-1].payload
        diff = payload["diff"]

        # 验证关键差异
        assert "name" in diff
        assert diff["name"]["old"] == "default"
        assert diff["name"]["new"] == "strict"
        assert "reversible_action" in diff

    async def test_update_profile_changes_engine(self) -> None:
        """update_profile() 更新 Engine 内部状态"""
        engine = PolicyEngine(profile=DEFAULT_PROFILE)

        assert engine.profile.name == "default"

        await engine.update_profile(STRICT_PROFILE)

        assert engine.profile.name == "strict"


# ============================================================
# T056: 完整审批流程端到端测试
# ============================================================


class TestEndToEndApprovalFlow:
    """端到端审批流程"""

    async def test_irreversible_tool_full_flow(self) -> None:
        """irreversible 工具完整流程: Pipeline ask -> 注册 -> 等待 -> 批准 -> 执行"""
        broadcaster = MockSSEBroadcaster()
        engine = PolicyEngine(
            profile=DEFAULT_PROFILE,
            sse_broadcaster=broadcaster,
        )

        hook = engine.hook

        async def approve_later():
            await asyncio.sleep(0.05)
            pending = engine.approval_manager.get_pending_approvals()
            if pending:
                await engine.approval_manager.resolve(
                    pending[0].request.approval_id,
                    ApprovalDecision.ALLOW_ONCE,
                )

        task = asyncio.create_task(approve_later())
        result = await hook.before_execute(
            tool_meta=_make_tool_meta(side_effect=SideEffectLevel.IRREVERSIBLE),
            args={"command": "rm -rf /tmp/test"},
            context=_make_context(),
        )
        await task

        assert result.proceed is True

    async def test_safe_tool_no_approval(self) -> None:
        """安全工具无需审批直接执行"""
        engine = PolicyEngine(profile=DEFAULT_PROFILE)
        hook = engine.hook

        result = await hook.before_execute(
            tool_meta=_make_tool_meta(
                name="read_file",
                side_effect=SideEffectLevel.NONE,
            ),
            args={"path": "/tmp/test.txt"},
            context=_make_context(),
        )

        assert result.proceed is True


# ============================================================
# T057: Task 状态机流转集成测试
# ============================================================


class TestTaskStateMachineFlow:
    """Task 状态机集成"""

    def test_running_to_waiting_approval(self) -> None:
        """RUNNING -> WAITING_APPROVAL 合法"""
        from octoagent.core.models.enums import TaskStatus, validate_transition

        assert validate_transition(TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL) is True

    def test_waiting_approval_to_running(self) -> None:
        """WAITING_APPROVAL -> RUNNING 合法（用户批准后恢复）"""
        from octoagent.core.models.enums import TaskStatus, validate_transition

        assert validate_transition(TaskStatus.WAITING_APPROVAL, TaskStatus.RUNNING) is True

    def test_waiting_approval_to_rejected(self) -> None:
        """WAITING_APPROVAL -> REJECTED 合法（用户拒绝/超时）"""
        from octoagent.core.models.enums import TaskStatus, validate_transition

        assert validate_transition(TaskStatus.WAITING_APPROVAL, TaskStatus.REJECTED) is True

    def test_invalid_transitions_blocked(self) -> None:
        """非法转换被拒绝"""
        from octoagent.core.models.enums import TaskStatus, validate_transition

        # WAITING_APPROVAL 不能直接到 SUCCEEDED
        assert validate_transition(TaskStatus.WAITING_APPROVAL, TaskStatus.SUCCEEDED) is False
        # CREATED 不能直接到 WAITING_APPROVAL
        assert validate_transition(TaskStatus.CREATED, TaskStatus.WAITING_APPROVAL) is False


# ============================================================
# T058: 启动恢复集成测试
# ============================================================


class TestStartupRecovery:
    """启动恢复"""

    async def test_recover_pending_approvals(self) -> None:
        """恢复 pending 审批"""
        manager = ApprovalManager()

        # 注册一个有效的审批
        request = _make_request(approval_id="pending-001", timeout_s=3600.0)
        await manager.register(request)

        # 模拟恢复
        recovered = await manager.recover_from_store()
        assert recovered == 1

    async def test_recover_expired_auto_deny(self) -> None:
        """恢复时过期的审批自动标记 EXPIRED"""
        manager = ApprovalManager()

        # 注册一个已过期的审批
        expired_req = ApprovalRequest(
            approval_id="expired-001",
            task_id="task-001",
            tool_name="shell_exec",
            tool_args_summary="test",
            risk_explanation="test",
            policy_label="test",
            side_effect_level=SideEffectLevel.IRREVERSIBLE,
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=10),
        )
        await manager.register(expired_req)

        await manager.recover_from_store()

        record = manager.get_approval("expired-001")
        assert record is not None
        assert record.status == ApprovalStatus.EXPIRED

    async def test_policy_engine_startup(self) -> None:
        """PolicyEngine startup 恢复"""
        engine = PolicyEngine(profile=DEFAULT_PROFILE)
        recovered = await engine.startup()
        assert recovered == 0  # 空启动，无需恢复
