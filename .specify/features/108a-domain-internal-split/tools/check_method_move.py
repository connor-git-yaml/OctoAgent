#!/usr/bin/env python3
"""F108a 方法级字节对账（W3-W5 用）：类内方法在 主类↔mixin 间搬运的保真验证。

提取每个文件中**所有 class 的所有方法**（含装饰器、含 async）的源码段，
按方法名比对原文件 vs 新文件集合：每个原方法必须在新集合中以逐字节相同
的源码段出现（类内缩进级一致时直接 byte-equal）。

用法：
    python check_method_move.py --original git:<ref>:<path> --moved <file>... \
        [--allow-new m1,m2] [--allow-missing m1] [--allow-dup m1]

import 行 / 模块 docstring / 类 docstring / 类头 / 模块级符号不参与（模块级
符号用 check_move_fidelity.py 另行对账）。
退出码 0 = 通过。
"""

from __future__ import annotations

import argparse
import ast
import difflib
import subprocess
import sys
from pathlib import Path


def _read(spec: str, cwd: Path) -> str:
    if spec.startswith("git:"):
        _, ref, path = spec.split(":", 2)
        out = subprocess.run(
            ["git", "show", f"{ref}:{path}"],
            cwd=cwd, capture_output=True, text=True, check=True,
        )
        return out.stdout
    return Path(spec).read_text(encoding="utf-8")


def method_segments(source: str) -> dict[str, list[str]]:
    """提取所有 class 内一级方法 名字 -> 源码段（含装饰器）。"""
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    segs: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for item in node.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            start = item.lineno
            if item.decorator_list:
                start = min(d.lineno for d in item.decorator_list)
            seg = "".join(lines[start - 1 : item.end_lineno])
            segs.setdefault(item.name, []).append(seg)
    return segs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--original", required=True)
    ap.add_argument("--moved", nargs="+", required=True)
    ap.add_argument("--allow-new", default="")
    ap.add_argument("--allow-missing", default="")
    ap.add_argument("--allow-dup", default="")
    ap.add_argument("--cwd", default=".")
    args = ap.parse_args()

    cwd = Path(args.cwd).resolve()
    allow_new = {s for s in args.allow_new.split(",") if s}
    allow_missing = {s for s in args.allow_missing.split(",") if s}
    allow_dup = {s for s in args.allow_dup.split(",") if s}

    orig = method_segments(_read(args.original, cwd))
    moved: dict[str, list[tuple[str, str]]] = {}
    for spec in args.moved:
        for name, seglist in method_segments(_read(spec, cwd)).items():
            for seg in seglist:
                moved.setdefault(name, []).append((spec, seg))

    failures: list[str] = []
    for name, seglist in orig.items():
        if name in allow_missing:
            continue
        if name not in moved:
            failures.append(f"[MISSING] 方法 {name} 不在任何新文件中")
            continue
        for orig_seg in seglist:
            if not any(seg.rstrip("\n") == orig_seg.rstrip("\n") for _, seg in moved[name]):
                locs = ", ".join(loc for loc, _ in moved[name])
                diff = "".join(difflib.unified_diff(
                    orig_seg.splitlines(keepends=True),
                    moved[name][0][1].splitlines(keepends=True),
                    fromfile=f"original:{name}", tofile=f"moved:{name} ({locs})", n=2,
                ))
                failures.append(f"[DIFF] 方法 {name} 源码段不一致 @ {locs}\n{diff[:1500]}")

    for name, entries in moved.items():
        if name not in orig and name not in allow_new:
            locs = ", ".join(loc for loc, _ in entries)
            failures.append(f"[NEW] 方法 {name} 新增（未在原文件）@ {locs}")
        if name in orig and len(entries) > len(orig[name]) and name not in allow_dup:
            failures.append(f"[DUP] 方法 {name}：新 {len(entries)} 次 vs 原 {len(orig[name])} 次")

    print(f"原方法数: {sum(len(v) for v in orig.values())}（{len(orig)} 名字）")
    print(f"新方法数: {sum(len(v) for v in moved.values())}（{len(moved)} 名字，跨 {len(args.moved)} 文件）")
    if failures:
        print(f"\n❌ 方法级对账失败 {len(failures)} 项：\n")
        for f in failures:
            print(f"  {f}\n")
        return 1
    print(f"✅ 方法级字节对账通过（allow_new={sorted(allow_new) or '∅'} / allow_missing={sorted(allow_missing) or '∅'}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
