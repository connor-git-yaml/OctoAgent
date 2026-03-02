"""PolicyCheckpoint Protocol 契约测试 -- T028

验证 PolicyCheckHook 满足 Feature 004 BeforeHook Protocol:
- name 属性
- priority 属性
- fail_mode 属性
- before_execute() 签名

覆盖 FR-015: PolicyCheckpoint Protocol 契约
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from octoagent.policy.approval_manager import ApprovalManager
from octoagent.policy.policy_check_hook import PolicyCheckHook
from octoagent.tooling.models import (
    BeforeHookResult,
    ExecutionContext,
    FailMode,
    SideEffectLevel,
    ToolMeta,
    ToolProfile,
)
from octoagent.tooling.protocols import BeforeHook


# ============================================================
# 辅助函数
# ============================================================


def _make_hook() -> PolicyCheckHook:
    """创建测试用 PolicyCheckHook"""
    return PolicyCheckHook(
        steps=[],
        approval_manager=ApprovalManager(),
    )


def _make_tool_meta() -> ToolMeta:
    """创建测试用 ToolMeta"""
    return ToolMeta(
        name="test_tool",
        description="测试工具",
        parameters_json_schema={"type": "object"},
        side_effect_level=SideEffectLevel.NONE,
        tool_profile=ToolProfile.STANDARD,
        tool_group="test",
    )


def _make_context() -> ExecutionContext:
    """创建测试用 ExecutionContext"""
    return ExecutionContext(
        task_id="task-001",
        trace_id="trace-001",
    )


# ============================================================
# Protocol 契约验证
# ============================================================


class TestBeforeHookProtocolConformance:
    """验证 PolicyCheckHook 满足 BeforeHook Protocol"""

    def test_has_name_property(self) -> None:
        """PolicyCheckHook 具有 name 属性"""
        hook = _make_hook()
        assert hasattr(hook, "name")
        assert isinstance(hook.name, str)
        assert len(hook.name) > 0

    def test_has_priority_property(self) -> None:
        """PolicyCheckHook 具有 priority 属性"""
        hook = _make_hook()
        assert hasattr(hook, "priority")
        assert isinstance(hook.priority, int)

    def test_has_fail_mode_property(self) -> None:
        """PolicyCheckHook 具有 fail_mode 属性"""
        hook = _make_hook()
        assert hasattr(hook, "fail_mode")
        assert isinstance(hook.fail_mode, FailMode)

    def test_has_before_execute_method(self) -> None:
        """PolicyCheckHook 具有 before_execute 方法"""
        hook = _make_hook()
        assert hasattr(hook, "before_execute")
        assert callable(hook.before_execute)

    def test_before_execute_is_async(self) -> None:
        """before_execute 是异步方法"""
        hook = _make_hook()
        assert inspect.iscoroutinefunction(hook.before_execute)

    def test_before_execute_signature(self) -> None:
        """before_execute 签名包含正确参数"""
        hook = _make_hook()
        sig = inspect.signature(hook.before_execute)
        param_names = list(sig.parameters.keys())

        # 验证参数名
        assert "tool_meta" in param_names
        assert "args" in param_names
        assert "context" in param_names

    async def test_before_execute_returns_before_hook_result(self) -> None:
        """before_execute 返回 BeforeHookResult"""
        hook = _make_hook()
        result = await hook.before_execute(
            tool_meta=_make_tool_meta(),
            args={},
            context=_make_context(),
        )
        assert isinstance(result, BeforeHookResult)

    def test_structural_subtyping_compatibility(self) -> None:
        """PolicyCheckHook 满足 BeforeHook Protocol 的结构化子类型"""
        hook = _make_hook()

        # 通过 Protocol 的结构化类型检查
        # 验证所有必需的属性和方法存在
        protocol_methods = ["before_execute"]
        protocol_properties = ["name", "priority", "fail_mode"]

        for prop in protocol_properties:
            assert hasattr(hook, prop), f"缺少 Protocol 属性: {prop}"

        for method in protocol_methods:
            assert hasattr(hook, method), f"缺少 Protocol 方法: {method}"
            assert callable(getattr(hook, method)), f"{method} 不是可调用的"


class TestBeforeHookProtocolValues:
    """验证 PolicyCheckHook 的 Protocol 属性值符合预期"""

    def test_name_is_policy_checkpoint(self) -> None:
        """name 为 'policy_checkpoint'"""
        hook = _make_hook()
        assert hook.name == "policy_checkpoint"

    def test_priority_is_zero(self) -> None:
        """priority 为 0（最高优先级）"""
        hook = _make_hook()
        assert hook.priority == 0

    def test_fail_mode_is_closed(self) -> None:
        """fail_mode 为 CLOSED（安全优先）"""
        hook = _make_hook()
        assert hook.fail_mode == FailMode.CLOSED


class TestBeforeHookResultContract:
    """验证返回的 BeforeHookResult 符合契约"""

    async def test_allow_result_has_proceed_true(self) -> None:
        """允许执行时 proceed=True"""
        hook = _make_hook()
        result = await hook.before_execute(
            tool_meta=_make_tool_meta(),
            args={},
            context=_make_context(),
        )
        # 空 steps 返回 allow
        assert result.proceed is True

    async def test_result_has_proceed_field(self) -> None:
        """结果包含 proceed 字段"""
        hook = _make_hook()
        result = await hook.before_execute(
            tool_meta=_make_tool_meta(),
            args={},
            context=_make_context(),
        )
        assert hasattr(result, "proceed")
        assert isinstance(result.proceed, bool)

    async def test_result_has_rejection_reason_field(self) -> None:
        """结果包含 rejection_reason 字段"""
        hook = _make_hook()
        result = await hook.before_execute(
            tool_meta=_make_tool_meta(),
            args={},
            context=_make_context(),
        )
        assert hasattr(result, "rejection_reason")
