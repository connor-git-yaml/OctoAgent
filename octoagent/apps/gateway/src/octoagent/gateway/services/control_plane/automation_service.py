"""AutomationDomainService -- 自动化作业（定时任务）领域服务。

从 control_plane.py 拆分：
- ensure_system_automation_jobs / _ensure_system_consolidate_job / _ensure_system_profile_generate_job
- _startup_consolidate_if_pending
- get_automation_document
- _handle_automation_create / _handle_automation_run / _handle_automation_pause_resume / _handle_automation_delete
- create_automation_run / record_automation_run_status
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine, Mapping
from datetime import UTC, datetime
from typing import Any

import structlog
from octoagent.core.models import (
    ActionRequestEnvelope,
    ActionResultEnvelope,
    AutomationJob,
    AutomationJobDocument,
    AutomationJobItem,
    AutomationJobRun,
    AutomationJobStatus,
    AutomationScheduleKind,
    ControlPlaneActor,
    ControlPlaneCapability,
    ControlPlaneEventType,
    ControlPlaneResourceRef,
    ControlPlaneSurface,
    ControlPlaneTargetRef,
)
from octoagent.provider.dx.automation_store import AutomationStore
from ulid import ULID

from ._base import ControlPlaneActionError, ControlPlaneContext, DomainServiceBase

log = structlog.get_logger()


class AutomationDomainService(DomainServiceBase):
    """自动化作业（定时任务）的全部 action / document / lifecycle 逻辑。"""

    def __init__(
        self,
        ctx: ControlPlaneContext,
        *,
        automation_store: AutomationStore | None = None,
    ) -> None:
        super().__init__(ctx)
        self._automation_store: AutomationStore = (
            automation_store or ctx.automation_store or AutomationStore(ctx.project_root)
        )
        self._automation_scheduler: Any = None

    # ------------------------------------------------------------------
    # 外部依赖绑定
    # ------------------------------------------------------------------

    @property
    def automation_store(self) -> AutomationStore:
        return self._automation_store

    def bind_automation_scheduler(self, scheduler: Any) -> None:
        self._automation_scheduler = scheduler

    # ------------------------------------------------------------------
    # action / document 路由
    # ------------------------------------------------------------------

    def action_routes(self) -> dict[str, Callable[..., Coroutine[Any, Any, ActionResultEnvelope]]]:
        return {
            "automation.create": self._handle_automation_create,
            "automation.run": self._handle_automation_run,
            "automation.pause": lambda req: self._handle_automation_pause_resume(req, enable=False),
            "automation.resume": lambda req: self._handle_automation_pause_resume(req, enable=True),
            "automation.delete": self._handle_automation_delete,
        }

    def document_routes(self) -> dict[str, Callable[..., Coroutine[Any, Any, Any]]]:
        return {
            "automation": self.get_automation_document,
        }

    # ------------------------------------------------------------------
    # 系统内置作业注册（startup）
    # ------------------------------------------------------------------

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
            await asyncio.sleep(5)

            memory_console_service = self._ctx.memory_console_service
            if memory_console_service is None:
                log.debug("startup_consolidate_skipped_no_memory_console")
                return

            result = await memory_console_service.run_consolidate()
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

    # ------------------------------------------------------------------
    # Document
    # ------------------------------------------------------------------

    async def get_automation_document(self) -> AutomationJobDocument:
        """全局展示所有自动化任务，不按项目过滤。"""
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

    # ------------------------------------------------------------------
    # Action handlers
    # ------------------------------------------------------------------

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
        # action_id 存在性校验：汇总所有 domain service 的 action_routes + coordinator inline
        known_action_ids: set[str] = set()
        for svc in self._ctx.service_registry.values():
            known_action_ids.update(svc.action_routes().keys())
        # coordinator 的 inline actions（未归属到任何 domain service 的简单处理器）
        known_action_ids.update({
            "wizard.refresh", "wizard.restart", "diagnostics.refresh",
            "capability.refresh", "work.refresh",
            "backup.create", "restore.plan",
            "update.dry_run", "update.apply",
            "runtime.restart", "runtime.verify",
            "operator.approval.resolve", "operator.alert.ack",
            "operator.task.retry", "operator.task.cancel",
            "channel.pairing.approve", "channel.pairing.reject",
        })
        if action_id not in known_action_ids:
            raise ControlPlaneActionError(
                "AUTOMATION_ACTION_INVALID",
                f"目标动作不存在: {action_id}",
            )
        try:
            schedule_kind = AutomationScheduleKind(schedule_kind_raw)
        except ValueError as exc:
            raise ControlPlaneActionError(
                "SCHEDULE_KIND_INVALID",
                f"不支持的 schedule_kind: {schedule_kind_raw}",
            ) from exc

        project_id = str(request.params.get("project_id", "")).strip()
        workspace_id = ""
        _, selected_project, _, _ = await self._resolve_selection()
        if not project_id and selected_project is not None:
            project_id = selected_project.project_id
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

    # ------------------------------------------------------------------
    # 公共 API（供 scheduler / runner 调用）
    # ------------------------------------------------------------------

    async def record_automation_run_status(
        self,
        *,
        run: AutomationJobRun,
        status: str,
        summary: str,
        result_code: str,
        resource_refs: list[ControlPlaneResourceRef] | None = None,
        publish_event: Any | None = None,
    ) -> AutomationJobRun:
        """更新 automation run 状态并持久化。

        Args:
            publish_event: 可选的回调，签名 (resource_ref, request, correlation_id, summary) -> str。
                           由 coordinator 层注入，用于发布 control-plane event。
        """
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

        if publish_event is not None:
            await publish_event(
                resource_ref=self._resource_ref("automation_job", "automation:jobs"),
                request=ActionRequestEnvelope(
                    request_id=run.request_id,
                    action_id="automation.run",
                    params={"job_id": run.job_id},
                    surface=ControlPlaneSurface.SYSTEM,
                    actor=ControlPlaneActor(
                        actor_id="system:automation", actor_label="automation"
                    ),
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

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    async def _get_automation_job_in_scope(self, job_id: str) -> AutomationJob:
        job = self._automation_store.get_job(job_id)
        if job is None:
            raise ControlPlaneActionError("JOB_NOT_FOUND", "automation job 不存在")
        return job
