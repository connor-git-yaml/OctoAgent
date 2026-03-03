"""TaskRunner -- 后台任务调度与恢复

将 LLM 处理任务持久化到 task_jobs 表，支持：
1) 启动时恢复 queued/running 任务
2) 超时监控
3) 避免路由层直接 fire-and-forget
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from octoagent.core.models import (
    TERMINAL_STATES,
    ResumeFailureType,
    ResumeResult,
    TaskStatus,
    WorkerExecutionStatus,
)
from octoagent.core.store import StoreGroup

from .orchestrator import OrchestratorService
from .resume_engine import ResumeEngine
from .task_service import TaskService
from .worker_runtime import WorkerCancellationRegistry, WorkerRuntimeConfig

log = structlog.get_logger()


@dataclass
class RunningJob:
    task: asyncio.Task[None]
    started_at: datetime


class TaskRunner:
    """后台任务执行器（带持久化恢复）"""

    def __init__(
        self,
        store_group: StoreGroup,
        sse_hub,
        llm_service,
        approval_manager=None,
        timeout_seconds: float = 600.0,
        monitor_interval_seconds: float = 5.0,
        worker_runtime_config: WorkerRuntimeConfig | None = None,
        docker_available_checker=None,
    ) -> None:
        self._stores = store_group
        self._sse_hub = sse_hub
        self._llm_service = llm_service
        self._timeout_seconds = timeout_seconds
        self._monitor_interval_seconds = monitor_interval_seconds
        self._running_jobs: dict[str, RunningJob] = {}
        self._monitor_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._cancellation_registry = WorkerCancellationRegistry()
        self._orchestrator = OrchestratorService(
            store_group=store_group,
            sse_hub=sse_hub,
            llm_service=llm_service,
            approval_manager=approval_manager,
            worker_runtime_config=worker_runtime_config,
            docker_available_checker=docker_available_checker,
            cancellation_registry=self._cancellation_registry,
        )
        self._resume_engine = ResumeEngine(store_group)

    async def startup(self) -> None:
        """启动恢复：清理 orphan running + 拉起 queued"""
        await self._recover_orphan_running_jobs()
        await self._dispatch_queued_jobs()
        if self._monitor_task is None or self._monitor_task.done():
            self._monitor_task = asyncio.create_task(self._monitor_loop())

    async def shutdown(self) -> None:
        """停止监控并取消在途任务"""
        if self._monitor_task is not None:
            self._monitor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._monitor_task
            self._monitor_task = None

        async with self._lock:
            running = list(self._running_jobs.items())
            self._running_jobs.clear()

        for task_id, running_job in running:
            self._cancellation_registry.cancel(task_id)
            running_job.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await running_job.task
            await self._stores.task_job_store.mark_failed(task_id, "runner_shutdown_cancelled")
            self._cancellation_registry.clear(task_id)

    async def enqueue(self, task_id: str, user_text: str, model_alias: str | None = None) -> None:
        """入队并尝试启动执行"""
        created = await self._stores.task_job_store.create_job(
            task_id=task_id,
            user_text=user_text,
            model_alias=model_alias,
        )
        if not created:
            return
        await self._start_job(task_id)

    async def resume_task(self, task_id: str, trigger: str = "manual") -> ResumeResult:
        """手动触发恢复并在成功时启动执行。"""
        job = await self._stores.task_job_store.get_job(task_id)
        if job is None:
            return ResumeResult(
                ok=False,
                task_id=task_id,
                failure_type=ResumeFailureType.DEPENDENCY_MISSING,
                message="task_jobs 中不存在可恢复任务记录",
            )

        resume_result = await self._resume_engine.try_resume(task_id, trigger=trigger)
        if not resume_result.ok:
            return resume_result

        if job.status == "QUEUED":
            marked = await self._stores.task_job_store.mark_running(task_id)
            if not marked:
                return ResumeResult(
                    ok=False,
                    task_id=task_id,
                    failure_type=ResumeFailureType.LEASE_CONFLICT,
                    message="任务未能切换到 RUNNING，可能被其他执行器接管",
                )

        self._cancellation_registry.ensure(task_id)
        await self._spawn_job(
            task_id=task_id,
            user_text=job.user_text,
            model_alias=job.model_alias,
            resume_from_node=resume_result.resumed_from_node,
            resume_state_snapshot=resume_result.state_snapshot,
        )
        return resume_result

    async def _dispatch_queued_jobs(self) -> None:
        jobs = await self._stores.task_job_store.list_jobs(["QUEUED"])
        for job in jobs:
            await self._start_job(job.task_id)

    async def _recover_orphan_running_jobs(self) -> None:
        jobs = await self._stores.task_job_store.list_jobs(["RUNNING"])
        if not jobs:
            return

        service = TaskService(self._stores, self._sse_hub)
        for job in jobs:
            task = await service.get_task(job.task_id)
            if task is None:
                await self._stores.task_job_store.mark_failed(
                    job.task_id,
                    "task_missing_for_recovery",
                )
                continue
            if task.status == TaskStatus.SUCCEEDED:
                await self._stores.task_job_store.mark_succeeded(job.task_id)
                continue
            if task.status in TERMINAL_STATES:
                await self._stores.task_job_store.mark_failed(
                    job.task_id,
                    f"task_terminal_status_{task.status}",
                )
                continue

            resume_result = await self._resume_engine.try_resume(job.task_id, trigger="startup")
            if resume_result.ok:
                self._cancellation_registry.ensure(job.task_id)
                await self._spawn_job(
                    task_id=job.task_id,
                    user_text=job.user_text,
                    model_alias=job.model_alias,
                    resume_from_node=resume_result.resumed_from_node,
                    resume_state_snapshot=resume_result.state_snapshot,
                )
                continue

            await self._stores.task_job_store.mark_failed(
                job.task_id,
                f"gateway_resume_failed:{resume_result.failure_type or 'unknown'}",
            )
            await service.mark_running_task_failed_for_recovery(
                job.task_id,
                reason=f"网关恢复失败: {resume_result.message}",
            )

    async def _start_job(self, task_id: str) -> None:
        async with self._lock:
            if task_id in self._running_jobs:
                return

        marked = await self._stores.task_job_store.mark_running(task_id)
        if not marked:
            return
        self._cancellation_registry.ensure(task_id)

        job = await self._stores.task_job_store.get_job(task_id)
        if job is None:
            await self._stores.task_job_store.mark_failed(task_id, "job_missing_after_mark_running")
            return

        await self._spawn_job(
            task_id=job.task_id,
            user_text=job.user_text,
            model_alias=job.model_alias,
        )

    async def _spawn_job(
        self,
        task_id: str,
        user_text: str,
        model_alias: str | None,
        resume_from_node: str | None = None,
        resume_state_snapshot: dict[str, Any] | None = None,
    ) -> None:
        task = asyncio.create_task(
            self._run_job(
                task_id=task_id,
                user_text=user_text,
                model_alias=model_alias,
                resume_from_node=resume_from_node,
                resume_state_snapshot=resume_state_snapshot,
            )
        )
        async with self._lock:
            self._running_jobs[task_id] = RunningJob(
                task=task,
                started_at=datetime.now(UTC),
            )
        task.add_done_callback(lambda t, tid=task_id: asyncio.create_task(self._on_done(tid)))

    async def _on_done(self, task_id: str) -> None:
        async with self._lock:
            self._running_jobs.pop(task_id, None)
        self._cancellation_registry.clear(task_id)

    async def cancel_task(self, task_id: str) -> bool:
        """通知运行中任务取消。"""
        self._cancellation_registry.cancel(task_id)

        async with self._lock:
            running = self._running_jobs.get(task_id)
        if running is None:
            return False

        running.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await running.task
        service = TaskService(self._stores, self._sse_hub)
        await service.mark_running_task_cancelled_for_runtime(
            task_id,
            reason="用户取消",
        )
        await self._stores.task_job_store.mark_cancelled(task_id)
        return True

    async def _run_job(
        self,
        task_id: str,
        user_text: str,
        model_alias: str | None,
        resume_from_node: str | None = None,
        resume_state_snapshot: dict[str, Any] | None = None,
    ) -> None:
        service = TaskService(self._stores, self._sse_hub)
        result = await self._orchestrator.dispatch(
            task_id=task_id,
            user_text=user_text,
            model_alias=model_alias,
            resume_from_node=resume_from_node,
            resume_state_snapshot=resume_state_snapshot,
        )
        task = await service.get_task(task_id)
        if task is None:
            await self._stores.task_job_store.mark_failed(task_id, "task_missing_after_processing")
            return
        if task.status == TaskStatus.SUCCEEDED:
            await self._stores.task_job_store.mark_succeeded(task_id)
            return
        if task.status == TaskStatus.CANCELLED or result.status == WorkerExecutionStatus.CANCELLED:
            await self._stores.task_job_store.mark_cancelled(task_id)
            return
        if task.status in TERMINAL_STATES:
            await self._stores.task_job_store.mark_failed(
                task_id,
                f"task_terminal_status_{task.status}",
            )
            return
        await self._stores.task_job_store.mark_failed(
            task_id,
            f"task_left_non_terminal_status_{task.status}",
        )

    async def _monitor_loop(self) -> None:
        while True:
            await asyncio.sleep(self._monitor_interval_seconds)
            threshold = datetime.now(UTC) - timedelta(seconds=self._timeout_seconds)
            timed_out_ids: list[str] = []
            async with self._lock:
                for task_id, running in self._running_jobs.items():
                    if running.started_at < threshold:
                        timed_out_ids.append(task_id)

            if not timed_out_ids:
                continue

            service = TaskService(self._stores, self._sse_hub)
            for task_id in timed_out_ids:
                async with self._lock:
                    running = self._running_jobs.get(task_id)
                if running is None:
                    continue

                self._cancellation_registry.cancel(task_id)
                running.task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await running.task

                await self._stores.task_job_store.mark_failed(
                    task_id,
                    f"job_timeout_after_{int(self._timeout_seconds)}s",
                )
                await service.mark_running_task_failed_for_recovery(
                    task_id,
                    reason=f"后台任务超时（>{int(self._timeout_seconds)}s）",
                )
                log.warning("task_runner_job_timeout", task_id=task_id)
