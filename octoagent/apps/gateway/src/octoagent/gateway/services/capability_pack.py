"""Feature 030: bundled capability pack / ToolIndex / bootstrap。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from octoagent.core.models import (
    BundledCapabilityPack,
    BundledSkillDefinition,
    BundledToolDefinition,
    DynamicToolSelection,
    RuntimeKind,
    ToolIndexQuery,
    WorkerBootstrapFile,
    WorkerCapabilityProfile,
    WorkerType,
)
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

    @property
    def tool_broker(self) -> ToolBroker:
        return self._tool_broker

    @property
    def skill_registry(self) -> SkillRegistry:
        return self._skill_registry

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
            tool.tool_name for tool in tools if tool.tool_profile in {"minimal", "standard"}
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
        return {
            "backend": self._tool_index.backend_name,
            "degraded_reason": pack.degraded_reason,
            "worker_profiles": [item.model_dump(mode="json") for item in pack.worker_profiles],
        }

    def build_skill_registry_document(self) -> list[BundledSkillDefinition]:
        if self._pack is None:
            return []
        return list(self._pack.skills)

    async def _register_builtin_tools(self) -> None:
        store_group = self._stores

        @tool_contract(
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="project",
            tags=["project", "workspace", "context"],
            worker_types=["ops", "research", "dev", "general"],
            manifest_ref="builtin://project.inspect",
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
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="session",
            tags=["task", "session", "status"],
            worker_types=["ops", "research", "dev"],
            manifest_ref="builtin://task.inspect",
        )
        async def task_inspect(task_id: str) -> str:
            """读取任务投影与最近 execution 概览。"""

            task = await store_group.task_store.get_task(task_id)
            if task is None:
                return json.dumps({"task_id": task_id, "status": "missing"}, ensure_ascii=False)
            events = await store_group.event_store.get_events_for_task(task_id)
            return json.dumps(
                {
                    "task": task.model_dump(mode="json"),
                    "event_count": len(events),
                    "latest_event_id": events[-1].event_id if events else "",
                },
                ensure_ascii=False,
            )

        @tool_contract(
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="artifact",
            tags=["artifact", "history", "output"],
            worker_types=["research", "dev"],
            manifest_ref="builtin://artifact.list",
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
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="runtime",
            tags=["runtime", "diagnostics", "health"],
            worker_types=["ops"],
            manifest_ref="builtin://runtime.inspect",
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
                },
                ensure_ascii=False,
            )

        @tool_contract(
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="session",
            tags=["work", "delegation", "ownership"],
            worker_types=["ops", "research", "dev"],
            manifest_ref="builtin://work.inspect",
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
            return json.dumps(
                {
                    "work": work.model_dump(mode="json"),
                    "pipeline_run": None if run is None else run.model_dump(mode="json"),
                },
                ensure_ascii=False,
            )

        for handler in (
            project_inspect,
            task_inspect,
            artifact_list,
            runtime_inspect,
            work_inspect,
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
                default_tool_groups=["project", "session"],
                bootstrap_file_ids=["bootstrap:shared", "bootstrap:general"],
                runtime_kinds=[RuntimeKind.WORKER, RuntimeKind.SUBAGENT],
            ),
            WorkerType.OPS: WorkerCapabilityProfile(
                worker_type=WorkerType.OPS,
                capabilities=["ops", "runtime", "automation", "recovery"],
                default_model_alias="main",
                default_tool_profile="minimal",
                default_tool_groups=["runtime", "session", "project"],
                bootstrap_file_ids=["bootstrap:shared", "bootstrap:ops"],
                runtime_kinds=[RuntimeKind.WORKER, RuntimeKind.ACP_RUNTIME],
            ),
            WorkerType.RESEARCH: WorkerCapabilityProfile(
                worker_type=WorkerType.RESEARCH,
                capabilities=["research", "analysis", "summarize"],
                default_model_alias="main",
                default_tool_profile="minimal",
                default_tool_groups=["project", "artifact", "session"],
                bootstrap_file_ids=["bootstrap:shared", "bootstrap:research"],
                runtime_kinds=[RuntimeKind.WORKER, RuntimeKind.SUBAGENT],
            ),
            WorkerType.DEV: WorkerCapabilityProfile(
                worker_type=WorkerType.DEV,
                capabilities=["dev", "code", "patch", "test"],
                default_model_alias="main",
                default_tool_profile="minimal",
                default_tool_groups=["project", "artifact", "session"],
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
