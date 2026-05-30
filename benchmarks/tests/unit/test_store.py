"""F103d Phase D T-D-2 — BenchmarkStore 单测."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.runner.store import (
    EXCLUDED_FROM_DENOMINATOR,
    RESULT_FAIL,
    RESULT_PASS,
    RESULT_QUOTA_SKIP,
    BenchmarkBaselineRecord,
    BenchmarkRunRecord,
    BenchmarkStore,
    make_baseline_id,
    make_run_id,
    make_session_id,
    utcnow_iso,
)


def _make_run(
    task_id: str = "T1-X",
    iteration: int = 1,
    *,
    session: str = "s1",
    tier: int = 1,
    domain: str = "memory",
    result: str = RESULT_PASS,
    score: float | None = 0.9,
    duration: float = 1.5,
    token_input: int | None = 100,
    token_output: int | None = 50,
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
        token_input=token_input,
        token_output=token_output,
    )


@pytest.fixture()
def store(tmp_path: Path) -> BenchmarkStore:
    return BenchmarkStore(tmp_path / "bench.db")


def test_run_record_validates_result():
    with pytest.raises(ValueError, match="Invalid result"):
        BenchmarkRunRecord(
            run_id="r1",
            bench_session_id="s1",
            task_id="T1",
            tier=1,
            domain="x",
            iteration=1,
            result="NOT_A_RESULT",
            score=None,
            duration_seconds=0.0,
        )


def test_run_record_validates_tier():
    with pytest.raises(ValueError, match="Invalid tier"):
        BenchmarkRunRecord(
            run_id="r1",
            bench_session_id="s1",
            task_id="T1",
            tier=5,  # invalid
            domain="x",
            iteration=1,
            result=RESULT_PASS,
            score=None,
            duration_seconds=0.0,
        )


def test_run_record_validates_iteration():
    with pytest.raises(ValueError, match="Invalid iteration"):
        BenchmarkRunRecord(
            run_id="r1",
            bench_session_id="s1",
            task_id="T1",
            tier=1,
            domain="x",
            iteration=0,  # invalid
            result=RESULT_PASS,
            score=None,
            duration_seconds=0.0,
        )


def test_run_record_validates_duration_negative():
    with pytest.raises(ValueError, match="Invalid duration_seconds"):
        BenchmarkRunRecord(
            run_id="r1",
            bench_session_id="s1",
            task_id="T1",
            tier=1,
            domain="x",
            iteration=1,
            result=RESULT_PASS,
            score=None,
            duration_seconds=-1.0,
        )


def test_append_run_fills_created_at(store: BenchmarkStore):
    rec = _make_run()
    written = store.append_run(rec)
    assert written.created_at  # 非空
    assert "T" in written.created_at  # ISO 8601 含 T


def test_append_run_idempotent_replace(store: BenchmarkStore):
    """同 session+task+iteration REPLACE（resume idempotent）。"""
    rec1 = _make_run(score=0.5, result=RESULT_FAIL)
    written1 = store.append_run(rec1)
    # 同 key 但不同 run_id + 不同 score
    rec2 = _make_run(score=0.9, result=RESULT_PASS)
    written2 = store.append_run(rec2)

    runs = store.get_runs_for_session("s1")
    assert len(runs) == 1
    assert runs[0].score == 0.9
    assert runs[0].result == RESULT_PASS


def test_get_completed_keys(store: BenchmarkStore):
    store.append_run(_make_run("T1", 1))
    store.append_run(_make_run("T1", 2))
    store.append_run(_make_run("T2", 1, session="s1"))
    store.append_run(_make_run("T3", 1, session="s2"))

    keys_s1 = store.get_completed_keys("s1")
    assert keys_s1 == {("T1", 1), ("T1", 2), ("T2", 1)}
    keys_s2 = store.get_completed_keys("s2")
    assert keys_s2 == {("T3", 1)}


def test_get_pending_runs_diff_planned(store: BenchmarkStore):
    """resume: planned - completed."""
    store.append_run(_make_run("T1", 1))
    store.append_run(_make_run("T1", 2))
    planned = [("T1", 1), ("T1", 2), ("T1", 3), ("T2", 1)]
    pending = store.get_pending_runs("s1", planned)
    pending_keys = [(p.task_id, p.iteration) for p in pending]
    assert pending_keys == [("T1", 3), ("T2", 1)]


def test_get_pending_runs_preserves_order(store: BenchmarkStore):
    """pending 返回必须保留 planned 顺序（caller 按顺序续跑）。"""
    planned = [("Z", 1), ("A", 2), ("M", 1)]
    pending = store.get_pending_runs("s1", planned)
    pending_keys = [(p.task_id, p.iteration) for p in pending]
    assert pending_keys == [("Z", 1), ("A", 2), ("M", 1)]


def test_get_pending_runs_deduplicates(store: BenchmarkStore):
    """planned 含重复 (task, iter) 时只返回一次。"""
    planned = [("X", 1), ("X", 1), ("Y", 1)]
    pending = store.get_pending_runs("s1", planned)
    pending_keys = [(p.task_id, p.iteration) for p in pending]
    assert pending_keys == [("X", 1), ("Y", 1)]


def test_save_baseline_and_get(store: BenchmarkStore):
    baseline = BenchmarkBaselineRecord(
        baseline_id=make_baseline_id(),
        commit_sha="abc1234",
        label="m5-baseline",
        aggregated_metrics_json='{"summary": {"pass_rate": 0.72}}',
        task_results_json='{"T1": "PASS"}',
        duration_minutes=47.3,
    )
    written = store.save_baseline(baseline)
    assert written.created_at

    fetched = store.get_baseline("m5-baseline")
    assert fetched is not None
    assert fetched.baseline_id == baseline.baseline_id
    assert fetched.commit_sha == "abc1234"


def test_get_baseline_not_found(store: BenchmarkStore):
    assert store.get_baseline("nonexistent") is None


def test_get_baseline_returns_latest_per_label(store: BenchmarkStore):
    """同 label 多次保存时返回最新（created_at DESC）。"""
    for sha, ts in (("aaa", "2026-05-30T10:00:00Z"), ("bbb", "2026-05-30T12:00:00Z")):
        store.save_baseline(
            BenchmarkBaselineRecord(
                baseline_id=make_baseline_id(),
                commit_sha=sha,
                label="m5",
                aggregated_metrics_json="{}",
                task_results_json="{}",
                created_at=ts,
            )
        )
    fetched = store.get_baseline("m5")
    assert fetched is not None
    assert fetched.commit_sha == "bbb"


def test_excluded_from_denominator_constants():
    """AC3-4：分母不含 QUOTA_SKIP / TIMEOUT / INFRA_ERROR。"""
    assert "QUOTA_SKIP" in EXCLUDED_FROM_DENOMINATOR
    assert "TIMEOUT" in EXCLUDED_FROM_DENOMINATOR
    assert "INFRA_ERROR" in EXCLUDED_FROM_DENOMINATOR
    assert "PASS" not in EXCLUDED_FROM_DENOMINATOR
    assert "FAIL" not in EXCLUDED_FROM_DENOMINATOR


def test_session_id_make_uniqueness():
    a = make_session_id()
    b = make_session_id()
    assert a != b
    assert a.startswith("sess-")


def test_list_baselines(store: BenchmarkStore):
    for label, ts in (("m5", "2026-05-30T10:00:00Z"), ("m6-f104", "2026-05-30T12:00:00Z")):
        store.save_baseline(
            BenchmarkBaselineRecord(
                baseline_id=make_baseline_id(),
                commit_sha="x",
                label=label,
                aggregated_metrics_json="{}",
                task_results_json="{}",
                created_at=ts,
            )
        )
    rows = store.list_baselines()
    assert {r.label for r in rows} == {"m5", "m6-f104"}


def test_db_path_directory_auto_created(tmp_path: Path):
    """BenchmarkStore 自动创建父目录（避免 'no such directory' 启动失败）。"""
    nested = tmp_path / "deep" / "nested" / "bench.db"
    store = BenchmarkStore(nested)
    assert nested.parent.is_dir()


def test_wal_mode_enabled(store: BenchmarkStore, tmp_path: Path):
    """PoC-H3：WAL 模式必须启用（并发安全）。"""
    conn = store._connect()
    journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert str(journal_mode).lower() == "wal"
