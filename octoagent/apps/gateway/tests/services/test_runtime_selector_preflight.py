"""F151 T060：runtime selector 精确值域与四域分离合同。"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from octoagent.core.models import (
    ActionRequestEnvelope,
    AgentProfile,
    ControlPlaneActor,
    ControlPlaneSurface,
    DelegationTargetKind,
    ExecutionBackend,
    RuntimeKind,
    Task,
    Work,
    WorkerDispatchState,
    WorkerResult,
)
from octoagent.core.models.task import RequesterInfo
from octoagent.gateway.services.control_plane import worker_service as worker_service_module
from octoagent.gateway.services.control_plane._base import (
    ControlPlaneActionError,
    ControlPlaneContext,
)
from octoagent.gateway.services.control_plane.work_service import WorkDomainService
from octoagent.gateway.services.control_plane.worker_service import (
    WorkerProfileDomainService,
)
from octoagent.gateway.services.worker_runtime import WorkerBackendUnavailableError
from ulid import ULID

_DELEGATION_TARGETS = (
    "worker",
    "subagent",
    "acp_runtime",
    "graph_agent",
    "fallback",
)
_INVALID_TARGETS: tuple[Any, ...] = (
    "",
    " worker",
    "worker ",
    "Worker",
    "docker",
    None,
    1,
    True,
    [],
    {},
)


def _request(action_id: str, params: dict[str, Any]) -> ActionRequestEnvelope:
    return ActionRequestEnvelope(
        request_id=str(ULID()),
        action_id=action_id,
        surface=ControlPlaneSurface.WEB,
        actor=ControlPlaneActor(actor_id="user:web", actor_label="Owner"),
        params=params,
    )


def _parent_task() -> Task:
    now = datetime.now(tz=UTC)
    return Task(
        task_id="parent-task",
        created_at=now,
        updated_at=now,
        title="parent",
        thread_id="parent-thread",
        scope_id="project-1",
        requester=RequesterInfo(channel="web", sender_id="owner"),
    )


def _parent_work() -> Work:
    return Work(work_id="parent-work", task_id="parent-task", title="parent")


def _context(tmp_path, **overrides: Any) -> ControlPlaneContext:
    values: dict[str, Any] = {
        "project_root": tmp_path,
        "store_group": SimpleNamespace(
            task_store=SimpleNamespace(get_task=AsyncMock(return_value=_parent_task()))
        ),
    }
    values.update(overrides)
    return ControlPlaneContext(**values)


def _record_invalid_result(
    issues: list[str],
    *,
    label: str,
    error: BaseException | None,
) -> None:
    if not isinstance(error, ControlPlaneActionError):
        issues.append(f"{label}: invalid selector was accepted")
    elif error.code != "WORKER_RUNTIME_SELECTOR_UNSUPPORTED":
        issues.append(f"{label}: unexpected error code {error.code}")


async def _call_control_plane(handler, request: ActionRequestEnvelope) -> BaseException | None:
    try:
        await handler(request)
    except BaseException as exc:  # noqa: BLE001 - 测试需把错误映射成唯一 RED oracle
        return exc
    return None


def _fail_if_issues(oracle: str, issues: list[str]) -> None:
    if issues:
        pytest.fail(f"{oracle}: {'; '.join(issues)}", pytrace=False)


@pytest.mark.asyncio
async def test_work_split_accepts_only_exact_delegation_target_values_without_coercion(
    tmp_path, monkeypatch
) -> None:
    runner = SimpleNamespace(launch_child_task=AsyncMock(return_value=("child", True)))
    service = WorkDomainService(_context(tmp_path, task_runner=runner))
    monkeypatch.setattr(service, "_get_work_in_scope", AsyncMock(return_value=_parent_work()))
    issues: list[str] = []

    for target in (*_DELEGATION_TARGETS, None):
        params = {"work_id": "parent-work", "objectives": ["child"]}
        if target is not None:
            params["target_kind"] = target
        result = await service._handle_work_split(_request("work.split", params))
        expected = target or "subagent"
        message = runner.launch_child_task.await_args.args[0]
        if result.data["target_kind"] != expected:
            issues.append(f"{target!r}: result target drifted")
        if message.control_metadata["target_kind"] != expected:
            issues.append(f"{target!r}: dispatch target drifted")

    for target in _INVALID_TARGETS:
        params = {
            "work_id": "parent-work",
            "objectives": ["child"],
            "target_kind": target,
            "worker_type": "dev",
        }
        before = runner.launch_child_task.await_count
        error = await _call_control_plane(
            service._handle_work_split,
            _request("work.split", params),
        )
        _record_invalid_result(issues, label=repr(target), error=error)
        if runner.launch_child_task.await_count != before:
            issues.append(f"{target!r}: child launch occurred")

    _fail_if_issues("F151_WORK_SPLIT_STRICT_SELECTOR_MISSING", issues)


def _worker_profile() -> AgentProfile:
    return AgentProfile(
        profile_id="worker-profile",
        name="Worker",
        kind="worker",
        model_alias="main",
        tool_profile="standard",
        active_revision=1,
    )


@pytest.mark.asyncio
async def test_spawn_from_profile_accepts_only_exact_delegation_target_values_without_coercion(
    tmp_path, monkeypatch
) -> None:
    runner = SimpleNamespace(launch_child_task=AsyncMock(return_value=("child", True)))
    service = WorkerProfileDomainService(_context(tmp_path, task_runner=runner))
    monkeypatch.setattr(
        service, "_get_worker_profile_in_scope", AsyncMock(return_value=_worker_profile())
    )
    monkeypatch.setattr(
        service, "_resolve_selection", AsyncMock(return_value=(None, None, None, None))
    )
    issues: list[str] = []

    for target in (*_DELEGATION_TARGETS, None):
        params = {"profile_id": "worker-profile", "objective": "child"}
        if target is not None:
            params["target_kind"] = target
        await service._handle_worker_spawn_from_profile(
            _request("worker.spawn_from_profile", params)
        )
        expected = target or "worker"
        message = runner.launch_child_task.await_args.args[0]
        if message.control_metadata["target_kind"] != expected:
            issues.append(f"{target!r}: dispatch target drifted")

    for target in _INVALID_TARGETS:
        before = runner.launch_child_task.await_count
        error = await _call_control_plane(
            service._handle_worker_spawn_from_profile,
            _request(
                "worker.spawn_from_profile",
                {
                    "profile_id": "worker-profile",
                    "objective": "child",
                    "target_kind": target,
                },
            ),
        )
        _record_invalid_result(issues, label=repr(target), error=error)
        if runner.launch_child_task.await_count != before:
            issues.append(f"{target!r}: child launch occurred")

    _fail_if_issues("F151_SPAWN_PROFILE_STRICT_SELECTOR_MISSING", issues)


async def _spawn_service(tmp_path, monkeypatch, *, task_runner) -> WorkerProfileDomainService:
    service = WorkerProfileDomainService(_context(tmp_path, task_runner=task_runner))
    monkeypatch.setattr(
        service, "_get_worker_profile_in_scope", AsyncMock(return_value=_worker_profile())
    )
    monkeypatch.setattr(
        service, "_resolve_selection", AsyncMock(return_value=(None, None, None, None))
    )
    return service


@pytest.mark.asyncio
async def test_spawn_from_profile_requires_task_runner_before_task_creation(
    tmp_path, monkeypatch
) -> None:
    create_task = AsyncMock(return_value=("fallback", True))
    task_service_factory = Mock(return_value=SimpleNamespace(create_task=create_task))
    monkeypatch.setattr(
        worker_service_module,
        "TaskService",
        task_service_factory,
        raising=False,
    )
    runner = SimpleNamespace(launch_child_task=AsyncMock(return_value=("child", True)))
    available = await _spawn_service(tmp_path, monkeypatch, task_runner=runner)
    await available._handle_worker_spawn_from_profile(
        _request("worker.spawn_from_profile", {"profile_id": "worker-profile", "objective": "ok"})
    )

    unavailable = await _spawn_service(tmp_path, monkeypatch, task_runner=None)
    error = await _call_control_plane(
        unavailable._handle_worker_spawn_from_profile,
        _request(
            "worker.spawn_from_profile",
            {"profile_id": "worker-profile", "objective": "must reject"},
        ),
    )
    issues: list[str] = []
    if not isinstance(error, ControlPlaneActionError):
        issues.append("missing TaskRunner was accepted")
    elif error.code != "WORKER_RUNTIME_UNAVAILABLE":
        issues.append(f"missing TaskRunner returned {error.code}")
    if create_task.await_count:
        issues.append("missing TaskRunner reached TaskService.create_task")
    if task_service_factory.call_count:
        issues.append("missing TaskRunner constructed fallback TaskService")
    if runner.launch_child_task.await_count != 1:
        issues.append("available TaskRunner control did not launch exactly once")
    _fail_if_issues("F151_TASK_RUNNER_PREFLIGHT_MISSING", issues)


@pytest.mark.asyncio
async def test_spawn_from_profile_requires_graph_runtime_before_task_creation(
    tmp_path, monkeypatch
) -> None:
    runner = SimpleNamespace(launch_child_task=AsyncMock(return_value=("child", True)))
    service = await _spawn_service(tmp_path, monkeypatch, task_runner=runner)
    preflight = Mock()
    monkeypatch.setattr(
        worker_service_module,
        "preflight_graph_runtime",
        preflight,
        raising=False,
    )
    graph_request = {
        "profile_id": "worker-profile",
        "objective": "graph child",
        "target_kind": "graph_agent",
    }
    await service._handle_worker_spawn_from_profile(
        _request("worker.spawn_from_profile", graph_request)
    )
    positive_launches = runner.launch_child_task.await_count
    runner.launch_child_task.reset_mock()
    preflight.reset_mock(side_effect=True)
    preflight.side_effect = WorkerBackendUnavailableError("graph unavailable")
    error = await _call_control_plane(
        service._handle_worker_spawn_from_profile,
        _request("worker.spawn_from_profile", graph_request),
    )
    issues: list[str] = []
    if positive_launches != 1:
        issues.append("available Graph runtime control did not launch exactly once")
    if not isinstance(error, ControlPlaneActionError):
        issues.append("unavailable Graph runtime was accepted")
    elif error.code != "WORKER_RUNTIME_UNAVAILABLE":
        issues.append(f"unavailable Graph runtime returned {error.code}")
    if runner.launch_child_task.await_count:
        issues.append("Graph preflight failure reached child creation")
    if preflight.call_count != 1:
        issues.append("Graph preflight was not called exactly once")
    _fail_if_issues("F151_GRAPH_PREFLIGHT_MISSING", issues)


@pytest.mark.asyncio
async def test_worker_apply_accepts_only_exact_assignment_target_values_without_coercion(
    tmp_path, monkeypatch
) -> None:
    apply = AsyncMock(return_value={"status": "applied"})
    pack = SimpleNamespace(apply_worker_plan=apply)
    service = WorkDomainService(
        _context(
            tmp_path,
            capability_pack_service=pack,
            task_runner=SimpleNamespace(),
        )
    )
    monkeypatch.setattr(service, "_get_work_in_scope", AsyncMock(return_value=_parent_work()))
    issues: list[str] = []

    for target in (*_DELEGATION_TARGETS, None):
        assignment: dict[str, Any] = {"objective": "child"}
        if target is not None:
            assignment["target_kind"] = target
        await service._handle_worker_apply(
            _request(
                "worker.apply",
                {"work_id": "parent-work", "plan": {"assignments": [assignment]}},
            )
        )
        normalized = apply.await_args.kwargs["plan"]["assignments"][0]
        if normalized.get("target_kind") != (target or "subagent"):
            issues.append(f"{target!r}: assignment default/exact value drifted")

    for target in _INVALID_TARGETS:
        before = apply.await_count
        error = await _call_control_plane(
            service._handle_worker_apply,
            _request(
                "worker.apply",
                {
                    "work_id": "parent-work",
                    "plan": {
                        "assignments": [
                            {
                                "objective": "child",
                                "target_kind": target,
                                "worker_type": "dev",
                            }
                        ]
                    },
                },
            ),
        )
        _record_invalid_result(issues, label=repr(target), error=error)
        if apply.await_count != before:
            issues.append(f"{target!r}: plan apply occurred")

    _fail_if_issues("F151_WORKER_APPLY_STRICT_SELECTOR_MISSING", issues)


@pytest.mark.asyncio
async def test_worker_apply_preflights_complete_batch_before_descendant_cancel_or_writes(
    tmp_path, monkeypatch
) -> None:
    apply = AsyncMock(return_value={"status": "applied"})
    cancel_task = AsyncMock()
    cancel_work = AsyncMock()
    pack = SimpleNamespace(apply_worker_plan=apply)
    service = WorkDomainService(
        _context(
            tmp_path,
            capability_pack_service=pack,
            task_runner=SimpleNamespace(cancel_task=cancel_task),
            delegation_plane_service=SimpleNamespace(cancel_work=cancel_work),
        )
    )
    monkeypatch.setattr(service, "_get_work_in_scope", AsyncMock(return_value=_parent_work()))
    issues: list[str] = []

    valid_plan = {
        "assignments": [
            {"objective": "first", "target_kind": "subagent"},
            {"objective": "second", "target_kind": "worker"},
        ]
    }
    await service._handle_worker_apply(
        _request("worker.apply", {"work_id": "parent-work", "plan": valid_plan})
    )
    if apply.await_count != 1:
        issues.append("valid batch did not reach apply exactly once")
    apply.reset_mock()

    invalid_plan = {
        "assignments": [
            {"objective": "first", "target_kind": "subagent"},
            {"objective": "second", "target_kind": "docker", "worker_type": "dev"},
        ]
    }
    error = await _call_control_plane(
        service._handle_worker_apply,
        _request("worker.apply", {"work_id": "parent-work", "plan": invalid_plan}),
    )
    _record_invalid_result(issues, label="mixed invalid batch", error=error)

    unavailable = WorkDomainService(
        _context(tmp_path, capability_pack_service=pack, task_runner=None)
    )
    monkeypatch.setattr(unavailable, "_get_work_in_scope", AsyncMock(return_value=_parent_work()))
    graph_plan = {
        "assignments": [
            {"objective": "first", "target_kind": "subagent"},
            {"objective": "second", "target_kind": "graph_agent"},
        ]
    }
    error = await _call_control_plane(
        unavailable._handle_worker_apply,
        _request("worker.apply", {"work_id": "parent-work", "plan": graph_plan}),
    )
    if not isinstance(error, ControlPlaneActionError):
        issues.append("unavailable batch was accepted")
    elif error.code != "WORKER_RUNTIME_UNAVAILABLE":
        issues.append(f"unavailable batch returned {error.code}")

    if apply.await_count or cancel_task.await_count or cancel_work.await_count:
        issues.append("failed preflight reached apply/cancel side effects")
    _fail_if_issues("F151_WORKER_APPLY_BATCH_PREFLIGHT_MISSING", issues)


def test_delegation_profile_worker_backend_and_projection_use_distinct_value_domains() -> None:
    issues: list[str] = []
    if {item.value for item in DelegationTargetKind} != set(_DELEGATION_TARGETS):
        issues.append("delegation target domain drifted")
    if {item.value for item in RuntimeKind} != {
        "worker",
        "subagent",
        "acp_runtime",
        "graph_agent",
    }:
        issues.append("profile capability domain drifted")
    if {item.value for item in ExecutionBackend} != {"inline", "docker"}:
        issues.append("persisted projection domain drifted")

    for model in (WorkerDispatchState, WorkerResult):
        backend_schema = model.model_json_schema()["properties"]["backend"]
        if set(backend_schema.get("enum", [])) != {"graph", "inline"}:
            issues.append(f"{model.__name__}: transient backend is not graph|inline")

    _fail_if_issues("F151_RUNTIME_VALUE_DOMAIN_SEPARATION_MISSING", issues)
