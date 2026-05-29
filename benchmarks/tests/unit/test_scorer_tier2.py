"""benchmarks/tests/unit/test_scorer_tier2.py — scorer Tier 2 扩展测试（T-B-4）.

覆盖:
- score_tier2_tau Pass@1 评分
- score_tier2_tau 处理 tau_bench__ 前缀
- score_tier2_tau ERROR 路径（actions 为空）
- score_tier2_gaia normalized 字符串匹配 + tolerance + alternates
"""
from __future__ import annotations

import pytest

from benchmarks.runner.scorer import (
    BenchmarkRunScore,
    TaskVerdict,
    score_tier2_gaia,
    score_tier2_tau,
)
from benchmarks.tiers.tier2.gaia_fallback_adapter import GaiaFallbackTaskMeta
from benchmarks.tiers.tier2.tau_bench_adapter import TauBenchTaskMeta


# ============================================================
# Tier 2 τ-bench Pass@1
# ============================================================


class TestScoreTier2Tau:
    def _make_task(self, action_names: list[str]) -> TauBenchTaskMeta:
        return TauBenchTaskMeta(
            task_idx=0,
            task_id="T2-TAU-TEST",
            user_id="u",
            instruction="test",
            actions=[{"name": n, "arguments": {}} for n in action_names],
            bucket="booking",
        )

    def test_pass_when_all_expected_actions_called(self) -> None:
        """PoC: 实际 tool calls 覆盖所有 expected.actions → PASS."""
        task = self._make_task(["book_reservation", "calculate"])
        actual_tool_calls = [
            {"name": "tau_bench__book_reservation", "arguments": {}},
            {"name": "tau_bench__calculate", "arguments": {}},
        ]
        result = score_tier2_tau(task, actual_tool_calls)
        assert result.verdict == TaskVerdict.PASS
        assert result.pass_fail_score == 1.0
        assert result.weighted_score == 1.0
        assert result.task_id == "T2-TAU-TEST"

    def test_pass_at_1_known_limitation_no_order_no_args(self) -> None:
        """Codex Phase B review MED-3 known limitation (Phase D 升级):
        Pass@1 当前只比 name + count，忽略顺序和 arguments。
        同名同次数但参数错或顺序错仍会 PASS（false PASS 风险）.
        Phase D T-D-6 升级为 order/arguments-aware 或用 tau-bench env reward."""
        task = self._make_task(["book_reservation"])
        # arguments 完全错误但 name 正确 → 当前仍 PASS（spec FR-B01 简化版）
        actual_tool_calls = [
            {"name": "tau_bench__book_reservation", "arguments": {"wrong": "args"}}
        ]
        result = score_tier2_tau(task, actual_tool_calls)
        assert result.verdict == TaskVerdict.PASS  # known limitation，Phase D 修

    def test_fail_when_expected_action_missing(self) -> None:
        """缺一个 expected action → FAIL."""
        task = self._make_task(["book_reservation", "send_certificate"])
        actual_tool_calls = [{"name": "tau_bench__book_reservation", "arguments": {}}]
        result = score_tier2_tau(task, actual_tool_calls)
        assert result.verdict == TaskVerdict.FAIL
        assert result.pass_fail_score == 0.0

    def test_extra_tool_calls_still_pass(self) -> None:
        """实际多调一些 tool（agent 探索性调用）但覆盖期望集 → PASS."""
        task = self._make_task(["book_reservation"])
        actual_tool_calls = [
            {"name": "tau_bench__book_reservation", "arguments": {}},
            {"name": "tau_bench__get_user_details", "arguments": {}},  # 探索性
            {"name": "tau_bench__calculate", "arguments": {}},          # 探索性
        ]
        result = score_tier2_tau(task, actual_tool_calls)
        assert result.verdict == TaskVerdict.PASS

    def test_empty_expected_actions_is_error(self) -> None:
        """task.actions 为空 → ERROR（不 silent PASS，避免无法判分 false positive）."""
        task = self._make_task([])
        result = score_tier2_tau(task, [{"name": "tau_bench__book_reservation"}])
        assert result.verdict == TaskVerdict.ERROR
        assert "actions 为空" in result.error_message

    def test_token_usage_passed_through(self) -> None:
        task = self._make_task(["book_reservation"])
        actual_tool_calls = [{"name": "tau_bench__book_reservation"}]
        result = score_tier2_tau(task, actual_tool_calls, token_usage=12345)
        assert result.token_usage == 12345

    def test_codex_phase_b_med_5_non_prefixed_calls_ignored(self) -> None:
        """Codex Phase B review MED-5 修复回归: 非 tau_bench__ 前缀的 tool call 必须严格忽略.

        修复前: 无前缀的 name 直接进 actual_counter，混入 production 同名调用会 false PASS.
        修复后: 只接受 startswith "tau_bench__" 的调用.
        """
        task = self._make_task(["book_reservation"])
        # 全部 production tool calls（无前缀）→ actual_counter 应为空 → FAIL
        actual_tool_calls = [
            {"name": "book_reservation"},  # 无前缀，忽略
            {"name": "production_book"},    # 无前缀，忽略
        ]
        result = score_tier2_tau(task, actual_tool_calls)
        assert result.verdict == TaskVerdict.FAIL
        assert "book_reservation" in (result.error_message or "")

    def test_codex_phase_b_med_5_scope_id_prefix_supported(self) -> None:
        """支持 tau_bench__<scope_id>__<tool_name> 形式（per-run unique prefix from HIGH-2 fix）."""
        task = self._make_task(["book_reservation"])
        # 含 scope_id 的前缀（来自 tau_bench_tool_scope HIGH-2 修复）
        actual_tool_calls = [{"name": "tau_bench__run42__book_reservation"}]
        result = score_tier2_tau(task, actual_tool_calls)
        assert result.verdict == TaskVerdict.PASS

    def test_codex_phase_b_med_action_count_matters(self) -> None:
        """Codex Phase B review MED 修复回归：同名 action 重复次数必须匹配.

        修复前: 用 set 折叠 expected actions，导致需要 N 次的 action 只调一次也 PASS（高估 Pass@1）.
        修复后: 用 Counter 计数，actual_count < expected_count 则 FAIL.
        """
        # 期望: 5 次 update_reservation_flights (例: T2-TAU-UPGRADE-002 实际场景)
        task = self._make_task([
            "update_reservation_flights",
            "update_reservation_flights",
            "update_reservation_flights",
            "update_reservation_flights",
            "update_reservation_flights",
        ])

        # agent 只调一次 → 修复后必须 FAIL（修复前会 set 折叠 PASS）
        actual_one_call = [{"name": "tau_bench__update_reservation_flights"}]
        result_one = score_tier2_tau(task, actual_one_call)
        assert result_one.verdict == TaskVerdict.FAIL
        assert "1/5" in (result_one.error_message or "")

        # agent 调 5 次全部带前缀（MED-5 修复后：非前缀的 tool call 被严格忽略）→ PASS
        actual_five_calls = [
            {"name": "tau_bench__update_reservation_flights"},
            {"name": "tau_bench__update_reservation_flights"},
            {"name": "tau_bench__update_reservation_flights"},
            {"name": "tau_bench__update_reservation_flights"},
            {"name": "tau_bench__update_reservation_flights"},
        ]
        result_five = score_tier2_tau(task, actual_five_calls)
        assert result_five.verdict == TaskVerdict.PASS

    def test_codex_phase_b_med_multi_action_partial_count(self) -> None:
        """混合 action 计数场景：3 个 book_reservation + 1 个 calculate."""
        task = self._make_task([
            "book_reservation",
            "book_reservation",
            "book_reservation",
            "calculate",
        ])
        # actual: 2 book + 1 calculate（book 缺 1 个）→ FAIL
        actual = [
            {"name": "tau_bench__book_reservation"},
            {"name": "tau_bench__book_reservation"},
            {"name": "tau_bench__calculate"},
        ]
        result = score_tier2_tau(task, actual)
        assert result.verdict == TaskVerdict.FAIL
        assert "book_reservation" in (result.error_message or "")


# ============================================================
# Tier 2 GAIA normalized 字符串匹配
# ============================================================


class TestScoreTier2Gaia:
    def _make_task(self, **kw) -> GaiaFallbackTaskMeta:
        defaults: dict = dict(
            task_id="T2-GAIA-TEST",
            domain="test",
            category="web_search",
            source_provenance="[GAIA-FALLBACK] test",
            prompt="test prompt",
            expected_answer="299792458",
        )
        defaults.update(kw)
        return GaiaFallbackTaskMeta(**defaults)

    def test_pass_exact_match(self) -> None:
        task = self._make_task()
        result = score_tier2_gaia(task, "299792458")
        assert result.verdict == TaskVerdict.PASS
        assert result.pass_fail_score == 1.0

    def test_pass_with_thousand_separator(self) -> None:
        """千分位分隔符处理: 数字 tolerance 路径 + 严格 exact 后仍要求 minimal answer.

        Codex Phase B review MED-4 修复: 不再接受 LLM 带 prefix 答案。
        GAIA fallback yaml prompt 已要求'仅返回精确数字'，所以测试 minimal answer.
        """
        task = self._make_task()
        # 数字 + 千分位 → 数字 tolerance 路径命中
        assert score_tier2_gaia(task, "299,792,458").verdict == TaskVerdict.PASS
        # 严格 exact 后 LLM 带 prefix 不再 PASS（spec FR-E03）
        assert score_tier2_gaia(task, "the answer is 299792458").verdict == TaskVerdict.FAIL

    def test_fail_wrong_answer(self) -> None:
        task = self._make_task()
        result = score_tier2_gaia(task, "12345")
        assert result.verdict == TaskVerdict.FAIL
        assert result.pass_fail_score == 0.0

    def test_alternates_match(self) -> None:
        task = self._make_task(
            expected_answer="Sutton",
            expected_answer_alternates=["Barto"],
        )
        result = score_tier2_gaia(task, "Barto")
        assert result.verdict == TaskVerdict.PASS

    def test_tolerance_numeric(self) -> None:
        task = self._make_task(
            expected_answer="149597",
            expected_answer_tolerance=100,
        )
        # ±100 内 PASS
        assert score_tier2_gaia(task, "149600").verdict == TaskVerdict.PASS
        # 超 tolerance FAIL
        assert score_tier2_gaia(task, "150000").verdict == TaskVerdict.FAIL

    def test_token_usage_passed_through(self) -> None:
        task = self._make_task()
        result = score_tier2_gaia(task, "299792458", token_usage=2345)
        assert result.token_usage == 2345
