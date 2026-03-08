"""Feature 024 installer bootstrap 核心逻辑。"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from octoagent.core.models import InstallAttempt, InstallStatus, ManagedRuntimeDescriptor, utc_now
from ulid import ULID

from .backup_service import resolve_project_root
from .console_output import create_console, render_panel
from .update_status_store import UpdateStatusStore

console = create_console()


def _run_command(command: list[str], cwd: Path) -> None:
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


def _build_runtime_descriptor(project_root: Path) -> ManagedRuntimeDescriptor:
    now = utc_now()
    port = "8000"
    return ManagedRuntimeDescriptor(
        project_root=str(project_root),
        start_command=[
            "uv",
            "run",
            "uvicorn",
            "octoagent.gateway.main:app",
            "--host",
            "0.0.0.0",
            "--port",
            port,
        ],
        verify_url=f"http://127.0.0.1:{port}/ready?profile=core",
        workspace_sync_command=["uv", "sync"],
        frontend_build_command=["npm", "run", "build"],
        environment_overrides={
            "OCTOAGENT_PROJECT_ROOT": str(project_root),
        },
        created_at=now,
        updated_at=now,
    )


def run_install_bootstrap(
    project_root: Path,
    *,
    force: bool = False,
    skip_frontend: bool = False,
) -> InstallAttempt:
    root = resolve_project_root(project_root).resolve()
    started_at = utc_now()
    attempt = InstallAttempt(
        install_id=str(ULID()),
        project_root=str(root),
        started_at=started_at,
        status=InstallStatus.SUCCEEDED,
    )
    status_store = UpdateStatusStore(root)

    if not (root / "pyproject.toml").exists():
        attempt.status = InstallStatus.FAILED
        attempt.errors.append("项目根缺少 pyproject.toml，无法执行 installer。")
        attempt.completed_at = utc_now()
        return attempt

    try:
        _run_command(["uv", "--version"], root)
        attempt.dependency_checks.append("uv")
        _run_command(["python3", "--version"], root)
        attempt.dependency_checks.append("python3")
    except Exception as exc:
        attempt.status = InstallStatus.FAILED
        attempt.errors.append(str(exc))
        attempt.next_actions.append("先安装 Python 3.12+ 与 uv，再重新执行 installer。")
        attempt.completed_at = utc_now()
        return attempt

    descriptor = status_store.load_runtime_descriptor()
    if descriptor is not None and not force:
        attempt.warnings.append("检测到已有 managed runtime descriptor，跳过重写。")
        attempt.runtime_descriptor_path = str(status_store.descriptor_path)
        attempt.next_actions.extend(
            [
                "运行 octo config init 或确认现有 octoagent.yaml。",
                "运行 octo doctor 检查当前实例。",
            ]
        )
        attempt.completed_at = utc_now()
        return attempt

    try:
        _run_command(["uv", "sync"], root)
        attempt.actions_completed.append("uv sync")
        frontend_root = root / "frontend"
        has_frontend = frontend_root.exists() and (frontend_root / "package.json").exists()
        if has_frontend and not skip_frontend:
            _run_command(["npm", "install"], frontend_root)
            _run_command(["npm", "run", "build"], frontend_root)
            attempt.actions_completed.extend(["npm install", "npm run build"])
        elif skip_frontend:
            attempt.warnings.append("已跳过前端依赖安装与构建。")

        descriptor = _build_runtime_descriptor(root)
        status_store.save_runtime_descriptor(descriptor)
        attempt.runtime_descriptor_path = str(status_store.descriptor_path)
        attempt.actions_completed.append("write managed runtime descriptor")
        attempt.next_actions.extend(
            [
                "运行 octo config init 初始化统一配置。",
                "运行 octo doctor 检查项目健康度。",
                (
                    "使用 uv run uvicorn octoagent.gateway.main:app "
                    "--host 0.0.0.0 --port 8000 启动 gateway。"
                ),
            ]
        )
        attempt.completed_at = utc_now()
        return attempt
    except Exception as exc:
        attempt.status = InstallStatus.FAILED
        attempt.errors.append(str(exc))
        attempt.next_actions.append("修复依赖或权限问题后重新执行 installer。")
        attempt.completed_at = utc_now()
        return attempt


def _format_attempt(attempt: InstallAttempt) -> None:
    lines = [
        f"status: {attempt.status}",
        f"project_root: {attempt.project_root}",
        f"dependency_checks: {', '.join(attempt.dependency_checks) or '-'}",
        f"actions_completed: {', '.join(attempt.actions_completed) or '-'}",
        f"descriptor: {attempt.runtime_descriptor_path or '-'}",
    ]
    if attempt.warnings:
        lines.append("warnings:")
        lines.extend(f"  - {item}" for item in attempt.warnings)
    if attempt.errors:
        lines.append("errors:")
        lines.extend(f"  - {item}" for item in attempt.errors)
    if attempt.next_actions:
        lines.append("next_actions:")
        lines.extend(f"  {idx}. {item}" for idx, item in enumerate(attempt.next_actions, start=1))
    console.print(
        render_panel(
            "Install Bootstrap",
            lines,
            border_style="green" if attempt.status == InstallStatus.SUCCEEDED else "red",
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="OctoAgent installer bootstrap")
    parser.add_argument("--project-root", default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--skip-frontend", action="store_true")
    args = parser.parse_args()

    root = resolve_project_root(Path(args.project_root) if args.project_root else None)
    attempt = run_install_bootstrap(
        root,
        force=args.force,
        skip_frontend=args.skip_frontend,
    )
    _format_attempt(attempt)
    if attempt.status == InstallStatus.FAILED:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
