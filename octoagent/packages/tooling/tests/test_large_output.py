"""大输出裁切测试 -- LargeOutputHandler + head_tail_truncate

验证：
- 动态阈值计算（按上下文窗口 50%）
- Head + Tail 智能截断
- 工具级自定义阈值
- ArtifactStore 不可用降级
- 超大输出
"""

from __future__ import annotations

from octoagent.tooling.hooks import LargeOutputHandler
from octoagent.tooling.hooks_legacy import (
    calculate_max_tool_result_chars,
    head_tail_truncate,
)
from octoagent.tooling.models import (
    ExecutionContext,
    SideEffectLevel,
    ToolMeta,
    ToolProfile,
    ToolResult,
)


def _make_meta(
    name: str = "test_tool",
    output_truncate_threshold: int | None = None,
) -> ToolMeta:
    """创建测试用 ToolMeta"""
    return ToolMeta(
        name=name,
        description="test",
        parameters_json_schema={},
        side_effect_level=SideEffectLevel.NONE,
        tool_profile=ToolProfile.MINIMAL,
        tool_group="system",
        output_truncate_threshold=output_truncate_threshold,
    )


def _make_context() -> ExecutionContext:
    return ExecutionContext(
        task_id="t1",
        trace_id="tr1",
        caller="test",
        agent_runtime_id="runtime-test-1",
        agent_session_id="session-test-1",
        work_id="work-test-1",
        profile=ToolProfile.STANDARD,
    )


class _CapturingBroadcaster:
    def __init__(self) -> None:
        self.broadcasts: list[tuple[str, object]] = []

    async def broadcast(self, task_id: str, event: object) -> None:
        self.broadcasts.append((task_id, event))


class TestCalculateMaxToolResultChars:
    """动态阈值计算测试"""

    def test_128k_context(self) -> None:
        # 128K × 50% × 4 = 256,000
        assert calculate_max_tool_result_chars(128_000) == 256_000

    def test_32k_context(self) -> None:
        # 32K × 50% × 4 = 64,000
        assert calculate_max_tool_result_chars(32_000) == 64_000

    def test_hard_max_cap(self) -> None:
        # 2M × 50% × 4 = 4,000,000 → 被 400K 硬上限 cap
        assert calculate_max_tool_result_chars(2_000_000) == 400_000

    def test_min_threshold(self) -> None:
        # 极小窗口 → 不低于 2,000
        assert calculate_max_tool_result_chars(100) == 2_000

    def test_zero_context(self) -> None:
        assert calculate_max_tool_result_chars(0) == 2_000


class TestHeadTailTruncate:
    """Head + Tail 智能截断测试"""

    def test_short_text_not_truncated(self) -> None:
        text = "hello world"
        assert head_tail_truncate(text, 100) == text

    def test_exactly_at_limit(self) -> None:
        text = "x" * 1000
        assert head_tail_truncate(text, 1000) == text

    def test_head_only_without_important_tail(self) -> None:
        """无重要尾部时只保留头部"""
        text = "a" * 5000
        result = head_tail_truncate(text, 1000)
        assert len(result) <= 1000
        assert result.startswith("aaa")
        assert "⚠️" in result
        assert "offset/limit" in result

    def test_head_tail_with_error_in_tail(self) -> None:
        """尾部包含 error 关键词时保留头尾"""
        head = "HEADER\n" + "data\n" * 200
        tail = "\nERROR: something failed\nTraceback:\n  line 42\n"
        text = head + "middle\n" * 200 + tail
        result = head_tail_truncate(text, 2000)
        assert "HEADER" in result
        assert "ERROR" in result or "failed" in result
        assert "⚠️" in result

    def test_head_tail_with_summary_in_tail(self) -> None:
        """尾部包含 summary/total 关键词时保留头尾"""
        text = "line\n" * 500 + "\nTotal: 42 items\nSummary: all done\n"
        result = head_tail_truncate(text, 2000)
        assert "Total" in result or "Summary" in result
        assert "⚠️" in result

    def test_truncation_mentions_offset_limit(self) -> None:
        """截断标记引导 LLM 使用 offset/limit"""
        text = "x" * 5000
        result = head_tail_truncate(text, 1000)
        assert "offset/limit" in result


class TestLargeOutputTruncation:
    """大输出裁切测试（使用小 context_window_tokens 模拟截断）"""

    async def test_over_threshold_truncated(self, mock_artifact_store) -> None:
        """使用工具级阈值 500，输出 800 > 500 触发截断"""
        handler = LargeOutputHandler(
            artifact_store=mock_artifact_store,
            context_window_tokens=128_000,
        )
        meta = _make_meta(output_truncate_threshold=500)
        result = ToolResult(output="x" * 800, duration=0.1)

        new_result = await handler.after_execute(meta, result, _make_context())

        assert new_result.truncated is True
        assert new_result.artifact_ref is not None
        # Head + Tail 截断标记
        assert "⚠️" in new_result.output
        # 完整内容存储到 ArtifactStore
        assert len(mock_artifact_store.contents) == 1

    async def test_under_threshold_not_truncated(self, mock_artifact_store) -> None:
        """未超阈值不裁切"""
        handler = LargeOutputHandler(
            artifact_store=mock_artifact_store,
            context_window_tokens=128_000,
        )
        meta = _make_meta()
        result = ToolResult(output="x" * 300, duration=0.1)

        new_result = await handler.after_execute(meta, result, _make_context())

        assert new_result.truncated is False
        assert new_result.artifact_ref is None
        assert new_result.output == "x" * 300
        assert len(mock_artifact_store.contents) == 0

    async def test_tool_level_threshold_overrides_default(self, mock_artifact_store) -> None:
        """FR-017: 工具级自定义阈值覆盖动态默认值"""
        handler = LargeOutputHandler(
            artifact_store=mock_artifact_store,
            context_window_tokens=128_000,
        )
        # 工具级阈值 1000，输出 800 < 1000 不裁切
        meta = _make_meta(output_truncate_threshold=1000)
        result = ToolResult(output="x" * 800, duration=0.1)

        new_result = await handler.after_execute(meta, result, _make_context())

        assert new_result.truncated is False
        assert new_result.output == "x" * 800

    async def test_artifact_store_unavailable_still_truncates(
        self, failing_artifact_store
    ) -> None:
        """FR-018: ArtifactStore 不可用时仍执行截断（不保留原文）"""
        handler = LargeOutputHandler(
            artifact_store=failing_artifact_store,
            context_window_tokens=128_000,
        )
        meta = _make_meta(output_truncate_threshold=500)
        output = "x" * 800
        result = ToolResult(output=output, duration=0.1)

        new_result = await handler.after_execute(meta, result, _make_context())

        # 截断仍然执行，只是没有 artifact_ref
        assert new_result.truncated is True
        assert new_result.artifact_ref is None
        assert "⚠️" in new_result.output

    async def test_very_large_output(self, mock_artifact_store) -> None:
        """超大输出 >400K 字符（超过动态阈值）"""
        handler = LargeOutputHandler(
            artifact_store=mock_artifact_store,
            context_window_tokens=128_000,
        )
        meta = _make_meta()
        large_output = "a" * 500_000  # 500K > 256K 动态阈值
        result = ToolResult(output=large_output, duration=0.5)

        new_result = await handler.after_execute(meta, result, _make_context())

        assert new_result.truncated is True
        assert new_result.artifact_ref is not None
        assert len(new_result.output) < 260_000

    async def test_error_result_not_truncated(self, mock_artifact_store) -> None:
        """错误结果不裁切"""
        handler = LargeOutputHandler(
            artifact_store=mock_artifact_store,
            context_window_tokens=128_000,
        )
        meta = _make_meta(output_truncate_threshold=500)
        result = ToolResult(output="x" * 800, duration=0.1, is_error=True, error="fail")

        new_result = await handler.after_execute(meta, result, _make_context())

        assert new_result.output == "x" * 800
        assert new_result.truncated is False

    async def test_truncated_result_has_head(self, mock_artifact_store) -> None:
        """裁切后 output 包含开头内容"""
        handler = LargeOutputHandler(
            artifact_store=mock_artifact_store,
            context_window_tokens=128_000,
        )
        meta = _make_meta(output_truncate_threshold=500)
        output = "PREFIX_" + "x" * 800
        result = ToolResult(output=output, duration=0.1)

        new_result = await handler.after_execute(meta, result, _make_context())

        assert "PREFIX_" in new_result.output
        assert new_result.truncated is True

    async def test_truncated_result_emits_artifact_created_event(
        self, mock_artifact_store, mock_event_store
    ) -> None:
        """裁切后的 artifact 会补写 ARTIFACT_CREATED 并挂上 agent session。"""
        handler = LargeOutputHandler(
            artifact_store=mock_artifact_store,
            event_store=mock_event_store,
            context_window_tokens=128_000,
        )
        meta = _make_meta(output_truncate_threshold=500)
        result = ToolResult(output="x" * 800, duration=0.1)

        new_result = await handler.after_execute(meta, result, _make_context())

        assert new_result.artifact_ref is not None
        artifact_events = [
            e for e in mock_event_store.events if e.type.value == "ARTIFACT_CREATED"
        ]
        assert len(artifact_events) == 1
        assert artifact_events[0].payload["artifact_id"] == new_result.artifact_ref
        assert artifact_events[0].payload["session_id"] == "session-test-1"
        assert artifact_events[0].payload["source"] == "tool_output:test_tool"

    async def test_truncated_result_broadcasts_artifact_created_event(
        self, mock_artifact_store, mock_event_store
    ) -> None:
        broadcaster = _CapturingBroadcaster()
        handler = LargeOutputHandler(
            artifact_store=mock_artifact_store,
            event_store=mock_event_store,
            event_broadcaster=broadcaster,
            context_window_tokens=128_000,
        )
        meta = _make_meta(output_truncate_threshold=500)
        result = ToolResult(output="x" * 800, duration=0.1)

        new_result = await handler.after_execute(meta, result, _make_context())

        assert new_result.artifact_ref is not None
        assert len(broadcaster.broadcasts) == 1
        task_id, event = broadcaster.broadcasts[0]
        assert task_id == "t1"
        assert getattr(event.type, "value", event.type) == "ARTIFACT_CREATED"
        assert event.payload["artifact_id"] == new_result.artifact_ref

    async def test_default_threshold_is_dynamic(self, mock_artifact_store) -> None:
        """128K 上下文窗口的默认阈值是 256K 字符，1589 字符的文件不会被截断"""
        handler = LargeOutputHandler(
            artifact_store=mock_artifact_store,
            context_window_tokens=128_000,
        )
        meta = _make_meta()
        # 模拟 USER.md (1589 字符) 加 JSON 包装（约 1700 字符）
        result = ToolResult(output="x" * 1700, duration=0.1)

        new_result = await handler.after_execute(meta, result, _make_context())

        # 不应被截断！这是之前 500 阈值导致的核心 bug
        assert new_result.truncated is False
        assert new_result.output == "x" * 1700


class TestLargeOutputHandlerProperties:
    """LargeOutputHandler 属性测试"""

    def test_name(self, mock_artifact_store) -> None:
        handler = LargeOutputHandler(artifact_store=mock_artifact_store)
        assert handler.name == "large_output_handler"

    def test_priority(self, mock_artifact_store) -> None:
        handler = LargeOutputHandler(artifact_store=mock_artifact_store)
        assert handler.priority == 50

    def test_fail_mode(self, mock_artifact_store) -> None:
        handler = LargeOutputHandler(artifact_store=mock_artifact_store)
        assert handler.fail_mode == "open"
