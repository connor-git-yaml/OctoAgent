"""F141 AC-4：changed-lines coverage 门单测（合成 lcov + 合成 diff，纯机械验证）。"""

from __future__ import annotations

import textwrap

import pytest

SRC_A = "octoagent/packages/core/src/octoagent/core/sample.py"
SRC_B = "octoagent/apps/gateway/src/octoagent/gateway/sample.py"
TEST_FILE = "octoagent/apps/gateway/tests/test_sample.py"
SCRIPT_FILE = "repo-scripts/lane.py"


def make_diff(path: str, start: int, count: int) -> str:
    """合成 unified=0 diff：path 在 start 起新增 count 行。"""
    lines = [
        f"diff --git a/{path} b/{path}",
        f"--- a/{path}",
        f"+++ b/{path}",
        f"@@ -{start - 1},0 +{start},{count} @@",
    ]
    lines += [f"+line{i}" for i in range(count)]
    return "\n".join(lines) + "\n"


def make_lcov(path_rel_octoagent: str, da: dict[int, int]) -> str:
    """合成 lcov 记录（SF 相对 octoagent/，模拟 relative_files=true 产物）。"""
    body = "\n".join(f"DA:{line},{hits}" for line, hits in sorted(da.items()))
    return f"TN:\nSF:{path_rel_octoagent}\n{body}\nend_of_record\n"


class TestDiffParsing:
    def test_added_lines_from_hunk(self, coverage_mod) -> None:
        diff = make_diff(SRC_A, 10, 3)
        added = coverage_mod.parse_added_lines_from_diff(diff)
        assert added[SRC_A] == {10, 11, 12}

    def test_count_default_1(self, coverage_mod) -> None:
        diff = textwrap.dedent(f"""\
            --- a/{SRC_A}
            +++ b/{SRC_A}
            @@ -5,0 +6 @@
            +single
        """)
        added = coverage_mod.parse_added_lines_from_diff(diff)
        assert added[SRC_A] == {6}

    def test_pure_deletion_no_added(self, coverage_mod) -> None:
        diff = textwrap.dedent(f"""\
            --- a/{SRC_A}
            +++ b/{SRC_A}
            @@ -5,2 +4,0 @@
            -gone1
            -gone2
        """)
        added = coverage_mod.parse_added_lines_from_diff(diff)
        assert SRC_A not in added

    def test_deleted_file_ignored(self, coverage_mod) -> None:
        diff = textwrap.dedent(f"""\
            --- a/{SRC_A}
            +++ /dev/null
            @@ -1,3 +0,0 @@
            -a
            -b
            -c
        """)
        assert coverage_mod.parse_added_lines_from_diff(diff) == {}


class TestScope:
    @pytest.mark.parametrize("path,expected", [
        (SRC_A, True),
        (SRC_B, True),
        (TEST_FILE, False),                       # 测试文件不计
        (SCRIPT_FILE, False),                     # repo-scripts 不计
        ("octoagent/frontend/src/App.tsx", False),  # 前端不计
        ("docs/blueprint/testing-strategy.md", False),
        ("octoagent/packages/core/src/octoagent/core/data.json", False),  # 非 .py
        ("octoagent/packages/core/tests/test_x.py", False),  # 包内测试不计
    ])
    def test_scope_rules(self, coverage_mod, path: str, expected: bool) -> None:
        assert coverage_mod.in_scope(path) is expected


class TestLcovParsing:
    def test_relative_sf_gets_prefix(self, coverage_mod) -> None:
        lcov = make_lcov("packages/core/src/octoagent/core/sample.py", {1: 1, 2: 0})
        cov = coverage_mod.parse_lcov(lcov)
        assert SRC_A in cov
        assert cov[SRC_A] == {1: 1, 2: 0}

    def test_absolute_sf_normalized(self, coverage_mod) -> None:
        lcov = make_lcov("packages/core/src/octoagent/core/sample.py", {1: 1}).replace(
            "SF:packages/",
            "SF:/Users/x/repo/octoagent/packages/",
        )
        cov = coverage_mod.parse_lcov(lcov)
        assert SRC_A in cov


class TestEvaluate:
    """AC-4 判定规则。"""

    def test_exact_90_passes(self, coverage_mod) -> None:
        added = {SRC_A: set(range(1, 11))}  # 10 行
        cov = {SRC_A: {i: (1 if i <= 9 else 0) for i in range(1, 11)}}  # 9/10 = 90%
        ok, _, covered, total = coverage_mod.evaluate(added, cov, 90.0)
        assert (ok, covered, total) == (True, 9, 10)

    def test_below_90_fails(self, coverage_mod) -> None:
        added = {SRC_A: set(range(1, 11))}
        cov = {SRC_A: {i: (1 if i <= 8 else 0) for i in range(1, 11)}}  # 80%
        ok, detail, covered, total = coverage_mod.evaluate(added, cov, 90.0)
        assert ok is False
        assert covered == 8 and total == 10
        assert any(SRC_A in line for line in detail)

    def test_new_file_without_lcov_counts_zero(self, coverage_mod) -> None:
        """范围内新文件无 lcov 记录 → 全按 0 覆盖计（抓无测试 import 的新模块）。"""
        added = {SRC_A: {1, 2, 3, 4}}
        ok, detail, covered, total = coverage_mod.evaluate(added, {}, 90.0)
        assert ok is False
        assert covered == 0 and total == 4
        assert any("lcov 无该文件记录" in line for line in detail)

    def test_non_executable_lines_excluded(self, coverage_mod) -> None:
        """DA 未列出的行 = 非可执行（空行/注释）→ 不计分母。"""
        added = {SRC_A: {1, 2, 3, 4, 5}}
        cov = {SRC_A: {1: 1, 3: 1}}  # 只有 1/3 可执行，且全覆盖
        ok, _, covered, total = coverage_mod.evaluate(added, cov, 90.0)
        assert (ok, covered, total) == (True, 2, 2)

    def test_zero_executable_added_passes(self, coverage_mod) -> None:
        """新增行全非可执行 → PASS。"""
        added = {SRC_A: {100, 101}}
        cov = {SRC_A: {1: 1}}  # 100/101 不在 DA
        ok, _, covered, total = coverage_mod.evaluate(added, cov, 90.0)
        assert (ok, total) == (True, 0)

    def test_out_of_scope_paths_ignored(self, coverage_mod) -> None:
        added = {TEST_FILE: {1, 2, 3}, SCRIPT_FILE: {1}}
        ok, _, covered, total = coverage_mod.evaluate(added, {}, 90.0)
        assert (ok, total) == (True, 0)

    def test_multi_file_aggregate(self, coverage_mod) -> None:
        added = {SRC_A: {1, 2}, SRC_B: {1, 2}}
        cov = {
            SRC_A: {1: 1, 2: 1},
            SRC_B: {1: 1, 2: 0},
        }  # 3/4 = 75%
        ok, _, covered, total = coverage_mod.evaluate(added, cov, 90.0)
        assert (ok, covered, total) == (False, 3, 4)


class TestExemptMarker:
    def test_exempt_marker_constant(self, coverage_mod) -> None:
        assert coverage_mod.EXEMPT_MARKER == "[cov-exempt]"

    def test_head_is_exempt_reads_git(self, coverage_mod, tmp_path) -> None:
        """真 git 仓验证 [cov-exempt] 识别（含大声记录所需的 sha+subject）。"""
        import subprocess

        def git(*args: str) -> None:
            subprocess.run(
                ["git", "-C", str(tmp_path), *args],
                check=True, capture_output=True,
                env={
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                    "PATH": "/usr/bin:/bin",
                    "HOME": str(tmp_path),
                },
            )

        git("init", "-q")
        (tmp_path / "f.txt").write_text("x", encoding="utf-8")
        git("add", "f.txt")
        git("commit", "-q", "-m", "normal commit")
        exempt, desc = coverage_mod.head_is_exempt(tmp_path)
        assert exempt is False and "normal commit" in desc

        (tmp_path / "f.txt").write_text("y", encoding="utf-8")
        git("add", "f.txt")
        git("commit", "-q", "-m", "fix: xx [cov-exempt] 理由：纯防御分支不可测")
        exempt, desc = coverage_mod.head_is_exempt(tmp_path)
        assert exempt is True
