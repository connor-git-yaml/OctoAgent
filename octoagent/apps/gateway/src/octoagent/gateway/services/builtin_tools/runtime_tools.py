"""runtime / project / artifact / automation 工具模块。"""

from __future__ import annotations

import json
import platform
import socket
from typing import Any

from octoagent.gateway.services.control_plane.automation_store import AutomationStore
from octoagent.tooling import SideEffectLevel, reflect_tool_schema, tool_contract

from ._deps import ToolDeps


async def register(broker: Any, deps: ToolDeps) -> None:
    """注册 project / artifact / runtime / automation 工具组。"""

    @tool_contract(
        name="project.inspect",
        side_effect_level=SideEffectLevel.NONE,
        tool_group="project",
        tags=["project", "workspace", "context"],
        manifest_ref="builtin://project.inspect",
        metadata={
            "entrypoints": ["agent_runtime", "web"],
            "runtime_kinds": ["worker", "subagent", "graph_agent"],
        },
    )
    async def project_inspect(project_id: str | None = None) -> str:
        """读取当前或指定 project/workspace 摘要。"""

        project, workspace = await deps._pack_service._resolve_project_context(
            project_id=project_id or ""
        )
        payload = {
            "project": None if project is None else project.model_dump(mode="json"),
            "workspace": None if workspace is None else workspace.model_dump(mode="json"),
        }
        return json.dumps(payload, ensure_ascii=False)

    @tool_contract(
        name="artifact.list",
        side_effect_level=SideEffectLevel.NONE,
        tool_group="artifact",
        tags=["artifact", "history", "output"],
        manifest_ref="builtin://artifact.list",
        metadata={
            "entrypoints": ["agent_runtime"],
            "runtime_kinds": ["worker", "subagent", "graph_agent"],
        },
    )
    async def artifact_list(task_id: str) -> str:
        """列出任务下的 artifact 摘要。"""

        artifacts = await deps.stores.artifact_store.list_artifacts_for_task(task_id)
        return json.dumps(
            {
                "task_id": task_id,
                "artifacts": [item.model_dump(mode="json") for item in artifacts],
            },
            ensure_ascii=False,
        )

    @tool_contract(
        name="runtime.inspect",
        side_effect_level=SideEffectLevel.NONE,
        tool_group="runtime",
        tags=["runtime", "diagnostics", "health"],
        manifest_ref="builtin://runtime.inspect",
        metadata={
            "entrypoints": ["agent_runtime", "web"],
            "runtime_kinds": ["worker", "acp_runtime"],
        },
    )
    async def runtime_inspect() -> str:
        """返回 runtime / queue / pipeline 摘要。"""

        works = await deps.stores.work_store.list_works()
        pipeline_runs = await deps.stores.work_store.list_pipeline_runs()
        tasks = await deps.stores.task_store.list_tasks()
        return json.dumps(
            {
                "task_count": len(tasks),
                "work_count": len(works),
                "pipeline_run_count": len(pipeline_runs),
                "pipeline_run_source": "delegation_plane",
                "graph_runtime_projection": "execution_console_only",
                "capability_backend": deps.tool_index.backend_name,
            },
            ensure_ascii=False,
        )

    @tool_contract(
        name="gateway.inspect",
        side_effect_level=SideEffectLevel.NONE,
        tool_group="runtime",
        tags=["gateway", "inspect", "metrics"],
        manifest_ref="builtin://gateway.inspect",
        metadata={
            "entrypoints": ["agent_runtime", "web"],
            "runtime_kinds": ["worker", "acp_runtime"],
        },
    )
    async def gateway_inspect() -> str:
        """读取 gateway / capability / queue 摘要。"""

        jobs = await deps.stores.task_job_store.list_jobs(
            ["QUEUED", "RUNNING", "WAITING_INPUT"]
        )
        return json.dumps(
            {
                "project_root": str(deps.project_root),
                "queued_jobs": len([item for item in jobs if item.status == "QUEUED"]),
                "running_jobs": len([item for item in jobs if item.status == "RUNNING"]),
                "deferred_jobs": len(
                    [
                        item
                        for item in jobs
                        if item.status in {"WAITING_INPUT", "WAITING_APPROVAL", "PAUSED"}
                    ]
                ),
                "tool_index_backend": deps.tool_index.backend_name,
                "capability_snapshot": deps._pack_service.capability_snapshot(),
            },
            ensure_ascii=False,
        )

    @tool_contract(
        name="nodes.list",
        side_effect_level=SideEffectLevel.NONE,
        tool_group="runtime",
        tags=["nodes", "runtime", "host"],
        manifest_ref="builtin://nodes.list",
        metadata={
            "entrypoints": ["agent_runtime", "web"],
            "runtime_kinds": ["worker", "acp_runtime"],
        },
    )
    async def nodes_list() -> str:
        """列出当前可见 runtime node。"""

        return json.dumps(
            {
                "nodes": [
                    {
                        "node_id": socket.gethostname(),
                        "role": "local-primary",
                        "platform": platform.platform(),
                        "python_version": platform.python_version(),
                        "project_root": str(deps.project_root),
                    }
                ]
            },
            ensure_ascii=False,
        )

    @tool_contract(
        name="cron.list",
        side_effect_level=SideEffectLevel.NONE,
        tool_group="automation",
        tags=["cron", "automation", "scheduler"],
        manifest_ref="builtin://cron.list",
        metadata={
            "entrypoints": ["agent_runtime", "web"],
            "runtime_kinds": ["worker", "acp_runtime"],
        },
    )
    async def cron_list(limit: int = 20) -> str:
        """列出当前 automation jobs。"""

        jobs = AutomationStore(deps.project_root).list_jobs()[: max(1, min(limit, 100))]
        return json.dumps(
            {"jobs": [item.model_dump(mode="json") for item in jobs]},
            ensure_ascii=False,
        )

    for handler in (
        project_inspect,
        artifact_list,
        runtime_inspect,
        gateway_inspect,
        nodes_list,
        cron_list,
    ):
        await broker.try_register(reflect_tool_schema(handler), handler)
