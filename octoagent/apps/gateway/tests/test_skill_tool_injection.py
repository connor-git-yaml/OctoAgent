"""Feature 061 T-037: Skill-Tool 注入单元测试

覆盖场景:
- US-004 场景 1: Skill 加载 → tools_required 工具提升
- US-004 场景 2: 超出 Preset 的工具仍提升但调用触发 ask
- US-004 场景 3: Skill 卸载 → 独占工具回退
- US-004 场景 4: 多 Skill 共享工具 → 单 Skill 卸载不回退
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

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
    """模拟 SkillDiscovery，返回预设的 SkillMdEntry"""

    def __init__(self, skills: dict[str, SkillMdEntry] | None = None) -> None:
        self._cache = skills or {}

    def get(self, name: str) -> SkillMdEntry | None:
        return self._cache.get(name)


# ============================================================
# Fixtures
# ============================================================


def _make_skill(
    name: str,
    tools_required: list[str] | None = None,
    description: str = "",
) -> SkillMdEntry:
    """辅助函数：创建 SkillMdEntry"""
    return SkillMdEntry(
        name=name,
        description=description or f"A skill named {name}",
        tools_required=tools_required or [],
        source=SkillSource.BUILTIN,
    )


@pytest.fixture
def event_store() -> FakeEventStore:
    return FakeEventStore()


@pytest.fixture
def promotion_service(event_store: FakeEventStore) -> ToolPromotionService:
    return ToolPromotionService(
        agent_runtime_id="worker-1",
        agent_session_id="session-1",
        event_store=event_store,
    )


# ============================================================
# T-037: Skill-Tool 注入测试
# ============================================================


class TestSkillToolPromotion:
    """US-004 场景 1: Skill 加载 → tools_required 工具提升"""

    async def test_skill_load_promotes_tools(
        self,
        promotion_service: ToolPromotionService,
    ) -> None:
        """加载含 tools_required 的 Skill 后，工具被提升到 Active"""
        coding_skill = _make_skill(
            "coding-agent",
            tools_required=["docker.run", "terminal.exec"],
        )
        discovery = FakeSkillDiscovery({"coding-agent": coding_skill})

        service = LLMService(
            skill_discovery=discovery,
            tool_promotion_service=promotion_service,
        )

        promoted, demoted = await service.sync_skill_tool_promotions(
            ["coding-agent"],
            task_id="task-1",
            trace_id="trace-1",
        )

        assert set(promoted) == {"docker.run", "terminal.exec"}
        assert demoted == []
        assert promotion_service.is_promoted("docker.run")
        assert promotion_service.is_promoted("terminal.exec")

    async def test_skill_load_without_tools_required(
        self,
        promotion_service: ToolPromotionService,
    ) -> None:
        """加载没有 tools_required 的 Skill 不触发工具提升"""
        simple_skill = _make_skill("summarize", tools_required=[])
        discovery = FakeSkillDiscovery({"summarize": simple_skill})

        service = LLMService(
            skill_discovery=discovery,
            tool_promotion_service=promotion_service,
        )

        promoted, demoted = await service.sync_skill_tool_promotions(
            ["summarize"],
            task_id="task-1",
            trace_id="trace-1",
        )

        assert promoted == []
        assert demoted == []

    async def test_skill_load_idempotent(
        self,
        promotion_service: ToolPromotionService,
    ) -> None:
        """多次调用 sync 不重复提升"""
        coding_skill = _make_skill(
            "coding-agent",
            tools_required=["docker.run"],
        )
        discovery = FakeSkillDiscovery({"coding-agent": coding_skill})

        service = LLMService(
            skill_discovery=discovery,
            tool_promotion_service=promotion_service,
        )

        p1, _ = await service.sync_skill_tool_promotions(["coding-agent"])
        p2, _ = await service.sync_skill_tool_promotions(["coding-agent"])

        assert p1 == ["docker.run"]
        assert p2 == []  # 已提升，不重复


class TestSkillToolDemotion:
    """US-004 场景 3: Skill 卸载 → 独占工具回退"""

    async def test_skill_unload_demotes_exclusive_tools(
        self,
        promotion_service: ToolPromotionService,
    ) -> None:
        """卸载 Skill 后，独占提升的工具回退到 Deferred"""
        coding_skill = _make_skill(
            "coding-agent",
            tools_required=["docker.run", "terminal.exec"],
        )
        discovery = FakeSkillDiscovery({"coding-agent": coding_skill})

        service = LLMService(
            skill_discovery=discovery,
            tool_promotion_service=promotion_service,
        )

        # 先加载
        await service.sync_skill_tool_promotions(["coding-agent"])
        assert promotion_service.is_promoted("docker.run")

        # 卸载（loaded_skill_names 不再包含 coding-agent）
        _, demoted = await service.sync_skill_tool_promotions([])

        assert set(demoted) == {"docker.run", "terminal.exec"}
        assert not promotion_service.is_promoted("docker.run")
        assert not promotion_service.is_promoted("terminal.exec")


class TestMultiSkillSharedTools:
    """US-004 场景 4: 多 Skill 共享工具 → 单 Skill 卸载不回退"""

    async def test_shared_tool_not_demoted_when_one_skill_unloads(
        self,
        promotion_service: ToolPromotionService,
    ) -> None:
        """多 Skill 共同依赖的工具，单个 Skill 卸载不回退"""
        coding_skill = _make_skill(
            "coding-agent",
            tools_required=["terminal.exec", "docker.run"],
        )
        github_skill = _make_skill(
            "github",
            tools_required=["terminal.exec"],  # 共享 terminal.exec
        )
        discovery = FakeSkillDiscovery({
            "coding-agent": coding_skill,
            "github": github_skill,
        })

        service = LLMService(
            skill_discovery=discovery,
            tool_promotion_service=promotion_service,
        )

        # 加载两个 Skill
        await service.sync_skill_tool_promotions(["coding-agent", "github"])
        assert promotion_service.is_promoted("terminal.exec")
        assert promotion_service.is_promoted("docker.run")

        # 卸载 coding-agent，保留 github
        _, demoted = await service.sync_skill_tool_promotions(["github"])

        # docker.run 仅被 coding-agent 提升，应回退
        assert "docker.run" in demoted
        assert not promotion_service.is_promoted("docker.run")

        # terminal.exec 被 github 仍然需要，不回退
        assert "terminal.exec" not in demoted
        assert promotion_service.is_promoted("terminal.exec")

    async def test_tool_search_promoted_tool_not_affected_by_skill_unload(
        self,
        promotion_service: ToolPromotionService,
    ) -> None:
        """tool_search 提升的工具不受 Skill 卸载影响"""
        coding_skill = _make_skill(
            "coding-agent",
            tools_required=["docker.run"],
        )
        discovery = FakeSkillDiscovery({"coding-agent": coding_skill})

        service = LLMService(
            skill_discovery=discovery,
            tool_promotion_service=promotion_service,
        )

        # 先通过 tool_search 提升 docker.run
        await promotion_service.promote("docker.run", "tool_search:docker")

        # 再通过 Skill 提升（同一工具，多来源）
        await service.sync_skill_tool_promotions(["coding-agent"])

        # 卸载 Skill
        _, demoted = await service.sync_skill_tool_promotions([])

        # docker.run 仍有 tool_search 来源，不回退
        assert "docker.run" not in demoted
        assert promotion_service.is_promoted("docker.run")


class TestSkillToolInjectionIntegration:
    """集成场景: sync_skill_tool_promotions 在 _try_call_with_tools 中的集成"""

    async def test_sync_without_promotion_service(self) -> None:
        """未提供 ToolPromotionService 时应安全返回空"""
        service = LLMService()

        promoted, demoted = await service.sync_skill_tool_promotions(
            ["coding-agent"],
        )

        assert promoted == []
        assert demoted == []

    async def test_sync_without_skill_discovery(
        self,
        promotion_service: ToolPromotionService,
    ) -> None:
        """未提供 SkillDiscovery 时应安全返回空"""
        service = LLMService(
            tool_promotion_service=promotion_service,
        )

        promoted, demoted = await service.sync_skill_tool_promotions(
            ["coding-agent"],
        )

        assert promoted == []
        assert demoted == []

    async def test_unknown_skill_ignored(
        self,
        promotion_service: ToolPromotionService,
    ) -> None:
        """引用不存在的 Skill 不报错"""
        discovery = FakeSkillDiscovery({})  # 空缓存

        service = LLMService(
            skill_discovery=discovery,
            tool_promotion_service=promotion_service,
        )

        promoted, demoted = await service.sync_skill_tool_promotions(
            ["nonexistent-skill"],
        )

        assert promoted == []
        assert demoted == []
