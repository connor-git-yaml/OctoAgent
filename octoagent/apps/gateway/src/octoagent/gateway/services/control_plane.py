"""Feature 026: Control Plane canonical producer。"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import structlog
from octoagent.core.behavior_workspace import (
    check_behavior_file_budget,
    ensure_filesystem_skeleton,
    materialize_agent_behavior_files,
    resolve_behavior_agent_slug,
    validate_behavior_file_path,
)
from octoagent.core.models import (
    A2AConversationItem,
    A2AMessageItem,
    ActionDefinition,
    ActionRegistryDocument,
    ActionRequestEnvelope,
    ActionResultEnvelope,
    ActorType,
    AgentProfile,
    AgentProfileItem,
    AgentProfileScope,
    AgentProfilesDocument,
    AgentRuntime,
    AgentRuntimeItem,
    AgentRuntimeRole,
    AgentRuntimeStatus,
    AgentSession,
    AgentSessionContinuityItem,
    AgentSessionKind,
    AgentSessionStatus,
    AutomationJob,
    AutomationJobDocument,
    AutomationJobItem,
    AutomationJobRun,
    AutomationJobStatus,
    AutomationScheduleKind,
    BootstrapSessionDocument,
    CapabilityPackDocument,
    ConfigFieldHint,
    ConfigSchemaDocument,
    ContextContinuityDocument,
    ContextFrameItem,
    ContextSessionItem,
    ControlPlaneActionStatus,
    ControlPlaneActor,
    ControlPlaneCapability,
    ControlPlaneDegradedState,
    ControlPlaneEvent,
    ControlPlaneEventType,
    ControlPlaneResourceRef,
    ControlPlaneState,
    ControlPlaneSupportStatus,
    ControlPlaneSurface,
    ControlPlaneTargetRef,
    CorpusKind,
    DelegationPlaneDocument,
    DelegationTargetKind,
    DiagnosticsFailureSummary,
    DiagnosticsSubsystemStatus,
    DiagnosticsSummaryDocument,
    DynamicToolSelection,
    Event,
    EventCausality,
    EventType,
    McpProviderCatalogDocument,
    McpProviderItem,
    MemoryConsoleDocument,
    MemoryNamespaceItem,
    MemoryProposalAuditDocument,
    MemorySubjectHistoryDocument,
    NormalizedMessage,
    OperatorActionKind,
    OperatorActionRequest,
    OperatorActionSource,
    OwnerProfileDocument,
    PipelineRunItem,
    PolicyProfileItem,
    PolicyProfilesDocument,
    Project,
    ProjectBindingType,
    ProjectOption,
    ProjectSelectorDocument,
    ProjectSelectorState,
    RecallFrameItem,
    RetrievalPlatformDocument,
    SessionProjectionDocument,
    SessionProjectionItem,
    SessionProjectionSummary,
    SetupGovernanceDocument,
    SetupGovernanceSection,
    SetupReviewSummary,
    SetupRiskItem,
    SkillGovernanceDocument,
    SkillGovernanceItem,
    SkillPipelineDocument,
    Task,
    TaskPointers,
    TaskStatus,
    UpdateTriggerSource,
    VaultAuthorizationDocument,
    WizardSessionDocument,
    WizardStepDocument,
    Work,
    WorkerProfile,
    WorkerProfileDynamicContext,
    WorkerProfileOriginKind,
    WorkerProfileRevision,
    WorkerProfileRevisionItem,
    WorkerProfileRevisionsDocument,
    WorkerProfilesDocument,
    WorkerProfileStaticConfig,
    WorkerProfileStatus,
    WorkerProfileViewItem,
    WorkProjectionItem,
    Workspace,
    WorkspaceOption,
    TurnExecutorKind,
)
from octoagent.core.models.payloads import ControlPlaneAuditPayload
from octoagent.core.models.task import RequesterInfo
from octoagent.memory import (
    EvidenceRef,
    MemoryLayer,
    MemoryMaintenanceCommandKind,
    MemoryPartition,
    ProposalStatus,
    VaultAccessDecision,
)
from octoagent.policy import DEFAULT_PROFILE, PERMISSIVE_PROFILE, STRICT_PROFILE, PolicyProfile
from octoagent.provider.auth.credentials import ApiKeyCredential
from octoagent.provider.auth.environment import detect_environment
from octoagent.provider.auth.oauth_flows import run_auth_code_pkce_flow
from octoagent.provider.auth.oauth_provider import OAuthProviderRegistry
from octoagent.provider.auth.profile import ProviderProfile
from octoagent.provider.auth.store import CredentialStore
from octoagent.provider.dx.automation_store import AutomationStore
from octoagent.provider.dx.backup_service import BackupService
from octoagent.provider.dx.chat_import_service import ChatImportService
from octoagent.provider.dx.config_schema import OctoAgentConfig
from octoagent.provider.dx.config_wizard import load_config, save_config
from octoagent.provider.dx.control_plane_state import ControlPlaneStateStore
from octoagent.provider.dx.import_workbench_models import (
    ImportRunDocument,
    ImportSourceDocument,
    ImportWorkbenchDocument,
)
from octoagent.provider.dx.import_workbench_service import (
    ImportWorkbenchError,
    ImportWorkbenchService,
)
from octoagent.provider.dx.litellm_generator import (
    check_litellm_sync_status,
    generate_litellm_config,
)
from octoagent.provider.dx.memory_console_service import (
    MemoryConsoleError,
    MemoryConsoleService,
)
from octoagent.provider.dx.memory_retrieval_profile import load_memory_retrieval_profile
from octoagent.provider.dx.onboarding_service import OnboardingService
from octoagent.provider.dx.retrieval_platform_service import (
    RetrievalPlatformError,
    RetrievalPlatformService,
)
from octoagent.provider.dx.runtime_activation import (
    RuntimeActivationError,
    RuntimeActivationService,
)
from octoagent.provider.dx.secret_service import SecretService
from pydantic import SecretStr, ValidationError
from ulid import ULID

from .agent_context import build_projected_session_id, build_scope_aware_session_id
from .butler_behavior import build_behavior_system_summary
from .connection_metadata import (
    merge_control_metadata,
    resolve_explicit_delegation_target_profile_id,
    resolve_explicit_session_owner_profile_id,
    resolve_turn_executor_kind,
)
from .mcp_registry import McpServerConfig
from .startup_bootstrap import (
    ensure_butler_runtime_and_session,
    ensure_default_project_agent_profile,
)
from .task_service import TaskService

_AUDIT_TASK_ID = "ops-control-plane"
_AUDIT_TRACE_ID = "trace-ops-control-plane"
_POLICY_TASK_ID = "system"
_POLICY_TRACE_ID = "trace-policy-engine"
_LEGACY_CONTEXT_POLLUTED_FLAG = "legacy_context_polluted"
_LEGACY_CONTEXT_POLLUTED_MESSAGE = (
    "这条历史会话仍沿用旧版 profile 继承语义，建议先重置 continuity，再继续新的对话。"
)
_TERMINAL_WORK_STATUSES = {"succeeded", "failed", "cancelled", "merged", "timed_out", "deleted"}
log = structlog.get_logger()


class ControlPlaneActionError(RuntimeError):
    """control-plane 动作执行异常。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ControlPlaneService:
    """对外提供 canonical control-plane resources / actions / events。"""

    def __init__(
        self,
        *,
        project_root: Path,
        store_group,
        sse_hub=None,
        task_runner=None,
        operator_action_service=None,
        operator_inbox_service=None,
        telegram_state_store=None,
        update_status_store=None,
        update_service=None,
        memory_console_service: MemoryConsoleService | None = None,
        capability_pack_service=None,
        delegation_plane_service=None,
        import_workbench_service: ImportWorkbenchService | None = None,
        policy_engine=None,
    ) -> None:
        self._project_root = project_root
        self._stores = store_group
        self._sse_hub = sse_hub
        self._task_runner = task_runner
        self._operator_action_service = operator_action_service
        self._operator_inbox_service = operator_inbox_service
        self._telegram_state_store = telegram_state_store
        self._update_status_store = update_status_store
        self._update_service = update_service
        self._memory_console_service = memory_console_service or MemoryConsoleService(
            project_root,
            store_group=store_group,
        )
        self._retrieval_platform_service = RetrievalPlatformService(
            project_root,
            store_group=store_group,
        )
        self._capability_pack_service = capability_pack_service
        self._delegation_plane_service = delegation_plane_service
        self._import_workbench_service = import_workbench_service or ImportWorkbenchService(
            project_root,
            surface="web",
            store_group=store_group,
        )
        self._policy_engine = policy_engine
        self._state_store = ControlPlaneStateStore(project_root)
        self._automation_store = AutomationStore(project_root)
        self._automation_scheduler = None
        self._registry = self._build_registry()
        self._audit_task_ensured = False

    @property
    def automation_store(self) -> AutomationStore:
        return self._automation_store

    def bind_automation_scheduler(self, scheduler: Any) -> None:
        self._automation_scheduler = scheduler

    async def ensure_system_automation_jobs(self) -> None:
        """确保系统内置的自动化作业已注册（Feature 065）。

        由 app startup 流程在 scheduler.startup() 之前调用。
        分离到独立方法避免在 bind_automation_scheduler 中产生副作用。

        启动时如果发现有未整理的 fragment，立即触发一次 consolidate（不等 4h）。
        如果上一次 consolidate 进程崩溃（通过检查 consolidated_at 标记的中间状态），
        同样触发恢复。
        """
        self._ensure_system_consolidate_job()
        self._ensure_system_profile_generate_job()

        # 启动时检查是否有积压的未整理 fragment，有则立即触发
        import asyncio
        asyncio.create_task(self._startup_consolidate_if_pending())

    def _ensure_system_consolidate_job(self) -> None:
        """确保 system:memory-consolidate 定时作业存在（Feature 065）。

        在系统首次启动时自动创建，后续启动跳过（已持久化）。
        用户可通过管理台调整间隔或禁用。
        """
        job_id = "system:memory-consolidate"
        existing = self._automation_store.get_job(job_id)
        if existing is not None:
            return

        try:
            job = AutomationJob(
                job_id=job_id,
                name="Memory Consolidate (定期整理)",
                action_id="memory.consolidate",
                params={},
                schedule_kind=AutomationScheduleKind.CRON,
                schedule_expr="0 */4 * * *",
                timezone="UTC",
                enabled=True,
            )
            self._automation_store.save_job(job)
            log.info(
                "system_job_registered",
                job_id=job_id,
                schedule_expr="0 */4 * * *",
            )
        except Exception as exc:
            log.warning(
                "system_job_registration_failed",
                job_id=job_id,
                error=str(exc),
            )

    async def _startup_consolidate_if_pending(self) -> None:
        """启动时检查未整理 fragment，有则立即触发 consolidate。

        也用于崩溃恢复：如果上一次 consolidate 进程在 commit 前崩溃，
        这些 fragment 的 consolidated_at 仍为空，重启后会被重新处理。
        consolidated_at 标记在 commit 成功后才写入，保证了幂等性。
        """
        try:
            # 延迟 5 秒等待其他服务就绪
            import asyncio
            await asyncio.sleep(5)

            result = await self._memory_console_service.run_consolidate()
            pending = result.get("consolidated_count", 0)
            if pending > 0:
                log.info(
                    "startup_consolidate_completed",
                    consolidated=pending,
                    skipped=result.get("skipped_count", 0),
                )
            else:
                log.debug("startup_consolidate_nothing_pending")
        except Exception as exc:
            log.warning(
                "startup_consolidate_failed",
                error_type=type(exc).__name__,
                error=str(exc),
            )

    def _ensure_system_profile_generate_job(self) -> None:
        """确保 system:memory-profile-generate 定时作业存在（Feature 065 Phase 3, US-9）。

        每天凌晨 2 点 UTC 自动聚合生成用户画像。
        """
        job_id = "system:memory-profile-generate"
        existing = self._automation_store.get_job(job_id)
        if existing is not None:
            return

        try:
            job = AutomationJob(
                job_id=job_id,
                name="Memory Profile Generate (用户画像)",
                action_id="memory.profile_generate",
                params={},
                schedule_kind=AutomationScheduleKind.CRON,
                schedule_expr="0 2 * * *",
                timezone="UTC",
                enabled=True,
            )
            self._automation_store.save_job(job)
            log.info(
                "system_job_registered",
                job_id=job_id,
                schedule_expr="0 2 * * *",
            )
        except Exception as exc:
            log.warning(
                "system_job_registration_failed",
                job_id=job_id,
                error=str(exc),
            )

    def bind_mcp_installer(self, installer: Any) -> None:
        """绑定 McpInstallerService（Feature 058: MCP 安装生命周期）。"""
        self._mcp_installer = installer

    async def _sync_web_project_selector_state(
        self,
        *,
        project,
        workspace,
        source: str,
        warnings: list[str] | None = None,
    ) -> None:
        await self._stores.project_store.save_selector_state(
            ProjectSelectorState(
                selector_id="selector-web",
                surface="web",
                active_project_id=project.project_id,
                active_workspace_id=workspace.workspace_id if workspace else None,
                source=source,
                warnings=list(warnings or []),
                updated_at=datetime.now(tz=UTC),
            )
        )

    async def get_snapshot(self) -> dict[str, Any]:
        registry = self.get_action_registry()
        resources: dict[str, Any] = {}
        degraded_sections: list[str] = []
        resource_errors: dict[str, dict[str, str]] = {}
        resolvers: tuple[tuple[str, Any], ...] = (
            ("wizard", self.get_wizard_session),
            ("config", self.get_config_schema),
            ("project_selector", self.get_project_selector),
            ("sessions", self.get_session_projection),
            ("agent_profiles", self.get_agent_profiles_document),
            ("worker_profiles", self.get_worker_profiles_document),
            ("owner_profile", self.get_owner_profile_document),
            ("bootstrap_session", self.get_bootstrap_session_document),
            ("context_continuity", self.get_context_continuity_document),
            ("policy_profiles", self.get_policy_profiles_document),
            ("capability_pack", self.get_capability_pack_document),
            ("skill_governance", self.get_skill_governance_document),
            ("mcp_provider_catalog", self.get_mcp_provider_catalog_document),
            ("setup_governance", self.get_setup_governance_document),
            ("delegation", self.get_delegation_document),
            ("pipelines", self.get_skill_pipeline_document),
            ("automation", self.get_automation_document),
            ("diagnostics", self.get_diagnostics_summary),
            ("retrieval_platform", self.get_retrieval_platform_document),
            ("memory", self.get_memory_console),
            ("imports", self.get_import_workbench),
        )
        for section, resolver in resolvers:
            try:
                document = await resolver()
                resources[section] = document.model_dump(mode="json", by_alias=True)
            except Exception as exc:  # pragma: no cover - 通过 API 测试覆盖
                log.warning(
                    "control_plane_snapshot_section_failed",
                    section=section,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    exc_info=True,
                )
                degraded_sections.append(section)
                resource_errors[section] = {
                    "code": "SNAPSHOT_SECTION_UNAVAILABLE",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
                resources[section] = self._degraded_snapshot_resource(
                    section=section,
                    error_type=type(exc).__name__,
                    message=str(exc),
                )
        return {
            "status": "degraded" if degraded_sections else "ready",
            "contract_version": registry.contract_version,
            "resources": resources,
            "registry": registry.model_dump(mode="json", by_alias=True),
            "degraded_sections": degraded_sections,
            "resource_errors": resource_errors,
            "generated_at": datetime.now(tz=UTC).isoformat(),
        }

    @staticmethod
    def _degraded_snapshot_resource(
        *,
        section: str,
        error_type: str,
        message: str,
    ) -> dict[str, Any]:
        return {
            "resource_type": f"{section}_unavailable",
            "status": "degraded",
            "warnings": [f"{section} snapshot unavailable"],
            "capabilities": [],
            "degraded": {
                "is_degraded": True,
                "reasons": ["snapshot_section_unavailable"],
            },
            "error": {
                "type": error_type,
                "message": message,
            },
        }

    def get_action_registry(self) -> ActionRegistryDocument:
        return self._registry

    def get_action_definition(self, action_id: str) -> ActionDefinition | None:
        return next(
            (item for item in self._registry.actions if item.action_id == action_id),
            None,
        )

    def build_telegram_action_request(
        self,
        text: str,
        *,
        actor_id: str,
        actor_label: str,
    ) -> ActionRequestEnvelope | None:
        raw = text.strip()
        if not raw.startswith("/"):
            return None

        parts = raw.split()
        if not parts:
            return None

        action_id = ""
        params: dict[str, Any] = {}
        command = parts[0].lower()
        if command == "/status" and self._has_telegram_alias("diagnostics.refresh", "/status"):
            action_id = "diagnostics.refresh"
        elif (
            command == "/project"
            and len(parts) >= 3
            and parts[1].lower() == "select"
            and self._has_telegram_alias("project.select", "/project select")
        ):
            action_id = "project.select"
            params["project_id"] = parts[2]
            if len(parts) >= 4:
                params["workspace_id"] = parts[3]
        elif (
            command == "/approve"
            and len(parts) >= 3
            and self._has_telegram_alias("operator.approval.resolve", "/approve")
        ):
            action_id = "operator.approval.resolve"
            params["approval_id"] = parts[1]
            params["mode"] = parts[2]
        elif (
            command == "/cancel"
            and len(parts) >= 2
            and self._has_telegram_alias("session.interrupt", "/cancel")
        ):
            action_id = "session.interrupt"
            params["task_id"] = parts[1]
        elif (
            command == "/retry"
            and len(parts) >= 2
            and self._has_telegram_alias("operator.task.retry", "/retry")
        ):
            action_id = "operator.task.retry"
            params["item_id"] = f"task:{parts[1]}"
        elif command == "/backup" and self._has_telegram_alias("backup.create", "/backup"):
            action_id = "backup.create"
            if len(parts) >= 2:
                params["label"] = " ".join(parts[1:])
        elif (
            command == "/update"
            and len(parts) >= 2
            and self._has_telegram_alias(
                f"update.{parts[1].lower().replace('-', '_')}", f"/update {parts[1].lower()}"
            )
        ):
            mode = parts[1].lower()
            if mode == "dry-run":
                action_id = "update.dry_run"
            elif mode == "apply":
                action_id = "update.apply"
        elif (
            command == "/automation"
            and len(parts) >= 3
            and parts[1].lower() == "run"
            and self._has_telegram_alias("automation.run", "/automation run")
        ):
            action_id = "automation.run"
            params["job_id"] = parts[2]
        elif (
            command == "/work"
            and len(parts) >= 3
            and parts[1].lower() == "cancel"
            and self._has_telegram_alias("work.cancel", "/work cancel")
        ):
            action_id = "work.cancel"
            params["work_id"] = parts[2]
        elif (
            command == "/work"
            and len(parts) >= 3
            and parts[1].lower() == "retry"
            and self._has_telegram_alias("work.retry", "/work retry")
        ):
            action_id = "work.retry"
            params["work_id"] = parts[2]
        elif (
            command == "/work"
            and len(parts) >= 3
            and parts[1].lower() == "delete"
            and self._has_telegram_alias("work.delete", "/work delete")
        ):
            action_id = "work.delete"
            params["work_id"] = parts[2]
        elif (
            command == "/work"
            and len(parts) >= 3
            and parts[1].lower() == "escalate"
            and self._has_telegram_alias("work.escalate", "/work escalate")
        ):
            action_id = "work.escalate"
            params["work_id"] = parts[2]
        elif (
            command == "/pipeline"
            and len(parts) >= 3
            and parts[1].lower() == "resume"
            and self._has_telegram_alias("pipeline.resume", "/pipeline resume")
        ):
            action_id = "pipeline.resume"
            params["work_id"] = parts[2]
        elif (
            command == "/pipeline"
            and len(parts) >= 3
            and parts[1].lower() == "retry"
            and self._has_telegram_alias("pipeline.retry_node", "/pipeline retry")
        ):
            action_id = "pipeline.retry_node"
            params["work_id"] = parts[2]

        if not action_id:
            return None

        return ActionRequestEnvelope(
            request_id=str(ULID()),
            action_id=action_id,
            params=params,
            surface=ControlPlaneSurface.TELEGRAM,
            actor=ControlPlaneActor(
                actor_id=actor_id,
                actor_label=actor_label,
            ),
            context={"raw_text": raw},
        )

    async def get_wizard_session(self) -> WizardSessionDocument:
        onboarding = OnboardingService(self._project_root)
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

    async def get_config_schema(self) -> ConfigSchemaDocument:
        config = load_config(self._project_root)
        if config is None:
            config = OctoAgentConfig(updated_at=date.today().isoformat())
        schema = OctoAgentConfig.model_json_schema()
        ui_hints = self._build_config_ui_hints()
        sync_ok, diffs = check_litellm_sync_status(config, self._project_root)
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
            selected_workspace,
            fallback_reason,
        ) = await self._resolve_selection()
        projects = await self._stores.project_store.list_projects()
        available_projects: list[ProjectOption] = []
        available_workspaces: list[WorkspaceOption] = []
        for project in projects:
            workspaces = await self._stores.project_store.list_workspaces(project.project_id)
            available_projects.append(
                ProjectOption(
                    project_id=project.project_id,
                    slug=project.slug,
                    name=project.name,
                    is_default=project.is_default,
                    status=project.status.value,
                    workspace_ids=[item.workspace_id for item in workspaces],
                )
            )
            for workspace in workspaces:
                if (
                    selected_project is not None
                    and workspace.project_id == selected_project.project_id
                ):
                    available_workspaces.append(
                        WorkspaceOption(
                            workspace_id=workspace.workspace_id,
                            project_id=workspace.project_id,
                            slug=workspace.slug,
                            name=workspace.name,
                            kind=workspace.kind.value,
                            root_path=workspace.root_path,
                        )
                    )

        switch_allowed = len(available_projects) > 1 or len(available_workspaces) > 1
        warnings: list[str] = []
        if fallback_reason:
            warnings.append(fallback_reason)
        return ProjectSelectorDocument(
            current_project_id=selected_project.project_id if selected_project else "",
            current_workspace_id=selected_workspace.workspace_id if selected_workspace else "",
            default_project_id=(await self._stores.project_store.get_default_project()).project_id
            if await self._stores.project_store.get_default_project()
            else "",
            fallback_reason=fallback_reason,
            switch_allowed=switch_allowed,
            available_projects=available_projects,
            available_workspaces=available_workspaces,
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
                        ControlPlaneSupportStatus.SUPPORTED
                        if switch_allowed
                        else ControlPlaneSupportStatus.DEGRADED
                    ),
                    reason="" if switch_allowed else "当前只有 default project",
                )
            ],
        )

    async def get_session_projection(self) -> SessionProjectionDocument:
        state, selected_project, selected_workspace, _ = await self._resolve_selection()
        session_items = await self._build_session_projection_items()
        focused_session_id, focused_thread_id = self._resolve_projected_focus(
            state=state,
            session_items=session_items,
        )
        session_summary = self._build_session_projection_summary(
            session_items=session_items,
            focused_session_id=focused_session_id,
        )
        operator_summary = None
        operator_items = []
        if self._operator_inbox_service is not None:
            try:
                inbox = await self._operator_inbox_service.get_inbox()
            except Exception:  # pragma: no cover - 防御性兜底
                inbox = None
            if inbox is not None:
                operator_summary = inbox.summary
                operator_items = inbox.items
        return SessionProjectionDocument(
            focused_session_id=focused_session_id,
            focused_thread_id=focused_thread_id,
            new_conversation_token=state.new_conversation_token,
            new_conversation_project_id=state.new_conversation_project_id,
            new_conversation_workspace_id=state.new_conversation_workspace_id,
            new_conversation_agent_profile_id=state.new_conversation_agent_profile_id,
            sessions=session_items,
            summary=session_summary,
            operator_summary=operator_summary,
            operator_items=operator_items,
            capabilities=[
                ControlPlaneCapability(
                    capability_id="session.new",
                    label="新对话",
                    action_id="session.new",
                ),
                ControlPlaneCapability(
                    capability_id="session.focus",
                    label="聚焦会话",
                    action_id="session.focus",
                ),
                ControlPlaneCapability(
                    capability_id="session.unfocus",
                    label="取消聚焦",
                    action_id="session.unfocus",
                ),
                ControlPlaneCapability(
                    capability_id="session.reset",
                    label="重置 continuity",
                    action_id="session.reset",
                ),
                ControlPlaneCapability(
                    capability_id="session.export",
                    label="导出会话",
                    action_id="session.export",
                ),
            ],
        )

    async def _build_session_projection_items(
        self,
    ) -> list[SessionProjectionItem]:
        tasks = await self._stores.task_store.list_tasks()
        works = await self._stores.work_store.list_works()
        # 不按项目过滤 session context，侧边栏需要展示所有项目的活跃会话
        session_states = await self._stores.agent_context_store.list_session_contexts()
        session_state_by_id = {item.session_id: item for item in session_states}
        latest_work_by_task: dict[str, Work] = {}
        for work in works:
            current = latest_work_by_task.get(work.task_id)
            if current is None or (work.updated_at or datetime.min.replace(tzinfo=UTC)) > (
                current.updated_at or datetime.min.replace(tzinfo=UTC)
            ):
                latest_work_by_task[work.task_id] = work
        grouped: dict[str, list[tuple[Task, Any]]] = defaultdict(list)
        for task in tasks:
            if task.task_id == _AUDIT_TASK_ID:
                continue
            workspace = await self._stores.project_store.resolve_workspace_for_scope(task.scope_id)
            if workspace is None:
                continue
            latest_metadata = await self._extract_latest_user_metadata(task.task_id)
            if str(latest_metadata.get("parent_task_id", "")).strip() or str(
                latest_metadata.get("parent_work_id", "")
            ).strip():
                continue
            session_id = self._resolve_projected_session_id_for_task(
                task=task,
                workspace=workspace,
                latest_metadata=latest_metadata,
            )
            grouped[session_id].append((task, workspace))

        session_items: list[SessionProjectionItem] = []
        for session_id, entries in grouped.items():
            latest, workspace = max(entries, key=lambda item: item[0].updated_at)
            session_state = session_state_by_id.get(session_id)
            session_runtime_kind = ""
            session_runtime_owner_profile_id = ""
            if session_state is not None and session_state.agent_session_id:
                agent_session = await self._stores.agent_context_store.get_agent_session(
                    session_state.agent_session_id
                )
                if agent_session is not None:
                    session_runtime_kind = agent_session.kind.value
                    runtime = await self._stores.agent_context_store.get_agent_runtime(
                        agent_session.agent_runtime_id
                    )
                    if runtime is not None:
                        session_runtime_owner_profile_id = str(
                            runtime.worker_profile_id or runtime.agent_profile_id or ""
                        ).strip()
            execution_summary: dict[str, Any] = {}
            latest_metadata = await self._extract_latest_user_metadata(latest.task_id)
            if self._task_runner is not None:
                session = await self._task_runner.get_execution_session(latest.task_id)
                if session is not None:
                    execution_summary = {
                        "session_id": session.session_id,
                        "state": session.state.value,
                        "interactive": session.interactive,
                        "current_step": session.current_step,
                        "runtime_kind": session.metadata.get("runtime_kind", ""),
                        "work_id": session.metadata.get("work_id", ""),
                    }
            latest_message = await self._extract_latest_user_message(latest.task_id)
            latest_work = latest_work_by_task.get(latest.task_id)
            runtime_kind = str(
                execution_summary.get(
                    "runtime_kind",
                    session_runtime_kind or latest_metadata.get("target_kind", ""),
                )
            )
            (
                session_owner_profile_id,
                turn_executor_kind,
                delegation_target_profile_id,
                session_agent_profile_id,
                compatibility_flags,
                compatibility_message,
                reset_recommended,
            ) = await self._resolve_session_projection_semantics(
                latest_metadata=latest_metadata,
                latest_work=latest_work,
                runtime_kind=runtime_kind,
                fallback_owner_profile_id=session_runtime_owner_profile_id,
            )
            if (
                turn_executor_kind == TurnExecutorKind.SELF.value
                and not delegation_target_profile_id
                and session_owner_profile_id
            ):
                owner_worker_profile = (
                    await self._stores.agent_context_store.get_worker_profile(
                        session_owner_profile_id
                    )
                )
                if owner_worker_profile is not None:
                    turn_executor_kind = TurnExecutorKind.WORKER.value
            session_owner_name = await self._resolve_profile_display_name(
                session_owner_profile_id
            )
            session_items.append(
                SessionProjectionItem(
                    session_id=session_id,
                    thread_id=(session_state.thread_id if session_state is not None else "")
                    or latest.thread_id,
                    task_id=latest.task_id,
                    parent_task_id=str(latest_metadata.get("parent_task_id", "")),
                    parent_work_id=str(latest_metadata.get("parent_work_id", "")),
                    title=latest.title,
                    status=latest.status.value,
                    channel=latest.requester.channel,
                    requester_id=latest.requester.sender_id,
                    project_id=workspace.project_id,
                    workspace_id=workspace.workspace_id,
                    agent_profile_id=session_agent_profile_id,
                    session_owner_profile_id=session_owner_profile_id,
                    session_owner_name=session_owner_name,
                    turn_executor_kind=turn_executor_kind,
                    delegation_target_profile_id=delegation_target_profile_id,
                    runtime_kind=runtime_kind,
                    compatibility_flags=compatibility_flags,
                    compatibility_message=compatibility_message,
                    reset_recommended=reset_recommended,
                    lane=self._session_lane_for_status(latest.status),
                    latest_message_summary=latest_message,
                    latest_event_at=latest.updated_at,
                    execution_summary=execution_summary,
                    capabilities=self._build_session_capabilities(latest),
                    detail_refs={
                        "task": f"/tasks/{latest.task_id}",
                        "task_api": f"/api/tasks/{latest.task_id}",
                        "execution_api": f"/api/tasks/{latest.task_id}/execution",
                    },
                )
            )
        # ── 第二遍：从 agent_sessions 补充没有 task 的会话 ──
        # 多 Session 侧边栏需要展示所有活跃会话（跨项目），
        # 不能只靠 tasks 推导——新创建的 Session 可能还没有任何 task。
        existing_session_ids = {item.session_id for item in session_items}
        all_agent_sessions = await self._stores.agent_context_store.list_agent_sessions(
            limit=50,
        )
        for agent_sess in all_agent_sessions:
            if agent_sess.status != AgentSessionStatus.ACTIVE:
                continue
            if agent_sess.kind in {
                AgentSessionKind.WORKER_INTERNAL,
                AgentSessionKind.SUBAGENT_INTERNAL,
            }:
                continue
            if agent_sess.kind not in {
                AgentSessionKind.DIRECT_WORKER,
                AgentSessionKind.BUTLER_MAIN,
            }:
                continue
            projected_session_id = build_projected_session_id(
                thread_id=agent_sess.thread_id or agent_sess.agent_session_id,
                surface="web" if agent_sess.surface in ("", "chat", "web") else agent_sess.surface,
                scope_id=(
                    f"workspace:{agent_sess.workspace_id}:chat:web:{agent_sess.thread_id}"
                    if agent_sess.workspace_id and agent_sess.thread_id
                    else ""
                ),
                project_id=agent_sess.project_id,
                workspace_id=agent_sess.workspace_id,
            )
            if projected_session_id in existing_session_ids:
                continue
            # 查找项目名
            proj = await self._stores.project_store.get_project(agent_sess.project_id)
            project_name = proj.name if proj else "未命名对话"
            # 查找 agent profile
            agent_profile_id = ""
            runtime = await self._stores.agent_context_store.get_agent_runtime(
                agent_sess.agent_runtime_id
            )
            if runtime is not None:
                agent_profile_id = runtime.worker_profile_id or runtime.agent_profile_id
            owner_name = await self._resolve_profile_display_name(agent_profile_id)
            session_items.append(
                SessionProjectionItem(
                    session_id=projected_session_id,
                    thread_id=agent_sess.thread_id or agent_sess.agent_session_id,
                    task_id="",
                    parent_task_id="",
                    parent_work_id="",
                    title=project_name,
                    status="created",
                    channel="web" if agent_sess.surface in ("chat", "web", "") else agent_sess.surface,
                    requester_id="",
                    project_id=agent_sess.project_id,
                    workspace_id=agent_sess.workspace_id,
                    agent_profile_id=agent_profile_id,
                    session_owner_profile_id=agent_profile_id,
                    session_owner_name=owner_name,
                    turn_executor_kind=self._default_turn_executor_kind_for_runtime(
                        agent_sess.kind.value
                    ),
                    delegation_target_profile_id="",
                    runtime_kind=agent_sess.kind.value,
                    compatibility_flags=[],
                    compatibility_message="",
                    reset_recommended=False,
                    lane="queue",
                    latest_message_summary="",
                    latest_event_at=agent_sess.updated_at,
                    execution_summary={},
                    capabilities=[],
                    detail_refs={},
                )
            )

        session_items.sort(
            key=lambda item: item.latest_event_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )
        return session_items

    @staticmethod
    def _normalize_turn_executor_kind(
        value: TurnExecutorKind | str | None,
    ) -> str:
        if isinstance(value, TurnExecutorKind):
            return value.value
        normalized = str(value or "").strip().lower()
        if normalized in {item.value for item in TurnExecutorKind}:
            return normalized
        return ""

    @staticmethod
    def _default_turn_executor_kind_for_runtime(
        runtime_kind: str,
        *,
        target_kind: str = "",
    ) -> str:
        normalized_runtime = str(runtime_kind).strip().lower()
        normalized_target = str(target_kind).strip().lower()
        if normalized_runtime in {
            AgentSessionKind.WORKER_INTERNAL.value,
            AgentSessionKind.DIRECT_WORKER.value,
        }:
            return TurnExecutorKind.WORKER.value
        if normalized_runtime == AgentSessionKind.SUBAGENT_INTERNAL.value:
            return TurnExecutorKind.SUBAGENT.value
        if normalized_target == "subagent":
            return TurnExecutorKind.SUBAGENT.value
        if normalized_target == "worker":
            return TurnExecutorKind.WORKER.value
        return TurnExecutorKind.SELF.value

    async def _resolve_profile_display_name(self, profile_id: str) -> str:
        """从 worker_profile 或 agent_profile 解析可展示名称，跨项目。"""
        resolved = str(profile_id or "").strip()
        if not resolved:
            return ""
        worker_profile = await self._stores.agent_context_store.get_worker_profile(resolved)
        if worker_profile is not None:
            return worker_profile.name or ""
        agent_profile = await self._stores.agent_context_store.get_agent_profile(resolved)
        if agent_profile is not None:
            return agent_profile.name or ""
        return ""

    async def _is_worker_profile_id(self, profile_id: str) -> bool:
        resolved_profile_id = str(profile_id or "").strip()
        if not resolved_profile_id:
            return False
        profile = await self._stores.agent_context_store.get_worker_profile(resolved_profile_id)
        return profile is not None

    async def _resolve_session_projection_semantics(
        self,
        *,
        latest_metadata: Mapping[str, Any] | None,
        latest_work: Work | None,
        runtime_kind: str,
        fallback_owner_profile_id: str,
    ) -> tuple[str, str, str, str, list[str], str, bool]:
        explicit_owner_profile_id = resolve_explicit_session_owner_profile_id(latest_metadata)
        explicit_delegation_target_profile_id = resolve_explicit_delegation_target_profile_id(
            latest_metadata
        )
        legacy_agent_profile_id = str(
            (latest_metadata or {}).get("agent_profile_id", "")
            or (latest_work.agent_profile_id if latest_work is not None else "")
        ).strip()
        legacy_requested_worker_profile_id = str(
            (latest_metadata or {}).get("requested_worker_profile_id", "")
            or (latest_work.requested_worker_profile_id if latest_work is not None else "")
        ).strip()
        legacy_agent_is_worker = await self._is_worker_profile_id(legacy_agent_profile_id)
        legacy_requested_is_worker = await self._is_worker_profile_id(
            legacy_requested_worker_profile_id
        )

        session_owner_profile_id = (
            explicit_owner_profile_id
            or (latest_work.session_owner_profile_id if latest_work is not None else "")
            or fallback_owner_profile_id
        )
        delegation_target_profile_id = (
            explicit_delegation_target_profile_id
            or (latest_work.delegation_target_profile_id if latest_work is not None else "")
        )
        normalized_runtime_kind = str(runtime_kind or "").strip().lower()
        compatibility_flags: list[str] = []
        compatibility_message = ""
        reset_recommended = False

        if (
            not delegation_target_profile_id
            and legacy_requested_worker_profile_id
            and legacy_requested_is_worker
        ):
            if normalized_runtime_kind in {
                AgentSessionKind.WORKER_INTERNAL.value,
                AgentSessionKind.SUBAGENT_INTERNAL.value,
            } or (
                latest_work is not None
                and latest_work.target_kind in {
                    DelegationTargetKind.WORKER,
                    DelegationTargetKind.SUBAGENT,
                }
            ):
                delegation_target_profile_id = legacy_requested_worker_profile_id

        if not session_owner_profile_id:
            if normalized_runtime_kind == AgentSessionKind.DIRECT_WORKER.value:
                session_owner_profile_id = (
                    fallback_owner_profile_id
                    or legacy_agent_profile_id
                    or legacy_requested_worker_profile_id
                )
            elif legacy_agent_profile_id and not legacy_agent_is_worker:
                session_owner_profile_id = legacy_agent_profile_id
            elif legacy_requested_worker_profile_id and not legacy_requested_is_worker:
                session_owner_profile_id = legacy_requested_worker_profile_id

        turn_executor_kind = self._normalize_turn_executor_kind(
            resolve_turn_executor_kind(latest_metadata)
            or (latest_work.turn_executor_kind if latest_work is not None else None)
        )
        if not turn_executor_kind:
            turn_executor_kind = self._default_turn_executor_kind_for_runtime(
                runtime_kind,
                target_kind=latest_work.target_kind.value if latest_work is not None else "",
            )

        legacy_context_polluted = (
            normalized_runtime_kind == AgentSessionKind.BUTLER_MAIN.value
            and not explicit_owner_profile_id
            and legacy_agent_is_worker
            and not delegation_target_profile_id
        )
        if legacy_context_polluted:
            if _LEGACY_CONTEXT_POLLUTED_FLAG not in compatibility_flags:
                compatibility_flags.append(_LEGACY_CONTEXT_POLLUTED_FLAG)
            compatibility_message = _LEGACY_CONTEXT_POLLUTED_MESSAGE
            reset_recommended = True
            session_owner_profile_id = fallback_owner_profile_id or session_owner_profile_id
            turn_executor_kind = TurnExecutorKind.SELF.value

        legacy_agent_profile_id = (
            session_owner_profile_id
            or delegation_target_profile_id
            or fallback_owner_profile_id
            or legacy_agent_profile_id
        )
        return (
            session_owner_profile_id,
            turn_executor_kind,
            delegation_target_profile_id,
            legacy_agent_profile_id,
            compatibility_flags,
            compatibility_message,
            reset_recommended,
        )

    @staticmethod
    def _resolve_projected_session_id_for_task(
        *,
        task: Task,
        workspace,
        latest_metadata: Mapping[str, Any] | None,
    ) -> str:
        metadata = latest_metadata or {}
        explicit_session_id = str(metadata.get("session_id", "")).strip()
        if explicit_session_id:
            return explicit_session_id
        return build_scope_aware_session_id(
            task,
            project_id=workspace.project_id,
            workspace_id=workspace.workspace_id,
        )

    @staticmethod
    def _session_lane_for_status(status: TaskStatus) -> str:
        if status is TaskStatus.RUNNING:
            return "running"
        if status in {
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.REJECTED,
        }:
            return "history"
        return "queue"

    def _build_session_projection_summary(
        self,
        *,
        session_items: list[SessionProjectionItem],
        focused_session_id: str,
    ) -> SessionProjectionSummary:
        running_sessions = sum(1 for item in session_items if item.lane == "running")
        history_sessions = sum(1 for item in session_items if item.lane == "history")
        queued_sessions = sum(1 for item in session_items if item.lane == "queue")
        return SessionProjectionSummary(
            total_sessions=len(session_items),
            running_sessions=running_sessions,
            queued_sessions=queued_sessions,
            history_sessions=history_sessions,
            focused_sessions=1 if focused_session_id.strip() else 0,
        )

    def _resolve_projected_focus(
        self,
        *,
        state: ControlPlaneState,
        session_items: list[SessionProjectionItem],
    ) -> tuple[str, str]:
        if not session_items:
            return "", ""
        if state.new_conversation_token.strip():
            return "", ""

        focused_session_id = state.focused_session_id.strip()
        if focused_session_id:
            focused = next(
                (item for item in session_items if item.session_id == focused_session_id),
                None,
            )
            if focused is not None:
                return focused.session_id, focused.thread_id
            return "", ""

        focused_thread_id = state.focused_thread_id.strip()
        if not focused_thread_id:
            return "", ""
        matches = [item for item in session_items if item.thread_id == focused_thread_id]
        if len(matches) != 1:
            return "", ""
        return matches[0].session_id, matches[0].thread_id

    async def _list_tasks_for_projected_session(
        self,
        *,
        session_id: str,
        selected_project,
        selected_workspace,
    ) -> list[Task]:
        tasks = await self._stores.task_store.list_tasks()
        matched: list[Task] = []
        for task in tasks:
            if task.task_id == _AUDIT_TASK_ID:
                continue
            workspace = await self._stores.project_store.resolve_workspace_for_scope(task.scope_id)
            if workspace is None:
                continue
            if not self._matches_selected_scope(
                item_project_id=workspace.project_id,
                item_workspace_id=workspace.workspace_id,
                selected_project=selected_project,
                selected_workspace=selected_workspace,
            ):
                continue
            latest_metadata = await self._extract_latest_user_metadata(task.task_id)
            if (
                self._resolve_projected_session_id_for_task(
                    task=task,
                    workspace=workspace,
                    latest_metadata=latest_metadata,
                )
                == session_id
            ):
                matched.append(task)
        matched.sort(key=lambda item: item.created_at)
        return matched

    async def get_agent_profiles_document(self) -> AgentProfilesDocument:
        _, selected_project, selected_workspace, _ = await self._resolve_selection()
        # 全局加载所有 agent profiles，不按项目过滤——管理页面需要看到全部
        profiles = await self._stores.agent_context_store.list_agent_profiles()
        items = [
            AgentProfileItem(
                profile_id=profile.profile_id,
                scope=profile.scope.value,
                project_id=profile.project_id,
                name=profile.name,
                persona_summary=profile.persona_summary,
                model_alias=profile.model_alias,
                tool_profile=profile.tool_profile,
                memory_access_policy=dict(profile.memory_access_policy),
                context_budget_policy=dict(profile.context_budget_policy),
                bootstrap_template_ids=list(profile.bootstrap_template_ids),
                behavior_system=build_behavior_system_summary(
                    agent_profile=profile,
                    project_name=selected_project.name if selected_project is not None else "",
                    project_slug=selected_project.slug if selected_project is not None else "",
                    project_root=self._project_root,
                    workspace_id=(
                        selected_workspace.workspace_id if selected_workspace is not None else ""
                    ),
                    workspace_slug=(
                        selected_workspace.slug if selected_workspace is not None else ""
                    ),
                    workspace_root_path=(
                        selected_workspace.root_path if selected_workspace is not None else ""
                    ),
                ),
                metadata=dict(profile.metadata),
                resource_limits=dict(profile.resource_limits),
                updated_at=profile.updated_at,
            )
            for profile in profiles
        ]
        return AgentProfilesDocument(
            active_project_id=selected_project.project_id if selected_project is not None else "",
            active_workspace_id=(
                selected_workspace.workspace_id if selected_workspace is not None else ""
            ),
            profiles=items,
            capabilities=[
                ControlPlaneCapability(
                    capability_id="agent_profile.refresh",
                    label="刷新 Agent Profiles",
                    action_id="agent_profile.refresh",
                )
            ],
            warnings=[] if items else ["当前作用域没有可见的 agent profiles。"],
            degraded=ControlPlaneDegradedState(
                is_degraded=not bool(items),
                reasons=["agent_profiles_empty"] if not items else [],
            ),
        )

    async def get_worker_profiles_document(self) -> WorkerProfilesDocument:
        _, selected_project, selected_workspace, _ = await self._resolve_selection()
        capability_pack = await self.get_capability_pack_document()
        # 全局加载所有 worker profiles，不按项目过滤——Agents 管理页面需要看到全部
        stored_profiles = await self._stores.agent_context_store.list_worker_profiles(
            include_archived=True,
        )
        project_works: list[Work] = []
        if self._delegation_plane_service is not None:
            project_works = await self._delegation_plane_service.list_works()
        worker_profile_ids = {profile.profile_id for profile in stored_profiles}
        works_by_profile_id: dict[str, list[Work]] = defaultdict(list)
        legacy_works_by_type: dict[str, list[Work]] = defaultdict(list)
        for work in project_works:
            if work.requested_worker_profile_id:
                works_by_profile_id[work.requested_worker_profile_id].append(work)
                continue
            if (
                work.turn_executor_kind is TurnExecutorKind.WORKER
                and work.session_owner_profile_id in worker_profile_ids
                and not work.delegation_target_profile_id
            ):
                works_by_profile_id[work.session_owner_profile_id].append(work)
                continue
            legacy_works_by_type[work.selected_worker_type].append(work)

        # 预备 behavior_system 构建所需的项目/工作区上下文
        _bs_project_name = selected_project.name if selected_project is not None else ""
        _bs_project_slug = selected_project.slug if selected_project is not None else ""
        _bs_workspace_id = selected_workspace.workspace_id if selected_workspace is not None else ""
        _bs_workspace_slug = selected_workspace.slug if selected_workspace is not None else ""
        _bs_workspace_root = (
            selected_workspace.root_path if selected_workspace is not None else ""
        )

        items: list[WorkerProfileViewItem] = []
        for profile in stored_profiles:
            matched_works = sorted(
                works_by_profile_id.get(profile.profile_id, []),
                key=lambda item: item.updated_at or datetime.min.replace(tzinfo=UTC),
                reverse=True,
            )
            latest = matched_works[0] if matched_works else None
            warnings: list[str] = []
            if profile.status == WorkerProfileStatus.ARCHIVED:
                warnings.append("当前 profile 已归档，只保留审计、复制和历史追溯。")
            elif profile.active_revision == 0:
                warnings.append("当前 profile 还是草稿，还没有已发布 revision。")
            elif profile.draft_revision > profile.active_revision:
                warnings.append(
                    f"存在未发布草稿 revision {profile.draft_revision}，当前线上版本是 {profile.active_revision}。"
                )
            if latest is None:
                warnings.append("当前还没有绑定到这个 profile 的运行中 work。")

            # 为自定义 worker profile 构建 behavior_system
            agent_profile_mirror = self._build_agent_profile_from_worker_profile(
                profile=profile,
                revision=profile.active_revision or profile.draft_revision,
            )
            # 确保 agent-private 行为文件存在（lazy materialization）
            _agent_slug = resolve_behavior_agent_slug(agent_profile_mirror)
            materialize_agent_behavior_files(
                self._project_root,
                agent_slug=_agent_slug,
                agent_name=profile.name,
                is_worker_profile=True,
            )
            behavior_sys = build_behavior_system_summary(
                agent_profile=agent_profile_mirror,
                project_name=_bs_project_name,
                project_slug=_bs_project_slug,
                project_root=self._project_root,
                workspace_id=_bs_workspace_id,
                workspace_slug=_bs_workspace_slug,
                workspace_root_path=_bs_workspace_root,
            )

            items.append(
                WorkerProfileViewItem(
                    profile_id=profile.profile_id,
                    name=profile.name,
                    scope=profile.scope.value,
                    project_id=profile.project_id,
                    mode="singleton",
                    origin_kind=profile.origin_kind,
                    status=profile.status,
                    active_revision=profile.active_revision,
                    draft_revision=profile.draft_revision,
                    effective_snapshot_id=(
                        latest.effective_worker_snapshot_id
                        if latest is not None
                        else self._worker_snapshot_id(
                            profile.profile_id,
                            profile.active_revision or profile.draft_revision,
                        )
                    ),
                    editable=profile.origin_kind != WorkerProfileOriginKind.BUILTIN,
                    summary=profile.summary
                    or self._worker_profile_summary(
                        list(profile.default_tool_groups),
                        list(profile.default_tool_groups),
                    ),
                    static_config=WorkerProfileStaticConfig(
                        summary=profile.summary,
                        model_alias=profile.model_alias,
                        tool_profile=profile.tool_profile,
                        default_tool_groups=list(profile.default_tool_groups),
                        selected_tools=list(profile.selected_tools),
                        runtime_kinds=list(profile.runtime_kinds),
                        capabilities=[],
                        metadata=dict(profile.metadata),
                        resource_limits=dict(profile.resource_limits),
                    ),
                    dynamic_context=self._build_worker_dynamic_context(
                        matched_works,
                        fallback_tools=profile.selected_tools or profile.default_tool_groups,
                        fallback_project_id=(
                            selected_project.project_id if selected_project is not None else ""
                        ),
                        fallback_workspace_id=(
                            selected_workspace.workspace_id
                            if selected_workspace is not None
                            else ""
                        ),
                    ),
                    behavior_system=behavior_sys,
                    warnings=warnings,
                    capabilities=self._worker_profile_control_capabilities(profile.status),
                )
            )

        for profile in capability_pack.pack.worker_profiles:
            worker_type = profile.worker_type
            matched_works = sorted(
                [
                    *works_by_profile_id.get(f"singleton:{worker_type}", []),
                    *legacy_works_by_type.get(worker_type, []),
                ],
                key=lambda item: item.updated_at or datetime.min.replace(tzinfo=UTC),
                reverse=True,
            )
            summary = self._worker_profile_summary(profile.capabilities, profile.default_tool_groups)
            builtin_latest = matched_works[0] if matched_works else None

            # 为 builtin profile 构建 behavior_system（合成临时 AgentProfile）
            builtin_agent_profile = AgentProfile(
                profile_id=f"singleton:{worker_type}",
                scope=AgentProfileScope.SYSTEM,
                name=self._worker_profile_label(worker_type),
                model_alias=profile.default_model_alias,
                tool_profile=profile.default_tool_profile,
                metadata={},
            )
            # 确保 agent-private 行为文件存在（lazy materialization）
            _builtin_slug = resolve_behavior_agent_slug(builtin_agent_profile)
            materialize_agent_behavior_files(
                self._project_root,
                agent_slug=_builtin_slug,
                agent_name=self._worker_profile_label(worker_type),
                is_worker_profile=True,
            )
            builtin_behavior_sys = build_behavior_system_summary(
                agent_profile=builtin_agent_profile,
                project_name=_bs_project_name,
                project_slug=_bs_project_slug,
                project_root=self._project_root,
                workspace_id=_bs_workspace_id,
                workspace_slug=_bs_workspace_slug,
                workspace_root_path=_bs_workspace_root,
            )

            items.append(
                WorkerProfileViewItem(
                    profile_id=f"singleton:{worker_type}",
                    name=self._worker_profile_label(worker_type),
                    scope="system",
                    project_id="",
                    mode="singleton",
                    origin_kind=WorkerProfileOriginKind.BUILTIN,
                    status=WorkerProfileStatus.ACTIVE,
                    active_revision=1,
                    draft_revision=1,
                    effective_snapshot_id=self._worker_snapshot_id(f"singleton:{worker_type}", 1),
                    editable=False,
                    summary=summary,
                    static_config=WorkerProfileStaticConfig(
                        summary=summary,
                        model_alias=profile.default_model_alias,
                        tool_profile=profile.default_tool_profile,
                        default_tool_groups=list(profile.default_tool_groups),
                        selected_tools=[],
                        runtime_kinds=[item.value for item in profile.runtime_kinds],
                        capabilities=list(profile.capabilities),
                        metadata={},
                    ),
                    dynamic_context=self._build_worker_dynamic_context(
                        matched_works,
                        fallback_tools=profile.default_tool_groups,
                        fallback_project_id=(
                            selected_project.project_id if selected_project is not None else ""
                        ),
                        fallback_workspace_id=(
                            selected_workspace.workspace_id
                            if selected_workspace is not None
                            else ""
                        ),
                    ),
                    behavior_system=builtin_behavior_sys,
                    warnings=[] if builtin_latest is not None else ["当前还没有运行中的 work。"],
                    capabilities=self._worker_profile_control_capabilities(
                        WorkerProfileStatus.ACTIVE,
                        builtin=True,
                    ),
                )
            )

        # 收集所有项目的 default_agent_profile_id，标记每个 profile 是否为其所属项目的默认
        all_projects = await self._stores.project_store.list_projects()
        default_profile_id_set: set[str] = set()
        primary_default_profile_id = ""
        for project in all_projects:
            pid = str(project.default_agent_profile_id or "").strip()
            if pid:
                default_profile_id_set.add(pid)
                if project.is_default:
                    primary_default_profile_id = pid
        for item in items:
            if item.profile_id in default_profile_id_set:
                item.is_default_for_project = True
        # summary 中的 default_profile_id 优先用默认项目的，退化到 selected_project
        default_profile_id = (
            primary_default_profile_id
            or (selected_project.default_agent_profile_id if selected_project is not None else "")
        )
        default_profile = next(
            (item for item in items if item.profile_id == default_profile_id),
            None,
        )

        return WorkerProfilesDocument(
            active_project_id=selected_project.project_id if selected_project is not None else "",
            active_workspace_id=(
                selected_workspace.workspace_id if selected_workspace is not None else ""
            ),
            profiles=items,
            summary={
                "profile_count": len(items),
                "singleton_count": len(
                    [
                        item
                        for item in items
                        if item.origin_kind == WorkerProfileOriginKind.BUILTIN
                    ]
                ),
                "custom_count": len(
                    [
                        item
                        for item in items
                        if item.origin_kind != WorkerProfileOriginKind.BUILTIN
                    ]
                ),
                "published_count": len(
                    [item for item in items if item.status == WorkerProfileStatus.ACTIVE]
                ),
                "draft_count": len(
                    [item for item in items if item.status == WorkerProfileStatus.DRAFT]
                ),
                "archived_count": len(
                    [item for item in items if item.status == WorkerProfileStatus.ARCHIVED]
                ),
                "active_count": len(
                    [item for item in items if item.dynamic_context.active_work_count > 0]
                ),
                "attention_count": len(
                    [item for item in items if item.dynamic_context.attention_work_count > 0]
                ),
                "default_profile_id": default_profile_id,
                "default_profile_name": default_profile.name if default_profile is not None else "",
                "default_profile_scope": default_profile.scope if default_profile is not None else "",
            },
            capabilities=[
                ControlPlaneCapability(
                    capability_id="worker_profile.create",
                    label="新建 Root Agent",
                    action_id="worker_profile.create",
                )
            ],
            refs={
                "revisions_base": "/api/control/resources/worker-profile-revisions/{profile_id}"
            },
            warnings=[] if items else ["当前没有可见的 Root Agent profiles。"],
            degraded=ControlPlaneDegradedState(
                is_degraded=not bool(items),
                reasons=["worker_profiles_empty"] if not items else [],
            ),
        )

    async def get_worker_profile_revisions_document(
        self,
        profile_id: str,
    ) -> WorkerProfileRevisionsDocument:
        _, selected_project, _, _ = await self._resolve_selection()
        stored_profile = await self._stores.agent_context_store.get_worker_profile(profile_id)
        items: list[WorkerProfileRevisionItem] = []
        warnings: list[str] = []

        if stored_profile is not None:
            if (
                stored_profile.scope.value == "project"
                and selected_project is not None
                and stored_profile.project_id
                and stored_profile.project_id != selected_project.project_id
            ):
                raise ControlPlaneActionError(
                    "WORKER_PROFILE_NOT_IN_SCOPE",
                    "当前 project 不能查看这个 Root Agent profile。",
                )
            revisions = await self._stores.agent_context_store.list_worker_profile_revisions(
                profile_id
            )
            items = [
                WorkerProfileRevisionItem(
                    revision_id=item.revision_id,
                    profile_id=item.profile_id,
                    revision=item.revision,
                    change_summary=item.change_summary,
                    created_by=item.created_by,
                    created_at=item.created_at,
                    snapshot_payload=item.snapshot_payload,
                )
                for item in revisions
            ]
            if not items:
                warnings.append("当前 profile 还没有已发布 revision。")
        elif profile_id.startswith("singleton:"):
            worker_type = profile_id.split(":", 1)[1]
            capability_pack = await self.get_capability_pack_document()
            builtin = next(
                (
                    item
                    for item in capability_pack.pack.worker_profiles
                    if item.worker_type == worker_type
                ),
                None,
            )
            if builtin is not None:
                summary = self._worker_profile_summary(
                    builtin.capabilities,
                    builtin.default_tool_groups,
                )
                items = [
                    WorkerProfileRevisionItem(
                        revision_id=self._worker_snapshot_id(profile_id, 1),
                        profile_id=profile_id,
                        revision=1,
                        change_summary="内建 archetype singleton snapshot",
                        created_by="system",
                        created_at=capability_pack.generated_at,
                        snapshot_payload={
                            "profile_id": profile_id,
                            "name": self._worker_profile_label(worker_type),
                            "summary": summary,
                            "model_alias": builtin.default_model_alias,
                            "tool_profile": builtin.default_tool_profile,
                            "default_tool_groups": list(builtin.default_tool_groups),
                            "runtime_kinds": [item.value for item in builtin.runtime_kinds],
                            "capabilities": list(builtin.capabilities),
                        },
                    )
                ]
            else:
                warnings.append("当前找不到对应的内建 singleton Root Agent。")
        else:
            warnings.append("当前找不到对应的 Root Agent profile。")

        return WorkerProfileRevisionsDocument(
            resource_id=f"worker-profile-revisions:{profile_id}",
            profile_id=profile_id,
            revisions=items,
            summary={
                "profile_id": profile_id,
                "revision_count": len(items),
                "latest_revision": items[0].revision if items else 0,
            },
            capabilities=[
                ControlPlaneCapability(
                    capability_id="worker_profile.publish",
                    label="发布 Revision",
                    action_id="worker_profile.publish",
                    enabled=not profile_id.startswith("singleton:"),
                    support_status=(
                        ControlPlaneSupportStatus.SUPPORTED
                        if not profile_id.startswith("singleton:")
                        else ControlPlaneSupportStatus.DEGRADED
                    ),
                    reason="内建 archetype 不能直接发布 revision。"
                    if profile_id.startswith("singleton:")
                    else "",
                )
            ],
            warnings=warnings,
            degraded=ControlPlaneDegradedState(
                is_degraded=not bool(items),
                reasons=["worker_profile_revisions_empty"] if not items else [],
            ),
        )

    async def get_owner_profile_document(self) -> OwnerProfileDocument:
        _, selected_project, selected_workspace, _ = await self._resolve_selection()
        profiles = await self._stores.agent_context_store.list_owner_profiles()
        profile = next(
            (item for item in profiles if item.owner_profile_id == "owner-profile-default"),
            profiles[0] if profiles else None,
        )
        overlays = (
            await self._stores.agent_context_store.list_owner_overlays(
                project_id=selected_project.project_id if selected_project is not None else None
            )
            if selected_project is not None
            else []
        )
        return OwnerProfileDocument(
            active_project_id=selected_project.project_id if selected_project is not None else "",
            active_workspace_id=(
                selected_workspace.workspace_id if selected_workspace is not None else ""
            ),
            profile=profile.model_dump(mode="json") if profile is not None else {},
            overlays=[item.model_dump(mode="json") for item in overlays],
            warnings=[] if profile is not None else ["尚未创建 owner profile。"],
            degraded=ControlPlaneDegradedState(
                is_degraded=profile is None,
                reasons=["owner_profile_missing"] if profile is None else [],
            ),
        )

    async def get_bootstrap_session_document(self) -> BootstrapSessionDocument:
        _, selected_project, selected_workspace, _ = await self._resolve_selection()
        session = None
        if selected_project is not None:
            session = await self._stores.agent_context_store.get_latest_bootstrap_session(
                project_id=selected_project.project_id,
                workspace_id=(
                    selected_workspace.workspace_id if selected_workspace is not None else ""
                ),
            )
        return BootstrapSessionDocument(
            active_project_id=selected_project.project_id if selected_project is not None else "",
            active_workspace_id=(
                selected_workspace.workspace_id if selected_workspace is not None else ""
            ),
            session=session.model_dump(mode="json") if session is not None else {},
            resumable=bool(session is not None and session.status.value != "completed"),
            warnings=[] if session is not None else ["当前 project 没有 bootstrap session。"],
            degraded=ControlPlaneDegradedState(
                is_degraded=session is None,
                reasons=["bootstrap_session_missing"] if session is None else [],
            ),
        )

    async def get_context_continuity_document(self) -> ContextContinuityDocument:
        _, selected_project, selected_workspace, _ = await self._resolve_selection()
        active_project_id = selected_project.project_id if selected_project is not None else ""
        active_workspace_id = (
            selected_workspace.workspace_id if selected_workspace is not None else ""
        )
        sessions = await self._stores.agent_context_store.list_session_contexts(
            project_id=active_project_id or None,
            workspace_id=active_workspace_id or None,
        )
        frames = await self._stores.agent_context_store.list_context_frames(
            project_id=active_project_id or None,
            workspace_id=active_workspace_id or None,
            limit=20,
        )
        agent_runtimes = await self._stores.agent_context_store.list_agent_runtimes(
            project_id=active_project_id or None,
        )
        agent_sessions = await self._stores.agent_context_store.list_agent_sessions(
            project_id=active_project_id or None,
            workspace_id=active_workspace_id or None,
            limit=20,
        )
        memory_namespaces = await self._stores.agent_context_store.list_memory_namespaces(
            project_id=active_project_id or None,
        )
        recall_frames = await self._stores.agent_context_store.list_recall_frames(
            project_id=active_project_id or None,
            workspace_id=active_workspace_id or None,
            limit=20,
        )
        a2a_conversations = await self._stores.a2a_store.list_conversations(
            project_id=active_project_id or None,
            workspace_id=active_workspace_id or None,
            limit=20,
        )
        a2a_messages = []
        for conversation in a2a_conversations[:5]:
            a2a_messages.extend(
                await self._stores.a2a_store.list_messages(
                    a2a_conversation_id=conversation.a2a_conversation_id,
                    limit=20,
                )
            )
        session_items = [
            ContextSessionItem(
                session_id=item.session_id,
                agent_runtime_id=item.agent_runtime_id,
                agent_session_id=item.agent_session_id,
                thread_id=item.thread_id,
                project_id=item.project_id,
                workspace_id=item.workspace_id,
                rolling_summary=item.rolling_summary,
                last_context_frame_id=item.last_context_frame_id,
                last_recall_frame_id=item.last_recall_frame_id,
                updated_at=item.updated_at,
            )
            for item in sessions
        ]
        frame_items = [
            ContextFrameItem(
                context_frame_id=item.context_frame_id,
                task_id=item.task_id,
                session_id=item.session_id,
                agent_runtime_id=item.agent_runtime_id,
                agent_session_id=item.agent_session_id,
                project_id=item.project_id,
                workspace_id=item.workspace_id,
                agent_profile_id=item.agent_profile_id,
                recall_frame_id=item.recall_frame_id or "",
                memory_namespace_ids=list(item.memory_namespace_ids),
                recent_summary=item.recent_summary,
                memory_hit_count=len(item.memory_hits),
                memory_hits=item.memory_hits,
                memory_recall=dict(item.budget.get("memory_recall", {})),
                budget=item.budget,
                source_refs=item.source_refs,
                degraded_reason=item.degraded_reason,
                created_at=item.created_at,
            )
            for item in frames
        ]
        runtime_items = [
            AgentRuntimeItem(
                agent_runtime_id=item.agent_runtime_id,
                role=item.role.value,
                project_id=item.project_id,
                workspace_id=item.workspace_id,
                agent_profile_id=item.agent_profile_id,
                worker_profile_id=item.worker_profile_id,
                name=item.name,
                persona_summary=item.persona_summary,
                status=item.status.value,
                metadata=item.metadata,
                updated_at=item.updated_at,
            )
            for item in agent_runtimes
        ]
        agent_session_items = [
            AgentSessionContinuityItem(
                agent_session_id=item.agent_session_id,
                agent_runtime_id=item.agent_runtime_id,
                kind=item.kind.value,
                status=item.status.value,
                project_id=item.project_id,
                workspace_id=item.workspace_id,
                thread_id=item.thread_id,
                legacy_session_id=item.legacy_session_id,
                work_id=item.work_id,
                last_context_frame_id=item.last_context_frame_id,
                last_recall_frame_id=item.last_recall_frame_id,
                updated_at=item.updated_at,
            )
            for item in agent_sessions
        ]
        namespace_items = [
            MemoryNamespaceItem(
                namespace_id=item.namespace_id,
                kind=item.kind.value,
                project_id=item.project_id,
                workspace_id=item.workspace_id,
                agent_runtime_id=item.agent_runtime_id,
                name=item.name,
                description=item.description,
                memory_scope_ids=list(item.memory_scope_ids),
                updated_at=item.updated_at,
            )
            for item in memory_namespaces
        ]
        recall_items = [
            RecallFrameItem(
                recall_frame_id=item.recall_frame_id,
                agent_runtime_id=item.agent_runtime_id,
                agent_session_id=item.agent_session_id,
                context_frame_id=item.context_frame_id,
                task_id=item.task_id,
                project_id=item.project_id,
                workspace_id=item.workspace_id,
                query=item.query,
                recent_summary=item.recent_summary,
                memory_namespace_ids=list(item.memory_namespace_ids),
                memory_hit_count=len(item.memory_hits),
                degraded_reason=item.degraded_reason,
                created_at=item.created_at,
            )
            for item in recall_frames
        ]
        conversation_items = [
            A2AConversationItem(
                a2a_conversation_id=item.a2a_conversation_id,
                task_id=item.task_id,
                work_id=item.work_id,
                project_id=item.project_id,
                workspace_id=item.workspace_id,
                source_agent_runtime_id=item.source_agent_runtime_id,
                source_agent_session_id=item.source_agent_session_id,
                target_agent_runtime_id=item.target_agent_runtime_id,
                target_agent_session_id=item.target_agent_session_id,
                source_agent=item.source_agent,
                target_agent=item.target_agent,
                context_frame_id=item.context_frame_id,
                request_message_id=item.request_message_id,
                latest_message_id=item.latest_message_id,
                latest_message_type=item.latest_message_type,
                status=item.status.value,
                message_count=item.message_count,
                trace_id=item.trace_id,
                metadata=item.metadata,
                updated_at=item.updated_at,
            )
            for item in a2a_conversations
        ]
        message_items = [
            A2AMessageItem(
                a2a_message_id=item.a2a_message_id,
                a2a_conversation_id=item.a2a_conversation_id,
                message_seq=item.message_seq,
                task_id=item.task_id,
                work_id=item.work_id,
                message_type=item.message_type,
                direction=item.direction.value,
                protocol_message_id=item.protocol_message_id,
                source_agent_runtime_id=item.source_agent_runtime_id,
                source_agent_session_id=item.source_agent_session_id,
                target_agent_runtime_id=item.target_agent_runtime_id,
                target_agent_session_id=item.target_agent_session_id,
                from_agent=item.from_agent,
                to_agent=item.to_agent,
                idempotency_key=item.idempotency_key,
                payload=item.payload,
                trace=item.trace,
                metadata=item.metadata,
                created_at=item.created_at,
            )
            for item in sorted(
                a2a_messages,
                key=lambda current: (
                    current.a2a_conversation_id,
                    current.message_seq,
                    current.created_at,
                ),
            )
        ]
        return ContextContinuityDocument(
            active_project_id=active_project_id,
            active_workspace_id=active_workspace_id,
            sessions=session_items,
            frames=frame_items,
            agent_runtimes=runtime_items,
            agent_sessions=agent_session_items,
            memory_namespaces=namespace_items,
            recall_frames=recall_items,
            a2a_conversations=conversation_items,
            a2a_messages=message_items,
            capabilities=[
                ControlPlaneCapability(
                    capability_id="context.refresh",
                    label="刷新 Context",
                    action_id="context.refresh",
                )
            ],
            warnings=[] if frame_items else ["当前作用域还没有 context frames。"],
            degraded=ControlPlaneDegradedState(
                is_degraded=not bool(frame_items),
                reasons=["context_frames_empty"] if not frame_items else [],
            ),
        )

    async def get_policy_profiles_document(self) -> PolicyProfilesDocument:
        _, selected_project, selected_workspace, _ = await self._resolve_selection()
        active_profile_id, _ = self._resolve_effective_policy_profile(selected_project)
        profiles = [
            PolicyProfileItem(
                profile_id=profile_id,
                label=label,
                description=profile.description,
                allowed_tool_profile=profile.allowed_tool_profile.value,
                approval_policy=self._describe_policy_approval(profile),
                risk_level=risk_level,
                recommended_for=recommended_for,
                is_active=profile_id == active_profile_id,
            )
            for profile_id, label, profile, risk_level, recommended_for in self._policy_catalog()
        ]
        return PolicyProfilesDocument(
            active_project_id=selected_project.project_id if selected_project is not None else "",
            active_workspace_id=(
                selected_workspace.workspace_id if selected_workspace is not None else ""
            ),
            active_profile_id=active_profile_id,
            profiles=profiles,
            capabilities=[
                ControlPlaneCapability(
                    capability_id="policy_profile.select",
                    label="切换安全等级",
                    action_id="policy_profile.select",
                )
            ],
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

    def _filter_capability_pack_for_project(
        self,
        pack,
        *,
        selected_project: Any | None,
    ):
        selected_item_ids, disabled_item_ids = self._resolve_project_skill_selection(
            selected_project
        )
        if not selected_item_ids and not disabled_item_ids:
            return pack

        skills = [
            skill
            for skill in pack.skills
            if self._skill_item_selected(
                item_id=f"skill:{skill.skill_id}",
                enabled_by_default=True,
                selected_item_ids=selected_item_ids,
                disabled_item_ids=disabled_item_ids,
            )[0]
        ]
        tools = [
            tool
            for tool in pack.tools
            if (
                tool.tool_group != "mcp"
                or self._skill_item_selected(
                    item_id=(
                        "mcp:"
                        + (str(tool.metadata.get("mcp_server_name", "")).strip() or "mcp")
                    ),
                    enabled_by_default=False,
                    selected_item_ids=selected_item_ids,
                    disabled_item_ids=disabled_item_ids,
                )[0]
            )
        ]
        tool_names = {item.tool_name for item in tools}
        fallback_toolset = [tool for tool in pack.fallback_toolset if tool in tool_names]
        return pack.model_copy(
            update={
                "skills": skills,
                "tools": tools,
                "fallback_toolset": fallback_toolset,
            }
        )

    async def get_skill_governance_document(
        self,
        *,
        config_value: dict[str, Any] | None = None,
        policy_profile_id: str | None = None,
        selected_project: Any | None = None,
        selected_workspace: Any | None = None,
        draft_selection: Mapping[str, Any] | None = None,
    ) -> SkillGovernanceDocument:
        if selected_project is None and selected_workspace is None:
            _, selected_project, selected_workspace, _ = await self._resolve_selection()
        elif selected_project is None:
            _, selected_project, _, _ = await self._resolve_selection()
        if self._capability_pack_service is None:
            capability_pack = CapabilityPackDocument(
                selected_project_id=(
                    selected_project.project_id if selected_project is not None else ""
                ),
                selected_workspace_id=(
                    selected_workspace.workspace_id if selected_workspace is not None else ""
                ),
            )
        else:
            capability_pack = CapabilityPackDocument(
                pack=await self._capability_pack_service.get_pack(),
                selected_project_id=(
                    selected_project.project_id if selected_project is not None else ""
                ),
                selected_workspace_id=(
                    selected_workspace.workspace_id if selected_workspace is not None else ""
                ),
            )
        capability_snapshot = (
            self._capability_pack_service.capability_snapshot()
            if self._capability_pack_service is not None
            else {}
        )
        items: list[SkillGovernanceItem] = []
        # Feature 057: 从 SkillDiscovery 获取 Skill 列表
        if self._capability_pack_service is not None:
            for entry in self._capability_pack_service.skill_discovery.list_items():
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
        mcp_configs = {}
        mcp_registry = (
            None
            if self._capability_pack_service is None
            else self._capability_pack_service.mcp_registry
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
            active_workspace_id=(
                selected_workspace.workspace_id if selected_workspace is not None else ""
            ),
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

    async def get_mcp_provider_catalog_document(self) -> McpProviderCatalogDocument:
        _, selected_project, selected_workspace, _ = await self._resolve_selection()
        mcp_registry = (
            None
            if self._capability_pack_service is None
            else self._capability_pack_service.mcp_registry
        )
        if mcp_registry is None:
            return McpProviderCatalogDocument(
                active_project_id=selected_project.project_id if selected_project else "",
                active_workspace_id=selected_workspace.workspace_id if selected_workspace else "",
                warnings=["MCP registry 尚未绑定，无法加载 MCP providers。"],
                degraded=ControlPlaneDegradedState(
                    is_degraded=True,
                    reasons=["mcp_registry_unavailable"],
                ),
            )
        servers = {item.server_name: item for item in mcp_registry.list_servers()}
        governance = await self.get_skill_governance_document(
            selected_project=selected_project,
            selected_workspace=selected_workspace,
        )
        governance_map = {item.item_id: item for item in governance.items}

        # Feature 058: 合并安装注册表数据
        install_records: dict[str, Any] = {}
        mcp_installer = getattr(self, "_mcp_installer", None)
        if mcp_installer is not None:
            install_records = {
                r.server_id: r for r in mcp_installer.list_installs()
            }

        items: list[McpProviderItem] = []
        for config in mcp_registry.list_configs():
            record = servers.get(config.name)
            governance_item = governance_map.get(f"mcp:{config.name}")
            install = install_records.get(config.name)
            items.append(
                McpProviderItem(
                    provider_id=config.name,
                    label=config.name,
                    description=record.error if record and record.error else config.command,
                    editable=True,
                    removable=True,
                    enabled=config.enabled,
                    status=record.status if record is not None else "unconfigured",
                    command=config.command,
                    args=list(config.args),
                    cwd=config.cwd,
                    env=dict(config.env),
                    mount_policy=str(config.mount_policy).strip().lower() or "auto_readonly",
                    tool_count=record.tool_count if record is not None else 0,
                    selection_item_id=f"mcp:{config.name}",
                    install_hint=governance_item.install_hint if governance_item else "",
                    error=record.error if record is not None else "",
                    warnings=(
                        []
                        if governance_item is None
                        else list(governance_item.missing_requirements)
                    ),
                    details={
                        "discovered_at": (
                            record.discovered_at.isoformat()
                            if record is not None and record.discovered_at is not None
                            else ""
                        )
                    },
                    install_source=str(install.install_source) if install else "",
                    install_version=install.version if install else "",
                    install_path=install.install_path if install else "",
                    installed_at=(
                        install.installed_at.isoformat() if install else ""
                    ),
                )
            )
        return McpProviderCatalogDocument(
            active_project_id=selected_project.project_id if selected_project is not None else "",
            active_workspace_id=(
                selected_workspace.workspace_id if selected_workspace is not None else ""
            ),
            items=items,
            summary={
                "installed_count": len(items),
                "enabled_count": len([item for item in items if item.enabled]),
                "healthy_count": len([item for item in items if item.status == "available"]),
                "auto_installed_count": len(
                    [i for i in items if i.install_source and i.install_source != "manual"]
                ),
                "manual_count": len(
                    [i for i in items if not i.install_source or i.install_source == "manual"]
                ),
            },
            capabilities=[
                ControlPlaneCapability(
                    capability_id="mcp_provider.save",
                    label="手动添加 MCP Provider",
                    action_id="mcp_provider.save",
                ),
                ControlPlaneCapability(
                    capability_id="mcp_provider.install",
                    label="安装 MCP Provider",
                    action_id="mcp_provider.install",
                ),
                ControlPlaneCapability(
                    capability_id="mcp_provider.uninstall",
                    label="卸载 MCP Provider",
                    action_id="mcp_provider.uninstall",
                ),
            ],
            warnings=[] if items else ["当前没有已安装的 MCP providers。"],
            degraded=ControlPlaneDegradedState(
                is_degraded=not bool(items),
                reasons=["mcp_provider_catalog_empty"] if not items else [],
            ),
        )

    async def get_setup_governance_document(self) -> SetupGovernanceDocument:
        _, selected_project, selected_workspace, fallback_reason = await self._resolve_selection()
        project_selector = await self.get_project_selector()
        config = await self.get_config_schema()
        diagnostics = await self.get_diagnostics_summary()
        agent_profiles = await self.get_agent_profiles_document()
        owner_profile = await self.get_owner_profile_document()
        capability_pack = await self.get_capability_pack_document()
        policy_profiles = await self.get_policy_profiles_document()
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
            selected_workspace=selected_workspace,
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
                (
                    f"{selected_project.name} / "
                    f"{self._workspace_summary_label(selected_workspace)}"
                )
                if selected_project is not None
                else "当前还没有可用 project。"
            ),
            warnings=[fallback_reason] if fallback_reason else [],
            blocking_reasons=["project_unavailable"] if selected_project is None else [],
            details={
                "project_id": selected_project.project_id if selected_project is not None else "",
                "project_name": selected_project.name if selected_project is not None else "",
                "workspace_id": (
                    selected_workspace.workspace_id if selected_workspace is not None else ""
                ),
                "workspace_name": selected_workspace.name if selected_workspace is not None else "",
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
                    self._capability_pack_service.capability_snapshot()
                    if self._capability_pack_service is not None
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
            active_workspace_id=(
                selected_workspace.workspace_id if selected_workspace is not None else ""
            ),
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

    async def get_automation_document(self) -> AutomationJobDocument:
        # 全局展示所有自动化任务，不按项目过滤
        jobs = self._automation_store.list_jobs()
        items: list[AutomationJobItem] = []
        for job in jobs:
            runs = self._automation_store.list_runs(job.job_id, limit=1)
            degraded_reason = (
                self._automation_scheduler.get_issue(job.job_id)
                if self._automation_scheduler is not None
                else ""
            )
            next_run_at = (
                self._automation_scheduler.get_next_run(job.job_id)
                if self._automation_scheduler is not None
                else None
            )
            status = AutomationJobStatus.PAUSED if not job.enabled else AutomationJobStatus.ACTIVE
            if runs and runs[0].status == "running":
                status = AutomationJobStatus.RUNNING
            elif runs and runs[0].status in {"failed", "rejected"}:
                status = AutomationJobStatus.FAILED
            if degraded_reason:
                status = AutomationJobStatus.DEGRADED
            items.append(
                AutomationJobItem(
                    job=job,
                    status=status,
                    next_run_at=next_run_at,
                    last_run=runs[0] if runs else None,
                    supported_actions=[
                        "automation.run",
                        "automation.pause",
                        "automation.resume",
                        "automation.delete",
                    ],
                    degraded_reason=degraded_reason,
                )
            )
        return AutomationJobDocument(
            jobs=items,
            run_history_cursor=items[0].last_run.run_id if items and items[0].last_run else "",
            capabilities=[
                ControlPlaneCapability(
                    capability_id="automation.create",
                    label="创建自动化任务",
                    action_id="automation.create",
                )
            ],
        )

    async def get_capability_pack_document(self) -> CapabilityPackDocument:
        # 能力包全局加载，不按项目过滤
        if self._capability_pack_service is None:
            return CapabilityPackDocument(
                selected_project_id="",
                selected_workspace_id="",
                degraded=ControlPlaneDegradedState(
                    is_degraded=True,
                    reasons=["capability_pack_unavailable"],
                ),
                warnings=["capability pack service unavailable"],
            )
        pack = await self._capability_pack_service.get_pack(
            project_id="",
            workspace_id="",
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

    async def get_delegation_document(self) -> DelegationPlaneDocument:
        if self._delegation_plane_service is None:
            return DelegationPlaneDocument(
                degraded=ControlPlaneDegradedState(
                    is_degraded=True,
                    reasons=["delegation_plane_unavailable"],
                ),
                warnings=["delegation plane unavailable"],
            )
        works = await self._delegation_plane_service.list_works()
        child_map: dict[str, list[str]] = defaultdict(list)
        for work in works:
            if work.parent_work_id:
                child_map[work.parent_work_id].append(work.work_id)
        # 全局展示所有 works，不按项目过滤——Work 页面是全局管理视图
        items = [
            self._build_work_projection_item(work=work, works=works, child_map=child_map)
            for work in works
        ]
        summary: dict[str, Any] = {
            "total": len(items),
            "by_status": {},
            "by_worker_type": {},
        }
        for item in items:
            summary["by_status"][item.status] = summary["by_status"].get(item.status, 0) + 1
            summary["by_worker_type"][item.selected_worker_type] = (
                summary["by_worker_type"].get(item.selected_worker_type, 0) + 1
            )
        return DelegationPlaneDocument(
            works=items,
            summary=summary,
            capabilities=[
                ControlPlaneCapability(
                    capability_id="work.refresh",
                    label="刷新委派视图",
                    action_id="work.refresh",
                )
            ],
        )

    def _build_work_projection_item(
        self,
        *,
        work: Work,
        works: list[Work],
        child_map: dict[str, list[str]],
    ) -> WorkProjectionItem:
        selection = self._tool_selection_from_work(work)
        return (
            WorkProjectionItem(
                work_id=work.work_id,
                task_id=work.task_id,
                parent_work_id=work.parent_work_id or "",
                title=work.title,
                status=work.status.value,
                target_kind=work.target_kind.value,
                selected_worker_type=work.selected_worker_type,
                route_reason=work.route_reason,
                owner_id=work.owner_id,
                selected_tools=work.selected_tools,
                pipeline_run_id=work.pipeline_run_id,
                runtime_id=work.runtime_id,
                project_id=work.project_id,
                workspace_id=work.workspace_id,
                agent_profile_id=work.agent_profile_id,
                session_owner_profile_id=work.session_owner_profile_id,
                turn_executor_kind=work.turn_executor_kind.value,
                delegation_target_profile_id=work.delegation_target_profile_id,
                requested_worker_profile_id=work.requested_worker_profile_id,
                requested_worker_profile_version=work.requested_worker_profile_version,
                effective_worker_snapshot_id=work.effective_worker_snapshot_id,
                tool_resolution_mode=(
                    selection.resolution_mode
                    if selection is not None
                    else str(work.metadata.get("tool_resolution_mode", ""))
                ),
                mounted_tools=list(selection.mounted_tools) if selection is not None else [],
                blocked_tools=list(selection.blocked_tools) if selection is not None else [],
                tool_resolution_warnings=list(selection.warnings) if selection is not None else [],
                child_work_ids=child_map.get(work.work_id, []),
                child_work_count=len(child_map.get(work.work_id, [])),
                merge_ready=self._is_work_merge_ready(work, works),
                a2a_conversation_id=str(work.metadata.get("a2a_conversation_id", "")),
                butler_agent_session_id=str(work.metadata.get("source_agent_session_id", "")),
                worker_agent_session_id=str(work.metadata.get("target_agent_session_id", "")),
                a2a_message_count=int(work.metadata.get("a2a_message_count", 0) or 0),
                runtime_summary={
                    "delegation_strategy": str(work.metadata.get("delegation_strategy", "")),
                    "final_speaker": str(work.metadata.get("final_speaker", "")),
                    "requested_target_kind": str(work.metadata.get("requested_target_kind", "")),
                    "requested_worker_type": str(work.metadata.get("requested_worker_type", "")),
                    "requested_tool_profile": str(work.metadata.get("requested_tool_profile", "")),
                    "requested_worker_profile_id": work.requested_worker_profile_id,
                    "requested_worker_profile_version": work.requested_worker_profile_version,
                    "effective_worker_snapshot_id": work.effective_worker_snapshot_id,
                    "a2a_conversation_id": str(work.metadata.get("a2a_conversation_id", "")),
                    "butler_agent_session_id": str(
                        work.metadata.get("source_agent_session_id", "")
                    ),
                    "worker_agent_session_id": str(
                        work.metadata.get("target_agent_session_id", "")
                    ),
                    "a2a_message_count": int(work.metadata.get("a2a_message_count", 0) or 0),
                    "research_child_task_id": str(
                        work.metadata.get("research_child_task_id", "")
                    ),
                    "research_child_thread_id": str(
                        work.metadata.get("research_child_thread_id", "")
                    ),
                    "research_child_work_id": str(
                        work.metadata.get("research_child_work_id", "")
                    ),
                    "research_child_status": str(
                        work.metadata.get("research_child_status", "")
                    ),
                    "research_worker_status": str(
                        work.metadata.get("research_worker_status", "")
                    ),
                    "research_worker_id": str(work.metadata.get("research_worker_id", "")),
                    "research_route_reason": str(work.metadata.get("research_route_reason", "")),
                    "research_tool_profile": str(
                        work.metadata.get("research_tool_profile", "")
                    ),
                    "research_a2a_conversation_id": str(
                        work.metadata.get("research_a2a_conversation_id", "")
                    ),
                    "research_butler_agent_session_id": str(
                        work.metadata.get("research_butler_agent_session_id", "")
                    ),
                    "research_worker_agent_session_id": str(
                        work.metadata.get("research_worker_agent_session_id", "")
                    ),
                    "research_a2a_message_count": int(
                        work.metadata.get("research_a2a_message_count", 0) or 0
                    ),
                    "research_result_artifact_ref": str(
                        work.metadata.get("research_result_artifact_ref", "")
                    ),
                    "research_handoff_artifact_ref": str(
                        work.metadata.get("research_handoff_artifact_ref", "")
                    ),
                    "freshness_resolution": str(work.metadata.get("freshness_resolution", "")),
                    "freshness_degraded_reason": str(
                        work.metadata.get("freshness_degraded_reason", "")
                    ),
                    "clarification_needed": str(work.metadata.get("clarification_needed", "")),
                    "runtime_status": str(work.metadata.get("runtime_status", "")),
                },
                updated_at=work.updated_at,
                capabilities=[
                    ControlPlaneCapability(
                        capability_id="work.cancel",
                        label="取消 Work",
                        action_id="work.cancel",
                        enabled=work.status.value not in _TERMINAL_WORK_STATUSES,
                    ),
                    ControlPlaneCapability(
                        capability_id="work.retry",
                        label="重试 Work",
                        action_id="work.retry",
                        enabled=work.status.value != "deleted",
                    ),
                    ControlPlaneCapability(
                        capability_id="worker.review",
                        label="评审 Worker 方案",
                        action_id="worker.review",
                        enabled=work.status.value not in _TERMINAL_WORK_STATUSES,
                    ),
                    ControlPlaneCapability(
                        capability_id="work.split",
                        label="拆分 Work",
                        action_id="work.split",
                        enabled=work.status.value not in _TERMINAL_WORK_STATUSES,
                    ),
                    ControlPlaneCapability(
                        capability_id="work.merge",
                        label="合并 Work",
                        action_id="work.merge",
                        enabled=self._is_work_merge_ready(work, works),
                        support_status=(
                            ControlPlaneSupportStatus.SUPPORTED
                            if self._is_work_merge_ready(work, works)
                            else ControlPlaneSupportStatus.DEGRADED
                        ),
                        reason=(
                            ""
                            if self._is_work_merge_ready(work, works)
                            else "存在未完成 child works 或尚未拆分"
                        ),
                    ),
                    ControlPlaneCapability(
                        capability_id="work.delete",
                        label="删除 Work",
                        action_id="work.delete",
                        enabled=work.status.value in _TERMINAL_WORK_STATUSES
                        and work.status.value != "deleted",
                    ),
                    ControlPlaneCapability(
                        capability_id="work.escalate",
                        label="升级 Work",
                        action_id="work.escalate",
                    ),
                    ControlPlaneCapability(
                        capability_id="worker.extract_profile_from_runtime",
                        label="提炼 Root Agent",
                        action_id="worker.extract_profile_from_runtime",
                    ),
                ],
            )
        )

    async def get_skill_pipeline_document(self) -> SkillPipelineDocument:
        if self._delegation_plane_service is None:
            return SkillPipelineDocument(
                degraded=ControlPlaneDegradedState(
                    is_degraded=True,
                    reasons=["delegation_plane_unavailable"],
                ),
                warnings=["skill pipeline unavailable"],
            )
        runs = await self._delegation_plane_service.list_pipeline_runs()
        # 全局展示所有 pipeline runs，不按项目过滤
        items: list[PipelineRunItem] = []
        for run in runs:
            work = await self._stores.work_store.get_work(run.work_id)
            if work is None:
                continue
            frames = await self._delegation_plane_service.list_pipeline_replay(run.run_id)
            items.append(
                PipelineRunItem(
                    run_id=run.run_id,
                    pipeline_id=run.pipeline_id,
                    task_id=run.task_id,
                    work_id=run.work_id,
                    status=run.status.value,
                    current_node_id=run.current_node_id,
                    pause_reason=run.pause_reason,
                    retry_cursor=run.retry_cursor,
                    updated_at=run.updated_at,
                    replay_frames=frames,
                )
            )
        summary = {
            "total": len(items),
            "paused": len(
                [
                    item
                    for item in items
                    if item.status in {"waiting_input", "waiting_approval", "paused"}
                ]
            ),
            "running": len([item for item in items if item.status == "running"]),
            "source": "delegation_plane_pipeline_runs",
            "graph_runtime_projection": "unavailable",
        }
        return SkillPipelineDocument(
            runs=items,
            summary=summary,
            degraded=ControlPlaneDegradedState(
                is_degraded=True,
                reasons=["graph_runtime_projection_unavailable"],
            ),
            warnings=[
                (
                    "当前视图仅展示 delegation preflight / skill pipeline runs，"
                    "不代表 graph runtime 的真实执行步进。"
                ),
                "graph runtime 细节目前仍需通过 execution console / session steps 查看。",
            ],
            capabilities=[
                ControlPlaneCapability(
                    capability_id="pipeline.resume",
                    label="恢复 Pipeline",
                    action_id="pipeline.resume",
                ),
                ControlPlaneCapability(
                    capability_id="pipeline.retry_node",
                    label="重试节点",
                    action_id="pipeline.retry_node",
                ),
            ],
        )

    async def get_diagnostics_summary(self) -> DiagnosticsSummaryDocument:
        subsystems: list[DiagnosticsSubsystemStatus] = []
        failures: list[DiagnosticsFailureSummary] = []
        runtime_snapshot = self._load_runtime_snapshot()
        update_summary = self._load_update_summary()
        recovery_summary = (
            BackupService(
                self._project_root,
                store_group=self._stores,
            )
            .get_recovery_summary()
            .model_dump(mode="json")
        )
        channel_summary = self._build_channel_summary()
        wizard = await self.get_wizard_session()
        _, selected_project, selected_workspace, _ = await self._resolve_selection()
        project_selector = await self.get_project_selector()
        memory_backend = await self._memory_console_service.get_backend_status(
            project_id=selected_project.project_id if selected_project is not None else "",
            workspace_id=selected_workspace.workspace_id
            if selected_workspace is not None
            else None,
        )

        subsystems.append(
            DiagnosticsSubsystemStatus(
                subsystem_id="runtime",
                label="Runtime",
                status="ok" if self._task_runner is not None else "unavailable",
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

    async def get_memory_console(
        self,
        *,
        project_id: str | None = None,
        workspace_id: str | None = None,
        scope_id: str | None = None,
        partition: str | None = None,
        layer: str | None = None,
        query: str | None = None,
        include_history: bool = False,
        include_vault_refs: bool = False,
        limit: int = 50,
    ) -> MemoryConsoleDocument:
        _, selected_project, selected_workspace, fallback_reason = await self._resolve_selection()
        resolved_project_id = project_id or (
            selected_project.project_id if selected_project is not None else ""
        )
        resolved_workspace_id = workspace_id or (
            selected_workspace.workspace_id if selected_workspace is not None else None
        )
        resolved_project = (
            await self._stores.project_store.get_project(resolved_project_id)
            if resolved_project_id
            else selected_project
        )
        resolved_workspace = (
            await self._stores.project_store.get_workspace(resolved_workspace_id)
            if resolved_workspace_id
            else selected_workspace
        )
        backend_status = await self._memory_console_service.get_backend_status(
            project_id=resolved_project_id,
            workspace_id=resolved_workspace_id,
        )
        active_embedding_target, requested_embedding_target = (
            await self._retrieval_platform_service.get_memory_embedding_targets(
                project=resolved_project,
                workspace=resolved_workspace,
                backend_status=backend_status,
            )
        )
        document = await self._memory_console_service.get_memory_console(
            project_id=resolved_project_id,
            workspace_id=resolved_workspace_id,
            scope_id=scope_id,
            partition=self._parse_memory_partition(partition),
            layer=self._parse_memory_layer(layer),
            query=query,
            include_history=include_history,
            include_vault_refs=include_vault_refs,
            limit=limit,
        )
        document.retrieval_profile = load_memory_retrieval_profile(
            self._project_root,
            backend_status=backend_status,
            active_embedding_target=active_embedding_target,
            requested_embedding_target=requested_embedding_target,
        )
        if fallback_reason:
            document.warnings.append(fallback_reason)
            document.degraded.is_degraded = True
            if fallback_reason not in document.degraded.reasons:
                document.degraded.reasons.append(fallback_reason)
        return document

    async def get_retrieval_platform_document(
        self,
        *,
        project_id: str | None = None,
        workspace_id: str | None = None,
    ) -> RetrievalPlatformDocument:
        _, selected_project, selected_workspace, fallback_reason = await self._resolve_selection()
        resolved_project_id = project_id or (
            selected_project.project_id if selected_project is not None else ""
        )
        resolved_workspace_id = workspace_id or (
            selected_workspace.workspace_id if selected_workspace is not None else ""
        )
        backend_status = await self._memory_console_service.get_backend_status(
            project_id=resolved_project_id,
            workspace_id=resolved_workspace_id or None,
        )
        document = await self._retrieval_platform_service.get_document(
            active_project_id=resolved_project_id,
            active_workspace_id=resolved_workspace_id,
            backend_status=backend_status,
        )
        if fallback_reason:
            document.warnings.append(fallback_reason)
            document.degraded.is_degraded = True
            if fallback_reason not in document.degraded.reasons:
                document.degraded.reasons.append(fallback_reason)
        return document

    async def get_import_workbench(
        self,
        *,
        project_id: str | None = None,
        workspace_id: str | None = None,
    ) -> ImportWorkbenchDocument:
        _, selected_project, selected_workspace, _ = await self._resolve_selection()
        resolved_project_id = project_id or (
            selected_project.project_id if selected_project is not None else None
        )
        resolved_workspace_id = workspace_id or (
            selected_workspace.workspace_id if selected_workspace is not None else None
        )
        return await self._import_workbench_service.get_workbench(
            project_id=resolved_project_id,
            workspace_id=resolved_workspace_id,
        )

    async def get_import_source(self, source_id: str) -> ImportSourceDocument:
        return await self._get_import_source_in_scope(source_id)

    async def get_import_run(self, run_id: str) -> ImportRunDocument:
        return await self._get_import_run_in_scope(run_id)

    async def get_memory_subject_history(
        self,
        subject_key: str,
        *,
        project_id: str | None = None,
        workspace_id: str | None = None,
        scope_id: str | None = None,
    ) -> MemorySubjectHistoryDocument:
        _, selected_project, selected_workspace, _ = await self._resolve_selection()
        resolved_project_id = project_id or (
            selected_project.project_id if selected_project is not None else ""
        )
        resolved_workspace_id = workspace_id or (
            selected_workspace.workspace_id if selected_workspace is not None else None
        )
        return await self._memory_console_service.get_memory_subject_history(
            subject_key=subject_key,
            project_id=resolved_project_id,
            workspace_id=resolved_workspace_id,
            scope_id=scope_id,
        )

    async def get_memory_proposal_audit(
        self,
        *,
        project_id: str | None = None,
        workspace_id: str | None = None,
        scope_id: str | None = None,
        status: str | None = None,
        source: str | None = None,
        limit: int = 50,
    ) -> MemoryProposalAuditDocument:
        _, selected_project, selected_workspace, _ = await self._resolve_selection()
        resolved_project_id = project_id or (
            selected_project.project_id if selected_project is not None else ""
        )
        resolved_workspace_id = workspace_id or (
            selected_workspace.workspace_id if selected_workspace is not None else None
        )
        proposal_status = ProposalStatus(status) if status else None
        return await self._memory_console_service.get_proposal_audit(
            project_id=resolved_project_id,
            workspace_id=resolved_workspace_id,
            scope_id=scope_id,
            status=proposal_status,
            source=source,
            limit=limit,
        )

    async def get_vault_authorization(
        self,
        *,
        project_id: str | None = None,
        workspace_id: str | None = None,
        scope_id: str | None = None,
        subject_key: str | None = None,
    ) -> VaultAuthorizationDocument:
        _, selected_project, selected_workspace, _ = await self._resolve_selection()
        resolved_project_id = project_id or (
            selected_project.project_id if selected_project is not None else ""
        )
        resolved_workspace_id = workspace_id or (
            selected_workspace.workspace_id if selected_workspace is not None else None
        )
        return await self._memory_console_service.get_vault_authorization(
            project_id=resolved_project_id,
            workspace_id=resolved_workspace_id,
            scope_id=scope_id,
            subject_key=subject_key,
        )

    async def list_events(
        self, after: str | None = None, limit: int = 100
    ) -> list[ControlPlaneEvent]:
        await self._ensure_audit_task()
        if after:
            events = await self._stores.event_store.get_events_after(_AUDIT_TASK_ID, after)
        else:
            events = await self._stores.event_store.get_events_for_task(_AUDIT_TASK_ID)

        result: list[ControlPlaneEvent] = []
        for event in events:
            if not str(event.type.value).startswith("CONTROL_PLANE_"):
                continue
            payload = ControlPlaneAuditPayload.model_validate(event.payload)
            result.append(
                ControlPlaneEvent(
                    event_id=event.event_id,
                    contract_version=payload.contract_version,
                    event_type=ControlPlaneEventType(payload.event_type),
                    request_id=payload.request_id,
                    correlation_id=payload.correlation_id,
                    causation_id=payload.causation_id,
                    actor=ControlPlaneActor(
                        actor_id=payload.actor_id or "system:control-plane",
                        actor_label=payload.actor_label or payload.actor_id or "system",
                    ),
                    surface=ControlPlaneSurface(payload.surface),
                    occurred_at=event.ts,
                    payload_summary=payload.payload_summary,
                    resource_ref=(
                        None
                        if payload.resource_ref is None
                        else ControlPlaneResourceRef.model_validate(payload.resource_ref)
                    ),
                    resource_refs=[
                        ControlPlaneResourceRef.model_validate(item)
                        for item in payload.resource_refs
                    ],
                    target_refs=[
                        ControlPlaneTargetRef.model_validate(item) for item in payload.target_refs
                    ],
                    metadata=payload.metadata,
                )
            )
        if limit <= 0:
            return result
        if after:
            return result[:limit]
        return result[-limit:]

    async def execute_action(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        await self._publish_action_event(
            event_type=ControlPlaneEventType.ACTION_REQUESTED,
            request=request,
            summary=f"{request.action_id} requested",
        )

        try:
            result = await self._dispatch_action(request)
        except ControlPlaneActionError as exc:
            result = self._rejected_result(
                request=request,
                code=exc.code,
                message=str(exc),
            )
        except RetrievalPlatformError as exc:
            result = self._rejected_result(
                request=request,
                code=exc.code,
                message=exc.message,
            )
        except Exception as exc:  # pragma: no cover - 防御性兜底
            result = self._rejected_result(
                request=request,
                code="ACTION_EXECUTION_FAILED",
                message=str(exc),
            )

        event_type = {
            ControlPlaneActionStatus.COMPLETED: ControlPlaneEventType.ACTION_COMPLETED,
            ControlPlaneActionStatus.REJECTED: ControlPlaneEventType.ACTION_REJECTED,
            ControlPlaneActionStatus.DEFERRED: ControlPlaneEventType.ACTION_DEFERRED,
        }[result.status]
        await self._publish_action_result_event(
            result=result, request=request, event_type=event_type
        )
        if result.status != ControlPlaneActionStatus.REJECTED:
            for resource_ref in result.resource_refs:
                await self._publish_resource_event(
                    resource_ref=resource_ref,
                    request=request,
                    correlation_id=result.correlation_id,
                    summary=f"{resource_ref.resource_type} projected",
                )
        return result

    async def _dispatch_action(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        action_id = request.action_id
        if action_id == "wizard.refresh":
            await OnboardingService(self._project_root).run(status_only=True)
            return self._completed_result(
                request=request,
                code="WIZARD_REFRESHED",
                message="已刷新 wizard 状态",
                resource_refs=[self._resource_ref("wizard_session", "wizard:default")],
            )
        if action_id == "wizard.restart":
            await OnboardingService(self._project_root).run(restart=True, status_only=False)
            return self._completed_result(
                request=request,
                code="WIZARD_RESTARTED",
                message="已重新启动 wizard",
                resource_refs=[self._resource_ref("wizard_session", "wizard:default")],
            )
        if action_id == "project.select":
            return await self._handle_project_select(request)
        if action_id == "setup.review":
            return await self._handle_setup_review(request)
        if action_id == "setup.apply":
            return await self._handle_setup_apply(request)
        if action_id == "setup.quick_connect":
            return await self._handle_setup_quick_connect(request)
        if action_id == "provider.oauth.openai_codex":
            return await self._handle_provider_oauth_openai_codex(request)
        if action_id == "diagnostics.refresh":
            diagnostics = await self.get_diagnostics_summary()
            return self._completed_result(
                request=request,
                code="DIAGNOSTICS_REFRESHED",
                message="已刷新诊断摘要",
                data={"overall_status": diagnostics.overall_status},
                resource_refs=[self._resource_ref("diagnostics_summary", "diagnostics:runtime")],
            )
        if action_id == "memory.query":
            return await self._handle_memory_query(request)
        if action_id == "memory.subject.inspect":
            return await self._handle_memory_subject_inspect(request)
        if action_id == "memory.proposal.inspect":
            return await self._handle_memory_proposal_inspect(request)
        if action_id == "memory.flush":
            return await self._handle_memory_maintenance(
                request,
                kind=MemoryMaintenanceCommandKind.FLUSH,
                success_code="MEMORY_FLUSH_COMPLETED",
                success_message="已执行 Memory flush。",
            )
        if action_id == "memory.reindex":
            return await self._handle_memory_maintenance(
                request,
                kind=MemoryMaintenanceCommandKind.REINDEX,
                success_code="MEMORY_REINDEX_COMPLETED",
                success_message="已执行 Memory reindex。",
            )
        if action_id == "memory.sync.resume":
            return await self._handle_memory_maintenance(
                request,
                kind=MemoryMaintenanceCommandKind.SYNC_RESUME,
                success_code="MEMORY_SYNC_RESUME_COMPLETED",
                success_message="已执行 Memory sync.resume。",
            )
        if action_id == "memory.consolidate":
            return await self._handle_memory_consolidate(request)
        if action_id == "memory.profile_generate":
            return await self._handle_memory_profile_generate(request)
        if action_id == "memory.sor.edit":
            return await self._handle_memory_sor_edit(request)
        if action_id == "memory.sor.archive":
            return await self._handle_memory_sor_archive(request)
        if action_id == "memory.sor.restore":
            return await self._handle_memory_sor_restore(request)
        if action_id == "memory.browse":
            return await self._handle_memory_browse(request)
        if action_id == "vault.access.request":
            return await self._handle_vault_access_request(request)
        if action_id == "vault.access.resolve":
            return await self._handle_vault_access_resolve(request)
        if action_id == "vault.retrieve":
            return await self._handle_vault_retrieve(request)
        if action_id == "memory.export.inspect":
            return await self._handle_memory_export_inspect(request)
        if action_id == "memory.restore.verify":
            return await self._handle_memory_restore_verify(request)
        if action_id == "retrieval.index.start":
            return await self._handle_retrieval_index_start(request)
        if action_id == "retrieval.index.cancel":
            return await self._handle_retrieval_index_cancel(request)
        if action_id == "retrieval.index.cutover":
            return await self._handle_retrieval_index_cutover(request)
        if action_id == "retrieval.index.rollback":
            return await self._handle_retrieval_index_rollback(request)
        if action_id == "capability.refresh":
            if self._capability_pack_service is not None:
                await self._capability_pack_service.refresh()
            await self.get_capability_pack_document()
            return self._completed_result(
                request=request,
                code="CAPABILITY_REFRESHED",
                message="已刷新 capability pack",
                resource_refs=[self._resource_ref("capability_pack", "capability:bundled")],
            )
        if action_id == "work.refresh":
            await self.get_delegation_document()
            return self._completed_result(
                request=request,
                code="WORK_REFRESHED",
                message="已刷新 delegation overview",
                resource_refs=[self._resource_ref("delegation_plane", "delegation:overview")],
            )
        if action_id == "session.focus":
            return await self._handle_session_focus(request)
        if action_id == "session.unfocus":
            return await self._handle_session_unfocus(request)
        if action_id == "session.new":
            return await self._handle_session_new(request)
        if action_id == "session.create_with_project":
            return await self._handle_session_create_with_project(request)
        if action_id == "session.reset":
            return await self._handle_session_reset(request)
        if action_id == "session.export":
            return await self._handle_session_export(request)
        if action_id == "session.interrupt":
            return await self._handle_session_interrupt(request)
        if action_id == "session.resume":
            return await self._handle_session_resume(request)
        if action_id == "agent.list_available_models":
            return await self._handle_agent_list_models(request)
        if action_id == "agent.list_worker_archetypes":
            return await self._handle_agent_list_archetypes(request)
        if action_id == "agent.list_tool_profiles":
            return await self._handle_agent_list_tool_profiles(request)
        if action_id == "agent.create_worker_with_project":
            return await self._handle_agent_create_worker_with_project(request)
        if action_id == "operator.approval.resolve":
            return await self._handle_operator_approval(request)
        if action_id == "operator.alert.ack":
            return await self._handle_operator_action(
                request,
                kind=OperatorActionKind.ACK_ALERT,
            )
        if action_id == "operator.task.retry":
            return await self._handle_operator_action(
                request,
                kind=OperatorActionKind.RETRY_TASK,
            )
        if action_id == "operator.task.cancel":
            return await self._handle_operator_action(
                request,
                kind=OperatorActionKind.CANCEL_TASK,
            )
        if action_id == "channel.pairing.approve":
            return await self._handle_operator_action(
                request,
                kind=OperatorActionKind.APPROVE_PAIRING,
            )
        if action_id == "channel.pairing.reject":
            return await self._handle_operator_action(
                request,
                kind=OperatorActionKind.REJECT_PAIRING,
            )
        if action_id == "agent_profile.save":
            return await self._handle_agent_profile_save(request)
        if action_id == "agent_profile.update_resource_limits":
            return await self._handle_update_resource_limits(request)
        if action_id == "policy_profile.select":
            return await self._handle_policy_profile_select(request)
        if action_id == "skills.selection.save":
            return await self._handle_skills_selection_save(request)
        if action_id == "mcp_provider.install":
            return await self._handle_mcp_provider_install(request)
        if action_id == "mcp_provider.install_status":
            return await self._handle_mcp_provider_install_status(request)
        if action_id == "mcp_provider.uninstall":
            return await self._handle_mcp_provider_uninstall(request)
        if action_id == "mcp_provider.save":
            return await self._handle_mcp_provider_save(request)
        if action_id == "mcp_provider.delete":
            return await self._handle_mcp_provider_delete(request)
        if action_id == "worker_profile.create":
            return await self._handle_worker_profile_create(request)
        if action_id == "worker_profile.update":
            return await self._handle_worker_profile_update(request)
        if action_id == "worker_profile.clone":
            return await self._handle_worker_profile_clone(request)
        if action_id == "worker_profile.archive":
            return await self._handle_worker_profile_archive(request)
        if action_id == "worker_profile.review":
            return await self._handle_worker_profile_review(request)
        if action_id == "worker_profile.apply":
            return await self._handle_worker_profile_apply(request)
        if action_id == "worker_profile.publish":
            return await self._handle_worker_profile_publish(request)
        if action_id == "worker_profile.bind_default":
            return await self._handle_worker_profile_bind_default(request)
        if action_id == "worker.spawn_from_profile":
            return await self._handle_worker_spawn_from_profile(request)
        if action_id == "worker.extract_profile_from_runtime":
            return await self._handle_worker_extract_profile_from_runtime(request)
        if action_id == "config.apply":
            return await self._handle_config_apply(request)
        if action_id == "backup.create":
            return await self._handle_backup_create(request)
        if action_id == "restore.plan":
            return await self._handle_restore_plan(request)
        if action_id == "import.source.detect":
            return await self._handle_import_source_detect(request)
        if action_id == "import.mapping.save":
            return await self._handle_import_mapping_save(request)
        if action_id == "import.preview":
            return await self._handle_import_preview(request)
        if action_id == "import.run":
            return await self._handle_import_run(request)
        if action_id == "import.resume":
            return await self._handle_import_resume(request)
        if action_id == "import.report.inspect":
            return await self._handle_import_report_inspect(request)
        if action_id == "update.dry_run":
            return await self._handle_update_dry_run(request)
        if action_id == "update.apply":
            return await self._handle_update_apply(request)
        if action_id == "runtime.restart":
            return await self._handle_runtime_restart(request)
        if action_id == "runtime.verify":
            return await self._handle_runtime_verify(request)
        if action_id == "automation.create":
            return await self._handle_automation_create(request)
        if action_id == "automation.run":
            return await self._handle_automation_run(request)
        if action_id == "automation.pause":
            return await self._handle_automation_pause_resume(request, enable=False)
        if action_id == "automation.resume":
            return await self._handle_automation_pause_resume(request, enable=True)
        if action_id == "automation.delete":
            return await self._handle_automation_delete(request)
        if action_id == "work.cancel":
            return await self._handle_work_cancel(request)
        if action_id == "work.retry":
            return await self._handle_work_retry(request)
        if action_id == "worker.review":
            return await self._handle_worker_review(request)
        if action_id == "worker.apply":
            return await self._handle_worker_apply(request)
        if action_id == "work.split":
            return await self._handle_work_split(request)
        if action_id == "work.merge":
            return await self._handle_work_merge(request)
        if action_id == "work.delete":
            return await self._handle_work_delete(request)
        if action_id == "work.escalate":
            return await self._handle_work_escalate(request)
        if action_id == "pipeline.resume":
            return await self._handle_pipeline_resume(request)
        if action_id == "pipeline.retry_node":
            return await self._handle_pipeline_retry_node(request)
        if action_id == "behavior.read_file":
            return await self._handle_behavior_read_file(request)
        if action_id == "behavior.write_file":
            return await self._handle_behavior_write_file(request)
        raise ControlPlaneActionError("ACTION_NOT_FOUND", f"未知动作: {action_id}")

    async def _handle_project_select(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        project_id = str(request.params.get("project_id", "")).strip()
        workspace_id = str(request.params.get("workspace_id", "")).strip()
        if not project_id:
            raise ControlPlaneActionError("PROJECT_ID_REQUIRED", "project_id 不能为空")
        project = await self._stores.project_store.get_project(project_id)
        if project is None:
            raise ControlPlaneActionError("PROJECT_NOT_FOUND", "目标 project 不存在")

        if workspace_id:
            workspace = await self._stores.project_store.get_workspace(workspace_id)
            if workspace is None or workspace.project_id != project_id:
                raise ControlPlaneActionError("WORKSPACE_NOT_FOUND", "目标 workspace 不存在")
        else:
            workspace = await self._stores.project_store.get_primary_workspace(project_id)

        state = self._state_store.load().model_copy(
            update={
                "selected_project_id": project_id,
                "selected_workspace_id": workspace.workspace_id if workspace else "",
                "updated_at": datetime.now(tz=UTC),
            }
        )
        self._state_store.save(state)
        await self._sync_web_project_selector_state(
            project=project,
            workspace=workspace,
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
                "workspace_id": workspace.workspace_id if workspace else "",
            },
            resource_refs=[self._resource_ref("project_selector", "project:selector")],
            target_refs=[
                ControlPlaneTargetRef(
                    target_type="project", target_id=project_id, label=project.name
                )
            ],
        )

    async def _handle_setup_review(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        current_config = load_config(self._project_root)
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

        _, selected_project, selected_workspace, _ = await self._resolve_selection()
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
                    selected_workspace=selected_workspace,
                )
            except ControlPlaneActionError as exc:
                validation_errors.append(str(exc))
        agent_profiles = await self.get_agent_profiles_document()
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
            selected_workspace=selected_workspace,
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
            selected_workspace=selected_workspace,
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

    async def _handle_setup_apply(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        draft = request.params.get("draft", {})
        if draft is None:
            draft = {}
        if not isinstance(draft, dict):
            raise ControlPlaneActionError("SETUP_DRAFT_REQUIRED", "draft 必须是对象")

        _, selected_project, selected_workspace, _ = await self._resolve_selection()
        skill_selection = draft.get("skill_selection")
        normalized_skill_selection: dict[str, Any] | None = None
        if isinstance(skill_selection, Mapping):
            normalized_skill_selection = await self._normalize_skill_selection_for_scope(
                skill_selection,
                selected_project=selected_project,
                selected_workspace=selected_workspace,
            )

        review_result = await self._handle_setup_review(
            request.model_copy(update={"action_id": "setup.review"})
        )
        review = SetupReviewSummary.model_validate(review_result.data.get("review", {}))
        if not review.ready:
            blocking = "、".join(review.blocking_reasons) or "存在未通过项"
            raise ControlPlaneActionError(
                "SETUP_REVIEW_BLOCKED",
                f"配置检查未通过，当前不能保存：{blocking}",
            )

        current_config = load_config(self._project_root)
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
            raise ControlPlaneActionError("POLICY_PROFILE_INVALID", "不支持的 policy profile")

        agent_request_payload: dict[str, Any] | None = None
        agent_profile_patch = draft.get("agent_profile", {})
        if isinstance(agent_profile_patch, dict) and agent_profile_patch:
            agent_profiles = await self.get_agent_profiles_document()
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
                raise ControlPlaneActionError(
                    "AGENT_PROFILE_SCOPE_INVALID", "scope 必须是 system/project"
                )
            if merged_scope == "project" and selected_project is not None:
                merged_agent_profile.setdefault("project_id", selected_project.project_id)
            if not str(merged_agent_profile.get("name", "")).strip():
                raise ControlPlaneActionError("AGENT_PROFILE_NAME_REQUIRED", "name 不能为空")
            agent_request_payload = merged_agent_profile

        save_config(config, self._project_root)
        litellm_path = generate_litellm_config(config, self._project_root)

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
            policy_result = await self._handle_policy_profile_select(
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
            agent_result = await self._handle_agent_profile_save(
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

        return self._completed_result(
            request=request,
            code="SETUP_APPLIED",
            message="配置已保存，主 Agent 与系统设置已同步。",
            data=data,
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

    async def _restart_runtime_after_delay(
        self,
        *,
        delay_seconds: float,
        trigger_source: UpdateTriggerSource,
    ) -> None:
        if self._update_service is None:
            return
        await asyncio.sleep(delay_seconds)
        try:
            await self._update_service.restart(trigger_source=trigger_source)
        except Exception as exc:  # pragma: no cover - 后台 restart 失败仅记录日志
            log.warning(
                "setup_quick_connect_restart_failed",
                error_type=type(exc).__name__,
                error=str(exc),
            )

    async def _activate_runtime_after_config_change(
        self,
        *,
        request: ActionRequestEnvelope,
        failure_code: str,
        failure_prefix: str,
        raise_on_failure: bool,
    ) -> dict[str, Any]:
        """激活 LiteLLM Proxy，并在托管实例中安排 runtime reload。"""
        activation_service = RuntimeActivationService(self._project_root)
        try:
            activation = await activation_service.start_proxy()
        except RuntimeActivationError as exc:
            if raise_on_failure:
                raise ControlPlaneActionError(
                    failure_code,
                    f"{failure_prefix}：{exc}",
                ) from exc
            return {
                "project_root": str(self._project_root),
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

        if activation.managed_runtime and self._update_service is not None:
            if request.surface == ControlPlaneSurface.CLI:
                await self._update_service.restart(
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
            raise ControlPlaneActionError(
                "SKILL_SELECTION_CONFLICT",
                f"skill selection 同时出现在 enabled/disabled 列表：{overlap[0]}",
            )
        if allowed_item_ids is not None:
            unknown = sorted((selected_item_ids | disabled_item_ids) - allowed_item_ids)
            if unknown:
                raise ControlPlaneActionError(
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
        selected_workspace: Any | None,
    ) -> dict[str, Any]:
        if selected_project is None:
            raise ControlPlaneActionError("PROJECT_REQUIRED", "当前没有可用 project")
        document = await self.get_skill_governance_document(
            selected_project=selected_project,
            selected_workspace=selected_workspace,
        )
        allowed_item_ids = {item.item_id for item in document.items}
        return self._normalize_skill_selection_payload(
            selection,
            allowed_item_ids=allowed_item_ids,
        )

    async def _handle_skills_selection_save(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        raw_selection = request.params.get("selection")
        if raw_selection is None:
            raw_selection = {}
        if not isinstance(raw_selection, Mapping):
            raise ControlPlaneActionError(
                "SKILL_SELECTION_REQUIRED",
                "selection 必须是对象",
            )

        _, selected_project, selected_workspace, _ = await self._resolve_selection()
        if selected_project is None:
            raise ControlPlaneActionError("PROJECT_REQUIRED", "当前没有可用 project")

        normalized = await self._normalize_skill_selection_for_scope(
            raw_selection,
            selected_project=selected_project,
            selected_workspace=selected_workspace,
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
            selected_workspace=selected_workspace,
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

    # ── Feature 058: MCP 安装生命周期 action handlers ──────────

    async def _handle_mcp_provider_install(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        """启动 MCP server 异步安装任务。"""
        mcp_installer = getattr(self, "_mcp_installer", None)
        if mcp_installer is None:
            raise ControlPlaneActionError("MCP_INSTALLER_UNAVAILABLE", "MCP Installer 未绑定")

        install_source = self._param_str(request.params, "install_source")
        if install_source not in {"npm", "pip"}:
            raise ControlPlaneActionError(
                "MCP_INSTALL_SOURCE_INVALID",
                f"安装来源不合法: {install_source}（支持 npm/pip）",
            )
        package_name = self._param_str(request.params, "package_name")
        if not package_name:
            raise ControlPlaneActionError("MCP_PACKAGE_NAME_REQUIRED", "包名不能为空")

        env = self._normalize_dict(request.params.get("env"))
        env = {str(k): str(v) for k, v in env.items() if str(k).strip()}

        try:
            task_id = await mcp_installer.install(
                install_source=install_source,
                package_name=package_name,
                env=env,
            )
        except ValueError as exc:
            err_msg = str(exc)
            if "已安装" in err_msg:
                raise ControlPlaneActionError("MCP_SERVER_ALREADY_INSTALLED", err_msg) from exc
            if "格式不合法" in err_msg or "危险字符" in err_msg:
                raise ControlPlaneActionError("MCP_PACKAGE_NAME_INVALID", err_msg) from exc
            raise ControlPlaneActionError("MCP_INSTALL_FAILED", err_msg) from exc

        # 计算预期 server_id
        from .mcp_installer import _slugify_server_id, InstallSource as _IS

        server_id = _slugify_server_id(_IS(install_source), package_name)

        return self._completed_result(
            request=request,
            code="MCP_INSTALL_STARTED",
            message="MCP server 安装已启动",
            data={"task_id": task_id, "server_id": server_id},
            resource_refs=[
                self._resource_ref("mcp_provider_catalog", "mcp-providers:catalog"),
            ],
        )

    async def _handle_mcp_provider_install_status(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        """查询安装任务进度。"""
        mcp_installer = getattr(self, "_mcp_installer", None)
        if mcp_installer is None:
            raise ControlPlaneActionError("MCP_INSTALLER_UNAVAILABLE", "MCP Installer 未绑定")

        task_id = self._param_str(request.params, "task_id")
        if not task_id:
            raise ControlPlaneActionError("MCP_INSTALL_TASK_NOT_FOUND", "task_id 不能为空")

        task = mcp_installer.get_install_status(task_id)
        if task is None:
            raise ControlPlaneActionError("MCP_INSTALL_TASK_NOT_FOUND", "安装任务不存在")

        return self._completed_result(
            request=request,
            code="MCP_INSTALL_STATUS",
            message="安装状态查询成功",
            data={
                "task_id": task.task_id,
                "status": str(task.status),
                "progress_message": task.progress_message,
                "error": task.error,
                "result": task.result if task.result else None,
            },
        )

    async def _handle_mcp_provider_uninstall(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        """卸载已安装 MCP server。"""
        mcp_installer = getattr(self, "_mcp_installer", None)
        if mcp_installer is None:
            raise ControlPlaneActionError("MCP_INSTALLER_UNAVAILABLE", "MCP Installer 未绑定")

        server_id = self._param_str(request.params, "server_id")
        if not server_id:
            raise ControlPlaneActionError("MCP_SERVER_ID_REQUIRED", "server_id 不能为空")

        try:
            result = await mcp_installer.uninstall(server_id)
        except ValueError as exc:
            raise ControlPlaneActionError(
                "MCP_SERVER_NOT_INSTALLED",
                str(exc),
            ) from exc

        return self._completed_result(
            request=request,
            code="MCP_SERVER_UNINSTALLED",
            message="MCP server 已卸载",
            data=result,
            resource_refs=[
                self._resource_ref("mcp_provider_catalog", "mcp-providers:catalog"),
                self._resource_ref("capability_pack", "capability:bundled"),
                self._resource_ref("skill_governance", "skills:governance"),
            ],
        )

    async def _handle_mcp_provider_save(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        if (
            self._capability_pack_service is None
            or self._capability_pack_service.mcp_registry is None
        ):
            raise ControlPlaneActionError("MCP_REGISTRY_UNAVAILABLE", "MCP registry 未绑定")
        raw = request.params.get("provider")
        if not isinstance(raw, Mapping):
            raise ControlPlaneActionError("MCP_PROVIDER_REQUIRED", "provider 必须是对象")
        provider_id = self._normalize_provider_id(
            self._param_str(raw, "provider_id") or self._param_str(raw, "label")
        )
        if not provider_id:
            raise ControlPlaneActionError("MCP_PROVIDER_ID_REQUIRED", "provider_id 不能为空")
        command = self._param_str(raw, "command")
        if not command:
            raise ControlPlaneActionError("MCP_PROVIDER_COMMAND_REQUIRED", "command 不能为空")
        mount_policy = self._param_str(raw, "mount_policy", default="auto_readonly").lower()
        if mount_policy not in {"explicit", "auto_readonly", "auto_all"}:
            raise ControlPlaneActionError(
                "MCP_PROVIDER_MOUNT_POLICY_INVALID",
                "mount_policy 不合法",
            )
        config = McpServerConfig.model_validate(
            {
                "name": provider_id,
                "command": command,
                "args": self._normalize_text_list(raw.get("args")),
                "env": {
                    key: str(value)
                    for key, value in self._normalize_dict(raw.get("env")).items()
                    if str(key).strip()
                },
                "cwd": self._param_str(raw, "cwd"),
                "enabled": self._param_bool(raw, "enabled", default=True),
                "mount_policy": mount_policy,
            }
        )
        self._capability_pack_service.mcp_registry.save_config(config)
        await self._capability_pack_service.refresh()
        document = await self.get_mcp_provider_catalog_document()
        return self._completed_result(
            request=request,
            code="MCP_PROVIDER_SAVED",
            message="MCP provider 已保存。",
            data={
                "provider_id": provider_id,
                "installed_count": document.summary.get("installed_count", 0),
            },
            resource_refs=[
                self._resource_ref("mcp_provider_catalog", "mcp-providers:catalog"),
                self._resource_ref("capability_pack", "capability:bundled"),
                self._resource_ref("skill_governance", "skills:governance"),
            ],
        )

    async def _handle_mcp_provider_delete(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        if (
            self._capability_pack_service is None
            or self._capability_pack_service.mcp_registry is None
        ):
            raise ControlPlaneActionError("MCP_REGISTRY_UNAVAILABLE", "MCP registry 未绑定")
        provider_id = self._normalize_provider_id(self._param_str(request.params, "provider_id"))
        if not provider_id:
            raise ControlPlaneActionError("MCP_PROVIDER_ID_REQUIRED", "provider_id 不能为空")
        removed = self._capability_pack_service.mcp_registry.delete_config(provider_id)
        if not removed:
            raise ControlPlaneActionError("MCP_PROVIDER_NOT_FOUND", "MCP provider 不存在")
        await self._capability_pack_service.refresh()
        return self._completed_result(
            request=request,
            code="MCP_PROVIDER_DELETED",
            message="MCP provider 已删除。",
            data={"provider_id": provider_id},
            resource_refs=[
                self._resource_ref("mcp_provider_catalog", "mcp-providers:catalog"),
                self._resource_ref("capability_pack", "capability:bundled"),
                self._resource_ref("skill_governance", "skills:governance"),
            ],
        )

    async def _handle_provider_oauth_openai_codex(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        env_name = self._param_str(request.params, "env_name", default="OPENAI_API_KEY")
        profile_name = self._param_str(
            request.params,
            "profile_name",
            default="openai-codex-default",
        )
        registry = OAuthProviderRegistry()
        provider_config = registry.get("openai-codex")
        if provider_config is None:
            raise ControlPlaneActionError("OAUTH_PROVIDER_UNAVAILABLE", "未找到 OpenAI OAuth 配置")

        environment = detect_environment()
        if environment.use_manual_mode:
            raise ControlPlaneActionError(
                "OAUTH_BROWSER_UNAVAILABLE",
                "当前环境无法直接打开浏览器，请先在本地桌面环境完成 OpenAI OAuth。",
            )

        credential = await run_auth_code_pkce_flow(
            config=provider_config,
            registry=registry,
            env=environment,
            use_gateway_callback=True,
        )
        store = self._credential_store()
        existing = store.get_profile(profile_name)
        now = datetime.now(tz=UTC)
        profile = ProviderProfile(
            name=profile_name,
            provider="openai-codex",
            auth_mode="oauth",
            credential=credential,
            is_default=(
                existing.is_default
                if existing is not None
                else store.get_default_profile() is None
            ),
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
        )
        store.set_profile(profile)
        self._write_env_values(
            self._project_root / ".env.litellm",
            {
                env_name: credential.access_token.get_secret_value(),
            },
        )

        # OAuth 成功后自动同步 litellm-config.yaml
        # 确保 LiteLLM Proxy 能路由模型（含 account_id headers 等）
        try:
            config = load_config(self._project_root)
            generate_litellm_config(config, self._project_root)
            log.info("oauth_litellm_config_synced")
        except Exception as exc:
            log.warning("oauth_litellm_config_sync_failed", error=str(exc))

        activation_data = await self._activate_runtime_after_config_change(
            request=request,
            failure_code="OPENAI_OAUTH_ACTIVATION_FAILED",
            failure_prefix="OpenAI Auth 已连接，但真实模型激活失败",
            raise_on_failure=False,
        )

        message = "OpenAI Auth 已连接，已写入本地凭证。"
        runtime_message = str(activation_data.get("runtime_reload_message", "")).strip()
        if runtime_message:
            message = runtime_message

        return self._completed_result(
            request=request,
            code="OPENAI_OAUTH_CONNECTED",
            message=message,
            data={
                "provider_id": "openai-codex",
                "profile_name": profile_name,
                "env_name": env_name,
                "expires_at": credential.expires_at.isoformat(),
                "account_id": credential.account_id or "",
                "activation": activation_data,
            },
            resource_refs=[
                self._resource_ref("setup_governance", "setup:governance"),
                self._resource_ref("diagnostics_summary", "diagnostics:runtime"),
            ],
            target_refs=[
                ControlPlaneTargetRef(
                    target_type="provider",
                    target_id="openai-codex",
                    label="OpenAI Codex",
                )
            ],
        )

    async def _resolve_memory_action_context(
        self,
        request: ActionRequestEnvelope,
    ) -> tuple[str, str | None]:
        _, selected_project, selected_workspace, _ = await self._resolve_selection()
        project_id = self._param_str(request.params, "project_id") or (
            selected_project.project_id if selected_project is not None else ""
        )
        workspace_id = self._param_str(request.params, "workspace_id") or (
            selected_workspace.workspace_id if selected_workspace is not None else None
        )
        return project_id, workspace_id

    async def _handle_memory_query(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        project_id, workspace_id = await self._resolve_memory_action_context(request)
        document = await self.get_memory_console(
            project_id=project_id or None,
            workspace_id=workspace_id,
            scope_id=self._param_str(request.params, "scope_id") or None,
            partition=self._param_str(request.params, "partition") or None,
            layer=self._param_str(request.params, "layer") or None,
            query=self._param_str(request.params, "query") or None,
            include_history=self._param_bool(request.params, "include_history"),
            include_vault_refs=self._param_bool(request.params, "include_vault_refs"),
            limit=self._param_int(request.params, "limit", default=50),
            derived_type=self._param_str(request.params, "derived_type") or "",
            status=self._param_str(request.params, "status") or "",
            updated_after=self._param_str(request.params, "updated_after") or "",
            updated_before=self._param_str(request.params, "updated_before") or "",
        )
        return self._completed_result(
            request=request,
            code="MEMORY_QUERY_COMPLETED",
            message="已刷新 Memory 总览。",
            data={
                "record_count": len(document.records),
                "active_project_id": document.active_project_id,
                "active_workspace_id": document.active_workspace_id,
            },
            resource_refs=[self._resource_ref("memory_console", "memory:overview")],
        )

    async def _handle_memory_subject_inspect(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        subject_key = self._param_str(request.params, "subject_key")
        if not subject_key:
            raise ControlPlaneActionError("SUBJECT_KEY_REQUIRED", "subject_key 不能为空")
        project_id, workspace_id = await self._resolve_memory_action_context(request)
        document = await self.get_memory_subject_history(
            subject_key,
            project_id=project_id or None,
            workspace_id=workspace_id,
            scope_id=self._param_str(request.params, "scope_id") or None,
        )
        return self._completed_result(
            request=request,
            code="MEMORY_SUBJECT_HISTORY_READY",
            message="已加载 Subject 历史。",
            data={
                "subject_key": subject_key,
                "history_count": len(document.history),
                "scope_id": document.scope_id,
            },
            resource_refs=[
                self._resource_ref(
                    "memory_subject_history",
                    f"memory-subject:{subject_key}",
                )
            ],
            target_refs=[
                ControlPlaneTargetRef(
                    target_type="memory_subject",
                    target_id=subject_key,
                    label=subject_key,
                )
            ],
        )

    async def _handle_memory_proposal_inspect(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        project_id, workspace_id = await self._resolve_memory_action_context(request)
        document = await self.get_memory_proposal_audit(
            project_id=project_id or None,
            workspace_id=workspace_id,
            scope_id=self._param_str(request.params, "scope_id") or None,
            status=self._param_str(request.params, "status") or None,
            source=self._param_str(request.params, "source") or None,
            limit=self._param_int(request.params, "limit", default=50),
        )
        return self._completed_result(
            request=request,
            code="MEMORY_PROPOSAL_AUDIT_READY",
            message="已加载 Memory Proposal 审计视图。",
            data={"item_count": len(document.items)},
            resource_refs=[
                self._resource_ref(
                    "memory_proposal_audit",
                    "memory-proposals:overview",
                )
            ],
        )

    async def _handle_memory_maintenance(
        self,
        request: ActionRequestEnvelope,
        *,
        kind: MemoryMaintenanceCommandKind,
        success_code: str,
        success_message: str,
    ) -> ActionResultEnvelope:
        project_id, workspace_id = await self._resolve_memory_action_context(request)
        partition_value = self._param_str(request.params, "partition")
        partition = self._parse_memory_partition(partition_value) if partition_value else None
        raw_evidence_refs = request.params.get("evidence_refs", [])
        evidence_refs = (
            [EvidenceRef.model_validate(item) for item in raw_evidence_refs]
            if isinstance(raw_evidence_refs, list)
            else []
        )
        run = await self._memory_console_service.run_maintenance(
            kind=kind,
            project_id=project_id or "",
            workspace_id=workspace_id,
            scope_id=self._param_str(request.params, "scope_id"),
            partition=partition,
            reason=self._param_str(request.params, "reason"),
            summary=self._param_str(request.params, "summary"),
            requested_by=request.actor.actor_id,
            evidence_refs=evidence_refs,
            metadata={
                "actor_id": request.actor.actor_id,
                "actor_label": request.actor.actor_label,
            },
        )
        return self._completed_result(
            request=request,
            code=success_code,
            message=success_message,
            data={
                "run_id": run.run_id,
                "status": run.status.value,
                "backend_used": run.backend_used,
                "error_summary": run.error_summary,
                "metadata": run.metadata,
            },
            resource_refs=[
                self._resource_ref("memory_console", "memory:overview"),
                self._resource_ref("diagnostics_summary", "diagnostics:runtime"),
            ],
            target_refs=self._memory_target_refs(request),
        )

    async def _handle_memory_consolidate(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        """使用 LLM 将待整理 fragment 整合为 SoR 现行事实。"""
        project_id, workspace_id = await self._resolve_memory_action_context(request)
        try:
            result = await self._memory_console_service.run_consolidate(
                project_id=project_id or "",
                workspace_id=workspace_id,
            )
        except MemoryConsoleError as exc:
            return self._failed_result(
                request=request,
                code=exc.code,
                message=exc.message,
            )
        return self._completed_result(
            request=request,
            code="MEMORY_CONSOLIDATE_COMPLETED",
            message=result.get("message", "记忆整理完成"),
            data=result,
            resource_refs=[
                self._resource_ref("memory_console", "memory:overview"),
            ],
            target_refs=self._memory_target_refs(request),
        )

    async def _handle_memory_profile_generate(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        """定期聚合生成用户画像（Feature 065 Phase 3, US-9）。"""
        project_id, workspace_id = await self._resolve_memory_action_context(request)

        # 复用 MemoryConsoleService 内部的 context/memory 解析模式
        try:
            context = await self._memory_console_service._resolve_context(
                active_project_id=project_id or "",
                active_workspace_id=workspace_id or "",
                project_id=project_id or "",
                workspace_id=workspace_id or "",
            )
            if not context.selected_scope_ids:
                return self._completed_result(
                    request=request,
                    code="PROFILE_GENERATE_NO_SCOPE",
                    message="没有可用的 scope",
                    data={"dimensions_generated": 0, "dimensions_updated": 0},
                    resource_refs=[],
                    target_refs=self._memory_target_refs(request),
                )
            memory = await self._memory_console_service._memory_service_for_context(context)
        except Exception as exc:
            return self._failed_result(
                request=request,
                code="MEMORY_SERVICE_UNAVAILABLE",
                message=f"Memory 服务不可用: {exc}",
            )

        # 延迟创建 ProfileGeneratorService
        try:
            from octoagent.memory import SqliteMemoryStore
            from octoagent.provider.dx.profile_generator_service import ProfileGeneratorService

            memory_store = SqliteMemoryStore(self._stores.conn)
            llm_service = getattr(self._stores, "llm_service", None) or self._memory_console_service._llm_service
            profile_service = ProfileGeneratorService(
                memory_store=memory_store,
                llm_service=llm_service,
                project_root=self._project_root,
            )
        except Exception as exc:
            return self._failed_result(
                request=request,
                code="PROFILE_SERVICE_UNAVAILABLE",
                message=f"画像服务初始化失败: {exc}",
            )

        total_generated = 0
        total_updated = 0
        all_errors: list[str] = []

        for scope_id in context.selected_scope_ids:
            try:
                result = await profile_service.generate_profile(
                    memory=memory,
                    scope_id=scope_id,
                )
                total_generated += result.dimensions_generated
                total_updated += result.dimensions_updated
                all_errors.extend(result.errors)
            except Exception as exc:
                all_errors.append(f"scope {scope_id} 画像生成失败: {exc}")
                log.warning(
                    "profile_generate_scope_failed",
                    scope_id=scope_id,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )

        return self._completed_result(
            request=request,
            code="PROFILE_GENERATE_COMPLETED",
            message=f"画像生成完成：{total_generated} 新增, {total_updated} 更新",
            data={
                "dimensions_generated": total_generated,
                "dimensions_updated": total_updated,
                "errors": all_errors[:10],
            },
            resource_refs=[
                self._resource_ref("memory_console", "memory:overview"),
            ],
            target_refs=self._memory_target_refs(request),
        )

    @staticmethod
    def _check_sensitive_partition(partition_str: str) -> bool:
        """检查 partition 是否属于敏感分区（HEALTH/FINANCE）。"""
        from octoagent.memory import SENSITIVE_PARTITIONS, MemoryPartition
        try:
            partition_enum = MemoryPartition(partition_str)
        except ValueError:
            return False
        return partition_enum in SENSITIVE_PARTITIONS

    def _get_memory_store(self):
        """获取 Memory Store 实例（避免多处直接穿透 _memory_console_service）。"""
        return self._memory_console_service._memory_store

    async def _handle_memory_sor_edit(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        """T023: 用户编辑 SoR 记忆——乐观锁 + Proposal 流程 + 审计事件。"""
        project_id, workspace_id = await self._resolve_memory_action_context(request)
        scope_id = self._param_str(request.params, "scope_id")
        subject_key = self._param_str(request.params, "subject_key")
        content = self._param_str(request.params, "content")
        new_subject_key = self._param_str(request.params, "new_subject_key")
        expected_version = self._param_int(request.params, "expected_version", default=0)
        edit_summary = self._param_str(request.params, "edit_summary")

        if not scope_id or not subject_key or not content or expected_version < 1:
            return self._rejected_result(
                request=request,
                code="INVALID_PARAMS",
                message="scope_id、subject_key、content、expected_version 均为必填项。",
            )

        store = self._get_memory_store()
        current = await store.get_current_sor(scope_id, subject_key)
        if current is None:
            return self._rejected_result(
                request=request,
                code="SOR_NOT_FOUND",
                message=f"未找到 scope={scope_id} subject_key={subject_key} 的 current SoR 记录。",
            )
        if current.version != expected_version:
            return self._rejected_result(
                request=request,
                code="VERSION_CONFLICT",
                message=f"版本冲突：期望版本 {expected_version}，当前版本 {current.version}。请刷新后重试。",
            )
        if self._check_sensitive_partition(current.partition):
            return self._rejected_result(
                request=request,
                code="VAULT_AUTHORIZATION_REQUIRED",
                message="此记忆属于敏感分区，编辑需要额外的 Vault 授权确认。",
            )

        # 走 propose-validate-commit 流程
        from datetime import UTC, datetime

        from octoagent.memory import EvidenceRef, MemoryService, WriteAction, WriteProposal
        from ulid import ULID

        target_subject_key = new_subject_key if new_subject_key else subject_key
        memory_service = await self._memory_console_service._memory_service_for_context(
            await self._memory_console_service._resolve_context(
                active_project_id=project_id or "",
                active_workspace_id=workspace_id or "",
                project_id=project_id or "",
                workspace_id=workspace_id or "",
                scope_id=scope_id,
            )
        )
        now = datetime.now(UTC)
        proposal = WriteProposal(
            proposal_id=f"01JPROP_{ULID()}",
            scope_id=scope_id,
            partition=current.partition,
            action=WriteAction.UPDATE,
            subject_key=target_subject_key,
            content=content,
            rationale=edit_summary or "用户手动编辑",
            confidence=1.0,
            evidence_refs=current.evidence_refs or [EvidenceRef(ref_id="user_edit", ref_type="user")],
            expected_version=current.version,
            metadata={"source": "user_edit", "edit_summary": edit_summary},
            created_at=now,
        )

        await memory_service.propose_write(proposal)
        validation = await memory_service.validate_proposal(proposal.proposal_id)
        if validation.errors:
            return self._rejected_result(
                request=request,
                code="VALIDATION_FAILED",
                message=f"编辑验证失败: {'; '.join(validation.errors)}",
            )

        result = await memory_service.commit_memory(proposal.proposal_id)

        # T026: 审计事件
        _log.info(
            "memory.sor.edit.completed",
            scope_id=scope_id,
            subject_key=subject_key,
            new_subject_key=target_subject_key,
            old_version=current.version,
            new_version=result.version if result else expected_version + 1,
            edit_summary=edit_summary,
            actor="user:web",
        )

        return self._completed_result(
            request=request,
            code="MEMORY_SOR_EDIT_COMPLETED",
            message="记忆已更新",
            data={
                "memory_id": result.memory_id if result else "",
                "subject_key": target_subject_key,
                "version": result.version if result else expected_version + 1,
            },
            resource_refs=[self._resource_ref("memory_console", "memory:overview")],
        )

    async def _handle_memory_sor_archive(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        """T034: 归档 SoR 记忆。"""
        project_id, workspace_id = await self._resolve_memory_action_context(request)
        scope_id = self._param_str(request.params, "scope_id")
        memory_id = self._param_str(request.params, "memory_id")
        expected_version = self._param_int(request.params, "expected_version", default=0)

        if not scope_id or not memory_id or expected_version < 1:
            return self._rejected_result(
                request=request,
                code="INVALID_PARAMS",
                message="scope_id、memory_id、expected_version 均为必填项。",
            )

        store = self._get_memory_store()
        current = await store.get_sor(memory_id)
        if current is None or current.scope_id != scope_id:
            return self._rejected_result(
                request=request, code="SOR_NOT_FOUND",
                message=f"未找到 memory_id={memory_id} 的 SoR 记录。",
            )
        if current.status != "current":
            return self._rejected_result(
                request=request, code="INVALID_STATUS",
                message=f"记忆状态为 {current.status}，只有 current 状态的记忆可以归档。",
            )
        if current.version != expected_version:
            return self._rejected_result(
                request=request, code="VERSION_CONFLICT",
                message=f"版本冲突：期望版本 {expected_version}，当前版本 {current.version}。请刷新后重试。",
            )
        if self._check_sensitive_partition(current.partition):
            return self._rejected_result(
                request=request, code="VAULT_AUTHORIZATION_REQUIRED",
                message="此记忆属于敏感分区，归档需要额外的 Vault 授权确认。",
            )

        from datetime import UTC, datetime

        now_str = datetime.now(UTC).isoformat()
        await store.update_sor_status(memory_id, status="archived", updated_at=now_str)

        # 审计事件
        _log.info(
            "memory.sor.archive.completed",
            scope_id=scope_id,
            memory_id=memory_id,
            subject_key=current.subject_key,
            actor="user:web",
        )

        return self._completed_result(
            request=request,
            code="MEMORY_SOR_ARCHIVE_COMPLETED",
            message="记忆已归档",
            data={
                "memory_id": memory_id,
                "subject_key": current.subject_key,
                "new_status": "archived",
            },
            resource_refs=[self._resource_ref("memory_console", "memory:overview")],
        )

    async def _handle_memory_sor_restore(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        """T035: 恢复已归档的 SoR 记忆。"""
        project_id, workspace_id = await self._resolve_memory_action_context(request)
        scope_id = self._param_str(request.params, "scope_id")
        memory_id = self._param_str(request.params, "memory_id")

        if not scope_id or not memory_id:
            return self._rejected_result(
                request=request,
                code="INVALID_PARAMS",
                message="scope_id 和 memory_id 均为必填项。",
            )

        store = self._get_memory_store()
        record = await store.get_sor(memory_id)
        if record is None or record.scope_id != scope_id:
            return self._rejected_result(
                request=request, code="SOR_NOT_FOUND",
                message=f"未找到 memory_id={memory_id} 的 SoR 记录。",
            )
        if record.status != "archived":
            return self._rejected_result(
                request=request,
                code="INVALID_STATUS",
                message=f"记忆状态为 {record.status}，只有 archived 状态的记忆可以恢复。",
            )

        # 检查同 subject_key 下是否已有 current 记录
        existing_current = await store.get_current_sor(scope_id, record.subject_key)
        if existing_current is not None:
            return self._rejected_result(
                request=request,
                code="SUBJECT_KEY_CONFLICT",
                message=(
                    f"同 subject_key ({record.subject_key}) 下已存在 current 记录 "
                    f"(memory_id={existing_current.memory_id})，无法恢复。"
                    f"请先归档或编辑现有记录。"
                ),
            )

        from datetime import UTC, datetime

        now_str = datetime.now(UTC).isoformat()
        await store.update_sor_status(memory_id, status="current", updated_at=now_str)

        # 审计事件
        _log.info(
            "memory.sor.restore.completed",
            scope_id=scope_id,
            memory_id=memory_id,
            subject_key=record.subject_key,
            actor="user:web",
        )

        return self._completed_result(
            request=request,
            code="MEMORY_SOR_RESTORE_COMPLETED",
            message="记忆已恢复",
            data={
                "memory_id": memory_id,
                "subject_key": record.subject_key,
                "new_status": "current",
            },
            resource_refs=[self._resource_ref("memory_console", "memory:overview")],
        )

    async def _handle_memory_browse(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        """T062: 前端 Memory UI 的 browse 查询。"""
        project_id, workspace_id = await self._resolve_memory_action_context(request)
        result = await self._memory_console_service.browse_memory(
            project_id=project_id or "",
            workspace_id=workspace_id,
            scope_id=self._param_str(request.params, "scope_id") or "",
            prefix=self._param_str(request.params, "prefix"),
            partition=self._param_str(request.params, "partition"),
            group_by=self._param_str(request.params, "group_by") or "partition",
            offset=self._param_int(request.params, "offset", default=0),
            limit=self._param_int(request.params, "limit", default=20),
        )
        return self._completed_result(
            request=request,
            code="MEMORY_BROWSE_COMPLETED",
            message="已获取记忆目录。",
            data=result,
            resource_refs=[self._resource_ref("memory_console", "memory:overview")],
        )

    async def _handle_retrieval_index_start(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        project_id, workspace_id = await self._resolve_memory_action_context(request)
        document = await self._retrieval_platform_service.start_memory_generation_build(
            actor_id=request.actor.actor_id,
            actor_label=request.actor.actor_label,
            project_id=project_id or "",
            workspace_id=workspace_id or "",
        )
        memory_state = next(
            (
                item
                for item in document.corpora
                if item.corpus_kind == CorpusKind.MEMORY
            ),
            None,
        )
        return self._completed_result(
            request=request,
            code="RETRIEVAL_BUILD_STARTED",
            message="已开始准备新的 embedding 索引。",
            data={
                "corpus_kind": CorpusKind.MEMORY.value,
                "state": memory_state.state if memory_state is not None else "unknown",
                "pending_generation_id": (
                    memory_state.pending_generation_id if memory_state is not None else ""
                ),
            },
            resource_refs=[
                self._resource_ref("retrieval_platform", "retrieval:platform"),
                self._resource_ref("memory_console", "memory:overview"),
            ],
        )

    async def _handle_retrieval_index_cancel(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        generation_id = self._param_str(request.params, "generation_id")
        if not generation_id:
            raise ControlPlaneActionError(
                "GENERATION_ID_REQUIRED",
                "generation_id 不能为空",
            )
        project_id, workspace_id = await self._resolve_memory_action_context(request)
        await self._retrieval_platform_service.cancel_generation(
            generation_id=generation_id,
            project_id=project_id or "",
            workspace_id=workspace_id or "",
        )
        return self._completed_result(
            request=request,
            code="RETRIEVAL_BUILD_CANCELLED",
            message="已取消新的 embedding 迁移，系统继续使用旧索引。",
            data={"generation_id": generation_id},
            resource_refs=[
                self._resource_ref("retrieval_platform", "retrieval:platform"),
                self._resource_ref("memory_console", "memory:overview"),
            ],
        )

    async def _handle_retrieval_index_cutover(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        generation_id = self._param_str(request.params, "generation_id")
        if not generation_id:
            raise ControlPlaneActionError(
                "GENERATION_ID_REQUIRED",
                "generation_id 不能为空",
            )
        project_id, workspace_id = await self._resolve_memory_action_context(request)
        await self._retrieval_platform_service.cutover_generation(
            generation_id=generation_id,
            project_id=project_id or "",
            workspace_id=workspace_id or "",
        )
        return self._completed_result(
            request=request,
            code="RETRIEVAL_CUTOVER_COMPLETED",
            message="已切换到新的 embedding 索引。",
            data={"generation_id": generation_id},
            resource_refs=[
                self._resource_ref("retrieval_platform", "retrieval:platform"),
                self._resource_ref("memory_console", "memory:overview"),
            ],
        )

    async def _handle_retrieval_index_rollback(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        generation_id = self._param_str(request.params, "generation_id")
        if not generation_id:
            raise ControlPlaneActionError(
                "GENERATION_ID_REQUIRED",
                "generation_id 不能为空",
            )
        project_id, workspace_id = await self._resolve_memory_action_context(request)
        await self._retrieval_platform_service.rollback_generation(
            generation_id=generation_id,
            project_id=project_id or "",
            workspace_id=workspace_id or "",
        )
        return self._completed_result(
            request=request,
            code="RETRIEVAL_ROLLBACK_COMPLETED",
            message="已回滚到上一版 embedding 索引。",
            data={"generation_id": generation_id},
            resource_refs=[
                self._resource_ref("retrieval_platform", "retrieval:platform"),
                self._resource_ref("memory_console", "memory:overview"),
            ],
        )

    async def _handle_vault_access_request(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        project_id, workspace_id = await self._resolve_memory_action_context(request)
        vault_request, decision = await self._memory_console_service.request_vault_access(
            actor_id=request.actor.actor_id,
            actor_label=request.actor.actor_label,
            active_project_id=project_id,
            active_workspace_id=workspace_id or "",
            project_id=project_id,
            workspace_id=workspace_id or "",
            scope_id=self._param_str(request.params, "scope_id") or None,
            partition=self._param_str(request.params, "partition"),
            subject_key=self._param_str(request.params, "subject_key") or None,
            reason=self._param_str(request.params, "reason"),
        )
        if not decision.allowed or vault_request is None:
            return self._rejected_result(
                request=request,
                code=decision.reason_code,
                message=decision.message,
            )
        return self._completed_result(
            request=request,
            code="VAULT_ACCESS_REQUEST_CREATED",
            message="已创建 Vault 授权申请。",
            data={"request_id": vault_request.request_id},
            resource_refs=[
                self._resource_ref("vault_authorization", "vault:authorization"),
                self._resource_ref("memory_console", "memory:overview"),
            ],
            target_refs=[
                ControlPlaneTargetRef(
                    target_type="vault_request",
                    target_id=vault_request.request_id,
                )
            ],
        )

    async def _handle_vault_access_resolve(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        request_id = self._param_str(request.params, "request_id")
        decision_raw = self._param_str(request.params, "decision").lower()
        if not request_id:
            raise ControlPlaneActionError("REQUEST_ID_REQUIRED", "request_id 不能为空")
        if decision_raw not in {"approve", "reject"}:
            raise ControlPlaneActionError(
                "VAULT_ACCESS_DECISION_INVALID",
                "decision 必须是 approve/reject",
            )
        try:
            resolved_request, grant = await self._memory_console_service.resolve_vault_access(
                request_id=request_id,
                decision=VaultAccessDecision(decision_raw),
                actor_id=request.actor.actor_id,
                actor_label=request.actor.actor_label,
                expires_in_seconds=self._param_int(
                    request.params,
                    "expires_in_seconds",
                    default=0,
                ),
            )
        except MemoryConsoleError as exc:
            return self._rejected_result(
                request=request,
                code=exc.code,
                message=str(exc),
            )
        code = (
            "VAULT_ACCESS_APPROVED"
            if resolved_request.status is not None and resolved_request.status.value == "approved"
            else "VAULT_ACCESS_REJECTED"
        )
        message = (
            "已批准 Vault 授权申请。"
            if code == "VAULT_ACCESS_APPROVED"
            else "已拒绝 Vault 授权申请。"
        )
        return self._completed_result(
            request=request,
            code=code,
            message=message,
            data={
                "request_id": resolved_request.request_id,
                "grant_id": grant.grant_id if grant is not None else "",
            },
            resource_refs=[
                self._resource_ref("vault_authorization", "vault:authorization"),
            ],
            target_refs=[
                ControlPlaneTargetRef(
                    target_type="vault_request",
                    target_id=resolved_request.request_id,
                )
            ],
        )

    async def _handle_vault_retrieve(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        project_id, workspace_id = await self._resolve_memory_action_context(request)
        code, payload, decision = await self._memory_console_service.retrieve_vault(
            actor_id=request.actor.actor_id,
            actor_label=request.actor.actor_label,
            active_project_id=project_id,
            active_workspace_id=workspace_id or "",
            project_id=project_id,
            workspace_id=workspace_id or "",
            scope_id=self._param_str(request.params, "scope_id") or None,
            partition=self._param_str(request.params, "partition"),
            subject_key=self._param_str(request.params, "subject_key") or None,
            query=self._param_str(request.params, "query") or None,
            grant_id=self._param_str(request.params, "grant_id") or None,
        )
        if code != "VAULT_RETRIEVE_AUTHORIZED":
            return self._rejected_result(
                request=request,
                code=code if decision.allowed else decision.reason_code,
                message=("当前没有可用的 Vault 授权。" if decision.allowed else decision.message),
                target_refs=self._memory_target_refs(request),
            )
        return self._completed_result(
            request=request,
            code=code,
            message="已返回授权范围内的 Vault 检索结果。",
            data=payload,
            resource_refs=[
                self._resource_ref("vault_authorization", "vault:authorization"),
            ],
            target_refs=self._memory_target_refs(request),
        )

    async def _handle_memory_export_inspect(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        scope_ids = self._param_list(request.params, "scope_ids")
        project_id, workspace_id = await self._resolve_memory_action_context(request)
        code, payload, decision = await self._memory_console_service.inspect_export(
            active_project_id=project_id,
            active_workspace_id=workspace_id or "",
            project_id=project_id,
            workspace_id=workspace_id or "",
            scope_ids=scope_ids or None,
            include_history=self._param_bool(request.params, "include_history"),
            include_vault_refs=self._param_bool(request.params, "include_vault_refs"),
        )
        if code != "MEMORY_EXPORT_INSPECTION_READY":
            return self._rejected_result(
                request=request,
                code=code if decision.allowed else decision.reason_code,
                message=("Memory 导出检查存在阻塞项。" if decision.allowed else decision.message),
                target_refs=self._memory_target_refs(request),
            )
        return self._completed_result(
            request=request,
            code=code,
            message="Memory 导出检查已就绪。",
            data=payload,
            resource_refs=[self._resource_ref("memory_console", "memory:overview")],
            target_refs=self._memory_target_refs(request),
        )

    async def _handle_memory_restore_verify(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        snapshot_ref = self._param_str(request.params, "snapshot_ref")
        if not snapshot_ref:
            raise ControlPlaneActionError("SNAPSHOT_REF_REQUIRED", "snapshot_ref 不能为空")
        project_id, _ = await self._resolve_memory_action_context(request)
        code, payload, decision = await self._memory_console_service.verify_restore(
            actor_id=request.actor.actor_id,
            active_project_id=project_id,
            active_workspace_id=self._param_str(request.params, "workspace_id"),
            project_id=project_id,
            workspace_id=self._param_str(request.params, "workspace_id"),
            snapshot_ref=snapshot_ref,
            target_scope_mode=self._param_str(
                request.params,
                "target_scope_mode",
                default="current_project",
            ),
            scope_ids=self._param_list(request.params, "scope_ids") or None,
        )
        if code != "MEMORY_RESTORE_VERIFICATION_READY":
            return self._rejected_result(
                request=request,
                code=code if decision.allowed else decision.reason_code,
                message=("Memory 恢复校验存在阻塞项。" if decision.allowed else decision.message),
                target_refs=self._memory_target_refs(request),
            )
        return self._completed_result(
            request=request,
            code=code,
            message="Memory 恢复校验已通过。",
            data=payload,
            resource_refs=[self._resource_ref("memory_console", "memory:overview")],
            target_refs=self._memory_target_refs(request),
        )

    async def _handle_session_focus(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        session = await self._resolve_session_projection_target(request)
        current_state = self._state_store.load()
        state = current_state.model_copy(
            update={
                "focused_session_id": session.session_id,
                "focused_thread_id": session.thread_id,
                "new_conversation_token": "",
                "new_conversation_project_id": "",
                "new_conversation_workspace_id": "",
                "new_conversation_agent_profile_id": "",
                "selected_project_id": session.project_id or current_state.selected_project_id,
                "selected_workspace_id": (
                    session.workspace_id or current_state.selected_workspace_id
                ),
                "updated_at": datetime.now(tz=UTC),
            }
        )
        self._state_store.save(state)
        if session.project_id:
            project = await self._stores.project_store.get_project(session.project_id)
            workspace = (
                await self._stores.project_store.get_workspace(session.workspace_id)
                if session.workspace_id
                else None
            )
            if project is not None:
                if workspace is None or workspace.project_id != project.project_id:
                    workspace = await self._stores.project_store.get_primary_workspace(
                        project.project_id
                    )
                await self._sync_web_project_selector_state(
                    project=project,
                    workspace=workspace,
                    source="session_focus",
                )
                await self._sync_policy_engine_for_project(project)
                await self._stores.conn.commit()
        return self._completed_result(
            request=request,
            code="SESSION_FOCUSED",
            message="已更新当前聚焦会话",
            data={
                "session_id": session.session_id,
                "thread_id": session.thread_id,
                "project_id": session.project_id,
                "workspace_id": session.workspace_id,
            },
            resource_refs=[self._resource_ref("session_projection", "sessions:overview")],
            target_refs=[
                ControlPlaneTargetRef(target_type="session", target_id=session.session_id),
                ControlPlaneTargetRef(target_type="thread", target_id=session.thread_id),
            ],
        )

    async def _handle_session_unfocus(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        state = self._state_store.load()
        previous_session_id = state.focused_session_id.strip()
        previous_thread_id = state.focused_thread_id.strip()
        self._state_store.save(
            state.model_copy(
                update={
                    "focused_session_id": "",
                    "focused_thread_id": "",
                    "updated_at": datetime.now(tz=UTC),
                }
            )
        )
        target_refs: list[ControlPlaneTargetRef] = []
        if previous_session_id:
            target_refs.append(
                ControlPlaneTargetRef(target_type="session", target_id=previous_session_id)
            )
        if previous_thread_id:
            target_refs.append(
                ControlPlaneTargetRef(target_type="thread", target_id=previous_thread_id)
            )
        return self._completed_result(
            request=request,
            code="SESSION_UNFOCUSED",
            message="已取消当前聚焦会话",
            data={
                "previous_session_id": previous_session_id,
                "previous_thread_id": previous_thread_id,
            },
            resource_refs=[self._resource_ref("session_projection", "sessions:overview")],
            target_refs=target_refs,
        )

    async def _handle_session_new(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        target = await self._resolve_session_projection_target(
            request,
            allow_empty=True,
            use_focused_when_empty=True,
        )
        _, selected_project, selected_workspace, _ = await self._resolve_selection()
        requested_agent_profile_id = str(
            request.params.get("agent_profile_id", "")
        ).strip()
        if requested_agent_profile_id:
            matched_profile = await self._resolve_direct_session_worker_profile(
                requested_agent_profile_id
            )
            if matched_profile is None:
                return self._rejected_result(
                    request=request,
                    code="SESSION_AGENT_PROFILE_NOT_FOUND",
                    message="指定的 Agent 当前不可用，无法作为新会话入口。",
                )
        token = str(ULID())
        state = self._state_store.load().model_copy(
            update={
                "focused_session_id": "",
                "focused_thread_id": "",
                "new_conversation_token": token,
                "new_conversation_project_id": (
                    selected_project.project_id if selected_project is not None else ""
                ),
                "new_conversation_workspace_id": (
                    selected_workspace.workspace_id if selected_workspace is not None else ""
                ),
                "new_conversation_agent_profile_id": requested_agent_profile_id,
                "updated_at": datetime.now(tz=UTC),
            }
        )
        self._state_store.save(state)
        target_refs: list[ControlPlaneTargetRef] = []
        if target is not None:
            target_refs = [
                ControlPlaneTargetRef(target_type="session", target_id=target.session_id),
                ControlPlaneTargetRef(target_type="thread", target_id=target.thread_id),
            ]
        return self._completed_result(
            request=request,
            code="SESSION_NEW_READY",
            message="已切换到新的会话起点",
            data={
                "new_conversation_token": token,
                "project_id": selected_project.project_id if selected_project is not None else "",
                "workspace_id": (
                    selected_workspace.workspace_id if selected_workspace is not None else ""
                ),
                "agent_profile_id": requested_agent_profile_id,
                "previous_session_id": target.session_id if target is not None else "",
                "previous_thread_id": target.thread_id if target is not None else "",
                "previous_task_id": target.task_id if target is not None else "",
            },
            resource_refs=[self._resource_ref("session_projection", "sessions:overview")],
            target_refs=target_refs,
        )

    async def _handle_session_create_with_project(
        self, request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        """创建新 Project + Session + 行为文件骨架，返回 session_id 和 conversation token。"""
        import re

        # 参数解析
        worker_profile_id = str(request.params.get("agent_profile_id", "")).strip()
        project_name = str(request.params.get("project_name", "")).strip()
        if not project_name:
            return self._rejected_result(
                request=request,
                code="SESSION_CREATE_MISSING_NAME",
                message="请为新对话输入一个名字。",
            )
        if not worker_profile_id:
            return self._rejected_result(
                request=request,
                code="SESSION_CREATE_MISSING_AGENT",
                message="请选择一个 Agent 来承接新对话。",
            )

        matched_profile = await self._resolve_direct_session_worker_profile(worker_profile_id)
        if matched_profile is None:
            return self._rejected_result(
                request=request,
                code="SESSION_AGENT_PROFILE_NOT_FOUND",
                message="指定的 Agent 当前不可用，无法作为新会话入口。",
            )

        # 生成 slug 并校验唯一性
        slug = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", project_name.lower()).strip("-") or "session"
        # 如果 slug 完全不含 ascii 字母，用 ULID 后缀
        if not re.search(r"[a-z0-9]", slug):
            slug = f"session-{str(ULID())[-6:]}"

        existing_project = await self._stores.project_store.get_project_by_slug(slug)
        if existing_project is not None:
            workspace = await self._stores.project_store.get_primary_workspace(
                existing_project.project_id
            )
            existing_session = await self._ensure_existing_project_session(
                project=existing_project,
                workspace=workspace,
            )
            if existing_session is not None:
                session_anchor = str(
                    existing_session.thread_id
                    or existing_session.legacy_session_id
                    or existing_session.agent_session_id
                ).strip()
                projected_session_id = build_projected_session_id(
                    thread_id=session_anchor,
                    surface=(
                        "web"
                        if existing_session.surface in {"", "chat", "web"}
                        else existing_session.surface
                    ),
                    scope_id=(
                        f"workspace:{workspace.workspace_id}:chat:web:{session_anchor}"
                        if workspace is not None and session_anchor
                        else ""
                    ),
                    project_id=existing_project.project_id,
                    workspace_id=workspace.workspace_id if workspace is not None else "",
                )
                existing_runtime = await self._stores.agent_context_store.get_agent_runtime(
                    existing_session.agent_runtime_id
                )
                existing_owner_profile_id = ""
                if existing_runtime is not None:
                    existing_owner_profile_id = str(
                        existing_runtime.worker_profile_id or existing_runtime.agent_profile_id or ""
                    ).strip()
                return self._completed_result(
                    request=request,
                    code="SESSION_OPENED_EXISTING_PROJECT",
                    message=f"已打开现有对话「{existing_project.name}」",
                    data={
                        "session_id": projected_session_id,
                        "agent_session_id": existing_session.agent_session_id,
                        "thread_id": session_anchor,
                        "project_id": existing_project.project_id,
                        "workspace_id": workspace.workspace_id if workspace is not None else "",
                        "agent_profile_id": existing_owner_profile_id or worker_profile_id,
                    },
                    resource_refs=[self._resource_ref("session_projection", "sessions:overview")],
                    target_refs=[
                        ControlPlaneTargetRef(
                            target_type="session",
                            target_id=projected_session_id,
                        ),
                    ],
                )
            return self._rejected_result(
                request=request,
                code="SESSION_CREATE_DUPLICATE_NAME",
                message=f"同名项目「{project_name}」已存在，请换个名字。",
            )

        # direct session 对应独立 project，runtime 始终在新 project 下新建
        agent_runtime_id = ""

        # 创建 Project
        now = datetime.now(tz=UTC)
        project_id = f"project-{str(ULID())}"
        project = Project(
            project_id=project_id,
            slug=slug,
            name=project_name,
            description=f"由用户创建的会话项目：{project_name}",
            status="active",
            is_default=False,
            default_agent_profile_id=worker_profile_id,
            primary_agent_id=agent_runtime_id,
            created_at=now,
            updated_at=now,
        )
        await self._stores.project_store.create_project(project)

        # 创建 Workspace
        workspace_id = f"workspace-{str(ULID())}"
        workspace = Workspace(
            workspace_id=workspace_id,
            project_id=project_id,
            slug="primary",
            name="Primary",
            kind="primary",
            created_at=now,
            updated_at=now,
        )
        await self._stores.project_store.create_workspace(workspace)

        # 创建行为文件骨架
        ensure_filesystem_skeleton(
            self._project_root,
            project_slug=slug,
        )
        if matched_profile is not None:
            agent_slug = resolve_behavior_agent_slug(matched_profile)
            materialize_agent_behavior_files(
                self._project_root,
                agent_slug=agent_slug,
                agent_name=matched_profile.name,
                is_worker_profile=True,
            )

        # 确保有真实的 AgentRuntime（FK 约束要求 agent_runtime_id 必须存在）
        if not agent_runtime_id:
            new_runtime = AgentRuntime(
                agent_runtime_id=f"runtime-{str(ULID())}",
                project_id=project_id,
                workspace_id=workspace_id,
                worker_profile_id=worker_profile_id,
                role=AgentRuntimeRole.WORKER,
                name=matched_profile.name,
                persona_summary=matched_profile.summary,
                status=AgentRuntimeStatus.ACTIVE,
                created_at=now,
                updated_at=now,
            )
            await self._stores.agent_context_store.save_agent_runtime(new_runtime)
            agent_runtime_id = new_runtime.agent_runtime_id
            # 回填 primary_agent_id
            project.primary_agent_id = agent_runtime_id
            await self._stores.project_store.set_primary_agent(project_id, agent_runtime_id)

        # 创建 AgentSession（内部 durable ID）与 projected session ID（前端路由 ID）
        session_id = f"session-{str(ULID())}"
        thread_id_seed = f"thread-{str(ULID())}"
        projected_session_id = build_projected_session_id(
            thread_id=thread_id_seed,
            surface="web",
            scope_id=f"workspace:{workspace_id}:chat:web:{thread_id_seed}",
            project_id=project_id,
            workspace_id=workspace_id,
        )
        session = AgentSession(
            agent_session_id=session_id,
            agent_runtime_id=agent_runtime_id,
            project_id=project_id,
            workspace_id=workspace_id,
            kind=AgentSessionKind.DIRECT_WORKER,
            status=AgentSessionStatus.ACTIVE,
            surface="web",
            thread_id=thread_id_seed,
            legacy_session_id=thread_id_seed,
            created_at=now,
            updated_at=now,
        )
        await self._stores.agent_context_store.save_agent_session(session)

        # 生成 conversation token 并更新 state
        token = str(ULID())
        state = self._state_store.load().model_copy(
            update={
                "focused_session_id": projected_session_id,
                "focused_thread_id": thread_id_seed,
                "new_conversation_token": token,
                "new_conversation_project_id": project_id,
                "new_conversation_workspace_id": workspace_id,
                "new_conversation_agent_profile_id": worker_profile_id,
                "selected_project_id": project_id,
                "selected_workspace_id": workspace_id,
                "updated_at": now,
            }
        )
        self._state_store.save(state)
        await self._sync_web_project_selector_state(
            project=project,
            workspace=workspace,
            source="session_create_with_project",
        )
        await self._sync_policy_engine_for_project(project)

        await self._stores.conn.commit()

        return self._completed_result(
            request=request,
            code="SESSION_CREATED_WITH_PROJECT",
            message=f"已创建对话「{project_name}」",
            data={
                "session_id": projected_session_id,
                "agent_session_id": session_id,
                "thread_id": thread_id_seed,
                "project_id": project_id,
                "workspace_id": workspace_id,
                "new_conversation_token": token,
                "agent_profile_id": worker_profile_id,
            },
            resource_refs=[self._resource_ref("session_projection", "sessions:overview")],
            target_refs=[
                ControlPlaneTargetRef(target_type="session", target_id=projected_session_id),
            ],
        )

    async def _ensure_existing_project_session(
        self,
        *,
        project: Project,
        workspace: Workspace | None,
    ) -> AgentSession | None:
        existing_session = await self._stores.agent_context_store.get_active_session_for_project(
            project.project_id
        )
        if existing_session is not None:
            return existing_session

        if project.is_default:
            agent_profile = await ensure_default_project_agent_profile(self._stores, project)
            if agent_profile is not None:
                await ensure_butler_runtime_and_session(
                    self._stores,
                    project,
                    workspace,
                    agent_profile,
                )
                await self._stores.conn.commit()
                return await self._stores.agent_context_store.get_active_session_for_project(
                    project.project_id,
                    kind=AgentSessionKind.BUTLER_MAIN,
                )

        return None

    async def _resolve_direct_session_worker_profile(
        self,
        profile_id: str,
    ) -> WorkerProfile | None:
        profile = await self._stores.agent_context_store.get_worker_profile(profile_id)
        if profile is None or profile.status == WorkerProfileStatus.ARCHIVED:
            return None
        return profile

    # ------------------------------------------------------------------
    # Phase 4: 主 Agent 查询 + 管理工具
    # ------------------------------------------------------------------

    async def _handle_agent_list_models(
        self, request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        """返回已配置的模型别名列表，供主 Agent 选择。"""
        config = load_config(self._project_root)
        if config is None:
            return self._completed_result(
                request=request,
                code="AGENT_MODELS_LISTED",
                message="尚未配置 octoagent.yaml",
                data={"model_aliases": {}},
            )
        aliases: dict[str, dict[str, str]] = {}
        for alias_key, alias_val in config.model_aliases.items():
            aliases[alias_key] = {
                "provider": alias_val.provider,
                "model": alias_val.model,
                "description": alias_val.description,
            }
        return self._completed_result(
            request=request,
            code="AGENT_MODELS_LISTED",
            message=f"共 {len(aliases)} 个模型别名",
            data={"model_aliases": aliases},
        )

    def _list_available_model_aliases(self) -> list[str]:
        """返回当前配置中可用于 Agent / Worker 的模型别名集合。"""
        try:
            config = load_config(self._project_root)
        except Exception:
            return ["main"]
        if config is None or not config.model_aliases:
            return ["main"]
        aliases = sorted(alias for alias in config.model_aliases.keys() if alias.strip())
        return aliases or ["main"]

    def _validate_model_alias(self, model_alias: str) -> tuple[bool, list[str]]:
        available_aliases = self._list_available_model_aliases()
        return model_alias.strip() in available_aliases, available_aliases

    async def _handle_agent_list_archetypes(
        self, request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        """返回内建 Worker archetype 列表。"""
        archetypes = [
            {
                "type": "general",
                "label": "通用",
                "description": "通用 Worker，适合多数场景",
            },
            {
                "type": "ops",
                "label": "运维",
                "description": "侧重运维操作（文件系统、Docker、监控）",
            },
            {
                "type": "research",
                "label": "调研",
                "description": "侧重信息搜集、网络检索、文档分析",
            },
            {
                "type": "dev",
                "label": "开发",
                "description": "侧重代码编写、测试、构建流程",
            },
        ]
        return self._completed_result(
            request=request,
            code="AGENT_ARCHETYPES_LISTED",
            message=f"共 {len(archetypes)} 个内建 archetype",
            data={"archetypes": archetypes},
        )

    async def _handle_agent_list_tool_profiles(
        self, request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        """返回工具权限等级列表。"""
        profiles = [
            {
                "profile": "minimal",
                "label": "最小",
                "description": "只读工具（查询、检索）",
            },
            {
                "profile": "standard",
                "label": "标准",
                "description": "读写工具（文件操作、记忆写入）",
            },
            {
                "profile": "privileged",
                "label": "特权",
                "description": "外部 API、Docker 执行、shell 命令",
            },
        ]
        return self._completed_result(
            request=request,
            code="AGENT_TOOL_PROFILES_LISTED",
            message=f"共 {len(profiles)} 个权限等级",
            data={"tool_profiles": profiles},
        )

    async def _handle_agent_create_worker_with_project(
        self, request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        """主 Agent 创建 Worker + Project + Session + 行为文件。"""
        import re

        # 参数解析
        worker_name = str(request.params.get("worker_name", "")).strip()
        project_name = str(request.params.get("project_name", "")).strip()
        model_alias = str(request.params.get("model_alias", "main")).strip()
        tool_profile = str(request.params.get("tool_profile", "minimal")).strip()
        project_goal = str(request.params.get("project_goal", "")).strip()
        # Feature 061 T-030: permission_preset + role_card 参数
        permission_preset = str(request.params.get("permission_preset", "normal")).strip().lower()
        role_card = str(request.params.get("role_card", "")).strip()

        if not worker_name:
            return self._rejected_result(
                request=request,
                code="WORKER_CREATE_MISSING_NAME",
                message="请为新 Worker 输入名称。",
            )
        if not project_name:
            project_name = worker_name

        # 验证 tool_profile
        valid_profiles = {"minimal", "standard", "privileged"}
        if tool_profile not in valid_profiles:
            return self._rejected_result(
                request=request,
                code="WORKER_CREATE_INVALID_TOOL_PROFILE",
                message=f"tool_profile 必须是 {', '.join(sorted(valid_profiles))} 之一。",
            )

        # Feature 061 T-030: 验证 permission_preset
        valid_presets = {"minimal", "normal", "full"}
        if permission_preset not in valid_presets:
            return self._rejected_result(
                request=request,
                code="WORKER_CREATE_INVALID_PRESET",
                message=f"permission_preset 必须是 {', '.join(sorted(valid_presets))} 之一。",
            )

        # 验证 model_alias 存在
        model_alias_valid, available_aliases = self._validate_model_alias(model_alias)
        if not model_alias_valid:
            available = ", ".join(available_aliases)
            return self._rejected_result(
                request=request,
                code="WORKER_CREATE_INVALID_MODEL",
                message=f"模型别名 '{model_alias}' 不存在，可选：{available}",
            )

        now = datetime.now(tz=UTC)

        # 创建 WorkerProfile
        worker_profile_id = f"worker-profile-{str(ULID())}"
        worker_profile = WorkerProfile(
            profile_id=worker_profile_id,
            scope=AgentProfileScope.PROJECT,
            project_id="",  # 后面回填
            name=worker_name,
            summary=project_goal or f"{worker_name} Worker",
            model_alias=model_alias,
            tool_profile=tool_profile,
            status=WorkerProfileStatus.ACTIVE,
            origin_kind=WorkerProfileOriginKind.CUSTOM,
            created_at=now,
            updated_at=now,
        )
        await self._stores.agent_context_store.save_worker_profile(worker_profile)

        # 从 WorkerProfile 同步生成 AgentProfile
        agent_profile_id = f"agent-profile-{worker_profile_id}"
        agent_profile = AgentProfile(
            profile_id=agent_profile_id,
            scope=AgentProfileScope.PROJECT,
            project_id="",
            name=worker_name,
            persona_summary=project_goal,
            model_alias=model_alias,
            tool_profile=tool_profile,
        )
        await self._stores.agent_context_store.save_agent_profile(agent_profile)

        # 创建 Project
        slug = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", project_name.lower()).strip("-") or "worker"
        if not re.search(r"[a-z0-9]", slug):
            slug = f"worker-{str(ULID())[-6:]}"

        # 避免 slug 冲突
        existing = await self._stores.project_store.get_project_by_slug(slug)
        if existing is not None:
            slug = f"{slug}-{str(ULID())[-6:]}"

        project_id = f"project-{str(ULID())}"
        project = Project(
            project_id=project_id,
            slug=slug,
            name=project_name,
            description=project_goal or f"Worker「{worker_name}」的工作空间",
            status="active",
            is_default=False,
            default_agent_profile_id=agent_profile_id,
            primary_agent_id="",  # Worker 的 runtime_id 创建后回填
            created_at=now,
            updated_at=now,
        )
        await self._stores.project_store.create_project(project)

        # 回填 WorkerProfile 的 project_id
        worker_profile.project_id = project_id
        await self._stores.agent_context_store.save_worker_profile(worker_profile)
        agent_profile.project_id = project_id
        await self._stores.agent_context_store.save_agent_profile(agent_profile)

        # 创建 Workspace
        workspace_id = f"workspace-{str(ULID())}"
        workspace = Workspace(
            workspace_id=workspace_id,
            project_id=project_id,
            slug="primary",
            name="Primary",
            kind="primary",
            created_at=now,
            updated_at=now,
        )
        await self._stores.project_store.create_workspace(workspace)

        # 创建行为文件骨架
        ensure_filesystem_skeleton(
            self._project_root,
            project_slug=slug,
        )
        agent_slug = resolve_behavior_agent_slug(agent_profile)
        materialize_agent_behavior_files(
            self._project_root,
            agent_slug=agent_slug,
            agent_name=worker_name,
            is_worker_profile=True,
        )

        # 创建 Worker AgentRuntime（FK 约束要求 agent_runtime_id 必须存在）
        runtime_id = f"runtime-{str(ULID())}"
        worker_runtime = AgentRuntime(
            agent_runtime_id=runtime_id,
            project_id=project_id,
            workspace_id=workspace_id,
            agent_profile_id=agent_profile_id,
            worker_profile_id=worker_profile_id,
            role=AgentRuntimeRole.WORKER,
            name=worker_name,
            persona_summary=project_goal,
            status=AgentRuntimeStatus.ACTIVE,
            permission_preset=permission_preset,
            role_card=role_card,
            metadata={},
            created_at=now,
            updated_at=now,
        )
        await self._stores.agent_context_store.save_agent_runtime(worker_runtime)

        # 创建 AgentSession
        session_id = f"session-{str(ULID())}"
        session = AgentSession(
            agent_session_id=session_id,
            agent_runtime_id=runtime_id,
            project_id=project_id,
            workspace_id=workspace_id,
            kind=AgentSessionKind.WORKER_INTERNAL,
            status=AgentSessionStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        await self._stores.agent_context_store.save_agent_session(session)

        # 回填 Project 的 primary_agent_id
        await self._stores.project_store.set_primary_agent(project_id, runtime_id)

        await self._stores.conn.commit()

        return self._completed_result(
            request=request,
            code="WORKER_CREATED_WITH_PROJECT",
            message=f"已创建 Worker「{worker_name}」+ 项目「{project_name}」",
            data={
                "worker_profile_id": worker_profile_id,
                "agent_profile_id": agent_profile_id,
                "project_id": project_id,
                "workspace_id": workspace_id,
                "session_id": session_id,
                "runtime_id": runtime_id,
            },
            resource_refs=[
                self._resource_ref("worker_profiles", "worker_profiles:overview"),
                self._resource_ref("session_projection", "sessions:overview"),
            ],
        )

    async def _handle_session_reset(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        session = await self._resolve_session_projection_target(
            request,
            use_focused_when_empty=True,
        )
        now = datetime.now(tz=UTC)
        reset_context = False
        session_state = await self._stores.agent_context_store.get_session_context(session.session_id)
        if session_state is None and session.thread_id:
            session_states = await self._stores.agent_context_store.list_session_contexts(
                project_id=session.project_id or None,
                workspace_id=session.workspace_id or None,
            )
            session_state = next(
                (item for item in session_states if item.thread_id == session.thread_id),
                None,
            )
        if session_state is not None:
            await self._stores.agent_context_store.save_session_context(
                session_state.model_copy(
                    update={
                        "recent_turn_refs": [],
                        "recent_artifact_refs": [],
                        "rolling_summary": "",
                        "summary_artifact_id": "",
                        "last_context_frame_id": "",
                        "last_recall_frame_id": "",
                        "updated_at": now,
                    }
                )
            )
            reset_context = True

        related_sessions: list[AgentSession] = []
        seen_agent_session_ids: set[str] = set()
        for legacy_session_id in {session.session_id, session.thread_id}:
            normalized = str(legacy_session_id).strip()
            if not normalized:
                continue
            candidates = await self._stores.agent_context_store.list_agent_sessions(
                legacy_session_id=normalized,
                project_id=session.project_id or None,
                workspace_id=session.workspace_id or None,
                limit=200,
            )
            for item in candidates:
                if item.agent_session_id in seen_agent_session_ids:
                    continue
                seen_agent_session_ids.add(item.agent_session_id)
                related_sessions.append(item)
        reset_agent_sessions = 0
        for item in related_sessions:
            await self._stores.agent_context_store.delete_agent_session_turns(
                agent_session_id=item.agent_session_id
            )
            metadata = dict(item.metadata)
            metadata["recent_transcript"] = []
            metadata["rolling_summary"] = ""
            metadata["latest_model_reply_summary"] = ""
            metadata["latest_model_reply_preview"] = ""
            metadata["latest_compaction_summary"] = ""
            metadata["latest_compaction_summary_artifact_id"] = ""
            await self._stores.agent_context_store.save_agent_session(
                item.model_copy(
                    update={
                        "status": AgentSessionStatus.CLOSED,
                        "last_context_frame_id": "",
                        "last_recall_frame_id": "",
                        "recent_transcript": [],
                        "rolling_summary": "",
                        "metadata": metadata,
                        "updated_at": now,
                        "closed_at": now,
                    }
                )
            )
            reset_agent_sessions += 1

        token = str(ULID())
        current_state = self._state_store.load()
        state = current_state.model_copy(
            update={
                "focused_session_id": "",
                "focused_thread_id": "",
                "new_conversation_token": token,
                "new_conversation_project_id": session.project_id,
                "new_conversation_workspace_id": session.workspace_id,
                "new_conversation_agent_profile_id": "",
                "selected_project_id": session.project_id or current_state.selected_project_id,
                "selected_workspace_id": (
                    session.workspace_id or current_state.selected_workspace_id
                ),
                "updated_at": now,
            }
        )
        self._state_store.save(state)
        if session.project_id:
            project = await self._stores.project_store.get_project(session.project_id)
            workspace = (
                await self._stores.project_store.get_workspace(session.workspace_id)
                if session.workspace_id
                else None
            )
            if project is not None:
                if workspace is None or workspace.project_id != project.project_id:
                    workspace = await self._stores.project_store.get_primary_workspace(
                        project.project_id
                    )
                await self._sync_web_project_selector_state(
                    project=project,
                    workspace=workspace,
                    source="session_reset",
                )
                await self._sync_policy_engine_for_project(project)
                await self._stores.conn.commit()

        return self._completed_result(
            request=request,
            code="SESSION_RESET",
            message="已清空该会话的 continuity，并准备新的对话起点",
            data={
                "session_id": session.session_id,
                "thread_id": session.thread_id,
                "task_id": session.task_id,
                "reset_session_context": reset_context,
                "reset_agent_session_count": reset_agent_sessions,
                "new_conversation_token": token,
                "project_id": session.project_id,
                "workspace_id": session.workspace_id,
            },
            resource_refs=[self._resource_ref("session_projection", "sessions:overview")],
            target_refs=[
                ControlPlaneTargetRef(target_type="session", target_id=session.session_id),
                ControlPlaneTargetRef(target_type="thread", target_id=session.thread_id),
                ControlPlaneTargetRef(target_type="task", target_id=session.task_id),
            ],
        )

    async def _handle_session_export(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        thread_id = str(request.params.get("thread_id", "")).strip()
        session_id = str(request.params.get("session_id", "")).strip()
        task_id = str(request.params.get("task_id", "")).strip()
        since = request.params.get("since")
        until = request.params.get("until")
        task_ids: list[str] | None = None
        if session_id and not thread_id and not task_id:
            session = await self._resolve_session_projection_target(request)
            _, selected_project, selected_workspace, _ = await self._resolve_selection()
            session_tasks = await self._list_tasks_for_projected_session(
                session_id=session.session_id,
                selected_project=selected_project,
                selected_workspace=selected_workspace,
            )
            task_ids = [item.task_id for item in session_tasks]
            if not task_ids and session.task_id:
                task_ids = [session.task_id]
        manifest = await BackupService(
            self._project_root,
            store_group=self._stores,
        ).export_chats(
            task_id=task_id or None,
            task_ids=task_ids,
            thread_id=thread_id or None,
            since=since,
            until=until,
        )
        return self._completed_result(
            request=request,
            code="SESSION_EXPORTED",
            message="已导出会话数据",
            data=manifest.model_dump(mode="json"),
            resource_refs=[self._resource_ref("session_projection", "sessions:overview")],
        )

    async def _resolve_session_projection_target(
        self,
        request: ActionRequestEnvelope,
        *,
        allow_empty: bool = False,
        use_focused_when_empty: bool = False,
    ) -> SessionProjectionItem | None:
        requested_session_id = self._param_str(request.params, "session_id")
        requested_thread_id = self._param_str(request.params, "thread_id")
        requested_task_id = self._param_str(request.params, "task_id")

        session_items = await self._build_session_projection_items()
        focused_session_id, focused_thread_id = self._resolve_projected_focus(
            state=self._state_store.load(),
            session_items=session_items,
        )
        if not requested_session_id and not requested_thread_id and not requested_task_id:
            if use_focused_when_empty and focused_session_id:
                requested_session_id = focused_session_id
            elif use_focused_when_empty and focused_thread_id:
                requested_thread_id = focused_thread_id
            elif allow_empty:
                return None
            else:
                raise ControlPlaneActionError(
                    "SESSION_ID_REQUIRED",
                    "session_id / thread_id / task_id 至少需要一个",
                )

        if requested_session_id:
            session = next(
                (item for item in session_items if item.session_id == requested_session_id),
                None,
            )
            if session is not None:
                return session
            thread_matches = [
                item for item in session_items if item.thread_id == requested_session_id
            ]
            if len(thread_matches) == 1:
                return thread_matches[0]
            if len(thread_matches) > 1:
                raise ControlPlaneActionError(
                    "SESSION_ID_REQUIRED",
                    "当前作用域存在多个同 thread_id 会话，请显式提供 session_id",
                )
            raise ControlPlaneActionError(
                "SESSION_NOT_FOUND",
                "当前作用域找不到对应的 session_id",
            )

        if requested_task_id:
            session = next((item for item in session_items if item.task_id == requested_task_id), None)
            if session is None:
                raise ControlPlaneActionError(
                    "TASK_NOT_FOUND",
                    "当前作用域找不到对应的 task_id",
                )
            return session

        matches = [item for item in session_items if item.thread_id == requested_thread_id]
        if not matches:
            raise ControlPlaneActionError(
                "THREAD_NOT_FOUND",
                "当前作用域找不到对应的 thread_id",
            )
        if len(matches) > 1:
            raise ControlPlaneActionError(
                "SESSION_ID_REQUIRED",
                "当前作用域存在多个同 thread_id 会话，请显式提供 session_id",
            )
        return matches[0]

    async def _handle_session_interrupt(
        self, request: ActionRequestEnvelope
    ) -> ActionResultEnvelope:
        task_id = str(request.params.get("task_id", "")).strip()
        if not task_id:
            raise ControlPlaneActionError("TASK_ID_REQUIRED", "task_id 不能为空")
        existing = await self._stores.task_store.get_task(task_id)
        if existing is None:
            raise ControlPlaneActionError("TASK_NOT_FOUND", "任务不存在")
        task = None
        if self._task_runner is not None:
            cancelled = await self._task_runner.cancel_task(task_id)
            if not cancelled:
                raise ControlPlaneActionError("TASK_CANCEL_NOT_ALLOWED", "当前状态不允许取消")
            task = await self._stores.task_store.get_task(task_id)
        else:
            task = await TaskService(self._stores, self._sse_hub).cancel_task(task_id)
            if task is None:
                raise ControlPlaneActionError("TASK_NOT_FOUND", "任务不存在")
        return self._completed_result(
            request=request,
            code="TASK_CANCELLED",
            message="已取消任务",
            data={"task_id": task_id, "status": task.status.value},
            resource_refs=[self._resource_ref("session_projection", "sessions:overview")],
            target_refs=[ControlPlaneTargetRef(target_type="task", target_id=task_id)],
        )

    async def _handle_session_resume(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        task_id = str(request.params.get("task_id", "")).strip()
        if not task_id:
            raise ControlPlaneActionError("TASK_ID_REQUIRED", "task_id 不能为空")
        if self._task_runner is None:
            raise ControlPlaneActionError(
                "TASK_RUNNER_UNAVAILABLE", "当前 runtime 未启用 TaskRunner"
            )
        result = await self._task_runner.resume_task(task_id, trigger="manual")
        if not result.ok:
            raise ControlPlaneActionError("TASK_RESUME_FAILED", result.message)
        return self._completed_result(
            request=request,
            code="TASK_RESUMED",
            message=result.message,
            data=result.model_dump(mode="json"),
            resource_refs=[self._resource_ref("session_projection", "sessions:overview")],
            target_refs=[ControlPlaneTargetRef(target_type="task", target_id=task_id)],
        )

    async def _handle_worker_review(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        work_id = str(request.params.get("work_id", "")).strip()
        if not work_id:
            raise ControlPlaneActionError("WORK_ID_REQUIRED", "work_id 不能为空")
        if self._capability_pack_service is None:
            raise ControlPlaneActionError("CAPABILITY_PACK_UNAVAILABLE", "capability pack 不可用")
        await self._get_work_in_scope(work_id)
        plan = await self._capability_pack_service.review_worker_plan(
            work_id=work_id,
            objective=self._param_str(request.params, "objective"),
        )
        return self._completed_result(
            request=request,
            code="WORKER_REVIEW_READY",
            message="已生成 Worker 评审方案。",
            data={"plan": plan.model_dump(mode="json")},
            resource_refs=[self._resource_ref("delegation_plane", "delegation:overview")],
            target_refs=[ControlPlaneTargetRef(target_type="work", target_id=work_id)],
        )

    async def _handle_worker_apply(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        work_id = str(request.params.get("work_id", "")).strip()
        raw_plan = request.params.get("plan")
        if not work_id:
            raise ControlPlaneActionError("WORK_ID_REQUIRED", "work_id 不能为空")
        if not isinstance(raw_plan, dict):
            raise ControlPlaneActionError("WORKER_PLAN_REQUIRED", "plan 必须是 object")
        if self._capability_pack_service is None:
            raise ControlPlaneActionError("CAPABILITY_PACK_UNAVAILABLE", "capability pack 不可用")
        await self._get_work_in_scope(work_id)
        result = await self._capability_pack_service.apply_worker_plan(
            plan={**raw_plan, "work_id": work_id},
            actor=request.actor.actor_id,
        )
        return self._completed_result(
            request=request,
            code="WORKER_PLAN_APPLIED",
            message="已按批准的 Worker 方案执行。",
            data=result,
            resource_refs=[
                self._resource_ref("delegation_plane", "delegation:overview"),
                self._resource_ref("session_projection", "sessions:overview"),
            ],
            target_refs=[ControlPlaneTargetRef(target_type="work", target_id=work_id)],
        )

    async def _handle_work_cancel(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        work_id = str(request.params.get("work_id", "")).strip()
        if not work_id:
            raise ControlPlaneActionError("WORK_ID_REQUIRED", "work_id 不能为空")
        if self._delegation_plane_service is None:
            raise ControlPlaneActionError("DELEGATION_UNAVAILABLE", "delegation plane 不可用")
        work = await self._get_work_in_scope(work_id)
        if self._task_runner is not None:
            descendants = await self._delegation_plane_service.list_descendant_works(work_id)
            task_ids = [item.task_id for item in descendants] + [work.task_id]
            for task_id in dict.fromkeys(task_ids):
                await self._task_runner.cancel_task(task_id)
        updated = await self._delegation_plane_service.cancel_work(
            work_id, reason="control_plane_cancel"
        )
        if updated is None:
            raise ControlPlaneActionError("WORK_NOT_FOUND", "work 不存在")
        return self._completed_result(
            request=request,
            code="WORK_CANCELLED",
            message="已取消 work",
            data=updated.model_dump(mode="json"),
            resource_refs=[
                self._resource_ref("delegation_plane", "delegation:overview"),
                self._resource_ref("skill_pipeline", "pipeline:overview"),
            ],
            target_refs=[ControlPlaneTargetRef(target_type="work", target_id=work_id)],
        )

    async def _handle_work_retry(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        work_id = str(request.params.get("work_id", "")).strip()
        if not work_id:
            raise ControlPlaneActionError("WORK_ID_REQUIRED", "work_id 不能为空")
        if self._delegation_plane_service is None:
            raise ControlPlaneActionError("DELEGATION_UNAVAILABLE", "delegation plane 不可用")
        work = await self._get_work_in_scope(work_id)
        if work.status.value == "deleted":
            raise ControlPlaneActionError("WORK_DELETED", "已删除的 work 不能重试")
        updated = await self._delegation_plane_service.retry_work(work_id)
        if updated is None:
            raise ControlPlaneActionError("WORK_NOT_FOUND", "work 不存在")
        return self._completed_result(
            request=request,
            code="WORK_RETRIED",
            message="已重置 work 为待重试状态",
            data=updated.model_dump(mode="json"),
            resource_refs=[self._resource_ref("delegation_plane", "delegation:overview")],
            target_refs=[ControlPlaneTargetRef(target_type="work", target_id=work_id)],
        )

    async def _handle_work_split(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        work_id = str(request.params.get("work_id", "")).strip()
        if not work_id:
            raise ControlPlaneActionError("WORK_ID_REQUIRED", "work_id 不能为空")
        if self._task_runner is None:
            raise ControlPlaneActionError(
                "TASK_RUNNER_UNAVAILABLE", "当前 runtime 未启用 TaskRunner"
            )
        parent_work = await self._get_work_in_scope(work_id)
        parent_task = await self._stores.task_store.get_task(parent_work.task_id)
        if parent_task is None:
            raise ControlPlaneActionError("PARENT_TASK_NOT_FOUND", "父 task 不存在")

        objectives = request.params.get("objectives", [])
        parsed_objectives = self._coerce_split_objectives(objectives)
        if not parsed_objectives:
            raise ControlPlaneActionError("OBJECTIVES_REQUIRED", "objectives 不能为空")

        worker_type = self._param_str(request.params, "worker_type") or "general"
        target_kind = self._param_str(request.params, "target_kind") or "subagent"
        tool_profile = self._param_str(request.params, "tool_profile") or "minimal"
        child_tasks: list[dict[str, Any]] = []
        for objective in parsed_objectives:
            message = NormalizedMessage(
                channel=parent_task.requester.channel,
                thread_id=f"{parent_task.thread_id}:child:{str(ULID())[:8]}",
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
                    "spawned_by": "control_plane",
                },
                idempotency_key=f"control-plane-split:{parent_task.task_id}:{ULID()}",
            )
            child_task_id, created = await self._task_runner.launch_child_task(message)
            child_tasks.append(
                {
                    "task_id": child_task_id,
                    "created": created,
                    "thread_id": message.thread_id,
                    "objective": objective,
                    "tool_profile": tool_profile,
                }
            )

        return self._completed_result(
            request=request,
            code="WORK_SPLIT_ACCEPTED",
            message="已创建 child works 对应的 child tasks",
            data={
                "work_id": parent_work.work_id,
                "child_tasks": child_tasks,
                "requested_worker_type": worker_type,
                "target_kind": target_kind,
                "tool_profile": tool_profile,
            },
            resource_refs=[
                self._resource_ref("delegation_plane", "delegation:overview"),
                self._resource_ref("session_projection", "sessions:overview"),
            ],
            target_refs=[ControlPlaneTargetRef(target_type="work", target_id=work_id)],
        )

    async def _handle_work_merge(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        work_id = str(request.params.get("work_id", "")).strip()
        if not work_id:
            raise ControlPlaneActionError("WORK_ID_REQUIRED", "work_id 不能为空")
        if self._delegation_plane_service is None:
            raise ControlPlaneActionError("DELEGATION_UNAVAILABLE", "delegation plane 不可用")
        await self._get_work_in_scope(work_id)
        child_works = await self._stores.work_store.list_works(parent_work_id=work_id)
        if not child_works:
            raise ControlPlaneActionError("CHILD_WORKS_REQUIRED", "当前 work 尚未拆分 child works")
        blocking = [
            item.work_id for item in child_works if item.status.value not in _TERMINAL_WORK_STATUSES
        ]
        if blocking:
            raise ControlPlaneActionError(
                "CHILD_WORKS_ACTIVE",
                f"仍有 child works 未完成: {', '.join(blocking)}",
            )
        summary = self._param_str(request.params, "summary") or "merged by control plane"
        updated = await self._delegation_plane_service.merge_work(work_id, summary=summary)
        if updated is None:
            raise ControlPlaneActionError("WORK_NOT_FOUND", "work 不存在")
        return self._completed_result(
            request=request,
            code="WORK_MERGED",
            message="已合并 child works",
            data={
                "work": updated.model_dump(mode="json"),
                "child_work_ids": [item.work_id for item in child_works],
            },
            resource_refs=[self._resource_ref("delegation_plane", "delegation:overview")],
            target_refs=[ControlPlaneTargetRef(target_type="work", target_id=work_id)],
        )

    async def _handle_work_delete(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        work_id = str(request.params.get("work_id", "")).strip()
        if not work_id:
            raise ControlPlaneActionError("WORK_ID_REQUIRED", "work_id 不能为空")
        if self._delegation_plane_service is None:
            raise ControlPlaneActionError("DELEGATION_UNAVAILABLE", "delegation plane 不可用")
        work = await self._get_work_in_scope(work_id)
        descendants = await self._delegation_plane_service.list_descendant_works(work_id)
        active = [
            item.work_id for item in descendants if item.status.value not in _TERMINAL_WORK_STATUSES
        ]
        if work.status.value not in _TERMINAL_WORK_STATUSES:
            active.insert(0, work.work_id)
        if active:
            raise ControlPlaneActionError(
                "WORK_DELETE_REQUIRES_TERMINAL",
                f"存在仍在运行的 work，不能删除: {', '.join(active)}",
            )
        updated = await self._delegation_plane_service.delete_work(
            work_id,
            reason="control_plane_delete",
        )
        if updated is None:
            raise ControlPlaneActionError("WORK_NOT_FOUND", "work 不存在")
        return self._completed_result(
            request=request,
            code="WORK_DELETED",
            message="已删除 work",
            data={
                "work": updated.model_dump(mode="json"),
                "child_work_ids": [item.work_id for item in descendants],
            },
            resource_refs=[self._resource_ref("delegation_plane", "delegation:overview")],
            target_refs=[ControlPlaneTargetRef(target_type="work", target_id=work_id)],
        )

    async def _handle_work_escalate(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        work_id = str(request.params.get("work_id", "")).strip()
        if not work_id:
            raise ControlPlaneActionError("WORK_ID_REQUIRED", "work_id 不能为空")
        if self._delegation_plane_service is None:
            raise ControlPlaneActionError("DELEGATION_UNAVAILABLE", "delegation plane 不可用")
        await self._get_work_in_scope(work_id)
        updated = await self._delegation_plane_service.escalate_work(
            work_id,
            reason="control_plane_escalate",
        )
        if updated is None:
            raise ControlPlaneActionError("WORK_NOT_FOUND", "work 不存在")
        return self._completed_result(
            request=request,
            code="WORK_ESCALATED",
            message="已升级 work",
            data=updated.model_dump(mode="json"),
            resource_refs=[self._resource_ref("delegation_plane", "delegation:overview")],
            target_refs=[ControlPlaneTargetRef(target_type="work", target_id=work_id)],
        )

    async def _handle_pipeline_resume(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        work_id = str(request.params.get("work_id", "")).strip()
        if not work_id:
            raise ControlPlaneActionError("WORK_ID_REQUIRED", "work_id 不能为空")
        if self._delegation_plane_service is None:
            raise ControlPlaneActionError("DELEGATION_UNAVAILABLE", "delegation plane 不可用")
        await self._get_work_in_scope(work_id)
        state_patch = request.params.get("state_patch")
        if state_patch is not None and not isinstance(state_patch, dict):
            raise ControlPlaneActionError("STATE_PATCH_INVALID", "state_patch 必须是 object")
        updated = await self._delegation_plane_service.resume_pipeline(
            work_id,
            state_patch=state_patch if isinstance(state_patch, dict) else None,
        )
        if updated is None:
            raise ControlPlaneActionError("WORK_NOT_FOUND", "work 不存在")
        return self._completed_result(
            request=request,
            code="PIPELINE_RESUMED",
            message="已恢复 pipeline",
            data=updated.model_dump(mode="json"),
            resource_refs=[
                self._resource_ref("delegation_plane", "delegation:overview"),
                self._resource_ref("skill_pipeline", "pipeline:overview"),
            ],
            target_refs=[ControlPlaneTargetRef(target_type="work", target_id=work_id)],
        )

    async def _handle_pipeline_retry_node(
        self, request: ActionRequestEnvelope
    ) -> ActionResultEnvelope:
        work_id = str(request.params.get("work_id", "")).strip()
        if not work_id:
            raise ControlPlaneActionError("WORK_ID_REQUIRED", "work_id 不能为空")
        if self._delegation_plane_service is None:
            raise ControlPlaneActionError("DELEGATION_UNAVAILABLE", "delegation plane 不可用")
        await self._get_work_in_scope(work_id)
        updated = await self._delegation_plane_service.retry_pipeline_node(work_id)
        if updated is None:
            raise ControlPlaneActionError("WORK_NOT_FOUND", "work 不存在")
        return self._completed_result(
            request=request,
            code="PIPELINE_NODE_RETRIED",
            message="已重试当前 pipeline 节点",
            data=updated.model_dump(mode="json"),
            resource_refs=[
                self._resource_ref("delegation_plane", "delegation:overview"),
                self._resource_ref("skill_pipeline", "pipeline:overview"),
            ],
            target_refs=[ControlPlaneTargetRef(target_type="work", target_id=work_id)],
        )

    async def _handle_behavior_read_file(
        self, request: ActionRequestEnvelope
    ) -> ActionResultEnvelope:
        """读取行为文件的内容（从磁盘）。"""
        file_path = str(request.params.get("file_path", "")).strip()
        if not file_path:
            raise ControlPlaneActionError("MISSING_PARAM", "file_path 不能为空")

        try:
            resolved = validate_behavior_file_path(self._project_root, file_path)
        except ValueError as exc:
            raise ControlPlaneActionError("INVALID_PATH", str(exc)) from exc

        if not resolved.exists():
            return self._completed_result(
                request=request,
                code="BEHAVIOR_FILE_NOT_FOUND",
                message="文件不存在，可能尚未 materialize",
                data={"file_path": file_path, "content": "", "exists": False},
            )

        try:
            content = resolved.read_text(encoding="utf-8")
        except Exception as exc:
            raise ControlPlaneActionError(
                "FILE_READ_ERROR", f"读取文件失败: {exc}"
            ) from exc

        return self._completed_result(
            request=request,
            code="BEHAVIOR_FILE_READ",
            message="已读取行为文件",
            data={"file_path": file_path, "content": content, "exists": True},
        )

    async def _handle_behavior_write_file(
        self, request: ActionRequestEnvelope
    ) -> ActionResultEnvelope:
        """写入行为文件内容（到磁盘）。"""
        file_path = str(request.params.get("file_path", "")).strip()
        content = str(request.params.get("content", ""))
        if not file_path:
            raise ControlPlaneActionError("MISSING_PARAM", "file_path 不能为空")

        try:
            resolved = validate_behavior_file_path(self._project_root, file_path)
        except ValueError as exc:
            raise ControlPlaneActionError("INVALID_PATH", str(exc)) from exc

        # 字符预算检查
        budget_result = check_behavior_file_budget(file_path, content)
        if not budget_result["within_budget"]:
            raise ControlPlaneActionError(
                "BUDGET_EXCEEDED",
                f"内容超出字符预算 {budget_result['exceeded_by']} 字符"
                f"（当前 {budget_result['current_chars']}/"
                f"预算 {budget_result['budget_chars']}），请精简后重试",
            )

        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(content, encoding="utf-8")
        except Exception as exc:
            raise ControlPlaneActionError(
                "FILE_WRITE_ERROR", f"写入文件失败: {exc}"
            ) from exc

        # 记录事件（FR-018）
        log = structlog.get_logger("control_plane.behavior")
        log.info(
            "behavior_file_written",
            source="control_plane",
            file_path=file_path,
            chars_written=len(content),
        )

        return self._completed_result(
            request=request,
            code="BEHAVIOR_FILE_WRITTEN",
            message="已保存行为文件",
            data={"file_path": file_path},
            resource_refs=[
                self._resource_ref("agent_profiles", "agent:profiles"),
            ],
        )

    async def _handle_operator_approval(
        self, request: ActionRequestEnvelope
    ) -> ActionResultEnvelope:
        approval_id = str(request.params.get("approval_id", "")).strip()
        mode = str(request.params.get("mode", "once")).strip().lower()
        if not approval_id:
            raise ControlPlaneActionError("APPROVAL_ID_REQUIRED", "approval_id 不能为空")
        kind = {
            "once": OperatorActionKind.APPROVE_ONCE,
            "always": OperatorActionKind.APPROVE_ALWAYS,
            "deny": OperatorActionKind.DENY,
        }.get(mode)
        if kind is None:
            raise ControlPlaneActionError("APPROVAL_MODE_INVALID", "mode 必须是 once/always/deny")
        return await self._handle_operator_request(
            request=request,
            item_id=f"approval:{approval_id}",
            kind=kind,
        )

    async def _handle_operator_action(
        self,
        request: ActionRequestEnvelope,
        *,
        kind: OperatorActionKind,
    ) -> ActionResultEnvelope:
        item_id = str(request.params.get("item_id", "")).strip()
        if not item_id:
            raise ControlPlaneActionError("ITEM_ID_REQUIRED", "item_id 不能为空")
        return await self._handle_operator_request(
            request=request,
            item_id=item_id,
            kind=kind,
        )

    async def _handle_operator_request(
        self,
        *,
        request: ActionRequestEnvelope,
        item_id: str,
        kind: OperatorActionKind,
    ) -> ActionResultEnvelope:
        if self._operator_action_service is None:
            raise ControlPlaneActionError(
                "OPERATOR_ACTION_UNAVAILABLE", "operator action service 不可用"
            )
        result = await self._operator_action_service.execute(
            OperatorActionRequest(
                item_id=item_id,
                kind=kind,
                source=self._map_operator_source(request.surface),
                actor_id=request.actor.actor_id,
                actor_label=request.actor.actor_label or request.actor.actor_id,
            )
        )
        if result.outcome.value in {"failed", "not_allowed", "not_found"}:
            return self._rejected_result(
                request=request,
                code=result.outcome.value.upper(),
                message=result.message,
                target_refs=[ControlPlaneTargetRef(target_type="operator_item", target_id=item_id)],
            )
        return self._completed_result(
            request=request,
            code=result.outcome.value.upper(),
            message=result.message,
            data=result.model_dump(mode="json"),
            resource_refs=[self._resource_ref("session_projection", "sessions:overview")],
            target_refs=[ControlPlaneTargetRef(target_type="operator_item", target_id=item_id)],
        )

    async def _handle_config_apply(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        payload = request.params.get("config")
        if not isinstance(payload, dict):
            raise ControlPlaneActionError("CONFIG_REQUIRED", "config payload 必须是对象")
        normalized = dict(payload)
        normalized.setdefault("updated_at", date.today().isoformat())
        config = OctoAgentConfig.model_validate(normalized)
        save_config(config, self._project_root)
        litellm_path = generate_litellm_config(config, self._project_root)
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

    async def _handle_agent_profile_save(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        payload = request.params.get("profile")
        raw = payload if isinstance(payload, dict) else request.params
        _, selected_project, _, _ = await self._resolve_selection()
        scope = self._param_str(raw, "scope", default="project").lower()
        if scope not in {"system", "project"}:
            raise ControlPlaneActionError(
                "AGENT_PROFILE_SCOPE_INVALID", "scope 必须是 system/project"
            )
        project_id = self._param_str(raw, "project_id")
        if scope == "project" and not project_id:
            if selected_project is None:
                raise ControlPlaneActionError(
                    "PROJECT_REQUIRED",
                    "project scope 的 agent profile 需要 project_id",
                )
            project_id = selected_project.project_id
        profile_id = self._param_str(raw, "profile_id")
        if not profile_id:
            profile_id = (
                f"agent-profile-{project_id or 'system-default'}"
                if scope == "project"
                else "agent-profile-system-default"
            )
        existing = await self._stores.agent_context_store.get_agent_profile(profile_id)
        profile = AgentProfileItem.model_validate(
            {
                "profile_id": profile_id,
                "scope": scope,
                "project_id": project_id,
                "name": self._param_str(raw, "name") or (existing.name if existing else ""),
                "persona_summary": self._param_str(raw, "persona_summary")
                or (existing.persona_summary if existing else ""),
                "model_alias": self._param_str(raw, "model_alias", default="main")
                or (existing.model_alias if existing else "main"),
                "tool_profile": self._param_str(raw, "tool_profile", default="standard")
                or (existing.tool_profile if existing else "standard"),
            }
        )
        if not profile.name:
            raise ControlPlaneActionError("AGENT_PROFILE_NAME_REQUIRED", "name 不能为空")
        model_alias_valid, available_aliases = self._validate_model_alias(profile.model_alias)
        if not model_alias_valid:
            raise ControlPlaneActionError(
                "AGENT_PROFILE_MODEL_ALIAS_INVALID",
                f"模型别名 '{profile.model_alias}' 不存在，可选：{', '.join(available_aliases)}",
            )
        saved = await self._stores.agent_context_store.save_agent_profile(
            AgentProfile(
                profile_id=profile.profile_id,
                scope=AgentProfileScope(profile.scope),
                project_id=profile.project_id,
                name=profile.name,
                persona_summary=profile.persona_summary,
                model_alias=profile.model_alias,
                tool_profile=profile.tool_profile,
                memory_access_policy=(
                    dict(raw.get("memory_access_policy", {}))
                    if isinstance(raw.get("memory_access_policy"), dict)
                    else {}
                ),
                context_budget_policy=(
                    dict(raw.get("context_budget_policy", {}))
                    if isinstance(raw.get("context_budget_policy"), dict)
                    else {}
                ),
                bootstrap_template_ids=[str(item) for item in raw.get("bootstrap_template_ids", [])]
                if isinstance(raw.get("bootstrap_template_ids"), list)
                else [],
                metadata=dict(raw.get("metadata", {}))
                if isinstance(raw.get("metadata"), dict)
                else {},
                resource_limits=dict(raw.get("resource_limits", {}))
                if isinstance(raw.get("resource_limits"), dict)
                else (dict(existing.resource_limits) if existing else {}),
                version=(existing.version if existing is not None else 1),
                created_at=(existing.created_at if existing is not None else datetime.now(tz=UTC)),
                updated_at=datetime.now(tz=UTC),
            )
        )
        set_as_default = (
            True
            if scope == "project" and "set_as_default" not in raw
            else self._param_bool(raw, "set_as_default")
        )
        target_project = None
        if scope == "project":
            target_project = await self._stores.project_store.get_project(project_id)
            if target_project is None:
                raise ControlPlaneActionError(
                    "PROJECT_NOT_FOUND",
                    "project_id 对应的 project 不存在",
                )
        if scope == "project" and target_project is not None and set_as_default:
            await self._stores.project_store.save_project(
                target_project.model_copy(
                    update={
                        "default_agent_profile_id": saved.profile_id,
                        "updated_at": datetime.now(tz=UTC),
                    }
                )
            )
        await self._stores.conn.commit()
        return self._completed_result(
            request=request,
            code="AGENT_PROFILE_SAVED",
            message="主 Agent 设置已保存。",
            data={
                "profile_id": saved.profile_id,
                "project_id": saved.project_id,
                "scope": saved.scope.value,
                "set_as_default": scope == "project" and set_as_default,
            },
            resource_refs=[
                self._resource_ref("agent_profiles", "agent-profiles:overview"),
                self._resource_ref("setup_governance", "setup:governance"),
            ],
            target_refs=[
                ControlPlaneTargetRef(
                    target_type="agent_profile",
                    target_id=saved.profile_id,
                    label=saved.name,
                )
            ],
        )

    async def _handle_update_resource_limits(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        """更新 Agent Profile 或 Worker Profile 的 resource_limits 字段。

        支持两种 target_type:
          - "agent_profile": 更新 AgentProfile.resource_limits
          - "worker_profile": 更新 WorkerProfile.resource_limits
        """
        raw = request.params
        target_type = self._param_str(raw, "target_type", default="agent_profile")
        profile_id = self._param_str(raw, "profile_id")
        if not profile_id:
            raise ControlPlaneActionError(
                "PROFILE_ID_REQUIRED", "profile_id 不能为空。"
            )
        resource_limits = raw.get("resource_limits")
        if not isinstance(resource_limits, dict):
            raise ControlPlaneActionError(
                "RESOURCE_LIMITS_INVALID",
                "resource_limits 必须是 dict 类型。",
            )
        # 白名单校验：只允许 UsageLimits 已知字段
        allowed_keys = {
            "max_steps", "max_request_tokens", "max_response_tokens",
            "max_tool_calls", "max_budget_usd", "max_duration_seconds",
            "repeat_signature_threshold",
        }
        sanitized: dict[str, Any] = {}
        for key, value in resource_limits.items():
            if key in allowed_keys and value is not None:
                sanitized[key] = value

        resource_refs = []
        target_label = ""

        if target_type == "worker_profile":
            existing = await self._get_worker_profile_in_scope(profile_id)
            updated = existing.model_copy(
                update={
                    "resource_limits": sanitized,
                    "updated_at": datetime.now(tz=UTC),
                }
            )
            await self._stores.agent_context_store.save_worker_profile(updated)
            resource_refs = [
                self._resource_ref("worker_profiles", "worker-profiles:overview"),
            ]
            target_label = existing.name
        else:
            existing_agent = await self._stores.agent_context_store.get_agent_profile(
                profile_id
            )
            if existing_agent is None:
                raise ControlPlaneActionError(
                    "AGENT_PROFILE_NOT_FOUND",
                    f"找不到 agent profile: {profile_id}",
                )
            updated_agent = existing_agent.model_copy(
                update={
                    "resource_limits": sanitized,
                    "updated_at": datetime.now(tz=UTC),
                }
            )
            await self._stores.agent_context_store.save_agent_profile(updated_agent)
            resource_refs = [
                self._resource_ref("agent_profiles", "agent-profiles:overview"),
            ]
            target_label = existing_agent.name

        await self._stores.conn.commit()
        return self._completed_result(
            request=request,
            code="RESOURCE_LIMITS_UPDATED",
            message="资源限制已更新。",
            data={
                "profile_id": profile_id,
                "target_type": target_type,
                "resource_limits": sanitized,
            },
            resource_refs=resource_refs,
            target_refs=[
                ControlPlaneTargetRef(
                    target_type=target_type,
                    target_id=profile_id,
                    label=target_label,
                )
            ],
        )

    async def _handle_worker_profile_review(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        draft = request.params.get("draft")
        raw = draft if isinstance(draft, Mapping) else request.params
        _, selected_project, selected_workspace, _ = await self._resolve_selection()

        source_profile: WorkerProfile | None = None
        existing: WorkerProfile | None = None
        mode = "create"
        profile_id = self._param_str(raw, "profile_id")
        source_profile_id = self._param_str(raw, "source_profile_id")
        if source_profile_id:
            source_profile = await self._get_worker_profile_in_scope(source_profile_id)
            mode = "clone"
        if profile_id and not source_profile_id:
            try:
                existing = await self._get_worker_profile_in_scope(profile_id)
            except ControlPlaneActionError:
                existing = None
            if existing is not None and existing.origin_kind != WorkerProfileOriginKind.BUILTIN:
                mode = "update"

        review = await self._review_worker_profile_draft(
            raw=raw,
            mode=mode,
            existing=existing,
            source_profile=source_profile,
            selected_project=selected_project,
            selected_workspace=selected_workspace,
            origin_kind=(
                WorkerProfileOriginKind.CLONED if mode == "clone" else None
            ),
        )
        target_profile_id = str(review["profile"].get("profile_id", "")).strip()
        return self._completed_result(
            request=request,
            code="WORKER_PROFILE_REVIEW_READY",
            message="Root Agent profile 检查已完成。",
            data={"review": review},
            resource_refs=[
                self._resource_ref("worker_profiles", "worker-profiles:overview"),
                self._resource_ref(
                    "worker_profile_revisions",
                    f"worker-profile-revisions:{target_profile_id}",
                ),
            ],
            target_refs=(
                [
                    ControlPlaneTargetRef(
                        target_type="worker_profile",
                        target_id=target_profile_id,
                        label=str(review["profile"].get("name", target_profile_id)),
                    )
                ]
                if target_profile_id
                else []
            ),
        )

    async def _handle_worker_profile_create(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        draft = request.params.get("draft")
        raw = draft if isinstance(draft, Mapping) else request.params
        profile_id = self._param_str(raw, "profile_id")
        if profile_id:
            existing = await self._stores.agent_context_store.get_worker_profile(profile_id)
            if existing is not None:
                raise ControlPlaneActionError(
                    "WORKER_PROFILE_ALREADY_EXISTS",
                    "同名 Root Agent profile 已存在，请改名或使用 clone/update。",
                )
        _, selected_project, selected_workspace, _ = await self._resolve_selection()
        review = await self._review_worker_profile_draft(
            raw=raw,
            mode="create",
            selected_project=selected_project,
            selected_workspace=selected_workspace,
            origin_kind=WorkerProfileOriginKind.CUSTOM,
        )
        if not bool(review.get("can_save")):
            message = "；".join(review.get("save_errors", [])) or "Root Agent 草稿不能保存。"
            raise ControlPlaneActionError("WORKER_PROFILE_CREATE_INVALID", message)
        saved = await self._save_worker_profile_draft(
            normalized_profile=review["profile"],
            existing=None,
            origin_kind=WorkerProfileOriginKind.CUSTOM,
        )
        # 为新 Agent 创建 agent-private 行为文件
        materialize_agent_behavior_files(
            self._project_root,
            agent_slug=saved.name or saved.profile_id,
            agent_name=saved.name,
            is_worker_profile=True,
        )
        return self._completed_result(
            request=request,
            code="WORKER_PROFILE_CREATED",
            message="已创建 Root Agent 草稿。",
            data={
                "profile_id": saved.profile_id,
                "status": saved.status.value,
                "draft_revision": saved.draft_revision,
                "active_revision": saved.active_revision,
                "review": review,
            },
            resource_refs=[
                self._resource_ref("worker_profiles", "worker-profiles:overview"),
                self._resource_ref(
                    "worker_profile_revisions",
                    f"worker-profile-revisions:{saved.profile_id}",
                ),
            ],
            target_refs=[
                ControlPlaneTargetRef(
                    target_type="worker_profile",
                    target_id=saved.profile_id,
                    label=saved.name,
                )
            ],
        )

    async def _handle_worker_profile_update(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        draft = request.params.get("draft")
        raw = draft if isinstance(draft, Mapping) else request.params
        profile_id = self._param_str(raw, "profile_id")
        if not profile_id:
            raise ControlPlaneActionError("WORKER_PROFILE_REQUIRED", "profile_id 不能为空。")
        existing = await self._get_worker_profile_in_scope(profile_id)
        if existing.origin_kind == WorkerProfileOriginKind.BUILTIN:
            raise ControlPlaneActionError(
                "WORKER_PROFILE_BUILTIN_READONLY",
                "内建 archetype 不能直接修改，请先 clone 一个新的 Root Agent。",
            )
        _, selected_project, selected_workspace, _ = await self._resolve_selection()
        review = await self._review_worker_profile_draft(
            raw=raw,
            mode="update",
            existing=existing,
            selected_project=selected_project,
            selected_workspace=selected_workspace,
        )
        if not bool(review.get("can_save")):
            message = "；".join(review.get("save_errors", [])) or "Root Agent 草稿不能保存。"
            raise ControlPlaneActionError("WORKER_PROFILE_UPDATE_INVALID", message)
        saved = await self._save_worker_profile_draft(
            normalized_profile=review["profile"],
            existing=existing,
        )
        return self._completed_result(
            request=request,
            code="WORKER_PROFILE_UPDATED",
            message="已更新 Root Agent 草稿。",
            data={
                "profile_id": saved.profile_id,
                "status": saved.status.value,
                "draft_revision": saved.draft_revision,
                "active_revision": saved.active_revision,
                "review": review,
            },
            resource_refs=[
                self._resource_ref("worker_profiles", "worker-profiles:overview"),
                self._resource_ref(
                    "worker_profile_revisions",
                    f"worker-profile-revisions:{saved.profile_id}",
                ),
            ],
            target_refs=[
                ControlPlaneTargetRef(
                    target_type="worker_profile",
                    target_id=saved.profile_id,
                    label=saved.name,
                )
            ],
        )

    async def _handle_worker_profile_clone(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        source_profile_id = self._param_str(request.params, "source_profile_id")
        if not source_profile_id:
            raise ControlPlaneActionError(
                "WORKER_PROFILE_SOURCE_REQUIRED",
                "source_profile_id 不能为空。",
            )
        source_profile = await self._get_worker_profile_in_scope(source_profile_id)
        raw = {
            **self._worker_profile_snapshot_payload(source_profile),
            "source_profile_id": source_profile_id,
        }
        if name := self._param_str(request.params, "name"):
            raw["name"] = name
        raw["profile_id"] = ""
        raw["origin_kind"] = WorkerProfileOriginKind.CLONED.value
        _, selected_project, selected_workspace, _ = await self._resolve_selection()
        review = await self._review_worker_profile_draft(
            raw=raw,
            mode="clone",
            source_profile=source_profile,
            selected_project=selected_project,
            selected_workspace=selected_workspace,
            origin_kind=WorkerProfileOriginKind.CLONED,
        )
        if not bool(review.get("can_save")):
            message = "；".join(review.get("save_errors", [])) or "克隆后的 Root Agent 草稿不能保存。"
            raise ControlPlaneActionError("WORKER_PROFILE_CLONE_INVALID", message)
        saved = await self._save_worker_profile_draft(
            normalized_profile=review["profile"],
            existing=None,
            origin_kind=WorkerProfileOriginKind.CLONED,
        )
        return self._completed_result(
            request=request,
            code="WORKER_PROFILE_CLONED",
            message="已复制为新的 Root Agent 草稿。",
            data={
                "profile_id": saved.profile_id,
                "source_profile_id": source_profile_id,
                "status": saved.status.value,
                "review": review,
            },
            resource_refs=[
                self._resource_ref("worker_profiles", "worker-profiles:overview"),
                self._resource_ref(
                    "worker_profile_revisions",
                    f"worker-profile-revisions:{saved.profile_id}",
                ),
            ],
            target_refs=[
                ControlPlaneTargetRef(
                    target_type="worker_profile",
                    target_id=saved.profile_id,
                    label=saved.name,
                )
            ],
        )

    async def _handle_worker_profile_archive(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        profile_id = self._param_str(request.params, "profile_id")
        if not profile_id:
            raise ControlPlaneActionError("WORKER_PROFILE_REQUIRED", "profile_id 不能为空。")
        existing = await self._get_worker_profile_in_scope(profile_id)
        if existing.origin_kind == WorkerProfileOriginKind.BUILTIN:
            raise ControlPlaneActionError(
                "WORKER_PROFILE_BUILTIN_READONLY",
                "内建 archetype 不能归档。",
            )
        archived = await self._stores.agent_context_store.save_worker_profile(
            existing.model_copy(
                update={
                    "status": WorkerProfileStatus.ARCHIVED,
                    "archived_at": datetime.now(tz=UTC),
                    "updated_at": datetime.now(tz=UTC),
                }
            )
        )
        await self._stores.conn.commit()
        return self._completed_result(
            request=request,
            code="WORKER_PROFILE_ARCHIVED",
            message="已归档 Root Agent profile。",
            data={
                "profile_id": archived.profile_id,
                "status": archived.status.value,
            },
            resource_refs=[
                self._resource_ref("worker_profiles", "worker-profiles:overview"),
                self._resource_ref(
                    "worker_profile_revisions",
                    f"worker-profile-revisions:{archived.profile_id}",
                ),
            ],
            target_refs=[
                ControlPlaneTargetRef(
                    target_type="worker_profile",
                    target_id=archived.profile_id,
                    label=archived.name,
                )
            ],
        )

    async def _handle_worker_profile_apply(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        draft = request.params.get("draft")
        raw = draft if isinstance(draft, Mapping) else request.params
        publish = self._param_bool(request.params, "publish")
        profile_id = self._param_str(raw, "profile_id")
        existing: WorkerProfile | None = None
        mode = "create"
        if profile_id:
            existing = await self._stores.agent_context_store.get_worker_profile(profile_id)
            if existing is not None:
                if existing.origin_kind == WorkerProfileOriginKind.BUILTIN:
                    raise ControlPlaneActionError(
                        "WORKER_PROFILE_BUILTIN_READONLY",
                        "内建 archetype 不能直接 apply，请先 clone 一个新的 Root Agent。",
                    )
                mode = "update"
        _, selected_project, selected_workspace, _ = await self._resolve_selection()
        review = await self._review_worker_profile_draft(
            raw=raw,
            mode=mode,
            existing=existing,
            selected_project=selected_project,
            selected_workspace=selected_workspace,
        )
        if not bool(review.get("can_save")):
            message = "；".join(review.get("save_errors", [])) or "Root Agent 草稿不能保存。"
            raise ControlPlaneActionError("WORKER_PROFILE_APPLY_INVALID", message)
        saved = await self._save_worker_profile_draft(
            normalized_profile=review["profile"],
            existing=existing,
        )
        data: dict[str, Any] = {
            "profile_id": saved.profile_id,
            "status": saved.status.value,
            "draft_revision": saved.draft_revision,
            "active_revision": saved.active_revision,
            "review": review,
        }
        message = "已保存 Root Agent 草稿。"
        if publish:
            if not bool(review.get("ready")):
                blocking = "；".join(review.get("blocking_reasons", [])) or "当前 review 未通过。"
                raise ControlPlaneActionError("WORKER_PROFILE_REVIEW_BLOCKED", blocking)
            published, revision, changed = await self._publish_worker_profile_revision(
                profile=saved,
                change_summary=(
                    self._param_str(request.params, "change_summary")
                    or "通过 Profile Studio apply 并发布"
                ),
                actor=request.actor.actor_id,
            )
            await self._sync_worker_profile_agent_profile(
                published,
                revision=revision.revision,
            )
            bound_as_default = False
            should_bind_default = (
                self._param_bool(request.params, "set_as_default")
                if "set_as_default" in request.params
                else bool(
                    published.scope == AgentProfileScope.PROJECT
                    and selected_project is not None
                    and not selected_project.default_agent_profile_id
                )
            )
            if should_bind_default:
                bound_as_default = await self._bind_worker_profile_as_default(profile=published)
            data["published_revision"] = revision.revision
            data["published"] = changed
            data["status"] = published.status.value
            data["active_revision"] = published.active_revision
            data["draft_revision"] = published.draft_revision
            data["bound_as_default"] = bound_as_default
            message = "已保存草稿并发布 Root Agent revision。"
            await self._stores.conn.commit()
        return self._completed_result(
            request=request,
            code="WORKER_PROFILE_APPLIED",
            message=message,
            data=data,
            resource_refs=[
                self._resource_ref("worker_profiles", "worker-profiles:overview"),
                self._resource_ref(
                    "worker_profile_revisions",
                    f"worker-profile-revisions:{saved.profile_id}",
                ),
                self._resource_ref("delegation_plane", "delegation:overview"),
            ],
            target_refs=[
                ControlPlaneTargetRef(
                    target_type="worker_profile",
                    target_id=saved.profile_id,
                    label=saved.name,
                )
            ],
        )

    async def _handle_worker_profile_publish(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        draft = request.params.get("draft")
        if isinstance(draft, Mapping):
            return await self._handle_worker_profile_apply(
                request.model_copy(update={"action_id": "worker_profile.apply", "params": {**request.params, "publish": True}})
            )

        profile_id = self._param_str(request.params, "profile_id")
        if not profile_id:
            raise ControlPlaneActionError("WORKER_PROFILE_REQUIRED", "profile_id 不能为空。")
        existing = await self._get_worker_profile_in_scope(profile_id)
        if existing.origin_kind == WorkerProfileOriginKind.BUILTIN:
            raise ControlPlaneActionError(
                "WORKER_PROFILE_BUILTIN_READONLY",
                "内建 archetype 不能直接发布 revision。",
            )
        _, selected_project, selected_workspace, _ = await self._resolve_selection()
        review = await self._review_worker_profile_draft(
            raw=existing.model_dump(mode="python"),
            mode="publish",
            existing=existing,
            selected_project=selected_project,
            selected_workspace=selected_workspace,
        )
        if not bool(review.get("ready")):
            blocking = "；".join(review.get("blocking_reasons", [])) or "当前 review 未通过。"
            raise ControlPlaneActionError("WORKER_PROFILE_REVIEW_BLOCKED", blocking)
        published, revision, changed = await self._publish_worker_profile_revision(
            profile=existing,
            change_summary=(
                self._param_str(request.params, "change_summary")
                or "通过 Profile Studio 发布"
            ),
            actor=request.actor.actor_id,
        )
        await self._sync_worker_profile_agent_profile(
            published,
            revision=revision.revision,
        )
        _, selected_project, _, _ = await self._resolve_selection()
        should_bind_default = (
            self._param_bool(request.params, "set_as_default")
            if "set_as_default" in request.params
            else bool(
                published.scope == AgentProfileScope.PROJECT
                and selected_project is not None
                and not selected_project.default_agent_profile_id
            )
        )
        bound_as_default = False
        if should_bind_default:
            bound_as_default = await self._bind_worker_profile_as_default(profile=published)
        await self._stores.conn.commit()
        return self._completed_result(
            request=request,
            code="WORKER_PROFILE_PUBLISHED",
            message="已发布 Root Agent revision。",
            data={
                "profile_id": published.profile_id,
                "revision": revision.revision,
                "published": changed,
                "bound_as_default": bound_as_default,
                "review": review,
            },
            resource_refs=[
                self._resource_ref("worker_profiles", "worker-profiles:overview"),
                self._resource_ref(
                    "worker_profile_revisions",
                    f"worker-profile-revisions:{published.profile_id}",
                ),
            ],
            target_refs=[
                ControlPlaneTargetRef(
                    target_type="worker_profile",
                    target_id=published.profile_id,
                    label=published.name,
                )
            ],
        )

    async def _handle_worker_profile_bind_default(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        profile_id = self._param_str(request.params, "profile_id")
        if not profile_id:
            raise ControlPlaneActionError("WORKER_PROFILE_REQUIRED", "profile_id 不能为空。")
        existing = await self._get_worker_profile_in_scope(profile_id)
        if existing.origin_kind == WorkerProfileOriginKind.BUILTIN:
            raise ControlPlaneActionError(
                "WORKER_PROFILE_BIND_UNSUPPORTED",
                "当前只支持把已发布的自定义 Root Agent 绑定为聊天默认。",
            )
        if existing.status != WorkerProfileStatus.ACTIVE:
            raise ControlPlaneActionError(
                "WORKER_PROFILE_NOT_PUBLISHED",
                "请先发布 revision，再绑定为默认聊天 Agent。",
            )
        revision = existing.active_revision or existing.draft_revision or 1
        await self._sync_worker_profile_agent_profile(existing, revision=revision)
        bound = await self._bind_worker_profile_as_default(profile=existing)
        await self._stores.conn.commit()
        return self._completed_result(
            request=request,
            code="WORKER_PROFILE_BOUND_DEFAULT",
            message="已绑定为当前 project 的默认聊天 Agent。",
            data={
                "profile_id": existing.profile_id,
                "bound": bound,
                "revision": revision,
            },
            resource_refs=[
                self._resource_ref("worker_profiles", "worker-profiles:overview"),
                self._resource_ref("agent_profiles", "agent-profiles:overview"),
                self._resource_ref("setup_governance", "setup:governance"),
            ],
            target_refs=[
                ControlPlaneTargetRef(
                    target_type="worker_profile",
                    target_id=existing.profile_id,
                    label=existing.name,
                )
            ],
        )

    async def _handle_worker_spawn_from_profile(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        profile_id = self._param_str(request.params, "profile_id")
        if not profile_id:
            raise ControlPlaneActionError("WORKER_PROFILE_REQUIRED", "profile_id 不能为空。")
        profile = await self._get_worker_profile_in_scope(profile_id)
        if profile.status == WorkerProfileStatus.ARCHIVED:
            raise ControlPlaneActionError(
                "WORKER_PROFILE_ARCHIVED",
                "归档后的 Root Agent 不能再启动新任务。",
            )
        objective = self._param_str(request.params, "objective") or self._param_str(
            request.params, "message"
        )
        if not objective:
            raise ControlPlaneActionError("OBJECTIVE_REQUIRED", "objective 不能为空。")
        _, selected_project, selected_workspace, _ = await self._resolve_selection()
        project_id = (
            profile.project_id
            or (selected_project.project_id if selected_project is not None else "")
        )
        workspace_id = (
            selected_workspace.workspace_id if selected_workspace is not None else ""
        )
        requested_revision = profile.active_revision or profile.draft_revision or 1
        message = NormalizedMessage(
            channel="web",
            thread_id=f"worker-profile:{profile.profile_id}",
            scope_id=project_id or f"worker-profile:{profile.profile_id}",
            sender_id="owner",
            sender_name=request.actor.actor_label or "Owner",
            text=objective,
            idempotency_key=f"spawn:{profile.profile_id}:{objective}:{ULID()}",
            control_metadata={
                "requested_worker_profile_id": profile.profile_id,
                "requested_worker_profile_version": requested_revision,
                "effective_worker_snapshot_id": self._worker_snapshot_id(
                    profile.profile_id,
                    requested_revision,
                ),
                "requested_worker_type": "general",
                "tool_profile": profile.tool_profile,
                "target_kind": self._param_str(request.params, "target_kind", default="worker")
                or "worker",
                "project_id": project_id,
                "workspace_id": workspace_id,
            },
        )
        if self._task_runner is not None:
            task_id, created = await self._task_runner.launch_child_task(
                message,
                model_alias=profile.model_alias,
            )
        else:
            task_id, created = await TaskService(self._stores, self._sse_hub).create_task(message)
        return self._completed_result(
            request=request,
            code="WORKER_PROFILE_SPAWNED",
            message="已按 Root Agent profile 创建任务。",
            data={
                "task_id": task_id,
                "created": created,
                "profile_id": profile.profile_id,
                "requested_worker_profile_version": requested_revision,
            },
            resource_refs=[
                self._resource_ref("session_projection", "sessions:overview"),
                self._resource_ref("delegation_plane", "delegation:overview"),
                self._resource_ref("worker_profiles", "worker-profiles:overview"),
            ],
            target_refs=[
                ControlPlaneTargetRef(target_type="task", target_id=task_id, label=objective[:48]),
                ControlPlaneTargetRef(
                    target_type="worker_profile",
                    target_id=profile.profile_id,
                    label=profile.name,
                ),
            ],
        )

    async def _handle_worker_extract_profile_from_runtime(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        work_id = self._param_str(request.params, "work_id")
        if not work_id:
            raise ControlPlaneActionError("WORK_ID_REQUIRED", "work_id 不能为空。")
        work = await self._get_work_in_scope(work_id)
        _, selected_project, selected_workspace, _ = await self._resolve_selection()
        raw = {
            "name": self._param_str(request.params, "name")
            or f"{self._worker_profile_label(work.selected_worker_type)} 提炼草稿",
            "summary": self._param_str(request.params, "summary")
            or (work.title or "从运行中的 Work 提炼而来。"),
            "tool_profile": str(work.metadata.get("requested_tool_profile", "minimal")),
            "selected_tools": list(work.selected_tools),
            "runtime_kinds": [work.target_kind.value]
            if work.target_kind.value in {"worker", "subagent", "acp_runtime", "graph_agent"}
            else ["worker"],
            "tags": [work.selected_worker_type, "runtime-extract"],
            "metadata": {
                "source_work_id": work.work_id,
                "source_task_id": work.task_id,
                "source_snapshot_id": work.effective_worker_snapshot_id,
            },
            "profile_id": "",
            "project_id": work.project_id or (selected_project.project_id if selected_project is not None else ""),
        }
        review = await self._review_worker_profile_draft(
            raw=raw,
            mode="extract",
            selected_project=selected_project,
            selected_workspace=selected_workspace,
            origin_kind=WorkerProfileOriginKind.EXTRACTED,
        )
        if not bool(review.get("can_save")):
            message = "；".join(review.get("save_errors", [])) or "提炼后的 Root Agent 草稿不能保存。"
            raise ControlPlaneActionError("WORKER_PROFILE_EXTRACT_INVALID", message)
        saved = await self._save_worker_profile_draft(
            normalized_profile=review["profile"],
            existing=None,
            origin_kind=WorkerProfileOriginKind.EXTRACTED,
        )
        return self._completed_result(
            request=request,
            code="WORKER_PROFILE_EXTRACTED",
            message="已从运行中的 Work 提炼出 Root Agent 草稿。",
            data={
                "profile_id": saved.profile_id,
                "source_work_id": work.work_id,
                "review": review,
            },
            resource_refs=[
                self._resource_ref("worker_profiles", "worker-profiles:overview"),
                self._resource_ref(
                    "worker_profile_revisions",
                    f"worker-profile-revisions:{saved.profile_id}",
                ),
                self._resource_ref("delegation_plane", "delegation:overview"),
            ],
            target_refs=[
                ControlPlaneTargetRef(target_type="work", target_id=work.work_id, label=work.title),
                ControlPlaneTargetRef(
                    target_type="worker_profile",
                    target_id=saved.profile_id,
                    label=saved.name,
                ),
            ],
        )

    async def _handle_policy_profile_select(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        profile_id = self._param_str(request.params, "profile_id").lower()
        if not profile_id:
            raise ControlPlaneActionError("POLICY_PROFILE_REQUIRED", "profile_id 不能为空")
        profile = self._policy_profile_by_id(profile_id)
        if profile is None:
            raise ControlPlaneActionError("POLICY_PROFILE_INVALID", "不支持的 policy profile")
        _, selected_project, _, _ = await self._resolve_selection()
        if selected_project is None:
            raise ControlPlaneActionError("PROJECT_REQUIRED", "当前没有可用 project")
        metadata = dict(selected_project.metadata)
        metadata["policy_profile_id"] = profile_id
        await self._stores.project_store.save_project(
            selected_project.model_copy(
                update={
                    "metadata": metadata,
                    "updated_at": datetime.now(tz=UTC),
                }
            )
        )
        await self._sync_policy_engine_for_project(
            selected_project.model_copy(update={"metadata": metadata})
        )
        await self._stores.conn.commit()
        return self._completed_result(
            request=request,
            code="POLICY_PROFILE_SELECTED",
            message="安全等级已更新。",
            data={
                "profile_id": profile_id,
                "allowed_tool_profile": profile.allowed_tool_profile.value,
                "approval_policy": self._describe_policy_approval(profile),
            },
            resource_refs=[
                self._resource_ref("policy_profiles", "policy:profiles"),
                self._resource_ref("setup_governance", "setup:governance"),
            ],
            target_refs=[
                ControlPlaneTargetRef(
                    target_type="policy_profile",
                    target_id=profile_id,
                    label=profile_id,
                )
            ],
        )

    async def _handle_backup_create(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        label = str(request.params.get("label", "")).strip() or None
        bundle = await BackupService(self._project_root, store_group=self._stores).create_bundle(
            label=label
        )
        return self._completed_result(
            request=request,
            code="BACKUP_CREATED",
            message="已创建 backup bundle",
            data=bundle.model_dump(mode="json"),
            resource_refs=[self._resource_ref("diagnostics_summary", "diagnostics:runtime")],
        )

    async def _handle_restore_plan(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        bundle = str(request.params.get("bundle", "")).strip()
        target_root = str(request.params.get("target_root", "")).strip() or None
        if not bundle:
            raise ControlPlaneActionError("BUNDLE_REQUIRED", "bundle 路径不能为空")
        plan = await BackupService(self._project_root, store_group=self._stores).plan_restore(
            bundle=bundle,
            target_root=target_root,
        )
        return self._completed_result(
            request=request,
            code="RESTORE_PLAN_READY",
            message="已生成 restore 计划",
            data=plan.model_dump(mode="json"),
            resource_refs=[self._resource_ref("diagnostics_summary", "diagnostics:runtime")],
        )

    async def _handle_import_source_detect(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        source_type = str(request.params.get("source_type", "")).strip().lower()
        input_path = str(request.params.get("input_path", "")).strip()
        media_root = str(request.params.get("media_root", "")).strip() or None
        format_hint = str(request.params.get("format_hint", "")).strip() or None
        if not source_type:
            raise ControlPlaneActionError("IMPORT_SOURCE_INVALID", "source_type 不能为空")
        if not input_path:
            raise ControlPlaneActionError("INPUT_PATH_REQUIRED", "input_path 不能为空")
        try:
            document = await self._import_workbench_service.detect_source(
                source_type=source_type,
                input_path=input_path,
                media_root=media_root,
                format_hint=format_hint,
            )
        except ImportWorkbenchError as exc:
            raise ControlPlaneActionError(exc.code, exc.message) from exc
        return self._completed_result(
            request=request,
            code="IMPORT_SOURCE_DETECTED",
            message="已识别导入源",
            data=document.model_dump(mode="json"),
            resource_refs=[
                self._resource_ref("import_workbench", "imports:workbench"),
                self._resource_ref("import_source", document.resource_id),
            ],
        )

    async def _handle_import_mapping_save(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        source_id = str(request.params.get("source_id", "")).strip()
        if not source_id:
            raise ControlPlaneActionError("IMPORT_SOURCE_INVALID", "source_id 不能为空")
        raw_conversation_mappings = request.params.get("conversation_mappings")
        raw_sender_mappings = request.params.get("sender_mappings")
        conversation_mappings = (
            list(raw_conversation_mappings) if isinstance(raw_conversation_mappings, list) else None
        )
        sender_mappings = (
            list(raw_sender_mappings) if isinstance(raw_sender_mappings, list) else None
        )
        try:
            await self.get_import_source(source_id)
            profile = await self._import_workbench_service.save_mapping(
                source_id=source_id,
                conversation_mappings=conversation_mappings,
                sender_mappings=sender_mappings,
                attachment_policy=str(
                    request.params.get("attachment_policy", "artifact-first")
                ).strip()
                or "artifact-first",
                memu_policy=str(request.params.get("memu_policy", "best-effort")).strip()
                or "best-effort",
            )
            source = await self.get_import_source(source_id)
        except ImportWorkbenchError as exc:
            raise ControlPlaneActionError(exc.code, exc.message) from exc
        return self._completed_result(
            request=request,
            code="IMPORT_MAPPING_SAVED",
            message="导入 mapping 已保存",
            data=profile.model_dump(mode="json"),
            resource_refs=[
                self._resource_ref("import_workbench", "imports:workbench"),
                self._resource_ref("import_source", source.resource_id),
            ],
        )

    async def _handle_import_preview(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        source_id = str(request.params.get("source_id", "")).strip()
        if not source_id:
            raise ControlPlaneActionError("IMPORT_SOURCE_INVALID", "source_id 不能为空")
        mapping_id = str(request.params.get("mapping_id", "")).strip() or None
        try:
            await self.get_import_source(source_id)
            document = await self._import_workbench_service.preview(
                source_id=source_id,
                mapping_id=mapping_id,
            )
        except ImportWorkbenchError as exc:
            raise ControlPlaneActionError(exc.code, exc.message) from exc
        return self._completed_result(
            request=request,
            code="IMPORT_PREVIEW_READY",
            message="已生成导入预览",
            data=document.model_dump(mode="json"),
            resource_refs=[
                self._resource_ref("import_workbench", "imports:workbench"),
                self._resource_ref("import_run", document.resource_id),
            ],
        )

    async def _handle_import_run(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        source_id = str(request.params.get("source_id", "")).strip()
        mapping_id = str(request.params.get("mapping_id", "")).strip() or None
        if source_id:
            try:
                await self.get_import_source(source_id)
                document = await self._import_workbench_service.run(
                    source_id=source_id,
                    mapping_id=mapping_id,
                    resume=bool(request.params.get("resume", False)),
                )
            except ImportWorkbenchError as exc:
                raise ControlPlaneActionError(exc.code, exc.message) from exc
            return self._completed_result(
                request=request,
                code="IMPORT_RUN_COMPLETED",
                message="导入执行完成",
                data=document.model_dump(mode="json"),
                resource_refs=[
                    self._resource_ref("import_workbench", "imports:workbench"),
                    self._resource_ref("import_run", document.resource_id),
                ],
            )

        input_path = str(request.params.get("input_path", "")).strip()
        if not input_path:
            raise ControlPlaneActionError("INPUT_PATH_REQUIRED", "input_path 不能为空")
        report = await ChatImportService(self._project_root, store_group=self._stores).import_chats(
            input_path=input_path,
            source_format=str(request.params.get("source_format", "normalized-jsonl")),
            source_id=(str(request.params.get("source_id", "")).strip() or None),
            channel=(str(request.params.get("channel", "")).strip() or None),
            thread_id=(str(request.params.get("thread_id", "")).strip() or None),
            dry_run=bool(request.params.get("dry_run", False)),
            resume=bool(request.params.get("resume", False)),
        )
        return self._completed_result(
            request=request,
            code="IMPORT_COMPLETED",
            message="聊天导入已完成",
            data=report.model_dump(mode="json"),
            resource_refs=[self._resource_ref("diagnostics_summary", "diagnostics:runtime")],
        )

    async def _handle_import_resume(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        resume_id = str(request.params.get("resume_id", "")).strip()
        if not resume_id:
            raise ControlPlaneActionError("IMPORT_RESUME_BLOCKED", "resume_id 不能为空")
        try:
            await self.get_import_source(resume_id.removeprefix("resume:"))
            document = await self._import_workbench_service.resume(resume_id=resume_id)
        except ImportWorkbenchError as exc:
            raise ControlPlaneActionError(exc.code, exc.message) from exc
        return self._completed_result(
            request=request,
            code="IMPORT_RESUME_COMPLETED",
            message="已恢复导入",
            data=document.model_dump(mode="json"),
            resource_refs=[
                self._resource_ref("import_workbench", "imports:workbench"),
                self._resource_ref("import_run", document.resource_id),
            ],
        )

    async def _handle_import_report_inspect(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        run_id = str(request.params.get("run_id", "")).strip()
        if not run_id:
            raise ControlPlaneActionError("IMPORT_REPORT_NOT_FOUND", "run_id 不能为空")
        try:
            document = await self.get_import_run(run_id)
        except ImportWorkbenchError as exc:
            raise ControlPlaneActionError(exc.code, exc.message) from exc
        return self._completed_result(
            request=request,
            code="IMPORT_REPORT_READY",
            message="已加载导入报告",
            data=document.model_dump(mode="json"),
            resource_refs=[self._resource_ref("import_run", document.resource_id)],
        )

    async def _handle_update_dry_run(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        if self._update_service is None:
            raise ControlPlaneActionError("UPDATE_SERVICE_UNAVAILABLE", "update service 不可用")
        summary = await self._update_service.preview(
            trigger_source=self._map_update_source(request.surface)
        )
        return self._completed_result(
            request=request,
            code="UPDATE_DRY_RUN_READY",
            message="已完成 update dry-run",
            data=summary.model_dump(mode="json"),
            resource_refs=[self._resource_ref("diagnostics_summary", "diagnostics:runtime")],
        )

    async def _handle_update_apply(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        if self._update_service is None:
            raise ControlPlaneActionError("UPDATE_SERVICE_UNAVAILABLE", "update service 不可用")
        summary = await self._update_service.apply(
            trigger_source=self._map_update_source(request.surface),
            wait=False,
        )
        attempt_id = str(getattr(summary, "attempt_id", "") or request.request_id)
        return self._deferred_result(
            request=request,
            code="UPDATE_APPLY_ACCEPTED",
            message="已受理 update apply",
            correlation_id=attempt_id,
            data=summary.model_dump(mode="json"),
            resource_refs=[self._resource_ref("diagnostics_summary", "diagnostics:runtime")],
        )

    async def _handle_runtime_restart(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        if self._update_service is None:
            raise ControlPlaneActionError("UPDATE_SERVICE_UNAVAILABLE", "update service 不可用")
        summary = await self._update_service.restart(
            trigger_source=self._map_update_source(request.surface)
        )
        attempt_id = str(getattr(summary, "attempt_id", "") or request.request_id)
        return self._deferred_result(
            request=request,
            code="RUNTIME_RESTART_ACCEPTED",
            message="已受理 runtime restart",
            correlation_id=attempt_id,
            data=summary.model_dump(mode="json"),
            resource_refs=[self._resource_ref("diagnostics_summary", "diagnostics:runtime")],
        )

    async def _handle_runtime_verify(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        if self._update_service is None:
            raise ControlPlaneActionError("UPDATE_SERVICE_UNAVAILABLE", "update service 不可用")
        summary = await self._update_service.verify(
            trigger_source=self._map_update_source(request.surface)
        )
        return self._completed_result(
            request=request,
            code="RUNTIME_VERIFY_COMPLETED",
            message="已完成 runtime verify",
            data=summary.model_dump(mode="json"),
            resource_refs=[self._resource_ref("diagnostics_summary", "diagnostics:runtime")],
        )

    async def _handle_automation_create(
        self, request: ActionRequestEnvelope
    ) -> ActionResultEnvelope:
        name = str(request.params.get("name", "")).strip()
        action_id = str(request.params.get("action_id", "")).strip()
        schedule_kind_raw = str(request.params.get("schedule_kind", "interval")).strip()
        schedule_expr = str(request.params.get("schedule_expr", "")).strip()
        action_params = request.params.get("action_params", {})
        if not name or not action_id or not schedule_expr:
            raise ControlPlaneActionError(
                "AUTOMATION_PARAMS_REQUIRED", "name/action_id/schedule_expr 不能为空"
            )
        if not isinstance(action_params, Mapping):
            raise ControlPlaneActionError(
                "AUTOMATION_ACTION_PARAMS_INVALID",
                "action_params 必须是 object/map",
            )
        if action_id.startswith("automation."):
            raise ControlPlaneActionError(
                "AUTOMATION_RECURSIVE_ACTION", "automation job 不能直接调度 automation.*"
            )
        if self.get_action_definition(action_id) is None:
            raise ControlPlaneActionError(
                "AUTOMATION_ACTION_INVALID",
                f"automation job 引用的 action_id 未注册: {action_id}",
            )
        try:
            schedule_kind = AutomationScheduleKind(schedule_kind_raw)
        except ValueError as exc:
            raise ControlPlaneActionError(
                "SCHEDULE_KIND_INVALID",
                f"不支持的 schedule_kind: {schedule_kind_raw}",
            ) from exc

        project_id = str(request.params.get("project_id", "")).strip()
        workspace_id = str(request.params.get("workspace_id", "")).strip()
        _, selected_project, selected_workspace, _ = await self._resolve_selection()
        if not project_id and selected_project is not None:
            project_id = selected_project.project_id
        if (
            not workspace_id
            and selected_workspace is not None
            and selected_workspace.project_id == project_id
        ):
            workspace_id = selected_workspace.workspace_id
        if not project_id:
            raise ControlPlaneActionError(
                "PROJECT_SELECTION_REQUIRED",
                "automation job 需要绑定 project",
            )

        project = await self._stores.project_store.get_project(project_id)
        if project is None:
            raise ControlPlaneActionError(
                "PROJECT_NOT_FOUND",
                f"project 不存在: {project_id}",
            )
        if workspace_id:
            workspace = await self._stores.project_store.get_workspace(workspace_id)
            if workspace is None or workspace.project_id != project_id:
                raise ControlPlaneActionError(
                    "WORKSPACE_NOT_FOUND",
                    "workspace 不存在或不属于指定 project",
                )
        else:
            workspace = await self._stores.project_store.get_primary_workspace(project_id)
            if workspace is None:
                raise ControlPlaneActionError(
                    "WORKSPACE_NOT_FOUND",
                    f"project 缺少 primary workspace: {project_id}",
                )
            workspace_id = workspace.workspace_id
        job = AutomationJob(
            job_id=str(ULID()),
            name=name,
            action_id=action_id,
            params=dict(action_params),
            project_id=project_id,
            workspace_id=workspace_id,
            schedule_kind=schedule_kind,
            schedule_expr=schedule_expr,
            timezone=str(request.params.get("timezone", "UTC")).strip() or "UTC",
            enabled=bool(request.params.get("enabled", True)),
        )
        self._automation_store.save_job(job)
        if self._automation_scheduler is not None:
            await self._automation_scheduler.sync_job(job)
        return self._completed_result(
            request=request,
            code="AUTOMATION_CREATED",
            message="已创建自动化任务",
            data=job.model_dump(mode="json"),
            resource_refs=[self._resource_ref("automation_job", "automation:jobs")],
            target_refs=[
                ControlPlaneTargetRef(
                    target_type="automation_job", target_id=job.job_id, label=job.name
                )
            ],
        )

    async def _handle_automation_run(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        job_id = str(request.params.get("job_id", "")).strip()
        if not job_id:
            raise ControlPlaneActionError("JOB_ID_REQUIRED", "job_id 不能为空")
        job = await self._get_automation_job_in_scope(job_id)
        if self._automation_scheduler is None:
            raise ControlPlaneActionError(
                "AUTOMATION_SCHEDULER_UNAVAILABLE", "automation scheduler 不可用"
            )
        run = await self._automation_scheduler.run_now(job_id, actor=request.actor)
        return self._deferred_result(
            request=request,
            code="AUTOMATION_RUN_ACCEPTED",
            message="已受理 automation run-now",
            correlation_id=run.run_id,
            data=run.model_dump(mode="json"),
            resource_refs=[self._resource_ref("automation_job", "automation:jobs")],
            target_refs=[
                ControlPlaneTargetRef(
                    target_type="automation_job", target_id=job_id, label=job.name
                )
            ],
        )

    async def _handle_automation_pause_resume(
        self,
        request: ActionRequestEnvelope,
        *,
        enable: bool,
    ) -> ActionResultEnvelope:
        job_id = str(request.params.get("job_id", "")).strip()
        if not job_id:
            raise ControlPlaneActionError("JOB_ID_REQUIRED", "job_id 不能为空")
        job = await self._get_automation_job_in_scope(job_id)
        updated = job.model_copy(update={"enabled": enable, "updated_at": datetime.now(tz=UTC)})
        self._automation_store.save_job(updated)
        if self._automation_scheduler is not None:
            await self._automation_scheduler.sync_job(updated)
        return self._completed_result(
            request=request,
            code="AUTOMATION_RESUMED" if enable else "AUTOMATION_PAUSED",
            message="已恢复自动化任务" if enable else "已暂停自动化任务",
            data=updated.model_dump(mode="json"),
            resource_refs=[self._resource_ref("automation_job", "automation:jobs")],
            target_refs=[
                ControlPlaneTargetRef(
                    target_type="automation_job", target_id=job_id, label=job.name
                )
            ],
        )

    async def _handle_automation_delete(
        self, request: ActionRequestEnvelope
    ) -> ActionResultEnvelope:
        job_id = str(request.params.get("job_id", "")).strip()
        if not job_id:
            raise ControlPlaneActionError("JOB_ID_REQUIRED", "job_id 不能为空")
        job = await self._get_automation_job_in_scope(job_id)
        deleted = self._automation_store.delete_job(job_id)
        if not deleted:
            raise ControlPlaneActionError("JOB_DELETE_FAILED", "automation job 删除失败")
        if self._automation_scheduler is not None:
            await self._automation_scheduler.remove_job(job_id)
        return self._completed_result(
            request=request,
            code="AUTOMATION_DELETED",
            message="已删除自动化任务",
            resource_refs=[self._resource_ref("automation_job", "automation:jobs")],
            target_refs=[
                ControlPlaneTargetRef(
                    target_type="automation_job", target_id=job_id, label=job.name
                )
            ],
        )

    async def record_automation_run_status(
        self,
        *,
        run: AutomationJobRun,
        status: str,
        summary: str,
        result_code: str,
        resource_refs: list[ControlPlaneResourceRef] | None = None,
    ) -> AutomationJobRun:
        updated = run.model_copy(
            update={
                "status": status,
                "summary": summary,
                "result_code": result_code,
                "resource_refs": resource_refs or run.resource_refs,
                "completed_at": datetime.now(tz=UTC),
            }
        )
        self._automation_store.save_run(updated)
        await self._publish_resource_event(
            resource_ref=self._resource_ref("automation_job", "automation:jobs"),
            request=ActionRequestEnvelope(
                request_id=run.request_id,
                action_id="automation.run",
                params={"job_id": run.job_id},
                surface=ControlPlaneSurface.SYSTEM,
                actor=ControlPlaneActor(actor_id="system:automation", actor_label="automation"),
            ),
            correlation_id=updated.run_id,
            summary=f"automation job {status}",
        )
        return updated

    async def create_automation_run(
        self,
        *,
        job: AutomationJob,
        actor: ControlPlaneActor,
    ) -> AutomationJobRun:
        run = AutomationJobRun(
            run_id=str(ULID()),
            job_id=job.job_id,
            request_id=str(ULID()),
            correlation_id=str(ULID()),
            status="running",
            summary=f"automation job {job.name} running",
        )
        self._automation_store.save_run(run)
        return run

    async def _publish_action_event(
        self,
        *,
        event_type: ControlPlaneEventType,
        request: ActionRequestEnvelope,
        summary: str,
        correlation_id: str | None = None,
    ) -> str:
        event = ControlPlaneEvent(
            event_type=event_type,
            request_id=request.request_id,
            correlation_id=correlation_id or request.request_id,
            causation_id=request.request_id,
            actor=request.actor,
            surface=request.surface,
            payload_summary=summary,
            metadata={"action_id": request.action_id},
        )
        return await self._append_control_event(event)

    async def _publish_action_result_event(
        self,
        *,
        result: ActionResultEnvelope,
        request: ActionRequestEnvelope,
        event_type: ControlPlaneEventType,
    ) -> str:
        event = ControlPlaneEvent(
            event_type=event_type,
            request_id=result.request_id,
            correlation_id=result.correlation_id,
            causation_id=request.request_id,
            actor=request.actor,
            surface=request.surface,
            payload_summary=result.message,
            resource_refs=result.resource_refs,
            target_refs=result.target_refs,
            metadata={"action_id": result.action_id, "code": result.code},
        )
        return await self._append_control_event(event)

    async def _publish_resource_event(
        self,
        *,
        resource_ref: ControlPlaneResourceRef,
        request: ActionRequestEnvelope,
        correlation_id: str,
        summary: str,
    ) -> str:
        event = ControlPlaneEvent(
            event_type=ControlPlaneEventType.RESOURCE_PROJECTED,
            request_id=request.request_id,
            correlation_id=correlation_id,
            causation_id=request.request_id,
            actor=request.actor,
            surface=request.surface,
            payload_summary=summary,
            resource_ref=resource_ref,
        )
        return await self._append_control_event(event)

    async def _append_control_event(self, event: ControlPlaneEvent) -> str:
        await self._ensure_audit_task()
        payload = ControlPlaneAuditPayload(
            event_type=event.event_type.value,
            contract_version=event.contract_version,
            request_id=event.request_id,
            correlation_id=event.correlation_id,
            causation_id=event.causation_id,
            actor_id=event.actor.actor_id,
            actor_label=event.actor.actor_label,
            surface=event.surface.value,
            payload_summary=event.payload_summary,
            resource_ref=event.resource_ref.model_dump(mode="json") if event.resource_ref else None,
            resource_refs=[item.model_dump(mode="json") for item in event.resource_refs],
            target_refs=[item.model_dump(mode="json") for item in event.target_refs],
            metadata=event.metadata,
        )
        audit_event = Event(
            event_id=str(ULID()),
            task_id=_AUDIT_TASK_ID,
            task_seq=await self._stores.event_store.get_next_task_seq(_AUDIT_TASK_ID),
            ts=event.occurred_at,
            type=self._map_control_event_type(event.event_type),
            actor=ActorType.SYSTEM,
            payload=payload.model_dump(mode="json"),
            trace_id=_AUDIT_TRACE_ID,
            causality=EventCausality(parent_event_id=event.causation_id or None),
        )
        await self._stores.event_store.append_event_committed(audit_event, update_task_pointer=True)
        return audit_event.event_id

    async def _ensure_audit_task(self) -> None:
        if self._audit_task_ensured:
            return
        existing = await self._stores.task_store.get_task(_AUDIT_TASK_ID)
        if existing is None:
            now = datetime.now(tz=UTC)
            await self._stores.task_store.create_task(
                Task(
                    task_id=_AUDIT_TASK_ID,
                    created_at=now,
                    updated_at=now,
                    status=TaskStatus.RUNNING,
                    title="Control Plane Audit",
                    thread_id="ops:control-plane",
                    scope_id="ops:control-plane",
                    requester=RequesterInfo(channel="system", sender_id="system:control-plane"),
                    pointers=TaskPointers(),
                    trace_id=_AUDIT_TRACE_ID,
                )
            )
            await self._stores.conn.commit()
        self._audit_task_ensured = True

    async def _ensure_policy_system_task(self) -> None:
        existing = await self._stores.task_store.get_task(_POLICY_TASK_ID)
        if existing is not None:
            return
        now = datetime.now(tz=UTC)
        await self._stores.task_store.create_task(
            Task(
                task_id=_POLICY_TASK_ID,
                created_at=now,
                updated_at=now,
                status=TaskStatus.RUNNING,
                title="Policy Engine Runtime",
                thread_id="ops:policy-engine",
                scope_id="ops:policy-engine",
                requester=RequesterInfo(channel="system", sender_id="system:policy-engine"),
                pointers=TaskPointers(),
                trace_id=_POLICY_TRACE_ID,
            )
        )
        await self._stores.conn.commit()

    async def _resolve_selection(self) -> tuple[ControlPlaneState, Any | None, Any | None, str]:
        state = self._state_store.load()
        fallback_reason = ""
        selector = await self._stores.project_store.get_selector_state("web")
        project = (
            await self._stores.project_store.get_project(state.selected_project_id)
            if state.selected_project_id
            else None
        )
        if project is None and selector is not None:
            project = await self._stores.project_store.get_project(selector.active_project_id)
        if project is None:
            project = await self._stores.project_store.get_default_project()
            if project is not None and state.selected_project_id:
                fallback_reason = "selected project 不存在，已回退到 default project"

        workspace = (
            await self._stores.project_store.get_workspace(state.selected_workspace_id)
            if state.selected_workspace_id
            else None
        )
        if workspace is None and selector is not None and selector.active_workspace_id:
            candidate_workspace = await self._stores.project_store.get_workspace(
                selector.active_workspace_id
            )
            if candidate_workspace is not None and (
                project is None or candidate_workspace.project_id == project.project_id
            ):
                workspace = candidate_workspace
        if project is not None and (
            workspace is None or workspace.project_id != project.project_id
        ):
            workspace = await self._stores.project_store.get_primary_workspace(project.project_id)
            if state.selected_workspace_id and workspace is not None:
                fallback_reason = (
                    fallback_reason or "selected workspace 不存在，已回退到 primary workspace"
                )

        if project is not None and (
            state.selected_project_id != project.project_id
            or (workspace is not None and state.selected_workspace_id != workspace.workspace_id)
        ):
            self._state_store.save(
                state.model_copy(
                    update={
                        "selected_project_id": project.project_id,
                        "selected_workspace_id": workspace.workspace_id if workspace else "",
                        "updated_at": datetime.now(tz=UTC),
                    }
                )
            )
        if project is not None and (
            selector is None
            or selector.active_project_id != project.project_id
            or selector.active_workspace_id != (workspace.workspace_id if workspace else None)
        ):
            await self._sync_web_project_selector_state(
                project=project,
                workspace=workspace,
                source="control_plane_sync",
                warnings=[fallback_reason] if fallback_reason else [],
            )
            await self._stores.conn.commit()
        return state, project, workspace, fallback_reason

    @staticmethod
    def _matches_selected_scope(
        *,
        item_project_id: str | None,
        item_workspace_id: str | None,
        selected_project: Any | None,
        selected_workspace: Any | None,
    ) -> bool:
        if selected_project is None:
            return not item_project_id
        if item_project_id and item_project_id != selected_project.project_id:
            return False
        return not (
            selected_workspace is not None
            and item_workspace_id
            and item_workspace_id != selected_workspace.workspace_id
        )

    async def _get_import_source_in_scope(self, source_id: str):
        return await self._import_workbench_service.get_source(source_id)

    async def _get_import_run_in_scope(self, run_id: str):
        return await self._import_workbench_service.get_run(run_id)

    async def _get_automation_job_in_scope(self, job_id: str) -> AutomationJob:
        job = self._automation_store.get_job(job_id)
        if job is None:
            raise ControlPlaneActionError("JOB_NOT_FOUND", "automation job 不存在")
        return job

    async def _get_work_in_scope(self, work_id: str):
        work = await self._stores.work_store.get_work(work_id)
        if work is None:
            raise ControlPlaneActionError("WORK_NOT_FOUND", "work 不存在")
        return work

    async def _collect_bridge_refs(self) -> list[dict[str, Any]]:
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

    def _credential_store(self) -> CredentialStore:
        return CredentialStore(store_path=self._project_root / "auth-profiles.json")

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

    def _collect_provider_runtime_details(
        self,
        config_value: Mapping[str, Any],
        *,
        secret_audit,
        bridge_refs: list[dict[str, Any]],
        litellm_sync_ok: bool,
    ) -> dict[str, Any]:
        providers = [
            item for item in config_value.get("providers", []) if isinstance(item, dict)
        ]
        env_litellm = self._env_file_values(self._project_root / ".env.litellm")
        env_runtime = self._env_file_values(self._project_root / ".env")
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

    def _provider_alias_defaults(
        self,
        provider_id: str,
        *,
        auth_type: str,
        api_key_env: str,
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        provider_name = (
            "OpenAI Codex (ChatGPT Pro OAuth)"
            if provider_id == "openai-codex"
            else provider_id.replace("-", " ").title()
        )
        providers = [
            {
                "id": provider_id,
                "name": provider_name,
                "auth_type": auth_type,
                "api_key_env": api_key_env,
                "enabled": True,
            }
        ]
        if provider_id == "openai-codex":
            aliases = {
                "main": {
                    "provider": provider_id,
                    "model": "gpt-5.4",
                    "description": "主力模型",
                    "thinking_level": "xhigh",
                },
                "cheap": {
                    "provider": provider_id,
                    "model": "gpt-5.4",
                    "description": "轻量模型",
                    "thinking_level": "low",
                },
            }
        else:
            default_model = (
                "openrouter/auto" if provider_id == "openrouter" else f"{provider_id}/auto"
            )
            aliases = {
                "main": {
                    "provider": provider_id,
                    "model": default_model,
                    "description": "主力模型",
                },
                "cheap": {
                    "provider": provider_id,
                    "model": default_model,
                    "description": "低成本模型",
                },
            }
        return providers, aliases

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

        self._write_env_values(self._project_root / ".env.litellm", litellm_updates)
        self._write_env_values(self._project_root / ".env", runtime_updates)

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

    def _build_session_capabilities(self, task: Task) -> list[ControlPlaneCapability]:
        can_resume = task.status in {TaskStatus.FAILED, TaskStatus.REJECTED}
        can_interrupt = task.status in {
            TaskStatus.CREATED,
            TaskStatus.RUNNING,
            TaskStatus.WAITING_INPUT,
            TaskStatus.WAITING_APPROVAL,
            TaskStatus.PAUSED,
        }
        return [
            ControlPlaneCapability(
                capability_id="session.focus",
                label="聚焦",
                action_id="session.focus",
            ),
            ControlPlaneCapability(
                capability_id="session.export",
                label="导出",
                action_id="session.export",
            ),
            ControlPlaneCapability(
                capability_id="session.reset",
                label="重置",
                action_id="session.reset",
            ),
            ControlPlaneCapability(
                capability_id="session.interrupt",
                label="中断",
                action_id="session.interrupt",
                enabled=can_interrupt,
                support_status=(
                    ControlPlaneSupportStatus.SUPPORTED
                    if can_interrupt
                    else ControlPlaneSupportStatus.DEGRADED
                ),
            ),
            ControlPlaneCapability(
                capability_id="session.resume",
                label="恢复",
                action_id="session.resume",
                enabled=can_resume,
                support_status=(
                    ControlPlaneSupportStatus.SUPPORTED
                    if can_resume
                    else ControlPlaneSupportStatus.DEGRADED
                ),
            ),
        ]

    async def _extract_latest_user_message(self, task_id: str) -> str:
        events = await self._stores.event_store.get_events_for_task(task_id)
        for event in reversed(events):
            if event.type == EventType.USER_MESSAGE:
                text = str(event.payload.get("text", "")).strip()
                if text:
                    return text
                return str(event.payload.get("text_preview", "")).strip()
        return ""

    async def _extract_latest_user_metadata(self, task_id: str) -> dict[str, Any]:
        events = await self._stores.event_store.get_events_for_task(task_id)
        return merge_control_metadata(events)

    async def _extract_latest_session_agent_profile_id(self, task_id: str) -> str:
        events = await self._stores.event_store.get_events_for_task(task_id)
        for event in reversed(events):
            if event.type is not EventType.USER_MESSAGE:
                continue
            payload = getattr(event, "payload", {}) or {}
            if not isinstance(payload, Mapping):
                continue
            control = payload.get("control_metadata", {})
            if not isinstance(control, Mapping):
                continue
            value = str(
                control.get("session_owner_profile_id", "")
                or control.get("agent_profile_id", "")
            ).strip()
            if value:
                return value
        return ""

    @staticmethod
    def _is_work_merge_ready(work, works: list[Any]) -> bool:
        children = [item for item in works if item.parent_work_id == work.work_id]
        if not children:
            return False
        return all(item.status.value in _TERMINAL_WORK_STATUSES for item in children)

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
    def _worker_profile_summary(capabilities: list[str], tool_groups: list[str]) -> str:
        capability_summary = "、".join(capabilities[:2]) if capabilities else "通用协调"
        tool_summary = "、".join(tool_groups[:2]) if tool_groups else "基础工具"
        return f"静态配置面向 {capability_summary}，当前默认能力组为 {tool_summary}。"

    @staticmethod
    def _worker_snapshot_id(profile_id: str, revision: int | None) -> str:
        resolved_revision = revision or 1
        return f"worker-snapshot:{profile_id}:{resolved_revision}"

    @staticmethod
    def _tool_selection_from_work(work: Work | None) -> DynamicToolSelection | None:
        if work is None:
            return None
        raw = work.metadata.get("tool_selection", {})
        if not isinstance(raw, dict):
            return None
        try:
            return DynamicToolSelection.model_validate(raw)
        except Exception:
            return None

    def _build_agent_profile_from_worker_profile(
        self,
        *,
        profile: WorkerProfile,
        revision: int,
        existing: AgentProfile | None = None,
    ) -> AgentProfile:
        metadata = dict(existing.metadata) if existing is not None else {}
        metadata.update(dict(profile.metadata))
        metadata.update(
            {
                "source_kind": "worker_profile_sync",
                "worker_profile_id": profile.profile_id,
                "worker_profile_revision": revision,
                "worker_profile_status": profile.status.value,
            }
        )
        return AgentProfile(
            profile_id=profile.profile_id,
            scope=profile.scope,
            project_id=profile.project_id,
            name=profile.name,
            persona_summary=profile.summary,
            model_alias=profile.model_alias,
            tool_profile=profile.tool_profile,
            metadata=metadata,
            version=max(existing.version if existing is not None else 1, revision or 1),
            created_at=existing.created_at if existing is not None else profile.created_at,
            updated_at=datetime.now(tz=UTC),
        )

    async def _sync_worker_profile_agent_profile(
        self,
        profile: WorkerProfile,
        *,
        revision: int,
    ) -> AgentProfile:
        existing = await self._stores.agent_context_store.get_agent_profile(profile.profile_id)
        mirrored = self._build_agent_profile_from_worker_profile(
            profile=profile,
            revision=revision,
            existing=existing,
        )
        await self._stores.agent_context_store.save_agent_profile(mirrored)
        # 同步时确保 agent-private 行为文件存在
        _slug = resolve_behavior_agent_slug(mirrored)
        materialize_agent_behavior_files(
            self._project_root,
            agent_slug=_slug,
            agent_name=profile.name,
            is_worker_profile=True,
        )
        return mirrored

    async def _bind_worker_profile_as_default(
        self,
        *,
        profile: WorkerProfile,
    ) -> bool:
        if profile.scope != AgentProfileScope.PROJECT or not profile.project_id:
            return False
        project = await self._stores.project_store.get_project(profile.project_id)
        if project is None:
            return False
        if project.default_agent_profile_id == profile.profile_id:
            return False
        await self._stores.project_store.save_project(
            project.model_copy(
                update={
                    "default_agent_profile_id": profile.profile_id,
                    "updated_at": datetime.now(tz=UTC),
                }
            )
        )
        return True

    def _build_worker_dynamic_context(
        self,
        works: list[Work],
        *,
        fallback_tools: list[str],
        fallback_project_id: str = "",
        fallback_workspace_id: str = "",
    ) -> WorkerProfileDynamicContext:
        active_statuses = {
            "created",
            "assigned",
            "running",
            "waiting_input",
            "waiting_approval",
            "paused",
            "escalated",
        }
        running_statuses = {"created", "assigned", "running"}
        attention_statuses = {"waiting_input", "waiting_approval", "paused", "escalated", "failed"}
        latest = works[0] if works else None
        selection = self._tool_selection_from_work(latest)
        active_works = [item for item in works if item.status.value in active_statuses]
        attention_works = [item for item in works if item.status.value in attention_statuses]
        return WorkerProfileDynamicContext(
            active_project_id=(
                latest.project_id if latest is not None else fallback_project_id
            ),
            active_workspace_id=(
                latest.workspace_id if latest is not None else fallback_workspace_id
            ),
            active_work_count=len(active_works),
            running_work_count=len(
                [item for item in active_works if item.status.value in running_statuses]
            ),
            attention_work_count=len(attention_works),
            latest_work_id=latest.work_id if latest is not None else "",
            latest_task_id=latest.task_id if latest is not None else "",
            latest_work_title=latest.title if latest is not None else "",
            latest_work_status=latest.status.value if latest is not None else "",
            latest_target_kind=latest.target_kind.value if latest is not None else "",
            current_selected_tools=(
                list(selection.effective_tool_universe.selected_tools)
                if selection is not None and selection.effective_tool_universe is not None
                else list(latest.selected_tools)
                if latest is not None and latest.selected_tools
                else list(fallback_tools)
            ),
            current_tool_resolution_mode=(
                selection.resolution_mode if selection is not None else ""
            ),
            current_tool_warnings=list(selection.warnings) if selection is not None else [],
            current_mounted_tools=(
                list(selection.mounted_tools) if selection is not None else []
            ),
            current_blocked_tools=(
                list(selection.blocked_tools) if selection is not None else []
            ),
            current_discovery_entrypoints=(
                list(selection.effective_tool_universe.discovery_entrypoints)
                if selection is not None and selection.effective_tool_universe is not None
                else []
            ),
            updated_at=latest.updated_at if latest is not None else None,
        )

    def _worker_profile_control_capabilities(
        self,
        status: WorkerProfileStatus,
        *,
        builtin: bool = False,
    ) -> list[ControlPlaneCapability]:
        if builtin:
            return [
                ControlPlaneCapability(
                    capability_id="worker_profile.clone",
                    label="Fork 成自定义 Root Agent",
                    action_id="worker_profile.clone",
                ),
                ControlPlaneCapability(
                    capability_id="worker.spawn_from_profile",
                    label="按这个 Root Agent 启动",
                    action_id="worker.spawn_from_profile",
                ),
            ]
        is_archived = status == WorkerProfileStatus.ARCHIVED
        return [
            ControlPlaneCapability(
                capability_id="worker_profile.review",
                label="检查 Profile",
                action_id="worker_profile.review",
                enabled=not is_archived,
                support_status=(
                    ControlPlaneSupportStatus.SUPPORTED
                    if not is_archived
                    else ControlPlaneSupportStatus.DEGRADED
                ),
                reason="归档后不能继续修改或发布。" if is_archived else "",
            ),
            ControlPlaneCapability(
                capability_id="worker_profile.apply",
                label="保存草稿",
                action_id="worker_profile.apply",
                enabled=not is_archived,
                support_status=(
                    ControlPlaneSupportStatus.SUPPORTED
                    if not is_archived
                    else ControlPlaneSupportStatus.DEGRADED
                ),
                reason="归档后不能继续修改或发布。" if is_archived else "",
            ),
            ControlPlaneCapability(
                capability_id="worker_profile.publish",
                label="发布 Revision",
                action_id="worker_profile.publish",
                enabled=not is_archived,
                support_status=(
                    ControlPlaneSupportStatus.SUPPORTED
                    if not is_archived
                    else ControlPlaneSupportStatus.DEGRADED
                ),
                reason="归档后不能继续发布 revision。" if is_archived else "",
            ),
            ControlPlaneCapability(
                capability_id="worker_profile.bind_default",
                label="设为聊天默认",
                action_id="worker_profile.bind_default",
                enabled=not is_archived and status == WorkerProfileStatus.ACTIVE,
                support_status=(
                    ControlPlaneSupportStatus.SUPPORTED
                    if (not is_archived and status == WorkerProfileStatus.ACTIVE)
                    else ControlPlaneSupportStatus.DEGRADED
                ),
                reason=(
                    ""
                    if (not is_archived and status == WorkerProfileStatus.ACTIVE)
                    else "只有已发布且未归档的 Root Agent 才能绑定为当前聊天默认。"
                ),
            ),
            ControlPlaneCapability(
                capability_id="worker_profile.clone",
                label="复制为新 Profile",
                action_id="worker_profile.clone",
            ),
            ControlPlaneCapability(
                capability_id="worker.spawn_from_profile",
                label="按这个 Root Agent 启动",
                action_id="worker.spawn_from_profile",
                enabled=not is_archived,
                support_status=(
                    ControlPlaneSupportStatus.SUPPORTED
                    if not is_archived
                    else ControlPlaneSupportStatus.DEGRADED
                ),
                reason="归档后不能再用于启动新任务。" if is_archived else "",
            ),
            ControlPlaneCapability(
                capability_id="worker_profile.archive",
                label="归档",
                action_id="worker_profile.archive",
                enabled=not is_archived,
                support_status=(
                    ControlPlaneSupportStatus.SUPPORTED
                    if not is_archived
                    else ControlPlaneSupportStatus.DEGRADED
                ),
                reason="当前 profile 已归档。" if is_archived else "",
            ),
        ]

    @staticmethod
    def _normalize_text_list(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        raw = str(value).strip()
        if not raw:
            return []
        return [item.strip() for item in raw.splitlines() if item.strip()]

    @staticmethod
    def _normalize_string_list(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        raw = str(value).strip()
        if not raw:
            return []
        return [item.strip() for item in raw.split(",") if item.strip()]

    @staticmethod
    def _normalize_dict(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        return {}

    @staticmethod
    def _slugify_worker_profile_token(value: str) -> str:
        lowered = value.strip().lower()
        if not lowered:
            return "worker"
        chars: list[str] = []
        previous_dash = False
        for char in lowered:
            if char.isascii() and char.isalnum():
                chars.append(char)
                previous_dash = False
                continue
            if previous_dash:
                continue
            chars.append("-")
            previous_dash = True
        token = "".join(chars).strip("-")
        return token or "worker"

    async def _generate_worker_profile_id(
        self,
        *,
        name: str,
        project_id: str,
        scope: str,
        existing_profile_id: str = "",
    ) -> str:
        seed = self._slugify_worker_profile_token(name)
        scope_prefix = project_id or "system" if scope == "project" else "system"
        candidate = f"worker-profile-{scope_prefix}-{seed}"
        if existing_profile_id and existing_profile_id == candidate:
            return candidate
        existing = await self._stores.agent_context_store.get_worker_profile(candidate)
        if existing is None or existing.profile_id == existing_profile_id:
            return candidate
        return f"{candidate}-{str(ULID()).lower()[-6:]}"

    async def _resolve_builtin_worker_source(
        self,
        profile_id: str,
    ) -> WorkerProfile | None:
        if not profile_id.startswith("singleton:"):
            return None
        worker_type = profile_id.split(":", 1)[1]
        capability_pack = await self.get_capability_pack_document()
        builtin = next(
            (
                item
                for item in capability_pack.pack.worker_profiles
                if item.worker_type == worker_type
            ),
            None,
        )
        if builtin is None:
            return None
        return WorkerProfile(
            profile_id=profile_id,
            scope=AgentProfileScope.SYSTEM,
            project_id="",
            name=self._worker_profile_label(worker_type),
            summary=self._worker_profile_summary(
                list(builtin.capabilities),
                list(builtin.default_tool_groups),
            ),
            model_alias=builtin.default_model_alias,
            tool_profile=builtin.default_tool_profile,
            default_tool_groups=list(builtin.default_tool_groups),
            selected_tools=[],
            runtime_kinds=[item.value for item in builtin.runtime_kinds],
            metadata={},
            status=WorkerProfileStatus.ACTIVE,
            origin_kind=WorkerProfileOriginKind.BUILTIN,
            draft_revision=1,
            active_revision=1,
        )

    async def _get_worker_profile_in_scope(
        self,
        profile_id: str,
    ) -> WorkerProfile:
        _, selected_project, _, _ = await self._resolve_selection()
        builtin = await self._resolve_builtin_worker_source(profile_id)
        if builtin is not None:
            return builtin
        profile = await self._stores.agent_context_store.get_worker_profile(profile_id)
        if profile is None:
            raise ControlPlaneActionError("WORKER_PROFILE_NOT_FOUND", "Root Agent profile 不存在")
        if (
            profile.scope == AgentProfileScope.PROJECT
            and selected_project is not None
            and profile.project_id
            and profile.project_id != selected_project.project_id
        ):
            raise ControlPlaneActionError(
                "WORKER_PROFILE_NOT_IN_SCOPE",
                "当前 project 不能操作这个 Root Agent profile。",
            )
        return profile

    async def _review_worker_profile_draft(
        self,
        *,
        raw: Mapping[str, Any],
        mode: str,
        existing: WorkerProfile | None = None,
        source_profile: WorkerProfile | None = None,
        selected_project: Any | None,
        selected_workspace: Any | None,
        origin_kind: WorkerProfileOriginKind | None = None,
    ) -> dict[str, Any]:
        capability_pack = await self.get_capability_pack_document()
        builtin_defaults = {
            item.worker_type: item for item in capability_pack.pack.worker_profiles
        }
        available_tool_groups = sorted(
            {
                tool.tool_group
                for tool in capability_pack.pack.tools
                if str(tool.tool_group).strip()
            }
        )
        available_tools = {
            tool.tool_name: tool for tool in capability_pack.pack.tools if str(tool.tool_name).strip()
        }
        valid_runtime_kinds = {"worker", "subagent", "acp_runtime", "graph_agent"}
        valid_tool_profiles = {"minimal", "standard", "privileged"}

        existing_data = existing.model_dump(mode="json") if existing is not None else {}
        source_data = source_profile.model_dump(mode="json") if source_profile is not None else {}
        scope = (
            self._param_str(raw, "scope")
            or str(existing_data.get("scope", ""))
            or str(source_data.get("scope", ""))
            or ("project" if selected_project is not None else "system")
        ).lower()
        project_id = (
            self._param_str(raw, "project_id")
            or str(existing_data.get("project_id", ""))
            or str(source_data.get("project_id", ""))
            or (selected_project.project_id if selected_project is not None else "")
        )
        name = (
            self._param_str(raw, "name")
            or str(existing_data.get("name", ""))
            or str(source_data.get("name", ""))
        )
        summary = (
            self._param_str(raw, "summary")
            or str(existing_data.get("summary", ""))
            or str(source_data.get("summary", ""))
        )
        builtin = builtin_defaults.get("general")
        default_tool_groups = self._normalize_string_list(raw.get("default_tool_groups"))
        if not default_tool_groups:
            default_tool_groups = (
                self._normalize_string_list(existing_data.get("default_tool_groups"))
                or self._normalize_string_list(source_data.get("default_tool_groups"))
                or (list(builtin.default_tool_groups) if builtin is not None else [])
            )
        selected_tools = self._normalize_string_list(raw.get("selected_tools"))
        if not selected_tools:
            selected_tools = self._normalize_string_list(existing_data.get("selected_tools")) or self._normalize_string_list(source_data.get("selected_tools"))
        runtime_kinds = self._normalize_string_list(raw.get("runtime_kinds"))
        if not runtime_kinds:
            runtime_kinds = (
                self._normalize_string_list(existing_data.get("runtime_kinds"))
                or self._normalize_string_list(source_data.get("runtime_kinds"))
                or ([item.value for item in builtin.runtime_kinds] if builtin is not None else ["worker"])
            )
        model_alias = (
            self._param_str(raw, "model_alias", default="")
            or str(existing_data.get("model_alias", ""))
            or str(source_data.get("model_alias", ""))
            or (builtin.default_model_alias if builtin is not None else "main")
        )
        tool_profile = (
            self._param_str(raw, "tool_profile", default="")
            or str(existing_data.get("tool_profile", ""))
            or str(source_data.get("tool_profile", ""))
            or (builtin.default_tool_profile if builtin is not None else "minimal")
        )
        metadata = self._normalize_dict(raw.get("metadata"))
        if not metadata:
            metadata = self._normalize_dict(existing_data.get("metadata")) or self._normalize_dict(source_data.get("metadata"))
        resource_limits = self._normalize_dict(raw.get("resource_limits"))
        if not resource_limits:
            resource_limits = self._normalize_dict(existing_data.get("resource_limits")) or self._normalize_dict(source_data.get("resource_limits"))

        profile_id = self._param_str(raw, "profile_id")
        if not profile_id:
            profile_id = str(existing_data.get("profile_id", "")) or str(source_data.get("profile_id", ""))
        if not profile_id or profile_id.startswith("singleton:"):
            profile_id = await self._generate_worker_profile_id(
                name=name or "",
                project_id=project_id,
                scope=scope,
                existing_profile_id=existing.profile_id if existing is not None else "",
            )

        normalized = {
            "profile_id": profile_id,
            "scope": scope,
            "project_id": project_id if scope == "project" else "",
            "name": name,
            "summary": summary
            or self._worker_profile_summary(default_tool_groups, default_tool_groups),
            "model_alias": model_alias or "main",
            "tool_profile": tool_profile or "minimal",
            "default_tool_groups": default_tool_groups,
            "selected_tools": selected_tools,
            "runtime_kinds": runtime_kinds,
            "metadata": metadata,
            "resource_limits": resource_limits,
            "origin_kind": (
                origin_kind.value
                if origin_kind is not None
                else (
                    existing.origin_kind.value
                    if existing is not None
                    else (
                        source_profile.origin_kind.value
                        if source_profile is not None
                        and source_profile.origin_kind != WorkerProfileOriginKind.BUILTIN
                        else WorkerProfileOriginKind.CUSTOM.value
                    )
                )
            ),
        }

        save_errors: list[str] = []
        blocking_reasons: list[str] = []
        warnings: list[str] = []
        if scope not in {"system", "project"}:
            save_errors.append("scope 只支持 system / project。")
        if not name:
            save_errors.append("name 不能为空。")
        if scope == "project" and not project_id:
            save_errors.append("project scope 的 Root Agent 需要 project_id。")
        model_alias_valid, available_aliases = self._validate_model_alias(model_alias or "main")
        if not model_alias_valid:
            save_errors.append(
                "model_alias 必须引用已存在的模型别名。"
                f" 当前为 '{model_alias or 'main'}'，可选：{', '.join(available_aliases)}。"
            )
        if tool_profile not in valid_tool_profiles:
            save_errors.append("tool_profile 只支持 minimal / standard / privileged。")
        invalid_runtime_kinds = [item for item in runtime_kinds if item not in valid_runtime_kinds]
        if invalid_runtime_kinds:
            save_errors.append(
                f"runtime_kinds 含无效值：{'、'.join(invalid_runtime_kinds)}。"
            )
        missing_tool_groups = [
            item for item in default_tool_groups if item not in available_tool_groups
        ]
        if missing_tool_groups:
            blocking_reasons.append(
                f"默认工具组不存在：{'、'.join(missing_tool_groups)}。"
            )
        missing_tools = [item for item in selected_tools if item not in available_tools]
        if missing_tools:
            blocking_reasons.append(f"选中的工具不存在：{'、'.join(missing_tools)}。")
        unavailable_tools = [
            item
            for item in selected_tools
            if item in available_tools
            and available_tools[item].availability.value != "available"
        ]
        if unavailable_tools:
            warnings.append(
                f"这些工具当前不是 available：{'、'.join(unavailable_tools)}。"
            )
        if not default_tool_groups and not selected_tools:
            warnings.append("当前没有默认工具组和固定工具，运行时会更依赖动态 tool index。")
        if not summary:
            warnings.append("建议补一段 summary，方便 Butler 和 Control Plane 解释这个 Root Agent。")
        if selected_project is not None:
            policy_profile_id, policy_profile = self._resolve_effective_policy_profile(
                selected_project
            )
            if not self._tool_profile_allowed(tool_profile, policy_profile.allowed_tool_profile.value):
                warnings.append(
                    "当前 profile 的 tool_profile 高于当前 project policy，运行时可能被降级或要求审批。"
                )
        if existing is not None and existing.status == WorkerProfileStatus.ARCHIVED:
            save_errors.append("归档后的 Root Agent 不能直接更新，请先 clone 一个新 profile。")

        snapshot_fields = (
            "name",
            "summary",
            "model_alias",
            "tool_profile",
            "default_tool_groups",
            "selected_tools",
            "runtime_kinds",
        )
        diff_items: list[dict[str, Any]] = []
        before_payload = existing_data or source_data
        for field in snapshot_fields:
            before_value = before_payload.get(field)
            after_value = normalized.get(field)
            if before_value != after_value:
                diff_items.append(
                    {
                        "field": field,
                        "before": before_value,
                        "after": after_value,
                    }
                )

        next_actions: list[str] = []
        if save_errors:
            next_actions.append("先补齐必填字段，再保存或发布这个 Root Agent。")
        elif blocking_reasons:
            next_actions.append("先处理工具组或工具可用性问题，再发布 revision。")
        else:
            next_actions.append("检查通过，可以保存草稿或直接发布 revision。")
        if not selected_tools:
            next_actions.append("如果你希望行为更稳定，建议至少 pin 1-3 个核心工具。")

        return {
            "mode": mode,
            "can_save": not save_errors,
            "ready": not save_errors and not blocking_reasons,
            "warnings": warnings,
            "save_errors": save_errors,
            "blocking_reasons": blocking_reasons,
            "next_actions": next_actions,
            "profile": normalized,
            "existing_profile": existing_data,
            "source_profile": source_data,
            "diff": {
                "has_changes": bool(diff_items),
                "changed_fields": diff_items,
            },
            "catalog": {
                "tool_group_count": len(available_tool_groups),
                "tool_count": len(available_tools),
                "available_tool_groups": available_tool_groups,
            },
            "dynamic_context_hint": {
                "project_id": selected_project.project_id if selected_project is not None else "",
                "workspace_id": (
                    selected_workspace.workspace_id if selected_workspace is not None else ""
                ),
            },
        }

    def _worker_profile_snapshot_payload(self, profile: WorkerProfile) -> dict[str, Any]:
        return {
            "profile_id": profile.profile_id,
            "scope": profile.scope.value,
            "project_id": profile.project_id,
            "name": profile.name,
            "summary": profile.summary,
            "model_alias": profile.model_alias,
            "tool_profile": profile.tool_profile,
            "default_tool_groups": list(profile.default_tool_groups),
            "selected_tools": list(profile.selected_tools),
            "runtime_kinds": list(profile.runtime_kinds),
            "metadata": dict(profile.metadata),
            "resource_limits": dict(profile.resource_limits),
            "origin_kind": profile.origin_kind.value,
        }

    async def _save_worker_profile_draft(
        self,
        *,
        normalized_profile: Mapping[str, Any],
        existing: WorkerProfile | None,
        origin_kind: WorkerProfileOriginKind | None = None,
    ) -> WorkerProfile:
        now = datetime.now(tz=UTC)
        resolved_origin = (
            origin_kind
            if origin_kind is not None
            else (
                existing.origin_kind
                if existing is not None
                else WorkerProfileOriginKind(
                    str(normalized_profile.get("origin_kind", WorkerProfileOriginKind.CUSTOM.value))
                )
            )
        )
        if existing is None:
            status = WorkerProfileStatus.DRAFT
            draft_revision = 1
            active_revision = 0
            created_at = now
        else:
            status = (
                WorkerProfileStatus.ACTIVE
                if existing.active_revision > 0 and existing.status != WorkerProfileStatus.ARCHIVED
                else WorkerProfileStatus.DRAFT
            )
            draft_revision = (
                max(existing.draft_revision, existing.active_revision + 1)
                if existing.active_revision > 0
                else max(existing.draft_revision, 1)
            )
            active_revision = existing.active_revision
            created_at = existing.created_at
        saved = await self._stores.agent_context_store.save_worker_profile(
            WorkerProfile(
                profile_id=str(normalized_profile.get("profile_id", "")),
                scope=AgentProfileScope(str(normalized_profile.get("scope", "project"))),
                project_id=str(normalized_profile.get("project_id", "")),
                name=str(normalized_profile.get("name", "")),
                summary=str(normalized_profile.get("summary", "")),
                model_alias=str(normalized_profile.get("model_alias", "main")),
                tool_profile=str(normalized_profile.get("tool_profile", "minimal")),
                default_tool_groups=self._normalize_string_list(
                    normalized_profile.get("default_tool_groups")
                ),
                selected_tools=self._normalize_string_list(
                    normalized_profile.get("selected_tools")
                ),
                runtime_kinds=self._normalize_string_list(
                    normalized_profile.get("runtime_kinds")
                ),
                metadata=self._normalize_dict(normalized_profile.get("metadata")),
                resource_limits=self._normalize_dict(normalized_profile.get("resource_limits")),
                status=status,
                origin_kind=resolved_origin,
                draft_revision=draft_revision,
                active_revision=active_revision,
                created_at=created_at,
                updated_at=now,
                archived_at=None,
            )
        )
        await self._stores.conn.commit()
        return saved

    async def _publish_worker_profile_revision(
        self,
        *,
        profile: WorkerProfile,
        change_summary: str,
        actor: str,
    ) -> tuple[WorkerProfile, WorkerProfileRevision, bool]:
        revisions = await self._stores.agent_context_store.list_worker_profile_revisions(
            profile.profile_id
        )
        snapshot_payload = self._worker_profile_snapshot_payload(profile)
        latest = revisions[0] if revisions else None
        if (
            latest is not None
            and latest.snapshot_payload == snapshot_payload
            and latest.revision == profile.active_revision
            and profile.status == WorkerProfileStatus.ACTIVE
        ):
            return profile, latest, False

        next_revision = profile.draft_revision or profile.active_revision or 1
        if next_revision <= profile.active_revision:
            next_revision = profile.active_revision + 1
        revision = await self._stores.agent_context_store.save_worker_profile_revision(
            WorkerProfileRevision(
                revision_id=self._worker_snapshot_id(profile.profile_id, next_revision),
                profile_id=profile.profile_id,
                revision=next_revision,
                change_summary=change_summary,
                snapshot_payload=snapshot_payload,
                created_by=actor,
                created_at=datetime.now(tz=UTC),
            )
        )
        updated = await self._stores.agent_context_store.save_worker_profile(
            profile.model_copy(
                update={
                    "status": WorkerProfileStatus.ACTIVE,
                    "active_revision": next_revision,
                    "draft_revision": next_revision,
                    "updated_at": datetime.now(tz=UTC),
                    "archived_at": None,
                }
            )
        )
        await self._stores.conn.commit()
        return updated, revision, True

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
        cfg = load_config(self._project_root)
        telegram_cfg = getattr(getattr(cfg, "channels", None), "telegram", None) if cfg else None
        pending_pairings = (
            len(self._telegram_state_store.list_pending_pairings())
            if self._telegram_state_store is not None
            else 0
        )
        approved = (
            0
            if self._telegram_state_store is None
            else len(getattr(self._telegram_state_store, "list_approved_users", lambda: [])())
        )
        return {
            "telegram": {
                "enabled": bool(getattr(telegram_cfg, "enabled", False)),
                "mode": str(getattr(telegram_cfg, "mode", "")),
                "dm_policy": str(getattr(telegram_cfg, "dm_policy", "")),
                "group_policy": str(getattr(telegram_cfg, "group_policy", "")),
                "pending_pairings": pending_pairings,
                "approved_users": approved,
                "allowed_groups": list(getattr(telegram_cfg, "allowed_groups", []) or []),
            }
        }

    def _policy_catalog(
        self,
    ) -> list[tuple[str, str, PolicyProfile, str, list[str]]]:
        return [
            ("strict", "谨慎", STRICT_PROFILE, "warning", ["首次使用", "公网暴露", "高风险项目"]),
            ("default", "平衡", DEFAULT_PROFILE, "info", ["本地开发", "可信内网", "默认推荐"]),
            ("permissive", "自主", PERMISSIVE_PROFILE, "high", ["完全受信任环境", "高级用户"]),
        ]

    def _policy_profile_by_id(self, profile_id: str) -> PolicyProfile | None:
        catalog = {item_id: profile for item_id, _, profile, _, _ in self._policy_catalog()}
        return catalog.get(str(profile_id).strip().lower())

    def _resolve_effective_policy_profile(
        self,
        project: Any | None,
    ) -> tuple[str, PolicyProfile]:
        if project is not None:
            metadata = getattr(project, "metadata", {}) or {}
            stored_profile_id = str(metadata.get("policy_profile_id", "")).strip().lower()
            stored_profile = self._policy_profile_by_id(stored_profile_id)
            if stored_profile is not None:
                return stored_profile_id, stored_profile
        if self._policy_engine is not None:
            runtime_profile = self._policy_engine.profile
            runtime_profile_id = str(runtime_profile.name).strip().lower() or "default"
            mapped = self._policy_profile_by_id(runtime_profile_id)
            if mapped is not None:
                return runtime_profile_id, mapped
        return "default", DEFAULT_PROFILE

    async def _sync_policy_engine_for_project(self, project: Any | None) -> None:
        if self._policy_engine is None:
            return
        _, profile = self._resolve_effective_policy_profile(project)
        current_name = str(self._policy_engine.profile.name).strip().lower()
        if current_name == profile.name:
            return
        await self._ensure_policy_system_task()
        await self._policy_engine.update_profile(profile)

    @staticmethod
    def _describe_policy_approval(profile: PolicyProfile) -> str:
        if profile.reversible_action.value == "ask" and profile.irreversible_action.value == "ask":
            return "可逆 / 不可逆操作都需要确认"
        if profile.irreversible_action.value == "ask":
            return "仅不可逆操作需要确认"
        return "默认直接执行"

    @staticmethod
    def _tool_profile_allowed(required: str, allowed: str) -> bool:
        ranking = {"minimal": 0, "standard": 1, "privileged": 2}
        return ranking.get(required, 1) <= ranking.get(allowed, 1)

    async def _safe_secret_audit(self, project_ref: str | None):
        try:
            return await SecretService(
                self._project_root,
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

    @staticmethod
    def _workspace_summary_label(workspace: Any | None) -> str:
        if workspace is None:
            return "default workspace"
        return str(getattr(workspace, "name", "") or "default workspace")

    def _build_setup_review_summary(
        self,
        *,
        config: dict[str, Any],
        config_warnings: list[str],
        selected_project: Any | None,
        selected_workspace: Any | None,
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
                    recommended_action='先修正配置字段，再点击“检查配置”。',
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
                    recommended_action='先确认右侧主 Agent 名称和 Persona，再点击“保存配置”。',
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
                    recommended_action='先填写主 Agent 名称，再点击“检查配置”。',
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
                policy_profile.allowed_tool_profile.value,
            ):
                agent_autonomy_risks.append(
                    SetupRiskItem(
                        risk_id="agent_profile_exceeds_policy",
                        severity="warning",
                        title="主 Agent 工具级别高于当前安全等级",
                        summary=(
                            f"Agent 要求 {agent_tool_profile}，但当前安全等级只允许 "
                            f"{policy_profile.allowed_tool_profile.value}。"
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
                            '先完成 Secret 绑定后，再点击“检查配置”。'
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
        warnings = [item.summary for item in all_risks if item.severity != "info"]
        if any(item.severity == "high" for item in all_risks):
            risk_level = "high"
        elif all_risks:
            risk_level = "warning"
        else:
            risk_level = "info"
        next_actions: list[str] = []
        if any(item.blocking for item in secret_binding_risks):
            next_actions.append('先补齐 Secret 绑定，再点击“检查配置”。')
        if any(item.blocking for item in provider_runtime_risks):
            next_actions.append("先修正 Provider / model alias 配置，确保主 Agent 可调用模型。")
        if any(item.blocking for item in agent_autonomy_risks):
            next_actions.append('先确认右侧主 Agent 名称和 Persona，再点击“保存配置”。')
        if any(item.blocking for item in tool_skill_readiness_risks):
            next_actions.append("先处理 skills / MCP 缺失依赖，避免首用时能力不可用。")
        if not next_actions:
            if requires_real_model:
                next_actions.append('检查已通过，可以点击“保存配置”。')
            else:
                next_actions.append("当前是体验模式，可以先保存默认配置并直接开始使用。")
                next_actions.append("后续如需真实模型，再补齐 Provider 和 main alias。")
        return SetupReviewSummary(
            ready=not bool(blocking_reasons),
            risk_level=risk_level,
            warnings=warnings,
            blocking_reasons=blocking_reasons,
            next_actions=next_actions,
            provider_runtime_risks=provider_runtime_risks,
            channel_exposure_risks=channel_exposure_risks,
            agent_autonomy_risks=agent_autonomy_risks,
            tool_skill_readiness_risks=tool_skill_readiness_risks,
            secret_binding_risks=secret_binding_risks,
        )

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

    def _parse_memory_partition(self, raw: str | None) -> MemoryPartition | None:
        if not raw:
            return None
        try:
            return MemoryPartition(raw)
        except ValueError as exc:
            raise ControlPlaneActionError(
                "MEMORY_PARTITION_INVALID",
                f"非法 partition: {raw}",
            ) from exc

    def _parse_memory_layer(self, raw: str | None) -> MemoryLayer | None:
        if not raw:
            return None
        try:
            return MemoryLayer(raw)
        except ValueError as exc:
            raise ControlPlaneActionError(
                "MEMORY_LAYER_INVALID",
                f"非法 layer: {raw}",
            ) from exc

    def _param_str(
        self,
        params: Mapping[str, Any],
        key: str,
        *,
        default: str = "",
    ) -> str:
        value = params.get(key, default)
        return str(value or default).strip()

    def _param_bool(self, params: Mapping[str, Any], key: str) -> bool:
        value = params.get(key, False)
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    def _param_int(
        self,
        params: Mapping[str, Any],
        key: str,
        *,
        default: int,
    ) -> int:
        value = params.get(key, default)
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ControlPlaneActionError(
                f"{key.upper()}_INVALID",
                f"{key} 必须是整数",
            ) from exc

    def _param_list(self, params: Mapping[str, Any], key: str) -> list[str]:
        value = params.get(key)
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        value_str = str(value).strip()
        if not value_str:
            return []
        return [item.strip() for item in value_str.split(",") if item.strip()]

    def _coerce_split_objectives(self, value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        raw = str(value or "").strip()
        if not raw:
            return []
        return [item.strip() for item in raw.splitlines() if item.strip()]

    def _memory_target_refs(
        self,
        request: ActionRequestEnvelope,
    ) -> list[ControlPlaneTargetRef]:
        refs: list[ControlPlaneTargetRef] = []
        if project_id := self._param_str(request.params, "project_id"):
            refs.append(
                ControlPlaneTargetRef(
                    target_type="project",
                    target_id=project_id,
                )
            )
        if scope_id := self._param_str(request.params, "scope_id"):
            refs.append(
                ControlPlaneTargetRef(
                    target_type="scope",
                    target_id=scope_id,
                )
            )
        if subject_key := self._param_str(request.params, "subject_key"):
            refs.append(
                ControlPlaneTargetRef(
                    target_type="memory_subject",
                    target_id=subject_key,
                    label=subject_key,
                )
            )
        return refs

    def _map_control_event_type(self, event_type: ControlPlaneEventType) -> EventType:
        mapping = {
            ControlPlaneEventType.RESOURCE_PROJECTED: EventType.CONTROL_PLANE_RESOURCE_PROJECTED,
            ControlPlaneEventType.RESOURCE_REMOVED: EventType.CONTROL_PLANE_RESOURCE_REMOVED,
            ControlPlaneEventType.ACTION_REQUESTED: EventType.CONTROL_PLANE_ACTION_REQUESTED,
            ControlPlaneEventType.ACTION_COMPLETED: EventType.CONTROL_PLANE_ACTION_COMPLETED,
            ControlPlaneEventType.ACTION_REJECTED: EventType.CONTROL_PLANE_ACTION_REJECTED,
            ControlPlaneEventType.ACTION_DEFERRED: EventType.CONTROL_PLANE_ACTION_DEFERRED,
        }
        return mapping[event_type]

    def _resource_ref(self, resource_type: str, resource_id: str) -> ControlPlaneResourceRef:
        return ControlPlaneResourceRef(
            resource_type=resource_type,
            resource_id=resource_id,
            schema_version=1,
        )

    def _completed_result(
        self,
        *,
        request: ActionRequestEnvelope,
        code: str,
        message: str,
        data: dict[str, Any] | None = None,
        resource_refs: list[ControlPlaneResourceRef] | None = None,
        target_refs: list[ControlPlaneTargetRef] | None = None,
    ) -> ActionResultEnvelope:
        return ActionResultEnvelope(
            request_id=request.request_id,
            correlation_id=request.request_id,
            action_id=request.action_id,
            status=ControlPlaneActionStatus.COMPLETED,
            code=code,
            message=message,
            data=data or {},
            resource_refs=resource_refs or [],
            target_refs=target_refs or [],
        )

    def _deferred_result(
        self,
        *,
        request: ActionRequestEnvelope,
        code: str,
        message: str,
        correlation_id: str,
        data: dict[str, Any] | None = None,
        resource_refs: list[ControlPlaneResourceRef] | None = None,
        target_refs: list[ControlPlaneTargetRef] | None = None,
    ) -> ActionResultEnvelope:
        return ActionResultEnvelope(
            request_id=request.request_id,
            correlation_id=correlation_id,
            action_id=request.action_id,
            status=ControlPlaneActionStatus.DEFERRED,
            code=code,
            message=message,
            data=data or {},
            resource_refs=resource_refs or [],
            target_refs=target_refs or [],
        )

    def _rejected_result(
        self,
        *,
        request: ActionRequestEnvelope,
        code: str,
        message: str,
        target_refs: list[ControlPlaneTargetRef] | None = None,
    ) -> ActionResultEnvelope:
        return ActionResultEnvelope(
            request_id=request.request_id,
            correlation_id=request.request_id,
            action_id=request.action_id,
            status=ControlPlaneActionStatus.REJECTED,
            code=code,
            message=message,
            target_refs=target_refs or [],
        )

    def _map_operator_source(self, surface: ControlPlaneSurface) -> OperatorActionSource:
        mapping = {
            ControlPlaneSurface.WEB: OperatorActionSource.WEB,
            ControlPlaneSurface.TELEGRAM: OperatorActionSource.TELEGRAM,
            ControlPlaneSurface.CLI: OperatorActionSource.SYSTEM,
            ControlPlaneSurface.SYSTEM: OperatorActionSource.SYSTEM,
        }
        return mapping[surface]

    def _map_update_source(self, surface: ControlPlaneSurface) -> UpdateTriggerSource:
        mapping = {
            ControlPlaneSurface.WEB: UpdateTriggerSource.WEB,
            ControlPlaneSurface.TELEGRAM: UpdateTriggerSource.SYSTEM,
            ControlPlaneSurface.CLI: UpdateTriggerSource.CLI,
            ControlPlaneSurface.SYSTEM: UpdateTriggerSource.SYSTEM,
        }
        return mapping[surface]

    @staticmethod
    def _normalize_provider_id(value: str) -> str:
        lowered = value.strip().lower()
        if not lowered:
            return ""
        chars: list[str] = []
        previous_dash = False
        for char in lowered:
            if char.isascii() and char.isalnum():
                chars.append(char)
                previous_dash = False
                continue
            if previous_dash:
                continue
            chars.append("-")
            previous_dash = True
        return "".join(chars).strip("-")

    def _param_str(
        self,
        params: Mapping[str, Any],
        key: str,
        *,
        default: str = "",
    ) -> str:
        value = params.get(key, default)
        if value is None:
            return default
        return str(value).strip()

    def _param_bool(
        self,
        params: Mapping[str, Any],
        key: str,
        *,
        default: bool = False,
    ) -> bool:
        value = params.get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    def _param_int(
        self,
        params: Mapping[str, Any],
        key: str,
        *,
        default: int,
    ) -> int:
        value = params.get(key, default)
        if value in {None, ""}:
            return default
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ControlPlaneActionError(
                "PARAM_INT_INVALID",
                f"{key} 必须是整数",
            ) from exc

    def _param_list(self, params: Mapping[str, Any], key: str) -> list[str]:
        value = params.get(key)
        if value is None:
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        raise ControlPlaneActionError("PARAM_LIST_INVALID", f"{key} 必须是 string/list")

    def _parse_memory_partition(self, value: str | None) -> MemoryPartition | None:
        if not value:
            return None
        try:
            return MemoryPartition(value)
        except ValueError as exc:
            raise ControlPlaneActionError(
                "MEMORY_PARTITION_INVALID",
                f"不支持的 partition: {value}",
            ) from exc

    def _parse_memory_layer(self, value: str | None) -> MemoryLayer | None:
        if not value:
            return None
        try:
            return MemoryLayer(value)
        except ValueError as exc:
            raise ControlPlaneActionError(
                "MEMORY_LAYER_INVALID",
                f"不支持的 layer: {value}",
            ) from exc

    def _memory_target_refs(self, request: ActionRequestEnvelope) -> list[ControlPlaneTargetRef]:
        targets: list[ControlPlaneTargetRef] = []
        for key, target_type in (
            ("project_id", "project"),
            ("workspace_id", "workspace"),
            ("scope_id", "scope"),
            ("subject_key", "memory_subject"),
        ):
            value = self._param_str(request.params, key)
            if value:
                targets.append(
                    ControlPlaneTargetRef(
                        target_type=target_type,
                        target_id=value,
                        label=value,
                    )
                )
        return targets

    def _build_registry(self) -> ActionRegistryDocument:
        def definition(
            action_id: str,
            label: str,
            *,
            category: str,
            description: str = "",
            telegram_aliases: list[str] | None = None,
            params_schema: dict[str, Any] | None = None,
            risk_hint: str = "low",
            approval_hint: str = "none",
            telegram_supported: bool = False,
        ) -> ActionDefinition:
            support_status_by_surface = {
                "web": ControlPlaneSupportStatus.SUPPORTED,
                "telegram": (
                    ControlPlaneSupportStatus.SUPPORTED
                    if telegram_supported
                    else ControlPlaneSupportStatus.DEGRADED
                ),
            }
            aliases: dict[str, list[str]] = {"web": [action_id]}
            if telegram_aliases:
                aliases["telegram"] = telegram_aliases
            return ActionDefinition(
                action_id=action_id,
                label=label,
                description=description,
                category=category,
                supported_surfaces=[ControlPlaneSurface.WEB, ControlPlaneSurface.SYSTEM]
                + ([ControlPlaneSurface.TELEGRAM] if telegram_aliases else []),
                surface_aliases=aliases,
                support_status_by_surface=support_status_by_surface,
                params_schema=params_schema or {"type": "object"},
                result_schema={"type": "object"},
                risk_hint=risk_hint,
                approval_hint=approval_hint,
                idempotency_hint="request_id",
                resource_targets=[],
            )

        return ActionRegistryDocument(
            actions=[
                definition("wizard.refresh", "刷新 Wizard", category="wizard"),
                definition(
                    "wizard.restart", "重新开始 Wizard", category="wizard", risk_hint="medium"
                ),
                definition(
                    "project.select",
                    "切换项目",
                    category="projects",
                    telegram_aliases=["/project select"],
                    params_schema={"type": "object", "required": ["project_id"]},
                    telegram_supported=True,
                ),
                definition(
                    "setup.review",
                    "检查配置",
                    category="setup",
                    description="统一检查模型、渠道、主 Agent 和技能配置是否可以保存。",
                    params_schema={"type": "object"},
                    risk_hint="medium",
                ),
                definition(
                    "setup.apply",
                    "保存配置",
                    category="setup",
                    description="把当前主 Agent、模型和渠道设置一起保存。",
                    params_schema={"type": "object"},
                    risk_hint="medium",
                ),
                definition(
                    "setup.quick_connect",
                    "连接并启用真实模型",
                    category="setup",
                    description=(
                        "保存 Provider 配置、启动 LiteLLM Proxy，"
                        "并在托管实例上自动切到真实模型。"
                    ),
                    params_schema={"type": "object"},
                    risk_hint="medium",
                ),
                definition(
                    "skills.selection.save",
                    "保存技能默认范围",
                    category="setup",
                    description="保存当前 project 的 skills / MCP 默认启用范围。",
                    params_schema={"type": "object"},
                    risk_hint="medium",
                ),
                definition(
                    "mcp_provider.save",
                    "保存 MCP Provider",
                    category="capability",
                    description="安装或编辑一个 MCP provider。",
                    params_schema={"type": "object", "required": ["provider"]},
                    risk_hint="medium",
                ),
                definition(
                    "mcp_provider.delete",
                    "删除 MCP Provider",
                    category="capability",
                    description="删除一个 MCP provider。",
                    params_schema={"type": "object", "required": ["provider_id"]},
                    risk_hint="medium",
                ),
                definition(
                    "provider.oauth.openai_codex",
                    "连接 OpenAI Auth",
                    category="setup",
                    description=(
                        "通过浏览器 OAuth 连接 ChatGPT Pro / OpenAI Codex，"
                        "并写入本地凭证。"
                    ),
                    params_schema={"type": "object"},
                    risk_hint="medium",
                ),
                definition("memory.query", "刷新 Memory 总览", category="memory"),
                definition(
                    "memory.sor.edit",
                    "编辑记忆内容",
                    category="memory",
                    risk_hint="medium",
                    params_schema={
                        "type": "object",
                        "required": ["scope_id", "subject_key", "content", "expected_version"],
                    },
                ),
                definition(
                    "memory.sor.archive",
                    "归档记忆",
                    category="memory",
                    risk_hint="medium",
                    params_schema={
                        "type": "object",
                        "required": ["scope_id", "memory_id", "expected_version"],
                    },
                ),
                definition(
                    "memory.sor.restore",
                    "恢复已归档记忆",
                    category="memory",
                    params_schema={
                        "type": "object",
                        "required": ["scope_id", "memory_id"],
                    },
                ),
                definition("memory.browse", "浏览记忆目录", category="memory"),
                definition(
                    "memory.subject.inspect",
                    "查看 Subject 历史",
                    category="memory",
                    params_schema={"type": "object", "required": ["subject_key"]},
                ),
                definition(
                    "memory.proposal.inspect",
                    "查看 Proposal 审计",
                    category="memory",
                ),
                definition(
                    "memory.flush",
                    "执行 Memory Flush",
                    category="memory",
                    risk_hint="medium",
                ),
                definition(
                    "memory.reindex",
                    "执行 Memory Reindex",
                    category="memory",
                    risk_hint="medium",
                ),
                definition(
                    "memory.sync.resume",
                    "恢复 Memory Sync",
                    category="memory",
                    risk_hint="medium",
                ),
                definition(
                    "vault.access.request",
                    "申请 Vault 授权",
                    category="memory",
                    approval_hint="operator",
                    params_schema={"type": "object", "required": ["project_id"]},
                ),
                definition(
                    "vault.access.resolve",
                    "处理 Vault 授权",
                    category="memory",
                    risk_hint="high",
                    approval_hint="operator",
                    params_schema={"type": "object", "required": ["request_id", "decision"]},
                ),
                definition(
                    "vault.retrieve",
                    "检索 Vault 引用",
                    category="memory",
                    risk_hint="high",
                    approval_hint="operator",
                ),
                definition(
                    "memory.export.inspect",
                    "检查 Memory 导出范围",
                    category="memory",
                ),
                definition(
                    "memory.restore.verify",
                    "校验 Memory 恢复快照",
                    category="memory",
                    risk_hint="high",
                    approval_hint="operator",
                    params_schema={"type": "object", "required": ["snapshot_ref"]},
                ),
                definition(
                    "retrieval.index.start",
                    "开始 embedding 迁移",
                    category="memory",
                    risk_hint="medium",
                ),
                definition(
                    "retrieval.index.cancel",
                    "取消 embedding 迁移",
                    category="memory",
                    risk_hint="medium",
                    params_schema={"type": "object", "required": ["generation_id"]},
                ),
                definition(
                    "retrieval.index.cutover",
                    "切换到新 embedding 索引",
                    category="memory",
                    risk_hint="medium",
                    params_schema={"type": "object", "required": ["generation_id"]},
                ),
                definition(
                    "retrieval.index.rollback",
                    "回滚 embedding 索引",
                    category="memory",
                    risk_hint="medium",
                    approval_hint="operator",
                    params_schema={"type": "object", "required": ["generation_id"]},
                ),
                definition("capability.refresh", "刷新能力包", category="capability"),
                definition("work.refresh", "刷新委派视图", category="delegation"),
                definition("session.focus", "聚焦会话", category="sessions"),
                definition("session.unfocus", "取消聚焦会话", category="sessions"),
                definition("session.new", "开始新对话", category="sessions"),
                definition("session.create_with_project", "创建对话（含 Project）", category="sessions"),
                definition("session.reset", "重置会话 continuity", category="sessions"),
                definition("agent.list_available_models", "查询可用模型别名", category="agent_management"),
                definition("agent.list_worker_archetypes", "查询 Worker archetype", category="agent_management"),
                definition("agent.list_tool_profiles", "查询工具权限等级", category="agent_management"),
                definition("agent.create_worker_with_project", "创建 Worker + Project", category="agent_management"),
                definition("session.export", "导出会话", category="sessions"),
                definition(
                    "session.interrupt",
                    "中断任务",
                    category="sessions",
                    telegram_aliases=["/cancel"],
                    risk_hint="medium",
                    telegram_supported=True,
                ),
                definition("session.resume", "恢复任务", category="sessions"),
                definition(
                    "operator.approval.resolve",
                    "处理审批",
                    category="operator",
                    telegram_aliases=["/approve"],
                    approval_hint="policy",
                    telegram_supported=True,
                ),
                definition("operator.alert.ack", "确认告警", category="operator"),
                definition(
                    "operator.task.retry",
                    "重试任务",
                    category="operator",
                    telegram_aliases=["/retry"],
                    telegram_supported=True,
                ),
                definition(
                    "operator.task.cancel",
                    "取消任务",
                    category="operator",
                ),
                definition("channel.pairing.approve", "批准 Pairing", category="channels"),
                definition("channel.pairing.reject", "拒绝 Pairing", category="channels"),
                definition(
                    "agent_profile.save",
                    "保存主 Agent",
                    category="setup",
                    risk_hint="medium",
                    params_schema={"type": "object"},
                ),
                definition(
                    "policy_profile.select",
                    "切换安全等级",
                    category="setup",
                    params_schema={"type": "object", "required": ["profile_id"]},
                ),
                definition(
                    "worker_profile.create",
                    "新建 Root Agent",
                    category="root_agents",
                    risk_hint="medium",
                    params_schema={"type": "object"},
                ),
                definition(
                    "worker_profile.update",
                    "更新 Root Agent 草稿",
                    category="root_agents",
                    risk_hint="medium",
                    params_schema={"type": "object", "required": ["profile_id"]},
                ),
                definition(
                    "worker_profile.clone",
                    "复制 Root Agent",
                    category="root_agents",
                    risk_hint="medium",
                    params_schema={"type": "object", "required": ["source_profile_id"]},
                ),
                definition(
                    "worker_profile.archive",
                    "归档 Root Agent",
                    category="root_agents",
                    risk_hint="medium",
                    params_schema={"type": "object", "required": ["profile_id"]},
                ),
                definition(
                    "worker_profile.review",
                    "检查 Root Agent",
                    category="root_agents",
                    risk_hint="medium",
                    params_schema={"type": "object"},
                ),
                definition(
                    "worker_profile.apply",
                    "保存 Root Agent 草稿",
                    category="root_agents",
                    risk_hint="medium",
                    params_schema={"type": "object"},
                ),
                definition(
                    "worker_profile.publish",
                    "发布 Root Agent Revision",
                    category="root_agents",
                    risk_hint="high",
                    approval_hint="operator",
                    params_schema={"type": "object", "required": ["profile_id"]},
                ),
                definition(
                    "worker_profile.bind_default",
                    "设为聊天默认 Root Agent",
                    category="root_agents",
                    risk_hint="medium",
                    params_schema={"type": "object", "required": ["profile_id"]},
                ),
                definition(
                    "worker.spawn_from_profile",
                    "按 Root Agent 启动任务",
                    category="root_agents",
                    risk_hint="medium",
                    params_schema={"type": "object", "required": ["profile_id", "objective"]},
                ),
                definition(
                    "worker.extract_profile_from_runtime",
                    "从运行态提炼 Root Agent",
                    category="root_agents",
                    risk_hint="medium",
                    params_schema={"type": "object", "required": ["work_id"]},
                ),
                definition("config.apply", "保存配置", category="config", risk_hint="medium"),
                definition(
                    "backup.create",
                    "创建备份",
                    category="ops",
                    telegram_aliases=["/backup"],
                    risk_hint="medium",
                    telegram_supported=True,
                ),
                definition("restore.plan", "生成恢复计划", category="ops", risk_hint="medium"),
                definition(
                    "import.source.detect",
                    "识别导入源",
                    category="imports",
                    risk_hint="low",
                    params_schema={"type": "object", "required": ["source_type", "input_path"]},
                ),
                definition(
                    "import.mapping.save",
                    "保存导入 Mapping",
                    category="imports",
                    risk_hint="medium",
                    params_schema={"type": "object", "required": ["source_id"]},
                ),
                definition(
                    "import.preview",
                    "生成导入预览",
                    category="imports",
                    risk_hint="low",
                    params_schema={"type": "object", "required": ["source_id"]},
                ),
                definition(
                    "import.run",
                    "执行聊天导入",
                    category="imports",
                    risk_hint="medium",
                    params_schema={"type": "object"},
                ),
                definition(
                    "import.resume",
                    "恢复导入",
                    category="imports",
                    risk_hint="medium",
                    params_schema={"type": "object", "required": ["resume_id"]},
                ),
                definition(
                    "import.report.inspect",
                    "查看导入报告",
                    category="imports",
                    risk_hint="low",
                    params_schema={"type": "object", "required": ["run_id"]},
                ),
                definition(
                    "update.dry_run",
                    "升级 Dry Run",
                    category="ops",
                    telegram_aliases=["/update dry-run"],
                    risk_hint="medium",
                    telegram_supported=True,
                ),
                definition(
                    "update.apply",
                    "执行升级",
                    category="ops",
                    telegram_aliases=["/update apply"],
                    risk_hint="high",
                    approval_hint="operator",
                    telegram_supported=True,
                ),
                definition("runtime.restart", "重启 Runtime", category="ops", risk_hint="high"),
                definition("runtime.verify", "校验 Runtime", category="ops"),
                definition(
                    "automation.create", "创建自动化任务", category="automation", risk_hint="medium"
                ),
                definition(
                    "automation.run",
                    "立即运行自动化任务",
                    category="automation",
                    telegram_aliases=["/automation run"],
                ),
                definition("automation.pause", "暂停自动化任务", category="automation"),
                definition("automation.resume", "恢复自动化任务", category="automation"),
                definition(
                    "automation.delete", "删除自动化任务", category="automation", risk_hint="medium"
                ),
                definition(
                    "work.cancel",
                    "取消 Work",
                    category="delegation",
                    telegram_aliases=["/work cancel"],
                    risk_hint="medium",
                    telegram_supported=True,
                ),
                definition(
                    "work.retry",
                    "重试 Work",
                    category="delegation",
                    telegram_aliases=["/work retry"],
                    telegram_supported=True,
                ),
                definition(
                    "worker.review",
                    "评审 Worker 方案",
                    category="delegation",
                    risk_hint="medium",
                    params_schema={"type": "object", "required": ["work_id"]},
                ),
                definition(
                    "worker.apply",
                    "应用 Worker 方案",
                    category="delegation",
                    risk_hint="high",
                    approval_hint="operator",
                    params_schema={"type": "object", "required": ["work_id", "plan"]},
                ),
                definition(
                    "work.split",
                    "拆分 Work",
                    category="delegation",
                    risk_hint="medium",
                    params_schema={"type": "object", "required": ["work_id", "objectives"]},
                ),
                definition(
                    "work.merge",
                    "合并 Work",
                    category="delegation",
                    risk_hint="medium",
                    params_schema={"type": "object", "required": ["work_id"]},
                ),
                definition(
                    "work.delete",
                    "删除 Work",
                    category="delegation",
                    telegram_aliases=["/work delete"],
                    risk_hint="medium",
                    telegram_supported=True,
                ),
                definition(
                    "work.escalate",
                    "升级 Work",
                    category="delegation",
                    telegram_aliases=["/work escalate"],
                    risk_hint="medium",
                    telegram_supported=True,
                ),
                definition(
                    "pipeline.resume",
                    "恢复 Pipeline",
                    category="pipeline",
                    telegram_aliases=["/pipeline resume"],
                    telegram_supported=True,
                ),
                definition(
                    "pipeline.retry_node",
                    "重试节点",
                    category="pipeline",
                    telegram_aliases=["/pipeline retry"],
                    telegram_supported=True,
                ),
                definition(
                    "diagnostics.refresh",
                    "刷新诊断",
                    category="diagnostics",
                    telegram_aliases=["/status"],
                    telegram_supported=True,
                ),
            ],
            capabilities=[
                ControlPlaneCapability(
                    capability_id="control.actions",
                    label="统一动作注册表",
                    action_id="",
                )
            ],
        )

    def _has_telegram_alias(self, action_id: str, alias: str) -> bool:
        definition = self.get_action_definition(action_id)
        if definition is None:
            return False
        aliases = definition.surface_aliases.get("telegram", [])
        return alias in aliases
