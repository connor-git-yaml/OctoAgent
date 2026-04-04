"""路径访问策略 — 工具层强制拦截，不可绕过。

解决问题：Agent 的 filesystem 工具能读取 instance root 下的源码（app/）、
API keys（.env.litellm）、认证配置（auth-profiles.json）等敏感文件。

设计：白名单 + 黑名单 + 灰名单三级判定。
- 白名单：当前 project 目录 + behavior/ + skills/ → 自动放行
- 黑名单：app/ + data/ + .env* + auth-* + 配置文件 + bin/ + 其他 project → 直接拒绝
- 灰名单：instance root 外的路径 → 交给 permission check（ASK 审批）
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)


class PathVerdict(StrEnum):
    """路径访问判定结果"""
    ALLOW = "allow"
    DENY = "deny"
    NEEDS_APPROVAL = "needs_approval"


@dataclass(frozen=True)
class PathAccessResult:
    """路径访问检查结果"""
    verdict: PathVerdict
    reason: str


# instance root 下允许 Agent 访问的子目录名（相对 instance root）
_WHITELIST_DIRS = frozenset({
    "behavior",
    "skills",
    "mcp-servers",
})

# instance root 下永远禁止 Agent 访问的路径（目录名或文件名前缀）
_BLACKLIST_DIRS = frozenset({
    "app",
    "data",
    "bin",
})

# instance root 下永远禁止 Agent 访问的文件名模式
_BLACKLIST_FILES = frozenset({
    "octoagent.yaml",
    "litellm-config.yaml",
    "auth-profiles.json",
})

# 以这些前缀开头的文件名永远禁止访问
_BLACKLIST_FILE_PREFIXES = (
    ".env",
)


def check_path_access(
    resolved_path: Path,
    instance_root: Path,
    current_project_slug: str,
) -> PathAccessResult:
    """检查路径访问权限。

    Args:
        resolved_path: 已解析的绝对路径（resolve() 后）
        instance_root: 实例根目录（如 ~/.octoagent）
        current_project_slug: 当前活跃 project 的 slug（空字符串表示无 project）

    Returns:
        PathAccessResult — verdict + reason
    """
    # Case 1: 路径在 instance root 外部 → 灰名单，交给 permission check
    if not _is_within(resolved_path, instance_root):
        return PathAccessResult(
            verdict=PathVerdict.NEEDS_APPROVAL,
            reason="outside_instance_root",
        )

    # Case 2: 路径就是 instance root 本身 → 拒绝（防止 list_dir(".") 暴露全局目录结构）
    if resolved_path == instance_root:
        return PathAccessResult(
            verdict=PathVerdict.DENY,
            reason="instance_root_direct_access",
        )

    # 计算相对于 instance root 的路径
    try:
        rel = resolved_path.relative_to(instance_root)
    except ValueError:
        return PathAccessResult(
            verdict=PathVerdict.NEEDS_APPROVAL,
            reason="outside_instance_root",
        )

    # 取第一级目录名（如 "app", "projects", "behavior"）
    parts = rel.parts
    if not parts:
        return PathAccessResult(
            verdict=PathVerdict.DENY,
            reason="instance_root_direct_access",
        )
    top_dir = parts[0]

    # Case 3: 黑名单目录
    if top_dir in _BLACKLIST_DIRS:
        return PathAccessResult(
            verdict=PathVerdict.DENY,
            reason=f"blacklist_dir:{top_dir}",
        )

    # Case 4: 黑名单文件
    filename = resolved_path.name
    if filename in _BLACKLIST_FILES:
        return PathAccessResult(
            verdict=PathVerdict.DENY,
            reason=f"blacklist_file:{filename}",
        )

    # Case 5: 黑名单文件前缀（.env*）
    for prefix in _BLACKLIST_FILE_PREFIXES:
        if filename.startswith(prefix):
            return PathAccessResult(
                verdict=PathVerdict.DENY,
                reason=f"blacklist_prefix:{prefix}",
            )

    # Case 6: 白名单目录
    if top_dir in _WHITELIST_DIRS:
        return PathAccessResult(
            verdict=PathVerdict.ALLOW,
            reason=f"whitelist_dir:{top_dir}",
        )

    # Case 7: projects 子目��
    if top_dir == "projects":
        if len(parts) < 2:
            # projects/ 目录本身 → 拒绝（防止列出所有 project）
            return PathAccessResult(
                verdict=PathVerdict.DENY,
                reason="projects_root_listing",
            )
        project_name = parts[1]

        # 当前 project → 放行
        if current_project_slug and project_name == current_project_slug:
            return PathAccessResult(
                verdict=PathVerdict.ALLOW,
                reason=f"current_project:{project_name}",
            )

        # 其他 project → 拒绝
        return PathAccessResult(
            verdict=PathVerdict.DENY,
            reason=f"cross_project:{project_name}",
        )

    # Case 8: 未知的 instance root 下子目录/文件 → 拒绝
    logger.debug(
        "path_access_unknown_toplevel",
        top_dir=top_dir,
        path=str(resolved_path),
    )
    return PathAccessResult(
        verdict=PathVerdict.DENY,
        reason=f"unknown_toplevel:{top_dir}",
    )


def _is_within(path: Path, root: Path) -> bool:
    """检查 path 是否在 root 内（含 root 本身）"""
    return path == root or path.is_relative_to(root)
