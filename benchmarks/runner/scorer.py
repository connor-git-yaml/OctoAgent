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
class AuditAssertionFailure:
    """Tier 3 audit chain 断言失败详情（T-C-6）。"""
    assertion_id: str
    kind: str               # "event_present" / "event_absent"
    event_type: str
    expected: dict[str, Any] = dataclasses.field(default_factory=dict)
    reason: str = ""        # 失败原因（"event_not_found" / "field_mismatch:<key>" / "forbidden_event_found"）
    closest_event: dict[str, Any] | None = None  # 部分命中时的最近事件（调试用）


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
    - audit_chain_failures：Tier 3 audit chain 失败断言详情（T-C-6 新增）
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

    # Tier 3 audit chain 失败断言详情（T-C-6）
    audit_chain_failures: list[AuditAssertionFailure] = dataclasses.field(default_factory=list)

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
    judge_trigger: LLMJudgeTrigger | None = None,
) -> BenchmarkRunScore:
    """
    Tier 1 task 主评分函数。

    参数：
    - task：从 YAML 加载的 task 定义（含 task_id / expected_events / partial_signals 等）
    - actual_events：task 执行后从 EventStore 查到的事件列表
    - rubric：scoring_rubrics.yaml 中的 tier1-v1 rubric（None 时使用默认权重）
    - token_usage：本次 task 消耗的 token 数量（input+output 之和）
    - judge_trigger：（F103d Phase E）外部注入的 LLMJudgeTrigger 实例（含 adapter）；
      None 时新建默认 LLMJudgeTrigger（adapter=StubJudgeAdapter，Phase A 默认行为）.
      Phase E runner 通过 ``LLMJudgeTrigger(adapter=ProviderRouterJudgeAdapter(...))``
      注入控变量 bench alias 真实 judge 路径；单测 / Phase A-D 不传保持向后兼容。

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

        # Step 2: 决定 verdict（外部 trigger 优先，否则新建默认 stub）
        if judge_trigger is None:
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
    # F114 假 0 修复（2026-06-07）：threat_scanner domain task 断言 ThreatScanner BLOCK
    # 路径 emit 的 MEMORY_ENTRY_BLOCKED（policy.py:222）。此前漏在默认查询列表外，导致
    # fetch_events_from_store 取不到该事件 → 即便 task 真触发 BLOCK 仍系统性 FAIL（第二重假 0）。
    EventType.MEMORY_ENTRY_BLOCKED,
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


# ============================================================
# Tier 3 评分（spec FR-B01 + FR-B04 + FR-F01~F03，T-C-6 实施）
# ============================================================

# Tier 3 默认查询 EventType 列表（实测真名）。score_tier3 调用 fetch_events_from_store
# 时使用，覆盖 H1/H2/H3-A/H3-B/H3-WW 5 task audit chain 所需事件。
# Codex Phase C Round 3 P2-2 部分接受：加 AGENT_SESSION_TURN_PERSISTED 让以后的 H1 task
# 可用 turn-level agent_session_kind 信号做更精确 H1 不变量断言（当前 yaml 未引入该字段
# 因为合法主 Agent user_channel session 也会有 ASSISTANT_MESSAGE turn，简单 event_absent
# 会误伤；精确表达需 conditional 逻辑——推迟 Phase D scorer 扩展）。
DEFAULT_TIER3_EVENT_TYPES: list[EventType] = [
    EventType.SUBAGENT_SPAWNED,
    EventType.SUBAGENT_COMPLETED,
    EventType.CONTROL_METADATA_UPDATED,   # F098 引入，承载 ask_back / N-H1 / source_runtime_kind 信号
    EventType.MEMORY_RECALL_COMPLETED,    # F094 B6 字段：queried_namespace_kinds / agent_runtime_id
    EventType.MEMORY_ENTRY_ADDED,
    EventType.WORKER_DISPATCHED,
    EventType.WORKER_RETURNED,
    EventType.STATE_TRANSITION,           # H3-B WAITING_INPUT → RUNNING 验证
    EventType.AGENT_SESSION_TURN_PERSISTED,  # F093 引入，含 agent_session_kind / kind 字段
]


def _get_nested_field(payload: dict[str, Any], dot_path: str) -> tuple[Any, bool]:
    """根据 dot path 取嵌套 dict 字段。

    用于 Tier 3 audit_assertions 访问 ``control_metadata.subagent_delegation.caller_project_id``
    等多层路径。dot path 仅在普通 key 之间分割；如果中间路径不是 dict 或 key 缺失，
    返回 (None, False) 表示字段不存在。

    Returns:
        (value, exists)：exists=False 表示路径中某段不存在或不是 dict。
    """
    if not dot_path:
        return (None, False)
    parts = dot_path.split(".")
    cur: Any = payload
    for part in parts:
        if not isinstance(cur, dict):
            return (None, False)
        if part not in cur:
            return (None, False)
        cur = cur[part]
    return (cur, True)


def _match_required_fields_tier3(
    event_payload: dict[str, Any],
    required_fields: dict[str, Any],
) -> tuple[bool, str]:
    """Tier 3 增强版字段匹配：支持嵌套 dot path + 复用 Phase A 语义。

    与 Phase A `_match_required_fields` 区别：
    - 支持嵌套 dot path（如 ``control_metadata.subagent_delegation.caller_project_id``）
    - 不复用 `tool_name_contains` 多候选字段语义（Tier 3 不需要）
    - 返回 (matched: bool, first_failing_key: str) 而非 missing list（用于 audit failure 报告）

    支持的约束：
    - 精确匹配：``{"source": "worker_runtime_dispatch"}``
    - 字符串包含：``{"target_worker_contains": "research"}``（包括 ``_contains: ""`` 仅检查字段存在 + 非空）
    - list 字段 contains：actual 为 list 时做 element-in-list 检查（与 Phase A 一致）
    - 嵌套 dot path：``{"control_metadata.is_caller_worker_signal": "1"}``
    """
    for key, expected_value in required_fields.items():
        # 拆分 base_field（去 _contains 后缀）和实际查询路径
        is_contains_check = key.endswith("_contains")
        base_path = key[: -len("_contains")] if is_contains_check else key

        actual_value, exists = _get_nested_field(event_payload, base_path)

        # 空 / null 约束：要求字段存在且非空（与 Phase A MED-7 修复保持一致）
        # Codex Phase C Round 2 P2-3 修复：扩展"非空"判定，覆盖 list/dict 容器
        # —— `_contains: ""` 对 list 字段必须 len > 0，对 dict 字段必须非空 dict，
        # 否则 H3-A caller_memory_namespace_ids=[] 退化场景会 false PASS
        if expected_value is None or expected_value == "":
            if not exists:
                return (False, key)
            # 任何容器类型的"空"都判为字段不存在（list / dict / str / None 统一处理）
            if actual_value is None:
                return (False, key)
            if isinstance(actual_value, (str, list, dict, tuple, set)) and len(actual_value) == 0:
                return (False, key)
            continue

        if not exists:
            return (False, key)

        if is_contains_check:
            # list 字段 contains：element-in-list 比对
            if isinstance(actual_value, list):
                if expected_value not in actual_value:
                    return (False, key)
            else:
                if str(expected_value) not in str(actual_value):
                    return (False, key)
        else:
            # 精确匹配（容错：字符串化比较，与 Phase A 一致）
            if str(actual_value) != str(expected_value):
                return (False, key)

    return (True, "")


def _assert_event_present(
    actual_events: list[dict[str, Any]],
    event_type: str,
    required_fields: dict[str, Any],
) -> tuple[bool, AuditAssertionFailure | None, dict[str, Any] | None]:
    """断言：存在至少一条 event_type 事件，其 payload 满足 required_fields 全部条件。

    Returns:
        (passed, failure_or_None, matched_event_or_None)
        - passed=True：matched_event 是命中事件
        - passed=False：failure 含 reason + closest_event（最近未命中的同类型事件）
    """
    # 过滤同类型事件
    candidates = [e for e in actual_events if e.get("event_type") == event_type]
    if not candidates:
        return (
            False,
            AuditAssertionFailure(
                assertion_id="",
                kind="event_present",
                event_type=event_type,
                expected=dict(required_fields),
                reason="event_not_found",
            ),
            None,
        )

    closest_event: dict[str, Any] | None = None
    first_failing_key = ""
    for candidate in candidates:
        # Phase A 兼容性：payload 可能嵌套在 "payload" 子字段，也可能扁平
        payload = candidate.get("payload", candidate)
        if not isinstance(payload, dict):
            payload = {}
        matched, failing_key = _match_required_fields_tier3(payload, required_fields)
        if matched:
            return (True, None, candidate)
        if closest_event is None:
            closest_event = candidate
            first_failing_key = failing_key

    return (
        False,
        AuditAssertionFailure(
            assertion_id="",
            kind="event_present",
            event_type=event_type,
            expected=dict(required_fields),
            reason=f"field_mismatch:{first_failing_key}",
            closest_event=closest_event,
        ),
        None,
    )


def _assert_event_absent(
    actual_events: list[dict[str, Any]],
    event_type: str,
    required_fields: dict[str, Any],
) -> tuple[bool, AuditAssertionFailure | None]:
    """断言：不存在任何 event_type 事件 payload 满足 required_fields 全部条件。

    语义边界（Codex review 重点）：
    - 若 required_fields 为空 → 任何同类型事件都视作命中 → 任何同类型事件存在即 FAIL
      （用于"完全禁止某 event_type"场景）
    - 若 required_fields 非空 → 仅当存在事件满足 required_fields 全部条件才 FAIL
      （用于"禁止某 event_type 出现特定 payload 组合"场景）

    Returns:
        (passed, failure_or_None)
    """
    candidates = [e for e in actual_events if e.get("event_type") == event_type]
    if not candidates:
        # 完全没有同类型事件 → 必然 absent，PASS
        return (True, None)

    if not required_fields:
        # 无 required_fields 约束：任何同类型事件都视作命中
        return (
            False,
            AuditAssertionFailure(
                assertion_id="",
                kind="event_absent",
                event_type=event_type,
                expected={},
                reason="forbidden_event_found",
                closest_event=candidates[0],
            ),
        )

    for candidate in candidates:
        payload = candidate.get("payload", candidate)
        if not isinstance(payload, dict):
            payload = {}
        matched, _ = _match_required_fields_tier3(payload, required_fields)
        if matched:
            return (
                False,
                AuditAssertionFailure(
                    assertion_id="",
                    kind="event_absent",
                    event_type=event_type,
                    expected=dict(required_fields),
                    reason="forbidden_event_found",
                    closest_event=candidate,
                ),
            )

    return (True, None)


def audit_chain_assert(
    audit_assertions: list[dict[str, Any]],
    actual_events: list[dict[str, Any]],
) -> tuple[bool, list[AuditAssertionFailure]]:
    """Tier 3 核心断言函数：逐条遍历 audit_assertions。

    全部断言通过 → (True, [])；任一断言失败 → (False, list_of_failures)。
    所有失败 assertion 都会被记录（不在第一条失败就 short-circuit），便于
    一次性看清所有失败原因——符合 spec FR-F03 "逐条断言报告"。

    每条 assertion 必填字段：
    - assertion_id: str
    - kind: "event_present" | "event_absent"
    - event_type: str
    - required_fields: dict (可空 dict)
    可选字段：
    - description: str（注释用，不影响断言逻辑）
    """
    failures: list[AuditAssertionFailure] = []
    if not audit_assertions:
        # 无断言 = 无法判分（避免 silent PASS，Codex MED-6 no silent cap）
        return (
            False,
            [
                AuditAssertionFailure(
                    assertion_id="<no_assertions>",
                    kind="meta",
                    event_type="",
                    reason="audit_assertions 为空，Tier 3 无法判分",
                )
            ],
        )

    for assertion in audit_assertions:
        assertion_id = str(assertion.get("assertion_id", "")) or "<unknown>"
        kind = str(assertion.get("kind", "")).strip().lower()
        event_type = str(assertion.get("event_type", "")).strip()
        # Codex Phase C Round 3 P3 修复：先保留原值做类型校验，避免 `or {}` 短路
        # 把 falsy 非 dict（如 []、""、0）转换成合法空 dict 导致 malformed assertion 静默 PASS
        raw_fields = assertion.get("required_fields")
        if raw_fields is None:
            required_fields: dict[str, Any] = {}
        elif isinstance(raw_fields, dict):
            required_fields = raw_fields
        else:
            failures.append(AuditAssertionFailure(
                assertion_id=assertion_id,
                kind=kind or "meta",
                event_type=event_type,
                reason="required_fields_must_be_dict",
            ))
            continue

        if not event_type:
            failures.append(AuditAssertionFailure(
                assertion_id=assertion_id,
                kind=kind or "meta",
                event_type="",
                reason="event_type_missing",
            ))
            continue

        if kind == "event_present":
            ok, failure, _ = _assert_event_present(actual_events, event_type, required_fields)
            if not ok and failure is not None:
                failure.assertion_id = assertion_id
                failures.append(failure)
        elif kind == "event_absent":
            ok, failure = _assert_event_absent(actual_events, event_type, required_fields)
            if not ok and failure is not None:
                failure.assertion_id = assertion_id
                failures.append(failure)
        else:
            failures.append(AuditAssertionFailure(
                assertion_id=assertion_id,
                kind=kind or "<missing>",
                event_type=event_type,
                expected=dict(required_fields),
                reason=f"unknown_kind:{kind}",
            ))

    return (len(failures) == 0, failures)


def _format_audit_failures(failures: list[AuditAssertionFailure]) -> str:
    """把 audit failure 列表格式化为 error_message 摘要（每条 ≤ 200 字符）。"""
    if not failures:
        return ""
    parts: list[str] = []
    for f in failures:
        # closest_event 中的 payload 可能很长——只保留 event_type + 前 80 字符 payload 摘要
        closest_summary = ""
        if f.closest_event:
            payload_summary = str(f.closest_event.get("payload", f.closest_event))[:80]
            closest_summary = f"; closest={payload_summary!r}"
        parts.append(
            f"[{f.assertion_id}] {f.kind} {f.event_type} FAIL "
            f"reason={f.reason} expected={f.expected}{closest_summary}"
        )
    return " | ".join(parts)


def score_tier3(
    task: dict[str, Any],
    actual_events: list[dict[str, Any]],
    rubric: dict[str, Any] | None = None,
    token_usage: int | None = None,
) -> BenchmarkRunScore:
    """Tier 3 task 主评分函数（spec FR-B04 / FR-F03 / AC4-3）。

    评分逻辑：audit_chain_assert（rubric pass_logic = "audit_chain_assert"）。
    - 逐条遍历 task.audit_assertions
    - 全部通过 → verdict=PASS, pass_fail_score=1.0
    - 任一不通过 → verdict=FAIL, pass_fail_score=0.0 + 记录所有失败断言详情
      （audit_chain_failures 字段 + error_message 摘要）

    rubric tier3-v1 是 100/0/0 二值评分，weighted_score = pass_fail_score（退化）。

    Args:
        task: YAML 加载的 task 定义（含 task_id / audit_assertions / rubric_id）
        actual_events: task 执行后从 EventStore 查到的事件列表
        rubric: scoring_rubrics.yaml tier3-v1（None 时用默认 100/0/0）
        token_usage: 本次 task input+output token 总数

    Returns:
        BenchmarkRunScore: verdict + pass_fail_score + audit_chain_failures 等
    """
    task_id = task.get("task_id", "T3-UNKNOWN")
    audit_assertions: list[dict[str, Any]] = task.get("audit_assertions", []) or []
    pass_fail_weight = (rubric or {}).get("pass_fail_weight", 1.0)

    try:
        passed, failures = audit_chain_assert(audit_assertions, actual_events)
        verdict = TaskVerdict.PASS if passed else TaskVerdict.FAIL
        pass_fail_score = 1.0 if passed else 0.0

        # Tier 3 rubric 100/0/0 二值评分：weighted_score 退化为 pass_fail_score
        # （与 _build_score 内 Tier 2 同 convention，未来 partial / efficiency 启用时再扩展）
        weighted_score = pass_fail_score

        error_message = _format_audit_failures(failures) if failures else None

        return BenchmarkRunScore(
            task_id=task_id,
            verdict=verdict,
            pass_fail_score=pass_fail_score,
            weighted_score=weighted_score,
            match_ratio=pass_fail_score,
            audit_chain_failures=failures,
            token_usage=token_usage,
            error_message=error_message,
        )
    except Exception as exc:
        return BenchmarkRunScore(
            task_id=task_id,
            verdict=TaskVerdict.ERROR,
            pass_fail_score=0.0,
            weighted_score=0.0,
            token_usage=token_usage,
            error_message=f"scorer 内部异常: {type(exc).__name__}: {exc}",
        )


# Codex Phase C Round 3 P2-1 修复：H3-WW worker A→worker B 的 grandchild task_id
# 必须能被发现。MAX_DESCENDANT_TRAVERSAL 是安全护栏，防止异常长 SUBAGENT_SPAWNED 链
# 导致查询无限放大（生产 DelegationManager.max_depth=2，正常 task 链 ≤ 3 层；
# 留 32 作为充裕上限，命中后停止扩散并 log warn 不 raise）。
MAX_DESCENDANT_TRAVERSAL = 32


async def fetch_events_from_store_tier3(
    event_store: EventStore,
    task_id: str,
    task_start_time: datetime.datetime,
    child_task_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Tier 3 专用 EventStore 查询封装：用 DEFAULT_TIER3_EVENT_TYPES 聚合父 + 子任务 +
    递归发现的孙任务事件。

    与 Phase A fetch_events_from_store 区别：
    - 默认 EventType 列表覆盖 SUBAGENT_SPAWNED / SUBAGENT_COMPLETED /
      CONTROL_METADATA_UPDATED / MEMORY_* / WORKER_* / STATE_TRANSITION /
      AGENT_SESSION_TURN_PERSISTED（Round 3 P2-2 加）
    - Codex Phase C review P1 修复（2026-05-29）：H1/H2/H3 关键信号写在 **child task_id** 上
      （worker_runtime_dispatch / subagent_delegation_init / Worker MEMORY_RECALL_COMPLETED /
      ask_back STATE_TRANSITION）；仅按父 task_id 查会让真实运行下 Tier 3 用例误判 FAIL。
      → 支持 child_task_ids 参数显式传入。
    - Codex Phase C Round 3 P2-1 修复（2026-05-30）：H3-WW worker A→worker B 的
      grandchild task_id 经常不在显式 child_task_ids 列表里——从已查到的 SUBAGENT_SPAWNED
      事件 payload.child_task_id 自动递归发现新的 descendant task_id 并继续查询，
      最多展开 MAX_DESCENDANT_TRAVERSAL 个 task_id（含 parent + children + grandchildren）。

    T-C-4 N-H1 验证关键：CONTROL_METADATA_UPDATED 是 N-H1 信号的承载体，必须包含在查询列表里。
    Phase D runner 接入点：runner 在派发子任务时可显式传 child_task_ids；不传时本函数自动
    从 SUBAGENT_SPAWNED 事件递归发现。
    scorer 按 (task_id, task_seq) 去重防止重复事件。

    Args:
        event_store: SqliteEventStore 实例
        task_id: 父 task_id（必填）
        task_start_time: 查询起点时间
        child_task_ids: 子任务 ID 列表（可选）；H1/H2/H3-A/H3-B/H3-WW 真实运行下必须传入
                       才能查到 worker / subagent 路径写入的关键事件。grandchild 自动递归
                       发现，调用方不必传完整 descendant 链。

    Returns:
        合并去重后的事件 dict 列表。每条事件已经 _normalize_event_to_dict 规范化字段名。
    """
    all_events: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, int]] = set()    # (task_id, task_seq) 去重

    # 用 BFS 遍历 task tree：pending 维持待查 task_id 队列；visited 防重入
    pending: list[str] = [task_id]
    visited: set[str] = set()
    if child_task_ids:
        for cid in child_task_ids:
            if cid and cid != task_id:
                pending.append(cid)

    while pending:
        if len(visited) >= MAX_DESCENDANT_TRAVERSAL:
            # 安全护栏：异常长链中止扩散（log 而非 raise，保证 Tier 3 评分仍可返回）
            import structlog as _structlog
            _structlog.get_logger(__name__).warning(
                "fetch_events_tier3_descendant_traversal_capped",
                root_task_id=task_id,
                visited_count=len(visited),
                remaining_pending=len(pending),
                cap=MAX_DESCENDANT_TRAVERSAL,
                hint="Worker→Worker chain 异常长，可能是审计回环或测试 fixture——停止扩散",
            )
            break

        tid = pending.pop(0)
        if not tid or tid in visited:
            continue
        visited.add(tid)

        events_for_id = await fetch_events_from_store(
            event_store=event_store,
            task_id=tid,
            task_start_time=task_start_time,
            event_types=DEFAULT_TIER3_EVENT_TYPES,
        )
        for evt in events_for_id:
            # 去重 key：(task_id, task_seq)；缺字段时退回 event_id（防御性）
            evt_task_id = str(evt.get("task_id", tid))
            evt_task_seq = evt.get("task_seq")
            if evt_task_seq is None:
                # task_seq 缺失时用 event_id 当唯一键（极少见的容错路径）
                dedup_key = (evt_task_id, hash(evt.get("event_id", "")))
            else:
                dedup_key = (evt_task_id, int(evt_task_seq))
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)
            all_events.append(evt)

            # 递归发现 grandchild：从 SUBAGENT_SPAWNED.payload.child_task_id 收集新 task_id
            if evt.get("event_type") == "SUBAGENT_SPAWNED":
                payload = evt.get("payload", {})
                if isinstance(payload, dict):
                    new_child = str(payload.get("child_task_id", "")).strip()
                    if new_child and new_child not in visited and new_child not in pending:
                        pending.append(new_child)

    return all_events
