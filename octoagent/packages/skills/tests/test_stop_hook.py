"""StopHook 单元测试 (T4.5)。

覆盖：
- should_stop 返回 False → 继续执行
- should_stop 返回 True → 标记 STOPPED
- 多 hook 任一 True 即停止
- STOPPED 状态在 SkillRunResult 中正确体现
"""

from __future__ import annotations

from typing import Any

from octoagent.skills.hooks import NoopSkillRunnerHook
from octoagent.skills.manifest import SkillManifest
from octoagent.skills.models import (
    SkillExecutionContext,
    SkillOutputEnvelope,
    SkillRunStatus,
    UsageLimits,
    UsageTracker,
)
from octoagent.skills.runner import SkillRunner

from .conftest import EchoInput, EchoOutput, MockEventStore, MockToolBroker, QueueModelClient


# ─── StopHook 实现 ───


class AlwaysStopHook(NoopSkillRunnerHook):
    """第一步之后就返回 True 的 StopHook。"""

    async def should_stop(
        self,
        manifest: SkillManifest,
        context: SkillExecutionContext,
        tracker: UsageTracker,
        last_output: SkillOutputEnvelope | None,
    ) -> bool:
        return True


class NeverStopHook(NoopSkillRunnerHook):
    """始终返回 False 的 StopHook。"""

    async def should_stop(
        self,
        manifest: SkillManifest,
        context: SkillExecutionContext,
        tracker: UsageTracker,
        last_output: SkillOutputEnvelope | None,
    ) -> bool:
        return False


class ConditionalStopHook(NoopSkillRunnerHook):
    """在指定步数后返回 True 的 StopHook。"""

    def __init__(self, stop_after_step: int) -> None:
        self._stop_after = stop_after_step

    async def should_stop(
        self,
        manifest: SkillManifest,
        context: SkillExecutionContext,
        tracker: UsageTracker,
        last_output: SkillOutputEnvelope | None,
    ) -> bool:
        return tracker.steps >= self._stop_after


# ─── 辅助 ───


def _make_context(usage_limits: UsageLimits | None = None) -> SkillExecutionContext:
    return SkillExecutionContext(
        task_id="task-stop-hook",
        trace_id="trace-stop-hook",
        caller="worker",
        usage_limits=usage_limits or UsageLimits(max_steps=100),
    )


def _make_manifest() -> SkillManifest:
    return SkillManifest(
        skill_id="test.stop_hook",
        version="0.1.0",
        input_model=EchoInput,
        output_model=EchoOutput,
        model_alias="main",
        tools_allowed=[],
    )


# ═══════════════════════════════════════
# 测试用例
# ═══════════════════════════════════════


async def test_noop_hook_should_stop_returns_false() -> None:
    """NoopSkillRunnerHook.should_stop() 默认返回 False。"""
    hook = NoopSkillRunnerHook()
    result = await hook.should_stop(
        manifest=_make_manifest(),
        context=_make_context(),
        tracker=UsageTracker(),
        last_output=None,
    )
    assert result is False


async def test_always_stop_hook_triggers_stopped() -> None:
    """should_stop 返回 True 时，SkillRunner 应返回 STOPPED。"""
    client = QueueModelClient([
        SkillOutputEnvelope(content="step-1-output", complete=False),
        SkillOutputEnvelope(content="step-2-output", complete=True),
    ])
    runner = SkillRunner(
        model_client=client,
        tool_broker=MockToolBroker(),
        event_store=MockEventStore(),
        hooks=[AlwaysStopHook()],
    )

    result = await runner.run(
        manifest=_make_manifest(),
        execution_context=_make_context(),
        skill_input=EchoInput(text="hello"),
        prompt="test",
    )

    assert result.status == SkillRunStatus.STOPPED
    assert result.output is not None
    assert result.output.content == "step-1-output"


async def test_never_stop_hook_allows_completion() -> None:
    """should_stop 返回 False 时，正常走完所有步骤。"""
    client = QueueModelClient([
        SkillOutputEnvelope(content="done", complete=True),
    ])
    runner = SkillRunner(
        model_client=client,
        tool_broker=MockToolBroker(),
        event_store=MockEventStore(),
        hooks=[NeverStopHook()],
    )

    result = await runner.run(
        manifest=_make_manifest(),
        execution_context=_make_context(),
        skill_input=EchoInput(text="hello"),
        prompt="test",
    )

    assert result.status == SkillRunStatus.SUCCEEDED
    assert result.output is not None
    assert result.output.content == "done"


async def test_conditional_stop_after_2_steps() -> None:
    """ConditionalStopHook 在第 2 步后停止。"""
    client = QueueModelClient([
        SkillOutputEnvelope(content="step-1", complete=False),
        SkillOutputEnvelope(content="step-2", complete=False),
        SkillOutputEnvelope(content="step-3", complete=True),
    ])
    runner = SkillRunner(
        model_client=client,
        tool_broker=MockToolBroker(),
        event_store=MockEventStore(),
        hooks=[ConditionalStopHook(stop_after_step=2)],
    )

    result = await runner.run(
        manifest=_make_manifest(),
        execution_context=_make_context(),
        skill_input=EchoInput(text="hello"),
        prompt="test",
    )

    assert result.status == SkillRunStatus.STOPPED
    assert result.steps == 2
    assert result.output is not None
    assert result.output.content == "step-2"


async def test_multiple_hooks_any_true_stops() -> None:
    """多个 hook，只要任一返回 True 就停止。"""
    client = QueueModelClient([
        SkillOutputEnvelope(content="output", complete=False),
        SkillOutputEnvelope(content="never-reached", complete=True),
    ])
    runner = SkillRunner(
        model_client=client,
        tool_broker=MockToolBroker(),
        event_store=MockEventStore(),
        hooks=[NeverStopHook(), AlwaysStopHook()],
    )

    result = await runner.run(
        manifest=_make_manifest(),
        execution_context=_make_context(),
        skill_input=EchoInput(text="hello"),
        prompt="test",
    )

    assert result.status == SkillRunStatus.STOPPED


async def test_stopped_includes_usage_report() -> None:
    """STOPPED 结果应包含 usage dict 和 total_cost_usd。"""
    client = QueueModelClient([
        SkillOutputEnvelope(
            content="output",
            complete=False,
            token_usage={"prompt_tokens": 100, "completion_tokens": 50},
            cost_usd=0.005,
        ),
    ])
    runner = SkillRunner(
        model_client=client,
        tool_broker=MockToolBroker(),
        event_store=MockEventStore(),
        hooks=[AlwaysStopHook()],
    )

    result = await runner.run(
        manifest=_make_manifest(),
        execution_context=_make_context(),
        skill_input=EchoInput(text="hello"),
        prompt="test",
    )

    assert result.status == SkillRunStatus.STOPPED
    assert result.usage.get("steps", 0) >= 1
    assert result.total_cost_usd >= 0.0
