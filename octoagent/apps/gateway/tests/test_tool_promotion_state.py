"""Feature 061 T-025a: ToolPromotionState + ToolPromotionService 单元测试

覆盖场景:
- 单来源 promote → demote → 回退
- 多来源 promote → 部分 demote → 不回退 → 全部 demote → 回退
- 重复 promote 同一来源 → 不重复计数
- 事件记录正确
- promote_from_search / promote_from_skill / demote_from_skill 批量操作
"""

from __future__ import annotations

from typing import Any

import pytest

from octoagent.gateway.services.tool_promotion import ToolPromotionService
from octoagent.tooling.models import ToolPromotionState


# ============================================================
# Fake 依赖
# ============================================================


class FakeEventStore:
    """模拟 EventStore"""

    def __init__(self) -> None:
        self.events: list[Any] = []

    async def append_event(self, event: Any) -> None:
        self.events.append(event)

    async def get_next_task_seq(self, task_id: str) -> int:
        return len(self.events) + 1


# ============================================================
# ToolPromotionState 数据模型测试
# ============================================================


class TestToolPromotionState:
    """测试 ToolPromotionState 数据模型的引用计数逻辑"""

    def test_single_source_promote_demote(self) -> None:
        """单来源 promote → demote → 回退"""
        state = ToolPromotionState()

        # 首次 promote 返回 True
        assert state.promote("docker.run", "tool_search:q1") is True
        assert state.is_promoted("docker.run") is True
        assert "docker.run" in state.active_tool_names

        # demote 后回退
        assert state.demote("docker.run", "tool_search:q1") is True
        assert state.is_promoted("docker.run") is False
        assert "docker.run" not in state.active_tool_names

    def test_multi_source_promote_partial_demote(self) -> None:
        """多来源 promote → 部分 demote → 不回退 → 全部 demote → 回退"""
        state = ToolPromotionState()

        # 两个来源 promote
        assert state.promote("docker.run", "tool_search:q1") is True
        assert state.promote("docker.run", "skill:coding-agent") is False  # 非首次

        # 部分 demote → 不回退
        assert state.demote("docker.run", "tool_search:q1") is False
        assert state.is_promoted("docker.run") is True  # 还有 skill 来源

        # 全部 demote → 回退
        assert state.demote("docker.run", "skill:coding-agent") is True
        assert state.is_promoted("docker.run") is False

    def test_duplicate_promote_same_source(self) -> None:
        """重复 promote 同一来源 → 不重复计数"""
        state = ToolPromotionState()

        assert state.promote("docker.run", "tool_search:q1") is True
        assert state.promote("docker.run", "tool_search:q1") is False  # 重复
        assert state.promote("docker.run", "tool_search:q1") is False  # 再次重复

        # 只需 demote 一次
        assert state.demote("docker.run", "tool_search:q1") is True

    def test_demote_nonexistent(self) -> None:
        """demote 不存在的工具返回 True（安全操作）"""
        state = ToolPromotionState()

        assert state.demote("nonexistent.tool", "unknown") is True

    def test_active_tool_names(self) -> None:
        """active_tool_names 返回正确列表"""
        state = ToolPromotionState()

        state.promote("tool_a", "source_1")
        state.promote("tool_b", "source_2")
        state.promote("tool_c", "source_3")

        names = state.active_tool_names
        assert set(names) == {"tool_a", "tool_b", "tool_c"}


# ============================================================
# ToolPromotionService 测试
# ============================================================


class TestToolPromotionServiceBasic:
    """测试 ToolPromotionService 基本操作"""

    async def test_promote_generates_event(self) -> None:
        """promote 新工具时生成 TOOL_PROMOTED 事件"""
        event_store = FakeEventStore()
        service = ToolPromotionService(
            agent_runtime_id="worker-1",
            agent_session_id="session-1",
            event_store=event_store,
        )

        result = await service.promote("docker.run", "tool_search:docker")
        assert result is True
        assert len(event_store.events) == 1

        event = event_store.events[0]
        assert event.type == "TOOL_PROMOTED"
        assert event.payload["tool_name"] == "docker.run"
        assert event.payload["direction"] == "promoted"
        assert event.payload["source"] == "tool_search"
        assert event.payload["source_id"] == "docker"

    async def test_duplicate_promote_no_event(self) -> None:
        """重复 promote 同一来源不生成事件"""
        event_store = FakeEventStore()
        service = ToolPromotionService(event_store=event_store)

        await service.promote("docker.run", "tool_search:q1")
        assert len(event_store.events) == 1

        # 再次 promote 同一来源 → 不是首次提升 → 不产生事件
        await service.promote("docker.run", "tool_search:q1")
        assert len(event_store.events) == 1  # 事件数不变

    async def test_demote_generates_event(self) -> None:
        """demote 后回退时生成 TOOL_DEMOTED 事件"""
        event_store = FakeEventStore()
        service = ToolPromotionService(event_store=event_store)

        await service.promote("docker.run", "tool_search:q1")
        result = await service.demote("docker.run", "tool_search:q1")

        assert result is True
        assert len(event_store.events) == 2
        demote_event = event_store.events[1]
        assert demote_event.type == "TOOL_DEMOTED"
        assert demote_event.payload["tool_name"] == "docker.run"

    async def test_partial_demote_no_event(self) -> None:
        """部分 demote（还有其他来源）不生成 demote 事件"""
        event_store = FakeEventStore()
        service = ToolPromotionService(event_store=event_store)

        await service.promote("docker.run", "tool_search:q1")
        await service.promote("docker.run", "skill:coding")
        assert len(event_store.events) == 1  # 只有首次 promote

        result = await service.demote("docker.run", "tool_search:q1")
        assert result is False  # 还有 skill 来源
        assert len(event_store.events) == 1  # 无新事件

    async def test_no_event_without_event_store(self) -> None:
        """未提供 event_store 时不报错"""
        service = ToolPromotionService()

        result = await service.promote("docker.run", "source")
        assert result is True
        assert service.is_promoted("docker.run")


class TestToolPromotionServiceBatchOps:
    """测试 ToolPromotionService 批量操作"""

    async def test_promote_from_search(self) -> None:
        """promote_from_search 批量提升 tool_search 结果"""
        event_store = FakeEventStore()
        service = ToolPromotionService(event_store=event_store)

        newly = await service.promote_from_search(
            ["docker.run", "docker.stop", "docker.logs"],
            query="docker container",
        )

        assert set(newly) == {"docker.run", "docker.stop", "docker.logs"}
        assert len(event_store.events) == 3

    async def test_promote_from_skill(self) -> None:
        """promote_from_skill 批量提升 Skill 依赖工具"""
        event_store = FakeEventStore()
        service = ToolPromotionService(event_store=event_store)

        newly = await service.promote_from_skill(
            ["filesystem.write_text", "terminal.exec"],
            skill_name="coding-agent",
        )

        assert set(newly) == {"filesystem.write_text", "terminal.exec"}
        assert len(event_store.events) == 2

    async def test_demote_from_skill_only_exclusive(self) -> None:
        """demote_from_skill 仅回退该 Skill 独占的工具"""
        event_store = FakeEventStore()
        service = ToolPromotionService(event_store=event_store)

        # docker.run 被 tool_search 和 skill 同时提升
        await service.promote("docker.run", "tool_search:q1")
        await service.promote("docker.run", "skill:coding-agent")
        # terminal.exec 仅被 skill 提升
        await service.promote("terminal.exec", "skill:coding-agent")

        demoted = await service.demote_from_skill(
            ["docker.run", "terminal.exec"],
            skill_name="coding-agent",
        )

        # docker.run 还有 tool_search 来源，不回退
        assert "docker.run" not in demoted
        assert service.is_promoted("docker.run")

        # terminal.exec 仅有 skill 来源，回退
        assert "terminal.exec" in demoted
        assert not service.is_promoted("terminal.exec")

    async def test_promote_from_search_idempotent(self) -> None:
        """重复搜索相同查询不重复提升"""
        service = ToolPromotionService()

        newly1 = await service.promote_from_search(
            ["docker.run"],
            query="docker",
        )
        newly2 = await service.promote_from_search(
            ["docker.run"],
            query="docker",
        )

        assert newly1 == ["docker.run"]
        assert newly2 == []  # 已经提升过了
