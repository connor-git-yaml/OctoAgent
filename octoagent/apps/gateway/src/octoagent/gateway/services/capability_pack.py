"""Feature 030: bundled capability pack / ToolIndex / bootstrap。"""

from __future__ import annotations

import html
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from octoagent.core.models import (
    BuiltinToolAvailabilityStatus,
    BundledCapabilityPack,
    BundledSkillDefinition,
    BundledToolDefinition,
    DynamicToolSelection,
    NormalizedMessage,
    RuntimeKind,
    ToolIndexQuery,
    WorkerBootstrapFile,
    WorkerCapabilityProfile,
    WorkerType,
)
from octoagent.memory import MemoryLayer, MemoryPartition
from octoagent.provider.dx.automation_store import AutomationStore
from octoagent.provider.dx.memory_console_service import MemoryConsoleService
from octoagent.skills import SkillManifest, SkillRegistry
from octoagent.tooling import (
    SideEffectLevel,
    ToolBroker,
    ToolIndex,
    ToolProfile,
    reflect_tool_schema,
    tool_contract,
)
from pydantic import BaseModel, Field
from ulid import ULID

from .execution_context import get_current_execution_context
from .task_service import TaskService


class _BuiltinSkillInput(BaseModel):
    objective: str = Field(min_length=1)
    context: dict[str, Any] = Field(default_factory=dict)


class _BuiltinSkillOutput(BaseModel):
    content: str = ""
    complete: bool = True
    skip_remaining_tools: bool = True
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CapabilityPackService:
    """统一管理 bundled tools / skills / ToolIndex / worker bootstrap。"""

    def __init__(
        self,
        *,
        project_root: Path,
        store_group,
        tool_broker: ToolBroker,
        skill_registry: SkillRegistry | None = None,
        preferred_tool_index_backend: str = "auto",
    ) -> None:
        self._project_root = project_root
        self._stores = store_group
        self._tool_broker = tool_broker
        self._skill_registry = skill_registry or SkillRegistry()
        self._tool_index = ToolIndex(preferred_backend=preferred_tool_index_backend)
        self._pack: BundledCapabilityPack | None = None
        self._bootstrapped = False
        self._profile_map = self._build_worker_profiles()
        self._bootstrap_templates = self._build_bootstrap_templates()
        self._task_runner = None
        self._delegation_plane = None
        self._memory_console_service = MemoryConsoleService(
            project_root,
            store_group=store_group,
        )

    @property
    def tool_broker(self) -> ToolBroker:
        return self._tool_broker

    @property
    def skill_registry(self) -> SkillRegistry:
        return self._skill_registry

    def bind_task_runner(self, task_runner) -> None:
        self._task_runner = task_runner

    def bind_delegation_plane(self, delegation_plane) -> None:
        self._delegation_plane = delegation_plane

    async def startup(self) -> None:
        if self._bootstrapped:
            return
        await self._register_builtin_tools()
        self._register_builtin_skills()
        await self.refresh()
        self._bootstrapped = True

    async def refresh(self) -> BundledCapabilityPack:
        metas = await self._tool_broker.discover()
        await self._tool_index.rebuild(metas)
        tools = [
            BundledToolDefinition(
                tool_name=meta.name,
                label=meta.name.replace(".", " ").title(),
                description=meta.description,
                tool_group=meta.tool_group,
                tool_profile=meta.tool_profile.value,
                tags=list(meta.tags),
                worker_types=[
                    WorkerType(item)
                    for item in meta.worker_types
                    if item in {member.value for member in WorkerType}
                ],
                manifest_ref=meta.manifest_ref,
                availability=self._resolve_tool_availability(meta.name),
                availability_reason=self._resolve_tool_availability_reason(meta.name),
                install_hint=self._resolve_tool_install_hint(meta.name),
                entrypoints=self._resolve_tool_entrypoints(meta.name),
                runtime_kinds=self._resolve_tool_runtime_kinds(meta.name),
                metadata=dict(meta.metadata),
            )
            for meta in metas
        ]
        skills = [
            BundledSkillDefinition(
                skill_id=manifest.skill_id,
                label=manifest.skill_id.replace("_", " ").title(),
                description=manifest.description or "",
                model_alias=manifest.model_alias,
                worker_types=self._resolve_skill_worker_types(manifest),
                tools_allowed=list(manifest.tools_allowed),
                pipeline_templates=["delegation:preflight"],
                metadata={
                    "tool_profile": manifest.tool_profile.value,
                    **manifest.metadata,
                },
            )
            for manifest in self._skill_registry.list_skills()
        ]
        bootstrap_files = list(self._bootstrap_templates.values())
        fallback_toolset = [
            tool.tool_name
            for tool in tools
            if tool.tool_profile in {"minimal", "standard"}
            and tool.availability
            in {
                BuiltinToolAvailabilityStatus.AVAILABLE,
                BuiltinToolAvailabilityStatus.DEGRADED,
            }
        ][:5]
        self._pack = BundledCapabilityPack(
            skills=skills,
            tools=tools,
            worker_profiles=list(self._profile_map.values()),
            bootstrap_files=bootstrap_files,
            fallback_toolset=fallback_toolset,
            degraded_reason=self._tool_index.degraded_reason,
        )
        return self._pack

    async def get_pack(self) -> BundledCapabilityPack:
        await self.startup()
        if self._pack is None:
            return await self.refresh()
        return self._pack

    def get_worker_profile(self, worker_type: WorkerType) -> WorkerCapabilityProfile:
        return self._profile_map.get(worker_type, self._profile_map[WorkerType.GENERAL])

    async def select_tools(
        self,
        request: ToolIndexQuery,
        *,
        worker_type: WorkerType,
    ) -> DynamicToolSelection:
        await self.startup()
        profile = self.get_worker_profile(worker_type)
        fallback = await self._resolve_fallback_toolset(worker_type)
        effective_request = request.model_copy(
            update={
                "tool_groups": request.tool_groups or profile.default_tool_groups,
                "worker_type": request.worker_type or worker_type,
                "tool_profile": request.tool_profile or profile.default_tool_profile,
            }
        )
        return await self._tool_index.select_tools(
            effective_request,
            static_fallback=fallback,
        )

    async def render_bootstrap_context(
        self,
        *,
        worker_type: WorkerType,
        project_id: str = "",
        workspace_id: str = "",
    ) -> list[dict[str, Any]]:
        await self.startup()
        project, workspace = await self._resolve_project_context(
            project_id=project_id,
            workspace_id=workspace_id,
        )
        replacements = {
            "{{project_id}}": project.project_id if project is not None else "",
            "{{project_slug}}": project.slug if project is not None else "default",
            "{{project_name}}": project.name if project is not None else "Default Project",
            "{{workspace_id}}": workspace.workspace_id if workspace is not None else "",
            "{{workspace_slug}}": workspace.slug if workspace is not None else "primary",
            "{{workspace_root}}": workspace.root_path if workspace is not None else "",
        }
        rendered: list[dict[str, Any]] = []
        for file in self._bootstrap_templates.values():
            if (
                worker_type not in file.applies_to_worker_types
                and WorkerType.GENERAL not in file.applies_to_worker_types
            ):
                continue
            content = file.content
            for source, target in replacements.items():
                content = content.replace(source, target)
            rendered.append(
                {
                    "file_id": file.file_id,
                    "path_hint": file.path_hint,
                    "content": content,
                    "metadata": file.metadata,
                }
            )
        return rendered

    def capability_snapshot(self) -> dict[str, Any]:
        pack = self._pack or BundledCapabilityPack()
        availability_summary: dict[str, int] = {}
        for item in pack.tools:
            key = item.availability.value
            availability_summary[key] = availability_summary.get(key, 0) + 1
        return {
            "backend": self._tool_index.backend_name,
            "degraded_reason": pack.degraded_reason,
            "tool_count": len(pack.tools),
            "tool_availability_summary": availability_summary,
            "worker_profiles": [item.model_dump(mode="json") for item in pack.worker_profiles],
        }

    def build_skill_registry_document(self) -> list[BundledSkillDefinition]:
        if self._pack is None:
            return []
        return list(self._pack.skills)

    async def _register_builtin_tools(self) -> None:
        store_group = self._stores
        task_service = TaskService(store_group)

        async def _current_parent() -> tuple[TaskService, Any, Any]:
            context = get_current_execution_context()
            task = await store_group.task_store.get_task(context.task_id)
            if task is None:
                raise RuntimeError("current task not found for builtin tool")
            return task_service, context, task

        def _coerce_objectives(objectives: list[str] | str) -> list[str]:
            if isinstance(objectives, list):
                return [item.strip() for item in objectives if item and item.strip()]
            return [item.strip() for item in str(objectives).splitlines() if item.strip()]

        async def _launch_child(
            *,
            objective: str,
            worker_type: str,
            target_kind: str,
            title: str = "",
        ) -> dict[str, Any]:
            if self._task_runner is None:
                raise RuntimeError("task runner is not bound for child task launch")
            _, context, parent_task = await _current_parent()
            child_id = str(ULID())
            child_thread_id = f"{parent_task.thread_id}:child:{child_id[:8]}"
            child_message = NormalizedMessage(
                channel=parent_task.requester.channel,
                thread_id=child_thread_id,
                scope_id=parent_task.scope_id,
                sender_id=parent_task.requester.sender_id,
                sender_name=parent_task.requester.sender_id or "owner",
                text=objective,
                metadata={
                    "parent_task_id": parent_task.task_id,
                    "parent_work_id": context.work_id,
                    "requested_worker_type": worker_type,
                    "target_kind": target_kind,
                    "spawned_by": "builtin_tool",
                    "child_title": title,
                },
                idempotency_key=f"builtin-child:{parent_task.task_id}:{child_id}",
            )
            task_id, created = await self._task_runner.launch_child_task(child_message)
            return {
                "task_id": task_id,
                "created": created,
                "thread_id": child_thread_id,
                "target_kind": target_kind,
                "worker_type": worker_type,
                "parent_task_id": parent_task.task_id,
                "parent_work_id": context.work_id,
                "title": title,
                "objective": objective,
            }

        @tool_contract(
            name="project.inspect",
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="project",
            tags=["project", "workspace", "context"],
            worker_types=["ops", "research", "dev", "general"],
            manifest_ref="builtin://project.inspect",
            metadata={
                "entrypoints": ["agent_runtime", "web"],
                "runtime_kinds": ["worker", "subagent", "graph_agent"],
            },
        )
        async def project_inspect(project_id: str | None = None) -> str:
            """读取当前或指定 project/workspace 摘要。"""

            project, workspace = await self._resolve_project_context(project_id=project_id or "")
            payload = {
                "project": None if project is None else project.model_dump(mode="json"),
                "workspace": None if workspace is None else workspace.model_dump(mode="json"),
            }
            return json.dumps(payload, ensure_ascii=False)

        @tool_contract(
            name="task.inspect",
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="session",
            tags=["task", "session", "status"],
            worker_types=["ops", "research", "dev", "general"],
            manifest_ref="builtin://task.inspect",
            metadata={
                "entrypoints": ["agent_runtime"],
                "runtime_kinds": ["worker", "subagent", "graph_agent"],
            },
        )
        async def task_inspect(task_id: str) -> str:
            """读取任务投影与最近 execution 概览。"""

            task = await store_group.task_store.get_task(task_id)
            if task is None:
                return json.dumps({"task_id": task_id, "status": "missing"}, ensure_ascii=False)
            events = await store_group.event_store.get_events_for_task(task_id)
            session = (
                await self._task_runner.get_execution_session(task_id)
                if self._task_runner is not None
                else None
            )
            return json.dumps(
                {
                    "task": task.model_dump(mode="json"),
                    "event_count": len(events),
                    "latest_event_id": events[-1].event_id if events else "",
                    "execution_session": None
                    if session is None
                    else session.model_dump(mode="json"),
                },
                ensure_ascii=False,
            )

        @tool_contract(
            name="artifact.list",
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="artifact",
            tags=["artifact", "history", "output"],
            worker_types=["research", "dev", "general"],
            manifest_ref="builtin://artifact.list",
            metadata={
                "entrypoints": ["agent_runtime"],
                "runtime_kinds": ["worker", "subagent", "graph_agent"],
            },
        )
        async def artifact_list(task_id: str) -> str:
            """列出任务下的 artifact 摘要。"""

            artifacts = await store_group.artifact_store.list_artifacts_for_task(task_id)
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
            tool_profile=ToolProfile.MINIMAL,
            tool_group="runtime",
            tags=["runtime", "diagnostics", "health"],
            worker_types=["ops", "general"],
            manifest_ref="builtin://runtime.inspect",
            metadata={
                "entrypoints": ["agent_runtime", "web"],
                "runtime_kinds": ["worker", "acp_runtime"],
            },
        )
        async def runtime_inspect() -> str:
            """返回 runtime / queue / pipeline 摘要。"""

            works = await store_group.work_store.list_works()
            pipeline_runs = await store_group.work_store.list_pipeline_runs()
            tasks = await store_group.task_store.list_tasks()
            return json.dumps(
                {
                    "task_count": len(tasks),
                    "work_count": len(works),
                    "pipeline_run_count": len(pipeline_runs),
                    "pipeline_run_source": "delegation_plane",
                    "graph_runtime_projection": "execution_console_only",
                    "capability_backend": self._tool_index.backend_name,
                },
                ensure_ascii=False,
            )

        @tool_contract(
            name="work.inspect",
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="delegation",
            tags=["work", "delegation", "ownership"],
            worker_types=["ops", "research", "dev", "general"],
            manifest_ref="builtin://work.inspect",
            metadata={
                "entrypoints": ["agent_runtime", "web"],
                "runtime_kinds": ["worker", "subagent", "graph_agent"],
            },
        )
        async def work_inspect(work_id: str) -> str:
            """读取 work 生命周期与 pipeline 关联。"""

            work = await store_group.work_store.get_work(work_id)
            if work is None:
                return json.dumps({"work_id": work_id, "status": "missing"}, ensure_ascii=False)
            run = (
                await store_group.work_store.get_pipeline_run(work.pipeline_run_id)
                if work.pipeline_run_id
                else None
            )
            children = await store_group.work_store.list_works(parent_work_id=work_id)
            return json.dumps(
                {
                    "work": work.model_dump(mode="json"),
                    "pipeline_run": None if run is None else run.model_dump(mode="json"),
                    "children": [item.model_dump(mode="json") for item in children],
                },
                ensure_ascii=False,
            )

        @tool_contract(
            name="agents.list",
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="session",
            tags=["agents", "workers", "profiles"],
            worker_types=["ops", "research", "dev", "general"],
            manifest_ref="builtin://agents.list",
            metadata={
                "entrypoints": ["agent_runtime", "web"],
                "runtime_kinds": ["worker", "subagent", "graph_agent", "acp_runtime"],
            },
        )
        async def agents_list() -> str:
            """列出内建 agent / worker 能力概览。"""

            pack = await self.get_pack()
            return json.dumps(
                {
                    "worker_profiles": [
                        item.model_dump(mode="json") for item in pack.worker_profiles
                    ],
                    "skills": [item.skill_id for item in pack.skills],
                },
                ensure_ascii=False,
            )

        @tool_contract(
            name="sessions.list",
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="session",
            tags=["sessions", "threads", "tasks"],
            worker_types=["ops", "research", "dev", "general"],
            manifest_ref="builtin://sessions.list",
            metadata={
                "entrypoints": ["agent_runtime", "web"],
                "runtime_kinds": ["worker", "subagent", "graph_agent"],
            },
        )
        async def sessions_list(limit: int = 20, status: str = "") -> str:
            """列出最近 session/task 概览。"""

            tasks = await store_group.task_store.list_tasks(status or None)
            payload = []
            for task in tasks[: max(1, min(limit, 50))]:
                session = (
                    await self._task_runner.get_execution_session(task.task_id)
                    if self._task_runner is not None
                    else None
                )
                payload.append(
                    {
                        "task_id": task.task_id,
                        "thread_id": task.thread_id,
                        "title": task.title,
                        "status": task.status.value,
                        "execution": None if session is None else session.model_dump(mode="json"),
                    }
                )
            return json.dumps({"sessions": payload}, ensure_ascii=False)

        @tool_contract(
            name="session.status",
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="session",
            tags=["session", "status", "execution"],
            worker_types=["ops", "research", "dev", "general"],
            manifest_ref="builtin://session.status",
            metadata={
                "entrypoints": ["agent_runtime", "web"],
                "runtime_kinds": ["worker", "subagent", "graph_agent"],
            },
        )
        async def session_status(task_id: str) -> str:
            """读取指定 task 的 execution session 状态。"""

            task = await store_group.task_store.get_task(task_id)
            session = (
                await self._task_runner.get_execution_session(task_id)
                if self._task_runner is not None
                else None
            )
            if task is None:
                return json.dumps({"task_id": task_id, "status": "missing"}, ensure_ascii=False)
            return json.dumps(
                {
                    "task": task.model_dump(mode="json"),
                    "execution_session": None
                    if session is None
                    else session.model_dump(mode="json"),
                },
                ensure_ascii=False,
            )

        @tool_contract(
            name="subagents.spawn",
            side_effect_level=SideEffectLevel.REVERSIBLE,
            tool_profile=ToolProfile.STANDARD,
            tool_group="delegation",
            tags=["subagent", "child_task", "delegation"],
            worker_types=["ops", "research", "dev", "general"],
            manifest_ref="builtin://subagents.spawn",
            metadata={
                "entrypoints": ["agent_runtime"],
                "runtime_kinds": ["subagent", "graph_agent"],
            },
        )
        async def subagents_spawn(
            objective: str,
            worker_type: str = "general",
            target_kind: str = "subagent",
            title: str = "",
        ) -> str:
            """创建并启动真实 child task / subagent runtime。"""

            payload = await _launch_child(
                objective=objective,
                worker_type=worker_type,
                target_kind=target_kind,
                title=title,
            )
            return json.dumps(payload, ensure_ascii=False)

        @tool_contract(
            name="work.split",
            side_effect_level=SideEffectLevel.REVERSIBLE,
            tool_profile=ToolProfile.STANDARD,
            tool_group="delegation",
            tags=["work", "split", "child_work"],
            worker_types=["ops", "research", "dev", "general"],
            manifest_ref="builtin://work.split",
            metadata={
                "entrypoints": ["agent_runtime", "web"],
                "runtime_kinds": ["worker", "subagent", "graph_agent"],
            },
        )
        async def work_split(
            objectives: list[str] | str,
            worker_type: str = "general",
            target_kind: str = "subagent",
        ) -> str:
            """把当前 work 拆成多个 child tasks。"""

            items = _coerce_objectives(objectives)
            if not items:
                raise RuntimeError("split objectives must not be empty")
            launched = [
                await _launch_child(
                    objective=item,
                    worker_type=worker_type,
                    target_kind=target_kind,
                )
                for item in items
            ]
            return json.dumps(
                {
                    "requested": len(items),
                    "created": len(launched),
                    "children": launched,
                },
                ensure_ascii=False,
            )

        @tool_contract(
            name="work.merge",
            side_effect_level=SideEffectLevel.REVERSIBLE,
            tool_profile=ToolProfile.STANDARD,
            tool_group="delegation",
            tags=["work", "merge", "child_work"],
            worker_types=["ops", "research", "dev", "general"],
            manifest_ref="builtin://work.merge",
            metadata={
                "entrypoints": ["agent_runtime", "web"],
                "runtime_kinds": ["worker", "subagent", "graph_agent"],
            },
        )
        async def work_merge(summary: str = "merged by builtin tool") -> str:
            """合并当前 work 的 child works。"""

            if self._delegation_plane is None:
                raise RuntimeError("delegation plane is not bound for work merge")
            _, context, _ = await _current_parent()
            if not context.work_id:
                raise RuntimeError("current execution context does not carry work_id")
            children = await store_group.work_store.list_works(parent_work_id=context.work_id)
            if not children:
                raise RuntimeError("current work has no child works to merge")
            blocking = [
                item.work_id
                for item in children
                if item.status.value not in {"succeeded", "failed", "cancelled", "merged"}
            ]
            if blocking:
                raise RuntimeError(f"child works still active: {', '.join(blocking)}")
            merged = await self._delegation_plane.merge_work(context.work_id, summary=summary)
            return json.dumps(
                {
                    "work_id": context.work_id,
                    "merged": None if merged is None else merged.model_dump(mode="json"),
                    "child_work_ids": [item.work_id for item in children],
                },
                ensure_ascii=False,
            )

        @tool_contract(
            name="web.fetch",
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="network",
            tags=["web", "http", "fetch"],
            worker_types=["ops", "research", "dev", "general"],
            manifest_ref="builtin://web.fetch",
            metadata={
                "entrypoints": ["agent_runtime"],
                "runtime_kinds": ["worker", "subagent", "graph_agent"],
            },
        )
        async def web_fetch(url: str, timeout_seconds: float = 10.0) -> str:
            """抓取网页内容摘要。"""

            import httpx

            async with httpx.AsyncClient(timeout=max(0.1, timeout_seconds)) as client:
                response = await client.get(url, follow_redirects=True)
            body = response.text[:2000]
            return json.dumps(
                {
                    "url": url,
                    "final_url": str(response.url),
                    "status_code": response.status_code,
                    "content_type": response.headers.get("content-type", ""),
                    "body_preview": body,
                    "body_length": len(response.text),
                },
                ensure_ascii=False,
            )

        @tool_contract(
            name="web.search",
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="network",
            tags=["web", "search", "http"],
            worker_types=["ops", "research", "dev", "general"],
            manifest_ref="builtin://web.search",
            metadata={
                "entrypoints": ["agent_runtime"],
                "runtime_kinds": ["worker", "subagent", "graph_agent"],
            },
        )
        async def web_search(
            query: str,
            limit: int = 5,
            timeout_seconds: float = 10.0,
        ) -> str:
            """执行无认证的网页搜索。"""

            payload = await self._search_web(
                query=query,
                limit=limit,
                timeout_seconds=timeout_seconds,
            )
            return json.dumps(payload, ensure_ascii=False)

        @tool_contract(
            name="browser.open",
            side_effect_level=SideEffectLevel.REVERSIBLE,
            tool_profile=ToolProfile.STANDARD,
            tool_group="browser",
            tags=["browser", "open", "url"],
            worker_types=["ops", "research", "dev", "general"],
            manifest_ref="builtin://browser.open",
            metadata={
                "entrypoints": ["agent_runtime"],
                "runtime_kinds": ["worker", "subagent", "graph_agent"],
            },
        )
        async def browser_open(url: str) -> str:
            """调用系统默认浏览器打开 URL。"""

            status_payload = self._browser_status_payload()
            if status_payload["availability"] != BuiltinToolAvailabilityStatus.AVAILABLE.value:
                return json.dumps(
                    {
                        "url": url,
                        "opened": False,
                        "manual_open_required": True,
                        **status_payload,
                    },
                    ensure_ascii=False,
                )

            opened = webbrowser.open(url, new=0, autoraise=False)
            return json.dumps(
                {
                    "url": url,
                    "opened": bool(opened),
                    "manual_open_required": not bool(opened),
                    **status_payload,
                },
                ensure_ascii=False,
            )

        @tool_contract(
            name="browser.status",
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="browser",
            tags=["browser", "status", "diagnostics"],
            worker_types=["ops", "research", "dev", "general"],
            manifest_ref="builtin://browser.status",
            metadata={
                "entrypoints": ["agent_runtime", "web"],
                "runtime_kinds": ["worker", "subagent", "graph_agent", "acp_runtime"],
            },
        )
        async def browser_status() -> str:
            """读取当前 runtime 的 browser 支持状态。"""

            return json.dumps(self._browser_status_payload(), ensure_ascii=False)

        @tool_contract(
            name="gateway.inspect",
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="runtime",
            tags=["gateway", "inspect", "metrics"],
            worker_types=["ops", "general"],
            manifest_ref="builtin://gateway.inspect",
            metadata={
                "entrypoints": ["agent_runtime", "web"],
                "runtime_kinds": ["worker", "acp_runtime"],
            },
        )
        async def gateway_inspect() -> str:
            """读取 gateway / capability / queue 摘要。"""

            jobs = await store_group.task_job_store.list_jobs(
                ["QUEUED", "RUNNING", "WAITING_INPUT"]
            )
            return json.dumps(
                {
                    "project_root": str(self._project_root),
                    "queued_jobs": len([item for item in jobs if item.status == "QUEUED"]),
                    "running_jobs": len([item for item in jobs if item.status == "RUNNING"]),
                    "deferred_jobs": len(
                        [
                            item
                            for item in jobs
                            if item.status in {"WAITING_INPUT", "WAITING_APPROVAL", "PAUSED"}
                        ]
                    ),
                    "tool_index_backend": self._tool_index.backend_name,
                    "capability_snapshot": self.capability_snapshot(),
                },
                ensure_ascii=False,
            )

        @tool_contract(
            name="cron.list",
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="automation",
            tags=["cron", "automation", "scheduler"],
            worker_types=["ops", "general"],
            manifest_ref="builtin://cron.list",
            metadata={
                "entrypoints": ["agent_runtime", "web"],
                "runtime_kinds": ["worker", "acp_runtime"],
            },
        )
        async def cron_list(limit: int = 20) -> str:
            """列出当前 automation jobs。"""

            jobs = AutomationStore(self._project_root).list_jobs()[: max(1, min(limit, 100))]
            return json.dumps(
                {"jobs": [item.model_dump(mode="json") for item in jobs]},
                ensure_ascii=False,
            )

        @tool_contract(
            name="nodes.list",
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="runtime",
            tags=["nodes", "runtime", "host"],
            worker_types=["ops", "general"],
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
                            "project_root": str(self._project_root),
                        }
                    ]
                },
                ensure_ascii=False,
            )

        @tool_contract(
            name="pdf.inspect",
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="document",
            tags=["pdf", "document", "inspect"],
            worker_types=["research", "dev", "general"],
            manifest_ref="builtin://pdf.inspect",
            metadata={
                "entrypoints": ["agent_runtime"],
                "runtime_kinds": ["worker", "subagent", "graph_agent"],
            },
        )
        async def pdf_inspect(path: str) -> str:
            """检查 PDF 文件摘要。"""

            payload = self._inspect_pdf_file(Path(path))
            return json.dumps(payload, ensure_ascii=False)

        @tool_contract(
            name="image.inspect",
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="media",
            tags=["image", "media", "inspect"],
            worker_types=["research", "dev", "general"],
            manifest_ref="builtin://image.inspect",
            metadata={
                "entrypoints": ["agent_runtime"],
                "runtime_kinds": ["worker", "subagent", "graph_agent"],
            },
        )
        async def image_inspect(path: str) -> str:
            """检查图片文件尺寸与格式。"""

            payload = self._inspect_image_file(Path(path))
            return json.dumps(payload, ensure_ascii=False)

        @tool_contract(
            name="tts.speak",
            side_effect_level=SideEffectLevel.REVERSIBLE,
            tool_profile=ToolProfile.STANDARD,
            tool_group="media",
            tags=["tts", "speech", "audio"],
            worker_types=["ops", "research", "dev", "general"],
            manifest_ref="builtin://tts.speak",
            metadata={
                "entrypoints": ["agent_runtime"],
                "runtime_kinds": ["worker", "subagent", "graph_agent"],
            },
        )
        async def tts_speak(text: str, voice: str = "") -> str:
            """通过系统 TTS 朗读文本。"""

            command = self._tts_command(text=text, voice=voice)
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
            )
            return json.dumps(
                {
                    "command": command,
                    "returncode": completed.returncode,
                    "stderr": completed.stderr.strip(),
                },
                ensure_ascii=False,
            )

        @tool_contract(
            name="canvas.write",
            side_effect_level=SideEffectLevel.REVERSIBLE,
            tool_profile=ToolProfile.STANDARD,
            tool_group="canvas",
            tags=["canvas", "artifact", "write"],
            worker_types=["research", "dev", "general"],
            manifest_ref="builtin://canvas.write",
            metadata={
                "entrypoints": ["agent_runtime"],
                "runtime_kinds": ["worker", "subagent", "graph_agent"],
            },
        )
        async def canvas_write(name: str, content: str, description: str = "") -> str:
            """在当前 task 下创建文本 artifact。"""

            _, context, parent_task = await _current_parent()
            artifact = await task_service.create_text_artifact(
                task_id=parent_task.task_id,
                name=name,
                description=description or f"Canvas output for {parent_task.task_id}",
                content=content,
                trace_id=context.trace_id,
                session_id=context.session_id,
                source="builtin:canvas.write",
            )
            return json.dumps(
                {
                    "artifact_id": artifact.artifact_id,
                    "task_id": parent_task.task_id,
                    "name": artifact.name,
                },
                ensure_ascii=False,
            )

        @tool_contract(
            name="memory.read",
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="memory",
            tags=["memory", "subject", "history"],
            worker_types=["ops", "research", "dev", "general"],
            manifest_ref="builtin://memory.read",
            metadata={
                "entrypoints": ["agent_runtime", "web"],
                "runtime_kinds": ["worker", "subagent", "graph_agent"],
            },
        )
        async def memory_read(
            subject_key: str,
            scope_id: str = "",
            project_id: str = "",
            workspace_id: str = "",
        ) -> str:
            """读取指定 subject 的 current/history。"""

            project, workspace = await self._resolve_project_context(
                project_id=project_id,
                workspace_id=workspace_id,
            )
            document = await self._memory_console_service.get_memory_subject_history(
                subject_key=subject_key,
                project_id=project.project_id if project is not None else "",
                workspace_id=workspace.workspace_id if workspace is not None else None,
                scope_id=scope_id or None,
            )
            return json.dumps(document.model_dump(mode="json"), ensure_ascii=False)

        @tool_contract(
            name="memory.search",
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="memory",
            tags=["memory", "search", "records"],
            worker_types=["ops", "research", "dev", "general"],
            manifest_ref="builtin://memory.search",
            metadata={
                "entrypoints": ["agent_runtime", "web"],
                "runtime_kinds": ["worker", "subagent", "graph_agent"],
            },
        )
        async def memory_search(
            query: str,
            scope_id: str = "",
            partition: str = "",
            layer: str = "",
            project_id: str = "",
            workspace_id: str = "",
            limit: int = 10,
        ) -> str:
            """按 query / scope / partition / layer 搜索 Memory。"""

            project, workspace = await self._resolve_project_context(
                project_id=project_id,
                workspace_id=workspace_id,
            )
            document = await self._memory_console_service.get_memory_console(
                project_id=project.project_id if project is not None else "",
                workspace_id=workspace.workspace_id if workspace is not None else None,
                scope_id=scope_id or None,
                partition=MemoryPartition(partition) if partition else None,
                layer=MemoryLayer(layer) if layer else None,
                query=query,
                include_history=False,
                include_vault_refs=True,
                limit=max(1, min(limit, 50)),
            )
            return json.dumps(document.model_dump(mode="json"), ensure_ascii=False)

        @tool_contract(
            name="memory.citations",
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="memory",
            tags=["memory", "citations", "evidence"],
            worker_types=["ops", "research", "dev", "general"],
            manifest_ref="builtin://memory.citations",
            metadata={
                "entrypoints": ["agent_runtime", "web"],
                "runtime_kinds": ["worker", "subagent", "graph_agent"],
            },
        )
        async def memory_citations(
            subject_key: str,
            scope_id: str = "",
            project_id: str = "",
            workspace_id: str = "",
        ) -> str:
            """读取 subject 的证据链引用。"""

            project, workspace = await self._resolve_project_context(
                project_id=project_id,
                workspace_id=workspace_id,
            )
            document = await self._memory_console_service.get_memory_subject_history(
                subject_key=subject_key,
                project_id=project.project_id if project is not None else "",
                workspace_id=workspace.workspace_id if workspace is not None else None,
                scope_id=scope_id or None,
            )
            citations = []
            if document.current_record is not None:
                citations.extend(document.current_record.evidence_refs)
            for record in document.history:
                citations.extend(record.evidence_refs)
            return json.dumps(
                {
                    "subject_key": subject_key,
                    "scope_id": document.scope_id,
                    "citations": citations,
                },
                ensure_ascii=False,
            )

        for handler in (
            project_inspect,
            task_inspect,
            artifact_list,
            runtime_inspect,
            work_inspect,
            agents_list,
            sessions_list,
            session_status,
            subagents_spawn,
            work_split,
            work_merge,
            web_fetch,
            web_search,
            browser_open,
            browser_status,
            gateway_inspect,
            cron_list,
            nodes_list,
            pdf_inspect,
            image_inspect,
            tts_speak,
            canvas_write,
            memory_read,
            memory_search,
            memory_citations,
        ):
            await self._tool_broker.try_register(
                reflect_tool_schema(handler),
                handler,
            )

    def _register_builtin_skills(self) -> None:
        existing_ids = {item.skill_id for item in self._skill_registry.list_skills()}
        definitions = [
            (
                "ops_triage",
                "你是 ops worker，优先诊断运行态、恢复策略、可观测性和风险收敛。",
                ["runtime.inspect", "task.inspect", "work.inspect", "project.inspect"],
                ToolProfile.MINIMAL,
                "ops",
            ),
            (
                "research_brief",
                "你是 research worker，优先收集 artifact、上下文与结论摘要。",
                ["project.inspect", "task.inspect", "artifact.list", "work.inspect"],
                ToolProfile.MINIMAL,
                "research",
            ),
            (
                "dev_patch_plan",
                "你是 dev worker，优先理解 project/workspace、产物与 work ownership。",
                ["project.inspect", "task.inspect", "artifact.list", "work.inspect"],
                ToolProfile.MINIMAL,
                "dev",
            ),
        ]
        for skill_id, prompt, tools_allowed, tool_profile, worker_type in definitions:
            if skill_id in existing_ids:
                continue
            self._skill_registry.register(
                SkillManifest(
                    skill_id=skill_id,
                    input_model=_BuiltinSkillInput,
                    output_model=_BuiltinSkillOutput,
                    description=f"bundled skill for {worker_type}",
                    description_md=worker_type,
                    tools_allowed=tools_allowed,
                    tool_profile=tool_profile,
                    metadata={"worker_type": worker_type},
                ),
                prompt_template=prompt,
            )
            existing_ids.add(skill_id)

    def _build_worker_profiles(self) -> dict[WorkerType, WorkerCapabilityProfile]:
        return {
            WorkerType.GENERAL: WorkerCapabilityProfile(
                worker_type=WorkerType.GENERAL,
                capabilities=["llm_generation", "general"],
                default_model_alias="main",
                default_tool_profile="minimal",
                default_tool_groups=["project", "session", "network", "memory"],
                bootstrap_file_ids=["bootstrap:shared", "bootstrap:general"],
                runtime_kinds=[RuntimeKind.WORKER, RuntimeKind.SUBAGENT],
            ),
            WorkerType.OPS: WorkerCapabilityProfile(
                worker_type=WorkerType.OPS,
                capabilities=["ops", "runtime", "automation", "recovery"],
                default_model_alias="main",
                default_tool_profile="minimal",
                default_tool_groups=["runtime", "session", "project", "automation", "delegation"],
                bootstrap_file_ids=["bootstrap:shared", "bootstrap:ops"],
                runtime_kinds=[RuntimeKind.WORKER, RuntimeKind.ACP_RUNTIME],
            ),
            WorkerType.RESEARCH: WorkerCapabilityProfile(
                worker_type=WorkerType.RESEARCH,
                capabilities=["research", "analysis", "summarize"],
                default_model_alias="main",
                default_tool_profile="minimal",
                default_tool_groups=[
                    "project",
                    "artifact",
                    "session",
                    "network",
                    "memory",
                    "document",
                    "media",
                ],
                bootstrap_file_ids=["bootstrap:shared", "bootstrap:research"],
                runtime_kinds=[RuntimeKind.WORKER, RuntimeKind.SUBAGENT],
            ),
            WorkerType.DEV: WorkerCapabilityProfile(
                worker_type=WorkerType.DEV,
                capabilities=["dev", "code", "patch", "test"],
                default_model_alias="main",
                default_tool_profile="minimal",
                default_tool_groups=[
                    "project",
                    "artifact",
                    "session",
                    "delegation",
                    "runtime",
                    "document",
                    "media",
                ],
                bootstrap_file_ids=["bootstrap:shared", "bootstrap:dev"],
                runtime_kinds=[RuntimeKind.WORKER, RuntimeKind.GRAPH_AGENT],
            ),
        }

    def _build_bootstrap_templates(self) -> dict[str, WorkerBootstrapFile]:
        return {
            "bootstrap:shared": WorkerBootstrapFile(
                file_id="bootstrap:shared",
                path_hint="bootstrap/shared.md",
                applies_to_worker_types=[WorkerType.GENERAL],
                content=(
                    "你当前运行在 OctoAgent 内建 capability pack。\n"
                    "Project: {{project_name}} ({{project_slug}} / {{project_id}})\n"
                    "Workspace: {{workspace_slug}} ({{workspace_id}})\n"
                    "Workspace Root: {{workspace_root}}\n"
                    "必须继续走 ToolBroker / Policy / audit，不得绕过治理面。"
                ),
                metadata={"scope": "shared"},
            ),
            "bootstrap:general": WorkerBootstrapFile(
                file_id="bootstrap:general",
                path_hint="bootstrap/general.md",
                applies_to_worker_types=[WorkerType.GENERAL],
                content="你是 general worker，负责单 worker / fallback 路径。",
                metadata={"worker_type": "general"},
            ),
            "bootstrap:ops": WorkerBootstrapFile(
                file_id="bootstrap:ops",
                path_hint="bootstrap/ops.md",
                applies_to_worker_types=[WorkerType.OPS],
                content="你是 ops worker，优先 runtime / diagnostics / recovery。",
                metadata={"worker_type": "ops"},
            ),
            "bootstrap:research": WorkerBootstrapFile(
                file_id="bootstrap:research",
                path_hint="bootstrap/research.md",
                applies_to_worker_types=[WorkerType.RESEARCH],
                content="你是 research worker，优先分析上下文、产物和证据。",
                metadata={"worker_type": "research"},
            ),
            "bootstrap:dev": WorkerBootstrapFile(
                file_id="bootstrap:dev",
                path_hint="bootstrap/dev.md",
                applies_to_worker_types=[WorkerType.DEV],
                content="你是 dev worker，优先改动方案、补丁和验证。",
                metadata={"worker_type": "dev"},
            ),
        }

    async def _resolve_fallback_toolset(self, worker_type: WorkerType) -> list[str]:
        metas = await self._tool_broker.discover()
        profile = self.get_worker_profile(worker_type)
        result: list[str] = []
        for meta in metas:
            if meta.tool_group not in profile.default_tool_groups:
                continue
            result.append(meta.name)
        if result:
            return result
        return [meta.name for meta in metas][:5]

    async def _resolve_project_context(
        self,
        *,
        project_id: str = "",
        workspace_id: str = "",
    ):
        project = None
        workspace = None
        if project_id:
            project = await self._stores.project_store.get_project(project_id)
        if workspace_id:
            workspace = await self._stores.project_store.get_workspace(workspace_id)
        if project is None:
            selector = await self._stores.project_store.get_selector_state("web")
            if selector is not None:
                project = await self._stores.project_store.get_project(selector.active_project_id)
                if selector.active_workspace_id:
                    workspace = await self._stores.project_store.get_workspace(
                        selector.active_workspace_id
                    )
        if project is None:
            project = await self._stores.project_store.get_default_project()
        if project is not None and workspace is None:
            workspace = await self._stores.project_store.get_primary_workspace(project.project_id)
        return project, workspace

    @staticmethod
    def _resolve_skill_worker_types(manifest: SkillManifest) -> list[WorkerType]:
        raw = str(manifest.metadata.get("worker_type", "")).strip().lower()
        if raw in {member.value for member in WorkerType}:
            return [WorkerType(raw)]
        return [WorkerType.GENERAL]

    def _resolve_tool_availability(
        self,
        tool_name: str,
    ) -> BuiltinToolAvailabilityStatus:
        if tool_name in {"subagents.spawn", "work.split"} and self._task_runner is None:
            return BuiltinToolAvailabilityStatus.UNAVAILABLE
        if tool_name == "work.merge" and (
            self._task_runner is None or self._delegation_plane is None
        ):
            return BuiltinToolAvailabilityStatus.UNAVAILABLE
        if tool_name in {"browser.open", "browser.status"}:
            status, _, _ = self._resolve_browser_support()
            return status
        if tool_name == "tts.speak" and not self._tts_binary():
            return BuiltinToolAvailabilityStatus.INSTALL_REQUIRED
        return BuiltinToolAvailabilityStatus.AVAILABLE

    def _resolve_tool_availability_reason(self, tool_name: str) -> str:
        if tool_name in {"subagents.spawn", "work.split"} and self._task_runner is None:
            return "task_runner_unbound"
        if tool_name == "work.merge" and self._delegation_plane is None:
            return "delegation_plane_unbound"
        if tool_name == "work.merge" and self._task_runner is None:
            return "task_runner_unbound"
        if tool_name in {"browser.open", "browser.status"}:
            _, reason, _ = self._resolve_browser_support()
            return reason
        if tool_name == "tts.speak" and not self._tts_binary():
            return "system_tts_binary_missing"
        return ""

    def _resolve_tool_install_hint(self, tool_name: str) -> str:
        if tool_name in {"browser.open", "browser.status"}:
            _, _, install_hint = self._resolve_browser_support()
            return install_hint
        if tool_name == "tts.speak" and not self._tts_binary():
            return "安装 macOS say 或 Linux espeak 后再使用 tts.speak"
        return ""

    @staticmethod
    def _resolve_tool_entrypoints(tool_name: str) -> list[str]:
        explicit: dict[str, list[str]] = {
            "project.inspect": ["agent_runtime", "web"],
            "runtime.inspect": ["agent_runtime", "web"],
            "gateway.inspect": ["agent_runtime", "web"],
            "browser.status": ["agent_runtime", "web"],
            "cron.list": ["agent_runtime", "web"],
            "nodes.list": ["agent_runtime", "web"],
            "work.split": ["agent_runtime", "web"],
            "work.merge": ["agent_runtime", "web"],
        }
        return explicit.get(tool_name, ["agent_runtime"])

    @staticmethod
    def _resolve_tool_runtime_kinds(tool_name: str) -> list[RuntimeKind]:
        if tool_name == "subagents.spawn":
            return [RuntimeKind.SUBAGENT, RuntimeKind.GRAPH_AGENT]
        if tool_name in {
            "gateway.inspect",
            "cron.list",
            "nodes.list",
            "runtime.inspect",
            "browser.status",
        }:
            return [RuntimeKind.WORKER, RuntimeKind.ACP_RUNTIME]
        return [RuntimeKind.WORKER, RuntimeKind.SUBAGENT, RuntimeKind.GRAPH_AGENT]

    @staticmethod
    def _tts_binary() -> str:
        return shutil.which("say") or shutil.which("espeak") or ""

    @staticmethod
    def _desktop_session_available() -> bool:
        if platform.system() == "Darwin":
            return True
        return any(
            os.environ.get(name)
            for name in (
                "DISPLAY",
                "WAYLAND_DISPLAY",
                "SWAYSOCK",
                "XDG_CURRENT_DESKTOP",
                "DESKTOP_SESSION",
            )
        )

    def _resolve_browser_support(
        self,
    ) -> tuple[BuiltinToolAvailabilityStatus, str, str]:
        try:
            webbrowser.get()
            return BuiltinToolAvailabilityStatus.AVAILABLE, "", ""
        except webbrowser.Error:
            pass

        if self._desktop_session_available():
            return (
                BuiltinToolAvailabilityStatus.INSTALL_REQUIRED,
                "browser_controller_missing",
                "配置默认浏览器或设置 BROWSER 环境变量后再使用 browser.*",
            )

        return (
            BuiltinToolAvailabilityStatus.DEGRADED,
            "desktop_session_unavailable",
            "当前 runtime 没有桌面会话；请在 GUI 环境中运行或设置 BROWSER 环境变量。",
        )

    def _browser_status_payload(self) -> dict[str, Any]:
        status, reason, install_hint = self._resolve_browser_support()
        controller = ""
        controller_error = ""
        try:
            controller = type(webbrowser.get()).__name__
        except webbrowser.Error as exc:
            controller_error = str(exc)
        return {
            "availability": status.value,
            "reason": reason,
            "install_hint": install_hint,
            "controller": controller,
            "controller_error": controller_error,
            "browser_env": os.environ.get("BROWSER", ""),
            "desktop_session_available": self._desktop_session_available(),
            "platform": platform.platform(),
        }

    def _tts_command(self, *, text: str, voice: str = "") -> list[str]:
        binary = self._tts_binary()
        if not binary:
            raise RuntimeError("system tts binary is unavailable")
        if Path(binary).name == "say":
            command = [binary]
            if voice.strip():
                command.extend(["-v", voice.strip()])
            command.append(text)
            return command
        command = [binary]
        if voice.strip():
            command.extend(["-v", voice.strip()])
        command.append(text)
        return command

    async def _search_web(
        self,
        *,
        query: str,
        limit: int,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        import httpx

        search_query = query.strip()
        if not search_query:
            raise ValueError("query must not be empty")

        effective_limit = max(1, min(limit, 10))
        search_urls = (
            "https://html.duckduckgo.com/html/",
            "https://duckduckgo.com/html/",
        )
        last_error = ""
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            )
        }

        async with httpx.AsyncClient(timeout=max(0.1, timeout_seconds), headers=headers) as client:
            for search_url in search_urls:
                try:
                    response = await client.get(
                        search_url,
                        params={"q": search_query},
                        follow_redirects=True,
                    )
                    response.raise_for_status()
                except Exception as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
                    continue

                results = self._parse_duckduckgo_results(response.text, limit=effective_limit)
                if not results:
                    last_error = "no_search_results_parsed"
                    continue
                return {
                    "query": search_query,
                    "engine": "duckduckgo",
                    "results": results,
                    "result_count": len(results),
                    "source_url": str(response.url),
                }

        raise RuntimeError(f"web search failed: {last_error or 'unknown_error'}")

    @classmethod
    def _parse_duckduckgo_results(
        cls,
        payload: str,
        *,
        limit: int,
    ) -> list[dict[str, str]]:
        anchor_pattern = re.compile(
            r"<a[^>]+class=[\"'][^\"']*(?:result__a|result-link)[^\"']*[\"'][^>]+"
            r"href=[\"'](?P<href>[^\"']+)[\"'][^>]*>(?P<title>.*?)</a>",
            re.IGNORECASE | re.DOTALL,
        )
        results: list[dict[str, str]] = []
        seen_urls: set[str] = set()
        for match in anchor_pattern.finditer(payload):
            raw_url = html.unescape(match.group("href"))
            url = cls._normalize_search_result_url(raw_url)
            title = cls._strip_html_text(match.group("title"))
            if not url or not title or url in seen_urls:
                continue
            seen_urls.add(url)
            results.append({"title": title, "url": url})
            if len(results) >= limit:
                break
        return results

    @staticmethod
    def _normalize_search_result_url(raw_url: str) -> str:
        parsed = urlparse(raw_url)
        if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
            encoded = parse_qs(parsed.query).get("uddg", [])
            if encoded:
                return unquote(encoded[0])
        return raw_url

    @staticmethod
    def _strip_html_text(payload: str) -> str:
        text = re.sub(r"<[^>]+>", "", payload)
        text = html.unescape(text)
        return " ".join(text.split())

    @staticmethod
    def _inspect_pdf_file(path: Path) -> dict[str, Any]:
        if not path.exists():
            raise FileNotFoundError(path)
        payload = path.read_bytes()
        if not payload.startswith(b"%PDF-"):
            raise RuntimeError("not a valid pdf header")
        page_count = payload.count(b"/Type /Page")
        return {
            "path": str(path),
            "size_bytes": len(payload),
            "format": "pdf",
            "page_count_estimate": max(page_count, 0),
            "header": payload[:8].decode("latin-1", errors="ignore"),
        }

    @staticmethod
    def _inspect_image_file(path: Path) -> dict[str, Any]:
        if not path.exists():
            raise FileNotFoundError(path)
        payload = path.read_bytes()
        size = len(payload)
        if payload.startswith(b"\x89PNG\r\n\x1a\n") and size >= 24:
            width = int.from_bytes(payload[16:20], "big")
            height = int.from_bytes(payload[20:24], "big")
            return {
                "path": str(path),
                "format": "png",
                "width": width,
                "height": height,
                "size_bytes": size,
            }
        if payload.startswith(b"GIF87a") or payload.startswith(b"GIF89a"):
            width = int.from_bytes(payload[6:8], "little")
            height = int.from_bytes(payload[8:10], "little")
            return {
                "path": str(path),
                "format": "gif",
                "width": width,
                "height": height,
                "size_bytes": size,
            }
        if payload.startswith(b"\xff\xd8"):
            offset = 2
            while offset + 9 < size:
                if payload[offset] != 0xFF:
                    offset += 1
                    continue
                marker = payload[offset + 1]
                if marker in {0xC0, 0xC1, 0xC2, 0xC3}:
                    height = int.from_bytes(payload[offset + 5 : offset + 7], "big")
                    width = int.from_bytes(payload[offset + 7 : offset + 9], "big")
                    return {
                        "path": str(path),
                        "format": "jpeg",
                        "width": width,
                        "height": height,
                        "size_bytes": size,
                    }
                if offset + 4 > size:
                    break
                segment_length = int.from_bytes(payload[offset + 2 : offset + 4], "big")
                if segment_length <= 0:
                    break
                offset += 2 + segment_length
            raise RuntimeError("jpeg dimensions not found")
        raise RuntimeError("unsupported image format")
