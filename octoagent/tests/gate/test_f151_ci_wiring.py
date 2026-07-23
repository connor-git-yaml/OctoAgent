"""F151 CI wiring 合同：真实 hook、独立 architecture/coverage 与完整前端门。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from yaml import BaseLoader, load

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_ROOT = REPO_ROOT / ".github" / "workflows"
HOOK = REPO_ROOT / ".githooks" / "pre-commit"
WORKFLOW_SELF = ".github/workflows/feature-007-integration.yml"
FEATURE_ROOT = ".specify/features/151-runtime-boundary-architecture-truth/**"
BENCHMARK_NODES = (
    "benchmarks/tests/unit/test_octo_runner.py::test_source_checkout_required_before_side_effects",
    "benchmarks/tests/unit/test_octo_runner.py::test_runner_fn_provider_error_maps_to_infra_error",
)


def _workflow_documents() -> list[tuple[Path, dict[str, Any]]]:
    documents: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(WORKFLOW_ROOT.glob("*.yml")):
        payload = load(path.read_text(encoding="utf-8"), Loader=BaseLoader)
        assert isinstance(payload, dict), f"workflow root must be a mapping: {path}"
        documents.append((path, payload))
    assert documents, "at least one GitHub workflow is required"
    return documents


def _jobs() -> dict[str, tuple[Path, dict[str, Any]]]:
    jobs: dict[str, tuple[Path, dict[str, Any]]] = {}
    for path, document in _workflow_documents():
        raw_jobs = document.get("jobs")
        assert isinstance(raw_jobs, dict), f"workflow jobs must be a mapping: {path}"
        for job_id, job in raw_jobs.items():
            assert job_id not in jobs, f"duplicate workflow job id: {job_id}"
            assert isinstance(job, dict), f"workflow job must be a mapping: {job_id}"
            jobs[str(job_id)] = (path, job)
    return jobs


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    values = job.get("steps")
    assert isinstance(values, list), "workflow job steps must be a list"
    assert all(isinstance(value, dict) for value in values)
    return values


def _run_text(job: dict[str, Any]) -> str:
    return "\n".join(str(step.get("run", "")) for step in _steps(job))


def _upload_steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        step
        for step in _steps(job)
        if str(step.get("uses", "")).startswith("actions/upload-artifact@")
    ]


def _fail(oracle: str, issues: list[str]) -> None:
    if issues:
        pytest.fail(f"{oracle}: {'; '.join(issues)}", pytrace=False)


def _trigger_paths(document: dict[str, Any], event: str) -> set[str]:
    triggers = document.get("on")
    if not isinstance(triggers, dict):
        return set()
    config = triggers.get(event)
    if not isinstance(config, dict):
        return set()
    paths = config.get("paths")
    return {str(value) for value in paths} if isinstance(paths, list) else set()


def _artifact_names(job: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for step in _upload_steps(job):
        config = step.get("with")
        if isinstance(config, dict) and config.get("name"):
            names.add(str(config["name"]))
    return names


def test_architecture_gate_runs_before_docs_fastpath_and_covers_docs_constitution_feature_paths() -> (  # noqa: E501
    None
):
    issues: list[str] = []
    hook = HOOK.read_text(encoding="utf-8")
    architecture = hook.find("check-runtime-architecture.py")
    docs_fastpath = hook.find("if [[ ${DOCS_ONLY} -eq 1 ]]")
    if architecture < 0 or docs_fastpath < 0 or architecture >= docs_fastpath:
        issues.append("real pre-commit architecture gate must precede docs-only exit")
    elif "--base-ref" not in hook[architecture:docs_fastpath]:
        issues.append("pre-commit architecture gate must consume an explicit base ref")

    required = {
        "octoagent/**",
        "repo-scripts/**",
        "docs/**",
        ".specify/memory/constitution.md",
        FEATURE_ROOT,
        WORKFLOW_SELF,
    }
    for path, document in _workflow_documents():
        for event in ("pull_request", "push"):
            missing = required - _trigger_paths(document, event)
            if missing:
                issues.append(f"{path.name}:{event} paths missing {sorted(missing)}")
    _fail("F151_CI_WIRING_MISSING", issues)


def test_ci_uses_pr_base_or_push_before_sha_with_full_history() -> None:
    issues: list[str] = []
    architecture_entry = _jobs().get("architecture")
    if architecture_entry is None:
        _fail("F151_CI_WIRING_MISSING", ["architecture job missing"])
        return
    _, architecture = architecture_entry
    steps = _steps(architecture)
    checkout = next(
        (step for step in steps if str(step.get("uses", "")).startswith("actions/checkout@")),
        None,
    )
    checkout_with = checkout.get("with") if checkout else None
    if not isinstance(checkout_with, dict) or checkout_with.get("fetch-depth") != "0":
        issues.append("architecture checkout must use fetch-depth 0")
    base_step = next(
        (
            step
            for step in steps
            if "github.event.pull_request.base.sha" in str(step.get("run", ""))
        ),
        None,
    )
    if base_step is None:
        issues.append("architecture base resolver missing")
    else:
        resolver = str(base_step.get("run", ""))
        required = (
            "github.event_name",
            "github.event.pull_request.base.sha",
            "github.event.before",
            "HEAD^",
            "git merge-base",
            "GITHUB_OUTPUT",
        )
        issues.extend(
            f"architecture base resolver missing {token}"
            for token in required
            if token not in resolver
        )
        if "origin/master" in resolver:
            issues.append("all-zero push fallback must use HEAD^, not a remote branch")
        step_id = str(base_step.get("id", ""))
        command = _run_text(architecture)
        if not step_id or f"steps.{step_id}.outputs.base" not in command:
            issues.append("architecture command does not consume resolved merge-base output")
    _fail("F151_CI_WIRING_MISSING", issues)


def test_architecture_and_backend_coverage_jobs_have_independent_artifact_flows() -> None:
    issues: list[str] = []
    jobs = _jobs()
    architecture_entry = jobs.get("architecture")
    coverage_entries = [
        (job_id, value)
        for job_id, value in jobs.items()
        if "check-changed-lines-coverage.py" in _run_text(value[1])
    ]
    if architecture_entry is None:
        issues.append("architecture job missing")
    if len(coverage_entries) != 1:
        issues.append(f"expected one backend coverage job, got {len(coverage_entries)}")
    if architecture_entry is not None and len(coverage_entries) == 1:
        _, architecture = architecture_entry
        coverage_id, (_, coverage) = coverage_entries[0]
        if architecture.get("needs") or coverage.get("needs"):
            issues.append("architecture and backend coverage jobs must be independent")
        architecture_run = _run_text(architecture)
        if any(
            token in architecture_run for token in ("lane.py", "coverage.lcov", "download-artifact")
        ):
            issues.append("architecture job consumes another gate or coverage artifact")
        architecture_names = _artifact_names(architecture)
        coverage_names = _artifact_names(coverage)
        if not architecture_names:
            issues.append("architecture job has no independent report artifact")
        if not coverage_names:
            issues.append(f"{coverage_id} has no independent coverage artifact")
        if architecture_names & coverage_names:
            issues.append("architecture and coverage artifact names overlap")
        coverage_upload = "\n".join(
            str(step.get("with", {}).get("path", ""))
            for step in _upload_steps(coverage)
            if isinstance(step.get("with"), dict)
        )
        if "coverage.lcov" not in coverage_upload or "changed-lines" not in coverage_upload:
            issues.append("backend coverage upload lacks LCOV or committed report")
    _fail("F151_CI_WIRING_MISSING", issues)


def test_backend_coverage_generates_fresh_lcov_before_committed_checker() -> None:
    issues: list[str] = []
    coverage_jobs = [
        job for _, job in _jobs().values() if "check-changed-lines-coverage.py" in _run_text(job)
    ]
    if len(coverage_jobs) != 1:
        _fail(
            "F151_CI_WIRING_MISSING",
            [f"expected one backend coverage job, got {len(coverage_jobs)}"],
        )
        return
    run = _run_text(coverage_jobs[0])
    ordered = (
        "rm -f coverage.lcov",
        "fresh_after_utc",
        "--cov-report=lcov:coverage.lcov",
        "check-changed-lines-coverage.py",
    )
    positions = [run.find(token) for token in ordered]
    if any(position < 0 for position in positions) or positions != sorted(positions):
        issues.append("fresh LCOV reset/start/generation/checker order is not exact")
    required = (
        "F151_COVERAGE_STAGE",
        "--mode committed",
        "--fresh-after-utc",
        "--expected-stage-task",
        "--expected-head-sha",
        "--expected-head-tree",
        "--expected-worktree-fingerprint",
        "--report-json",
    )
    issues.extend(
        f"committed coverage command missing {token}" for token in required if token not in run
    )
    _fail("F151_CI_WIRING_MISSING", issues)


def test_benchmark_lane_is_independent_and_frontend_runs_full_vitest_and_tsc(
    lane_mod: Any,
) -> None:
    issues: list[str] = []
    jobs = _jobs()
    benchmark_entry = jobs.get("benchmark")
    frontend_entry = jobs.get("frontend")
    if benchmark_entry is None:
        issues.append("independent benchmark job missing")
    else:
        _, benchmark = benchmark_entry
        benchmark_run = _run_text(benchmark)
        if benchmark.get("needs"):
            issues.append("benchmark job must not depend on backend or architecture")
        issues.extend(
            f"benchmark job missing exact node {node}"
            for node in BENCHMARK_NODES
            if node not in benchmark_run
        )
    if frontend_entry is None:
        issues.append("frontend job missing")
    else:
        frontend_run = _run_text(frontend_entry[1])
        if "npm exec vitest -- run" not in frontend_run:
            issues.append("frontend does not run the full Vitest command")
        if "npm exec tsc -- -b" not in frontend_run:
            issues.append("frontend does not run the TypeScript build check")
        if "--exclude" in frontend_run:
            issues.append("frontend full gate still excludes tests")
    for mode in ("baseline", "release"):
        lanes = [lane for lane in lane_mod.lanes_for_mode(mode) if lane.id == "benchmark-unit"]
        if len(lanes) != 1:
            issues.append(f"{mode} must contain one independent benchmark-unit lane")
            continue
        command = " ".join(lanes[0].command)
        issues.extend(
            f"{mode} benchmark lane missing exact node {node}"
            for node in BENCHMARK_NODES
            if node not in command
        )
    _fail("F151_CI_WIRING_MISSING", issues)
