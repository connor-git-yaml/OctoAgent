"""Feature 030: bundled capability pack / ToolIndex / bootstrap。"""

from __future__ import annotations

import asyncio
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
    DelegationTargetKind,
    DynamicToolSelection,
    EffectiveToolUniverse,
    NormalizedMessage,
    OwnerProfile,
    ProjectBindingType,
    RuntimeKind,
    TurnExecutorKind,
    ToolAvailabilityExplanation,
    ToolIndexQuery,
    WorkerBootstrapFile,
    WorkerCapabilityProfile,
    WorkerProfileStatus,
    WorkStatus,
)
from octoagent.memory import (
    EvidenceRef,
    MemoryAccessPolicy,
    MemoryLayer,
    MemoryPartition,
    MemoryRecallHookOptions,
    MemoryRecallPostFilterMode,
    MemoryRecallRerankMode,
    MemoryRecallResult,
    SqliteMemoryStore,
    WriteAction,
)
from octoagent.provider.dx.automation_store import AutomationStore
from octoagent.provider.dx.memory_console_service import MemoryConsoleService
from octoagent.provider.dx.memory_retrieval_profile import (
    apply_retrieval_profile_to_hook_options,
)
from octoagent.provider.dx.memory_runtime_service import MemoryRuntimeService
from octoagent.skills import SkillDiscovery
from octoagent.tooling import (
    SideEffectLevel,
    ToolBroker,
    ToolIndex,
    ToolProfile,
    profile_allows,
    reflect_tool_schema,
    tool_contract,
)
from octoagent.tooling.hooks import ApprovalOverrideHook, PresetBeforeHook
from octoagent.tooling.models import CoreToolSet, DeferredToolEntry, ToolTier

from .tool_search_tool import create_tool_search_handler
from pydantic import BaseModel, Field
from ulid import ULID

import structlog

_log = structlog.get_logger()

from octoagent.core.behavior_workspace import (
    BOOTSTRAP_COMPLETED_MARKER,
    check_behavior_file_budget,
    get_behavior_file_review_modes,
    mark_onboarding_completed,
    resolve_write_path_by_file_id,
)
from octoagent.core.models.behavior import BehaviorReviewMode

from .agent_context import build_ambient_runtime_facts, build_default_memory_recall_hook_options
from .execution_context import get_current_execution_context
from .task_service import TaskService

if TYPE_CHECKING:
    from .mcp_installer import McpInstallerService
    from .mcp_registry import McpRegistryService

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
    worker_type: str
    model_alias: str
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


class _ApprovalOverrideMemoryCache:
    """Feature 061: ApprovalOverride 内存缓存

    实现 ApprovalOverrideCacheProtocol（hooks 依赖的接口），
    运行时 O(1) 查询，避免每次工具调用都查 SQLite。
    key = (agent_runtime_id, tool_name) → True
    """

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str], bool] = {}

    def has(self, agent_runtime_id: str, tool_name: str) -> bool:
        """检查缓存中是否存在 always 覆盖"""
        return self._cache.get((agent_runtime_id, tool_name), False)

    def set(self, agent_runtime_id: str, tool_name: str) -> None:
        """设置缓存条目"""
        self._cache[(agent_runtime_id, tool_name)] = True

    def remove(self, agent_runtime_id: str, tool_name: str) -> None:
        """移除缓存条目"""
        self._cache.pop((agent_runtime_id, tool_name), None)

    def load_from_records(self, records: list) -> None:
        """从 ApprovalOverride 记录批量加载缓存"""
        for record in records:
            self._cache[(record.agent_runtime_id, record.tool_name)] = True

    def clear_agent(self, agent_runtime_id: str) -> None:
        """清除指定 Agent 的所有缓存条目"""
        keys_to_remove = [k for k in self._cache if k[0] == agent_runtime_id]
        for key in keys_to_remove:
            del self._cache[key]

    def clear_tool(self, tool_name: str) -> None:
        """清除指定工具的所有缓存条目"""
        keys_to_remove = [k for k in self._cache if k[1] == tool_name]
        for key in keys_to_remove:
            del self._cache[key]

    @property
    def size(self) -> int:
        """缓存条目总数"""
        return len(self._cache)

    def list_for_agent(self, agent_runtime_id: str) -> list[str]:
        """列出指定 Agent 的所有 always 授权工具名"""
        return [tn for (rid, tn) in self._cache if rid == agent_runtime_id]


class CapabilityPackService:
    """统一管理 bundled tools / skills / ToolIndex / worker bootstrap。"""

    def __init__(
        self,
        *,
        project_root: Path,
        store_group,
        tool_broker: ToolBroker,
        preferred_tool_index_backend: str = "auto",
        approval_override_cache: _ApprovalOverrideMemoryCache | None = None,
    ) -> None:
        self._project_root = project_root
        self._stores = store_group
        self._tool_broker = tool_broker
        self._tool_index = ToolIndex(preferred_backend=preferred_tool_index_backend)
        self._pack: BundledCapabilityPack | None = None
        self._pack_revision: int = 0
        self._bootstrapped = False
        self._profile_map = self._build_worker_profiles()
        self._bootstrap_templates = self._build_bootstrap_templates()
        self._task_runner = None
        self._delegation_plane = None
        self._mcp_registry: McpRegistryService | None = None
        self._mcp_installer: McpInstallerService | None = None
        self._browser_sessions: dict[str, _BrowserSessionState] = {}
        # Feature 061: ApprovalOverride 内存缓存（给 Hook 使用）
        # 外部传入时与 ApprovalManager 共享同一实例
        self._approval_override_cache = approval_override_cache or _ApprovalOverrideMemoryCache()
        self._memory_console_service = MemoryConsoleService(
            project_root,
            store_group=store_group,
        )
        self._memory_runtime_service = MemoryRuntimeService(
            project_root,
            store_group=store_group,
        )
        # Feature 057: SKILL.md 文件系统驱动的 Skill 发现服务
        # 三级目录：内置 (skills/) > 用户 (~/.octoagent/skills/) > 项目 ({project}/skills/)
        _repo_root = Path(__file__).resolve().parents[7]  # .../octoagent/apps/gateway/src/octoagent/gateway/services -> repo root
        _user_skills_dir = Path.home() / ".octoagent" / "skills"
        _project_skills_dir = project_root / "skills"
        self._skill_discovery = SkillDiscovery(
            builtin_dir=_repo_root / "skills",
            user_dir=_user_skills_dir,
            project_dir=_project_skills_dir,
        )

    @property
    def tool_broker(self) -> ToolBroker:
        return self._tool_broker

    @property
    def skill_discovery(self) -> SkillDiscovery:
        """Feature 057: 返回 SkillDiscovery 实例供依赖注入使用。"""
        return self._skill_discovery

    def bind_task_runner(self, task_runner) -> None:
        self._task_runner = task_runner

    def bind_delegation_plane(self, delegation_plane) -> None:
        self._delegation_plane = delegation_plane

    def bind_mcp_registry(self, mcp_registry: McpRegistryService) -> None:
        self._mcp_registry = mcp_registry

    def bind_mcp_installer(self, mcp_installer: McpInstallerService) -> None:
        self._mcp_installer = mcp_installer

    @property
    def mcp_registry(self) -> McpRegistryService | None:
        return self._mcp_registry

    @property
    def approval_override_cache(self) -> _ApprovalOverrideMemoryCache:
        """Feature 061: 返回 ApprovalOverride 内存缓存实例。"""
        return self._approval_override_cache

    async def startup(self) -> None:
        if self._bootstrapped:
            return
        # Feature 057: 首次启动时扫描 SKILL.md 文件系统
        self._skill_discovery.scan()
        # Feature 061: 注册权限检查 Hooks 到 ToolBroker
        event_store = getattr(self._stores, "event_store", None)
        self._tool_broker.add_hook(
            ApprovalOverrideHook(
                cache=self._approval_override_cache,
                event_store=event_store,
            )
        )
        self._tool_broker.add_hook(
            PresetBeforeHook(
                event_store=event_store,
                override_cache=self._approval_override_cache,
            )
        )
        await self._register_builtin_tools()
        if self._mcp_registry is not None:
            await self._mcp_registry.startup()
        await self.refresh()
        self._bootstrapped = True

    async def refresh(self) -> BundledCapabilityPack:
        if self._mcp_registry is not None:
            await self._mcp_registry.refresh()
        # Feature 057: 刷新 SKILL.md 文件系统缓存
        self._skill_discovery.refresh()
        metas = await self._tool_broker.discover()
        await self._tool_index.rebuild(metas)

        # 构建 MCP server -> install_source 映射（Feature 058）
        mcp_install_source_map: dict[str, str] = {}
        if self._mcp_installer is not None:
            for record in self._mcp_installer.list_installs():
                mcp_install_source_map[record.server_id] = record.install_source

        tools = [
            BundledToolDefinition(
                tool_name=meta.name,
                label=meta.name.replace(".", " ").title(),
                description=meta.description,
                tool_group=meta.tool_group,
                tool_profile=meta.tool_profile.value,
                tags=list(meta.tags),
                manifest_ref=meta.manifest_ref,
                availability=self._resolve_tool_availability(meta.name),
                availability_reason=self._resolve_tool_availability_reason(meta.name),
                install_hint=self._resolve_tool_install_hint(meta.name),
                entrypoints=self._resolve_tool_entrypoints(meta.name),
                runtime_kinds=self._resolve_tool_runtime_kinds(meta.name),
                metadata=self._enrich_mcp_metadata(
                    dict(meta.metadata), mcp_install_source_map
                ),
            )
            for meta in metas
        ]
        # Feature 057: 从 SkillDiscovery 构建 BundledSkillDefinition
        skills = [
            BundledSkillDefinition(
                skill_id=entry.name,
                label=entry.name.replace("-", " ").title(),
                description=entry.description,
                metadata={
                    "source": entry.source.value,
                    "version": entry.version,
                    "tags": entry.tags,
                },
            )
            for entry in self._skill_discovery.list_items()
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
        self._pack_revision += 1
        return self._pack

    @property
    def pack_revision(self) -> int:
        """当前 pack 版本号，MCP 安装/卸载等导致 refresh 时递增。"""
        return self._pack_revision

    def invalidate_pack(self) -> None:
        """标记 pack 缓存过期，下次 get_pack 会重新构建。"""
        self._pack = None

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

    def get_worker_profile(self, worker_type: str = "general") -> WorkerCapabilityProfile:
        return self._profile_map.get(worker_type, self._profile_map["general"])

    @property
    def tool_index(self) -> ToolIndex:
        """Feature 061: 返回 ToolIndex 实例供 tool_search 等使用。"""
        return self._tool_index

    async def build_tool_context(
        self,
        *,
        core_tool_set: CoreToolSet | None = None,
    ) -> tuple[list[Any], list[DeferredToolEntry]]:
        """Feature 061 T-021: 按 ToolTier 将工具分为 Core 和 Deferred 两组

        Core Tools → 完整 ToolMeta 列表（用于构建 FunctionToolset JSON Schema）
        Deferred Tools → {name, one_line_desc} 精简列表（注入 system prompt）

        使用 CoreToolSet.default() 确定初始 Core 清单，
        也可通过 core_tool_set 参数自定义。

        Args:
            core_tool_set: 自定义 Core Tools 清单，默认使用 CoreToolSet.default()

        Returns:
            (core_tool_metas, deferred_entries) 二元组:
            - core_tool_metas: Core 工具的完整 ToolMeta 列表
            - deferred_entries: Deferred 工具的精简列表
        """
        await self.startup()

        effective_core_set = core_tool_set or CoreToolSet.default()
        all_metas = await self._tool_broker.discover()

        core_metas: list[Any] = []
        deferred_entries: list[DeferredToolEntry] = []

        for meta in all_metas:
            if effective_core_set.is_core(meta.name):
                core_metas.append(meta)
            else:
                # 截断描述到 80 字符
                desc = (meta.description or meta.name)[:80].strip()
                deferred_entries.append(
                    DeferredToolEntry(
                        name=meta.name,
                        one_line_desc=desc,
                        tool_group=meta.tool_group,
                        side_effect_level=meta.side_effect_level.value,
                    )
                )

        return core_metas, deferred_entries

    async def resolve_worker_binding(
        self,
        *,
        requested_profile_id: str = "",
        fallback_worker_type: str = "general",
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
                    model_alias="main",
                    tool_profile=builtin_profile.default_tool_profile,
                    default_tool_groups=list(builtin_profile.default_tool_groups),
                    selected_tools=[],
                    source_kind="builtin_singleton",
                    profile_name="Root Agent",
                )
            stored_profile = await self._stores.agent_context_store.get_worker_profile(
                normalized_profile_id
            )
            if stored_profile is not None and stored_profile.status != WorkerProfileStatus.ARCHIVED:
                worker_type = "general"
                builtin_profile = self.get_worker_profile("general")
                return _ResolvedWorkerBinding(
                    profile_id=stored_profile.profile_id,
                    profile_revision=(
                        stored_profile.active_revision or stored_profile.draft_revision or 1
                    ),
                    worker_type=worker_type,
                    model_alias=stored_profile.model_alias or "main",
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
                    model_alias=agent_profile.model_alias or "main",
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
            profile_id=f"singleton:{builtin_profile.worker_type}",
            profile_revision=1,
            worker_type=builtin_profile.worker_type,
            model_alias="main",
            tool_profile=builtin_profile.default_tool_profile,
            default_tool_groups=list(builtin_profile.default_tool_groups),
            selected_tools=[],
            source_kind="builtin_fallback",
            profile_name="Root Agent",
        )

    async def resolve_worker_type_for_profile(self, profile_id: str) -> str | None:
        normalized = profile_id.strip()
        if not normalized:
            return None
        builtin_worker_type = self._builtin_worker_type_from_profile_id(normalized)
        if builtin_worker_type is not None:
            return builtin_worker_type
        stored_profile = await self._stores.agent_context_store.get_worker_profile(normalized)
        if stored_profile is None or stored_profile.status == WorkerProfileStatus.ARCHIVED:
            return None
        return "general"

    async def select_tools(
        self,
        request: ToolIndexQuery,
        *,
        worker_type: str = "general",
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
        worker_type: str = "general",
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
        if self._requires_weather_toolset(request.query):
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
        recommended_tools = list(discovery_entrypoints or mounted_names[:6])

        return DynamicToolSelection(
            selection_id=str(ULID()),
            query=request,
            selected_tools=mounted_names,
            recommended_tools=recommended_tools,
            hits=discovery.hits,
            backend=self._tool_index.backend_name,
            is_fallback="profile_first_empty_fallback_to_static_toolset" in warnings,
            warnings=self._dedupe_preserve_order(warnings),
            resolution_mode="profile_first_core",
            effective_tool_universe=EffectiveToolUniverse(
                profile_id=binding.profile_id,
                profile_revision=binding.profile_revision,
                worker_type=binding.worker_type,
                tool_profile=context_profile.value,
                resolution_mode="profile_first_core",
                selected_tools=mounted_names,
                recommended_tools=recommended_tools,
                discovery_entrypoints=discovery_entrypoints,
                warnings=self._dedupe_preserve_order(warnings),
            ),
            mounted_tools=mounted_tools,
            blocked_tools=blocked_tools,
        )

    async def render_bootstrap_context(
        self,
        *,
        worker_type: str = "general",
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
            "{{worker_type}}": worker_type,
            "{{worker_capabilities}}": ", ".join(worker_profile.capabilities) or "none",
            "{{default_tool_profile}}": worker_profile.default_tool_profile,
            "{{default_tool_groups}}": ", ".join(worker_profile.default_tool_groups) or "none",
            "{{runtime_kinds}}": ", ".join(item.value for item in worker_profile.runtime_kinds)
            or "none",
        }
        rendered: list[dict[str, Any]] = []
        for file in self._bootstrap_templates.values():
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
                **self._mcp_install_summary(),
            },
            "skills": {
                "discovered_count": len(self._skill_discovery.list_items()),
            },
        }

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

        async def _resolve_workspace_root(
            *,
            project_id: str = "",
            workspace_id: str = "",
        ) -> Path:
            project, workspace, _task = await _resolve_runtime_project_context(
                project_id=project_id,
                workspace_id=workspace_id,
            )
            root = (
                Path(str(workspace.root_path).strip())
                if workspace is not None and str(workspace.root_path).strip()
                else self._project_root
            )
            return root.resolve()

        def _resolve_workspace_path(
            workspace_root: Path,
            raw_path: str,
            *,
            allow_outside_workspace: bool = False,
        ) -> Path:
            """解析路径，支持 workspace 内外访问。

            路径安全策略与 Policy Engine 双维度模型对齐：
            - allow_outside_workspace=True（读操作）：允许任意路径，
              安全由 PresetBeforeHook + PolicyCheckHook 保障
            - allow_outside_workspace=False（写操作）：限制在 workspace 内，
              防止误写系统文件
            """
            normalized = raw_path.strip()
            candidate = (
                Path(normalized)
                if normalized
                else workspace_root
            )
            # 展开 ~ 前缀
            if str(candidate).startswith("~"):
                candidate = candidate.expanduser()
            if not candidate.is_absolute():
                candidate = workspace_root / candidate
            resolved = candidate.resolve()
            if resolved != workspace_root and not resolved.is_relative_to(workspace_root):
                if allow_outside_workspace:
                    return resolved
                raise RuntimeError(
                    f"path escapes workspace root ({workspace_root}). "
                    f"写操作仅允许 workspace 内路径。"
                )
            return resolved

        def _truncate_text(value: str, *, limit: int = 100_000) -> str:
            text = value.strip()
            if len(text) <= limit:
                return text
            omitted = len(text) - limit
            return (
                f"{text[:limit].rstrip()}\n\n"
                f"⚠️ [内容已截断：原文 {len(text)} 字符，已显示前 {limit} 字符，"
                f"省略 {omitted} 字符。如需完整内容请增大 max_chars 参数。]"
            )

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
            name="filesystem.list_dir",
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="filesystem",
            tags=["filesystem", "directory", "list"],
            manifest_ref="builtin://filesystem.list_dir",
            metadata={
                "entrypoints": ["agent_runtime"],
                "runtime_kinds": ["worker", "subagent", "graph_agent"],
            },
        )
        async def filesystem_list_dir(
            path: str = ".",
            max_entries: int = 50,
        ) -> str:
            """列出目录内容。支持 workspace 内路径和用户 HOME 目录下的路径。"""

            workspace_root = await _resolve_workspace_root()
            target = _resolve_workspace_path(workspace_root, path, allow_outside_workspace=True)
            if not target.exists():
                raise RuntimeError(f"path not found: {target}")
            if not target.is_dir():
                raise RuntimeError(f"path is not a directory: {target}")
            entries = []
            bounded_limit = max(1, min(max_entries, 200))
            for item in sorted(target.iterdir(), key=lambda current: (not current.is_dir(), current.name))[
                :bounded_limit
            ]:
                relative = "." if item == workspace_root else str(item.relative_to(workspace_root))
                entries.append(
                    {
                        "name": item.name,
                        "path": relative,
                        "kind": "directory" if item.is_dir() else "file",
                    }
                )
            return json.dumps(
                {
                    "workspace_root": str(workspace_root),
                    "path": "." if target == workspace_root else str(target.relative_to(workspace_root)),
                    "entries": entries,
                },
                ensure_ascii=False,
            )

        @tool_contract(
            name="filesystem.read_text",
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="filesystem",
            tags=["filesystem", "file", "read"],
            manifest_ref="builtin://filesystem.read_text",
            metadata={
                "entrypoints": ["agent_runtime"],
                "runtime_kinds": ["worker", "subagent", "graph_agent"],
            },
        )
        async def filesystem_read_text(
            path: str,
            max_chars: int = 100_000,
        ) -> str:
            """读取文本文件内容。支持 workspace 内路径和用户 HOME 目录下的路径。"""

            workspace_root = await _resolve_workspace_root()
            target = _resolve_workspace_path(workspace_root, path, allow_outside_workspace=True)
            if not target.exists():
                # 返回结构化的 "不存在" 响应，而非抛异常，让 Agent 更容易处理
                return json.dumps(
                    {"exists": False, "path": str(target), "error": "file not found"},
                    ensure_ascii=False,
                )
            if not target.is_file():
                raise RuntimeError(f"path is not a file: {target}")
            content = target.read_text(encoding="utf-8")
            # 工具层不做低阈值截断——由 LargeOutputHandler 按上下文比例统一管理
            bounded_limit = max(200, min(max_chars, 500_000))
            return json.dumps(
                {
                    "workspace_root": str(workspace_root),
                    "path": str(target.relative_to(workspace_root)),
                    "content": _truncate_text(content, limit=bounded_limit),
                },
                ensure_ascii=False,
            )

        @tool_contract(
            name="filesystem.write_text",
            side_effect_level=SideEffectLevel.REVERSIBLE,
            tool_profile=ToolProfile.STANDARD,
            tool_group="filesystem",
            tags=["filesystem", "file", "write"],
            manifest_ref="builtin://filesystem.write_text",
            metadata={
                "entrypoints": ["agent_runtime"],
                "runtime_kinds": ["worker", "subagent", "graph_agent"],
            },
        )
        async def filesystem_write_text(
            path: str,
            content: str,
            create_dirs: bool = True,
        ) -> str:
            """在 workspace 内创建或覆盖文本文件。自动创建中间目录。"""

            workspace_root = await _resolve_workspace_root()
            target = _resolve_workspace_path(workspace_root, path)
            if target.is_dir():
                raise RuntimeError(f"path is a directory, not a file: {target}")
            if create_dirs:
                target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            relative = str(target.relative_to(workspace_root))
            return json.dumps(
                {
                    "workspace_root": str(workspace_root),
                    "path": relative,
                    "bytes_written": len(content.encode("utf-8")),
                    "created_dirs": create_dirs and not target.parent.exists(),
                },
                ensure_ascii=False,
            )

        @tool_contract(
            name="terminal.exec",
            # REVERSIBLE: 默认策略自动放行，避免 node -v/grep 等只读命令
            # 也被审批拦截。真正高危操作由 Policy Profile 的 irreversible
            # 规则或 Skill 层的 Side-effect Two-Phase 保护。
            side_effect_level=SideEffectLevel.REVERSIBLE,
            tool_profile=ToolProfile.STANDARD,
            tool_group="terminal",
            tags=["terminal", "command", "exec"],
            manifest_ref="builtin://terminal.exec",
            metadata={
                "entrypoints": ["agent_runtime"],
                "runtime_kinds": ["worker", "subagent", "graph_agent"],
            },
        )
        async def terminal_exec(
            command: str,
            cwd: str = ".",
            timeout_seconds: float = 300.0,
            max_output_chars: int = 200_000,
        ) -> str:
            """在当前 workspace 内执行受治理终端命令。"""

            workspace_root = await _resolve_workspace_root()
            working_dir = _resolve_workspace_path(workspace_root, cwd)
            if not working_dir.exists() or not working_dir.is_dir():
                raise RuntimeError(f"cwd is not a directory: {working_dir}")
            # 超时上限 600s（对齐 MCP 安装等长命令场景）
            bounded_timeout = max(1.0, min(timeout_seconds, 600.0))
            # 工具层不做低阈值截断——由 LargeOutputHandler 按上下文比例统一管理
            bounded_limit = max(200, min(max_output_chars, 500_000))
            cwd_label = "." if working_dir == workspace_root else str(
                working_dir.relative_to(workspace_root)
            )

            # 使用 asyncio subprocess 避免阻塞事件循环
            proc = await asyncio.create_subprocess_exec(
                "/bin/sh", "-lc", command,
                cwd=str(working_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            timed_out = False
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=bounded_timeout,
                )
            except asyncio.TimeoutError:
                # 超时后先尝试 terminate，给 2s 优雅退出，不行再 kill
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
                stdout_bytes = b""
                stderr_bytes = b""
                timed_out = True

            stdout_text = (stdout_bytes or b"").decode("utf-8", errors="replace")
            stderr_text = (stderr_bytes or b"").decode("utf-8", errors="replace")
            payload = {
                "workspace_root": str(workspace_root),
                "cwd": cwd_label,
                "command": command,
                "returncode": proc.returncode,
                "stdout": _truncate_text(stdout_text, limit=bounded_limit),
                "stderr": _truncate_text(stderr_text, limit=bounded_limit),
                "timed_out": timed_out,
            }
            if timed_out:
                payload["timeout_seconds"] = bounded_timeout
            return json.dumps(payload, ensure_ascii=False)

        @tool_contract(
            name="runtime.inspect",
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
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
                        "selected_worker_type": item.selected_worker_type,
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
            manifest_ref="builtin://web.fetch",
            metadata={
                "entrypoints": ["agent_runtime"],
                "runtime_kinds": ["worker", "subagent", "graph_agent"],
            },
        )
        async def web_fetch(
            url: str,
            timeout_seconds: float = 30.0,
            max_chars: int = 100_000,
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
                    "body_preview": _truncate_text(page.text_content, limit=max(100, min(max_chars, 500_000))),
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
            manifest_ref="builtin://web.search",
            metadata={
                "entrypoints": ["agent_runtime"],
                "runtime_kinds": ["worker", "subagent", "graph_agent"],
            },
        )
        async def web_search(
            query: str,
            limit: int = 5,
            timeout_seconds: float = 30.0,
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
            manifest_ref="builtin://browser.open",
            metadata={
                "entrypoints": ["agent_runtime"],
                "runtime_kinds": ["worker", "subagent", "graph_agent"],
            },
        )
        async def browser_open(url: str, timeout_seconds: float = 30.0) -> str:
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
            manifest_ref="builtin://browser.navigate",
            metadata={
                "entrypoints": ["agent_runtime"],
                "runtime_kinds": ["worker", "subagent", "graph_agent"],
            },
        )
        async def browser_navigate(url: str, timeout_seconds: float = 30.0) -> str:
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
            manifest_ref="builtin://browser.snapshot",
            metadata={
                "entrypoints": ["agent_runtime"],
                "runtime_kinds": ["worker", "subagent", "graph_agent"],
            },
        )
        async def browser_snapshot(max_chars: int = 100_000, link_limit: int = 20) -> str:
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
            manifest_ref="builtin://browser.act",
            metadata={
                "entrypoints": ["agent_runtime"],
                "runtime_kinds": ["worker", "subagent", "graph_agent"],
            },
        )
        async def browser_act(
            kind: str = "click",
            ref: str = "",
            timeout_seconds: float = 30.0,
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
            name="mcp.install",
            side_effect_level=SideEffectLevel.REVERSIBLE,
            tool_profile=ToolProfile.STANDARD,
            tool_group="mcp",
            tags=["mcp", "install"],
            manifest_ref="builtin://mcp.install",
            metadata={
                "entrypoints": ["agent_runtime", "web"],
                "runtime_kinds": ["worker", "subagent", "graph_agent", "acp_runtime"],
            },
        )
        async def mcp_install(
            install_source: str,
            package_name: str,
            env: str = "{}",
            command: str = "",
            args: str = "[]",
        ) -> str:
            """安装或注册一个 MCP server。

            支持三种模式：
            1. npm 包安装: install_source="npm", package_name="@org/mcp-server-xxx"
            2. pip 包安装: install_source="pip", package_name="mcp-server-xxx"
            3. 本地注册: install_source="local", package_name 作为 server 名称,
               command + args + env 指定运行配置

            Args:
                install_source: "npm"、"pip" 或 "local"
                package_name: npm/pip 模式下为包名，local 模式下为 server 名称（如 "openrouter-perplexity"）
                env: JSON 格式的环境变量，如 '{"API_KEY": "sk-xxx"}'
                command: local 模式下的启动命令（如 "node"）
                args: local 模式下的启动参数，JSON 数组格式（如 '["/path/to/server.js"]'）

            示例（本地注册）:
                mcp_install(
                    install_source="local",
                    package_name="openrouter-perplexity",
                    command="node",
                    args='["/Users/xxx/.claude/mcp-servers/openrouter-perplexity/server.js"]',
                    env='{"OPENROUTER_API_KEY": "sk-xxx", "OPENROUTER_MODEL": "perplexity/sonar-pro-search"}'
                )
            """
            try:
                env_dict = json.loads(env) if env and env.strip() != "{}" else {}
            except json.JSONDecodeError as exc:
                return json.dumps(
                    {"error": f"env 参数 JSON 格式不合法: {exc}"},
                    ensure_ascii=False,
                )

            # local 模式：直接写入 mcp-servers.json 并触发发现
            if install_source == "local":
                if not package_name.strip():
                    return json.dumps(
                        {"error": "local 模式下 package_name 用作 server 名称，不能为空"},
                        ensure_ascii=False,
                    )
                if not command.strip():
                    return json.dumps(
                        {"error": "local 模式下 command 不能为空（如 node、python）"},
                        ensure_ascii=False,
                    )
                try:
                    args_list = json.loads(args) if args and args.strip() != "[]" else []
                    if not isinstance(args_list, list):
                        raise ValueError("args 必须是 JSON 数组")
                except (json.JSONDecodeError, ValueError) as exc:
                    return json.dumps(
                        {"error": f"args 参数格式不合法: {exc}"},
                        ensure_ascii=False,
                    )

                if self._mcp_registry is None:
                    return json.dumps(
                        {"error": "MCP Registry 未绑定"},
                        ensure_ascii=False,
                    )

                # 写入配置
                try:
                    config_path = self._mcp_registry.config_path
                    config_path.parent.mkdir(parents=True, exist_ok=True)
                    # 读取现有配置
                    existing: dict[str, Any] = {}
                    if config_path.exists():
                        try:
                            existing = json.loads(config_path.read_text(encoding="utf-8"))
                        except Exception:
                            existing = {}
                    if not isinstance(existing, dict):
                        existing = {}

                    server_name = package_name.strip()
                    existing[server_name] = {
                        "type": "stdio",
                        "command": command.strip(),
                        "args": args_list,
                        "env": env_dict,
                    }
                    config_path.write_text(
                        json.dumps(existing, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )

                    # 触发 MCP 发现
                    try:
                        await self._mcp_registry.discover_and_register()
                    except Exception as disc_exc:
                        _log.warning(
                            "mcp_local_register_discover_failed",
                            server_name=server_name,
                            error=str(disc_exc),
                        )

                    return json.dumps(
                        {
                            "status": "registered",
                            "server_name": server_name,
                            "config_path": str(config_path),
                            "command": command.strip(),
                            "args": args_list,
                            "env_keys": list(env_dict.keys()),
                            "message": f"MCP server '{server_name}' 已注册到 {config_path}",
                        },
                        ensure_ascii=False,
                    )
                except Exception as exc:
                    return json.dumps(
                        {"error": f"本地注册失败: {exc}"},
                        ensure_ascii=False,
                    )

            # npm/pip 模式：走 MCP Installer
            if self._mcp_installer is None:
                return json.dumps(
                    {"error": "MCP Installer 未绑定，无法安装 MCP server"},
                    ensure_ascii=False,
                )
            try:
                task_id = await self._mcp_installer.install(
                    install_source=install_source,
                    package_name=package_name,
                    env=env_dict,
                )
                return json.dumps(
                    {
                        "status": "install_started",
                        "task_id": task_id,
                        "message": f"安装任务已启动，使用 mcp.install_status 查询进度（task_id={task_id}）",
                    },
                    ensure_ascii=False,
                )
            except ValueError as exc:
                return json.dumps({"error": str(exc)}, ensure_ascii=False)
            except Exception as exc:
                return json.dumps(
                    {"error": f"安装启动失败: {exc}"},
                    ensure_ascii=False,
                )

        @tool_contract(
            name="mcp.install_status",
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="mcp",
            tags=["mcp", "install", "status"],
            manifest_ref="builtin://mcp.install_status",
            metadata={
                "entrypoints": ["agent_runtime", "web"],
                "runtime_kinds": ["worker", "subagent", "graph_agent", "acp_runtime"],
            },
        )
        async def mcp_install_status(task_id: str) -> str:
            """查询 MCP server 安装任务的进度。

            Args:
                task_id: 由 mcp.install 返回的安装任务 ID
            """
            if self._mcp_installer is None:
                return json.dumps(
                    {"error": "MCP Installer 未绑定"},
                    ensure_ascii=False,
                )
            task = self._mcp_installer.get_install_status(task_id)
            if task is None:
                return json.dumps(
                    {"error": f"安装任务 {task_id} 不存在"},
                    ensure_ascii=False,
                )
            result = task.model_dump(mode="json")
            # 安装完成后自动刷新 capability pack，让新 MCP 工具立即可发现
            if task.status == "completed":
                try:
                    await self.refresh()
                    mcp_tools = [
                        t.tool_name
                        for t in (self._pack.tools if self._pack else [])
                        if t.tool_name.startswith("mcp.") and t.tool_name not in {
                            "mcp.servers.list", "mcp.tools.list", "mcp.tools.refresh",
                            "mcp.install", "mcp.install_status", "mcp.uninstall",
                        }
                    ]
                    result["available_mcp_tools"] = mcp_tools
                    result["hint"] = (
                        "安装已完成，新的 MCP 工具已注册。"
                        "你现在可以直接使用上面列出的 MCP 工具。"
                        "如果当前对话无法调用，请在新对话中使用。"
                    )
                except Exception:
                    pass
            return json.dumps(result, ensure_ascii=False, default=str)

        @tool_contract(
            name="mcp.uninstall",
            side_effect_level=SideEffectLevel.REVERSIBLE,
            tool_profile=ToolProfile.STANDARD,
            tool_group="mcp",
            tags=["mcp", "uninstall"],
            manifest_ref="builtin://mcp.uninstall",
            metadata={
                "entrypoints": ["agent_runtime", "web"],
                "runtime_kinds": ["worker", "subagent", "graph_agent", "acp_runtime"],
            },
        )
        async def mcp_uninstall(server_id: str) -> str:
            """卸载已安装的 MCP server。

            Args:
                server_id: 要卸载的 MCP server ID（通过 mcp.servers.list 查看）
            """
            if self._mcp_installer is None:
                return json.dumps(
                    {"error": "MCP Installer 未绑定"},
                    ensure_ascii=False,
                )
            try:
                result = await self._mcp_installer.uninstall(server_id)
                return json.dumps(
                    {"status": "uninstalled", **result},
                    ensure_ascii=False,
                )
            except ValueError as exc:
                return json.dumps({"error": str(exc)}, ensure_ascii=False)
            except Exception as exc:
                return json.dumps(
                    {"error": f"卸载失败: {exc}"},
                    ensure_ascii=False,
                )

        @tool_contract(
            name="pdf.inspect",
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="document",
            tags=["pdf", "document", "inspect"],
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
            name="memory.browse",
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="memory",
            tags=["memory", "browse", "directory"],
            manifest_ref="builtin://memory.browse",
            metadata={
                "entrypoints": ["agent_runtime", "web"],
                "runtime_kinds": ["worker", "subagent", "graph_agent"],
            },
        )
        async def memory_browse(
            prefix: str = "",
            partition: str = "",
            scope_id: str = "",
            group_by: str = "partition",
            offset: int = 0,
            limit: int = 20,
            project_id: str = "",
            workspace_id: str = "",
        ) -> str:
            """按 subject_key 前缀、partition、scope 等维度浏览记忆目录，获取分组统计和概览。"""

            project, workspace, _task = await _resolve_runtime_project_context(
                project_id=project_id,
                workspace_id=workspace_id,
            )
            result = await self._memory_console_service.browse_memory(
                project_id=project.project_id if project is not None else "",
                workspace_id=workspace.workspace_id if workspace is not None else None,
                scope_id=scope_id or "",
                prefix=prefix,
                partition=partition,
                group_by=group_by,
                offset=offset,
                limit=max(1, min(limit, 100)),
            )
            return json.dumps(result, ensure_ascii=False)

        @tool_contract(
            name="memory.search",
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="memory",
            tags=["memory", "search", "records"],
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
            derived_type: str = "",
            status: str = "",
            updated_after: str = "",
            updated_before: str = "",
        ) -> str:
            """按 query / scope / partition / layer / derived_type / status / 时间范围搜索 Memory。

            新增可选参数：
            - derived_type: 按派生类型筛选（profile/tom/entity/relation）
            - status: 按 SoR 状态筛选（current/archived/superseded）
            - updated_after: 更新时间下界（ISO 8601）
            - updated_before: 更新时间上界（ISO 8601）
            所有新参数均为可选，默认空字符串，向后兼容。
            """

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
                include_history=bool(status and status != "current"),
                include_vault_refs=True,
                limit=max(1, min(limit, 50)),
                derived_type=derived_type,
                status=status,
                updated_after=updated_after,
                updated_before=updated_before,
            )
            return json.dumps(document.model_dump(mode="json"), ensure_ascii=False)

        @tool_contract(
            name="memory.citations",
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="memory",
            tags=["memory", "citations", "evidence"],
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
            backend_status = await memory_service.get_backend_status()
            retrieval_profile = await self._memory_runtime_service.retrieval_profile_for_scope(
                project=project,
                workspace=workspace,
                backend_status=backend_status,
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
                    backend_status=backend_status,
                    degraded_reasons=["memory_scope_unresolved"],
                )
                return json.dumps(empty.model_dump(mode="json"), ensure_ascii=False)
            bounded_limit = max(1, min(limit, 8))
            hook_options = apply_retrieval_profile_to_hook_options(
                build_default_memory_recall_hook_options(
                    subject_hint=subject_hint,
                ).model_copy(
                    update={
                        "post_filter_mode": post_filter_mode,
                        "rerank_mode": rerank_mode,
                        "focus_terms": list(focus_terms or []),
                    }
                ),
                retrieval_profile,
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

        # ---- memory.write 工具 (Feature 065) ----

        _VALID_PARTITIONS = {"core", "profile", "work", "health", "finance", "chat"}

        @tool_contract(
            name="memory.write",
            side_effect_level=SideEffectLevel.REVERSIBLE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="memory",
            tags=["memory", "write", "persist"],
            manifest_ref="builtin://memory.write",
            metadata={
                "entrypoints": ["agent_runtime", "web"],
                "runtime_kinds": ["worker", "subagent", "graph_agent"],
            },
        )
        async def memory_write(
            subject_key: str,
            content: str,
            partition: str = "work",
            evidence_refs: list[dict[str, str]] | None = None,
            scope_id: str = "",
            project_id: str = "",
            workspace_id: str = "",
        ) -> str:
            """将重要信息持久化为长期记忆（SoR 记录）。

            当用户透露偏好、事实、决策或其他值得长期记住的信息时，
            调用此工具保存。系统会自动判断是新增还是更新已有记忆。

            Args:
                subject_key: 记忆主题标识，用 `/` 分层（如"用户偏好/编程语言"）
                content: 记忆内容，完整的陈述句
                partition: 业务分区 (core/profile/work/health/finance/chat)，默认 work
                evidence_refs: 证据引用列表 [{"ref_id": "...", "ref_type": "message"}]
                scope_id: 可选，指定 scope
                project_id: 可选，指定 project
                workspace_id: 可选，指定 workspace
            """
            # 1. 参数校验
            subject_key = subject_key.strip()
            if not subject_key:
                return json.dumps(
                    {"error": "MISSING_PARAM", "message": "subject_key 不能为空"},
                    ensure_ascii=False,
                )
            content = content.strip()
            if not content:
                return json.dumps(
                    {"error": "MISSING_PARAM", "message": "content 不能为空"},
                    ensure_ascii=False,
                )
            partition = partition.strip().lower()
            if partition not in _VALID_PARTITIONS:
                return json.dumps(
                    {
                        "error": "INVALID_PARTITION",
                        "message": f"无效的 partition 值 '{partition}'，有效值为: {', '.join(sorted(_VALID_PARTITIONS))}",
                    },
                    ensure_ascii=False,
                )

            # 2. 解析 project/workspace/scope context
            try:
                project, workspace, task = await _resolve_runtime_project_context(
                    project_id=project_id,
                    workspace_id=workspace_id,
                )
                scope_ids = await _resolve_memory_scope_ids(
                    task=task,
                    project=project,
                    workspace=workspace,
                    explicit_scope_id=scope_id,
                )
            except Exception as exc:
                return json.dumps(
                    {"error": "SCOPE_UNRESOLVED", "message": f"无法解析 memory scope: {exc}"},
                    ensure_ascii=False,
                )

            if not scope_ids:
                return json.dumps(
                    {"error": "SCOPE_UNRESOLVED", "message": "无法解析 memory scope，请确认 project 和 workspace 配置"},
                    ensure_ascii=False,
                )

            resolved_scope_id = scope_ids[0]

            # 3. 获取 MemoryService
            try:
                memory_service = await self._memory_runtime_service.memory_service_for_scope(
                    project=project,
                    workspace=workspace,
                )
            except Exception as exc:
                return json.dumps(
                    {"error": "INTERNAL_ERROR", "message": f"获取 memory 服务失败: {exc}"},
                    ensure_ascii=False,
                )

            # 4. 查询是否已存在 SoR → 决定 ADD 或 UPDATE
            memory_store = SqliteMemoryStore(store_group.conn)
            try:
                existing = await memory_store.get_current_sor(resolved_scope_id, subject_key)
            except Exception:
                existing = None

            if existing is not None:
                action = WriteAction.UPDATE
                expected_version = existing.version
                action_label = "update"
            else:
                action = WriteAction.ADD
                expected_version = None
                action_label = "add"

            # 5. 构建 evidence_refs
            refs: list[EvidenceRef] = []
            if evidence_refs:
                for ref_dict in evidence_refs:
                    refs.append(
                        EvidenceRef(
                            ref_id=str(ref_dict.get("ref_id", "")).strip(),
                            ref_type=str(ref_dict.get("ref_type", "message")).strip(),
                            snippet=str(ref_dict.get("snippet", "")).strip() or None,
                        )
                    )
            # 自动追加当前 task_id 作为证据
            try:
                _, ctx, current_task = await _current_parent()
                if current_task is not None:
                    refs.append(
                        EvidenceRef(
                            ref_id=current_task.task_id,
                            ref_type="task",
                        )
                    )
            except Exception:
                pass
            # 确保至少有一个 evidence_ref
            if not refs:
                refs.append(EvidenceRef(ref_id="memory.write", ref_type="tool"))

            # 6. 治理流程: propose_write -> validate_proposal -> commit_memory
            mem_partition = MemoryPartition(partition)
            try:
                proposal = await memory_service.propose_write(
                    scope_id=resolved_scope_id,
                    partition=mem_partition,
                    action=action,
                    subject_key=subject_key,
                    content=content,
                    rationale="memory.write tool",
                    confidence=1.0,
                    evidence_refs=refs,
                    expected_version=expected_version,
                    metadata={"source": "memory.write"},
                )
            except Exception as exc:
                return json.dumps(
                    {"error": "PROPOSE_FAILED", "message": f"记忆写入提案失败: {exc}"},
                    ensure_ascii=False,
                )

            try:
                validation = await memory_service.validate_proposal(proposal.proposal_id)
            except Exception as exc:
                return json.dumps(
                    {"error": "VALIDATE_FAILED", "message": f"记忆写入验证失败: {exc}"},
                    ensure_ascii=False,
                )

            if not validation.accepted:
                _log.info(
                    "memory_rejected",
                    subject_key=subject_key,
                    errors=validation.errors,
                    scope_id=resolved_scope_id,
                    action=action_label,
                )
                return json.dumps(
                    {
                        "status": "rejected",
                        "action": action_label,
                        "subject_key": subject_key,
                        "errors": validation.errors,
                        "scope_id": resolved_scope_id,
                    },
                    ensure_ascii=False,
                )

            try:
                commit_result = await memory_service.commit_memory(proposal.proposal_id)
            except Exception as exc:
                return json.dumps(
                    {"error": "COMMIT_FAILED", "message": f"记忆写入失败: {exc}"},
                    ensure_ascii=False,
                )

            _log.info(
                "memory_committed",
                action=action_label,
                subject_key=subject_key,
                memory_id=commit_result.memory_id,
                version=commit_result.version,
                scope_id=resolved_scope_id,
                partition=partition,
            )

            return json.dumps(
                {
                    "status": "committed",
                    "action": action_label,
                    "subject_key": subject_key,
                    "memory_id": commit_result.memory_id,
                    "version": commit_result.version,
                    "scope_id": resolved_scope_id,
                    "partition": partition,
                },
                ensure_ascii=False,
            )

        # ---- behavior 工具 ----

        # 预构建 review_mode 查找表（file_id -> BehaviorReviewMode）
        _behavior_review_modes = get_behavior_file_review_modes(include_advanced=True)

        def _extract_agent_slug(ctx: Any) -> str:
            """从执行上下文的 agent_runtime_id 中提取 agent slug。"""
            runtime_id = getattr(ctx, "agent_runtime_id", "") or ""
            for part in runtime_id.split("|"):
                if part.startswith("worker_profile:"):
                    return part.split(":", 1)[1].strip() or "butler"
            return "butler"

        def _extract_project_slug(ctx: Any) -> str:
            """从执行上下文的 agent_runtime_id 中提取 project slug。"""
            runtime_id = getattr(ctx, "agent_runtime_id", "") or ""
            for part in runtime_id.split("|"):
                if part.startswith("project:"):
                    raw = part.split(":", 1)[1].strip()
                    # project-default -> default
                    if raw.startswith("project-"):
                        return raw[len("project-"):]
                    return raw or "default"
            return "default"

        @tool_contract(
            name="behavior.write_file",
            side_effect_level=SideEffectLevel.REVERSIBLE,
            tool_profile=ToolProfile.STANDARD,
            tool_group="behavior",
            tags=["behavior", "file", "write", "context"],
            manifest_ref="builtin://behavior.write_file",
            metadata={
                "entrypoints": ["agent_runtime"],
                "runtime_kinds": ["worker", "subagent", "graph_agent"],
            },
        )
        async def behavior_write_file(
            file_id: str,
            content: str,
            confirmed: bool = False,
        ) -> str:
            """修改行为文件内容。file_id 为短名（如 USER.md），系统自动解析路径。"""
            file_id = file_id.strip()
            if not file_id:
                return json.dumps(
                    {"error": "MISSING_PARAM", "message": "file_id 不能为空"},
                    ensure_ascii=False,
                )

            # 从执行上下文获取 agent_slug 和 project_slug
            ctx = get_current_execution_context()
            agent_slug = _extract_agent_slug(ctx)
            project_slug = _extract_project_slug(ctx)

            # 根据 file_id 自动解析磁盘路径
            try:
                resolved = resolve_write_path_by_file_id(
                    self._project_root,
                    file_id,
                    agent_slug=agent_slug,
                    project_slug=project_slug,
                )
            except ValueError as exc:
                return json.dumps(
                    {"error": "INVALID_FILE_ID", "message": str(exc)},
                    ensure_ascii=False,
                )

            # 字符预算检查
            budget_result = check_behavior_file_budget(file_id, content)
            if not budget_result["within_budget"]:
                return json.dumps(
                    {
                        "file_id": file_id,
                        "written": False,
                        "error": "BUDGET_EXCEEDED",
                        "current_chars": budget_result["current_chars"],
                        "budget_chars": budget_result["budget_chars"],
                        "exceeded_by": budget_result["exceeded_by"],
                        "message": (
                            f"内容超出字符预算 {budget_result['exceeded_by']} 字符，请精简后重试"
                        ),
                    },
                    ensure_ascii=False,
                )

            # 查找 review_mode
            review_mode = _behavior_review_modes.get(
                file_id, BehaviorReviewMode.REVIEW_REQUIRED,
            )

            # proposal 模式：review_required 且未确认时返回 proposal
            if review_mode == BehaviorReviewMode.REVIEW_REQUIRED and not confirmed:
                # 读取当前内容用于对比
                try:
                    if resolved.exists():
                        current_content = resolved.read_text(encoding="utf-8")
                        exists = True
                    else:
                        current_content = ""
                        exists = False
                except Exception:
                    current_content = ""
                    exists = False
                return json.dumps(
                    {
                        "file_id": file_id,
                        "proposal": True,
                        "review_mode": review_mode.value if hasattr(review_mode, "value") else str(review_mode),
                        "current_content": current_content,
                        "proposed_content": content,
                        "current_chars": len(current_content),
                        "proposed_chars": len(content),
                        "budget_chars": budget_result["budget_chars"],
                        "message": "请向用户展示修改摘要并请求确认，确认后再次调用并设置 confirmed=true",
                    },
                    ensure_ascii=False,
                )

            # 实际写入磁盘（confirmed=true 时直接信任 Agent 传入的 content）
            try:
                resolved.parent.mkdir(parents=True, exist_ok=True)
                resolved.write_text(content, encoding="utf-8")
            except Exception as exc:
                return json.dumps(
                    {"error": "FILE_WRITE_ERROR", "message": str(exc)},
                    ensure_ascii=False,
                )

            # 记录 structlog 事件（FR-018）
            _log = structlog.get_logger("behavior.write_file")
            _log.info(
                "behavior_file_written",
                source="llm_tool",
                file_id=file_id,
                chars_written=len(content),
                resolved_path=str(resolved),
            )

            # Feature 063 T1.4: 路径 A — 检测 BOOTSTRAP.md 的 <!-- COMPLETED --> 标记
            onboarding_completed = False
            if file_id == "BOOTSTRAP.md" and BOOTSTRAP_COMPLETED_MARKER in content:
                try:
                    mark_onboarding_completed(self._project_root)
                    onboarding_completed = True
                    _log.info(
                        "onboarding_completed_via_marker",
                        file_id=file_id,
                    )
                except Exception:
                    _log.warning(
                        "onboarding_completion_mark_failed",
                        file_id=file_id,
                    )

            # Feature 063 T2.4: 所有副作用完成后 invalidate 缓存
            # 确保 onboarding 标记等状态变更已落盘，避免缓存重建读到过期状态
            from octoagent.gateway.services.agent_decision import (
                invalidate_behavior_pack_cache,
            )

            invalidate_behavior_pack_cache(project_root=self._project_root)

            result_payload: dict[str, Any] = {
                "file_id": file_id,
                "written": True,
                "chars_written": len(content),
                "budget_chars": budget_result["budget_chars"],
            }
            if onboarding_completed:
                result_payload["onboarding_completed"] = True
            return json.dumps(result_payload, ensure_ascii=False)

        # ---- Feature 065: config 管理工具 ----
        # 让 Worker 能直接读写 octoagent.yaml，不再依赖 terminal.exec 手动编辑

        from octoagent.provider.dx.config_wizard import load_config as _load_config
        from octoagent.provider.dx.config_wizard import save_config as _save_config

        def _parse_setup_draft_json(raw: str) -> dict[str, Any]:
            text = str(raw or "").strip()
            if not text:
                return {}
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"draft_json 不是合法 JSON：{exc.msg}") from exc
            if not isinstance(payload, dict):
                raise ValueError("draft_json 必须是 object JSON")
            return payload

        @tool_contract(
            name="config.inspect",
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="config",
            tags=["config", "provider", "model", "inspect"],
            manifest_ref="builtin://config.inspect",
            metadata={
                "entrypoints": ["agent_runtime", "web"],
                "runtime_kinds": ["worker", "subagent", "graph_agent"],
            },
        )
        async def config_inspect(section: str = "") -> str:
            """读取 octoagent.yaml 配置。section 可选: providers, model_aliases, memory, channels, runtime, 留空返回全部。"""
            config = _load_config(self._project_root)
            if config is None:
                return json.dumps(
                    {"error": "CONFIG_NOT_FOUND", "message": "octoagent.yaml 不存在或解析失败"},
                    ensure_ascii=False,
                )
            data = config.model_dump(mode="json")
            section = section.strip().lower()
            if section and section in data:
                return json.dumps({section: data[section]}, ensure_ascii=False, indent=2)
            if section:
                return json.dumps(
                    {"error": "UNKNOWN_SECTION", "message": f"未知 section: {section}", "available": list(data.keys())},
                    ensure_ascii=False,
                )
            return json.dumps(data, ensure_ascii=False, indent=2)

        @tool_contract(
            name="config.add_provider",
            side_effect_level=SideEffectLevel.REVERSIBLE,
            tool_profile=ToolProfile.STANDARD,
            tool_group="config",
            tags=["config", "provider", "add"],
            manifest_ref="builtin://config.add_provider",
            metadata={
                "entrypoints": ["agent_runtime", "web"],
                "runtime_kinds": ["worker", "subagent", "graph_agent"],
            },
        )
        async def config_add_provider(
            provider_id: str,
            name: str = "",
            auth_type: str = "",
            api_key_env: str = "",
            base_url: str = "",
            enabled: bool | None = None,
            clear_base_url: bool = False,
        ) -> str:
            """添加或更新一个 LLM Provider 到 octoagent.yaml。修改后可执行 config.sync 重新生成 LiteLLM 衍生配置。"""
            config = _load_config(self._project_root)
            if config is None:
                return json.dumps(
                    {"error": "CONFIG_NOT_FOUND", "message": "octoagent.yaml 不存在"},
                    ensure_ascii=False,
                )
            provider_id = provider_id.strip().lower()
            if not provider_id:
                return json.dumps(
                    {"error": "MISSING_PARAM", "message": "provider_id 不能为空"},
                    ensure_ascii=False,
                )
            existing_provider = next((p for p in config.providers if p.id == provider_id), None)
            resolved_name = name.strip() or (
                existing_provider.name if existing_provider is not None else provider_id
            )
            resolved_auth_type = auth_type.strip() or (
                existing_provider.auth_type if existing_provider is not None else "api_key"
            )
            resolved_api_key_env = api_key_env.strip() or (
                existing_provider.api_key_env
                if existing_provider is not None
                else f"{provider_id.upper().replace('-', '_')}_API_KEY"
            )
            resolved_base_url = ""
            if clear_base_url:
                resolved_base_url = ""
            elif base_url.strip():
                resolved_base_url = base_url.strip()
            elif existing_provider is not None:
                resolved_base_url = existing_provider.base_url
            resolved_enabled = (
                enabled
                if enabled is not None
                else (existing_provider.enabled if existing_provider is not None else True)
            )

            from octoagent.provider.dx.config_schema import ProviderEntry
            new_entry = ProviderEntry(
                id=provider_id,
                name=resolved_name,
                auth_type=resolved_auth_type,
                api_key_env=resolved_api_key_env,
                enabled=resolved_enabled,
                base_url=resolved_base_url,
            )
            # 更新或追加
            found = False
            for i, p in enumerate(config.providers):
                if p.id == provider_id:
                    config.providers[i] = new_entry
                    found = True
                    break
            if not found:
                config.providers.append(new_entry)

            from datetime import date
            config.updated_at = date.today().isoformat()
            _save_config(config, self._project_root)
            action = "updated" if found else "added"
            return json.dumps(
                {
                    "success": True,
                    "action": action,
                    "provider_id": provider_id,
                    "hint": (
                        "执行 config.sync 重新生成 litellm-config.yaml；"
                        "如需保存并启用真实模型，可继续执行 setup.quick_connect；"
                        "也可使用 Web 设置页或 CLI `octo setup`。"
                    ),
                },
                ensure_ascii=False,
            )

        @tool_contract(
            name="config.set_model_alias",
            side_effect_level=SideEffectLevel.REVERSIBLE,
            tool_profile=ToolProfile.STANDARD,
            tool_group="config",
            tags=["config", "model", "alias", "set"],
            manifest_ref="builtin://config.set_model_alias",
            metadata={
                "entrypoints": ["agent_runtime", "web"],
                "runtime_kinds": ["worker", "subagent", "graph_agent"],
            },
        )
        async def config_set_model_alias(
            alias: str,
            provider: str,
            model: str,
            description: str = "",
            thinking_level: str = "",
        ) -> str:
            """设置模型别名（如 main, cheap）到具体 Provider + 模型。修改后可执行 config.sync 重新生成 LiteLLM 衍生配置。"""
            config = _load_config(self._project_root)
            if config is None:
                return json.dumps(
                    {"error": "CONFIG_NOT_FOUND", "message": "octoagent.yaml 不存在"},
                    ensure_ascii=False,
                )
            alias = alias.strip().lower()
            if not alias or not provider.strip() or not model.strip():
                return json.dumps(
                    {"error": "MISSING_PARAM", "message": "alias, provider, model 都不能为空"},
                    ensure_ascii=False,
                )
            # 校验 provider 存在
            provider_ids = {p.id for p in config.providers}
            if provider.strip() not in provider_ids:
                return json.dumps(
                    {"error": "UNKNOWN_PROVIDER", "message": f"Provider '{provider}' 不存在", "available": sorted(provider_ids)},
                    ensure_ascii=False,
                )
            from octoagent.provider.dx.config_schema import ModelAlias as _ModelAlias
            kwargs: dict[str, Any] = {
                "provider": provider.strip(),
                "model": model.strip(),
            }
            if description.strip():
                kwargs["description"] = description.strip()
            if thinking_level.strip():
                kwargs["thinking_level"] = thinking_level.strip()
            config.model_aliases[alias] = _ModelAlias(**kwargs)

            from datetime import date
            config.updated_at = date.today().isoformat()
            _save_config(config, self._project_root)
            return json.dumps(
                {
                    "success": True,
                    "alias": alias,
                    "provider": provider.strip(),
                    "model": model.strip(),
                    "hint": (
                        "执行 config.sync 重新生成 litellm-config.yaml；"
                        "如需保存并启用真实模型，可继续执行 setup.quick_connect；"
                        "也可使用 Web 设置页或 CLI `octo setup`。"
                    ),
                },
                ensure_ascii=False,
            )

        @tool_contract(
            name="config.sync",
            side_effect_level=SideEffectLevel.REVERSIBLE,
            tool_profile=ToolProfile.STANDARD,
            tool_group="config",
            tags=["config", "sync", "litellm"],
            manifest_ref="builtin://config.sync",
            metadata={
                "entrypoints": ["agent_runtime", "web"],
                "runtime_kinds": ["worker", "subagent", "graph_agent"],
            },
        )
        async def config_sync() -> str:
            """把 octoagent.yaml 重新生成到 litellm-config.yaml。等同于 `octo config sync`，但不会自动重启 runtime。"""
            try:
                config = _load_config(self._project_root)
                if config is None:
                    return json.dumps(
                        {"error": "CONFIG_NOT_FOUND", "message": "octoagent.yaml 不存在"},
                        ensure_ascii=False,
                    )
                from octoagent.provider.dx.litellm_generator import generate_litellm_config as _gen_litellm
                out_path = _gen_litellm(config, self._project_root)
                enabled_providers = [p.id for p in config.providers if p.enabled]
                enabled_aliases = [
                    k for k, v in config.model_aliases.items()
                    if any(p.id == v.provider and p.enabled for p in config.providers)
                ]
                return json.dumps(
                    {
                        "success": True,
                        "message": "LiteLLM 衍生配置已同步",
                        "output_path": str(out_path),
                        "enabled_providers": enabled_providers,
                        "enabled_aliases": enabled_aliases,
                        "hint": (
                            "这一步只会重新生成 litellm-config.yaml；"
                            "如需启动或切换到真实模型，请使用 Web 设置页的“连接真实模型”"
                            "或 CLI `octo setup`。"
                        ),
                    },
                    ensure_ascii=False,
                )
            except Exception as exc:
                return json.dumps(
                    {"error": "SYNC_FAILED", "message": str(exc)},
                    ensure_ascii=False,
                )

        @tool_contract(
            name="setup.review",
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.STANDARD,
            tool_group="setup",
            tags=["setup", "review", "config"],
            manifest_ref="builtin://setup.review",
            metadata={
                "entrypoints": ["agent_runtime", "web"],
                "runtime_kinds": ["worker", "subagent", "graph_agent"],
            },
        )
        async def setup_review(draft_json: str = "{}") -> str:
            """调用 canonical setup.review 检查 draft 是否可保存。draft_json 需为 JSON object。"""
            try:
                draft = _parse_setup_draft_json(draft_json)
            except ValueError as exc:
                return json.dumps(
                    {"error": "INVALID_DRAFT_JSON", "message": str(exc)},
                    ensure_ascii=False,
                )

            from octoagent.provider.dx.setup_governance_adapter import (
                LocalSetupGovernanceAdapter,
            )

            adapter = LocalSetupGovernanceAdapter(self._project_root)
            prepared = await adapter.prepare_wizard_draft(draft)
            result = await adapter.review(prepared)
            payload: dict[str, Any] = {
                "success": str(result.status) == "completed",
                "status": str(result.status),
                "code": result.code,
                "message": result.message,
            }
            if isinstance(result.data, dict):
                if isinstance(result.data.get("review"), dict):
                    payload["review"] = result.data["review"]
                if result.data.get("warnings"):
                    payload["warnings"] = result.data["warnings"]
            if not payload["success"]:
                payload["error"] = result.code
            return json.dumps(payload, ensure_ascii=False)

        @tool_contract(
            name="setup.quick_connect",
            side_effect_level=SideEffectLevel.REVERSIBLE,
            tool_profile=ToolProfile.STANDARD,
            tool_group="setup",
            tags=["setup", "quick_connect", "activation"],
            manifest_ref="builtin://setup.quick_connect",
            metadata={
                "entrypoints": ["agent_runtime", "web"],
                "runtime_kinds": ["worker", "subagent", "graph_agent"],
            },
        )
        async def setup_quick_connect(draft_json: str) -> str:
            """调用 canonical setup.quick_connect 完成保存、同步衍生配置与 runtime activation。"""
            try:
                draft = _parse_setup_draft_json(draft_json)
            except ValueError as exc:
                return json.dumps(
                    {"error": "INVALID_DRAFT_JSON", "message": str(exc)},
                    ensure_ascii=False,
                )

            from octoagent.provider.dx.setup_governance_adapter import (
                LocalSetupGovernanceAdapter,
            )

            adapter = LocalSetupGovernanceAdapter(self._project_root)
            prepared = await adapter.prepare_wizard_draft(draft)
            result = await adapter.quick_connect(prepared)
            payload: dict[str, Any] = {
                "success": str(result.status) == "completed",
                "status": str(result.status),
                "code": result.code,
                "message": result.message,
            }
            if isinstance(result.data, dict):
                if isinstance(result.data.get("review"), dict):
                    payload["review"] = result.data["review"]
                if isinstance(result.data.get("activation"), dict):
                    payload["activation"] = result.data["activation"]
                if result.data.get("resource_refs"):
                    payload["resource_refs"] = result.data["resource_refs"]
            if not payload["success"]:
                payload["error"] = result.code
            return json.dumps(payload, ensure_ascii=False)

        # Feature 057: skills tool -- LLM 主动发现和加载 SKILL.md
        from octoagent.skills.tools import SkillsTool as _SkillsTool

        _skills_tool = _SkillsTool(self._skill_discovery)

        @tool_contract(
            name="skills",
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="skills",
            tags=["skills", "discovery", "knowledge"],
            manifest_ref="builtin://skills",
            metadata={
                "entrypoints": ["agent_runtime", "web"],
                "runtime_kinds": ["worker", "subagent"],
            },
        )
        async def skills(action: str, name: str = "") -> str:
            """管理和使用 SKILL.md 定义的技能。支持列出所有可用技能的摘要，或加载指定技能的完整指令到当前会话。"""
            # 读取 AgentSession.metadata 作为 loaded_skill_names 的持久化层
            session_metadata: dict[str, Any] | None = None
            agent_session_id = ""
            try:
                context = get_current_execution_context()
                agent_session_id = getattr(context, "agent_session_id", "") or ""
            except Exception:
                pass

            if agent_session_id and action in ("load", "unload", "list"):
                try:
                    agent_session = await self._stores.agent_context_store.get_agent_session(
                        agent_session_id
                    )
                    if agent_session is not None:
                        session_metadata = agent_session.metadata
                except Exception as _skill_sess_exc:
                    _log.warning(
                        "skills_session_lookup_failed",
                        agent_session_id=agent_session_id[:80],
                        error=str(_skill_sess_exc),
                    )

            if session_metadata is None and action in ("load", "unload"):
                # 降级：使用空 metadata 而非拒绝执行。
                # skill load 只需要读写 loaded_skill_names，空 dict 足以让首次 load 工作。
                _log.warning(
                    "skills_session_metadata_fallback",
                    agent_session_id=agent_session_id[:80],
                    action=action,
                )
                session_metadata = {}

            result = await _skills_tool.execute(
                action=action,
                name=name,
                session_metadata=session_metadata,
            )

            # 写回 AgentSession.metadata，持久化 loaded_skill_names
            if (
                action in ("load", "unload")
                and agent_session_id
                and session_metadata is not None
            ):
                try:
                    agent_session = await self._stores.agent_context_store.get_agent_session(
                        agent_session_id
                    )
                    if agent_session is not None:
                        agent_session.metadata["loaded_skill_names"] = session_metadata.get(
                            "loaded_skill_names", []
                        )
                        from datetime import datetime, UTC
                        agent_session.updated_at = datetime.now(UTC)
                        await self._stores.agent_context_store.save_agent_session(agent_session)
                except Exception as exc:
                    _log.warning("skill_session_metadata_writeback_failed", error=str(exc))

            return result

        for handler in (
            project_inspect,
            task_inspect,
            artifact_list,
            filesystem_list_dir,
            filesystem_read_text,
            filesystem_write_text,
            terminal_exec,
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
            mcp_install,
            mcp_install_status,
            mcp_uninstall,
            pdf_inspect,
            image_inspect,
            tts_speak,
            canvas_write,
            memory_read,
            memory_browse,
            memory_search,
            memory_citations,
            memory_recall,
            behavior_write_file,
            config_inspect,
            config_add_provider,
            config_set_model_alias,
            config_sync,
            setup_review,
            setup_quick_connect,
            skills,
        ):
            await self._tool_broker.try_register(
                reflect_tool_schema(handler),
                handler,
            )

        # Feature 061 T-020/T-021: 注册 tool_search 核心工具
        event_store = getattr(self._stores, "event_store", None)
        tool_search_handler = create_tool_search_handler(
            tool_index=self._tool_index,
            event_store=event_store,
        )
        await self._tool_broker.try_register(
            reflect_tool_schema(tool_search_handler),
            tool_search_handler,
        )

    def _build_worker_profiles(self) -> dict[str, WorkerCapabilityProfile]:
        # Feature 065: WorkerType 枚举已删除，所有 Agent 共享同一工具集。
        # 差异化通过 PermissionPreset + Behavior Files 表达。
        # 保留 "general" profile 作为唯一内建 profile，兼容历史数据。
        _UNIFIED_TOOL_GROUPS = [
            "project",
            "artifact",
            "document",
            "session",
            "filesystem",
            "terminal",
            "network",
            "browser",
            "memory",
            "supervision",
            "delegation",
            "mcp",
            "skills",
            "runtime",
            "automation",
            "media",
            "config",
            "setup",
            "behavior",
        ]
        return {
            "general": WorkerCapabilityProfile(
                worker_type="general",
                capabilities=["llm_generation", "general", "ops", "research", "dev"],
                default_model_alias="main",
                default_tool_profile="standard",
                default_tool_groups=list(_UNIFIED_TOOL_GROUPS),
                bootstrap_file_ids=["bootstrap:shared"],
                runtime_kinds=[
                    RuntimeKind.WORKER,
                    RuntimeKind.SUBAGENT,
                    RuntimeKind.ACP_RUNTIME,
                    RuntimeKind.GRAPH_AGENT,
                ],
            ),
        }

    def _build_bootstrap_templates(self) -> dict[str, WorkerBootstrapFile]:
        # Feature 065: WorkerType 枚举已删除，bootstrap 模板对所有 Agent 共享。
        return {
            "bootstrap:shared": WorkerBootstrapFile(
                file_id="bootstrap:shared",
                path_hint="bootstrap/shared.md",
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
                    "Runtime Kinds: {{runtime_kinds}}\n"
                    "Ambient Degraded Reasons: {{ambient_degraded_reasons}}\n"
                    "必须继续走 ToolBroker / Policy / audit，不得绕过治理面。"
                ),
                metadata={"scope": "shared"},
            ),
        }

    @staticmethod
    def _builtin_worker_type_from_profile_id(profile_id: str) -> str | None:
        normalized = profile_id.strip().lower()
        if not normalized.startswith("singleton:"):
            return None
        suffix = normalized.split(":", 1)[1]
        # Feature 065: 所有 singleton: profile 都映射到 "general"
        if suffix in {"general", "ops", "research", "dev"}:
            return "general"
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
    def _requires_standard_web_access(objective: str) -> bool:
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
        return any(token in lowered for token in common_tokens)

    @staticmethod
    def _requires_weather_toolset(objective: str) -> bool:
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

    @staticmethod
    def _effective_tool_profile_for_objective(*, objective: str) -> str:
        del objective
        return ToolProfile.STANDARD.value

    def _build_worker_assignment(
        self,
        objective: str,
        *,
        index: int,
    ) -> _WorkerPlanAssignment:
        tool_profile = ToolProfile.STANDARD.value
        return _WorkerPlanAssignment(
            objective=objective,
            worker_type="general",
            target_kind=RuntimeKind.SUBAGENT.value,
            tool_profile=tool_profile,
            title=f"worker-{index}",
            reason="子任务由 worker 处理。",
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
        self._enforce_child_target_kind_policy(target_kind)
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

    @staticmethod
    def _enforce_child_target_kind_policy(target_kind: str) -> None:
        normalized_target_kind = str(target_kind).strip().lower()
        if normalized_target_kind != DelegationTargetKind.WORKER.value:
            return
        try:
            execution_context = get_current_execution_context()
        except RuntimeError:
            return
        runtime_kind = str(execution_context.runtime_kind or "").strip().lower()
        runtime_turn_kind = ""
        if execution_context.runtime_context is not None:
            runtime_turn_kind = str(
                execution_context.runtime_context.turn_executor_kind.value
            ).strip().lower()
        if runtime_kind == RuntimeKind.WORKER.value or runtime_turn_kind == TurnExecutorKind.WORKER.value:
            raise RuntimeError(
                "worker runtime cannot delegate to another worker; use a subagent target instead"
            )

    async def _resolve_fallback_toolset(self, worker_type: str = "general") -> list[str]:
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
        worker_type: str = "general",
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

    @staticmethod
    def _skill_item_state(
        *,
        item_id: str,
        enabled_by_default: bool,
        selected_item_ids: set[str],
        disabled_item_ids: set[str],
    ) -> tuple[bool, bool]:
        if item_id in selected_item_ids:
            return True, True
        if item_id in disabled_item_ids:
            return False, True
        return enabled_by_default, False

    def _mcp_install_summary(self) -> dict[str, int]:
        """返回 MCP 安装来源统计（Feature 058）。"""
        if self._mcp_installer is None:
            return {}
        records = self._mcp_installer.list_installs()
        auto_count = sum(
            1 for r in records if r.install_source and r.install_source != "manual"
        )
        return {
            "auto_installed_count": auto_count,
            "manual_count": (
                (self._mcp_registry.configured_server_count() if self._mcp_registry else 0)
                - auto_count
            ),
        }

    @staticmethod
    def _enrich_mcp_metadata(
        metadata: dict,
        install_source_map: dict[str, str],
    ) -> dict:
        """将 McpInstallRecord.install_source 注入 MCP 工具元数据。"""
        if metadata.get("source") != "mcp":
            return metadata
        server_name = str(metadata.get("mcp_server_name", "")).strip()
        if server_name and server_name in install_source_map:
            metadata["install_source"] = install_source_map[server_name]
        else:
            metadata.setdefault("install_source", "manual")
        return metadata

    def _resolve_mcp_mount_policy(self, server_name: str) -> str:
        if self._mcp_registry is None:
            return "explicit"
        return self._mcp_registry.get_mount_policy(server_name)

    def _mcp_tool_enabled_by_default(
        self,
        *,
        server_name: str,
        tool_profile: str,
    ) -> bool:
        mount_policy = self._resolve_mcp_mount_policy(server_name)
        normalized_profile = str(tool_profile).strip().lower() or "standard"
        if mount_policy == "auto_all":
            return True
        if mount_policy == "auto_readonly":
            return normalized_profile == ToolProfile.MINIMAL.value
        return False

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

        def selection_state(
            item_id: str,
            *,
            enabled_by_default: bool,
        ) -> tuple[bool, bool]:
            project_selected, project_explicit = self._skill_item_state(
                item_id=item_id,
                enabled_by_default=enabled_by_default,
                selected_item_ids=project_selected_item_ids,
                disabled_item_ids=project_disabled_item_ids,
            )
            profile_selected, profile_explicit = self._skill_item_state(
                item_id=item_id,
                enabled_by_default=project_selected,
                selected_item_ids=profile_selected_item_ids,
                disabled_item_ids=profile_disabled_item_ids,
            )
            return profile_selected, project_explicit or profile_explicit

        skills = [
            skill
            for skill in pack.skills
            if selection_state(item_id=f"skill:{skill.skill_id}", enabled_by_default=True)[0]
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
            if tool.tool_group == "mcp" and str(tool.metadata.get("source", "")).strip() == "mcp":
                server_name = str(tool.metadata.get("mcp_server_name", "")).strip() or "mcp"
                include, explicitly_selected = selection_state(
                    item_id=f"mcp:{server_name}",
                    enabled_by_default=self._mcp_tool_enabled_by_default(
                        server_name=server_name,
                        tool_profile=tool.tool_profile,
                    ),
                )
                if include and not explicitly_selected:
                    include = self._mcp_tool_enabled_by_default(
                        server_name=server_name,
                        tool_profile=tool.tool_profile,
                    )
            else:
                include = True
                if tool.tool_name in governed_skill_tool_names:
                    include = tool.tool_name in enabled_skill_tool_names
                include = selection_state(
                    item_id=f"skill:{tool.tool_name}",
                    enabled_by_default=include,
                )[0]
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
        filtered_recommended_tools = [
            tool_name
            for tool_name in selection.recommended_tools
            if tool_name in allowed_tool_names
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
        if not filtered_recommended_tools:
            filtered_recommended_tools = list(filtered_selected_tools)

        deduped_warnings: list[str] = []
        for warning in warnings:
            if warning not in deduped_warnings:
                deduped_warnings.append(warning)
        return selection.model_copy(
            update={
                "selected_tools": filtered_selected_tools,
                "recommended_tools": filtered_recommended_tools,
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
        if tool_name in {"mcp.install", "mcp.install_status", "mcp.uninstall"}:
            if self._mcp_installer is None:
                return BuiltinToolAvailabilityStatus.UNAVAILABLE
            return BuiltinToolAvailabilityStatus.AVAILABLE
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
        if tool_name in {"mcp.install", "mcp.install_status", "mcp.uninstall"}:
            if self._mcp_installer is None:
                return "mcp_installer_unbound"
            return ""
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
        if tool_name in {"mcp.install", "mcp.install_status", "mcp.uninstall"}:
            if self._mcp_installer is None:
                return "McpInstallerService 未绑定，检查 Gateway 初始化流程"
            return ""
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
            "memory.write": ["agent_runtime", "web"],
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
        timeout_seconds: float = 30.0,
    ) -> _BrowserSessionState:
        normalized_url = self._validate_remote_url(url)
        async with httpx.AsyncClient(
            timeout=max(0.1, timeout_seconds),
            headers={"User-Agent": "OctoAgent Browser Tool/0.1"},
        ) as client:
            response = await client.get(normalized_url, follow_redirects=True)
        html = response.text[:500_000]  # 安全保底，LargeOutputHandler 按上下文比例统一管理
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
            html_preview=html[:50_000],
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
        timeout_seconds: float = 30.0,
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
        max_chars: int = 100_000,
        link_limit: int = 20,
    ) -> dict[str, Any]:
        effective_chars = max(100, min(max_chars, 500_000))
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
            "text_preview": _truncate_text(session.text_content, limit=effective_chars),
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
