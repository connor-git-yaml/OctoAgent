"""Feature 061 T-020a: tool_search 工具单元测试

覆盖场景:
- 正常查询返回匹配工具的完整 schema
- 降级场景返回 is_fallback=True
- 事件记录正确（TOOL_SEARCH_EXECUTED）
- 空查询处理正确
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from octoagent.gateway.services.tool_search_tool import create_tool_search_handler
from octoagent.tooling.models import (
    ToolSearchHit,
    ToolSearchResult,
    ToolTier,
)
from octoagent.tooling.schema import reflect_tool_schema


# ============================================================
# Fake 依赖
# ============================================================


class FakeToolIndex:
    """模拟 ToolIndex，支持正常检索和降级模式"""

    def __init__(
        self,
        *,
        results: list[ToolSearchHit] | None = None,
        raise_error: bool = False,
        is_fallback: bool = False,
    ) -> None:
        self._results = results or []
        self._raise_error = raise_error
        self._is_fallback = is_fallback
        self.last_query: str = ""
        self.last_limit: int = 0

    async def search_for_deferred(
        self,
        query: str,
        *,
        limit: int = 5,
    ) -> ToolSearchResult:
        self.last_query = query
        self.last_limit = limit

        if self._raise_error:
            raise RuntimeError("ToolIndex unavailable")

        return ToolSearchResult(
            query=query,
            results=self._results[:limit],
            total_deferred=10,
            is_fallback=self._is_fallback,
            backend="in_memory",
            latency_ms=1,
        )


class FakeEventStore:
    """模拟 EventStore，记录事件"""

    def __init__(self) -> None:
        self.events: list[Any] = []

    async def append_event(self, event: Any) -> None:
        self.events.append(event)

    async def get_next_task_seq(self, task_id: str) -> int:
        return len(self.events) + 1


# ============================================================
# 测试用例
# ============================================================


class TestToolSearchToolMeta:
    """测试 tool_search 工具的元数据（ToolMeta）"""

    def test_tool_meta_is_core(self) -> None:
        """tool_search 必须注册为 CORE tier"""
        handler = create_tool_search_handler(FakeToolIndex())
        meta = reflect_tool_schema(handler)
        assert meta.tier == ToolTier.CORE

    def test_tool_meta_name(self) -> None:
        """tool_search 名称必须为 'tool_search'"""
        handler = create_tool_search_handler(FakeToolIndex())
        meta = reflect_tool_schema(handler)
        assert meta.name == "tool_search"

    def test_tool_meta_side_effect_none(self) -> None:
        """tool_search 的 side_effect_level 必须为 NONE"""
        handler = create_tool_search_handler(FakeToolIndex())
        meta = reflect_tool_schema(handler)
        assert meta.side_effect_level == "none"

    def test_tool_meta_tool_group(self) -> None:
        """tool_search 属于 system 工具组"""
        handler = create_tool_search_handler(FakeToolIndex())
        meta = reflect_tool_schema(handler)
        assert meta.tool_group == "system"

    def test_tool_meta_has_query_param(self) -> None:
        """tool_search 的 schema 包含 query 参数"""
        handler = create_tool_search_handler(FakeToolIndex())
        meta = reflect_tool_schema(handler)
        schema = meta.parameters_json_schema
        assert "query" in schema.get("properties", {}), (
            f"schema 缺少 query 参数: {schema}"
        )


class TestToolSearchNormalQuery:
    """测试 tool_search 正常查询场景"""

    @pytest.fixture
    def sample_hits(self) -> list[ToolSearchHit]:
        return [
            ToolSearchHit(
                tool_name="docker.run",
                description="运行 Docker 容器",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "image": {"type": "string"},
                        "command": {"type": "string"},
                    },
                },
                score=0.95,
                side_effect_level="reversible",
                tool_group="docker",
                tags=["docker", "container"],
            ),
            ToolSearchHit(
                tool_name="docker.stop",
                description="停止 Docker 容器",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "container_id": {"type": "string"},
                    },
                },
                score=0.80,
                side_effect_level="reversible",
                tool_group="docker",
                tags=["docker", "container"],
            ),
        ]

    async def test_normal_query_returns_results(
        self,
        sample_hits: list[ToolSearchHit],
    ) -> None:
        """正常查询返回匹配工具的完整 schema"""
        index = FakeToolIndex(results=sample_hits)
        handler = create_tool_search_handler(index)

        raw = await handler(query="docker container management")
        result = json.loads(raw)

        assert len(result["results"]) == 2
        assert result["results"][0]["tool_name"] == "docker.run"
        assert result["results"][1]["tool_name"] == "docker.stop"
        assert result["is_fallback"] is False
        assert "parameters_schema" in result["results"][0]

    async def test_query_passed_to_index(
        self,
        sample_hits: list[ToolSearchHit],
    ) -> None:
        """查询参数正确传递给 ToolIndex"""
        index = FakeToolIndex(results=sample_hits)
        handler = create_tool_search_handler(index)

        await handler(query="docker管理", limit=3)

        assert index.last_query == "docker管理"
        assert index.last_limit == 3

    async def test_limit_clamped(self) -> None:
        """limit 参数被限制在 [1, 20] 范围"""
        index = FakeToolIndex()
        handler = create_tool_search_handler(index)

        # limit 超过上限
        await handler(query="test", limit=50)
        assert index.last_limit == 20

        # limit 小于下限
        await handler(query="test", limit=0)
        assert index.last_limit == 1

    async def test_result_contains_latency(
        self,
        sample_hits: list[ToolSearchHit],
    ) -> None:
        """结果包含实际延迟（ms）"""
        index = FakeToolIndex(results=sample_hits)
        handler = create_tool_search_handler(index)

        raw = await handler(query="docker")
        result = json.loads(raw)

        assert "latency_ms" in result
        assert isinstance(result["latency_ms"], int)
        assert result["latency_ms"] >= 0


class TestToolSearchFallback:
    """测试 tool_search 降级场景"""

    async def test_index_error_returns_fallback(self) -> None:
        """ToolIndex 不可用时返回 is_fallback=True"""
        index = FakeToolIndex(raise_error=True)
        handler = create_tool_search_handler(index)

        raw = await handler(query="docker")
        result = json.loads(raw)

        assert result["is_fallback"] is True
        assert result["backend"] == "error"
        assert len(result["results"]) == 0

    async def test_index_fallback_mode(self) -> None:
        """ToolIndex 降级模式正确标记"""
        hits = [
            ToolSearchHit(
                tool_name="some.tool",
                description="desc",
                parameters_schema={},
                score=0.0,
            ),
        ]
        index = FakeToolIndex(results=hits, is_fallback=True)
        handler = create_tool_search_handler(index)

        raw = await handler(query="anything")
        result = json.loads(raw)

        assert result["is_fallback"] is True


class TestToolSearchEmptyQuery:
    """测试 tool_search 空查询处理"""

    async def test_empty_query_returns_empty_results(self) -> None:
        """空查询返回空结果"""
        index = FakeToolIndex()
        handler = create_tool_search_handler(index)

        raw = await handler(query="")
        result = json.loads(raw)

        assert result["results"] == []


class TestToolSearchEventRecording:
    """测试 tool_search 事件记录"""

    async def test_event_recorded_on_success(self) -> None:
        """成功查询时生成 TOOL_SEARCH_EXECUTED 事件"""
        hits = [
            ToolSearchHit(
                tool_name="docker.run",
                description="运行容器",
                parameters_schema={"type": "object"},
                score=0.9,
            ),
        ]
        index = FakeToolIndex(results=hits)
        event_store = FakeEventStore()
        handler = create_tool_search_handler(index, event_store=event_store)

        await handler(query="docker")

        assert len(event_store.events) == 1
        event = event_store.events[0]
        assert event.type == "TOOL_SEARCH_EXECUTED"
        assert event.payload["query"] == "docker"
        assert event.payload["results_count"] == 1
        assert event.payload["result_names"] == ["docker.run"]
        assert event.payload["is_fallback"] is False

    async def test_event_recorded_on_fallback(self) -> None:
        """降级查询时事件标记 is_fallback=True"""
        index = FakeToolIndex(raise_error=True)
        event_store = FakeEventStore()
        handler = create_tool_search_handler(index, event_store=event_store)

        await handler(query="docker")

        assert len(event_store.events) == 1
        event = event_store.events[0]
        assert event.payload["is_fallback"] is True

    async def test_no_event_without_event_store(self) -> None:
        """未提供 event_store 时不报错"""
        index = FakeToolIndex()
        handler = create_tool_search_handler(index, event_store=None)

        # 不应抛出异常
        raw = await handler(query="docker")
        result = json.loads(raw)
        assert "query" in result

    async def test_event_store_error_does_not_block(self) -> None:
        """event_store 写入失败不阻塞主逻辑"""
        index = FakeToolIndex()
        broken_store = AsyncMock()
        broken_store.append_event = AsyncMock(side_effect=RuntimeError("DB down"))
        broken_store.get_next_task_seq = AsyncMock(return_value=1)

        handler = create_tool_search_handler(index, event_store=broken_store)

        # 不应抛出异常
        raw = await handler(query="docker")
        result = json.loads(raw)
        assert result["query"] == "docker"
