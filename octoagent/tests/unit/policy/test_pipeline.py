"""Pipeline 纯函数测试 -- T015

覆盖:
- 空 steps 返回 allow
- 单层 deny 短路
- 多层"只收紧不放松"
- label 链完整性
- 异常处理（fail-closed）
"""

import pytest

from octoagent.policy.models import PolicyAction, PolicyDecision, PolicyStep
from octoagent.policy.pipeline import evaluate_pipeline
from octoagent.tooling.models import (
    ExecutionContext,
    SideEffectLevel,
    ToolMeta,
    ToolProfile,
)


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


def _allow_evaluator(
    tool_meta: ToolMeta,
    params: dict,
    context: ExecutionContext,
) -> PolicyDecision:
    """总是返回 allow"""
    return PolicyDecision(
        action=PolicyAction.ALLOW,
        label="test.allow",
        reason="测试放行",
    )


def _ask_evaluator(
    tool_meta: ToolMeta,
    params: dict,
    context: ExecutionContext,
) -> PolicyDecision:
    """总是返回 ask"""
    return PolicyDecision(
        action=PolicyAction.ASK,
        label="test.ask",
        reason="测试需审批",
    )


def _deny_evaluator(
    tool_meta: ToolMeta,
    params: dict,
    context: ExecutionContext,
) -> PolicyDecision:
    """总是返回 deny"""
    return PolicyDecision(
        action=PolicyAction.DENY,
        label="test.deny",
        reason="测试拒绝",
    )


def _error_evaluator(
    tool_meta: ToolMeta,
    params: dict,
    context: ExecutionContext,
) -> PolicyDecision:
    """总是抛异常"""
    raise RuntimeError("评估器内部错误")


class TestEmptyPipeline:
    """空 steps 列表"""

    def test_returns_allow(self) -> None:
        """空 Pipeline 返回 allow"""
        decision, trace = evaluate_pipeline(
            steps=[],
            tool_meta=_make_tool_meta(),
            params={},
            context=_make_context(),
        )
        assert decision.action == PolicyAction.ALLOW
        assert decision.label == "pipeline.default"
        assert len(trace) == 1


class TestSingleLayerDenyShortCircuit:
    """单层 deny 短路"""

    def test_deny_stops_pipeline(self) -> None:
        """deny 后不执行后续层"""
        steps = [
            PolicyStep(evaluator=_deny_evaluator, label="deny_layer"),
            PolicyStep(evaluator=_allow_evaluator, label="should_not_run"),
        ]
        decision, trace = evaluate_pipeline(
            steps=steps,
            tool_meta=_make_tool_meta(),
            params={},
            context=_make_context(),
        )
        assert decision.action == PolicyAction.DENY
        assert len(trace) == 1  # 只执行了第一层

    def test_deny_returns_correct_label(self) -> None:
        """deny 决策包含正确的 label"""
        steps = [
            PolicyStep(evaluator=_deny_evaluator, label="my_deny"),
        ]
        decision, trace = evaluate_pipeline(
            steps=steps,
            tool_meta=_make_tool_meta(),
            params={},
            context=_make_context(),
        )
        assert decision.label == "test.deny"


class TestTightenOnly:
    """多层只收紧不放松"""

    def test_allow_then_ask_tightens_to_ask(self) -> None:
        """allow -> ask => 最终 ask"""
        steps = [
            PolicyStep(evaluator=_allow_evaluator, label="layer1"),
            PolicyStep(evaluator=_ask_evaluator, label="layer2"),
        ]
        decision, trace = evaluate_pipeline(
            steps=steps,
            tool_meta=_make_tool_meta(),
            params={},
            context=_make_context(),
        )
        assert decision.action == PolicyAction.ASK
        assert len(trace) == 2

    def test_ask_then_allow_stays_ask(self) -> None:
        """ask -> allow => 最终 ask（不放松）"""
        steps = [
            PolicyStep(evaluator=_ask_evaluator, label="layer1"),
            PolicyStep(evaluator=_allow_evaluator, label="layer2"),
        ]
        decision, trace = evaluate_pipeline(
            steps=steps,
            tool_meta=_make_tool_meta(),
            params={},
            context=_make_context(),
        )
        assert decision.action == PolicyAction.ASK
        assert len(trace) == 2

    def test_allow_then_allow_stays_allow(self) -> None:
        """allow -> allow => 最终 allow"""
        steps = [
            PolicyStep(evaluator=_allow_evaluator, label="layer1"),
            PolicyStep(evaluator=_allow_evaluator, label="layer2"),
        ]
        decision, trace = evaluate_pipeline(
            steps=steps,
            tool_meta=_make_tool_meta(),
            params={},
            context=_make_context(),
        )
        assert decision.action == PolicyAction.ALLOW
        assert len(trace) == 2


class TestTraceCompleteness:
    """label 链完整性"""

    def test_all_layers_in_trace(self) -> None:
        """所有层的评估结果都在 trace 中"""
        steps = [
            PolicyStep(evaluator=_allow_evaluator, label="layer1"),
            PolicyStep(evaluator=_ask_evaluator, label="layer2"),
        ]
        _, trace = evaluate_pipeline(
            steps=steps,
            tool_meta=_make_tool_meta(),
            params={},
            context=_make_context(),
        )
        assert len(trace) == 2
        assert trace[0].label == "test.allow"
        assert trace[1].label == "test.ask"

    def test_deny_trace_stops_at_deny(self) -> None:
        """deny 短路后 trace 只包含到 deny 的层"""
        steps = [
            PolicyStep(evaluator=_allow_evaluator, label="layer1"),
            PolicyStep(evaluator=_deny_evaluator, label="layer2"),
            PolicyStep(evaluator=_allow_evaluator, label="layer3"),
        ]
        _, trace = evaluate_pipeline(
            steps=steps,
            tool_meta=_make_tool_meta(),
            params={},
            context=_make_context(),
        )
        assert len(trace) == 2


class TestExceptionHandling:
    """异常处理（fail-closed）"""

    def test_evaluator_exception_produces_deny(self) -> None:
        """评估器异常 -> deny（fail-closed）"""
        steps = [
            PolicyStep(evaluator=_error_evaluator, label="error_layer"),
        ]
        decision, trace = evaluate_pipeline(
            steps=steps,
            tool_meta=_make_tool_meta(),
            params={},
            context=_make_context(),
        )
        assert decision.action == PolicyAction.DENY
        assert "异常" in decision.reason
        assert len(trace) == 1

    def test_exception_short_circuits(self) -> None:
        """异常产生 deny，后续层不再执行"""
        steps = [
            PolicyStep(evaluator=_error_evaluator, label="error_layer"),
            PolicyStep(evaluator=_allow_evaluator, label="should_not_run"),
        ]
        decision, trace = evaluate_pipeline(
            steps=steps,
            tool_meta=_make_tool_meta(),
            params={},
            context=_make_context(),
        )
        assert decision.action == PolicyAction.DENY
        assert len(trace) == 1
