"""F103d Phase E 第 1 步 — octo_runner.runner_fn 4 tier 分派 + LLM judge wire 单测.

测试范围（不真打 LLM / 不起真 OctoHarness）:

- 4 tier 分派路由正确（tier=1 / 2-tau / 2-gaia / 3）
- score 调用链通（mock OctoHarness → mock events → score → TaskExecutionOutcome）
- LLM judge wire：``make_provider_router_chat_fn`` + ``_build_judge_trigger`` 构造
  ``ProviderRouterJudgeAdapter`` 并通过 ``chat_fn`` 路径调用 mock provider router
- 边界：tier 缺失 / domain 不识别 → ERROR outcome
- 兜底：runner_fn 内部异常返回 ERROR（不 raise）
- TaskExecutionOutcome 字段映射正确（result / duration / token / audit）

不在测试范围（推迟 Phase E 第 2 步）:
- 真 ProviderRouter chat 路径
- 真 OctoHarness bootstrap + e2e task 流程
- 真 tau_bench / GAIA dataset
"""

from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from benchmarks.runner import octo_runner
from benchmarks.runner.llm_judge import (
    LLMJudgeTrigger,
    ProviderRouterJudgeAdapter,
    StubJudgeAdapter,
)


# pytest-asyncio strict 模式：用 pytestmark 让所有 async 测试自动 asyncio mode.
# worktree 根没有 pyproject.toml 继承 octoagent/ 的 auto 模式，所以显式标记。
# 同步测试 PytestWarning 无害（不影响 pass 数）；CI strict 时可在 conftest 里
# 改用 collection_modifyitems 钩子只标 async。
pytestmark = pytest.mark.asyncio
from benchmarks.runner.scorer import BenchmarkRunScore, TaskVerdict
from benchmarks.runner.store import (
    RESULT_ERROR,
    RESULT_FAIL,
    RESULT_PASS,
    RESULT_TIMEOUT,
)


# ---------------------------------------------------------------------------
# Test fixtures + helpers
# ---------------------------------------------------------------------------


@dataclass
class FakeYamlTaskMeta:
    """模拟 cli.YamlTaskMeta（runner_fn 输入）。"""

    task_id: str
    tier: int
    domain: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class FakeTauTaskMeta:
    """模拟 Tier 2 τ-bench dataclass (TauBenchTaskMeta 兼容子集)。"""

    task_id: str
    tier: int = 2
    domain: str = "tau_bench_airline"
    task_idx: int = 0
    instruction: str = "请帮我取消航班 ABC123"
    actions: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class FakeGaiaTaskMeta:
    """模拟 Tier 2 GAIA fallback dataclass。"""

    task_id: str
    tier: int = 2
    domain: str = "gaia_fallback"
    prompt: str = "什么是 Pydantic？"
    expected_answer: str = "Pydantic 是 Python 数据验证库"
    expected_answer_alternates: list[str] = field(default_factory=list)
    expected_answer_tolerance: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def _make_score(
    *,
    verdict: TaskVerdict = TaskVerdict.PASS,
    pass_fail: float = 1.0,
    weighted: float = 1.0,
    error: str | None = None,
    audit_failures: list[Any] | None = None,
) -> BenchmarkRunScore:
    """构造 mock BenchmarkRunScore。"""
    return BenchmarkRunScore(
        task_id="MOCK",
        verdict=verdict,
        pass_fail_score=pass_fail,
        weighted_score=weighted,
        error_message=error,
        audit_chain_failures=audit_failures or [],
    )


@asynccontextmanager
async def _fake_harness_session(
    *,
    store_group: Any | None = None,
    task_runner: Any | None = None,
    provider_router: Any | None = None,
    tool_registry: Any | None = None,
) -> AsyncIterator[octo_runner.HarnessHandle]:
    """直接 yield mock HarnessHandle（绕过真 OctoHarness bootstrap）。"""
    app = MagicMock()
    app.state.store_group = store_group
    app.state.sse_hub = MagicMock()
    app.state.task_runner = task_runner
    yield octo_runner.HarnessHandle(
        harness=MagicMock(),
        app=app,
        project_root=MagicMock(),
        store_group=store_group,
        task_runner=task_runner,
        provider_router=provider_router,
        tool_registry=tool_registry,
    )


# ---------------------------------------------------------------------------
# tier 分派路由测试
# ---------------------------------------------------------------------------


def test_resolve_tier_domain_from_dataclass():
    task = FakeYamlTaskMeta(task_id="T1-001", tier=1, domain="memory", raw={})
    tier, domain = octo_runner._resolve_tier_domain(task)
    assert tier == 1
    assert domain == "memory"


def test_resolve_tier_domain_from_dict():
    task = {"task_id": "T2-TAU", "tier": 2, "domain": "tau_bench_airline"}
    tier, domain = octo_runner._resolve_tier_domain(task)
    assert tier == 2
    assert domain == "tau_bench_airline"


def test_resolve_tier_domain_default_when_missing():
    task = FakeYamlTaskMeta(task_id="X", tier=0, domain="")
    tier, domain = octo_runner._resolve_tier_domain(task)
    assert tier == 0
    assert domain == ""


def test_raw_dict_from_yaml_task_meta_raw():
    task = FakeYamlTaskMeta(
        task_id="T1-001", tier=1, domain="memory", raw={"prompt": "hi"}
    )
    raw = octo_runner._raw_dict(task)
    assert raw == {"prompt": "hi"}


def test_raw_dict_from_dict_passthrough():
    task = {"task_id": "T", "tier": 1, "domain": "memory", "prompt": "p"}
    raw = octo_runner._raw_dict(task)
    assert raw == task


def test_raw_dict_returns_empty_when_no_raw():
    """task_meta 没 raw / 不是 dict → 返回空 dict（避免 attribute error）。"""

    @dataclass
    class _NoRaw:
        task_id: str
        tier: int
        domain: str

    task = _NoRaw(task_id="X", tier=1, domain="memory")
    assert octo_runner._raw_dict(task) == {}


# ---------------------------------------------------------------------------
# total_tokens / outcome 映射
# ---------------------------------------------------------------------------


def test_total_tokens_sum_input_output_excludes_cache_read():
    """合计 input + output；不含 cache_read（避免缓存命中拉低真实成本）。"""
    assert octo_runner._total_tokens({"input": 100, "output": 50, "cache_read": 200}) == 150


def test_total_tokens_missing_keys_treated_as_zero():
    assert octo_runner._total_tokens({}) == 0
    assert octo_runner._total_tokens({"input": 10}) == 10


def test_outcome_from_score_pass_verdict_maps_to_result_pass():
    bench = _make_score(verdict=TaskVerdict.PASS, pass_fail=1.0, weighted=1.0)
    outcome = octo_runner._outcome_from_score(bench, started_at=0.0, token_usage={"input": 5, "output": 3, "cache_read": 0})
    assert outcome.result == RESULT_PASS
    assert outcome.score == 1.0
    assert outcome.token_input == 5
    assert outcome.token_output == 3
    assert outcome.duration_seconds >= 0


def test_outcome_from_score_fail_verdict_maps_to_result_fail():
    bench = _make_score(verdict=TaskVerdict.FAIL, pass_fail=0.0, weighted=0.0, error="boom")
    outcome = octo_runner._outcome_from_score(
        bench, started_at=0.0, token_usage={"input": 0, "output": 0, "cache_read": 0}
    )
    assert outcome.result == RESULT_FAIL
    assert outcome.score == 0.0
    assert outcome.error_message == "boom"


def test_outcome_from_score_error_verdict_maps_to_result_error():
    bench = _make_score(verdict=TaskVerdict.ERROR, error="scorer crash")
    outcome = octo_runner._outcome_from_score(
        bench, started_at=0.0, token_usage={"input": 0, "output": 0, "cache_read": 0}
    )
    assert outcome.result == RESULT_ERROR
    assert outcome.error_message == "scorer crash"


def test_outcome_from_score_audit_failures_serialized_to_json():
    """Tier 3 audit chain failures 字段必须序列化到 audit_assertions_json."""
    from benchmarks.runner.scorer import AuditAssertionFailure

    failures = [
        AuditAssertionFailure(
            assertion_id="H1-1",
            kind="event_present",
            event_type="SUBAGENT_SPAWNED",
            reason="event_not_found",
        )
    ]
    bench = _make_score(verdict=TaskVerdict.FAIL, audit_failures=failures)
    outcome = octo_runner._outcome_from_score(
        bench, started_at=0.0, token_usage={"input": 0, "output": 0, "cache_read": 0}
    )
    assert outcome.audit_assertions_json is not None
    assert "H1-1" in outcome.audit_assertions_json
    assert "SUBAGENT_SPAWNED" in outcome.audit_assertions_json


# ---------------------------------------------------------------------------
# token 字段解析（兼容多种 payload schema）
# ---------------------------------------------------------------------------


def test_read_token_field_top_level_key():
    assert octo_runner._read_token_field({"input_tokens": 42}, ("input_tokens",)) == 42


def test_read_token_field_alternate_names():
    assert (
        octo_runner._read_token_field({"prompt_tokens": 11}, ("input_tokens", "prompt_tokens"))
        == 11
    )


def test_read_token_field_nested_usage_object():
    payload = {"usage": {"input_tokens": 99}}
    assert octo_runner._read_token_field(payload, ("input_tokens",)) == 99


def test_read_token_field_no_match_returns_zero():
    assert octo_runner._read_token_field({"foo": "bar"}, ("input_tokens",)) == 0


def test_read_token_field_invalid_type_treated_as_zero():
    """非数字字符串 / None 不抛错，返回 0。"""
    assert octo_runner._read_token_field({"input_tokens": "not a number"}, ("input_tokens",)) == 0


# ---------------------------------------------------------------------------
# LLM judge wire：ProviderRouterJudgeAdapter chat_fn 单测
# ---------------------------------------------------------------------------


def test_make_provider_router_chat_fn_returns_callable():
    """工厂返回 callable，可被 ProviderRouterJudgeAdapter 接受。"""
    router = MagicMock()
    chat_fn = octo_runner.make_provider_router_chat_fn(router)
    assert callable(chat_fn)


def test_build_judge_trigger_returns_none_when_router_missing():
    """provider_router=None 时返回 None，让 score_tier1 走默认 stub 路径（不阻塞）。"""
    trigger = octo_runner._build_judge_trigger(None)
    assert trigger is None


def test_build_judge_trigger_returns_real_trigger_when_router_present():
    router = MagicMock()
    trigger = octo_runner._build_judge_trigger(router)
    assert isinstance(trigger, LLMJudgeTrigger)
    assert isinstance(trigger.adapter, ProviderRouterJudgeAdapter)


def test_chat_fn_uses_default_bench_model_alias():
    """chat_fn 强制走 bench alias，无视 adapter 传入的 model 参数（控变量）。

    make_provider_router_chat_fn 返回的 chat_fn 是 sync callable（被 LLM judge
    在 async context 中调用），内部桥接到 async _provider_router_chat。
    """
    router = MagicMock()
    captured: dict[str, Any] = {}

    async def _fake_chat(router_arg, *, messages, model, temperature, max_tokens):
        captured["model"] = model
        return {"text": "0.7", "usage": {}}

    with patch.object(octo_runner, "_provider_router_chat", new=_fake_chat):
        chat_fn = octo_runner.make_provider_router_chat_fn(router)
        # judge adapter 会传 "claude-sonnet-4-5" 这个 default model 进来——
        # runner chat_fn 必须忽略它，走 alias=bench（控变量）
        result = chat_fn(
            [{"role": "user", "content": "judge this"}],
            "claude-sonnet-4-5",
            0.0,
            512,
        )

    assert result == "0.7"
    assert captured["model"] == octo_runner.DEFAULT_BENCH_MODEL_ALIAS


def test_chat_fn_uses_explicit_model_override():
    """显式 model 参数优先于 DEFAULT_BENCH_MODEL_ALIAS（octoagent.yaml 自定义）。"""
    router = MagicMock()
    captured: dict[str, Any] = {}

    async def _fake_chat(router_arg, *, messages, model, temperature, max_tokens):
        captured["model"] = model
        return {"text": "0.5", "usage": {}}

    with patch.object(octo_runner, "_provider_router_chat", new=_fake_chat):
        chat_fn = octo_runner.make_provider_router_chat_fn(router, model="custom-alias")
        result = chat_fn(
            [{"role": "user", "content": "x"}],
            "ignored",
            0.0,
            512,
        )

    assert result == "0.5"
    assert captured["model"] == "custom-alias"


async def test_provider_router_chat_uses_resolve_for_alias_and_client_call():
    """_provider_router_chat 真实路径：resolve_for_alias → client.call。

    验证 messages 拆分逻辑：第一个 system → instructions / 其余 → history。
    """
    router = MagicMock()
    fake_client = MagicMock()
    fake_client.call = AsyncMock(
        return_value=("Pydantic 是 Python 数据验证库", [], {"usage": {"input_tokens": 12, "output_tokens": 8}})
    )
    fake_resolved = MagicMock(client=fake_client, model_name="deepseek-ai/DeepSeek-V3.2", provider_id="siliconflow")
    router.resolve_for_alias = MagicMock(return_value=fake_resolved)

    messages = [
        {"role": "system", "content": "You are an expert."},
        {"role": "user", "content": "What is Pydantic?"},
    ]
    resp = await octo_runner._provider_router_chat(
        router, messages=messages, model="bench", temperature=0.0, max_tokens=512
    )

    assert resp["text"] == "Pydantic 是 Python 数据验证库"
    assert resp["usage"]["input_tokens"] == 12

    # 验证拆分逻辑
    router.resolve_for_alias.assert_called_once_with("bench", task_scope=None)
    fake_client.call.assert_awaited_once()
    call_kwargs = fake_client.call.call_args.kwargs
    assert call_kwargs["instructions"] == "You are an expert."
    assert len(call_kwargs["history"]) == 1
    assert call_kwargs["history"][0]["role"] == "user"
    assert call_kwargs["model_name"] == "deepseek-ai/DeepSeek-V3.2"


async def test_provider_router_chat_raises_when_router_incompatible():
    """router 不实现 resolve_for_alias → 显式 AttributeError 不静默吞掉。"""

    class _NotProviderRouter:
        pass

    with pytest.raises(AttributeError, match="resolve_for_alias"):
        await octo_runner._provider_router_chat(
            _NotProviderRouter(),
            messages=[{"role": "user", "content": "x"}],
            model="bench",
            temperature=0.0,
            max_tokens=10,
        )


async def test_provider_router_chat_no_system_message():
    """无 system message 时 instructions=""，所有 messages 进 history。"""
    router = MagicMock()
    fake_client = MagicMock()
    fake_client.call = AsyncMock(return_value=("ok", [], {}))
    fake_resolved = MagicMock(client=fake_client, model_name="m", provider_id="p")
    router.resolve_for_alias = MagicMock(return_value=fake_resolved)

    await octo_runner._provider_router_chat(
        router,
        messages=[{"role": "user", "content": "hi"}],
        model="bench",
        temperature=0.0,
        max_tokens=10,
    )
    call_kwargs = fake_client.call.call_args.kwargs
    assert call_kwargs["instructions"] == ""
    assert call_kwargs["history"] == [{"role": "user", "content": "hi"}]


# ---------------------------------------------------------------------------
# _normalize_chat_response：多形态兼容
# ---------------------------------------------------------------------------


def test_normalize_chat_response_dict_text_key():
    r = octo_runner._normalize_chat_response({"text": "hi", "usage": {"input_tokens": 5}})
    assert r["text"] == "hi"
    assert r["usage"]["input_tokens"] == 5


def test_normalize_chat_response_dict_content_key():
    r = octo_runner._normalize_chat_response({"content": "yo"})
    assert r["text"] == "yo"


def test_normalize_chat_response_str_passthrough():
    r = octo_runner._normalize_chat_response("plain")
    assert r["text"] == "plain"
    assert r["usage"] == {}


def test_normalize_chat_response_openai_style_choices():
    """openai chat completion 形态：resp.choices[0].message.content."""

    class _Msg:
        content = "from openai"

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]
        usage = {"input_tokens": 3}

    r = octo_runner._normalize_chat_response(_Resp())
    assert r["text"] == "from openai"
    assert r["usage"] == {"input_tokens": 3}


# ---------------------------------------------------------------------------
# runner_fn 4 tier 分派（high-level）
# ---------------------------------------------------------------------------


@pytest.fixture
def patched_harness_session(monkeypatch: pytest.MonkeyPatch):
    """patch octo_harness_session → mock handle，让 runner_fn 不真起 OctoHarness。"""

    state: dict[str, Any] = {}

    @asynccontextmanager
    async def _fake(
        *,
        credential_store: Any | None = None,
        llm_adapter: Any | None = None,
        project_template_root: Any | None = None,
    ) -> AsyncIterator[octo_runner.HarnessHandle]:
        # 把 mock objects 暴露给测试断言
        store_group = state.get("store_group", MagicMock())
        task_runner = state.get("task_runner", MagicMock())
        provider_router = state.get("provider_router", MagicMock())
        tool_registry = state.get("tool_registry", MagicMock())

        async with _fake_harness_session(
            store_group=store_group,
            task_runner=task_runner,
            provider_router=provider_router,
            tool_registry=tool_registry,
        ) as handle:
            state["handle"] = handle
            yield handle

    monkeypatch.setattr(octo_runner, "octo_harness_session", _fake)
    return state


async def test_runner_fn_tier1_dispatches_to_run_tier1(patched_harness_session):
    """tier=1 → 调用 _run_tier1（mock 路径）。"""
    task = FakeYamlTaskMeta(task_id="T1-MEM", tier=1, domain="memory", raw={"prompt": "p"})

    async def _fake_tier1(task_meta, iteration, rubrics, started_at):
        return octo_runner.TaskExecutionOutcome(
            result=RESULT_PASS, score=1.0, duration_seconds=0.1
        )

    with patch.object(octo_runner, "_run_tier1", new=_fake_tier1):
        out = await octo_runner.runner_fn(task, iteration=1)
    assert out.result == RESULT_PASS
    assert out.score == 1.0


async def test_runner_fn_tier2_tau_domain_dispatches_to_run_tier2_tau(patched_harness_session):
    """tier=2 + domain 含 tau → _run_tier2_tau。"""
    task = FakeTauTaskMeta(task_id="T2-TAU-1", domain="tau_bench_airline")
    invoked: dict[str, Any] = {}

    async def _fake(task_meta, iteration, rubrics, started_at):
        invoked["called"] = True
        invoked["task_id"] = task_meta.task_id
        return octo_runner.TaskExecutionOutcome(
            result=RESULT_PASS, score=1.0, duration_seconds=0.1
        )

    with patch.object(octo_runner, "_run_tier2_tau", new=_fake):
        out = await octo_runner.runner_fn(task, iteration=2)
    assert invoked["called"] is True
    assert invoked["task_id"] == "T2-TAU-1"
    assert out.result == RESULT_PASS


async def test_runner_fn_tier2_gaia_domain_dispatches_to_run_tier2_gaia(patched_harness_session):
    """tier=2 + domain 含 gaia → _run_tier2_gaia。"""
    task = FakeGaiaTaskMeta(task_id="T2-GAIA-1", domain="gaia_fallback")
    invoked: dict[str, Any] = {}

    async def _fake(task_meta, iteration, rubrics, started_at):
        invoked["called"] = True
        return octo_runner.TaskExecutionOutcome(
            result=RESULT_FAIL, score=0.0, duration_seconds=0.1
        )

    with patch.object(octo_runner, "_run_tier2_gaia", new=_fake):
        out = await octo_runner.runner_fn(task, iteration=1)
    assert invoked["called"] is True
    assert out.result == RESULT_FAIL


async def test_runner_fn_tier3_dispatches_to_run_tier3(patched_harness_session):
    """tier=3 → _run_tier3。"""
    task = FakeYamlTaskMeta(
        task_id="T3-H1", tier=3, domain="philosophy_h1", raw={"prompt": "delegate"}
    )

    async def _fake(task_meta, iteration, rubrics, started_at):
        return octo_runner.TaskExecutionOutcome(
            result=RESULT_PASS, score=1.0, duration_seconds=0.5
        )

    with patch.object(octo_runner, "_run_tier3", new=_fake):
        out = await octo_runner.runner_fn(task, iteration=1)
    assert out.result == RESULT_PASS


async def test_runner_fn_unsupported_tier_returns_error():
    """tier=0 / tier=5 → ERROR outcome（不 raise）。"""
    task = FakeYamlTaskMeta(task_id="X", tier=99, domain="bogus")
    out = await octo_runner.runner_fn(task, iteration=1)
    assert out.result == RESULT_ERROR
    assert "unsupported tier" in (out.error_message or "")


async def test_runner_fn_tier2_unsupported_domain_returns_error():
    """tier=2 + domain 既不含 tau 也不含 gaia → ERROR。"""
    task = FakeYamlTaskMeta(task_id="X", tier=2, domain="weird_domain")
    out = await octo_runner.runner_fn(task, iteration=1)
    assert out.result == RESULT_ERROR
    assert "tier 2 unsupported domain" in (out.error_message or "")


async def test_runner_fn_internal_exception_returns_error_outcome(monkeypatch):
    """runner_fn 内部任何 exception 都不 raise，返回 ERROR + error_message。"""

    async def _raises(task_meta, iteration, rubrics, started_at):
        raise RuntimeError("simulated tier1 crash")

    monkeypatch.setattr(octo_runner, "_run_tier1", _raises)
    task = FakeYamlTaskMeta(task_id="X", tier=1, domain="memory", raw={})
    out = await octo_runner.runner_fn(task, iteration=1)
    assert out.result == RESULT_ERROR
    assert "simulated tier1 crash" in (out.error_message or "")


# ---------------------------------------------------------------------------
# _run_tier1：通过 mock harness 跑完整链路（不真打 LLM）
# ---------------------------------------------------------------------------


async def test_run_tier1_full_chain_with_mock_harness_returns_pass(monkeypatch):
    """Tier 1 完整调用链：submit → fetch events → score → outcome。"""
    # mock store_group + event_store + task_store
    fake_event_store = MagicMock()
    fake_event_store.get_events_by_types_since = AsyncMock(return_value=[])
    fake_task_store = MagicMock()
    fake_task = MagicMock()
    # 模拟 SUCCEEDED 终态
    from octoagent.core.models.enums import TaskStatus

    fake_task.status = TaskStatus.SUCCEEDED
    fake_task_store.get_task = AsyncMock(return_value=fake_task)
    fake_sg = MagicMock(event_store=fake_event_store, task_store=fake_task_store)

    fake_task_runner = MagicMock(enqueue=AsyncMock())
    fake_router = MagicMock()

    # patch TaskService.create_task 返回固定 task_id
    async def _fake_create_task(self_, message):
        return "mock-task-id", True

    monkeypatch.setattr(
        "octoagent.gateway.services.task_service.TaskService.create_task",
        _fake_create_task,
    )

    # patch fetch_events_from_store 返回空 events（→ FAIL，因 expected_events 非空时 match_ratio=0）
    async def _fake_fetch(event_store, task_id, task_start_time, event_types=None):
        return []

    monkeypatch.setattr(octo_runner, "fetch_events_from_store", _fake_fetch)

    @asynccontextmanager
    async def _fake_session(**kwargs):
        async with _fake_harness_session(
            store_group=fake_sg,
            task_runner=fake_task_runner,
            provider_router=fake_router,
        ) as handle:
            yield handle

    monkeypatch.setattr(octo_runner, "octo_harness_session", _fake_session)

    task = FakeYamlTaskMeta(
        task_id="T1-MEM",
        tier=1,
        domain="memory",
        raw={
            "task_id": "T1-MEM",
            "tier": 1,
            "domain": "memory",
            "prompt": "remember X",
            "timeout_seconds": 5,
            # 无 expected_events → score_tier1 默认 PASS (空列表分支)
            "expected_events": [],
        },
    )

    out = await octo_runner._run_tier1(
        task, iteration=1, rubrics=None, started_at=asyncio.get_running_loop().time()
    )
    assert out.result == RESULT_PASS
    assert out.score == 1.0
    fake_task_runner.enqueue.assert_awaited()


async def test_run_tier1_timeout_returns_result_timeout(monkeypatch):
    """submit 等终态超时 → TIMEOUT outcome（不 raise）。"""
    fake_event_store = MagicMock()
    fake_task_store = MagicMock()

    # 模拟 task 永远停在 CREATED 状态（不达终态）→ 触发 _submit_and_wait_task 超时
    from octoagent.core.models.enums import TaskStatus

    fake_task = MagicMock()
    fake_task.status = TaskStatus.CREATED
    fake_task_store.get_task = AsyncMock(return_value=fake_task)
    fake_sg = MagicMock(event_store=fake_event_store, task_store=fake_task_store)

    fake_task_runner = MagicMock(enqueue=AsyncMock())
    fake_router = MagicMock()

    async def _fake_create_task(self_, message):
        return "mock-task-id", True

    monkeypatch.setattr(
        "octoagent.gateway.services.task_service.TaskService.create_task",
        _fake_create_task,
    )

    # 让 poll interval 飞快（不在测试里真等）
    monkeypatch.setattr(octo_runner, "TASK_POLL_INTERVAL_SECONDS", 0.01)

    @asynccontextmanager
    async def _fake_session(**kwargs):
        async with _fake_harness_session(
            store_group=fake_sg,
            task_runner=fake_task_runner,
            provider_router=fake_router,
        ) as handle:
            yield handle

    monkeypatch.setattr(octo_runner, "octo_harness_session", _fake_session)

    task = FakeYamlTaskMeta(
        task_id="T1-X",
        tier=1,
        domain="memory",
        raw={"prompt": "p", "timeout_seconds": 0.05},  # 极短 timeout 触发 TimeoutError
    )

    out = await octo_runner._run_tier1(
        task, iteration=1, rubrics=None, started_at=asyncio.get_running_loop().time()
    )
    assert out.result == RESULT_TIMEOUT
    assert "terminal state" in (out.error_message or "")


# ---------------------------------------------------------------------------
# Tier 1 + judge_trigger 注入：score 调用链确实传 trigger
# ---------------------------------------------------------------------------


async def test_run_tier1_passes_judge_trigger_to_score(monkeypatch):
    """_run_tier1 必须把 _build_judge_trigger 结果传给 score()。"""
    fake_event_store = MagicMock()
    fake_event_store.get_events_by_types_since = AsyncMock(return_value=[])
    from octoagent.core.models.enums import TaskStatus

    fake_task = MagicMock()
    fake_task.status = TaskStatus.SUCCEEDED
    fake_task_store = MagicMock()
    fake_task_store.get_task = AsyncMock(return_value=fake_task)
    fake_sg = MagicMock(event_store=fake_event_store, task_store=fake_task_store)

    fake_task_runner = MagicMock(enqueue=AsyncMock())
    fake_router = MagicMock()

    async def _fake_create_task(self_, message):
        return "mock-task-id", True

    monkeypatch.setattr(
        "octoagent.gateway.services.task_service.TaskService.create_task",
        _fake_create_task,
    )

    async def _fake_fetch(event_store, task_id, task_start_time, event_types=None):
        return []

    monkeypatch.setattr(octo_runner, "fetch_events_from_store", _fake_fetch)

    captured: dict[str, Any] = {}

    def _spy_score(task, run_result, *, rubrics=None, judge_trigger=None):
        captured["judge_trigger"] = judge_trigger
        return _make_score(verdict=TaskVerdict.PASS, weighted=1.0)

    monkeypatch.setattr(octo_runner, "score", _spy_score)

    @asynccontextmanager
    async def _fake_session(**kwargs):
        async with _fake_harness_session(
            store_group=fake_sg,
            task_runner=fake_task_runner,
            provider_router=fake_router,
        ) as handle:
            yield handle

    monkeypatch.setattr(octo_runner, "octo_harness_session", _fake_session)

    task = FakeYamlTaskMeta(
        task_id="T1-J",
        tier=1,
        domain="memory",
        raw={"prompt": "p", "timeout_seconds": 5},
    )
    await octo_runner._run_tier1(
        task, iteration=1, rubrics=None, started_at=asyncio.get_running_loop().time()
    )
    trigger = captured.get("judge_trigger")
    assert isinstance(trigger, LLMJudgeTrigger)
    assert isinstance(trigger.adapter, ProviderRouterJudgeAdapter)


async def test_run_tier1_judge_trigger_is_none_when_router_missing(monkeypatch):
    """provider_router=None → judge_trigger=None（不阻塞 score 调用）。"""
    fake_event_store = MagicMock()
    fake_event_store.get_events_by_types_since = AsyncMock(return_value=[])
    from octoagent.core.models.enums import TaskStatus

    fake_task = MagicMock()
    fake_task.status = TaskStatus.SUCCEEDED
    fake_task_store = MagicMock()
    fake_task_store.get_task = AsyncMock(return_value=fake_task)
    fake_sg = MagicMock(event_store=fake_event_store, task_store=fake_task_store)

    fake_task_runner = MagicMock(enqueue=AsyncMock())

    async def _fake_create_task(self_, message):
        return "mock-task-id", True

    monkeypatch.setattr(
        "octoagent.gateway.services.task_service.TaskService.create_task",
        _fake_create_task,
    )

    async def _fake_fetch(event_store, task_id, task_start_time, event_types=None):
        return []

    monkeypatch.setattr(octo_runner, "fetch_events_from_store", _fake_fetch)

    captured: dict[str, Any] = {}

    def _spy_score(task, run_result, *, rubrics=None, judge_trigger=None):
        captured["judge_trigger"] = judge_trigger
        return _make_score(verdict=TaskVerdict.PASS, weighted=1.0)

    monkeypatch.setattr(octo_runner, "score", _spy_score)

    @asynccontextmanager
    async def _fake_session(**kwargs):
        async with _fake_harness_session(
            store_group=fake_sg,
            task_runner=fake_task_runner,
            provider_router=None,  # 关键：无 router
        ) as handle:
            yield handle

    monkeypatch.setattr(octo_runner, "octo_harness_session", _fake_session)

    task = FakeYamlTaskMeta(
        task_id="T1-NR",
        tier=1,
        domain="memory",
        raw={"prompt": "p", "timeout_seconds": 5},
    )
    await octo_runner._run_tier1(
        task, iteration=1, rubrics=None, started_at=asyncio.get_running_loop().time()
    )
    assert captured.get("judge_trigger") is None


# ---------------------------------------------------------------------------
# score_tier1 真实接受 judge_trigger 注入（不 mock score）
# ---------------------------------------------------------------------------


def test_score_tier1_accepts_external_judge_trigger():
    """score_tier1 签名扩展后必须真接受 judge_trigger 参数（向后兼容）。"""
    from benchmarks.runner.scorer import score_tier1

    task = {"task_id": "T", "expected_events": []}
    # 不传 → 默认行为
    out_default = score_tier1(task, [])
    # 传 stub trigger → 行为应一致（无 expected events → PASS）
    out_with_stub = score_tier1(task, [], judge_trigger=LLMJudgeTrigger(adapter=StubJudgeAdapter()))
    assert out_default.verdict == TaskVerdict.PASS
    assert out_with_stub.verdict == TaskVerdict.PASS


def test_score_tier1_partial_path_uses_external_trigger():
    """match_ratio in [0.5, 1.0) 时，外部 trigger 的 adapter 被调用。"""
    from benchmarks.runner.scorer import score_tier1

    call_count = {"n": 0}

    @dataclass
    class _SpyAdapter:
        def judge(self, **kwargs):
            from benchmarks.runner.llm_judge import JudgeResult

            call_count["n"] += 1
            return JudgeResult(score=0.85, reasoning="from spy", is_stub=False)

    task = {
        "task_id": "T-PARTIAL",
        "prompt": "p",
        "expected_events": [
            {"event_type": "A", "required_fields": {}},
            {"event_type": "B", "required_fields": {}},
        ],
    }
    actual = [{"event_type": "A", "payload": {}}]  # 1/2 hit → match_ratio=0.5 → judge 触发
    spy_trigger = LLMJudgeTrigger(adapter=_SpyAdapter())
    out = score_tier1(task, actual, judge_trigger=spy_trigger)
    assert call_count["n"] == 1
    assert out.verdict == TaskVerdict.PARTIAL
    assert out.partial_score == 0.85


# ---------------------------------------------------------------------------
# score_dispatch.score 把 judge_trigger 转发到 Tier 1
# ---------------------------------------------------------------------------


def test_score_dispatch_forwards_judge_trigger_to_tier1():
    """score_dispatch.score 必须把 judge_trigger 转发给 score_tier1。"""
    from benchmarks.runner.score_dispatch import RunResult, score
    from benchmarks.runner.scorer import score_tier1 as _real_score_tier1

    captured: dict[str, Any] = {}

    def _spy(task, actual_events, rubric=None, token_usage=None, judge_trigger=None):
        captured["judge_trigger"] = judge_trigger
        return _real_score_tier1(task, actual_events, rubric, token_usage, judge_trigger)

    with patch("benchmarks.runner.score_dispatch.score_tier1", new=_spy):
        task = {"task_id": "T", "tier": 1, "domain": "memory", "expected_events": []}
        trigger = LLMJudgeTrigger()
        result = score(task, RunResult(actual_events=[]), judge_trigger=trigger)
    assert captured["judge_trigger"] is trigger
    assert result.verdict == TaskVerdict.PASS


# ---------------------------------------------------------------------------
# octo_harness_session 集成不真起 OctoHarness（仅验证 tmpdir 生命周期）
# ---------------------------------------------------------------------------


async def test_octo_harness_session_creates_isolated_tmpdir(monkeypatch):
    """每次进入 session 应创建独立 tmpdir（不复用 / 不污染宿主）。"""

    # mock OctoHarness + FastAPI 避免真 bootstrap
    captured_paths: list[Any] = []

    class _FakeOctoHarness:
        def __init__(self, *, project_root, credential_store, llm_adapter, mcp_servers_dir, data_dir):
            captured_paths.append(project_root)
            captured_paths.append(data_dir)

        async def bootstrap(self, app):
            # bootstrap 内部不做任何事（mock store_group）
            app.state.store_group = MagicMock()
            app.state.task_runner = MagicMock()
            app.state.provider_router = MagicMock()

        def commit_to_app(self, app):
            pass

        async def shutdown(self, app):
            pass

    monkeypatch.setattr(
        "octoagent.gateway.harness.octo_harness.OctoHarness", _FakeOctoHarness
    )

    # 也 mock get_registry（防止真 import production tool_registry singleton）
    monkeypatch.setattr(
        "octoagent.gateway.harness.tool_registry.get_registry",
        lambda: MagicMock(),
    )

    async with octo_runner.octo_harness_session() as handle1:
        project1 = handle1.project_root
        assert project1.exists()

    # tmpdir 应已清理
    assert not project1.exists()

    async with octo_runner.octo_harness_session() as handle2:
        project2 = handle2.project_root
        assert project2.exists()
        # 两次 session 用独立目录
        assert project2 != project1
