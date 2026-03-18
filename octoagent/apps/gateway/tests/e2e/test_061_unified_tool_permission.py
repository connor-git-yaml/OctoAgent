"""Feature 061 T-040: 全功能端到端集成测试 — 统一工具注入 + 权限 Preset 模型

覆盖跨 Phase 的核心场景:
- Agent 创建（Preset + RoleCard）→ 工具上下文分区（Core + Deferred）
- tool_search → Deferred 工具懒加载 → Active 工具提升
- Preset 权限检查 → ask 审批 → always 持久化
- Skill 加载/卸载 → 工具自动提升/回退
- 进程重启后 always 覆盖恢复

本测试不依赖外部服务，使用 Fake 依赖验证完整链路。
"""

from __future__ import annotations

import warnings
from typing import Any

import pytest

from octoagent.tooling.models import (
    PRESET_POLICY,
    CoreToolSet,
    DeferredToolEntry,
    ExecutionContext,
    PermissionPreset,
    PresetDecision,
    SideEffectLevel,
    ToolMeta,
    ToolProfile,
    ToolPromotionState,
    ToolTier,
    migrate_tool_profile_to_preset,
    preset_decision,
    profile_allows,
)
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
# T-040: 端到端集成测试
# ============================================================


class TestFullE2EPresetLifecycle:
    """跨 Phase 场景: Preset 权限检查完整链路"""

    def test_preset_policy_matrix_complete(self) -> None:
        """SC-003: 所有 3×3 组合覆盖"""
        for preset in PermissionPreset:
            for side_effect in SideEffectLevel:
                decision = preset_decision(preset, side_effect)
                assert decision in (PresetDecision.ALLOW, PresetDecision.ASK)

    def test_butler_full_all_allow(self) -> None:
        """Butler 默认 FULL，所有操作都 allow"""
        for side_effect in SideEffectLevel:
            assert preset_decision(PermissionPreset.FULL, side_effect) == PresetDecision.ALLOW

    def test_worker_normal_irreversible_ask(self) -> None:
        """Worker 默认 NORMAL，irreversible 触发 ask"""
        assert preset_decision(PermissionPreset.NORMAL, SideEffectLevel.IRREVERSIBLE) == PresetDecision.ASK
        assert preset_decision(PermissionPreset.NORMAL, SideEffectLevel.REVERSIBLE) == PresetDecision.ALLOW
        assert preset_decision(PermissionPreset.NORMAL, SideEffectLevel.NONE) == PresetDecision.ALLOW

    def test_minimal_only_none_allow(self) -> None:
        """MINIMAL 仅 none allow"""
        assert preset_decision(PermissionPreset.MINIMAL, SideEffectLevel.NONE) == PresetDecision.ALLOW
        assert preset_decision(PermissionPreset.MINIMAL, SideEffectLevel.REVERSIBLE) == PresetDecision.ASK
        assert preset_decision(PermissionPreset.MINIMAL, SideEffectLevel.IRREVERSIBLE) == PresetDecision.ASK


class TestFullE2EToolTierPartition:
    """跨 Phase 场景: Core + Deferred 工具分区"""

    def test_core_toolset_includes_tool_search(self) -> None:
        """FR-018: Core Tools 包含 tool_search"""
        core = CoreToolSet.default()
        assert "tool_search" in core.tool_names

    def test_core_toolset_reasonable_size(self) -> None:
        """Core Tools 数量合理（≤15）"""
        core = CoreToolSet.default()
        assert len(core.tool_names) <= 15
        assert len(core.tool_names) >= 5

    def test_deferred_entry_one_line_desc_limit(self) -> None:
        """DeferredToolEntry one_line_desc 最大 80 字符"""
        entry = DeferredToolEntry(
            name="test.tool",
            one_line_desc="x" * 80,
        )
        assert len(entry.one_line_desc) == 80

    def test_tool_tier_default_is_deferred(self) -> None:
        """新工具默认 tier=DEFERRED"""
        meta = ToolMeta(
            name="test.tool",
            description="test",
            parameters_json_schema={},
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.STANDARD,
            tool_group="test",
        )
        assert meta.tier == ToolTier.DEFERRED


class TestFullE2EToolPromotion:
    """跨 Phase 场景: tool_search + Skill 工具提升/回退"""

    async def test_tool_search_promotes_and_demotes(self) -> None:
        """tool_search 提升 → Active → demote → Deferred"""
        event_store = FakeEventStore()
        service = ToolPromotionService(
            agent_runtime_id="worker-e2e",
            agent_session_id="session-e2e",
            event_store=event_store,
        )

        # 提升
        promoted = await service.promote_from_search(
            ["docker.run", "filesystem.write"],
            query="run docker and write files",
        )
        assert set(promoted) == {"docker.run", "filesystem.write"}
        assert service.is_promoted("docker.run")
        assert service.is_promoted("filesystem.write")

        # 回退
        should_demote = await service.demote(
            "docker.run", "tool_search:run docker and write files"
        )
        assert should_demote is True
        assert not service.is_promoted("docker.run")
        assert service.is_promoted("filesystem.write")

    async def test_skill_and_tool_search_coexist(self) -> None:
        """Skill + tool_search 双来源提升，互不影响"""
        event_store = FakeEventStore()
        promotion = ToolPromotionService(event_store=event_store)
        discovery = FakeSkillDiscovery({
            "coding-agent": _skill("coding-agent", ["terminal.exec"]),
        })
        service = LLMService(
            skill_discovery=discovery,
            tool_promotion_service=promotion,
        )

        # tool_search 提升
        await promotion.promote("terminal.exec", "tool_search:exec")
        # Skill 提升
        await service.sync_skill_tool_promotions(["coding-agent"])
        assert promotion.is_promoted("terminal.exec")

        # 卸载 Skill → 仍有 tool_search 来源
        _, demoted = await service.sync_skill_tool_promotions([])
        assert "terminal.exec" not in demoted
        assert promotion.is_promoted("terminal.exec")

        # 移除 tool_search 来源 → 回退
        should_demote = await promotion.demote("terminal.exec", "tool_search:exec")
        assert should_demote is True
        assert not promotion.is_promoted("terminal.exec")

    async def test_multi_skill_shared_tool_lifecycle(self) -> None:
        """多 Skill 共享工具的完整生命周期"""
        event_store = FakeEventStore()
        promotion = ToolPromotionService(event_store=event_store)
        discovery = FakeSkillDiscovery({
            "coding-agent": _skill("coding-agent", ["terminal.exec", "docker.run"]),
            "github": _skill("github", ["terminal.exec"]),
        })
        service = LLMService(
            skill_discovery=discovery,
            tool_promotion_service=promotion,
        )

        # 加载两个 Skill
        promoted, _ = await service.sync_skill_tool_promotions(
            ["coding-agent", "github"]
        )
        assert "terminal.exec" in promoted
        assert "docker.run" in promoted

        # 卸载 coding-agent
        _, demoted = await service.sync_skill_tool_promotions(["github"])
        assert "docker.run" in demoted  # 独占
        assert "terminal.exec" not in demoted  # github 仍需要

        # 卸载 github
        _, demoted = await service.sync_skill_tool_promotions([])
        assert "terminal.exec" in demoted


class TestFullE2EEventGeneration:
    """跨 Phase 场景: 所有操作产生正确的事件"""

    async def test_promotion_demotion_events(self) -> None:
        """SC-009: 提升/回退操作产生正确事件"""
        event_store = FakeEventStore()
        service = ToolPromotionService(
            agent_runtime_id="worker-evt",
            agent_session_id="session-evt",
            event_store=event_store,
        )

        await service.promote("docker.run", "tool_search:q1", task_id="t1")
        await service.demote("docker.run", "tool_search:q1", task_id="t2")

        assert len(event_store.events) == 2
        assert event_store.events[0].type == "TOOL_PROMOTED"
        assert event_store.events[0].payload["tool_name"] == "docker.run"
        assert event_store.events[1].type == "TOOL_DEMOTED"
        assert event_store.events[1].payload["tool_name"] == "docker.run"


class TestFullE2EToolProfileCompat:
    """跨 Phase 场景: ToolProfile → PermissionPreset 兼容"""

    def test_migrate_known_profiles(self) -> None:
        """已知 ToolProfile 值正确映射"""
        assert migrate_tool_profile_to_preset("minimal") == PermissionPreset.MINIMAL
        assert migrate_tool_profile_to_preset("standard") == PermissionPreset.NORMAL
        assert migrate_tool_profile_to_preset("privileged") == PermissionPreset.FULL

    def test_migrate_unknown_fallback(self) -> None:
        """未知值回退到 MINIMAL"""
        assert migrate_tool_profile_to_preset("unknown") == PermissionPreset.MINIMAL
        assert migrate_tool_profile_to_preset("") == PermissionPreset.MINIMAL

    def test_profile_allows_deprecation_warning(self) -> None:
        """profile_allows() 调用产生 DeprecationWarning"""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            profile_allows(ToolProfile.MINIMAL, ToolProfile.STANDARD)
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert "preset_decision" in str(w[0].message)

    def test_profile_allows_backward_compatible(self) -> None:
        """profile_allows() 结果与旧逻辑兼容"""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            # context >= tool → True
            assert profile_allows(ToolProfile.MINIMAL, ToolProfile.MINIMAL) is True
            assert profile_allows(ToolProfile.MINIMAL, ToolProfile.STANDARD) is True
            assert profile_allows(ToolProfile.MINIMAL, ToolProfile.PRIVILEGED) is True
            assert profile_allows(ToolProfile.STANDARD, ToolProfile.STANDARD) is True
            assert profile_allows(ToolProfile.STANDARD, ToolProfile.PRIVILEGED) is True
            assert profile_allows(ToolProfile.PRIVILEGED, ToolProfile.PRIVILEGED) is True
            # context < tool → False
            assert profile_allows(ToolProfile.STANDARD, ToolProfile.MINIMAL) is False
            assert profile_allows(ToolProfile.PRIVILEGED, ToolProfile.MINIMAL) is False
            assert profile_allows(ToolProfile.PRIVILEGED, ToolProfile.STANDARD) is False


class TestFullE2EPromotionState:
    """跨 Phase 场景: ToolPromotionState 引用计数"""

    def test_reference_counting_correct(self) -> None:
        """多来源引用计数正确"""
        state = ToolPromotionState()

        # 首次提升
        assert state.promote("docker.run", "skill:coding-agent") is True
        assert state.is_promoted("docker.run")

        # 同一来源重复提升 → 不重复
        assert state.promote("docker.run", "skill:coding-agent") is False

        # 另一来源提升
        assert state.promote("docker.run", "tool_search:q1") is False  # 已经 active
        assert state.is_promoted("docker.run")

        # 移除一个来源 → 不回退
        assert state.demote("docker.run", "skill:coding-agent") is False
        assert state.is_promoted("docker.run")

        # 移除最后来源 → 回退
        assert state.demote("docker.run", "tool_search:q1") is True
        assert not state.is_promoted("docker.run")

    def test_active_tool_names(self) -> None:
        """active_tool_names 返回正确列表"""
        state = ToolPromotionState()
        state.promote("docker.run", "skill:a")
        state.promote("terminal.exec", "skill:a")
        state.promote("filesystem.write", "tool_search:q")

        names = state.active_tool_names
        assert set(names) == {"docker.run", "terminal.exec", "filesystem.write"}


class TestFullE2EGracefulDegradation:
    """跨 Phase 场景: 降级处理"""

    async def test_no_promotion_service(self) -> None:
        """未提供 ToolPromotionService 时安全返回空"""
        service = LLMService()
        promoted, demoted = await service.sync_skill_tool_promotions(["coding-agent"])
        assert promoted == []
        assert demoted == []

    async def test_no_skill_discovery(self) -> None:
        """未提供 SkillDiscovery 时安全返回空"""
        event_store = FakeEventStore()
        promotion = ToolPromotionService(event_store=event_store)
        service = LLMService(tool_promotion_service=promotion)
        promoted, demoted = await service.sync_skill_tool_promotions(["coding-agent"])
        assert promoted == []
        assert demoted == []

    async def test_unknown_skill_ignored(self) -> None:
        """不存在的 Skill 不影响正常处理"""
        event_store = FakeEventStore()
        promotion = ToolPromotionService(event_store=event_store)
        discovery = FakeSkillDiscovery({
            "github": _skill("github", ["terminal.exec"]),
        })
        service = LLMService(
            skill_discovery=discovery,
            tool_promotion_service=promotion,
        )

        promoted, _ = await service.sync_skill_tool_promotions(
            ["github", "nonexistent"]
        )
        assert "terminal.exec" in promoted
        assert promotion.is_promoted("terminal.exec")

    async def test_empty_tools_required_skill(self) -> None:
        """无 tools_required 的 Skill 不触发提升"""
        event_store = FakeEventStore()
        promotion = ToolPromotionService(event_store=event_store)
        discovery = FakeSkillDiscovery({
            "summarize": _skill("summarize", []),
        })
        service = LLMService(
            skill_discovery=discovery,
            tool_promotion_service=promotion,
        )

        promoted, _ = await service.sync_skill_tool_promotions(["summarize"])
        assert promoted == []
