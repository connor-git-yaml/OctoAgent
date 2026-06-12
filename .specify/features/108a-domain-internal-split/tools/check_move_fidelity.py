#!/usr/bin/env python3
"""F108a 字节级对账工具（F113 范式的自动化版）。

验证"文件拆分/搬运"重构的保真度：原文件的每个顶层符号（def / class /
模块级赋值）必须在新位置以**逐字节相同**的源码段出现，且无丢失、无新增、
无重复（显式豁免除外）。

用法：
    python check_move_fidelity.py --original <file-or-gitref> --moved <file-or-dir>... \
        [--allow-dup name1,name2] [--allow-new name1,...] [--allow-missing name1,...]

- --original 支持 "git:<ref>:<path>"（如 git:d6148903:octoagent/packages/.../behavior_workspace.py）
  或本地文件路径。
- --moved 可以是多个文件或目录（目录递归 *.py；自动跳过 __init__.py，对其单独做
  "仅 import/docstring/__all__" 检查）。
- import 行（ast.Import/ImportFrom）与模块 docstring 不参与对账（唯一结构性豁免）。
- 退出码：0 = 全部通过；1 = 有差异（打印明细）。
"""

from __future__ import annotations

import argparse
import ast
import difflib
import subprocess
import sys
from pathlib import Path

DEFAULT_ALLOW_DUP = {"log"}  # 各模块独立 `log = structlog.get_logger()` 属预期


def _read_original(spec: str, cwd: Path) -> str:
    if spec.startswith("git:"):
        _, ref, path = spec.split(":", 2)
        out = subprocess.run(
            ["git", "show", f"{ref}:{path}"],
            cwd=cwd, capture_output=True, text=True, check=True,
        )
        return out.stdout
    return Path(spec).read_text(encoding="utf-8")


def _node_name(node: ast.stmt) -> str:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return node.name
    if isinstance(node, ast.Assign):
        names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        return ",".join(names) if names else f"<assign@{node.lineno}>"
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return node.target.id
    return f"<{type(node).__name__}@{node.lineno}>"


def _is_docstring(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def top_level_segments(source: str) -> dict[str, list[str]]:
    """提取顶层符号 -> 源码段（含装饰器，不含 import / docstring）。"""
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    segs: dict[str, list[str]] = {}
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)) or _is_docstring(node):
            continue
        if isinstance(node, ast.If):
            # TYPE_CHECKING 块等顶层 if：整块对账
            name = f"<if@{node.lineno}>"
        else:
            name = _node_name(node)
        start = node.lineno
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.decorator_list:
            start = min(d.lineno for d in node.decorator_list)
        seg = "".join(lines[start - 1 : node.end_lineno])
        segs.setdefault(name, []).append(seg)
    return segs


def check_init_is_reexport_only(path: Path) -> list[str]:
    """__init__.py 只允许 docstring / import / __all__ 赋值。返回违规描述。"""
    problems: list[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)) or _is_docstring(node):
            continue
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets
        ):
            continue
        problems.append(f"{path.name}:{node.lineno} 非 re-export 语句: {type(node).__name__}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--original", required=True)
    ap.add_argument("--moved", nargs="+", required=True)
    ap.add_argument("--allow-dup", default="")
    ap.add_argument("--allow-new", default="")
    ap.add_argument("--allow-missing", default="")
    ap.add_argument("--cwd", default=".")
    args = ap.parse_args()

    cwd = Path(args.cwd).resolve()
    allow_dup = DEFAULT_ALLOW_DUP | {s for s in args.allow_dup.split(",") if s}
    allow_new = {s for s in args.allow_new.split(",") if s}
    allow_missing = {s for s in args.allow_missing.split(",") if s}

    orig_src = _read_original(args.original, cwd)
    orig = top_level_segments(orig_src)

    moved_files: list[Path] = []
    init_problems: list[str] = []
    for spec in args.moved:
        p = Path(spec)
        if p.is_dir():
            for f in sorted(p.rglob("*.py")):
                if f.name == "__init__.py":
                    init_problems.extend(check_init_is_reexport_only(f))
                else:
                    moved_files.append(f)
        elif p.name == "__init__.py":
            init_problems.extend(check_init_is_reexport_only(p))
        else:
            moved_files.append(p)

    moved: dict[str, list[tuple[str, str]]] = {}
    for f in moved_files:
        for name, seglist in top_level_segments(f.read_text(encoding="utf-8")).items():
            for seg in seglist:
                moved.setdefault(name, []).append((str(f), seg))

    failures: list[str] = []

    for name, seglist in orig.items():
        if name in allow_missing:
            continue
        if name not in moved:
            failures.append(f"[MISSING] {name} 不在任何新模块中")
            continue
        for orig_seg in seglist:
            matches = [seg for _, seg in moved[name] if seg.rstrip("\n") == orig_seg.rstrip("\n")]
            if not matches:
                locs = ", ".join(loc for loc, _ in moved[name])
                diff = "".join(
                    difflib.unified_diff(
                        orig_seg.splitlines(keepends=True),
                        moved[name][0][1].splitlines(keepends=True),
                        fromfile=f"original:{name}",
                        tofile=f"moved:{name} ({locs})",
                        n=2,
                    )
                )
                failures.append(f"[DIFF] {name} 源码段不一致 @ {locs}\n{diff[:2000]}")

    for name, entries in moved.items():
        if name not in orig and name not in allow_new:
            locs = ", ".join(loc for loc, _ in entries)
            failures.append(f"[NEW] {name} 是新增符号（未在原文件中）@ {locs}")
        if name in orig and len(entries) > len(orig[name]) and name not in allow_dup:
            locs = ", ".join(loc for loc, _ in entries)
            failures.append(f"[DUP] {name} 在新模块中出现 {len(entries)} 次（原 {len(orig[name])} 次）@ {locs}")

    failures.extend(init_problems)

    print(f"原文件顶层符号: {sum(len(v) for v in orig.values())}（{len(orig)} 个名字）")
    print(f"新模块顶层符号: {sum(len(v) for v in moved.values())}（{len(moved)} 个名字，跨 {len(moved_files)} 文件）")
    if failures:
        print(f"\n❌ 对账失败 {len(failures)} 项：\n")
        for f in failures:
            print(f"  {f}\n")
        return 1
    print("✅ 字节级对账通过（豁免：import 行 / 模块 docstring / "
          f"allow_dup={sorted(allow_dup)} / allow_new={sorted(allow_new) or '∅'} / allow_missing={sorted(allow_missing) or '∅'}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
