"""F103d Phase D T-D-2 — SQLite append-only persistence.

Schema 严格按 plan §6.1：
- benchmark_run：每 task × iteration = 1 行
- benchmark_baseline：每次完整 Daily Bench = 1 行

DB 路径：默认 ``benchmarks/baselines/bench.db``（与 JSON 报告同目录，不进入 production
data_dir，FR-H01 零侵入）。

关键设计：
- SQLite WAL mode（PoC-H3 实测 p95 < 2s 写延迟 @ 8 并发）
- 用 stdlib sqlite3（同步）；async caller 用 ``loop.run_in_executor`` 包装
- 每个线程独立 connection（threading.local），避免 SQLite check_same_thread 问题
- append_run 用 ``INSERT OR REPLACE``：resume 路径同 session+task+iteration 幂等覆盖
  （UNIQUE 索引 + INSERT OR REPLACE 替代 UPSERT，保持 SQLite 简单）

resume 语义（AC5-1）：
- store 不跟踪 PENDING 状态——只 append 已 finished runs
- ``get_pending_runs(session_id, planned)`` 返回 planned - finished
- caller（worker.py）按返回 list 继续跑，已 finished 的不重复执行
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Sequence

# ---------------------------------------------------------------------------
# Schema（plan §6.1）
# ---------------------------------------------------------------------------

_SCHEMA_RUN = """
CREATE TABLE IF NOT EXISTS benchmark_run (
    run_id                  TEXT PRIMARY KEY,
    bench_session_id        TEXT NOT NULL,
    task_id                 TEXT NOT NULL,
    tier                    INTEGER NOT NULL,
    domain                  TEXT NOT NULL,
    iteration               INTEGER NOT NULL,
    result                  TEXT NOT NULL,
    score                   REAL,
    duration_seconds        REAL NOT NULL,
    token_input             INTEGER,
    token_output            INTEGER,
    token_cache_read        INTEGER,
    audit_assertions_json   TEXT,
    error_message           TEXT,
    created_at              TEXT NOT NULL,
    UNIQUE(bench_session_id, task_id, iteration)
);
"""

_SCHEMA_BASELINE = """
CREATE TABLE IF NOT EXISTS benchmark_baseline (
    baseline_id             TEXT PRIMARY KEY,
    commit_sha              TEXT NOT NULL,
    label                   TEXT,
    aggregated_metrics_json TEXT NOT NULL,
    task_results_json       TEXT NOT NULL,
    duration_minutes        REAL,
    created_at              TEXT NOT NULL
);
"""

_INDEX_DDL: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_run_session ON benchmark_run(bench_session_id)",
    "CREATE INDEX IF NOT EXISTS idx_run_task ON benchmark_run(task_id)",
    "CREATE INDEX IF NOT EXISTS idx_run_result ON benchmark_run(result)",
    "CREATE INDEX IF NOT EXISTS idx_baseline_label ON benchmark_baseline(label)",
)

# ---------------------------------------------------------------------------
# Result 枚举（与 scorer.TaskVerdict 对齐 + runner 三态扩展）
# ---------------------------------------------------------------------------

RESULT_PASS = "PASS"
RESULT_FAIL = "FAIL"
RESULT_PARTIAL = "PARTIAL"
RESULT_TIMEOUT = "TIMEOUT"
RESULT_QUOTA_SKIP = "QUOTA_SKIP"
RESULT_INFRA_ERROR = "INFRA_ERROR"
RESULT_INCONSISTENT = "INCONSISTENT"
RESULT_ERROR = "ERROR"

ALL_RESULTS: frozenset[str] = frozenset(
    {
        RESULT_PASS,
        RESULT_FAIL,
        RESULT_PARTIAL,
        RESULT_TIMEOUT,
        RESULT_QUOTA_SKIP,
        RESULT_INFRA_ERROR,
        RESULT_INCONSISTENT,
        RESULT_ERROR,
    }
)

# Daily Bench 分母不含的结果（AC3-4：pass rate 分母不含 SKIP/TIMEOUT）
EXCLUDED_FROM_DENOMINATOR: frozenset[str] = frozenset(
    {RESULT_QUOTA_SKIP, RESULT_TIMEOUT, RESULT_INFRA_ERROR}
)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BenchmarkRunRecord:
    """单条 BenchmarkRun（task × iteration）。"""

    run_id: str
    bench_session_id: str
    task_id: str
    tier: int
    domain: str
    iteration: int
    result: str
    score: float | None
    duration_seconds: float
    token_input: int | None = None
    token_output: int | None = None
    token_cache_read: int | None = None
    audit_assertions_json: str | None = None
    error_message: str | None = None
    created_at: str = ""  # 空字符串时 store.append_run 自动填充 utcnow

    def __post_init__(self) -> None:
        if self.result not in ALL_RESULTS:
            raise ValueError(
                f"Invalid result {self.result!r}; must be one of {sorted(ALL_RESULTS)}"
            )
        if self.tier not in (1, 2, 3):
            raise ValueError(f"Invalid tier {self.tier}; must be 1/2/3")
        if self.iteration < 1:
            raise ValueError(f"Invalid iteration {self.iteration}; must be >= 1")
        if self.duration_seconds < 0:
            raise ValueError(f"Invalid duration_seconds {self.duration_seconds}; must be >= 0")


@dataclass(frozen=True, slots=True)
class BenchmarkBaselineRecord:
    """单次完整 Daily Bench 的归档（Phase E 末写入）。"""

    baseline_id: str
    commit_sha: str
    label: str | None
    aggregated_metrics_json: str
    task_results_json: str
    duration_minutes: float | None = None
    created_at: str = ""


@dataclass(frozen=True, slots=True)
class PendingRunSpec:
    """resume 时 store 返回的"待跑"task × iteration 标识。"""

    task_id: str
    iteration: int


# ---------------------------------------------------------------------------
# ID helpers（避免在 worker.py 硬编码 uuid 调用，方便单测注入）
# ---------------------------------------------------------------------------


def make_run_id() -> str:
    return f"run-{uuid.uuid4().hex[:16]}"


def make_baseline_id() -> str:
    return f"bsl-{uuid.uuid4().hex[:16]}"


def make_session_id() -> str:
    return f"sess-{uuid.uuid4().hex[:16]}"


def utcnow_iso() -> str:
    """UTC ISO 8601（不含 microsec，Z 后缀）。"""
    now = dt.datetime.now(dt.UTC).replace(microsecond=0)
    return now.isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# BenchmarkStore
# ---------------------------------------------------------------------------


class BenchmarkStore:
    """SQLite append-only store。线程安全（per-thread connection + WAL）。"""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._tls = threading.local()
        # 主线程先创建一次 schema（idempotent IF NOT EXISTS）
        conn = self._connect()
        try:
            conn.executescript(_SCHEMA_RUN)
            conn.executescript(_SCHEMA_BASELINE)
            for ddl in _INDEX_DDL:
                conn.execute(ddl)
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    @property
    def db_path(self) -> Path:
        return self._db_path

    def _connect(self) -> sqlite3.Connection:
        """返回当前线程的 connection（lazy 创建 + WAL/busy_timeout）。"""
        conn: sqlite3.Connection | None = getattr(self._tls, "conn", None)
        if conn is None:
            conn = sqlite3.connect(
                str(self._db_path),
                isolation_level="DEFERRED",
                timeout=30.0,
                check_same_thread=False,
            )
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=30000")
            conn.row_factory = sqlite3.Row
            self._tls.conn = conn
        return conn

    def close(self) -> None:
        """关闭当前线程 connection。多线程场景下 caller 应在每个线程退出前调用。"""
        conn = getattr(self._tls, "conn", None)
        if conn is not None:
            conn.close()
            self._tls.conn = None

    # -- BenchmarkRun CRUD -------------------------------------------------

    def append_run(self, run: BenchmarkRunRecord) -> BenchmarkRunRecord:
        """Append 一条 BenchmarkRun（resume idempotent：同 session+task+iter REPLACE）。

        返回 created_at 填充后的实际写入对象（caller 可用作回显）。
        """
        record = run if run.created_at else replace(run, created_at=utcnow_iso())
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO benchmark_run (
                    run_id, bench_session_id, task_id, tier, domain, iteration,
                    result, score, duration_seconds,
                    token_input, token_output, token_cache_read,
                    audit_assertions_json, error_message, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record.run_id,
                    record.bench_session_id,
                    record.task_id,
                    record.tier,
                    record.domain,
                    record.iteration,
                    record.result,
                    record.score,
                    record.duration_seconds,
                    record.token_input,
                    record.token_output,
                    record.token_cache_read,
                    record.audit_assertions_json,
                    record.error_message,
                    record.created_at,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return record

    def get_runs_for_session(self, session_id: str) -> list[BenchmarkRunRecord]:
        """读出某 session 全部已写入的 BenchmarkRun（reporter.py 用）。"""
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM benchmark_run WHERE bench_session_id = ? "
            "ORDER BY task_id, iteration",
            (session_id,),
        ).fetchall()
        return [_row_to_run(row) for row in rows]

    def get_completed_keys(self, session_id: str) -> set[tuple[str, int]]:
        """读出某 session 已完成的 (task_id, iteration) 集合（resume diff 用）。"""
        conn = self._connect()
        rows = conn.execute(
            "SELECT task_id, iteration FROM benchmark_run "
            "WHERE bench_session_id = ?",
            (session_id,),
        ).fetchall()
        return {(r["task_id"], int(r["iteration"])) for r in rows}

    def get_pending_runs(
        self,
        session_id: str,
        planned: Sequence[tuple[str, int]],
    ) -> list[PendingRunSpec]:
        """返回 ``planned`` 中尚未在 store 出现的 (task_id, iteration) 列表（resume 入口）。

        ``planned`` 是 caller 计划要跑的全部 (task_id, iter)（含 iteration ∈ {1, 2, 3}）。
        本函数读 store 已完成集合 → diff → 返回剩余 list（保留 planned 顺序）。

        AC5-1：caller 用此返回值按顺序续跑；同 session+task+iter 不会重复执行。
        """
        completed = self.get_completed_keys(session_id)
        pending: list[PendingRunSpec] = []
        seen: set[tuple[str, int]] = set()
        for task_id, iteration in planned:
            key = (task_id, int(iteration))
            if key in completed or key in seen:
                continue
            seen.add(key)
            pending.append(PendingRunSpec(task_id=task_id, iteration=int(iteration)))
        return pending

    def get_session_ids(self) -> list[str]:
        """所有已知 session_id（运维 / debug 用）。"""
        conn = self._connect()
        rows = conn.execute(
            "SELECT DISTINCT bench_session_id FROM benchmark_run ORDER BY bench_session_id"
        ).fetchall()
        return [str(r["bench_session_id"]) for r in rows]

    # -- BenchmarkBaseline CRUD -------------------------------------------

    def save_baseline(self, baseline: BenchmarkBaselineRecord) -> BenchmarkBaselineRecord:
        """写入一条 BenchmarkBaseline（Phase E 末或手工 archive 入口）。"""
        record = (
            baseline
            if baseline.created_at
            else replace(baseline, created_at=utcnow_iso())
        )
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO benchmark_baseline (
                    baseline_id, commit_sha, label,
                    aggregated_metrics_json, task_results_json,
                    duration_minutes, created_at
                ) VALUES (?,?,?,?,?,?,?)
                """,
                (
                    record.baseline_id,
                    record.commit_sha,
                    record.label,
                    record.aggregated_metrics_json,
                    record.task_results_json,
                    record.duration_minutes,
                    record.created_at,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return record

    def get_baseline(self, label: str) -> BenchmarkBaselineRecord | None:
        """按 label 查 baseline；同 label 多条返回最新（created_at desc）。"""
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM benchmark_baseline WHERE label = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (label,),
        ).fetchone()
        if row is None:
            return None
        return _row_to_baseline(row)

    def list_baselines(self) -> list[BenchmarkBaselineRecord]:
        """全部 baseline 列表（运维 / `--list-baselines`）。"""
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM benchmark_baseline ORDER BY created_at DESC"
        ).fetchall()
        return [_row_to_baseline(r) for r in rows]


# ---------------------------------------------------------------------------
# Row → dataclass
# ---------------------------------------------------------------------------


def _row_to_run(row: sqlite3.Row) -> BenchmarkRunRecord:
    return BenchmarkRunRecord(
        run_id=str(row["run_id"]),
        bench_session_id=str(row["bench_session_id"]),
        task_id=str(row["task_id"]),
        tier=int(row["tier"]),
        domain=str(row["domain"]),
        iteration=int(row["iteration"]),
        result=str(row["result"]),
        score=float(row["score"]) if row["score"] is not None else None,
        duration_seconds=float(row["duration_seconds"]),
        token_input=int(row["token_input"]) if row["token_input"] is not None else None,
        token_output=int(row["token_output"]) if row["token_output"] is not None else None,
        token_cache_read=(
            int(row["token_cache_read"]) if row["token_cache_read"] is not None else None
        ),
        audit_assertions_json=(
            str(row["audit_assertions_json"])
            if row["audit_assertions_json"] is not None
            else None
        ),
        error_message=(
            str(row["error_message"]) if row["error_message"] is not None else None
        ),
        created_at=str(row["created_at"]),
    )


def _row_to_baseline(row: sqlite3.Row) -> BenchmarkBaselineRecord:
    return BenchmarkBaselineRecord(
        baseline_id=str(row["baseline_id"]),
        commit_sha=str(row["commit_sha"]),
        label=str(row["label"]) if row["label"] is not None else None,
        aggregated_metrics_json=str(row["aggregated_metrics_json"]),
        task_results_json=str(row["task_results_json"]),
        duration_minutes=(
            float(row["duration_minutes"]) if row["duration_minutes"] is not None else None
        ),
        created_at=str(row["created_at"]),
    )


__all__ = (
    "BenchmarkStore",
    "BenchmarkRunRecord",
    "BenchmarkBaselineRecord",
    "PendingRunSpec",
    "RESULT_PASS",
    "RESULT_FAIL",
    "RESULT_PARTIAL",
    "RESULT_TIMEOUT",
    "RESULT_QUOTA_SKIP",
    "RESULT_INFRA_ERROR",
    "RESULT_INCONSISTENT",
    "RESULT_ERROR",
    "ALL_RESULTS",
    "EXCLUDED_FROM_DENOMINATOR",
    "make_run_id",
    "make_baseline_id",
    "make_session_id",
    "utcnow_iso",
)
