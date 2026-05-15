"""F100 Phase G: mock-based perf 基准（MEDIUM-2 修订后）。

v0.2 修订前用 e2e_smoke 5x P50/P95 + 5% hard gate（统计基础不足，被 Codex MED-2 抓）。
v0.2 改为 mock-based 控制变量测量：

- 测量入口：直接调 is_recall_planner_skip / is_single_loop_main_active helper（无 LLM/DB/IO）
- 测量样本：1000+ 次 timeit 求平均（微秒级）
- 通过门：F100 实施后 helper 调用耗时不应明显恶化
  - simple-query 路径：runtime_context=None / unspecified → 一次 attribute 访问 + early return
  - main_inline + skip：早期 short-circuit
  - AUTO + delegate：多一次 switch 分支
- 容忍度：单次调用 <= 5μs（mock 环境）；不设硬 gate（perf 是相对 baseline 比较，需 baseline 数据）

本测试作为 perf 监控基准——记录 F100 实施后的耗时基线，未来若 helper 内部变复杂时
有数据对照（baseline 数据见 phase-g-perf-report.md）。

测试输出非测量数据，仅断言 helper 在 mock 环境下"足够快"（< 100μs 单次调用）。
更详细的 perf benchmark 可用 pytest-benchmark 扩展（F100 不引入额外依赖）。
"""

from __future__ import annotations

import statistics
import time

import pytest

from octoagent.core.models import RuntimeControlContext
from octoagent.gateway.services.runtime_control import (
    is_recall_planner_skip,
    is_single_loop_main_active,
)


# 单次调用容忍上限（mock 环境，无 IO 应远低于此值）
SINGLE_CALL_MAX_MICROSECONDS = 100.0

# 测量样本数
N_SAMPLES = 2000


def _measure_microseconds(func, *args, **kwargs) -> float:
    """测量 func 多次调用的平均耗时（微秒）。"""
    samples = []
    for _ in range(N_SAMPLES):
        start = time.perf_counter()
        func(*args, **kwargs)
        elapsed = time.perf_counter() - start
        samples.append(elapsed * 1_000_000)  # → microseconds
    return statistics.mean(samples)


class TestRuntimeControlHelperPerf:
    """mock-based helper perf 基准。

    F100 实施后 helper 内部分支：
    - force_full_recall early check（新增）
    - delegation_mode explicit check
    - recall_planner_mode switch（含 AUTO 决议分支）
    - fallback 移除后 unspecified → return False

    各路径分支次数：
    - simple-query (None runtime_context): 1 if-check
    - main_inline + skip: 3 if-checks（force_full_recall False / mode != unspecified / mode==skip）
    - AUTO + delegate: 4-5 if-checks
    """

    def test_is_recall_planner_skip_none_path_fast(self) -> None:
        """N runtime_context → early return False，应最快。"""
        avg_us = _measure_microseconds(is_recall_planner_skip, None, {})
        assert avg_us < SINGLE_CALL_MAX_MICROSECONDS, (
            f"is_recall_planner_skip(None, {{}}) 平均 {avg_us:.2f}μs，"
            f"超过 {SINGLE_CALL_MAX_MICROSECONDS}μs 容忍上限"
        )

    def test_is_recall_planner_skip_main_inline_skip_fast(self) -> None:
        """main_inline + skip → short-circuit return True"""
        ctx = RuntimeControlContext(
            task_id="t1", delegation_mode="main_inline", recall_planner_mode="skip"
        )
        avg_us = _measure_microseconds(is_recall_planner_skip, ctx, {})
        assert avg_us < SINGLE_CALL_MAX_MICROSECONDS

    def test_is_recall_planner_skip_auto_inline_fast(self) -> None:
        """AUTO + main_inline → 多一次 switch 分支，仍应在容忍内。"""
        ctx = RuntimeControlContext(
            task_id="t1", delegation_mode="main_inline", recall_planner_mode="auto"
        )
        avg_us = _measure_microseconds(is_recall_planner_skip, ctx, {})
        assert avg_us < SINGLE_CALL_MAX_MICROSECONDS

    def test_is_recall_planner_skip_force_full_recall_fast(self) -> None:
        """force_full_recall=True → 最早 short-circuit return False"""
        ctx = RuntimeControlContext(
            task_id="t1",
            delegation_mode="main_inline",
            recall_planner_mode="skip",
            force_full_recall=True,
        )
        avg_us = _measure_microseconds(is_recall_planner_skip, ctx, {})
        assert avg_us < SINGLE_CALL_MAX_MICROSECONDS

    def test_is_single_loop_main_active_main_inline_fast(self) -> None:
        ctx = RuntimeControlContext(task_id="t1", delegation_mode="main_inline")
        avg_us = _measure_microseconds(is_single_loop_main_active, ctx, {})
        assert avg_us < SINGLE_CALL_MAX_MICROSECONDS

    def test_is_single_loop_main_active_unspecified_fast(self) -> None:
        ctx = RuntimeControlContext(task_id="t1", delegation_mode="unspecified")
        avg_us = _measure_microseconds(is_single_loop_main_active, ctx, {})
        assert avg_us < SINGLE_CALL_MAX_MICROSECONDS


@pytest.mark.parametrize(
    "delegation_mode,recall_planner_mode,force_full_recall",
    [
        ("main_inline", "skip", False),  # F051 兼容路径
        ("worker_inline", "skip", False),  # Worker 路径
        ("main_inline", "auto", False),  # AUTO inline → skip
        ("main_delegate", "auto", False),  # AUTO delegate → full
        ("main_inline", "auto", True),  # H1 override
    ],
)
def test_perf_all_paths_under_tolerance(
    delegation_mode: str,
    recall_planner_mode: str,
    force_full_recall: bool,
) -> None:
    """所有 F100 决策路径单次调用都应在容忍内。"""
    ctx = RuntimeControlContext(
        task_id="t1",
        delegation_mode=delegation_mode,
        recall_planner_mode=recall_planner_mode,
        force_full_recall=force_full_recall,
    )
    avg_us = _measure_microseconds(is_recall_planner_skip, ctx, {})
    assert avg_us < SINGLE_CALL_MAX_MICROSECONDS, (
        f"path delegation={delegation_mode}, recall={recall_planner_mode}, "
        f"override={force_full_recall} 平均 {avg_us:.2f}μs 超容忍 {SINGLE_CALL_MAX_MICROSECONDS}μs"
    )
