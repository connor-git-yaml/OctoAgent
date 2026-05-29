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

# tool_name_contains 在多个候选字段中搜索（防止把多个字段无分隔拼接导致跨字段误匹配，
# Codex Phase A review MED-7 修复 2026-05-29）。按 OctoAgent 真实事件 payload 习惯
# 排序：TOOL_CALL_* / MEMORY_ENTRY_ADDED 用 `tool_name` / `tool` / `function_name` / `name`。
_TOOL_NAME_CANDIDATE_FIELDS: tuple[str, ...] = ("tool_name", "tool", "function_name", "name")


def _match_required_fields(
    event_payload: dict[str, Any],
    required_fields: dict[str, Any],
) -> tuple[bool, list[str]]:
    """
    检查实际事件的 payload 是否满足 required_fields 约束。

    支持的约束类型：
    - 精确匹配：required_fields = {"namespace_kind": "AGENT_PRIVATE"}
    - 字符串包含匹配：required_fields = {"preview_contains": "OctoAgent"}
      （以 _contains 结尾的 key 会用 in 操作符匹配目标字段）
    - list 字段 contains：当 payload[base_field] 是 list 时，做 element-in-list 检查
      （Codex Phase A review MED-7：list-aware contains，支持 queried_namespace_kinds 等）
    - tool_name_contains：在多个候选字段中任一命中即 OK
      （Codex Phase A review MED-7：从无分隔拼接改为按字段单独匹配）
    - 空 / null 约束：要求字段存在（Codex Phase A review MED-7）

    返回：(是否全部命中, 缺失/不匹配的字段列表)
    """
    missing: list[str] = []

    for key, expected_value in required_fields.items():
        # 空 / null 约束（Codex MED-7：从"无条件跳过"改为"必须字段存在"）
        if expected_value is None or expected_value == "":
            base_field = key[: -len("_contains")] if key.endswith("_contains") else key
            if base_field not in event_payload:
                missing.append(key)
            continue

        if key.endswith("_contains"):
            base_field = key[: -len("_contains")]

            # tool_name_contains：在候选字段中任一命中即 OK（不再拼接，避免跨字段误匹配）
            if base_field == "tool_name":
                hit = False
                for field in _TOOL_NAME_CANDIDATE_FIELDS:
                    field_val = event_payload.get(field)
                    if field_val is not None and str(expected_value) in str(field_val):
                        hit = True
                        break
                if not hit:
                    missing.append(key)
                continue

            actual_value = event_payload.get(base_field)
            if actual_value is None:
                missing.append(key)
                continue

            # list 字段 contains：element-in-list
            if isinstance(actual_value, list):
                if expected_value not in actual_value:
                    missing.append(key)
            else:
                if str(expected_value) not in str(actual_value):
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
            # Codex Phase A review MED-5 修复 2026-05-29:
            # PARTIAL 不再用 match_ratio 作 pass_fail_score（避免 LLM judge 与 EventStore
            # 匹配双重计分），pass_fail 二值化：PARTIAL → 0.0（partial 维度独立体现）
            pass_fail_score = 0.0
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

        # Step 4: 加权总分（活跃维度归一化）
        # Codex Phase A review HIGH-2 修复 2026-05-29:
        # 未触发的维度（partial / efficiency = None）不计入权重分母，
        # 否则 Phase A-D PASS task 上限只能到 pass_fail_weight (0.65)，
        # M5 baseline weighted_score 系统性偏低。
        active_pass_fail_weight = pass_fail_weight
        active_partial_weight = partial_weight if partial_score is not None else 0.0
        active_efficiency_weight = efficiency_weight if efficiency_score is not None else 0.0
        total_active_weight = (
            active_pass_fail_weight + active_partial_weight + active_efficiency_weight
        )

        if total_active_weight > 0:
            weighted_score = (
                pass_fail_score * active_pass_fail_weight
                + (partial_score or 0.0) * active_partial_weight
                + (efficiency_score or 0.0) * active_efficiency_weight
            ) / total_active_weight
        else:
            # 极端情况：所有 weight 都 0（rubric 配置异常），退化为 pass_fail_score
            weighted_score = pass_fail_score

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


DEFAULT_TIER1_EVENT_TYPES: list[EventType] = [
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


def _normalize_event_to_dict(evt: Any) -> dict[str, Any] | None:
    """将单个 Event 对象规范化为 scorer 可消费的 dict 格式。

    Event 模型字段名是 `type`（不是 `event_type`），scorer.event_store_assert 用
    `e.get("event_type")` 过滤——这里统一把 `type` 映射为 `event_type` 字符串。

    返回 None 表示无法处理的事件对象，调用方应跳过。
    """
    if isinstance(evt, dict):
        d = dict(evt)
    elif hasattr(evt, "model_dump"):
        # pydantic BaseModel（生产路径 SqliteEventStore 返回 Event）
        d = evt.model_dump()
    elif hasattr(evt, "__dict__"):
        d = dict(evt.__dict__)
    else:
        return None

    # 字段名标准化：Event.type → event_type（枚举值展平为字符串）
    raw_type = d.pop("type", None)
    if raw_type is None:
        raw_type = d.get("event_type")
    if raw_type is not None:
        d["event_type"] = raw_type.value if hasattr(raw_type, "value") else raw_type
    return d


async def fetch_events_from_store(
    event_store: EventStore,
    task_id: str,
    task_start_time: datetime.datetime,
    event_types: list[EventType] | None = None,
) -> list[dict[str, Any]]:
    """异步从 EventStore 查询 task 执行期间的事件。

    Codex Phase A review P1 修复（2026-05-29）：
    - `SqliteEventStore.get_events_by_types_since` 是 async 方法，
      真实签名 `(task_id: str, event_types: list[EventType], since_ts: datetime)`
    - `Event` 模型字段名是 `type` 不是 `event_type`（pydantic 字段）

    参数：
    - event_store：OctoAgent SqliteEventStore 实例
    - task_id：task ID（按 task 过滤；从调用方 BenchmarkRun 传入）
    - task_start_time：task 开始执行的时间（仅返回 ts >= task_start_time 的事件）
    - event_types：要查询的 EventType 列表（None = 使用 DEFAULT_TIER1_EVENT_TYPES）

    返回：事件 dict 列表，每条含 `event_type`（标准化为字符串）+ payload + 其他字段。

    注意（PoC-H0 实测）：
    - harness._store_group（private）需用 getattr(harness, "_store_group", None) 访问
    - 从 _store_group 获取 event_store 实例后再调用此函数
    """
    if event_types is None:
        event_types = DEFAULT_TIER1_EVENT_TYPES

    raw_events = await event_store.get_events_by_types_since(
        task_id=task_id,
        event_types=event_types,
        since_ts=task_start_time,
    )

    result: list[dict[str, Any]] = []
    for evt in raw_events:
        d = _normalize_event_to_dict(evt)
        if d is not None:
            result.append(d)
    return result


# ============================================================
# Tier 2 评分（spec FR-B01 / FR-E01~E04, T-B-4 实施）
# ============================================================


def _build_score(
    task_id: str,
    verdict: TaskVerdict,
    pass_fail_score: float,
    pass_fail_weight: float,
    token_usage: int | None = None,
    error_message: str | None = None,
) -> BenchmarkRunScore:
    """Tier 2 用的简化加权计算（partial / efficiency 暂未启用）.

    Tier 2 当前 rubric (tier2-tau-v1 + tier2-gaia-v1) 都是 100/0/0 二值评分，
    weighted_score 严格 = pass_fail_score（活跃归一化退化到 pass_fail）.
    """
    weighted_score = pass_fail_score  # 100/0/0 二值，归一化后等价
    return BenchmarkRunScore(
        task_id=task_id,
        verdict=verdict,
        pass_fail_score=pass_fail_score,
        weighted_score=weighted_score,
        match_ratio=pass_fail_score,
        token_usage=token_usage,
        error_message=error_message,
    )


def score_tier2_tau(
    task: Any,
    actual_tool_calls: list[dict[str, Any]],
    rubric: dict[str, Any] | None = None,
    token_usage: int | None = None,
) -> BenchmarkRunScore:
    """Tier 2 τ-bench Pass@1 评分（spec FR-B01 + FR-B05）.

    Pass@1 简化版（Phase B）:
    - 期望 action names：task.actions 中每条 `name`（W5 实测 list[dict] of {name, arguments}）
    - 实际 action names：actual_tool_calls 中每条 `name`（runner 收集）
    - 自动去 TAU_BENCH_TOOL_PREFIX（"tau_bench__"）前缀对齐
    - 完整覆盖（expected ⊆ actual unprefixed names）= PASS；任一 expected 缺失 = FAIL
    - 严格版（Phase D）: order-aware + arguments 比对，含 user_simulator multi-turn 检查

    Args:
        task: TauBenchTaskMeta（含 task_id / actions / user_id ...）
        actual_tool_calls: agent 实际调用记录，每条 dict 含 `name` (str) + 可选 `arguments`
        rubric: scoring_rubrics.yaml tier2-tau-v1（None 时用默认 100/0/0）
        token_usage: input+output token 总数

    Returns:
        BenchmarkRunScore: verdict + pass_fail_score + weighted_score 等
    """
    from collections import Counter

    task_id = getattr(task, "task_id", "T2-TAU-UNKNOWN")
    pass_fail_weight = (rubric or {}).get("pass_fail_weight", 1.0)

    try:
        expected_actions = getattr(task, "actions", None) or []
        # Codex Phase B review MED 修复 2026-05-29:
        # 用 Counter 保留同名 action 重复次数（spec FR-B01 + plan §4.1 W5 "actions 序列匹配率"）。
        # 旧 set 版本会让 "需要 5 次 update_reservation_flights" 的 task 被 agent 只调一次
        # 也判 PASS，系统性高估 Pass@1。
        expected_counter = Counter(
            a.get("name", "") for a in expected_actions if a.get("name")
        )
        if not expected_counter:
            # 无 expected actions = 无法判分，避免 silent PASS（Codex MED-6 no silent cap）
            return _build_score(
                task_id=task_id,
                verdict=TaskVerdict.ERROR,
                pass_fail_score=0.0,
                pass_fail_weight=pass_fail_weight,
                token_usage=token_usage,
                error_message="task.actions 为空，无法 Pass@1 评分",
            )

        # 实际 tool_calls 用 Counter；
        # Codex Phase B review MED-5 修复 2026-05-29:
        # 强制要求 startswith "tau_bench__" 前缀，否则忽略（避免 production 同名工具调用
        # 混入 actual_counter 导致 false PASS）。Phase D runner 必须保证 actual_tool_calls
        # 只含 benchmark_scope=tau_bench_benchmark 的 call.
        actual_counter: Counter[str] = Counter()
        for call in actual_tool_calls:
            name = call.get("name", "")
            if not name.startswith("tau_bench__"):
                continue  # 严格忽略非 tau_bench 调用
            unprefixed = name[len("tau_bench__"):]
            # 若 scope_id 形式（tau_bench__<scope_id>__<tool_name>），剥离 scope_id
            if "__" in unprefixed:
                # 形式："<scope_id>__<tool_name>" → 取 tool_name 部分
                _, tool_name_only = unprefixed.split("__", 1)
                actual_counter[tool_name_only] += 1
            else:
                actual_counter[unprefixed] += 1

        # Pass@1: 对每个 expected action name，actual 调用次数 >= expected 次数
        missing_calls: list[str] = []
        for name, expected_count in expected_counter.items():
            actual_count = actual_counter.get(name, 0)
            if actual_count < expected_count:
                missing_calls.append(f"{name}({actual_count}/{expected_count})")
        pass_at_1 = len(missing_calls) == 0

        return _build_score(
            task_id=task_id,
            verdict=TaskVerdict.PASS if pass_at_1 else TaskVerdict.FAIL,
            pass_fail_score=1.0 if pass_at_1 else 0.0,
            pass_fail_weight=pass_fail_weight,
            token_usage=token_usage,
            error_message=(
                f"Pass@1 缺调用: {', '.join(missing_calls)}" if missing_calls else None
            ),
        )
    except Exception as exc:
        return _build_score(
            task_id=task_id,
            verdict=TaskVerdict.ERROR,
            pass_fail_score=0.0,
            pass_fail_weight=pass_fail_weight,
            token_usage=token_usage,
            error_message=f"scorer 内部异常: {type(exc).__name__}: {exc}",
        )


def score_tier2_gaia(
    task: Any,
    actual_answer: str,
    rubric: dict[str, Any] | None = None,
    token_usage: int | None = None,
) -> BenchmarkRunScore:
    """Tier 2 GAIA fallback normalized 字符串匹配评分（spec FR-B01 + FR-E03）.

    Phase B 主路径: 走 gaia_fallback_adapter.match_answer（normalized + tolerance + alternates）.
    Phase D T-D-6 升级: 主路径不命中时触发 LLM-judge fallback（spec FR-B03）.

    Args:
        task: GaiaFallbackTaskMeta（含 task_id / expected_answer / tolerance / alternates）
        actual_answer: agent 实际回答（一般是 LLM 最终回复 plain text）
        rubric: scoring_rubrics.yaml tier2-gaia-v1（None 时用默认 100/0/0）
        token_usage: input+output token 总数
    """
    task_id = getattr(task, "task_id", "T2-GAIA-UNKNOWN")
    pass_fail_weight = (rubric or {}).get("pass_fail_weight", 1.0)

    try:
        # Lazy import 避免循环依赖（gaia_fallback_adapter 在 tier2/ 下）
        from benchmarks.tiers.tier2.gaia_fallback_adapter import match_answer

        matched = match_answer(actual_answer, task)
        return _build_score(
            task_id=task_id,
            verdict=TaskVerdict.PASS if matched else TaskVerdict.FAIL,
            pass_fail_score=1.0 if matched else 0.0,
            pass_fail_weight=pass_fail_weight,
            token_usage=token_usage,
        )
    except Exception as exc:
        return _build_score(
            task_id=task_id,
            verdict=TaskVerdict.ERROR,
            pass_fail_score=0.0,
            pass_fail_weight=pass_fail_weight,
            token_usage=token_usage,
            error_message=f"scorer 内部异常: {type(exc).__name__}: {exc}",
        )
