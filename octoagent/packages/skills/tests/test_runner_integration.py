"""SkillRunner 集成测试 (T1.18)。

覆盖：不同资源维度超限时返回正确 ErrorCategory + 友好提示。
- step_limit_exceeded
- token_limit_exceeded (request)
- token_limit_exceeded (response)
- tool_call_limit_exceeded
- budget_exceeded
- timeout_exceeded (通过 mock time)
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from octoagent.skills.manifest import SkillManifest
from octoagent.skills.models import (
    ErrorCategory,
    SkillExecutionContext,
    SkillOutputEnvelope,
    SkillRunStatus,
    UsageLimits,
)
from octoagent.skills.runner import SkillRunner

from .conftest import EchoInput, EchoOutput, MockEventStore, MockToolBroker, QueueModelClient


# ─── 辅助 ───


def _make_context(usage_limits: UsageLimits | None = None) -> SkillExecutionContext:
    return SkillExecutionContext(
        task_id="task-integ",
        trace_id="trace-integ",
        caller="worker",
        usage_limits=usage_limits or UsageLimits(max_steps=100),
    )


def _make_manifest() -> SkillManifest:
    return SkillManifest(
        skill_id="test.integration",
        version="0.1.0",
        input_model=EchoInput,
        output_model=EchoOutput,
        model_alias="main",
        tools_allowed=[],
    )


def _make_envelope(
    content: str = "step",
    complete: bool = False,
    token_usage: dict[str, int] | None = None,
    cost_usd: float = 0.0,
) -> SkillOutputEnvelope:
    return SkillOutputEnvelope(
        content=content,
        complete=complete,
        token_usage=token_usage or {},
        cost_usd=cost_usd,
    )


# ═══════════════════════════════════════
# 测试用例
# ═══════════════════════════════════════


class TestStepLimitExceeded:
    @pytest.mark.asyncio
    async def test_step_limit_triggers_at_boundary(self) -> None:
        """max_steps=3 时，第 3 步完成后检测到超限。"""
        # 3 个不完成的 envelope + 1 个备用（不应被读到）
        client = QueueModelClient([
            _make_envelope("step-1"),
            _make_envelope("step-2"),
            _make_envelope("step-3"),
            _make_envelope("step-4"),
        ])
        runner = SkillRunner(
            model_client=client,
            tool_broker=MockToolBroker(),
            event_store=MockEventStore(),
        )

        result = await runner.run(
            manifest=_make_manifest(),
            execution_context=_make_context(UsageLimits(max_steps=3)),
            skill_input=EchoInput(text="hello"),
            prompt="test",
        )

        assert result.status == SkillRunStatus.FAILED
        assert result.error_category == ErrorCategory.STEP_LIMIT_EXCEEDED
        assert result.steps == 3
        assert result.usage.get("steps") == 3


class TestTokenLimitExceeded:
    @pytest.mark.asyncio
    async def test_request_token_limit(self) -> None:
        """request_tokens 超过 max_request_tokens 时触发 TOKEN_LIMIT_EXCEEDED。"""
        # 每步 600 prompt tokens，limit=1000，第 2 步后累计 1200 超限
        client = QueueModelClient([
            _make_envelope("step-1", token_usage={"prompt_tokens": 600, "completion_tokens": 50}),
            _make_envelope("step-2", token_usage={"prompt_tokens": 600, "completion_tokens": 50}),
            _make_envelope("step-3"),
        ])
        runner = SkillRunner(
            model_client=client,
            tool_broker=MockToolBroker(),
            event_store=MockEventStore(),
        )

        result = await runner.run(
            manifest=_make_manifest(),
            execution_context=_make_context(
                UsageLimits(max_steps=100, max_request_tokens=1000)
            ),
            skill_input=EchoInput(text="hello"),
            prompt="test",
        )

        assert result.status == SkillRunStatus.FAILED
        assert result.error_category == ErrorCategory.TOKEN_LIMIT_EXCEEDED

    @pytest.mark.asyncio
    async def test_response_token_limit(self) -> None:
        """response_tokens 超过 max_response_tokens 时触发 TOKEN_LIMIT_EXCEEDED。"""
        # 每步 400 completion tokens，limit=500，第 2 步后累计 800 超限
        client = QueueModelClient([
            _make_envelope("step-1", token_usage={"prompt_tokens": 50, "completion_tokens": 400}),
            _make_envelope("step-2", token_usage={"prompt_tokens": 50, "completion_tokens": 400}),
            _make_envelope("step-3"),
        ])
        runner = SkillRunner(
            model_client=client,
            tool_broker=MockToolBroker(),
            event_store=MockEventStore(),
        )

        result = await runner.run(
            manifest=_make_manifest(),
            execution_context=_make_context(
                UsageLimits(max_steps=100, max_response_tokens=500)
            ),
            skill_input=EchoInput(text="hello"),
            prompt="test",
        )

        assert result.status == SkillRunStatus.FAILED
        assert result.error_category == ErrorCategory.TOKEN_LIMIT_EXCEEDED


class TestToolCallLimitExceeded:
    @pytest.mark.asyncio
    async def test_tool_call_limit(self) -> None:
        """tool_calls 超过 max_tool_calls 时触发 TOOL_CALL_LIMIT_EXCEEDED。"""
        from octoagent.skills.models import ToolCallSpec

        # 每步 2 个 tool_calls，limit=3，第 2 步后累计 4 超限
        client = QueueModelClient([
            SkillOutputEnvelope(
                content="step-1",
                complete=False,
                tool_calls=[
                    ToolCallSpec(tool_name="system.echo", arguments={"text": "a"}),
                    ToolCallSpec(tool_name="system.echo", arguments={"text": "b"}),
                ],
            ),
            SkillOutputEnvelope(
                content="step-2",
                complete=False,
                tool_calls=[
                    ToolCallSpec(tool_name="system.echo", arguments={"text": "c"}),
                    ToolCallSpec(tool_name="system.echo", arguments={"text": "d"}),
                ],
            ),
            _make_envelope("step-3", complete=True),
        ])
        manifest = SkillManifest(
            skill_id="test.tool_limit",
            version="0.1.0",
            input_model=EchoInput,
            output_model=EchoOutput,
            model_alias="main",
            tools_allowed=["system.echo"],
        )
        runner = SkillRunner(
            model_client=client,
            tool_broker=MockToolBroker(),
            event_store=MockEventStore(),
        )

        result = await runner.run(
            manifest=manifest,
            execution_context=_make_context(
                UsageLimits(max_steps=100, max_tool_calls=3)
            ),
            skill_input=EchoInput(text="hello"),
            prompt="test",
        )

        assert result.status == SkillRunStatus.FAILED
        assert result.error_category == ErrorCategory.TOOL_CALL_LIMIT_EXCEEDED


class TestBudgetExceeded:
    @pytest.mark.asyncio
    async def test_budget_exceeded(self) -> None:
        """cost_usd 超过 max_budget_usd 时触发 BUDGET_EXCEEDED。"""
        # 每步 $0.02，budget=$0.03，第 2 步后累计 $0.04 超限
        client = QueueModelClient([
            _make_envelope("step-1", cost_usd=0.02),
            _make_envelope("step-2", cost_usd=0.02),
            _make_envelope("step-3"),
        ])
        runner = SkillRunner(
            model_client=client,
            tool_broker=MockToolBroker(),
            event_store=MockEventStore(),
        )

        result = await runner.run(
            manifest=_make_manifest(),
            execution_context=_make_context(
                UsageLimits(max_steps=100, max_budget_usd=0.03)
            ),
            skill_input=EchoInput(text="hello"),
            prompt="test",
        )

        assert result.status == SkillRunStatus.FAILED
        assert result.error_category == ErrorCategory.BUDGET_EXCEEDED
        assert result.total_cost_usd >= 0.03


class TestTimeoutExceeded:
    @pytest.mark.asyncio
    async def test_timeout_exceeded(self) -> None:
        """duration 超过 max_duration_seconds 时触发 TIMEOUT_EXCEEDED。"""
        import unittest.mock

        # 模拟 time.monotonic() 使得每步耗时 50 秒
        original_monotonic = time.monotonic
        call_count = 0
        base_time = original_monotonic()

        def mock_monotonic() -> float:
            nonlocal call_count
            call_count += 1
            # 每次调用递增 30 秒，确保超时
            return base_time + call_count * 30

        client = QueueModelClient([
            _make_envelope("step-1"),
            _make_envelope("step-2"),
            _make_envelope("step-3"),
        ])
        runner = SkillRunner(
            model_client=client,
            tool_broker=MockToolBroker(),
            event_store=MockEventStore(),
        )

        with unittest.mock.patch("time.monotonic", side_effect=mock_monotonic):
            result = await runner.run(
                manifest=_make_manifest(),
                execution_context=_make_context(
                    UsageLimits(max_steps=100, max_duration_seconds=60.0)
                ),
                skill_input=EchoInput(text="hello"),
                prompt="test",
            )

        assert result.status == SkillRunStatus.FAILED
        assert result.error_category == ErrorCategory.TIMEOUT_EXCEEDED


class TestUsageReportInResult:
    @pytest.mark.asyncio
    async def test_exceeded_result_includes_usage(self) -> None:
        """超限结果包含 usage dict 和 total_cost_usd。"""
        client = QueueModelClient([
            _make_envelope(
                "step-1",
                token_usage={"prompt_tokens": 500, "completion_tokens": 200},
                cost_usd=0.01,
            ),
        ])
        runner = SkillRunner(
            model_client=client,
            tool_broker=MockToolBroker(),
            event_store=MockEventStore(),
        )

        result = await runner.run(
            manifest=_make_manifest(),
            execution_context=_make_context(UsageLimits(max_steps=1)),
            skill_input=EchoInput(text="hello"),
            prompt="test",
        )

        assert result.status == SkillRunStatus.FAILED
        assert result.usage.get("steps") == 1
        assert result.usage.get("request_tokens") == 500
        assert result.usage.get("response_tokens") == 200
        assert result.usage.get("cost_usd") == 0.01
        assert result.total_cost_usd == 0.01

    @pytest.mark.asyncio
    async def test_succeeded_result_includes_usage(self) -> None:
        """正常成功的结果也包含 usage 数据。"""
        client = QueueModelClient([
            _make_envelope(
                "done",
                complete=True,
                token_usage={"prompt_tokens": 100, "completion_tokens": 50},
                cost_usd=0.005,
            ),
        ])
        runner = SkillRunner(
            model_client=client,
            tool_broker=MockToolBroker(),
            event_store=MockEventStore(),
        )

        result = await runner.run(
            manifest=_make_manifest(),
            execution_context=_make_context(),
            skill_input=EchoInput(text="hello"),
            prompt="test",
        )

        assert result.status == SkillRunStatus.SUCCEEDED
        assert result.usage.get("steps") == 1
        assert result.usage.get("request_tokens") == 100
        assert result.total_cost_usd == 0.005
