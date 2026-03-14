"""TaskRunner -- 后台任务调度与恢复

将 LLM 处理任务持久化到 task_jobs 表，支持：
1) 启动时恢复 queued/running 任务
2) 超时监控
3) 避免路由层直接 fire-and-forget
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from octoagent.core.models import (
    TERMINAL_STATES,
    DispatchEnvelope,
    ExecutionConsoleSession,
    ExecutionSessionState,
    NormalizedMessage,
    ResumeFailureType,
    ResumeResult,
    TaskStatus,
    WorkerExecutionStatus,
)
from octoagent.core.store import StoreGroup

from .execution_console import (
    AttachInputResult,
    ExecutionConsoleService,
    ExecutionInputError,
)
from .orchestrator import OrchestratorService
from .resume_engine import ResumeEngine
from .task_service import TaskService
from .worker_runtime import WorkerCancellationRegistry, WorkerRuntimeConfig

log = structlog.get_logger()

_DEFERRED_TASK_STATUSES: dict[TaskStatus, str] = {
    TaskStatus.WAITING_INPUT: "WAITING_INPUT",
    TaskStatus.WAITING_APPROVAL: "WAITING_APPROVAL",
    TaskStatus.PAUSED: "PAUSED",
}
_DEFERRED_JOB_STATUSES = set(_DEFERRED_TASK_STATUSES.values())
_TERMINAL_JOB_STATUSES = {"SUCCEEDED", "FAILED", "REJECTED", "CANCELLED"}


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
        completion_notifier: Callable[[str], Awaitable[None]] | None = None,
        worker_runtime_config: WorkerRuntimeConfig | None = None,
        docker_available_checker=None,
        delegation_plane=None,
    ) -> None:
        self._stores = store_group
        self._sse_hub = sse_hub
        self._llm_service = llm_service
        self._timeout_seconds = timeout_seconds
        self._monitor_interval_seconds = monitor_interval_seconds
        self._completion_notifier = completion_notifier
        self._running_jobs: dict[str, RunningJob] = {}
        self._monitor_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._cancellation_registry = WorkerCancellationRegistry()
        self._execution_console = ExecutionConsoleService(
            store_group=store_group,
            sse_hub=sse_hub,
            approval_manager=approval_manager,
        )
        self._orchestrator = OrchestratorService(
            store_group=store_group,
            sse_hub=sse_hub,
            llm_service=llm_service,
            approval_manager=approval_manager,
            delegation_plane=delegation_plane,
            worker_runtime_config=worker_runtime_config,
            docker_available_checker=docker_available_checker,
            cancellation_registry=self._cancellation_registry,
            execution_console=self._execution_console,
        )
        if delegation_plane is not None:
            delegation_plane.bind_dispatch_scheduler(self.schedule_dispatch_envelope)
        self._resume_engine = ResumeEngine(store_group)

    @property
    def execution_console(self) -> ExecutionConsoleService:
        return self._execution_console

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

        task_service = TaskService(self._stores, self._sse_hub)
        for task_id, running_job in running:
            self._cancellation_registry.cancel(task_id)
            running_job.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await running_job.task
            task = await self._stores.task_store.get_task(task_id)
            deferred_job_status = (
                _DEFERRED_TASK_STATUSES.get(task.status) if task is not None else None
            )
            if deferred_job_status is not None:
                await self._stores.task_job_store.mark_deferred(task_id, deferred_job_status)
            else:
                await self._stores.task_job_store.mark_failed(
                    task_id,
                    "runner_shutdown_cancelled",
                )
                await task_service.mark_running_task_failed_for_recovery(
                    task_id,
                    reason="实例重启或停止时取消了当前执行，请重新发起这条请求。",
                )
                await self._mark_execution_terminal(
                    task_id=task_id,
                    status=ExecutionSessionState.FAILED,
                    message="runner shutdown cancelled execution",
                )
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

    async def launch_child_task(
        self,
        message: NormalizedMessage,
        *,
        model_alias: str | None = None,
    ) -> tuple[str, bool]:
        """创建并启动 child task。"""
        service = TaskService(self._stores, self._sse_hub)
        task_id, created = await service.create_task(message)
        if created:
            await self.enqueue(task_id, message.text, model_alias=model_alias)
        return task_id, created

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
        if jobs:
            await asyncio.gather(*[self._start_job(j.task_id) for j in jobs])

    async def _recover_orphan_running_jobs(self) -> None:
        jobs = await self._stores.task_job_store.list_jobs(["RUNNING"])
        if not jobs:
            return

        service = TaskService(self._stores, self._sse_hub)
        await asyncio.gather(*[self._recover_one_orphan_job(j, service) for j in jobs])

    async def _recover_one_orphan_job(self, job, service: TaskService) -> None:
        task = await service.get_task(job.task_id)
        if task is None:
            await self._stores.task_job_store.mark_failed(
                job.task_id,
                "task_missing_for_recovery",
            )
            return
        if task.status == TaskStatus.SUCCEEDED:
            await self._stores.task_job_store.mark_succeeded(job.task_id)
            await self._notify_completion(job.task_id)
            return
        if task.status == TaskStatus.WAITING_INPUT:
            await self._stores.task_job_store.mark_waiting_input(job.task_id)
            return
        if task.status == TaskStatus.WAITING_APPROVAL:
            await self._stores.task_job_store.mark_waiting_approval(job.task_id)
            return
        if task.status == TaskStatus.PAUSED:
            await self._stores.task_job_store.mark_paused(job.task_id)
            return
        if task.status in TERMINAL_STATES:
            await self._stores.task_job_store.mark_failed(
                job.task_id,
                f"task_terminal_status_{task.status}",
            )
            await self._notify_completion(job.task_id)
            return

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
            return

        await self._stores.task_job_store.mark_failed(
            job.task_id,
            f"gateway_resume_failed:{resume_result.failure_type or 'unknown'}",
        )
        await service.mark_running_task_failed_for_recovery(
            job.task_id,
            reason=f"网关恢复失败: {resume_result.message}",
        )
        await self._notify_completion(job.task_id)

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
        dispatch_envelope: DispatchEnvelope | None = None,
    ) -> None:
        task = asyncio.create_task(
            self._run_job(
                task_id=task_id,
                user_text=user_text,
                model_alias=model_alias,
                resume_from_node=resume_from_node,
                resume_state_snapshot=resume_state_snapshot,
                dispatch_envelope=dispatch_envelope,
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
        await self._execution_console.record_cancel_request(
            task_id=task_id,
            actor="user:web",
            reason="用户取消",
        )
        try:
            await self._orchestrator.record_cancel(
                task_id=task_id,
                reason="用户取消",
                actor="user:web",
            )
        except Exception as exc:  # pragma: no cover - 取消不应因 A2A 审计失败而阻塞
            log.warning(
                "task_runner_a2a_cancel_failed",
                task_id=task_id,
                error_type=type(exc).__name__,
            )
        self._cancellation_registry.cancel(task_id)

        async with self._lock:
            running = self._running_jobs.get(task_id)
        service = TaskService(self._stores, self._sse_hub)
        if running is None:
            job = await self._stores.task_job_store.get_job(task_id)
            if job is not None and job.status in _DEFERRED_JOB_STATUSES:
                await service.mark_running_task_cancelled_for_runtime(
                    task_id,
                    reason="用户取消",
                )
                await self._stores.task_job_store.mark_cancelled(task_id)
                await self._mark_execution_terminal(
                    task_id=task_id,
                    status=ExecutionSessionState.CANCELLED,
                    message="用户取消",
                )
                await self._notify_completion(task_id)
                return True
            return False

        running.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await running.task
        await service.mark_running_task_cancelled_for_runtime(
            task_id,
            reason="用户取消",
        )
        await self._stores.task_job_store.mark_cancelled(task_id)
        await self._mark_execution_terminal(
            task_id=task_id,
            status=ExecutionSessionState.CANCELLED,
            message="用户取消",
        )
        await self._notify_completion(task_id)
        return True

    async def get_execution_session(self, task_id: str) -> ExecutionConsoleSession | None:
        """查询 execution session。"""
        return await self._execution_console.get_session(task_id)

    async def collect_artifacts(self, task_id: str):
        """查询 execution artifacts。"""
        return await self._execution_console.collect_artifacts(task_id)

    async def attach_input(
        self,
        task_id: str,
        text: str,
        *,
        actor: str = "user:web",
        approval_id: str | None = None,
    ) -> AttachInputResult:
        """提交人工输入；若 live waiter 不存在则自动恢复执行。"""
        result = await self._execution_console.attach_input(
            task_id=task_id,
            text=text,
            actor=actor,
            approval_id=approval_id,
        )
        if result.delivered_live:
            async with self._lock:
                running = self._running_jobs.get(task_id)
                if running is not None:
                    running.started_at = datetime.now(UTC)
            return result

        job = await self._stores.task_job_store.get_job(task_id)
        if job is None:
            raise ExecutionInputError(
                "task job missing for input resume",
                code="TASK_JOB_MISSING",
            )

        async with self._lock:
            if task_id in self._running_jobs:
                return result

        await self._stores.task_job_store.mark_running_from_waiting_input(task_id)
        self._cancellation_registry.ensure(task_id)
        await self._spawn_job(
            task_id=task_id,
            user_text=job.user_text,
            model_alias=job.model_alias,
            resume_from_node="state_running",
            resume_state_snapshot={
                "execution_session_id": result.session_id,
                "human_input_artifact_id": result.artifact_id,
                "input_request_id": result.request_id,
            },
        )
        return result

    async def schedule_dispatch_envelope(self, envelope: DispatchEnvelope) -> bool:
        """为预构建 dispatch envelope 重新排队并异步执行。"""
        task_id = envelope.task_id
        async with self._lock:
            if task_id in self._running_jobs:
                return False

        job = await self._stores.task_job_store.get_job(task_id)
        if job is None or job.status in _TERMINAL_JOB_STATUSES:
            created = await self._stores.task_job_store.create_job(
                task_id,
                envelope.user_text,
                envelope.model_alias,
            )
            if not created:
                return False
            marked = await self._stores.task_job_store.mark_running(task_id)
        elif job.status in _DEFERRED_JOB_STATUSES:
            marked = await self._stores.task_job_store.mark_running_from_deferred(task_id)
        elif job.status == "QUEUED":
            marked = await self._stores.task_job_store.mark_running(task_id)
        elif job.status == "RUNNING":
            return False
        else:
            return False

        if not marked:
            return False

        self._cancellation_registry.ensure(task_id)
        await self._spawn_job(
            task_id=task_id,
            user_text=envelope.user_text,
            model_alias=envelope.model_alias,
            dispatch_envelope=envelope,
        )
        return True

    async def _run_job(
        self,
        task_id: str,
        user_text: str,
        model_alias: str | None,
        resume_from_node: str | None = None,
        resume_state_snapshot: dict[str, Any] | None = None,
        dispatch_envelope: DispatchEnvelope | None = None,
    ) -> None:
        service = TaskService(self._stores, self._sse_hub)
        if dispatch_envelope is None:
            metadata = await service.get_latest_user_metadata(task_id)
            result = await self._orchestrator.dispatch(
                task_id=task_id,
                user_text=user_text,
                model_alias=model_alias,
                resume_from_node=resume_from_node,
                resume_state_snapshot=resume_state_snapshot,
                tool_profile=str(metadata.get("tool_profile", "standard")).strip() or "standard",
                metadata=metadata,
            )
        else:
            result = await self._orchestrator.dispatch_prepared(dispatch_envelope)
        task = await service.get_task(task_id)
        if task is None:
            await self._stores.task_job_store.mark_failed(task_id, "task_missing_after_processing")
            return
        if task.status == TaskStatus.SUCCEEDED:
            await self._stores.task_job_store.mark_succeeded(task_id)
            await self._notify_completion(task_id)
            return
        deferred_job_status = _DEFERRED_TASK_STATUSES.get(task.status)
        if deferred_job_status is not None:
            await self._stores.task_job_store.mark_deferred(task_id, deferred_job_status)
            return
        if task.status == TaskStatus.CANCELLED or result.status == WorkerExecutionStatus.CANCELLED:
            await self._stores.task_job_store.mark_cancelled(task_id)
            await self._notify_completion(task_id)
            return
        if task.status in TERMINAL_STATES:
            await self._stores.task_job_store.mark_failed(
                task_id,
                f"task_terminal_status_{task.status}",
            )
            await self._notify_completion(task_id)
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
                task = await service.get_task(task_id)
                if task is not None and task.status in {
                    TaskStatus.WAITING_INPUT,
                    TaskStatus.WAITING_APPROVAL,
                    TaskStatus.PAUSED,
                }:
                    continue
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
                await self._mark_execution_terminal(
                    task_id=task_id,
                    status=ExecutionSessionState.FAILED,
                    message="worker runtime timeout",
                )
                await self._notify_completion(task_id)
                log.warning("task_runner_job_timeout", task_id=task_id)

    async def _notify_completion(self, task_id: str) -> None:
        if self._completion_notifier is None:
            return
        try:
            await self._completion_notifier(task_id)
        except Exception as exc:  # pragma: no cover - 通知链路异常不能影响主流程
            log.warning(
                "task_runner_completion_notifier_failed",
                task_id=task_id,
                error_type=type(exc).__name__,
            )

    async def _mark_execution_terminal(
        self,
        *,
        task_id: str,
        status: ExecutionSessionState,
        message: str,
    ) -> None:
        session = await self._execution_console.get_session(task_id)
        if session is None:
            return
        if session.live is False:
            events = await self._execution_console.list_execution_events(
                task_id,
                session_id=session.session_id,
            )
            latest_status = next(
                (event for event in reversed(events) if event.kind.value == "status"),
                None,
            )
            if latest_status is not None and latest_status.final:
                return
        await self._execution_console.mark_status(
            task_id=task_id,
            session_id=session.session_id,
            status=status,
            message=message,
        )
