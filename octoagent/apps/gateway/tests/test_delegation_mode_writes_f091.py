"""F091 Phase D: delegation_mode 显式写入测试（medium #1 + medium #2 闭环）。

覆盖：
- DelegationPlaneService.delegation_mode_for_target_kind helper 真值表
  （SUBAGENT → "subagent"，其余 → "main_delegate"）
- prepare_dispatch 标准 delegation 路径产出的 dispatch_envelope.runtime_context.delegation_mode
  与最终 target_kind 一致（pipeline 解析后写入，避免 initial vs final 不一致）
"""

from pathlib import Path

import pytest

from octoagent.core.models import DelegationTargetKind, NormalizedMessage, OrchestratorRequest
from octoagent.gateway.services.delegation_plane import DelegationPlaneService


# ============================================================
# medium #2: delegation_mode_for_target_kind helper 真值表
# ============================================================


class TestDelegationModeForTargetKind:
    """根据 DelegationTargetKind 推断 delegation_mode。"""

    def test_subagent_target_kind_maps_to_subagent(self) -> None:
        result = DelegationPlaneService.delegation_mode_for_target_kind(
            DelegationTargetKind.SUBAGENT
        )
        assert result == "subagent"

    @pytest.mark.parametrize(
        "target_kind",
        [
            DelegationTargetKind.WORKER,
            DelegationTargetKind.ACP_RUNTIME,
            DelegationTargetKind.GRAPH_AGENT,
            DelegationTargetKind.FALLBACK,
        ],
    )
    def test_non_subagent_target_kinds_map_to_main_delegate(
        self, target_kind: DelegationTargetKind
    ) -> None:
        result = DelegationPlaneService.delegation_mode_for_target_kind(target_kind)
        assert result == "main_delegate"

    def test_all_target_kinds_covered(self) -> None:
        """每个 DelegationTargetKind 值都应有显式映射（不允许 unspecified 漏值）。"""
        for tk in DelegationTargetKind:
            result = DelegationPlaneService.delegation_mode_for_target_kind(tk)
            assert result in ("subagent", "main_delegate"), (
                f"DelegationTargetKind.{tk.name} 映射到非预期 delegation_mode: {result}"
            )


# ============================================================
# medium #2 集成：prepare_dispatch 路径的 delegation_mode 与最终 target_kind 一致
# ============================================================


async def test_prepare_dispatch_writes_delegation_mode_matching_final_target_kind(
    tmp_path: Path,
) -> None:
    """F091 Phase D Codex finding 闭环：dispatch_envelope.runtime_context.delegation_mode
    必须根据 pipeline 解析后的 final target_kind 写入，而不是 initial_target_kind。

    场景：dev 任务 pipeline 路由到 graph_agent → delegation_mode = "main_delegate"。
    """
    from .test_delegation_plane import _build_services

    store_group, task_service, delegation_plane = await _build_services(tmp_path)
    task_id, _ = await task_service.create_task(
        NormalizedMessage(
            text="请修复代码并补测试",
            idempotency_key="f091-phase-d-delegation-mode",
        )
    )

    plan = await delegation_plane.prepare_dispatch(
        OrchestratorRequest(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            user_text="请修复代码并补测试",
            worker_capability="llm_generation",
            metadata={},
        )
    )

    assert plan.dispatch_envelope is not None
    assert plan.dispatch_envelope.runtime_context is not None
    final_target_kind = plan.work.target_kind
    expected_mode = DelegationPlaneService.delegation_mode_for_target_kind(final_target_kind)
    assert plan.dispatch_envelope.runtime_context.delegation_mode == expected_mode, (
        f"final target_kind={final_target_kind.value} → "
        f"expected delegation_mode={expected_mode}, "
        f"actual={plan.dispatch_envelope.runtime_context.delegation_mode}"
    )

    await store_group.conn.close()
