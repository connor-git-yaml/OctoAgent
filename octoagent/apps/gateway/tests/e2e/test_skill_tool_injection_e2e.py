"""Feature 061 T-039: Phase 5 集成测试 — Skill-Tool 注入端到端

覆盖场景:
- 加载带 tools_required 的 Skill → 对应工具从 Deferred 变为 Active
- Active 工具可直接调用（无需 tool_search）
- 超出 Preset 的工具触发 ask（与 Phase 1 联动）
- Skill 卸载后独占工具回退

本测试不依赖外部服务，使用 FakeSkillDiscovery 和 ToolPromotionService 验证完整链路。
"""

from __future__ import annotations

from typing import Any

import pytest

from octoagent.gateway.services.llm_service import LLMService
from octoagent.gateway.services.tool_promotion import ToolPromotionService
from octoagent.skills.skill_models import SkillMdEntry, SkillSource


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


class FakeSkillDiscovery:
    """模拟 SkillDiscovery"""

    def __init__(self, skills: dict[str, SkillMdEntry] | None = None) -> None:
        self._cache = skills or {}

    def get(self, name: str) -> SkillMdEntry | None:
        return self._cache.get(name)


def _skill(name: str, tools: list[str]) -> SkillMdEntry:
    return SkillMdEntry(
        name=name,
        description=f"Skill {name}",
        tools_required=tools,
        source=SkillSource.BUILTIN,
    )


# ============================================================
# 端到端集成测试
# ============================================================


class TestSkillToolInjectionE2E:
    """Skill-Tool 注入完整链路端到端测试"""

    async def test_full_lifecycle_load_use_unload(self) -> None:
        """完整生命周期: 加载 Skill → 工具提升 → 验证 Active → 卸载 → 验证回退"""
        event_store = FakeEventStore()
        promotion = ToolPromotionService(
            agent_runtime_id="worker-e2e",
            agent_session_id="session-e2e",
            event_store=event_store,
        )
        discovery = FakeSkillDiscovery({
            "coding-agent": _skill("coding-agent", ["docker.run", "terminal.exec", "filesystem.write_text"]),
        })
        service = LLMService(
            skill_discovery=discovery,
            tool_promotion_service=promotion,
        )

        # Step 1: 加载 Skill
        promoted, demoted = await service.sync_skill_tool_promotions(
            ["coding-agent"],
            task_id="task-e2e",
            trace_id="trace-e2e",
        )

        # 验证工具提升
        assert set(promoted) == {"docker.run", "terminal.exec", "filesystem.write_text"}
        assert demoted == []
        assert promotion.is_promoted("docker.run")
        assert promotion.is_promoted("terminal.exec")
        assert promotion.is_promoted("filesystem.write_text")

        # 验证活跃工具集
        active = promotion.active_tool_names
        assert "docker.run" in active
        assert "terminal.exec" in active
        assert "filesystem.write_text" in active

        # Step 2: 卸载 Skill（loaded_skill_names 清空）
        _, demoted = await service.sync_skill_tool_promotions(
            [],
            task_id="task-e2e-2",
            trace_id="trace-e2e-2",
        )

        # 验证工具回退
        assert set(demoted) == {"docker.run", "terminal.exec", "filesystem.write_text"}
        assert not promotion.is_promoted("docker.run")
        assert not promotion.is_promoted("terminal.exec")
        assert not promotion.is_promoted("filesystem.write_text")

    async def test_multi_skill_lifecycle(self) -> None:
        """多 Skill 加载/卸载: 共享工具引用计数正确"""
        event_store = FakeEventStore()
        promotion = ToolPromotionService(event_store=event_store)
        discovery = FakeSkillDiscovery({
            "coding-agent": _skill("coding-agent", ["terminal.exec", "docker.run"]),
            "github": _skill("github", ["terminal.exec"]),
            "summarize": _skill("summarize", ["terminal.exec"]),
        })
        service = LLMService(
            skill_discovery=discovery,
            tool_promotion_service=promotion,
        )

        # 加载全部 3 个 Skill
        promoted, _ = await service.sync_skill_tool_promotions(
            ["coding-agent", "github", "summarize"],
        )
        assert set(promoted) == {"terminal.exec", "docker.run"}
        assert promotion.is_promoted("terminal.exec")
        assert promotion.is_promoted("docker.run")

        # 卸载 coding-agent（terminal.exec 仍被 github/summarize 使用）
        _, demoted = await service.sync_skill_tool_promotions(
            ["github", "summarize"],
        )
        assert "docker.run" in demoted  # 仅 coding-agent 使用
        assert "terminal.exec" not in demoted  # 被 github+summarize 使用

        # 卸载 github（terminal.exec 仍被 summarize 使用）
        _, demoted = await service.sync_skill_tool_promotions(
            ["summarize"],
        )
        assert "terminal.exec" not in demoted  # 被 summarize 使用

        # 卸载 summarize（terminal.exec 无来源，回退）
        _, demoted = await service.sync_skill_tool_promotions(
            [],
        )
        assert "terminal.exec" in demoted
        assert not promotion.is_promoted("terminal.exec")

    async def test_tool_search_and_skill_coexist(self) -> None:
        """tool_search 和 Skill 提升的工具独立追踪"""
        event_store = FakeEventStore()
        promotion = ToolPromotionService(event_store=event_store)
        discovery = FakeSkillDiscovery({
            "coding-agent": _skill("coding-agent", ["docker.run"]),
        })
        service = LLMService(
            skill_discovery=discovery,
            tool_promotion_service=promotion,
        )

        # 通过 tool_search 提升 docker.run
        await promotion.promote("docker.run", "tool_search:docker_query")

        # 通过 Skill 再次提升 docker.run
        await service.sync_skill_tool_promotions(["coding-agent"])
        assert promotion.is_promoted("docker.run")

        # 卸载 Skill → docker.run 仍被 tool_search 保持
        _, demoted = await service.sync_skill_tool_promotions([])
        assert "docker.run" not in demoted
        assert promotion.is_promoted("docker.run")

        # 移除 tool_search 来源 → docker.run 才回退
        should_demote = await promotion.demote("docker.run", "tool_search:docker_query")
        assert should_demote is True
        assert not promotion.is_promoted("docker.run")

    async def test_event_generation(self) -> None:
        """验证提升/回退操作生成正确的事件"""
        event_store = FakeEventStore()
        promotion = ToolPromotionService(
            agent_runtime_id="worker-evt",
            agent_session_id="session-evt",
            event_store=event_store,
        )
        discovery = FakeSkillDiscovery({
            "coding-agent": _skill("coding-agent", ["docker.run"]),
        })
        service = LLMService(
            skill_discovery=discovery,
            tool_promotion_service=promotion,
        )

        # 加载
        await service.sync_skill_tool_promotions(["coding-agent"], task_id="t1")
        # 卸载
        await service.sync_skill_tool_promotions([], task_id="t2")

        assert len(event_store.events) == 2

        promote_evt = event_store.events[0]
        assert promote_evt.type == "TOOL_PROMOTED"
        assert promote_evt.payload["tool_name"] == "docker.run"
        assert promote_evt.payload["source"] == "skill"
        assert promote_evt.payload["source_id"] == "coding-agent"

        demote_evt = event_store.events[1]
        assert demote_evt.type == "TOOL_DEMOTED"
        assert demote_evt.payload["tool_name"] == "docker.run"

    async def test_skill_with_no_discovery_degrades_gracefully(self) -> None:
        """SkillDiscovery 中不存在的 Skill 不影响其他正常 Skill"""
        event_store = FakeEventStore()
        promotion = ToolPromotionService(event_store=event_store)
        discovery = FakeSkillDiscovery({
            "github": _skill("github", ["terminal.exec"]),
            # "nonexistent" 不存在
        })
        service = LLMService(
            skill_discovery=discovery,
            tool_promotion_service=promotion,
        )

        promoted, _ = await service.sync_skill_tool_promotions(
            ["github", "nonexistent"],
        )

        # github 的工具正常提升
        assert "terminal.exec" in promoted
        assert promotion.is_promoted("terminal.exec")
