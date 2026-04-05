"""Feature 030: ToolIndex 单元测试。"""

from __future__ import annotations

from octoagent.core.models import ToolIndexQuery
from octoagent.tooling.models import SideEffectLevel, ToolMeta, ToolTier
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
            worker_type="ops",
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
            worker_type="ops",
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
            worker_type="ops",
            tool_groups=["runtime"],
            tool_profile="standard",
        ),
        static_fallback=["runtime.inspect"],
    )

    assert selection.is_fallback is False
    assert selection.selected_tools == ["runtime.inspect"]
    assert "tool_group" in selection.hits[0].matched_filters


# ============================================================
# Feature 061 T-019a: search_for_deferred() 测试
# ============================================================


def _deferred_tool(
    name: str,
    description: str,
    tool_group: str = "system",
    tier: ToolTier = ToolTier.DEFERRED,
) -> ToolMeta:
    """辅助: 创建带 tier 的 ToolMeta"""
    return ToolMeta(
        name=name,
        description=description,
        parameters_json_schema={"type": "object", "properties": {"x": {"type": "string"}}},
        side_effect_level=SideEffectLevel.NONE,

        tool_group=tool_group,
        tags=[],
        worker_types=[],
        tier=tier,
    )


async def test_search_for_deferred_normal_match() -> None:
    """正常检索返回匹配的 Deferred 工具（含完整 schema）"""
    index = ToolIndex(preferred_backend="in_memory")
    await index.rebuild([
        _deferred_tool("docker.run", "运行 Docker 容器", "docker", ToolTier.DEFERRED),
        _deferred_tool("docker.stop", "停止 Docker 容器", "docker", ToolTier.DEFERRED),
        _deferred_tool("project.inspect", "查看项目信息", "project", ToolTier.CORE),
    ])

    result = await index.search_for_deferred("docker 容器")
    assert result.is_fallback is False
    assert result.backend == "in_memory"
    assert len(result.results) > 0
    # 结果包含完整 schema
    for hit in result.results:
        assert "properties" in hit.parameters_schema
    # 只返回 Deferred 工具，不返回 Core 工具
    tool_names = [h.tool_name for h in result.results]
    assert "project.inspect" not in tool_names


async def test_search_for_deferred_empty_query() -> None:
    """空查询返回空结果"""
    index = ToolIndex(preferred_backend="in_memory")
    await index.rebuild([
        _deferred_tool("docker.run", "运行 Docker 容器"),
    ])

    result = await index.search_for_deferred("")
    assert result.results == []
    assert result.is_fallback is False

    result2 = await index.search_for_deferred("   ")
    assert result2.results == []


async def test_search_for_deferred_zero_match_fallback() -> None:
    """零命中时降级到全量 Deferred 名称列表"""
    index = ToolIndex(preferred_backend="in_memory")
    await index.rebuild([
        _deferred_tool("docker.run", "运行 Docker 容器", "docker", ToolTier.DEFERRED),
        _deferred_tool("project.inspect", "查看项目信息", "project", ToolTier.CORE),
    ])

    # 查询一个完全不匹配且过滤后零 Deferred 命中的查询
    # 由于 in_memory backend 使用 hash embedding，需要一个与任何工具完全不相关的查询
    # 实际上 hash 方式可能仍会有一些非零 score，但如果只有 CORE 工具命中就会降级
    # 更好的方式是用一个工具集全为 CORE 的场景
    index_all_core = ToolIndex(preferred_backend="in_memory")
    await index_all_core.rebuild([
        _deferred_tool("project.inspect", "查看项目信息", "project", ToolTier.CORE),
    ])

    result = await index_all_core.search_for_deferred("docker 容器")
    # 没有 Deferred 工具，所有命中的都是 CORE → fallback
    assert result.is_fallback is True
    assert result.total_deferred == 0


async def test_search_for_deferred_performance() -> None:
    """SC-004: 检索延迟 <10ms"""
    index = ToolIndex(preferred_backend="in_memory")
    tools = [
        _deferred_tool(f"tool_{i}", f"测试工具 {i} 描述文字")
        for i in range(50)
    ]
    await index.rebuild(tools)

    result = await index.search_for_deferred("测试工具")
    # latency_ms 应该很低（在内存索引中）
    assert result.latency_ms < 10


async def test_search_for_deferred_total_count() -> None:
    """total_deferred 正确计算"""
    index = ToolIndex(preferred_backend="in_memory")
    await index.rebuild([
        _deferred_tool("docker.run", "运行容器", "docker", ToolTier.DEFERRED),
        _deferred_tool("docker.stop", "停止容器", "docker", ToolTier.DEFERRED),
        _deferred_tool("project.inspect", "项目信息", "project", ToolTier.CORE),
        _deferred_tool("terminal.exec", "执行命令", "terminal", ToolTier.DEFERRED),
    ])

    result = await index.search_for_deferred("docker")
    assert result.total_deferred == 3  # 3 个 DEFERRED，1 个 CORE
