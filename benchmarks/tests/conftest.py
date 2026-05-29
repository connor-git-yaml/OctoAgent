"""benchmarks/tests/conftest.py

Independent conftest for benchmarks module tests.
不复用 octoagent/conftest.py（FR-H01 零侵入：benchmarks/ 模块自包含）。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 把 octoagent monorepo 各 package 的 src 加入 sys.path（test import 用），
# 避免 benchmarks 测试需要 `octoagent` 模块时找不到。
_WORKTREE_ROOT = Path(__file__).resolve().parent.parent.parent
_OCTO_ROOT = _WORKTREE_ROOT / "octoagent"

if _OCTO_ROOT.exists():
    for pkg_src in [
        _OCTO_ROOT / "packages" / "core" / "src",
        _OCTO_ROOT / "packages" / "memory" / "src",
        _OCTO_ROOT / "packages" / "policy" / "src",
        _OCTO_ROOT / "packages" / "protocol" / "src",
        _OCTO_ROOT / "packages" / "provider" / "src",
        _OCTO_ROOT / "packages" / "skills" / "src",
        _OCTO_ROOT / "apps" / "gateway" / "src",
    ]:
        if pkg_src.exists() and str(pkg_src) not in sys.path:
            sys.path.insert(0, str(pkg_src))

# benchmarks 模块自身需要可 import
if str(_WORKTREE_ROOT) not in sys.path:
    sys.path.insert(0, str(_WORKTREE_ROOT))
