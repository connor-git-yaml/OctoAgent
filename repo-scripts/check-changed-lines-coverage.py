#!/usr/bin/env python3
"""F141 件4：changed-lines coverage 门（stdlib-only，CI backend job 调用）。

机械规则（cc-haha coverage.ts ``evaluateChangedLineCoverage`` 范式，spec D5v2）：
1. ``git diff --unified=0 --no-ext-diff <base>...HEAD`` 解析 hunk 头，取**新增行号**；
2. 范围过滤：只计生产 python 源码
   ``octoagent/packages/*/src/**/*.py`` + ``octoagent/apps/gateway/src/**/*.py``
   （测试 / repo-scripts / frontend / 文档天然不计——存量不背债，脚本改动不背 coverage 债）；
3. lcov（pytest-cov ``--cov-report=lcov`` 产物，``relative_files=true`` + config source）
   的 ``SF:`` 路径是相对 ``octoagent/`` 的 ``packages/.../src/...``——补 ``octoagent/``
   前缀后与 git 路径对齐；``DA:<line>,<hits>`` 即可执行行集合与命中集合；
4. 判定：新增行 ∩ 可执行行 的覆盖率 < ``--min-percent``（默认 90）→ exit 1，并列出
   未覆盖 文件:行号；**范围内新文件在 lcov 无记录 → 该文件新增行全按可执行 0 覆盖计**
   （抓「加了模块没有任何测试 import」）；非可执行行（空行/注释/pragma 排除行）不计；
   新增可执行行数 = 0 → PASS（docs/测试/脚本改动天然过门）；
5. escape hatch：HEAD commit message 含 ``[cov-exempt]`` → exempt 状态 PASS，
   **大声记录** HEAD sha + subject（单人仓治「忘」不治「恶」——与 SKIP_E2E 同级显式 bypass，
   Codex M1 拒绝记录见 .specify/features/141-three-mode-lanes/codex-review-spec.md）。

exit code：0 = 过门/exempt/无新增可执行行；1 = 覆盖率不足或输入损坏；2 = 参数错误。

用法（CI）：
    python3 repo-scripts/check-changed-lines-coverage.py \
        --lcov octoagent/coverage.lcov --base <sha> [--min-percent 90]
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

EXEMPT_MARKER = "[cov-exempt]"

# 生产源码范围（相对 repo root）
_SCOPE_RE = re.compile(
    r"^octoagent/(?:packages/[^/]+/src/.+|apps/gateway/src/.+)\.py$"
)

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _git(repo_root: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def in_scope(path: str) -> bool:
    """是否属于 changed-lines 门的生产源码范围。"""
    return bool(_SCOPE_RE.match(path))


def parse_added_lines_from_diff(diff_text: str) -> dict[str, set[int]]:
    """解析 ``git diff --unified=0`` 输出 → {repo 相对路径: 新增行号集合}。

    只看 hunk 头的 ``+<start>,<count>``——unified=0 下新增行号即
    ``start..start+count-1``（count 缺省 = 1，count=0 = 纯删除无新增）。
    """
    added: dict[str, set[int]] = {}
    current: str | None = None
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            target = line[4:].strip()
            if target == "/dev/null":
                current = None
            elif target.startswith("b/"):
                current = target[2:]
            else:
                current = target
            continue
        match = _HUNK_RE.match(line)
        if match and current is not None:
            start = int(match.group(1))
            count = int(match.group(2)) if match.group(2) is not None else 1
            if count > 0:
                added.setdefault(current, set()).update(range(start, start + count))
    return added


def parse_lcov(lcov_text: str, path_prefix: str = "octoagent/") -> dict[str, dict[int, int]]:
    """解析 lcov → {repo 相对路径: {行号: 命中次数}}。

    SF 路径若非绝对且不带 ``octoagent/`` 前缀（relative_files=true 相对 cwd=octoagent/），
    补 ``path_prefix`` 归一到 repo 相对路径。
    """
    coverage: dict[str, dict[int, int]] = {}
    current: str | None = None
    for line in lcov_text.splitlines():
        if line.startswith("SF:"):
            raw = line[3:].strip()
            if raw.startswith("/"):
                # 绝对路径：截取 octoagent/ 之后部分再归一（防守——理论上 relative_files 不产）
                idx = raw.find("/octoagent/")
                raw = raw[idx + 1:] if idx >= 0 else raw
            if not raw.startswith(path_prefix):
                raw = path_prefix + raw
            current = raw
            coverage.setdefault(current, {})
        elif line.startswith("DA:") and current is not None:
            payload = line[3:].split(",")
            if len(payload) >= 2:
                try:
                    lineno, hits = int(payload[0]), int(payload[1])
                except ValueError:
                    continue
                coverage[current][lineno] = hits
        elif line.strip() == "end_of_record":
            current = None
    return coverage


def evaluate(
    added: dict[str, set[int]],
    coverage: dict[str, dict[int, int]],
    min_percent: float,
) -> tuple[bool, list[str], int, int]:
    """核心判定。返回 (是否过门, 明细行, 覆盖行数, 可执行新增行数)。"""
    detail: list[str] = []
    total = 0
    covered = 0
    for path in sorted(added):
        if not in_scope(path):
            continue
        lines = added[path]
        file_cov = coverage.get(path)
        if file_cov is None:
            # 范围内新文件无任何 lcov 记录：全部新增行按可执行 0 覆盖计（spec D5 规则）
            total += len(lines)
            detail.append(
                f"  {path}: 0/{len(lines)} —— lcov 无该文件记录（新模块无任何测试 import？）"
            )
            continue
        executable = sorted(line for line in lines if line in file_cov)
        if not executable:
            continue  # 新增行全是非可执行行（空行/注释/被排除）
        hit = [line for line in executable if file_cov[line] > 0]
        missed = [line for line in executable if file_cov[line] == 0]
        total += len(executable)
        covered += len(hit)
        if missed:
            detail.append(
                f"  {path}: {len(hit)}/{len(executable)} 未覆盖行 {_fmt_lines(missed)}"
            )
    if total == 0:
        return True, detail, covered, total
    pct = covered / total * 100.0
    return pct >= min_percent, detail, covered, total


def _fmt_lines(lines: list[int], cap: int = 20) -> str:
    shown = ",".join(str(x) for x in lines[:cap])
    return shown + (f",…(+{len(lines) - cap})" if len(lines) > cap else "")


def head_is_exempt(repo_root: Path) -> tuple[bool, str]:
    """HEAD commit message 是否含 [cov-exempt]；返回 (exempt, 'sha subject')。"""
    out = _git(repo_root, ["log", "-1", "--format=%H%x00%s%x00%B"]).split("\x00", 2)
    sha, subject, body = out[0], out[1], out[2] if len(out) > 2 else ""
    return EXEMPT_MARKER in body, f"{sha[:12]} {subject}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="F141 changed-lines coverage 门")
    parser.add_argument("--lcov", type=Path, required=True)
    parser.add_argument("--base", type=str, required=True, help="diff 基准 commit sha")
    parser.add_argument("--min-percent", type=float, default=90.0)
    parser.add_argument("--repo-root", type=Path,
                        default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args(argv)

    repo_root = args.repo_root

    exempt, head_desc = head_is_exempt(repo_root)
    if exempt:
        print("=" * 72)
        print(f"[changed-lines EXEMPT] HEAD commit 声明 {EXEMPT_MARKER} —— 本次 push 豁免覆盖门")
        print(f"  HEAD: {head_desc}")
        print("  语义：与 SKIP_E2E 同级的显式 bypass；豁免记录留在 CI 日志")
        print("=" * 72)
        return 0

    try:
        diff_text = _git(repo_root, [
            "diff", "--unified=0", "--no-ext-diff", f"{args.base}...HEAD", "--",
        ])
    except subprocess.CalledProcessError as exc:
        print(f"[changed-lines FAIL] git diff 失败（base={args.base}）: {exc.stderr}",
              file=sys.stderr)
        return 1

    added = parse_added_lines_from_diff(diff_text)
    scoped = {p: v for p, v in added.items() if in_scope(p)}
    if not scoped:
        print("[changed-lines PASS] 本次 diff 无范围内生产源码新增行（0 changed executable lines）")
        return 0

    if not args.lcov.is_file():
        print(f"[changed-lines FAIL] lcov 文件不存在: {args.lcov}", file=sys.stderr)
        return 1
    coverage = parse_lcov(args.lcov.read_text(encoding="utf-8"))

    ok, detail, covered, total = evaluate(scoped, coverage, args.min_percent)
    pct_text = f"{covered}/{total}" + (f" = {covered / total * 100.0:.1f}%" if total else "")
    if total == 0:
        print("[changed-lines PASS] 范围内新增行均非可执行行（注释/空行）")
        return 0
    if ok:
        print(f"[changed-lines PASS] 新增可执行行覆盖 {pct_text} ≥ {args.min_percent:.0f}%")
        for line in detail:
            print(line)
        return 0
    print(
        f"[changed-lines FAIL] 新增可执行行覆盖 {pct_text} < {args.min_percent:.0f}%",
        file=sys.stderr,
    )
    for line in detail:
        print(line, file=sys.stderr)
    print(
        f"  修法：为新增行补测试；或（确属不可测例外）HEAD commit message 加 {EXEMPT_MARKER} 标记",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
