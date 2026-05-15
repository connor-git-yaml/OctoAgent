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
    """F100 Phase E2 迁移：fallback metadata flag 已移除。

    读取优先级：
    - runtime_context.delegation_mode in {"main_inline", "worker_inline"} → True
    - runtime_context.delegation_mode in {"main_delegate", "subagent"} → False
    - delegation_mode == "unspecified" 或 runtime_context = None → return False
      （v0.3：与 baseline metadata flag 缺失时等价；不再 fallback 到 metadata flag）
    """

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
        """显式 delegation_mode → 直接返回；不受 metadata flag 影响。"""
        ctx = _make_context(delegation_mode=delegation_mode)
        # 即使 metadata flag 矛盾，runtime_context 优先（F091 baseline 不变）
        assert is_single_loop_main_active(ctx, {"single_loop_executor": not expected}) is expected

    def test_unspecified_returns_false_regardless_of_metadata_flag_true(self) -> None:
        """F100 Phase E2 迁移：unspecified + metadata flag True → False（不再 fallback）。

        baseline 行为：unspecified → fallback metadata_flag → True
        F100 v0.3 行为：unspecified → return False（移除 fallback）
        """
        ctx = _make_context(delegation_mode="unspecified")
        assert is_single_loop_main_active(ctx, {"single_loop_executor": True}) is False

    def test_unspecified_returns_false_with_metadata_flag_false(self) -> None:
        """unspecified + metadata flag False → False（行为等价）。"""
        ctx = _make_context(delegation_mode="unspecified")
        assert is_single_loop_main_active(ctx, {"single_loop_executor": False}) is False

    def test_unspecified_no_metadata_returns_false(self) -> None:
        ctx = _make_context(delegation_mode="unspecified")
        assert is_single_loop_main_active(ctx, {}) is False

    def test_runtime_context_none_returns_false_regardless_of_metadata(self) -> None:
        """F100 Phase E2：None runtime_context 不再 fallback 到 metadata flag。"""
        assert is_single_loop_main_active(None, {"single_loop_executor": True}) is False
        assert is_single_loop_main_active(None, {"single_loop_executor": False}) is False
        assert is_single_loop_main_active(None, {}) is False

    def test_runtime_context_none_metadata_none(self) -> None:
        """两者都 None 默认 False（与 baseline 一致）。"""
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

    def test_default_context_returns_false_no_metadata_fallback(self) -> None:
        """F100 Phase E2 迁移：delegation_mode = unspecified → return False（不 fallback metadata）。

        baseline 行为：unspecified + metadata flag True → fallback → True
        F100 v0.3 行为：unspecified → return False（无 fallback；与 baseline metadata 缺失时等价）
        """
        ctx = _make_context()  # delegation_mode = unspecified
        # F100 Phase E2：fallback 移除，所有 unspecified 路径都 return False
        assert is_recall_planner_skip(ctx, {"single_loop_executor": True}) is False
        assert is_recall_planner_skip(ctx, {"single_loop_executor": False}) is False
        assert is_recall_planner_skip(ctx, {}) is False

    def test_auto_mode_enabled_in_f100(self) -> None:
        """F091 占位 raise NotImplementedError 已在 F100 Phase D 启用。

        F100 启用语义：依 delegation_mode 自动决议
        - main_inline / worker_inline → skip (True)
        - main_delegate / subagent → full (False)

        详细 AUTO 决议覆盖见 test_runtime_control_f100.py::TestAutoModeResolution。
        本测试仅迁移历史 F091 占位 raise 断言到 F100 启用后行为。
        """
        ctx_main = _make_context(
            delegation_mode="main_inline",
            recall_planner_mode="auto",
        )
        assert is_recall_planner_skip(ctx_main, {}) is True

        ctx_delegate = _make_context(
            delegation_mode="main_delegate",
            recall_planner_mode="auto",
        )
        assert is_recall_planner_skip(ctx_delegate, {}) is False

    def test_runtime_context_none_returns_false(self) -> None:
        """F100 Phase E2：runtime_context = None → return False（无 fallback；与 baseline 等价）。"""
        assert is_recall_planner_skip(None, {"single_loop_executor": True}) is False
        assert is_recall_planner_skip(None, {"single_loop_executor": False}) is False
        assert is_recall_planner_skip(None, {}) is False


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
