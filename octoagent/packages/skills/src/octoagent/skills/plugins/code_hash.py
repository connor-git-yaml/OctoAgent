"""整树 code_hash（F106 Phase B；审批绑定 + 换码检测）。

审批绑定 plugin **整个目录树**的 hash（全文件排序 path+content），**非仅 .py**——
防 .so/.pyc/data 文件换码绕过重审（review H6）。换码（任一文件变）→ hash 变 →
强制重新审批（spec FR-2.3 / DP-6）。

残余（spec §0.3）：runtime-fetch / eval 远程内容是静态 hash 无法覆盖的——
code_hash 闭合"磁盘文件换码"洞，不闭合"已审批代码运行时拉取并执行"。
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .manifest import PLUGIN_APPROVED_MARKER, PLUGIN_DISABLED_MARKER

# 不计入 hash 的目录/文件：
# - loader 管理的状态 marker（.disabled/.approved）：toggle/approve 不应改 code_hash
# - .git/：git 元数据随 pull 变，不是 plugin 代码本身（provenance 另读 commit）
# - __pycache__/：派生字节码，随运行变，不稳定（其源 .py 已计入；__pycache__ 存在性由分类捕获）
_HASH_EXCLUDE_DIRS: frozenset[str] = frozenset({".git", "__pycache__"})
_HASH_EXCLUDE_NAMES: frozenset[str] = frozenset(
    {PLUGIN_DISABLED_MARKER, PLUGIN_APPROVED_MARKER}
)


def compute_tree_hash(plugin_dir: Path) -> str:
    """计算 plugin 目录树的稳定 sha256（全文件排序 path+content）。

    排序保证确定性；path 计入 hash 防"重命名同内容文件"绕过。排除 loader marker /
    .git / __pycache__（见模块 docstring）。

    Args:
        plugin_dir: plugin 根目录。

    Returns:
        64 字符 hex sha256；目录不存在/无文件返回空树 hash。
    """
    h = hashlib.sha256()
    entries: list[Path] = []
    for f in plugin_dir.rglob("*"):
        if any(part in _HASH_EXCLUDE_DIRS for part in f.relative_to(plugin_dir).parts):
            continue
        if f.name in _HASH_EXCLUDE_NAMES:
            continue
        if f.is_symlink():
            # 不跳过——fold symlink 的 path+target 进 hash（review H-1 defense-in-depth：
            # 跳过会让 symlink retarget 绕过重审；plugin 一般已在校验期被 validate_no_symlinks 拒）。
            entries.append(f)
            continue
        if not f.is_file():
            continue
        entries.append(f)

    for f in sorted(entries, key=lambda p: p.relative_to(plugin_dir).as_posix()):
        rel = f.relative_to(plugin_dir).as_posix()
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        if f.is_symlink():
            h.update(b"<symlink>\0")
            try:
                h.update(os.readlink(f).encode("utf-8", "surrogatepass"))
            except OSError:
                h.update(b"<unreadable-link>")
        else:
            try:
                h.update(f.read_bytes())
            except OSError:
                # 读失败计入 hash（变化即触发重审，fail-safe）
                h.update(b"<unreadable>")
        h.update(b"\0")

    return h.hexdigest()
