"""Feature 030: Delegation Plane 集成测试。"""

from __future__ import annotations

from pathlib import Path

from octoagent.core.models import (
    ContextFrame,
    NormalizedMessage,
    OrchestratorRequest,
    Project,
    ProjectSelectorState,
    SessionContextState,
    Workspace,
    WorkStatus,
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
    await store_group.project_store.create_workspace(
        Workspace(
            workspace_id="workspace-default",
            project_id="project-default",
            slug="primary",
            name="Primary",
            root_path=str(tmp_path),
        )
    )
    await store_group.project_store.save_selector_state(
        ProjectSelectorState(
            selector_id="selector-web",
            surface="web",
            active_project_id="project-default",
            active_workspace_id="workspace-default",
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
    assert plan.work.selected_worker_type.value == "dev"
    assert plan.work.target_kind.value == "graph_agent"
    assert plan.dispatch_envelope.worker_capability == "dev"
    assert plan.tool_selection.selected_tools

    stored = await store_group.work_store.get_work(plan.work.work_id)
    assert stored is not None
    assert stored.pipeline_run_id
    assert stored.project_id == "project-default"
    assert stored.workspace_id == "workspace-default"

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
                workspace_id="workspace-default",
            ),
            thread_id="thread-context",
            project_id="project-default",
            workspace_id="workspace-default",
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
                workspace_id="workspace-default",
            ),
            project_id="project-default",
            workspace_id="workspace-default",
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

    assert plan.work.agent_profile_id == "agent-profile-default"
    assert plan.work.context_frame_id == "context-frame-1"
    assert plan.dispatch_envelope is not None
    assert plan.dispatch_envelope.metadata["agent_profile_id"] == "agent-profile-default"
    assert plan.dispatch_envelope.metadata["context_frame_id"] == "context-frame-1"

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
        workspace_id="workspace-default",
    )
    beta_session_id = build_scope_aware_session_id(
        beta_task,
        project_id="project-default",
        workspace_id="workspace-default",
    )
    await store_group.agent_context_store.save_session_context(
        SessionContextState(
            session_id=alpha_session_id,
            thread_id="thread-shared",
            project_id="project-default",
            workspace_id="workspace-default",
            task_ids=[alpha_task_id],
            last_context_frame_id="context-frame-alpha",
        )
    )
    await store_group.agent_context_store.save_session_context(
        SessionContextState(
            session_id=beta_session_id,
            thread_id="thread-shared",
            project_id="project-default",
            workspace_id="workspace-default",
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
            workspace_id="workspace-default",
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
            workspace_id="workspace-default",
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
    assert plan.work.selected_worker_type.value == "research"
    assert plan.work.target_kind.value == "subagent"
    assert plan.dispatch_envelope.metadata["parent_work_id"] == "work-parent-1"
    assert plan.dispatch_envelope.metadata["parent_task_id"] == "task-parent-1"
    assert plan.dispatch_envelope.metadata["selected_worker_type"] == "research"
    assert plan.dispatch_envelope.metadata["target_kind"] == "subagent"

    await store_group.conn.close()
