"""ControlPlaneService — 从 domain service 切换后的唯一公共入口。

整合所有 domain service，对外暴露统一 API：
- __init__: 创建 ControlPlaneContext + 实例化所有 domain service
- get_snapshot: 并行调用各 service 的 document getter
- execute_action: 事件发布包装 + action 路由分发
- get_action_registry / get_action_definition: 静态 action 注册表
- build_telegram_action_request: Telegram 命令解析
- list_events: 事件查询
- 各种 get_*_document: facade 委托到对应 domain service
- bind_* / ensure_system_automation_jobs: 延迟注入与启动初始化
- record_automation_run_status / create_automation_run: 自动化 run 记录
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from octoagent.core.models import (
    ActionDefinition,
    ActionRegistryDocument,
    ActionRequestEnvelope,
    ActionResultEnvelope,
    ActorType,
    AgentProfile,
    AgentProfileScope,
    AgentRuntime,
    AgentRuntimeRole,
    AgentRuntimeStatus,
    AgentSession,
    AgentSessionKind,
    AgentSessionStatus,
    AutomationJob,
    AutomationScheduleKind,
    ControlPlaneActionStatus,
    ControlPlaneActor,
    ControlPlaneEvent,
    ControlPlaneEventType,
    ControlPlaneResourceRef,
    ControlPlaneSurface,
    ControlPlaneTargetRef,
    Event,
    EventCausality,
    EventType,
    Task,
    TaskPointers,
    TaskStatus,
)
from octoagent.core.models.agent_context import DEFAULT_PERMISSION_PRESET
from octoagent.core.models.payloads import ControlPlaneAuditPayload
from octoagent.core.models.task import RequesterInfo
from octoagent.core.store import StoreGroup
from octoagent.gateway.services.control_plane.automation_store import AutomationStore
from octoagent.gateway.services.control_plane.control_plane_state import ControlPlaneStateStore
from octoagent.gateway.services.memory.memory_console_service import MemoryConsoleService
from octoagent.gateway.services.memory.retrieval_platform_service import (
    RetrievalPlatformError,
    RetrievalPlatformService,
)
from octoagent.provider.dx.import_workbench_service import ImportWorkbenchService
from ulid import ULID

from ._base import ControlPlaneActionError, ControlPlaneContext, ControlPlaneServiceRegistry
from .action_registry import build_action_registry
from .agent_service import AgentProfileDomainService
from .automation_service import AutomationDomainService
from .import_service import ImportDomainService
from .mcp_service import McpDomainService
from .memory_service import MemoryDomainService
from .session_service import SessionDomainService
from .setup_service import SetupDomainService
from .telegram_commands import TelegramCommandMixin
from .work_service import WorkDomainService
from .worker_service import WorkerProfileDomainService

_AUDIT_TASK_ID = "ops-control-plane"
_AUDIT_TRACE_ID = "trace-ops-control-plane"
log = structlog.get_logger()


class ControlPlaneService(TelegramCommandMixin):
    """对外提供 canonical control-plane resources / actions / events。

    Thin facade — 不包含业务逻辑，所有 domain logic 委托给各 DomainService。
    """

    def __init__(
        self,
        *,
        project_root: Path,
        store_group: StoreGroup,
        sse_hub: Any = None,
        task_runner: Any = None,
        operator_action_service: Any = None,
        operator_inbox_service: Any = None,
        telegram_state_store: Any = None,
        update_status_store: Any = None,
        update_service: Any = None,
        memory_console_service: MemoryConsoleService | None = None,
        capability_pack_service: Any = None,
        delegation_plane_service: Any = None,
        import_workbench_service: ImportWorkbenchService | None = None,
        policy_engine: Any = None,
    ) -> None:
        # 构建共享上下文
        self._ctx = ControlPlaneContext(
            project_root=project_root,
            store_group=store_group,
            sse_hub=sse_hub,
            task_runner=task_runner,
            capability_pack_service=capability_pack_service,
            delegation_plane_service=delegation_plane_service,
            import_workbench_service=import_workbench_service or ImportWorkbenchService(
                project_root,
                surface="web",
                store_group=store_group,
            ),
            memory_console_service=memory_console_service or MemoryConsoleService(
                project_root,
                store_group=store_group,
            ),
            retrieval_platform_service=RetrievalPlatformService(
                project_root,
                store_group=store_group,
            ),
            operator_action_service=operator_action_service,
            operator_inbox_service=operator_inbox_service,
            policy_engine=policy_engine,
            update_service=update_service,
            automation_store=AutomationStore(project_root),
        )

        self._stores = store_group
        self._project_root = project_root
        self._automation_scheduler: Any = None
        self._audit_task_ensured = False
        self._telegram_state_store = telegram_state_store
        self._update_status_store = update_status_store

        # 实例化 domain services
        self._session_service = SessionDomainService(self._ctx)
        self._work_service = WorkDomainService(self._ctx)
        self._agent_service = AgentProfileDomainService(self._ctx)
        self._automation_service = AutomationDomainService(self._ctx)
        self._import_service = ImportDomainService(self._ctx)
        self._mcp_service = McpDomainService(self._ctx)
        self._memory_service = MemoryDomainService(
            self._ctx,
            memory_console_service=self._ctx.memory_console_service,
            retrieval_platform_service=self._ctx.retrieval_platform_service,
        )
        self._setup_service = SetupDomainService(
            self._ctx,
            telegram_state_store=telegram_state_store,
            update_status_store=update_status_store,
        )
        self._worker_service = WorkerProfileDomainService(self._ctx)

        # 注册跨 service 调用注册表（F108b W7：构造期 typed 对象，缺字段即 TypeError）
        self._ctx.services = ControlPlaneServiceRegistry(
            agent=self._agent_service,
            automation=self._automation_service,
            import_=self._import_service,
            mcp=self._mcp_service,
            memory=self._memory_service,
            session=self._session_service,
            setup=self._setup_service,
            work=self._work_service,
            worker=self._worker_service,
        )

        # 汇总 action 路由
        all_services = [
            self._session_service,
            self._work_service,
            self._agent_service,
            self._automation_service,
            self._import_service,
            self._mcp_service,
            self._memory_service,
            self._setup_service,
            self._worker_service,
        ]
        self._action_dispatch: dict[str, Any] = {}
        for svc in all_services:
            self._action_dispatch.update(svc.action_routes())

        # 汇总 document 路由
        self._document_dispatch: dict[str, Any] = {}
        for svc in all_services:
            self._document_dispatch.update(svc.document_routes())

        # 构建 action 注册表
        self._registry = build_action_registry()

    # ------------------------------------------------------------------
    # 属性和延迟绑定
    # ------------------------------------------------------------------

    @property
    def automation_store(self) -> AutomationStore:
        return self._ctx.automation_store

    def bind_automation_scheduler(self, scheduler: Any) -> None:
        self._automation_scheduler = scheduler
        self._automation_service.bind_automation_scheduler(scheduler)

    def bind_proxy_manager(self, proxy_manager: Any | None) -> None:
        self._proxy_manager = proxy_manager
        self._setup_service.bind_proxy_manager(proxy_manager)
        self._mcp_service.bind_proxy_manager(proxy_manager)

    def bind_mcp_installer(self, installer: Any) -> None:
        self._mcp_installer = installer
        self._mcp_service.bind_mcp_installer(installer)

    # ------------------------------------------------------------------
    # Action Registry
    # ------------------------------------------------------------------

    def get_action_registry(self) -> ActionRegistryDocument:
        return self._registry

    def get_action_definition(self, action_id: str) -> ActionDefinition | None:
        return next(
            (item for item in self._registry.actions if item.action_id == action_id),
            None,
        )

    # ------------------------------------------------------------------
    # 启动初始化
    # ------------------------------------------------------------------

    async def ensure_system_automation_jobs(self) -> None:
        """确保系统内置的自动化作业已注册。"""
        await self._automation_service.ensure_system_automation_jobs()

    # ------------------------------------------------------------------
    # execute_action
    # ------------------------------------------------------------------

    async def execute_action(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        await self._publish_action_event(
            event_type=ControlPlaneEventType.ACTION_REQUESTED,
            request=request,
            summary=f"{request.action_id} requested",
        )

        try:
            result = await self._dispatch_action(request)
        except ControlPlaneActionError as exc:
            result = ActionResultEnvelope(
                request_id=request.request_id,
                correlation_id=request.request_id,
                action_id=request.action_id,
                status=ControlPlaneActionStatus.REJECTED,
                code=exc.code,
                message=str(exc),
            )
        except RetrievalPlatformError as exc:
            result = ActionResultEnvelope(
                request_id=request.request_id,
                correlation_id=request.request_id,
                action_id=request.action_id,
                status=ControlPlaneActionStatus.REJECTED,
                code=exc.code,
                message=exc.message,
            )
        except Exception as exc:
            result = ActionResultEnvelope(
                request_id=request.request_id,
                correlation_id=request.request_id,
                action_id=request.action_id,
                status=ControlPlaneActionStatus.REJECTED,
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
        # 先检查 coordinator 内联动作
        inline_result = await self._dispatch_inline_action(request)
        if inline_result is not None:
            return inline_result
        # 再委托到 domain services
        handler = self._action_dispatch.get(request.action_id)
        if handler is not None:
            return await handler(request)
        raise ControlPlaneActionError("ACTION_NOT_FOUND", f"未知动作: {request.action_id}")

    async def _dispatch_inline_action(
        self, request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope | None:
        """处理不属于任何 domain service 的简单 inline actions。"""
        action_id = request.action_id

        if action_id == "wizard.refresh":
            from octoagent.gateway.services.onboarding import OnboardingService
            await OnboardingService(self._project_root).run(status_only=True)
            return self._completed_result(
                request=request,
                code="WIZARD_REFRESHED",
                message="已刷新 wizard 状态",
                resource_refs=[self._resource_ref("wizard_session", "wizard:default")],
            )

        if action_id == "wizard.restart":
            from octoagent.gateway.services.onboarding import OnboardingService
            await OnboardingService(self._project_root).run(restart=True, status_only=False)
            return self._completed_result(
                request=request,
                code="WIZARD_RESTARTED",
                message="已重新启动 wizard",
                resource_refs=[self._resource_ref("wizard_session", "wizard:default")],
            )

        if action_id == "diagnostics.refresh":
            diagnostics = await self.get_diagnostics_summary()
            return self._completed_result(
                request=request,
                code="DIAGNOSTICS_REFRESHED",
                message="已刷新诊断摘要",
                data={"overall_status": diagnostics.overall_status},
                resource_refs=[self._resource_ref("diagnostics_summary", "diagnostics:runtime")],
            )

        if action_id == "capability.refresh":
            if self._ctx.capability_pack_service is not None:
                await self._ctx.capability_pack_service.refresh()
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

        if action_id == "backup.create":
            from octoagent.provider.dx.backup_service import BackupService
            label = str(request.params.get("label", "")).strip() or None
            bundle = await BackupService(self._project_root, store_group=self._stores).create_bundle(
                label=label,
            )
            return self._completed_result(
                request=request,
                code="BACKUP_CREATED",
                message="已创建 backup bundle",
                data=bundle.model_dump(mode="json"),
                resource_refs=[self._resource_ref("diagnostics_summary", "diagnostics:runtime")],
            )

        if action_id == "restore.plan":
            from octoagent.provider.dx.backup_service import BackupService
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

        if action_id == "update.dry_run":
            if self._ctx.update_service is None:
                raise ControlPlaneActionError("UPDATE_SERVICE_UNAVAILABLE", "update service 不可用")
            summary = await self._ctx.update_service.preview(
                trigger_source=self._map_update_source(request.surface),
            )
            return self._completed_result(
                request=request,
                code="UPDATE_DRY_RUN_READY",
                message="已完成 update dry-run",
                data=summary.model_dump(mode="json"),
                resource_refs=[self._resource_ref("diagnostics_summary", "diagnostics:runtime")],
            )

        if action_id == "update.apply":
            if self._ctx.update_service is None:
                raise ControlPlaneActionError("UPDATE_SERVICE_UNAVAILABLE", "update service 不可用")
            summary = await self._ctx.update_service.apply(
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

        if action_id == "runtime.restart":
            if self._ctx.update_service is None:
                raise ControlPlaneActionError("UPDATE_SERVICE_UNAVAILABLE", "update service 不可用")
            summary = await self._ctx.update_service.restart(
                trigger_source=self._map_update_source(request.surface),
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

        if action_id == "runtime.verify":
            if self._ctx.update_service is None:
                raise ControlPlaneActionError("UPDATE_SERVICE_UNAVAILABLE", "update service 不可用")
            summary = await self._ctx.update_service.verify(
                trigger_source=self._map_update_source(request.surface),
            )
            return self._completed_result(
                request=request,
                code="RUNTIME_VERIFY_COMPLETED",
                message="已完成 runtime verify",
                data=summary.model_dump(mode="json"),
                resource_refs=[self._resource_ref("diagnostics_summary", "diagnostics:runtime")],
            )

        if action_id == "operator.approval.resolve":
            return await self._handle_operator_approval(request)

        if action_id in {
            "operator.alert.ack", "operator.task.retry", "operator.task.cancel",
            "channel.pairing.approve", "channel.pairing.reject",
        }:
            from octoagent.core.models import OperatorActionKind
            kind_map = {
                "operator.alert.ack": OperatorActionKind.ACK_ALERT,
                "operator.task.retry": OperatorActionKind.RETRY_TASK,
                "operator.task.cancel": OperatorActionKind.CANCEL_TASK,
                "channel.pairing.approve": OperatorActionKind.APPROVE_PAIRING,
                "channel.pairing.reject": OperatorActionKind.REJECT_PAIRING,
            }
            return await self._handle_operator_action(request, kind=kind_map[action_id])

        return None

    # ------------------------------------------------------------------
    # Operator action helpers
    # ------------------------------------------------------------------

    async def _handle_operator_approval(
        self, request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        from octoagent.core.models import OperatorActionKind
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
        kind: Any,
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
        kind: Any,
    ) -> ActionResultEnvelope:
        from octoagent.core.models import OperatorActionRequest, OperatorActionSource
        if self._ctx.operator_action_service is None:
            raise ControlPlaneActionError(
                "OPERATOR_ACTION_UNAVAILABLE", "operator action service 不可用",
            )
        result = await self._ctx.operator_action_service.execute(
            OperatorActionRequest(
                item_id=item_id,
                kind=kind,
                source=self._map_operator_source(request.surface),
                actor_id=request.actor.actor_id,
                actor_label=request.actor.actor_label or request.actor.actor_id,
            )
        )
        if result.outcome.value in {"failed", "not_allowed", "not_found"}:
            return ActionResultEnvelope(
                request_id=request.request_id,
                correlation_id=request.request_id,
                action_id=request.action_id,
                status=ControlPlaneActionStatus.REJECTED,
                code=result.outcome.value.upper(),
                message=result.message,
                target_refs=[ControlPlaneTargetRef(target_type="operator_item", target_id=item_id)],
            )
        return ActionResultEnvelope(
            request_id=request.request_id,
            correlation_id=request.request_id,
            action_id=request.action_id,
            status=ControlPlaneActionStatus.COMPLETED,
            code=result.outcome.value.upper(),
            message=result.message,
            data=result.model_dump(mode="json"),
            resource_refs=[self._resource_ref("session_projection", "sessions:overview")],
            target_refs=[ControlPlaneTargetRef(target_type="operator_item", target_id=item_id)],
        )

    @staticmethod
    def _map_operator_source(surface: ControlPlaneSurface) -> Any:
        from octoagent.core.models import OperatorActionSource
        mapping = {
            ControlPlaneSurface.WEB: OperatorActionSource.WEB,
            ControlPlaneSurface.CLI: OperatorActionSource.CLI,
            ControlPlaneSurface.TELEGRAM: OperatorActionSource.TELEGRAM,
        }
        return mapping.get(surface, OperatorActionSource.SYSTEM)

    @staticmethod
    def _map_update_source(surface: ControlPlaneSurface) -> Any:
        from octoagent.core.models.update import UpdateTriggerSource
        mapping = {
            ControlPlaneSurface.WEB: UpdateTriggerSource.WEB,
            ControlPlaneSurface.CLI: UpdateTriggerSource.CLI,
            ControlPlaneSurface.TELEGRAM: UpdateTriggerSource.TELEGRAM,
        }
        return mapping.get(surface, UpdateTriggerSource.SYSTEM)

    # ------------------------------------------------------------------
    # 结果构建工具（inline actions 使用）
    # ------------------------------------------------------------------

    def _completed_result(
        self,
        *,
        request: ActionRequestEnvelope,
        code: str,
        message: str,
        data: dict[str, Any] | None = None,
        resource_refs: list[ControlPlaneResourceRef] | None = None,
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
        )

    @staticmethod
    def _resource_ref(resource_type: str, resource_id: str) -> ControlPlaneResourceRef:
        return ControlPlaneResourceRef(
            resource_type=resource_type,
            resource_id=resource_id,
            schema_version=1,
        )

    # ------------------------------------------------------------------
    # get_snapshot — 聚合所有 document getter
    # ------------------------------------------------------------------

    async def get_snapshot(self, *, mode: str | None = None) -> dict[str, Any]:
        await self._ensure_default_main_agent_bootstrap()
        registry = self.get_action_registry()
        resources: dict[str, Any] = {}
        degraded_sections: list[str] = []
        resource_errors: dict[str, dict[str, str]] = {}
        resolvers: tuple[tuple[str, Any], ...] = (
            ("config", self.get_config_schema),
            ("project_selector", self.get_project_selector),
            ("sessions", self.get_session_projection),
            ("agent_profiles", self.get_agent_profiles_document),
            ("worker_profiles", self.get_worker_profiles_document),
            ("owner_profile", self.get_owner_profile_document),
            ("bootstrap_session", self.get_bootstrap_session_document),
            ("context_continuity", self.get_context_continuity_document),
            ("capability_pack", self.get_capability_pack_document),
            ("skill_governance", self.get_skill_governance_document),
            ("mcp_provider_catalog", self.get_mcp_provider_catalog_document),
            ("setup_governance", self.get_setup_governance_document),
            ("delegation", self.get_delegation_document),
            ("diagnostics", self.get_diagnostics_summary),
            ("retrieval_platform", self.get_retrieval_platform_document),
            ("memory", self.get_memory_console),
        )
        lite_sections = {
            "config",
            "project_selector",
            "sessions",
            "agent_profiles",
            "worker_profiles",
            "owner_profile",
            "bootstrap_session",
            "context_continuity",
            "capability_pack",
            "skill_governance",
            "mcp_provider_catalog",
            "setup_governance",
            "delegation",
            "diagnostics",
        }
        selected_resolvers = (
            [item for item in resolvers if item[0] in lite_sections]
            if str(mode or "").strip().lower() == "lite"
            else list(resolvers)
        )
        skipped_sections = {
            name for name, _ in resolvers
            if str(mode or "").strip().lower() == "lite" and name not in lite_sections
        }

        async def _run_resolver(section: str, resolver: Any) -> tuple[str, Any, Exception | None]:
            try:
                document = await resolver()
                return section, document, None
            except Exception as exc:
                return section, None, exc

        snapshot_started = time.perf_counter()
        section_timings_ms: dict[str, float] = {}

        async def _run_timed_resolver(section: str, resolver: Any) -> tuple[str, Any, Exception | None]:
            started = time.perf_counter()
            try:
                return await _run_resolver(section, resolver)
            finally:
                section_timings_ms[section] = (time.perf_counter() - started) * 1000

        results = await asyncio.gather(
            *[
                _run_timed_resolver(section, resolver)
                for section, resolver in selected_resolvers
            ]
        )
        for section, document, exc in results:
            if exc is None:
                resources[section] = document.model_dump(mode="json", by_alias=True)
                continue
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

        for section in skipped_sections:
            degraded_sections.append(section)
            resource_errors[section] = {
                "code": "SNAPSHOT_SECTION_SKIPPED",
                "error_type": "LiteSnapshot",
                "message": "该资源在 lite snapshot 中跳过，稍后会自动补齐。",
            }
            resources[section] = self._degraded_snapshot_resource(
                section=section,
                error_type="LiteSnapshot",
                message="该资源在 lite snapshot 中跳过，稍后会自动补齐。",
            )
        snapshot_payload = {
            "status": "degraded" if degraded_sections else "ready",
            "contract_version": registry.contract_version,
            "resources": resources,
            "registry": registry.model_dump(mode="json", by_alias=True),
            "degraded_sections": degraded_sections,
            "resource_errors": resource_errors,
            "generated_at": datetime.now(tz=UTC).isoformat(),
        }
        total_ms = (time.perf_counter() - snapshot_started) * 1000
        log.info(
            "control_plane_snapshot_ready",
            mode=(str(mode).strip().lower() if mode is not None else "full"),
            total_ms=round(total_ms, 2),
            section_ms={key: round(value, 2) for key, value in section_timings_ms.items()},
            skipped_sections=sorted(skipped_sections),
            degraded_sections=sorted(degraded_sections),
        )
        return snapshot_payload

    # ------------------------------------------------------------------
    # Document getter facades（委托到 domain services）
    # ------------------------------------------------------------------

    async def get_wizard_session(self):
        return await self._setup_service._get_wizard_session()

    async def get_config_schema(self):
        return await self._setup_service.get_config_schema()

    async def get_project_selector(self):
        return await self._setup_service.get_project_selector()

    async def get_session_projection(self):
        return await self._session_service.get_session_projection()

    async def _resolve_selection(self):
        """Facade: 委托到 setup service 的 _resolve_selection。"""
        return await self._setup_service._resolve_selection()

    def _resolve_active_agent_profile_payload(self, **kwargs):
        """Facade: 委托到 agent service。"""
        return self._agent_service._resolve_active_agent_profile_payload(**kwargs)

    async def get_agent_profiles_document(self):
        return await self._agent_service.get_agent_profiles_document()

    async def get_worker_profiles_document(self):
        return await self._worker_service.get_worker_profiles_document()

    async def get_worker_profile_revisions_document(self, profile_id: str):
        return await self._worker_service.get_worker_profile_revisions_document(profile_id)

    async def get_owner_profile_document(self):
        return await self._agent_service.get_owner_profile_document()

    async def get_bootstrap_session_document(self):
        return await self._session_service.get_bootstrap_session_document()

    async def get_context_continuity_document(self):
        return await self._session_service.get_context_continuity_document()

    async def get_policy_profiles_document(self):
        return await self._agent_service.get_policy_profiles_document()

    async def get_skill_governance_document(self, **kwargs):
        return await self._setup_service.get_skill_governance_document(**kwargs)

    async def get_mcp_provider_catalog_document(self):
        return await self._mcp_service.get_mcp_provider_catalog_document()

    async def get_setup_governance_document(self):
        return await self._setup_service.get_setup_governance_document()

    async def get_automation_document(self):
        return await self._automation_service.get_automation_document()

    async def get_capability_pack_document(self):
        return await self._setup_service.get_capability_pack_document()

    async def get_delegation_document(self):
        return await self._work_service.get_delegation_document()

    async def get_skill_pipeline_document(self):
        return await self._work_service.get_skill_pipeline_document()

    async def get_diagnostics_summary(self):
        return await self._setup_service.get_diagnostics_summary()

    async def get_memory_console(self, **kwargs):
        return await self._memory_service.get_memory_console(**kwargs)

    async def get_retrieval_platform_document(self, **kwargs):
        return await self._memory_service.get_retrieval_platform_document(**kwargs)

    async def list_recall_frames(self, **kwargs):
        # F096 块 B + H3 闭环：audit endpoint 转发到 MemoryDomainService
        return await self._memory_service.list_recall_frames(**kwargs)

    # ------------------------------------------------------------------
    # list_events
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # 自动化 run 记录（automation_scheduler 调用）
    # ------------------------------------------------------------------

    async def record_automation_run_status(
        self,
        *,
        run: Any,
        status: str,
        summary: str,
        result_code: str,
        resource_refs: list[ControlPlaneResourceRef] | None = None,
    ) -> Any:
        return await self._automation_service.record_automation_run_status(
            run=run,
            status=status,
            summary=summary,
            result_code=result_code,
            resource_refs=resource_refs,
            publish_event=self._publish_resource_event,
        )

    async def create_automation_run(
        self,
        *,
        job: AutomationJob,
        actor: Any = None,
        trigger: str = "scheduler",
    ) -> Any:
        from octoagent.core.models import ControlPlaneActor as _Actor
        resolved_actor = actor or _Actor(
            actor_id="system:automation",
            actor_label="Automation Scheduler",
        )
        return await self._automation_service.create_automation_run(
            job=job, actor=resolved_actor,
        )

    # ------------------------------------------------------------------
    # 默认 bootstrap
    # ------------------------------------------------------------------

    async def _ensure_default_main_agent_bootstrap(self) -> None:
        """确保默认 Project 有主 Agent + 直接会话（仅缺失时创建）。"""
        project = await self._stores.project_store.get_default_project()
        if project is None:
            return

        now = datetime.now(tz=UTC)
        agent_profile_id = project.default_agent_profile_id
        dirty = False

        if agent_profile_id:
            existing_agent_profile = await self._stores.agent_context_store.get_agent_profile(
                agent_profile_id
            )
            if existing_agent_profile is None:
                agent_profile_id = ""

        if not agent_profile_id:
            # F117 Wave 4 id-收口：主 Agent（kind=main 默认）写干净 bare agent_profile——去
            # agent-profile-{worker-profile-{id}} 双前缀 + 去 worker_profiles 行 + reverse-replace。
            # 主 Agent 非 worker，不需 worker_profiles 行/镜像。仅 fresh 默认 project bootstrap 触发
            # （幂等：已有 default_agent_profile_id 即跳过）→ 存量实例不受影响。
            agent_profile_id = f"agent-profile-{str(ULID())}"
            agent_profile = AgentProfile(
                profile_id=agent_profile_id,
                scope=AgentProfileScope.PROJECT,
                project_id=project.project_id,
                name=f"{project.name} 主 Agent",
                persona_summary="",
                model_alias="main",
                tool_profile="standard",
            )
            await self._stores.agent_context_store.save_agent_profile(agent_profile)
            dirty = True

            project = project.model_copy(
                update={
                    "default_agent_profile_id": agent_profile_id,
                    "updated_at": now,
                }
            )
            await self._stores.project_store.save_project(project)
            dirty = True

        runtimes = await self._stores.agent_context_store.list_agent_runtimes(
            project_id=project.project_id,
            role=AgentRuntimeRole.MAIN,
        )
        runtime = next(
            (item for item in runtimes if item.agent_profile_id == agent_profile_id),
            None,
        )
        if runtime is None:
            runtime = AgentRuntime(
                agent_runtime_id=f"runtime-{str(ULID())}",
                project_id=project.project_id,
                workspace_id="",
                agent_profile_id=agent_profile_id,
                worker_profile_id="",
                role=AgentRuntimeRole.MAIN,
                name=project.name,
                persona_summary="",
                status=AgentRuntimeStatus.ACTIVE,
                permission_preset=DEFAULT_PERMISSION_PRESET,
                role_card="",
                metadata={},
                created_at=now,
                updated_at=now,
            )
            await self._stores.agent_context_store.save_agent_runtime(runtime)
            dirty = True

        sessions = await self._stores.agent_context_store.list_agent_sessions(
            agent_runtime_id=runtime.agent_runtime_id,
            kind=AgentSessionKind.DIRECT_WORKER,
            limit=1,
        )
        if not sessions:
            session = AgentSession(
                agent_session_id=f"session-{str(ULID())}",
                agent_runtime_id=runtime.agent_runtime_id,
                project_id=project.project_id,
                workspace_id="",
                kind=AgentSessionKind.DIRECT_WORKER,
                status=AgentSessionStatus.ACTIVE,
                surface="chat",
                created_at=now,
                updated_at=now,
            )
            await self._stores.agent_context_store.save_agent_session(session)
            dirty = True

        if dirty:
            await self._stores.conn.commit()

    # ------------------------------------------------------------------
    # 事件基础设施
    # ------------------------------------------------------------------

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
