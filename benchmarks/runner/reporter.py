"""F103d Phase D T-D-4 — JSON + Markdown 报告 + --compare delta.

核心 API:

- ``generate_report(session_id, store, *, commit_sha, baseline_label=None)``
  → ``BenchmarkReport`` 完整聚合（plan §6.3 JSON 结构）
- ``write_report_json(report, path)`` / ``write_report_markdown(report, path)``
- ``compare_with_baseline(report, baseline)`` → 在 report 上挂 ``delta`` 区块
  （W7：pass rate 精度 0.001 + regression / improvement 列表）

AC1-2 / AC1-4 / AC3-4 / AC4-1 / AC4-2 / AC6-1 / AC6-2 全覆盖。

Tier 2 按 domain 拆分（tau_bench / gaia）；其他 tier 不拆。
Pass rate 分母不含 ``QUOTA_SKIP`` / ``TIMEOUT`` / ``INFRA_ERROR``（AC3-4）。
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from benchmarks.runner.store import (
    EXCLUDED_FROM_DENOMINATOR,
    RESULT_FAIL,
    RESULT_INCONSISTENT,
    RESULT_PARTIAL,
    RESULT_PASS,
    RESULT_QUOTA_SKIP,
    RESULT_TIMEOUT,
    BenchmarkBaselineRecord,
    BenchmarkRunRecord,
    BenchmarkStore,
    utcnow_iso,
)

logger = logging.getLogger(__name__)

DELTA_PRECISION = 3  # W7：pass rate 精度 0.001


# ---------------------------------------------------------------------------
# Tier 2 sub-domain 识别
# ---------------------------------------------------------------------------


def _tier2_subdomain(record: BenchmarkRunRecord) -> str:
    """识别 tier 2 sub-domain（tau_bench / gaia）。

    判断顺序：domain 含 "gaia" → gaia；含 "tau" → tau_bench；其他 → "other"。
    """
    d = record.domain.lower()
    if "gaia" in d:
        return "gaia"
    if "tau" in d:
        return "tau_bench"
    return "other"


# ---------------------------------------------------------------------------
# 聚合数据结构
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TaskAggregate:
    """单 task 跨 iterations 聚合（plan §6.3 ``task_details`` 元素）。"""

    task_id: str
    tier: int
    domain: str
    majority_result: str
    iterations: tuple[dict[str, Any], ...]
    inconsistency_note: str | None


@dataclass(slots=True)
class BenchmarkReport:
    """完整 Daily Bench 报告（plan §6.3 顶层结构 + delta 可选）。"""

    session_id: str
    commit_sha: str
    created_at: str
    summary: dict[str, Any]
    by_tier: dict[str, Any]
    by_domain: dict[str, Any]
    task_details: list[dict[str, Any]]
    delta: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """序列化为 plan §6.3 顶层结构。"""
        out: dict[str, Any] = {
            "run_id": self.session_id,
            "baseline_sha": self.commit_sha,
            "created_at": self.created_at,
            "summary": self.summary,
            "by_tier": self.by_tier,
            "by_domain": self.by_domain,
            "task_details": self.task_details,
        }
        if self.delta is not None:
            out["delta"] = self.delta
        return out


# ---------------------------------------------------------------------------
# Pass rate 计算（AC3-4：分母不含 SKIP/TIMEOUT/INFRA_ERROR）
# ---------------------------------------------------------------------------


def _is_pass(result: str) -> bool:
    return result == RESULT_PASS


def _counts_in_denominator(results: Iterable[str]) -> int:
    return sum(1 for r in results if r not in EXCLUDED_FROM_DENOMINATOR)


def _calc_pass_rate(results: Iterable[str]) -> float:
    """pass_rate = #PASS / (total - excluded)。分母 0 时返回 0.0。"""
    rs = list(results)
    denom = _counts_in_denominator(rs)
    if denom == 0:
        return 0.0
    numer = sum(1 for r in rs if _is_pass(r))
    return round(numer / denom, DELTA_PRECISION)


def _avg_score(records: Sequence[BenchmarkRunRecord]) -> float:
    """有 score 的 record 取算术平均；全 None 返回 0.0。"""
    scores = [r.score for r in records if r.score is not None]
    if not scores:
        return 0.0
    return round(sum(scores) / len(scores), DELTA_PRECISION)


# ---------------------------------------------------------------------------
# Task majority result（3 iteration → 多数；全异 = INCONSISTENT）
# ---------------------------------------------------------------------------


def majority_result(results: Sequence[str]) -> tuple[str, str | None]:
    """计算 majority + inconsistency_note。

    Rules（SC-011）:
    - 全部相同 → 直接返回该结果，note=None
    - 有真正"多数"（出现 ≥ ⌈n/2⌉+1 次）→ 返回多数 + note 描述少数
    - 否则 → ``INCONSISTENT`` + note 描述三种结果占比

    EXCLUDED 类（QUOTA_SKIP / TIMEOUT / INFRA_ERROR）参与多数判定（避免
    全 SKIP 被算成"PASS"）。
    """
    if not results:
        return RESULT_FAIL, "empty iterations"
    counter = Counter(results)
    n = len(results)
    most_common = counter.most_common()
    top_result, top_count = most_common[0]
    if top_count == n:
        return top_result, None
    # 真正多数 = 严格大于 n/2
    if top_count * 2 > n:
        minority = [f"{r}×{c}" for r, c in most_common[1:]]
        return top_result, f"majority={top_result}({top_count}/{n}); minority: {', '.join(minority)}"
    # 无真正多数 → INCONSISTENT
    detail = ", ".join(f"{r}×{c}" for r, c in most_common)
    return RESULT_INCONSISTENT, f"no majority across {n} iterations: {detail}"


# ---------------------------------------------------------------------------
# 单 task 聚合
# ---------------------------------------------------------------------------


def _aggregate_task(records: Sequence[BenchmarkRunRecord]) -> TaskAggregate:
    """对同 task_id 的所有 iteration 聚合（按 iteration 升序）。"""
    if not records:
        raise ValueError("empty records to _aggregate_task")
    sorted_records = sorted(records, key=lambda r: r.iteration)
    iterations: list[dict[str, Any]] = []
    for r in sorted_records:
        iterations.append(
            {
                "iteration": r.iteration,
                "result": r.result,
                "score": r.score,
                "duration": round(r.duration_seconds, 3),
                "token_input": r.token_input,
                "token_output": r.token_output,
                "token_cache_read": r.token_cache_read,
            }
        )
    results = [r.result for r in sorted_records]
    majority, note = majority_result(results)
    head = sorted_records[0]
    return TaskAggregate(
        task_id=head.task_id,
        tier=head.tier,
        domain=head.domain,
        majority_result=majority,
        iterations=tuple(iterations),
        inconsistency_note=note,
    )


# ---------------------------------------------------------------------------
# Tier / Domain 聚合
# ---------------------------------------------------------------------------


def _aggregate_by_tier(task_aggregates: Sequence[TaskAggregate]) -> dict[str, Any]:
    """plan §6.3 by_tier 结构（tier2 拆 tau_bench / gaia）。"""
    by_tier: dict[str, Any] = {}
    # tier 1 / tier 3 单一结构
    for t in (1, 3):
        items = [a for a in task_aggregates if a.tier == t]
        results = [a.majority_result for a in items]
        passed = sum(1 for r in results if _is_pass(r))
        by_tier[f"tier{t}"] = {
            "pass_rate": _calc_pass_rate(results),
            "tasks": len(items),
            "passed": passed,
        }

    # tier 2 拆 tau_bench / gaia / other
    tier2_items = [a for a in task_aggregates if a.tier == 2]
    sub_buckets: dict[str, list[TaskAggregate]] = defaultdict(list)
    for a in tier2_items:
        # 用第 1 个 record 的 domain 识别（同 task 所有 iteration domain 一致）
        # TaskAggregate 已存 domain 字段
        sub_buckets[_subdomain_from_str(a.domain)].append(a)
    tier2_payload: dict[str, Any] = {}
    for sub, items in sub_buckets.items():
        if not items:
            continue
        results = [a.majority_result for a in items]
        passed = sum(1 for r in results if _is_pass(r))
        tier2_payload[sub] = {
            "pass_rate": _calc_pass_rate(results),
            "tasks": len(items),
            "passed": passed,
        }
    by_tier["tier2"] = tier2_payload
    return by_tier


def _subdomain_from_str(domain: str) -> str:
    d = domain.lower()
    if "gaia" in d:
        return "gaia"
    if "tau" in d:
        return "tau_bench"
    return "other"


def _aggregate_by_domain(task_aggregates: Sequence[TaskAggregate]) -> dict[str, Any]:
    """plan §6.3 by_domain 结构。"""
    buckets: dict[str, list[TaskAggregate]] = defaultdict(list)
    for a in task_aggregates:
        buckets[a.domain].append(a)
    out: dict[str, Any] = {}
    for domain, items in sorted(buckets.items()):
        results = [a.majority_result for a in items]
        passed = sum(1 for r in results if _is_pass(r))
        out[domain] = {
            "pass_rate": _calc_pass_rate(results),
            "tasks": len(items),
            "passed": passed,
        }
    return out


# ---------------------------------------------------------------------------
# 总报告生成（plan §6.3）
# ---------------------------------------------------------------------------


def generate_report(
    session_id: str,
    store: BenchmarkStore,
    *,
    commit_sha: str,
    created_at: str | None = None,
) -> BenchmarkReport:
    """从 store 读 session 全部 BenchmarkRun → 聚合 → BenchmarkReport。"""
    runs = store.get_runs_for_session(session_id)
    return generate_report_from_runs(
        runs,
        session_id=session_id,
        commit_sha=commit_sha,
        created_at=created_at,
    )


def generate_report_from_runs(
    runs: Sequence[BenchmarkRunRecord],
    *,
    session_id: str,
    commit_sha: str,
    created_at: str | None = None,
) -> BenchmarkReport:
    """从已加载的 runs 列表生成 BenchmarkReport（无 store 依赖，便于单测）。"""
    # 按 task_id 聚合
    by_task: dict[str, list[BenchmarkRunRecord]] = defaultdict(list)
    for r in runs:
        by_task[r.task_id].append(r)

    task_aggregates = [_aggregate_task(records) for records in by_task.values()]
    task_aggregates.sort(key=lambda a: (a.tier, a.domain, a.task_id))

    # Summary
    all_results = [a.majority_result for a in task_aggregates]
    total_tasks = len(task_aggregates)
    skipped_counter: Counter[str] = Counter()
    for r in runs:
        if r.result in EXCLUDED_FROM_DENOMINATOR:
            skipped_counter[r.result] += 1

    total_tokens = {
        "input": sum((r.token_input or 0) for r in runs),
        "output": sum((r.token_output or 0) for r in runs),
        "cache_read": sum((r.token_cache_read or 0) for r in runs),
    }
    total_duration_seconds = sum(r.duration_seconds for r in runs)
    duration_minutes = round(total_duration_seconds / 60.0, DELTA_PRECISION)

    summary = {
        "total_tasks": total_tasks,
        "total_iterations": len(runs),
        "pass_rate": _calc_pass_rate(all_results),
        "weighted_score": _avg_score(runs),
        "token_usage": total_tokens,
        "duration_minutes": duration_minutes,
        "skipped": dict(skipped_counter),
        "inconsistent_count": sum(
            1 for a in task_aggregates if a.majority_result == RESULT_INCONSISTENT
        ),
    }

    by_tier = _aggregate_by_tier(task_aggregates)
    by_domain = _aggregate_by_domain(task_aggregates)

    task_details: list[dict[str, Any]] = []
    for a in task_aggregates:
        task_details.append(
            {
                "task_id": a.task_id,
                "tier": a.tier,
                "domain": a.domain,
                "majority_result": a.majority_result,
                "iterations": list(a.iterations),
                "inconsistency_note": a.inconsistency_note,
            }
        )

    return BenchmarkReport(
        session_id=session_id,
        commit_sha=commit_sha,
        created_at=created_at or utcnow_iso(),
        summary=summary,
        by_tier=by_tier,
        by_domain=by_domain,
        task_details=task_details,
    )


# ---------------------------------------------------------------------------
# --compare delta（W7 / AC1-4 / AC6-1 / AC6-2）
# ---------------------------------------------------------------------------


def compare_with_baseline(
    report: BenchmarkReport,
    baseline: BenchmarkBaselineRecord,
) -> BenchmarkReport:
    """在 report 上挂 ``delta`` 区块（W7：0.001 精度 + regression / improvement 列表）。

    baseline ``aggregated_metrics_json`` 必须含 plan §6.3 summary + by_tier 结构；
    ``task_results_json`` 必须是 ``{task_id: majority_result}`` 映射（reporter 之前
    存的）。

    返回原 report（mutated：delta 字段已填充）。
    """
    try:
        baseline_metrics = json.loads(baseline.aggregated_metrics_json)
        baseline_task_results: dict[str, str] = json.loads(baseline.task_results_json)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ValueError(
            f"baseline {baseline.baseline_id!r} aggregated metrics 解析失败: {exc}"
        ) from exc

    current_tasks = {a["task_id"]: a["majority_result"] for a in report.task_details}

    # Summary delta
    baseline_summary = baseline_metrics.get("summary", {})
    baseline_by_tier = baseline_metrics.get("by_tier", {})
    delta_summary = {
        "pass_rate_delta": _signed_delta(
            report.summary.get("pass_rate", 0.0),
            baseline_summary.get("pass_rate", 0.0),
        ),
        "weighted_score_delta": _signed_delta(
            report.summary.get("weighted_score", 0.0),
            baseline_summary.get("weighted_score", 0.0),
        ),
    }
    # Per-tier delta
    for tier_key in ("tier1", "tier3"):
        cur = report.by_tier.get(tier_key, {}).get("pass_rate", 0.0)
        bl = baseline_by_tier.get(tier_key, {}).get("pass_rate", 0.0)
        delta_summary[f"{tier_key}_delta"] = _signed_delta(cur, bl)
    # Tier 2 sub-domain delta
    cur_t2 = report.by_tier.get("tier2", {})
    bl_t2 = baseline_by_tier.get("tier2", {})
    for sub in set(cur_t2.keys()) | set(bl_t2.keys()):
        cur = cur_t2.get(sub, {}).get("pass_rate", 0.0)
        bl = bl_t2.get(sub, {}).get("pass_rate", 0.0)
        delta_summary[f"tier2_{sub}_delta"] = _signed_delta(cur, bl)

    # Regressions / improvements（task_id 级别）
    regressions: list[dict[str, Any]] = []
    improvements: list[dict[str, Any]] = []
    for task_id, current_result in sorted(current_tasks.items()):
        prev_result = baseline_task_results.get(task_id)
        if prev_result is None:
            continue  # new task; M5 baseline 不含，不算 regression
        if prev_result == RESULT_PASS and current_result != RESULT_PASS:
            failure_detail = _extract_failure_detail(report, task_id)
            regressions.append(
                {
                    "task_id": task_id,
                    "baseline_result": prev_result,
                    "current_result": current_result,
                    "failure_detail": failure_detail,
                }
            )
        elif prev_result != RESULT_PASS and current_result == RESULT_PASS:
            improvements.append(
                {
                    "task_id": task_id,
                    "baseline_result": prev_result,
                    "current_result": current_result,
                }
            )

    report.delta = {
        "baseline_id": baseline.baseline_id,
        "baseline_label": baseline.label,
        "baseline_commit": baseline.commit_sha,
        "compared_at": utcnow_iso(),
        "summary": delta_summary,
        "regressions": regressions,
        "improvements": improvements,
        "regression_count": len(regressions),
        "improvement_count": len(improvements),
    }
    return report


def _signed_delta(current: float, baseline: float) -> str:
    """格式化 delta 字符串：'+0.050' / '-0.030' / '+0.000'。精度 0.001。"""
    diff = round(float(current) - float(baseline), DELTA_PRECISION)
    sign = "+" if diff >= 0 else ""
    return f"{sign}{diff:.{DELTA_PRECISION}f}"


def _extract_failure_detail(report: BenchmarkReport, task_id: str) -> str | None:
    """从 task_details 抽 task_id 的失败摘要（首条非 PASS iteration 的 error 描述）。"""
    for a in report.task_details:
        if a["task_id"] != task_id:
            continue
        for it in a["iterations"]:
            if it["result"] != RESULT_PASS:
                # 仅返回 iteration result + score，详细 audit failures 在 store error_message
                return f"{it['result']} score={it.get('score')}"
    return None


# ---------------------------------------------------------------------------
# 文件 IO
# ---------------------------------------------------------------------------


def write_report_json(report: BenchmarkReport, path: Path) -> Path:
    """JSON 报告写盘（AC1-2）。`path` 父目录自动创建。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=False),
        encoding="utf-8",
    )
    return path


def write_report_markdown(report: BenchmarkReport, path: Path) -> Path:
    """Markdown 摘要写盘（AC1-2）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(report), encoding="utf-8")
    return path


def render_markdown(report: BenchmarkReport) -> str:
    """渲染 Markdown 摘要（人眼可读，对照 plan §6.3 关键字段）。"""
    lines: list[str] = []
    summary = report.summary
    lines.append(f"# OctoBench Daily Bench Report")
    lines.append("")
    lines.append(f"- session_id: `{report.session_id}`")
    lines.append(f"- commit_sha: `{report.commit_sha}`")
    lines.append(f"- created_at: `{report.created_at}`")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- total_tasks: {summary['total_tasks']}")
    lines.append(f"- total_iterations: {summary['total_iterations']}")
    lines.append(f"- pass_rate: **{summary['pass_rate']:.3f}**")
    lines.append(f"- weighted_score: {summary['weighted_score']:.3f}")
    lines.append(f"- duration_minutes: {summary['duration_minutes']:.3f}")
    tk = summary.get("token_usage", {})
    lines.append(
        f"- token_usage: input={tk.get('input', 0)}, "
        f"output={tk.get('output', 0)}, "
        f"cache_read={tk.get('cache_read', 0)}"
    )
    if summary.get("skipped"):
        skipped_str = ", ".join(
            f"{k}={v}" for k, v in sorted(summary["skipped"].items())
        )
        lines.append(f"- skipped: {skipped_str}")
    inc = summary.get("inconsistent_count", 0)
    if inc:
        lines.append(f"- ⚠ inconsistent_count: {inc} ({inc / max(1, summary['total_tasks']):.1%})")
    lines.append("")
    lines.append("## By Tier")
    lines.append("")
    for tier_key in ("tier1", "tier2", "tier3"):
        block = report.by_tier.get(tier_key)
        if not block:
            continue
        if tier_key == "tier2":
            lines.append("- tier2:")
            for sub, info in sorted(block.items()):
                lines.append(
                    f"  - {sub}: pass_rate={info['pass_rate']:.3f} "
                    f"({info['passed']}/{info['tasks']})"
                )
        else:
            lines.append(
                f"- {tier_key}: pass_rate={block['pass_rate']:.3f} "
                f"({block['passed']}/{block['tasks']})"
            )
    lines.append("")
    lines.append("## By Domain")
    lines.append("")
    for domain, info in sorted(report.by_domain.items()):
        lines.append(
            f"- {domain}: pass_rate={info['pass_rate']:.3f} "
            f"({info['passed']}/{info['tasks']})"
        )
    lines.append("")
    # Delta（如有）
    if report.delta:
        lines.append("## Delta vs Baseline")
        lines.append("")
        lines.append(f"- baseline_label: `{report.delta.get('baseline_label')}`")
        lines.append(f"- baseline_commit: `{report.delta.get('baseline_commit')}`")
        lines.append(f"- regression_count: {report.delta.get('regression_count', 0)}")
        lines.append(f"- improvement_count: {report.delta.get('improvement_count', 0)}")
        for k, v in sorted(report.delta.get("summary", {}).items()):
            lines.append(f"- {k}: {v}")
        regressions = report.delta.get("regressions", [])
        if regressions:
            lines.append("")
            lines.append("### Regressions")
            lines.append("")
            for r in regressions:
                lines.append(
                    f"- `{r['task_id']}`: {r['baseline_result']} → "
                    f"{r['current_result']} ({r.get('failure_detail', '')})"
                )
        improvements = report.delta.get("improvements", [])
        if improvements:
            lines.append("")
            lines.append("### Improvements")
            lines.append("")
            for r in improvements:
                lines.append(
                    f"- `{r['task_id']}`: {r['baseline_result']} → {r['current_result']}"
                )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def archive_report(
    report: BenchmarkReport,
    out_dir: Path,
    *,
    label: str | None = None,
) -> tuple[Path, Path]:
    """把 report 归档到 ``out_dir``，返回 ``(json_path, md_path)``。

    文件名：``{label or sha}-{ts}.json`` / ``.md``。
    """
    out_dir = Path(out_dir)
    sha_short = (report.commit_sha or "unknown")[:8]
    ts = report.created_at.replace(":", "").replace("-", "").replace("Z", "")
    name_prefix = label or sha_short
    json_path = out_dir / f"{name_prefix}-{ts}.json"
    md_path = out_dir / f"{name_prefix}-{ts}.md"
    write_report_json(report, json_path)
    write_report_markdown(report, md_path)
    return json_path, md_path


# ---------------------------------------------------------------------------
# Baseline 写入（Phase E 末 archive，T-E-4 / T-E-5 用）
# ---------------------------------------------------------------------------


def report_to_baseline_record(
    report: BenchmarkReport,
    *,
    baseline_id: str,
    label: str,
) -> BenchmarkBaselineRecord:
    """把 BenchmarkReport 转成 BenchmarkBaselineRecord（写 store 用）。

    ``aggregated_metrics_json`` = summary + by_tier；``task_results_json`` =
    ``{task_id: majority_result}`` 映射（compare_with_baseline 消费）。
    """
    metrics = {
        "summary": report.summary,
        "by_tier": report.by_tier,
        "by_domain": report.by_domain,
    }
    task_results = {a["task_id"]: a["majority_result"] for a in report.task_details}
    return BenchmarkBaselineRecord(
        baseline_id=baseline_id,
        commit_sha=report.commit_sha,
        label=label,
        aggregated_metrics_json=json.dumps(metrics, ensure_ascii=False),
        task_results_json=json.dumps(task_results, ensure_ascii=False),
        duration_minutes=report.summary.get("duration_minutes"),
        created_at=report.created_at,
    )


# ---------------------------------------------------------------------------
# --compare 入口（CLI 调用）
# ---------------------------------------------------------------------------


def attach_delta_or_raise(
    report: BenchmarkReport,
    store: BenchmarkStore,
    baseline_label: str,
) -> BenchmarkReport:
    """``--compare <label>`` 入口：查 baseline，找不到 raise（AC6-2）。"""
    baseline = store.get_baseline(baseline_label)
    if baseline is None:
        raise FileNotFoundError(
            f"M5 baseline not found（label={baseline_label!r}）；"
            "请先跑 `octo bench daily --label m5-baseline` 建立 baseline。"
        )
    return compare_with_baseline(report, baseline)


__all__ = (
    "BenchmarkReport",
    "TaskAggregate",
    "generate_report",
    "generate_report_from_runs",
    "compare_with_baseline",
    "majority_result",
    "write_report_json",
    "write_report_markdown",
    "render_markdown",
    "archive_report",
    "report_to_baseline_record",
    "attach_delta_or_raise",
    "DELTA_PRECISION",
)
