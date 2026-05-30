"""F103d Phase D T-D-3 — worker.py asyncio 8 并发 + retry 单测."""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from pathlib import Path

import pytest

from benchmarks.runner.store import (
    RESULT_INFRA_ERROR,
    RESULT_PASS,
    RESULT_QUOTA_SKIP,
    RESULT_TIMEOUT,
    BenchmarkStore,
)
from benchmarks.runner.worker import (
    DEFAULT_MAX_RETRIES,
    GAIA_TASK_TIMEOUT_SECONDS,
    MAX_CONSECUTIVE_INFRA_ERRORS,
    ConsecutiveInfraErrorCounter,
    PlannedRun,
    TaskExecutionOutcome,
    exponential_backoff_with_jitter,
    filter_planned_for_resume,
    get_retry_after_seconds,
    is_rate_limit_error,
    planned_from_full_set,
    resolve_task_timeout,
    run_daily_bench,
    run_task_with_retry,
)


# ---------------------------------------------------------------------------
# Test TaskMeta（最小 dataclass）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FakeTask:
    task_id: str
    tier: int
    domain: str


def make_task(task_id: str = "T", *, tier: int = 1, domain: str = "memory") -> FakeTask:
    return FakeTask(task_id=task_id, tier=tier, domain=domain)


# ---------------------------------------------------------------------------
# is_rate_limit_error / get_retry_after_seconds / backoff
# ---------------------------------------------------------------------------


class _RateLimitErrorType(Exception):
    error_type = "rate_limit"


class _RateLimit429(Exception):
    status_code = 429


class _RateLimitWithRetryAfter(Exception):
    error_type = "rate_limit"
    retry_after = 7.5


def test_is_rate_limit_via_error_type():
    assert is_rate_limit_error(_RateLimitErrorType()) is True


def test_is_rate_limit_via_status_code():
    assert is_rate_limit_error(_RateLimit429()) is True


def test_is_rate_limit_generic_runtime_not_matched():
    """generic RuntimeError("quota exhausted") 不匹配（F087 quota_skip 协议）。"""
    assert is_rate_limit_error(RuntimeError("quota exhausted")) is False


def test_get_retry_after_seconds_from_attr():
    assert get_retry_after_seconds(_RateLimitWithRetryAfter()) == 7.5


def test_get_retry_after_seconds_none_when_missing():
    assert get_retry_after_seconds(RuntimeError("x")) is None


def test_get_retry_after_seconds_invalid_string():
    class _ExcWithBadAttr(Exception):
        retry_after = "not-a-number"

    assert get_retry_after_seconds(_ExcWithBadAttr()) is None


def test_exponential_backoff_jitter_within_range():
    rng = random.Random(42)
    for attempt in range(5):
        v = exponential_backoff_with_jitter(attempt, rng=rng)
        assert 0.0 <= v <= min(60.0, 2 ** attempt)


def test_exponential_backoff_cap():
    rng = random.Random(42)
    v = exponential_backoff_with_jitter(20, rng=rng)  # 2^20 远大于 60
    assert 0.0 <= v <= 60.0


def test_resolve_task_timeout_gaia():
    task = make_task(domain="gaia_fallback")
    assert resolve_task_timeout(task) == GAIA_TASK_TIMEOUT_SECONDS


def test_resolve_task_timeout_default():
    assert resolve_task_timeout(make_task(domain="memory")) == 300.0


# ---------------------------------------------------------------------------
# run_task_with_retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_task_with_retry_pass_first_try():
    async def runner(task, iteration):
        return TaskExecutionOutcome(
            result=RESULT_PASS, score=1.0, duration_seconds=0.1
        )

    out = await run_task_with_retry(make_task(), 1, runner)
    assert out.result == RESULT_PASS


@pytest.mark.asyncio
async def test_run_task_with_retry_timeout_no_retry():
    """asyncio.TimeoutError 不重试，直接 TIMEOUT。"""

    async def runner(task, iteration):
        await asyncio.sleep(10.0)
        return TaskExecutionOutcome(result=RESULT_PASS, score=1.0, duration_seconds=10.0)

    out = await run_task_with_retry(
        make_task(), 1, runner, timeout_seconds=0.05
    )
    assert out.result == RESULT_TIMEOUT
    assert "timeout" in (out.error_message or "")


@pytest.mark.asyncio
async def test_run_task_with_retry_rate_limit_recovers():
    """前 N 次 rate_limit，第 N+1 次 PASS。"""
    attempts: list[int] = []

    async def runner(task, iteration):
        attempts.append(len(attempts))
        if len(attempts) <= 1:
            raise _RateLimitErrorType("first attempt")
        return TaskExecutionOutcome(result=RESULT_PASS, score=1.0, duration_seconds=0.1)

    fake_sleeps: list[float] = []

    async def fake_sleep(d: float) -> None:
        fake_sleeps.append(d)

    out = await run_task_with_retry(
        make_task(), 1, runner, max_retries=3, sleep_fn=fake_sleep, rng=random.Random(0)
    )
    assert out.result == RESULT_PASS
    assert len(fake_sleeps) >= 1  # 至少睡过一次


@pytest.mark.asyncio
async def test_run_task_with_retry_rate_limit_exhausted_quota_skip():
    async def runner(task, iteration):
        raise _RateLimitErrorType("always fail")

    fake_sleeps: list[float] = []

    async def fake_sleep(d: float) -> None:
        fake_sleeps.append(d)

    out = await run_task_with_retry(
        make_task(), 1, runner, max_retries=3, sleep_fn=fake_sleep, rng=random.Random(0)
    )
    assert out.result == RESULT_QUOTA_SKIP
    assert len(fake_sleeps) == 2  # 重试 N-1 次睡眠


@pytest.mark.asyncio
async def test_run_task_with_retry_rate_limit_uses_retry_after_priority():
    """retry-after 优先于 exp backoff。"""

    async def runner(task, iteration):
        raise _RateLimitWithRetryAfter("with retry_after=7.5")

    fake_sleeps: list[float] = []

    async def fake_sleep(d: float) -> None:
        fake_sleeps.append(d)

    out = await run_task_with_retry(
        make_task(), 1, runner, max_retries=2, sleep_fn=fake_sleep, rng=random.Random(0)
    )
    assert out.result == RESULT_QUOTA_SKIP
    assert fake_sleeps[0] == 7.5  # 来自 retry_after，不是 backoff


@pytest.mark.asyncio
async def test_run_task_with_retry_keyboard_interrupt_propagates():
    """HIGH-1 fix 回归：KeyboardInterrupt 必须 propagate 不被吞掉。"""

    async def runner(task, iteration):
        raise KeyboardInterrupt("ctrl-c")

    with pytest.raises(KeyboardInterrupt):
        await run_task_with_retry(make_task(), 1, runner)


@pytest.mark.asyncio
async def test_run_task_with_retry_system_exit_propagates():
    """HIGH-1 fix 回归：SystemExit 必须 propagate。"""

    async def runner(task, iteration):
        raise SystemExit(2)

    with pytest.raises(SystemExit):
        await run_task_with_retry(make_task(), 1, runner)


@pytest.mark.asyncio
async def test_run_task_with_retry_infra_error_no_retry():
    """非 rate_limit 异常一次 INFRA_ERROR（不重试，避免掩盖 bug）。"""
    call_count = 0

    async def runner(task, iteration):
        nonlocal call_count
        call_count += 1
        raise RuntimeError("internal bug")

    out = await run_task_with_retry(make_task(), 1, runner, max_retries=3)
    assert out.result == RESULT_INFRA_ERROR
    assert call_count == 1  # 不重试
    assert "RuntimeError" in (out.error_message or "")


# ---------------------------------------------------------------------------
# ConsecutiveInfraErrorCounter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_counter_threshold_triggers_stop_event():
    counter = ConsecutiveInfraErrorCounter(threshold=3)
    for _ in range(3):
        await counter.record_result(RESULT_INFRA_ERROR)
    assert counter.stopped is True
    assert counter.stop_event.is_set()


@pytest.mark.asyncio
async def test_counter_resets_on_non_infra_result():
    counter = ConsecutiveInfraErrorCounter(threshold=3)
    await counter.record_result(RESULT_INFRA_ERROR)
    await counter.record_result(RESULT_INFRA_ERROR)
    await counter.record_result(RESULT_PASS)  # 重置
    assert counter.consecutive_count == 0
    assert counter.stopped is False


@pytest.mark.asyncio
async def test_counter_does_not_trigger_below_threshold():
    counter = ConsecutiveInfraErrorCounter(threshold=5)
    for _ in range(4):
        await counter.record_result(RESULT_INFRA_ERROR)
    assert counter.stopped is False


# ---------------------------------------------------------------------------
# run_daily_bench（集成 store / counter）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_daily_bench_writes_records_to_store(tmp_path: Path):
    store = BenchmarkStore(tmp_path / "bench.db")
    tasks = [make_task(f"T{i}") for i in range(5)]
    planned = planned_from_full_set(tasks, iterations=2)

    async def runner(task, iteration):
        return TaskExecutionOutcome(result=RESULT_PASS, score=1.0, duration_seconds=0.01)

    written = await run_daily_bench(
        planned,
        runner,
        store,
        session_id="s1",
        semaphore_size=3,
        ramp_delay_seconds=0.0,  # 不需 ramp（测试加速）
    )
    assert len(written) == 10
    assert {r.task_id for r in written} == {f"T{i}" for i in range(5)}
    persisted = store.get_runs_for_session("s1")
    assert len(persisted) == 10


@pytest.mark.asyncio
async def test_run_daily_bench_stops_on_consecutive_infra_errors(tmp_path: Path):
    """连续 5 个 INFRA_ERROR 触发 stop_event；后续 task 不跑。"""
    store = BenchmarkStore(tmp_path / "bench.db")
    tasks = [make_task(f"T{i}") for i in range(20)]
    planned = planned_from_full_set(tasks, iterations=1)

    seen_calls = 0

    async def runner(task, iteration):
        nonlocal seen_calls
        seen_calls += 1
        raise RuntimeError("infra bug")

    counter = ConsecutiveInfraErrorCounter(threshold=5)
    written = await run_daily_bench(
        planned,
        runner,
        store,
        session_id="s1",
        semaphore_size=1,  # 串行确保连续性
        ramp_delay_seconds=0.0,
        counter=counter,
    )
    assert counter.stopped is True
    # 至少跑 5 个达 threshold，剩下不再启动
    assert 5 <= len(written) < 20


@pytest.mark.asyncio
async def test_run_daily_bench_resume_via_filter(tmp_path: Path):
    """T-D-8 resume 路径：filter_planned_for_resume + 重跑只覆盖未完成。"""
    store = BenchmarkStore(tmp_path / "bench.db")
    tasks = [make_task(f"T{i}") for i in range(3)]
    planned = planned_from_full_set(tasks, iterations=2)

    # 先跑 partial（只 4 个）
    async def runner(task, iteration):
        return TaskExecutionOutcome(result=RESULT_PASS, score=1.0, duration_seconds=0.01)

    written_partial = await run_daily_bench(
        planned[:4],
        runner,
        store,
        session_id="resume-s",
        semaphore_size=2,
        ramp_delay_seconds=0.0,
    )
    assert len(written_partial) == 4
    assert len(store.get_runs_for_session("resume-s")) == 4

    # resume：planned 全集 - 已完成 = 剩余 2
    remaining = filter_planned_for_resume(planned, store, "resume-s")
    assert len(remaining) == 2

    # 再跑 remaining
    written_resume = await run_daily_bench(
        remaining,
        runner,
        store,
        session_id="resume-s",
        semaphore_size=2,
        ramp_delay_seconds=0.0,
    )
    assert len(written_resume) == 2
    assert len(store.get_runs_for_session("resume-s")) == 6


@pytest.mark.asyncio
async def test_run_daily_bench_progress_callback(tmp_path: Path):
    """on_record_written 实时回调（reporter 进度用）。"""
    store = BenchmarkStore(tmp_path / "bench.db")
    tasks = [make_task(f"T{i}") for i in range(3)]
    planned = planned_from_full_set(tasks, iterations=1)

    async def runner(task, iteration):
        return TaskExecutionOutcome(result=RESULT_PASS, score=1.0, duration_seconds=0.01)

    seen: list[str] = []
    await run_daily_bench(
        planned,
        runner,
        store,
        session_id="s1",
        semaphore_size=2,
        ramp_delay_seconds=0.0,
        on_record_written=lambda r: seen.append(r.task_id),
    )
    assert sorted(seen) == ["T0", "T1", "T2"]


@pytest.mark.asyncio
async def test_planned_from_full_set_3_iterations():
    tasks = [make_task("A"), make_task("B")]
    plan = planned_from_full_set(tasks, iterations=3)
    assert len(plan) == 6
    iters_for_a = sorted(p.iteration for p in plan if p.task.task_id == "A")
    assert iters_for_a == [1, 2, 3]


# ---------------------------------------------------------------------------
# Phase C 归档：H3-B follow_up_inputs helper（Round 4 P2-1）
# ---------------------------------------------------------------------------


def test_extract_follow_up_inputs_t3_h3b_format():
    """t3_h3b_001.yaml 实际格式：[{description, text}, ...]"""
    from benchmarks.runner.worker import extract_follow_up_inputs

    raw = {
        "follow_up_inputs": [
            {"description": "round 1", "text": "我的预算 5000-8000 元"},
            {"description": "round 2", "text": "5-7 天"},
        ]
    }
    assert extract_follow_up_inputs(raw) == ["我的预算 5000-8000 元", "5-7 天"]


def test_extract_follow_up_inputs_missing_returns_empty():
    from benchmarks.runner.worker import extract_follow_up_inputs

    assert extract_follow_up_inputs({}) == []
    assert extract_follow_up_inputs({"follow_up_inputs": None}) == []
    assert extract_follow_up_inputs({"follow_up_inputs": "not a list"}) == []


def test_extract_follow_up_inputs_skips_invalid_entries():
    from benchmarks.runner.worker import extract_follow_up_inputs

    raw = {
        "follow_up_inputs": [
            {"text": "valid"},
            {"description": "no text"},
            {"text": ""},  # 空 text 跳过
            "plain string entry",  # 也接受 str 形式
            42,  # 非 dict 非 str 跳过
        ]
    }
    assert extract_follow_up_inputs(raw) == ["valid", "plain string entry"]


@pytest.mark.asyncio
async def test_run_daily_bench_continues_on_single_task_unhandled_exception(tmp_path: Path):
    """HIGH-3 fix 回归：单 task 抛 unhandled exception 不拖垮整 batch。

    模拟一个 runner_fn 内部 bug（不是 catch-able），其他 task 必须照常完成。
    """
    store = BenchmarkStore(tmp_path / "bench.db")
    tasks = [make_task(f"T{i}") for i in range(5)]
    planned = planned_from_full_set(tasks, iterations=1)

    crash_task_ids: set[str] = set()

    async def runner(task, iteration):
        if task.task_id == "T2":
            # 模拟 runner_fn 内部 unhandled bug（不是 LLM 或 timeout 路径）
            raise Exception("bug in runner_fn")
        crash_task_ids.add(task.task_id)
        return TaskExecutionOutcome(result=RESULT_PASS, score=1.0, duration_seconds=0.01)

    written = await run_daily_bench(
        planned,
        runner,
        store,
        session_id="cs",
        semaphore_size=3,
        ramp_delay_seconds=0.0,
    )
    # T0/T1/T3/T4 PASS；T2 由 run_task_with_retry catch 后写 INFRA_ERROR
    pass_ids = {r.task_id for r in written if r.result == RESULT_PASS}
    assert pass_ids == {"T0", "T1", "T3", "T4"}


@pytest.mark.asyncio
async def test_run_daily_bench_no_threading_lock_across_await(tmp_path: Path):
    """Phase B 前车之鉴：worker 不持锁跨 await（counter / results_lock 用 asyncio.Lock）。

    回归测：高并发 task 同时完成不死锁（counter / results_lock 释放正常）。
    """
    store = BenchmarkStore(tmp_path / "bench.db")
    tasks = [make_task(f"T{i}") for i in range(20)]
    planned = planned_from_full_set(tasks, iterations=1)

    async def runner(task, iteration):
        # 让所有 task 几乎同时完成，最大化锁竞争
        await asyncio.sleep(0.01)
        return TaskExecutionOutcome(result=RESULT_PASS, score=1.0, duration_seconds=0.01)

    written = await asyncio.wait_for(
        run_daily_bench(
            planned,
            runner,
            store,
            session_id="hl",
            semaphore_size=8,
            ramp_delay_seconds=0.0,
        ),
        timeout=10.0,  # 不该卡死
    )
    assert len(written) == 20
