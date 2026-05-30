"""F103d Phase D T-D-7/T-D-8 — CLI + resume 单测."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from benchmarks.runner import cli as cli_mod
from benchmarks.runner.cli import (
    YamlTaskMeta,
    _dry_run_runner,
    _parse_tier_filter,
    _stub_runner,
    detect_commit_sha,
    load_yaml_task,
    main,
    resolve_runner,
)
from benchmarks.runner.store import (
    RESULT_INFRA_ERROR,
    RESULT_PASS,
    BenchmarkStore,
)
from benchmarks.runner.worker import (
    PlannedRun,
    TaskExecutionOutcome,
    planned_from_full_set,
    run_daily_bench,
)


# ---------------------------------------------------------------------------
# YAML task 加载
# ---------------------------------------------------------------------------


def test_load_yaml_task_minimal(tmp_path: Path):
    p = tmp_path / "t1_test.yaml"
    p.write_text(
        "task_id: T1-TEST\n"
        "tier: 1\n"
        "domain: memory\n"
        "prompt: anything\n",
        encoding="utf-8",
    )
    task = load_yaml_task(p)
    assert task.task_id == "T1-TEST"
    assert task.tier == 1
    assert task.domain == "memory"


def test_load_yaml_task_invalid_top_level(tmp_path: Path):
    p = tmp_path / "bad.yaml"
    p.write_text("- just a list\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_yaml_task(p)


def test_yaml_task_meta_implements_task_meta_protocol():
    """worker.TaskMeta Protocol 兼容性。"""
    from benchmarks.runner.worker import TaskMeta

    task = YamlTaskMeta(task_id="T", tier=1, domain="m", raw={})
    assert isinstance(task, TaskMeta)


# ---------------------------------------------------------------------------
# Tier filter
# ---------------------------------------------------------------------------


def test_parse_tier_filter_none():
    assert _parse_tier_filter(None) is None


def test_parse_tier_filter_subset():
    assert _parse_tier_filter("1,3") == {1, 3}


def test_parse_tier_filter_invalid_exits():
    with pytest.raises(SystemExit):
        _parse_tier_filter("1,foo")


# ---------------------------------------------------------------------------
# Runner resolution
# ---------------------------------------------------------------------------


def test_resolve_runner_default_to_stub():
    runner = resolve_runner(None, dry_run=False)
    assert runner is _stub_runner


def test_resolve_runner_dry_run_built_in():
    runner = resolve_runner(None, dry_run=True)
    assert runner is _dry_run_runner


def test_resolve_runner_invalid_spec_exits():
    with pytest.raises(SystemExit):
        resolve_runner("not-a-valid-spec", dry_run=False)


def test_resolve_runner_module_attr():
    """指向已存在的 callable："""
    runner = resolve_runner("benchmarks.runner.cli:_dry_run_runner", dry_run=False)
    assert runner is _dry_run_runner


def test_resolve_runner_factory_call():
    """factory 返回 callable 时也接受。"""
    import benchmarks.runner.cli as cli_mod

    def _factory():
        return _dry_run_runner

    cli_mod._test_factory = _factory  # type: ignore[attr-defined]
    try:
        runner = resolve_runner("benchmarks.runner.cli:_test_factory", dry_run=False)
        assert runner is _dry_run_runner
    finally:
        del cli_mod._test_factory  # type: ignore[attr-defined]


def test_resolve_runner_factory_typeerror_warns_uses_target(capsys):
    """MED-2 fix 回归：factory 签名不对（接受 args）时不再静默吞，warn 到 stderr。"""
    import benchmarks.runner.cli as cli_mod

    async def _runner_with_args(task, iteration, extra_arg):
        return _dry_run_runner  # signature 不符合 factory 模式

    cli_mod._test_runner_with_args = _runner_with_args  # type: ignore[attr-defined]
    try:
        target = resolve_runner(
            "benchmarks.runner.cli:_test_runner_with_args", dry_run=False
        )
        # 应该返回 target 自身 + warn
        assert target is _runner_with_args
        captured = capsys.readouterr()
        assert "factory" in captured.err.lower()
    finally:
        del cli_mod._test_runner_with_args  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# detect_commit_sha
# ---------------------------------------------------------------------------


def test_detect_commit_sha_returns_string():
    # 在 repo 里 OR 非 repo 里都应返回 str（"unknown" 兜底）
    sha = detect_commit_sha(Path.cwd())
    assert isinstance(sha, str)
    assert len(sha) > 0


# ---------------------------------------------------------------------------
# _stub_runner / _dry_run_runner
# ---------------------------------------------------------------------------


def test_stub_runner_returns_infra_error():
    from benchmarks.runner.worker import TaskExecutionOutcome

    class FakeT:
        task_id = "T"
        tier = 1
        domain = "x"

    out = asyncio.run(_stub_runner(FakeT(), 1))
    assert out.result == RESULT_INFRA_ERROR
    assert "stub runner" in (out.error_message or "")


def test_dry_run_runner_returns_pass():
    class FakeT:
        task_id = "T"
        tier = 1
        domain = "x"

    out = asyncio.run(_dry_run_runner(FakeT(), 1))
    assert out.result == RESULT_PASS
    assert out.score == 1.0


# ---------------------------------------------------------------------------
# CLI smoke: list-baselines / show
# ---------------------------------------------------------------------------


def test_cli_list_baselines_no_db_returns_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OCTOAGENT_BENCH_ROOT", str(tmp_path))
    # No DB created
    rc = main(["list-baselines"])
    assert rc == 1


def test_cli_show_no_baseline_returns_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OCTOAGENT_BENCH_ROOT", str(tmp_path))
    rc = main(["show", "nonexistent"])
    assert rc == 1


# ---------------------------------------------------------------------------
# T-D-8 RESUME 验证（核心）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t_d_8_resume_only_runs_pending(tmp_path: Path):
    """T-D-8：跑 partial 后 resume 只续未完成 task。

    步骤：
    1. 跑 3 个 task × 2 iterations = 6 planned
    2. 提前完成 4 个（模拟"跑到 3 个 task #1 + 第 1 个 task #2 后 Ctrl+C"）
    3. 用 filter_planned_for_resume + 同 session_id 续跑
    4. 验证只跑剩下 2 个；最终报告等价于全跑
    """
    db_path = tmp_path / "bench.db"
    store = BenchmarkStore(db_path)

    tasks = [YamlTaskMeta(f"T{i}", 1, "memory", {"task_id": f"T{i}"}) for i in range(3)]
    planned = planned_from_full_set(tasks, iterations=2)
    assert len(planned) == 6

    # 第一次跑 partial（4 个）
    await run_daily_bench(
        planned[:4],
        _dry_run_runner,
        store,
        session_id="rs-test",
        semaphore_size=2,
        ramp_delay_seconds=0.0,
    )
    completed_keys = store.get_completed_keys("rs-test")
    assert len(completed_keys) == 4

    # Resume：filter_planned_for_resume 返回剩下
    from benchmarks.runner.worker import filter_planned_for_resume

    remaining = filter_planned_for_resume(planned, store, "rs-test")
    assert len(remaining) == 2

    # 第二次跑 remaining
    await run_daily_bench(
        remaining,
        _dry_run_runner,
        store,
        session_id="rs-test",
        semaphore_size=2,
        ramp_delay_seconds=0.0,
    )

    final_runs = store.get_runs_for_session("rs-test")
    assert len(final_runs) == 6

    # 验证报告
    from benchmarks.runner.reporter import generate_report

    report = generate_report("rs-test", store, commit_sha="x")
    assert report.summary["total_iterations"] == 6
    assert report.summary["total_tasks"] == 3
    assert report.summary["pass_rate"] == 1.0


def test_cli_daily_dry_run_creates_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    """CLI smoke: --dry-run --tier 仅 Tier 1/3 不需要 preflight，能跑通。"""
    # 准备 tier1 task
    benchmarks_root = tmp_path / "benchmarks"
    tier1_dir = benchmarks_root / "tiers" / "tier1"
    tier1_dir.mkdir(parents=True)
    (tier1_dir / "t1_smoke.yaml").write_text(
        "task_id: T1-SMOKE\ntier: 1\ndomain: memory\nprompt: x\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "baselines"

    # repo_root 设置
    monkeypatch.setenv("OCTOAGENT_BENCH_ROOT", str(tmp_path))

    rc = main(
        [
            "daily",
            "--dry-run",
            "--tier",
            "1",
            "--iterations",
            "2",
            "--semaphore",
            "2",
            "--ramp",
            "0.0",
            "--db-path",
            str(tmp_path / "bench.db"),
            "--out-dir",
            str(out_dir),
            "--skip-preflight",
        ]
    )
    assert rc == 0
    # 报告归档
    json_files = list(out_dir.glob("*.json"))
    md_files = list(out_dir.glob("*.md"))
    assert json_files, "未生成 JSON 报告"
    assert md_files, "未生成 Markdown 报告"
    # SQLite 持久化
    store = BenchmarkStore(tmp_path / "bench.db")
    sessions = store.get_session_ids()
    assert sessions, "session 未持久化"


def test_cli_daily_resume_via_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """T-D-8 端到端：通过 CLI 跑两次（partial + resume）。"""
    benchmarks_root = tmp_path / "benchmarks"
    tier1_dir = benchmarks_root / "tiers" / "tier1"
    tier1_dir.mkdir(parents=True)
    for i in range(3):
        (tier1_dir / f"t1_r{i}.yaml").write_text(
            f"task_id: T1-R{i}\ntier: 1\ndomain: memory\nprompt: x\n", encoding="utf-8"
        )
    out_dir = tmp_path / "baselines"
    db_path = tmp_path / "bench.db"
    monkeypatch.setenv("OCTOAGENT_BENCH_ROOT", str(tmp_path))

    # 第一次跑（限 iterations=2 + 全 3 task = 6 planned）
    rc = main(
        [
            "daily",
            "--dry-run",
            "--tier",
            "1",
            "--iterations",
            "2",
            "--semaphore",
            "2",
            "--ramp",
            "0.0",
            "--db-path",
            str(db_path),
            "--out-dir",
            str(out_dir),
            "--skip-preflight",
        ]
    )
    assert rc == 0
    store = BenchmarkStore(db_path)
    [first_session] = store.get_session_ids()
    runs_first = store.get_runs_for_session(first_session)
    assert len(runs_first) == 6

    # 第二次：resume 同 session_id（已全完成，应该 nothing to run）
    rc2 = main(
        [
            "daily",
            "--dry-run",
            "--resume",
            first_session,
            "--tier",
            "1",
            "--iterations",
            "2",
            "--semaphore",
            "2",
            "--ramp",
            "0.0",
            "--db-path",
            str(db_path),
            "--out-dir",
            str(out_dir),
            "--skip-preflight",
        ]
    )
    assert rc2 == 0
    # session 不应新增 runs
    assert len(store.get_runs_for_session(first_session)) == 6


def test_cli_daily_label_saves_baseline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """--label m5-baseline 保存 baseline 行（Phase E T-E-4 / T-E-5 用）。"""
    benchmarks_root = tmp_path / "benchmarks"
    tier1_dir = benchmarks_root / "tiers" / "tier1"
    tier1_dir.mkdir(parents=True)
    (tier1_dir / "t1_a.yaml").write_text(
        "task_id: T1-A\ntier: 1\ndomain: memory\nprompt: x\n", encoding="utf-8"
    )
    out_dir = tmp_path / "baselines"
    db_path = tmp_path / "bench.db"
    monkeypatch.setenv("OCTOAGENT_BENCH_ROOT", str(tmp_path))

    rc = main(
        [
            "daily",
            "--dry-run",
            "--label",
            "ut-bsl",
            "--tier",
            "1",
            "--iterations",
            "1",
            "--semaphore",
            "1",
            "--ramp",
            "0.0",
            "--db-path",
            str(db_path),
            "--out-dir",
            str(out_dir),
            "--skip-preflight",
        ]
    )
    assert rc == 0
    store = BenchmarkStore(db_path)
    fetched = store.get_baseline("ut-bsl")
    assert fetched is not None


def test_cli_daily_compare_baseline_not_found_returns_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """AC6-2：--compare 时 baseline not found 报错（rc=1）。"""
    benchmarks_root = tmp_path / "benchmarks"
    tier1_dir = benchmarks_root / "tiers" / "tier1"
    tier1_dir.mkdir(parents=True)
    (tier1_dir / "t1_a.yaml").write_text(
        "task_id: T1-A\ntier: 1\ndomain: memory\nprompt: x\n", encoding="utf-8"
    )
    db_path = tmp_path / "bench.db"
    monkeypatch.setenv("OCTOAGENT_BENCH_ROOT", str(tmp_path))

    rc = main(
        [
            "daily",
            "--dry-run",
            "--compare",
            "no-such-baseline",
            "--tier",
            "1",
            "--iterations",
            "1",
            "--semaphore",
            "1",
            "--ramp",
            "0.0",
            "--db-path",
            str(db_path),
            "--out-dir",
            str(tmp_path / "baselines"),
            "--skip-preflight",
        ]
    )
    assert rc == 1


def test_cli_daily_compare_baseline_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """--compare 成功路径：先建 baseline，再 compare。"""
    benchmarks_root = tmp_path / "benchmarks"
    tier1_dir = benchmarks_root / "tiers" / "tier1"
    tier1_dir.mkdir(parents=True)
    (tier1_dir / "t1_a.yaml").write_text(
        "task_id: T1-A\ntier: 1\ndomain: memory\nprompt: x\n", encoding="utf-8"
    )
    db_path = tmp_path / "bench.db"
    out_dir = tmp_path / "baselines"
    monkeypatch.setenv("OCTOAGENT_BENCH_ROOT", str(tmp_path))

    # 1) 先建 baseline
    rc1 = main(
        [
            "daily",
            "--dry-run",
            "--label",
            "m5-baseline",
            "--tier",
            "1",
            "--iterations",
            "1",
            "--semaphore",
            "1",
            "--ramp",
            "0.0",
            "--db-path",
            str(db_path),
            "--out-dir",
            str(out_dir),
            "--skip-preflight",
        ]
    )
    assert rc1 == 0

    # 2) 用 --compare 跑（结果应等于 baseline，delta 为 0）
    rc2 = main(
        [
            "daily",
            "--dry-run",
            "--compare",
            "m5-baseline",
            "--tier",
            "1",
            "--iterations",
            "1",
            "--semaphore",
            "1",
            "--ramp",
            "0.0",
            "--db-path",
            str(db_path),
            "--out-dir",
            str(out_dir),
            "--skip-preflight",
        ]
    )
    assert rc2 == 0
    # 报告 JSON 含 delta 区块
    json_files = sorted(out_dir.glob("*.json"))
    last = json.loads(json_files[-1].read_text())
    assert "delta" in last
