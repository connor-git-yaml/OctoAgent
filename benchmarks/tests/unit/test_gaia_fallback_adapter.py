"""benchmarks/tests/unit/test_gaia_fallback_adapter.py — GAIA fallback adapter 测试.

覆盖:
- 5 task yaml 加载（FR-E04 分层验证）
- normalize_answer（FR-E03 normalized 字符串匹配）
- match_answer 主答案 / alternates / tolerance
- adapter evaluate 主路径
"""
from __future__ import annotations

import pytest

from benchmarks.tiers.tier2.gaia_fallback_adapter import (
    EXPECTED_CATEGORY_DISTRIBUTION,
    GaiaFallbackAdapter,
    GaiaFallbackTaskMeta,
    load_fallback_tasks,
    match_answer,
    normalize_answer,
)


# ============================================================
# YAML 加载（FR-E04 分层验证）
# ============================================================


class TestLoadFallbackTasks:
    def test_loads_exactly_5_tasks(self) -> None:
        """fallback yaml 含 5 task（FR-E04）."""
        tasks = load_fallback_tasks()
        assert len(tasks) == 5

    def test_categories_match_fr_e04(self) -> None:
        """5 task 分层 == EXPECTED_CATEGORY_DISTRIBUTION（FR-E04 严格）."""
        tasks = load_fallback_tasks()
        dist: dict[str, int] = {}
        for t in tasks:
            dist[t.category] = dist.get(t.category, 0) + 1
        assert dist == EXPECTED_CATEGORY_DISTRIBUTION
        assert dist == {"web_search": 2, "doc_parse": 2, "multi_tool_chain": 1}

    def test_all_tasks_have_gaia_fallback_provenance(self) -> None:
        """所有 task source_provenance 含 [GAIA-FALLBACK] 标签（非官方明示）."""
        tasks = load_fallback_tasks()
        for t in tasks:
            assert "[GAIA-FALLBACK]" in t.source_provenance

    def test_all_tasks_use_tier2_gaia_v1_rubric(self) -> None:
        """rubric_id 全部 tier2-gaia-v1."""
        tasks = load_fallback_tasks()
        for t in tasks:
            assert t.rubric_id == "tier2-gaia-v1"

    def test_task_id_unique(self) -> None:
        """5 task task_id 唯一."""
        tasks = load_fallback_tasks()
        ids = [t.task_id for t in tasks]
        assert len(set(ids)) == 5


# ============================================================
# normalize_answer（FR-E03）
# ============================================================


class TestNormalizeAnswer:
    def test_lower_case(self) -> None:
        assert normalize_answer("Sutton") == "sutton"

    def test_strip_whitespace(self) -> None:
        assert normalize_answer("  Sutton  ") == "sutton"

    def test_thousand_separator_removed(self) -> None:
        """千分位逗号去除（1,000 → 1000）."""
        assert normalize_answer("299,792,458") == "299792458"

    def test_punctuation_removed(self) -> None:
        """常见标点（句号除外，与数字小数点同字符）."""
        assert normalize_answer("Hello, World!") == "hello world"

    def test_decimal_point_preserved(self) -> None:
        """小数点保留."""
        assert normalize_answer("3.14159") == "3.14159"


# ============================================================
# match_answer
# ============================================================


class TestMatchAnswer:
    def _make_task(self, **kw) -> GaiaFallbackTaskMeta:
        defaults: dict = dict(
            task_id="T2-GAIA-FB-001",
            domain="test",
            category="web_search",
            source_provenance="[GAIA-FALLBACK] test",
            prompt="test prompt",
            expected_answer="299792458",
        )
        defaults.update(kw)
        return GaiaFallbackTaskMeta(**defaults)

    def test_exact_match_passes(self) -> None:
        task = self._make_task()
        assert match_answer("299792458", task) is True

    def test_thousand_separator_match(self) -> None:
        """实际回答带千分位 → 与 expected 数字相等 → PASS."""
        task = self._make_task()
        assert match_answer("299,792,458", task) is True

    def test_case_insensitive_string(self) -> None:
        task = self._make_task(expected_answer="Sutton")
        assert match_answer("sutton", task) is True
        assert match_answer("SUTTON", task) is True

    def test_alternate_answer_match(self) -> None:
        """主答案不匹配但 alternate 匹配 → PASS（2024 图灵奖 Sutton/Barto 共同）."""
        task = self._make_task(
            expected_answer="Sutton",
            expected_answer_alternates=["Barto"],
        )
        assert match_answer("Barto", task) is True
        assert match_answer("barto", task) is True

    def test_tolerance_numeric(self) -> None:
        """tolerance=100 时 ±100 内 PASS（multi_tool_chain task 用）."""
        task = self._make_task(
            expected_answer="149597",
            expected_answer_tolerance=100,
        )
        assert match_answer("149597", task) is True
        assert match_answer("149600", task) is True   # ±100 内
        assert match_answer("149500", task) is True   # 边界 (差 97 < 100)
        assert match_answer("149696", task) is True   # 差 99 < 100
        assert match_answer("150000", task) is False  # 超 tolerance

    def test_no_match_returns_false(self) -> None:
        task = self._make_task()
        assert match_answer("totally wrong", task) is False

    def test_codex_phase_b_med_strict_exact_match(self) -> None:
        """Codex Phase B review MED-4 修复回归: 严格 normalized exact match.

        旧实现 (substring / token-sequence) 让 "the answer is auto" / "not exactly auto"
        命中 expected="auto"，偏离 spec FR-E03 "字符串精确匹配 / normalized 比较".
        修复后: 只接受 actual_norm == expected_norm.
        Phase D LLM-judge fallback 处理 LLM 带 prefix 的回答场景.
        """
        task = self._make_task(expected_answer="auto")

        # 严格相等 PASS
        assert match_answer("auto", task) is True
        assert match_answer("AUTO", task) is True  # case-insensitive normalize
        assert match_answer("  auto  ", task) is True  # strip

        # LLM 带 prefix / suffix 不再 PASS（spec exact match）
        assert match_answer("the answer is auto", task) is False
        assert match_answer("asyncio_mode value is auto", task) is False
        assert match_answer("auto is the answer", task) is False

        # 否定 / 包含但语义错的回答必须 FAIL
        assert match_answer("not auto", task) is False
        assert match_answer("not exactly auto", task) is False
        assert match_answer("automobile", task) is False  # substring 不匹配

    def test_codex_phase_b_med_strict_match_with_alternates(self) -> None:
        """alternates 也走严格 exact match."""
        task = self._make_task(
            expected_answer="Sutton",
            expected_answer_alternates=["Barto"],
        )
        assert match_answer("Sutton", task) is True
        assert match_answer("Barto", task) is True
        assert match_answer("sutton", task) is True
        # 带 prefix 不再 PASS
        assert match_answer("the winner is Sutton", task) is False


# ============================================================
# Adapter 入口
# ============================================================


class TestGaiaFallbackAdapter:
    def test_load_returns_5_tasks(self) -> None:
        adapter = GaiaFallbackAdapter()
        tasks = adapter.load()
        assert len(tasks) == 5

    def test_evaluate_pass(self) -> None:
        adapter = GaiaFallbackAdapter()
        tasks = adapter.load()
        # T2-GAIA-FB-001 expected_answer="299792458"
        task_1 = next(t for t in tasks if t.task_id == "T2-GAIA-FB-001")
        assert adapter.evaluate(task_1, "299792458") is True
        assert adapter.evaluate(task_1, "299,792,458") is True

    def test_evaluate_fail(self) -> None:
        adapter = GaiaFallbackAdapter()
        tasks = adapter.load()
        task_1 = next(t for t in tasks if t.task_id == "T2-GAIA-FB-001")
        assert adapter.evaluate(task_1, "12345") is False
