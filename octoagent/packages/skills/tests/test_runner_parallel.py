"""SkillRunner 并行分桶执行单元测试 (Feature 064 T-064-06)。

覆盖：
- 3 个 NONE 工具并行执行（通过 mock sleep 验证）
- 混合 NONE + REVERSIBLE + IRREVERSIBLE 执行顺序
- 并行中 1 个失败 → 2 个成功结果 + 1 个错误反馈
- 单个 tool_call 不生成 BATCH 事件
- BATCH 事件 payload 含 batch_id、tool_names、execution_mode
- 结果顺序与输入 tool_calls 顺序一致
- 回归：单工具场景行为不变
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from octoagent.core.models.event import Event
from octoagent.skills.manifest import SkillManifest
from octoagent.skills.models import (
    SkillExecutionContext,
    SkillOutputEnvelope,
    SkillRunStatus,
    ToolCallSpec,
    UsageLimits,
)
from octoagent.skills.runner import SkillRunner
from octoagent.tooling.models import (
    SideEffectLevel,
    ToolMeta,
    ToolProfile,
    ToolResult,
)

from .conftest import EchoInput, EchoOutput, MockEventStore, MockToolBroker, QueueModelClient


# ─── 辅助 ───


def _make_manifest() -> SkillManifest:
    return SkillManifest(
        skill_id="test.parallel",
        version="0.1.0",
        input_model=EchoInput,
        output_model=EchoOutput,
        model_alias="main",
        tools_allowed=["tool_a", "tool_b", "tool_c", "tool_d", "tool_e"],
    )


def _make_context() -> SkillExecutionContext:
    return SkillExecutionContext(
        task_id="task-parallel",
        trace_id="trace-parallel",
        caller="worker",
        agent_runtime_id="runtime-1",
        usage_limits=UsageLimits(max_steps=10),
    )


def _make_tool_meta(name: str, side_effect: SideEffectLevel) -> ToolMeta:
    return ToolMeta(
        name=name,
        description=f"Test tool {name}",
        parameters_json_schema={"type": "object", "properties": {}},
        side_effect_level=side_effect,
        tool_profile=ToolProfile.STANDARD,
        tool_group="test",
    )


class SlowMockToolBroker(MockToolBroker):
    """工具执行带延迟的 MockToolBroker，用于验证并行性。"""

    def __init__(self) -> None:
        super().__init__()
        self._delays: dict[str, float] = {}
        self._execution_order: list[str] = []

    def set_delay(self, tool_name: str, delay: float) -> None:
        self._delays[tool_name] = delay

    async def execute(self, tool_name: str, args: dict[str, Any], context: Any) -> ToolResult:
        self._execution_order.append(tool_name)
        delay = self._delays.get(tool_name, 0.0)
        if delay > 0:
            await asyncio.sleep(delay)
        return await super().execute(tool_name, args, context)

    @property
    def execution_order(self) -> list[str]:
        return list(self._execution_order)


class FailingMockToolBroker(MockToolBroker):
    """指定工具执行失败的 MockToolBroker。"""

    def __init__(self) -> None:
        super().__init__()
        self._fail_tools: set[str] = set()

    def set_fail(self, tool_name: str) -> None:
        self._fail_tools.add(tool_name)

    async def execute(self, tool_name: str, args: dict[str, Any], context: Any) -> ToolResult:
        self.calls.append((tool_name, args))
        self.contexts.append(context)
        if tool_name in self._fail_tools:
            return ToolResult(
                output="",
                is_error=True,
                error=f"Tool {tool_name} failed",
                duration=0.01,
                tool_name=tool_name,
            )
        return ToolResult(
            output=f"result:{tool_name}",
            is_error=False,
            duration=0.01,
            tool_name=tool_name,
        )


def _get_batch_events(events: list[Event]) -> tuple[list[Event], list[Event]]:
    """从事件列表中提取 BATCH_STARTED 和 BATCH_COMPLETED 事件。"""
    started = [e for e in events if e.type.value == "TOOL_BATCH_STARTED"]
    completed = [e for e in events if e.type.value == "TOOL_BATCH_COMPLETED"]
    return started, completed


# ─── 测试 ───


@pytest.mark.asyncio
async def test_parallel_none_tools_concurrent_execution() -> None:
    """3 个 NONE 工具并行执行，总耗时接近最慢单个。"""
    broker = SlowMockToolBroker()
    # 注册 3 个 NONE 工具
    for name in ["tool_a", "tool_b", "tool_c"]:
        broker.set_tool_meta(name, _make_tool_meta(name, SideEffectLevel.NONE))
        broker.set_delay(name, 0.1)  # 每个 100ms

    event_store = MockEventStore()

    client = QueueModelClient([
        SkillOutputEnvelope(
            content="call tools",
            complete=False,
            tool_calls=[
                {"tool_name": "tool_a", "arguments": {}},
                {"tool_name": "tool_b", "arguments": {}},
                {"tool_name": "tool_c", "arguments": {}},
            ],
        ),
        SkillOutputEnvelope(content="done", complete=True),
    ])

    runner = SkillRunner(
        model_client=client,
        tool_broker=broker,
        event_store=event_store,
    )

    start = time.monotonic()
    result = await runner.run(
        manifest=_make_manifest(),
        execution_context=_make_context(),
        skill_input={"text": "test"},
        prompt="test",
    )
    elapsed = time.monotonic() - start

    assert result.status == SkillRunStatus.SUCCEEDED
    # 并行执行：总耗时应 < 300ms（串行需 300ms+），允许一些 overhead
    assert elapsed < 0.25, f"并行执行耗时 {elapsed:.3f}s，预期 < 0.25s"


@pytest.mark.asyncio
async def test_mixed_buckets_execution_order() -> None:
    """混合 NONE + REVERSIBLE + IRREVERSIBLE 工具按正确顺序执行。"""
    broker = SlowMockToolBroker()
    broker.set_tool_meta("tool_a", _make_tool_meta("tool_a", SideEffectLevel.NONE))
    broker.set_tool_meta("tool_b", _make_tool_meta("tool_b", SideEffectLevel.REVERSIBLE))
    broker.set_tool_meta("tool_c", _make_tool_meta("tool_c", SideEffectLevel.IRREVERSIBLE))
    broker.set_delay("tool_a", 0.05)

    event_store = MockEventStore()
    client = QueueModelClient([
        SkillOutputEnvelope(
            content="call tools",
            complete=False,
            tool_calls=[
                {"tool_name": "tool_c", "arguments": {}},  # IRREVERSIBLE（排最后）
                {"tool_name": "tool_a", "arguments": {}},  # NONE（排最先）
                {"tool_name": "tool_b", "arguments": {}},  # REVERSIBLE（排中间）
            ],
        ),
        SkillOutputEnvelope(content="done", complete=True),
    ])

    runner = SkillRunner(
        model_client=client,
        tool_broker=broker,
        event_store=event_store,
    )

    result = await runner.run(
        manifest=_make_manifest(),
        execution_context=_make_context(),
        skill_input={"text": "test"},
        prompt="test",
    )

    assert result.status == SkillRunStatus.SUCCEEDED
    # 验证执行顺序: NONE(tool_a) → REVERSIBLE(tool_b) → IRREVERSIBLE(tool_c)
    order = broker.execution_order
    assert order.index("tool_a") < order.index("tool_b"), "NONE 应在 REVERSIBLE 之前"
    assert order.index("tool_b") < order.index("tool_c"), "REVERSIBLE 应在 IRREVERSIBLE 之前"


@pytest.mark.asyncio
async def test_parallel_partial_failure() -> None:
    """并行中 1 个失败 → 2 个成功 + 1 个错误。"""
    broker = FailingMockToolBroker()
    for name in ["tool_a", "tool_b", "tool_c"]:
        broker.set_tool_meta(name, _make_tool_meta(name, SideEffectLevel.NONE))
    broker.set_fail("tool_b")

    event_store = MockEventStore()
    client = QueueModelClient([
        SkillOutputEnvelope(
            content="call tools",
            complete=False,
            tool_calls=[
                {"tool_name": "tool_a", "arguments": {}},
                {"tool_name": "tool_b", "arguments": {}},
                {"tool_name": "tool_c", "arguments": {}},
            ],
        ),
        SkillOutputEnvelope(content="done", complete=True),
    ])

    runner = SkillRunner(
        model_client=client,
        tool_broker=broker,
        event_store=event_store,
    )

    result = await runner.run(
        manifest=_make_manifest(),
        execution_context=_make_context(),
        skill_input={"text": "test"},
        prompt="test",
    )

    # 虽然有错误，但只要没超过 retry 上限就会继续
    assert result.status in (SkillRunStatus.SUCCEEDED, SkillRunStatus.FAILED)

    # 验证 BATCH_COMPLETED 事件中 error_count
    _, batch_completed = _get_batch_events(event_store.events)
    assert len(batch_completed) == 1
    payload = batch_completed[0].payload
    assert payload["error_count"] == 1
    assert payload["success_count"] == 2


@pytest.mark.asyncio
async def test_single_tool_no_batch_event() -> None:
    """单个 tool_call 不生成 BATCH 事件。"""
    broker = MockToolBroker()
    broker.set_tool_meta("tool_a", _make_tool_meta("tool_a", SideEffectLevel.NONE))

    event_store = MockEventStore()
    client = QueueModelClient([
        SkillOutputEnvelope(
            content="call tool",
            complete=False,
            tool_calls=[{"tool_name": "tool_a", "arguments": {}}],
        ),
        SkillOutputEnvelope(content="done", complete=True),
    ])

    runner = SkillRunner(
        model_client=client,
        tool_broker=broker,
        event_store=event_store,
    )

    result = await runner.run(
        manifest=_make_manifest(),
        execution_context=_make_context(),
        skill_input={"text": "test"},
        prompt="test",
    )

    assert result.status == SkillRunStatus.SUCCEEDED
    started, completed = _get_batch_events(event_store.events)
    assert len(started) == 0, "单 tool_call 不应生成 BATCH_STARTED"
    assert len(completed) == 0, "单 tool_call 不应生成 BATCH_COMPLETED"


@pytest.mark.asyncio
async def test_batch_event_payload() -> None:
    """BATCH 事件 payload 含 batch_id、tool_names、execution_mode 等必要字段。"""
    broker = MockToolBroker()
    for name in ["tool_a", "tool_b"]:
        broker.set_tool_meta(name, _make_tool_meta(name, SideEffectLevel.NONE))

    event_store = MockEventStore()
    client = QueueModelClient([
        SkillOutputEnvelope(
            content="call tools",
            complete=False,
            tool_calls=[
                {"tool_name": "tool_a", "arguments": {}},
                {"tool_name": "tool_b", "arguments": {}},
            ],
        ),
        SkillOutputEnvelope(content="done", complete=True),
    ])

    runner = SkillRunner(
        model_client=client,
        tool_broker=broker,
        event_store=event_store,
    )

    result = await runner.run(
        manifest=_make_manifest(),
        execution_context=_make_context(),
        skill_input={"text": "test"},
        prompt="test",
    )

    assert result.status == SkillRunStatus.SUCCEEDED
    started, completed = _get_batch_events(event_store.events)
    assert len(started) == 1
    assert len(completed) == 1

    # 验证 STARTED payload
    sp = started[0].payload
    assert sp["batch_id"]  # 非空 ULID
    assert sp["tool_names"] == ["tool_a", "tool_b"]
    assert sp["execution_mode"] == "parallel"
    assert sp["batch_size"] == 2
    assert sp["skill_id"] == "test.parallel"
    assert sp["bucket_none_count"] == 2
    assert sp["bucket_reversible_count"] == 0
    assert sp["bucket_irreversible_count"] == 0

    # 验证 COMPLETED payload
    cp = completed[0].payload
    assert cp["batch_id"] == sp["batch_id"]  # 同一 batch_id
    assert cp["success_count"] == 2
    assert cp["error_count"] == 0
    assert cp["total_count"] == 2
    assert cp["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_results_order_matches_input() -> None:
    """结果列表顺序与输入 tool_calls 顺序一致。"""
    broker = SlowMockToolBroker()
    # tool_c 最快完成，tool_a 最慢
    broker.set_tool_meta("tool_a", _make_tool_meta("tool_a", SideEffectLevel.NONE))
    broker.set_tool_meta("tool_b", _make_tool_meta("tool_b", SideEffectLevel.NONE))
    broker.set_tool_meta("tool_c", _make_tool_meta("tool_c", SideEffectLevel.NONE))
    broker.set_delay("tool_a", 0.08)
    broker.set_delay("tool_b", 0.04)
    broker.set_delay("tool_c", 0.01)

    # 捕获 feedback 的客户端
    feedback_captured: list[Any] = []

    class CaptureFeedbackClient(QueueModelClient):
        async def generate(self, **kwargs: Any) -> SkillOutputEnvelope:
            feedback_captured.append(list(kwargs.get("feedback", [])))
            return await super().generate(**kwargs)

    event_store = MockEventStore()
    client = CaptureFeedbackClient([
        SkillOutputEnvelope(
            content="call tools",
            complete=False,
            tool_calls=[
                {"tool_name": "tool_a", "arguments": {}},
                {"tool_name": "tool_b", "arguments": {}},
                {"tool_name": "tool_c", "arguments": {}},
            ],
        ),
        SkillOutputEnvelope(content="done", complete=True),
    ])

    runner = SkillRunner(
        model_client=client,
        tool_broker=broker,
        event_store=event_store,
    )

    result = await runner.run(
        manifest=_make_manifest(),
        execution_context=_make_context(),
        skill_input={"text": "test"},
        prompt="test",
    )

    assert result.status == SkillRunStatus.SUCCEEDED
    # 第二次 generate 调用时收到的 feedback 应按 tool_a, tool_b, tool_c 顺序
    assert len(feedback_captured) >= 2
    fbs = feedback_captured[1]  # 第二次调用
    tool_names = [fb.tool_name for fb in fbs if hasattr(fb, "tool_name")]
    # 过滤出本轮的工具反馈（不包括之前的）
    last_three = tool_names[-3:]
    assert last_three == ["tool_a", "tool_b", "tool_c"], (
        f"结果顺序应与输入一致，实际: {last_three}"
    )


@pytest.mark.asyncio
async def test_unknown_tool_treated_as_irreversible() -> None:
    """未注册工具（get_tool_meta 返回 None）视为 IRREVERSIBLE。"""
    broker = MockToolBroker()
    # 故意不注册 tool_a 的 meta

    event_store = MockEventStore()
    client = QueueModelClient([
        SkillOutputEnvelope(
            content="call tool",
            complete=False,
            tool_calls=[{"tool_name": "tool_a", "arguments": {}}],
        ),
        SkillOutputEnvelope(content="done", complete=True),
    ])

    runner = SkillRunner(
        model_client=client,
        tool_broker=broker,
        event_store=event_store,
    )

    result = await runner.run(
        manifest=_make_manifest(),
        execution_context=_make_context(),
        skill_input={"text": "test"},
        prompt="test",
    )

    # 应该正常执行（作为 IRREVERSIBLE 串行执行）
    assert result.status == SkillRunStatus.SUCCEEDED
    assert len(broker.calls) == 1


@pytest.mark.asyncio
async def test_tool_call_id_propagation() -> None:
    """tool_call_id 从 ToolCallSpec 传递到 ToolFeedbackMessage。"""
    broker = MockToolBroker()
    broker.set_tool_meta("tool_a", _make_tool_meta("tool_a", SideEffectLevel.NONE))

    feedback_captured: list[Any] = []

    class CaptureFeedbackClient(QueueModelClient):
        async def generate(self, **kwargs: Any) -> SkillOutputEnvelope:
            feedback_captured.append(list(kwargs.get("feedback", [])))
            return await super().generate(**kwargs)

    event_store = MockEventStore()
    client = CaptureFeedbackClient([
        SkillOutputEnvelope(
            content="call tool",
            complete=False,
            tool_calls=[
                {"tool_name": "tool_a", "arguments": {}, "tool_call_id": "call_abc123"},
            ],
        ),
        SkillOutputEnvelope(content="done", complete=True),
    ])

    runner = SkillRunner(
        model_client=client,
        tool_broker=broker,
        event_store=event_store,
    )

    result = await runner.run(
        manifest=_make_manifest(),
        execution_context=_make_context(),
        skill_input={"text": "test"},
        prompt="test",
    )

    assert result.status == SkillRunStatus.SUCCEEDED
    # 验证 feedback 中的 tool_call_id
    assert len(feedback_captured) >= 2
    fbs = feedback_captured[1]
    tool_fbs = [fb for fb in fbs if hasattr(fb, "tool_call_id") and fb.tool_name == "tool_a"]
    assert len(tool_fbs) > 0
    assert tool_fbs[0].tool_call_id == "call_abc123"


@pytest.mark.asyncio
async def test_regression_single_tool_unchanged_behavior(
    echo_manifest, execution_context, tool_broker, event_store
) -> None:
    """回归测试：单工具调用场景行为与改动前完全一致。"""
    client = QueueModelClient([
        SkillOutputEnvelope(
            content="call tool",
            complete=False,
            tool_calls=[{"tool_name": "system.echo", "arguments": {"text": "hello"}}],
        ),
        SkillOutputEnvelope(content="done", complete=True),
    ])

    runner = SkillRunner(
        model_client=client,
        tool_broker=tool_broker,
        event_store=event_store,
    )

    result = await runner.run(
        manifest=echo_manifest,
        execution_context=execution_context,
        skill_input={"text": "hi"},
        prompt="prompt",
    )

    assert result.status == SkillRunStatus.SUCCEEDED
    assert len(tool_broker.calls) == 1
    assert tool_broker.calls[0][0] == "system.echo"
