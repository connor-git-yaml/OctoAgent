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
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx
from octoagent.core.models import (
    BuiltinToolAvailabilityStatus,
    BundledCapabilityPack,
    BundledSkillDefinition,
    BundledToolDefinition,
    DynamicToolSelection,
    NormalizedMessage,
    ProjectBindingType,
    RuntimeKind,
    ToolIndexQuery,
    WorkerBootstrapFile,
    WorkerCapabilityProfile,
    WorkerType,
    WorkStatus,
)
from octoagent.memory import (
    MemoryAccessPolicy,
    MemoryLayer,
    MemoryPartition,
    MemoryRecallHookOptions,
    MemoryRecallPostFilterMode,
    MemoryRecallRerankMode,
    MemoryRecallResult,
)
from octoagent.provider.dx.automation_store import AutomationStore
from octoagent.provider.dx.memory_console_service import MemoryConsoleService
from octoagent.provider.dx.memory_runtime_service import MemoryRuntimeService
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

from .agent_context import build_default_memory_recall_hook_options
from .execution_context import get_current_execution_context
from .task_service import TaskService

if TYPE_CHECKING:
    from .mcp_registry import McpRegistryService


class _BuiltinSkillInput(BaseModel):
    objective: str = Field(min_length=1)
    context: dict[str, Any] = Field(default_factory=dict)


class _BuiltinSkillOutput(BaseModel):
    content: str = ""
    complete: bool = True
    skip_remaining_tools: bool = True
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


_WORK_TERMINAL_VALUES = {
    WorkStatus.SUCCEEDED.value,
    WorkStatus.FAILED.value,
    WorkStatus.CANCELLED.value,
    WorkStatus.MERGED.value,
    WorkStatus.TIMED_OUT.value,
    WorkStatus.DELETED.value,
}

_MEMORY_BINDING_TYPES = {
    ProjectBindingType.SCOPE,
    ProjectBindingType.MEMORY_SCOPE,
    ProjectBindingType.IMPORT_SCOPE,
}


def _normalize_browser_text(value: str) -> str:
    return " ".join(value.split())


@dataclass(slots=True)
class _BrowserLinkRef:
    ref: str
    text: str
    url: str


@dataclass(slots=True)
class _BrowserSnapshot:
    title: str
    text: str
    links: list[_BrowserLinkRef]


@dataclass(slots=True)
class _BrowserSessionState:
    session_id: str
    task_id: str
    work_id: str
    current_url: str
    final_url: str
    status_code: int
    content_type: str
    title: str
    text_content: str
    html_preview: str
    body_length: int
    links: list[_BrowserLinkRef]


class _HtmlSnapshotParser(HTMLParser):
    def __init__(self, *, base_url: str, link_limit: int = 40) -> None:
        super().__init__(convert_charrefs=True)
        self._base_url = base_url
        self._link_limit = max(1, link_limit)
        self._title_parts: list[str] = []
        self._text_parts: list[str] = []
        self._links: list[_BrowserLinkRef] = []
        self._in_title = False
        self._ignored_tag_depth = 0
        self._current_href: str | None = None
        self._current_link_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lower = tag.lower()
        if lower == "title":
            self._in_title = True
            return
        if lower in {"script", "style"}:
            self._ignored_tag_depth += 1
            return
        if lower == "a" and len(self._links) < self._link_limit:
            href = ""
            for key, value in attrs:
                if key.lower() == "href" and value:
                    href = value.strip()
                    break
            if href:
                self._current_href = urljoin(self._base_url, href)
                self._current_link_parts = []

    def handle_endtag(self, tag: str) -> None:
        lower = tag.lower()
        if lower == "title":
            self._in_title = False
            return
        if lower in {"script", "style"} and self._ignored_tag_depth > 0:
            self._ignored_tag_depth -= 1
            return
        if lower == "a" and self._current_href:
            text = _normalize_browser_text(" ".join(self._current_link_parts)) or self._current_href
            ref = f"link:{len(self._links) + 1}"
            self._links.append(_BrowserLinkRef(ref=ref, text=text, url=self._current_href))
            self._current_href = None
            self._current_link_parts = []

    def handle_data(self, data: str) -> None:
        if self._ignored_tag_depth > 0:
            return
        text = _normalize_browser_text(data)
        if not text:
            return
        if self._in_title:
            self._title_parts.append(text)
            return
        self._text_parts.append(text)
        if self._current_href:
            self._current_link_parts.append(text)

    def snapshot(self) -> _BrowserSnapshot:
        return _BrowserSnapshot(
            title=_normalize_browser_text(" ".join(self._title_parts)),
            text=_normalize_browser_text(" ".join(self._text_parts)),
            links=list(self._links),
        )


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
        self._mcp_registry: McpRegistryService | None = None
        self._browser_sessions: dict[str, _BrowserSessionState] = {}
        self._memory_console_service = MemoryConsoleService(
            project_root,
            store_group=store_group,
        )
        self._memory_runtime_service = MemoryRuntimeService(
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

    def bind_mcp_registry(self, mcp_registry: McpRegistryService) -> None:
        self._mcp_registry = mcp_registry

    async def startup(self) -> None:
        if self._bootstrapped:
            return
        await self._register_builtin_tools()
        self._register_builtin_skills()
        if self._mcp_registry is not None:
            await self._mcp_registry.startup()
        await self.refresh()
        self._bootstrapped = True

    async def refresh(self) -> BundledCapabilityPack:
        if self._mcp_registry is not None:
            await self._mcp_registry.refresh()
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
            "browser_session_count": len(self._browser_sessions),
            "mcp": None
            if self._mcp_registry is None
            else {
                "config_path": str(self._mcp_registry.config_path),
                "config_error": self._mcp_registry.last_config_error,
                "configured_server_count": self._mcp_registry.configured_server_count(),
                "healthy_server_count": self._mcp_registry.healthy_server_count(),
                "registered_tool_count": self._mcp_registry.registered_tool_count(),
            },
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

        async def _resolve_runtime_project_context(
            *,
            project_id: str = "",
            workspace_id: str = "",
        ) -> tuple[Any, Any, Any | None]:
            task = None
            if project_id.strip() or workspace_id.strip():
                project, workspace = await self._resolve_project_context(
                    project_id=project_id.strip(),
                    workspace_id=workspace_id.strip(),
                )
                return project, workspace, task
            try:
                _, _context, task = await _current_parent()
            except Exception:
                task = None
            if task is not None:
                project, workspace = await task_service._agent_context.resolve_project_scope(
                    task=task,
                    surface=task.requester.channel,
                )
                if project is not None or workspace is not None:
                    return project, workspace, task
            project, workspace = await self._resolve_project_context(
                project_id="",
                workspace_id="",
            )
            return project, workspace, task

        async def _resolve_memory_scope_ids(
            *,
            task: Any | None,
            project: Any,
            workspace: Any,
            explicit_scope_id: str = "",
        ) -> list[str]:
            scope_ids: list[str] = []
            if explicit_scope_id.strip():
                scope_ids.append(explicit_scope_id.strip())
            elif task is not None and task.scope_id:
                scope_ids.append(task.scope_id)

            if project is not None:
                bindings = await store_group.project_store.list_bindings(project.project_id)
                for binding in bindings:
                    if binding.binding_type not in _MEMORY_BINDING_TYPES:
                        continue
                    if workspace is not None and binding.workspace_id not in {
                        None,
                        workspace.workspace_id,
                    }:
                        continue
                    if binding.binding_key:
                        scope_ids.append(binding.binding_key)
            return list(dict.fromkeys(item for item in scope_ids if item))

        async def _current_work_context() -> tuple[Any, Any]:
            _, context, task = await _current_parent()
            if not context.work_id:
                raise RuntimeError("current execution context does not carry work_id")
            return context, task

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

        async def _descendant_works_for_current_context() -> tuple[Any, list[Any]]:
            if self._delegation_plane is None:
                raise RuntimeError("delegation plane is not bound for descendant work lookup")
            context, _task = await _current_work_context()
            descendants = await self._delegation_plane.list_descendant_works(context.work_id)
            descendants.sort(key=lambda item: item.created_at)
            return context, descendants

        async def _resolve_child_work(
            *,
            task_id: str = "",
            work_id: str = "",
        ):
            context, descendants = await _descendant_works_for_current_context()
            if work_id.strip():
                target = next(
                    (item for item in descendants if item.work_id == work_id.strip()),
                    None,
                )
                if target is None:
                    raise RuntimeError(f"descendant work not found: {work_id}")
                return context, target, descendants
            if task_id.strip():
                target = next(
                    (item for item in descendants if item.task_id == task_id.strip()),
                    None,
                )
                if target is None:
                    raise RuntimeError(f"descendant task not found: {task_id}")
                return context, target, descendants
            raise RuntimeError("either task_id or work_id is required")

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
            name="subagents.list",
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="delegation",
            tags=["subagent", "list", "delegation"],
            worker_types=["ops", "research", "dev", "general"],
            manifest_ref="builtin://subagents.list",
            metadata={
                "entrypoints": ["agent_runtime", "web"],
                "runtime_kinds": ["worker", "subagent", "graph_agent"],
            },
        )
        async def subagents_list(limit: int = 20, include_terminal: bool = False) -> str:
            """列出当前 work 之下的 descendant child works / sessions。"""

            _context, descendants = await _descendant_works_for_current_context()
            if not include_terminal:
                descendants = [
                    item for item in descendants if item.status.value not in _WORK_TERMINAL_VALUES
                ]
            payload = []
            for item in descendants[: max(1, min(limit, 100))]:
                session = (
                    await self._task_runner.get_execution_session(item.task_id)
                    if self._task_runner is not None
                    else None
                )
                payload.append(
                    {
                        "work_id": item.work_id,
                        "task_id": item.task_id,
                        "parent_work_id": item.parent_work_id,
                        "title": item.title,
                        "status": item.status.value,
                        "target_kind": item.target_kind.value,
                        "selected_worker_type": item.selected_worker_type.value,
                        "runtime_id": item.runtime_id,
                        "result_summary": str(item.metadata.get("result_summary", "")),
                        "execution_session": None
                        if session is None
                        else session.model_dump(mode="json"),
                        "steerable": bool(session is not None and session.can_attach_input),
                        "cancellable": item.status.value not in _WORK_TERMINAL_VALUES,
                    }
                )
            return json.dumps(
                {
                    "count": len(payload),
                    "include_terminal": include_terminal,
                    "items": payload,
                },
                ensure_ascii=False,
            )

        @tool_contract(
            name="subagents.kill",
            side_effect_level=SideEffectLevel.REVERSIBLE,
            tool_profile=ToolProfile.STANDARD,
            tool_group="delegation",
            tags=["subagent", "cancel", "kill"],
            worker_types=["ops", "research", "dev", "general"],
            manifest_ref="builtin://subagents.kill",
            metadata={
                "entrypoints": ["agent_runtime", "web"],
                "runtime_kinds": ["worker", "subagent", "graph_agent"],
            },
        )
        async def subagents_kill(
            task_id: str = "",
            work_id: str = "",
            reason: str = "cancelled by parent agent",
        ) -> str:
            """取消当前 work 之下的指定 child work / task。"""

            if self._task_runner is None:
                raise RuntimeError("task runner is not bound for subagents.kill")
            if self._delegation_plane is None:
                raise RuntimeError("delegation plane is not bound for subagents.kill")
            _context, target, _descendants = await _resolve_child_work(
                task_id=task_id,
                work_id=work_id,
            )
            runtime_cancelled = await self._task_runner.cancel_task(target.task_id)
            updated = await self._delegation_plane.cancel_work(
                target.work_id,
                reason=reason,
            )
            return json.dumps(
                {
                    "task_id": target.task_id,
                    "work_id": target.work_id,
                    "runtime_cancelled": runtime_cancelled,
                    "work": None if updated is None else updated.model_dump(mode="json"),
                },
                ensure_ascii=False,
            )

        @tool_contract(
            name="subagents.steer",
            side_effect_level=SideEffectLevel.REVERSIBLE,
            tool_profile=ToolProfile.STANDARD,
            tool_group="delegation",
            tags=["subagent", "steer", "input"],
            worker_types=["ops", "research", "dev", "general"],
            manifest_ref="builtin://subagents.steer",
            metadata={
                "entrypoints": ["agent_runtime", "web"],
                "runtime_kinds": ["worker", "subagent", "graph_agent"],
            },
        )
        async def subagents_steer(
            text: str,
            task_id: str = "",
            work_id: str = "",
            approval_id: str = "",
        ) -> str:
            """向等待输入的 child runtime 附加 steering input。"""

            if self._task_runner is None:
                raise RuntimeError("task runner is not bound for subagents.steer")
            context, target, _descendants = await _resolve_child_work(
                task_id=task_id,
                work_id=work_id,
            )
            result = await self._task_runner.attach_input(
                target.task_id,
                text,
                actor=f"parent:{context.task_id}",
                approval_id=approval_id or None,
            )
            session = await self._task_runner.get_execution_session(target.task_id)
            return json.dumps(
                {
                    "task_id": result.task_id,
                    "work_id": target.work_id,
                    "session_id": result.session_id,
                    "request_id": result.request_id,
                    "artifact_id": result.artifact_id,
                    "delivered_live": result.delivered_live,
                    "approval_id": result.approval_id,
                    "execution_session": None
                    if session is None
                    else session.model_dump(mode="json"),
                },
                ensure_ascii=False,
            )

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
                item.work_id for item in children if item.status.value not in _WORK_TERMINAL_VALUES
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
            name="work.delete",
            side_effect_level=SideEffectLevel.REVERSIBLE,
            tool_profile=ToolProfile.STANDARD,
            tool_group="delegation",
            tags=["work", "delete", "archive"],
            worker_types=["ops", "research", "dev", "general"],
            manifest_ref="builtin://work.delete",
            metadata={
                "entrypoints": ["agent_runtime", "web"],
                "runtime_kinds": ["worker", "subagent", "graph_agent"],
            },
        )
        async def work_delete(reason: str = "deleted by builtin tool") -> str:
            """软删除当前 work 及其已完成 child works。"""

            if self._delegation_plane is None:
                raise RuntimeError("delegation plane is not bound for work delete")
            _, context, _ = await _current_parent()
            if not context.work_id:
                raise RuntimeError("current execution context does not carry work_id")
            descendants = await self._delegation_plane.list_descendant_works(context.work_id)
            active = [
                item.work_id
                for item in descendants
                if item.status.value not in _WORK_TERMINAL_VALUES
            ]
            current = await store_group.work_store.get_work(context.work_id)
            if current is None:
                raise RuntimeError("current work no longer exists")
            if current.status.value not in _WORK_TERMINAL_VALUES:
                active.insert(0, current.work_id)
            if active:
                raise RuntimeError(f"work delete requires terminal status: {', '.join(active)}")
            deleted = await self._delegation_plane.delete_work(context.work_id, reason=reason)
            return json.dumps(
                {
                    "work_id": context.work_id,
                    "deleted": None if deleted is None else deleted.model_dump(mode="json"),
                    "child_work_ids": [item.work_id for item in descendants],
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
        async def web_fetch(
            url: str,
            timeout_seconds: float = 10.0,
            max_chars: int = 2000,
            link_limit: int = 10,
        ) -> str:
            """抓取网页内容摘要。"""

            page = await self._fetch_browser_page(url, timeout_seconds=timeout_seconds)
            return json.dumps(
                {
                    "url": page.current_url,
                    "final_url": page.final_url,
                    "status_code": page.status_code,
                    "content_type": page.content_type,
                    "title": page.title,
                    "body_preview": page.text_content[: max(100, min(max_chars, 20_000))],
                    "body_length": page.body_length,
                    "links": [
                        {"ref": item.ref, "text": item.text, "url": item.url}
                        for item in page.links[: max(1, min(link_limit, 20))]
                    ],
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
        async def browser_open(url: str, timeout_seconds: float = 10.0) -> str:
            """打开并缓存当前 execution context 的浏览器会话页面。"""

            context = get_current_execution_context()
            page = await self._browser_open_session(context, url, timeout_seconds=timeout_seconds)
            return json.dumps(
                self._browser_session_payload(page, action="open"),
                ensure_ascii=False,
            )

        @tool_contract(
            name="browser.status",
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="browser",
            tags=["browser", "status", "session"],
            worker_types=["ops", "research", "dev", "general"],
            manifest_ref="builtin://browser.status",
            metadata={
                "entrypoints": ["agent_runtime", "web"],
                "runtime_kinds": ["worker", "subagent", "graph_agent", "acp_runtime"],
            },
        )
        async def browser_status() -> str:
            """读取当前 execution context 的浏览器会话状态。"""

            page = self._get_browser_session(get_current_execution_context())
            if page is None:
                return json.dumps(
                    {
                        "status": "missing",
                        "supported_actions": ["open", "navigate", "snapshot", "click", "close"],
                    },
                    ensure_ascii=False,
                )
            return json.dumps(
                self._browser_session_payload(page, action="status"),
                ensure_ascii=False,
            )

        @tool_contract(
            name="browser.navigate",
            side_effect_level=SideEffectLevel.REVERSIBLE,
            tool_profile=ToolProfile.STANDARD,
            tool_group="browser",
            tags=["browser", "navigate", "url"],
            worker_types=["ops", "research", "dev", "general"],
            manifest_ref="builtin://browser.navigate",
            metadata={
                "entrypoints": ["agent_runtime"],
                "runtime_kinds": ["worker", "subagent", "graph_agent"],
            },
        )
        async def browser_navigate(url: str, timeout_seconds: float = 10.0) -> str:
            """导航当前浏览器会话到指定 URL。"""

            context = get_current_execution_context()
            page = await self._browser_open_session(context, url, timeout_seconds=timeout_seconds)
            return json.dumps(
                self._browser_session_payload(page, action="navigate"),
                ensure_ascii=False,
            )

        @tool_contract(
            name="browser.snapshot",
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="browser",
            tags=["browser", "snapshot", "dom"],
            worker_types=["ops", "research", "dev", "general"],
            manifest_ref="builtin://browser.snapshot",
            metadata={
                "entrypoints": ["agent_runtime"],
                "runtime_kinds": ["worker", "subagent", "graph_agent"],
            },
        )
        async def browser_snapshot(max_chars: int = 4000, link_limit: int = 20) -> str:
            """读取当前浏览器会话的文本快照与可点击 link refs。"""

            page = self._require_browser_session(get_current_execution_context())
            return json.dumps(
                self._browser_session_payload(
                    page,
                    action="snapshot",
                    max_chars=max_chars,
                    link_limit=link_limit,
                ),
                ensure_ascii=False,
            )

        @tool_contract(
            name="browser.act",
            side_effect_level=SideEffectLevel.REVERSIBLE,
            tool_profile=ToolProfile.STANDARD,
            tool_group="browser",
            tags=["browser", "act", "click"],
            worker_types=["ops", "research", "dev", "general"],
            manifest_ref="builtin://browser.act",
            metadata={
                "entrypoints": ["agent_runtime"],
                "runtime_kinds": ["worker", "subagent", "graph_agent"],
            },
        )
        async def browser_act(
            kind: str = "click",
            ref: str = "",
            timeout_seconds: float = 10.0,
        ) -> str:
            """执行最小浏览器动作，当前仅支持点击 link ref。"""

            if kind.strip().lower() != "click":
                raise RuntimeError("browser.act currently supports only kind=click")
            context = get_current_execution_context()
            page = self._require_browser_session(context)
            target = next((item for item in page.links if item.ref == ref.strip()), None)
            if target is None:
                raise RuntimeError(f"browser ref not found: {ref}")
            updated = await self._browser_open_session(
                context,
                target.url,
                timeout_seconds=timeout_seconds,
            )
            return json.dumps(
                {
                    **self._browser_session_payload(updated, action="click"),
                    "clicked": {"ref": target.ref, "text": target.text, "url": target.url},
                },
                ensure_ascii=False,
            )

        @tool_contract(
            name="browser.close",
            side_effect_level=SideEffectLevel.REVERSIBLE,
            tool_profile=ToolProfile.STANDARD,
            tool_group="browser",
            tags=["browser", "close", "session"],
            worker_types=["ops", "research", "dev", "general"],
            manifest_ref="builtin://browser.close",
            metadata={
                "entrypoints": ["agent_runtime"],
                "runtime_kinds": ["worker", "subagent", "graph_agent"],
            },
        )
        async def browser_close() -> str:
            """关闭当前 execution context 的浏览器会话。"""

            context = get_current_execution_context()
            closed = self._close_browser_session(context)
            return json.dumps(
                {
                    "session_id": self._browser_session_id(context),
                    "closed": closed,
                },
                ensure_ascii=False,
            )

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
            name="mcp.servers.list",
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="mcp",
            tags=["mcp", "servers", "discovery"],
            worker_types=["ops", "research", "dev", "general"],
            manifest_ref="builtin://mcp.servers.list",
            metadata={
                "entrypoints": ["agent_runtime", "web"],
                "runtime_kinds": ["worker", "subagent", "graph_agent", "acp_runtime"],
            },
        )
        async def mcp_servers_list() -> str:
            """列出当前已配置 MCP servers 及发现状态。"""

            if self._mcp_registry is None:
                return json.dumps({"status": "unbound", "servers": []}, ensure_ascii=False)
            return json.dumps(
                {
                    "config_path": str(self._mcp_registry.config_path),
                    "config_error": self._mcp_registry.last_config_error,
                    "servers": [
                        item.model_dump(mode="json") for item in self._mcp_registry.list_servers()
                    ],
                },
                ensure_ascii=False,
            )

        @tool_contract(
            name="mcp.tools.list",
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="mcp",
            tags=["mcp", "tools", "discovery"],
            worker_types=["ops", "research", "dev", "general"],
            manifest_ref="builtin://mcp.tools.list",
            metadata={
                "entrypoints": ["agent_runtime", "web"],
                "runtime_kinds": ["worker", "subagent", "graph_agent", "acp_runtime"],
            },
        )
        async def mcp_tools_list(server_name: str = "", limit: int = 50) -> str:
            """列出当前已发现并注册到 ToolBroker 的 MCP tools。"""

            if self._mcp_registry is None:
                return json.dumps({"status": "unbound", "tools": []}, ensure_ascii=False)
            tools = self._mcp_registry.list_tools(server_name=server_name)
            return json.dumps(
                {
                    "config_path": str(self._mcp_registry.config_path),
                    "config_error": self._mcp_registry.last_config_error,
                    "tools": [
                        item.model_dump(mode="json") for item in tools[: max(1, min(limit, 200))]
                    ],
                },
                ensure_ascii=False,
            )

        @tool_contract(
            name="mcp.tools.refresh",
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="mcp",
            tags=["mcp", "tools", "refresh"],
            worker_types=["ops", "research", "dev", "general"],
            manifest_ref="builtin://mcp.tools.refresh",
            metadata={
                "entrypoints": ["agent_runtime", "web"],
                "runtime_kinds": ["worker", "subagent", "graph_agent", "acp_runtime"],
            },
        )
        async def mcp_tools_refresh() -> str:
            """重新发现 MCP servers 并刷新 capability pack。"""

            if self._mcp_registry is None:
                return json.dumps({"status": "unbound", "tools": []}, ensure_ascii=False)
            await self.refresh()
            return json.dumps(
                {
                    "config_path": str(self._mcp_registry.config_path),
                    "config_error": self._mcp_registry.last_config_error,
                    "server_count": self._mcp_registry.configured_server_count(),
                    "healthy_server_count": self._mcp_registry.healthy_server_count(),
                    "registered_tool_count": self._mcp_registry.registered_tool_count(),
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

            project, workspace, _task = await _resolve_runtime_project_context(
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

            project, workspace, _task = await _resolve_runtime_project_context(
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

            project, workspace, _task = await _resolve_runtime_project_context(
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

        @tool_contract(
            name="memory.recall",
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="memory",
            tags=["memory", "recall", "context"],
            worker_types=["ops", "research", "dev", "general"],
            manifest_ref="builtin://memory.recall",
            metadata={
                "entrypoints": ["agent_runtime", "web"],
                "runtime_kinds": ["worker", "subagent", "graph_agent"],
            },
        )
        async def memory_recall(
            query: str,
            scope_id: str = "",
            project_id: str = "",
            workspace_id: str = "",
            limit: int = 4,
            allow_vault: bool = False,
            post_filter_mode: MemoryRecallPostFilterMode = (
                MemoryRecallPostFilterMode.KEYWORD_OVERLAP
            ),
            rerank_mode: MemoryRecallRerankMode = MemoryRecallRerankMode.HEURISTIC,
            subject_hint: str = "",
            focus_terms: list[str] | None = None,
        ) -> str:
            """生成结构化 recall pack。

            返回 query 扩展、命中、citation、backend truth 与 hook trace。
            """

            project, workspace, task = await _resolve_runtime_project_context(
                project_id=project_id,
                workspace_id=workspace_id,
            )
            memory_service = await self._memory_runtime_service.memory_service_for_scope(
                project=project,
                workspace=workspace,
            )
            scope_ids = await _resolve_memory_scope_ids(
                task=task,
                project=project,
                workspace=workspace,
                explicit_scope_id=scope_id,
            )
            if not scope_ids:
                empty = MemoryRecallResult(
                    query=query.strip(),
                    expanded_queries=[],
                    scope_ids=[],
                    hits=[],
                    backend_status=await memory_service.get_backend_status(),
                    degraded_reasons=["memory_scope_unresolved"],
                )
                return json.dumps(empty.model_dump(mode="json"), ensure_ascii=False)
            bounded_limit = max(1, min(limit, 8))
            hook_options = build_default_memory_recall_hook_options(
                subject_hint=subject_hint,
            ).model_copy(
                update={
                    "post_filter_mode": post_filter_mode,
                    "rerank_mode": rerank_mode,
                    "focus_terms": list(focus_terms or []),
                }
            )
            recall = await memory_service.recall_memory(
                scope_ids=scope_ids[:4],
                query=query,
                policy=MemoryAccessPolicy(allow_vault=allow_vault),
                per_scope_limit=min(4, bounded_limit),
                max_hits=bounded_limit,
                hook_options=MemoryRecallHookOptions.model_validate(hook_options),
            )
            return json.dumps(recall.model_dump(mode="json"), ensure_ascii=False)

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
            subagents_list,
            subagents_kill,
            subagents_steer,
            work_split,
            work_merge,
            work_delete,
            web_fetch,
            web_search,
            browser_open,
            browser_status,
            browser_navigate,
            browser_snapshot,
            browser_act,
            browser_close,
            gateway_inspect,
            cron_list,
            nodes_list,
            mcp_servers_list,
            mcp_tools_list,
            mcp_tools_refresh,
            pdf_inspect,
            image_inspect,
            tts_speak,
            canvas_write,
            memory_read,
            memory_search,
            memory_citations,
            memory_recall,
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
                default_tool_groups=["project", "session", "network", "browser", "memory", "mcp"],
                bootstrap_file_ids=["bootstrap:shared", "bootstrap:general"],
                runtime_kinds=[RuntimeKind.WORKER, RuntimeKind.SUBAGENT],
            ),
            WorkerType.OPS: WorkerCapabilityProfile(
                worker_type=WorkerType.OPS,
                capabilities=["ops", "runtime", "automation", "recovery"],
                default_model_alias="main",
                default_tool_profile="minimal",
                default_tool_groups=[
                    "runtime",
                    "session",
                    "project",
                    "automation",
                    "delegation",
                    "mcp",
                ],
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
                    "browser",
                    "memory",
                    "document",
                    "media",
                    "mcp",
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
                    "browser",
                    "document",
                    "media",
                    "mcp",
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
        mcp_status = (
            None if self._mcp_registry is None else self._mcp_registry.get_tool_status(tool_name)[0]
        )
        if mcp_status is not None:
            return mcp_status
        if tool_name in {"subagents.spawn", "work.split"} and self._task_runner is None:
            return BuiltinToolAvailabilityStatus.UNAVAILABLE
        if tool_name in {"subagents.kill", "subagents.steer"} and self._task_runner is None:
            return BuiltinToolAvailabilityStatus.UNAVAILABLE
        if tool_name in {"subagents.list", "subagents.kill", "work.merge", "work.delete"} and (
            self._delegation_plane is None
        ):
            return BuiltinToolAvailabilityStatus.UNAVAILABLE
        if tool_name in {"sessions.list", "session.status"} and self._task_runner is None:
            return BuiltinToolAvailabilityStatus.DEGRADED
        if tool_name in {"browser.status", "browser.snapshot", "browser.act", "browser.close"} and (
            not self._browser_sessions
        ):
            return BuiltinToolAvailabilityStatus.DEGRADED
        if tool_name in {"mcp.servers.list", "mcp.tools.list", "mcp.tools.refresh"}:
            if self._mcp_registry is None:
                return BuiltinToolAvailabilityStatus.UNAVAILABLE
            if not self._mcp_registry.has_enabled_servers():
                return BuiltinToolAvailabilityStatus.DEGRADED
            if self._mcp_registry.last_config_error:
                return BuiltinToolAvailabilityStatus.DEGRADED
            return BuiltinToolAvailabilityStatus.AVAILABLE
        if tool_name == "tts.speak" and not self._tts_binary():
            return BuiltinToolAvailabilityStatus.INSTALL_REQUIRED
        return BuiltinToolAvailabilityStatus.AVAILABLE

    def _resolve_tool_availability_reason(self, tool_name: str) -> str:
        if self._mcp_registry is not None:
            mcp_status, mcp_reason, _mcp_hint = self._mcp_registry.get_tool_status(tool_name)
            if mcp_status is not None:
                return mcp_reason
        if tool_name in {"subagents.spawn", "work.split"} and self._task_runner is None:
            return "task_runner_unbound"
        if tool_name in {"subagents.kill", "subagents.steer"} and self._task_runner is None:
            return "task_runner_unbound"
        if tool_name in {"subagents.list", "subagents.kill", "work.merge", "work.delete"} and (
            self._delegation_plane is None
        ):
            return "delegation_plane_unbound"
        if tool_name in {"sessions.list", "session.status"} and self._task_runner is None:
            return "execution_runtime_unbound"
        if tool_name in {"browser.status", "browser.snapshot", "browser.act", "browser.close"} and (
            not self._browser_sessions
        ):
            return "browser_session_missing"
        if tool_name in {"mcp.servers.list", "mcp.tools.list", "mcp.tools.refresh"}:
            if self._mcp_registry is None:
                return "mcp_registry_unbound"
            if self._mcp_registry.last_config_error:
                return "mcp_config_invalid"
            if not self._mcp_registry.has_enabled_servers():
                return "mcp_server_unconfigured"
            return ""
        if tool_name == "tts.speak" and not self._tts_binary():
            return "system_tts_binary_missing"
        return ""

    def _resolve_tool_install_hint(self, tool_name: str) -> str:
        if self._mcp_registry is not None:
            mcp_status, _mcp_reason, mcp_hint = self._mcp_registry.get_tool_status(tool_name)
            if mcp_status is not None:
                return mcp_hint
        if tool_name in {"mcp.servers.list", "mcp.tools.list", "mcp.tools.refresh"}:
            if self._mcp_registry is None:
                return "绑定 McpRegistryService 后才能发现 MCP servers"
            if self._mcp_registry.last_config_error:
                return "修复 MCP 配置文件格式后再刷新工具"
            if not self._mcp_registry.has_enabled_servers():
                return (
                    f"在 {self._mcp_registry.config_path} 配置 enabled 的 stdio MCP server 后再刷新"
                )
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
            "work.delete": ["agent_runtime", "web"],
            "subagents.list": ["agent_runtime", "web"],
            "subagents.kill": ["agent_runtime", "web"],
            "subagents.steer": ["agent_runtime", "web"],
            "mcp.servers.list": ["agent_runtime", "web"],
            "mcp.tools.list": ["agent_runtime", "web"],
            "mcp.tools.refresh": ["agent_runtime", "web"],
            "memory.read": ["agent_runtime", "web"],
            "memory.search": ["agent_runtime", "web"],
            "memory.citations": ["agent_runtime", "web"],
            "memory.recall": ["agent_runtime", "web"],
        }
        if tool_name.startswith("mcp."):
            return ["agent_runtime", "web"]
        return explicit.get(tool_name, ["agent_runtime"])

    @staticmethod
    def _resolve_tool_runtime_kinds(tool_name: str) -> list[RuntimeKind]:
        if tool_name == "subagents.spawn":
            return [RuntimeKind.SUBAGENT, RuntimeKind.GRAPH_AGENT]
        if tool_name in {"subagents.list", "subagents.kill", "subagents.steer"}:
            return [RuntimeKind.WORKER, RuntimeKind.SUBAGENT, RuntimeKind.GRAPH_AGENT]
        if tool_name in {"gateway.inspect", "cron.list", "nodes.list", "runtime.inspect"}:
            return [RuntimeKind.WORKER, RuntimeKind.ACP_RUNTIME]
        if tool_name in {"mcp.servers.list", "mcp.tools.list", "mcp.tools.refresh"}:
            return [
                RuntimeKind.WORKER,
                RuntimeKind.SUBAGENT,
                RuntimeKind.GRAPH_AGENT,
                RuntimeKind.ACP_RUNTIME,
            ]
        if tool_name.startswith("mcp."):
            return [
                RuntimeKind.WORKER,
                RuntimeKind.SUBAGENT,
                RuntimeKind.GRAPH_AGENT,
                RuntimeKind.ACP_RUNTIME,
            ]
        return [RuntimeKind.WORKER, RuntimeKind.SUBAGENT, RuntimeKind.GRAPH_AGENT]

    @staticmethod
    def _validate_remote_url(url: str) -> str:
        normalized = url.strip()
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise RuntimeError("url must be a valid http/https address")
        return normalized

    @staticmethod
    def _parse_browser_snapshot(
        base_url: str,
        html: str,
        *,
        link_limit: int = 40,
    ) -> _BrowserSnapshot:
        parser = _HtmlSnapshotParser(base_url=base_url, link_limit=link_limit)
        parser.feed(html)
        parser.close()
        return parser.snapshot()

    async def _fetch_browser_page(
        self,
        url: str,
        *,
        timeout_seconds: float = 10.0,
    ) -> _BrowserSessionState:
        normalized_url = self._validate_remote_url(url)
        async with httpx.AsyncClient(
            timeout=max(0.1, timeout_seconds),
            headers={"User-Agent": "OctoAgent Browser Tool/0.1"},
        ) as client:
            response = await client.get(normalized_url, follow_redirects=True)
        html = response.text[:200_000]
        final_url = str(response.url)
        snapshot = self._parse_browser_snapshot(final_url, html)
        return _BrowserSessionState(
            session_id="",
            task_id="",
            work_id="",
            current_url=normalized_url,
            final_url=final_url,
            status_code=response.status_code,
            content_type=response.headers.get("content-type", ""),
            title=snapshot.title,
            text_content=snapshot.text,
            html_preview=html[:10_000],
            body_length=len(response.text),
            links=snapshot.links,
        )

    @staticmethod
    def _browser_session_scope_key(context) -> str:
        return context.work_id or context.task_id

    @staticmethod
    def _browser_session_id(context) -> str:
        scope = context.work_id or context.task_id
        return f"browser:{scope}"

    def _get_browser_session(self, context) -> _BrowserSessionState | None:
        return self._browser_sessions.get(self._browser_session_scope_key(context))

    def _require_browser_session(self, context) -> _BrowserSessionState:
        session = self._get_browser_session(context)
        if session is None:
            raise RuntimeError("browser session is not initialized; call browser.open first")
        return session

    async def _browser_open_session(
        self,
        context,
        url: str,
        *,
        timeout_seconds: float = 10.0,
    ) -> _BrowserSessionState:
        fetched = await self._fetch_browser_page(url, timeout_seconds=timeout_seconds)
        session = _BrowserSessionState(
            session_id=self._browser_session_id(context),
            task_id=context.task_id,
            work_id=context.work_id,
            current_url=url.strip(),
            final_url=fetched.final_url,
            status_code=fetched.status_code,
            content_type=fetched.content_type,
            title=fetched.title,
            text_content=fetched.text_content,
            html_preview=fetched.html_preview,
            body_length=fetched.body_length,
            links=fetched.links,
        )
        self._browser_sessions[self._browser_session_scope_key(context)] = session
        return session

    def _close_browser_session(self, context) -> bool:
        return (
            self._browser_sessions.pop(self._browser_session_scope_key(context), None) is not None
        )

    @staticmethod
    def _browser_session_payload(
        session: _BrowserSessionState,
        *,
        action: str,
        max_chars: int = 4000,
        link_limit: int = 20,
    ) -> dict[str, Any]:
        effective_chars = max(100, min(max_chars, 20_000))
        effective_links = max(1, min(link_limit, 20))
        return {
            "action": action,
            "session_id": session.session_id,
            "task_id": session.task_id,
            "work_id": session.work_id,
            "url": session.current_url,
            "final_url": session.final_url,
            "status_code": session.status_code,
            "content_type": session.content_type,
            "title": session.title,
            "body_length": session.body_length,
            "text_preview": session.text_content[:effective_chars],
            "links": [
                {"ref": item.ref, "text": item.text, "url": item.url}
                for item in session.links[:effective_links]
            ],
            "supported_actions": ["click", "navigate", "snapshot", "close"],
        }

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
