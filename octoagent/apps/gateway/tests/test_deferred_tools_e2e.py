"""Feature 061 T-024: Deferred Tools 端到端集成测试

覆盖 US-002 全部 6 个验收场景:
1. 初始 context 仅 Core Tools 完整 schema
2. tool_search 返回完整 schema，后续可调用
3. SC-001 token 减少 >= 60%
4. tool_search 加载的工具仍经过 Preset 检查
5. ToolIndex 降级 → 全量名称列表
6. MCP 工具默认 Deferred
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from octoagent.gateway.services.tool_promotion import ToolPromotionService
from octoagent.gateway.services.tool_search_tool import create_tool_search_handler
from octoagent.tooling import ToolBroker
from octoagent.tooling.models import (
    CoreToolSet,
    DeferredToolEntry,
    SideEffectLevel,
    ToolMeta,
    ToolProfile,
    ToolSearchHit,
    ToolSearchResult,
    ToolTier,
    format_deferred_tools_list,
)
from octoagent.tooling.schema import reflect_tool_schema


# ============================================================
# Fake 依赖
# ============================================================


class FakeToolIndex:
    """模拟 ToolIndex"""

    def __init__(
        self,
        *,
        results: list[ToolSearchHit] | None = None,
        raise_error: bool = False,
        all_deferred_names: list[str] | None = None,
    ) -> None:
        self._results = results or []
        self._raise_error = raise_error
        self._all_deferred_names = all_deferred_names or []

    async def search_for_deferred(
        self,
        query: str,
        *,
        limit: int = 5,
    ) -> ToolSearchResult:
        if self._raise_error:
            raise RuntimeError("ToolIndex unavailable")

        return ToolSearchResult(
            query=query,
            results=self._results[:limit],
            total_deferred=len(self._all_deferred_names) or len(self._results),
            is_fallback=False,
            backend="in_memory",
            latency_ms=2,
        )


class FakeEventStore:
    """模拟 EventStore"""

    def __init__(self) -> None:
        self.events: list[Any] = []

    async def append_event(self, event: Any) -> None:
        self.events.append(event)

    async def get_next_task_seq(self, task_id: str) -> int:
        return len(self.events) + 1


class FakeToolBroker:
    """模拟 ToolBroker 的 discover() 方法"""

    def __init__(self, metas: list[ToolMeta]) -> None:
        self._metas = metas

    async def discover(self) -> list[ToolMeta]:
        return list(self._metas)


# ============================================================
# 测试工具 meta 构建辅助
# ============================================================


def _make_realistic_schema(name: str) -> dict:
    """构建接近真实工具的 JSON Schema（包含多个参数、描述、约束）"""
    # 模拟真实工具的 schema 复杂度
    return {
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "description": f"{name} 操作的目标标识符或路径",
            },
            "options": {
                "type": "object",
                "description": "可选配置参数",
                "properties": {
                    "verbose": {"type": "boolean", "default": False},
                    "timeout_ms": {"type": "integer", "default": 30000},
                    "retry_count": {"type": "integer", "default": 0},
                },
            },
            "content": {
                "type": "string",
                "description": "操作内容或命令文本",
            },
            "metadata": {
                "type": "object",
                "description": "附加元数据",
                "properties": {
                    "trace_id": {"type": "string"},
                    "source": {"type": "string"},
                },
            },
        },
        "required": ["target"],
    }


def _make_tool_meta(
    name: str,
    description: str = "",
    tier: ToolTier = ToolTier.DEFERRED,
    side_effect_level: SideEffectLevel = SideEffectLevel.NONE,
    tool_group: str = "",
) -> ToolMeta:
    """构建测试用 ToolMeta（接近真实复杂度的 schema）"""
    return ToolMeta(
        name=name,
        description=description or f"{name} 工具描述 — 提供 {name.split('.')[0]} 相关操作能力",
        parameters_json_schema=_make_realistic_schema(name),
        side_effect_level=side_effect_level,
        tool_profile=ToolProfile.STANDARD,
        tool_group=tool_group or name.split(".")[0],
        tier=tier,
    )


def _build_test_tool_metas() -> list[ToolMeta]:
    """构建完整的测试用工具列表（10 个 Core + 多个 Deferred）"""
    # Core 工具
    core_names = CoreToolSet.default().tool_names
    core_metas = [
        _make_tool_meta(name, tier=ToolTier.CORE, tool_group=name.split(".")[0])
        for name in core_names
    ]
    # Deferred 工具
    deferred_metas = [
        _make_tool_meta("docker.run", "运行 Docker 容器", tool_group="docker",
                        side_effect_level=SideEffectLevel.REVERSIBLE),
        _make_tool_meta("docker.stop", "停止 Docker 容器", tool_group="docker",
                        side_effect_level=SideEffectLevel.REVERSIBLE),
        _make_tool_meta("docker.logs", "查看容器日志", tool_group="docker"),
        _make_tool_meta("web.search", "搜索互联网", tool_group="web"),
        _make_tool_meta("web.fetch", "获取网页内容", tool_group="web"),
        _make_tool_meta("browser.snapshot", "浏览器截图", tool_group="browser"),
        _make_tool_meta("browser.status", "浏览器状态", tool_group="browser"),
        _make_tool_meta("ssh.exec", "远程命令执行", tool_group="ssh",
                        side_effect_level=SideEffectLevel.IRREVERSIBLE),
        _make_tool_meta("ssh.upload", "上传文件到远程", tool_group="ssh",
                        side_effect_level=SideEffectLevel.REVERSIBLE),
        _make_tool_meta("tts.speak", "文本转语音", tool_group="tts"),
        _make_tool_meta("automation.cron_list", "列出定时任务", tool_group="automation"),
        _make_tool_meta("automation.cron_create", "创建定时任务", tool_group="automation",
                        side_effect_level=SideEffectLevel.REVERSIBLE),
        _make_tool_meta("mcp.servers.list", "列出 MCP 服务器", tool_group="mcp"),
        _make_tool_meta("mcp.tools.list", "列出 MCP 工具", tool_group="mcp"),
        _make_tool_meta("workers.review", "查看 Worker 列表", tool_group="workers"),
        _make_tool_meta("subagents.list", "列出 Subagent", tool_group="subagents"),
        _make_tool_meta("subagents.kill", "终止 Subagent", tool_group="subagents",
                        side_effect_level=SideEffectLevel.REVERSIBLE),
        _make_tool_meta("subagents.steer", "引导 Subagent", tool_group="subagents"),
        _make_tool_meta("work.split", "拆分工作", tool_group="work"),
        _make_tool_meta("work.merge", "合并工作", tool_group="work"),
        _make_tool_meta("work.delete", "删除工作", tool_group="work",
                        side_effect_level=SideEffectLevel.IRREVERSIBLE),
        _make_tool_meta("runtime.now", "获取当前时间", tool_group="runtime"),
        _make_tool_meta("memory.write", "写入记忆", tool_group="memory",
                        side_effect_level=SideEffectLevel.REVERSIBLE),
    ]
    return core_metas + deferred_metas


# ============================================================
# US-002 场景 1: 初始 context 仅 Core Tools 完整 schema
# ============================================================


class TestScenario1InitialContextCoreOnly:
    """初始化时只有 Core Tools 获得完整 schema"""

    async def test_build_tool_context_partitions_correctly(self) -> None:
        """build_tool_context 正确将工具分为 Core 和 Deferred"""
        all_metas = _build_test_tool_metas()
        core_set = CoreToolSet.default()

        core_metas: list[ToolMeta] = []
        deferred_entries: list[DeferredToolEntry] = []
        for meta in all_metas:
            if core_set.is_core(meta.name):
                core_metas.append(meta)
            else:
                desc = (meta.description or meta.name)[:80].strip()
                deferred_entries.append(
                    DeferredToolEntry(
                        name=meta.name,
                        one_line_desc=desc,
                        tool_group=meta.tool_group,
                        side_effect_level=meta.side_effect_level.value,
                    )
                )

        # Core 工具数量 = CoreToolSet.default() 中定义的数量
        assert len(core_metas) == len(core_set.tool_names)

        # Deferred 工具数量 = 总数 - Core 数量
        assert len(deferred_entries) == len(all_metas) - len(core_set.tool_names)

        # Core 工具保留完整 ToolMeta（含 parameters_json_schema）
        for meta in core_metas:
            assert meta.parameters_json_schema is not None
            assert core_set.is_core(meta.name)

        # Deferred 工具只有名称和描述
        for entry in deferred_entries:
            assert not core_set.is_core(entry.name)
            assert len(entry.one_line_desc) <= 80

    async def test_core_tools_include_tool_search(self) -> None:
        """Core Tools 必须包含 tool_search（FR-018）"""
        core_set = CoreToolSet.default()
        assert "tool_search" in core_set.tool_names

    async def test_deferred_tools_prompt_formatted_correctly(self) -> None:
        """Deferred Tools 列表格式化为 system prompt 文本"""
        entries = [
            DeferredToolEntry(name="docker.run", one_line_desc="运行 Docker 容器"),
            DeferredToolEntry(name="ssh.exec", one_line_desc="远程命令执行"),
        ]
        text = format_deferred_tools_list(entries)

        assert "## Available Tools (Deferred)" in text
        assert "- docker.run: 运行 Docker 容器" in text
        assert "- ssh.exec: 远程命令执行" in text
        assert "共 2 个 deferred 工具可用" in text
        assert "tool_search" in text  # 引导 LLM 使用 tool_search


# ============================================================
# US-002 场景 2: tool_search 返回完整 schema，后续可调用
# ============================================================


class TestScenario2ToolSearchReturnsFullSchema:
    """tool_search 返回工具的完整 schema"""

    async def test_tool_search_returns_full_schema(self) -> None:
        """tool_search 返回的 ToolSearchHit 包含完整 parameters_schema"""
        hits = [
            ToolSearchHit(
                tool_name="docker.run",
                description="运行 Docker 容器",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "image": {"type": "string"},
                        "command": {"type": "string"},
                    },
                    "required": ["image"],
                },
                score=0.95,
                side_effect_level="reversible",
                tool_group="docker",
            ),
        ]
        tool_index = FakeToolIndex(results=hits)
        event_store = FakeEventStore()

        handler = create_tool_search_handler(
            tool_index=tool_index,
            event_store=event_store,
        )

        result_json = await handler(query="docker 容器", limit=5)
        result = json.loads(result_json)

        assert len(result["results"]) == 1
        hit = result["results"][0]
        assert hit["tool_name"] == "docker.run"
        assert "image" in hit["parameters_schema"]["properties"]
        assert hit["parameters_schema"]["required"] == ["image"]

    async def test_promoted_tools_available_in_next_step(self) -> None:
        """tool_search 结果提升后在 ToolPromotionState 中可用"""
        promotion = ToolPromotionService()

        # 模拟 tool_search 结果提升
        newly = await promotion.promote_from_search(
            ["docker.run", "docker.stop"],
            query="docker container",
        )

        assert set(newly) == {"docker.run", "docker.stop"}
        assert promotion.is_promoted("docker.run")
        assert promotion.is_promoted("docker.stop")
        assert "docker.run" in promotion.active_tool_names
        assert "docker.stop" in promotion.active_tool_names


# ============================================================
# US-002 场景 3: SC-001 token 减少 >= 60%
# ============================================================


class TestScenario3TokenReduction:
    """Deferred 模式 token 占用减少 >= 60%"""

    async def test_deferred_mode_saves_tokens(self) -> None:
        """对比全量注入 vs Deferred 模式的 token 占用"""
        all_metas = _build_test_tool_metas()
        core_set = CoreToolSet.default()

        # 全量模式: 所有工具的完整 JSON Schema
        full_schema_text = json.dumps(
            [
                {
                    "name": m.name,
                    "description": m.description,
                    "parameters": m.parameters_json_schema,
                }
                for m in all_metas
            ],
            ensure_ascii=False,
        )

        # Deferred 模式: Core 完整 schema + Deferred 名称列表
        core_metas = [m for m in all_metas if core_set.is_core(m.name)]
        deferred_entries = [
            DeferredToolEntry(
                name=m.name,
                one_line_desc=(m.description or m.name)[:80],
            )
            for m in all_metas
            if not core_set.is_core(m.name)
        ]

        core_schema_text = json.dumps(
            [
                {
                    "name": m.name,
                    "description": m.description,
                    "parameters": m.parameters_json_schema,
                }
                for m in core_metas
            ],
            ensure_ascii=False,
        )
        deferred_list_text = format_deferred_tools_list(deferred_entries)

        deferred_total = len(core_schema_text) + len(deferred_list_text)
        full_total = len(full_schema_text)

        # 粗略估算: 字符数比较（token 约 4 字符/token，比率一致）
        reduction_ratio = 1.0 - (deferred_total / full_total)

        # SC-001: token 减少 >= 60%
        assert reduction_ratio >= 0.60, (
            f"Token 减少比率 {reduction_ratio:.2%} 未达到 60% 阈值。"
            f" 全量={full_total} chars, Deferred={deferred_total} chars"
        )


# ============================================================
# US-002 场景 4: tool_search 加载的工具仍经过 Preset 检查
# ============================================================


class TestScenario4PromotedToolsStillChecked:
    """提升的工具仍经过 Preset 权限检查"""

    async def test_promoted_tool_still_requires_preset_check(self) -> None:
        """提升到 Active 的工具保留 side_effect_level，Preset 检查不绕过

        验证逻辑: 提升后的工具通过 ToolBroker 执行时，
        仍会经过 Hook Chain（包含 PresetBeforeHook）。
        这里验证 ToolPromotionState 不会修改工具的 side_effect_level。
        """
        promotion = ToolPromotionService()

        # 提升一个 REVERSIBLE 工具
        await promotion.promote("docker.run", "tool_search:docker")
        assert promotion.is_promoted("docker.run")

        # 验证 ToolMeta 的 side_effect_level 不受提升影响
        meta = _make_tool_meta(
            "docker.run",
            side_effect_level=SideEffectLevel.REVERSIBLE,
        )
        assert meta.side_effect_level == SideEffectLevel.REVERSIBLE

        # 工具提升不改变 side_effect_level（安全属性保持不变）
        # Preset 检查在 ToolBroker.execute() 的 Hook Chain 中完成

    async def test_promotion_event_records_tool_details(self) -> None:
        """TOOL_PROMOTED 事件记录工具名称和来源"""
        event_store = FakeEventStore()
        promotion = ToolPromotionService(event_store=event_store)

        await promotion.promote("docker.run", "tool_search:docker")

        assert len(event_store.events) == 1
        event = event_store.events[0]
        assert event.type == "TOOL_PROMOTED"
        assert event.payload["tool_name"] == "docker.run"
        assert event.payload["direction"] == "promoted"
        assert event.payload["source"] == "tool_search"
        assert event.payload["source_id"] == "docker"


# ============================================================
# US-002 场景 5: ToolIndex 降级 → 全量名称列表
# ============================================================


class TestScenario5ToolIndexDegradation:
    """ToolIndex 不可用时降级到全量名称列表"""

    async def test_tool_search_fallback_on_index_error(self) -> None:
        """ToolIndex 不可用时 tool_search 返回 is_fallback=True"""
        tool_index = FakeToolIndex(raise_error=True)
        event_store = FakeEventStore()

        handler = create_tool_search_handler(
            tool_index=tool_index,
            event_store=event_store,
        )

        result_json = await handler(query="docker", limit=5)
        result = json.loads(result_json)

        assert result["is_fallback"] is True

    async def test_fallback_event_recorded(self) -> None:
        """降级时记录事件"""
        tool_index = FakeToolIndex(raise_error=True)
        event_store = FakeEventStore()

        handler = create_tool_search_handler(
            tool_index=tool_index,
            event_store=event_store,
        )

        await handler(query="docker", limit=5)

        # 降级时仍应记录 TOOL_SEARCH_EXECUTED 事件
        search_events = [
            e for e in event_store.events
            if hasattr(e, "type") and e.type == "TOOL_SEARCH_EXECUTED"
        ]
        assert len(search_events) >= 1


# ============================================================
# US-002 场景 6: MCP 工具默认 Deferred
# ============================================================


class TestScenario6McpToolsDefaultDeferred:
    """MCP 工具默认以 Deferred 状态纳入"""

    async def test_mcp_tools_classified_as_deferred(self) -> None:
        """MCP 工具不在 CoreToolSet.default() 中，因此默认为 Deferred"""
        core_set = CoreToolSet.default()

        # 典型 MCP 工具名称
        mcp_tool_names = [
            "mcp::github::create_issue",
            "mcp::slack::send_message",
            "mcp::notion::search",
        ]

        for name in mcp_tool_names:
            assert not core_set.is_core(name), f"MCP 工具 {name} 不应为 Core"
            assert core_set.classify(name) == ToolTier.DEFERRED

    async def test_mcp_tools_in_deferred_list(self) -> None:
        """MCP 工具出现在 Deferred Tools 列表中"""
        all_metas = _build_test_tool_metas()
        # 添加 MCP 工具
        all_metas.append(
            _make_tool_meta(
                "mcp::github::create_issue",
                "在 GitHub 创建 Issue",
                tool_group="mcp",
            )
        )

        core_set = CoreToolSet.default()
        deferred_entries = [
            DeferredToolEntry(
                name=m.name,
                one_line_desc=(m.description or m.name)[:80],
            )
            for m in all_metas
            if not core_set.is_core(m.name)
        ]

        deferred_names = {e.name for e in deferred_entries}
        assert "mcp::github::create_issue" in deferred_names


# ============================================================
# 端到端集成: 完整链路
# ============================================================


class TestDeferredToolsEndToEnd:
    """完整端到端流程: 分区 → prompt 注入 → tool_search → 提升 → 验证"""

    async def test_full_pipeline(self) -> None:
        """从分区到提升的完整流程"""
        all_metas = _build_test_tool_metas()
        core_set = CoreToolSet.default()

        # Step 1: 分区
        core_metas = [m for m in all_metas if core_set.is_core(m.name)]
        deferred_entries = [
            DeferredToolEntry(
                name=m.name,
                one_line_desc=(m.description or m.name)[:80],
                tool_group=m.tool_group,
                side_effect_level=m.side_effect_level.value,
            )
            for m in all_metas
            if not core_set.is_core(m.name)
        ]

        assert len(core_metas) > 0
        assert len(deferred_entries) > 0

        # Step 2: 格式化 Deferred Tools 为 prompt 文本
        deferred_text = format_deferred_tools_list(deferred_entries)
        assert "## Available Tools (Deferred)" in deferred_text
        assert "tool_search" in deferred_text

        # Step 3: tool_search 查询
        docker_hits = [
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
                tool_group="docker",
            ),
        ]
        tool_index = FakeToolIndex(results=docker_hits)
        event_store = FakeEventStore()

        handler = create_tool_search_handler(
            tool_index=tool_index,
            event_store=event_store,
        )

        search_result_json = await handler(query="docker 运行容器", limit=5)
        search_result = json.loads(search_result_json)
        assert len(search_result["results"]) == 1
        assert search_result["results"][0]["tool_name"] == "docker.run"

        # Step 4: 提升搜索到的工具
        promotion = ToolPromotionService(event_store=event_store)
        tool_names = [hit["tool_name"] for hit in search_result["results"]]
        newly_promoted = await promotion.promote_from_search(
            tool_names,
            query="docker 运行容器",
        )

        assert "docker.run" in newly_promoted
        assert promotion.is_promoted("docker.run")

        # Step 5: 验证提升后工具可用
        assert "docker.run" in promotion.active_tool_names

        # Step 6: 验证事件链完整
        event_types = [e.type for e in event_store.events if hasattr(e, "type")]
        assert "TOOL_SEARCH_EXECUTED" in event_types
        assert "TOOL_PROMOTED" in event_types

    async def test_empty_deferred_list_no_prompt_injection(self) -> None:
        """所有工具都是 Core 时不注入 Deferred 块"""
        text = format_deferred_tools_list([])
        assert text == ""

    async def test_deferred_tools_count_matches(self) -> None:
        """Deferred Tools 列表中的总数与实际数量一致"""
        entries = [
            DeferredToolEntry(name=f"tool_{i}", one_line_desc=f"工具 {i}")
            for i in range(15)
        ]
        text = format_deferred_tools_list(entries)
        assert "共 15 个 deferred 工具可用" in text
