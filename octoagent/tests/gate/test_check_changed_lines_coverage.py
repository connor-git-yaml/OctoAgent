"""F141 AC-4：changed-lines coverage 门单测（合成 lcov + 合成 diff，纯机械验证）。"""

from __future__ import annotations

import ast
import base64
import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import textwrap
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

SRC_A = "octoagent/packages/core/src/octoagent/core/sample.py"
SRC_B = "octoagent/apps/gateway/src/octoagent/gateway/sample.py"
TEST_FILE = "octoagent/apps/gateway/tests/test_sample.py"
SCRIPT_FILE = "repo-scripts/lane.py"
RELOCATION_SOURCE = "octoagent/packages/provider/src/octoagent/provider/dx/moved.py"
RELOCATION_TARGET = "octoagent/apps/gateway/src/octoagent/gateway/moved.py"
RELOCATION_NEW = "octoagent/apps/gateway/src/octoagent/gateway/new_behavior.py"
RELOCATION_SNAPSHOT = (
    ".specify/features/151-runtime-boundary-architecture-truth/evidence/local/atomic/"
    "S017-namespace-atomic/T029-AFTER/atomic-namespace-after.v1.json"
)
LOCAL_MODE_ORACLE = "F151_LOCAL_WORKTREE_COVERAGE_MODE_MISSING"
FRESH_EXEMPT_ORACLE = "F151_FRESH_LCOV_AND_EXEMPT_CONTRACT_MISSING"
FRESH_AFTER_UTC = "2026-01-01T00:00:00Z"
FRESH_AFTER_NS = 1_767_225_600_000_000_000
FRESH_MTIME_NS = FRESH_AFTER_NS + 1_000_000_000
STALE_MTIME_NS = FRESH_AFTER_NS - 1_000_000_000
STAGE_TASK = "T122"
EARLY_COVERAGE_ORACLE = "F151_C19_EARLY_COVERAGE_START_MISSING"
RERUN_DIAGNOSTIC_ORACLE = "F151_C19_RERUN_DIAGNOSTIC_MISSING"
RELOCATION_POST_SNAPSHOT_ORACLE = "F151_RELOCATION_POST_SNAPSHOT_COVERAGE_MISSING"
REPORT_FIELDS = {
    "status",
    "mode",
    "base_ref",
    "resolved_base_sha",
    "fresh_after_utc",
    "stage_task",
    "head_sha",
    "head_tree_sha",
    "worktree_fingerprint",
    "lcov_sha256",
    "lcov_mtime_utc",
    "lcov_fresh",
}


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env={
            "GIT_AUTHOR_NAME": "f151-test",
            "GIT_AUTHOR_EMAIL": "f151-test@example.invalid",
            "GIT_COMMITTER_NAME": "f151-test",
            "GIT_COMMITTER_EMAIL": "f151-test@example.invalid",
            "GIT_CONFIG_NOSYSTEM": "1",
            "HOME": str(repo),
            "PATH": "/usr/bin:/bin",
            "XDG_CONFIG_HOME": str(repo / ".xdg"),
        },
    )
    return result.stdout


def _write_repo_file(repo: Path, relative: str, content: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _init_repo(repo: Path, tracked: dict[str, str]) -> str:
    _git(repo, "init", "-q")
    for relative, content in tracked.items():
        _write_repo_file(repo, relative, content)
    _git(repo, "add", "--", *tracked)
    _git(repo, "commit", "-q", "-m", "baseline")
    return _git(repo, "rev-parse", "HEAD").strip()


def _collect_local_lines(coverage_mod: object, repo: Path, base_ref: str) -> dict[str, set[int]]:
    collector = getattr(coverage_mod, "collect_added_lines", None)
    if not callable(collector):
        pytest.fail(LOCAL_MODE_ORACLE, pytrace=False)
    result: Any = collector(
        repo_root=repo,
        base_ref=base_ref,
        mode="local-working-tree",
    )
    if not isinstance(result, dict):
        pytest.fail(f"{LOCAL_MODE_ORACLE}: result is not a mapping", pytrace=False)
    return {str(path): set(lines) for path, lines in result.items()}


def _assert_local_lines(actual: dict[str, set[int]], expected: dict[str, set[int]]) -> None:
    if actual != expected:
        pytest.fail(
            f"{LOCAL_MODE_ORACLE}: expected {expected!r}, got {actual!r}",
            pytrace=False,
        )


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


def _fresh_contract_fail(detail: str) -> None:
    pytest.fail(f"{FRESH_EXEMPT_ORACLE}: {detail}", pytrace=False)


def _require_fresh_contract(condition: bool, detail: str) -> None:
    if not condition:
        _fresh_contract_fail(detail)


def _worktree_fingerprint(repo: Path) -> str:
    status = _git(repo, "status", "--porcelain=v2", "-z", "--untracked-files=all")
    return hashlib.sha256(status.encode()).hexdigest()


def _head_metadata(repo: Path) -> dict[str, str]:
    return {
        "head_sha": _git(repo, "rev-parse", "HEAD").strip(),
        "head_tree_sha": _git(repo, "rev-parse", "HEAD^{tree}").strip(),
        "worktree_fingerprint": _worktree_fingerprint(repo),
    }


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _normalized_ast_sha256(content: bytes) -> str:
    tree = ast.parse(content.decode("utf-8"))
    normalized = ast.dump(tree, include_attributes=False).encode()
    return _sha256(normalized)


def _snapshot_file_row(content: bytes, *, source: str, target: str) -> dict[str, Any]:
    return {
        "bucket": "operations",
        "content_b64": base64.b64encode(content).decode("ascii"),
        "normalized_ast_sha256": _normalized_ast_sha256(content),
        "sha256": _sha256(content),
        "size_bytes": len(content),
        "source": source,
        "target": target,
    }


def _relocation_snapshot_payload(
    repo: Path, base_ref: str, source: bytes, target: bytes
) -> dict[str, Any]:
    source_row = _snapshot_file_row(source, source=RELOCATION_SOURCE, target=RELOCATION_TARGET)
    target_row = _snapshot_file_row(target, source=RELOCATION_SOURCE, target=RELOCATION_TARGET)
    return {
        "approved_hash_exceptions": [],
        "base_sha": base_ref,
        "base_source_files": [source_row],
        "before_artifact_sha256": "1" * 64,
        "c15_pre_verdict": "PASS",
        "captured_utc": "2026-01-01T00:00:00Z",
        "deletions": [],
        "expected_counts": {
            "buckets": {"operations": 1},
            "deletes": 0,
            "moves": 1,
            "roles": {},
            "source": 0,
            "target": 1,
        },
        "feature_id": "F151",
        "fingerprint_files": [],
        "fingerprint_scope": "git status --porcelain=v2 -z --untracked-files=all raw bytes",
        "head_sha": base_ref,
        "head_tree_sha": _git(repo, "rev-parse", "HEAD^{tree}").strip(),
        "lifecycle_type": "atomic-namespace-after",
        "manifest_path": "inventories/namespace-migration.v1.json",
        "manifest_sha256": "2" * 64,
        "phase": "AFTER",
        "projection_pairs": [
            {
                "base_source_normalized_ast_sha256": source_row["normalized_ast_sha256"],
                "base_source_sha256": source_row["sha256"],
                "source": RELOCATION_SOURCE,
                "target": RELOCATION_TARGET,
                "target_normalized_ast_sha256": target_row["normalized_ast_sha256"],
                "target_sha256": target_row["sha256"],
            }
        ],
        "provider_dx_import_occurrence_count": 0,
        "slice_id": "S017-namespace-atomic",
        "source_absence_count": 1,
        "target_files": [target_row],
        "target_presence_count": 1,
        "task_id": "T029",
        "version": 1,
        "worktree_fingerprint": "3" * 64,
    }


def _init_coverage_repo(repo: Path, *, exempt: bool = False) -> str:
    repo.mkdir(parents=True, exist_ok=True)
    base_ref = _init_repo(repo, {"README.md": "baseline\n"})
    _write_repo_file(repo, SRC_A, "covered = 1\n")
    _git(repo, "add", "--", SRC_A)
    message = "test: covered delta"
    if exempt:
        message += " [cov-exempt] 理由：合成显式豁免"
    _git(repo, "commit", "-q", "-m", message)
    return base_ref


def _write_lcov(path: Path, content: str, *, mtime_ns: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    os.utime(path, ns=(mtime_ns, mtime_ns))
    _require_fresh_contract(path.stat().st_mtime_ns == mtime_ns, "lcov mtime drift")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _relocation_fixture(
    root: Path,
) -> tuple[Path, str, Path, Path, Path, str]:
    repo = root / "repo"
    repo.mkdir(parents=True)
    source = b"same = 1\nvalue = 'old'\n"
    target = b"same = 1\nvalue = 'new'\nadded = 3\n"
    base_ref = _init_repo(repo, {RELOCATION_SOURCE: source.decode()})
    (repo / RELOCATION_SOURCE).unlink()
    _write_repo_file(repo, RELOCATION_TARGET, target.decode())
    _write_repo_file(repo, RELOCATION_NEW, "ordinary = 1\n")
    snapshot = repo / RELOCATION_SNAPSHOT
    _write_json(snapshot, _relocation_snapshot_payload(repo, base_ref, source, target))
    artifacts = root / "artifacts"
    lcov = artifacts / "coverage.lcov"
    _write_lcov(
        lcov,
        make_lcov(RELOCATION_TARGET.removeprefix("octoagent/"), {1: 1, 2: 1, 3: 1})
        + make_lcov(RELOCATION_NEW.removeprefix("octoagent/"), {1: 1}),
        mtime_ns=FRESH_MTIME_NS,
    )
    return repo, base_ref, lcov, snapshot, artifacts, _sha256(snapshot.read_bytes())


def _relocation_argv(
    repo: Path,
    base_ref: str,
    lcov: Path,
    report: Path,
    snapshot: Path,
    digest: str,
) -> list[str]:
    argv = _coverage_argv(repo, base_ref, lcov, report, "local-working-tree")
    relative = snapshot.relative_to(repo).as_posix()
    return [*argv, "--relocation-snapshot", f"{relative}@{digest}"]


def _invoke_relocation(
    coverage_mod: object,
    argv: list[str],
    *,
    expected_exit: int,
    oracle: str = LOCAL_MODE_ORACLE,
) -> dict[str, Any] | None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result: Any = coverage_mod.main(argv)
    except SystemExit as exc:
        pytest.fail(f"{oracle}: coverage CLI exited {exc.code}", pytrace=False)
    if result != expected_exit:
        pytest.fail(
            f"{oracle}: expected exit {expected_exit}, got {result}",
            pytrace=False,
        )
    report = Path(argv[argv.index("--report-json") + 1])
    if expected_exit == 0:
        return json.loads(report.read_text(encoding="utf-8"))
    return None


def _assert_relocation_accept(
    coverage_mod: object, fixture: tuple[Path, str, Path, Path, Path, str]
) -> None:
    repo, base_ref, lcov, snapshot, artifacts, digest = fixture
    report = artifacts / "accepted.json"
    payload = _invoke_relocation(
        coverage_mod,
        _relocation_argv(repo, base_ref, lcov, report, snapshot, digest),
        expected_exit=0,
    )
    assert payload is not None
    expected = {
        "covered_lines": 3,
        "executable_added_lines": 3,
        "relocation_mapping_count": 1,
        "relocation_snapshot_path": RELOCATION_SNAPSHOT,
        "relocation_snapshot_sha256": digest,
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        pytest.fail(f"{LOCAL_MODE_ORACLE}: relocation report mismatch", pytrace=False)


def _mutate_relocation_case(name: str, repo: Path, snapshot: Path, digest: str) -> str:
    if name == "missing":
        snapshot.unlink()
        return digest
    if name == "bad-sha":
        return "0" * 64
    payload = json.loads(snapshot.read_text(encoding="utf-8"))
    if name == "schema":
        payload.pop("projection_pairs")
    elif name == "duplicate":
        payload["projection_pairs"].append(dict(payload["projection_pairs"][0]))
    elif name == "source-baseline-drift":
        row = payload["base_source_files"][0]
        drift = b"same = 1\nvalue = 'drift'\n"
        row.update(_snapshot_file_row(drift, source=row["source"], target=row["target"]))
        projection = payload["projection_pairs"][0]
        projection["base_source_sha256"] = row["sha256"]
        projection["base_source_normalized_ast_sha256"] = row["normalized_ast_sha256"]
    elif name == "target-missing":
        (repo / RELOCATION_TARGET).unlink()
    elif name == "unknown-path":
        payload["projection_pairs"][0]["target"] = f"{RELOCATION_TARGET}.unknown"
    else:
        raise AssertionError(name)
    if name != "target-missing":
        _write_json(snapshot, payload)
    return _sha256(snapshot.read_bytes())


def _assert_relocation_rejects(coverage_mod: object, root: Path) -> None:
    for name in (
        "missing",
        "bad-sha",
        "schema",
        "duplicate",
        "source-baseline-drift",
        "target-missing",
        "unknown-path",
    ):
        fixture = _relocation_fixture(root / name)
        _assert_relocation_accept(coverage_mod, fixture)
        repo, base_ref, lcov, snapshot, artifacts, digest = fixture
        invalid_digest = _mutate_relocation_case(name, repo, snapshot, digest)
        report = artifacts / "rejected.json"
        sentinel = b"unchanged-report\n"
        report.write_bytes(sentinel)
        argv = _relocation_argv(repo, base_ref, lcov, report, snapshot, invalid_digest)
        _invoke_relocation(coverage_mod, argv, expected_exit=1)
        assert report.read_bytes() == sentinel


def _assert_post_snapshot_target_change(
    coverage_mod: object,
    fixture: tuple[Path, str, Path, Path, Path, str],
) -> None:
    repo, base_ref, lcov, snapshot, artifacts, digest = fixture
    _write_repo_file(
        repo,
        RELOCATION_TARGET,
        "same = 1\nvalue = 'new'\nadded = 3\npost_snapshot = 4\n",
    )
    _write_lcov(
        lcov,
        make_lcov(
            RELOCATION_TARGET.removeprefix("octoagent/"),
            {1: 1, 2: 1, 3: 1, 4: 1},
        )
        + make_lcov(RELOCATION_NEW.removeprefix("octoagent/"), {1: 1}),
        mtime_ns=FRESH_MTIME_NS,
    )
    report = artifacts / "post-snapshot.json"
    payload = _invoke_relocation(
        coverage_mod,
        _relocation_argv(repo, base_ref, lcov, report, snapshot, digest),
        expected_exit=0,
        oracle=RELOCATION_POST_SNAPSHOT_ORACLE,
    )
    expected = {
        "covered_lines": 4,
        "executable_added_lines": 4,
        "relocation_mapping_count": 1,
        "relocation_snapshot_path": RELOCATION_SNAPSHOT,
        "relocation_snapshot_sha256": digest,
    }
    if payload is None or any(payload.get(key) != value for key, value in expected.items()):
        pytest.fail(
            f"{RELOCATION_POST_SNAPSHOT_ORACLE}: final-target coverage mismatch",
            pytrace=False,
        )


def _early_coverage_fixture(root: Path) -> tuple[Path, dict[str, str]]:
    site = root / "site"
    package = site / "fixturepkg"
    dist_info = site / "fixture_early-1.0.dist-info"
    package.mkdir(parents=True)
    dist_info.mkdir()
    (package / "__init__.py").write_text('EARLY_MARKER = "loaded"\n', encoding="utf-8")
    (package / "plugin.py").write_text(
        "def pytest_configure(config):\n    del config\n", encoding="utf-8"
    )
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: fixture-early\nVersion: 1.0\n",
        encoding="utf-8",
    )
    (dist_info / "entry_points.txt").write_text(
        "[pytest11]\nfixture_early = fixturepkg.plugin\n",
        encoding="utf-8",
    )
    for name in ("one", "two"):
        (root / f"test_{name}.py").write_text(
            "import fixturepkg\n\n"
            f"def test_{name}():\n    assert fixturepkg.EARLY_MARKER == 'loaded'\n",
            encoding="utf-8",
        )
    env = {
        **os.environ,
        "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": str(site),
    }
    return root / "coverage.ini", env


def _coverage_run(
    root: Path, env: dict[str, str], *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        cwd=root,
        env=env,
        check=check,
        capture_output=True,
        text=True,
    )


def _lcov_hits(path: Path, suffix: str) -> dict[int, int]:
    current = False
    hits: dict[int, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("SF:"):
            current = line[3:].endswith(suffix)
        elif current and line.startswith("DA:"):
            lineno, count, *_ = line[3:].split(",")
            hits[int(lineno)] = int(count)
        elif current and line == "end_of_record":
            break
    return hits


def _run_early_suite(root: Path, config: Path, env: dict[str, str], name: str) -> None:
    _coverage_run(
        root,
        env,
        "-m",
        "coverage",
        "run",
        f"--rcfile={config}",
        "--parallel-mode",
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cov",
        "-n",
        "1",
        "--dist=loadgroup",
        f"test_{name}.py",
    )


def _assert_early_coverage_runtime(root: Path) -> None:
    config, env = _early_coverage_fixture(root)
    old_lcov = root / "old.lcov"
    _coverage_run(
        root,
        env,
        "-m",
        "pytest",
        "-q",
        "test_one.py",
        "--cov=fixturepkg",
        f"--cov-report=lcov:{old_lcov}",
    )
    if _lcov_hits(old_lcov, "fixturepkg/__init__.py").get(1, 0) != 0:
        pytest.fail(f"{EARLY_COVERAGE_ORACLE}: old pytest-cov traced plugin import")
    config.write_text(
        "[run]\nrelative_files = true\nparallel = true\npatch = subprocess\nsource = fixturepkg\n",
        encoding="utf-8",
    )
    env["COVERAGE_FILE"] = str(root / ".coverage")
    for name in ("one", "two"):
        _run_early_suite(root, config, env, name)
    if len(list(root.glob(".coverage.*"))) < 4:
        pytest.fail(f"{EARLY_COVERAGE_ORACLE}: controller/worker data missing")
    _coverage_run(root, env, "-m", "coverage", "combine", f"--rcfile={config}", str(root))
    new_lcov = root / "new.lcov"
    _coverage_run(root, env, "-m", "coverage", "lcov", f"--rcfile={config}", "-o", str(new_lcov))
    if _lcov_hits(new_lcov, "fixturepkg/__init__.py").get(1, 0) <= 0:
        pytest.fail(f"{EARLY_COVERAGE_ORACLE}: early plugin import was not measured")
    failed_root = root / "failed-suite"
    failed_root.mkdir()
    (failed_root / "test_failed.py").write_text("def test_failed():\n    assert False\n")
    failed_env = {**env, "COVERAGE_FILE": str(failed_root / ".coverage")}
    failed = _coverage_run(
        failed_root,
        failed_env,
        "-m",
        "coverage",
        "run",
        f"--rcfile={config}",
        "--parallel-mode",
        "-m",
        "pytest",
        "-q",
        "test_failed.py",
        check=False,
    )
    if failed.returncode == 0 or (failed_root / "coverage.lcov").exists():
        pytest.fail(f"{EARLY_COVERAGE_ORACLE}: failed suite produced a false PASS or LCOV")


def _assert_rerun_diagnostics_runtime(root: Path) -> None:
    config, env = _early_coverage_fixture(root)
    config.write_text(
        "[run]\nrelative_files = true\nparallel = true\npatch = subprocess\nsource = fixturepkg\n",
        encoding="utf-8",
    )
    state = root / "rerun-state.txt"
    nodeid = "test_rerun.py::test_rerun_is_persisted"
    (root / "test_rerun.py").write_text(
        "from pathlib import Path\n\n"
        "import pytest\n\n"
        "@pytest.mark.flaky(reruns=1, reruns_delay=0)\n"
        "def test_rerun_is_persisted():\n"
        f"    state = Path({str(state)!r})\n"
        "    if not state.exists():\n"
        "        state.write_text('first-attempt-failed\\n', encoding='utf-8')\n"
        "        pytest.fail('controlled-first-attempt')\n"
        "    assert state.read_text(encoding='utf-8') == 'first-attempt-failed\\n'\n",
        encoding="utf-8",
    )
    junit = root / "rerun-junit.xml"
    env["COVERAGE_FILE"] = str(root / ".coverage")
    result = _coverage_run(
        root,
        env,
        "-m",
        "coverage",
        "run",
        f"--rcfile={config}",
        "--parallel-mode",
        "-m",
        "pytest",
        "-q",
        "-rR",
        "-n",
        "1",
        "--dist=loadgroup",
        f"--junitxml={junit}",
        "test_rerun.py",
        check=False,
    )
    if result.returncode != 0 or f"RERUN {nodeid}" not in result.stdout:
        pytest.fail(f"{RERUN_DIAGNOSTIC_ORACLE}: rerun nodeid transcript missing")
    root_node = ET.parse(junit).getroot()
    suites = list(root_node.iter("testsuite"))
    cases = list(root_node.iter("testcase"))
    if (
        len(suites) != 1
        or len(cases) != 1
        or cases[0].attrib.get("name") != "test_rerun_is_persisted"
        or list(cases[0])
        or suites[0].attrib.get("tests") != "1"
        or suites[0].attrib.get("failures") != "0"
        or suites[0].attrib.get("errors") != "0"
        or not suites[0].attrib.get("timestamp")
        or not suites[0].attrib.get("time")
    ):
        pytest.fail(f"{RERUN_DIAGNOSTIC_ORACLE}: final JUnit outcome is incomplete")


def _assert_machine_uses_early_coverage(repo_root: Path) -> None:
    matrix = (
        repo_root
        / ".specify/features/151-runtime-boundary-architecture-truth/inventories/testing-matrix.md"
    ).read_text(encoding="utf-8")
    expected = {
        "python -m coverage run": 4,
        "--parallel-mode -m pytest": 4,
        "python -m coverage combine": 2,
        "python -m coverage lcov": 2,
        'patch = [\\"subprocess\\"]': 2,
    }
    if any(matrix.count(text) != count for text, count in expected.items()):
        pytest.fail(f"{EARLY_COVERAGE_ORACLE}: C19 machine command starts coverage late")


def _assert_machine_persists_rerun_diagnostics(repo_root: Path) -> None:
    matrix = (
        repo_root
        / ".specify/features/151-runtime-boundary-architecture-truth/inventories/testing-matrix.md"
    ).read_text(encoding="utf-8")
    expected = {
        "-q -rR": 4,
        '--junitxml="$tmp_dir/main-junit.xml"': 2,
        '--junitxml="$tmp_dir/e2e-junit.xml"': 2,
        'main_stdout="$tmp_dir/main-stdout.txt"': 2,
        'main_stderr="$tmp_dir/main-stderr.txt"': 2,
        'e2e_stdout="$tmp_dir/e2e-stdout.txt"': 2,
        'e2e_stderr="$tmp_dir/e2e-stderr.txt"': 2,
        'main_started_utc="$tmp_dir/main-started-utc.txt"': 2,
        'main_finished_utc="$tmp_dir/main-finished-utc.txt"': 2,
        'main_exit_code="$tmp_dir/main-exit-code.txt"': 2,
        'e2e_started_utc="$tmp_dir/e2e-started-utc.txt"': 2,
        'e2e_finished_utc="$tmp_dir/e2e-finished-utc.txt"': 2,
        'e2e_exit_code="$tmp_dir/e2e-exit-code.txt"': 2,
        "! grep -q '^RERUN '": 4,
    }
    if any(matrix.count(text) != count for text, count in expected.items()):
        pytest.fail(f"{RERUN_DIAGNOSTIC_ORACLE}: C19 diagnostics are not retained")


def _lcov_mtime_utc(path: Path) -> str:
    seconds, remainder = divmod(path.stat().st_mtime_ns, 1_000_000_000)
    _require_fresh_contract(remainder == 0, "lcov mtime is not an exact second")
    return datetime.fromtimestamp(seconds, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _coverage_argv(
    repo: Path,
    base_ref: str,
    lcov: Path,
    report: Path,
    mode: str,
) -> list[str]:
    metadata = _head_metadata(repo)
    return [
        "--mode",
        mode,
        "--repo-root",
        str(repo),
        "--lcov",
        str(lcov),
        "--base",
        base_ref,
        "--fresh-after-utc",
        FRESH_AFTER_UTC,
        "--expected-stage-task",
        STAGE_TASK,
        "--expected-head-sha",
        metadata["head_sha"],
        "--expected-head-tree",
        metadata["head_tree_sha"],
        "--expected-worktree-fingerprint",
        metadata["worktree_fingerprint"],
        "--min-percent",
        "90",
        "--report-json",
        str(report),
    ]


def _invoke_coverage_contract(
    coverage_mod: object,
    argv: list[str],
    report: Path,
) -> tuple[int, dict[str, Any]]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result: Any = coverage_mod.main(argv)
    except SystemExit as exc:
        _fresh_contract_fail(f"coverage CLI exited {exc.code}")
    _require_fresh_contract(result in {0, 1}, f"unexpected exit {result!r}")
    _require_fresh_contract(report.is_file(), "JSON report missing")
    try:
        payload: Any = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fresh_contract_fail(f"JSON report unreadable: {exc}")
    _require_fresh_contract(isinstance(payload, dict), "JSON report is not an object")
    return int(result), payload


def _assert_coverage_report(
    payload: dict[str, Any],
    *,
    status: str,
    mode: str,
    repo: Path,
    base_ref: str,
    lcov: Path,
    fresh: bool,
) -> None:
    metadata = _head_metadata(repo)
    _require_fresh_contract(payload.keys() >= REPORT_FIELDS, "report fields missing")
    expected = {
        "status": status,
        "mode": mode,
        "base_ref": base_ref,
        "resolved_base_sha": base_ref,
        "fresh_after_utc": FRESH_AFTER_UTC,
        "stage_task": STAGE_TASK,
        **metadata,
        "lcov_fresh": fresh,
    }
    for key, value in expected.items():
        _require_fresh_contract(payload[key] == value, f"{key} mismatch")
    if not lcov.is_file():
        _require_fresh_contract(payload["lcov_sha256"] is None, "missing lcov sha")
        _require_fresh_contract(payload["lcov_mtime_utc"] is None, "missing lcov mtime")
        return
    expected_sha = hashlib.sha256(lcov.read_bytes()).hexdigest()
    _require_fresh_contract(payload["lcov_sha256"] == expected_sha, "lcov sha mismatch")
    expected_mtime = _lcov_mtime_utc(lcov)
    _require_fresh_contract(payload["lcov_mtime_utc"] == expected_mtime, "lcov mtime mismatch")


def _run_coverage_case(
    coverage_mod: object,
    *,
    repo: Path,
    base_ref: str,
    lcov: Path,
    report: Path,
    mode: str,
    status: str,
    exit_code: int,
    fresh: bool,
) -> None:
    argv = _coverage_argv(repo, base_ref, lcov, report, mode)
    actual_exit, payload = _invoke_coverage_contract(coverage_mod, argv, report)
    _require_fresh_contract(actual_exit == exit_code, "coverage exit mismatch")
    _assert_coverage_report(
        payload,
        status=status,
        mode=mode,
        repo=repo,
        base_ref=base_ref,
        lcov=lcov,
        fresh=fresh,
    )


def _fresh_case_paths(tmp_path: Path, name: str) -> tuple[Path, str, Path, Path]:
    repo = tmp_path / name / "repo"
    base_ref = _init_coverage_repo(repo)
    artifacts = tmp_path / name / "artifacts"
    lcov = artifacts / "coverage.lcov"
    report = artifacts / "report.json"
    _write_lcov(
        lcov,
        make_lcov("packages/core/src/octoagent/core/sample.py", {1: 1}),
        mtime_ns=FRESH_MTIME_NS,
    )
    return repo, base_ref, lcov, report


def _assert_fresh_then_missing(coverage_mod: object, tmp_path: Path) -> None:
    repo, base_ref, lcov, report = _fresh_case_paths(tmp_path, "missing")
    _run_coverage_case(
        coverage_mod,
        repo=repo,
        base_ref=base_ref,
        lcov=lcov,
        report=report,
        mode="committed",
        status="PASS",
        exit_code=0,
        fresh=True,
    )
    fresh_sha = hashlib.sha256(lcov.read_bytes()).hexdigest()
    lcov.unlink()
    _require_fresh_contract(not lcov.exists(), "missing lcov mutation absent")
    missing_report = report.with_name("missing-report.json")
    _run_coverage_case(
        coverage_mod,
        repo=repo,
        base_ref=base_ref,
        lcov=lcov,
        report=missing_report,
        mode="committed",
        status="FAIL",
        exit_code=1,
        fresh=False,
    )
    _require_fresh_contract(bool(fresh_sha), "fresh control sha absent")


def _assert_fresh_then_stale(coverage_mod: object, tmp_path: Path) -> None:
    repo, base_ref, lcov, report = _fresh_case_paths(tmp_path, "stale")
    _run_coverage_case(
        coverage_mod,
        repo=repo,
        base_ref=base_ref,
        lcov=lcov,
        report=report,
        mode="committed",
        status="PASS",
        exit_code=0,
        fresh=True,
    )
    fresh_mtime = lcov.stat().st_mtime_ns
    os.utime(lcov, ns=(STALE_MTIME_NS, STALE_MTIME_NS))
    _require_fresh_contract(lcov.stat().st_mtime_ns != fresh_mtime, "stale delta absent")
    stale_report = report.with_name("stale-report.json")
    _run_coverage_case(
        coverage_mod,
        repo=repo,
        base_ref=base_ref,
        lcov=lcov,
        report=stale_report,
        mode="committed",
        status="FAIL",
        exit_code=1,
        fresh=False,
    )


def _apply_local_delta(repo: Path, kind: str) -> str:
    path = f"octoagent/packages/core/src/octoagent/core/{kind}_uncovered.py"
    if kind == "staged":
        _write_repo_file(repo, path, "uncovered = 1\n")
        _git(repo, "add", "--", path)
        observed = _git(repo, "diff", "--cached", "--name-only").splitlines()
    elif kind == "unstaged":
        path = SRC_A
        _write_repo_file(repo, path, "covered = 1\nuncovered = 2\n")
        observed = _git(repo, "diff", "--name-only").splitlines()
    else:
        _write_repo_file(repo, path, "uncovered = 1\n")
        observed = _git(repo, "ls-files", "--others", "--exclude-standard").splitlines()
    _require_fresh_contract(path in observed, f"{kind} delta absent")
    return path


def _local_lcov(kind: str, dirty_path: str) -> str:
    records = [make_lcov("packages/core/src/octoagent/core/sample.py", {1: 1, 2: 0})]
    if dirty_path != SRC_A:
        records.append(make_lcov(dirty_path.removeprefix("octoagent/"), {1: 0}))
    return "".join(records)


def _assert_exempt_local_case(
    coverage_mod: object,
    tmp_path: Path,
    kind: str,
) -> None:
    repo = tmp_path / f"local-{kind}" / "repo"
    base_ref = _init_coverage_repo(repo, exempt=True)
    artifacts = tmp_path / f"local-{kind}" / "artifacts"
    lcov = artifacts / "coverage.lcov"
    _write_lcov(
        lcov,
        make_lcov("packages/core/src/octoagent/core/sample.py", {1: 1}),
        mtime_ns=FRESH_MTIME_NS,
    )
    _run_coverage_case(
        coverage_mod,
        repo=repo,
        base_ref=base_ref,
        lcov=lcov,
        report=artifacts / "committed.json",
        mode="committed",
        status="EXEMPT",
        exit_code=0,
        fresh=True,
    )
    clean_fingerprint = _worktree_fingerprint(repo)
    dirty_path = _apply_local_delta(repo, kind)
    _require_fresh_contract(
        _worktree_fingerprint(repo) != clean_fingerprint, "dirty fingerprint unchanged"
    )
    _write_lcov(lcov, _local_lcov(kind, dirty_path), mtime_ns=FRESH_MTIME_NS)
    _run_coverage_case(
        coverage_mod,
        repo=repo,
        base_ref=base_ref,
        lcov=lcov,
        report=artifacts / "local.json",
        mode="local-working-tree",
        status="FAIL",
        exit_code=1,
        fresh=True,
    )


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
    @pytest.mark.parametrize(
        "path,expected",
        [
            (SRC_A, True),
            (SRC_B, True),
            (TEST_FILE, False),  # 测试文件不计
            (SCRIPT_FILE, False),  # repo-scripts 不计
            ("octoagent/frontend/src/App.tsx", False),  # 前端不计
            ("docs/blueprint/testing-strategy.md", False),
            ("octoagent/packages/core/src/octoagent/core/data.json", False),  # 非 .py
            ("octoagent/packages/core/tests/test_x.py", False),  # 包内测试不计
        ],
    )
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
                check=True,
                capture_output=True,
                env={
                    "GIT_AUTHOR_NAME": "t",
                    "GIT_AUTHOR_EMAIL": "t@t",
                    "GIT_COMMITTER_NAME": "t",
                    "GIT_COMMITTER_EMAIL": "t@t",
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


class TestLocalWorkingTreeMode:
    def test_includes_staged_unstaged_and_untracked_production_lines(
        self, coverage_mod: object, tmp_path: Path
    ) -> None:
        staged = "octoagent/packages/core/src/octoagent/core/staged_sample.py"
        unstaged = "octoagent/packages/core/src/octoagent/core/unstaged_sample.py"
        untracked = "octoagent/apps/gateway/src/octoagent/gateway/untracked_sample.py"
        base_ref = _init_repo(tmp_path, {unstaged: "baseline = 0\n"})
        _write_repo_file(tmp_path, staged, "staged = 1\nstaged_second = 2\n")
        _git(tmp_path, "add", "--", staged)
        _write_repo_file(tmp_path, unstaged, "baseline = 0\nunstaged = 1\n")
        _write_repo_file(tmp_path, untracked, "untracked = 1\n")

        actual = _collect_local_lines(coverage_mod, tmp_path, base_ref)

        _assert_local_lines(
            actual,
            {staged: {1, 2}, unstaged: {2}, untracked: {1}},
        )

    def test_deduplicates_final_worktree_line_numbers(
        self,
        coverage_mod: object,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        path = "octoagent/packages/core/src/octoagent/core/deduplicated.py"
        base_ref = _init_repo(tmp_path, {path: "baseline = 0\n"})
        _write_repo_file(tmp_path, path, "baseline = 0\nvalue = 'staged'\n")
        _git(tmp_path, "add", "--", path)
        _write_repo_file(tmp_path, path, "baseline = 0\nvalue = 'final'\n")

        actual = _collect_local_lines(coverage_mod, tmp_path, base_ref)

        _assert_local_lines(actual, {path: {2}})
        monkeypatch.setenv("F151_COVERAGE_STAGE", STAGE_TASK)
        no_snapshot = _relocation_fixture(tmp_path / "no-snapshot")
        repo, baseline, _, _, _, _ = no_snapshot
        _assert_local_lines(
            _collect_local_lines(coverage_mod, repo, baseline),
            {RELOCATION_NEW: {1}, RELOCATION_TARGET: {1, 2, 3}},
        )
        _assert_relocation_accept(coverage_mod, _relocation_fixture(tmp_path / "accepted"))
        _assert_relocation_rejects(coverage_mod, tmp_path / "rejected")
        _assert_early_coverage_runtime(tmp_path / "early-coverage")
        _assert_rerun_diagnostics_runtime(tmp_path / "rerun-diagnostics")
        _assert_machine_uses_early_coverage(Path(__file__).resolve().parents[3])
        _assert_machine_persists_rerun_diagnostics(Path(__file__).resolve().parents[3])

    def test_ignores_nonproduction_untracked_files(
        self, coverage_mod: object, tmp_path: Path
    ) -> None:
        production = "octoagent/packages/core/src/octoagent/core/kept.py"
        base_ref = _init_repo(tmp_path, {"README.md": "baseline\n"})
        _write_repo_file(tmp_path, production, "kept = 1\nkept_second = 2\n")
        _write_repo_file(tmp_path, "docs/untracked-note.md", "ignore me\n")
        _write_repo_file(
            tmp_path,
            "octoagent/packages/core/tests/test_untracked.py",
            "def test_ignored():\n    assert True\n",
        )

        actual = _collect_local_lines(coverage_mod, tmp_path, base_ref)

        _assert_local_lines(actual, {production: {1, 2}})


class TestRelocationPostSnapshotCoverage:
    def test_post_snapshot_target_changes_use_base_source_to_final_target(
        self,
        coverage_mod: object,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """C15/C20授权target变化；coverage只重算base source→final target。"""
        monkeypatch.setenv("F151_COVERAGE_STAGE", STAGE_TASK)
        _assert_relocation_accept(
            coverage_mod,
            _relocation_fixture(tmp_path / "snapshot-target-same"),
        )
        _assert_post_snapshot_target_change(
            coverage_mod,
            _relocation_fixture(tmp_path / "post-snapshot-target-change"),
        )
        _assert_relocation_rejects(coverage_mod, tmp_path / "fail-closed")


class TestFreshLcov:
    def test_rejects_missing_or_stale_lcov(
        self,
        coverage_mod: object,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("F151_COVERAGE_STAGE", STAGE_TASK)
        repo, base_ref, lcov, report = _fresh_case_paths(tmp_path, "fresh")
        _run_coverage_case(
            coverage_mod,
            repo=repo,
            base_ref=base_ref,
            lcov=lcov,
            report=report,
            mode="committed",
            status="PASS",
            exit_code=0,
            fresh=True,
        )
        _assert_fresh_then_missing(coverage_mod, tmp_path)
        _assert_fresh_then_stale(coverage_mod, tmp_path)


class TestExemptOutcome:
    def test_committed_exempt_is_not_pass_and_local_mode_does_not_inherit_exemption(
        self,
        coverage_mod: object,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("F151_COVERAGE_STAGE", STAGE_TASK)
        repo, base_ref, lcov, report = _fresh_case_paths(tmp_path, "normal")
        _run_coverage_case(
            coverage_mod,
            repo=repo,
            base_ref=base_ref,
            lcov=lcov,
            report=report,
            mode="committed",
            status="PASS",
            exit_code=0,
            fresh=True,
        )
        for kind in ("staged", "unstaged", "untracked"):
            _assert_exempt_local_case(coverage_mod, tmp_path, kind)
