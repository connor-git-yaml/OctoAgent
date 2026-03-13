"""Feature 049: Butler persona / clarification behavior 测试。"""

from __future__ import annotations

from octoagent.core.models import AgentProfile, AgentProfileScope, BehaviorLayerKind
from octoagent.gateway.services.butler_behavior import (
    build_behavior_slice_envelope,
    build_behavior_system_summary,
    decide_clarification,
    render_behavior_system_block,
    resolve_behavior_pack,
)


def _build_profile() -> AgentProfile:
    return AgentProfile(
        profile_id="agent-profile-default",
        scope=AgentProfileScope.PROJECT,
        project_id="project-default",
        name="Default Butler",
        persona_summary="负责长期协作。",
    )


def _build_worker_profile() -> AgentProfile:
    return AgentProfile(
        profile_id="singleton:research",
        scope=AgentProfileScope.SYSTEM,
        name="Research Worker",
        persona_summary="负责外部调研。",
        metadata={
            "source_kind": "worker_profile_mirror",
            "source_worker_profile_id": "singleton:research",
        },
    )


def test_resolve_behavior_pack_builds_default_files_layers_and_worker_slice() -> None:
    profile = _build_profile()

    pack = resolve_behavior_pack(
        agent_profile=profile,
        project_name="Default Project",
    )
    slice_envelope = build_behavior_slice_envelope(pack)

    assert pack.profile_id == profile.profile_id
    assert len(pack.files) == 7
    assert pack.source_chain == ["default_behavior_templates", "project:Default Project"]
    assert [item.layer for item in pack.layers][:3] == [
        BehaviorLayerKind.ROLE,
        BehaviorLayerKind.COMMUNICATION,
        BehaviorLayerKind.SOLVING,
    ]
    assert slice_envelope.shared_file_ids == ["AGENTS.md", "PROJECT.md", "TOOLS.md"]
    assert [item.layer for item in slice_envelope.layers] == [
        BehaviorLayerKind.ROLE,
        BehaviorLayerKind.SOLVING,
        BehaviorLayerKind.TOOL_BOUNDARY,
    ]


def test_behavior_summary_and_block_expose_effective_sources() -> None:
    profile = _build_profile()

    summary = build_behavior_system_summary(
        agent_profile=profile,
        project_name="Default Project",
    )
    block = render_behavior_system_block(
        agent_profile=profile,
        project_name="Default Project",
    )

    assert summary["source_chain"] == ["default_behavior_templates", "project:Default Project"]
    assert summary["worker_slice"]["shared_file_ids"] == ["AGENTS.md", "PROJECT.md", "TOOLS.md"]
    assert "BehaviorSystem:" in block
    assert "tool_boundary:" in block
    assert "default_behavior_templates" in block


def test_worker_behavior_block_uses_worker_identity_and_shared_slice_only() -> None:
    profile = _build_worker_profile()

    pack = resolve_behavior_pack(agent_profile=profile, project_name="Default Project")
    block = render_behavior_system_block(
        agent_profile=profile,
        project_name="Default Project",
        shared_only=True,
    )

    assert pack.files[0].title == "Worker 总约束"
    assert "Root Agent" in pack.files[0].content
    assert "Butler 的总控职责" in pack.files[0].content
    assert "communication:" not in block
    assert "memory_policy:" not in block
    assert "bootstrap:" not in block
    assert "tool_boundary:" in block


def test_decide_clarification_identifies_work_priority_missing_context() -> None:
    decision = decide_clarification(
        "帮我把今天下午的工作拆成 3 个优先级，并给我一个先做什么后做什么的顺序。"
    )

    assert decision.category == "work_priority_context"
    assert decision.action.value == "clarify"
    assert decision.missing_inputs == ["今天下午的待办列表或日程"]
    assert "真实待办 / 日程列表" in decision.followup_prompt


def test_decide_clarification_identifies_weather_and_recommendation_context() -> None:
    weather = decide_clarification("今天天气怎么样？")
    recommend = decide_clarification("帮我推荐一家餐厅")

    assert weather.category == "weather_location"
    assert weather.action.value == "delegate_after_clarification"
    assert "城市 / 区县" in weather.followup_prompt

    assert recommend.category == "recommendation_context"
    assert recommend.action.value == "clarify"
    assert "地点 / 预算 / 使用场景" in recommend.followup_prompt


def test_decide_clarification_skips_technical_recommendation_requests() -> None:
    decision = decide_clarification("帮我推荐一个 Python 日志库")

    assert decision.action.value == "direct"
    assert decision.category == ""


def test_decide_clarification_skips_technical_comparison_requests() -> None:
    decision = decide_clarification("帮我比较一下这个 PR 和 master 的实现差异")

    assert decision.action.value == "direct"
    assert decision.category == ""
