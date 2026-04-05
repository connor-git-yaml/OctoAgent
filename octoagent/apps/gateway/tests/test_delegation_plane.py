"""Feature 030: Delegation Plane 集成测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from octoagent.core.models import (
    AgentProfile,
    AgentProfileScope,
    ContextFrame,
    NormalizedMessage,
    OrchestratorRequest,
    Project,
    ProjectSelectorState,
    SessionContextState,
    WorkerProfile,
    WorkerProfileOriginKind,
    WorkerProfileStatus,
    WorkStatus,
    TurnExecutorKind,
)
from octoagent.core.store import create_store_group
from octoagent.gateway.services.agent_context import build_scope_aware_session_id
from octoagent.gateway.services.capability_pack import CapabilityPackService
from octoagent.gateway.services.delegation_plane import DelegationPlaneService
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.gateway.services.task_service import TaskService
from octoagent.tooling import ToolBroker


async def _build_services(tmp_path: Path):
    store_group = await create_store_group(
        str(tmp_path / "gateway.db"),
        str(tmp_path / "artifacts"),
    )
    await store_group.project_store.create_project(
        Project(
            project_id="project-default",
            slug="default",
            name="Default Project",
            is_default=True,
        )
    )
    await store_group.project_store.save_selector_state(
        ProjectSelectorState(
            selector_id="selector-web",
            surface="web",
            active_project_id="project-default",
            source="test",
        )
    )
    await store_group.conn.commit()

    sse_hub = SSEHub()
    capability_pack = CapabilityPackService(
        project_root=tmp_path,
        store_group=store_group,
        tool_broker=ToolBroker(event_store=store_group.event_store),
    )
    await capability_pack.startup()
    delegation_plane = DelegationPlaneService(
        project_root=tmp_path,
        store_group=store_group,
        sse_hub=sse_hub,
        capability_pack=capability_pack,
    )
    task_service = TaskService(store_group, sse_hub)
    return store_group, task_service, delegation_plane


async def test_prepare_dispatch_routes_dev_request_and_persists_work(
    tmp_path: Path,
) -> None:
    store_group, task_service, delegation_plane = await _build_services(tmp_path)
    task_id, _ = await task_service.create_task(
        NormalizedMessage(
            text="请修复代码并补测试",
            idempotency_key="delegation-dev-route",
        )
    )

    plan = await delegation_plane.prepare_dispatch(
        OrchestratorRequest(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            user_text="请修复代码并补测试",
            worker_capability="llm_generation",
            metadata={},
        )
    )

    assert plan.dispatch_envelope is not None
    assert plan.work.selected_worker_type == "dev"
    assert plan.work.target_kind.value == "graph_agent"
    assert plan.dispatch_envelope.worker_capability == "dev"
    assert plan.tool_selection.selected_tools

    stored = await store_group.work_store.get_work(plan.work.work_id)
    assert stored is not None
    assert stored.pipeline_run_id
    assert stored.project_id == "project-default"

    await store_group.conn.close()



async def test_prepare_dispatch_inherits_context_refs(tmp_path: Path) -> None:
    store_group, task_service, delegation_plane = await _build_services(tmp_path)
    task_id, _ = await task_service.create_task(
        NormalizedMessage(
            text="请沿用当前上下文继续处理",
            thread_id="thread-context",
            idempotency_key="delegation-context-route",
        )
    )
    task = await store_group.task_store.get_task(task_id)
    assert task is not None
    await store_group.agent_context_store.save_session_context(
        SessionContextState(
            session_id=build_scope_aware_session_id(
                task,
                project_id="project-default",
            ),
            thread_id="thread-context",
            project_id="project-default",
            
            task_ids=[task_id],
            last_context_frame_id="context-frame-1",
        )
    )
    await store_group.agent_context_store.save_context_frame(
        ContextFrame(
            context_frame_id="context-frame-1",
            task_id=task_id,
            session_id=build_scope_aware_session_id(
                task,
                project_id="project-default",
            ),
            project_id="project-default",
            
            agent_profile_id="agent-profile-default",
            owner_profile_id="owner-profile-default",
        )
    )
    await store_group.conn.commit()

    plan = await delegation_plane.prepare_dispatch(
        OrchestratorRequest(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            user_text="请沿用当前上下文继续处理",
            worker_capability="llm_generation",
            metadata={},
        )
    )

    assert plan.work.session_owner_profile_id == "agent-profile-default"
    assert plan.work.agent_profile_id == "agent-profile-default"
    assert plan.work.delegation_target_profile_id == ""
    assert plan.work.context_frame_id == "context-frame-1"
    assert plan.dispatch_envelope is not None
    assert plan.dispatch_envelope.metadata["session_owner_profile_id"] == "agent-profile-default"
    assert plan.dispatch_envelope.metadata["agent_profile_id"] == "agent-profile-default"
    assert plan.dispatch_envelope.metadata["delegation_target_profile_id"] == ""
    assert plan.dispatch_envelope.metadata["context_frame_id"] == "context-frame-1"
    assert plan.dispatch_envelope.runtime_context is not None
    assert (
        plan.dispatch_envelope.runtime_context.session_owner_profile_id
        == "agent-profile-default"
    )
    assert plan.dispatch_envelope.runtime_context.agent_profile_id == "agent-profile-default"
    assert plan.dispatch_envelope.runtime_context.delegation_target_profile_id == ""
    assert plan.dispatch_envelope.runtime_context.context_frame_id == "context-frame-1"
    assert plan.dispatch_envelope.runtime_context.project_id == "project-default"
    assert "runtime_context_json" in plan.dispatch_envelope.metadata
    assert (
        plan.work.metadata["runtime_context"]["context_frame_id"] == "context-frame-1"
    )

    await store_group.conn.close()


async def test_prepare_dispatch_uses_requested_root_agent_profile_for_tool_universe(
    tmp_path: Path,
) -> None:
    store_group, task_service, delegation_plane = await _build_services(tmp_path)
    profile = WorkerProfile(
        profile_id="worker-profile-weather-alpha",
        scope=AgentProfileScope.PROJECT,
        project_id="project-default",
        name="Weather Root Agent",
        summary="专门处理需要联网查询的实时信息。",
        model_alias="main",
        tool_profile="standard",
        default_tool_groups=["network", "browser", "project"],
        selected_tools=["web.search"],
        runtime_kinds=["worker", "subagent"],
        status=WorkerProfileStatus.ACTIVE,
        origin_kind=WorkerProfileOriginKind.CUSTOM,
        draft_revision=1,
        active_revision=1,
    )
    await store_group.agent_context_store.save_worker_profile(profile)
    await store_group.conn.commit()
    task_id, _ = await task_service.create_task(
        NormalizedMessage(
            text="帮我查一下深圳今天的天气",
            thread_id="thread-weather",
            idempotency_key="delegation-profile-first-weather",
        )
    )

    plan = await delegation_plane.prepare_dispatch(
        OrchestratorRequest(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            user_text="帮我查一下深圳今天的天气",
            worker_capability="llm_generation",
            metadata={"delegation_target_profile_id": profile.profile_id},
        )
    )

    assert plan.work.delegation_target_profile_id == profile.profile_id
    assert plan.work.requested_worker_profile_id == profile.profile_id
    assert plan.work.selected_worker_type == "general"
    assert plan.tool_selection.resolution_mode == "profile_first_core"
    assert plan.tool_selection.effective_tool_universe is not None
    assert plan.tool_selection.effective_tool_universe.profile_id == profile.profile_id
    assert "web.search" in plan.tool_selection.selected_tools
    assert plan.dispatch_envelope is not None
    assert plan.dispatch_envelope.metadata["requested_worker_profile_id"] == profile.profile_id

    await store_group.conn.close()


async def test_prepare_dispatch_uses_agent_profile_capability_selection_for_tool_universe(
    tmp_path: Path,
) -> None:
    store_group, task_service, delegation_plane = await _build_services(tmp_path)
    project = await store_group.project_store.get_default_project()
    assert project is not None
    await store_group.project_store.save_project(
        project.model_copy(
            update={
                "metadata": {
                    **dict(project.metadata),
                    "skill_selection": {
                        "selected_item_ids": [],
                        "disabled_item_ids": ["skill:coding-agent"],
                    },
                },
                "updated_at": datetime.now(tz=UTC),
            }
        )
    )
    agent_profile = AgentProfile(
        profile_id="agent-profile-ops-boundary",
        scope=AgentProfileScope.PROJECT,
        project_id="project-default",
        name="Ops Agent",
        persona_summary="按受控边界处理运行问题。",
        tool_profile="standard",
        model_alias="main",
        metadata={
            "capability_provider_selection": {
                "selected_item_ids": ["skill:coding-agent"],
                "disabled_item_ids": [],
            }
        },
    )
    await store_group.agent_context_store.save_agent_profile(agent_profile)
    await store_group.conn.commit()

    base_pack = await delegation_plane._capability_pack.get_pack(
        project_id="project-default",
        
    )
    # Feature 057: 验证 disabled skill 从 pack 的 skills 列表中被过滤
    assert "coding-agent" not in {item.skill_id for item in base_pack.skills}

    task_id, _ = await task_service.create_task(
        NormalizedMessage(
            text="请帮我诊断当前运行状态",
            thread_id="thread-ops-boundary",
            idempotency_key="delegation-agent-profile-boundary",
        )
    )

    plan = await delegation_plane.prepare_dispatch(
        OrchestratorRequest(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            user_text="请帮我诊断当前运行状态",
            worker_capability="llm_generation",
            metadata={
                "session_owner_profile_id": agent_profile.profile_id,
                "requested_worker_type": "ops",
            },
        )
    )

    assert plan.work.session_owner_profile_id == agent_profile.profile_id
    assert plan.work.agent_profile_id == agent_profile.profile_id
    assert plan.work.requested_worker_profile_id == ""
    assert plan.work.delegation_target_profile_id == ""
    assert plan.work.selected_worker_type == "ops"
    assert plan.tool_selection.effective_tool_universe is not None
    assert (
        plan.tool_selection.effective_tool_universe.profile_id == agent_profile.profile_id
    )
    assert plan.tool_selection.selected_tools, "selected_tools 不应为空"

    await store_group.conn.close()


async def test_prepare_dispatch_does_not_promote_session_owner_to_delegation_target(
    tmp_path: Path,
) -> None:
    store_group, task_service, delegation_plane = await _build_services(tmp_path)
    task_id, _ = await task_service.create_task(
        NormalizedMessage(
            text="继续由 finance root agent 自己处理",
            thread_id="thread-owner-self",
            idempotency_key="delegation-owner-not-target",
        )
    )

    plan = await delegation_plane.prepare_dispatch(
        OrchestratorRequest(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            user_text="继续由 finance root agent 自己处理",
            worker_capability="llm_generation",
            metadata={
                "session_owner_profile_id": "worker-profile-finance-root",
                "agent_profile_id": "worker-profile-finance-root",
            },
        )
    )

    assert plan.work.session_owner_profile_id == "worker-profile-finance-root"
    assert plan.work.agent_profile_id == "worker-profile-finance-root"
    assert plan.work.delegation_target_profile_id == ""
    assert plan.work.requested_worker_profile_id == ""
    assert plan.work.turn_executor_kind == TurnExecutorKind.WORKER
    assert plan.dispatch_envelope is not None
    assert plan.dispatch_envelope.metadata["session_owner_profile_id"] == (
        "worker-profile-finance-root"
    )
    assert not plan.dispatch_envelope.metadata.get("delegation_target_profile_id")
    assert not plan.dispatch_envelope.metadata.get("requested_worker_profile_id")

    await store_group.conn.close()


async def test_prepare_dispatch_preserves_typed_metadata_in_dispatch_envelope(
    tmp_path: Path,
) -> None:
    store_group, task_service, delegation_plane = await _build_services(tmp_path)
    task_id, _ = await task_service.create_task(
        NormalizedMessage(
            text="请走一遍 typed metadata 路由",
            idempotency_key="delegation-typed-metadata",
        )
    )

    plan = await delegation_plane.prepare_dispatch(
        OrchestratorRequest(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            user_text="请走一遍 typed metadata 路由",
            worker_capability="llm_generation",
            metadata={
                "requested_worker_profile_version": 7,
                "target_kind": "worker",
            },
        )
    )

    assert plan.dispatch_envelope is not None
    assert isinstance(plan.dispatch_envelope.metadata["requested_worker_profile_version"], int)
    assert (
        plan.dispatch_envelope.metadata["requested_worker_profile_version"]
        == plan.work.requested_worker_profile_version
    )
    assert isinstance(plan.dispatch_envelope.metadata["selected_tools"], list)
    assert plan.dispatch_envelope.metadata["target_kind"] == "worker"

    await store_group.conn.close()


async def test_prepare_dispatch_uses_scope_aware_session_key(tmp_path: Path) -> None:
    store_group, task_service, delegation_plane = await _build_services(tmp_path)
    alpha_task_id, _ = await task_service.create_task(
        NormalizedMessage(
            text="Alpha task",
            thread_id="thread-shared",
            scope_id="scope-alpha",
            idempotency_key="delegation-scope-alpha",
        )
    )
    beta_task_id, _ = await task_service.create_task(
        NormalizedMessage(
            text="Beta task",
            thread_id="thread-shared",
            scope_id="scope-beta",
            idempotency_key="delegation-scope-beta",
        )
    )
    alpha_task = await store_group.task_store.get_task(alpha_task_id)
    beta_task = await store_group.task_store.get_task(beta_task_id)
    assert alpha_task is not None
    assert beta_task is not None

    alpha_session_id = build_scope_aware_session_id(
        alpha_task,
        project_id="project-default",
    )
    beta_session_id = build_scope_aware_session_id(
        beta_task,
        project_id="project-default",
    )
    await store_group.agent_context_store.save_session_context(
        SessionContextState(
            session_id=alpha_session_id,
            thread_id="thread-shared",
            project_id="project-default",
            
            task_ids=[alpha_task_id],
            last_context_frame_id="context-frame-alpha",
        )
    )
    await store_group.agent_context_store.save_session_context(
        SessionContextState(
            session_id=beta_session_id,
            thread_id="thread-shared",
            project_id="project-default",
            
            task_ids=[beta_task_id],
            last_context_frame_id="context-frame-beta",
        )
    )
    await store_group.agent_context_store.save_context_frame(
        ContextFrame(
            context_frame_id="context-frame-alpha",
            task_id=alpha_task_id,
            session_id=alpha_session_id,
            project_id="project-default",
            
            agent_profile_id="agent-profile-alpha",
            owner_profile_id="owner-profile-default",
        )
    )
    await store_group.agent_context_store.save_context_frame(
        ContextFrame(
            context_frame_id="context-frame-beta",
            task_id=beta_task_id,
            session_id=beta_session_id,
            project_id="project-default",
            
            agent_profile_id="agent-profile-beta",
            owner_profile_id="owner-profile-default",
        )
    )
    await store_group.conn.commit()

    alpha_plan = await delegation_plane.prepare_dispatch(
        OrchestratorRequest(
            task_id=alpha_task_id,
            trace_id=f"trace-{alpha_task_id}",
            user_text="Alpha task",
            worker_capability="llm_generation",
            metadata={},
        )
    )
    beta_plan = await delegation_plane.prepare_dispatch(
        OrchestratorRequest(
            task_id=beta_task_id,
            trace_id=f"trace-{beta_task_id}",
            user_text="Beta task",
            worker_capability="llm_generation",
            metadata={},
        )
    )

    assert alpha_plan.work.context_frame_id == "context-frame-alpha"
    assert alpha_plan.work.agent_profile_id == "agent-profile-alpha"
    assert beta_plan.work.context_frame_id == "context-frame-beta"
    assert beta_plan.work.agent_profile_id == "agent-profile-beta"

    await store_group.conn.close()


async def test_prepare_dispatch_pause_resume_and_cancel_pipeline(tmp_path: Path) -> None:
    store_group, task_service, delegation_plane = await _build_services(tmp_path)
    scheduled_dispatches = []

    async def fake_scheduler(envelope) -> bool:
        scheduled_dispatches.append(envelope)
        return True

    delegation_plane.bind_dispatch_scheduler(fake_scheduler)
    paused_task_id, _ = await task_service.create_task(
        NormalizedMessage(
            text="请先等待审批后再继续诊断",
            idempotency_key="delegation-pause-route",
        )
    )

    paused_plan = await delegation_plane.prepare_dispatch(
        OrchestratorRequest(
            task_id=paused_task_id,
            trace_id=f"trace-{paused_task_id}",
            user_text="请先等待审批后再继续诊断",
            worker_capability="llm_generation",
            metadata={"delegation_pause": "approval"},
        )
    )

    assert paused_plan.dispatch_envelope is None
    assert paused_plan.pipeline_status.value == "waiting_approval"
    assert paused_plan.work.status.value == "waiting_approval"

    cancelled = await delegation_plane.cancel_work(
        paused_plan.work.work_id,
        reason="operator_cancelled",
    )
    assert cancelled is not None
    assert cancelled.status.value == "cancelled"

    cancelled_run = await store_group.work_store.get_pipeline_run(paused_plan.work.pipeline_run_id)
    assert cancelled_run is not None
    assert cancelled_run.status.value == "cancelled"
    assert cancelled_run.pause_reason == "work_cancelled:operator_cancelled"

    resume_task_id, _ = await task_service.create_task(
        NormalizedMessage(
            text="请先等待审批，稍后继续诊断",
            idempotency_key="delegation-resume-route",
        )
    )
    resume_plan = await delegation_plane.prepare_dispatch(
        OrchestratorRequest(
            task_id=resume_task_id,
            trace_id=f"trace-{resume_task_id}",
            user_text="请先等待审批，稍后继续诊断",
            worker_capability="llm_generation",
            metadata={"delegation_pause": "approval"},
        )
    )
    resumed = await delegation_plane.resume_pipeline(
        resume_plan.work.work_id,
        state_patch={"approval_granted": True},
    )
    assert resumed is not None
    assert resumed.status.value == "created"

    run = await store_group.work_store.get_pipeline_run(resumed.pipeline_run_id)
    assert run is not None
    assert run.status.value == "succeeded"
    assert run.state_snapshot["approval_granted"] is True
    assert len(scheduled_dispatches) == 1
    assert scheduled_dispatches[0].task_id == resume_task_id
    assert scheduled_dispatches[0].metadata["work_id"] == resume_plan.work.work_id

    await store_group.conn.close()


async def test_retry_work_requeues_successful_preflight_and_dispatches(tmp_path: Path) -> None:
    store_group, task_service, delegation_plane = await _build_services(tmp_path)
    scheduled_dispatches = []

    async def fake_scheduler(envelope) -> bool:
        scheduled_dispatches.append(envelope)
        return True

    delegation_plane.bind_dispatch_scheduler(fake_scheduler)
    task_id, _ = await task_service.create_task(
        NormalizedMessage(
            text="请修复代码并补测试",
            idempotency_key="delegation-retry-route",
        )
    )

    plan = await delegation_plane.prepare_dispatch(
        OrchestratorRequest(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            user_text="请修复代码并补测试",
            worker_capability="llm_generation",
            metadata={},
        )
    )
    assert plan.dispatch_envelope is not None

    failed_work = plan.work.model_copy(update={"status": WorkStatus.FAILED})
    await store_group.work_store.save_work(failed_work)
    await store_group.conn.commit()

    retried = await delegation_plane.retry_work(plan.work.work_id)
    assert retried is not None
    assert retried.retry_count == 1
    assert retried.status.value == "created"
    assert retried.completed_at is None
    assert len(scheduled_dispatches) == 1
    assert scheduled_dispatches[0].task_id == task_id
    assert scheduled_dispatches[0].worker_capability == "dev"
    assert scheduled_dispatches[0].metadata["work_id"] == plan.work.work_id

    await store_group.conn.close()


async def test_cancel_work_cascades_to_descendant_works(tmp_path: Path) -> None:
    store_group, task_service, delegation_plane = await _build_services(tmp_path)
    parent_task_id, _ = await task_service.create_task(
        NormalizedMessage(
            text="请先暂停父 work",
            idempotency_key="delegation-parent-cancel-cascade",
        )
    )
    parent = await delegation_plane.prepare_dispatch(
        OrchestratorRequest(
            task_id=parent_task_id,
            trace_id=f"trace-{parent_task_id}",
            user_text="请先暂停父 work",
            worker_capability="llm_generation",
            metadata={"delegation_pause": "approval"},
        )
    )

    child_task_id, _ = await task_service.create_task(
        NormalizedMessage(
            text="请先暂停子 work",
            idempotency_key="delegation-child-cancel-cascade",
        )
    )
    child = await delegation_plane.prepare_dispatch(
        OrchestratorRequest(
            task_id=child_task_id,
            trace_id=f"trace-{child_task_id}",
            user_text="请先暂停子 work",
            worker_capability="llm_generation",
            metadata={
                "delegation_pause": "approval",
                "parent_work_id": parent.work.work_id,
                "parent_task_id": parent_task_id,
            },
        )
    )

    cancelled = await delegation_plane.cancel_work(parent.work.work_id, reason="cascade_cancelled")
    assert cancelled is not None
    assert cancelled.status.value == "cancelled"

    child_after = await store_group.work_store.get_work(child.work.work_id)
    child_run = await store_group.work_store.get_pipeline_run(child.work.pipeline_run_id)
    assert child_after is not None
    assert child_after.status.value == "cancelled"
    assert child_run is not None
    assert child_run.status.value == "cancelled"
    assert child_run.pause_reason == "work_cancelled:cascade_cancelled:cascade"

    await store_group.conn.close()


async def test_prepare_dispatch_honors_explicit_parent_and_worker_route(tmp_path: Path) -> None:
    store_group, task_service, delegation_plane = await _build_services(tmp_path)
    task_id, _ = await task_service.create_task(
        NormalizedMessage(
            text="请把这项工作委派给 research subagent",
            idempotency_key="delegation-explicit-child-route",
        )
    )

    plan = await delegation_plane.prepare_dispatch(
        OrchestratorRequest(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            user_text="请把这项工作委派给 research subagent",
            worker_capability="llm_generation",
            metadata={
                "parent_work_id": "work-parent-1",
                "parent_task_id": "task-parent-1",
                "requested_worker_type": "research",
                "target_kind": "subagent",
            },
        )
    )

    assert plan.dispatch_envelope is not None
    assert plan.work.parent_work_id == "work-parent-1"
    assert plan.work.selected_worker_type == "research"
    assert plan.work.target_kind.value == "subagent"
    assert plan.dispatch_envelope.metadata["parent_work_id"] == "work-parent-1"
    assert plan.dispatch_envelope.metadata["parent_task_id"] == "task-parent-1"
    assert plan.dispatch_envelope.metadata["selected_worker_type"] == "research"
    assert plan.dispatch_envelope.metadata["target_kind"] == "subagent"

    await store_group.conn.close()


async def test_prepare_dispatch_preserves_resume_state_for_top_level_work(tmp_path: Path) -> None:
    store_group, task_service, delegation_plane = await _build_services(tmp_path)
    task_id, _ = await task_service.create_task(
        NormalizedMessage(
            text="请继续处理这个顶层任务",
            idempotency_key="delegation-top-level-resume",
        )
    )

    plan = await delegation_plane.prepare_dispatch(
        OrchestratorRequest(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            user_text="请继续处理这个顶层任务",
            worker_capability="llm_generation",
            resume_from_node="model_call_started",
            resume_state_snapshot={
                "llm_call_idempotency_key": "task-top-level:llm_call:main:test",
            },
            metadata={"requested_worker_type": "dev"},
        )
    )

    assert plan.dispatch_envelope is not None
    assert plan.dispatch_envelope.resume_from_node == "model_call_started"
    assert plan.dispatch_envelope.resume_state_snapshot == {
        "llm_call_idempotency_key": "task-top-level:llm_call:main:test",
    }
    assert plan.work.metadata["request_context"]["resume_from_node"] == "model_call_started"
    assert plan.work.metadata["request_context"]["resume_state_snapshot"] == {
        "llm_call_idempotency_key": "task-top-level:llm_call:main:test",
    }

    await store_group.conn.close()


async def test_prepare_dispatch_clears_resume_state_for_child_subagent(tmp_path: Path) -> None:
    store_group, task_service, delegation_plane = await _build_services(tmp_path)
    task_id, _ = await task_service.create_task(
        NormalizedMessage(
            text="请把这项工作继续委派给 research subagent",
            idempotency_key="delegation-child-resume-cleared",
        )
    )

    plan = await delegation_plane.prepare_dispatch(
        OrchestratorRequest(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            user_text="请把这项工作继续委派给 research subagent",
            worker_capability="llm_generation",
            resume_from_node="model_call_started",
            resume_state_snapshot={
                "llm_call_idempotency_key": "task-child:llm_call:main:test",
            },
            metadata={
                "parent_work_id": "work-parent-1",
                "parent_task_id": "task-parent-1",
                "requested_worker_type": "research",
                "target_kind": "subagent",
                "spawned_by": "agent_freshness_delegate",
            },
        )
    )

    assert plan.dispatch_envelope is not None
    assert plan.dispatch_envelope.resume_from_node is None
    assert plan.dispatch_envelope.resume_state_snapshot is None
    assert plan.work.metadata["resume_from_node"] == ""
    assert plan.work.metadata["request_context"]["resume_from_node"] == ""
    assert plan.work.metadata["request_context"]["resume_state_snapshot"] == {}

    await store_group.conn.close()


# ============================================================
# W4: Work 状态机校验集成测试
# ============================================================

import pytest  # noqa: E402
from octoagent.core.models import DelegationResult, WorkTransitionError


async def test_mark_dispatched_rejects_terminal_work(tmp_path: Path) -> None:
    """对已终态的 work 调用 mark_dispatched 应抛出 WorkTransitionError。"""
    store_group, task_service, delegation_plane = await _build_services(tmp_path)
    task_id, _ = await task_service.create_task(
        NormalizedMessage(text="test", idempotency_key="trans-test-1")
    )
    plan = await delegation_plane.prepare_dispatch(
        OrchestratorRequest(
            task_id=task_id, trace_id=f"trace-{task_id}",
            user_text="test", worker_capability="llm_generation", metadata={},
        )
    )
    # 通过 cancel 让 work 进入终态（CREATED → CANCELLED 合法）
    await delegation_plane.cancel_work(plan.work.work_id, reason="test")
    # 对已 CANCELLED 的 work 做 mark_dispatched → 应抛出
    with pytest.raises(WorkTransitionError):
        await delegation_plane.mark_dispatched(
            work_id=plan.work.work_id, worker_id="w2", dispatch_id="d2",
        )
    await store_group.conn.close()


async def test_escalate_rejects_created_work(tmp_path: Path) -> None:
    """对 CREATED 状态的 work 调用 escalate_work 应抛出 WorkTransitionError。"""
    store_group, task_service, delegation_plane = await _build_services(tmp_path)
    task_id, _ = await task_service.create_task(
        NormalizedMessage(text="test", idempotency_key="trans-test-2")
    )
    plan = await delegation_plane.prepare_dispatch(
        OrchestratorRequest(
            task_id=task_id, trace_id=f"trace-{task_id}",
            user_text="test", worker_capability="llm_generation", metadata={},
        )
    )
    # work 目前是 CREATED，不能直接 escalate（需要先 RUNNING）
    with pytest.raises(WorkTransitionError):
        await delegation_plane.escalate_work(plan.work.work_id, reason="test")
    await store_group.conn.close()
