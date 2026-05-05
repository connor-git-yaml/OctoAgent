"""F091 Phase C: runtime_control helpers 单测。

覆盖：
- is_single_loop_main_active：runtime_context 优先 / metadata fallback / unspecified 兜底
- is_recall_planner_skip：recall_planner_mode 优先 / fallback 经 single_loop 路径
- 各 delegation_mode 路径的真值表
"""

import pytest

from octoagent.core.models import RuntimeControlContext
from octoagent.gateway.services.runtime_control import (
    is_recall_planner_skip,
    is_single_loop_main_active,
    metadata_flag,
)


def _make_context(
    *,
    delegation_mode: str = "unspecified",
    recall_planner_mode: str = "full",
) -> RuntimeControlContext:
    return RuntimeControlContext(
        task_id="task-f091-test",
        delegation_mode=delegation_mode,
        recall_planner_mode=recall_planner_mode,
    )


# ============================================================
# is_single_loop_main_active 真值表
# ============================================================


class TestIsSingleLoopMainActive:
    """读取优先级：runtime_context.delegation_mode (非 unspecified) → metadata fallback。"""

    @pytest.mark.parametrize(
        "delegation_mode,expected",
        [
            ("main_inline", True),
            ("worker_inline", True),
            ("main_delegate", False),
            ("subagent", False),
        ],
    )
    def test_runtime_context_explicit_modes(
        self, delegation_mode: str, expected: bool
    ) -> None:
        """显式 delegation_mode 优先于 metadata flag。"""
        ctx = _make_context(delegation_mode=delegation_mode)
        # 即使 metadata flag 矛盾，runtime_context 优先
        assert is_single_loop_main_active(ctx, {"single_loop_executor": not expected}) is expected

    def test_unspecified_falls_back_to_metadata_flag_true(self) -> None:
        """delegation_mode = unspecified 时 fallback metadata flag。"""
        ctx = _make_context(delegation_mode="unspecified")
        assert is_single_loop_main_active(ctx, {"single_loop_executor": True}) is True

    def test_unspecified_falls_back_to_metadata_flag_false(self) -> None:
        ctx = _make_context(delegation_mode="unspecified")
        assert is_single_loop_main_active(ctx, {"single_loop_executor": False}) is False

    def test_unspecified_no_metadata_returns_false(self) -> None:
        ctx = _make_context(delegation_mode="unspecified")
        assert is_single_loop_main_active(ctx, {}) is False

    def test_runtime_context_none_falls_back_to_metadata(self) -> None:
        """runtime_context = None 时直接走 metadata fallback。"""
        assert is_single_loop_main_active(None, {"single_loop_executor": True}) is True
        assert is_single_loop_main_active(None, {"single_loop_executor": False}) is False
        assert is_single_loop_main_active(None, {}) is False

    def test_runtime_context_none_metadata_none(self) -> None:
        """两者都 None 默认 False。"""
        assert is_single_loop_main_active(None, None) is False


# ============================================================
# is_recall_planner_skip 真值表
# ============================================================


class TestIsRecallPlannerSkip:
    """读取优先级：runtime_context.recall_planner_mode → fallback is_single_loop_main_active。"""

    def test_skip_mode_with_explicit_delegation_returns_true(self) -> None:
        """recall_planner_mode = skip + delegation_mode 显式 → True。"""
        ctx = _make_context(delegation_mode="main_inline", recall_planner_mode="skip")
        assert is_recall_planner_skip(ctx, {}) is True

    def test_skip_mode_overrides_metadata(self) -> None:
        """recall_planner_mode = skip + delegation_mode 显式 优先于 metadata flag false。"""
        ctx = _make_context(delegation_mode="main_inline", recall_planner_mode="skip")
        assert is_recall_planner_skip(ctx, {"single_loop_executor": False}) is True

    def test_full_mode_with_explicit_delegation_returns_false(self) -> None:
        """recall_planner_mode = full + delegation_mode 显式 → False。"""
        ctx = _make_context(delegation_mode="main_delegate", recall_planner_mode="full")
        assert is_recall_planner_skip(ctx, {}) is False

    def test_full_mode_overrides_metadata_when_delegation_explicit(self) -> None:
        """recall_planner_mode = full + delegation_mode 显式 优先于 metadata flag true。"""
        ctx = _make_context(delegation_mode="main_delegate", recall_planner_mode="full")
        assert is_recall_planner_skip(ctx, {"single_loop_executor": True}) is False

    def test_default_context_falls_back_to_metadata(self) -> None:
        """Final Codex M2 闭环：delegation_mode = unspecified（默认）时 recall_planner_mode 不权威，
        fallback metadata flag——保持与旧逻辑等价。"""
        ctx = _make_context()  # delegation_mode = unspecified, recall_planner_mode = full（默认）
        # 默认 ctx + metadata flag True → 应 skip（旧逻辑）
        assert is_recall_planner_skip(ctx, {"single_loop_executor": True}) is True
        # 默认 ctx + metadata flag False → 应不 skip
        assert is_recall_planner_skip(ctx, {"single_loop_executor": False}) is False
        # 默认 ctx + 无 metadata → 默认 False
        assert is_recall_planner_skip(ctx, {}) is False

    def test_auto_mode_raises_not_implemented_when_delegation_explicit(self) -> None:
        """recall_planner_mode = auto + delegation_mode 显式 → raise NotImplementedError。

        Codex M1 闭环：F091 不可通过 fallback 隐式定义 "auto" 行为，避免锁死 F100 设计空间。
        """
        ctx_main = _make_context(
            delegation_mode="main_inline",
            recall_planner_mode="auto",
        )
        with pytest.raises(NotImplementedError, match='"auto" not implemented in F091'):
            is_recall_planner_skip(ctx_main, {})

        ctx_delegate = _make_context(
            delegation_mode="main_delegate",
            recall_planner_mode="auto",
        )
        with pytest.raises(NotImplementedError, match='"auto" not implemented in F091'):
            is_recall_planner_skip(ctx_delegate, {})

    def test_runtime_context_none_falls_back_to_metadata(self) -> None:
        """runtime_context = None 时 fallback 到 metadata flag。"""
        assert is_recall_planner_skip(None, {"single_loop_executor": True}) is True
        assert is_recall_planner_skip(None, {"single_loop_executor": False}) is False


# ============================================================
# metadata_flag 边界
# ============================================================


class TestMetadataFlag:
    """generic flag 解析：bool / "true" / "1" / "yes" / "on" 接受为真，其余为假。"""

    @pytest.mark.parametrize(
        "value,expected",
        [
            (True, True),
            (False, False),
            ("true", True),
            ("True", True),
            ("1", True),
            ("yes", True),
            ("on", True),
            ("false", False),
            ("0", False),
            ("", False),
            (None, False),
        ],
    )
    def test_parse_truthy_values(self, value, expected: bool) -> None:
        assert metadata_flag({"key": value}, "key") is expected

    def test_metadata_none_returns_false(self) -> None:
        assert metadata_flag(None, "key") is False

    def test_missing_key_returns_false(self) -> None:
        assert metadata_flag({}, "key") is False
