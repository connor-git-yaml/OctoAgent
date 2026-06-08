"""F091 Phase C: runtime_control helpers 单测。

覆盖：
- is_single_loop_main_active：runtime_context.delegation_mode 真值表 + unspecified/None 兜底
- is_recall_planner_skip：recall_planner_mode 优先 + AUTO 决议 + unspecified/None 兜底

F100 Phase E2 移除 metadata flag fallback、F112 删除两 helper 的 metadata 形参后，
决议完全由 runtime_context 决定；原"metadata flag 被忽略/兜底"系列用例随形参删除收敛
（字面重复用例合并，真值表覆盖不变；module-level metadata_flag 已删，对应 TestMetadataFlag 移除）。
"""

import pytest

from octoagent.core.models import RuntimeControlContext
from octoagent.gateway.services.runtime_control import (
    is_recall_planner_skip,
    is_single_loop_main_active,
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
    """决议完全基于 runtime_context.delegation_mode：

    - delegation_mode in {"main_inline", "worker_inline"} → True
    - delegation_mode in {"main_delegate", "subagent"} → False
    - delegation_mode == "unspecified" 或 runtime_context = None → False
      （F100 Phase E2 / F112：与 baseline metadata flag 缺失时的默认行为等价）
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
        """显式 delegation_mode → 直接按真值表返回。"""
        ctx = _make_context(delegation_mode=delegation_mode)
        assert is_single_loop_main_active(ctx) is expected

    def test_unspecified_returns_false(self) -> None:
        """delegation_mode = unspecified → False（无 metadata fallback）。"""
        ctx = _make_context(delegation_mode="unspecified")
        assert is_single_loop_main_active(ctx) is False

    def test_runtime_context_none_returns_false(self) -> None:
        """runtime_context = None → False（与 baseline 一致）。"""
        assert is_single_loop_main_active(None) is False


# ============================================================
# is_recall_planner_skip 真值表
# ============================================================


class TestIsRecallPlannerSkip:
    """决议基于 runtime_context.recall_planner_mode（含 AUTO 依 delegation_mode 决议）+ unspecified/None 兜底。"""

    def test_skip_mode_with_explicit_delegation_returns_true(self) -> None:
        """recall_planner_mode = skip + delegation_mode 显式 → True。"""
        ctx = _make_context(delegation_mode="main_inline", recall_planner_mode="skip")
        assert is_recall_planner_skip(ctx) is True

    def test_full_mode_with_explicit_delegation_returns_false(self) -> None:
        """recall_planner_mode = full + delegation_mode 显式 → False。"""
        ctx = _make_context(delegation_mode="main_delegate", recall_planner_mode="full")
        assert is_recall_planner_skip(ctx) is False

    def test_unspecified_returns_false(self) -> None:
        """delegation_mode = unspecified → False（无 metadata fallback；与 baseline 缺失时等价）。"""
        ctx = _make_context()  # delegation_mode = unspecified
        assert is_recall_planner_skip(ctx) is False

    def test_auto_mode_enabled_in_f100(self) -> None:
        """AUTO 依 delegation_mode 决议（F100 Phase D 启用）：

        - main_inline / worker_inline → skip (True)
        - main_delegate / subagent → full (False)

        详细 AUTO 决议覆盖见 test_runtime_control_f100.py::TestAutoModeResolution。
        """
        ctx_main = _make_context(
            delegation_mode="main_inline",
            recall_planner_mode="auto",
        )
        assert is_recall_planner_skip(ctx_main) is True

        ctx_delegate = _make_context(
            delegation_mode="main_delegate",
            recall_planner_mode="auto",
        )
        assert is_recall_planner_skip(ctx_delegate) is False

    def test_runtime_context_none_returns_false(self) -> None:
        """runtime_context = None → False（无 fallback；与 baseline 等价）。"""
        assert is_recall_planner_skip(None) is False
