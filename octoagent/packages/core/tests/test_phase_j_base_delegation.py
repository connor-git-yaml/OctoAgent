"""F098 Phase J: BaseDelegation 公共抽象单测（OD-5 选 A）。

测试场景：
- AC-J1: BaseDelegation 字段完整性（7 个共享字段）
- AC-J2: SubagentDelegation 继承 BaseDelegation 不破坏子类语义
- AC-J3: BaseDelegation round-trip + 子类 round-trip
- F097 baseline 测试 0 regression（继承不破坏序列化）
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from octoagent.core.models import SubagentDelegation
from octoagent.core.models.delegation import BaseDelegation, DelegationTargetKind


_NOW = datetime(2026, 5, 10, 12, 0, 0, tzinfo=UTC)


def _make_subagent() -> SubagentDelegation:
    return SubagentDelegation(
        delegation_id="01J0J00000000000000000DELJ",
        parent_task_id="parent-task-j",
        parent_work_id="parent-work-j",
        child_task_id="child-task-j",
        child_agent_session_id="child-session-j",
        caller_agent_runtime_id="caller-runtime-j",
        caller_project_id="caller-project-j",
        caller_memory_namespace_ids=["ns-caller"],
        spawned_by="delegate_task",
        created_at=_NOW,
    )


# ---- AC-J1: BaseDelegation 字段完整性 ----


def test_base_delegation_required_fields():
    """AC-J1: BaseDelegation 7 个共享字段都是 required（min_length=1）。"""
    # 缺 delegation_id
    with pytest.raises(ValidationError):
        BaseDelegation(
            parent_task_id="parent",
            parent_work_id="work",
            child_task_id="child",
            caller_agent_runtime_id="runtime",
            spawned_by="tool",
            created_at=_NOW,
        )

    # 空字符串不允许
    with pytest.raises(ValidationError):
        BaseDelegation(
            delegation_id="",
            parent_task_id="parent",
            parent_work_id="work",
            child_task_id="child",
            caller_agent_runtime_id="runtime",
            spawned_by="tool",
            created_at=_NOW,
        )


def test_base_delegation_minimal_construction():
    """AC-J1: BaseDelegation 提供 7 个共享字段就可构造（不含子类专属字段）。"""
    base = BaseDelegation(
        delegation_id="d-1",
        parent_task_id="p-1",
        parent_work_id="w-1",
        child_task_id="c-1",
        caller_agent_runtime_id="r-1",
        spawned_by="tool",
        created_at=_NOW,
    )
    assert base.delegation_id == "d-1"
    assert base.closed_at is None  # default


def test_base_delegation_round_trip():
    """AC-J3: BaseDelegation model_dump / model_validate round-trip。"""
    base = BaseDelegation(
        delegation_id="d-rt",
        parent_task_id="p-rt",
        parent_work_id="w-rt",
        child_task_id="c-rt",
        caller_agent_runtime_id="r-rt",
        spawned_by="tool",
        created_at=_NOW,
        closed_at=_NOW,
    )
    serialized = base.model_dump(mode="json")
    rebuilt = BaseDelegation.model_validate(serialized)
    assert rebuilt.delegation_id == base.delegation_id
    assert rebuilt.closed_at == base.closed_at


# ---- AC-J2: SubagentDelegation 继承 BaseDelegation ----


def test_subagent_delegation_inherits_base_delegation():
    """AC-J2: SubagentDelegation 继承 BaseDelegation，isinstance 检测通过。"""
    sub = _make_subagent()
    assert isinstance(sub, BaseDelegation), (
        "AC-J2 闭环失败：SubagentDelegation 不是 BaseDelegation 子类"
    )
    # 父类字段可访问
    assert sub.delegation_id == "01J0J00000000000000000DELJ"
    assert sub.parent_task_id == "parent-task-j"


def test_subagent_delegation_keeps_specific_fields():
    """AC-J2: SubagentDelegation 子类专属字段（child_agent_session_id / caller_project_id /
    caller_memory_namespace_ids / target_kind）保留。"""
    sub = _make_subagent()
    assert sub.child_agent_session_id == "child-session-j"
    assert sub.caller_project_id == "caller-project-j"
    assert sub.caller_memory_namespace_ids == ["ns-caller"]
    assert sub.target_kind == DelegationTargetKind.SUBAGENT


def test_subagent_delegation_round_trip_after_inheritance():
    """AC-J3: SubagentDelegation 继承 BaseDelegation 后 round-trip 完整字段都保留。"""
    sub = _make_subagent()
    serialized = sub.model_dump(mode="json")
    rebuilt = SubagentDelegation.model_validate(serialized)
    assert rebuilt.delegation_id == sub.delegation_id
    assert rebuilt.child_agent_session_id == sub.child_agent_session_id
    assert rebuilt.caller_memory_namespace_ids == sub.caller_memory_namespace_ids
    assert rebuilt.target_kind == DelegationTargetKind.SUBAGENT


def test_subagent_delegation_target_kind_literal_enforced():
    """AC-J2: target_kind Literal 仍生效（反序列化拒绝非 SUBAGENT 值）。"""
    sub = _make_subagent()
    serialized = sub.model_dump(mode="json")

    # 篡改 target_kind 应被拒绝
    serialized["target_kind"] = "worker"
    with pytest.raises(ValidationError):
        SubagentDelegation.model_validate(serialized)


def test_subagent_delegation_caller_project_id_required():
    """AC-J2: caller_project_id 仍是 required（子类专属字段约束保留）。"""
    with pytest.raises(ValidationError):
        SubagentDelegation(
            delegation_id="d",
            parent_task_id="p",
            parent_work_id="w",
            child_task_id="c",
            caller_agent_runtime_id="r",
            caller_project_id="",  # 空字符串应被拒绝
            spawned_by="tool",
            created_at=_NOW,
        )
