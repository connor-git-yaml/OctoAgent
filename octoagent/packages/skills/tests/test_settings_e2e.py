"""Settings 端到端测试 (T6.5)。

覆盖：Settings 修改 resource_limits → 新请求立即生效。
通过模拟完整 pipeline 验证：
  global_defaults → merge_usage_limits(base, profile_rl, skill_rl) → UsageLimits
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import patch

import pytest
from octoagent.skills.limits import get_global_defaults, merge_usage_limits
from octoagent.skills.models import (
    ErrorCategory,
    SkillExecutionContext,
    SkillOutputEnvelope,
    SkillRunStatus,
    UsageLimits,
)
from octoagent.skills.runner import SkillRunner

from .conftest import EchoInput, EchoOutput, MockEventStore, MockToolBroker, QueueModelClient


# ═══════════════════════════════════════
# 辅助
# ═══════════════════════════════════════


def _make_manifest():
    from octoagent.skills.manifest import SkillManifest

    return SkillManifest(
        skill_id="test.e2e_settings",
        version="0.1.0",
        input_model=EchoInput,
        output_model=EchoOutput,
        model_alias="main",
        tools_allowed=[],
    )


def _simulate_settings_pipeline(
    profile_resource_limits: dict[str, Any] | None = None,
    skill_resource_limits: dict[str, Any] | None = None,
) -> UsageLimits:
    """模拟 llm_service.py 中的完整限制合并链路。

    Pipeline:
        get_global_defaults()
        → merge_usage_limits(base, profile_rl, skill_rl)
        → UsageLimits
    """
    base_limits = get_global_defaults()
    return merge_usage_limits(
        base_limits,
        profile_resource_limits or {},
        skill_resource_limits or {},
    )


# ═══════════════════════════════════════
# 端到端场景测试
# ═══════════════════════════════════════


class TestSettingsImmediateEffect:
    """验证 Settings 修改后，新请求立即使用更新后的限制。"""

    @pytest.mark.asyncio
    async def test_profile_override_reduces_steps(self) -> None:
        """Profile resource_limits 覆盖 max_steps=5 → 运行 5 步后超限。"""
        from octoagent.skills.models import ToolCallSpec

        # 模拟用户在 Settings 中将 max_steps 设为 5
        limits = _simulate_settings_pipeline(
            profile_resource_limits={"max_steps": 5},
        )
        assert limits.max_steps == 5  # Settings 覆盖生效

        # 每步包含 tool_calls，使 runner 继续循环而非触发 retry 失败
        # tool_calls 参数不同以避免 repeat_signature_threshold 触发
        client = QueueModelClient([
            SkillOutputEnvelope(
                content=f"step-{i}",
                complete=False,
                tool_calls=[ToolCallSpec(tool_name="system.echo", arguments={"i": i})],
            )
            for i in range(10)
        ])
        manifest = _make_manifest()
        # 允许 tool_calls 中使用的工具
        manifest = manifest.model_copy(update={"tools_allowed": ["system.echo"]})

        runner = SkillRunner(
            model_client=client,
            tool_broker=MockToolBroker(),
            event_store=MockEventStore(),
        )

        context = SkillExecutionContext(
            task_id="task-e2e-1",
            trace_id="trace-e2e-1",
            caller="main:general",
            usage_limits=limits,
        )

        result = await runner.run(
            manifest=manifest,
            execution_context=context,
            skill_input=EchoInput(text="hello"),
            prompt="test",
        )

        assert result.status == SkillRunStatus.FAILED
        assert result.error_category == ErrorCategory.STEP_LIMIT_EXCEEDED
        assert result.steps == 5

    @pytest.mark.asyncio
    async def test_profile_override_increases_budget(self) -> None:
        """Profile 提高 max_budget_usd → 之前会超限的场景现在可以完成。"""
        limits = _simulate_settings_pipeline(
            profile_resource_limits={"max_budget_usd": 1.0},
        )
        assert limits.max_budget_usd == 1.0

        client = QueueModelClient([
            SkillOutputEnvelope(content="step-1", complete=False, cost_usd=0.30),
            SkillOutputEnvelope(content="step-2", complete=False, cost_usd=0.30),
            SkillOutputEnvelope(content="done", complete=True, cost_usd=0.10),
        ])
        runner = SkillRunner(
            model_client=client,
            tool_broker=MockToolBroker(),
            event_store=MockEventStore(),
        )

        context = SkillExecutionContext(
            task_id="task-e2e-2",
            trace_id="trace-e2e-2",
            caller="main:general",
            usage_limits=limits,
        )

        result = await runner.run(
            manifest=_make_manifest(),
            execution_context=context,
            skill_input=EchoInput(text="hello"),
            prompt="test",
        )

        assert result.status == SkillRunStatus.SUCCEEDED
        assert result.total_cost_usd >= 0.60

    @pytest.mark.asyncio
    async def test_skill_rl_overrides_profile(self) -> None:
        """SKILL.md resource_limits 覆盖 Profile 设置。"""
        limits = _simulate_settings_pipeline(
            profile_resource_limits={"max_steps": 50},
            skill_resource_limits={"max_steps": 10},  # SKILL.md 优先
        )
        assert limits.max_steps == 10  # SKILL.md 层优先

    @pytest.mark.asyncio
    async def test_default_without_settings(self) -> None:
        """无 Settings 覆盖时使用 runtime 兜底默认值。

        UsageLimits 类层 max_steps=None（API 契约"不限制"语义），但
        get_global_defaults() 注入 _RUNTIME_FALLBACK_MAX_STEPS=30，避免
        LLM 误解 intent 时无限循环烧资源。
        """
        limits = _simulate_settings_pipeline()
        assert limits.max_steps == 30  # runtime 兜底
        assert limits.max_budget_usd is None
        assert limits.max_duration_seconds == 7200.0  # 2 小时

    @pytest.mark.asyncio
    async def test_env_var_defaults(self) -> None:
        """环境变量设置 → 使用环境变量默认值。"""
        with patch.dict(os.environ, {"OCTOAGENT_DEFAULT_MAX_STEPS": "75"}):
            limits = _simulate_settings_pipeline()
            assert limits.max_steps == 75

    @pytest.mark.asyncio
    async def test_settings_override_env_var(self) -> None:
        """Settings (profile) > 环境变量：profile 覆盖 env 设置。"""
        with patch.dict(os.environ, {"OCTOAGENT_DEFAULT_MAX_STEPS": "200"}):
            limits = _simulate_settings_pipeline(
                profile_resource_limits={"max_steps": 80},  # profile 覆盖
            )
            assert limits.max_steps == 80


class TestSettingsMultiDimensionIntegration:
    """多维度联合测试：Settings 修改多个维度同时生效。"""

    @pytest.mark.asyncio
    async def test_multi_dimension_settings(self) -> None:
        """同时设置 max_steps + max_budget_usd + max_tool_calls → 全部生效。"""
        limits = _simulate_settings_pipeline(
            profile_resource_limits={
                "max_steps": 200,
                "max_budget_usd": 2.0,
                "max_tool_calls": 100,
            },
        )
        assert limits.max_steps == 200
        assert limits.max_budget_usd == 2.0
        assert limits.max_tool_calls == 100

    @pytest.mark.asyncio
    async def test_partial_override_preserves_default(self) -> None:
        """部分覆盖：仅修改一个维度，其余保留全局默认值。"""
        limits = _simulate_settings_pipeline(
            profile_resource_limits={"max_duration_seconds": 600.0},
        )
        assert limits.max_steps == 30  # runtime 兜底保留
        assert limits.max_budget_usd is None  # 全局默认保留
        assert limits.max_duration_seconds == 600.0  # 被覆盖
        assert limits.max_tool_calls is None  # 全局默认保留

    @pytest.mark.asyncio
    async def test_none_and_zero_do_not_override(self) -> None:
        """None 和 0 值不覆盖默认。"""
        limits = _simulate_settings_pipeline(
            profile_resource_limits={
                "max_steps": None,
                "max_budget_usd": 0,
                "max_duration_seconds": None,
            },
        )
        assert limits.max_steps == 30  # None 不覆盖 → runtime 兜底保留
        assert limits.max_budget_usd is None  # 0 不覆盖
        assert limits.max_duration_seconds == 7200.0  # None 不覆盖


class TestSettingsFullPipeline:
    """完整 pipeline 端到端：Settings → merge → context → runner → 结果。"""

    @pytest.mark.asyncio
    async def test_runner_uses_settings_budget_limit(self) -> None:
        """Settings 设 budget=0.05 → 超限时返回 BUDGET_EXCEEDED + 友好提示文案。"""
        limits = _simulate_settings_pipeline(
            profile_resource_limits={"max_budget_usd": 0.05},
        )

        # 每步 $0.03，2步=$0.06 > $0.05
        client = QueueModelClient([
            SkillOutputEnvelope(content="step-1", complete=False, cost_usd=0.03),
            SkillOutputEnvelope(content="step-2", complete=False, cost_usd=0.03),
            SkillOutputEnvelope(content="done", complete=True),
        ])
        runner = SkillRunner(
            model_client=client,
            tool_broker=MockToolBroker(),
            event_store=MockEventStore(),
        )

        context = SkillExecutionContext(
            task_id="task-e2e-budget",
            trace_id="trace-e2e-budget",
            caller="worker:general",
            usage_limits=limits,
        )

        result = await runner.run(
            manifest=_make_manifest(),
            execution_context=context,
            skill_input=EchoInput(text="hello"),
            prompt="test",
        )

        assert result.status == SkillRunStatus.FAILED
        assert result.error_category == ErrorCategory.BUDGET_EXCEEDED
        assert result.total_cost_usd >= 0.05

    @pytest.mark.asyncio
    async def test_runner_completes_within_limits(self) -> None:
        """在限制范围内正常完成的请求。"""
        limits = _simulate_settings_pipeline(
            profile_resource_limits={"max_steps": 10, "max_budget_usd": 0.20},
        )

        client = QueueModelClient([
            SkillOutputEnvelope(
                content="done",
                complete=True,
                token_usage={"prompt_tokens": 50, "completion_tokens": 20},
                cost_usd=0.005,
            ),
        ])
        runner = SkillRunner(
            model_client=client,
            tool_broker=MockToolBroker(),
            event_store=MockEventStore(),
        )

        context = SkillExecutionContext(
            task_id="task-e2e-ok",
            trace_id="trace-e2e-ok",
            caller="main:general",
            usage_limits=limits,
        )

        result = await runner.run(
            manifest=_make_manifest(),
            execution_context=context,
            skill_input=EchoInput(text="hello"),
            prompt="test",
        )

        assert result.status == SkillRunStatus.SUCCEEDED
        assert result.steps == 1
        assert result.total_cost_usd == 0.005
        assert result.usage.get("request_tokens") == 50
