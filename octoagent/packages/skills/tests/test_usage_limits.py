"""UsageLimits / UsageTracker 单元测试 (T1.17 + T3.2-T3.4)。

覆盖：
- 各维度独立触发
- 组合触发（先触发的维度优先返回）
- LoopGuardPolicy.to_usage_limits() 转换
- 超时检测
- 浮点容差（budget）
- to_dict() 序列化
- 成本熔断器边界（T3.2-T3.4）
"""

from __future__ import annotations

import time

import pytest
from octoagent.skills.models import (
    ErrorCategory,
    LoopGuardPolicy,
    UsageLimits,
    UsageTracker,
)


# ═══════════════════════════════════════
# UsageLimits 模型测试
# ═══════════════════════════════════════


class TestUsageLimits:
    def test_defaults(self) -> None:
        limits = UsageLimits()
        assert limits.max_steps is None  # 不限步数
        assert limits.max_request_tokens is None
        assert limits.max_response_tokens is None
        assert limits.max_tool_calls is None  # 不限工具调用
        assert limits.max_budget_usd is None
        assert limits.max_duration_seconds == 7200.0  # 统一 2 小时
        assert limits.repeat_signature_threshold == 10

    def test_custom_values(self) -> None:
        limits = UsageLimits(
            max_steps=100,
            max_request_tokens=50000,
            max_budget_usd=1.5,
            max_duration_seconds=120.0,
        )
        assert limits.max_steps == 100
        assert limits.max_request_tokens == 50000
        assert limits.max_budget_usd == 1.5
        assert limits.max_duration_seconds == 120.0

    def test_max_steps_none_means_unlimited(self) -> None:
        """max_steps=None 表示不限步数。"""
        limits = UsageLimits()
        assert limits.max_steps is None

    def test_max_steps_large_value_accepted(self) -> None:
        """max_steps 不再有硬上限（旧的 _MAX_STEPS_HARD_CEILING 仅用于降级 clamp）。"""
        limits = UsageLimits(max_steps=10000)
        assert limits.max_steps == 10000


# ═══════════════════════════════════════
# LoopGuardPolicy.to_usage_limits()
# ═══════════════════════════════════════


class TestLoopGuardPolicyConversion:
    def test_basic_conversion(self) -> None:
        policy = LoopGuardPolicy(max_steps=50, repeat_signature_threshold=5)
        limits = policy.to_usage_limits()
        assert limits.max_steps == 50
        assert limits.repeat_signature_threshold == 5
        assert limits.max_budget_usd is None

    def test_large_steps_preserved(self) -> None:
        policy = LoopGuardPolicy(max_steps=200)
        limits = policy.to_usage_limits()
        assert limits.max_steps == 200

    def test_default_conversion(self) -> None:
        policy = LoopGuardPolicy()
        limits = policy.to_usage_limits()
        assert limits.max_steps == 30
        assert limits.repeat_signature_threshold == 10


# ═══════════════════════════════════════
# UsageTracker.check_limits() — 各维度独立
# ═══════════════════════════════════════


class TestUsageTrackerCheckLimits:
    def test_no_limit_exceeded(self) -> None:
        tracker = UsageTracker(steps=5, start_time=time.monotonic())
        limits = UsageLimits(max_steps=30)
        assert tracker.check_limits(limits) is None

    def test_step_limit_exceeded(self) -> None:
        tracker = UsageTracker(steps=30, start_time=time.monotonic())
        limits = UsageLimits(max_steps=30)
        assert tracker.check_limits(limits) == ErrorCategory.STEP_LIMIT_EXCEEDED

    def test_step_limit_not_exceeded_at_boundary_minus_one(self) -> None:
        tracker = UsageTracker(steps=29, start_time=time.monotonic())
        limits = UsageLimits(max_steps=30)
        assert tracker.check_limits(limits) is None

    def test_max_steps_none_never_exceeded(self) -> None:
        """max_steps=None（不限）时，步数再多也不触发。"""
        tracker = UsageTracker(steps=999999, start_time=time.monotonic())
        limits = UsageLimits(max_steps=None, max_duration_seconds=99999.0)
        assert tracker.check_limits(limits) is None

    def test_request_token_limit_exceeded(self) -> None:
        tracker = UsageTracker(request_tokens=10000, start_time=time.monotonic())
        limits = UsageLimits(max_request_tokens=10000)
        assert tracker.check_limits(limits) == ErrorCategory.TOKEN_LIMIT_EXCEEDED

    def test_response_token_limit_exceeded(self) -> None:
        tracker = UsageTracker(response_tokens=5000, start_time=time.monotonic())
        limits = UsageLimits(max_response_tokens=5000)
        assert tracker.check_limits(limits) == ErrorCategory.TOKEN_LIMIT_EXCEEDED

    def test_tool_call_limit_exceeded(self) -> None:
        tracker = UsageTracker(tool_calls=20, start_time=time.monotonic())
        limits = UsageLimits(max_tool_calls=20)
        assert tracker.check_limits(limits) == ErrorCategory.TOOL_CALL_LIMIT_EXCEEDED

    def test_tool_calls_none_never_exceeded(self) -> None:
        """max_tool_calls=None（不限）时，调用再多也不触发。"""
        tracker = UsageTracker(tool_calls=999999, start_time=time.monotonic())
        limits = UsageLimits(max_tool_calls=None, max_duration_seconds=99999.0)
        assert tracker.check_limits(limits) is None

    def test_budget_exceeded(self) -> None:
        tracker = UsageTracker(cost_usd=0.50, start_time=time.monotonic())
        limits = UsageLimits(max_budget_usd=0.50)
        assert tracker.check_limits(limits) == ErrorCategory.BUDGET_EXCEEDED

    def test_timeout_exceeded(self) -> None:
        # 模拟 start_time 在很久以前
        old_start = time.monotonic() - 200
        tracker = UsageTracker(start_time=old_start)
        limits = UsageLimits(max_duration_seconds=60.0)
        assert tracker.check_limits(limits) == ErrorCategory.TIMEOUT_EXCEEDED

    def test_timeout_not_exceeded(self) -> None:
        tracker = UsageTracker(start_time=time.monotonic())
        limits = UsageLimits(max_duration_seconds=60.0)
        assert tracker.check_limits(limits) is None

    def test_none_limits_not_checked(self) -> None:
        """None 的维度不参与检查。"""
        tracker = UsageTracker(
            request_tokens=999999,
            response_tokens=999999,
            tool_calls=999999,
            cost_usd=999999.0,
            start_time=time.monotonic(),
        )
        # max_steps=None, max_tool_calls=None 等都不限，只设大 duration 避免超时
        limits = UsageLimits(max_duration_seconds=99999.0)
        assert tracker.check_limits(limits) is None

    def test_default_timeout_is_7200(self) -> None:
        """默认 max_duration_seconds=7200s。"""
        limits = UsageLimits()
        assert limits.max_duration_seconds == 7200.0


# ═══════════════════════════════════════
# check_limits() — 优先级（先触发先返回）
# ═══════════════════════════════════════


class TestCheckLimitsPriority:
    def test_step_checked_first(self) -> None:
        """steps 在 check_limits 中排第一，应该先返回。"""
        tracker = UsageTracker(
            steps=30,
            tool_calls=100,
            cost_usd=100.0,
            start_time=time.monotonic() - 9999,
        )
        limits = UsageLimits(
            max_steps=30,
            max_tool_calls=20,
            max_budget_usd=0.5,
            max_duration_seconds=10.0,
        )
        assert tracker.check_limits(limits) == ErrorCategory.STEP_LIMIT_EXCEEDED


# ═══════════════════════════════════════
# 成本熔断器 (T3.2-T3.4)
# ═══════════════════════════════════════


class TestCostFuse:
    def test_cost_fuse_triggers_at_budget(self) -> None:
        """T3.2: Mock 每步 $0.01，budget=$0.03，第 4 步前应触发。"""
        limits = UsageLimits(max_budget_usd=0.03)
        tracker = UsageTracker(start_time=time.monotonic())

        # 模拟 3 步，每步 $0.01
        for step in range(3):
            tracker.cost_usd += 0.01
            tracker.steps = step + 1

        # 第 3 步后 cost=0.03，应触发 BUDGET_EXCEEDED
        assert tracker.check_limits(limits) == ErrorCategory.BUDGET_EXCEEDED

    def test_zero_cost_no_fuse(self) -> None:
        """T3.3: cost_usd=0.0 不触发熔断。"""
        limits = UsageLimits(max_budget_usd=0.03)
        tracker = UsageTracker(cost_usd=0.0, start_time=time.monotonic())
        assert tracker.check_limits(limits) is None

    def test_float_precision_tolerance(self) -> None:
        """T3.4: 100 步 × $0.003 = $0.30 ± 浮点容差。"""
        limits = UsageLimits(max_budget_usd=0.30)
        tracker = UsageTracker(start_time=time.monotonic())

        for _ in range(100):
            tracker.cost_usd += 0.003

        # 由于浮点精度，cost_usd 可能略不等于 0.30
        # check_limits 使用 >= budget - 1e-9 容差
        assert tracker.check_limits(limits) == ErrorCategory.BUDGET_EXCEEDED

    def test_budget_just_below_threshold(self) -> None:
        """预算刚好低于阈值（考虑容差）时不触发。"""
        limits = UsageLimits(max_budget_usd=0.50)
        tracker = UsageTracker(cost_usd=0.49, start_time=time.monotonic())
        assert tracker.check_limits(limits) is None

    def test_budget_at_exact_threshold(self) -> None:
        """预算恰好等于阈值时触发。"""
        limits = UsageLimits(max_budget_usd=0.50)
        tracker = UsageTracker(cost_usd=0.50, start_time=time.monotonic())
        assert tracker.check_limits(limits) == ErrorCategory.BUDGET_EXCEEDED


# ═══════════════════════════════════════
# UsageTracker.to_dict()
# ═══════════════════════════════════════


class TestUsageTrackerToDict:
    def test_basic_serialization(self) -> None:
        tracker = UsageTracker(
            steps=10,
            request_tokens=5000,
            response_tokens=2000,
            tool_calls=8,
            cost_usd=0.15,
            start_time=time.monotonic() - 30,
        )
        result = tracker.to_dict()
        assert result["steps"] == 10
        assert result["request_tokens"] == 5000
        assert result["response_tokens"] == 2000
        assert result["tool_calls"] == 8
        assert result["cost_usd"] == 0.15
        assert "duration_seconds" in result
        assert result["duration_seconds"] >= 29  # 至少 29 秒

    def test_fresh_tracker(self) -> None:
        tracker = UsageTracker(start_time=time.monotonic())
        result = tracker.to_dict()
        assert result["steps"] == 0
        assert result["cost_usd"] == 0.0
        assert result["duration_seconds"] >= 0
