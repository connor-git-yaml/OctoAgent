"""F103d Phase D T-D-4 — reporter.py JSON + Markdown + delta 单测."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.runner.reporter import (
    DELTA_PRECISION,
    BenchmarkReport,
    archive_report,
    attach_delta_or_raise,
    compare_with_baseline,
    generate_report_from_runs,
    majority_result,
    render_markdown,
    report_to_baseline_record,
    write_report_json,
    write_report_markdown,
)
from benchmarks.runner.store import (
    RESULT_FAIL,
    RESULT_INCONSISTENT,
    RESULT_PARTIAL,
    RESULT_PASS,
    RESULT_QUOTA_SKIP,
    RESULT_TIMEOUT,
    BenchmarkBaselineRecord,
    BenchmarkRunRecord,
    BenchmarkStore,
    make_baseline_id,
    make_run_id,
)


def _run(
    task_id: str,
    iteration: int,
    result: str,
    *,
    tier: int = 1,
    domain: str = "memory",
    score: float | None = 0.9,
    duration: float = 1.0,
    session: str = "s",
    tk_in: int = 100,
    tk_out: int = 50,
) -> BenchmarkRunRecord:
    return BenchmarkRunRecord(
        run_id=make_run_id(),
        bench_session_id=session,
        task_id=task_id,
        tier=tier,
        domain=domain,
        iteration=iteration,
        result=result,
        score=score,
        duration_seconds=duration,
        token_input=tk_in,
        token_output=tk_out,
    )


# ---------------------------------------------------------------------------
# majority_result
# ---------------------------------------------------------------------------


def test_majority_unanimous():
    res, note = majority_result([RESULT_PASS, RESULT_PASS, RESULT_PASS])
    assert res == RESULT_PASS
    assert note is None


def test_majority_two_out_of_three():
    res, note = majority_result([RESULT_PASS, RESULT_PASS, RESULT_FAIL])
    assert res == RESULT_PASS
    assert "majority=PASS" in (note or "")


def test_majority_no_majority_inconsistent():
    """3 个全异 → INCONSISTENT。"""
    res, note = majority_result([RESULT_PASS, RESULT_FAIL, RESULT_PARTIAL])
    assert res == RESULT_INCONSISTENT
    assert "no majority" in (note or "")


def test_majority_with_skip():
    """SKIP 参与多数判定（避免 PASS+SKIP+SKIP 被判 PASS）。"""
    res, _ = majority_result([RESULT_QUOTA_SKIP, RESULT_QUOTA_SKIP, RESULT_PASS])
    assert res == RESULT_QUOTA_SKIP


def test_majority_empty():
    res, note = majority_result([])
    assert res == RESULT_FAIL
    assert note is not None


# ---------------------------------------------------------------------------
# generate_report_from_runs
# ---------------------------------------------------------------------------


def test_generate_report_basic_structure():
    runs = [
        _run("T1-M-1", 1, RESULT_PASS, tier=1, domain="memory"),
        _run("T1-M-1", 2, RESULT_PASS, tier=1, domain="memory"),
        _run("T1-M-1", 3, RESULT_PASS, tier=1, domain="memory"),
        _run("T2-TAU-1", 1, RESULT_FAIL, tier=2, domain="tau_bench_airline"),
        _run("T2-GAIA-1", 1, RESULT_PASS, tier=2, domain="gaia_fallback"),
        _run("T3-H1-1", 1, RESULT_PASS, tier=3, domain="philosophy_h1"),
    ]
    report = generate_report_from_runs(
        runs, session_id="s", commit_sha="abc1234"
    )
    assert report.summary["total_tasks"] == 4  # T1-M-1, T2-TAU-1, T2-GAIA-1, T3-H1-1
    assert report.summary["total_iterations"] == 6
    assert "tier1" in report.by_tier
    assert "tier2" in report.by_tier
    assert "tier3" in report.by_tier
    # Tier 2 子拆分
    assert "tau_bench" in report.by_tier["tier2"]
    assert "gaia" in report.by_tier["tier2"]
    assert report.by_tier["tier2"]["tau_bench"]["tasks"] == 1
    assert report.by_tier["tier2"]["gaia"]["tasks"] == 1


def test_generate_report_pass_rate_excludes_skip_timeout():
    """AC3-4：QUOTA_SKIP / TIMEOUT 不计入分母。"""
    runs = [
        _run("T1", 1, RESULT_PASS, tier=1, domain="x"),
        _run("T2", 1, RESULT_QUOTA_SKIP, tier=1, domain="x"),
        _run("T3", 1, RESULT_TIMEOUT, tier=1, domain="x"),
    ]
    report = generate_report_from_runs(runs, session_id="s", commit_sha="x")
    # 分母 = 1（T1），pass = 1 → 1.0
    assert report.summary["pass_rate"] == 1.0
    assert report.by_tier["tier1"]["pass_rate"] == 1.0


def test_generate_report_inconsistent_count():
    runs = [
        _run("Tinc", 1, RESULT_PASS, tier=1, domain="x"),
        _run("Tinc", 2, RESULT_FAIL, tier=1, domain="x"),
        _run("Tinc", 3, RESULT_PARTIAL, tier=1, domain="x"),
    ]
    report = generate_report_from_runs(runs, session_id="s", commit_sha="x")
    assert report.summary["inconsistent_count"] == 1


def test_generate_report_token_usage_aggregate():
    runs = [
        _run("T1", 1, RESULT_PASS, tier=1, domain="x", tk_in=100, tk_out=50),
        _run("T1", 2, RESULT_PASS, tier=1, domain="x", tk_in=200, tk_out=100),
    ]
    report = generate_report_from_runs(runs, session_id="s", commit_sha="x")
    assert report.summary["token_usage"]["input"] == 300
    assert report.summary["token_usage"]["output"] == 150


def test_generate_report_task_details_sorted():
    runs = [
        _run("Zztask", 1, RESULT_PASS, tier=1, domain="memory"),
        _run("Atask", 1, RESULT_FAIL, tier=1, domain="memory"),
    ]
    report = generate_report_from_runs(runs, session_id="s", commit_sha="x")
    task_ids = [d["task_id"] for d in report.task_details]
    assert task_ids == ["Atask", "Zztask"]  # sorted by (tier, domain, task_id)


# ---------------------------------------------------------------------------
# Delta compare（W7 / AC1-4 / AC6-1）
# ---------------------------------------------------------------------------


def _build_baseline(passing_tasks: list[str], pass_rate: float) -> BenchmarkBaselineRecord:
    metrics = {
        "summary": {"pass_rate": pass_rate, "weighted_score": 0.6},
        "by_tier": {
            "tier1": {"pass_rate": pass_rate, "tasks": len(passing_tasks), "passed": len(passing_tasks)},
            "tier2": {},
            "tier3": {"pass_rate": 0.0, "tasks": 0, "passed": 0},
        },
        "by_domain": {},
    }
    task_results = {tid: RESULT_PASS for tid in passing_tasks}
    return BenchmarkBaselineRecord(
        baseline_id=make_baseline_id(),
        commit_sha="m5sha",
        label="m5-baseline",
        aggregated_metrics_json=json.dumps(metrics),
        task_results_json=json.dumps(task_results),
    )


def test_compare_with_baseline_attaches_delta():
    runs = [
        _run("T1", 1, RESULT_PASS, tier=1, domain="memory"),
        _run("T2", 1, RESULT_PASS, tier=1, domain="memory"),
    ]
    report = generate_report_from_runs(runs, session_id="s", commit_sha="now")
    baseline = _build_baseline(passing_tasks=["T1", "T2"], pass_rate=1.0)
    out = compare_with_baseline(report, baseline)
    assert out.delta is not None
    assert out.delta["baseline_label"] == "m5-baseline"


def test_compare_delta_precision_three_decimals():
    """W7：pass rate delta 精度 0.001。"""
    runs = [
        _run("T1", 1, RESULT_PASS, tier=1, domain="x"),
        _run("T2", 1, RESULT_FAIL, tier=1, domain="x"),
    ]
    report = generate_report_from_runs(runs, session_id="s", commit_sha="now")
    baseline = _build_baseline(passing_tasks=["T1", "T2"], pass_rate=1.0)
    out = compare_with_baseline(report, baseline)
    # current pass_rate = 0.5, baseline = 1.0 → delta = -0.500
    delta_str = out.delta["summary"]["pass_rate_delta"]
    assert delta_str == "-0.500"
    # 校验精度：必含 . + 3 位小数
    assert delta_str.count(".") == 1
    decimals = delta_str.split(".")[1]
    assert len(decimals) == DELTA_PRECISION


def test_compare_regressions_and_improvements():
    runs = [
        _run("T1", 1, RESULT_FAIL, tier=1, domain="x"),  # T1 baseline PASS → regression
        _run("T2", 1, RESULT_PASS, tier=1, domain="x"),  # T2 baseline FAIL → improvement
    ]
    report = generate_report_from_runs(runs, session_id="s", commit_sha="x")
    metrics = {
        "summary": {"pass_rate": 0.5, "weighted_score": 0.5},
        "by_tier": {"tier1": {"pass_rate": 0.5, "tasks": 2, "passed": 1}},
        "by_domain": {},
    }
    baseline = BenchmarkBaselineRecord(
        baseline_id=make_baseline_id(),
        commit_sha="m5",
        label="m5",
        aggregated_metrics_json=json.dumps(metrics),
        task_results_json=json.dumps({"T1": RESULT_PASS, "T2": RESULT_FAIL}),
    )
    out = compare_with_baseline(report, baseline)
    assert out.delta["regression_count"] == 1
    assert out.delta["improvement_count"] == 1
    regressions = out.delta["regressions"]
    assert regressions[0]["task_id"] == "T1"
    assert regressions[0]["current_result"] == RESULT_FAIL


def test_compare_signed_delta_format():
    """+/-/+0.000 各种格式。"""
    from benchmarks.runner.reporter import _signed_delta

    assert _signed_delta(0.75, 0.70) == "+0.050"
    assert _signed_delta(0.70, 0.75) == "-0.050"
    assert _signed_delta(0.50, 0.50) == "+0.000"


def test_attach_delta_or_raise_baseline_not_found(tmp_path: Path):
    """AC6-2：baseline not found 时报错。"""
    store = BenchmarkStore(tmp_path / "bench.db")
    runs = [_run("T1", 1, RESULT_PASS, tier=1, domain="x")]
    report = generate_report_from_runs(runs, session_id="s", commit_sha="x")
    with pytest.raises(FileNotFoundError, match="baseline not found"):
        attach_delta_or_raise(report, store, "m5-baseline")


def test_attach_delta_or_raise_baseline_present(tmp_path: Path):
    store = BenchmarkStore(tmp_path / "bench.db")
    baseline = _build_baseline(passing_tasks=["T1"], pass_rate=1.0)
    store.save_baseline(baseline)
    runs = [_run("T1", 1, RESULT_FAIL, tier=1, domain="x")]
    report = generate_report_from_runs(runs, session_id="s", commit_sha="x")
    out = attach_delta_or_raise(report, store, "m5-baseline")
    assert out.delta is not None


# ---------------------------------------------------------------------------
# 文件 IO
# ---------------------------------------------------------------------------


def test_write_report_json_round_trip(tmp_path: Path):
    runs = [_run("T1", 1, RESULT_PASS, tier=1, domain="memory")]
    report = generate_report_from_runs(runs, session_id="s", commit_sha="abc")
    path = tmp_path / "out.json"
    written = write_report_json(report, path)
    assert written.exists()
    loaded = json.loads(written.read_text(encoding="utf-8"))
    assert loaded["run_id"] == "s"
    assert "summary" in loaded
    assert "by_tier" in loaded


def test_render_markdown_has_sections(tmp_path: Path):
    runs = [_run("T1", 1, RESULT_PASS, tier=1, domain="memory")]
    report = generate_report_from_runs(runs, session_id="s", commit_sha="abc")
    md = render_markdown(report)
    assert "# OctoBench Daily Bench Report" in md
    assert "## Summary" in md
    assert "## By Tier" in md
    assert "## By Domain" in md


def test_render_markdown_includes_delta_section_when_present():
    runs = [_run("T1", 1, RESULT_PASS, tier=1, domain="x")]
    report = generate_report_from_runs(runs, session_id="s", commit_sha="x")
    baseline = _build_baseline(passing_tasks=["T1"], pass_rate=1.0)
    compare_with_baseline(report, baseline)
    md = render_markdown(report)
    assert "## Delta vs Baseline" in md


def test_archive_report_writes_json_and_md(tmp_path: Path):
    runs = [_run("T1", 1, RESULT_PASS, tier=1, domain="memory")]
    report = generate_report_from_runs(runs, session_id="s", commit_sha="abc1234")
    json_path, md_path = archive_report(report, tmp_path / "out", label="m5-test")
    assert json_path.exists()
    assert md_path.exists()
    assert "m5-test" in json_path.name


def test_report_to_baseline_record():
    runs = [
        _run("T1", 1, RESULT_PASS, tier=1, domain="memory"),
        _run("T2", 1, RESULT_FAIL, tier=1, domain="memory"),
    ]
    report = generate_report_from_runs(runs, session_id="s", commit_sha="abc")
    record = report_to_baseline_record(
        report, baseline_id="bsl-1", label="test"
    )
    metrics = json.loads(record.aggregated_metrics_json)
    assert "summary" in metrics
    task_results = json.loads(record.task_results_json)
    assert task_results == {"T1": RESULT_PASS, "T2": RESULT_FAIL}
