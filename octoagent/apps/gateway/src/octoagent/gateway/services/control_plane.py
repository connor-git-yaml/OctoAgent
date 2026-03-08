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
    AutomationJob,
    AutomationJobDocument,
    AutomationJobItem,
    AutomationJobRun,
    AutomationJobStatus,
    AutomationScheduleKind,
    ConfigFieldHint,
    ConfigSchemaDocument,
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
    DiagnosticsFailureSummary,
    DiagnosticsSubsystemStatus,
    DiagnosticsSummaryDocument,
    Event,
    EventCausality,
    EventType,
    MemoryConsoleDocument,
    MemoryProposalAuditDocument,
    MemorySubjectHistoryDocument,
    OperatorActionKind,
    OperatorActionRequest,
    OperatorActionSource,
    ProjectBindingType,
    ProjectOption,
    ProjectSelectorDocument,
    SessionProjectionDocument,
    SessionProjectionItem,
    Task,
    TaskPointers,
    TaskStatus,
    UpdateTriggerSource,
    VaultAuthorizationDocument,
    WizardSessionDocument,
    WizardStepDocument,
    WorkspaceOption,
)
from octoagent.core.models.payloads import ControlPlaneAuditPayload
from octoagent.core.models.task import RequesterInfo
from octoagent.memory import MemoryLayer, MemoryPartition, ProposalStatus, VaultAccessDecision
from octoagent.provider.dx.automation_store import AutomationStore
from octoagent.provider.dx.backup_service import BackupService
from octoagent.provider.dx.chat_import_service import ChatImportService
from octoagent.provider.dx.config_schema import OctoAgentConfig
from octoagent.provider.dx.config_wizard import load_config, save_config
from octoagent.provider.dx.control_plane_state import ControlPlaneStateStore
from octoagent.provider.dx.litellm_generator import (
    check_litellm_sync_status,
    generate_litellm_config,
)
from octoagent.provider.dx.memory_console_service import (
    MemoryConsoleError,
    MemoryConsoleService,
)
from octoagent.provider.dx.onboarding_service import OnboardingService
from ulid import ULID

from .task_service import TaskService

_AUDIT_TASK_ID = "ops-control-plane"
_AUDIT_TRACE_ID = "trace-ops-control-plane"


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

    async def get_snapshot(self) -> dict[str, Any]:
        wizard = await self.get_wizard_session()
        config = await self.get_config_schema()
        project_selector = await self.get_project_selector()
        sessions = await self.get_session_projection()
        automation = await self.get_automation_document()
        diagnostics = await self.get_diagnostics_summary()
        memory = await self.get_memory_console()
        registry = self.get_action_registry()
        return {
            "contract_version": registry.contract_version,
            "resources": {
                "wizard": wizard.model_dump(mode="json", by_alias=True),
                "config": config.model_dump(mode="json", by_alias=True),
                "project_selector": project_selector.model_dump(
                    mode="json", by_alias=True
                ),
                "sessions": sessions.model_dump(mode="json", by_alias=True),
                "automation": automation.model_dump(mode="json", by_alias=True),
                "diagnostics": diagnostics.model_dump(mode="json", by_alias=True),
                "memory": memory.model_dump(mode="json", by_alias=True),
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
        tasks = await self._stores.task_store.list_tasks()
        grouped: dict[str, list[Task]] = defaultdict(list)
        for task in tasks:
            if task.task_id == _AUDIT_TASK_ID:
                continue
            grouped[task.thread_id].append(task)

        state = self._state_store.load()
        session_items: list[SessionProjectionItem] = []
        for thread_id, task_items in grouped.items():
            latest = max(task_items, key=lambda item: item.updated_at)
            execution_summary: dict[str, Any] = {}
            if self._task_runner is not None:
                session = await self._task_runner.get_execution_session(latest.task_id)
                if session is not None:
                    execution_summary = {
                        "session_id": session.session_id,
                        "state": session.state.value,
                        "interactive": session.interactive,
                        "current_step": session.current_step,
                    }
            latest_message = await self._extract_latest_user_message(latest.task_id)
            workspace = await self._stores.project_store.resolve_workspace_for_scope(
                latest.scope_id
            )
            session_items.append(
                SessionProjectionItem(
                    session_id=thread_id,
                    thread_id=thread_id,
                    task_id=latest.task_id,
                    title=latest.title,
                    status=latest.status.value,
                    channel=latest.requester.channel,
                    requester_id=latest.requester.sender_id,
                    project_id=workspace.project_id if workspace else state.selected_project_id,
                    workspace_id=workspace.workspace_id
                    if workspace
                    else state.selected_workspace_id,
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
            key=lambda item: item.latest_event_at or datetime.min.replace(tzinfo=UTC), reverse=True
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
            focused_thread_id=state.focused_thread_id,
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

    async def get_automation_document(self) -> AutomationJobDocument:
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
        project_selector = await self.get_project_selector()

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
        if action_id == "config.apply":
            return await self._handle_config_apply(request)
        if action_id == "backup.create":
            return await self._handle_backup_create(request)
        if action_id == "restore.plan":
            return await self._handle_restore_plan(request)
        if action_id == "import.run":
            return await self._handle_import_run(request)
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
            if resolved_request.status is not None
            and resolved_request.status.value == "approved"
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
                message=(
                    "当前没有可用的 Vault 授权。"
                    if decision.allowed
                    else decision.message
                ),
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
                message=(
                    "Memory 导出检查存在阻塞项。"
                    if decision.allowed
                    else decision.message
                ),
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
                message=(
                    "Memory 恢复校验存在阻塞项。"
                    if decision.allowed
                    else decision.message
                ),
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
        thread_id = str(request.params.get("thread_id", "")).strip()
        if not thread_id:
            raise ControlPlaneActionError("THREAD_ID_REQUIRED", "thread_id 不能为空")
        state = self._state_store.load().model_copy(
            update={
                "focused_thread_id": thread_id,
                "updated_at": datetime.now(tz=UTC),
            }
        )
        self._state_store.save(state)
        return self._completed_result(
            request=request,
            code="SESSION_FOCUSED",
            message="已更新当前聚焦会话",
            data={"thread_id": thread_id},
            resource_refs=[self._resource_ref("session_projection", "sessions:overview")],
            target_refs=[ControlPlaneTargetRef(target_type="thread", target_id=thread_id)],
        )

    async def _handle_session_export(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        thread_id = str(request.params.get("thread_id", "")).strip()
        task_id = str(request.params.get("task_id", "")).strip()
        since = request.params.get("since")
        until = request.params.get("until")
        manifest = await BackupService(
            self._project_root,
            store_group=self._stores,
        ).export_chats(
            task_id=task_id or None,
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

    async def _handle_import_run(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        input_path = str(request.params.get("input_path", "")).strip()
        if not input_path:
            raise ControlPlaneActionError("INPUT_PATH_REQUIRED", "input_path 不能为空")
        report = await ChatImportService(self._project_root, store_group=self._stores).import_chats(
            input_path=input_path,
            source_format=str(request.params.get("source_format", "normalized_jsonl")),
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
        job = self._automation_store.get_job(job_id)
        if job is None:
            raise ControlPlaneActionError("JOB_NOT_FOUND", "automation job 不存在")
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
        job = self._automation_store.get_job(job_id)
        if job is None:
            raise ControlPlaneActionError("JOB_NOT_FOUND", "automation job 不存在")
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
        job = self._automation_store.get_job(job_id)
        if job is None:
            raise ControlPlaneActionError("JOB_NOT_FOUND", "automation job 不存在")
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

    async def _resolve_selection(self) -> tuple[ControlPlaneState, Any | None, Any | None, str]:
        state = self._state_store.load()
        fallback_reason = ""
        project = (
            await self._stores.project_store.get_project(state.selected_project_id)
            if state.selected_project_id
            else None
        )
        if project is None:
            project = await self._stores.project_store.get_default_project()
            if project is not None and state.selected_project_id:
                fallback_reason = "selected project 不存在，已回退到 default project"

        workspace = (
            await self._stores.project_store.get_workspace(state.selected_workspace_id)
            if state.selected_workspace_id
            else None
        )
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
        return state, project, workspace, fallback_reason

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
            "channels.telegram.enabled": ConfigFieldHint(
                field_path="channels.telegram.enabled",
                section="channels",
                label="启用 Telegram",
                widget="toggle",
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
                order=90,
            ),
            "channels.telegram.allow_users": ConfigFieldHint(
                field_path="channels.telegram.allow_users",
                section="channels",
                label="允许的私聊用户",
                widget="string-list",
                order=100,
            ),
            "channels.telegram.allowed_groups": ConfigFieldHint(
                field_path="channels.telegram.allowed_groups",
                section="channels",
                label="允许的群组",
                widget="string-list",
                order=110,
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
                return str(event.payload.get("text_preview", "")).strip()
        return ""

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
                definition("import.run", "执行聊天导入", category="ops", risk_hint="medium"),
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
                    "diagnostics.refresh",
                    "刷新诊断",
                    category="diagnostics",
                    telegram_aliases=["/status"],
                    telegram_supported=True,
                ),
                definition("memory.query", "查询 Memory", category="memory"),
                definition(
                    "memory.subject.inspect",
                    "查看 Subject 历史",
                    category="memory",
                ),
                definition(
                    "memory.proposal.inspect",
                    "查看 Proposal 审计",
                    category="memory",
                ),
                definition(
                    "vault.access.request",
                    "申请 Vault 授权",
                    category="memory",
                ),
                definition(
                    "vault.access.resolve",
                    "审批 Vault 授权",
                    category="memory",
                    risk_hint="high",
                    approval_hint="operator",
                ),
                definition(
                    "vault.retrieve",
                    "检索 Vault",
                    category="memory",
                    risk_hint="high",
                    approval_hint="grant",
                ),
                definition(
                    "memory.export.inspect",
                    "检查 Memory 导出范围",
                    category="memory",
                ),
                definition(
                    "memory.restore.verify",
                    "校验 Memory 恢复",
                    category="memory",
                    risk_hint="high",
                    approval_hint="operator",
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
