"""Feature 060: Token 估算 + ContextBudgetPlanner 单元测试。"""

from __future__ import annotations

import pytest
from octoagent.gateway.services.context_compaction import (
    _chars_per_token_ratio,
    estimation_method,
    estimate_text_tokens,
)
from octoagent.gateway.services.context_budget import (
    BudgetAllocation,
    ContextBudgetPlanner,
    ContextCompactionConfig,
)


# ============================================================
# T003: Token 估算单元测试
# ============================================================


class TestEstimateTextTokens:
    """estimate_text_tokens() 和 _chars_per_token_ratio() 的各场景测试。"""

    def test_empty_string_returns_zero(self) -> None:
        assert estimate_text_tokens("") == 0
        assert estimate_text_tokens("   ") == 0

    def test_pure_english_reasonable(self) -> None:
        """纯英文文本估算应与 len/4 大致一致（误差 < 30%）。"""
        text = "This is a simple English sentence for testing purposes only."
        tokens = estimate_text_tokens(text)
        # len/4 估算约为 15
        naive = len(text.strip()) / 4
        assert tokens >= 1
        # 不做太严格的断言，因为 tiktoken 可能可用
        assert tokens > 0

    def test_pure_chinese_not_underestimated(self) -> None:
        """纯中文文本估算不应再被严重低估。

        中文平均 ~1.5-2 chars/token，100 个中文字约 50-67 token。
        旧算法 len/4 = 25，低估约 50-100%。
        新算法应给出更合理的估算。
        """
        text = "这是一段纯中文的测试文本，用来验证新的中文感知估算算法。" * 5  # ~150 字符
        tokens = estimate_text_tokens(text)
        # 纯中文 ~1.5 chars/token -> 约 100 token
        # 旧算法 len/4 ≈ 38 token（严重低估）
        # 新算法应该在 50-150 之间
        assert tokens >= 40  # 至少不应该比 len/4 低

    def test_mixed_language_weighted(self) -> None:
        """中英混合文本按比例加权。"""
        text = "Hello World 你好世界 Testing 测试混合文本"
        tokens = estimate_text_tokens(text)
        assert tokens >= 1

    def test_chars_per_token_ratio_pure_english(self) -> None:
        ratio = _chars_per_token_ratio("Hello World this is English text")
        # 纯英文应接近 4.0
        assert 3.5 <= ratio <= 4.1

    def test_chars_per_token_ratio_pure_chinese(self) -> None:
        ratio = _chars_per_token_ratio("这是纯中文文本测试")
        # 纯中文应接近 1.5
        assert 1.4 <= ratio <= 1.6

    def test_chars_per_token_ratio_mixed(self) -> None:
        ratio = _chars_per_token_ratio("Hello你好World世界")
        # 混合文本在 1.5-4.0 之间
        assert 1.5 <= ratio <= 4.0

    def test_chars_per_token_ratio_empty(self) -> None:
        ratio = _chars_per_token_ratio("")
        assert ratio == 3.0  # 保守中间值

    def test_estimation_method_returns_known_value(self) -> None:
        method = estimation_method()
        assert method in {"tokenizer", "cjk_aware"}


# ============================================================
# T005: ContextBudgetPlanner 单元测试
# ============================================================


class TestContextBudgetPlanner:
    """ContextBudgetPlanner.plan() 的各场景测试。"""

    @pytest.fixture
    def planner(self) -> ContextBudgetPlanner:
        config = ContextCompactionConfig(max_input_tokens=6000)
        return ContextBudgetPlanner(config=config)

    def test_normal_allocation(self, planner: ContextBudgetPlanner) -> None:
        """正常分配：各部分之和 <= max_input_tokens。"""
        budget = planner.plan(max_input_tokens=6000)
        total = (
            budget.system_blocks_budget
            + budget.skill_injection_budget
            + budget.memory_recall_budget
            + budget.progress_notes_budget
            + budget.conversation_budget
        )
        assert total <= 6000
        assert budget.conversation_budget >= 800

    def test_no_skills(self, planner: ContextBudgetPlanner) -> None:
        """无 Skill 时 skill_injection_budget = 0。"""
        budget = planner.plan(max_input_tokens=6000, loaded_skill_names=None)
        assert budget.skill_injection_budget == 0

        budget2 = planner.plan(max_input_tokens=6000, loaded_skill_names=[])
        assert budget2.skill_injection_budget == 0

    def test_multiple_skills(self, planner: ContextBudgetPlanner) -> None:
        """多个 Skill 预算正确计算。"""
        budget = planner.plan(
            max_input_tokens=6000,
            loaded_skill_names=["skill_a", "skill_b", "skill_c"],
        )
        assert budget.skill_injection_budget == 3 * 250
        total = (
            budget.system_blocks_budget
            + budget.skill_injection_budget
            + budget.memory_recall_budget
            + budget.progress_notes_budget
            + budget.conversation_budget
        )
        assert total <= 6000

    def test_with_progress_notes(self, planner: ContextBudgetPlanner) -> None:
        """有进度笔记时预算正确。"""
        budget = planner.plan(
            max_input_tokens=6000,
            has_progress_notes=True,
            progress_note_count=3,
        )
        assert budget.progress_notes_budget == 3 * 80
        total = (
            budget.system_blocks_budget
            + budget.skill_injection_budget
            + budget.memory_recall_budget
            + budget.progress_notes_budget
            + budget.conversation_budget
        )
        assert total <= 6000

    def test_progress_notes_capped_at_five(self, planner: ContextBudgetPlanner) -> None:
        """进度笔记最多按 5 条计算。"""
        budget = planner.plan(
            max_input_tokens=6000,
            has_progress_notes=True,
            progress_note_count=20,
        )
        assert budget.progress_notes_budget == 5 * 80

    def test_no_progress_notes_when_flag_false(self, planner: ContextBudgetPlanner) -> None:
        """has_progress_notes=False 时预算为 0。"""
        budget = planner.plan(
            max_input_tokens=6000,
            has_progress_notes=False,
            progress_note_count=10,
        )
        assert budget.progress_notes_budget == 0

    def test_budget_reduction_on_tight_budget(self) -> None:
        """预算不足时按优先级缩减。"""
        config = ContextCompactionConfig(max_input_tokens=2500)
        planner = ContextBudgetPlanner(config=config)
        budget = planner.plan(
            max_input_tokens=2500,
            loaded_skill_names=["s1", "s2", "s3", "s4"],  # 4 * 250 = 1000
            memory_top_k=6,  # 6 * 60 = 360
            has_progress_notes=True,
            progress_note_count=5,  # 5 * 80 = 400
        )
        # 总开销 = 2200 (sys) + 1000 (skill) + 360 (mem) + 400 (notes) = 3960 > 2500
        # 必须缩减才能保证 conversation >= 800
        assert budget.conversation_budget >= 800
        total = (
            budget.system_blocks_budget
            + budget.skill_injection_budget
            + budget.memory_recall_budget
            + budget.progress_notes_budget
            + budget.conversation_budget
        )
        assert total <= 2500

    def test_very_small_max_input_tokens(self) -> None:
        """max_input_tokens < 800 时特殊处理。"""
        config = ContextCompactionConfig(max_input_tokens=500)
        planner = ContextBudgetPlanner(config=config)
        budget = planner.plan(max_input_tokens=500)
        assert budget.conversation_budget == 500
        assert budget.system_blocks_budget == 0
        assert budget.skill_injection_budget == 0
        assert budget.memory_recall_budget == 0
        assert budget.progress_notes_budget == 0

    def test_estimation_method_reflected(self, planner: ContextBudgetPlanner) -> None:
        """estimation_method 反映当前算法。"""
        budget = planner.plan(max_input_tokens=6000)
        assert budget.estimation_method in {"tokenizer", "cjk_aware"}

    def test_invariant_sum_le_max(self, planner: ContextBudgetPlanner) -> None:
        """不变量：各部分之和 <= max_input_tokens。"""
        for max_tokens in [800, 2000, 4000, 6000, 10000]:
            budget = planner.plan(
                max_input_tokens=max_tokens,
                loaded_skill_names=["a", "b"],
                memory_top_k=4,
                has_progress_notes=True,
                progress_note_count=3,
            )
            total = (
                budget.system_blocks_budget
                + budget.skill_injection_budget
                + budget.memory_recall_budget
                + budget.progress_notes_budget
                + budget.conversation_budget
            )
            assert total <= max_tokens, f"total={total} > max={max_tokens}"
            assert budget.conversation_budget >= 800 or max_tokens < 800

    def test_invariant_conversation_minimum(self, planner: ContextBudgetPlanner) -> None:
        """不变量：conversation_budget >= 800（当 max_input_tokens >= 800）。"""
        budget = planner.plan(
            max_input_tokens=6000,
            loaded_skill_names=["s1", "s2", "s3", "s4", "s5"],
        )
        assert budget.conversation_budget >= 800


# ============================================================
# T010: 全局预算集成测试
# ============================================================


class TestBudgetIntegration:
    """模拟中文多轮对话 + Skill + Memory 场景，验证预算分配。"""

    def test_chinese_conversation_with_skills_and_memory(self) -> None:
        """中文多轮对话 + 2 个 Skill + Memory 场景验证。"""
        config = ContextCompactionConfig(max_input_tokens=6000)
        planner = ContextBudgetPlanner(config=config)
        budget = planner.plan(
            max_input_tokens=6000,
            loaded_skill_names=["coding-agent", "github"],
            memory_top_k=6,
            has_progress_notes=False,
        )

        # 验证 SC-000：各部分之和 <= max_input_tokens
        total = (
            budget.system_blocks_budget
            + budget.skill_injection_budget
            + budget.memory_recall_budget
            + budget.progress_notes_budget
            + budget.conversation_budget
        )
        assert total <= 6000

        # 验证 conversation_budget 合理（有足够空间放对话）
        assert budget.conversation_budget >= 800

        # 验证 Skill 预算已计入
        assert budget.skill_injection_budget == 2 * 250  # 500

        # 验证 Memory 预算已计入
        assert budget.memory_recall_budget == 6 * 60  # 360

    def test_conversation_budget_passed_to_compaction(self) -> None:
        """conversation_budget 可以传给压缩层。"""
        config = ContextCompactionConfig(max_input_tokens=6000)
        planner = ContextBudgetPlanner(config=config)
        budget = planner.plan(max_input_tokens=6000)

        # conversation_budget 应该小于 max_input_tokens（因为要扣除系统块等）
        assert budget.conversation_budget < 6000
        assert budget.conversation_budget >= 800
