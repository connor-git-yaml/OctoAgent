"""PolicyCheckHook 单元测试 -- T027, T029

覆盖:
- allow 决策映射为 proceed=True (T029/US2)
- deny 决策映射为 proceed=False (T027/US1)
- ask 决策映射为注册+等待+proceed (T027/US1)
- evaluator 异常时 fail_mode=closed 拒绝 (EC-3) (T027)
- 参数脱敏 (FR-028) (T027)
- none 工具 -> allow -> proceed=True 无审批 (T029/US2)
- reversible 工具 -> allow -> proceed=True 无审批 (T029/US2)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from octoagent.policy.approval_manager import ApprovalManager
from octoagent.policy.models import (
    ApprovalDecision,
    ApprovalRecord,
    ApprovalStatus,
    PolicyAction,
    PolicyDecision,
    PolicyProfile,
    PolicyStep,
)
from octoagent.policy.policy_check_hook import PolicyCheckHook
from octoagent.tooling.models import (
    BeforeHookResult,
    ExecutionContext,
    FailMode,
    SideEffectLevel,
    ToolMeta,
    ToolProfile,
)


# ============================================================
# 测试辅助函数
# ============================================================


def _make_tool_meta(
    name: str = "test_tool",
    side_effect: SideEffectLevel = SideEffectLevel.NONE,
    profile: ToolProfile = ToolProfile.STANDARD,
) -> ToolMeta:
    """创建测试用 ToolMeta"""
    return ToolMeta(
        name=name,
        description="测试工具",
        parameters_json_schema={"type": "object"},
        side_effect_level=side_effect,
        tool_profile=profile,
        tool_group="test",
    )


def _make_context() -> ExecutionContext:
    """创建测试用 ExecutionContext"""
    return ExecutionContext(
        task_id="task-001",
        trace_id="trace-001",
    )


def _make_allow_step() -> PolicyStep:
    """创建返回 allow 的步骤"""
    def evaluator(tool_meta: ToolMeta, params: dict, context: ExecutionContext) -> PolicyDecision:
        return PolicyDecision(
            action=PolicyAction.ALLOW,
            label="test.allow",
            reason="测试放行",
        )
    return PolicyStep(evaluator=evaluator, label="test_allow")


def _make_deny_step() -> PolicyStep:
    """创建返回 deny 的步骤"""
    def evaluator(tool_meta: ToolMeta, params: dict, context: ExecutionContext) -> PolicyDecision:
        return PolicyDecision(
            action=PolicyAction.DENY,
            label="test.deny",
            reason="测试拒绝",
        )
    return PolicyStep(evaluator=evaluator, label="test_deny")


def _make_ask_step() -> PolicyStep:
    """创建返回 ask 的步骤"""
    def evaluator(tool_meta: ToolMeta, params: dict, context: ExecutionContext) -> PolicyDecision:
        return PolicyDecision(
            action=PolicyAction.ASK,
            label="test.ask",
            reason="测试需审批",
        )
    return PolicyStep(evaluator=evaluator, label="test_ask")


def _make_error_step() -> PolicyStep:
    """创建抛出异常的步骤"""
    def evaluator(tool_meta: ToolMeta, params: dict, context: ExecutionContext) -> PolicyDecision:
        raise RuntimeError("评估器内部错误")
    return PolicyStep(evaluator=evaluator, label="test_error")


# ============================================================
# T027: PolicyCheckHook 单元测试
# ============================================================


class TestAllowDecisionMapping:
    """allow 决策映射为 proceed=True"""

    async def test_allow_returns_proceed_true(self) -> None:
        """Pipeline 返回 allow 时，before_execute 返回 proceed=True"""
        manager = ApprovalManager()
        hook = PolicyCheckHook(
            steps=[_make_allow_step()],
            approval_manager=manager,
        )

        result = await hook.before_execute(
            tool_meta=_make_tool_meta(),
            args={},
            context=_make_context(),
        )

        assert isinstance(result, BeforeHookResult)
        assert result.proceed is True

    async def test_allow_no_rejection_reason(self) -> None:
        """allow 决策不包含拒绝原因"""
        manager = ApprovalManager()
        hook = PolicyCheckHook(
            steps=[_make_allow_step()],
            approval_manager=manager,
        )

        result = await hook.before_execute(
            tool_meta=_make_tool_meta(),
            args={},
            context=_make_context(),
        )

        assert result.rejection_reason is None


class TestDenyDecisionMapping:
    """deny 决策映射为 proceed=False"""

    async def test_deny_returns_proceed_false(self) -> None:
        """Pipeline 返回 deny 时，before_execute 返回 proceed=False"""
        manager = ApprovalManager()
        hook = PolicyCheckHook(
            steps=[_make_deny_step()],
            approval_manager=manager,
        )

        result = await hook.before_execute(
            tool_meta=_make_tool_meta(),
            args={},
            context=_make_context(),
        )

        assert result.proceed is False

    async def test_deny_includes_rejection_reason(self) -> None:
        """deny 决策包含拒绝原因"""
        manager = ApprovalManager()
        hook = PolicyCheckHook(
            steps=[_make_deny_step()],
            approval_manager=manager,
        )

        result = await hook.before_execute(
            tool_meta=_make_tool_meta(),
            args={},
            context=_make_context(),
        )

        assert result.rejection_reason is not None
        assert "策略拒绝" in result.rejection_reason


class TestAskDecisionMapping:
    """ask 决策映射为注册+等待+proceed"""

    async def test_ask_approved_returns_proceed_true(self) -> None:
        """ask 决策审批通过后返回 proceed=True"""
        manager = ApprovalManager()
        hook = PolicyCheckHook(
            steps=[_make_ask_step()],
            approval_manager=manager,
        )

        # 在后台线程中解决审批
        async def approve_later() -> None:
            await asyncio.sleep(0.05)
            # 找到 pending 审批并解决
            pending = manager.get_pending_approvals()
            if pending:
                approval_id = pending[0].request.approval_id
                await manager.resolve(approval_id, ApprovalDecision.ALLOW_ONCE)

        task = asyncio.create_task(approve_later())
        result = await hook.before_execute(
            tool_meta=_make_tool_meta(side_effect=SideEffectLevel.IRREVERSIBLE),
            args={"command": "rm -rf /tmp/test"},
            context=_make_context(),
        )
        await task

        assert result.proceed is True

    async def test_ask_denied_returns_proceed_false(self) -> None:
        """ask 决策审批拒绝后返回 proceed=False"""
        manager = ApprovalManager()
        hook = PolicyCheckHook(
            steps=[_make_ask_step()],
            approval_manager=manager,
        )

        async def deny_later() -> None:
            await asyncio.sleep(0.05)
            pending = manager.get_pending_approvals()
            if pending:
                approval_id = pending[0].request.approval_id
                await manager.resolve(approval_id, ApprovalDecision.DENY)

        task = asyncio.create_task(deny_later())
        result = await hook.before_execute(
            tool_meta=_make_tool_meta(side_effect=SideEffectLevel.IRREVERSIBLE),
            args={"command": "rm -rf /tmp/test"},
            context=_make_context(),
        )
        await task

        assert result.proceed is False
        assert result.rejection_reason is not None
        assert "用户拒绝" in result.rejection_reason

    async def test_ask_timeout_returns_proceed_false(self) -> None:
        """ask 决策超时后返回 proceed=False"""
        manager = ApprovalManager(default_timeout_s=0.1)

        # 使用短超时的 profile
        profile = PolicyProfile(
            name="test",
            approval_timeout_seconds=0.1,
        )
        hook = PolicyCheckHook(
            steps=[_make_ask_step()],
            approval_manager=manager,
            profile=profile,
        )

        result = await hook.before_execute(
            tool_meta=_make_tool_meta(side_effect=SideEffectLevel.IRREVERSIBLE),
            args={},
            context=_make_context(),
        )

        assert result.proceed is False
        assert result.rejection_reason is not None
        assert "超时" in result.rejection_reason

    async def test_ask_allow_always_auto_approves(self) -> None:
        """ask 决策命中 allow-always 白名单时自动放行"""
        manager = ApprovalManager()

        # 先注册一个审批并 allow-always
        from octoagent.policy.models import ApprovalRequest
        now = datetime.now(timezone.utc)
        req = ApprovalRequest(
            approval_id="pre-001",
            task_id="task-000",
            tool_name="my_tool",
            tool_args_summary="test",
            risk_explanation="test",
            policy_label="test",
            side_effect_level=SideEffectLevel.IRREVERSIBLE,
            expires_at=now + timedelta(seconds=120),
        )
        await manager.register(req)
        await manager.resolve("pre-001", ApprovalDecision.ALLOW_ALWAYS)

        hook = PolicyCheckHook(
            steps=[_make_ask_step()],
            approval_manager=manager,
        )

        result = await hook.before_execute(
            tool_meta=_make_tool_meta(name="my_tool", side_effect=SideEffectLevel.IRREVERSIBLE),
            args={},
            context=_make_context(),
        )

        assert result.proceed is True


class TestFailClosedOnException:
    """EC-3: evaluator 异常时 fail_mode=closed 拒绝"""

    async def test_exception_returns_proceed_false(self) -> None:
        """评估器异常时返回 proceed=False"""
        manager = ApprovalManager()
        hook = PolicyCheckHook(
            steps=[_make_error_step()],
            approval_manager=manager,
        )

        result = await hook.before_execute(
            tool_meta=_make_tool_meta(),
            args={},
            context=_make_context(),
        )

        assert result.proceed is False

    async def test_exception_includes_reason(self) -> None:
        """异常时包含错误说明"""
        manager = ApprovalManager()
        hook = PolicyCheckHook(
            steps=[_make_error_step()],
            approval_manager=manager,
        )

        result = await hook.before_execute(
            tool_meta=_make_tool_meta(),
            args={},
            context=_make_context(),
        )

        assert result.rejection_reason is not None
        assert "异常" in result.rejection_reason or "fail-closed" in result.rejection_reason


class TestHookProperties:
    """Hook 属性测试"""

    def test_name(self) -> None:
        """hook 名称为 policy_checkpoint"""
        manager = ApprovalManager()
        hook = PolicyCheckHook(
            steps=[],
            approval_manager=manager,
        )
        assert hook.name == "policy_checkpoint"

    def test_priority(self) -> None:
        """hook 优先级为 0（最高）"""
        manager = ApprovalManager()
        hook = PolicyCheckHook(
            steps=[],
            approval_manager=manager,
        )
        assert hook.priority == 0

    def test_fail_mode(self) -> None:
        """hook fail_mode 为 CLOSED"""
        manager = ApprovalManager()
        hook = PolicyCheckHook(
            steps=[],
            approval_manager=manager,
        )
        assert hook.fail_mode == FailMode.CLOSED


class TestParameterSanitization:
    """FR-028: 参数脱敏"""

    async def test_args_summary_generation(self) -> None:
        """工具参数被脱敏后生成摘要"""
        manager = ApprovalManager()
        hook = PolicyCheckHook(
            steps=[_make_ask_step()],
            approval_manager=manager,
        )

        # 测试 _generate_args_summary 内部方法
        summary = hook._generate_args_summary({"password": "secret123", "command": "ls"})
        assert isinstance(summary, str)
        assert len(summary) > 0

    async def test_empty_args_summary(self) -> None:
        """空参数生成默认摘要"""
        manager = ApprovalManager()
        hook = PolicyCheckHook(
            steps=[_make_ask_step()],
            approval_manager=manager,
        )

        summary = hook._generate_args_summary({})
        assert summary == "(无参数)"

    async def test_long_value_truncation(self) -> None:
        """过长的参数值被截断"""
        manager = ApprovalManager()
        hook = PolicyCheckHook(
            steps=[_make_ask_step()],
            approval_manager=manager,
        )

        long_value = "x" * 200
        summary = hook._generate_args_summary({"data": long_value})
        # 验证摘要包含截断标记
        assert "..." in summary


# ============================================================
# T029: 安全操作直接执行测试
# ============================================================


class TestSafeOperationDirectExecution:
    """US2: 安全操作直接执行，无审批"""

    async def test_none_tool_direct_allow(self) -> None:
        """none 副作用工具直接放行"""
        manager = ApprovalManager()
        hook = PolicyCheckHook(
            steps=[_make_allow_step()],
            approval_manager=manager,
        )

        result = await hook.before_execute(
            tool_meta=_make_tool_meta(
                name="read_file",
                side_effect=SideEffectLevel.NONE,
            ),
            args={"path": "/tmp/test.txt"},
            context=_make_context(),
        )

        assert result.proceed is True
        # 确认没有创建审批请求
        assert len(manager.get_pending_approvals()) == 0

    async def test_reversible_tool_direct_allow(self) -> None:
        """reversible 副作用工具在默认 Profile 下直接放行"""
        manager = ApprovalManager()
        hook = PolicyCheckHook(
            steps=[_make_allow_step()],
            approval_manager=manager,
        )

        result = await hook.before_execute(
            tool_meta=_make_tool_meta(
                name="write_file",
                side_effect=SideEffectLevel.REVERSIBLE,
            ),
            args={"path": "/tmp/test.txt", "content": "hello"},
            context=_make_context(),
        )

        assert result.proceed is True
        assert len(manager.get_pending_approvals()) == 0

    async def test_no_steps_direct_allow(self) -> None:
        """空 Pipeline（无评估步骤）时直接放行"""
        manager = ApprovalManager()
        hook = PolicyCheckHook(
            steps=[],
            approval_manager=manager,
        )

        result = await hook.before_execute(
            tool_meta=_make_tool_meta(),
            args={},
            context=_make_context(),
        )

        assert result.proceed is True
        assert len(manager.get_pending_approvals()) == 0
