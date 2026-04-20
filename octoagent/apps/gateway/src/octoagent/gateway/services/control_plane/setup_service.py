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
from pathlib import Path
from typing import Any

import structlog
from octoagent.core.models import (
    ActionRequestEnvelope,
    ActionResultEnvelope,
    AgentProfilesDocument,
    BlockingReason,
    CapabilityPackDocument,
    ConfigFieldHint,
    ConfigSchemaDocument,
    ControlPlaneCapability,
    ControlPlaneDegradedState,
    ControlPlaneResourceRef,
    ControlPlaneSurface,
    ControlPlaneTargetRef,
    DiagnosticsFailureSummary,
    DiagnosticsSubsystemStatus,
    DiagnosticsSummaryDocument,
    McpProviderCatalogDocument,
    OwnerProfileDocument,
    PolicyProfilesDocument,
    Project,
    ProjectOption,
    ProjectSelectorDocument,
    SetupGovernanceDocument,
    SetupGovernanceSection,
    SetupReviewSummary,
    SetupRiskItem,
    SkillGovernanceDocument,
    SkillGovernanceItem,
    UpdateTriggerSource,
)
from octoagent.policy import DEFAULT_PROFILE
from octoagent.provider.auth.credentials import ApiKeyCredential
from octoagent.provider.auth.profile import ProviderProfile
from octoagent.provider.auth.store import CredentialStore
from octoagent.provider.dx.backup_service import BackupService
from octoagent.gateway.services.config.config_schema import OctoAgentConfig
from octoagent.gateway.services.config.config_wizard import load_config, save_config
from octoagent.gateway.services.config.litellm_generator import (
    check_litellm_sync_status,
    generate_litellm_config,
)
import octoagent.gateway.services.control_plane as _cp_pkg  # 通过 package 引用以支持 monkeypatch
from octoagent.provider.dx.secret_service import SecretService
from pydantic import SecretStr, ValidationError

from ._base import ControlPlaneActionError, ControlPlaneContext, DomainServiceBase

log = structlog.get_logger()


class SetupDomainService(DomainServiceBase):
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
        sync_ok, diffs = check_litellm_sync_status(config, self._ctx.project_root)
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
            warnings=[] if sync_ok else diffs,
            degraded=ControlPlaneDegradedState(
                is_degraded=not sync_ok,
                reasons=[] if sync_ok else ["litellm_config_out_of_sync"],
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
        agent_profiles = await self._get_service("agent").get_agent_profiles_document()
        owner_profile = await self._get_service("agent").get_owner_profile_document()
        capability_pack = await self.get_capability_pack_document()
        policy_profiles = await self._get_service("agent").get_policy_profiles_document()
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
                f"runtime={config.current_value.get('runtime', {}).get('llm_mode', '')}，"
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
        agent_profiles = await self._get_service("agent").get_agent_profiles_document()
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
            agent_profiles = await self._get_service("agent").get_agent_profiles_document()
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
        litellm_path = generate_litellm_config(config, self._ctx.project_root)

        resource_refs = [
            self._resource_ref("config_schema", "config:octoagent"),
            self._resource_ref("diagnostics_summary", "diagnostics:runtime"),
            self._resource_ref("setup_governance", "setup:governance"),
            self._resource_ref("skill_governance", "skills:governance"),
        ]
        data: dict[str, Any] = {
            "review": review.model_dump(mode="json"),
            "litellm_config_path": str(litellm_path),
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
            policy_result = await self._get_service("agent")._handle_policy_profile_select(
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
            agent_result = await self._get_service("agent")._handle_agent_profile_save(
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
        oauth_result = await self._get_service("mcp")._handle_provider_oauth_openai_codex(
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
            failure_prefix="配置已保存，但 LiteLLM Proxy 启动失败",
            raise_on_failure=True,
        )

        # quick_connect 后重启 Proxy
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
        litellm_path = generate_litellm_config(config, self._ctx.project_root)
        return self._completed_result(
            request=request,
            code="CONFIG_APPLIED",
            message="配置已保存并同步 LiteLLM bridge",
            data={"litellm_config_path": str(litellm_path)},
            resource_refs=[
                self._resource_ref("config_schema", "config:octoagent"),
                self._resource_ref("diagnostics_summary", "diagnostics:runtime"),
            ],
        )

    # ══════════════════════════════════════════════════════════════
    #  Private Helpers
    # ══════════════════════════════════════════════════════════════

    # ── skill selection ──────────────────────────────────────────

    def _normalize_skill_selection_payload(
        self,
        selection: Mapping[str, Any],
        *,
        allowed_item_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        selected_item_ids = {
            str(item).strip()
            for item in selection.get("selected_item_ids", [])
            if str(item).strip()
        }
        disabled_item_ids = {
            str(item).strip()
            for item in selection.get("disabled_item_ids", [])
            if str(item).strip()
        }
        overlap = sorted(selected_item_ids & disabled_item_ids)
        if overlap:
            raise self._action_error(
                "SKILL_SELECTION_CONFLICT",
                f"skill selection 同时出现在 enabled/disabled 列表：{overlap[0]}",
            )
        if allowed_item_ids is not None:
            unknown = sorted((selected_item_ids | disabled_item_ids) - allowed_item_ids)
            if unknown:
                raise self._action_error(
                    "SKILL_SELECTION_UNKNOWN_ITEM",
                    f"未知的 skill governance item: {unknown[0]}",
                )
        return {
            "selected_item_ids": sorted(selected_item_ids),
            "disabled_item_ids": sorted(disabled_item_ids),
            "updated_at": datetime.now(tz=UTC).isoformat(),
        }

    async def _normalize_skill_selection_for_scope(
        self,
        selection: Mapping[str, Any],
        *,
        selected_project: Any | None,
    ) -> dict[str, Any]:
        if selected_project is None:
            raise self._action_error("PROJECT_REQUIRED", "当前没有可用 project")
        document = await self.get_skill_governance_document(
            selected_project=selected_project,
        )
        allowed_item_ids = {item.item_id for item in document.items}
        return self._normalize_skill_selection_payload(
            selection,
            allowed_item_ids=allowed_item_ids,
        )

    def _resolve_project_skill_selection(
        self,
        selected_project: Any | None,
        *,
        draft_selection: Mapping[str, Any] | None = None,
    ) -> tuple[set[str], set[str]]:
        selection = draft_selection
        if selection is None and selected_project is not None:
            metadata = (
                dict(selected_project.metadata)
                if isinstance(getattr(selected_project, "metadata", None), dict)
                else {}
            )
            raw = metadata.get("skill_selection")
            if isinstance(raw, Mapping):
                selection = raw
        if selection is None:
            return set(), set()
        selected_item_ids = {
            str(item).strip()
            for item in selection.get("selected_item_ids", [])
            if str(item).strip()
        }
        disabled_item_ids = {
            str(item).strip()
            for item in selection.get("disabled_item_ids", [])
            if str(item).strip()
        }
        return selected_item_ids, disabled_item_ids

    def _skill_item_selected(
        self,
        *,
        item_id: str,
        enabled_by_default: bool,
        selected_item_ids: set[str],
        disabled_item_ids: set[str],
    ) -> tuple[bool, str]:
        if item_id in selected_item_ids:
            return True, "project_override"
        if item_id in disabled_item_ids:
            return False, "project_override"
        return enabled_by_default, "default"

    def _apply_skill_selection_to_items(
        self,
        *,
        items: list[SkillGovernanceItem],
        selected_project: Any | None,
        draft_selection: Mapping[str, Any] | None = None,
    ) -> list[SkillGovernanceItem]:
        selected_item_ids, disabled_item_ids = self._resolve_project_skill_selection(
            selected_project,
            draft_selection=draft_selection,
        )
        projected: list[SkillGovernanceItem] = []
        for item in items:
            selected, selection_source = self._skill_item_selected(
                item_id=item.item_id,
                enabled_by_default=item.enabled_by_default,
                selected_item_ids=selected_item_ids,
                disabled_item_ids=disabled_item_ids,
            )
            projected.append(
                item.model_copy(
                    update={
                        "selected": selected,
                        "selection_source": selection_source,
                    }
                )
            )
        return projected

    # ── config / secret ──────────────────────────────────────────

    def _save_runtime_secret_values(
        self,
        *,
        config: OctoAgentConfig,
        secret_values: Mapping[str, Any],
    ) -> dict[str, Any]:
        normalized = {
            str(key).strip(): str(value).strip()
            for key, value in secret_values.items()
            if str(key).strip() and str(value).strip()
        }
        if not normalized:
            return {"litellm_env_names": [], "runtime_env_names": [], "profile_names": []}

        litellm_targets = {config.runtime.master_key_env}
        runtime_targets: set[str] = set()
        for provider in config.providers:
            litellm_targets.add(provider.api_key_env)
        if config.front_door.bearer_token_env:
            runtime_targets.add(config.front_door.bearer_token_env)
        if config.front_door.trusted_proxy_token_env:
            runtime_targets.add(config.front_door.trusted_proxy_token_env)
        telegram = config.channels.telegram
        if telegram.bot_token_env:
            runtime_targets.add(telegram.bot_token_env)
        if telegram.webhook_secret_env:
            runtime_targets.add(telegram.webhook_secret_env)

        litellm_updates = {
            env_name: value for env_name, value in normalized.items() if env_name in litellm_targets
        }
        runtime_updates = {
            env_name: value for env_name, value in normalized.items() if env_name in runtime_targets
        }
        if config.runtime.master_key_env in litellm_updates:
            master_key = litellm_updates[config.runtime.master_key_env]
            if config.runtime.master_key_env == "LITELLM_MASTER_KEY":
                litellm_updates.setdefault("LITELLM_PROXY_KEY", master_key)

        self._write_env_values(self._ctx.project_root / ".env.litellm", litellm_updates)
        self._write_env_values(self._ctx.project_root / ".env", runtime_updates)

        store = self._credential_store()
        saved_profiles: list[str] = []
        for provider in config.providers:
            if provider.auth_type != "api_key":
                continue
            secret_value = litellm_updates.get(provider.api_key_env)
            if not secret_value:
                continue
            existing = store.get_profile(f"{provider.id}-default")
            profile = ProviderProfile(
                name=f"{provider.id}-default",
                provider=provider.id,
                auth_mode="api_key",
                credential=ApiKeyCredential(
                    provider=provider.id,
                    key=SecretStr(secret_value),
                ),
                is_default=(
                    existing.is_default
                    if existing is not None
                    else store.get_default_profile() is None
                ),
                created_at=existing.created_at if existing is not None else datetime.now(tz=UTC),
                updated_at=datetime.now(tz=UTC),
            )
            store.set_profile(profile)
            saved_profiles.append(profile.name)

        return {
            "litellm_env_names": sorted(litellm_updates.keys()),
            "runtime_env_names": sorted(runtime_updates.keys()),
            "profile_names": saved_profiles,
        }

    def _build_config_ui_hints(self) -> dict[str, ConfigFieldHint]:
        hints = {
            "runtime.llm_mode": ConfigFieldHint(
                field_path="runtime.llm_mode",
                section="runtime",
                label="LLM 模式",
                description="Gateway 当前运行模式",
                widget="select",
                order=10,
            ),
            "runtime.litellm_proxy_url": ConfigFieldHint(
                field_path="runtime.litellm_proxy_url",
                section="runtime",
                label="LiteLLM 代理地址",
                placeholder="http://localhost:4000",
                order=20,
            ),
            "runtime.master_key_env": ConfigFieldHint(
                field_path="runtime.master_key_env",
                section="runtime",
                label="主密钥环境变量",
                widget="env-ref",
                sensitive=True,
                order=30,
            ),
            "memory.reasoning_model_alias": ConfigFieldHint(
                field_path="memory.reasoning_model_alias",
                section="memory-models",
                label="加工模型别名",
                description="负责片段整理、摘要、候选结论与候选事实加工。",
                placeholder="main",
                help_text="留空时默认回退到 main。",
                order=33,
            ),
            "memory.expand_model_alias": ConfigFieldHint(
                field_path="memory.expand_model_alias",
                section="memory-models",
                label="扩写模型别名",
                description="负责 recall query expansion；不填时回退到 main。",
                placeholder="main",
                help_text="适合绑定成本较低、理解查询改写较稳定的 alias。",
                order=34,
            ),
            "memory.embedding_model_alias": ConfigFieldHint(
                field_path="memory.embedding_model_alias",
                section="memory-models",
                label="Embedding 模型别名",
                description="负责语义检索 projection。留空时走内建默认层。",
                placeholder="knowledge-embed",
                help_text="后续切换 embedding 时会触发后台重建，不会立即替换现网索引。",
                order=35,
            ),
            "memory.rerank_model_alias": ConfigFieldHint(
                field_path="memory.rerank_model_alias",
                section="memory-models",
                label="Rerank 模型别名",
                description="负责召回结果重排；不填时回退到 heuristic。",
                placeholder="memory-rerank",
                help_text="没有专门 rerank alias 也可以先留空。",
                order=36,
            ),
            "providers": ConfigFieldHint(
                field_path="providers",
                section="providers",
                label="模型提供方列表",
                description="这里配置 OpenRouter、OpenAI 等模型提供方。",
                widget="provider-list",
                placeholder="[]",
                order=40,
            ),
            "model_aliases": ConfigFieldHint(
                field_path="model_aliases",
                section="models",
                label="模型别名",
                widget="alias-map",
                placeholder="{}",
                order=50,
            ),
            "front_door.mode": ConfigFieldHint(
                field_path="front_door.mode",
                section="security",
                label="对外访问模式",
                description="控制谁可以访问 owner-facing API。",
                widget="select",
                help_text="本机使用 loopback；公网部署使用 bearer 或 trusted_proxy。",
                order=55,
            ),
            "front_door.bearer_token_env": ConfigFieldHint(
                field_path="front_door.bearer_token_env",
                section="security",
                label="Bearer Token 环境变量",
                widget="env-ref",
                sensitive=True,
                help_text="仅在 bearer 模式下需要。",
                order=56,
            ),
            "front_door.trusted_proxy_header": ConfigFieldHint(
                field_path="front_door.trusted_proxy_header",
                section="security",
                label="Trusted Proxy Header",
                help_text="trusted_proxy 模式下由反向代理注入的共享 header。",
                order=57,
            ),
            "front_door.trusted_proxy_token_env": ConfigFieldHint(
                field_path="front_door.trusted_proxy_token_env",
                section="security",
                label="Trusted Proxy Token 环境变量",
                widget="env-ref",
                sensitive=True,
                order=58,
            ),
            "front_door.trusted_proxy_cidrs": ConfigFieldHint(
                field_path="front_door.trusted_proxy_cidrs",
                section="security",
                label="Trusted Proxy 来源 CIDR",
                widget="string-list",
                help_text="必须限制为受信代理来源，避免旁路直接访问 Gateway。",
                order=59,
            ),
            "channels.telegram.enabled": ConfigFieldHint(
                field_path="channels.telegram.enabled",
                section="channels",
                label="启用 Telegram",
                widget="toggle",
                help_text="启用前需完成 Provider 和 Secret 配置。",
                order=60,
            ),
            "channels.telegram.mode": ConfigFieldHint(
                field_path="channels.telegram.mode",
                section="channels",
                label="Telegram 接入模式",
                widget="select",
                order=70,
            ),
            "channels.telegram.bot_token_env": ConfigFieldHint(
                field_path="channels.telegram.bot_token_env",
                section="channels",
                label="Telegram Bot Token 环境变量",
                widget="env-ref",
                sensitive=True,
                order=80,
            ),
            "channels.telegram.webhook_url": ConfigFieldHint(
                field_path="channels.telegram.webhook_url",
                section="channels",
                label="Webhook URL",
                help_text="仅 webhook 模式需要。无公网 HTTPS 时使用 polling。",
                order=90,
            ),
            "channels.telegram.webhook_secret_env": ConfigFieldHint(
                field_path="channels.telegram.webhook_secret_env",
                section="channels",
                label="Webhook Secret 环境变量",
                widget="env-ref",
                sensitive=True,
                order=95,
            ),
            "channels.telegram.dm_policy": ConfigFieldHint(
                field_path="channels.telegram.dm_policy",
                section="channels",
                label="私聊访问策略",
                widget="select",
                help_text="pairing 需配对后使用；open 允许任意用户触发。",
                order=97,
            ),
            "channels.telegram.allow_users": ConfigFieldHint(
                field_path="channels.telegram.allow_users",
                section="channels",
                label="允许的私聊用户",
                widget="string-list",
                order=100,
            ),
            "channels.telegram.group_policy": ConfigFieldHint(
                field_path="channels.telegram.group_policy",
                section="channels",
                label="群聊访问策略",
                widget="select",
                help_text="allowlist 限定可触发的群组；open 允许所有群组。",
                order=105,
            ),
            "channels.telegram.allowed_groups": ConfigFieldHint(
                field_path="channels.telegram.allowed_groups",
                section="channels",
                label="允许的群组",
                widget="string-list",
                order=110,
            ),
            "channels.telegram.group_allow_users": ConfigFieldHint(
                field_path="channels.telegram.group_allow_users",
                section="channels",
                label="群聊内允许用户",
                widget="string-list",
                order=115,
            ),
        }
        return hints

    # ── review summary builder ───────────────────────────────────

    def _build_setup_review_summary(
        self,
        *,
        config: dict[str, Any],
        config_warnings: list[str],
        selected_project: Any | None,
        diagnostics: DiagnosticsSummaryDocument,
        active_agent_profile: dict[str, Any],
        policy_profile_id: str,
        skill_governance: SkillGovernanceDocument,
        secret_audit: Any | None,
        validation_errors: list[str],
    ) -> SetupReviewSummary:
        config_ref = self._resource_ref("config_schema", "config:octoagent")
        diagnostics_ref = self._resource_ref("diagnostics_summary", "diagnostics:runtime")
        agent_ref = self._resource_ref("agent_profiles", "agent-profiles:overview")
        policy_ref = self._resource_ref("policy_profiles", "policy:profiles")
        skill_ref = self._resource_ref("skill_governance", "skills:governance")
        provider_runtime_risks: list[SetupRiskItem] = []
        channel_exposure_risks: list[SetupRiskItem] = []
        agent_autonomy_risks: list[SetupRiskItem] = []
        tool_skill_readiness_risks: list[SetupRiskItem] = []
        secret_binding_risks: list[SetupRiskItem] = []

        providers = [
            item
            for item in config.get("providers", [])
            if isinstance(item, dict) and item.get("enabled", True)
        ]
        model_aliases = config.get("model_aliases", {})
        runtime_cfg = (
            config.get("runtime", {}) if isinstance(config.get("runtime"), dict) else {}
        )
        llm_mode = str(runtime_cfg.get("llm_mode", "echo")).strip().lower() or "echo"
        requires_real_model = llm_mode != "echo"
        front_door = (
            config.get("front_door", {}) if isinstance(config.get("front_door"), dict) else {}
        )
        telegram_cfg = (
            config.get("channels", {}).get("telegram", {})
            if isinstance(config.get("channels"), dict)
            else {}
        )
        for message in validation_errors:
            provider_runtime_risks.append(
                SetupRiskItem(
                    risk_id="config_validation_failed",
                    severity="high",
                    title="配置草稿未通过校验",
                    summary=message,
                    blocking=True,
                    recommended_action='先修正配置字段，再点击"检查配置"。',
                    source_ref=config_ref,
                )
            )
        if selected_project is None:
            provider_runtime_risks.append(
                SetupRiskItem(
                    risk_id="project_unavailable",
                    severity="high",
                    title="当前没有可用 Project",
                    summary="setup 需要先解析到一个可用的 project / workspace。",
                    blocking=True,
                    recommended_action="先完成 project 选择或初始化默认项目。",
                    source_ref=self._resource_ref("project_selector", "project:selector"),
                )
            )
        if not providers:
            provider_runtime_risks.append(
                SetupRiskItem(
                    risk_id="provider_missing",
                    severity="high" if requires_real_model else "warning",
                    title="还没有可用 Provider",
                    summary=(
                        "当前没有任何启用中的 provider，主 Agent 不能调用真实模型。"
                        if requires_real_model
                        else (
                            "当前处于体验模式，还没有接入真实模型；"
                            "你仍然可以先用 Web 跑通基础流程。"
                        )
                    ),
                    blocking=requires_real_model,
                    recommended_action=(
                        "至少配置 1 个 provider，并补齐对应 secret 引用。"
                        if requires_real_model
                        else (
                            "如果你只是先体验本地 Web，可暂时保留为空；"
                            "接 OpenRouter / OpenAI 时再补齐。"
                        )
                    ),
                    source_ref=config_ref,
                )
            )
        if "main" not in model_aliases:
            provider_runtime_risks.append(
                SetupRiskItem(
                    risk_id="main_alias_missing",
                    severity="high" if requires_real_model else "warning",
                    title="缺少 main 模型别名",
                    summary=(
                        "主 Agent 依赖 main alias，当前 setup 还没有可用的默认模型。"
                        if requires_real_model
                        else "当前是体验模式，main alias 可以稍后再补；接入真实模型前需要配置好它。"
                    ),
                    blocking=requires_real_model,
                    recommended_action=(
                        "先为 main alias 指定 provider 和模型。"
                        if requires_real_model
                        else "准备接真实模型时，再为 main alias 指定 provider 和模型。"
                    ),
                    source_ref=config_ref,
                )
            )
        if "cheap" not in model_aliases:
            provider_runtime_risks.append(
                SetupRiskItem(
                    risk_id="cheap_alias_missing",
                    severity="warning",
                    title="缺少 cheap 模型别名",
                    summary="当前系统仍可运行，但自动降级与低成本路径不可用。",
                    blocking=False,
                    recommended_action="建议补一个 cheap alias，便于 fallback 和后台任务使用。",
                    source_ref=config_ref,
                )
            )
        provider_runtime_risks.extend(
            self._collect_memory_alias_risks(
                config=config,
                config_ref=config_ref,
            )
        )
        for warning in config_warnings:
            provider_runtime_risks.append(
                SetupRiskItem(
                    risk_id="config_warning",
                    severity="warning",
                    title="Provider / Runtime 仍有告警",
                    summary=warning,
                    blocking=False,
                    recommended_action="建议先处理 bridge 或 LiteLLM sync 告警。",
                    source_ref=diagnostics_ref,
                )
            )
        front_door_mode = str(front_door.get("mode", "loopback")).strip().lower() or "loopback"
        if front_door_mode == "trusted_proxy" and not front_door.get("trusted_proxy_cidrs"):
            channel_exposure_risks.append(
                SetupRiskItem(
                    risk_id="trusted_proxy_cidrs_missing",
                    severity="high",
                    title="Trusted Proxy 未限制来源",
                    summary="trusted_proxy 模式缺少受信代理来源 CIDR。",
                    blocking=True,
                    recommended_action="补齐 trusted_proxy_cidrs，避免非代理来源直接访问 Gateway。",
                    source_ref=config_ref,
                )
            )
        if telegram_cfg.get("enabled"):
            telegram_mode = str(telegram_cfg.get("mode", "webhook")).strip().lower()
            if telegram_mode == "webhook" and not telegram_cfg.get("webhook_url"):
                channel_exposure_risks.append(
                    SetupRiskItem(
                        risk_id="telegram_webhook_url_missing",
                        severity="high",
                        title="Telegram webhook 配置不完整",
                        summary="Telegram webhook 模式缺少 webhook_url。",
                        blocking=True,
                        recommended_action="补齐 webhook_url，或切换到 polling 模式。",
                        source_ref=config_ref,
                    )
                )
            if str(
                telegram_cfg.get("dm_policy", "")
            ).strip().lower() == "open" and not telegram_cfg.get("allow_users"):
                channel_exposure_risks.append(
                    SetupRiskItem(
                        risk_id="telegram_dm_open",
                        severity="warning",
                        title="Telegram 私聊对任意用户开放",
                        summary="当前 DM policy=open，陌生人也可以直接触发主 Agent。",
                        blocking=False,
                        recommended_action="小白默认建议使用 pairing 或 allowlist。",
                        source_ref=diagnostics_ref,
                    )
                )
            if str(
                telegram_cfg.get("group_policy", "")
            ).strip().lower() == "open" and not telegram_cfg.get("allowed_groups"):
                channel_exposure_risks.append(
                    SetupRiskItem(
                        risk_id="telegram_group_open",
                        severity="warning",
                        title="Telegram 群聊默认开放",
                        summary="当前 group policy=open，未限制 allowed_groups。",
                        blocking=False,
                        recommended_action="建议至少限制 allowed_groups 或改为 allowlist。",
                        source_ref=diagnostics_ref,
                    )
                )
        if not active_agent_profile:
            agent_autonomy_risks.append(
                SetupRiskItem(
                    risk_id="agent_profile_missing",
                    severity="high",
                    title="主 Agent 设置还没有保存",
                    summary="当前 project 还没有保存主 Agent 的名称、Persona 和默认能力。",
                    blocking=True,
                    recommended_action='先确认右侧主 Agent 名称和 Persona，再点击"保存配置"。',
                    source_ref=agent_ref,
                )
            )
        elif not str(active_agent_profile.get("name", "")).strip():
            agent_autonomy_risks.append(
                SetupRiskItem(
                    risk_id="agent_profile_name_missing",
                    severity="high",
                    title="主 Agent 名称不能为空",
                    summary="主 Agent 名称还是空的，当前设置还不能保存。",
                    blocking=True,
                    recommended_action='先填写主 Agent 名称，再点击"检查配置"。',
                    source_ref=agent_ref,
                )
            )
        policy_profile = self._policy_profile_by_id(policy_profile_id) or DEFAULT_PROFILE
        if policy_profile_id == "permissive":
            agent_autonomy_risks.append(
                SetupRiskItem(
                    risk_id="policy_profile_permissive",
                    severity="high",
                    title="当前安全等级为自主",
                    summary="自主模式会放宽审批和工具边界，只适用于完全受信环境。",
                    blocking=False,
                    recommended_action="普通用户默认建议使用谨慎或平衡。",
                    source_ref=policy_ref,
                )
            )
        if active_agent_profile:
            agent_tool_profile = str(active_agent_profile.get("tool_profile", "standard")).strip()
            if not self._tool_profile_allowed(
                agent_tool_profile,
                policy_profile.allowed_tool_profile,
            ):
                agent_autonomy_risks.append(
                    SetupRiskItem(
                        risk_id="agent_profile_exceeds_policy",
                        severity="warning",
                        title="主 Agent 工具级别高于当前安全等级",
                        summary=(
                            f"Agent 要求 {agent_tool_profile}，但当前安全等级只允许 "
                            f"{policy_profile.allowed_tool_profile}。"
                        ),
                        blocking=False,
                        recommended_action="降低 Agent tool_profile，或显式切换更高安全 preset。",
                        source_ref=policy_ref,
                    )
                )
        for item in skill_governance.items:
            if not item.selected:
                continue
            if item.availability == "available":
                continue
            is_blocking = item.blocking and requires_real_model
            tool_skill_readiness_risks.append(
                SetupRiskItem(
                    risk_id=f"{item.item_id}:not_ready",
                    severity="high" if is_blocking else "warning",
                    title=f"{item.label} 尚未就绪",
                    summary=(
                        "；".join(item.missing_requirements) or f"状态={item.availability}"
                        if requires_real_model
                        else "当前处于体验模式，这项扩展能力可以稍后再接入。"
                    ),
                    blocking=is_blocking,
                    recommended_action=(
                        item.install_hint or "先处理缺失依赖后再启用该能力。"
                        if requires_real_model
                        else "如果你只是先跑通 Web，可暂时忽略；需要真实模型或扩展能力时再处理。"
                    ),
                    source_ref=skill_ref,
                )
            )
        if secret_audit is not None:
            for target_key in secret_audit.missing_targets:
                secret_binding_risks.append(
                    SetupRiskItem(
                        risk_id=f"secret_missing:{target_key}",
                        severity="high",
                        title="缺少 Secret 绑定",
                        summary=f"{target_key} 还没有完成 canonical secret binding。",
                        blocking=True,
                        recommended_action=(
                            '先完成 Secret 绑定后，再点击"检查配置"。'
                        ),
                        source_ref=config_ref,
                    )
                )
            for unresolved in secret_audit.unresolved_refs:
                secret_binding_risks.append(
                    SetupRiskItem(
                        risk_id=f"secret_unresolved:{unresolved}",
                        severity="high",
                        title="Secret 引用无法解析",
                        summary=unresolved,
                        blocking=True,
                        recommended_action="修正 secret ref 或环境变量后重试。",
                        source_ref=config_ref,
                    )
                )
            for plaintext in secret_audit.plaintext_risks:
                secret_binding_risks.append(
                    SetupRiskItem(
                        risk_id="secret_plaintext_risk",
                        severity="high",
                        title="检测到明文 Secret 风险",
                        summary=plaintext,
                        blocking=True,
                        recommended_action="移除明文凭证，改用 refs-only secret binding。",
                        source_ref=config_ref,
                    )
                )
            if secret_audit.reload_required:
                secret_binding_risks.append(
                    SetupRiskItem(
                        risk_id="secret_reload_required",
                        severity="warning",
                        title="Secret 绑定已变更但尚未重载",
                        summary="当前 secret bindings 已更新，但 runtime 仍需要 reload / restart。",
                        blocking=False,
                        recommended_action="完成 reload 或重启后，再做健康检查或保存配置。",
                        source_ref=diagnostics_ref,
                    )
                )
            for warning in secret_audit.warnings:
                secret_binding_risks.append(
                    SetupRiskItem(
                        risk_id="secret_warning",
                        severity="warning",
                        title="Secret 配置仍有告警",
                        summary=warning,
                        blocking=False,
                        recommended_action=(
                            "建议把 legacy / provider bridge 迁移到 "
                            "canonical secret binding。"
                        ),
                        source_ref=config_ref,
                    )
                )
        else:
            secret_binding_risks.append(
                SetupRiskItem(
                    risk_id="secret_audit_unavailable",
                    severity="warning",
                    title="Secret audit 当前不可用",
                    summary="暂时无法确认 provider / runtime / channel 所需的 secret 是否完整。",
                    blocking=False,
                    recommended_action="稍后重试或检查 secret service 是否可用。",
                    source_ref=diagnostics_ref,
                )
            )
        all_risks = (
            provider_runtime_risks
            + channel_exposure_risks
            + agent_autonomy_risks
            + tool_skill_readiness_risks
            + secret_binding_risks
        )
        blocking_reasons = [item.risk_id for item in all_risks if item.blocking]
        # Feature 079 Phase 4：同一组 blocking 项对应的结构化描述。前端 modal
        # 用这条渲染可读标题/说明/建议操作，不再依赖解析 risk_id 字符串。
        blocking_reasons_detail = [
            BlockingReason(
                risk_id=item.risk_id,
                title=item.title,
                summary=item.summary,
                recommended_action=item.recommended_action,
                severity=item.severity,
            )
            for item in all_risks
            if item.blocking
        ]
        warnings = [item.summary for item in all_risks if item.severity != "info"]
        if any(item.severity == "high" for item in all_risks):
            risk_level = "high"
        elif all_risks:
            risk_level = "warning"
        else:
            risk_level = "info"
        next_actions: list[str] = []
        if any(item.blocking for item in secret_binding_risks):
            next_actions.append('先补齐 Secret 绑定，再点击"检查配置"。')
        if any(item.blocking for item in provider_runtime_risks):
            next_actions.append("先修正 Provider / model alias 配置，确保主 Agent 可调用模型。")
        if any(item.blocking for item in agent_autonomy_risks):
            next_actions.append('先确认右侧主 Agent 名称和 Persona，再点击"保存配置"。')
        if any(item.blocking for item in tool_skill_readiness_risks):
            next_actions.append("先处理 skills / MCP 缺失依赖，避免首用时能力不可用。")
        if not next_actions:
            if requires_real_model:
                next_actions.append('检查已通过，可以点击"保存配置"。')
            else:
                next_actions.append("当前是体验模式，可以先保存默认配置并直接开始使用。")
                next_actions.append("后续如需真实模型，再补齐 Provider 和 main alias。")
        return SetupReviewSummary(
            ready=not bool(blocking_reasons),
            risk_level=risk_level,
            warnings=warnings,
            blocking_reasons=blocking_reasons,
            blocking_reasons_detail=blocking_reasons_detail,
            next_actions=next_actions,
            provider_runtime_risks=provider_runtime_risks,
            channel_exposure_risks=channel_exposure_risks,
            agent_autonomy_risks=agent_autonomy_risks,
            tool_skill_readiness_risks=tool_skill_readiness_risks,
            secret_binding_risks=secret_binding_risks,
        )

    # ── agent profile helpers ──────────────────────────────────

    def _resolve_active_agent_profile_payload(
        self,
        *,
        agent_profiles: AgentProfilesDocument,
        selected_project: Any | None,
    ) -> dict[str, Any]:
        if not agent_profiles.profiles:
            return {}
        if selected_project is not None and selected_project.default_agent_profile_id:
            matched = next(
                (
                    item
                    for item in agent_profiles.profiles
                    if item.profile_id == selected_project.default_agent_profile_id
                ),
                None,
            )
            if matched is not None:
                return matched.model_dump(mode="json")
        return agent_profiles.profiles[0].model_dump(mode="json")

    def _merge_agent_profile_payload(
        self,
        base: dict[str, Any],
        patch: dict[str, Any],
        *,
        selected_project: Any | None,
    ) -> dict[str, Any]:
        merged = self._deep_merge_dicts(base, patch)
        if (
            str(merged.get("scope", "")).strip().lower() == "project"
            and selected_project is not None
        ):
            merged.setdefault("project_id", selected_project.project_id)
        return merged

    # ── env / credential / runtime helpers ───────────────────────

    def _credential_store(self) -> CredentialStore:
        return CredentialStore(store_path=self._ctx.project_root / "auth-profiles.json")

    def _env_file_values(self, path: Path) -> dict[str, str]:
        if not path.exists():
            return {}
        values: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key:
                values[key] = value
        return values

    def _write_env_values(self, path: Path, updates: Mapping[str, str]) -> None:
        normalized = {
            str(key).strip(): str(value)
            for key, value in updates.items()
            if str(key).strip() and str(value).strip()
        }
        if not normalized:
            return
        existing_lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        rendered: list[str] = []
        seen_keys: set[str] = set()
        for line in existing_lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in line:
                rendered.append(line)
                continue
            key, _ = line.split("=", 1)
            env_name = key.strip()
            if env_name in normalized:
                rendered.append(f"{env_name}={normalized[env_name]}")
                seen_keys.add(env_name)
            else:
                rendered.append(line)
        for env_name, value in normalized.items():
            if env_name not in seen_keys:
                rendered.append(f"{env_name}={value}")
        path.parent.mkdir(parents=True, exist_ok=True)
        content = "\n".join(rendered).rstrip()
        path.write_text(f"{content}\n" if content else "", encoding="utf-8")
        path.chmod(0o600)

    async def _safe_secret_audit(self, project_ref: str | None) -> Any | None:
        try:
            return await SecretService(
                self._ctx.project_root,
                store_group=self._stores,
            ).audit(project_ref=project_ref)
        except Exception:
            return None

    @staticmethod
    def _format_config_validation_errors(exc: ValidationError) -> list[str]:
        messages: list[str] = []
        for item in exc.errors():
            loc = ".".join(str(part) for part in item.get("loc", ()))
            message = str(item.get("msg", "")).strip()
            if loc and message:
                messages.append(f"{loc}: {message}")
            elif message:
                messages.append(message)
        return messages or [str(exc)]

    def _collect_memory_alias_risks(
        self,
        *,
        config: Mapping[str, Any],
        config_ref: ControlPlaneResourceRef,
    ) -> list[SetupRiskItem]:
        memory_cfg = config.get("memory", {}) if isinstance(config.get("memory"), dict) else {}
        model_aliases = (
            config.get("model_aliases", {}) if isinstance(config.get("model_aliases"), dict) else {}
        )
        providers_raw = config.get("providers", []) if isinstance(config.get("providers"), list) else []
        providers_by_id = {
            str(item.get("id", "")).strip(): item
            for item in providers_raw
            if isinstance(item, dict) and str(item.get("id", "")).strip()
        }
        memory_bindings = [
            ("reasoning_model_alias", "记忆加工", "main（默认）"),
            ("expand_model_alias", "查询扩写", "main（默认）"),
            ("embedding_model_alias", "语义检索", "内建 embedding"),
            ("rerank_model_alias", "结果重排", "heuristic（默认）"),
        ]
        risks: list[SetupRiskItem] = []
        for field_name, label, fallback_label in memory_bindings:
            alias_name = str(memory_cfg.get(field_name, "")).strip()
            if not alias_name:
                continue
            alias_payload = model_aliases.get(alias_name)
            field_path = f"memory.{field_name}"
            if not isinstance(alias_payload, dict):
                risks.append(
                    SetupRiskItem(
                        risk_id=f"memory_alias_missing:{field_path}",
                        severity="high",
                        title=f"{label} 模型别名不存在",
                        summary=(
                            f"{field_path} 当前填写为 {alias_name}，"
                            "但 model_aliases 中找不到这个 alias。"
                        ),
                        blocking=True,
                        recommended_action=(
                            f"把 {field_path} 改成已有 alias，"
                            f"或先补齐 {alias_name} 的 model_aliases 配置。"
                        ),
                        source_ref=config_ref,
                    )
                )
                continue
            provider_id = str(alias_payload.get("provider", "")).strip()
            provider_payload = providers_by_id.get(provider_id)
            if provider_payload is None or provider_payload.get("enabled", True) is False:
                risks.append(
                    SetupRiskItem(
                        risk_id=f"memory_alias_provider_unavailable:{field_path}",
                        severity="warning",
                        title=f"{label} 当前会回退",
                        summary=(
                            f"{field_path} 绑定的 alias {alias_name} 引用的 Provider "
                            f"{provider_id or '(未填写)'} 当前不可用，"
                            f"运行时会回退到 {fallback_label}。"
                        ),
                        blocking=False,
                        recommended_action=(
                            f"启用或修正 alias {alias_name} 对应的 Provider，"
                            f"否则 Memory 会继续回退到 {fallback_label}。"
                        ),
                        source_ref=config_ref,
                    )
                )
        return risks

    def _collect_provider_runtime_details(
        self,
        config_value: Mapping[str, Any],
        *,
        secret_audit: Any | None,
        bridge_refs: list[dict[str, Any]],
        litellm_sync_ok: bool,
    ) -> dict[str, Any]:
        providers = [
            item for item in config_value.get("providers", []) if isinstance(item, dict)
        ]
        env_litellm = self._env_file_values(self._ctx.project_root / ".env.litellm")
        env_runtime = self._env_file_values(self._ctx.project_root / ".env")
        profiles = self._credential_store().list_profiles()
        oauth_profile = next(
            (profile for profile in profiles if profile.provider == "openai-codex"),
            None,
        )
        return {
            "enabled_provider_ids": [
                item.get("id", "") for item in providers if item.get("enabled", True)
            ],
            "provider_entries": providers,
            "model_aliases": sorted(config_value.get("model_aliases", {}).keys()),
            "litellm_sync_ok": litellm_sync_ok,
            "bridge_ref_count": len(bridge_refs),
            "secret_audit_status": secret_audit.overall_status if secret_audit else "unknown",
            "litellm_env_names": sorted(env_litellm.keys()),
            "runtime_env_names": sorted(env_runtime.keys()),
            "credential_profiles": [
                {
                    "name": profile.name,
                    "provider": profile.provider,
                    "auth_mode": profile.auth_mode,
                    "is_default": profile.is_default,
                    "expires_at": (
                        profile.credential.expires_at.isoformat()
                        if hasattr(profile.credential, "expires_at")
                        and getattr(profile.credential, "expires_at", None) is not None
                        else ""
                    ),
                    "account_id": (
                        str(getattr(profile.credential, "account_id", "") or "")
                    ),
                }
                for profile in profiles
            ],
            "openai_oauth_connected": oauth_profile is not None,
            "openai_oauth_profile": oauth_profile.name if oauth_profile is not None else "",
        }

    async def _collect_bridge_refs(self) -> list[dict[str, Any]]:
        from octoagent.core.models import ProjectBindingType

        project = await self._stores.project_store.get_default_project()
        if project is None:
            return []
        bindings = await self._stores.project_store.list_bindings(project.project_id)
        results: list[dict[str, Any]] = []
        for binding in bindings:
            if binding.binding_type not in {
                ProjectBindingType.ENV_REF,
                ProjectBindingType.ENV_FILE,
            }:
                continue
            results.append(binding.model_dump(mode="json"))
        return results

    # ── runtime activation ───────────────────────────────────────

    async def _activate_runtime_after_config_change(
        self,
        *,
        request: ActionRequestEnvelope,
        failure_code: str,
        failure_prefix: str,
        raise_on_failure: bool,
    ) -> dict[str, Any]:
        """激活 LiteLLM Proxy，并在托管实例中安排 runtime reload。"""
        activation_service = _cp_pkg.RuntimeActivationService(self._ctx.project_root)
        try:
            activation = await activation_service.start_proxy()
        except _cp_pkg.RuntimeActivationError as exc:
            if raise_on_failure:
                raise self._action_error(
                    failure_code,
                    f"{failure_prefix}：{exc}",
                ) from exc
            return {
                "project_root": str(self._ctx.project_root),
                "source_root": "",
                "compose_file": "",
                "proxy_url": "",
                "managed_runtime": activation_service.has_managed_runtime(),
                "warnings": [str(exc)],
                "runtime_reload_mode": "activation_failed",
                "runtime_reload_message": f"{failure_prefix}：{exc}",
                "activation_succeeded": False,
            }

        activation_data: dict[str, Any] = {
            "project_root": activation.project_root,
            "source_root": activation.source_root,
            "compose_file": activation.compose_file,
            "proxy_url": activation.proxy_url,
            "managed_runtime": activation.managed_runtime,
            "warnings": list(activation.warnings),
            "runtime_reload_mode": "none",
            "runtime_reload_message": "真实模型连接已准备完成。",
            "activation_succeeded": True,
        }

        update_service = self._ctx.update_service
        if activation.managed_runtime and update_service is not None:
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
                    "已启动 LiteLLM Proxy，当前实例会在几秒内自动重启并切到真实模型。"
                )
        else:
            activation_data["runtime_reload_mode"] = "manual_restart_required"
            activation_data["runtime_reload_message"] = (
                "LiteLLM Proxy 已启动；如果当前 Gateway 正在运行，请手动重启后再开始真实对话。"
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
