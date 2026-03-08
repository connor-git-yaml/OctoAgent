"""Feature 030: ToolIndex 单元测试。"""

from __future__ import annotations

from octoagent.core.models import ToolIndexQuery, WorkerType
from octoagent.tooling.models import SideEffectLevel, ToolMeta, ToolProfile
from octoagent.tooling.tool_index import ToolIndex


def _tool_meta(
    *,
    name: str,
    description: str,
    tool_group: str,
    tags: list[str],
    worker_types: list[str],
) -> ToolMeta:
    return ToolMeta(
        name=name,
        description=description,
        parameters_json_schema={"type": "object", "properties": {}},
        side_effect_level=SideEffectLevel.NONE,
        tool_profile=ToolProfile.MINIMAL,
        tool_group=tool_group,
        tags=tags,
        worker_types=worker_types,
        manifest_ref=f"builtin://{name}",
    )


async def test_tool_index_selects_tools_with_worker_filter() -> None:
    index = ToolIndex(preferred_backend="in_memory")
    await index.rebuild(
        [
            _tool_meta(
                name="runtime.inspect",
                description="诊断 runtime 与 health",
                tool_group="runtime",
                tags=["runtime", "diagnostics"],
                worker_types=["ops"],
            ),
            _tool_meta(
                name="artifact.list",
                description="查看 artifact 与研究资料",
                tool_group="artifact",
                tags=["artifact", "research"],
                worker_types=["research", "dev"],
            ),
        ]
    )

    selection = await index.select_tools(
        ToolIndexQuery(
            query="请诊断 runtime health",
            limit=3,
            worker_type=WorkerType.OPS,
            tool_groups=["runtime"],
            tags=["diagnostics"],
        ),
        static_fallback=["runtime.inspect"],
    )

    assert selection.backend == "in_memory"
    assert selection.is_fallback is False
    assert selection.selected_tools == ["runtime.inspect"]
    assert selection.hits[0].matched_filters == ["tool_group", "worker_type", "tags"]


async def test_tool_index_empty_hits_falls_back_to_static_toolset() -> None:
    index = ToolIndex(preferred_backend="unknown-backend")
    await index.rebuild(
        [
            _tool_meta(
                name="project.inspect",
                description="读取 project 摘要",
                tool_group="project",
                tags=["project"],
                worker_types=["general"],
            )
        ]
    )

    selection = await index.select_tools(
        ToolIndexQuery(
            query="完全不相关的查询",
            limit=2,
            worker_type=WorkerType.OPS,
            tool_groups=["runtime"],
        ),
        static_fallback=["project.inspect", "runtime.inspect"],
    )

    assert index.degraded_reason == "unknown_backend_fallback"
    assert selection.is_fallback is True
    assert selection.selected_tools == ["project.inspect", "runtime.inspect"]
    assert selection.warnings == ["tool_index_empty_fallback_to_static_toolset"]
    assert selection.hits == []


async def test_tool_index_standard_profile_can_match_minimal_tools() -> None:
    index = ToolIndex(preferred_backend="in_memory")
    await index.rebuild(
        [
            _tool_meta(
                name="runtime.inspect",
                description="诊断 runtime 与 health",
                tool_group="runtime",
                tags=["runtime", "diagnostics"],
                worker_types=["ops"],
            )
        ]
    )

    selection = await index.select_tools(
        ToolIndexQuery(
            query="请先做 runtime 诊断",
            limit=3,
            worker_type=WorkerType.OPS,
            tool_groups=["runtime"],
            tool_profile=ToolProfile.STANDARD.value,
        ),
        static_fallback=["runtime.inspect"],
    )

    assert selection.is_fallback is False
    assert selection.selected_tools == ["runtime.inspect"]
    assert "tool_profile" in selection.hits[0].matched_filters
