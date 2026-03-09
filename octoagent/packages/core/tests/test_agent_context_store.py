"""Feature 033: AgentContextStore 持久化测试。"""

from __future__ import annotations

from pathlib import Path

from octoagent.core.models import (
    AgentProfile,
    AgentProfileScope,
    BootstrapSession,
    BootstrapSessionStatus,
    ContextFrame,
    OwnerOverlayScope,
    OwnerProfile,
    OwnerProfileOverlay,
    SessionContextState,
)
from octoagent.core.store import create_store_group


async def test_agent_context_store_roundtrip(tmp_path: Path) -> None:
    store_group = await create_store_group(
        str(tmp_path / "agent-context.db"),
        str(tmp_path / "artifacts"),
    )
    agent_profile = AgentProfile(
        profile_id="agent-profile-alpha",
        scope=AgentProfileScope.PROJECT,
        project_id="project-alpha",
        name="Alpha Agent",
        persona_summary="负责 Alpha 项目的连续协作。",
        instruction_overlays=["保持 project 上下文连续性。"],
    )
    owner_profile = OwnerProfile(
        owner_profile_id="owner-profile-default",
        display_name="Connor",
        working_style="偏好直接结论。",
    )
    owner_overlay = OwnerProfileOverlay(
        owner_overlay_id="owner-overlay-alpha",
        owner_profile_id=owner_profile.owner_profile_id,
        scope=OwnerOverlayScope.PROJECT,
        project_id="project-alpha",
        assistant_identity_overrides={"assistant_name": "Alpha Agent"},
        interaction_preferences_override=["回答前先对齐 project 事实。"],
    )
    bootstrap = BootstrapSession(
        bootstrap_id="bootstrap-alpha",
        project_id="project-alpha",
        owner_profile_id=owner_profile.owner_profile_id,
        owner_overlay_id=owner_overlay.owner_overlay_id,
        agent_profile_id=agent_profile.profile_id,
        status=BootstrapSessionStatus.COMPLETED,
        current_step="done",
        answers={"assistant_identity": "Alpha Agent", "tone": "direct"},
    )
    session_state = SessionContextState(
        session_id="thread-alpha",
        thread_id="thread-alpha",
        project_id="project-alpha",
        workspace_id="workspace-alpha",
        task_ids=["task-1"],
        recent_turn_refs=["task-1"],
        recent_artifact_refs=["artifact-1"],
        rolling_summary="已经确认 Alpha 项目的主要约束。",
        last_context_frame_id="context-frame-alpha",
    )
    context_frame = ContextFrame(
        context_frame_id="context-frame-alpha",
        task_id="task-1",
        session_id="thread-alpha",
        project_id="project-alpha",
        workspace_id="workspace-alpha",
        agent_profile_id=agent_profile.profile_id,
        owner_profile_id=owner_profile.owner_profile_id,
        owner_overlay_id=owner_overlay.owner_overlay_id,
        bootstrap_session_id=bootstrap.bootstrap_id,
        system_blocks=[{"role": "system", "content": "Alpha Agent system context"}],
        recent_summary=session_state.rolling_summary,
        memory_hits=[{"record_id": "memory-1", "summary": "Alpha memory"}],
        source_refs=[{"ref_type": "agent_profile", "ref_id": agent_profile.profile_id}],
    )

    await store_group.agent_context_store.save_agent_profile(agent_profile)
    await store_group.agent_context_store.save_owner_profile(owner_profile)
    await store_group.agent_context_store.save_owner_overlay(owner_overlay)
    await store_group.agent_context_store.save_bootstrap_session(bootstrap)
    await store_group.agent_context_store.save_session_context(session_state)
    await store_group.agent_context_store.save_context_frame(context_frame)
    await store_group.conn.commit()

    stored_profile = await store_group.agent_context_store.get_agent_profile(
        agent_profile.profile_id
    )
    stored_overlay = await store_group.agent_context_store.get_owner_overlay_for_scope(
        project_id="project-alpha"
    )
    stored_bootstrap = await store_group.agent_context_store.get_latest_bootstrap_session(
        project_id="project-alpha"
    )
    stored_session = await store_group.agent_context_store.get_session_context("thread-alpha")
    stored_frame = await store_group.agent_context_store.get_context_frame(
        "context-frame-alpha"
    )

    assert stored_profile is not None
    assert stored_profile.persona_summary == "负责 Alpha 项目的连续协作。"
    assert stored_overlay is not None
    assert stored_overlay.assistant_identity_overrides["assistant_name"] == "Alpha Agent"
    assert stored_bootstrap is not None
    assert stored_bootstrap.answers["tone"] == "direct"
    assert stored_session is not None
    assert stored_session.rolling_summary == "已经确认 Alpha 项目的主要约束。"
    assert stored_frame is not None
    assert stored_frame.memory_hits[0]["record_id"] == "memory-1"

    frames = await store_group.agent_context_store.list_context_frames(
        project_id="project-alpha",
        limit=5,
    )
    assert [item.context_frame_id for item in frames] == ["context-frame-alpha"]

    await store_group.conn.close()
