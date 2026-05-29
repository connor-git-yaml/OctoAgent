"""
benchmarks/runner/scorer.py

Tier 1 评分器初始版（Phase A）。

职责：
- EventStore 断言逻辑（event_store_assert：按 expected_events 查询 EventStore）
- LLM judge 触发（调用 llm_judge.LLMJudgeTrigger.should_trigger_judge）
- BenchmarkRunScore 计算（pass_fail_weight=0.65 / partial_weight=0.25 / efficiency_weight=0.10）

不修改 packages/ 或 apps/ 下任何现有文件（FR-H01 零侵入）。
"""
from __future__ import annotations

import dataclasses
import datetime
import enum
from pathlib import Path
from typing import Any

import yaml

# 从 octoagent venv 导入 SqliteEventStore 和 EventType
# 注意：实测类名为 SqliteEventStore（不是 EventStore），PoC §6 已记录此差异
from octoagent.core.models.enums import EventType
from octoagent.core.store.event_store import SqliteEventStore as EventStore

from .llm_judge import JudgeResult, LLMJudgeTrigger


# ============================================================
# 数据类型定义
# ============================================================

class TaskVerdict(str, enum.Enum):
    """单 task 评分结论"""
    PASS = "PASS"
    FAIL = "FAIL"
    PARTIAL = "PARTIAL"
    ERROR = "ERROR"         # scorer 内部异常（区别于 task 执行失败）
    INCONSISTENT = "INCONSISTENT"  # 3 次迭代结果不一致（Phase E 多轮后判定）


@dataclasses.dataclass
class EventMatchResult:
    """单个 expected_event 的匹配结果"""
    event_type: str
    matched: bool
    matched_event: dict[str, Any] | None = None   # 命中的实际事件
    missing_fields: list[str] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class BenchmarkRunScore:
    """
    单 task 评分结果。

    字段语义与 scoring_rubrics.yaml 对齐：
    - pass_fail_score：event_store_assert 结论（1.0=PASS / 0.0=FAIL）
    - partial_score：LLM judge 评分（0.0~1.0，仅在 match_ratio in [0.5, 1.0) 时有值）
    - efficiency_score：token 效率分（Phase E 末 efficiency_baseline_tokens 填入后生效）
    - weighted_score：加权总分（按 rubric 权重计算）
    - verdict：最终结论（PASS / FAIL / PARTIAL / ERROR）
    - judge_result：LLM judge 结果（如触发）
    """
    task_id: str
    verdict: TaskVerdict

    # 三维分数（对应 scoring_rubrics.yaml 三个权重维度）
    pass_fail_score: float = 0.0   # 0.0 or 1.0
    partial_score: float | None = None   # None = 未触发 judge
    efficiency_score: float | None = None  # None = efficiency_baseline_tokens 为 null

    # 加权总分
    weighted_score: float = 0.0

    # 断言细节
    event_matches: list[EventMatchResult] = dataclasses.field(default_factory=list)
    match_ratio: float = 0.0   # matched / total expected

    # LLM judge 结果（如触发）
    judge_result: JudgeResult | None = None

    # 元数据
    token_usage: int | None = None  # 本次 task 消耗的 token 总数（input+output）
    scored_at: datetime.datetime = dataclasses.field(
        default_factory=datetime.datetime.utcnow
    )
    error_message: str | None = None


# ============================================================
# EventStore 断言逻辑
# ============================================================

def _match_required_fields(
    event_payload: dict[str, Any],
    required_fields: dict[str, Any],
) -> tuple[bool, list[str]]:
    """
    检查实际事件的 payload 是否满足 required_fields 约束。

    支持的约束类型：
    - 精确匹配：required_fields = {"namespace": "AGENT_PRIVATE"}
    - 字符串包含匹配：required_fields = {"content_contains": "OctoAgent"}
      （以 _contains 结尾的 key 会用 in 操作符匹配目标字段）
    - tool_name_contains：特殊处理（匹配 payload 中任何 tool_name 相关字段）

    返回：(是否全部命中, 缺失/不匹配的字段列表)
    """
    missing: list[str] = []

    for key, expected_value in required_fields.items():
        if not expected_value and expected_value != 0:
            # 空字符串 / null 约束：只需字段存在即可
            continue

        if key.endswith("_contains"):
            # 字符串包含匹配：key = "content_contains" → 在 payload["content"] 中搜索
            base_field = key[: -len("_contains")]
            actual_value = event_payload.get(base_field, "")

            # tool_name_contains：搜索所有可能包含工具名的字段
            if base_field == "tool_name":
                actual_value = (
                    str(event_payload.get("tool_name", ""))
                    + str(event_payload.get("function_name", ""))
                    + str(event_payload.get("name", ""))
                )

            if expected_value not in str(actual_value):
                missing.append(key)
        else:
            # 精确匹配
            actual_value = event_payload.get(key)
            if str(actual_value) != str(expected_value):
                missing.append(key)

    return (len(missing) == 0, missing)


def event_store_assert(
    expected_events: list[dict[str, Any]],
    actual_events: list[dict[str, Any]],
) -> tuple[float, list[EventMatchResult]]:
    """
    核心断言函数：检查 expected_events 中每条期望事件是否在 actual_events 中有命中。

    匹配策略：
    - 按 event_type 过滤 actual_events
    - 对匹配的事件逐一检查 required_fields
    - 贪心匹配（每条 expected_event 找 actual_events 中第一个满足条件的事件）

    返回：(match_ratio: float, matches: list[EventMatchResult])
    """
    if not expected_events:
        # 无期望事件 = 无法评分，默认 PASS（用于占位 task）
        return 1.0, []

    matches: list[EventMatchResult] = []

    for expected in expected_events:
        evt_type = expected.get("event_type", "")
        required_fields: dict[str, Any] = expected.get("required_fields", {}) or {}

        # 在 actual_events 中找所有同类型事件
        candidates = [e for e in actual_events if e.get("event_type") == evt_type]

        matched = False
        matched_event = None
        all_missing: list[str] = []

        for candidate in candidates:
            payload = candidate.get("payload", candidate)  # 兼容 payload 嵌套或扁平格式
            ok, missing = _match_required_fields(payload, required_fields)
            if ok:
                matched = True
                matched_event = candidate
                all_missing = []
                break
            else:
                all_missing = missing  # 记录最近一次不匹配的字段

        matches.append(EventMatchResult(
            event_type=evt_type,
            matched=matched,
            matched_event=matched_event,
            missing_fields=all_missing,
        ))

    matched_count = sum(1 for m in matches if m.matched)
    match_ratio = matched_count / len(expected_events)

    return match_ratio, matches


# ============================================================
# Tier 1 主评分函数
# ============================================================

def score_tier1(
    task: dict[str, Any],
    actual_events: list[dict[str, Any]],
    rubric: dict[str, Any] | None = None,
    token_usage: int | None = None,
) -> BenchmarkRunScore:
    """
    Tier 1 task 主评分函数。

    参数：
    - task：从 YAML 加载的 task 定义（含 task_id / expected_events / partial_signals 等）
    - actual_events：task 执行后从 EventStore 查到的事件列表
    - rubric：scoring_rubrics.yaml 中的 tier1-v1 rubric（None 时使用默认权重）
    - token_usage：本次 task 消耗的 token 数量（input+output 之和）

    评分流程：
    1. EventStore 断言（event_store_assert）
    2. 计算 match_ratio
    3. 决定 verdict（PASS / FAIL / 触发 LLM judge）
    4. 加权计算 weighted_score
    """
    task_id = task.get("task_id", "UNKNOWN")
    expected_events: list[dict[str, Any]] = task.get("expected_events", []) or []

    # 默认 rubric 权重（与 scoring_rubrics.yaml tier1-v1 对齐）
    pass_fail_weight = 0.65
    partial_weight = 0.25
    efficiency_weight = 0.10

    if rubric:
        pass_fail_weight = rubric.get("pass_fail_weight", pass_fail_weight)
        partial_weight = rubric.get("partial_weight", partial_weight)
        efficiency_weight = rubric.get("efficiency_weight", efficiency_weight)

    try:
        # Step 1: EventStore 断言
        match_ratio, event_matches = event_store_assert(expected_events, actual_events)

        # Step 2: 决定 verdict
        judge_trigger = LLMJudgeTrigger()
        judge_result: JudgeResult | None = None
        pass_fail_score: float
        partial_score: float | None = None
        verdict: TaskVerdict

        if match_ratio == 1.0:
            # 全部命中 → PASS（不触发 LLM judge）
            verdict = TaskVerdict.PASS
            pass_fail_score = 1.0

        elif match_ratio == 0.0 and len(expected_events) > 0:
            # 全部未命中 → FAIL
            verdict = TaskVerdict.FAIL
            pass_fail_score = 0.0

        elif judge_trigger.should_trigger_judge(match_ratio):
            # 部分命中 [0.5, 1.0) → 触发 LLM judge（Phase A stub）
            judge_result = judge_trigger.invoke_judge(
                task_id=task_id,
                prompt=task.get("prompt", ""),
                expected_events=expected_events,
                actual_events=actual_events,
                match_ratio=match_ratio,
            )
            partial_score = judge_result.score
            # pass_fail 维度：按 match_ratio 线性插值（部分命中）
            pass_fail_score = match_ratio
            verdict = TaskVerdict.PARTIAL

        else:
            # match_ratio in (0.0, 0.5)：不触发 judge，直接 FAIL
            verdict = TaskVerdict.FAIL
            pass_fail_score = 0.0

        # Step 3: 效率分（Phase A ~ D: efficiency_baseline_tokens=null，不计入）
        efficiency_score: float | None = None
        if rubric and rubric.get("efficiency_baseline_tokens") is not None:
            baseline = rubric["efficiency_baseline_tokens"]
            if token_usage is not None and baseline > 0:
                # 效率分：token_usage / baseline（越低越好，上限 1.0）
                efficiency_score = min(1.0, baseline / token_usage)

        # Step 4: 加权总分
        partial_contribution = (partial_score or 0.0) * partial_weight
        efficiency_contribution = (efficiency_score or 0.0) * efficiency_weight
        weighted_score = (
            pass_fail_score * pass_fail_weight
            + partial_contribution
            + efficiency_contribution
        )

        return BenchmarkRunScore(
            task_id=task_id,
            verdict=verdict,
            pass_fail_score=pass_fail_score,
            partial_score=partial_score,
            efficiency_score=efficiency_score,
            weighted_score=weighted_score,
            event_matches=event_matches,
            match_ratio=match_ratio,
            judge_result=judge_result,
            token_usage=token_usage,
        )

    except Exception as exc:
        # 评分内部异常：不 silent fail，明确标记 ERROR verdict
        return BenchmarkRunScore(
            task_id=task_id,
            verdict=TaskVerdict.ERROR,
            error_message=f"scorer 内部异常: {type(exc).__name__}: {exc}",
        )


# ============================================================
# YAML task 加载工具函数
# ============================================================

def load_task_yaml(yaml_path: Path) -> dict[str, Any]:
    """从 YAML 文件加载 task 定义"""
    with yaml_path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_scoring_rubrics(rubrics_yaml_path: Path) -> dict[str, dict[str, Any]]:
    """
    从 scoring_rubrics.yaml 加载所有 rubric，返回 rubric_id → rubric dict 映射。
    """
    with rubrics_yaml_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    rubrics_list = data.get("rubrics", [])
    return {r["rubric_id"]: r for r in rubrics_list}


def fetch_events_from_store(
    event_store: EventStore,
    task_start_time: datetime.datetime,
    event_types: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    从 EventStore 查询 task 执行期间的事件。

    参数：
    - event_store：OctoAgent EventStore 实例
    - task_start_time：task 开始执行的时间（仅查询此时间之后的事件）
    - event_types：需要查询的 EventType 列表（None = 查询所有类型）

    返回：事件 dict 列表（扁平化，含 event_type / payload / created_at）

    注意（PoC-H0 实测）：
    - harness._store_group（private）需用 getattr(harness, "_store_group", None) 访问
    - 从 _store_group 获取 event_store 实例后再调用此函数
    """
    if event_types is None:
        # 默认查询 Tier 1 常用事件类型
        event_types = [
            EventType.MEMORY_ENTRY_ADDED,
            EventType.MEMORY_RECALL_COMPLETED,
            EventType.MEMORY_RECALL_SCHEDULED,
            EventType.TOOL_CALL_STARTED,
            EventType.TOOL_CALL_COMPLETED,
            EventType.TOOL_CALL_FAILED,
            EventType.SKILL_STARTED,
            EventType.SKILL_COMPLETED,
            EventType.SUBAGENT_SPAWNED,
            EventType.SUBAGENT_COMPLETED,
            EventType.WORKER_DISPATCHED,
            EventType.WORKER_RETURNED,
            EventType.WORKER_LOG_EMITTED,
            EventType.A2A_MESSAGE_SENT,
            EventType.A2A_MESSAGE_RECEIVED,
            EventType.ROUTINE_TRIGGERED,
            EventType.ROUTINE_COMPLETED,
            EventType.ROUTINE_SKIPPED,
            EventType.POLICY_DECISION,
            EventType.BEHAVIOR_PACK_LOADED,
            EventType.MODEL_CALL_COMPLETED,
            EventType.PIPELINE_RUN_UPDATED,
            EventType.RESOURCE_LIMIT_HIT,
            EventType.OBSERVATION_OBSERVED,
        ]

    raw_events = event_store.get_events_by_types_since(
        since=task_start_time,
        event_types=event_types,
    )

    # 将 OctoAgent Event 对象序列化为 scorer 可消费的 dict 格式
    result: list[dict[str, Any]] = []
    for evt in raw_events:
        # 兼容不同的 event 对象格式（dataclass / pydantic model / dict）
        if isinstance(evt, dict):
            result.append(evt)
        elif hasattr(evt, "__dict__"):
            d = evt.__dict__.copy()
            # event_type 统一为字符串
            if "event_type" in d and hasattr(d["event_type"], "value"):
                d["event_type"] = d["event_type"].value
            result.append(d)
        elif hasattr(evt, "model_dump"):
            d = evt.model_dump()
            if "event_type" in d and hasattr(d["event_type"], "value"):
                d["event_type"] = d["event_type"].value
            result.append(d)

    return result
