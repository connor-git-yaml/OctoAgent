"""Feature 026: automation scheduler runtime。"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from octoagent.core.models import (
    ActionRequestEnvelope,
    AutomationJob,
    AutomationJobRun,
    ControlPlaneActionStatus,
    ControlPlaneActor,
    ControlPlaneSurface,
)


class AutomationSchedulerService:
    """恢复 automation jobs，并统一走 control-plane action executor。"""

    def __init__(self, *, control_plane_service: Any, automation_store: Any) -> None:
        self._control_plane_service = control_plane_service
        self._automation_store = automation_store
        self._scheduler = AsyncIOScheduler(timezone="UTC")
        self._issues: dict[str, str] = {}
        self._background_tasks: set[asyncio.Task[None]] = set()

    async def startup(self) -> None:
        if not self._scheduler.running:
            self._scheduler.start()
        for job in self._automation_store.list_jobs():
            await self.sync_job(job)

    async def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        tasks = list(self._background_tasks)
        self._background_tasks.clear()
        for task in tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def sync_job(self, job: AutomationJob) -> None:
        await self.remove_job(job.job_id)
        if not job.enabled:
            self._issues.pop(job.job_id, None)
            return

        try:
            trigger = self._build_trigger(job)
        except ValueError as exc:
            self._issues[job.job_id] = str(exc)
            return

        self._issues.pop(job.job_id, None)
        self._scheduler.add_job(
            self._run_scheduled_job,
            trigger=trigger,
            args=[job.job_id],
            id=job.job_id,
            replace_existing=True,
            misfire_grace_time=30,
        )

    async def remove_job(self, job_id: str) -> None:
        with contextlib.suppress(Exception):
            self._scheduler.remove_job(job_id)
        self._issues.pop(job_id, None)

    def get_next_run(self, job_id: str) -> datetime | None:
        scheduled = self._scheduler.get_job(job_id)
        if scheduled is None:
            return None
        next_run = getattr(scheduled, "next_run_time", None)
        return next_run

    def get_issue(self, job_id: str) -> str:
        return self._issues.get(job_id, "")

    async def run_now(
        self,
        job_id: str,
        *,
        actor: ControlPlaneActor,
    ) -> AutomationJobRun:
        job = self._automation_store.get_job(job_id)
        if job is None:
            raise ValueError("automation job 不存在")
        run = await self._control_plane_service.create_automation_run(
            job=job,
            actor=actor,
        )
        self._spawn_background_run(job, run, actor=actor, manual=True)
        return run

    async def _run_scheduled_job(self, job_id: str) -> None:
        job = self._automation_store.get_job(job_id)
        if job is None or not job.enabled:
            return
        run = await self._control_plane_service.create_automation_run(
            job=job,
            actor=ControlPlaneActor(
                actor_id="system:automation",
                actor_label="Automation Scheduler",
            ),
        )
        await self._execute_job(
            job,
            run,
            actor=ControlPlaneActor(
                actor_id="system:automation",
                actor_label="Automation Scheduler",
            ),
            manual=False,
        )

    def _spawn_background_run(
        self,
        job: AutomationJob,
        run: AutomationJobRun,
        *,
        actor: ControlPlaneActor,
        manual: bool,
    ) -> None:
        task = asyncio.create_task(self._execute_job(job, run, actor=actor, manual=manual))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _execute_job(
        self,
        job: AutomationJob,
        run: AutomationJobRun,
        *,
        actor: ControlPlaneActor,
        manual: bool,
    ) -> None:
        request = ActionRequestEnvelope(
            request_id=run.request_id,
            action_id=job.action_id,
            params=job.params,
            surface=ControlPlaneSurface.SYSTEM,
            actor=ControlPlaneActor(
                actor_id="system:automation",
                actor_label="Automation Scheduler"
                if not manual
                else f"Automation via {actor.actor_label or actor.actor_id}",
            ),
            context={
                "automation_job_id": job.job_id,
                "automation_run_id": run.run_id,
                "trigger_actor_id": actor.actor_id,
                "trigger_actor_label": actor.actor_label,
            },
        )
        result = await self._control_plane_service.execute_action(request)
        if result.status == ControlPlaneActionStatus.COMPLETED:
            status = "succeeded"
            summary = result.message
        elif result.status == ControlPlaneActionStatus.DEFERRED:
            status = "deferred"
            summary = result.message
        else:
            status = "rejected"
            summary = result.message
        await self._control_plane_service.record_automation_run_status(
            run=run,
            status=status,
            summary=summary,
            result_code=result.code,
            resource_refs=result.resource_refs,
        )

    @staticmethod
    def _build_trigger(job: AutomationJob) -> Any:
        if job.schedule_kind.value == "interval":
            try:
                seconds = int(job.schedule_expr)
            except ValueError as exc:
                raise ValueError("interval schedule_expr 必须是秒数整数") from exc
            if seconds <= 0:
                raise ValueError("interval schedule_expr 必须大于 0")
            return IntervalTrigger(seconds=seconds, timezone=job.timezone)

        if job.schedule_kind.value == "cron":
            try:
                return CronTrigger.from_crontab(job.schedule_expr, timezone=job.timezone)
            except ValueError as exc:
                raise ValueError("cron schedule_expr 必须是标准 crontab 表达式") from exc

        if job.schedule_kind.value == "once":
            try:
                run_at = datetime.fromisoformat(job.schedule_expr)
            except ValueError as exc:
                raise ValueError("once schedule_expr 必须是 ISO datetime") from exc
            if run_at.tzinfo is None:
                run_at = run_at.replace(tzinfo=UTC)
            return DateTrigger(run_date=run_at)

        raise ValueError(f"不支持的 schedule_kind: {job.schedule_kind}")
