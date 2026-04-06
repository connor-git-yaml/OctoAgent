"""Managed runtime descriptor 默认命令与兼容升级。"""

from __future__ import annotations

from octoagent.core.models import ManagedRuntimeDescriptor, utc_now

_LEGACY_WORKSPACE_SYNC_COMMAND = [
    "/bin/bash",
    "-lc",
    "git pull --ff-only origin master && uv sync",
]

_LEGACY_FRONTEND_BUILD_COMMAND = [
    "/bin/bash",
    "-lc",
    "npm install && npm run build",
]


def build_workspace_sync_command() -> list[str]:
    return [
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
    ]


def build_frontend_build_command() -> list[str]:
    return [
        "/bin/bash",
        "-lc",
        "npm ci && npm run build",
    ]


def normalize_runtime_descriptor(
    descriptor: ManagedRuntimeDescriptor,
) -> tuple[ManagedRuntimeDescriptor, bool]:
    updates: dict[str, object] = {}
    if descriptor.workspace_sync_command == _LEGACY_WORKSPACE_SYNC_COMMAND:
        updates["workspace_sync_command"] = build_workspace_sync_command()
    if descriptor.frontend_build_command == _LEGACY_FRONTEND_BUILD_COMMAND:
        updates["frontend_build_command"] = build_frontend_build_command()
    if not updates:
        return descriptor, False
    updates["updated_at"] = utc_now()
    return descriptor.model_copy(update=updates), True
