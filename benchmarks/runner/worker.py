"""F103d Phase D T-D-3 — asyncio 8 并发 runner（最高风险 task）.

核心 API（纯 orchestration 层，不耦合具体 LLM 路径）:

- ``run_task_with_retry(task, iteration, runner_fn, ...)`` — 单 task 执行 + retry-after
  优先 + exp backoff jitter + QUOTA_SKIP/TIMEOUT/INFRA_ERROR 三态分类
- ``run_daily_bench(planned, runner_fn, store, session_id, ...)`` — Semaphore(8) +
  gradual ramp 0.5s + 连续 5 INFRA_ERROR 主动 stop

并发设计（避免 Phase B threading.Lock 跨 await 陷阱）:

- 共享状态用 ``asyncio.Lock`` 保护（短临界区，不持锁 await 任务执行）
- ``ConsecutiveInfraErrorCounter`` 实例化在 ``run_daily_bench`` 入口（不做 module
  singleton；多 event loop 间天然隔离）
- ``asyncio.Semaphore(8)`` 限流；``asyncio.wait`` 而非 ``asyncio.gather``（让 stop 信号
  能及时取消未启动的 task）

零侵入：worker.py 不直接 import production；通过 ``TaskRunner`` Protocol 让 caller
（Phase E baseline 跑或单测）注入真实 OctoHarness wire 或 mock runner。
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from benchmarks.runner.store import (
    EXCLUDED_FROM_DENOMINATOR,
    RESULT_ERROR,
    RESULT_FAIL,
    RESULT_INFRA_ERROR,
    RESULT_INCONSISTENT,
    RESULT_PARTIAL,
    RESULT_PASS,
    RESULT_QUOTA_SKIP,
    RESULT_TIMEOUT,
    BenchmarkRunRecord,
    BenchmarkStore,
    make_run_id,
    utcnow_iso,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants（plan §6.2 + spec FR-A01~A07）
# ---------------------------------------------------------------------------

DEFAULT_SEMAPHORE_SIZE = 8
DEFAULT_RAMP_DELAY_SECONDS = 0.5
DEFAULT_MAX_RETRIES = 3
DEFAULT_TASK_TIMEOUT_SECONDS = 300.0
GAIA_TASK_TIMEOUT_SECONDS = 480.0  # plan NEW-R4 GAIA L2
MAX_CONSECUTIVE_INFRA_ERRORS = 5
MAX_BACKOFF_SECONDS = 60.0
RATE_LIMIT_ERROR_TYPE = "rate_limit"
HTTP_RATE_LIMIT_STATUS = 429


# ---------------------------------------------------------------------------
# Protocols（不耦合具体 LLM/harness 实现）
# ---------------------------------------------------------------------------


@runtime_checkable
class TaskMeta(Protocol):
    """单 task 元数据最小契约（runner 需要的字段）。"""

    @property
    def task_id(self) -> str: ...
    @property
    def tier(self) -> int: ...
    @property
    def domain(self) -> str: ...


@dataclass(frozen=True, slots=True)
class TaskExecutionOutcome:
    """单 task 执行结果（runner_fn 返回，scorer 已评完分）。

    runner_fn 负责 wire OctoHarness + 跑 LLM + 调 scorer.score（按 tier 分发）。
    本结构是 worker.py 写 BenchmarkRunRecord 时的中间承载。
    """

    result: str
    score: float | None
    duration_seconds: float
    token_input: int | None = None
    token_output: int | None = None
    token_cache_read: int | None = None
    audit_assertions_json: str | None = None
    error_message: str | None = None


# TaskRunner 签名：runner_fn(task_meta, iteration) -> TaskExecutionOutcome（async）
TaskRunner = Callable[[TaskMeta, int], Awaitable[TaskExecutionOutcome]]


# ---------------------------------------------------------------------------
# Retry / error 分类 helpers
# ---------------------------------------------------------------------------


def is_rate_limit_error(exc: BaseException) -> bool:
    """检测 exception 是否属于 quota/429（与 F087 quota_skip 协议对齐）。

    仅匹配结构化协议（``error_type == "rate_limit"`` 或 ``status_code == 429``），
    不做 substring 匹配（避免误判 generic RuntimeError）。
    """
    if getattr(exc, "error_type", "") == RATE_LIMIT_ERROR_TYPE:
        return True
    if getattr(exc, "status_code", 0) == HTTP_RATE_LIMIT_STATUS:
        return True
    return False


def get_retry_after_seconds(exc: BaseException) -> float | None:
    """从 exception 抽 retry-after seconds（FR-A07：retry-after 优先）。

    支持的属性名：``retry_after`` / ``retry_after_seconds``。失败返回 None。
    """
    for attr in ("retry_after_seconds", "retry_after"):
        val = getattr(exc, attr, None)
        if val is None:
            continue
        try:
            v = float(val)
        except (TypeError, ValueError):
            continue
        if v >= 0:
            return v
    return None


def exponential_backoff_with_jitter(
    attempt: int,
    *,
    max_seconds: float = MAX_BACKOFF_SECONDS,
    rng: random.Random | None = None,
) -> float:
    """exp backoff with full jitter（FR-A07）。

    attempt 从 0 起：base = min(max, 2 ** attempt)；返回 uniform(0, base)。
    """
    rand = rng if rng is not None else random
    base = min(max_seconds, float(2 ** max(0, attempt)))
    return rand.uniform(0.0, base)


def resolve_task_timeout(task: TaskMeta) -> float:
    """根据 tier/domain 解析 timeout（FR-A03）。

    GAIA fallback 任务（domain 含 "gaia"）给 480s；其余给 300s。
    """
    if "gaia" in task.domain.lower():
        return GAIA_TASK_TIMEOUT_SECONDS
    return DEFAULT_TASK_TIMEOUT_SECONDS


# ---------------------------------------------------------------------------
# ConsecutiveInfraErrorCounter（FR-A：连续 5 INFRA_ERROR 主动停止）
# ---------------------------------------------------------------------------


class ConsecutiveInfraErrorCounter:
    """连续 INFRA_ERROR 计数器；达 threshold 触发 ``stop_event``。

    设计要点：
    - asyncio.Lock 保护 count + stop_event 状态（短临界区，不跨 await）
    - 任何非 INFRA_ERROR 结果都重置计数（"连续"语义）
    - reset() 同步方法，仅供单测重新初始化用

    NOT 是 module singleton：``run_daily_bench`` 每次调用时新建实例，多 event
    loop 间天然隔离。
    """

    def __init__(self, threshold: int = MAX_CONSECUTIVE_INFRA_ERRORS) -> None:
        self._threshold = max(1, int(threshold))
        self._lock = asyncio.Lock()
        self._count = 0
        self._stop_event = asyncio.Event()

    async def record_result(self, result: str) -> None:
        async with self._lock:
            if result == RESULT_INFRA_ERROR:
                self._count += 1
                if self._count >= self._threshold:
                    self._stop_event.set()
            else:
                self._count = 0

    def reset(self) -> None:
        """同步 reset（不获取 lock；仅供 caller 在 worker 外手动调用）。"""
        self._count = 0
        self._stop_event = asyncio.Event()

    @property
    def threshold(self) -> int:
        return self._threshold

    @property
    def consecutive_count(self) -> int:
        return self._count

    @property
    def stop_event(self) -> asyncio.Event:
        return self._stop_event

    @property
    def stopped(self) -> bool:
        return self._stop_event.is_set()


# ---------------------------------------------------------------------------
# 单 task 执行 + retry（FR-A03/A04/A07）
# ---------------------------------------------------------------------------


async def run_task_with_retry(
    task: TaskMeta,
    iteration: int,
    runner_fn: TaskRunner,
    *,
    timeout_seconds: float | None = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    rng: random.Random | None = None,
    sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> TaskExecutionOutcome:
    """单 task 执行 + retry-after 优先 + exp backoff jitter + 三态分类。

    返回 ``TaskExecutionOutcome``，不抛出（所有异常已被分类）：

    - rate_limit（429）：重试 max_retries 次，仍失败 → ``QUOTA_SKIP``
    - asyncio.TimeoutError：直接 ``TIMEOUT``（不重试，超时通常意味更深问题）
    - 其他 exception：``INFRA_ERROR`` + error_message 记录
    - runner_fn 返回正常 outcome：直接返回（PASS/FAIL/PARTIAL 来自 scorer）

    Args:
        task: TaskMeta（含 task_id/tier/domain）
        iteration: 第几次（1-indexed）
        runner_fn: ``async runner_fn(task, iteration) -> TaskExecutionOutcome``
        timeout_seconds: 单次 attempt 超时；None 时按 ``resolve_task_timeout(task)``
        max_retries: 最多重试次数（默认 3）
        rng: random 注入点（单测用）
        sleep_fn: 注入 sleep（单测用 fake clock）
    """
    timeout = timeout_seconds if timeout_seconds is not None else resolve_task_timeout(task)
    attempts = max(1, max_retries)
    last_error: BaseException | None = None

    for attempt in range(attempts):
        try:
            return await asyncio.wait_for(
                runner_fn(task, iteration), timeout=timeout
            )
        except asyncio.TimeoutError as exc:
            # FR-A03：timeout 不重试
            logger.warning(
                "bench_task_timeout",
                extra={"task_id": task.task_id, "iteration": iteration, "timeout": timeout},
            )
            return TaskExecutionOutcome(
                result=RESULT_TIMEOUT,
                score=None,
                duration_seconds=timeout,
                error_message=f"task timeout after {timeout}s",
            )
        except asyncio.CancelledError:
            # 上层取消（stop_event 触发）必须直接传递
            raise
        except (KeyboardInterrupt, SystemExit):
            # HIGH-1 fix：Ctrl+C / sys.exit 必须 propagate（不能被 retry 吞掉）
            raise
        except Exception as exc:
            last_error = exc
            if is_rate_limit_error(exc):
                # 取 retry-after，否则 exp backoff
                retry_after = get_retry_after_seconds(exc)
                if retry_after is not None:
                    delay = retry_after
                else:
                    delay = exponential_backoff_with_jitter(attempt, rng=rng)
                logger.info(
                    "bench_task_rate_limit_retry",
                    extra={
                        "task_id": task.task_id,
                        "iteration": iteration,
                        "attempt": attempt + 1,
                        "delay": delay,
                    },
                )
                if attempt < attempts - 1:
                    await sleep_fn(delay)
                    continue
                # 用完 retry 仍 429 → QUOTA_SKIP（AC3-4 不计入分母）
                return TaskExecutionOutcome(
                    result=RESULT_QUOTA_SKIP,
                    score=None,
                    duration_seconds=0.0,
                    error_message=f"rate_limit after {attempts} retries: {exc!r}",
                )
            # 非 rate_limit 异常 → INFRA_ERROR（不重试；业务错误 retry 会掩盖 bug）
            logger.error(
                "bench_task_infra_error",
                extra={
                    "task_id": task.task_id,
                    "iteration": iteration,
                    "error": repr(exc),
                },
            )
            return TaskExecutionOutcome(
                result=RESULT_INFRA_ERROR,
                score=None,
                duration_seconds=0.0,
                error_message=f"infra_error: {exc!r}",
            )

    # 理论上 unreachable（循环内必返回）；defensive 兜底
    return TaskExecutionOutcome(
        result=RESULT_INFRA_ERROR,
        score=None,
        duration_seconds=0.0,
        error_message=f"unreachable: last_error={last_error!r}",
    )


# ---------------------------------------------------------------------------
# Daily Bench 并发主循环（FR-A01/A02/A07）
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PlannedRun:
    """planned 列表元素：task_meta + iteration 序号。"""

    task: TaskMeta
    iteration: int


async def run_daily_bench(
    planned: Sequence[PlannedRun],
    runner_fn: TaskRunner,
    store: BenchmarkStore,
    session_id: str,
    *,
    semaphore_size: int = DEFAULT_SEMAPHORE_SIZE,
    ramp_delay_seconds: float = DEFAULT_RAMP_DELAY_SECONDS,
    max_retries: int = DEFAULT_MAX_RETRIES,
    timeout_resolver: Callable[[TaskMeta], float] = resolve_task_timeout,
    counter: ConsecutiveInfraErrorCounter | None = None,
    rng: random.Random | None = None,
    sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
    on_record_written: Callable[[BenchmarkRunRecord], None] | None = None,
) -> list[BenchmarkRunRecord]:
    """主并发循环（FR-A01/A02/A07）。

    - ``asyncio.Semaphore(semaphore_size)`` 限流；slot 错开 ``ramp_delay_seconds``
    - 每 task 完成后 ``store.append_run`` 持久化（resume idempotent）
    - 累计 ``threshold`` 个连续 INFRA_ERROR 时 ``counter.stop_event`` 触发，
      停止派发新 task；正在跑的 task 让其自然完成或被 caller 取消（不强制 kill）

    返回所有 finished BenchmarkRunRecord（含中途因 stop 跳过未跑的，不在返回里）。

    Args:
        planned: 计划要跑的 (task, iteration) 列表
        runner_fn: 单 task 执行回调（注入 OctoHarness wire）
        store: BenchmarkStore 实例（每次 append_run 立即落盘）
        session_id: 本次 Daily Bench 的分组 ID（resume 用）
        semaphore_size: 并发上限（默认 8）
        ramp_delay_seconds: gradual ramp 启动延迟（默认 0.5s）
        max_retries: 单 task rate_limit 重试上限
        timeout_resolver: 按 task 解析 timeout
        counter: 连续 INFRA_ERROR 计数器；None 时新建
        rng / sleep_fn: 单测注入点
        on_record_written: 每条 record 写入 store 后回调（reporter 实时进度用）
    """
    if not planned:
        return []

    sem = asyncio.Semaphore(max(1, semaphore_size))
    infra_counter = counter if counter is not None else ConsecutiveInfraErrorCounter()
    results: list[BenchmarkRunRecord] = []
    results_lock = asyncio.Lock()

    async def _run_one(item: PlannedRun, slot_idx: int) -> None:
        # Gradual ramp（FR-A02）：在拿到 semaphore 前先 sleep 错开
        if ramp_delay_seconds > 0 and slot_idx > 0:
            try:
                await sleep_fn(slot_idx * ramp_delay_seconds)
            except asyncio.CancelledError:
                raise

        # FR-A07：进入 semaphore 前检查 stop 信号
        if infra_counter.stopped:
            logger.warning(
                "bench_skipped_due_to_consecutive_infra_errors",
                extra={
                    "task_id": item.task.task_id,
                    "iteration": item.iteration,
                    "session_id": session_id,
                },
            )
            return

        async with sem:
            # 进入 semaphore 后再次检查（在等 sem 期间 stop 可能触发）
            if infra_counter.stopped:
                return

            timeout = timeout_resolver(item.task)
            t0 = asyncio.get_running_loop().time()  # HIGH-2 fix: get_event_loop deprecated
            outcome = await run_task_with_retry(
                item.task,
                item.iteration,
                runner_fn,
                timeout_seconds=timeout,
                max_retries=max_retries,
                rng=rng,
                sleep_fn=sleep_fn,
            )
            wall = asyncio.get_running_loop().time() - t0

            # 优先使用 runner_fn 报告的 duration（含 task 内部）；否则用 wall
            duration = (
                outcome.duration_seconds
                if outcome.duration_seconds > 0
                else max(0.0, wall)
            )

            record = BenchmarkRunRecord(
                run_id=make_run_id(),
                bench_session_id=session_id,
                task_id=item.task.task_id,
                tier=item.task.tier,
                domain=item.task.domain,
                iteration=item.iteration,
                result=outcome.result,
                score=outcome.score,
                duration_seconds=duration,
                token_input=outcome.token_input,
                token_output=outcome.token_output,
                token_cache_read=outcome.token_cache_read,
                audit_assertions_json=outcome.audit_assertions_json,
                error_message=outcome.error_message,
                created_at=utcnow_iso(),
            )

            # 写盘（store.append_run 内部 commit；同 session+task+iter REPLACE 幂等）
            # 用 asyncio 默认 executor 避免 sqlite3 同步阻塞 event loop
            loop = asyncio.get_running_loop()
            written = await loop.run_in_executor(None, store.append_run, record)

            # 累计 INFRA_ERROR 计数（在写盘后，保证 record 已落盘）
            await infra_counter.record_result(written.result)

            async with results_lock:
                results.append(written)

            if on_record_written is not None:
                try:
                    on_record_written(written)
                except Exception:  # pragma: no cover - 进度回调不能影响主流程
                    logger.exception("on_record_written_failed")

    tasks = [
        asyncio.create_task(_run_one(item, idx), name=f"bench:{item.task.task_id}#{item.iteration}")
        for idx, item in enumerate(planned)
    ]

    try:
        # HIGH-3 fix：用 return_exceptions=True 防单 task unhandled exc 拖垮整 batch。
        # 已 stop 的 _run_one 会 early-return；不会卡死。
        gather_results = await asyncio.gather(*tasks, return_exceptions=True)
        # 单测 task unhandled exc 也得 log 出来（不静默吞掉）
        for idx, gr in enumerate(gather_results):
            if isinstance(gr, BaseException) and not isinstance(gr, asyncio.CancelledError):
                logger.error(
                    "bench_task_unhandled_exception",
                    extra={"task_index": idx, "error": repr(gr)},
                )
    except asyncio.CancelledError:
        # 上层取消（Ctrl+C）：cancel 所有未完成 task，等它们结束
        for t in tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise

    return results


# ---------------------------------------------------------------------------
# Resume helper（AC5-1）
# ---------------------------------------------------------------------------


def planned_from_full_set(
    tasks: Iterable[TaskMeta],
    *,
    iterations: int = 3,
) -> list[PlannedRun]:
    """把 task 集合展开为完整 planned 列表（每 task × iterations）。"""
    out: list[PlannedRun] = []
    for task in tasks:
        for it in range(1, max(1, iterations) + 1):
            out.append(PlannedRun(task=task, iteration=it))
    return out


def filter_planned_for_resume(
    planned: Sequence[PlannedRun],
    store: BenchmarkStore,
    session_id: str,
) -> list[PlannedRun]:
    """``--resume`` 入口：从 planned 中剔除已 finished 的 (task, iteration)。

    AC5-1：仅续跑 store 中没有的；同 (task, iteration) 已完成则直接跳过。
    """
    completed = store.get_completed_keys(session_id)
    return [p for p in planned if (p.task.task_id, p.iteration) not in completed]


# ---------------------------------------------------------------------------
# Phase C 归档：H3-B follow_up_inputs 接入 helper（Round 4 P2-1）
# ---------------------------------------------------------------------------


def extract_follow_up_inputs(task_raw: dict) -> list[str]:
    """从 task YAML dict 抽出 ``follow_up_inputs`` 列表的 text 字段。

    用于 H3-B ask_back 多轮 task 在 WAITING_INPUT 状态时由 runner_fn 自动
    attach_input（无人值守 benchmark 不卡到超时）。

    YAML schema（t3_h3b_001.yaml 已定稿）::

        follow_up_inputs:
          - description: "ask_back 第一轮响应：提供预算 + 偏好"
            text: "我的预算 5000-8000 元/人，喜欢有历史文化和美食的目的地，时间 5-7 天。"

    runner_fn 使用模式（Phase E 实现 OctoHarness wire 时）::

        from benchmarks.runner.worker import extract_follow_up_inputs

        async def my_runner(task_meta, iteration):
            inputs = extract_follow_up_inputs(task_meta.raw)
            # 在每轮 WAITING_INPUT → attach_input(inputs[i])
            ...

    返回 ``[]`` 表示 task 不需 follow_up（普通 task / 单轮 ask_back）。
    """
    follow_ups = task_raw.get("follow_up_inputs") or []
    if not isinstance(follow_ups, list):
        return []
    out: list[str] = []
    for entry in follow_ups:
        if isinstance(entry, dict):
            text = entry.get("text")
            if isinstance(text, str) and text:
                out.append(text)
        elif isinstance(entry, str):
            out.append(entry)
    return out


__all__ = (
    "DEFAULT_SEMAPHORE_SIZE",
    "DEFAULT_RAMP_DELAY_SECONDS",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_TASK_TIMEOUT_SECONDS",
    "GAIA_TASK_TIMEOUT_SECONDS",
    "MAX_CONSECUTIVE_INFRA_ERRORS",
    "MAX_BACKOFF_SECONDS",
    "TaskMeta",
    "TaskRunner",
    "TaskExecutionOutcome",
    "PlannedRun",
    "ConsecutiveInfraErrorCounter",
    "is_rate_limit_error",
    "get_retry_after_seconds",
    "exponential_backoff_with_jitter",
    "resolve_task_timeout",
    "run_task_with_retry",
    "run_daily_bench",
    "planned_from_full_set",
    "filter_planned_for_resume",
    "extract_follow_up_inputs",
)
