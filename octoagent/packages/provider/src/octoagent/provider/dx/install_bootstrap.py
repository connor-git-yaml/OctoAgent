"""Feature 024 installer bootstrap 核心逻辑。"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from octoagent.core.models import InstallAttempt, InstallStatus, ManagedRuntimeDescriptor, utc_now
from ulid import ULID

from .backup_service import resolve_project_root
from .config_bootstrap import bootstrap_config
from octoagent.gateway.services.config.config_wizard import load_config
from .console_output import create_console, render_panel
from octoagent.gateway.services.config.litellm_generator import generate_litellm_config
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


def _write_script(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _build_runtime_descriptor(
    project_root: Path,
    *,
    instance_root: Path | None = None,
) -> ManagedRuntimeDescriptor:
    now = utc_now()
    port = "8000"
    environment_overrides: dict[str, str] = {}
    start_command: list[str]
    if instance_root is None:
        environment_overrides["OCTOAGENT_PROJECT_ROOT"] = str(project_root)
        start_command = [
            "uv",
            "run",
            "uvicorn",
            "octoagent.gateway.main:app",
            "--host",
            "0.0.0.0",
            "--port",
            port,
        ]
    else:
        resolved_instance_root = instance_root.expanduser().resolve()
        environment_overrides.update(
            {
                "OCTOAGENT_INSTANCE_ROOT": str(resolved_instance_root),
                "OCTOAGENT_PROJECT_ROOT": str(resolved_instance_root),
                "OCTOAGENT_DATA_DIR": str(resolved_instance_root / "data"),
                "OCTOAGENT_PORT": port,
            }
        )
        start_command = [
            "/bin/bash",
            str(project_root / "scripts" / "run-octo-home.sh"),
        ]
    return ManagedRuntimeDescriptor(
        project_root=str(project_root),
        start_command=start_command,
        verify_url=f"http://127.0.0.1:{port}/ready?profile=core",
        workspace_sync_command=[
            "/bin/bash",
            "-lc",
            "GIT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) && "
            "if [ -n \"$GIT_ROOT\" ]; then "
            "  git -C \"$GIT_ROOT\" fetch origin master && "
            "  if git -C \"$GIT_ROOT\" diff --quiet 2>/dev/null; then "
            "    git -C \"$GIT_ROOT\" merge --ff-only origin/master; "
            "  else "
            "    echo 'Local changes detected, resetting to origin/master...' && "
            "    git -C \"$GIT_ROOT\" checkout -- . && "
            "    git -C \"$GIT_ROOT\" merge --ff-only origin/master; "
            "  fi; "
            "fi && "
            "uv sync",
        ],
        frontend_build_command=[
            "/bin/bash",
            "-lc",
            "npm install && npm run build",
        ],
        environment_overrides=environment_overrides,
        created_at=now,
        updated_at=now,
    )


def _build_env_loader(instance_root: Path) -> str:
    return f"""INSTANCE_ROOT="{instance_root}"

export OCTOAGENT_INSTANCE_ROOT="$INSTANCE_ROOT"
export OCTOAGENT_PROJECT_ROOT="${{OCTOAGENT_PROJECT_ROOT:-$INSTANCE_ROOT}}"
export OCTOAGENT_DATA_DIR="${{OCTOAGENT_DATA_DIR:-$INSTANCE_ROOT/data}}"

if [[ -f "$INSTANCE_ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$INSTANCE_ROOT/.env"
  set +a
fi

if [[ -f "$INSTANCE_ROOT/.env.litellm" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$INSTANCE_ROOT/.env.litellm"
  set +a
fi
"""


def _write_user_launchers(
    source_root: Path,
    instance_root: Path,
) -> list[str]:
    root = instance_root.expanduser().resolve()
    bin_dir = root / "bin"
    env_loader = _build_env_loader(root)
    created: list[str] = []

    launcher_specs = {
        "octo": f"""#!/usr/bin/env bash
set -euo pipefail

SOURCE_ROOT="{source_root}"
{env_loader}
cd "$SOURCE_ROOT"
exec uv run octo "$@"
""",
        "octo-start": f"""#!/usr/bin/env bash
set -euo pipefail

SOURCE_ROOT="{source_root}"
{env_loader}
cd "$SOURCE_ROOT"
exec "$SOURCE_ROOT/scripts/run-octo-home.sh" "$@"
""",
        "octo-doctor": f"""#!/usr/bin/env bash
set -euo pipefail

SOURCE_ROOT="{source_root}"
{env_loader}
cd "$SOURCE_ROOT"
exec "$SOURCE_ROOT/scripts/doctor-octo-home.sh" "$@"
""",
    }

    for name, content in launcher_specs.items():
        launcher_path = bin_dir / name
        _write_script(launcher_path, content)
        created.append(str(launcher_path))

    return created


def _bootstrap_instance_root(
    source_root: Path,
    instance_root: Path,
    *,
    force: bool = False,
) -> tuple[list[str], list[str], list[str]]:
    root = instance_root.expanduser().resolve()
    data_dir = root / "data"
    warnings: list[str] = []
    actions = [
        f"prepare instance root: {root}",
    ]
    next_actions = [
        f"运行 {root / 'bin' / 'octo-start'} 启动个人 Web 实例。",
        f"运行 {root / 'bin' / 'octo-doctor'} 检查个人实例健康度。",
        f"如需真实模型，再执行 {root / 'bin' / 'octo'} setup 完成 provider 配置与启用。",
        f"如需直接使用 CLI，可把 {root / 'bin'} 加入 PATH。",
    ]

    for path in (
        root,
        data_dir,
        data_dir / "sqlite",
        data_dir / "artifacts",
        data_dir / "ops",
        data_dir / "lancedb",
    ):
        path.mkdir(parents=True, exist_ok=True)

    config_path = root / "octoagent.yaml"
    if force or not config_path.exists():
        bootstrap_config(root, echo=True)
        actions.append(f"bootstrap echo config: {config_path}")
    else:
        warnings.append(f"检测到已有实例配置，保留现有 octoagent.yaml：{config_path}")
        try:
            config = load_config(root)
            if config is not None:
                generate_litellm_config(config, root)
                actions.append(f"sync litellm-config.yaml: {root / 'litellm-config.yaml'}")
        except Exception as exc:
            warnings.append(f"现有实例配置未能自动同步 litellm-config.yaml：{exc}")

    for launcher_path in _write_user_launchers(source_root, root):
        actions.append(f"write launcher: {launcher_path}")

    return actions, warnings, next_actions


def run_install_bootstrap(
    project_root: Path,
    *,
    force: bool = False,
    skip_frontend: bool = False,
    instance_root: Path | None = None,
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
    should_skip_runtime_bootstrap = descriptor is not None and not force

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

        if should_skip_runtime_bootstrap:
            attempt.warnings.append("检测到已有 managed runtime descriptor，保留现有描述符。")
            attempt.runtime_descriptor_path = str(status_store.descriptor_path)
            attempt.next_actions.extend(
                [
                    "运行 octo setup 或确认现有 octoagent.yaml。",
                    "运行 octo doctor 检查当前实例。",
                ]
            )
        else:
            descriptor = _build_runtime_descriptor(
                root,
                instance_root=instance_root,
            )
            status_store.save_runtime_descriptor(descriptor)
            if instance_root is not None:
                UpdateStatusStore(instance_root.expanduser().resolve()).save_runtime_descriptor(
                    descriptor
                )
            attempt.runtime_descriptor_path = str(status_store.descriptor_path)
            attempt.actions_completed.append("write managed runtime descriptor")
            if instance_root is None:
                attempt.next_actions.extend(
                    [
                        "运行 octo setup 初始化统一配置。",
                        "运行 octo doctor 检查项目健康度。",
                        (
                            "使用 uv run uvicorn octoagent.gateway.main:app "
                            "--host 0.0.0.0 --port 8000 启动 gateway。"
                        ),
                    ]
                )

        if instance_root is not None:
            instance_actions, instance_warnings, instance_next_actions = _bootstrap_instance_root(
                root,
                instance_root,
                force=force,
            )
            attempt.actions_completed.extend(instance_actions)
            attempt.warnings.extend(instance_warnings)
            attempt.next_actions.extend(instance_next_actions)
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
    parser.add_argument(
        "--instance-root",
        default=None,
        help="额外初始化个人实例根目录（如 ~/.octoagent）",
    )
    args = parser.parse_args()

    root = resolve_project_root(Path(args.project_root) if args.project_root else None)
    attempt = run_install_bootstrap(
        root,
        force=args.force,
        skip_frontend=args.skip_frontend,
        instance_root=Path(args.instance_root).expanduser() if args.instance_root else None,
    )
    _format_attempt(attempt)
    if attempt.status == InstallStatus.FAILED:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
