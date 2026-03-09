"""Feature 032: built-in tool suite / child runtime 集成测试。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from octoagent.core.models import (
    NormalizedMessage,
    OrchestratorRequest,
    Project,
    ProjectSelectorState,
    Workspace,
)
from octoagent.core.store import create_store_group
from octoagent.gateway.services.capability_pack import CapabilityPackService
from octoagent.gateway.services.delegation_plane import DelegationPlaneService
from octoagent.gateway.services.execution_context import (
    ExecutionRuntimeContext,
    bind_execution_context,
)
from octoagent.gateway.services.llm_service import LLMService
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.gateway.services.task_runner import TaskRunner
from octoagent.gateway.services.task_service import TaskService
from octoagent.tooling import ExecutionContext, ToolBroker, ToolProfile


async def _build_runtime_services(tmp_path: Path):
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
            source="tests",
        )
    )
    await store_group.conn.commit()

    sse_hub = SSEHub()
    tool_broker = ToolBroker(event_store=store_group.event_store)
    capability_pack = CapabilityPackService(
        project_root=tmp_path,
        store_group=store_group,
        tool_broker=tool_broker,
    )
    delegation_plane = DelegationPlaneService(
        project_root=tmp_path,
        store_group=store_group,
        sse_hub=sse_hub,
        capability_pack=capability_pack,
    )
    task_runner = TaskRunner(
        store_group=store_group,
        sse_hub=sse_hub,
        llm_service=LLMService(),
        delegation_plane=delegation_plane,
    )
    capability_pack.bind_delegation_plane(delegation_plane)
    capability_pack.bind_task_runner(task_runner)
    await capability_pack.startup()
    await task_runner.startup()
    task_service = TaskService(store_group, sse_hub)
    return (
        store_group,
        sse_hub,
        task_service,
        capability_pack,
        delegation_plane,
        task_runner,
        tool_broker,
    )


async def test_capability_pack_exposes_builtin_tool_catalog_and_availability(
    tmp_path: Path,
) -> None:
    (
        store_group,
        _sse_hub,
        _task_service,
        capability_pack,
        _delegation_plane,
        task_runner,
        _tool_broker,
    ) = await _build_runtime_services(tmp_path)

    try:
        pack = await capability_pack.get_pack()
        tool_names = {item.tool_name for item in pack.tools}

        assert len(pack.tools) >= 15
        assert {
            "project.inspect",
            "subagents.spawn",
            "work.split",
            "work.merge",
            "web.fetch",
            "memory.search",
        }.issubset(tool_names)

        spawn_tool = next(item for item in pack.tools if item.tool_name == "subagents.spawn")
        assert spawn_tool.availability.value == "available"
        assert "agent_runtime" in spawn_tool.entrypoints

        tts_tool = next(item for item in pack.tools if item.tool_name == "tts.speak")
        assert tts_tool.availability.value in {"available", "install_required"}
    finally:
        await task_runner.shutdown()
        await store_group.conn.close()


async def test_work_split_tool_creates_real_child_tasks_and_canvas_artifact(
    tmp_path: Path,
) -> None:
    (
        store_group,
        _sse_hub,
        task_service,
        _capability_pack,
        delegation_plane,
        task_runner,
        tool_broker,
    ) = await _build_runtime_services(tmp_path)

    try:
        task_id, created = await task_service.create_task(
            NormalizedMessage(
                text="请拆分这项工作",
                idempotency_key="feature-032-work-split",
            )
        )
        assert created is True

        plan = await delegation_plane.prepare_dispatch(
            OrchestratorRequest(
                task_id=task_id,
                trace_id=f"trace-{task_id}",
                user_text="请拆分这项工作",
                worker_capability="llm_generation",
                metadata={},
            )
        )
        assert plan.dispatch_envelope is not None

        runtime_context = ExecutionRuntimeContext(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            session_id="session-032",
            worker_id="worker.test",
            backend="inline",
            console=task_runner.execution_console,
            work_id=plan.work.work_id,
            runtime_kind="subagent",
        )
        broker_context = ExecutionContext(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            caller="worker:test",
            profile=ToolProfile.STANDARD,
        )

        with bind_execution_context(runtime_context):
            split_result = await tool_broker.execute(
                "work.split",
                {
                    "objectives": ["先调研当前 API", "再补一组测试"],
                    "worker_type": "research",
                    "target_kind": "subagent",
                },
                broker_context,
            )
            canvas_result = await tool_broker.execute(
                "canvas.write",
                {
                    "name": "split-summary.md",
                    "content": "# child plan\n- 调研\n- 测试\n",
                },
                broker_context,
            )

        assert split_result.is_error is False
        split_payload = json.loads(split_result.output)
        assert split_payload["requested"] == 2
        assert len(split_payload["children"]) == 2

        assert canvas_result.is_error is False
        canvas_payload = json.loads(canvas_result.output)
        artifact = await store_group.artifact_store.get_artifact(canvas_payload["artifact_id"])
        assert artifact is not None
        assert artifact.name == "split-summary.md"

        child_works = []
        for _ in range(30):
            child_works = await store_group.work_store.list_works(parent_work_id=plan.work.work_id)
            if len(child_works) >= 2:
                break
            await asyncio.sleep(0.05)

        assert len(child_works) == 2
        assert {item.parent_work_id for item in child_works} == {plan.work.work_id}
        assert {item.selected_worker_type.value for item in child_works} == {"research"}
        assert {item.target_kind.value for item in child_works} == {"subagent"}
    finally:
        await task_runner.shutdown()
        await store_group.conn.close()


async def test_subagents_spawn_uses_objective_as_child_prompt_when_title_is_provided(
    tmp_path: Path,
) -> None:
    (
        store_group,
        _sse_hub,
        task_service,
        _capability_pack,
        delegation_plane,
        task_runner,
        tool_broker,
    ) = await _build_runtime_services(tmp_path)

    try:
        task_id, created = await task_service.create_task(
            NormalizedMessage(
                text="请启动子代理",
                idempotency_key="feature-032-subagents-spawn-title",
            )
        )
        assert created is True

        plan = await delegation_plane.prepare_dispatch(
            OrchestratorRequest(
                task_id=task_id,
                trace_id=f"trace-{task_id}",
                user_text="请启动子代理",
                worker_capability="llm_generation",
                metadata={},
            )
        )
        assert plan.dispatch_envelope is not None

        runtime_context = ExecutionRuntimeContext(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            session_id="session-032-spawn",
            worker_id="worker.test",
            backend="inline",
            console=task_runner.execution_console,
            work_id=plan.work.work_id,
            runtime_kind="subagent",
        )
        broker_context = ExecutionContext(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            caller="worker:test",
            profile=ToolProfile.STANDARD,
        )

        with bind_execution_context(runtime_context):
            spawn_result = await tool_broker.execute(
                "subagents.spawn",
                {
                    "objective": "请先读取 API 现状，再输出研究摘要",
                    "title": "研究子任务",
                    "worker_type": "research",
                    "target_kind": "subagent",
                },
                broker_context,
            )

        assert spawn_result.is_error is False
        payload = json.loads(spawn_result.output)
        assert payload["objective"] == "请先读取 API 现状，再输出研究摘要"
        assert payload["title"] == "研究子任务"

        events = await store_group.event_store.get_events_for_task(payload["task_id"])
        user_event = next(event for event in events if event.type.value == "USER_MESSAGE")
        assert user_event.payload["text_preview"] == "请先读取 API 现状，再输出研究摘要"
        assert user_event.payload["metadata"]["child_title"] == "研究子任务"
    finally:
        await task_runner.shutdown()
        await store_group.conn.close()
