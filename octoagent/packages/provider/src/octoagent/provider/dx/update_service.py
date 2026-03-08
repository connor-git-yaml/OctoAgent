"""Feature 024 installer / update / restart / verify 领域服务。"""

from __future__ import annotations

import asyncio
import errno
import os
import signal
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx
from octoagent.core.models import (
    ManagedRuntimeDescriptor,
    MigrationStepKind,
    MigrationStepResult,
    RuntimeManagementMode,
    UpdateAttempt,
    UpdateAttemptSummary,
    UpdateOverallStatus,
    UpdatePhaseName,
    UpdatePhaseResult,
    UpdatePhaseStatus,
    UpdateTriggerSource,
    UpgradeFailureReport,
    utc_now,
)
from ulid import ULID

from .backup_service import BackupService, resolve_project_root
from .doctor import CheckStatus, DoctorRunner
from .update_status_store import UpdateStatusStore

PhaseMap = dict[UpdatePhaseName, UpdatePhaseResult]
CommandRunner = Callable[[list[str], Path], str]
WorkerLauncher = Callable[[Path, str], None]


class UpdateActionError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        attempt_id: str | None = None,
        status_code: int = 400,
        exit_code: int = 1,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.attempt_id = attempt_id
        self.status_code = status_code
        self.exit_code = exit_code


class ActiveUpdateError(UpdateActionError):
    def __init__(self, attempt_id: str) -> None:
        super().__init__(
            "UPDATE_ACTIVE_ATTEMPT",
            "当前已有进行中的 update/restart/verify，请稍后重试。",
            attempt_id=attempt_id,
            status_code=409,
        )


def _default_run_command(command: list[str], cwd: Path) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(stderr or f"命令执行失败: {' '.join(command)}")
    return result.stdout.strip()


def _default_launch_worker(project_root: Path, attempt_id: str) -> None:
    env = os.environ.copy()
    env["OCTOAGENT_PROJECT_ROOT"] = str(project_root)
    subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            "-m",
            "octoagent.provider.dx.update_worker",
            "--project-root",
            str(project_root),
            "--attempt-id",
            attempt_id,
        ],
        cwd=project_root,
        env=env,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@dataclass(slots=True)
class PreflightResult:
    blocking_messages: list[str]
    warnings: list[str]
    descriptor: ManagedRuntimeDescriptor | None


class UpdateService:
    def __init__(
        self,
        project_root: Path,
        *,
        status_store: UpdateStatusStore | None = None,
        doctor_factory: Callable[[Path], DoctorRunner] | None = None,
        command_runner: CommandRunner | None = None,
        worker_launcher: WorkerLauncher | None = None,
    ) -> None:
        self._root = resolve_project_root(project_root).resolve()
        self._status_store = status_store or UpdateStatusStore(self._root)
        self._doctor_factory = doctor_factory or (lambda root: DoctorRunner(project_root=root))
        self._command_runner = command_runner or _default_run_command
        self._worker_launcher = worker_launcher or _default_launch_worker

    def load_summary(self) -> UpdateAttemptSummary:
        return self._status_store.load_summary()

    async def preview(self, *, trigger_source: UpdateTriggerSource) -> UpdateAttemptSummary:
        self._ensure_no_active_attempt()
        attempt = self._create_attempt(trigger_source=trigger_source, dry_run=True)
        phases = self._phase_map(attempt)

        preflight = await self._run_preflight(require_managed=True)
        self._finalize_preflight_phase(phases[UpdatePhaseName.PREFLIGHT], preflight)
        if preflight.blocking_messages:
            attempt.overall_status = UpdateOverallStatus.ACTION_REQUIRED
            attempt.failure_report = self._build_failure_report(
                attempt,
                failed_phase=UpdatePhaseName.PREFLIGHT,
                message=preflight.blocking_messages[0],
                instance_state="preflight_blocked",
                last_successful_phase=None,
                suggested_actions=preflight.blocking_messages[1:] or preflight.warnings,
            )
            phases[UpdatePhaseName.MIGRATE].status = UpdatePhaseStatus.SKIPPED
            phases[UpdatePhaseName.RESTART].status = UpdatePhaseStatus.SKIPPED
            phases[UpdatePhaseName.VERIFY].status = UpdatePhaseStatus.SKIPPED
        else:
            self._mark_phase_succeeded(
                phases[UpdatePhaseName.MIGRATE],
                "dry-run 预览完成，真实执行时将运行 migrate registry。",
                migration_steps=self._plan_migration_steps(preflight.descriptor),
            )
            phases[UpdatePhaseName.RESTART].status = UpdatePhaseStatus.SKIPPED
            phases[UpdatePhaseName.RESTART].summary = "dry-run 跳过 restart"
            phases[UpdatePhaseName.VERIFY].status = UpdatePhaseStatus.SKIPPED
            phases[UpdatePhaseName.VERIFY].summary = "dry-run 跳过 verify"
            attempt.overall_status = UpdateOverallStatus.SUCCEEDED

        attempt.completed_at = utc_now()
        self._status_store.save_latest_attempt(attempt)
        return UpdateAttemptSummary.from_attempt(attempt)

    async def apply(
        self,
        *,
        trigger_source: UpdateTriggerSource,
        wait: bool,
    ) -> UpdateAttemptSummary:
        self._ensure_no_active_attempt()
        descriptor = self._status_store.load_runtime_descriptor()
        management_mode = (
            RuntimeManagementMode.MANAGED
            if descriptor is not None
            else RuntimeManagementMode.UNMANAGED
        )
        attempt = self._create_attempt(
            trigger_source=trigger_source,
            dry_run=False,
            management_mode=management_mode,
        )
        attempt.overall_status = UpdateOverallStatus.RUNNING
        self._persist_attempt(attempt)

        if wait:
            final_attempt = await self.execute_attempt(attempt.attempt_id)
            return UpdateAttemptSummary.from_attempt(final_attempt)

        try:
            self._worker_launcher(self._root, attempt.attempt_id)
        except Exception as exc:
            message = f"无法启动后台 update worker: {exc}"
            self._finalize_failure(
                attempt,
                failed_phase=UpdatePhaseName.PREFLIGHT,
                message=message,
                instance_state="worker_launch_failed",
                suggested_actions=[
                    "检查 Python 环境、provider 安装与当前用户的进程启动权限。",
                    "修复后重新执行 octo update 或从 Web recovery 面板重试。",
                ],
            )
            raise UpdateActionError(
                "UPDATE_APPLY_FAILED",
                message,
                attempt_id=attempt.attempt_id,
                status_code=500,
            ) from exc
        return UpdateAttemptSummary.from_attempt(attempt)

    async def restart(self, *, trigger_source: UpdateTriggerSource) -> UpdateAttemptSummary:
        self._ensure_no_active_attempt()
        attempt = self._create_attempt(trigger_source=trigger_source, dry_run=False)
        phases = self._phase_map(attempt)
        self._mark_phase_skipped(phases[UpdatePhaseName.PREFLIGHT], "restart-only 跳过 preflight")
        self._mark_phase_skipped(phases[UpdatePhaseName.MIGRATE], "restart-only 跳过 migrate")
        attempt.current_phase = UpdatePhaseName.RESTART
        attempt.overall_status = UpdateOverallStatus.RUNNING
        self._persist_attempt(attempt)
        try:
            descriptor = self._require_descriptor(attempt.attempt_id)
            self._mark_phase_running(phases[UpdatePhaseName.RESTART], "restart 阶段进行中。")
            self._persist_attempt(attempt)
            await self._run_restart_phase(phases[UpdatePhaseName.RESTART], descriptor)
            self._mark_phase_skipped(phases[UpdatePhaseName.VERIFY], "restart-only 跳过 verify")
            attempt.overall_status = UpdateOverallStatus.SUCCEEDED
            attempt.completed_at = utc_now()
            self._persist_attempt(attempt)
        except UpdateActionError as exc:
            self._finalize_failure(
                attempt,
                failed_phase=UpdatePhaseName.RESTART,
                message=exc.message,
                instance_state="restart_failed",
            )
            raise
        except Exception as exc:
            self._finalize_failure(
                attempt,
                failed_phase=UpdatePhaseName.RESTART,
                message=str(exc),
                instance_state="restart_failed",
            )
        return UpdateAttemptSummary.from_attempt(attempt)

    async def verify(self, *, trigger_source: UpdateTriggerSource) -> UpdateAttemptSummary:
        self._ensure_no_active_attempt()
        attempt = self._create_attempt(trigger_source=trigger_source, dry_run=False)
        phases = self._phase_map(attempt)
        self._mark_phase_skipped(phases[UpdatePhaseName.PREFLIGHT], "verify-only 跳过 preflight")
        self._mark_phase_skipped(phases[UpdatePhaseName.MIGRATE], "verify-only 跳过 migrate")
        self._mark_phase_skipped(phases[UpdatePhaseName.RESTART], "verify-only 跳过 restart")
        attempt.current_phase = UpdatePhaseName.VERIFY
        attempt.overall_status = UpdateOverallStatus.RUNNING
        self._persist_attempt(attempt)
        try:
            descriptor = self._require_descriptor(attempt.attempt_id)
            self._mark_phase_running(phases[UpdatePhaseName.VERIFY], "verify 阶段进行中。")
            self._persist_attempt(attempt)
            await self._run_verify_phase(phases[UpdatePhaseName.VERIFY], descriptor)
            attempt.overall_status = UpdateOverallStatus.SUCCEEDED
            attempt.completed_at = utc_now()
            self._persist_attempt(attempt)
        except UpdateActionError as exc:
            self._finalize_failure(
                attempt,
                failed_phase=UpdatePhaseName.VERIFY,
                message=exc.message,
                instance_state="verify_failed",
            )
            raise
        except Exception as exc:
            self._finalize_failure(
                attempt,
                failed_phase=UpdatePhaseName.VERIFY,
                message=str(exc),
                instance_state="verify_failed",
            )
        return UpdateAttemptSummary.from_attempt(attempt)

    async def execute_attempt(self, attempt_id: str) -> UpdateAttempt:
        attempt = self._load_or_raise_attempt(attempt_id)
        phases = self._phase_map(attempt)
        last_successful_phase: UpdatePhaseName | None = None

        try:
            self._mark_phase_running(
                phases[UpdatePhaseName.PREFLIGHT],
                "preflight 检查进行中。",
            )
            self._persist_attempt(attempt)
            preflight = await self._run_preflight(require_managed=True)
            self._finalize_preflight_phase(phases[UpdatePhaseName.PREFLIGHT], preflight)
            self._persist_attempt(attempt)
            if preflight.blocking_messages:
                raise UpdateActionError(
                    "UPDATE_APPLY_FAILED",
                    preflight.blocking_messages[0],
                    attempt_id=attempt_id,
                )
            last_successful_phase = UpdatePhaseName.PREFLIGHT

            attempt.current_phase = UpdatePhaseName.MIGRATE
            self._mark_phase_running(phases[UpdatePhaseName.MIGRATE], "migrate 阶段进行中。")
            self._persist_attempt(attempt)
            await self._run_migrate_phase(
                phases[UpdatePhaseName.MIGRATE],
                preflight.descriptor,
            )
            last_successful_phase = UpdatePhaseName.MIGRATE
            self._persist_attempt(attempt)

            attempt.current_phase = UpdatePhaseName.RESTART
            self._mark_phase_running(phases[UpdatePhaseName.RESTART], "restart 阶段进行中。")
            self._persist_attempt(attempt)
            await self._run_restart_phase(phases[UpdatePhaseName.RESTART], preflight.descriptor)
            last_successful_phase = UpdatePhaseName.RESTART
            self._persist_attempt(attempt)

            attempt.current_phase = UpdatePhaseName.VERIFY
            self._mark_phase_running(phases[UpdatePhaseName.VERIFY], "verify 阶段进行中。")
            self._persist_attempt(attempt)
            await self._run_verify_phase(phases[UpdatePhaseName.VERIFY], preflight.descriptor)
            last_successful_phase = UpdatePhaseName.VERIFY
            self._persist_attempt(attempt)

            attempt.overall_status = UpdateOverallStatus.SUCCEEDED
            attempt.completed_at = utc_now()
            self._persist_attempt(attempt)
            return attempt
        except Exception as exc:
            failed_phase = attempt.current_phase
            if failed_phase == UpdatePhaseName.PREFLIGHT:
                attempt.overall_status = UpdateOverallStatus.ACTION_REQUIRED
            else:
                attempt.overall_status = UpdateOverallStatus.FAILED
                self._mark_phase_failed(phases[failed_phase], str(exc))
            attempt.failure_report = self._build_failure_report(
                attempt,
                failed_phase=failed_phase,
                message=str(exc),
                instance_state=self._instance_state_for_failure(failed_phase),
                last_successful_phase=last_successful_phase,
            )
            attempt.completed_at = utc_now()
            self._persist_attempt(attempt)
            return attempt

    def _load_or_raise_attempt(self, attempt_id: str) -> UpdateAttempt:
        active_attempt = self._status_store.load_active_attempt()
        if active_attempt is not None and active_attempt.attempt_id == attempt_id:
            return active_attempt
        latest_attempt = self._status_store.load_latest_attempt()
        if latest_attempt is not None and latest_attempt.attempt_id == attempt_id:
            self._status_store.save_active_attempt(latest_attempt)
            return latest_attempt
        raise UpdateActionError(
            "UPDATE_APPLY_FAILED",
            f"未找到 update attempt: {attempt_id}",
            attempt_id=attempt_id,
            status_code=404,
        )

    def _create_attempt(
        self,
        *,
        trigger_source: UpdateTriggerSource,
        dry_run: bool,
        management_mode: RuntimeManagementMode | None = None,
    ) -> UpdateAttempt:
        started_at = utc_now()
        return UpdateAttempt(
            attempt_id=str(ULID()),
            trigger_source=trigger_source,
            dry_run=dry_run,
            management_mode=management_mode or self._management_mode(),
            project_root=str(self._root),
            started_at=started_at,
            overall_status=UpdateOverallStatus.PENDING if dry_run else UpdateOverallStatus.RUNNING,
            current_phase=UpdatePhaseName.PREFLIGHT,
            phases=[
                UpdatePhaseResult(phase=UpdatePhaseName.PREFLIGHT),
                UpdatePhaseResult(phase=UpdatePhaseName.MIGRATE),
                UpdatePhaseResult(phase=UpdatePhaseName.RESTART),
                UpdatePhaseResult(phase=UpdatePhaseName.VERIFY),
            ],
        )

    def _management_mode(self) -> RuntimeManagementMode:
        return (
            RuntimeManagementMode.MANAGED
            if self._status_store.load_runtime_descriptor() is not None
            else RuntimeManagementMode.UNMANAGED
        )

    def _phase_map(self, attempt: UpdateAttempt) -> PhaseMap:
        return {phase.phase: phase for phase in attempt.phases}

    def _persist_attempt(self, attempt: UpdateAttempt) -> None:
        if attempt.overall_status in (
            UpdateOverallStatus.SUCCEEDED,
            UpdateOverallStatus.FAILED,
            UpdateOverallStatus.ACTION_REQUIRED,
        ):
            self._status_store.clear_active_attempt()
        else:
            self._status_store.save_active_attempt(attempt)
        self._status_store.save_latest_attempt(attempt)

    def _ensure_no_active_attempt(self) -> None:
        active = self._status_store.load_active_attempt()
        if active is not None:
            raise ActiveUpdateError(active.attempt_id)

    async def _run_preflight(self, *, require_managed: bool) -> PreflightResult:
        blocking_messages: list[str] = []
        warnings: list[str] = []
        doctor_runner = self._doctor_factory(self._root)
        report = await doctor_runner.run_all_checks(live=False)
        for check in report.checks:
            if check.level.value == "required" and check.status == CheckStatus.FAIL:
                blocking_messages.append(check.fix_hint or check.message)
            elif check.status == CheckStatus.WARN:
                warnings.append(check.message)

        descriptor = self._status_store.load_runtime_descriptor()
        if require_managed and descriptor is None:
            blocking_messages.append(
                "未检测到 managed runtime descriptor，请先执行 scripts/install-octo.sh。"
            )
        return PreflightResult(
            blocking_messages=blocking_messages,
            warnings=warnings,
            descriptor=descriptor,
        )

    def _finalize_preflight_phase(
        self,
        phase: UpdatePhaseResult,
        preflight: PreflightResult,
    ) -> None:
        phase.started_at = utc_now()
        phase.completed_at = utc_now()
        phase.warnings = preflight.warnings
        if preflight.blocking_messages:
            phase.status = UpdatePhaseStatus.BLOCKED
            phase.errors = preflight.blocking_messages
            phase.summary = "preflight 检测到阻塞项。"
            phase.suggested_actions = preflight.blocking_messages
            return
        phase.status = UpdatePhaseStatus.SUCCEEDED
        phase.summary = "preflight 检查通过。"

    def _plan_migration_steps(
        self,
        descriptor: ManagedRuntimeDescriptor | None,
    ) -> list[MigrationStepResult]:
        steps: list[MigrationStepResult] = []
        if descriptor is None:
            return steps
        if descriptor.workspace_sync_command:
            steps.append(
                MigrationStepResult(
                    step_id="workspace-sync",
                    kind=MigrationStepKind.WORKSPACE_SYNC,
                    description="同步 workspace 依赖",
                    status=UpdatePhaseStatus.SKIPPED,
                )
            )
        steps.append(
            MigrationStepResult(
                step_id="config-migrate",
                kind=MigrationStepKind.CONFIG_MIGRATE,
                description="检查统一配置兼容性",
                status=UpdatePhaseStatus.SKIPPED,
            )
        )
        if descriptor.frontend_build_command:
            steps.append(
                MigrationStepResult(
                    step_id="frontend-build",
                    kind=MigrationStepKind.FRONTEND_BUILD,
                    description="重建前端静态资源",
                    status=UpdatePhaseStatus.SKIPPED,
                )
            )
        return steps

    async def _run_migrate_phase(
        self,
        phase: UpdatePhaseResult,
        descriptor: ManagedRuntimeDescriptor | None,
    ) -> None:
        descriptor = descriptor or self._require_descriptor(None)
        steps = self._plan_migration_steps(descriptor)

        for step in steps:
            if step.kind == MigrationStepKind.WORKSPACE_SYNC:
                output = self._command_runner(descriptor.workspace_sync_command, self._root)
                step.status = UpdatePhaseStatus.SUCCEEDED
                step.summary = output or "workspace sync 完成。"
                step.applied_at = utc_now()
            elif step.kind == MigrationStepKind.CONFIG_MIGRATE:
                if (self._root / "octoagent.yaml").exists():
                    step.status = UpdatePhaseStatus.SUCCEEDED
                    step.summary = "已检测到 octoagent.yaml，无需迁移。"
                else:
                    raise RuntimeError("未检测到 octoagent.yaml，当前实例无法安全执行 migrate。")
            elif step.kind == MigrationStepKind.FRONTEND_BUILD:
                frontend_root = self._root / "frontend"
                if frontend_root.exists():
                    output = self._command_runner(descriptor.frontend_build_command, frontend_root)
                    step.status = UpdatePhaseStatus.SUCCEEDED
                    step.summary = output or "frontend build 完成。"
                    step.applied_at = utc_now()
                else:
                    step.status = UpdatePhaseStatus.SKIPPED
                    step.summary = "未检测到 frontend 目录，跳过。"

        phase.migration_steps = steps
        phase.completed_at = utc_now()
        phase.status = UpdatePhaseStatus.SUCCEEDED
        phase.summary = "migrate 阶段完成。"

    async def _run_restart_phase(
        self,
        phase: UpdatePhaseResult,
        descriptor: ManagedRuntimeDescriptor | None,
    ) -> None:
        descriptor = descriptor or self._require_descriptor(None)
        runtime_state = self._status_store.load_runtime_state()
        if runtime_state is not None and runtime_state.pid > 0:
            try:
                os.kill(runtime_state.pid, signal.SIGTERM)
            except ProcessLookupError:
                phase.warnings.append("旧 pid 不存在，继续尝试启动新进程。")
            else:
                stopped = await self._wait_for_pid_exit(runtime_state.pid, timeout_s=5.0)
                if not stopped:
                    raise RuntimeError(
                        f"旧 pid {runtime_state.pid} 在 5 秒内未退出，无法安全执行 restart。"
                    )

        env = os.environ.copy()
        env.update(descriptor.environment_overrides)
        env["OCTOAGENT_PROJECT_ROOT"] = str(self._root)
        process = subprocess.Popen(  # noqa: S603
            descriptor.start_command,
            cwd=self._root,
            env=env,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        await asyncio.sleep(1.0)
        if process.poll() is not None:
            raise RuntimeError(
                f"新进程启动后立即退出，exit code={process.poll()}。"
            )
        phase.completed_at = utc_now()
        phase.status = UpdatePhaseStatus.SUCCEEDED
        phase.summary = "restart 命令已发起。"

    async def _run_verify_phase(
        self,
        phase: UpdatePhaseResult,
        descriptor: ManagedRuntimeDescriptor | None,
    ) -> None:
        descriptor = descriptor or self._require_descriptor(None)
        deadline = asyncio.get_running_loop().time() + 30
        async with httpx.AsyncClient(timeout=5.0) as client:
            while asyncio.get_running_loop().time() < deadline:
                try:
                    response = await client.get(descriptor.verify_url)
                    if response.status_code == 200:
                        payload = response.json()
                        if payload.get("status") in {"ready", "ok"}:
                            phase.completed_at = utc_now()
                            phase.status = UpdatePhaseStatus.SUCCEEDED
                            phase.summary = "verify 通过。"
                            return
                except Exception:
                    pass
                await asyncio.sleep(1)
        raise RuntimeError("升级后 30 秒内未通过 /ready 验证。")

    def _require_descriptor(self, attempt_id: str | None) -> ManagedRuntimeDescriptor:
        descriptor = self._status_store.load_runtime_descriptor()
        if descriptor is None:
            raise UpdateActionError(
                "RESTART_UNAVAILABLE",
                "当前 runtime 未托管，无法执行 restart/update。",
                attempt_id=attempt_id,
            )
        return descriptor

    def _build_failure_report(
        self,
        attempt: UpdateAttempt,
        *,
        failed_phase: UpdatePhaseName,
        message: str,
        instance_state: str,
        last_successful_phase: UpdatePhaseName | None,
        suggested_actions: list[str] | None = None,
    ) -> UpgradeFailureReport:
        recovery_summary = BackupService(self._root).get_recovery_summary()
        latest_backup_path = ""
        latest_recovery_status = ""
        if recovery_summary.latest_backup is not None:
            latest_backup_path = recovery_summary.latest_backup.output_path
        if recovery_summary.latest_recovery_drill is not None:
            latest_recovery_status = recovery_summary.latest_recovery_drill.status.value
        actions = suggested_actions or [
            "查看最新 update summary 与 gateway 日志。",
            "如需恢复，优先参考最近一次 backup / recovery drill 状态。",
        ]
        return UpgradeFailureReport(
            attempt_id=attempt.attempt_id,
            failed_phase=failed_phase,
            last_successful_phase=last_successful_phase,
            message=message,
            instance_state=instance_state,
            suggested_actions=actions,
            latest_backup_path=latest_backup_path,
            latest_recovery_status=latest_recovery_status,
        )

    def _instance_state_for_failure(self, failed_phase: UpdatePhaseName) -> str:
        return {
            UpdatePhaseName.PREFLIGHT: "preflight_blocked",
            UpdatePhaseName.MIGRATE: "migrate_failed",
            UpdatePhaseName.RESTART: "migrated_not_restarted",
            UpdatePhaseName.VERIFY: "restarted_not_verified",
        }[failed_phase]

    def _mark_phase_succeeded(
        self,
        phase: UpdatePhaseResult,
        summary: str,
        *,
        migration_steps: list[MigrationStepResult] | None = None,
    ) -> None:
        phase.started_at = utc_now()
        phase.completed_at = utc_now()
        phase.status = UpdatePhaseStatus.SUCCEEDED
        phase.summary = summary
        if migration_steps is not None:
            phase.migration_steps = migration_steps

    def _mark_phase_running(self, phase: UpdatePhaseResult, summary: str) -> None:
        if phase.started_at is None:
            phase.started_at = utc_now()
        phase.status = UpdatePhaseStatus.RUNNING
        phase.summary = summary

    def _mark_phase_skipped(self, phase: UpdatePhaseResult, summary: str) -> None:
        phase.started_at = utc_now()
        phase.completed_at = utc_now()
        phase.status = UpdatePhaseStatus.SKIPPED
        phase.summary = summary

    def _mark_phase_failed(self, phase: UpdatePhaseResult, message: str) -> None:
        if phase.started_at is None:
            phase.started_at = utc_now()
        phase.completed_at = utc_now()
        if phase.status != UpdatePhaseStatus.BLOCKED:
            phase.status = UpdatePhaseStatus.FAILED
        phase.errors.append(message)
        phase.summary = message

    def _finalize_failure(
        self,
        attempt: UpdateAttempt,
        *,
        failed_phase: UpdatePhaseName,
        message: str,
        instance_state: str,
        suggested_actions: list[str] | None = None,
        last_successful_phase: UpdatePhaseName | None = None,
    ) -> None:
        attempt.overall_status = UpdateOverallStatus.FAILED
        self._mark_phase_failed(self._phase_map(attempt)[failed_phase], message)
        attempt.failure_report = self._build_failure_report(
            attempt,
            failed_phase=failed_phase,
            message=message,
            instance_state=instance_state,
            last_successful_phase=last_successful_phase,
            suggested_actions=suggested_actions,
        )
        attempt.completed_at = utc_now()
        self._persist_attempt(attempt)

    async def _wait_for_pid_exit(self, pid: int, *, timeout_s: float) -> bool:
        deadline = asyncio.get_running_loop().time() + timeout_s
        while asyncio.get_running_loop().time() < deadline:
            if not self._pid_exists(pid):
                return True
            await asyncio.sleep(0.1)
        return not self._pid_exists(pid)

    def _pid_exists(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except OSError as exc:
            return exc.errno != errno.ESRCH
        return True
