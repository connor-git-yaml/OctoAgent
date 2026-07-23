"""F151 T067：Execution Console 只投影 inline 执行事实。"""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from octoagent.core.models import (
    ActorType,
    EventType,
    ExecutionBackend,
    ExecutionSessionState,
    HumanInputPolicy,
)
from octoagent.core.models.message import NormalizedMessage
from octoagent.core.models.payloads import ExecutionStatusChangedPayload
from octoagent.core.store import create_store_group
from octoagent.gateway.services.execution_console import (
    ExecutionConsoleService,
    ExecutionInputError,
)
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.gateway.services.task_service import TaskService


@pytest_asyncio.fixture
async def console_fixture(tmp_path: Path) -> AsyncIterator[tuple]:
    stores = await create_store_group(
        str(tmp_path / "gateway.db"),
        str(tmp_path / "artifacts"),
    )
    sse_hub = SSEHub()
    task_service = TaskService(stores, sse_hub, storage_only=True)
    console = ExecutionConsoleService(stores, sse_hub)
    try:
        yield stores, task_service, console
    finally:
        await stores.close()


async def _create_task(task_service: TaskService, key: str) -> str:
    task_id, created = await task_service.create_task(
        NormalizedMessage(text=key, idempotency_key=key)
    )
    assert created is True
    return task_id


def _fail_if_issues(oracle: str, issues: list[str]) -> None:
    if issues:
        pytest.fail(f"{oracle}: {'; '.join(issues)}", pytrace=False)


@pytest.mark.asyncio
async def test_register_session_has_no_backend_parameter_and_writes_inline(
    console_fixture: tuple,
) -> None:
    stores, task_service, console = console_fixture
    task_id = await _create_task(task_service, "t067-register")
    issues: list[str] = []
    if "backend" in inspect.signature(console.register_session).parameters:
        issues.append("register_session still exposes backend selection")
    session = await console.register_session(
        task_id=task_id,
        session_id="session-t067-register",
        backend_job_id="job-t067-register",
        interactive=True,
        input_policy=HumanInputPolicy.EXPLICIT_REQUEST_ONLY,
    )
    events = await stores.event_store.get_events_for_task(task_id)
    status_payload = ExecutionStatusChangedPayload.model_validate(events[-1].payload)
    if session.backend != ExecutionBackend.INLINE:
        issues.append(f"session backend={session.backend}, expected inline")
    if status_payload.backend != ExecutionBackend.INLINE.value:
        issues.append(f"event backend={status_payload.backend!r}, expected inline")
    _fail_if_issues("F151_NEW_EXECUTION_INLINE_ONLY_MISSING", issues)


@pytest.mark.asyncio
async def test_unknown_historical_backend_returns_stable_projection_error(
    console_fixture: tuple,
) -> None:
    _, task_service, console = console_fixture
    task_id = await _create_task(task_service, "t067-unknown")
    await task_service.append_structured_event(
        task_id=task_id,
        event_type=EventType.EXECUTION_STATUS_CHANGED,
        actor=ActorType.WORKER,
        payload=ExecutionStatusChangedPayload(
            session_id="session-t067-unknown",
            backend="future-container",
            backend_job_id="job-t067-unknown",
            status=ExecutionSessionState.RUNNING,
        ).model_dump(mode="json"),
    )
    issues: list[str] = []
    try:
        await console.get_session(task_id)
    except ExecutionInputError as exc:
        if exc.code != "EXECUTION_BACKEND_UNKNOWN":
            issues.append(f"error code={exc.code!r}, expected EXECUTION_BACKEND_UNKNOWN")
    except Exception as exc:  # 当前缺口：原始枚举ValueError泄漏
        issues.append(f"raw {type(exc).__name__} escaped projection boundary")
    else:
        issues.append("unknown historical backend was accepted")
    _fail_if_issues("F151_UNKNOWN_BACKEND_ERROR_MISSING", issues)


@pytest.mark.asyncio
async def test_graph_runtime_projects_inline_with_runtime_kind_metadata(
    console_fixture: tuple,
) -> None:
    _, task_service, console = console_fixture
    task_id = await _create_task(task_service, "t067-graph")
    session = await console.register_session(
        task_id=task_id,
        session_id="session-t067-graph",
        backend_job_id="job-t067-graph",
        interactive=True,
        input_policy=HumanInputPolicy.EXPLICIT_REQUEST_ONLY,
        metadata={"runtime_kind": "graph_agent"},
    )
    issues: list[str] = []
    if session.backend != ExecutionBackend.INLINE:
        issues.append(f"session backend={session.backend}, expected inline")
    if session.metadata.get("runtime_kind") != "graph_agent":
        issues.append("graph runtime_kind metadata was not preserved")
    projected = await console.get_session(task_id)
    if projected is None or projected.backend != ExecutionBackend.INLINE:
        issues.append("graph execution did not project as inline")
    _fail_if_issues("F151_GRAPH_PROJECTION_CONTRACT_MISSING", issues)
