"""Worker Runtime（Feature 009）

职责：
1) WorkerSession 运行态管理（loop_step/max_steps/tool_profile/backend）
2) backend 选择（disabled/preferred/required）
3) privileged profile 显式授权 gate
4) 分层超时（先实现 max_exec，first/between 作为配置保留）
5) cancel 信号检查
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import structlog
from octoagent.core.models import (
    DispatchEnvelope,
    ExecutionBackend,
    ExecutionSessionState,
    HumanInputPolicy,
    TaskStatus,
    WorkerExecutionStatus,
    WorkerResult,
    WorkerRuntimeState,
    WorkerSession,
)
from octoagent.core.store import StoreGroup
from ulid import ULID

from .execution_context import ExecutionRuntimeContext
from .task_service import TaskService

log = structlog.get_logger()

_ALLOWED_TOOL_PROFILES = {"minimal", "standard", "privileged"}


class WorkerRuntimeError(RuntimeError):
    """Worker Runtime 基础异常。"""


class WorkerProfileDeniedError(WorkerRuntimeError):
    """工具权限级别被拒绝。"""


class WorkerBackendUnavailableError(WorkerRuntimeError):
    """执行后端不可用。"""


class WorkerRuntimeTimeoutError(WorkerRuntimeError):
    """执行超时。"""


class WorkerRuntimeCancelled(WorkerRuntimeError):
    """收到取消信号。"""


class WorkerBudgetExhaustedError(WorkerRuntimeError):
    """预算耗尽。"""


@dataclass(frozen=True)
class WorkerRuntimeConfig:
    """Worker Runtime 配置。"""

    max_steps: int = 3
    first_output_timeout_seconds: float = 30.0
    between_output_timeout_seconds: float = 15.0
    max_execution_timeout_seconds: float = 180.0
    docker_mode: str = "preferred"  # disabled/preferred/required
    default_tool_profile: str = "standard"
    privileged_approval_key: str = "privileged_approved"

    @classmethod
    def from_env(cls) -> WorkerRuntimeConfig:
        docker_mode = os.environ.get("OCTOAGENT_WORKER_DOCKER_MODE", "preferred").strip().lower()
        if docker_mode not in {"disabled", "preferred", "required"}:
            docker_mode = "preferred"

        profile = (
            os.environ.get("OCTOAGENT_WORKER_DEFAULT_TOOL_PROFILE", "standard")
            .strip()
            .lower()
        )
        if profile not in _ALLOWED_TOOL_PROFILES:
            profile = "standard"

        def _int_env(name: str, default: int) -> int:
            raw = os.environ.get(name)
            if raw is None:
                return default
            try:
                value = int(raw)
            except ValueError:
                return default
            return max(1, value)

        def _float_env(name: str, default: float) -> float:
            raw = os.environ.get(name)
            if raw is None:
                return default
            try:
                value = float(raw)
            except ValueError:
                return default
            return max(0.01, value)

        return cls(
            max_steps=_int_env("OCTOAGENT_WORKER_MAX_STEPS", 3),
            first_output_timeout_seconds=_float_env(
                "OCTOAGENT_WORKER_TIMEOUT_FIRST_OUTPUT_S", 30.0
            ),
            between_output_timeout_seconds=_float_env(
                "OCTOAGENT_WORKER_TIMEOUT_BETWEEN_OUTPUT_S", 15.0
            ),
            max_execution_timeout_seconds=_float_env(
                "OCTOAGENT_WORKER_TIMEOUT_MAX_EXEC_S", 180.0
            ),
            docker_mode=docker_mode,
            default_tool_profile=profile,
            privileged_approval_key=os.environ.get(
                "OCTOAGENT_WORKER_PRIVILEGED_APPROVAL_KEY",
                "privileged_approved",
            ),
        )


class WorkerCancellationRegistry:
    """任务级取消信号注册表。"""

    def __init__(self) -> None:
        self._signals: dict[str, asyncio.Event] = {}

    def ensure(self, task_id: str) -> asyncio.Event:
        signal = self._signals.get(task_id)
        if signal is None:
            signal = asyncio.Event()
            self._signals[task_id] = signal
        return signal

    def get(self, task_id: str) -> asyncio.Event | None:
        return self._signals.get(task_id)

    def cancel(self, task_id: str) -> bool:
        signal = self._signals.get(task_id)
        if signal is None:
            return False
        signal.set()
        return True

    def clear(self, task_id: str) -> None:
        self._signals.pop(task_id, None)


class RuntimeBackend(Protocol):
    """Runtime backend 抽象。"""

    name: str
    supports_stream_progress: bool

    async def execute(
        self,
        *,
        task_service: TaskService,
        envelope: DispatchEnvelope,
        llm_service,
        execution_context: ExecutionRuntimeContext | None = None,
    ) -> None:
        """执行一次 worker 步骤。"""


class InlineRuntimeBackend:
    """默认 inline backend。"""

    name = "inline"
    supports_stream_progress = False

    async def execute(
        self,
        *,
        task_service: TaskService,
        envelope: DispatchEnvelope,
        llm_service,
        execution_context: ExecutionRuntimeContext | None = None,
    ) -> None:
        await task_service.process_task_with_llm(
            task_id=envelope.task_id,
            user_text=envelope.user_text,
            llm_service=llm_service,
            model_alias=envelope.model_alias,
            resume_from_node=envelope.resume_from_node,
            resume_state_snapshot=envelope.resume_state_snapshot,
            execution_context=execution_context,
        )


class DockerRuntimeBackend(InlineRuntimeBackend):
    """Docker backend（M1.5 先接入路由与探测，执行路径复用当前 TaskService）。"""

    name = "docker"


def default_docker_available_checker() -> bool:
    """检测 Docker daemon 可用性。"""
    docker_bin = shutil.which("docker")
    if docker_bin is None:
        return False
    if os.environ.get("OCTOAGENT_WORKER_DOCKER_INFO_CHECK", "0") != "1":
        return True
    try:
        subprocess.run(
            [docker_bin, "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=0.5,
        )
        return True
    except Exception as exc:
        log.debug(
            "docker_availability_check_failed",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return False


class WorkerRuntime:
    """Worker Runtime 执行器。"""

    def __init__(
        self,
        store_group: StoreGroup,
        sse_hub,
        llm_service,
        *,
        config: WorkerRuntimeConfig | None = None,
        docker_available_checker: Callable[[], bool] | None = None,
        cancellation_registry: WorkerCancellationRegistry | None = None,
        execution_console=None,
    ) -> None:
        self._stores = store_group
        self._sse_hub = sse_hub
        self._llm_service = llm_service
        self._config = config or WorkerRuntimeConfig.from_env()
        self._docker_available_checker = (
            docker_available_checker or default_docker_available_checker
        )
        self._cancellation_registry = cancellation_registry
        self._execution_console = execution_console
        self._inline_backend = InlineRuntimeBackend()
        self._docker_backend = DockerRuntimeBackend()

    async def run(self, envelope: DispatchEnvelope, *, worker_id: str) -> WorkerResult:
        profile = self._resolve_tool_profile(envelope)
        session = WorkerSession(
            session_id=str(ULID()),
            dispatch_id=envelope.dispatch_id,
            task_id=envelope.task_id,
            worker_id=worker_id,
            state=WorkerRuntimeState.PENDING,
            loop_step=0,
            max_steps=self._config.max_steps,
            tool_profile=profile,
        )

        task_service = TaskService(self._stores, self._sse_hub)
        cancel_signal = (
            self._cancellation_registry.ensure(envelope.task_id)
            if self._cancellation_registry is not None
            else None
        )

        try:
            self._check_profile_gate(profile, envelope)
            backend = self._select_backend()
            session.backend = backend.name
            session.state = WorkerRuntimeState.RUNNING
            if self._execution_console is not None:
                await self._execution_console.register_session(
                    task_id=envelope.task_id,
                    session_id=session.session_id,
                    backend_job_id=session.dispatch_id,
                    backend=(
                        ExecutionBackend.DOCKER
                        if backend.name == "docker"
                        else ExecutionBackend.INLINE
                    ),
                    interactive=True,
                    input_policy=HumanInputPolicy.EXPLICIT_REQUEST_ONLY,
                    worker_id=worker_id,
                    message="worker runtime selected backend",
                )

            execution_context = (
                ExecutionRuntimeContext(
                    task_id=envelope.task_id,
                    trace_id=envelope.trace_id,
                    session_id=session.session_id,
                    worker_id=worker_id,
                    backend=backend.name,
                    console=self._execution_console,
                    resume_state_snapshot=envelope.resume_state_snapshot,
                )
                if self._execution_console is not None
                else None
            )

            for step in range(1, self._config.max_steps + 1):
                session.loop_step = step
                if self._execution_console is not None:
                    await self._execution_console.emit_step(
                        task_id=envelope.task_id,
                        session_id=session.session_id,
                        step_name=f"loop_step_{step}",
                        summary="worker runtime iteration",
                    )

                if cancel_signal is not None and cancel_signal.is_set():
                    raise WorkerRuntimeCancelled("cancel_signal_received")

                start = time.monotonic()
                try:
                    await self._await_backend_execute(
                        backend=backend,
                        task_service=task_service,
                        envelope=envelope,
                        cancel_signal=cancel_signal,
                        execution_context=execution_context,
                    )
                except TimeoutError as exc:
                    raise WorkerRuntimeTimeoutError("max_exec_timeout") from exc
                finally:
                    elapsed = time.monotonic() - start
                    if (
                        self._config.first_output_timeout_seconds > 0
                        and elapsed > self._config.first_output_timeout_seconds
                    ):
                        log.info(
                            "worker_runtime_first_output_timeout_budget_exceeded",
                            task_id=envelope.task_id,
                            elapsed_s=round(elapsed, 3),
                            threshold_s=self._config.first_output_timeout_seconds,
                        )

                task = await task_service.get_task(envelope.task_id)
                if task is None:
                    return self._failure_result(
                        envelope=envelope,
                        worker_id=worker_id,
                        session=session,
                        retryable=False,
                        summary="task_missing_after_worker_execution",
                        error_type="TaskNotFound",
                        error_message="Task projection not found after worker execution",
                    )

                if task.status == TaskStatus.SUCCEEDED:
                    session.state = WorkerRuntimeState.SUCCEEDED
                    if self._execution_console is not None:
                        await self._execution_console.mark_status(
                            task_id=envelope.task_id,
                            session_id=session.session_id,
                            status=ExecutionSessionState.SUCCEEDED,
                            message="worker execution succeeded",
                        )
                    return WorkerResult(
                        dispatch_id=envelope.dispatch_id,
                        task_id=envelope.task_id,
                        worker_id=worker_id,
                        status=WorkerExecutionStatus.SUCCEEDED,
                        retryable=False,
                        summary="worker_execution_succeeded",
                        loop_step=session.loop_step,
                        max_steps=session.max_steps,
                        backend=session.backend,
                        tool_profile=session.tool_profile,
                    )

                if task.status == TaskStatus.CANCELLED:
                    session.state = WorkerRuntimeState.CANCELLED
                    if self._execution_console is not None:
                        await self._execution_console.mark_status(
                            task_id=envelope.task_id,
                            session_id=session.session_id,
                            status=ExecutionSessionState.CANCELLED,
                            message="worker runtime cancelled",
                        )
                    return WorkerResult(
                        dispatch_id=envelope.dispatch_id,
                        task_id=envelope.task_id,
                        worker_id=worker_id,
                        status=WorkerExecutionStatus.CANCELLED,
                        retryable=False,
                        summary="worker_runtime_cancelled_by_signal",
                        loop_step=session.loop_step,
                        max_steps=session.max_steps,
                        backend=session.backend,
                        tool_profile=session.tool_profile,
                    )

                if task.status == TaskStatus.FAILED:
                    session.state = WorkerRuntimeState.FAILED
                    if self._execution_console is not None:
                        await self._execution_console.mark_status(
                            task_id=envelope.task_id,
                            session_id=session.session_id,
                            status=ExecutionSessionState.FAILED,
                            message="worker execution failed",
                        )
                    return self._failure_result(
                        envelope=envelope,
                        worker_id=worker_id,
                        session=session,
                        retryable=True,
                        summary="worker_execution_terminal:FAILED",
                        error_type="WorkerExecutionFailed",
                        error_message="task status=FAILED",
                    )

            session.state = WorkerRuntimeState.FAILED
            session.budget_exhausted = True
            raise WorkerBudgetExhaustedError("max_steps_exhausted")

        except WorkerRuntimeCancelled as exc:
            session.state = WorkerRuntimeState.CANCELLED
            await task_service.mark_running_task_cancelled_for_runtime(
                envelope.task_id,
                reason="worker runtime收到取消信号",
            )
            if self._execution_console is not None:
                await self._execution_console.mark_status(
                    task_id=envelope.task_id,
                    session_id=session.session_id,
                    status=ExecutionSessionState.CANCELLED,
                    message="worker runtime收到取消信号",
                )
            return WorkerResult(
                dispatch_id=envelope.dispatch_id,
                task_id=envelope.task_id,
                worker_id=worker_id,
                status=WorkerExecutionStatus.CANCELLED,
                retryable=False,
                summary="worker_runtime_cancelled_by_signal",
                error_type=type(exc).__name__,
                error_message=str(exc),
                loop_step=session.loop_step,
                max_steps=session.max_steps,
                backend=session.backend,
                tool_profile=session.tool_profile,
            )
        except WorkerRuntimeTimeoutError as exc:
            session.state = WorkerRuntimeState.TIMED_OUT
            await task_service.mark_running_task_failed_for_recovery(
                envelope.task_id,
                reason=f"worker runtime超时: {exc}",
            )
            if self._execution_console is not None:
                await self._execution_console.mark_status(
                    task_id=envelope.task_id,
                    session_id=session.session_id,
                    status=ExecutionSessionState.FAILED,
                    message="worker runtime timeout",
                )
            return self._failure_result(
                envelope=envelope,
                worker_id=worker_id,
                session=session,
                retryable=True,
                summary="worker_runtime_timeout:max_exec",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
        except WorkerRuntimeError as exc:
            session.state = WorkerRuntimeState.FAILED
            if self._execution_console is not None:
                await self._execution_console.mark_status(
                    task_id=envelope.task_id,
                    session_id=session.session_id,
                    status=ExecutionSessionState.FAILED,
                    message=str(exc),
                )
            return self._failure_result(
                envelope=envelope,
                worker_id=worker_id,
                session=session,
                retryable=False,
                summary="worker_runtime_rejected",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
        except Exception as exc:  # pragma: no cover - 防御性兜底
            session.state = WorkerRuntimeState.FAILED
            if self._execution_console is not None:
                await self._execution_console.mark_status(
                    task_id=envelope.task_id,
                    session_id=session.session_id,
                    status=ExecutionSessionState.FAILED,
                    message=str(exc),
                )
            return self._failure_result(
                envelope=envelope,
                worker_id=worker_id,
                session=session,
                retryable=True,
                summary="worker_runtime_exception",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )

    def _resolve_tool_profile(self, envelope: DispatchEnvelope) -> str:
        raw = (
            envelope.metadata.get("tool_profile")
            or envelope.tool_profile
            or self._config.default_tool_profile
        )
        profile = str(raw).strip().lower()
        if profile not in _ALLOWED_TOOL_PROFILES:
            return self._config.default_tool_profile
        return profile

    def _check_profile_gate(self, profile: str, envelope: DispatchEnvelope) -> None:
        if profile != "privileged":
            return
        approved = envelope.metadata.get(self._config.privileged_approval_key, "")
        if approved.strip().lower() not in {"1", "true", "yes"}:
            raise WorkerProfileDeniedError("privileged profile requires explicit approval")

    def _select_backend(self) -> RuntimeBackend:
        docker_mode = self._config.docker_mode
        docker_available = self._docker_available_checker()

        if docker_mode == "disabled":
            return self._inline_backend

        if docker_available:
            return self._docker_backend

        if docker_mode == "required":
            raise WorkerBackendUnavailableError("docker backend is required but unavailable")
        return self._inline_backend

    async def _await_backend_execute(
        self,
        *,
        backend: RuntimeBackend,
        task_service: TaskService,
        envelope: DispatchEnvelope,
        cancel_signal: asyncio.Event | None,
        execution_context: ExecutionRuntimeContext | None,
    ) -> None:
        backend_task = asyncio.create_task(
            backend.execute(
                task_service=task_service,
                envelope=envelope,
                llm_service=self._llm_service,
                execution_context=execution_context,
            )
        )
        deadline = time.monotonic() + self._config.max_execution_timeout_seconds
        while True:
            if cancel_signal is not None and cancel_signal.is_set():
                backend_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await backend_task
                raise WorkerRuntimeCancelled("cancel_signal_received")
            try:
                await asyncio.wait_for(asyncio.shield(backend_task), timeout=0.1)
                return
            except TimeoutError:
                task = await task_service.get_task(envelope.task_id)
                if task is not None and task.status == TaskStatus.WAITING_INPUT:
                    deadline = time.monotonic() + self._config.max_execution_timeout_seconds
                    continue
                if time.monotonic() > deadline:
                    backend_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await backend_task
                    raise

    @staticmethod
    def _failure_result(
        *,
        envelope: DispatchEnvelope,
        worker_id: str,
        session: WorkerSession,
        retryable: bool,
        summary: str,
        error_type: str,
        error_message: str,
    ) -> WorkerResult:
        return WorkerResult(
            dispatch_id=envelope.dispatch_id,
            task_id=envelope.task_id,
            worker_id=worker_id,
            status=WorkerExecutionStatus.FAILED,
            retryable=retryable,
            summary=summary,
            error_type=error_type,
            error_message=error_message,
            loop_step=session.loop_step,
            max_steps=session.max_steps,
            backend=session.backend,
            tool_profile=session.tool_profile,
        )
