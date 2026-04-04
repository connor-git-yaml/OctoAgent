"""ControlPlaneCoordinator — thin coordinator，整合所有 domain service。

Phase 8C: 从 control_plane.py 中保留的顶层协调逻辑：
- __init__: 创建 ControlPlaneContext + 实例化所有 domain service
- get_snapshot: 并行调用各 service 的 document getter
- execute_action: 事件发布包装
- _dispatch_action: 从字典路由分发到各 service
- _publish_resource_event / _append_control_event: 事件基础设施
- build_telegram_action_request: Telegram 适配
- startup / ensure_system_automation_jobs: 启动时初始化
- 各种 bind_* 方法: 延迟注入

注意：此文件是拆分后的 coordinator 参考实现，不替换原始 control_plane.py。
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from octoagent.core.models import (
    ActionRegistryDocument,
    ActionRequestEnvelope,
    ActionResultEnvelope,
    ActorType,
    AutomationJob,
    AutomationScheduleKind,
    ControlPlaneActionStatus,
    ControlPlaneActor,
    ControlPlaneEvent,
    ControlPlaneEventType,
    ControlPlaneResourceRef,
    ControlPlaneSurface,
    Event,
    EventCausality,
    EventType,
    Task,
    TaskPointers,
    TaskStatus,
)
from octoagent.core.models.payloads import ControlPlaneAuditPayload
from octoagent.core.models.task import RequesterInfo
from octoagent.core.store import StoreGroup
from octoagent.provider.dx.automation_store import AutomationStore
from octoagent.provider.dx.control_plane_state import ControlPlaneStateStore
from octoagent.provider.dx.import_workbench_service import ImportWorkbenchService
from octoagent.provider.dx.memory_console_service import MemoryConsoleService
from octoagent.provider.dx.retrieval_platform_service import (
    RetrievalPlatformError,
    RetrievalPlatformService,
)
from ulid import ULID

from ._base import ControlPlaneActionError, ControlPlaneContext

from .session_service import SessionDomainService
from .work_service import WorkDomainService

_AUDIT_TASK_ID = "ops-control-plane"
_AUDIT_TRACE_ID = "trace-ops-control-plane"
log = structlog.get_logger()


class ControlPlaneCoordinator:
    """Thin coordinator — 路由分发 + 事件发布 + snapshot 聚合。

    不包含任何业务逻辑，所有 domain logic 委托给各 DomainService。
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

        # 汇总 action 路由
        self._action_dispatch: dict[str, Any] = {}
        self._action_dispatch.update(self._session_service.action_routes())
        self._action_dispatch.update(self._work_service.action_routes())

        # 汇总 document 路由
        self._document_dispatch: dict[str, Any] = {}
        self._document_dispatch.update(self._session_service.document_routes())
        self._document_dispatch.update(self._work_service.document_routes())

    # ------------------------------------------------------------------
    # 属性和延迟绑定
    # ------------------------------------------------------------------

    @property
    def automation_store(self) -> AutomationStore:
        return self._ctx.automation_store

    def bind_automation_scheduler(self, scheduler: Any) -> None:
        self._automation_scheduler = scheduler

    def bind_proxy_manager(self, proxy_manager: Any | None) -> None:
        self._proxy_manager = proxy_manager

    def bind_mcp_installer(self, installer: Any) -> None:
        self._mcp_installer = installer

    # ------------------------------------------------------------------
    # 启动初始化
    # ------------------------------------------------------------------

    async def ensure_system_automation_jobs(self) -> None:
        """确保系统内置的自动化作业已注册。"""
        self._ensure_system_consolidate_job()
        self._ensure_system_profile_generate_job()
        asyncio.create_task(self._startup_consolidate_if_pending())

    def _ensure_system_consolidate_job(self) -> None:
        job_id = "system:memory-consolidate"
        existing = self._ctx.automation_store.get_job(job_id)
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
            self._ctx.automation_store.save_job(job)
            log.info("system_job_registered", job_id=job_id, schedule_expr="0 */4 * * *")
        except Exception as exc:
            log.warning("system_job_registration_failed", job_id=job_id, error=str(exc))

    def _ensure_system_profile_generate_job(self) -> None:
        job_id = "system:memory-profile-generate"
        existing = self._ctx.automation_store.get_job(job_id)
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
            self._ctx.automation_store.save_job(job)
            log.info("system_job_registered", job_id=job_id, schedule_expr="0 2 * * *")
        except Exception as exc:
            log.warning("system_job_registration_failed", job_id=job_id, error=str(exc))

    async def _startup_consolidate_if_pending(self) -> None:
        try:
            await asyncio.sleep(5)
            result = await self._ctx.memory_console_service.run_consolidate()
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

    # ------------------------------------------------------------------
    # Telegram 适配
    # ------------------------------------------------------------------

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

        # Telegram 命令映射表（与原 control_plane.py 一致）
        dispatch_table: list[tuple[str, str, dict[str, Any] | None]] = []
        if command == "/status":
            dispatch_table.append(("/status", "diagnostics.refresh", {}))
        elif command == "/project" and len(parts) >= 3 and parts[1].lower() == "select":
            dispatch_table.append(("/project select", "project.select", {"project_id": parts[2]}))
        elif command == "/approve" and len(parts) >= 3:
            dispatch_table.append((
                "/approve", "operator.approval.resolve",
                {"approval_id": parts[1], "mode": parts[2]},
            ))
        elif command == "/cancel" and len(parts) >= 2:
            dispatch_table.append(("/cancel", "session.interrupt", {"task_id": parts[1]}))
        elif command == "/retry" and len(parts) >= 2:
            dispatch_table.append(("/retry", "operator.task.retry", {"item_id": f"task:{parts[1]}"}))
        elif command == "/backup":
            label = " ".join(parts[1:]) if len(parts) >= 2 else ""
            p = {"label": label} if label else {}
            dispatch_table.append(("/backup", "backup.create", p))
        elif command == "/update" and len(parts) >= 2:
            mode = parts[1].lower()
            if mode == "dry-run":
                dispatch_table.append((f"/update {mode}", "update.dry_run", {}))
            elif mode == "apply":
                dispatch_table.append((f"/update {mode}", "update.apply", {}))
        elif command == "/automation" and len(parts) >= 3 and parts[1].lower() == "run":
            dispatch_table.append(("/automation run", "automation.run", {"job_id": parts[2]}))
        elif command == "/work" and len(parts) >= 3:
            sub = parts[1].lower()
            if sub == "cancel":
                dispatch_table.append(("/work cancel", "work.cancel", {"work_id": parts[2]}))
            elif sub == "retry":
                dispatch_table.append(("/work retry", "work.retry", {"work_id": parts[2]}))
            elif sub == "delete":
                dispatch_table.append(("/work delete", "work.delete", {"work_id": parts[2]}))
            elif sub == "escalate":
                dispatch_table.append(("/work escalate", "work.escalate", {"work_id": parts[2]}))
        elif command == "/pipeline" and len(parts) >= 3:
            sub = parts[1].lower()
            if sub == "resume":
                dispatch_table.append(("/pipeline resume", "pipeline.resume", {"work_id": parts[2]}))
            elif sub == "retry":
                dispatch_table.append(("/pipeline retry", "pipeline.retry_node", {"work_id": parts[2]}))

        for _alias, aid, p in dispatch_table:
            action_id = aid
            params = p or {}
            break

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

    # ------------------------------------------------------------------
    # execute_action + _dispatch_action
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
        handler = self._action_dispatch.get(request.action_id)
        if handler is not None:
            return await handler(request)
        raise ControlPlaneActionError("ACTION_NOT_FOUND", f"未知动作: {request.action_id}")

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
