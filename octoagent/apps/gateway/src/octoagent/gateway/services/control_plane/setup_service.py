"""Phase 6: Setup / 配置治理领域服务。

从 control_plane.py 提取的 setup review / apply / quick_connect、
project select、config apply、skills selection save，
以及 get_config_schema / get_project_selector / get_setup_governance_document /
get_skill_governance_document / get_diagnostics_summary / get_capability_pack_document
等 resource producer 和辅助方法。
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any

import structlog
from octoagent.core.models import (
    ActionRequestEnvelope,
    ActionResultEnvelope,
    CapabilityPackDocument,
    ConfigSchemaDocument,
    ControlPlaneCapability,
    ControlPlaneDegradedState,
    ControlPlaneResourceRef,
    ControlPlaneSurface,
    ControlPlaneTargetRef,
    DiagnosticsFailureSummary,
    DiagnosticsSubsystemStatus,
    DiagnosticsSummaryDocument,
    ProjectOption,
    ProjectSelectorDocument,
    SetupGovernanceDocument,
    SetupGovernanceSection,
    SetupReviewSummary,
    SkillGovernanceDocument,
    SkillGovernanceItem,
    UpdateTriggerSource,
)
from octoagent.provider.dx.backup_service import BackupService
from octoagent.gateway.services.config.config_schema import OctoAgentConfig
from octoagent.gateway.services.config.config_wizard import load_config, save_config
import octoagent.gateway.services.control_plane as _cp_pkg  # 通过 package 引用以支持 monkeypatch
from pydantic import ValidationError

from ._base import ControlPlaneActionError, ControlPlaneContext, DomainServiceBase
from .setup_config_io import SetupConfigIOMixin
from .setup_review import SetupReviewMixin
from .setup_skill_selection import SetupSkillSelectionMixin

log = structlog.get_logger()


class SetupDomainService(
    SetupReviewMixin,
    SetupConfigIOMixin,
    SetupSkillSelectionMixin,
    DomainServiceBase,
):
    """Setup governance、config schema、project selector、skill governance、
    diagnostics 等 resource producer 以及对应的 action handler。
    """

    def __init__(
        self,
        ctx: ControlPlaneContext,
        *,
        proxy_manager: Any | None = None,
        telegram_state_store: Any | None = None,
        update_status_store: Any | None = None,
    ) -> None:
        super().__init__(ctx)
        self._proxy_manager = proxy_manager
        self._telegram_state_store = telegram_state_store
        self._update_status_store = update_status_store

    def bind_proxy_manager(self, proxy_manager: Any | None) -> None:
        """startup 期延迟资源绑定（F108b W7：替代 coordinator 直捅私有属性）。"""
        self._proxy_manager = proxy_manager

    # ══════════════════════════════════════════════════════════════
    #  Action / Document Routes
    # ══════════════════════════════════════════════════════════════

    def action_routes(self) -> dict[str, Any]:
        return {
            "project.select": self._handle_project_select,
            "setup.review": self._handle_setup_review,
            "setup.apply": self._handle_setup_apply,
            # Feature 079 Phase 2：原子化 OAuth + setup.apply，闭合"授权但没入 config"
            # 的 UX 断层。旧 provider.oauth.* 继续可用（CLI 路径 + 向后兼容）。
            "setup.oauth_and_apply": self._handle_setup_oauth_and_apply,
            "setup.quick_connect": self._handle_setup_quick_connect,
            "skills.selection.save": self._handle_skills_selection_save,
            "config.apply": self._handle_config_apply,
        }

    def document_routes(self) -> dict[str, Any]:
        return {
            "config_schema": self.get_config_schema,
            "project_selector": self.get_project_selector,
            "setup_governance": self.get_setup_governance_document,
            "skill_governance": self.get_skill_governance_document,
            "capability_pack": self.get_capability_pack_document,
            "diagnostics_summary": self.get_diagnostics_summary,
        }

    # ══════════════════════════════════════════════════════════════
    #  Resource Producers
    # ══════════════════════════════════════════════════════════════

    async def get_config_schema(self) -> ConfigSchemaDocument:
        config = load_config(self._ctx.project_root)
        if config is None:
            config = OctoAgentConfig(updated_at=date.today().isoformat())
        schema = OctoAgentConfig.model_json_schema()
        ui_hints = self._build_config_ui_hints()
        # Feature 081 P4：移除 litellm_config sync 检查（无 litellm-config.yaml 需要同步）
        bridge_refs = await self._collect_bridge_refs()
        return ConfigSchemaDocument(
            schema=schema,
            ui_hints=ui_hints,
            current_value=config.model_dump(mode="json"),
            validation_rules=[
                "Provider ID 必须唯一",
                "model_aliases.provider 必须引用已存在 provider",
                "secret 实值不得写入 YAML",
            ],
            bridge_refs=bridge_refs,
            warnings=[],
            degraded=ControlPlaneDegradedState(
                is_degraded=False,
                reasons=[],
            ),
            capabilities=[
                ControlPlaneCapability(
                    capability_id="config.apply",
                    label="保存配置",
                    action_id="config.apply",
                )
            ],
        )

    async def get_project_selector(self) -> ProjectSelectorDocument:
        (
            state,
            selected_project,
            _,
            fallback_reason,
        ) = await self._resolve_selection()
        projects = await self._stores.project_store.list_projects()
        available_projects: list[ProjectOption] = []
        for project in projects:
            available_projects.append(
                ProjectOption(
                    project_id=project.project_id,
                    slug=project.slug,
                    name=project.name,
                    is_default=project.is_default,
                    status=project.status.value,
                    workspace_ids=[],
                )
            )

        switch_allowed = len(available_projects) > 1
        warnings: list[str] = []
        if fallback_reason:
            warnings.append(fallback_reason)
        return ProjectSelectorDocument(
            current_project_id=selected_project.project_id if selected_project else "",
            current_workspace_id="",
            default_project_id=(await self._stores.project_store.get_default_project()).project_id
            if await self._stores.project_store.get_default_project()
            else "",
            fallback_reason=fallback_reason,
            switch_allowed=switch_allowed,
            available_projects=available_projects,
            available_workspaces=[],
            warnings=warnings,
            degraded=ControlPlaneDegradedState(
                is_degraded=selected_project is None,
                reasons=["project_unavailable"] if selected_project is None else [],
            ),
            capabilities=[
                ControlPlaneCapability(
                    capability_id="project.select",
                    label="切换项目",
                    action_id="project.select",
                    enabled=switch_allowed,
                    support_status=(
                        "supported"
                        if switch_allowed
                        else "degraded"
                    ),
                    reason="" if switch_allowed else "当前只有 default project",
                )
            ],
        )

    async def get_skill_governance_document(
        self,
        *,
        config_value: dict[str, Any] | None = None,
        policy_profile_id: str | None = None,
        selected_project: Any | None = None,
        draft_selection: Mapping[str, Any] | None = None,
    ) -> SkillGovernanceDocument:
        if selected_project is None:
            _, selected_project, _, _ = await self._resolve_selection()
        if self._ctx.capability_pack_service is None:
            capability_pack = CapabilityPackDocument(
                selected_project_id=(
                    selected_project.project_id if selected_project is not None else ""
                ),
                selected_workspace_id="",
            )
        else:
            capability_pack = CapabilityPackDocument(
                pack=await self._ctx.capability_pack_service.get_pack(),
                selected_project_id=(
                    selected_project.project_id if selected_project is not None else ""
                ),
                selected_workspace_id="",
            )
        capability_snapshot = (
            self._ctx.capability_pack_service.capability_snapshot()
            if self._ctx.capability_pack_service is not None
            else {}
        )
        items: list[SkillGovernanceItem] = []
        # Feature 057: 从 SkillDiscovery 获取 Skill 列表
        if self._ctx.capability_pack_service is not None:
            for entry in self._ctx.capability_pack_service.skill_discovery.list_items():
                items.append(
                    SkillGovernanceItem(
                        item_id=f"skill:{entry.name}",
                        label=entry.name.replace("-", " ").title(),
                        source_kind=entry.source.value if hasattr(entry, "source") else "builtin",
                        scope="project",
                        enabled_by_default=True,
                        selected=True,
                        availability="available",
                        trust_level="trusted",
                        details={
                            "skill_id": entry.name,
                            "description": entry.description,
                            "tags": list(entry.tags),
                            "version": entry.version,
                        },
                    )
                )

        mcp_tools: dict[str, list[Any]] = defaultdict(list)
        mcp_configs: dict[str, Any] = {}
        mcp_registry = (
            None
            if self._ctx.capability_pack_service is None
            else self._ctx.capability_pack_service.mcp_registry
        )
        if mcp_registry is not None:
            mcp_configs = {item.name: item for item in mcp_registry.list_configs()}
        for tool in capability_pack.pack.tools:
            if tool.tool_group != "mcp":
                continue
            server_name = str(tool.metadata.get("mcp_server_name", "")).strip() or "mcp"
            mcp_tools[server_name].append(tool)
        for server_name, tools in mcp_tools.items():
            availability = "available"
            missing_requirements: list[str] = []
            install_hints = [item.install_hint for item in tools if item.install_hint]
            if any(item.availability.value == "unavailable" for item in tools):
                availability = "unavailable"
                missing_requirements.append("存在不可用的 MCP tools。")
            elif any(item.availability.value != "available" for item in tools):
                availability = "degraded"
                missing_requirements.append("部分 MCP tools 当前处于降级状态。")
            config = mcp_configs.get(server_name)
            mount_policy = (
                str(config.mount_policy).strip().lower()
                if config is not None
                else "auto_readonly"
            )
            items.append(
                SkillGovernanceItem(
                    item_id=f"mcp:{server_name}",
                    label=f"MCP / {server_name}",
                    source_kind="mcp",
                    scope="project",
                    enabled_by_default=mount_policy in {"auto_readonly", "auto_all"},
                    selected=False,
                    availability=availability,
                    trust_level="external",
                    missing_requirements=missing_requirements,
                    install_hint=install_hints[0] if install_hints else "",
                    details={
                        "server_name": server_name,
                        "mount_policy": mount_policy,
                        "tool_count": len(tools),
                        "tools": [item.tool_name for item in tools],
                    },
                )
            )
        if capability_snapshot.get("mcp") and not mcp_tools:
            mcp_summary = capability_snapshot["mcp"]
            items.append(
                SkillGovernanceItem(
                    item_id="mcp:registry",
                    label="MCP Registry",
                    source_kind="mcp",
                    scope="project",
                    enabled_by_default=False,
                    selected=False,
                    availability=(
                        "degraded" if mcp_summary.get("configured_server_count", 0) else "disabled"
                    ),
                    trust_level="external",
                    missing_requirements=[str(mcp_summary.get("config_error", "")).strip()]
                    if mcp_summary.get("config_error")
                    else [],
                    details=dict(mcp_summary),
                )
            )
        items = self._apply_skill_selection_to_items(
            items=items,
            selected_project=selected_project,
            draft_selection=draft_selection,
        )
        blocked_items = len([item for item in items if item.selected and item.blocking])
        selected_items = len([item for item in items if item.selected])
        warnings = [] if items else ["当前没有可治理的 skills / MCP readiness 条目。"]
        return SkillGovernanceDocument(
            active_project_id=selected_project.project_id if selected_project is not None else "",
            active_workspace_id="",
            items=items,
            summary={
                "item_count": len(items),
                "selected_count": selected_items,
                "disabled_count": len(items) - selected_items,
                "blocked_count": blocked_items,
                "builtin_skill_count": len(
                    [item for item in items if item.source_kind == "builtin"]
                ),
                "mcp_item_count": len([item for item in items if item.source_kind == "mcp"]),
            },
            warnings=warnings,
            degraded=ControlPlaneDegradedState(
                is_degraded=bool(blocked_items),
                reasons=["skills_blocked"] if blocked_items else [],
            ),
        )

    async def get_setup_governance_document(self) -> SetupGovernanceDocument:
        _, selected_project, _, fallback_reason = await self._resolve_selection()
        project_selector = await self.get_project_selector()
        config = await self.get_config_schema()
        diagnostics = await self.get_diagnostics_summary()
        agent_profiles = await self._agent_domain.get_agent_profiles_document()
        owner_profile = await self._agent_domain.get_owner_profile_document()
        capability_pack = await self.get_capability_pack_document()
        policy_profiles = await self._agent_domain.get_policy_profiles_document()
        skill_governance = await self.get_skill_governance_document()
        secret_audit = await self._safe_secret_audit(
            selected_project.project_id if selected_project else None
        )
        active_agent_profile = self._resolve_active_agent_profile_payload(
            agent_profiles=agent_profiles,
            selected_project=selected_project,
        )
        review = self._build_setup_review_summary(
            config=config.current_value,
            config_warnings=config.warnings,
            selected_project=selected_project,
            diagnostics=diagnostics,
            active_agent_profile=active_agent_profile,
            policy_profile_id=policy_profiles.active_profile_id,
            skill_governance=skill_governance,
            secret_audit=secret_audit,
            validation_errors=[],
        )
        project_scope = SetupGovernanceSection(
            section_id="project_scope",
            label="Project Scope",
            status="ready" if selected_project is not None else "blocked",
            summary=(
                selected_project.name
                if selected_project is not None
                else "当前还没有可用 project。"
            ),
            warnings=[fallback_reason] if fallback_reason else [],
            blocking_reasons=["project_unavailable"] if selected_project is None else [],
            details={
                "project_id": selected_project.project_id if selected_project is not None else "",
                "project_name": selected_project.name if selected_project is not None else "",
                "workspace_id": "",
                "workspace_name": "",
                "fallback_reason": fallback_reason,
                "default_project_id": project_selector.default_project_id,
            },
            source_refs=[self._resource_ref("project_selector", "project:selector")],
        )
        provider_runtime = SetupGovernanceSection(
            section_id="provider_runtime",
            label="Provider Runtime",
            status="blocked"
            if review.provider_runtime_risks
            and any(item.blocking for item in review.provider_runtime_risks)
            else ("action_required" if review.provider_runtime_risks else "ready"),
            summary=(
                f"已启用 {len(config.current_value.get('providers', []))} 个 provider，"
                f"凭证={len(self._credential_store().list_profiles())}"
            ),
            warnings=list(config.warnings),
            blocking_reasons=[
                item.risk_id for item in review.provider_runtime_risks if item.blocking
            ],
            details=self._collect_provider_runtime_details(
                config.current_value,
                secret_audit=secret_audit,
                bridge_refs=config.bridge_refs,
                litellm_sync_ok=not config.degraded.is_degraded,
            ),
            source_refs=[
                self._resource_ref("config_schema", "config:octoagent"),
                self._resource_ref("diagnostics_summary", "diagnostics:runtime"),
            ],
        )
        channel_summary = diagnostics.channel_summary.get("telegram", {})
        channel_access = SetupGovernanceSection(
            section_id="channel_access",
            label="Channel Access",
            status="blocked"
            if review.channel_exposure_risks
            and any(item.blocking for item in review.channel_exposure_risks)
            else ("action_required" if review.channel_exposure_risks else "ready"),
            summary=(
                f"front_door={config.current_value.get('front_door', {}).get('mode', 'loopback')}，"
                f"telegram={'enabled' if channel_summary.get('enabled') else 'disabled'}"
            ),
            warnings=[item.summary for item in review.channel_exposure_risks if not item.blocking],
            blocking_reasons=[
                item.risk_id for item in review.channel_exposure_risks if item.blocking
            ],
            details={
                "front_door": dict(config.current_value.get("front_door", {})),
                "telegram": dict(channel_summary),
            },
            source_refs=[
                self._resource_ref("config_schema", "config:octoagent"),
                self._resource_ref("diagnostics_summary", "diagnostics:runtime"),
            ],
        )
        agent_governance = SetupGovernanceSection(
            section_id="agent_governance",
            label="Agent Governance",
            status="blocked"
            if review.agent_autonomy_risks
            and any(item.blocking for item in review.agent_autonomy_risks)
            else ("action_required" if review.agent_autonomy_risks else "ready"),
            summary=(
                f"主 Agent={active_agent_profile.get('name', '未配置')}，"
                f"安全等级={policy_profiles.active_profile_id or 'default'}"
            ),
            warnings=[item.summary for item in review.agent_autonomy_risks if not item.blocking],
            blocking_reasons=[
                item.risk_id for item in review.agent_autonomy_risks if item.blocking
            ],
            details={
                "active_agent_profile": active_agent_profile,
                "owner_profile_id": str(
                    owner_profile.profile.get("owner_profile_id", "")
                    if isinstance(owner_profile.profile, dict)
                    else ""
                ),
                "owner_overlay_count": len(owner_profile.overlays),
                "policy_profile_id": policy_profiles.active_profile_id,
            },
            source_refs=[
                self._resource_ref("agent_profiles", "agent-profiles:overview"),
                self._resource_ref("owner_profile", "owner-profile:default"),
                self._resource_ref("policy_profiles", "policy:profiles"),
            ],
        )
        tools_skills = SetupGovernanceSection(
            section_id="tools_skills",
            label="Tools & Skills",
            status="blocked"
            if review.tool_skill_readiness_risks
            and any(item.blocking for item in review.tool_skill_readiness_risks)
            else ("action_required" if review.tool_skill_readiness_risks else "ready"),
            summary=(
                f"tools={len(capability_pack.pack.tools)}，"
                f"skills={skill_governance.summary.get('builtin_skill_count', 0)}，"
                f"mcp={skill_governance.summary.get('mcp_item_count', 0)}"
            ),
            warnings=[
                item.summary for item in review.tool_skill_readiness_risks if not item.blocking
            ],
            blocking_reasons=[
                item.risk_id for item in review.tool_skill_readiness_risks if item.blocking
            ],
            details={
                "capability_summary": (
                    self._ctx.capability_pack_service.capability_snapshot()
                    if self._ctx.capability_pack_service is not None
                    else {}
                ),
                "skill_summary": dict(skill_governance.summary),
            },
            source_refs=[
                self._resource_ref("capability_pack", "capability:bundled"),
                self._resource_ref("skill_governance", "skills:governance"),
            ],
        )
        warnings = list(review.warnings)
        if not warnings and not review.ready:
            warnings.append("当前 setup 仍有待完成项。")
        return SetupGovernanceDocument(
            active_project_id=selected_project.project_id if selected_project is not None else "",
            active_workspace_id="",
            project_scope=project_scope,
            provider_runtime=provider_runtime,
            channel_access=channel_access,
            agent_governance=agent_governance,
            tools_skills=tools_skills,
            review=review,
            warnings=warnings,
            degraded=ControlPlaneDegradedState(
                is_degraded=not review.ready,
                reasons=list(review.blocking_reasons),
                unavailable_sections=[
                    item.section_id
                    for item in (
                        project_scope,
                        provider_runtime,
                        channel_access,
                        agent_governance,
                        tools_skills,
                    )
                    if item.status == "blocked"
                ],
            ),
            capabilities=[
                ControlPlaneCapability(
                    capability_id="setup.review",
                    label="检查配置",
                    action_id="setup.review",
                ),
                ControlPlaneCapability(
                    capability_id="provider.oauth.openai_codex",
                    label="连接 OpenAI Auth",
                    action_id="provider.oauth.openai_codex",
                ),
                ControlPlaneCapability(
                    capability_id="agent_profile.save",
                    label="保存主 Agent",
                    action_id="agent_profile.save",
                ),
                ControlPlaneCapability(
                    capability_id="policy_profile.select",
                    label="切换安全等级",
                    action_id="policy_profile.select",
                ),
            ],
        )

    async def get_capability_pack_document(self) -> CapabilityPackDocument:
        # 能力包全局加载，不按项目过滤
        if self._ctx.capability_pack_service is None:
            return CapabilityPackDocument(
                selected_project_id="",
                selected_workspace_id="",
                degraded=ControlPlaneDegradedState(
                    is_degraded=True,
                    reasons=["capability_pack_unavailable"],
                ),
                warnings=["capability pack service unavailable"],
            )
        pack = await self._ctx.capability_pack_service.get_pack(
            project_id="",
        )
        return CapabilityPackDocument(
            pack=pack,
            selected_project_id="",
            selected_workspace_id="",
            capabilities=[
                ControlPlaneCapability(
                    capability_id="capability.refresh",
                    label="刷新能力包",
                    action_id="capability.refresh",
                )
            ],
            degraded=ControlPlaneDegradedState(
                is_degraded=bool(pack.degraded_reason),
                reasons=[pack.degraded_reason] if pack.degraded_reason else [],
            ),
        )

    async def get_diagnostics_summary(self) -> DiagnosticsSummaryDocument:
        subsystems: list[DiagnosticsSubsystemStatus] = []
        failures: list[DiagnosticsFailureSummary] = []
        runtime_snapshot = self._load_runtime_snapshot()
        update_summary = self._load_update_summary()
        recovery_summary = (
            BackupService(
                self._ctx.project_root,
                store_group=self._stores,
            )
            .get_recovery_summary()
            .model_dump(mode="json")
        )
        channel_summary = self._build_channel_summary()
        wizard = await self._get_wizard_session()
        _, selected_project, _, _ = await self._resolve_selection()
        project_selector = await self.get_project_selector()
        memory_backend = await self._ctx.memory_console_service.get_backend_status(
            project_id=selected_project.project_id if selected_project is not None else "",
        )

        subsystems.append(
            DiagnosticsSubsystemStatus(
                subsystem_id="runtime",
                label="Runtime",
                status="ok" if self._ctx.task_runner is not None else "unavailable",
                summary="TaskRunner / Execution runtime",
                detail_ref="/health",
            )
        )
        subsystems.append(
            DiagnosticsSubsystemStatus(
                subsystem_id="wizard",
                label="Wizard",
                status=wizard.status,
                summary=wizard.blocking_reason or "wizard session",
                detail_ref="/api/control/resources/wizard",
                warnings=wizard.warnings,
            )
        )
        subsystems.append(
            DiagnosticsSubsystemStatus(
                subsystem_id="projects",
                label="Projects",
                status="ok" if project_selector.current_project_id else "degraded",
                summary=project_selector.fallback_reason or "project selector",
                detail_ref="/api/control/resources/project-selector",
                warnings=project_selector.warnings,
            )
        )
        subsystems.append(
            DiagnosticsSubsystemStatus(
                subsystem_id="memory",
                label="Memory",
                status=memory_backend.state.value,
                summary=memory_backend.message
                or (
                    "active backend: "
                    f"{memory_backend.active_backend or memory_backend.backend_id}"
                    + (
                        f" ({memory_backend.project_binding})"
                        if memory_backend.project_binding
                        else ""
                    )
                ),
                detail_ref="/api/control/resources/memory",
                warnings=(
                    ([memory_backend.failure_code] if memory_backend.failure_code else [])
                    + (
                        [f"project_binding={memory_backend.project_binding}"]
                        if memory_backend.project_binding
                        else []
                    )
                    + (
                        [f"last_ingest_at={memory_backend.last_ingest_at.isoformat()}"]
                        if memory_backend.last_ingest_at is not None
                        else []
                    )
                    + (
                        [f"last_maintenance_at={memory_backend.last_maintenance_at.isoformat()}"]
                        if memory_backend.last_maintenance_at is not None
                        else []
                    )
                    + (
                        [f"retry_after={memory_backend.retry_after.isoformat()}"]
                        if memory_backend.retry_after is not None
                        else []
                    )
                ),
            )
        )
        update_status = str(update_summary.get("overall_status", "") or "")
        if update_status:
            subsystems.append(
                DiagnosticsSubsystemStatus(
                    subsystem_id="update",
                    label="Update",
                    status=update_status.lower(),
                    summary=update_status,
                    detail_ref="/api/ops/update/status",
                )
            )
        if not recovery_summary.get("ready_for_restore", False):
            failures.append(
                DiagnosticsFailureSummary(
                    source="recovery",
                    message="最近一次 recovery drill 尚未通过或未执行",
                )
            )
        if update_summary.get("failure_report"):
            failure_report = update_summary["failure_report"]
            failures.append(
                DiagnosticsFailureSummary(
                    source="update",
                    message=str(failure_report.get("message", "update failed")),
                )
            )
        if memory_backend.state.value in {"degraded", "unavailable", "recovering"}:
            failures.append(
                DiagnosticsFailureSummary(
                    source="memory",
                    message=memory_backend.message
                    or f"memory backend 状态为 {memory_backend.state.value}",
                )
            )
        overall_status = "ready" if not failures else "degraded"
        return DiagnosticsSummaryDocument(
            overall_status=overall_status,
            status=overall_status,
            degraded=ControlPlaneDegradedState(
                is_degraded=bool(failures),
                reasons=[item.source for item in failures],
            ),
            subsystems=subsystems,
            recent_failures=failures,
            runtime_snapshot=runtime_snapshot,
            recovery_summary=recovery_summary,
            update_summary=update_summary,
            channel_summary=channel_summary,
            deep_refs={
                "health": "/ready?profile=full",
                "events": "/api/control/events",
                "operator": "/api/operator/inbox",
                "memory": "/api/control/resources/memory",
            },
            capabilities=[
                ControlPlaneCapability(
                    capability_id="diagnostics.refresh",
                    label="刷新诊断",
                    action_id="diagnostics.refresh",
                )
            ],
        )

    # ══════════════════════════════════════════════════════════════
    #  Action Handlers
    # ══════════════════════════════════════════════════════════════

    async def _handle_project_select(
        self, request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        project_id = str(request.params.get("project_id", "")).strip()
        if not project_id:
            raise self._action_error("PROJECT_ID_REQUIRED", "project_id 不能为空")
        project = await self._stores.project_store.get_project(project_id)
        if project is None:
            raise self._action_error("PROJECT_NOT_FOUND", "目标 project 不存在")

        state = self._ctx.state_store.load().model_copy(
            update={
                "selected_project_id": project_id,
                "updated_at": datetime.now(tz=UTC),
            }
        )
        self._ctx.state_store.save(state)
        await self._sync_web_project_selector_state(
            project=project,
            source="control_plane_action",
        )
        await self._sync_policy_engine_for_project(project)
        await self._stores.conn.commit()
        return self._completed_result(
            request=request,
            code="PROJECT_SELECTED",
            message="已切换当前 project",
            data={
                "project_id": project_id,
                "workspace_id": "",
            },
            resource_refs=[self._resource_ref("project_selector", "project:selector")],
            target_refs=[
                ControlPlaneTargetRef(
                    target_type="project", target_id=project_id, label=project.name
                )
            ],
        )

    async def _handle_setup_review(
        self, request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        current_config = load_config(self._ctx.project_root)
        if current_config is None:
            current_config = OctoAgentConfig(updated_at=date.today().isoformat())
        draft = request.params.get("draft", {})
        config_patch = draft.get("config", {}) if isinstance(draft, dict) else {}
        config_data = current_config.model_dump(mode="python")
        candidate_config_payload: dict[str, Any] = config_data
        if isinstance(config_patch, dict):
            config_data = self._deep_merge_dicts(config_data, config_patch)
            candidate_config_payload = config_data
        validation_errors: list[str] = []
        try:
            candidate_config = OctoAgentConfig.model_validate(config_data)
            candidate_config_payload = candidate_config.model_dump(mode="json")
        except ValidationError as exc:
            candidate_config = current_config
            validation_errors.extend(self._format_config_validation_errors(exc))
        except Exception as exc:
            candidate_config = current_config
            validation_errors.append(str(exc))

        _, selected_project, _, _ = await self._resolve_selection()
        draft_skill_selection = (
            draft.get("skill_selection")
            if isinstance(draft.get("skill_selection"), Mapping)
            else None
        )
        normalized_skill_selection: dict[str, Any] | None = None
        if draft_skill_selection is not None:
            try:
                normalized_skill_selection = await self._normalize_skill_selection_for_scope(
                    draft_skill_selection,
                    selected_project=selected_project,
                )
            except Exception as exc:
                validation_errors.append(str(exc))
        agent_profiles = await self._agent_domain.get_agent_profiles_document()
        active_agent_profile = self._resolve_active_agent_profile_payload(
            agent_profiles=agent_profiles,
            selected_project=selected_project,
        )
        agent_profile_patch = draft.get("agent_profile", {}) if isinstance(draft, dict) else {}
        if isinstance(agent_profile_patch, dict) and agent_profile_patch:
            active_agent_profile = self._merge_agent_profile_payload(
                active_agent_profile,
                agent_profile_patch,
                selected_project=selected_project,
            )
        policy_profile_id = (
            str(draft.get("policy_profile_id", "")).strip() if isinstance(draft, dict) else ""
        )
        if not policy_profile_id:
            policy_profile_id, _ = self._resolve_effective_policy_profile(selected_project)
        skill_governance = await self.get_skill_governance_document(
            config_value=candidate_config_payload,
            policy_profile_id=policy_profile_id,
            selected_project=selected_project,
            draft_selection=normalized_skill_selection,
        )
        diagnostics = await self.get_diagnostics_summary()
        secret_audit = await self._safe_secret_audit(
            selected_project.project_id if selected_project else None
        )
        review = self._build_setup_review_summary(
            config=candidate_config_payload,
            config_warnings=[],
            selected_project=selected_project,
            diagnostics=diagnostics,
            active_agent_profile=active_agent_profile,
            policy_profile_id=policy_profile_id,
            skill_governance=skill_governance,
            secret_audit=secret_audit,
            validation_errors=validation_errors,
        )
        return self._completed_result(
            request=request,
            code="SETUP_REVIEW_READY",
            message="配置检查已完成。",
            data={"review": review.model_dump(mode="json")},
            resource_refs=[self._resource_ref("setup_governance", "setup:governance")],
        )

    async def _handle_setup_apply(
        self, request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        draft = request.params.get("draft", {})
        if draft is None:
            draft = {}
        if not isinstance(draft, dict):
            raise self._action_error("SETUP_DRAFT_REQUIRED", "draft 必须是对象")

        _, selected_project, _, _ = await self._resolve_selection()
        skill_selection = draft.get("skill_selection")
        normalized_skill_selection: dict[str, Any] | None = None
        if isinstance(skill_selection, Mapping):
            normalized_skill_selection = await self._normalize_skill_selection_for_scope(
                skill_selection,
                selected_project=selected_project,
            )

        review_result = await self._handle_setup_review(
            request.model_copy(update={"action_id": "setup.review"})
        )
        review = SetupReviewSummary.model_validate(review_result.data.get("review", {}))
        if not review.ready:
            # 如果 blocking 原因是 secret_missing 但 draft 中已包含对应密钥值，
            # 则不阻止保存——密钥即将在本次 apply 中写入。
            secret_values = draft.get("secret_values", {})
            pending_env_names = (
                {str(k).strip() for k, v in secret_values.items() if str(v).strip()}
                if isinstance(secret_values, Mapping)
                else set()
            )
            effective_blocking = [
                reason for reason in review.blocking_reasons
                if not (
                    reason.startswith("secret_missing:")
                    and pending_env_names
                )
            ]
            if effective_blocking:
                blocking = "、".join(effective_blocking) or "存在未通过项"
                raise self._action_error(
                    "SETUP_REVIEW_BLOCKED",
                    f"配置检查未通过，当前不能保存：{blocking}",
                )

        current_config = load_config(self._ctx.project_root)
        if current_config is None:
            current_config = OctoAgentConfig(updated_at=date.today().isoformat())
        config_patch = draft.get("config", {})
        config_data = current_config.model_dump(mode="python")
        if isinstance(config_patch, dict):
            config_data = self._deep_merge_dicts(config_data, config_patch)
        config_data.setdefault("updated_at", date.today().isoformat())
        config = OctoAgentConfig.model_validate(config_data)

        policy_profile_id = str(draft.get("policy_profile_id", "")).strip().lower()
        if policy_profile_id and self._policy_profile_by_id(policy_profile_id) is None:
            raise self._action_error("POLICY_PROFILE_INVALID", "不支持的 policy profile")

        agent_request_payload: dict[str, Any] | None = None
        agent_profile_patch = draft.get("agent_profile", {})
        if isinstance(agent_profile_patch, dict) and agent_profile_patch:
            agent_profiles = await self._agent_domain.get_agent_profiles_document()
            active_agent_profile = self._resolve_active_agent_profile_payload(
                agent_profiles=agent_profiles,
                selected_project=selected_project,
            )
            merged_agent_profile = self._merge_agent_profile_payload(
                active_agent_profile,
                agent_profile_patch,
                selected_project=selected_project,
            )
            merged_scope = str(merged_agent_profile.get("scope", "")).strip().lower()
            if not merged_scope:
                merged_scope = "project" if selected_project is not None else "system"
                merged_agent_profile["scope"] = merged_scope
            if merged_scope not in {"system", "project"}:
                raise self._action_error(
                    "AGENT_PROFILE_SCOPE_INVALID", "scope 必须是 system/project"
                )
            if merged_scope == "project" and selected_project is not None:
                merged_agent_profile.setdefault("project_id", selected_project.project_id)
            if not str(merged_agent_profile.get("name", "")).strip():
                raise self._action_error("AGENT_PROFILE_NAME_REQUIRED", "name 不能为空")
            agent_request_payload = merged_agent_profile

        save_config(config, self._ctx.project_root)
        # Feature 081 P4：不再生成 litellm-config.yaml（Provider 直连）

        resource_refs = [
            self._resource_ref("config_schema", "config:octoagent"),
            self._resource_ref("diagnostics_summary", "diagnostics:runtime"),
            self._resource_ref("setup_governance", "setup:governance"),
            self._resource_ref("skill_governance", "skills:governance"),
        ]
        data: dict[str, Any] = {
            "review": review.model_dump(mode="json"),
        }
        secret_values = draft.get("secret_values", {})
        if isinstance(secret_values, Mapping):
            secret_result = self._save_runtime_secret_values(
                config=config,
                secret_values=secret_values,
            )
            if (
                secret_result["litellm_env_names"]
                or secret_result["runtime_env_names"]
                or secret_result["profile_names"]
            ):
                data["saved_secrets"] = secret_result

        if policy_profile_id:
            policy_result = await self._agent_domain._handle_policy_profile_select(
                request.model_copy(
                    update={
                        "action_id": "policy_profile.select",
                        "params": {"profile_id": policy_profile_id},
                    }
                )
            )
            data["policy_profile"] = dict(policy_result.data)
            resource_refs.extend(policy_result.resource_refs)

        if agent_request_payload is not None:
            agent_result = await self._agent_domain._handle_agent_profile_save(
                request.model_copy(
                    update={
                        "action_id": "agent_profile.save",
                        "params": {"profile": agent_request_payload},
                    }
                )
            )
            data["agent_profile"] = dict(agent_result.data)
            resource_refs.extend(agent_result.resource_refs)

        if normalized_skill_selection is not None:
            skill_result = await self._handle_skills_selection_save(
                request.model_copy(
                    update={
                        "action_id": "skills.selection.save",
                        "params": {"selection": dict(normalized_skill_selection)},
                    }
                )
            )
            data["skill_selection"] = dict(skill_result.data)
            resource_refs.extend(skill_result.resource_refs)

        # 密钥或配置变更后重启 Proxy
        if self._proxy_manager is not None:
            try:
                await self._proxy_manager.restart()
            except Exception as exc:
                log.warning(
                    "proxy_restart_after_setup_apply_failed",
                    error_type=type(exc).__name__,
                    error=str(exc),
                )

        return self._completed_result(
            request=request,
            code="SETUP_APPLIED",
            message="配置已保存，主 Agent 与系统设置已同步。",
            data=data,
            resource_refs=self._dedupe_resource_refs(resource_refs),
        )

    async def _handle_setup_oauth_and_apply(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        """Feature 079 Phase 2：原子化 "OAuth 授权 + setup.apply"。

        背景：之前授权走 ``provider.oauth.openai_codex`` 只写 ``auth-profiles.json``
        + ``.env.litellm``，不写 ``octoagent.yaml.providers[]``；用户还得再点一次
        "保存配置" 才能让 main 这类 alias 真正指向该 provider。结果出现"授权成
        功但配置没生效" 的断层（provider 没入 config，main 还是旧的 Qwen）。

        本 action 把两步合并成一次原子操作：先跑 OAuth，再立即 setup.apply。
        OAuth 成功后 credential 已落盘；若后续 apply 被 review blocking，credential
        不回滚（没有风险，只是没被引用），由前端展示结构化 blocking 让用户修复
        后再重试 setup.apply。

        Params:
        - ``provider_id``: 目前仅支持 "openai-codex"
        - ``env_name``: 传给 OAuth handler 的 env 变量名（默认 OPENAI_API_KEY）
        - ``profile_name``: auth-profiles.json 里的 profile 名（默认 openai-codex-default）
        - ``draft``: 完整的 setup.apply draft（含 providers / aliases / secrets）

        Returns:
        - 成功：``code=SETUP_OAUTH_AND_APPLIED``，data 合并 OAuth + apply 结果
        - OAuth 失败：原 OAuth error（不触达 apply）
        - OAuth 成功但 apply blocking：``code=SETUP_OAUTH_OK_APPLY_BLOCKED``，
          credential 已保留，前端应提示用户修复 blocking 后重试 setup.apply
        """
        provider_id = self._param_str(request.params, "provider_id", default="openai-codex")
        if provider_id != "openai-codex":
            raise self._action_error(
                "OAUTH_PROVIDER_UNSUPPORTED",
                f"setup.oauth_and_apply 暂只支持 openai-codex，收到 {provider_id!r}",
            )
        env_name = self._param_str(request.params, "env_name", default="OPENAI_API_KEY")
        profile_name = self._param_str(
            request.params,
            "profile_name",
            default="openai-codex-default",
        )
        draft = request.params.get("draft")
        if draft is None or not isinstance(draft, dict):
            raise self._action_error(
                "SETUP_DRAFT_REQUIRED",
                "setup.oauth_and_apply 需要 draft（与 setup.apply 同结构）",
            )

        # Step 1：调用现有 OAuth handler（保持不动，共用同一套 credential 写入逻辑）
        oauth_request = request.model_copy(
            update={
                "action_id": "provider.oauth.openai_codex",
                "params": {
                    "env_name": env_name,
                    "profile_name": profile_name,
                },
            }
        )
        oauth_result = await self._mcp_domain._handle_provider_oauth_openai_codex(
            oauth_request
        )
        oauth_data: dict[str, Any] = dict(oauth_result.data) if oauth_result.data else {}

        # Step 2：OAuth 成功 → 尝试 setup.apply。apply 内部会再做 review，一旦 blocking
        # 由于我们已经写入了 credential，不能让整个 action 失败掉 —— 应该告诉前端
        # "授权部分已完成，但配置保存被 review 拦了"，让用户能继续救火。
        apply_request = request.model_copy(
            update={
                "action_id": "setup.apply",
                "params": {"draft": draft},
            }
        )
        apply_data: dict[str, Any] = {}
        apply_blocked = False
        apply_error_message = ""
        try:
            apply_result = await self._handle_setup_apply(apply_request)
            apply_data = dict(apply_result.data) if apply_result.data else {}
        except ControlPlaneActionError as exc:
            # 区分 "review blocking"（可修复）和其他 runtime 错误（需要继续抛）
            if exc.code == "SETUP_REVIEW_BLOCKED":
                apply_blocked = True
                apply_error_message = str(exc)
                log.warning(
                    "setup_oauth_and_apply_blocked_after_oauth",
                    error=apply_error_message,
                )
            else:
                raise

        merged_data: dict[str, Any] = {
            "oauth": oauth_data,
            "apply": apply_data,
            "apply_blocked": apply_blocked,
        }
        if apply_blocked:
            merged_data["apply_error_message"] = apply_error_message

        resource_refs = [
            *oauth_result.resource_refs,
            self._resource_ref("config_schema", "config:octoagent"),
            self._resource_ref("diagnostics_summary", "diagnostics:runtime"),
            self._resource_ref("setup_governance", "setup:governance"),
        ]

        if apply_blocked:
            return self._completed_result(
                request=request,
                code="SETUP_OAUTH_OK_APPLY_BLOCKED",
                message=(
                    "授权已完成并写入凭证；但保存配置被风险检查拦下。"
                    "修复下方风险项后，再点击保存即可。"
                ),
                data=merged_data,
                resource_refs=self._dedupe_resource_refs(resource_refs),
            )
        return self._completed_result(
            request=request,
            code="SETUP_OAUTH_AND_APPLIED",
            message="授权已完成，配置已保存。",
            data=merged_data,
            resource_refs=self._dedupe_resource_refs(resource_refs),
        )

    async def _handle_setup_quick_connect(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        apply_result = await self._handle_setup_apply(
            request.model_copy(update={"action_id": "setup.apply"})
        )
        activation_data = await self._activate_runtime_after_config_change(
            request=request,
            failure_code="SETUP_ACTIVATION_FAILED",
            failure_prefix="配置已保存，但 runtime reload 失败",
            raise_on_failure=True,
        )

        # Feature 081 P4：proxy_manager 总是 None（Provider 直连无 Proxy）；
        # 此分支仅做安全检查保留，下个版本删除
        if self._proxy_manager is not None:
            try:
                await self._proxy_manager.restart()
            except Exception as exc:
                log.warning(
                    "proxy_restart_after_quick_connect_failed",
                    error_type=type(exc).__name__,
                    error=str(exc),
                )

        review_result = await self._handle_setup_review(
            request.model_copy(update={"action_id": "setup.review", "params": {"draft": {}}})
        )
        refreshed_review = review_result.data.get("review", {})
        data = dict(apply_result.data)
        if isinstance(refreshed_review, dict) and refreshed_review:
            data["review"] = refreshed_review
        data["activation"] = activation_data

        message = str(activation_data["runtime_reload_message"])
        return self._completed_result(
            request=request,
            code="SETUP_QUICK_CONNECTED",
            message=message,
            data=data,
            resource_refs=self._dedupe_resource_refs(
                list(apply_result.resource_refs)
                + [
                    self._resource_ref("config_schema", "config:octoagent"),
                    self._resource_ref("diagnostics_summary", "diagnostics:runtime"),
                    self._resource_ref("setup_governance", "setup:governance"),
                ]
            ),
        )

    async def _handle_skills_selection_save(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        raw_selection = request.params.get("selection")
        if raw_selection is None:
            raw_selection = {}
        if not isinstance(raw_selection, Mapping):
            raise self._action_error(
                "SKILL_SELECTION_REQUIRED",
                "selection 必须是对象",
            )

        _, selected_project, _, _ = await self._resolve_selection()
        if selected_project is None:
            raise self._action_error("PROJECT_REQUIRED", "当前没有可用 project")

        normalized = await self._normalize_skill_selection_for_scope(
            raw_selection,
            selected_project=selected_project,
        )

        metadata = dict(selected_project.metadata)
        metadata["skill_selection"] = normalized
        await self._stores.project_store.save_project(
            selected_project.model_copy(
                update={
                    "metadata": metadata,
                    "updated_at": datetime.now(tz=UTC),
                }
            )
        )
        await self._stores.conn.commit()

        refreshed = await self.get_skill_governance_document(
            selected_project=selected_project.model_copy(update={"metadata": metadata}),
        )
        return self._completed_result(
            request=request,
            code="SKILL_SELECTION_SAVED",
            message="Skills 默认启用范围已保存。",
            data={
                "selection": normalized,
                "selected_count": refreshed.summary.get("selected_count", 0),
                "disabled_count": refreshed.summary.get("disabled_count", 0),
            },
            resource_refs=[
                self._resource_ref("skill_governance", "skills:governance"),
                self._resource_ref("setup_governance", "setup:governance"),
                self._resource_ref("capability_pack", "capability:bundled"),
            ],
            target_refs=[
                ControlPlaneTargetRef(
                    target_type="project",
                    target_id=selected_project.project_id,
                    label=selected_project.name,
                )
            ],
        )

    async def _handle_config_apply(
        self, request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        payload = request.params.get("config")
        if not isinstance(payload, dict):
            raise self._action_error("CONFIG_REQUIRED", "config payload 必须是对象")
        normalized = dict(payload)
        normalized.setdefault("updated_at", date.today().isoformat())
        config = OctoAgentConfig.model_validate(normalized)
        save_config(config, self._ctx.project_root)
        # Feature 081 P4：不再生成 litellm-config.yaml
        return self._completed_result(
            request=request,
            code="CONFIG_APPLIED",
            message="配置已保存（ProviderRouter 直连，无 LiteLLM bridge）",
            data={},
            resource_refs=[
                self._resource_ref("config_schema", "config:octoagent"),
                self._resource_ref("diagnostics_summary", "diagnostics:runtime"),
            ],
        )

    # ══════════════════════════════════════════════════════════════
    #  Private Helpers
    # ══════════════════════════════════════════════════════════════

    # ── runtime activation ───────────────────────────────────────

    async def _activate_runtime_after_config_change(
        self,
        *,
        request: ActionRequestEnvelope,
        failure_code: str,
        failure_prefix: str,
        raise_on_failure: bool,
    ) -> dict[str, Any]:
        """触发 runtime reload（Feature 081 后不再启动 LiteLLM Proxy）。

        Feature 081 P4 修复（Codex F1）：原实现调用 ``start_proxy()`` 启动 LiteLLM
        Proxy 子进程；Provider 直连后无 Proxy 概念，``start_proxy()`` 已退役为
        总是抛 ``RuntimeActivationError``。修复后直接跳过 proxy 启动，仅保留
        managed runtime 的 reload 分支（让 Gateway 进程重读新 yaml + 重建
        ProviderRouter alias 缓存）。

        ``failure_code`` / ``failure_prefix`` / ``raise_on_failure`` 仍接受但
        在 Provider 直连模式下不会触发——保留参数以最小化调用方改动。
        """
        activation_service = _cp_pkg.RuntimeActivationService(self._ctx.project_root)
        managed_runtime = activation_service.has_managed_runtime()

        # Feature 081 P4：跳过 start_proxy()；直接构造成功 activation_data
        activation_data: dict[str, Any] = {
            "project_root": str(self._ctx.project_root),
            "source_root": str(self._ctx.project_root),
            "compose_file": "",
            "proxy_url": "",
            "managed_runtime": managed_runtime,
            "warnings": [],
            "runtime_reload_mode": "none",
            "runtime_reload_message": "配置已保存，Provider 直连已就绪。",
            "activation_succeeded": True,
        }

        update_service = self._ctx.update_service
        if managed_runtime and update_service is not None:
            if request.surface == ControlPlaneSurface.CLI:
                await update_service.restart(
                    trigger_source=self._map_update_source(request.surface)
                )
                activation_data["runtime_reload_mode"] = "managed_restart_completed"
                activation_data["runtime_reload_message"] = (
                    "已自动重启托管实例，真实模型会在新进程里生效。"
                )
            else:
                asyncio.create_task(
                    self._restart_runtime_after_delay(
                        delay_seconds=2.0,
                        trigger_source=self._map_update_source(request.surface),
                    )
                )
                activation_data["runtime_reload_mode"] = "managed_restart_scheduled"
                activation_data["runtime_reload_message"] = (
                    "配置已保存，当前实例会在几秒内自动重启并切到真实模型（Provider 直连）。"
                )
        else:
            activation_data["runtime_reload_mode"] = "manual_restart_required"
            activation_data["runtime_reload_message"] = (
                "配置已保存（Provider 直连）；如果当前 Gateway 正在运行，请手动重启后再开始真实对话。"
            )
        return activation_data

    async def _restart_runtime_after_delay(
        self,
        *,
        delay_seconds: float,
        trigger_source: UpdateTriggerSource,
    ) -> None:
        update_service = self._ctx.update_service
        if update_service is None:
            return
        await asyncio.sleep(delay_seconds)
        try:
            await update_service.restart(trigger_source=trigger_source)
        except Exception as exc:  # pragma: no cover
            log.warning(
                "setup_quick_connect_restart_failed",
                error_type=type(exc).__name__,
                error=str(exc),
            )

    @staticmethod
    def _map_update_source(surface: ControlPlaneSurface) -> UpdateTriggerSource:
        mapping = {
            ControlPlaneSurface.WEB: UpdateTriggerSource.WEB,
            ControlPlaneSurface.CLI: UpdateTriggerSource.CLI,
        }
        return mapping.get(surface, UpdateTriggerSource.SYSTEM)

    # ── wizard session ────────────────────────────────────────────

    async def _get_wizard_session(self) -> Any:
        from octoagent.core.models import (
            WizardSessionDocument,
            WizardStepDocument,
        )
        from octoagent.provider.dx.onboarding_service import OnboardingService

        onboarding = OnboardingService(self._ctx.project_root)
        session, _, notes = onboarding.load_or_create_session()
        current_step = session.current_step.value
        steps = [
            WizardStepDocument(
                step_id=step.value,
                label=step.value.replace("_", " "),
                status=state.status.value,
                summary=state.summary,
                actions=[action.model_dump(mode="json") for action in state.actions],
                detail_ref=state.detail_ref,
            )
            for step, state in session.steps.items()
        ]
        status = "ready" if session.summary.overall_status.value == "READY" else "action_required"
        warnings = list(notes)
        degraded = ControlPlaneDegradedState(
            is_degraded=status != "ready",
            reasons=list(notes),
        )
        return WizardSessionDocument(
            current_step=current_step,
            steps=steps,
            summary=session.summary.model_dump(mode="json"),
            next_actions=[
                action.model_dump(mode="json") for action in session.summary.next_actions
            ],
            resumable=True,
            blocking_reason=session.summary.headline,
            status=status,
            warnings=warnings,
            degraded=degraded,
            capabilities=[
                ControlPlaneCapability(
                    capability_id="wizard.refresh",
                    label="刷新引导状态",
                    action_id="wizard.refresh",
                ),
                ControlPlaneCapability(
                    capability_id="wizard.restart",
                    label="重新开始引导",
                    action_id="wizard.restart",
                ),
            ],
        )

    # ── diagnostics helpers ──────────────────────────────────────

    def _load_runtime_snapshot(self) -> dict[str, Any]:
        if self._update_status_store is None:
            return {}
        loader = getattr(self._update_status_store, "load_runtime_state", None)
        if not callable(loader):
            return {}
        runtime_state = loader()
        if runtime_state is None:
            return {}
        return runtime_state.model_dump(mode="json")

    def _load_update_summary(self) -> dict[str, Any]:
        if self._update_status_store is None:
            return {}
        loader = getattr(self._update_status_store, "load_summary", None)
        if not callable(loader):
            return {}
        summary = loader()
        if summary is None:
            return {}
        return summary.model_dump(mode="json")

    def _build_channel_summary(self) -> dict[str, Any]:
        cfg = load_config(self._ctx.project_root)
        telegram_cfg = getattr(getattr(cfg, "channels", None), "telegram", None) if cfg else None
        pending_pairings = (
            len(self._telegram_state_store.list_pending_pairings())
            if self._telegram_state_store is not None
            else 0
        )
        return {
            "telegram": {
                "enabled": telegram_cfg.enabled if telegram_cfg else False,
                "mode": telegram_cfg.mode if telegram_cfg else "",
                "pending_pairings": pending_pairings,
            }
        }

    # ── merge / dedupe helpers ───────────────────────────────────

    def _deep_merge_dicts(
        self,
        base: dict[str, Any],
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        merged = dict(base)
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = self._deep_merge_dicts(merged[key], value)
            else:
                merged[key] = value
        return merged

    @staticmethod
    def _dedupe_resource_refs(
        refs: list[ControlPlaneResourceRef],
    ) -> list[ControlPlaneResourceRef]:
        seen: set[tuple[str, str, int]] = set()
        deduped: list[ControlPlaneResourceRef] = []
        for ref in refs:
            key = (ref.resource_type, ref.resource_id, ref.schema_version)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(ref)
        return deduped
