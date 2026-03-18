"""Feature 049: Butler persona / clarification behavior 测试。"""

from __future__ import annotations

from pathlib import Path

from octoagent.core.behavior_workspace import normalize_behavior_agent_slug
from octoagent.core.models import (
    AgentProfile,
    AgentProfileScope,
    BehaviorLayerKind,
    ButlerDecisionMode,
    DynamicToolSelection,
    EffectiveToolUniverse,
    ToolAvailabilityExplanation,
    ToolIndexQuery,
    WorkerType,
)
from octoagent.gateway.services.butler_behavior import (
    behavior_pack_cache_size,
    build_behavior_slice_envelope,
    build_behavior_system_summary,
    build_runtime_hint_bundle,
    build_tool_universe_hints,
    contains_explicit_location,
    decide_butler_decision,
    decide_clarification,
    invalidate_behavior_pack_cache,
    render_behavior_system_block,
    render_runtime_hint_block,
    resolve_behavior_pack,
)


def _build_profile() -> AgentProfile:
    return AgentProfile(
        profile_id="agent-profile-default",
        scope=AgentProfileScope.PROJECT,
        project_id="project-default",
        name="Default Butler",
        persona_summary="负责长期协作。",
        bootstrap_template_ids=[
            "behavior:system:AGENTS.md",
            "behavior:system:USER.md",
            "behavior:system:TOOLS.md",
            "behavior:system:BOOTSTRAP.md",
            "behavior:agent:IDENTITY.md",
            "behavior:agent:SOUL.md",
            "behavior:agent:HEARTBEAT.md",
            "behavior:project:PROJECT.md",
            "behavior:project:KNOWLEDGE.md",
            "behavior:project:USER.md",
            "behavior:project:TOOLS.md",
            "behavior:project:instructions/README.md",
        ],
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


def test_resolve_behavior_pack_builds_default_files_layers_and_worker_slice(tmp_path: Path) -> None:
    profile = _build_profile()

    pack = resolve_behavior_pack(
        agent_profile=profile,
        project_name="Default Project",
        project_slug="default-project",
        project_root=tmp_path,
    )
    slice_envelope = build_behavior_slice_envelope(pack)

    assert pack.profile_id == profile.profile_id
    assert len(pack.files) == 6
    assert "default_behavior_templates" in pack.source_chain
    assert [item.layer for item in pack.layers][:3] == [
        BehaviorLayerKind.ROLE,
        BehaviorLayerKind.COMMUNICATION,
        BehaviorLayerKind.SOLVING,
    ]
    # Feature 063: WORKER profile 白名单过滤（AGENTS + TOOLS + IDENTITY + PROJECT + KNOWLEDGE）
    # 与 share_with_workers 取交集，IDENTITY 是 advanced（不在默认 pack），
    # USER 和 BOOTSTRAP 不在 WORKER allowlist
    assert slice_envelope.shared_file_ids == [
        "AGENTS.md",
        "PROJECT.md",
        "KNOWLEDGE.md",
        "TOOLS.md",
    ]
    assert [item.layer for item in slice_envelope.layers] == [
        BehaviorLayerKind.ROLE,
        BehaviorLayerKind.SOLVING,
        BehaviorLayerKind.TOOL_BOUNDARY,
    ]


def test_behavior_summary_and_block_expose_effective_sources() -> None:
    profile = _build_profile()
    workspace_root = Path("/tmp/runtime-workspace")

    summary = build_behavior_system_summary(
        agent_profile=profile,
        project_name="Default Project",
        project_slug="default-project",
        workspace_id="workspace-primary",
        workspace_slug="primary",
        workspace_root_path=workspace_root,
    )
    block = render_behavior_system_block(
        agent_profile=profile,
        project_name="Default Project",
        project_slug="default-project",
        workspace_id="workspace-primary",
        workspace_slug="primary",
        workspace_root_path=workspace_root,
    )

    assert "default_behavior_templates" in summary["source_chain"]
    # Feature 063: WORKER profile 白名单过滤
    assert summary["worker_slice"]["shared_file_ids"] == [
        "AGENTS.md",
        "PROJECT.md",
        "KNOWLEDGE.md",
        "TOOLS.md",
    ]
    assert "direct_answer" in summary["decision_modes"]
    assert "effective_location_hint" in summary["runtime_hint_fields"]
    assert "recent_worker_lane_topic" in summary["runtime_hint_fields"]
    assert summary["files"][0]["path_hint"].endswith("behavior/system/AGENTS.md")
    assert summary["files"][0]["is_advanced"] is False
    assert summary["path_manifest"]["repository_root"]
    assert summary["path_manifest"]["project_root"] == str(workspace_root.resolve())
    assert summary["path_manifest"]["project_root_source"] == "runtime_project_root"
    assert summary["path_manifest"]["project_workspace_root"] == str(workspace_root.resolve())
    assert summary["path_manifest"]["project_workspace_root_source"] == "workspace.root_path"
    assert summary["path_manifest"]["workspace_id"] == "workspace-primary"
    assert summary["path_manifest"]["workspace_slug"] == "primary"
    assert (
        summary["path_manifest"]["secret_bindings_path"]
        .endswith("projects/default-project/project.secret-bindings.json")
    )
    assert summary["storage_boundary_hints"]["facts_store"] == "MemoryService"
    assert "MemoryService / memory tools" in summary["storage_boundary_hints"]["facts_access"]
    assert summary["storage_boundary_hints"]["secret_bindings_metadata_path"].endswith(
        "projects/default-project/project.secret-bindings.json"
    )
    assert "behavior:system:AGENTS.md" in summary["bootstrap_template_ids"]
    assert summary["bootstrap_templates"]["shared"] == [
        "behavior:system:AGENTS.md",
        "behavior:system:USER.md",
        "behavior:system:TOOLS.md",
        "behavior:system:BOOTSTRAP.md",
    ]
    assert summary["bootstrap_templates"]["agent_private"] == [
        "behavior:agent:IDENTITY.md",
        "behavior:agent:SOUL.md",
        "behavior:agent:HEARTBEAT.md",
    ]
    assert summary["bootstrap_routes"]["facts"]["store"] == "MemoryService"
    assert "memory tools" in summary["bootstrap_routes"]["facts"]["access"]
    assert summary["bootstrap_routes"]["secrets"]["metadata_path"].endswith(
        "projects/default-project/project.secret-bindings.json"
    )
    assert summary["bootstrap_routes"]["assistant_identity"]["target"] == "IDENTITY.md"
    assert "project_root" in summary["path_manifest"]
    assert "BehaviorSystem:" in block
    assert "decision_modes:" in block
    assert "tool_boundary:" in block
    assert "default_behavior_templates" in block
    assert "ProjectPathManifest:" in block
    assert "StorageBoundaries:" in block
    assert "project_workspace_root:" in block
    assert "facts_store: MemoryService" in block
    assert "facts_access:" in block
    assert "secrets_access:" in block


def test_normalize_behavior_agent_slug_uses_stable_hash_for_non_ascii_names() -> None:
    research = normalize_behavior_agent_slug("研究员")
    reviewer = normalize_behavior_agent_slug("审稿助手")

    assert research.startswith("agent-")
    assert reviewer.startswith("agent-")
    assert research != reviewer


def test_worker_behavior_block_uses_worker_identity_and_shared_slice_only() -> None:
    profile = _build_worker_profile()

    pack = resolve_behavior_pack(
        agent_profile=profile,
        project_name="Default Project",
        project_slug="default-project",
    )
    from octoagent.core.behavior_workspace import BehaviorLoadProfile

    block = render_behavior_system_block(
        agent_profile=profile,
        project_name="Default Project",
        project_slug="default-project",
        load_profile=BehaviorLoadProfile.WORKER,
    )

    assert pack.files[0].title == "行为总约束"
    assert "specialist Worker" in pack.files[0].content
    assert "Butler 负责默认会话总控" in pack.files[0].content
    # Feature 063: WORKER profile 不含 USER.md（communication 层）和 BOOTSTRAP.md（bootstrap 层）
    assert "communication:" not in block
    assert "bootstrap:" not in block
    assert "tool_boundary:" in block
    assert "role:" in block


def test_resolve_behavior_pack_prefers_project_and_system_workspace_files(tmp_path: Path) -> None:
    profile = _build_profile()
    system_dir = tmp_path / "behavior" / "system"
    project_dir = tmp_path / "projects" / "default-project" / "behavior"
    system_dir.mkdir(parents=True)
    project_dir.mkdir(parents=True)
    (system_dir / "TOOLS.md").write_text("system tools", encoding="utf-8")
    (project_dir / "PROJECT.md").write_text("project context", encoding="utf-8")

    pack = resolve_behavior_pack(
        agent_profile=profile,
        project_name="Default Project",
        project_slug="default-project",
        project_root=tmp_path,
    )

    files = {item.file_id: item for item in pack.files}
    assert pack.source_chain == [
        "filesystem:projects/default-project/behavior",
        "filesystem:behavior/system",
        "default_behavior_templates",
    ]
    assert files["PROJECT.md"].content == "project context"
    assert files["PROJECT.md"].source_kind == "project_file"
    assert files["TOOLS.md"].content == "system tools"
    assert files["TOOLS.md"].source_kind == "system_file"
    assert files["USER.md"].source_kind == "default_template"


def test_resolve_behavior_pack_supports_local_override_and_truncation(tmp_path: Path) -> None:
    profile = _build_profile()
    project_dir = tmp_path / "projects" / "default-project" / "behavior"
    project_dir.mkdir(parents=True)
    long_tools = "web.search 允许联网检索。\n" * 300
    (project_dir / "TOOLS.local.md").write_text(long_tools, encoding="utf-8")

    pack = resolve_behavior_pack(
        agent_profile=profile,
        project_name="Default Project",
        project_slug="default-project",
        project_root=tmp_path,
    )
    summary = build_behavior_system_summary(
        agent_profile=profile,
        project_name="Default Project",
        project_slug="default-project",
        project_root=tmp_path,
    )
    block = render_behavior_system_block(
        agent_profile=profile,
        project_name="Default Project",
        project_slug="default-project",
        project_root=tmp_path,
    )

    files = {item.file_id: item for item in pack.files}
    tools_file = files["TOOLS.md"]
    assert pack.source_chain[0] == "filesystem:projects/default-project/behavior/*.local"
    assert tools_file.source_kind == "project_local_file"
    assert tools_file.truncated is True
    assert tools_file.truncation_reason == "char_budget_exceeded"
    assert tools_file.original_char_count > tools_file.effective_char_count
    assert "project_local_file" in summary["budget"]["overlay_order"]
    assert any(item["file_id"] == "TOOLS.md" and item["truncated"] for item in summary["files"])
    assert "truncated files: TOOLS.md" in block
    assert "[TOOLS.md; truncated" in block


def test_resolve_behavior_pack_reads_legacy_project_behavior_directory(tmp_path: Path) -> None:
    profile = _build_profile()
    legacy_project_dir = tmp_path / "behavior" / "projects" / "default-project"
    legacy_project_dir.mkdir(parents=True)
    (legacy_project_dir / "PROJECT.md").write_text("legacy project context", encoding="utf-8")

    pack = resolve_behavior_pack(
        agent_profile=profile,
        project_name="Default Project",
        project_slug="default-project",
        project_root=tmp_path,
    )

    files = {item.file_id: item for item in pack.files}
    assert files["PROJECT.md"].content == "legacy project context"
    assert files["PROJECT.md"].source_kind == "project_file"
    assert "filesystem:behavior/projects/default-project" in pack.source_chain


def test_decide_clarification_keeps_non_weather_requests_for_model_phase() -> None:
    decision = decide_clarification(
        "帮我把今天下午的工作拆成 3 个优先级，并给我一个先做什么后做什么的顺序。"
    )

    assert decision.category == ""
    assert decision.action.value == "direct"
    assert decision.followup_prompt == ""
    assert decision.metadata == {}


def test_decide_clarification_only_keeps_weather_boundary_fallback() -> None:
    weather = decide_clarification("今天天气怎么样？")
    recommend = decide_clarification("帮我推荐一家餐厅")

    assert weather.category == "weather_location"
    assert weather.action.value == "delegate_after_clarification"
    assert "城市 / 区县" in weather.followup_prompt

    assert recommend.category == ""
    assert recommend.action.value == "direct"
    assert recommend.metadata == {}


def test_decide_clarification_skips_technical_recommendation_requests() -> None:
    decision = decide_clarification("帮我推荐一个 Python 日志库")

    assert decision.action.value == "direct"
    assert decision.category == ""


def test_decide_clarification_skips_technical_comparison_requests() -> None:
    decision = decide_clarification("帮我比较一下这个 PR 和 master 的实现差异")

    assert decision.action.value == "direct"
    assert decision.category == ""


def test_contains_explicit_location_does_not_treat_websearch_prefix_as_location() -> None:
    assert contains_explicit_location("你直接去 Websearch 今天天气怎么样") is False


def test_decide_butler_decision_resumes_weather_followup_after_location_reply() -> None:
    hints = build_runtime_hint_bundle(
        user_text="深圳",
        can_delegate_research=True,
        recent_clarification_category="weather_location",
        recent_clarification_source_text="今天天气怎么样？",
    )

    decision = decide_butler_decision("深圳", runtime_hints=hints)

    assert decision.mode is ButlerDecisionMode.DELEGATE_RESEARCH
    assert decision.category == "weather_location_followup"
    assert decision.metadata["rewritten_user_text"] == "深圳，今天天气怎么样？"
    assert decision.metadata["decision_source"] == "compatibility_fallback"
    assert decision.metadata["decision_fallback_reason"] == "weather_followup_resume"


def test_decide_butler_decision_uses_best_effort_for_explicit_websearch_without_location(
) -> None:
    hints = build_runtime_hint_bundle(
        user_text="你直接去 Websearch 今天天气怎么样",
        can_delegate_research=True,
        recent_clarification_category="weather_location",
        recent_clarification_source_text="今天天气怎么样？",
    )

    decision = decide_butler_decision(
        "你直接去 Websearch 今天天气怎么样",
        runtime_hints=hints,
    )

    assert decision.mode is ButlerDecisionMode.BEST_EFFORT_ANSWER
    assert decision.category == "weather_location_missing"
    assert "缺少**城市 / 区县**信息" in decision.reply_prompt
    assert decision.metadata["decision_source"] == "compatibility_fallback"
    assert decision.metadata["decision_fallback_reason"] == "weather_location_missing_best_effort"


def test_render_runtime_hint_block_exposes_effective_location_and_followup_context() -> None:
    hints = build_runtime_hint_bundle(
        user_text="深圳",
        can_delegate_research=True,
        recent_clarification_category="weather_location",
        recent_clarification_source_text="今天天气怎么样？",
    )

    block = render_runtime_hint_block(user_text="深圳", runtime_hints=hints)

    assert "RuntimeHints:" in block
    assert "can_delegate_research: True" in block
    assert "effective_location_hint: 深圳" in block
    assert "recent_clarification_category: weather_location" in block
    assert "recent_worker_lane_topic: N/A" in block
    assert "ToolUniverseHints:" in block


def test_default_butler_behavior_templates_emphasize_direct_tools_and_sticky_worker_lanes(
    tmp_path: Path,
) -> None:
    pack = resolve_behavior_pack(
        agent_profile=_build_profile(),
        project_name="Default Project",
        project_slug="default-project",
        project_root=tmp_path,
    )

    files = {item.file_id: item for item in pack.files}
    assert "web / filesystem / terminal" in files["AGENTS.md"].content
    assert "sticky worker lane" in files["AGENTS.md"].content
    assert "specialist worker lane" in files["AGENTS.md"].content
    assert "不要把用户原话原封不动转发过去" in files["TOOLS.md"].content


def test_render_runtime_hint_block_exposes_tool_universe_hints() -> None:
    selection = DynamicToolSelection(
        selection_id="selection-1",
        query=ToolIndexQuery(query="今天天气怎么样", worker_type=WorkerType.GENERAL),
        selected_tools=["runtime.now", "web.search"],
        resolution_mode="profile_first_core",
        effective_tool_universe=EffectiveToolUniverse(
            profile_id="agent-profile-default",
            profile_revision=1,
            worker_type="general",
            tool_profile="standard",
            resolution_mode="profile_first_core",
            selected_tools=["runtime.now", "web.search"],
            discovery_entrypoints=["web.search"],
            warnings=[],
        ),
        mounted_tools=[
            ToolAvailabilityExplanation(
                tool_name="runtime.now",
                status="mounted",
                summary="当前时间工具",
            ),
            ToolAvailabilityExplanation(
                tool_name="web.search",
                status="mounted",
                summary="联网检索工具",
            ),
        ],
        blocked_tools=[
            ToolAvailabilityExplanation(
                tool_name="browser.open",
                status="blocked",
                reason_code="tool_profile_not_allowed",
                summary="浏览器工具未挂载",
            )
        ],
    )
    hints = build_runtime_hint_bundle(
        user_text="今天天气怎么样",
        can_delegate_research=True,
        tool_universe=build_tool_universe_hints(
            selection,
            note="resolved_before_butler_decision",
            tool_profile_fallback="standard",
        ),
    )

    block = render_runtime_hint_block(
        user_text="今天天气怎么样",
        runtime_hints=hints,
    )

    assert "tool_resolution_mode: profile_first_core" in block
    assert "selected_tools: runtime.now, web.search" in block
    assert "web.search(mounted)" in block
    assert "browser.open(blocked:tool_profile_not_allowed)" in block
    assert "tool_universe_note: resolved_before_butler_decision" in block


# ============================================================================
# Feature 063 T2.4: Session 级 BehaviorPack 缓存
# ============================================================================


def test_behavior_pack_cache_hit_returns_same_object(tmp_path: Path) -> None:
    """第二次调用 resolve_behavior_pack 应返回缓存对象（同一引用）。"""
    invalidate_behavior_pack_cache()  # 确保干净起始状态
    profile = _build_profile()
    kwargs = {
        "agent_profile": profile,
        "project_name": "Default Project",
        "project_slug": "default-project",
        "project_root": tmp_path,
    }
    pack1 = resolve_behavior_pack(**kwargs)
    pack2 = resolve_behavior_pack(**kwargs)
    assert pack1 is pack2  # 同一引用，说明命中了缓存
    invalidate_behavior_pack_cache()


def test_behavior_pack_cache_invalidate_forces_rebuild(tmp_path: Path) -> None:
    """invalidate 后再次调用应返回新对象（不同引用）。"""
    invalidate_behavior_pack_cache()
    profile = _build_profile()
    kwargs = {
        "agent_profile": profile,
        "project_name": "Default Project",
        "project_slug": "default-project",
        "project_root": tmp_path,
    }
    pack1 = resolve_behavior_pack(**kwargs)

    count = invalidate_behavior_pack_cache(project_root=tmp_path)
    assert count >= 1

    pack2 = resolve_behavior_pack(**kwargs)
    assert pack1 is not pack2  # 新对象
    # 内容应相同
    assert {f.file_id for f in pack1.files} == {f.file_id for f in pack2.files}
    invalidate_behavior_pack_cache()


def test_behavior_pack_cache_invalidate_all(tmp_path: Path) -> None:
    """不传 project_root 时清除全部缓存。"""
    invalidate_behavior_pack_cache()
    profile = _build_profile()
    resolve_behavior_pack(
        agent_profile=profile,
        project_name="Default Project",
        project_slug="default-project",
        project_root=tmp_path,
    )
    assert behavior_pack_cache_size() >= 1

    count = invalidate_behavior_pack_cache()
    assert count >= 1
    assert behavior_pack_cache_size() == 0
