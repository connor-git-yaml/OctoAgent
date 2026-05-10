"""F097 Phase A — SubagentDelegation Pydantic model 单测。

覆盖：
1. 所有 11 个字段的类型 + 默认值 + 必填验证
2. round-trip：model_dump_json → model_validate_json 等价
3. child_task.metadata.subagent_delegation 路径模拟（AC-A2 + CL#16）
4. target_kind 默认值为 DelegationTargetKind.SUBAGENT
5. closed_at 默认 None
6. child_agent_session_id 可为 None（spawn 失败 / 早期阶段）
7. caller_memory_namespace_ids 默认空列表
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from octoagent.core.models import DelegationTargetKind, SubagentDelegation


# ============================================================
# 测试辅助：构造最小合法 SubagentDelegation
# ============================================================

_ULID_DELEGATION_ID = "01HY4A3X8QGKBD3F2TQR7N5GNZ"
_ULID_PARENT_TASK = "01HY4A3X8QGKBD3F2TQR7N5GP0"
_ULID_PARENT_WORK = "01HY4A3X8QGKBD3F2TQR7N5GP1"
_ULID_CHILD_TASK = "01HY4A3X8QGKBD3F2TQR7N5GP2"
_ULID_CHILD_SESSION = "01HY4A3X8QGKBD3F2TQR7N5GP3"
_ULID_CALLER_RUNTIME = "01HY4A3X8QGKBD3F2TQR7N5GP4"
_ULID_CALLER_PROJECT = "01HY4A3X8QGKBD3F2TQR7N5GP5"
_CREATED_AT = datetime(2026, 5, 10, 12, 0, 0, tzinfo=UTC)


def _make_delegation(**overrides) -> SubagentDelegation:
    """创建最小合法 SubagentDelegation，可通过 overrides 覆盖字段。"""
    defaults = {
        "delegation_id": _ULID_DELEGATION_ID,
        "parent_task_id": _ULID_PARENT_TASK,
        "parent_work_id": _ULID_PARENT_WORK,
        "child_task_id": _ULID_CHILD_TASK,
        "caller_agent_runtime_id": _ULID_CALLER_RUNTIME,
        "caller_project_id": _ULID_CALLER_PROJECT,
        "spawned_by": "delegate_task",
        "created_at": _CREATED_AT,
    }
    defaults.update(overrides)
    return SubagentDelegation(**defaults)


# ============================================================
# 测试组 1：字段默认值 + 必填验证
# ============================================================


class TestFieldDefaults:
    """字段默认值 + 可选字段语义。"""

    def test_target_kind_default_is_subagent(self) -> None:
        """target_kind 默认为 DelegationTargetKind.SUBAGENT（AC-A2）。"""
        d = _make_delegation()
        assert d.target_kind is DelegationTargetKind.SUBAGENT
        assert d.target_kind == "subagent"

    def test_closed_at_default_none(self) -> None:
        """closed_at 默认 None，表示委托仍活跃（AC-A2）。"""
        d = _make_delegation()
        assert d.closed_at is None

    def test_child_agent_session_id_default_none(self) -> None:
        """child_agent_session_id 默认 None（GATE_DESIGN C-1，spawn 失败时合法）。"""
        d = _make_delegation()
        assert d.child_agent_session_id is None

    def test_caller_memory_namespace_ids_default_empty_list(self) -> None:
        """caller_memory_namespace_ids 默认空列表（OD-1 α 语义初始状态）。"""
        d = _make_delegation()
        assert d.caller_memory_namespace_ids == []

    def test_all_required_fields_present(self) -> None:
        """所有必填字段赋值后正常构造（AC-A1：11 字段全覆盖验证）。"""
        d = _make_delegation(
            child_agent_session_id=_ULID_CHILD_SESSION,
            caller_memory_namespace_ids=["ns-id-1", "ns-id-2"],
            closed_at=datetime(2026, 5, 10, 13, 0, 0, tzinfo=UTC),
        )
        # 必填字段
        assert d.delegation_id == _ULID_DELEGATION_ID
        assert d.parent_task_id == _ULID_PARENT_TASK
        assert d.parent_work_id == _ULID_PARENT_WORK
        assert d.child_task_id == _ULID_CHILD_TASK
        assert d.child_agent_session_id == _ULID_CHILD_SESSION
        assert d.caller_agent_runtime_id == _ULID_CALLER_RUNTIME
        assert d.caller_project_id == _ULID_CALLER_PROJECT
        assert d.caller_memory_namespace_ids == ["ns-id-1", "ns-id-2"]
        assert d.spawned_by == "delegate_task"
        assert d.target_kind is DelegationTargetKind.SUBAGENT
        assert d.created_at == _CREATED_AT
        assert d.closed_at is not None

    def test_missing_required_field_raises(self) -> None:
        """缺少必填字段时 Pydantic 抛出 ValidationError。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            SubagentDelegation(
                # 缺 delegation_id / parent_task_id / parent_work_id 等
                child_task_id=_ULID_CHILD_TASK,
                caller_agent_runtime_id=_ULID_CALLER_RUNTIME,
                caller_project_id=_ULID_CALLER_PROJECT,
                spawned_by="delegate_task",
                created_at=_CREATED_AT,
            )


# ============================================================
# 测试组 2：round-trip 序列化（AC-A2 + CL#16）
# ============================================================


class TestRoundTrip:
    """JSON 序列化 round-trip + child_task.metadata 路径模拟。"""

    def test_model_dump_json_and_validate_json_equivalent(self) -> None:
        """model_dump_json → model_validate_json 等价（字段值完整保留）。"""
        d = _make_delegation(
            child_agent_session_id=_ULID_CHILD_SESSION,
            caller_memory_namespace_ids=["ns-a", "ns-b"],
        )
        json_str = d.model_dump_json()
        restored = SubagentDelegation.model_validate_json(json_str)

        assert restored.delegation_id == d.delegation_id
        assert restored.parent_task_id == d.parent_task_id
        assert restored.parent_work_id == d.parent_work_id
        assert restored.child_task_id == d.child_task_id
        assert restored.child_agent_session_id == d.child_agent_session_id
        assert restored.caller_agent_runtime_id == d.caller_agent_runtime_id
        assert restored.caller_project_id == d.caller_project_id
        assert restored.caller_memory_namespace_ids == d.caller_memory_namespace_ids
        assert restored.spawned_by == d.spawned_by
        assert restored.target_kind is DelegationTargetKind.SUBAGENT
        assert restored.created_at == d.created_at
        assert restored.closed_at is None

    def test_child_task_metadata_path_simulation(self) -> None:
        """模拟写入 child_task.metadata['subagent_delegation'] 路径（CL#16 决策）。

        验证：SubagentDelegation 序列化为 JSON 字符串后可作为 task.metadata value 存储，
        再从 task.metadata 取出后反序列化等价（AC-A2 + AC-A3：无独立 SQL 表）。
        """
        d = _make_delegation(child_agent_session_id=_ULID_CHILD_SESSION)

        # 模拟写入 task.metadata（task store 存储 metadata 为 dict[str, Any]）
        task_metadata: dict = {}
        task_metadata["subagent_delegation"] = d.model_dump_json()

        # 模拟从 task.metadata 读取并反序列化
        raw = task_metadata["subagent_delegation"]
        assert isinstance(raw, str), "metadata value 应为 JSON 字符串"
        restored = SubagentDelegation.model_validate_json(raw)

        assert restored.delegation_id == d.delegation_id
        assert restored.child_task_id == d.child_task_id
        assert restored.child_agent_session_id == _ULID_CHILD_SESSION

    def test_round_trip_with_closed_at(self) -> None:
        """closed_at 非 None 时 round-trip 保留时区信息。"""
        closed_at = datetime(2026, 5, 10, 14, 30, 0, tzinfo=UTC)
        d = _make_delegation(closed_at=closed_at)
        restored = SubagentDelegation.model_validate_json(d.model_dump_json())
        assert restored.closed_at == closed_at

    def test_round_trip_child_session_none(self) -> None:
        """child_agent_session_id=None 时 round-trip 保留 None（spawn 失败场景）。"""
        d = _make_delegation(child_agent_session_id=None)
        json_str = d.model_dump_json()
        restored = SubagentDelegation.model_validate_json(json_str)
        assert restored.child_agent_session_id is None

    def test_model_dump_contains_expected_keys(self) -> None:
        """model_dump() 返回 dict 包含所有 11 个字段键（AC-A1 字段完整性）。"""
        d = _make_delegation()
        dumped = d.model_dump()
        expected_keys = {
            "delegation_id",
            "parent_task_id",
            "parent_work_id",
            "child_task_id",
            "child_agent_session_id",
            "caller_agent_runtime_id",
            "caller_project_id",
            "caller_memory_namespace_ids",
            "spawned_by",
            "target_kind",
            "created_at",
            "closed_at",
        }
        assert expected_keys.issubset(set(dumped.keys())), (
            f"model_dump() 缺少字段：{expected_keys - set(dumped.keys())}"
        )


# ============================================================
# 测试组 3：语义验证
# ============================================================


class TestSemantics:
    """SubagentDelegation 语义边界验证。"""

    def test_subagent_spawn_tool_variants(self) -> None:
        """spawned_by 支持 delegate_task / subagents.spawn 两种来源（OD-2）。"""
        d1 = _make_delegation(spawned_by="delegate_task")
        d2 = _make_delegation(spawned_by="subagents.spawn")
        assert d1.spawned_by == "delegate_task"
        assert d2.spawned_by == "subagents.spawn"

    def test_target_kind_can_be_set_explicitly(self) -> None:
        """target_kind 可显式设置为 SUBAGENT（等价于默认值）。"""
        d = _make_delegation(target_kind=DelegationTargetKind.SUBAGENT)
        assert d.target_kind is DelegationTargetKind.SUBAGENT

    def test_caller_memory_namespace_ids_multiple_values(self) -> None:
        """caller_memory_namespace_ids 支持多个 namespace（OD-1 α 语义）。"""
        ns_ids = ["ns-private-1", "ns-private-2", "ns-shared-3"]
        d = _make_delegation(caller_memory_namespace_ids=ns_ids)
        assert d.caller_memory_namespace_ids == ns_ids
        # round-trip 保留列表内容
        restored = SubagentDelegation.model_validate_json(d.model_dump_json())
        assert restored.caller_memory_namespace_ids == ns_ids


class TestCodexHardenings:
    """Phase A Codex review (P2-1 / P2-2) 闭环单测。"""

    def test_target_kind_rejects_non_subagent_value(self) -> None:
        """Codex P2-1: target_kind 是 Literal[SUBAGENT]，反序列化时 worker 等值必须被拒绝。"""
        from pydantic import ValidationError

        d = _make_delegation()
        payload = json.loads(d.model_dump_json())
        for bad_value in ("worker", "main"):
            payload_bad = dict(payload, target_kind=bad_value)
            with pytest.raises(ValidationError):
                SubagentDelegation.model_validate(payload_bad)

    def test_required_ids_reject_empty_string(self) -> None:
        """Codex P2-2: 必填 ID 必须 min_length=1，空字符串被拒（与 Work/DelegationEnvelope 一致）。"""
        from pydantic import ValidationError

        for required_field in (
            "delegation_id",
            "parent_task_id",
            "parent_work_id",
            "child_task_id",
            "caller_agent_runtime_id",
            "caller_project_id",
            "spawned_by",
        ):
            with pytest.raises(ValidationError) as exc_info:
                _make_delegation(**{required_field: ""})
            error_messages = str(exc_info.value)
            assert required_field in error_messages or "min_length" in error_messages or "at least 1" in error_messages

    def test_child_agent_session_id_can_be_empty_or_none(self) -> None:
        """child_agent_session_id 是可选字段（None），但若提供必须非空（与 P2-2 一致）。

        当前 spec 允许 None（spawn 失败场景）。空字符串视为非法（同必填 ID 一致性原则）。
        但因为 child_agent_session_id 类型是 str | None，默认 None，未加 min_length 约束以保持灵活——
        cleanup hook 已对 None 做 early return。空字符串场景由 Phase B / E 实施时显式传 None 而非 ''。
        """
        # None 合法
        d = _make_delegation(child_agent_session_id=None)
        assert d.child_agent_session_id is None
        # 非空字符串合法
        d2 = _make_delegation(child_agent_session_id="session-123")
        assert d2.child_agent_session_id == "session-123"
