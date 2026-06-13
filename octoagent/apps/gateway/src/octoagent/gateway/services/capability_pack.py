"""Feature 030: bundled capability pack / ToolIndex / bootstrap。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

# F108a W5：httpx 仅作 patch 锚点保留——test_capability_pack_tools.py 经字符串路径
# "octoagent.gateway.services.capability_pack.httpx.AsyncClient" 解析到 httpx 模块
# 对象后全局 patch；主文件自身已无直接 httpx 引用（browser / web 簇已迁 mixin）。
import httpx  # noqa: F401 -- patch 锚点：test_capability_pack_tools patch capability_pack.httpx.AsyncClient
import structlog
from octoagent.core.models import (
    AgentProfileStatus,
    BuiltinToolAvailabilityStatus,
    BundledCapabilityPack,
    BundledSkillDefinition,
    BundledToolDefinition,
    DelegationTargetKind,
    DynamicToolSelection,
    EffectiveToolUniverse,
    NormalizedMessage,
    OwnerProfile,
    RuntimeKind,
    ToolAvailabilityExplanation,
    ToolIndexQuery,
    WorkerBootstrapFile,
    WorkerCapabilityProfile,
)
from octoagent.gateway.services.memory.memory_console_service import MemoryConsoleService
from octoagent.gateway.services.memory.memory_runtime_service import MemoryRuntimeService
from octoagent.skills import SkillDiscovery
from octoagent.tooling import (
    ToolBroker,
    ToolIndex,
    reflect_tool_schema,
)

# Feature 070: 权限 Hook 已移除，权限检查内联到 ToolBroker.execute()
from octoagent.tooling.models import CoreToolSet, DeferredToolEntry
from octoagent.tooling.permission import (
    ApprovalOverrideCacheProtocol,
    _ApprovalOverrideMemoryCache,
)
from ulid import ULID

from .tool_search_tool import create_tool_search_handler

_log = structlog.get_logger()

from .agent_context import build_ambient_runtime_facts
from .agent_decision import is_worker_behavior_profile  # F117 Wave 2bc: worker 镜像判别
from .execution_context import get_current_execution_context

if TYPE_CHECKING:
    from .mcp_installer import McpInstallerService
    from .mcp_registry import McpRegistryService


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


# ToolProfile 等级映射（minimal < standard < privileged）
_PROFILE_LEVELS: dict[str, int] = {"minimal": 0, "standard": 1, "privileged": 2}


def _profile_allows(tool_profile: str, context_profile: str) -> bool:
    """检查工具的 profile 是否在 context_profile 允许范围内。"""
    tool_level = _PROFILE_LEVELS.get(str(tool_profile).strip().lower(), 1)
    ctx_level = _PROFILE_LEVELS.get(str(context_profile).strip().lower(), 1)
    return tool_level <= ctx_level


from .builtin_tools._browser_support import (
    _BrowserSessionState,
)

# F108a W5：5 个职责簇 mixin（browser / web / media / worker_plan / availability）。
# _ssrf_request_hook 经 browser 模块单一定义后在此 re-export，保外部 import 路径不变
# （e2e_live/test_e2e_ssrf_guard.py 等 from capability_pack import _ssrf_request_hook）。
from .capability_pack_availability import ToolAvailabilityMixin
from .capability_pack_browser import (  # noqa: F401 -- re-export：test_e2e_ssrf_guard 直接 from capability_pack import
    BrowserSessionMixin,
    _ssrf_request_hook,
)
from .capability_pack_media import MediaInspectMixin
from .capability_pack_web import WebSearchMixin
from .capability_pack_worker_plan import WorkerPlanMixin


class CapabilityPackService(
    BrowserSessionMixin,
    WebSearchMixin,
    MediaInspectMixin,
    WorkerPlanMixin,
    ToolAvailabilityMixin,
):
    """统一管理 bundled tools / skills / ToolIndex / worker bootstrap。"""

    def __init__(
        self,
        *,
        project_root: Path,
        store_group,
        tool_broker: ToolBroker,
        preferred_tool_index_backend: str = "auto",
        approval_override_cache: ApprovalOverrideCacheProtocol | None = None,
        provider_router: Any | None = None,
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
        # F099 Codex Final F2 修复：ApprovalGate 实例（worker.escalate_permission 使用）
        # 通过 bind_approval_gate 在 startup() 之前注入（octo_harness.py 负责创建和绑定）
        self._approval_gate: Any = None
        # Feature 080 Phase 4：embedding 路由通过 ProviderRouter 直连，
        # 不再依赖 LiteLLM Proxy（router 为 None 时 BuiltinMemUBridge 走 fallback）
        self._provider_router = provider_router
        self._memory_console_service = MemoryConsoleService(
            project_root,
            store_group=store_group,
        )
        self._memory_runtime_service = MemoryRuntimeService(
            project_root,
            store_group=store_group,
            provider_router=provider_router,
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
        # 延迟初始化，在 _register_builtin_tools 完成后赋值
        self._tool_deps = None

    @property
    def tool_broker(self) -> ToolBroker:
        return self._tool_broker

    @property
    def skill_discovery(self) -> SkillDiscovery:
        """Feature 057: 返回 SkillDiscovery 实例供依赖注入使用。"""
        return self._skill_discovery

    def bind_task_runner(self, task_runner) -> None:
        self._task_runner = task_runner
        if self._tool_deps is not None:
            self._tool_deps._task_runner = task_runner

    def bind_delegation_plane(self, delegation_plane) -> None:
        self._delegation_plane = delegation_plane
        if self._tool_deps is not None:
            self._tool_deps._delegation_plane = delegation_plane

    def bind_mcp_registry(self, mcp_registry: McpRegistryService) -> None:
        self._mcp_registry = mcp_registry
        if self._tool_deps is not None:
            self._tool_deps._mcp_registry = mcp_registry

    def bind_mcp_installer(self, mcp_installer: McpInstallerService) -> None:
        self._mcp_installer = mcp_installer
        if self._tool_deps is not None:
            self._tool_deps._mcp_installer = mcp_installer

    def bind_approval_gate(self, approval_gate: Any) -> None:
        """F099 Codex Final F2 修复：绑定 ApprovalGate 实例到生产 ToolDeps。

        必须在 startup() 之前调用（或在 startup 之后直接设置 _tool_deps._approval_gate）。
        octo_harness.py 负责在 lifespan 中创建 ApprovalGate 并调用此方法。
        """
        self._approval_gate = approval_gate
        if self._tool_deps is not None:
            self._tool_deps._approval_gate = approval_gate

    @property
    def mcp_registry(self) -> McpRegistryService | None:
        return self._mcp_registry

    @property
    def approval_override_cache(self) -> ApprovalOverrideCacheProtocol:
        """Feature 061: 返回 ApprovalOverride 内存缓存实例。"""
        return self._approval_override_cache

    async def startup(self) -> None:
        if self._bootstrapped:
            return
        # Feature 057: 首次启动时扫描 SKILL.md 文件系统
        self._skill_discovery.scan()
        # Feature 070: 权限检查已内联到 ToolBroker.execute()，不再注册权限 Hook
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

        # F086 T2：删 _resolve_tool_entrypoints thin proxy（F084 D1 修复后留下的）
        # 直接从 ToolRegistry 一次性取 name → entrypoints map，避免 O(N²) 查找
        from octoagent.gateway.harness.tool_registry import get_registry as _get_registry
        _registry_entries = _get_registry()._snapshot_entries()
        _entrypoints_map: dict[str, list[str]] = {
            e.name: sorted(e.entrypoints) for e in _registry_entries
        }

        def _resolve_entrypoints_for(tool_name: str) -> list[str]:
            """ToolRegistry 命中 → 用 ToolEntry.entrypoints；未命中（如 mcp.* 动态工具）走降级。"""
            if tool_name in _entrypoints_map:
                return _entrypoints_map[tool_name]
            if tool_name.startswith("mcp."):
                return ["agent_runtime", "web"]
            return ["agent_runtime"]

        tools = [
            BundledToolDefinition(
                tool_name=meta.name,
                label=meta.name.replace(".", " ").title(),
                description=meta.description,
                tool_group=meta.tool_group,
                tool_profile="standard",
                tags=list(meta.tags),
                manifest_ref=meta.manifest_ref,
                availability=self._resolve_tool_availability(meta.name),
                availability_reason=self._resolve_tool_availability_reason(meta.name),
                install_hint=self._resolve_tool_install_hint(meta.name),
                entrypoints=_resolve_entrypoints_for(meta.name),
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
        profile_id: str = "",
    ) -> BundledCapabilityPack:
        await self.startup()
        if self._pack is None:
            self._pack = await self.refresh()
        if not project_id and not profile_id:
            return self._pack
        return await self._filter_pack_for_scope(
            self._pack,
            project_id=project_id,
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
            # F117 Wave 2bc：单读统一 agent_profiles 行。worker 镜像（kind=worker /
            # metadata source_kind）走 worker 分支映射 9 字段（Wave 0 吸收 + Wave 1 populate
            # 后统一行携带）；非 worker（main/subagent）走 builtin fallback 分支。两分支字段
            # 映射与 baseline 逐字段等价；source_kind 字面（'worker_profile'/'agent_profile'）
            # 保留为下游 binding 身份判据。
            agent_profile = await self._stores.agent_context_store.get_agent_profile(
                normalized_profile_id
            )
            if (
                agent_profile is not None
                and is_worker_behavior_profile(agent_profile)
                and agent_profile.status != AgentProfileStatus.ARCHIVED
            ):
                builtin_profile = self.get_worker_profile("general")
                return _ResolvedWorkerBinding(
                    profile_id=agent_profile.profile_id,
                    profile_revision=(
                        agent_profile.active_revision or agent_profile.draft_revision or 1
                    ),
                    worker_type="general",
                    model_alias=agent_profile.model_alias or "main",
                    tool_profile=agent_profile.tool_profile or builtin_profile.default_tool_profile,
                    default_tool_groups=list(
                        agent_profile.default_tool_groups or builtin_profile.default_tool_groups
                    ),
                    selected_tools=list(agent_profile.selected_tools),
                    source_kind="worker_profile",
                    profile_name=agent_profile.name,
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
        # F117 Wave 2bc：读统一行。not-worker guard 必需——baseline get_worker_profile
        # 只返回 worker 行，故非 worker 的 agent_profile 必须排除，保持"仅 worker 返回 type"契约。
        agent_profile = await self._stores.agent_context_store.get_agent_profile(normalized)
        if (
            agent_profile is None
            or not is_worker_behavior_profile(agent_profile)
            or agent_profile.status == AgentProfileStatus.ARCHIVED
        ):
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
            profile_id=binding.profile_id,
        )
        effective_tool_profile = request.tool_profile or binding.tool_profile
        context_profile = self._coerce_tool_profile(effective_tool_profile)
        tool_by_name = {tool.tool_name: tool for tool in pack.tools}
        desired_tools = self._dedupe_preserve_order(
            [
                *binding.selected_tools,
                *self._profile_first_candidate_tool_names(),
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
        deferred_entries: list[DeferredToolEntry] = []
        warnings: list[str] = []

        # Feature 072: 用 CoreToolSet 区分 mount/defer
        # Promoted 工具（tool_search 提升的）在后续 _get_tool_schemas 层面注入，
        # 此处仅基于 CoreToolSet 做静态分流。
        core_set = CoreToolSet.default()

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
            if not _profile_allows(tool_profile, context_profile):
                blocked_tools.append(
                    ToolAvailabilityExplanation(
                        tool_name=tool_name,
                        status="blocked",
                        source_kind=source_kind,
                        tool_group=bundled.tool_group,
                        tool_profile=bundled.tool_profile,
                        reason_code="tool_profile_not_allowed",
                        summary=(
                            f"当前 Root Agent 允许的 tool_profile={context_profile}，"
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

            # Feature 072: Core 工具 mount 完整 schema，其余 defer
            if core_set.is_core(tool_name):
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
                        },
                    )
                )
            elif (
                bundled.tool_group == "mcp"
                and str(bundled.metadata.get("source", "")).strip() == "mcp"
            ):
                # Feature 077/079: 动态注册的 MCP 工具（mcp_registry 写入
                # metadata.source="mcp"）由 litellm_client 的 is_runtime_exempt
                # 豁免直接以完整 schema 注入 LLM tools 参数，不再进 deferred 清单。
                # 避免 system prompt 的 "tool_search 激活" 提示与 schema 层可直接
                # 调用的信号冲突，LLM 收到矛盾信号会退化成反复调具体 MCP 工具
                # 做"验证"并触发工具交替循环熔断。
                # builtin 管理工具（mcp.servers.list / mcp.install 等）没有
                # source 标记，仍按下面的 else 走 deferred 路径。
                continue
            else:
                # Deferred: 只保留名称和描述
                deferred_entries.append(
                    DeferredToolEntry(
                        name=tool_name,
                        one_line_desc=(bundled.description or bundled.label or tool_name)[:80],
                        tool_group=bundled.tool_group,
                    )
                )

        if not mounted_names:
            warnings.append("profile_first_empty_fallback_to_static_toolset")
            for tool_name in self._resolve_fallback_toolset_from_pack(pack, binding.worker_type):
                bundled = tool_by_name.get(tool_name)
                if bundled is None:
                    continue
                if not _profile_allows(self._coerce_tool_profile(bundled.tool_profile), context_profile):
                    continue
                if bundled.availability not in {
                    BuiltinToolAvailabilityStatus.AVAILABLE,
                    BuiltinToolAvailabilityStatus.DEGRADED,
                }:
                    continue
                # Feature 072: fallback 也按 Core/Deferred 分流
                if core_set.is_core(tool_name):
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
                            },
                        )
                    )
                elif (
                    bundled.tool_group == "mcp"
                    and str(bundled.metadata.get("source", "")).strip() == "mcp"
                ):
                    # 同上：动态 MCP 工具走 is_runtime_exempt 豁免，不进 deferred。
                    continue
                else:
                    deferred_entries.append(
                        DeferredToolEntry(
                            name=tool_name,
                            one_line_desc=(bundled.description or bundled.label or tool_name)[:80],
                            tool_group=bundled.tool_group,
                        )
                    )

        discovery_request = request.model_copy(
            update={
                "limit": max(6, min(12, request.limit)),
                "worker_type": binding.worker_type,
                "tool_profile": context_profile,
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
                tool_profile=context_profile,
                resolution_mode="profile_first_core",
                selected_tools=mounted_names,
                recommended_tools=recommended_tools,
                discovery_entrypoints=discovery_entrypoints,
                warnings=self._dedupe_preserve_order(warnings),
            ),
            mounted_tools=mounted_tools,
            blocked_tools=blocked_tools,
            deferred_tool_entries=[e.model_dump() for e in deferred_entries],
        )

    async def render_bootstrap_context(
        self,
        *,
        worker_type: str = "general",
        project_id: str = "",
        surface: str = "",
    ) -> list[dict[str, Any]]:
        await self.startup()
        project, workspace = await self._resolve_project_context(
            project_id=project_id,
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
            "{{project_name}}": project.name if project is not None else "OctoAgent",
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

    def build_skill_registry_document(self) -> list[BundledSkillDefinition]:
        if self._pack is None:
            return []
        return list(self._pack.skills)

    async def _register_builtin_tools(self) -> None:
        """注册所有内置工具 — 委派给 builtin_tools 子包。"""
        from .builtin_tools import ToolDeps, register_all

        deps = ToolDeps(
            project_root=self._project_root,
            stores=self._stores,
            tool_broker=self._tool_broker,
            tool_index=self._tool_index,
            skill_discovery=self._skill_discovery,
            memory_console_service=self._memory_console_service,
            memory_runtime_service=self._memory_runtime_service,
            browser_sessions=self._browser_sessions,
            _task_runner=self._task_runner,
            _delegation_plane=self._delegation_plane,
            _mcp_registry=self._mcp_registry,
            _mcp_installer=self._mcp_installer,
            _pack_service=self,
            # F099 Codex Final F2 修复：注入 ApprovalGate 到 ToolDeps
            # 通过 bind_approval_gate() 在 startup() 之前设置 self._approval_gate
            _approval_gate=self._approval_gate,
        )
        self._tool_deps = deps
        await register_all(self._tool_broker, deps)

        # tool_search 注册（保留在此处，因为它有独立的工厂函数）
        event_store = getattr(self._stores, "event_store", None)
        tool_search_handler = create_tool_search_handler(
            tool_index=self._tool_index,
            event_store=event_store,
        )
        tool_search_meta = reflect_tool_schema(tool_search_handler)
        await self._tool_broker.try_register(tool_search_meta, tool_search_handler)

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
            "orchestration",  # F088 followup: graph_pipeline 等编排型工具
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
                    "Current Datetime Local: {{current_datetime_local}}\n"
                    "Current Weekday Local: {{current_weekday_local}}\n"
                    "Owner Timezone: {{owner_timezone}} (UTC {{owner_utc_offset}})\n"
                    "Owner Locale: {{owner_locale}}\n"
                    "Surface: {{surface}}\n"
                    "Worker Type: {{worker_type}}\n"
                    "Capabilities: {{worker_capabilities}}\n"
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
    def _coerce_tool_profile(value: str) -> str:
        normalized = value.strip().lower()
        if normalized in {"minimal", "standard", "privileged"}:
            return normalized
        return "standard"

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
    def _profile_first_candidate_tool_names() -> list[str]:
        return [
            "project.inspect",
            "task.inspect",
            "artifact.list",
            "sessions.list",
            "session.status",
            "work.plan",
            "subagents.spawn",
            "subagents.list",
            "subagents.steer",
            "mcp.tools.list",
            # F088 followup: graph_pipeline 走 profile-first 候选，绕开
            # stored_profile.default_tool_groups 过滤 —— 升级前已存的 profile
            # 不会含 "orchestration" 工具组，但仍能挂载 graph_pipeline。
            "graph_pipeline",
        ]

    @staticmethod
    def _profile_first_discovery_tool_names() -> set[str]:
        return {
            "work.plan",
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
        if tool_name in cls._profile_first_candidate_tool_names():
            return "profile_first_core"
        return "default_tool_group"

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
        extra_control_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        
        if self._task_runner is None:
            raise RuntimeError("task runner is not bound for child task launch")
        # F098 Phase C: Worker→Worker A2A 解禁（H2 完整对等性）。
        # F084 引入的 enforce_child_target_kind_policy（Worker→Worker 硬禁止）已删除。
        # 解禁后 Worker A 调用 delegate_task(target_kind=worker) 不再 raise，走 plane.spawn_child
        # 创建 child Worker（已是 baseline 路径）。死循环防护由 DelegationManager max_depth=2 兜底。
        child_id = str(ULID())
        child_thread_id = f"{parent_task.thread_id}:child:{child_id[:8]}"

        # F097 Phase B-1 + Phase D 联合修复（Codex Phase B P1-2 + Phase D P1-1 闭环）：
        # 之前两版实施都把 __subagent_delegation_init__ / __caller_runtime_hints__ 写在
        # await launch_child_task() 之后，是 race（production runner 已 normalize + enqueue
        # 完成，post-hoc 修改 control_metadata 不可见）。修复：在 child_message 构造**之前**
        # 计算所有 raw fields，一次性放入 control_metadata，确保 launch_child_task 看到完整数据。
        base_control_metadata: dict[str, Any] = {
            "parent_task_id": parent_task.task_id,
            "parent_work_id": parent_work.work_id,
            "requested_worker_type": worker_type,
            "target_kind": target_kind,
            "tool_profile": tool_profile,
            "spawned_by": spawned_by,
            "child_title": title,
            "worker_plan_id": plan_id,
        }

        # F097 Phase B-1: SubagentDelegation raw fields（仅 target_kind=subagent）
        # P2-6 闭环：caller_agent_runtime_id 无值时保持空字符串（不 fallback 到 task_id）
        if str(target_kind).strip().lower() == DelegationTargetKind.SUBAGENT.value:
            caller_agent_runtime_id = ""
            try:
                exec_ctx = get_current_execution_context()
                caller_agent_runtime_id = exec_ctx.agent_runtime_id or ""
            except RuntimeError:
                pass
            base_control_metadata["__subagent_delegation_init__"] = {
                "delegation_id": str(ULID()),
                "parent_task_id": parent_task.task_id,
                "parent_work_id": parent_work.work_id,
                "caller_agent_runtime_id": caller_agent_runtime_id,
                "caller_project_id": parent_work.project_id or "",
                "spawned_by": spawned_by,
            }

            # F097 Phase D: caller RuntimeHintBundle 拷贝（Codex P1-2 接受现状归档）
            # AC-D1 完整解读：caller-side RuntimeHintBundle 由 orchestrator
            # _build_request_runtime_hints 每 turn 重建，spawn 时不持有完整 caller 实例。
            # F097 Phase D 范围：surface 字段从 exec_ctx.runtime_context 真拷贝
            # （唯一可获取的 caller 字段）；其他字段为默认占位，child runtime 通过自己的
            # _build_request_runtime_hints 重新构造（与 main / worker 路径一致）。
            try:
                caller_surface = ""
                try:
                    _exec_ctx = get_current_execution_context()
                    if _exec_ctx.runtime_context is not None:
                        caller_surface = _exec_ctx.runtime_context.surface or ""
                except RuntimeError:
                    pass
                if not caller_surface:
                    caller_surface = str(
                        getattr(getattr(parent_task, "requester", None), "channel", "")
                        or ""
                    )
                base_control_metadata["__caller_runtime_hints__"] = {
                    "surface": caller_surface,
                    "can_delegate_research": False,
                    "recent_clarification_category": "",
                    "recent_clarification_source_text": "",
                    "recent_worker_lane_worker_type": "",
                    "recent_worker_lane_profile_id": "",
                    "recent_worker_lane_topic": "",
                    "recent_worker_lane_summary": "",
                    "tool_universe": None,
                }
            except Exception as _hint_exc:
                _log.warning(
                    "phase_d_hint_copy_failed",
                    error=str(_hint_exc),
                    target_kind=target_kind,
                    reason="spawn 主流程不受影响",
                )

        # F099 Phase C: 合并调用方注入的额外 control_metadata（低优先级：不覆盖基础字段）
        # extra_control_metadata 由 delegate_task_tool / delegation_tools 在 worker 环境下注入
        # source_runtime_kind=worker，保证 _resolve_a2a_source_role 能正确派生 source（FR-C2/C3）
        # 合并策略：先设 base，再更新 extra；extra 不覆盖 base 的核心字段
        # （由 delegate_task_tool 注入条件控制：非 worker 环境不注入 → 不影响主 Agent 路径）
        if extra_control_metadata:
            # extra 仅注入"注释性"字段（source_runtime_kind / source_worker_capability 等）
            # 不允许覆盖核心路由字段（parent_task_id / target_kind / requested_worker_type 等）
            for key, val in extra_control_metadata.items():
                if key not in base_control_metadata:
                    base_control_metadata[key] = val

        child_message = NormalizedMessage(
            channel=parent_task.requester.channel,
            thread_id=child_thread_id,
            scope_id=parent_task.scope_id,
            sender_id=parent_task.requester.sender_id,
            sender_name=parent_task.requester.sender_id or "owner",
            text=objective,
            control_metadata=base_control_metadata,
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
    ) -> tuple[set[str], set[str]]:
        if not project_id:
            return set(), set()
        project, _workspace = await self._resolve_project_context(
            project_id=project_id,
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

        # F117 Wave 2bc：统一行权威，删除 worker_profile metadata fallback（镜像已携 metadata）。
        metadata: dict[str, Any] = {}
        agent_profile = await self._stores.agent_context_store.get_agent_profile(
            normalized_profile_id
        )
        if agent_profile is not None and isinstance(agent_profile.metadata, dict):
            metadata = dict(agent_profile.metadata)

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
            return normalized_profile == "minimal"
        return False

    async def _filter_pack_for_scope(
        self,
        pack: BundledCapabilityPack,
        *,
        project_id: str = "",
        profile_id: str = "",
    ) -> BundledCapabilityPack:
        (
            project_selected_item_ids,
            project_disabled_item_ids,
        ) = await self._resolve_scope_skill_selection(
            project_id=project_id,
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
    ):
        project = None
        if project_id:
            project = await self._stores.project_store.get_project(project_id)
        if project is None:
            selector = await self._stores.project_store.get_selector_state("web")
            if selector is not None:
                project = await self._stores.project_store.get_project(selector.active_project_id)
        if project is None:
            project = await self._stores.project_store.get_default_project()
        return project, None
