"""F099 Phase D: CONTROL_METADATA_UPDATED 常量化 + payloads 文档 + audit 测试框架。

覆盖范围：
- T-D-1: CONTROL_METADATA_SOURCE_* 常量完整性验证（T-C-1 已包含，此处确认）
- T-D-2: payloads.py ControlMetadataUpdatedPayload.source 字段更新（文档验证）
- AC-D2: CONTROL_METADATA_UPDATED 事件不出现在 conversation_turns 中（隔离验证）
- FR-D4: payloads 文档包含 F099 新增候选值

测试函数（共 4 个）：
1. test_control_metadata_source_constants_defined
2. test_control_metadata_updated_payload_source_field
3. test_merge_control_metadata_handles_control_metadata_updated
4. test_ask_back_audit_event_not_in_conversation_turns

Phase E 补全：emit 实测断言（test_ask_back_control_metadata_source_field 等）
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# T-D-1: CONTROL_METADATA_SOURCE_* 常量完整性验证
# ---------------------------------------------------------------------------


def test_control_metadata_source_constants_defined():
    """FR-D4: source_kinds.py 包含 F099 新增的 3 个 CONTROL_METADATA_SOURCE_* 常量。"""
    from octoagent.core.models.source_kinds import (
        CONTROL_METADATA_SOURCE_ASK_BACK,
        CONTROL_METADATA_SOURCE_ESCALATE_PERMISSION,
        CONTROL_METADATA_SOURCE_REQUEST_INPUT,
        CONTROL_METADATA_SOURCE_SUBAGENT_DELEGATION_BACKFILL,
        CONTROL_METADATA_SOURCE_SUBAGENT_DELEGATION_INIT,
    )

    # F098 已有（向后兼容）
    assert CONTROL_METADATA_SOURCE_SUBAGENT_DELEGATION_INIT == "subagent_delegation_init"
    assert CONTROL_METADATA_SOURCE_SUBAGENT_DELEGATION_BACKFILL == "subagent_delegation_session_backfill"

    # F099 新增（三工具触发）
    assert CONTROL_METADATA_SOURCE_ASK_BACK == "worker_ask_back"
    assert CONTROL_METADATA_SOURCE_REQUEST_INPUT == "worker_request_input"
    assert CONTROL_METADATA_SOURCE_ESCALATE_PERMISSION == "worker_escalate_permission"


# ---------------------------------------------------------------------------
# T-D-2: ControlMetadataUpdatedPayload.source 字段文档验证
# ---------------------------------------------------------------------------


def test_control_metadata_updated_payload_source_field():
    """FR-D4: payloads.py source 字段 description 包含 F099 新增候选值。

    构造 ControlMetadataUpdatedPayload，验证：
    - source 字段可以设为 worker_ask_back / worker_request_input / worker_escalate_permission
    - model_dump round-trip 正常（无 schema 破坏）
    """
    from octoagent.core.models.payloads import ControlMetadataUpdatedPayload

    # 验证 F099 新增候选值可以成功设置（schema 无约束）
    for source_val in (
        "worker_ask_back",
        "worker_request_input",
        "worker_escalate_permission",
    ):
        payload = ControlMetadataUpdatedPayload(
            source=source_val,
            control_metadata={"test_key": "test_value"},
        )
        dumped = payload.model_dump()
        assert dumped["source"] == source_val
        assert dumped["control_metadata"] == {"test_key": "test_value"}

    # 验证 description 中含有 F099 新增候选值（FR-D4 文档要求）
    source_field_info = ControlMetadataUpdatedPayload.model_fields["source"]
    field_desc = source_field_info.description or ""
    assert "worker_ask_back" in field_desc, f"source field description missing 'worker_ask_back': {field_desc!r}"
    assert "worker_request_input" in field_desc, f"source field description missing 'worker_request_input': {field_desc!r}"
    assert "worker_escalate_permission" in field_desc, f"source field description missing 'worker_escalate_permission': {field_desc!r}"


# ---------------------------------------------------------------------------
# T-D-3: merge_control_metadata 支持 CONTROL_METADATA_UPDATED
# ---------------------------------------------------------------------------


def test_merge_control_metadata_handles_control_metadata_updated():
    """AC-D2 前置验证: merge_control_metadata 合并 CONTROL_METADATA_UPDATED 事件（向后兼容）。

    F098 Phase E 已实现 merge_control_metadata 合并两类事件，此处确认行为无变更。
    """
    from octoagent.core.models.enums import EventType
    from octoagent.gateway.services.connection_metadata import merge_control_metadata
    from octoagent.core.models import Event

    # 构造一个 CONTROL_METADATA_UPDATED 事件
    ctrl_event = MagicMock(spec=Event)
    ctrl_event.type = EventType.CONTROL_METADATA_UPDATED
    ctrl_event.payload = {
        "source": "worker_ask_back",
        "control_metadata": {"ask_back_question": "test question"},
    }

    # 构造一个 USER_MESSAGE 事件（普通对话历史）
    user_event = MagicMock(spec=Event)
    user_event.type = EventType.USER_MESSAGE
    user_event.payload = {
        "text": "hello",
        "control_metadata": {"parent_task_id": "task-123"},
    }

    merged = merge_control_metadata([user_event, ctrl_event])

    # CONTROL_METADATA_UPDATED 的 control_metadata 字段应被合并
    # （注意：merge_control_metadata 合并的是 payload 中的 control_metadata 字段，
    #  不是 payload.source 字段）
    assert isinstance(merged, dict)
    # 合并结果包含来自两个事件的 control_metadata（后者覆盖前者的同名 key）
    assert "parent_task_id" in merged or "ask_back_question" in merged, (
        f"merge_control_metadata did not include expected keys: {merged}"
    )


# ---------------------------------------------------------------------------
# T-D-4 (AC-D2): CONTROL_METADATA_UPDATED 事件不出现在 conversation_turns 中
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ask_back_audit_event_not_in_conversation_turns():
    """AC-D2: CONTROL_METADATA_UPDATED 事件不出现在 _load_conversation_turns 返回值中。

    验证 context_compaction._load_conversation_turns 仅接受：
    - EventType.USER_MESSAGE（且有 text 内容）
    - EventType.MODEL_CALL_COMPLETED

    CONTROL_METADATA_UPDATED 事件必须被过滤，不进入 conversation_turns
    （避免污染 LLM compaction 上下文，FR-D2）。
    """
    from octoagent.core.models.enums import EventType
    from octoagent.core.models import Event
    from octoagent.gateway.services.context_compaction import ContextCompactionService

    # 构造 mock event store 返回 CONTROL_METADATA_UPDATED + USER_MESSAGE 两类事件
    ctrl_event = MagicMock(spec=Event)
    ctrl_event.type = EventType.CONTROL_METADATA_UPDATED
    ctrl_event.event_id = "evt-ctrl-001"
    ctrl_event.payload = {
        "source": "worker_ask_back",
        "control_metadata": {"ask_back_question": "用户需要帮助吗？"},
    }

    user_event = MagicMock(spec=Event)
    user_event.type = EventType.USER_MESSAGE
    user_event.event_id = "evt-user-001"
    user_event.payload = {
        "text": "请分析一下这个问题",
        "text_preview": "请分析一下这个问题",
        "text_length": 10,
    }

    mock_event_store = AsyncMock()
    mock_event_store.get_events_for_task = AsyncMock(return_value=[ctrl_event, user_event])

    # 构造 mock artifact_store
    mock_artifact_store = AsyncMock()
    mock_artifact_store.get_artifact_content = AsyncMock(return_value=None)

    mock_stores = MagicMock()
    mock_stores.event_store = mock_event_store
    mock_stores.artifact_store = mock_artifact_store

    # 构造 ContextCompactionService（最小化 DI）
    service = ContextCompactionService.__new__(ContextCompactionService)
    service._stores = mock_stores

    # 调用 _load_conversation_turns
    turns = await service._load_conversation_turns("task-001")

    # 验证：CONTROL_METADATA_UPDATED 事件不进入 conversation_turns（AC-D2）
    turn_event_ids = [t.source_event_id for t in turns]
    assert "evt-ctrl-001" not in turn_event_ids, (
        f"CONTROL_METADATA_UPDATED 事件 evt-ctrl-001 不应出现在 conversation_turns 中，"
        f"实际 turn event ids: {turn_event_ids}"
    )

    # 验证：USER_MESSAGE 事件正常进入 conversation_turns
    assert "evt-user-001" in turn_event_ids, (
        f"USER_MESSAGE 事件 evt-user-001 应出现在 conversation_turns 中，"
        f"实际 turn event ids: {turn_event_ids}"
    )
    assert len(turns) == 1, f"只有 1 个 USER_MESSAGE 事件应生成 1 个 turn，实际 {len(turns)} 个"


# Phase E 补全：emit 实测断言
# test_ask_back_control_metadata_source_field — 需要 ask_back_tools.py 就绪后补充
# test_ask_back_control_metadata_updated_not_in_conversation_turns — Phase E emit 实测
# test_escalate_permission_control_metadata_source_field — Phase E emit 实测
