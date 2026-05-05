"""F090 D1: RuntimeControlContext 显式化 metadata flag 测试。

验证 delegation_mode + recall_planner_mode 字段：
- 默认值兼容老调用
- Literal 类型严格校验
- 序列化/反序列化 round-trip
"""

from __future__ import annotations

import pytest
from octoagent.core.models import (
    DelegationMode,
    RecallPlannerMode,
    RuntimeControlContext,
)


def test_runtime_control_context_default_values() -> None:
    """老路径不传 delegation_mode / recall_planner_mode 时默认值合理。"""
    ctx = RuntimeControlContext(task_id="t-001")
    assert ctx.delegation_mode == "unspecified"
    assert ctx.recall_planner_mode == "full"


def test_runtime_control_context_explicit_values() -> None:
    """显式赋值 delegation_mode + recall_planner_mode。"""
    ctx = RuntimeControlContext(
        task_id="t-002",
        delegation_mode="main_inline",
        recall_planner_mode="skip",
    )
    assert ctx.delegation_mode == "main_inline"
    assert ctx.recall_planner_mode == "skip"


@pytest.mark.parametrize(
    "delegation_mode",
    ["unspecified", "main_inline", "main_delegate", "worker_inline", "subagent"],
)
def test_runtime_control_context_all_delegation_modes(
    delegation_mode: DelegationMode,
) -> None:
    """所有 DelegationMode Literal 值均可接受。"""
    ctx = RuntimeControlContext(
        task_id="t-003",
        delegation_mode=delegation_mode,
    )
    assert ctx.delegation_mode == delegation_mode


@pytest.mark.parametrize(
    "recall_planner_mode",
    ["full", "skip", "auto"],
)
def test_runtime_control_context_all_recall_planner_modes(
    recall_planner_mode: RecallPlannerMode,
) -> None:
    """所有 RecallPlannerMode Literal 值均可接受。"""
    ctx = RuntimeControlContext(
        task_id="t-004",
        recall_planner_mode=recall_planner_mode,
    )
    assert ctx.recall_planner_mode == recall_planner_mode


def test_runtime_control_context_invalid_delegation_mode_rejected() -> None:
    """非法 delegation_mode 值被 Pydantic 拒绝。"""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        RuntimeControlContext(
            task_id="t-005",
            delegation_mode="invalid_mode",  # type: ignore[arg-type]
        )


def test_runtime_control_context_invalid_recall_planner_mode_rejected() -> None:
    """非法 recall_planner_mode 值被 Pydantic 拒绝。"""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        RuntimeControlContext(
            task_id="t-006",
            recall_planner_mode="not_a_mode",  # type: ignore[arg-type]
        )


def test_runtime_control_context_round_trip_serialization() -> None:
    """model_dump + model_validate round-trip 保留 delegation_mode + recall_planner_mode。"""
    ctx = RuntimeControlContext(
        task_id="t-007",
        delegation_mode="worker_inline",
        recall_planner_mode="skip",
    )
    dumped = ctx.model_dump(mode="json")
    assert dumped["delegation_mode"] == "worker_inline"
    assert dumped["recall_planner_mode"] == "skip"

    rebuilt = RuntimeControlContext.model_validate(dumped)
    assert rebuilt.delegation_mode == "worker_inline"
    assert rebuilt.recall_planner_mode == "skip"


def test_runtime_control_context_model_copy_update() -> None:
    """model_copy update 字段保持其他字段不变。"""
    ctx = RuntimeControlContext(
        task_id="t-008",
        surface="chat",
        delegation_mode="unspecified",
        recall_planner_mode="full",
    )
    updated = ctx.model_copy(
        update={
            "delegation_mode": "main_inline",
            "recall_planner_mode": "skip",
        }
    )
    assert updated.task_id == "t-008"
    assert updated.surface == "chat"
    assert updated.delegation_mode == "main_inline"
    assert updated.recall_planner_mode == "skip"
    # 原 ctx 不变（model_copy immutable）
    assert ctx.delegation_mode == "unspecified"
    assert ctx.recall_planner_mode == "full"


def test_dispatch_envelope_runtime_context_propagation() -> None:
    """F090 D1: DispatchEnvelope 透传 RuntimeControlContext 字段不丢失。"""
    from octoagent.core.models import DispatchEnvelope

    ctx = RuntimeControlContext(
        task_id="t-009",
        delegation_mode="main_inline",
        recall_planner_mode="skip",
    )
    envelope = DispatchEnvelope(
        dispatch_id="d-001",
        task_id="t-009",
        trace_id="trace-009",
        route_reason="test",
        worker_capability="llm_generation",
        hop_count=0,
        max_hops=3,
        user_text="hello",
        runtime_context=ctx,
    )
    assert envelope.runtime_context is not None
    assert envelope.runtime_context.delegation_mode == "main_inline"
    assert envelope.runtime_context.recall_planner_mode == "skip"
    # round-trip JSON 后字段保留
    rebuilt = DispatchEnvelope.model_validate(envelope.model_dump(mode="json"))
    assert rebuilt.runtime_context is not None
    assert rebuilt.runtime_context.delegation_mode == "main_inline"
    assert rebuilt.runtime_context.recall_planner_mode == "skip"
