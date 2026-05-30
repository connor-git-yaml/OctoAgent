"""F103d Phase D T-D-6 — score_dispatch + LLM judge 单测."""

from __future__ import annotations

import pytest

from benchmarks.runner.llm_judge import (
    DEFAULT_JUDGE_MAX_TOKENS,
    DEFAULT_JUDGE_MODEL,
    DEFAULT_JUDGE_TEMPERATURE,
    LLM_JUDGE_MAX_CALLS_PER_TASK,
    LLM_JUDGE_TRIGGER_MAX_RATIO,
    LLM_JUDGE_TRIGGER_MIN_RATIO,
    JudgeResult,
    LLMJudgeTrigger,
    ProviderRouterJudgeAdapter,
    StubJudgeAdapter,
    _clip_score,
    _parse_judge_response,
)
from benchmarks.runner.score_dispatch import RunResult, score
from benchmarks.runner.scorer import TaskVerdict


# ---------------------------------------------------------------------------
# 触发常量锁死（F-01 patch known-issues 守护）
# ---------------------------------------------------------------------------


def test_llm_judge_trigger_constants_locked():
    """Phase D T-D-6 严格要求：触发常量不变。"""
    assert LLM_JUDGE_TRIGGER_MIN_RATIO == 0.5
    assert LLM_JUDGE_TRIGGER_MAX_RATIO == 1.0
    assert LLM_JUDGE_MAX_CALLS_PER_TASK == 2


def test_default_judge_model_is_sonnet_4_5():
    assert DEFAULT_JUDGE_MODEL == "claude-sonnet-4-5"
    assert DEFAULT_JUDGE_TEMPERATURE == 0.0


# ---------------------------------------------------------------------------
# LLMJudgeTrigger.should_trigger_judge
# ---------------------------------------------------------------------------


def test_should_trigger_below_min_no_trigger():
    trigger = LLMJudgeTrigger()
    assert trigger.should_trigger_judge(0.4) is False


def test_should_trigger_at_min_triggers():
    trigger = LLMJudgeTrigger()
    assert trigger.should_trigger_judge(0.5) is True


def test_should_trigger_at_max_no_trigger():
    """边界：match_ratio = 1.0 直接 PASS，不触发。"""
    trigger = LLMJudgeTrigger()
    assert trigger.should_trigger_judge(1.0) is False


def test_should_trigger_blocked_after_max_calls():
    trigger = LLMJudgeTrigger()
    # 先消耗 max calls
    for _ in range(LLM_JUDGE_MAX_CALLS_PER_TASK):
        trigger.invoke_judge("t1", "p", [], [], 0.6)
    assert trigger.should_trigger_judge(0.6) is False


# ---------------------------------------------------------------------------
# Adapter 注入 + fallback
# ---------------------------------------------------------------------------


def test_default_adapter_is_stub():
    trigger = LLMJudgeTrigger()
    result = trigger.invoke_judge("t1", "p", [], [], 0.6)
    assert result.is_stub is True
    assert 0.0 <= result.score <= 1.0


def test_provider_adapter_calls_chat_fn():
    """ProviderRouterJudgeAdapter 应用注入的 chat_fn 路径。"""
    calls = []

    def fake_chat(messages, model, temperature, max_tokens):
        calls.append({"model": model, "temperature": temperature, "max_tokens": max_tokens})
        return "0.85\nactual events covered most of the expected intent."

    adapter = ProviderRouterJudgeAdapter(chat_fn=fake_chat)
    trigger = LLMJudgeTrigger(adapter=adapter)
    result = trigger.invoke_judge(
        "t1",
        "demo prompt",
        [{"event_type": "MEMORY_ENTRY_ADDED"}],
        [{"event_type": "MEMORY_ENTRY_ADDED"}],
        0.5,
    )
    assert result.is_stub is False
    assert result.score == 0.85
    assert calls[0]["model"] == DEFAULT_JUDGE_MODEL
    assert calls[0]["temperature"] == 0.0


def test_provider_adapter_clips_oob_score():
    """LLM 返回 > 1 时 clip 到 1.0。"""

    def fake_chat(messages, model, temperature, max_tokens):
        return "1.5\nover"

    adapter = ProviderRouterJudgeAdapter(chat_fn=fake_chat)
    trigger = LLMJudgeTrigger(adapter=adapter)
    result = trigger.invoke_judge("t", "p", [], [], 0.6)
    assert result.score == 1.0
    assert result.is_stub is False


def test_provider_adapter_falls_back_on_exception():
    """LLM 调用失败 → fallback stub 0.5。"""

    def fake_chat(messages, model, temperature, max_tokens):
        raise RuntimeError("network down")

    adapter = ProviderRouterJudgeAdapter(chat_fn=fake_chat)
    trigger = LLMJudgeTrigger(adapter=adapter)
    result = trigger.invoke_judge("t", "p", [], [], 0.6)
    assert result.is_stub is True
    assert result.score == 0.5


def test_provider_adapter_falls_back_on_unparseable_response():
    def fake_chat(messages, model, temperature, max_tokens):
        return "this is not a number\nsome reasoning"

    adapter = ProviderRouterJudgeAdapter(chat_fn=fake_chat)
    trigger = LLMJudgeTrigger(adapter=adapter)
    result = trigger.invoke_judge("t", "p", [], [], 0.6)
    assert result.is_stub is True


def test_max_calls_per_task_enforced():
    """超 MAX_CALLS 不再调 LLM；返回 stub mid-score。"""
    call_count = 0

    def fake_chat(messages, model, temperature, max_tokens):
        nonlocal call_count
        call_count += 1
        return "0.7\nok"

    adapter = ProviderRouterJudgeAdapter(chat_fn=fake_chat)
    trigger = LLMJudgeTrigger(adapter=adapter)
    for _ in range(LLM_JUDGE_MAX_CALLS_PER_TASK):
        trigger.invoke_judge("t", "p", [], [], 0.6)
    # 第 3 次不再调 LLM
    result = trigger.invoke_judge("t", "p", [], [], 0.6)
    assert result.is_stub is True
    assert call_count == LLM_JUDGE_MAX_CALLS_PER_TASK


def test_reset_call_count_allows_more():
    trigger = LLMJudgeTrigger()
    for _ in range(LLM_JUDGE_MAX_CALLS_PER_TASK):
        trigger.invoke_judge("t", "p", [], [], 0.6)
    assert trigger.should_trigger_judge(0.6) is False
    trigger.reset_call_count()
    assert trigger.should_trigger_judge(0.6) is True


# ---------------------------------------------------------------------------
# _parse_judge_response / _clip_score
# ---------------------------------------------------------------------------


def test_parse_judge_response_with_reasoning():
    score, reasoning = _parse_judge_response("0.7\nbecause ...")
    assert score == 0.7
    assert "because" in reasoning


def test_parse_judge_response_no_reasoning():
    score, reasoning = _parse_judge_response("0.0")
    assert score == 0.0
    assert reasoning == ""


def test_parse_judge_response_invalid():
    with pytest.raises(ValueError):
        _parse_judge_response("not-a-number")


def test_clip_score_in_range():
    assert _clip_score(0.0) == 0.0
    assert _clip_score(0.5) == 0.5
    assert _clip_score(1.0) == 1.0


def test_clip_score_out_of_range():
    assert _clip_score(-0.5) == 0.0
    assert _clip_score(2.0) == 1.0


def test_clip_score_nan_returns_stub_mid():
    """HIGH-4 fix 回归：NaN 必须返回 0.5（防 reporter 计算抛 ValueError / NaN 污染）。"""
    import math

    assert _clip_score(math.nan) == 0.5


def test_clip_score_inf_returns_stub_mid():
    import math

    assert _clip_score(math.inf) == 0.5
    assert _clip_score(-math.inf) == 0.5


# ---------------------------------------------------------------------------
# score_dispatch.score（T-D-6 统一接口）
# ---------------------------------------------------------------------------


def test_score_dispatch_tier1_routes_to_tier1():
    """Tier 1 dict task → score_tier1 路径。"""
    task = {
        "task_id": "T1-X",
        "tier": 1,
        "domain": "memory",
        "expected_events": [
            {"event_type": "MEMORY_ENTRY_ADDED", "required_fields": {}},
        ],
        "rubric_id": "tier1-v1",
    }
    actual = [{"event_type": "MEMORY_ENTRY_ADDED", "payload": {}}]
    result = score(task, RunResult(actual_events=actual, token_usage=100))
    assert result.verdict in (TaskVerdict.PASS, TaskVerdict.FAIL, TaskVerdict.PARTIAL)


def test_score_dispatch_tier2_tau_routes_to_tau():
    """Tier 2 tau_bench domain → score_tier2_tau。"""

    class _TauMeta:
        task_id = "T2-TAU-1"
        tier = 2
        domain = "tau_bench_airline"
        actions = [{"name": "search_flights"}]

    result = score(
        _TauMeta(),
        RunResult(actual_tool_calls=[{"name": "tau_bench__search_flights"}], token_usage=100),
    )
    assert result.verdict in (TaskVerdict.PASS, TaskVerdict.FAIL)


def test_score_dispatch_tier2_gaia_routes_to_gaia():
    """Tier 2 gaia domain → score_tier2_gaia（用真实 GaiaFallbackTaskMeta dataclass）。"""
    from benchmarks.tiers.tier2.gaia_fallback_adapter import GaiaFallbackTaskMeta

    task = GaiaFallbackTaskMeta(
        task_id="T2-GAIA-1",
        domain="gaia_fallback",
        category="web_search",
        source_provenance="[GAIA-FALLBACK]",
        prompt="What is the answer?",
        expected_answer="42",
    )
    # 给 dataclass 加 tier 属性（score_dispatch 用 getattr 拿 tier）
    object.__setattr__(task, "tier", 2)

    result = score(task, RunResult(actual_answer="42", token_usage=100))
    assert result.verdict == TaskVerdict.PASS


def test_score_dispatch_tier2_unknown_domain_returns_error():
    class _Unknown:
        task_id = "T2-UNKNOWN"
        tier = 2
        domain = "weird_domain"

    result = score(_Unknown(), RunResult(token_usage=0))
    assert result.verdict == TaskVerdict.ERROR


def test_score_dispatch_tier3_routes_to_tier3():
    """Tier 3 dispatch：至少 1 assertion 匹配时 PASS。"""
    task = {
        "task_id": "T3-X",
        "tier": 3,
        "domain": "philosophy_h1",
        "audit_assertions": [
            {
                "assertion_id": "X-1",
                "kind": "event_present",
                "event_type": "SUBAGENT_SPAWNED",
                "required_fields": {},
            }
        ],
        "rubric_id": "tier3-v1",
    }
    events = [{"event_type": "SUBAGENT_SPAWNED", "payload": {"child_task_id": "child"}}]
    result = score(task, RunResult(actual_events=events))
    assert result.verdict == TaskVerdict.PASS


def test_score_dispatch_unsupported_tier():
    task = {"task_id": "T", "tier": 7, "domain": "x"}
    result = score(task, RunResult())
    assert result.verdict == TaskVerdict.ERROR


def test_score_dispatch_catches_internal_exceptions():
    """scorer 内部异常被 dispatch 捕获 → verdict=ERROR。"""
    # tier1 task 缺 expected_events 字段 → scorer 应该能处理（默认 []）
    # 这里构造一个会让 dispatch 内部炸的输入：task=None
    result = score(None, RunResult())  # type: ignore[arg-type]
    assert result.verdict == TaskVerdict.ERROR


def test_run_result_default_fields():
    rr = RunResult()
    assert rr.actual_events is None
    assert rr.actual_tool_calls is None
    assert rr.actual_answer is None
    assert rr.token_usage is None


def test_default_judge_max_tokens_constant():
    """合理上限保护，避免 token 暴炸。"""
    assert DEFAULT_JUDGE_MAX_TOKENS == 512
