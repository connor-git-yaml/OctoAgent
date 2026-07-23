"""F151 T063：runtime selector 拒绝的 HTTP 与审计边界。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from octoagent.core.models import (
    ActionResultEnvelope,
    ActorType,
    AgentProfile,
    ControlPlaneActionStatus,
    DispatchEnvelope,
    Event,
    EventCausality,
    EventType,
    ExecutionSessionState,
    TaskStatus,
    Work,
)
from octoagent.core.models.agent_context import AgentProfileStatus
from octoagent.core.models.message import NormalizedMessage
from octoagent.core.models.payloads import ExecutionStatusChangedPayload
from octoagent.core.store import create_store_group
from octoagent.gateway.deps import require_front_door_access
from octoagent.gateway.routes import control_plane as control_plane_routes
from octoagent.gateway.routes import execution as execution_routes
from octoagent.gateway.services.control_plane import ControlPlaneService
from octoagent.gateway.services.execution_console import ExecutionConsoleService
from octoagent.gateway.services.orchestrator import OrchestratorService
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.gateway.services.task_service import TaskService
from octoagent.gateway.services.worker_runtime import (
    WorkerBackendUnavailableError,
    WorkerRuntimeConfig,
    preflight_graph_runtime,
)

from apps.gateway.tests.runtime_service_fixtures import runtime_service_fixture

_AUDIT_TASK_ID = "ops-control-plane"


@pytest_asyncio.fixture
async def control_plane_http(tmp_path: Path) -> AsyncIterator[SimpleNamespace]:
    stores = await create_store_group(
        str(tmp_path / "gateway.db"),
        str(tmp_path / "artifacts"),
    )
    runner = SimpleNamespace(
        launch_child_task=AsyncMock(return_value=("child-task", True)),
        cancel_task=AsyncMock(),
    )
    capability_pack = SimpleNamespace(
        apply_worker_plan=AsyncMock(return_value={"status": "applied"})
    )
    service = ControlPlaneService(
        project_root=tmp_path,
        store_group=stores,
        task_runner=runner,
        capability_pack_service=capability_pack,
    )
    app = FastAPI()
    execution_console = ExecutionConsoleService(store_group=stores, sse_hub=SSEHub())
    app.include_router(execution_routes.router)
    app.include_router(
        control_plane_routes.router,
        dependencies=[Depends(require_front_door_access)],
    )
    app.state.project_root = tmp_path
    app.state.store_group = stores
    app.state.execution_console = execution_console
    app.state.control_plane_service = service
    transport = ASGITransport(app=app, client=("127.0.0.1", 123))
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield SimpleNamespace(
                client=client,
                stores=stores,
                service=service,
                runner=runner,
                capability_pack=capability_pack,
                execution_console=execution_console,
            )
    finally:
        await stores.close()


@pytest.mark.asyncio
async def test_raw_docker_execution_event_replays_through_rest_projection(
    control_plane_http: SimpleNamespace,
) -> None:
    task_service = TaskService(control_plane_http.stores, SSEHub(), storage_only=True)
    task_id, created = await task_service.create_task(
        NormalizedMessage(text="raw docker history", idempotency_key="t068-raw-history")
    )
    event = Event(
        event_id="event-t068-raw-history",
        task_id=task_id,
        task_seq=await control_plane_http.stores.event_store.get_next_task_seq(task_id),
        ts=datetime.now(UTC),
        type=EventType.EXECUTION_STATUS_CHANGED,
        actor=ActorType.SYSTEM,
        payload=ExecutionStatusChangedPayload(
            session_id="session-t068-docker-history",
            backend="docker",
            backend_job_id="container-t068-history",
            status=ExecutionSessionState.RUNNING,
            interactive=False,
            runtime_dir="/historical/runtime/t068",
            container_name="octoagent-history-t068",
            message="historical docker event",
            metadata={"runtime_kind": "historical_container"},
        ).model_dump(mode="json"),
        trace_id=f"trace-{task_id}",
        causality=EventCausality(idempotency_key="t068-raw-history-event"),
    )
    await control_plane_http.stores.event_store.append_event_committed(event)

    response = await control_plane_http.client.get(f"/api/tasks/{task_id}/execution")
    issues: list[str] = []
    if created is not True:
        issues.append("raw history fixture did not create its task")
    if response.status_code != 200:
        issues.append(f"HTTP {response.status_code}, expected 200")
    else:
        session = response.json().get("session", {})
        expected = {
            "session_id": "session-t068-docker-history",
            "backend": "docker",
            "backend_job_id": "container-t068-history",
        }
        for key, value in expected.items():
            if session.get(key) != value:
                issues.append(f"{key}={session.get(key)!r}, expected {value!r}")
        metadata = session.get("metadata", {})
        if metadata.get("runtime_kind") != "historical_container":
            issues.append("historical runtime_kind metadata was not projected")
        if metadata.get("container_name") != "octoagent-history-t068":
            issues.append("historical container_name was not projected")
    _fail_if_issues("F151_RAW_HISTORY_REPLAY_MISSING", issues)


def _action_body(request_id: str, action_id: str, params: dict[str, Any]) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "action_id": action_id,
        "surface": "web",
        "actor": {"actor_id": "user:web", "actor_label": "Owner"},
        "params": params,
    }


async def _post_action(
    client: AsyncClient,
    *,
    request_id: str,
    action_id: str,
    params: dict[str, Any],
):
    return await client.post(
        "/api/control/actions",
        json=_action_body(request_id, action_id, params),
    )


async def _audit_types_for_request(stores: Any, request_id: str) -> list[EventType]:
    events = await stores.event_store.get_events_for_task(_AUDIT_TASK_ID)
    return [item.type for item in events if item.payload.get("request_id") == request_id]


def _record_rejection_issues(
    issues: list[str],
    *,
    response: Any,
    expected_status: int,
    expected_code: str,
) -> None:
    if response.status_code != expected_status:
        issues.append(f"HTTP {response.status_code}, expected {expected_status}")
        return
    result = response.json().get("result", {})
    if result.get("status") != "rejected":
        issues.append(f"status={result.get('status')!r}, expected rejected")
    if result.get("code") != expected_code:
        issues.append(f"code={result.get('code')!r}, expected {expected_code}")


def _record_audit_issues(issues: list[str], event_types: list[EventType]) -> None:
    expected = [
        EventType.CONTROL_PLANE_ACTION_REQUESTED,
        EventType.CONTROL_PLANE_ACTION_REJECTED,
    ]
    if event_types != expected:
        issues.append(f"audit types={event_types!r}, expected {expected!r}")


def _fail_if_issues(oracle: str, issues: list[str]) -> None:
    if issues:
        pytest.fail(f"{oracle}: {'; '.join(issues)}", pytrace=False)


@pytest.mark.asyncio
async def test_control_plane_work_split_rejects_unsupported_selector_with_exact_audit(
    control_plane_http: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent_work = Work(work_id="parent-work", task_id="parent-task", title="parent")
    parent_task = SimpleNamespace(
        task_id="parent-task",
        thread_id="parent-thread",
        scope_id="project-1",
        requester=SimpleNamespace(channel="web", sender_id="owner"),
    )
    monkeypatch.setattr(
        control_plane_http.service._work_service,
        "_get_work_in_scope",
        AsyncMock(return_value=parent_work),
    )
    original_get_task = control_plane_http.stores.task_store.get_task

    async def get_task(task_id: str):
        if task_id == "parent-task":
            return parent_task
        return await original_get_task(task_id)

    monkeypatch.setattr(control_plane_http.stores.task_store, "get_task", get_task)

    accepted = await _post_action(
        control_plane_http.client,
        request_id="t063-work-accepted",
        action_id="work.split",
        params={
            "work_id": "parent-work",
            "objectives": ["accepted child"],
            "target_kind": "subagent",
        },
    )
    positive_launches = control_plane_http.runner.launch_child_task.await_count
    control_plane_http.runner.launch_child_task.reset_mock()

    request_id = "t063-work-rejected"
    rejected = await _post_action(
        control_plane_http.client,
        request_id=request_id,
        action_id="work.split",
        params={
            "work_id": "parent-work",
            "objectives": ["must not launch"],
            "target_kind": "docker",
        },
    )
    issues: list[str] = []
    if accepted.status_code != 200 or positive_launches != 1:
        issues.append("valid work.split accept control did not launch exactly once")
    _record_rejection_issues(
        issues,
        response=rejected,
        expected_status=422,
        expected_code="WORKER_RUNTIME_SELECTOR_UNSUPPORTED",
    )
    _record_audit_issues(
        issues,
        await _audit_types_for_request(control_plane_http.stores, request_id),
    )
    if control_plane_http.runner.launch_child_task.await_count:
        issues.append("rejected selector reached child launch")
    _fail_if_issues("F151_WORK_SPLIT_HTTP_AUDIT_MAPPING_MISSING", issues)


@pytest.mark.asyncio
async def test_control_plane_worker_spawn_rejects_unavailable_runtime_with_exact_audit(
    control_plane_http: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = AgentProfile(
        profile_id="worker-profile",
        name="Worker",
        kind="worker",
        status=AgentProfileStatus.ACTIVE,
        active_revision=1,
    )
    monkeypatch.setattr(
        control_plane_http.service._worker_service,
        "_get_worker_profile_in_scope",
        AsyncMock(return_value=profile),
    )
    monkeypatch.setattr(
        control_plane_http.service._worker_service,
        "_resolve_selection",
        AsyncMock(return_value=(None, None, None, None)),
    )

    accepted = await _post_action(
        control_plane_http.client,
        request_id="t063-spawn-accepted",
        action_id="worker.spawn_from_profile",
        params={"profile_id": "worker-profile", "objective": "accepted child"},
    )
    positive_launches = control_plane_http.runner.launch_child_task.await_count
    control_plane_http.runner.launch_child_task.reset_mock()
    control_plane_http.service._ctx.task_runner = None

    request_id = "t063-spawn-rejected"
    rejected = await _post_action(
        control_plane_http.client,
        request_id=request_id,
        action_id="worker.spawn_from_profile",
        params={"profile_id": "worker-profile", "objective": "must not launch"},
    )
    issues: list[str] = []
    if accepted.status_code != 200 or positive_launches != 1:
        issues.append("available runtime accept control did not launch exactly once")
    _record_rejection_issues(
        issues,
        response=rejected,
        expected_status=503,
        expected_code="WORKER_RUNTIME_UNAVAILABLE",
    )
    _record_audit_issues(
        issues,
        await _audit_types_for_request(control_plane_http.stores, request_id),
    )
    if control_plane_http.runner.launch_child_task.await_count:
        issues.append("unavailable runtime reached child launch")
    _fail_if_issues("F151_SPAWN_HTTP_AUDIT_MAPPING_MISSING", issues)


@pytest.mark.asyncio
async def test_control_plane_worker_apply_rejects_mixed_batch_before_workload_side_effects(
    control_plane_http: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent_work = Work(work_id="parent-work", task_id="parent-task", title="parent")
    monkeypatch.setattr(
        control_plane_http.service._work_service,
        "_get_work_in_scope",
        AsyncMock(return_value=parent_work),
    )
    accepted = await _post_action(
        control_plane_http.client,
        request_id="t063-apply-accepted",
        action_id="worker.apply",
        params={
            "work_id": "parent-work",
            "plan": {"assignments": [{"objective": "accepted", "target_kind": "subagent"}]},
        },
    )
    positive_applies = control_plane_http.capability_pack.apply_worker_plan.await_count
    control_plane_http.capability_pack.apply_worker_plan.reset_mock()
    control_plane_http.runner.cancel_task.reset_mock()

    request_id = "t063-apply-rejected"
    rejected = await _post_action(
        control_plane_http.client,
        request_id=request_id,
        action_id="worker.apply",
        params={
            "work_id": "parent-work",
            "plan": {
                "assignments": [
                    {"objective": "valid", "target_kind": "subagent"},
                    {"objective": "invalid", "target_kind": "docker"},
                ]
            },
        },
    )
    issues: list[str] = []
    if accepted.status_code != 200 or positive_applies != 1:
        issues.append("valid worker.apply accept control did not apply exactly once")
    _record_rejection_issues(
        issues,
        response=rejected,
        expected_status=422,
        expected_code="WORKER_RUNTIME_SELECTOR_UNSUPPORTED",
    )
    _record_audit_issues(
        issues,
        await _audit_types_for_request(control_plane_http.stores, request_id),
    )
    if control_plane_http.capability_pack.apply_worker_plan.await_count:
        issues.append("mixed invalid batch reached plan apply")
    if control_plane_http.runner.cancel_task.await_count:
        issues.append("mixed invalid batch reached task cancellation")
    _fail_if_issues("F151_APPLY_HTTP_AUDIT_MAPPING_MISSING", issues)


@pytest.mark.asyncio
async def test_request_time_runtime_dependency_failure_returns_503_without_workload_side_effects(
    control_plane_http: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execute_action = AsyncMock(
        return_value=ActionResultEnvelope(
            request_id="t065-accepted",
            correlation_id="t065-accepted",
            action_id="runtime.probe",
            status=ControlPlaneActionStatus.COMPLETED,
            code="RUNTIME_PROBE_COMPLETED",
            message="accepted",
        )
    )
    monkeypatch.setattr(control_plane_http.service, "execute_action", execute_action)
    accepted = await _post_action(
        control_plane_http.client,
        request_id="t065-accepted",
        action_id="runtime.probe",
        params={},
    )
    positive_calls = execute_action.await_count
    execute_action.reset_mock()

    (control_plane_http.service._ctx.project_root / "octoagent.yaml").write_text(
        "front_door: [\n",
        encoding="utf-8",
    )
    rejected = await _post_action(
        control_plane_http.client,
        request_id="t065-rejected",
        action_id="runtime.probe",
        params={},
    )

    issues: list[str] = []
    if accepted.status_code != 200 or positive_calls != 1:
        issues.append("valid request dependency accept control did not reach workload once")
    detail = rejected.json().get("detail", {})
    if rejected.status_code != 503:
        issues.append(f"HTTP {rejected.status_code}, expected 503")
    if detail.get("code") != "FRONT_DOOR_CONFIG_INVALID":
        issues.append(f"code={detail.get('code')!r}, expected FRONT_DOOR_CONFIG_INVALID")
    if execute_action.await_count:
        issues.append("invalid request-time security config reached workload")
    if control_plane_http.runner.launch_child_task.await_count:
        issues.append("invalid request-time security config launched a child task")
    if control_plane_http.capability_pack.apply_worker_plan.await_count:
        issues.append("invalid request-time security config applied a worker plan")
    _fail_if_issues("F151_REQUEST_RUNTIME_503_MISSING", issues)


@pytest.mark.asyncio
async def test_graph_runtime_disappears_after_preflight_fails_existing_task_once_without_inline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stores = await create_store_group(
        str(tmp_path / "gateway.db"),
        str(tmp_path / "artifacts"),
    )
    sse_hub = SSEHub()
    task_service = TaskService(stores, sse_hub, storage_only=True)
    task_id, created = await task_service.create_task(
        NormalizedMessage(text="graph race", idempotency_key="t066-graph-race")
    )
    orchestrator = OrchestratorService(
        stores,
        sse_hub,
        runtime_service_fixture(AsyncMock()).bundle,
        worker_runtime_config=WorkerRuntimeConfig(docker_mode="disabled"),
        project_root=tmp_path,
    )
    worker_runtime = orchestrator._workers["llm_generation"]._runtime
    graph_execute = AsyncMock(
        side_effect=WorkerBackendUnavailableError("graph disappeared after preflight")
    )
    inline_execute = AsyncMock()
    monkeypatch.setattr(worker_runtime._graph_backend, "execute", graph_execute)
    monkeypatch.setattr(worker_runtime._inline_backend, "execute", inline_execute)
    preflight_graph_runtime()
    envelope = DispatchEnvelope(
        dispatch_id="dispatch-t066-graph-race",
        task_id=task_id,
        trace_id=f"trace-{task_id}",
        contract_version="1.0",
        route_reason="graph_preflight_succeeded",
        worker_capability="llm_generation",
        hop_count=1,
        max_hops=3,
        user_text="graph race",
        model_alias="main",
        metadata={"target_kind": "graph_agent"},
    )
    try:
        result = await orchestrator.dispatch_prepared(envelope)
        task = await task_service.get_task(task_id)
        events = await stores.event_store.get_events_for_task(task_id)
        failed_events = [
            event
            for event in events
            if event.type == EventType.STATE_TRANSITION
            and event.payload.get("to_status") == TaskStatus.FAILED.value
        ]
        assert created is True
        assert result.status == TaskStatus.FAILED
        assert result.error_type == "WorkerBackendUnavailableError"
        assert task is not None and task.status == TaskStatus.FAILED
        assert len(failed_events) == 1
        assert graph_execute.await_count == 1
        assert inline_execute.await_count == 0
    finally:
        await stores.close()
