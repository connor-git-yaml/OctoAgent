"""F100 Phase D: RecallPlannerMode "auto" 决议 + force_full_recall override + FR-H 接入测试。

覆盖：
- AC-1：force_full_recall=True + delegation_mode=main_inline → False (skip 被 override)
- AC-2：force_full_recall=True 对所有 4 个 delegation_mode 都返回 False
- AC-3：AUTO 决议 4 case：main_inline/worker_inline→True, main_delegate/subagent→False
- AC-4：worker_inline + skip baseline 行为不变（不引入 regression）
- AC-11：supports_single_loop_executor 类属性保留 + duck-type 检测正确
- AC-H1：metadata["force_full_recall"]=True → patched runtime_context.force_full_recall == True
- AC-H2：metadata 不含 hint → patched runtime_context.force_full_recall == False
- round-trip：encode/decode 保留 force_full_recall 字段
"""

from __future__ import annotations

import pytest

from octoagent.core.models import RuntimeControlContext
from octoagent.gateway.services.runtime_control import (
    decode_runtime_context,
    encode_runtime_context,
    is_recall_planner_skip,
    is_single_loop_main_active,
)


def _ctx(
    *,
    delegation_mode: str = "unspecified",
    recall_planner_mode: str = "full",
    force_full_recall: bool = False,
) -> RuntimeControlContext:
    return RuntimeControlContext(
        task_id="task-f100-test",
        delegation_mode=delegation_mode,
        recall_planner_mode=recall_planner_mode,
        force_full_recall=force_full_recall,
    )


# ============================================================
# AC-1 / AC-2: force_full_recall override 优先级最高
# ============================================================


class TestForceFullRecallOverride:
    """force_full_recall=True 覆盖所有 mode 决议（H1 完整决策环）。"""

    def test_ac1_force_full_recall_overrides_main_inline_skip(self) -> None:
        """AC-1：main_inline + skip + force_full_recall=True → False (full)"""
        ctx = _ctx(
            delegation_mode="main_inline",
            recall_planner_mode="skip",
            force_full_recall=True,
        )
        assert is_recall_planner_skip(ctx, {}) is False

    @pytest.mark.parametrize(
        "delegation_mode",
        ["main_inline", "main_delegate", "worker_inline", "subagent"],
    )
    @pytest.mark.parametrize(
        "recall_planner_mode",
        ["full", "skip", "auto"],
    )
    def test_ac2_force_full_recall_overrides_all_modes(
        self, delegation_mode: str, recall_planner_mode: str
    ) -> None:
        """AC-2：force_full_recall=True 对所有 delegation_mode × recall_planner_mode 组合都返回 False"""
        ctx = _ctx(
            delegation_mode=delegation_mode,
            recall_planner_mode=recall_planner_mode,
            force_full_recall=True,
        )
        assert is_recall_planner_skip(ctx, {}) is False, (
            f"force_full_recall=True 应该 override "
            f"delegation_mode={delegation_mode}, recall_planner_mode={recall_planner_mode}"
        )


# ============================================================
# AC-3: AUTO 决议依 delegation_mode 自动决议
# ============================================================


class TestAutoModeResolution:
    """RecallPlannerMode.AUTO + delegation_mode 自动决议（F100 启用）。"""

    @pytest.mark.parametrize(
        "delegation_mode,expected_skip",
        [
            ("main_inline", True),  # AUTO + main_inline → skip（F051 兼容）
            ("worker_inline", True),  # AUTO + worker_inline → skip
            ("main_delegate", False),  # AUTO + main_delegate → full（H1 决策环）
            ("subagent", False),  # AUTO + subagent → full
        ],
    )
    def test_ac3_auto_resolution_by_delegation_mode(
        self, delegation_mode: str, expected_skip: bool
    ) -> None:
        """AC-3：AUTO 决议——inline → skip / delegate / subagent → full"""
        ctx = _ctx(
            delegation_mode=delegation_mode,
            recall_planner_mode="auto",
            force_full_recall=False,
        )
        assert is_recall_planner_skip(ctx, {}) is expected_skip

    def test_unknown_recall_planner_mode_raises(self) -> None:
        """defense-in-depth：未来若引入未识别的 recall_planner_mode 取值，应 raise。

        当前 RecallPlannerMode Literal 仅 3 个值；pydantic 在构造时已限制。
        本测试通过绕过 pydantic 直接 model_construct 构造，验证 helper 内部
        防御逻辑（unknown mode 拒绝静默 fallback）。
        """
        # model_construct 绕过 pydantic 验证，模拟未来 mode 取值演化场景
        ctx = RuntimeControlContext.model_construct(
            task_id="task-test",
            delegation_mode="main_inline",
            recall_planner_mode="hypothetical_future_mode",  # type: ignore[arg-type]
            force_full_recall=False,
        )
        with pytest.raises(ValueError, match="Unknown recall_planner_mode"):
            is_recall_planner_skip(ctx, {})


# ============================================================
# AC-4: worker_inline + skip baseline 行为不变（不引入 regression）
# ============================================================


class TestWorkerInlineBaselineUnchanged:
    """Worker 路径 baseline 行为零变化（F100 默认 force_full_recall=False）。"""

    def test_ac4_worker_inline_skip_returns_true(self) -> None:
        ctx = _ctx(delegation_mode="worker_inline", recall_planner_mode="skip")
        assert is_recall_planner_skip(ctx, {}) is True

    def test_ac4_worker_inline_full_returns_false(self) -> None:
        ctx = _ctx(delegation_mode="worker_inline", recall_planner_mode="full")
        assert is_recall_planner_skip(ctx, {}) is False

    def test_ac4_main_inline_skip_returns_true_baseline(self) -> None:
        """baseline F091 行为：main_inline + skip → True"""
        ctx = _ctx(delegation_mode="main_inline", recall_planner_mode="skip")
        assert is_recall_planner_skip(ctx, {}) is True

    def test_ac4_main_delegate_full_returns_false_baseline(self) -> None:
        """baseline F091 行为：main_delegate + full → False"""
        ctx = _ctx(delegation_mode="main_delegate", recall_planner_mode="full")
        assert is_recall_planner_skip(ctx, {}) is False


# ============================================================
# AC-11: supports_single_loop_executor 类属性保留
# ============================================================


class TestSupportsSingleLoopExecutorPreserved:
    """F091 实证：mock fixture duck-type 依赖此类属性表达"不支持"。F100 必须保留。"""

    def test_ac11_llm_service_class_has_attribute(self) -> None:
        from octoagent.gateway.services.llm_service import LLMService

        assert hasattr(LLMService, "supports_single_loop_executor")
        assert LLMService.supports_single_loop_executor is True

    def test_ac11_duck_type_missing_attribute_returns_false(self) -> None:
        """模拟 mock fixture：构造一个无该属性的对象，getattr fallback 返回 False。"""
        class MockLLMServiceWithoutAttr:
            pass

        mock = MockLLMServiceWithoutAttr()
        assert getattr(mock, "supports_single_loop_executor", False) is False


# ============================================================
# AC-H1 / AC-H2: FR-H metadata hint → force_full_recall 接入
# ============================================================


class TestForceFullRecallHintInjection:
    """FR-H：orchestrator._with_delegation_mode 接受 metadata["force_full_recall"] hint。

    H1 minimal trigger 验证：上层（chat 路由 / API 参数 / 调试工具）可通过 metadata
    hint 显式触发 H1 完整决策环。
    """

    def _make_request(self):
        """构造最小 OrchestratorRequest 用于测试 _with_delegation_mode."""
        from octoagent.core.models import OrchestratorRequest

        return OrchestratorRequest(
            task_id="task-h1-test",
            trace_id="trace-h1-test",
            user_text="test user input",
            contract_version="1.0",
        )

    def test_ach1_metadata_hint_true_writes_force_full_recall(self) -> None:
        """AC-H1：metadata["force_full_recall"]=True → patched runtime_context.force_full_recall == True"""
        from octoagent.gateway.services.orchestrator import OrchestratorService

        request = self._make_request()
        patched = OrchestratorService._with_delegation_mode(
            request=request,
            metadata={"force_full_recall": True},
            delegation_mode="main_inline",
            recall_planner_mode="auto",
        )
        assert patched.force_full_recall is True

    def test_ach1_metadata_hint_string_true_writes_force_full_recall(self) -> None:
        """AC-H1 兼容性：metadata["force_full_recall"]="1" / "true" 等字符串也生效。"""
        from octoagent.gateway.services.orchestrator import OrchestratorService

        request = self._make_request()
        for str_value in ("1", "true", "True", "yes", "on"):
            patched = OrchestratorService._with_delegation_mode(
                request=request,
                metadata={"force_full_recall": str_value},
                delegation_mode="main_inline",
                recall_planner_mode="auto",
            )
            assert patched.force_full_recall is True, (
                f'metadata["force_full_recall"]={str_value!r} 应解析为 True'
            )

    def test_ach2_no_metadata_hint_default_false(self) -> None:
        """AC-H2：metadata 不含 hint → patched runtime_context.force_full_recall == False（默认）"""
        from octoagent.gateway.services.orchestrator import OrchestratorService

        request = self._make_request()
        patched = OrchestratorService._with_delegation_mode(
            request=request,
            metadata={},
            delegation_mode="main_inline",
            recall_planner_mode="skip",
        )
        assert patched.force_full_recall is False

    def test_ach2_explicit_kwarg_overrides_hint(self) -> None:
        """显式传 force_full_recall=False 覆盖 metadata hint=True。"""
        from octoagent.gateway.services.orchestrator import OrchestratorService

        request = self._make_request()
        patched = OrchestratorService._with_delegation_mode(
            request=request,
            metadata={"force_full_recall": True},  # metadata hint True
            delegation_mode="main_inline",
            recall_planner_mode="skip",
            force_full_recall=False,  # 显式参数覆盖
        )
        assert patched.force_full_recall is False


# ============================================================
# round-trip: encode/decode 保留 force_full_recall
# ============================================================


class TestForceFullRecallRoundTrip:
    """pydantic round-trip 不破坏 force_full_recall 字段。"""

    def test_encode_decode_round_trip_true(self) -> None:
        original = _ctx(
            delegation_mode="main_inline",
            recall_planner_mode="auto",
            force_full_recall=True,
        )
        encoded = encode_runtime_context(original)
        decoded = decode_runtime_context(encoded)
        assert decoded is not None
        assert decoded.force_full_recall is True
        assert decoded.delegation_mode == "main_inline"
        assert decoded.recall_planner_mode == "auto"

    def test_encode_decode_round_trip_false(self) -> None:
        original = _ctx(
            delegation_mode="worker_inline",
            recall_planner_mode="skip",
            force_full_recall=False,
        )
        encoded = encode_runtime_context(original)
        decoded = decode_runtime_context(encoded)
        assert decoded is not None
        assert decoded.force_full_recall is False


# ============================================================
# end-to-end: AUTO + force_full_recall override 综合行为
# ============================================================


class TestE2EAutoAndOverride:
    """综合场景：AUTO 决议 vs force_full_recall override 优先级。"""

    def test_force_full_recall_overrides_auto_inline_skip(self) -> None:
        """AUTO + main_inline 默认 → skip；force_full_recall=True → full（H1 启用）"""
        ctx = _ctx(
            delegation_mode="main_inline",
            recall_planner_mode="auto",
            force_full_recall=True,
        )
        # H1 完整决策环启用：override 让 inline 也走 full
        assert is_recall_planner_skip(ctx, {}) is False

    def test_auto_inline_no_override_still_skip(self) -> None:
        """AUTO + main_inline 默认（不 override）→ skip（F051 性能兼容）"""
        ctx = _ctx(
            delegation_mode="main_inline",
            recall_planner_mode="auto",
            force_full_recall=False,
        )
        assert is_recall_planner_skip(ctx, {}) is True

    def test_auto_delegate_no_override_full(self) -> None:
        """AUTO + main_delegate（不 override）→ full（H1 默认走完整决策）"""
        ctx = _ctx(
            delegation_mode="main_delegate",
            recall_planner_mode="auto",
            force_full_recall=False,
        )
        assert is_recall_planner_skip(ctx, {}) is False
