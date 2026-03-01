"""大输出裁切测试 -- US4 Large Output Handler

验证超阈值裁切、未超阈值不裁切、工具级自定义阈值、
ArtifactStore 不可用降级、超大输出 >100KB。
"""

from __future__ import annotations

from octoagent.tooling.hooks import LargeOutputHandler
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
        task_id="t1", trace_id="tr1", caller="test", profile=ToolProfile.STANDARD
    )


class TestLargeOutputTruncation:
    """大输出裁切测试"""

    async def test_over_threshold_truncated(self, mock_artifact_store) -> None:
        """超阈值裁切（800 > 500）"""
        handler = LargeOutputHandler(artifact_store=mock_artifact_store)
        meta = _make_meta()
        result = ToolResult(output="x" * 800, duration=0.1)

        new_result = await handler.after_execute(meta, result, _make_context())

        assert new_result.truncated is True
        assert new_result.artifact_ref is not None
        assert "artifact:" in new_result.output
        # 完整内容存储到 ArtifactStore
        assert len(mock_artifact_store.contents) == 1

    async def test_under_threshold_not_truncated(self, mock_artifact_store) -> None:
        """未超阈值不裁切（300 < 500）"""
        handler = LargeOutputHandler(artifact_store=mock_artifact_store)
        meta = _make_meta()
        result = ToolResult(output="x" * 300, duration=0.1)

        new_result = await handler.after_execute(meta, result, _make_context())

        assert new_result.truncated is False
        assert new_result.artifact_ref is None
        assert new_result.output == "x" * 300
        assert len(mock_artifact_store.contents) == 0

    async def test_tool_level_threshold_overrides_global(self, mock_artifact_store) -> None:
        """FR-017: 工具级自定义阈值覆盖全局"""
        handler = LargeOutputHandler(artifact_store=mock_artifact_store, default_threshold=500)
        # 工具级阈值 1000，输出 800 < 1000 不裁切
        meta = _make_meta(output_truncate_threshold=1000)
        result = ToolResult(output="x" * 800, duration=0.1)

        new_result = await handler.after_execute(meta, result, _make_context())

        assert new_result.truncated is False
        assert new_result.output == "x" * 800

    async def test_artifact_store_unavailable_fallback(self, failing_artifact_store) -> None:
        """FR-018 / EC-3: ArtifactStore 不可用降级保留原文"""
        handler = LargeOutputHandler(artifact_store=failing_artifact_store)
        meta = _make_meta()
        output = "x" * 800
        result = ToolResult(output=output, duration=0.1)

        new_result = await handler.after_execute(meta, result, _make_context())

        # 降级：保留原文
        assert new_result.output == output
        assert new_result.truncated is False
        assert new_result.artifact_ref is None

    async def test_very_large_output(self, mock_artifact_store) -> None:
        """EC-6: 超大输出 >100KB"""
        handler = LargeOutputHandler(artifact_store=mock_artifact_store)
        meta = _make_meta()
        large_output = "a" * 150_000  # 150KB
        result = ToolResult(output=large_output, duration=0.5)

        new_result = await handler.after_execute(meta, result, _make_context())

        assert new_result.truncated is True
        assert new_result.artifact_ref is not None
        # 前缀保留（200 字符 + artifact ID）
        assert len(new_result.output) < 500

    async def test_error_result_not_truncated(self, mock_artifact_store) -> None:
        """错误结果不裁切"""
        handler = LargeOutputHandler(artifact_store=mock_artifact_store)
        meta = _make_meta()
        result = ToolResult(output="x" * 800, duration=0.1, is_error=True, error="fail")

        new_result = await handler.after_execute(meta, result, _make_context())

        assert new_result.output == "x" * 800
        assert new_result.truncated is False

    async def test_truncated_result_has_prefix(self, mock_artifact_store) -> None:
        """裁切后 output 包含前 200 字符前缀"""
        handler = LargeOutputHandler(artifact_store=mock_artifact_store)
        meta = _make_meta()
        # 使用可识别的前缀
        output = "PREFIX_" + "x" * 800
        result = ToolResult(output=output, duration=0.1)

        new_result = await handler.after_execute(meta, result, _make_context())

        assert "PREFIX_" in new_result.output
        assert new_result.truncated is True


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
