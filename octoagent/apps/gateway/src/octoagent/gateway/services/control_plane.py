"""Feature 026: Control Plane canonical producer。"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from octoagent.core.models import (
    ActionDefinition,
    ActionRegistryDocument,
    ActionRequestEnvelope,
    ActionResultEnvelope,
    ActorType,
    AgentProfile,
    AgentProfileItem,
    AgentProfileScope,
    AgentProfilesDocument,
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
    DelegationPlaneDocument,
    DiagnosticsFailureSummary,
    DiagnosticsSubsystemStatus,
    DiagnosticsSummaryDocument,
    Event,
    EventCausality,
    EventType,
    MemoryConsoleDocument,
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
    ProjectBindingType,
    ProjectOption,
    ProjectSelectorDocument,
    ProjectSelectorState,
    SessionProjectionDocument,
    SessionProjectionItem,
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
    WorkProjectionItem,
    WorkspaceOption,
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
from octoagent.provider.dx.onboarding_service import OnboardingService
from octoagent.provider.dx.secret_service import SecretService
from ulid import ULID

from .agent_context import build_scope_aware_session_id
from .task_service import TaskService

_AUDIT_TASK_ID = "ops-control-plane"
_AUDIT_TRACE_ID = "trace-ops-control-plane"
_POLICY_TASK_ID = "system"
_POLICY_TRACE_ID = "trace-policy-engine"
_TERMINAL_WORK_STATUSES = {"succeeded", "failed", "cancelled", "merged", "timed_out", "deleted"}


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
        wizard = await self.get_wizard_session()
        config = await self.get_config_schema()
        project_selector = await self.get_project_selector()
        sessions = await self.get_session_projection()
        agent_profiles = await self.get_agent_profiles_document()
        owner_profile = await self.get_owner_profile_document()
        bootstrap_session = await self.get_bootstrap_session_document()
        context_continuity = await self.get_context_continuity_document()
        policy_profiles = await self.get_policy_profiles_document()
        capability_pack = await self.get_capability_pack_document()
        skill_governance = await self.get_skill_governance_document()
        setup_governance = await self.get_setup_governance_document()
        delegation = await self.get_delegation_document()
        pipelines = await self.get_skill_pipeline_document()
        automation = await self.get_automation_document()
        diagnostics = await self.get_diagnostics_summary()
        memory = await self.get_memory_console()
        imports = await self.get_import_workbench()
        registry = self.get_action_registry()
        return {
            "contract_version": registry.contract_version,
            "resources": {
                "wizard": wizard.model_dump(mode="json", by_alias=True),
                "config": config.model_dump(mode="json", by_alias=True),
                "project_selector": project_selector.model_dump(mode="json", by_alias=True),
                "sessions": sessions.model_dump(mode="json", by_alias=True),
                "agent_profiles": agent_profiles.model_dump(mode="json", by_alias=True),
                "owner_profile": owner_profile.model_dump(mode="json", by_alias=True),
                "bootstrap_session": bootstrap_session.model_dump(mode="json", by_alias=True),
                "context_continuity": context_continuity.model_dump(mode="json", by_alias=True),
                "policy_profiles": policy_profiles.model_dump(mode="json", by_alias=True),
                "capability_pack": capability_pack.model_dump(mode="json", by_alias=True),
                "skill_governance": skill_governance.model_dump(mode="json", by_alias=True),
                "setup_governance": setup_governance.model_dump(mode="json", by_alias=True),
                "delegation": delegation.model_dump(mode="json", by_alias=True),
                "pipelines": pipelines.model_dump(mode="json", by_alias=True),
                "automation": automation.model_dump(mode="json", by_alias=True),
                "diagnostics": diagnostics.model_dump(mode="json", by_alias=True),
                "memory": memory.model_dump(mode="json", by_alias=True),
                "imports": imports.model_dump(mode="json", by_alias=True),
            },
            "registry": registry.model_dump(mode="json", by_alias=True),
            "generated_at": datetime.now(tz=UTC).isoformat(),
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
        session_items = await self._build_session_projection_items(
            selected_project=selected_project,
            selected_workspace=selected_workspace,
        )
        focused_session_id, focused_thread_id = self._resolve_projected_focus(
            state=state,
            session_items=session_items,
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
            sessions=session_items,
            operator_summary=operator_summary,
            operator_items=operator_items,
            capabilities=[
                ControlPlaneCapability(
                    capability_id="session.focus",
                    label="聚焦会话",
                    action_id="session.focus",
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
        *,
        selected_project,
        selected_workspace,
    ) -> list[SessionProjectionItem]:
        tasks = await self._stores.task_store.list_tasks()
        session_states = await self._stores.agent_context_store.list_session_contexts(
            project_id=selected_project.project_id if selected_project is not None else None,
            workspace_id=(
                selected_workspace.workspace_id if selected_workspace is not None else None
            ),
        )
        session_state_by_id = {item.session_id: item for item in session_states}
        grouped: dict[str, list[tuple[Task, Any]]] = defaultdict(list)
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
            session_id = build_scope_aware_session_id(
                task,
                project_id=workspace.project_id,
                workspace_id=workspace.workspace_id,
            )
            grouped[session_id].append((task, workspace))

        session_items: list[SessionProjectionItem] = []
        for session_id, entries in grouped.items():
            latest, workspace = max(entries, key=lambda item: item[0].updated_at)
            session_state = session_state_by_id.get(session_id)
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
                    runtime_kind=str(
                        execution_summary.get(
                            "runtime_kind",
                            latest_metadata.get("target_kind", ""),
                        )
                    ),
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
        session_items.sort(
            key=lambda item: item.latest_event_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )
        return session_items

    def _resolve_projected_focus(
        self,
        *,
        state: ControlPlaneState,
        session_items: list[SessionProjectionItem],
    ) -> tuple[str, str]:
        if not session_items:
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
            if (
                build_scope_aware_session_id(
                    task,
                    project_id=workspace.project_id,
                    workspace_id=workspace.workspace_id,
                )
                == session_id
            ):
                matched.append(task)
        matched.sort(key=lambda item: item.created_at)
        return matched

    async def get_agent_profiles_document(self) -> AgentProfilesDocument:
        _, selected_project, selected_workspace, _ = await self._resolve_selection()
        profiles = await self._stores.agent_context_store.list_agent_profiles(
            project_id=selected_project.project_id if selected_project is not None else None
        )
        items = [
            AgentProfileItem(
                profile_id=profile.profile_id,
                scope=profile.scope.value,
                project_id=profile.project_id,
                name=profile.name,
                persona_summary=profile.persona_summary,
                model_alias=profile.model_alias,
                tool_profile=profile.tool_profile,
                updated_at=profile.updated_at,
            )
            for profile in profiles
            if self._matches_selected_scope(
                item_project_id=profile.project_id or None,
                item_workspace_id=None,
                selected_project=selected_project,
                selected_workspace=selected_workspace,
            )
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
        sessions = await self._stores.agent_context_store.list_session_contexts(
            project_id=selected_project.project_id if selected_project is not None else None,
            workspace_id=(
                selected_workspace.workspace_id if selected_workspace is not None else None
            ),
        )
        frames = await self._stores.agent_context_store.list_context_frames(
            project_id=selected_project.project_id if selected_project is not None else None,
            workspace_id=(
                selected_workspace.workspace_id if selected_workspace is not None else None
            ),
            limit=20,
        )
        session_items = [
            ContextSessionItem(
                session_id=item.session_id,
                thread_id=item.thread_id,
                project_id=item.project_id,
                workspace_id=item.workspace_id,
                rolling_summary=item.rolling_summary,
                last_context_frame_id=item.last_context_frame_id,
                updated_at=item.updated_at,
            )
            for item in sessions
        ]
        frame_items = [
            ContextFrameItem(
                context_frame_id=item.context_frame_id,
                task_id=item.task_id,
                session_id=item.session_id,
                project_id=item.project_id,
                workspace_id=item.workspace_id,
                agent_profile_id=item.agent_profile_id,
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
        return ContextContinuityDocument(
            active_project_id=selected_project.project_id if selected_project is not None else "",
            active_workspace_id=(
                selected_workspace.workspace_id if selected_workspace is not None else ""
            ),
            sessions=session_items,
            frames=frame_items,
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

    async def get_skill_governance_document(
        self,
        *,
        config_value: dict[str, Any] | None = None,
        policy_profile_id: str | None = None,
        selected_project: Any | None = None,
        selected_workspace: Any | None = None,
    ) -> SkillGovernanceDocument:
        if selected_project is None and selected_workspace is None:
            _, selected_project, selected_workspace, _ = await self._resolve_selection()
        elif selected_project is None:
            _, selected_project, _, _ = await self._resolve_selection()
        if policy_profile_id:
            effective_policy = self._policy_profile_by_id(policy_profile_id) or DEFAULT_PROFILE
        else:
            _, effective_policy = self._resolve_effective_policy_profile(selected_project)
        capability_pack = await self.get_capability_pack_document()
        capability_snapshot = (
            self._capability_pack_service.capability_snapshot()
            if self._capability_pack_service is not None
            else {}
        )
        items: list[SkillGovernanceItem] = []
        if config_value is None:
            config_value = (await self.get_config_schema()).current_value
        model_aliases_raw = (
            config_value.get("model_aliases", {}) if isinstance(config_value, dict) else {}
        )
        model_aliases = (
            set(model_aliases_raw.keys()) if isinstance(model_aliases_raw, dict) else set()
        )
        for skill in capability_pack.pack.skills:
            required_profile = str(skill.metadata.get("tool_profile", "standard"))
            missing_requirements: list[str] = []
            availability = "available"
            blocking = False
            if skill.model_alias not in model_aliases:
                availability = "degraded"
                blocking = True
                missing_requirements.append(f"缺少 model alias: {skill.model_alias}")
            if not self._tool_profile_allowed(
                required_profile,
                effective_policy.allowed_tool_profile.value,
            ):
                availability = "policy_blocked"
                missing_requirements.append(
                    f"当前安全等级只允许 {effective_policy.allowed_tool_profile.value} 工具。"
                )
            items.append(
                SkillGovernanceItem(
                    item_id=f"skill:{skill.skill_id}",
                    label=skill.label or skill.skill_id,
                    source_kind="builtin",
                    scope="project",
                    enabled_by_default=True,
                    availability=availability,
                    trust_level="trusted",
                    blocking=blocking,
                    missing_requirements=missing_requirements,
                    details={
                        "skill_id": skill.skill_id,
                        "model_alias": skill.model_alias,
                        "tools_allowed": list(skill.tools_allowed),
                        "required_tool_profile": required_profile,
                        "worker_types": [item.value for item in skill.worker_types],
                    },
                )
            )

        mcp_tools: dict[str, list[Any]] = defaultdict(list)
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
            items.append(
                SkillGovernanceItem(
                    item_id=f"mcp:{server_name}",
                    label=f"MCP / {server_name}",
                    source_kind="mcp",
                    scope="project",
                    enabled_by_default=False,
                    availability=availability,
                    trust_level="external",
                    missing_requirements=missing_requirements,
                    install_hint=install_hints[0] if install_hints else "",
                    details={
                        "server_name": server_name,
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
        blocked_items = len([item for item in items if item.blocking])
        warnings = [] if items else ["当前没有可治理的 skills / MCP readiness 条目。"]
        return SkillGovernanceDocument(
            active_project_id=selected_project.project_id if selected_project is not None else "",
            active_workspace_id=(
                selected_workspace.workspace_id if selected_workspace is not None else ""
            ),
            items=items,
            summary={
                "item_count": len(items),
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
                f"runtime={config.current_value.get('runtime', {}).get('llm_mode', '')}"
            ),
            warnings=list(config.warnings),
            blocking_reasons=[
                item.risk_id for item in review.provider_runtime_risks if item.blocking
            ],
            details={
                "enabled_provider_ids": [
                    item.get("id", "")
                    for item in config.current_value.get("providers", [])
                    if item.get("enabled", True)
                ],
                "model_aliases": sorted(config.current_value.get("model_aliases", {}).keys()),
                "litellm_sync_ok": not config.degraded.is_degraded,
                "bridge_ref_count": len(config.bridge_refs),
                "secret_audit_status": secret_audit.overall_status if secret_audit else "unknown",
            },
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
                    label="审查 Setup 风险",
                    action_id="setup.review",
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
        _, selected_project, selected_workspace, _ = await self._resolve_selection()
        jobs = self._automation_store.list_jobs()
        items: list[AutomationJobItem] = []
        for job in jobs:
            if not self._matches_selected_scope(
                item_project_id=job.project_id,
                item_workspace_id=job.workspace_id,
                selected_project=selected_project,
                selected_workspace=selected_workspace,
            ):
                continue
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
        state = self._state_store.load()
        if self._capability_pack_service is None:
            return CapabilityPackDocument(
                selected_project_id=state.selected_project_id,
                selected_workspace_id=state.selected_workspace_id,
                degraded=ControlPlaneDegradedState(
                    is_degraded=True,
                    reasons=["capability_pack_unavailable"],
                ),
                warnings=["capability pack service unavailable"],
            )
        pack = await self._capability_pack_service.get_pack()
        return CapabilityPackDocument(
            pack=pack,
            selected_project_id=state.selected_project_id,
            selected_workspace_id=state.selected_workspace_id,
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
        _, selected_project, selected_workspace, _ = await self._resolve_selection()
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
        items = [
            WorkProjectionItem(
                work_id=work.work_id,
                task_id=work.task_id,
                parent_work_id=work.parent_work_id or "",
                title=work.title,
                status=work.status.value,
                target_kind=work.target_kind.value,
                selected_worker_type=work.selected_worker_type.value,
                route_reason=work.route_reason,
                owner_id=work.owner_id,
                selected_tools=work.selected_tools,
                pipeline_run_id=work.pipeline_run_id,
                runtime_id=work.runtime_id,
                project_id=work.project_id,
                workspace_id=work.workspace_id,
                child_work_ids=child_map.get(work.work_id, []),
                child_work_count=len(child_map.get(work.work_id, [])),
                merge_ready=self._is_work_merge_ready(work, works),
                runtime_summary={
                    "requested_target_kind": str(work.metadata.get("requested_target_kind", "")),
                    "requested_worker_type": str(work.metadata.get("requested_worker_type", "")),
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
                ],
            )
            for work in works
            if self._matches_selected_scope(
                item_project_id=work.project_id,
                item_workspace_id=work.workspace_id,
                selected_project=selected_project,
                selected_workspace=selected_workspace,
            )
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

    async def get_skill_pipeline_document(self) -> SkillPipelineDocument:
        _, selected_project, selected_workspace, _ = await self._resolve_selection()
        if self._delegation_plane_service is None:
            return SkillPipelineDocument(
                degraded=ControlPlaneDegradedState(
                    is_degraded=True,
                    reasons=["delegation_plane_unavailable"],
                ),
                warnings=["skill pipeline unavailable"],
            )
        runs = await self._delegation_plane_service.list_pipeline_runs()
        items: list[PipelineRunItem] = []
        for run in runs:
            work = await self._stores.work_store.get_work(run.work_id)
            if work is None:
                continue
            if not self._matches_selected_scope(
                item_project_id=work.project_id,
                item_workspace_id=work.workspace_id,
                selected_project=selected_project,
                selected_workspace=selected_workspace,
            ):
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
        if action_id == "memory.bridge.reconnect":
            return await self._handle_memory_maintenance(
                request,
                kind=MemoryMaintenanceCommandKind.BRIDGE_RECONNECT,
                success_code="MEMORY_BRIDGE_RECONNECT_COMPLETED",
                success_message="已执行 Memory bridge reconnect。",
            )
        if action_id == "memory.sync.resume":
            return await self._handle_memory_maintenance(
                request,
                kind=MemoryMaintenanceCommandKind.SYNC_RESUME,
                success_code="MEMORY_SYNC_RESUME_COMPLETED",
                success_message="已执行 Memory sync.resume。",
            )
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
        if action_id == "session.export":
            return await self._handle_session_export(request)
        if action_id == "session.interrupt":
            return await self._handle_session_interrupt(request)
        if action_id == "session.resume":
            return await self._handle_session_resume(request)
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
        if action_id == "policy_profile.select":
            return await self._handle_policy_profile_select(request)
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
        except Exception as exc:
            candidate_config = current_config
            validation_errors.append(str(exc))

        _, selected_project, selected_workspace, _ = await self._resolve_selection()
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
            message="Setup review 已生成。",
            data={"review": review.model_dump(mode="json")},
            resource_refs=[self._resource_ref("setup_governance", "setup:governance")],
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
        state = self._state_store.load().model_copy(
            update={
                "focused_session_id": session.session_id,
                "focused_thread_id": session.thread_id,
                "updated_at": datetime.now(tz=UTC),
            }
        )
        self._state_store.save(state)
        return self._completed_result(
            request=request,
            code="SESSION_FOCUSED",
            message="已更新当前聚焦会话",
            data={
                "session_id": session.session_id,
                "thread_id": session.thread_id,
            },
            resource_refs=[self._resource_ref("session_projection", "sessions:overview")],
            target_refs=[
                ControlPlaneTargetRef(target_type="session", target_id=session.session_id),
                ControlPlaneTargetRef(target_type="thread", target_id=session.thread_id),
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
    ) -> SessionProjectionItem:
        requested_session_id = self._param_str(request.params, "session_id")
        requested_thread_id = self._param_str(request.params, "thread_id")
        if not requested_session_id and not requested_thread_id:
            raise ControlPlaneActionError(
                "SESSION_ID_REQUIRED",
                "session_id 或 thread_id 至少需要一个",
            )

        _, selected_project, selected_workspace, _ = await self._resolve_selection()
        session_items = await self._build_session_projection_items(
            selected_project=selected_project,
            selected_workspace=selected_workspace,
        )
        if requested_session_id:
            session = next(
                (item for item in session_items if item.session_id == requested_session_id),
                None,
            )
            if session is None:
                raise ControlPlaneActionError(
                    "SESSION_NOT_FOUND",
                    "当前作用域找不到对应的 session_id",
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
        child_tasks: list[dict[str, Any]] = []
        for objective in parsed_objectives:
            message = NormalizedMessage(
                channel=parent_task.requester.channel,
                thread_id=f"{parent_task.thread_id}:child:{str(ULID())[:8]}",
                scope_id=parent_task.scope_id,
                sender_id=parent_task.requester.sender_id,
                sender_name=parent_task.requester.sender_id or "owner",
                text=objective,
                metadata={
                    "parent_task_id": parent_task.task_id,
                    "parent_work_id": parent_work.work_id,
                    "requested_worker_type": worker_type,
                    "target_kind": target_kind,
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
        instruction_overlays = raw.get("instruction_overlays", [])
        if not isinstance(instruction_overlays, list):
            instruction_overlays = []
        policy_refs = raw.get("policy_refs", [])
        if not isinstance(policy_refs, list):
            policy_refs = []
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
        saved = await self._stores.agent_context_store.save_agent_profile(
            AgentProfile(
                profile_id=profile.profile_id,
                scope=AgentProfileScope(profile.scope),
                project_id=profile.project_id,
                name=profile.name,
                persona_summary=profile.persona_summary,
                instruction_overlays=[str(item) for item in instruction_overlays],
                model_alias=profile.model_alias,
                tool_profile=profile.tool_profile,
                policy_refs=[str(item) for item in policy_refs],
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
            message="主 Agent profile 已保存。",
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
        document = await self._import_workbench_service.get_source(source_id)
        _, selected_project, selected_workspace, _ = await self._resolve_selection()
        if not self._matches_selected_scope(
            item_project_id=document.active_project_id,
            item_workspace_id=document.active_workspace_id,
            selected_project=selected_project,
            selected_workspace=selected_workspace,
        ):
            raise ImportWorkbenchError(
                "IMPORT_SOURCE_NOT_ALLOWED",
                "导入源不属于当前选中的 project/workspace。",
            )
        return document

    async def _get_import_run_in_scope(self, run_id: str):
        document = await self._import_workbench_service.get_run(run_id)
        _, selected_project, selected_workspace, _ = await self._resolve_selection()
        if not self._matches_selected_scope(
            item_project_id=document.active_project_id,
            item_workspace_id=document.active_workspace_id,
            selected_project=selected_project,
            selected_workspace=selected_workspace,
        ):
            raise ImportWorkbenchError(
                "IMPORT_REPORT_NOT_ALLOWED",
                "导入运行不属于当前选中的 project/workspace。",
            )
        return document

    async def _get_automation_job_in_scope(self, job_id: str) -> AutomationJob:
        job = self._automation_store.get_job(job_id)
        if job is None:
            raise ControlPlaneActionError("JOB_NOT_FOUND", "automation job 不存在")
        _, selected_project, selected_workspace, _ = await self._resolve_selection()
        if not self._matches_selected_scope(
            item_project_id=job.project_id,
            item_workspace_id=job.workspace_id,
            selected_project=selected_project,
            selected_workspace=selected_workspace,
        ):
            raise ControlPlaneActionError(
                "PROJECT_SCOPE_NOT_ALLOWED",
                "automation job 不属于当前选中的 project/workspace。",
            )
        return job

    async def _get_work_in_scope(self, work_id: str):
        work = await self._stores.work_store.get_work(work_id)
        if work is None:
            raise ControlPlaneActionError("WORK_NOT_FOUND", "work 不存在")
        _, selected_project, selected_workspace, _ = await self._resolve_selection()
        if not self._matches_selected_scope(
            item_project_id=work.project_id,
            item_workspace_id=work.workspace_id,
            selected_project=selected_project,
            selected_workspace=selected_workspace,
        ):
            raise ControlPlaneActionError(
                "PROJECT_SCOPE_NOT_ALLOWED",
                "work 不属于当前选中的 project/workspace。",
            )
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
                label="LiteLLM Proxy URL",
                placeholder="http://localhost:4000",
                order=20,
            ),
            "runtime.master_key_env": ConfigFieldHint(
                field_path="runtime.master_key_env",
                section="runtime",
                label="Master Key 环境变量",
                widget="env-ref",
                sensitive=True,
                order=30,
            ),
            "providers": ConfigFieldHint(
                field_path="providers",
                section="providers",
                label="Providers",
                description="Provider 列表",
                widget="provider-list",
                order=40,
            ),
            "model_aliases": ConfigFieldHint(
                field_path="model_aliases",
                section="models",
                label="Model Aliases",
                widget="alias-map",
                order=50,
            ),
            "front_door.mode": ConfigFieldHint(
                field_path="front_door.mode",
                section="security",
                label="对外访问模式",
                description="控制谁可以访问 owner-facing API。",
                widget="select",
                help_text="小白默认建议 loopback；公网场景建议 bearer 或 trusted_proxy。",
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
                help_text="建议先完成 Provider / Secret 配置，再启用 Telegram。",
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
                help_text="仅 webhook 模式需要；没有公网 HTTPS 时优先用 polling。",
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
                help_text="默认推荐 pairing；open 会允许陌生人直接触发主 Agent。",
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
                help_text="默认推荐 allowlist，避免 Agent 在任意群聊被触发。",
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

    async def _extract_latest_user_metadata(self, task_id: str) -> dict[str, str]:
        events = await self._stores.event_store.get_events_for_task(task_id)
        for event in reversed(events):
            if event.type != EventType.USER_MESSAGE:
                continue
            raw = event.payload.get("metadata", {})
            if not isinstance(raw, dict):
                return {}
            return {str(key): str(value) for key, value in raw.items()}
        return {}

    @staticmethod
    def _is_work_merge_ready(work, works: list[Any]) -> bool:
        children = [item for item in works if item.parent_work_id == work.work_id]
        if not children:
            return False
        return all(item.status.value in _TERMINAL_WORK_STATUSES for item in children)

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
                    recommended_action="先修正配置字段，再重新执行 setup.review。",
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
                    severity="high",
                    title="还没有可用 Provider",
                    summary="当前没有任何启用中的 provider，主 Agent 不能调用真实模型。",
                    blocking=True,
                    recommended_action="至少配置 1 个 provider，并补齐对应 secret 引用。",
                    source_ref=config_ref,
                )
            )
        if "main" not in model_aliases:
            provider_runtime_risks.append(
                SetupRiskItem(
                    risk_id="main_alias_missing",
                    severity="high",
                    title="缺少 main 模型别名",
                    summary="主 Agent 依赖 main alias，当前 setup 还没有可用的默认模型。",
                    blocking=True,
                    recommended_action="先为 main alias 指定 provider 和模型。",
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
                    title="主 Agent profile 尚未配置",
                    summary="当前 project 还没有清晰的主 Agent persona / model / tool profile。",
                    blocking=True,
                    recommended_action="先保存一个 project-scope 的主 Agent profile。",
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
            if item.availability == "available":
                continue
            tool_skill_readiness_risks.append(
                SetupRiskItem(
                    risk_id=f"{item.item_id}:not_ready",
                    severity="high" if item.blocking else "warning",
                    title=f"{item.label} 尚未就绪",
                    summary="；".join(item.missing_requirements) or f"状态={item.availability}",
                    blocking=item.blocking,
                    recommended_action=item.install_hint or "先处理缺失依赖后再启用该能力。",
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
                            "先完成 secret configure/apply，再重新执行 setup.review。"
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
                        recommended_action="完成 reload 或重启后再做 doctor / setup.apply。",
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
            next_actions.append("先补齐 Secret 绑定，再重新执行 setup.review。")
        if any(item.blocking for item in provider_runtime_risks):
            next_actions.append("先修正 Provider / model alias 配置，确保主 Agent 可调用模型。")
        if any(item.blocking for item in agent_autonomy_risks):
            next_actions.append("先保存主 Agent profile，再继续 apply。")
        if any(item.blocking for item in tool_skill_readiness_risks):
            next_actions.append("先处理 skills / MCP 缺失依赖，避免首用时能力不可用。")
        if not next_actions:
            next_actions.append("当前 setup review 已通过，可以继续执行 setup.apply。")
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

    def _param_bool(self, params: Mapping[str, Any], key: str) -> bool:
        value = params.get(key, False)
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
                    "审查 Setup 风险",
                    category="setup",
                    description="聚合 Provider / Channel / Agent / Skills 的风险和阻塞项。",
                    params_schema={"type": "object"},
                    risk_hint="medium",
                ),
                definition("memory.query", "刷新 Memory 总览", category="memory"),
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
                    "memory.bridge.reconnect",
                    "重连 Memory Bridge",
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
                definition("capability.refresh", "刷新能力包", category="capability"),
                definition("work.refresh", "刷新委派视图", category="delegation"),
                definition("session.focus", "聚焦会话", category="sessions"),
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
