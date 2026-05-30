"""F103d Phase D T-D-6 — 统一 scorer dispatch 接口.

scorer.py 已有 ``score_tier1`` / ``score_tier2_tau`` / ``score_tier2_gaia`` /
``score_tier3`` 四个函数（Phase A/B/C 实现）。Phase D 在此提供统一入口：

    score(task, run_result, *, event_store=None, rubrics=None) -> BenchmarkRunScore

调用方按 tier + domain 自动分发，无需自己挑 score_tier* 函数。

设计要点：
- ``task`` 是 YAML 加载后的 dict 或 Tier 2 dataclass（TauBenchTaskMeta / GaiaFallbackTaskMeta）
- ``run_result`` 内含 actual_events（Tier 1/3）或 actual_tool_calls（Tier 2 τ-bench）
  或 actual_answer（Tier 2 GAIA）；按需消费对应字段
- ``event_store`` 仅 Tier 1/3 真实运行时用（caller 已 fetch 完事件并放 run_result 时可省）
- ``rubrics`` 为 ``load_scoring_rubrics()`` 加载的 dict[rubric_id, rubric]

零侵入：本模块不修改 scorer.py 任何已有函数；只组合 dispatch 逻辑。
"""

from __future__ import annotations

import dataclasses
from typing import Any

from benchmarks.runner.scorer import (
    BenchmarkRunScore,
    TaskVerdict,
    _build_score,
    score_tier1,
    score_tier2_gaia,
    score_tier2_tau,
    score_tier3,
)


@dataclasses.dataclass(frozen=True, slots=True)
class RunResult:
    """Caller 收集到的 task 执行产物（按 tier 不同字段非空）。

    Tier 1/3：填 ``actual_events``（从 EventStore 查得）+ ``token_usage``
    Tier 2 τ-bench：填 ``actual_tool_calls``
    Tier 2 GAIA：填 ``actual_answer``
    """

    actual_events: list[dict[str, Any]] | None = None
    actual_tool_calls: list[dict[str, Any]] | None = None
    actual_answer: str | None = None
    token_usage: int | None = None


def _get_tier(task: Any) -> int:
    """从 dict 或 dataclass 拿 tier（dataclass-friendly）。"""
    if isinstance(task, dict):
        return int(task.get("tier", 0))
    val = getattr(task, "tier", None)
    if val is not None:
        return int(val)
    return 0


def _get_domain(task: Any) -> str:
    """从 dict 或 dataclass 拿 domain。"""
    if isinstance(task, dict):
        return str(task.get("domain", ""))
    val = getattr(task, "domain", None)
    return str(val) if val is not None else ""


def _resolve_rubric(
    task: Any,
    rubrics: dict[str, dict[str, Any]] | None,
) -> dict[str, Any] | None:
    """按 task.rubric_id 解析 rubric；rubrics=None 或 id 不存在时返回 None。"""
    if rubrics is None:
        return None
    rubric_id: str | None = None
    if isinstance(task, dict):
        rubric_id = task.get("rubric_id")
    else:
        rubric_id = getattr(task, "rubric_id", None)
    if not rubric_id:
        return None
    return rubrics.get(str(rubric_id))


def score(
    task: Any,
    run_result: RunResult,
    *,
    rubrics: dict[str, dict[str, Any]] | None = None,
) -> BenchmarkRunScore:
    """统一 scorer 入口（T-D-6）。按 tier + domain 分发到对应 score_tier* 函数。

    返回 BenchmarkRunScore（含 verdict / pass_fail_score / weighted_score / 失败详情）。
    任何上游异常被捕获并返回 verdict=ERROR + 字符串化 message。

    分发规则：
    - tier 1 → ``score_tier1(task, actual_events, rubric, token_usage)``
    - tier 2 + domain 含 "tau" → ``score_tier2_tau(task, actual_tool_calls, rubric, token_usage)``
    - tier 2 + domain 含 "gaia" → ``score_tier2_gaia(task, actual_answer, rubric, token_usage)``
    - tier 3 → ``score_tier3(task, actual_events, rubric, token_usage)``
    - 其他 → verdict=ERROR
    """
    tier = _get_tier(task)
    domain = _get_domain(task).lower()
    rubric = _resolve_rubric(task, rubrics)
    token_usage = run_result.token_usage
    task_id_repr = _task_id(task)

    try:
        if tier == 1:
            events = run_result.actual_events or []
            return score_tier1(task, events, rubric, token_usage)

        if tier == 2:
            if "tau" in domain:
                tool_calls = run_result.actual_tool_calls or []
                return score_tier2_tau(task, tool_calls, rubric, token_usage)
            if "gaia" in domain:
                answer = run_result.actual_answer or ""
                return score_tier2_gaia(task, answer, rubric, token_usage)
            return _build_score(
                task_id=task_id_repr,
                verdict=TaskVerdict.ERROR,
                pass_fail_score=0.0,
                pass_fail_weight=(rubric or {}).get("pass_fail_weight", 1.0),
                token_usage=token_usage,
                error_message=f"tier 2 task 未识别 domain={domain!r}（需含 tau / gaia）",
            )

        if tier == 3:
            events = run_result.actual_events or []
            return score_tier3(task, events, rubric, token_usage)

        return _build_score(
            task_id=task_id_repr,
            verdict=TaskVerdict.ERROR,
            pass_fail_score=0.0,
            pass_fail_weight=(rubric or {}).get("pass_fail_weight", 1.0),
            token_usage=token_usage,
            error_message=f"unsupported tier={tier!r}（需为 1/2/3）",
        )
    except Exception as exc:
        return _build_score(
            task_id=task_id_repr,
            verdict=TaskVerdict.ERROR,
            pass_fail_score=0.0,
            pass_fail_weight=(rubric or {}).get("pass_fail_weight", 1.0),
            token_usage=token_usage,
            error_message=f"score_dispatch internal error: {exc!r}",
        )


def _task_id(task: Any) -> str:
    if isinstance(task, dict):
        return str(task.get("task_id", "UNKNOWN"))
    return str(getattr(task, "task_id", "UNKNOWN"))


__all__ = ("score", "RunResult")
