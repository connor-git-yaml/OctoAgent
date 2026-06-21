"""code plugin 审批持久化（.approved marker 记 code_hash）（F106 Phase B）。

审批 = 用户独立 human-initiated 动作（REST POST /approve），绑定 plugin 整树 code_hash。
持久化为 plugin 目录下 .approved marker（内容 = code_hash），文件系统 SoT，跨重启。
bootstrap 仅自动加载 .approved 存在且记录 hash == 当前整树 hash 的 code plugin（FR-2.3）。
hash 不匹配（换码）→ 视为未审批 → pending_approval（闭合"审批一次后换码"洞）。

纯文件操作，无 gateway/core 依赖。
"""

from __future__ import annotations

from pathlib import Path

from .manifest import PLUGIN_APPROVED_MARKER


def read_approval(plugin_dir: Path) -> str | None:
    """读 .approved marker 记录的 code_hash；不存在/读失败返回 None。"""
    marker = plugin_dir / PLUGIN_APPROVED_MARKER
    if not marker.is_file():
        return None
    try:
        return marker.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def write_approval(plugin_dir: Path, code_hash: str) -> None:
    """写 .approved marker = code_hash（审批通过）。"""
    (plugin_dir / PLUGIN_APPROVED_MARKER).write_text(code_hash, encoding="utf-8")


def clear_approval(plugin_dir: Path) -> None:
    """清除审批（换码检测 / 卸载）。"""
    try:
        (plugin_dir / PLUGIN_APPROVED_MARKER).unlink(missing_ok=True)
    except OSError:
        pass


def is_approved(plugin_dir: Path, current_hash: str) -> bool:
    """当前 code_hash 是否已审批（marker 存在且 hash 精确匹配）。"""
    approved = read_approval(plugin_dir)
    return approved is not None and approved == current_hash
