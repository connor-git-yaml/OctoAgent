"""F103d Phase D T-D-7 — ``octo-bench daily`` CLI 主逻辑.

入口由 ``apps/gateway/src/octoagent/gateway/cli/bench_commands.py`` thin wrapper
调用本模块的 ``main(argv)``。

子命令：

- ``daily``：跑 Daily Bench（默认全 50 task × 3 iterations × 8 并发）
  - ``--label <label>``：归档时 baseline label（如 ``m5-baseline``）
  - ``--resume <session_id>``：续跑（AC5-1）
  - ``--compare <baseline_label>``：对比基线（AC6-1）
  - ``--commit <sha>``：标记本次 baseline commit（默认 ``git rev-parse HEAD``）
  - ``--db-path <path>``：覆盖默认 store DB（``benchmarks/baselines/bench.db``）
  - ``--out-dir <path>``：覆盖归档目录（默认 ``benchmarks/baselines/``）
  - ``--iterations <n>``：覆盖 iteration 次数（默认 3）
  - ``--semaphore <n>``：覆盖并发上限（默认 8）
  - ``--ramp <s>``：覆盖 gradual ramp 延迟（默认 0.5）
  - ``--dry-run``：用 stub runner 跑（不调 LLM，仅验证 resume / report / SQLite 路径）
  - ``--runner <module:attr>``：注入真实 runner_fn（Phase E 用；默认 stub）
  - ``--skip-preflight``：跳过 tau_bench/datasets 依赖检查（仅 Tier 1/3）
  - ``--tier <1,3>``：限制跑哪些 tier（逗号分隔）

- ``list-baselines``：列出 SQLite 中所有 baseline
- ``show <label>``：打印 baseline 摘要 JSON
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import logging
import os
import random
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from benchmarks.runner.preflight import check_or_fail as _preflight_check_or_fail
from benchmarks.runner.reporter import (
    BenchmarkReport,
    archive_report,
    attach_delta_or_raise,
    generate_report,
    report_to_baseline_record,
)
from benchmarks.runner.store import (
    BenchmarkRunRecord,
    BenchmarkStore,
    RESULT_INFRA_ERROR,
    RESULT_PASS,
    make_baseline_id,
    make_session_id,
)
from benchmarks.runner.worker import (
    DEFAULT_RAMP_DELAY_SECONDS,
    DEFAULT_SEMAPHORE_SIZE,
    PlannedRun,
    TaskExecutionOutcome,
    TaskMeta,
    ConsecutiveInfraErrorCounter,
    filter_planned_for_resume,
    planned_from_full_set,
    run_daily_bench,
)


logger = logging.getLogger("octobench.cli")


# ---------------------------------------------------------------------------
# TaskMeta 适配器（YAML dict → TaskMeta-compatible）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class YamlTaskMeta:
    """从 YAML 加载的 task 字段最小子集（TaskMeta 兼容）。"""

    task_id: str
    tier: int
    domain: str
    raw: dict[str, Any]


# ---------------------------------------------------------------------------
# 默认 stub runner（Phase E 由用户 wire 真实 OctoHarness 路径）
# ---------------------------------------------------------------------------


async def _stub_runner(task: TaskMeta, iteration: int) -> TaskExecutionOutcome:
    """``--dry-run`` 默认 runner：立即返回 INFRA_ERROR 标识"未 wire"。

    Phase D 验收（T-D-8 resume）改用 ``--runner benchmarks.runner.runners.dry:fast_pass``
    或在 cli `--dry-run` 时 wire 内置 ``_dry_run_runner``（PASS 立返）。
    """
    return TaskExecutionOutcome(
        result=RESULT_INFRA_ERROR,
        score=None,
        duration_seconds=0.0,
        error_message=(
            "stub runner — Phase E 前需 wire 真实 OctoHarness 路径，"
            "用 `--runner <module>:<factory>` 注入"
        ),
    )


async def _dry_run_runner(task: TaskMeta, iteration: int) -> TaskExecutionOutcome:
    """``--dry-run`` 内置 runner：所有 task PASS（仅验证 SQLite + reporter 路径）。

    用于 T-D-8 resume 验证（无需 LLM key）。
    """
    return TaskExecutionOutcome(
        result=RESULT_PASS,
        score=1.0,
        duration_seconds=0.01,
        token_input=0,
        token_output=0,
        error_message=None,
    )


# ---------------------------------------------------------------------------
# Runner 注入（importlib 解析 module:attr）
# ---------------------------------------------------------------------------


def resolve_runner(spec: str | None, *, dry_run: bool) -> Any:
    """解析 ``--runner module:attr``；优先级 spec > dry_run > stub.

    MED-2 fix：factory call TypeError 不再静默吞，改 log warning + 退回 target 自身用
    （让用户能在 stderr 看到"factory 签名不对"提示，避免 silent fallback 引入二次错误）。
    """
    if spec:
        try:
            module_name, _, attr = spec.partition(":")
            if not module_name or not attr:
                raise ValueError(f"invalid --runner spec {spec!r}; expected 'module:attr'")
            mod = importlib.import_module(module_name)
            target = getattr(mod, attr)
        except Exception as exc:
            print(f"[octo-bench] resolve --runner {spec!r} failed: {exc!r}", file=sys.stderr)
            sys.exit(2)
        # 如果是 factory（callable returning runner），尝试调用一次
        if callable(target):
            try:
                maybe = target()
            except TypeError as exc:
                # 不是 zero-arg factory，假定 target 自身就是 runner_fn
                # MED-2 fix：log 让 caller 能调试
                print(
                    f"[octo-bench] --runner {spec!r}: target {attr!r} 不是 zero-arg factory "
                    f"({exc!r}); 假定 target 自身就是 async runner_fn",
                    file=sys.stderr,
                )
                return target
            return maybe if callable(maybe) else target
        return target

    if dry_run:
        return _dry_run_runner
    return _stub_runner


# ---------------------------------------------------------------------------
# YAML task 加载
# ---------------------------------------------------------------------------


def discover_task_yamls(tier_dirs: dict[int, Path]) -> dict[int, list[Path]]:
    """扫描各 tier 目录下的 YAML（非 *_fallback*.yaml 不限定）。"""
    out: dict[int, list[Path]] = {1: [], 3: []}
    for tier, base_dir in tier_dirs.items():
        if not base_dir.is_dir():
            continue
        for path in sorted(base_dir.glob("*.yaml")):
            # 过滤明显非 task 的 yaml（rubrics / fallback list）
            name = path.name.lower()
            if "rubrics" in name:
                continue
            out.setdefault(tier, []).append(path)
    return out


def load_yaml_task(path: Path) -> YamlTaskMeta:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"YAML 顶层不是 dict: {path}")
    task_id = str(raw.get("task_id") or path.stem)
    tier = int(raw.get("tier", 0))
    domain = str(raw.get("domain", ""))
    return YamlTaskMeta(task_id=task_id, tier=tier, domain=domain, raw=raw)


def load_tier1_tier3_tasks(repo_root: Path) -> list[YamlTaskMeta]:
    """加载 Tier 1 + Tier 3 task（不含 Tier 2，需要 preflight）。"""
    tiers = discover_task_yamls(
        {
            1: repo_root / "benchmarks" / "tiers" / "tier1",
            3: repo_root / "benchmarks" / "tiers" / "tier3",
        }
    )
    tasks: list[YamlTaskMeta] = []
    for paths in tiers.values():
        for p in paths:
            try:
                tasks.append(load_yaml_task(p))
            except Exception as exc:
                logger.warning("skip_yaml_load_failure", extra={"path": str(p), "error": repr(exc)})
    # 过滤 PLACEHOLDER（Connor 4 task 未拍板时不计入）
    tasks = [t for t in tasks if not _is_placeholder(t)]
    return tasks


def _is_placeholder(task: YamlTaskMeta) -> bool:
    return str(task.raw.get("status", "")).upper() == "PLACEHOLDER"


# ---------------------------------------------------------------------------
# Tier 2 加载（需要 preflight）
# ---------------------------------------------------------------------------


def load_tier2_tasks(repo_root: Path) -> list[YamlTaskMeta]:
    """加载 Tier 2 τ-bench airline + GAIA fallback YAML（PoC-H1 走 fallback）。"""
    tasks: list[YamlTaskMeta] = []

    # GAIA fallback（YAML）—— 直接 import adapter 拿元数据
    try:
        from benchmarks.tiers.tier2.gaia_fallback_adapter import load_fallback_tasks

        for meta in load_fallback_tasks():
            tasks.append(
                YamlTaskMeta(
                    task_id=meta.task_id,
                    tier=2,
                    domain="gaia_fallback",
                    raw={"task_id": meta.task_id, "tier": 2, "domain": "gaia_fallback"},
                )
            )
    except Exception as exc:
        logger.warning("gaia_fallback_load_failed", extra={"error": repr(exc)})

    # τ-bench airline（adapter 走 production tau_bench 库）
    try:
        from benchmarks.tiers.tier2.tau_bench_adapter import load_airline_tasks

        for meta in load_airline_tasks():
            tasks.append(
                YamlTaskMeta(
                    task_id=meta.task_id,
                    tier=2,
                    domain="tau_bench_airline",
                    raw={"task_id": meta.task_id, "tier": 2, "domain": "tau_bench_airline"},
                )
            )
    except Exception as exc:
        logger.warning("tau_bench_load_failed", extra={"error": repr(exc)})

    return tasks


# ---------------------------------------------------------------------------
# Commit SHA helper
# ---------------------------------------------------------------------------


def detect_commit_sha(repo_root: Path) -> str:
    """git rev-parse HEAD。失败返回 "unknown"。"""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return "unknown"


# ---------------------------------------------------------------------------
# CLI 主入口
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="octo-bench", description="OctoBench Daily Bench CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # daily
    daily = sub.add_parser("daily", help="Run a Daily Bench (50 task × 3 iter × 8 concurrent)")
    daily.add_argument("--label", default=None, help="archive label (e.g. m5-baseline)")
    daily.add_argument("--resume", default=None, help="session_id to resume (AC5-1)")
    daily.add_argument("--compare", default=None, help="baseline label to compare against")
    daily.add_argument("--commit", default=None, help="override commit_sha (default: git HEAD)")
    daily.add_argument(
        "--db-path",
        default=None,
        help="override BenchmarkStore DB path (default: benchmarks/baselines/bench.db)",
    )
    daily.add_argument(
        "--out-dir",
        default=None,
        help="override report archive dir (default: benchmarks/baselines/)",
    )
    daily.add_argument("--iterations", type=int, default=3, help="iterations per task")
    daily.add_argument(
        "--semaphore", type=int, default=DEFAULT_SEMAPHORE_SIZE, help="concurrency limit"
    )
    daily.add_argument(
        "--ramp", type=float, default=DEFAULT_RAMP_DELAY_SECONDS, help="gradual ramp seconds"
    )
    daily.add_argument(
        "--dry-run",
        action="store_true",
        help="use built-in dry-run runner (no LLM); for T-D-8 resume verification",
    )
    daily.add_argument(
        "--runner",
        default=None,
        help="inject runner via 'module:factory_or_callable' (Phase E)",
    )
    daily.add_argument(
        "--skip-preflight",
        action="store_true",
        help="skip tau_bench/datasets preflight check (only Tier 1/3 will run)",
    )
    daily.add_argument(
        "--tier",
        default=None,
        help="comma-separated tiers to include (e.g. '1,3'); default all",
    )

    # list-baselines
    sub.add_parser("list-baselines", help="List all archived baselines")

    # show
    show = sub.add_parser("show", help="Print baseline metrics JSON by label")
    show.add_argument("label", help="baseline label")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.cmd == "daily":
        return _cmd_daily(args)
    if args.cmd == "list-baselines":
        return _cmd_list_baselines(args)
    if args.cmd == "show":
        return _cmd_show(args)
    return 2


def _resolve_repo_root() -> Path:
    """从 OCTOAGENT_BENCH_ROOT env 或 cwd 推导 repo root。"""
    env = os.environ.get("OCTOAGENT_BENCH_ROOT")
    if env:
        return Path(env).resolve()
    return Path.cwd().resolve()


def _resolve_db_path(args: argparse.Namespace, repo_root: Path) -> Path:
    db_path_attr = getattr(args, "db_path", None)
    if db_path_attr:
        return Path(db_path_attr).expanduser().resolve()
    return repo_root / "benchmarks" / "baselines" / "bench.db"


def _resolve_out_dir(args: argparse.Namespace, repo_root: Path) -> Path:
    out_dir_attr = getattr(args, "out_dir", None)
    if out_dir_attr:
        return Path(out_dir_attr).expanduser().resolve()
    return repo_root / "benchmarks" / "baselines"


def _parse_tier_filter(value: str | None) -> set[int] | None:
    if not value:
        return None
    out: set[int] = set()
    for piece in value.split(","):
        piece = piece.strip()
        if piece:
            try:
                out.add(int(piece))
            except ValueError:
                print(f"[octo-bench] invalid --tier value {piece!r}", file=sys.stderr)
                sys.exit(2)
    return out


def _cmd_daily(args: argparse.Namespace) -> int:
    repo_root = _resolve_repo_root()
    db_path = _resolve_db_path(args, repo_root)
    out_dir = _resolve_out_dir(args, repo_root)
    commit_sha = args.commit or detect_commit_sha(repo_root)
    tier_filter = _parse_tier_filter(args.tier)

    # 加载 task
    tasks: list[YamlTaskMeta] = []
    include_tier_1_3 = tier_filter is None or (1 in tier_filter or 3 in tier_filter)
    include_tier_2 = tier_filter is None or (2 in tier_filter)

    if include_tier_1_3:
        tasks.extend(load_tier1_tier3_tasks(repo_root))

    if include_tier_2:
        if not args.skip_preflight:
            try:
                _preflight_check_or_fail()
            except SystemExit as e:
                if e.code != 0:
                    print(
                        "[octo-bench] preflight failed; rerun with --skip-preflight to run only "
                        "Tier 1/3, or install tau_bench/datasets first.",
                        file=sys.stderr,
                    )
                    return int(e.code or 2)
        tasks.extend(load_tier2_tasks(repo_root))

    if tier_filter is not None:
        tasks = [t for t in tasks if t.tier in tier_filter]

    if not tasks:
        print("[octo-bench] no tasks discovered after filter; abort", file=sys.stderr)
        return 2

    print(f"[octo-bench] loaded {len(tasks)} task(s); iterations={args.iterations}")

    # Planning + resume
    store = BenchmarkStore(db_path)
    session_id = args.resume or make_session_id()
    is_resume = bool(args.resume)
    full_plan: list[PlannedRun] = planned_from_full_set(
        tasks, iterations=args.iterations
    )
    if is_resume:
        pending = filter_planned_for_resume(full_plan, store, session_id)
        print(
            f"[octo-bench] resume session={session_id} "
            f"pending={len(pending)}/{len(full_plan)}"
        )
    else:
        pending = full_plan
        print(f"[octo-bench] new session={session_id} total={len(full_plan)}")

    if not pending:
        print("[octo-bench] nothing to run (session already complete); generating report")
        return _finalize(args, store, session_id, commit_sha, out_dir)

    # Runner injection
    runner_fn = resolve_runner(args.runner, dry_run=args.dry_run)
    counter = ConsecutiveInfraErrorCounter()

    print(
        f"[octo-bench] runner={runner_fn.__name__} semaphore={args.semaphore} "
        f"ramp={args.ramp}s"
    )

    started = time.time()

    def _progress(record: BenchmarkRunRecord) -> None:
        print(
            f"[octo-bench] done {record.task_id}#{record.iteration} "
            f"result={record.result} dur={record.duration_seconds:.2f}s"
        )

    try:
        asyncio.run(
            run_daily_bench(
                pending,
                runner_fn,
                store,
                session_id,
                semaphore_size=args.semaphore,
                ramp_delay_seconds=args.ramp,
                counter=counter,
                on_record_written=_progress,
            )
        )
    except KeyboardInterrupt:
        print("[octo-bench] interrupted by user; partial results persisted in SQLite")
        return 130

    elapsed = time.time() - started
    print(f"[octo-bench] daily run finished in {elapsed:.1f}s session={session_id}")

    if counter.stopped:
        print(
            f"[octo-bench] WARNING: stopped due to {counter.consecutive_count} "
            f"consecutive INFRA_ERROR(s)",
            file=sys.stderr,
        )

    return _finalize(args, store, session_id, commit_sha, out_dir)


def _finalize(
    args: argparse.Namespace,
    store: BenchmarkStore,
    session_id: str,
    commit_sha: str,
    out_dir: Path,
) -> int:
    report = generate_report(session_id, store, commit_sha=commit_sha)

    if args.compare:
        try:
            attach_delta_or_raise(report, store, args.compare)
        except FileNotFoundError as exc:
            print(f"[octo-bench] {exc}", file=sys.stderr)
            return 1

    json_path, md_path = archive_report(report, out_dir, label=args.label)
    print(f"[octo-bench] report archived:\n  json: {json_path}\n  md  : {md_path}")

    if args.label:
        record = report_to_baseline_record(
            report, baseline_id=make_baseline_id(), label=args.label
        )
        store.save_baseline(record)
        print(f"[octo-bench] baseline saved label={args.label} id={record.baseline_id}")

    return 0


def _cmd_list_baselines(args: argparse.Namespace) -> int:
    repo_root = _resolve_repo_root()
    db_path = _resolve_db_path(args, repo_root)
    if not db_path.exists():
        print(f"[octo-bench] no DB at {db_path}", file=sys.stderr)
        return 1
    store = BenchmarkStore(db_path)
    baselines = store.list_baselines()
    if not baselines:
        print("[octo-bench] (no baselines yet)")
        return 0
    print(f"{'label':<24} {'commit':<10} {'created_at':<22} baseline_id")
    print("-" * 80)
    for b in baselines:
        print(
            f"{(b.label or '-'):<24} {b.commit_sha[:8]:<10} "
            f"{b.created_at:<22} {b.baseline_id}"
        )
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    import json as _json

    repo_root = _resolve_repo_root()
    db_path = _resolve_db_path(args, repo_root)
    store = BenchmarkStore(db_path)
    baseline = store.get_baseline(args.label)
    if baseline is None:
        print(f"[octo-bench] no baseline with label {args.label!r}", file=sys.stderr)
        return 1
    print(_json.dumps(_json.loads(baseline.aggregated_metrics_json), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(main(sys.argv[1:]))


__all__ = (
    "main",
    "YamlTaskMeta",
    "load_yaml_task",
    "load_tier1_tier3_tasks",
    "load_tier2_tasks",
    "detect_commit_sha",
    "resolve_runner",
)
