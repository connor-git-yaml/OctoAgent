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
from collections.abc import Mapping
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
    EffectiveToolUniverse,
    NormalizedMessage,
    OwnerProfile,
    ProjectBindingType,
    RuntimeKind,
    ToolAvailabilityExplanation,
    ToolIndexQuery,
    WorkerBootstrapFile,
    WorkerCapabilityProfile,
    WorkerProfileStatus,
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
    profile_allows,
    reflect_tool_schema,
    tool_contract,
)
from pydantic import BaseModel, Field
from ulid import ULID

from .agent_context import build_ambient_runtime_facts, build_default_memory_recall_hook_options
from .execution_context import get_current_execution_context
from .task_service import TaskService

if TYPE_CHECKING:
    from .mcp_registry import McpRegistryService

_DEFAULT_SKILL_PROVIDER_CONFIG_PATH = Path("data/ops/skill-providers.json")


class SkillProviderConfig(BaseModel):
    provider_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    description: str = Field(default="")
    enabled: bool = True
    model_alias: str = Field(default="main")
    worker_type: str = Field(default="general")
    tool_profile: str = Field(default="minimal")
    tools_allowed: list[str] = Field(default_factory=list)
    prompt_template: str = Field(min_length=1)
    install_hint: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)


class _BuiltinSkillInput(BaseModel):
    objective: str = Field(min_length=1)
    context: dict[str, Any] = Field(default_factory=dict)


class _BuiltinSkillOutput(BaseModel):
    content: str = ""
    complete: bool = True
    skip_remaining_tools: bool = True
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class _WorkerPlanAssignment(BaseModel):
    objective: str = Field(min_length=1)
    worker_type: str = Field(default="research")
    target_kind: str = Field(default="subagent")
    tool_profile: str = Field(default="minimal")
    title: str = Field(default="")
    reason: str = Field(default="")


class _WorkerPlanProposal(BaseModel):
    plan_id: str = Field(min_length=1)
    work_id: str = Field(default="")
    task_id: str = Field(default="")
    proposal_kind: str = Field(default="split")
    objective: str = Field(default="")
    summary: str = Field(default="")
    requires_user_confirmation: bool = True
    assignments: list[_WorkerPlanAssignment] = Field(default_factory=list)
    merge_candidate_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


@dataclass(slots=True)
class _ResolvedWorkerBinding:
    profile_id: str
    profile_revision: int
    worker_type: WorkerType
    tool_profile: str
    default_tool_groups: list[str]
    selected_tools: list[str]
    source_kind: str
    profile_name: str


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

    @property
    def mcp_registry(self) -> McpRegistryService | None:
        return self._mcp_registry

    async def startup(self) -> None:
        if self._bootstrapped:
            return
        await self._register_builtin_tools()
        if self._mcp_registry is not None:
            await self._mcp_registry.startup()
        await self.refresh()
        self._bootstrapped = True

    async def refresh(self) -> BundledCapabilityPack:
        if self._mcp_registry is not None:
            await self._mcp_registry.refresh()
        self._rebuild_skill_registry()
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
                label=(
                    str(manifest.metadata.get("label", "")).strip()
                    or manifest.skill_id.replace("_", " ").title()
                ),
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

    async def get_pack(
        self,
        *,
        project_id: str = "",
        workspace_id: str = "",
        profile_id: str = "",
    ) -> BundledCapabilityPack:
        await self.startup()
        if self._pack is None:
            self._pack = await self.refresh()
        if not project_id and not workspace_id and not profile_id:
            return self._pack
        return await self._filter_pack_for_scope(
            self._pack,
            project_id=project_id,
            workspace_id=workspace_id,
            profile_id=profile_id,
        )

    def get_worker_profile(self, worker_type: WorkerType) -> WorkerCapabilityProfile:
        return self._profile_map.get(worker_type, self._profile_map[WorkerType.GENERAL])

    async def resolve_worker_binding(
        self,
        *,
        requested_profile_id: str = "",
        fallback_worker_type: WorkerType = WorkerType.GENERAL,
    ) -> _ResolvedWorkerBinding:
        await self.startup()
        normalized_profile_id = requested_profile_id.strip()
        if normalized_profile_id:
            builtin_worker_type = self._builtin_worker_type_from_profile_id(normalized_profile_id)
            if builtin_worker_type is not None:
                builtin_profile = self.get_worker_profile(builtin_worker_type)
                return _ResolvedWorkerBinding(
                    profile_id=normalized_profile_id,
                    profile_revision=1,
                    worker_type=builtin_worker_type,
                    tool_profile=builtin_profile.default_tool_profile,
                    default_tool_groups=list(builtin_profile.default_tool_groups),
                    selected_tools=[],
                    source_kind="builtin_singleton",
                    profile_name=self._worker_profile_label(builtin_worker_type.value),
                )
            stored_profile = await self._stores.agent_context_store.get_worker_profile(
                normalized_profile_id
            )
            if stored_profile is not None and stored_profile.status != WorkerProfileStatus.ARCHIVED:
                worker_type = self._coerce_worker_type_name(stored_profile.base_archetype)
                builtin_profile = self.get_worker_profile(worker_type)
                return _ResolvedWorkerBinding(
                    profile_id=stored_profile.profile_id,
                    profile_revision=(
                        stored_profile.active_revision or stored_profile.draft_revision or 1
                    ),
                    worker_type=worker_type,
                    tool_profile=stored_profile.tool_profile or builtin_profile.default_tool_profile,
                    default_tool_groups=list(
                        stored_profile.default_tool_groups or builtin_profile.default_tool_groups
                    ),
                    selected_tools=list(stored_profile.selected_tools),
                    source_kind="worker_profile",
                    profile_name=stored_profile.name,
                )
            agent_profile = await self._stores.agent_context_store.get_agent_profile(
                normalized_profile_id
            )
            if agent_profile is not None:
                builtin_profile = self.get_worker_profile(fallback_worker_type)
                return _ResolvedWorkerBinding(
                    profile_id=agent_profile.profile_id,
                    profile_revision=agent_profile.version,
                    worker_type=fallback_worker_type,
                    tool_profile=(
                        agent_profile.tool_profile or builtin_profile.default_tool_profile
                    ),
                    default_tool_groups=list(builtin_profile.default_tool_groups),
                    selected_tools=[],
                    source_kind="agent_profile",
                    profile_name=agent_profile.name,
                )
        builtin_profile = self.get_worker_profile(fallback_worker_type)
        return _ResolvedWorkerBinding(
            profile_id=f"singleton:{builtin_profile.worker_type.value}",
            profile_revision=1,
            worker_type=builtin_profile.worker_type,
            tool_profile=builtin_profile.default_tool_profile,
            default_tool_groups=list(builtin_profile.default_tool_groups),
            selected_tools=[],
            source_kind="builtin_fallback",
            profile_name=self._worker_profile_label(builtin_profile.worker_type.value),
        )

    async def resolve_worker_type_for_profile(self, profile_id: str) -> WorkerType | None:
        normalized = profile_id.strip()
        if not normalized:
            return None
        builtin_worker_type = self._builtin_worker_type_from_profile_id(normalized)
        if builtin_worker_type is not None:
            return builtin_worker_type
        stored_profile = await self._stores.agent_context_store.get_worker_profile(normalized)
        if stored_profile is None or stored_profile.status == WorkerProfileStatus.ARCHIVED:
            return None
        return self._coerce_worker_type_name(stored_profile.base_archetype)

    async def select_tools(
        self,
        request: ToolIndexQuery,
        *,
        worker_type: WorkerType,
    ) -> DynamicToolSelection:
        await self.startup()
        profile = self.get_worker_profile(worker_type)
        effective_request = request.model_copy(
            update={
                "tool_groups": request.tool_groups or profile.default_tool_groups,
                "worker_type": request.worker_type or worker_type,
                "tool_profile": request.tool_profile or profile.default_tool_profile,
            }
        )
        pack = await self.get_pack(
            project_id=effective_request.project_id,
            workspace_id=effective_request.workspace_id,
        )
        fallback = self._resolve_fallback_toolset_from_pack(pack, worker_type)
        raw_selection = await self._tool_index.select_tools(
            effective_request,
            static_fallback=fallback,
        )
        return self._restrict_selection_to_pack(
            raw_selection,
            pack=pack,
            fallback=fallback,
        )

    async def resolve_profile_first_tools(
        self,
        request: ToolIndexQuery,
        *,
        worker_type: WorkerType,
        requested_profile_id: str = "",
    ) -> DynamicToolSelection:
        await self.startup()
        binding = await self.resolve_worker_binding(
            requested_profile_id=requested_profile_id,
            fallback_worker_type=worker_type,
        )
        pack = await self.get_pack(
            project_id=request.project_id,
            workspace_id=request.workspace_id,
            profile_id=binding.profile_id,
        )
        effective_tool_profile = request.tool_profile or binding.tool_profile
        context_profile = self._coerce_tool_profile(effective_tool_profile)
        tool_by_name = {tool.tool_name: tool for tool in pack.tools}
        if self._requires_weather_toolset(request.query, binding.worker_type):
            desired_tools = self._dedupe_preserve_order(
                [
                    *binding.selected_tools,
                    *self._profile_first_core_tool_names(),
                    "runtime.now",
                    "web.search",
                    "web.fetch",
                ]
            )
        else:
            desired_tools = self._dedupe_preserve_order(
                [
                    *binding.selected_tools,
                    *self._profile_first_core_tool_names(),
                    *[
                        tool.tool_name
                        for tool in pack.tools
                        if tool.tool_group in binding.default_tool_groups
                    ],
                ]
            )
        mounted_tools: list[ToolAvailabilityExplanation] = []
        blocked_tools: list[ToolAvailabilityExplanation] = []
        mounted_names: list[str] = []
        warnings: list[str] = []

        for tool_name in desired_tools:
            bundled = tool_by_name.get(tool_name)
            source_kind = self._resolve_profile_first_source_kind(binding, tool_name)
            if bundled is None:
                blocked_tools.append(
                    ToolAvailabilityExplanation(
                        tool_name=tool_name,
                        status="missing",
                        source_kind=source_kind,
                        reason_code="tool_not_in_scope_pack",
                        summary="当前 project / workspace 治理面没有暴露这个工具。",
                        recommended_action="检查技能治理、MCP 配置或 Root Agent 静态配置。",
                    )
                )
                warnings.append("profile_first_tool_missing_from_pack")
                continue
            tool_profile = self._coerce_tool_profile(bundled.tool_profile)
            if not profile_allows(tool_profile, context_profile):
                blocked_tools.append(
                    ToolAvailabilityExplanation(
                        tool_name=tool_name,
                        status="blocked",
                        source_kind=source_kind,
                        tool_group=bundled.tool_group,
                        tool_profile=bundled.tool_profile,
                        reason_code="tool_profile_not_allowed",
                        summary=(
                            f"当前 Root Agent 允许的 tool_profile={context_profile.value}，"
                            f"不足以挂载 {bundled.tool_profile} 工具。"
                        ),
                        recommended_action="提升 Root Agent 的 tool_profile，或移除该工具依赖。",
                        metadata={"entrypoints": list(bundled.entrypoints)},
                    )
                )
                continue
            if bundled.availability not in {
                BuiltinToolAvailabilityStatus.AVAILABLE,
                BuiltinToolAvailabilityStatus.DEGRADED,
            }:
                blocked_tools.append(
                    ToolAvailabilityExplanation(
                        tool_name=tool_name,
                        status=bundled.availability.value,
                        source_kind=source_kind,
                        tool_group=bundled.tool_group,
                        tool_profile=bundled.tool_profile,
                        reason_code=bundled.availability_reason,
                        summary=bundled.description or bundled.label or tool_name,
                        recommended_action=bundled.install_hint,
                        metadata={"entrypoints": list(bundled.entrypoints)},
                    )
                )
                warnings.append("profile_first_tool_unavailable")
                continue
            mounted_names.append(tool_name)
            mounted_tools.append(
                ToolAvailabilityExplanation(
                    tool_name=tool_name,
                    status=(
                        "degraded"
                        if bundled.availability == BuiltinToolAvailabilityStatus.DEGRADED
                        else "mounted"
                    ),
                    source_kind=source_kind,
                    tool_group=bundled.tool_group,
                    tool_profile=bundled.tool_profile,
                    reason_code=bundled.availability_reason,
                    summary=bundled.description or bundled.label or tool_name,
                    recommended_action=bundled.install_hint,
                    metadata={
                        "entrypoints": list(bundled.entrypoints),
                        "runtime_kinds": [item.value for item in bundled.runtime_kinds],
                    },
                )
            )

        if not mounted_names:
            warnings.append("profile_first_empty_fallback_to_static_toolset")
            for tool_name in self._resolve_fallback_toolset_from_pack(pack, binding.worker_type):
                bundled = tool_by_name.get(tool_name)
                if bundled is None:
                    continue
                if not profile_allows(self._coerce_tool_profile(bundled.tool_profile), context_profile):
                    continue
                if bundled.availability not in {
                    BuiltinToolAvailabilityStatus.AVAILABLE,
                    BuiltinToolAvailabilityStatus.DEGRADED,
                }:
                    continue
                mounted_names.append(tool_name)
                mounted_tools.append(
                    ToolAvailabilityExplanation(
                        tool_name=tool_name,
                        status=(
                            "degraded"
                            if bundled.availability == BuiltinToolAvailabilityStatus.DEGRADED
                            else "mounted"
                        ),
                        source_kind="fallback_toolset",
                        tool_group=bundled.tool_group,
                        tool_profile=bundled.tool_profile,
                        reason_code=bundled.availability_reason,
                        summary=bundled.description or bundled.label or tool_name,
                        recommended_action=bundled.install_hint,
                        metadata={
                            "entrypoints": list(bundled.entrypoints),
                            "runtime_kinds": [item.value for item in bundled.runtime_kinds],
                        },
                    )
                )

        discovery_request = request.model_copy(
            update={
                "limit": max(6, min(12, request.limit)),
                "worker_type": binding.worker_type,
                "tool_profile": context_profile.value,
                "tool_groups": [],
            }
        )
        discovery = await self._tool_index.select_tools(discovery_request)
        discovery_entrypoints = self._dedupe_preserve_order(
            [
                tool_name
                for tool_name in mounted_names
                if tool_name in self._profile_first_discovery_tool_names()
            ]
            + [
                hit.tool_name
                for hit in discovery.hits
                if hit.tool_name in mounted_names
            ]
        )

        return DynamicToolSelection(
            selection_id=str(ULID()),
            query=request,
            selected_tools=mounted_names,
            hits=discovery.hits,
            backend=self._tool_index.backend_name,
            is_fallback="profile_first_empty_fallback_to_static_toolset" in warnings,
            warnings=self._dedupe_preserve_order(warnings),
            resolution_mode="profile_first_core",
            effective_tool_universe=EffectiveToolUniverse(
                profile_id=binding.profile_id,
                profile_revision=binding.profile_revision,
                worker_type=binding.worker_type.value,
                tool_profile=context_profile.value,
                resolution_mode="profile_first_core",
                selected_tools=mounted_names,
                discovery_entrypoints=discovery_entrypoints,
                warnings=self._dedupe_preserve_order(warnings),
            ),
            mounted_tools=mounted_tools,
            blocked_tools=blocked_tools,
        )

    async def render_bootstrap_context(
        self,
        *,
        worker_type: WorkerType,
        project_id: str = "",
        workspace_id: str = "",
        surface: str = "",
    ) -> list[dict[str, Any]]:
        await self.startup()
        project, workspace = await self._resolve_project_context(
            project_id=project_id,
            workspace_id=workspace_id,
        )
        owner_profile = await self._resolve_owner_profile()
        worker_profile = self.get_worker_profile(worker_type)
        ambient_runtime, ambient_reasons = build_ambient_runtime_facts(
            owner_profile=owner_profile,
            surface=surface or "chat",
        )
        replacements = {
            "{{project_id}}": project.project_id if project is not None else "",
            "{{project_slug}}": project.slug if project is not None else "default",
            "{{project_name}}": project.name if project is not None else "Default Project",
            "{{workspace_id}}": workspace.workspace_id if workspace is not None else "",
            "{{workspace_slug}}": workspace.slug if workspace is not None else "primary",
            "{{workspace_root}}": workspace.root_path if workspace is not None else "",
            "{{current_datetime_local}}": ambient_runtime["current_datetime_local"],
            "{{current_date_local}}": ambient_runtime["current_date_local"],
            "{{current_time_local}}": ambient_runtime["current_time_local"],
            "{{current_weekday_local}}": ambient_runtime["current_weekday_local"],
            "{{owner_timezone}}": ambient_runtime["timezone"],
            "{{owner_utc_offset}}": ambient_runtime["utc_offset"],
            "{{owner_locale}}": ambient_runtime["locale"],
            "{{surface}}": ambient_runtime["surface"],
            "{{ambient_source}}": ambient_runtime["source"],
            "{{ambient_degraded_reasons}}": ", ".join(ambient_reasons) or "none",
            "{{worker_type}}": worker_type.value,
            "{{worker_capabilities}}": ", ".join(worker_profile.capabilities) or "none",
            "{{default_tool_profile}}": worker_profile.default_tool_profile,
            "{{default_tool_groups}}": ", ".join(worker_profile.default_tool_groups) or "none",
            "{{runtime_kinds}}": ", ".join(item.value for item in worker_profile.runtime_kinds)
            or "none",
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

    async def _resolve_owner_profile(self) -> OwnerProfile | None:
        return await self._stores.agent_context_store.get_owner_profile("owner-profile-default")

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
            "skill_providers": {
                "config_path": str(self._resolve_skill_provider_config_path()),
                "installed_count": len(self.list_skill_provider_configs()),
            },
        }

    def list_skill_provider_configs(self) -> list[SkillProviderConfig]:
        return self._load_skill_provider_configs()

    def save_skill_provider_config(self, config: SkillProviderConfig) -> None:
        configs = {item.provider_id: item for item in self._load_skill_provider_configs()}
        configs[config.provider_id] = config
        self._write_skill_provider_configs(list(configs.values()))

    def delete_skill_provider_config(self, provider_id: str) -> bool:
        configs = {item.provider_id: item for item in self._load_skill_provider_configs()}
        removed = configs.pop(provider_id, None)
        if removed is None:
            return False
        self._write_skill_provider_configs(list(configs.values()))
        return True

    async def review_worker_plan(
        self,
        *,
        work_id: str,
        objective: str = "",
    ) -> _WorkerPlanProposal:
        if self._delegation_plane is None:
            raise RuntimeError("delegation plane is not bound for worker review")
        work = await self._stores.work_store.get_work(work_id)
        if work is None:
            raise RuntimeError(f"work not found: {work_id}")
        task = await self._stores.task_store.get_task(work.task_id)
        if task is None:
            raise RuntimeError(f"task not found for work: {work.task_id}")
        descendants = await self._delegation_plane.list_descendant_works(work_id)
        proposal_objective = objective.strip() or work.title or task.title
        fragments = self._split_worker_objectives(proposal_objective)
        if not fragments:
            fragments = [proposal_objective or "review current work and propose next action"]

        active_descendants = [
            item for item in descendants if item.status.value not in _WORK_TERMINAL_VALUES
        ]
        terminal_descendants = [
            item for item in descendants if item.status.value in _WORK_TERMINAL_VALUES
        ]
        if (
            descendants
            and not objective.strip()
            and terminal_descendants
            and not active_descendants
        ):
            proposal_kind = "merge"
        elif descendants and objective.strip():
            proposal_kind = "repartition"
        else:
            proposal_kind = "split"

        assignments = (
            [
                self._build_worker_assignment(item, index=index)
                for index, item in enumerate(fragments, 1)
            ]
            if proposal_kind in {"split", "repartition"}
            else []
        )
        warnings: list[str] = []
        if proposal_kind == "merge" and active_descendants:
            warnings.append("仍有 child works 在运行，当前不能直接 merge。")
        if proposal_kind == "repartition" and active_descendants:
            warnings.append("apply 时会先取消当前仍在运行的 child works，再按新计划重划分。")
        if not descendants and proposal_kind == "merge":
            warnings.append("当前 work 还没有 child works，merge 不会生效。")

        summary = {
            "merge": "建议合并已完成的 child works，并回收当前父 work。",
            "repartition": "建议先收拢现有 child works，再按新计划重新划分 worker。",
            "split": "建议按可执行子任务拆分给具体 worker，而不是让主 Agent 直接动手。",
        }[proposal_kind]
        return _WorkerPlanProposal(
            plan_id=str(ULID()),
            work_id=work.work_id,
            task_id=work.task_id,
            proposal_kind=proposal_kind,
            objective=proposal_objective,
            summary=summary,
            assignments=assignments,
            merge_candidate_ids=[item.work_id for item in terminal_descendants],
            warnings=warnings,
        )

    async def apply_worker_plan(
        self,
        *,
        plan: dict[str, Any] | _WorkerPlanProposal,
        actor: str = "control_plane",
    ) -> dict[str, Any]:
        if self._delegation_plane is None:
            raise RuntimeError("delegation plane is not bound for worker apply")
        if self._task_runner is None:
            raise RuntimeError("task runner is not bound for worker apply")
        proposal = (
            plan
            if isinstance(plan, _WorkerPlanProposal)
            else _WorkerPlanProposal.model_validate(plan)
        )
        work = await self._stores.work_store.get_work(proposal.work_id)
        if work is None:
            raise RuntimeError(f"work not found: {proposal.work_id}")
        task = await self._stores.task_store.get_task(work.task_id)
        if task is None:
            raise RuntimeError(f"task not found for work: {work.task_id}")

        descendants = await self._delegation_plane.list_descendant_works(work.work_id)
        cancelled_work_ids: list[str] = []
        if proposal.proposal_kind == "repartition":
            for child in descendants:
                if child.status.value in _WORK_TERMINAL_VALUES:
                    continue
                await self._task_runner.cancel_task(child.task_id)
                await self._delegation_plane.cancel_work(
                    child.work_id,
                    reason=f"worker_review_repartition:{actor}",
                )
                cancelled_work_ids.append(child.work_id)
        if proposal.proposal_kind == "merge":
            merged = await self._delegation_plane.merge_work(
                work.work_id,
                summary=f"worker review approved by {actor}",
            )
            return {
                "plan_id": proposal.plan_id,
                "proposal_kind": proposal.proposal_kind,
                "cancelled_work_ids": cancelled_work_ids,
                "child_tasks": [],
                "merged_work": None if merged is None else merged.model_dump(mode="json"),
            }

        child_tasks = [
            await self._launch_child_task(
                parent_task=task,
                parent_work=work,
                objective=item.objective,
                worker_type=item.worker_type,
                target_kind=item.target_kind,
                tool_profile=item.tool_profile,
                title=item.title,
                spawned_by="worker_review_apply",
                plan_id=proposal.plan_id,
            )
            for item in proposal.assignments
        ]
        return {
            "plan_id": proposal.plan_id,
            "proposal_kind": proposal.proposal_kind,
            "cancelled_work_ids": cancelled_work_ids,
            "child_tasks": child_tasks,
            "merged_work": None,
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
            tool_profile: str = "minimal",
            title: str = "",
        ) -> dict[str, Any]:
            context, parent_task = await _current_work_context()
            parent_work = await store_group.work_store.get_work(context.work_id)
            if parent_work is None:
                raise RuntimeError(f"current work not found: {context.work_id}")
            return await self._launch_child_task(
                parent_task=parent_task,
                parent_work=parent_work,
                objective=objective,
                worker_type=worker_type,
                target_kind=target_kind,
                tool_profile=tool_profile,
                title=title,
                spawned_by="builtin_tool",
            )

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
            name="runtime.now",
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="session",
            tags=["runtime", "time", "clock"],
            worker_types=["ops", "research", "dev", "general"],
            manifest_ref="builtin://runtime.now",
            metadata={
                "entrypoints": ["agent_runtime"],
                "runtime_kinds": ["worker", "subagent", "graph_agent", "acp_runtime"],
            },
        )
        async def runtime_now(timezone: str = "", locale: str = "") -> str:
            """读取当前本地时间、日期和时区摘要。"""

            context = get_current_execution_context()
            owner_profile = await self._resolve_owner_profile()
            if timezone.strip() or locale.strip():
                base_profile = owner_profile or OwnerProfile(
                    owner_profile_id="owner-profile-default",
                    timezone="",
                    locale="",
                )
                owner_profile = base_profile.model_copy(
                    update={
                        "timezone": timezone.strip() or base_profile.timezone,
                        "locale": locale.strip() or base_profile.locale,
                    }
                )
            facts, degraded_reasons = build_ambient_runtime_facts(
                owner_profile=owner_profile,
                surface=(
                    context.runtime_context.surface
                    if context.runtime_context is not None
                    else "chat"
                ),
            )
            return json.dumps(
                {
                    **facts,
                    "degraded_reasons": degraded_reasons,
                },
                ensure_ascii=False,
            )

        @tool_contract(
            name="work.inspect",
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="supervision",
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
                tool_profile=self._effective_tool_profile_for_objective(
                    self._coerce_worker_type_name(worker_type),
                    objective=objective,
                ),
                title=title,
            )
            return json.dumps(payload, ensure_ascii=False)

        @tool_contract(
            name="subagents.list",
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="supervision",
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
            name="workers.review",
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="supervision",
            tags=["worker", "review", "governance"],
            worker_types=["general", "ops"],
            manifest_ref="builtin://workers.review",
            metadata={
                "entrypoints": ["agent_runtime", "web"],
                "runtime_kinds": ["worker", "subagent", "graph_agent"],
            },
        )
        async def workers_review(objective: str = "") -> str:
            """评审当前 work 的 worker 划分建议，但不直接执行。"""

            context, _task = await _current_work_context()
            plan = await self.review_worker_plan(
                work_id=context.work_id,
                objective=objective,
            )
            return json.dumps(plan.model_dump(mode="json"), ensure_ascii=False)

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
                    tool_profile=self._effective_tool_profile_for_objective(
                        self._coerce_worker_type_name(worker_type),
                        objective=item,
                    ),
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
            runtime_now,
            work_inspect,
            agents_list,
            sessions_list,
            session_status,
            subagents_spawn,
            subagents_list,
            subagents_kill,
            subagents_steer,
            workers_review,
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
                default_tool_groups=["project", "session", "supervision", "delegation"],
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
                    "Current Datetime Local: {{current_datetime_local}}\n"
                    "Current Weekday Local: {{current_weekday_local}}\n"
                    "Owner Timezone: {{owner_timezone}} (UTC {{owner_utc_offset}})\n"
                    "Owner Locale: {{owner_locale}}\n"
                    "Surface: {{surface}}\n"
                    "Worker Type: {{worker_type}}\n"
                    "Capabilities: {{worker_capabilities}}\n"
                    "Default Tool Profile: {{default_tool_profile}}\n"
                    "Default Tool Groups: {{default_tool_groups}}\n"
                    "Runtime Kinds: {{runtime_kinds}}\n"
                    "Ambient Degraded Reasons: {{ambient_degraded_reasons}}\n"
                    "必须继续走 ToolBroker / Policy / audit，不得绕过治理面。"
                ),
                metadata={"scope": "shared"},
            ),
            "bootstrap:general": WorkerBootstrapFile(
                file_id="bootstrap:general",
                path_hint="bootstrap/general.md",
                applies_to_worker_types=[WorkerType.GENERAL],
                content=(
                    "你是 OctoAgent 的 Butler（主 Agent / supervisor），"
                    "也是负责长期协作节奏的 Agent 管家。\n"
                    "内部 runtime 仍可能把你的 worker_type 标成 general，但对外统一自称 Butler，"
                    "不要把自己叫作 general worker。\n"
                    "你的职责是先梳理目标、上下文和下一步，再在策略允许和用户授权范围内创建、拆分、合并、删除"
                    "或重划分 worker。\n"
                    "面对 under-specified 请求时，"
                    "先判断是否缺真实待办、地点、预算、比较标准等关键输入；"
                    "优先补最关键的 1-2 个条件，不要先给伪完整答案。\n"
                    "不要自己承担 web/browser/code 等具体执行工作；"
                    "优先用 workers.review 或 delegation 工具形成方案，"
                    "再让 research/dev/ops worker 落地。\n"
                    "遇到今天、天气、最新资料、官网、网页信息这类依赖实时外部事实的问题时，"
                    "先判断是否缺城市、对象名等关键参数；"
                    "如果系统已有受治理 worker/web/browser 路径，"
                    "不要直接把自己表述成没有实时能力。"
                ),
                metadata={"worker_type": "general"},
            ),
            "bootstrap:ops": WorkerBootstrapFile(
                file_id="bootstrap:ops",
                path_hint="bootstrap/ops.md",
                applies_to_worker_types=[WorkerType.OPS],
                content=(
                    "你是 ops worker，优先 runtime / diagnostics / recovery。\n"
                    "如果任务涉及状态页、控制台、网页健康检查或最新运行事实，可使用受治理 "
                    "runtime/web 工具，并明确记录来源与限制。"
                ),
                metadata={"worker_type": "ops"},
            ),
            "bootstrap:research": WorkerBootstrapFile(
                file_id="bootstrap:research",
                path_hint="bootstrap/research.md",
                applies_to_worker_types=[WorkerType.RESEARCH],
                content=(
                    "你是 research worker，优先分析上下文、产物和证据。\n"
                    "如果问题依赖今天、最新公开信息、官网、网页资料或外部文档，优先使用受治理 "
                    "web.search / web.fetch / browser.* 收集证据，并在结果中说明来源与不确定性。"
                ),
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

    @staticmethod
    def _worker_profile_label(worker_type: str) -> str:
        labels = {
            "general": "Butler Root Agent",
            "ops": "Ops Root Agent",
            "research": "Research Root Agent",
            "dev": "Dev Root Agent",
        }
        return labels.get(worker_type, worker_type)

    @staticmethod
    def _builtin_worker_type_from_profile_id(profile_id: str) -> WorkerType | None:
        normalized = profile_id.strip().lower()
        if not normalized.startswith("singleton:"):
            return None
        try:
            return WorkerType(normalized.split(":", 1)[1])
        except ValueError:
            return None

    @staticmethod
    def _coerce_tool_profile(value: str) -> ToolProfile:
        try:
            return ToolProfile(value.strip().lower())
        except Exception:
            return ToolProfile.STANDARD

    @staticmethod
    def _dedupe_preserve_order(values: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            normalized = value.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            result.append(normalized)
        return result

    @staticmethod
    def _profile_first_core_tool_names() -> list[str]:
        return [
            "project.inspect",
            "task.inspect",
            "artifact.list",
            "sessions.list",
            "session.status",
            "workers.review",
            "subagents.spawn",
            "subagents.list",
            "subagents.steer",
            "mcp.tools.list",
        ]

    @staticmethod
    def _profile_first_discovery_tool_names() -> set[str]:
        return {
            "workers.review",
            "subagents.spawn",
            "subagents.list",
            "subagents.steer",
            "mcp.tools.list",
            "mcp.servers.list",
            "web.search",
        }

    @classmethod
    def _resolve_profile_first_source_kind(
        cls,
        binding: _ResolvedWorkerBinding,
        tool_name: str,
    ) -> str:
        if tool_name in binding.selected_tools:
            return "profile_selected"
        if tool_name in cls._profile_first_core_tool_names():
            return "profile_first_core"
        return "default_tool_group"

    @staticmethod
    def _split_worker_objectives(objective: str) -> list[str]:
        normalized = objective.strip()
        if not normalized:
            return []
        for token in ["\r\n", "；", ";", "。", "，然后", "然后", "并且", "接着", "再"]:
            normalized = normalized.replace(token, "\n")
        items = [item.strip(" -\t") for item in normalized.splitlines() if item.strip(" -\t")]
        if len(items) > 1:
            return items[:4]
        return [normalized]

    @staticmethod
    def _classify_worker_type(objective: str) -> WorkerType:
        lowered = objective.lower()
        if any(token in lowered for token in ("代码", "修复", "实现", "测试", "patch", "dev")):
            return WorkerType.DEV
        if any(
            token in lowered
            for token in ("部署", "运行", "恢复", "诊断", "日志", "监控", "重启", "ops")
        ):
            return WorkerType.OPS
        if any(
            token in lowered
            for token in ("调研", "分析", "资料", "阅读", "总结", "research", "investigate")
        ):
            return WorkerType.RESEARCH
        return WorkerType.RESEARCH

    @staticmethod
    def _coerce_worker_type_name(worker_type: str) -> WorkerType:
        try:
            return WorkerType(worker_type.strip().lower())
        except Exception:
            return WorkerType.RESEARCH

    @staticmethod
    def _target_kind_for_worker_type(worker_type: WorkerType) -> str:
        if worker_type == WorkerType.DEV:
            return RuntimeKind.GRAPH_AGENT.value
        if worker_type == WorkerType.OPS:
            return RuntimeKind.ACP_RUNTIME.value
        return RuntimeKind.SUBAGENT.value

    @staticmethod
    def _requires_standard_web_access(objective: str, worker_type: WorkerType) -> bool:
        lowered = objective.lower()
        common_tokens = (
            "天气",
            "今天",
            "最新",
            "官网",
            "官方",
            "网页",
            "网站",
            "站点",
            "搜索",
            "查一下",
            "查找",
            "browser",
            "navigate",
            "search",
            "latest",
            "today",
            "weather",
            "website",
            "official",
        )
        ops_tokens = ("状态页", "status page", "console", "health", "incident", "控制台", "健康")
        if any(token in lowered for token in common_tokens):
            return worker_type in {WorkerType.RESEARCH, WorkerType.OPS}
        return worker_type == WorkerType.OPS and any(token in lowered for token in ops_tokens)

    @staticmethod
    def _requires_weather_toolset(objective: str, worker_type: WorkerType) -> bool:
        if worker_type != WorkerType.RESEARCH:
            return False
        lowered = objective.lower()
        weather_tokens = (
            "天气",
            "weather",
            "气温",
            "温度",
            "下雨",
            "降雨",
            "体感",
            "穿衣",
        )
        return any(token in lowered for token in weather_tokens)

    @classmethod
    def _effective_tool_profile_for_objective(
        cls,
        worker_type: WorkerType,
        *,
        objective: str,
    ) -> str:
        if worker_type == WorkerType.DEV:
            return ToolProfile.STANDARD.value
        if cls._requires_standard_web_access(objective, worker_type):
            return ToolProfile.STANDARD.value
        return ToolProfile.MINIMAL.value

    def _build_worker_assignment(
        self,
        objective: str,
        *,
        index: int,
    ) -> _WorkerPlanAssignment:
        worker_type = self._classify_worker_type(objective)
        tool_profile = self._effective_tool_profile_for_objective(
            worker_type,
            objective=objective,
        )
        reason = f"该子任务更适合由 {worker_type.value} worker 处理。"
        if worker_type == WorkerType.RESEARCH and tool_profile == ToolProfile.STANDARD.value:
            reason = (
                "该子任务涉及最新公开信息或网页资料，"
                "适合由 research worker 使用受治理 web/browser 工具处理。"
            )
        elif worker_type == WorkerType.OPS and tool_profile == ToolProfile.STANDARD.value:
            reason = (
                "该子任务涉及外部状态或网页检查，"
                "适合由 ops worker 使用受治理 runtime/web 工具处理。"
            )
        return _WorkerPlanAssignment(
            objective=objective,
            worker_type=worker_type.value,
            target_kind=self._target_kind_for_worker_type(worker_type),
            tool_profile=tool_profile,
            title=f"{worker_type.value}-worker-{index}",
            reason=reason,
        )

    async def _launch_child_task(
        self,
        *,
        parent_task,
        parent_work,
        objective: str,
        worker_type: str,
        target_kind: str,
        tool_profile: str,
        title: str = "",
        spawned_by: str,
        plan_id: str = "",
    ) -> dict[str, Any]:
        if self._task_runner is None:
            raise RuntimeError("task runner is not bound for child task launch")
        child_id = str(ULID())
        child_thread_id = f"{parent_task.thread_id}:child:{child_id[:8]}"
        child_message = NormalizedMessage(
            channel=parent_task.requester.channel,
            thread_id=child_thread_id,
            scope_id=parent_task.scope_id,
            sender_id=parent_task.requester.sender_id,
            sender_name=parent_task.requester.sender_id or "owner",
            text=objective,
            control_metadata={
                "parent_task_id": parent_task.task_id,
                "parent_work_id": parent_work.work_id,
                "requested_worker_type": worker_type,
                "target_kind": target_kind,
                "tool_profile": tool_profile,
                "spawned_by": spawned_by,
                "child_title": title,
                "worker_plan_id": plan_id,
            },
            idempotency_key=f"{spawned_by}:{parent_task.task_id}:{child_id}",
        )
        task_id, created = await self._task_runner.launch_child_task(child_message)
        return {
            "task_id": task_id,
            "created": created,
            "thread_id": child_thread_id,
            "target_kind": target_kind,
            "worker_type": worker_type,
            "tool_profile": tool_profile,
            "parent_task_id": parent_task.task_id,
            "parent_work_id": parent_work.work_id,
            "title": title,
            "objective": objective,
            "worker_plan_id": plan_id,
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

    def _resolve_fallback_toolset_from_pack(
        self,
        pack: BundledCapabilityPack,
        worker_type: WorkerType,
    ) -> list[str]:
        profile = self.get_worker_profile(worker_type)
        result = [
            tool.tool_name
            for tool in pack.tools
            if tool.tool_group in profile.default_tool_groups
            and tool.availability
            in {
                BuiltinToolAvailabilityStatus.AVAILABLE,
                BuiltinToolAvailabilityStatus.DEGRADED,
            }
        ]
        if result:
            return result[:5]
        if pack.fallback_toolset:
            return list(pack.fallback_toolset)[:5]
        return [tool.tool_name for tool in pack.tools[:5]]

    async def _resolve_scope_skill_selection(
        self,
        *,
        project_id: str = "",
        workspace_id: str = "",
    ) -> tuple[set[str], set[str]]:
        if not project_id and not workspace_id:
            return set(), set()
        project, _workspace = await self._resolve_project_context(
            project_id=project_id,
            workspace_id=workspace_id,
        )
        if project is None:
            return set(), set()
        metadata = (
            dict(project.metadata)
            if isinstance(getattr(project, "metadata", None), dict)
            else {}
        )
        raw_selection = metadata.get("skill_selection")
        if not isinstance(raw_selection, Mapping):
            return set(), set()
        selected_item_ids = {
            str(item).strip()
            for item in raw_selection.get("selected_item_ids", [])
            if str(item).strip()
        }
        disabled_item_ids = {
            str(item).strip()
            for item in raw_selection.get("disabled_item_ids", [])
            if str(item).strip()
        }
        return selected_item_ids, disabled_item_ids

    async def _resolve_profile_skill_selection(
        self,
        *,
        profile_id: str = "",
    ) -> tuple[set[str], set[str]]:
        normalized_profile_id = profile_id.strip()
        if not normalized_profile_id:
            return set(), set()

        metadata: dict[str, Any] = {}
        agent_profile = await self._stores.agent_context_store.get_agent_profile(
            normalized_profile_id
        )
        if agent_profile is not None and isinstance(agent_profile.metadata, dict):
            metadata = dict(agent_profile.metadata)
        else:
            worker_profile = await self._stores.agent_context_store.get_worker_profile(
                normalized_profile_id
            )
            if worker_profile is not None and isinstance(worker_profile.metadata, dict):
                metadata = dict(worker_profile.metadata)

        raw_selection = metadata.get("capability_provider_selection")
        if not isinstance(raw_selection, Mapping):
            raw_selection = metadata.get("skill_selection")
        if not isinstance(raw_selection, Mapping):
            return set(), set()
        selected_item_ids = {
            str(item).strip()
            for item in raw_selection.get("selected_item_ids", [])
            if str(item).strip()
        }
        disabled_item_ids = {
            str(item).strip()
            for item in raw_selection.get("disabled_item_ids", [])
            if str(item).strip()
        }
        return selected_item_ids, disabled_item_ids

    @staticmethod
    def _skill_item_selected(
        *,
        item_id: str,
        enabled_by_default: bool,
        selected_item_ids: set[str],
        disabled_item_ids: set[str],
    ) -> bool:
        if item_id in selected_item_ids:
            return True
        if item_id in disabled_item_ids:
            return False
        return enabled_by_default

    async def _filter_pack_for_scope(
        self,
        pack: BundledCapabilityPack,
        *,
        project_id: str = "",
        workspace_id: str = "",
        profile_id: str = "",
    ) -> BundledCapabilityPack:
        (
            project_selected_item_ids,
            project_disabled_item_ids,
        ) = await self._resolve_scope_skill_selection(
            project_id=project_id,
            workspace_id=workspace_id,
        )
        (
            profile_selected_item_ids,
            profile_disabled_item_ids,
        ) = await self._resolve_profile_skill_selection(
            profile_id=profile_id,
        )
        if (
            not project_selected_item_ids
            and not project_disabled_item_ids
            and not profile_selected_item_ids
            and not profile_disabled_item_ids
        ):
            return pack

        def is_selected(item_id: str, *, enabled_by_default: bool) -> bool:
            project_selected = self._skill_item_selected(
                item_id=item_id,
                enabled_by_default=enabled_by_default,
                selected_item_ids=project_selected_item_ids,
                disabled_item_ids=project_disabled_item_ids,
            )
            return self._skill_item_selected(
                item_id=item_id,
                enabled_by_default=project_selected,
                selected_item_ids=profile_selected_item_ids,
                disabled_item_ids=profile_disabled_item_ids,
            )

        skills = [
            skill
            for skill in pack.skills
            if is_selected(item_id=f"skill:{skill.skill_id}", enabled_by_default=True)
        ]
        governed_skill_tool_names = {
            tool_name
            for skill in pack.skills
            for tool_name in skill.tools_allowed
            if tool_name
        }
        enabled_skill_tool_names = {
            tool_name
            for skill in skills
            for tool_name in skill.tools_allowed
            if tool_name
        }

        tools: list[BundledToolDefinition] = []
        for tool in pack.tools:
            if tool.tool_group == "mcp":
                server_name = str(tool.metadata.get("mcp_server_name", "")).strip() or "mcp"
                include = is_selected(
                    item_id=f"mcp:{server_name}",
                    enabled_by_default=False,
                )
            else:
                include = True
                if tool.tool_name in governed_skill_tool_names:
                    include = tool.tool_name in enabled_skill_tool_names
                include = is_selected(
                    item_id=f"skill:{tool.tool_name}",
                    enabled_by_default=include,
                )
            if include:
                tools.append(tool)

        allowed_tool_names = {tool.tool_name for tool in tools}
        fallback_toolset = [
            tool_name for tool_name in pack.fallback_toolset if tool_name in allowed_tool_names
        ]
        return pack.model_copy(
            update={
                "skills": skills,
                "tools": tools,
                "fallback_toolset": fallback_toolset,
            }
        )

    def _restrict_selection_to_pack(
        self,
        selection: DynamicToolSelection,
        *,
        pack: BundledCapabilityPack,
        fallback: list[str],
    ) -> DynamicToolSelection:
        allowed_tool_names = {tool.tool_name for tool in pack.tools}
        filtered_hits = [hit for hit in selection.hits if hit.tool_name in allowed_tool_names]
        filtered_selected_tools = [
            tool_name for tool_name in selection.selected_tools if tool_name in allowed_tool_names
        ]
        warnings = list(selection.warnings)
        is_fallback = selection.is_fallback

        if len(filtered_hits) != len(selection.hits):
            warnings.append("tool_selection_filtered_by_skill_governance")
        if not filtered_selected_tools and selection.selected_tools and fallback:
            filtered_selected_tools = list(fallback)[: selection.query.limit]
            warnings.append("tool_selection_empty_after_skill_governance_fallback")
            is_fallback = True
        elif not filtered_selected_tools and selection.selected_tools:
            warnings.append("tool_selection_empty_after_skill_governance")

        deduped_warnings: list[str] = []
        for warning in warnings:
            if warning not in deduped_warnings:
                deduped_warnings.append(warning)
        return selection.model_copy(
            update={
                "selected_tools": filtered_selected_tools,
                "hits": filtered_hits,
                "warnings": deduped_warnings,
                "is_fallback": is_fallback,
            }
        )

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
            if workspace is not None and project is None:
                project = await self._stores.project_store.get_project(workspace.project_id)
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

    def _resolve_skill_provider_config_path(self) -> Path:
        override = os.getenv("OCTOAGENT_SKILL_PROVIDERS_PATH", "").strip()
        if override:
            return Path(override)
        return self._project_root / _DEFAULT_SKILL_PROVIDER_CONFIG_PATH

    def _load_skill_provider_configs(self) -> list[SkillProviderConfig]:
        path = self._resolve_skill_provider_config_path()
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
        raw_items = payload.get("providers", payload) if isinstance(payload, dict) else payload
        if not isinstance(raw_items, list):
            return []
        configs: list[SkillProviderConfig] = []
        for item in raw_items:
            try:
                configs.append(SkillProviderConfig.model_validate(item))
            except Exception:
                continue
        return configs

    def _write_skill_provider_configs(self, configs: list[SkillProviderConfig]) -> None:
        path = self._resolve_skill_provider_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "providers": [
                item.model_dump(mode="json", by_alias=True)
                for item in sorted(configs, key=lambda current: current.provider_id.lower())
            ]
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _rebuild_skill_registry(self) -> None:
        current_ids = [item.skill_id for item in self._skill_registry.list_skills()]
        for skill_id in current_ids:
            self._skill_registry.unregister(skill_id)
        self._register_builtin_skills()
        self._register_custom_skill_providers()

    def _register_custom_skill_providers(self) -> None:
        existing_ids = {item.skill_id for item in self._skill_registry.list_skills()}
        for config in self._load_skill_provider_configs():
            if not config.enabled or config.provider_id in existing_ids:
                continue
            try:
                tool_profile = ToolProfile(str(config.tool_profile).strip().lower() or "minimal")
            except ValueError:
                tool_profile = ToolProfile.MINIMAL
            worker_type = str(config.worker_type).strip().lower() or "general"
            self._skill_registry.register(
                SkillManifest(
                    skill_id=config.provider_id,
                    input_model=_BuiltinSkillInput,
                    output_model=_BuiltinSkillOutput,
                    description=config.description or config.label,
                    model_alias=config.model_alias or "main",
                    tools_allowed=list(config.tools_allowed),
                    tool_profile=tool_profile,
                    metadata={
                        "label": config.label,
                        "worker_type": worker_type,
                        "source_kind": "custom",
                        "provider_id": config.provider_id,
                        "install_hint": config.install_hint,
                        **dict(config.metadata),
                    },
                ),
                prompt_template=config.prompt_template,
            )
            existing_ids.add(config.provider_id)

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
            "workers.review": ["agent_runtime", "web"],
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
        if tool_name in {"subagents.list", "subagents.kill", "subagents.steer", "workers.review"}:
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
