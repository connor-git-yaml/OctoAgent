from __future__ import annotations

import re
from pathlib import Path


def _local_override_file_id(file_id: str) -> str:
    base = Path(file_id)
    return f"{base.stem}.local{base.suffix}"


def validate_behavior_file_path(project_root: Path, file_path: str) -> Path:
    """校验行为文件路径安全性，返回 resolved 绝对路径。

    规则：
    1. file_path 必须是相对路径（不以 / 开头）
    2. 不允许 .. 路径组件（防止 path traversal）
    3. resolve 后必须在 project_root 内
    4. 必须在 behavior 目录体系内（behavior/ 或 projects/*/behavior/）

    Raises:
        ValueError: 路径不合法或超出安全边界时抛出
    """
    stripped = file_path.strip()
    if not stripped:
        raise ValueError("file_path 不能为空")

    # 拒绝绝对路径
    if stripped.startswith("/") or stripped.startswith("\\"):
        raise ValueError(f"不允许绝对路径: {stripped}")

    # 拒绝 .. 组件
    parts = Path(stripped).parts
    if ".." in parts:
        raise ValueError(f"不允许 path traversal (..): {stripped}")

    resolved = (project_root.resolve() / stripped).resolve()
    root_resolved = project_root.resolve()

    # 确保在 project_root 内
    if not str(resolved).startswith(str(root_resolved) + "/") and resolved != root_resolved:
        raise ValueError(f"路径超出项目根目录: {stripped}")

    # 确保在 behavior 目录体系内
    relative = str(resolved.relative_to(root_resolved))
    in_behavior = relative.startswith("behavior/") or relative.startswith("behavior\\")
    in_project_behavior = bool(
        re.match(r"projects/[^/]+/behavior(/|\\)", relative)
    )
    if not (in_behavior or in_project_behavior):
        raise ValueError(f"路径不在 behavior 目录体系内: {stripped}")

    return resolved
