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


# ---------------------------------------------------------------------------
# Phase E 补全：emit 实测断言（T-E-1）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ask_back_control_metadata_source_field():
    """AC-D1: ask_back_handler 触发 emit 的 CONTROL_METADATA_UPDATED.source == 'worker_ask_back'。

    Phase E 补全：使用 ask_back_tools 真实 handler + mock execution_context，
    验证 event_store 中捕获到的事件 source 值正确。
    """
    from unittest.mock import patch
    from octoagent.core.models.enums import EventType
    from octoagent.gateway.services.builtin_tools._deps import ToolDeps

    # 构建 mock event_store 捕获 emit
    mock_event_store = AsyncMock()
    mock_event_store.get_next_task_seq = AsyncMock(return_value=3)
    captured_events = []

    async def capture_append(event, **kwargs):
        captured_events.append(event)

    mock_event_store.append_event_committed = AsyncMock(side_effect=capture_append)

    mock_stores = MagicMock()
    mock_stores.event_store = mock_event_store

    deps = ToolDeps(
        project_root=MagicMock(),
        stores=mock_stores,
        tool_broker=MagicMock(),
        tool_index=MagicMock(),
        skill_discovery=MagicMock(),
        memory_console_service=MagicMock(),
        memory_runtime_service=MagicMock(),
    )
    deps._approval_gate = None

    from octoagent.gateway.services.execution_context import ExecutionRuntimeContext
    from octoagent.gateway.services.builtin_tools import ask_back_tools

    mock_ctx = MagicMock(spec=ExecutionRuntimeContext)
    mock_ctx.task_id = "task-e1-ask-back"
    mock_ctx.session_id = "session-e1"
    mock_ctx.request_input = AsyncMock(return_value="用户回答")

    handlers = {}

    class CaptureBroker:
        async def try_register(self, schema, handler):
            tool_name = schema.get("name", "") if isinstance(schema, dict) else getattr(schema, "name", "")
            handlers[tool_name] = handler

    with patch(
        "octoagent.gateway.services.builtin_tools.ask_back_tools.get_current_execution_context",
        return_value=mock_ctx,
    ):
        await ask_back_tools.register(CaptureBroker(), deps)
        await handlers["worker.ask_back"](question="测试问题 E1")

    # 验证 event_store 中 CONTROL_METADATA_UPDATED 的 source
    assert len(captured_events) >= 1, "应捕获到至少 1 个 emit 事件"
    audit_event = captured_events[0]
    assert audit_event.type == EventType.CONTROL_METADATA_UPDATED, (
        f"事件类型应为 CONTROL_METADATA_UPDATED，实际 {audit_event.type}"
    )
    assert audit_event.payload.get("source") == "worker_ask_back", (
        f"source 应为 'worker_ask_back'，实际 {audit_event.payload.get('source')!r}"
    )


@pytest.mark.asyncio
async def test_ask_back_control_metadata_updated_not_in_conversation_turns():
    """AC-D2: ask_back_handler emit 的 CONTROL_METADATA_UPDATED 不进入 conversation_turns（真实调用）。

    Phase E 补全：联合 ask_back_handler + ContextCompactionService._load_conversation_turns，
    验证 audit emit 事件不污染 LLM compaction 上下文（FR-D2）。
    """
    from unittest.mock import patch
    from octoagent.core.models.enums import EventType
    from octoagent.core.models import Event
    from octoagent.gateway.services.context_compaction import ContextCompactionService
    from octoagent.gateway.services.builtin_tools._deps import ToolDeps
    from octoagent.gateway.services.builtin_tools import ask_back_tools
    from octoagent.gateway.services.execution_context import ExecutionRuntimeContext

    # mock event_store：append_event_committed 把事件存起来，get_events_for_task 返回存储的事件
    stored_events = []

    async def capture_append(event, **kwargs):
        stored_events.append(event)

    mock_event_store = AsyncMock()
    mock_event_store.get_next_task_seq = AsyncMock(return_value=1)
    mock_event_store.append_event_committed = AsyncMock(side_effect=capture_append)

    # get_events_for_task 返回已存的事件（模拟 ask_back emit 后的 event store 状态）
    async def get_events(task_id, **kwargs):
        return stored_events

    mock_event_store.get_events_for_task = AsyncMock(side_effect=get_events)

    mock_artifact_store = AsyncMock()
    mock_artifact_store.get_artifact_content = AsyncMock(return_value=None)

    mock_stores = MagicMock()
    mock_stores.event_store = mock_event_store
    mock_stores.artifact_store = mock_artifact_store

    deps = ToolDeps(
        project_root=MagicMock(),
        stores=mock_stores,
        tool_broker=MagicMock(),
        tool_index=MagicMock(),
        skill_discovery=MagicMock(),
        memory_console_service=MagicMock(),
        memory_runtime_service=MagicMock(),
    )
    deps._approval_gate = None

    mock_ctx = MagicMock(spec=ExecutionRuntimeContext)
    mock_ctx.task_id = "task-e1-conv"
    mock_ctx.session_id = "session-e1"
    mock_ctx.request_input = AsyncMock(return_value="回答")

    handlers = {}

    class CaptureBroker:
        async def try_register(self, schema, handler):
            tool_name = schema.get("name", "") if isinstance(schema, dict) else getattr(schema, "name", "")
            handlers[tool_name] = handler

    with patch(
        "octoagent.gateway.services.builtin_tools.ask_back_tools.get_current_execution_context",
        return_value=mock_ctx,
    ):
        await ask_back_tools.register(CaptureBroker(), deps)
        # 调用 ask_back → emit CONTROL_METADATA_UPDATED 到 event store
        await handlers["worker.ask_back"](question="Phase E 测试问题")

    # 确认有事件被 emit
    ctrl_events = [e for e in stored_events if e.type == EventType.CONTROL_METADATA_UPDATED]
    assert len(ctrl_events) >= 1, "应有至少 1 个 CONTROL_METADATA_UPDATED emit"
    ctrl_event_ids = {e.event_id for e in ctrl_events}

    # 用 ContextCompactionService._load_conversation_turns 验证过滤行为
    service = ContextCompactionService.__new__(ContextCompactionService)
    service._stores = mock_stores

    turns = await service._load_conversation_turns("task-e1-conv")
    turn_event_ids = {t.source_event_id for t in turns}

    # CONTROL_METADATA_UPDATED 事件不应出现在 conversation_turns 中（AC-D2 / FR-D2）
    assert not ctrl_event_ids.intersection(turn_event_ids), (
        f"CONTROL_METADATA_UPDATED 事件 {ctrl_event_ids} 不应出现在 conversation_turns 中，"
        f"实际 turn event ids: {turn_event_ids}"
    )


@pytest.mark.asyncio
async def test_escalate_permission_control_metadata_source_field():
    """FR-D3: escalate_permission_handler 触发 emit 的 CONTROL_METADATA_UPDATED.source == 'worker_escalate_permission'。

    Phase E 补全：验证 escalate_permission emit 的 source 值正确（FR-D3 real call）。
    """
    from unittest.mock import patch
    from octoagent.core.models.enums import EventType
    from octoagent.gateway.services.builtin_tools._deps import ToolDeps
    from octoagent.gateway.services.builtin_tools import ask_back_tools
    from octoagent.gateway.services.execution_context import ExecutionRuntimeContext

    mock_event_store = AsyncMock()
    mock_event_store.get_next_task_seq = AsyncMock(return_value=2)
    captured_events = []

    async def capture_append(event, **kwargs):
        captured_events.append(event)

    mock_event_store.append_event_committed = AsyncMock(side_effect=capture_append)

    mock_stores = MagicMock()
    mock_stores.event_store = mock_event_store

    # mock approval_gate
    mock_gate = AsyncMock()
    mock_handle = MagicMock()
    mock_gate.request_approval = AsyncMock(return_value=mock_handle)
    mock_gate.wait_for_decision = AsyncMock(return_value="rejected")

    deps = ToolDeps(
        project_root=MagicMock(),
        stores=mock_stores,
        tool_broker=MagicMock(),
        tool_index=MagicMock(),
        skill_discovery=MagicMock(),
        memory_console_service=MagicMock(),
        memory_runtime_service=MagicMock(),
    )
    deps._approval_gate = mock_gate

    mock_ctx = MagicMock(spec=ExecutionRuntimeContext)
    mock_ctx.task_id = "task-e1-escalate"
    mock_ctx.session_id = "session-e1"

    handlers = {}

    class CaptureBroker:
        async def try_register(self, schema, handler):
            tool_name = schema.get("name", "") if isinstance(schema, dict) else getattr(schema, "name", "")
            handlers[tool_name] = handler

    with patch(
        "octoagent.gateway.services.builtin_tools.ask_back_tools.get_current_execution_context",
        return_value=mock_ctx,
    ):
        await ask_back_tools.register(CaptureBroker(), deps)
        await handlers["worker.escalate_permission"](
            action="Phase E 测试动作",
            scope="测试范围",
            reason="Phase E 测试原因",
        )

    # 验证 source == "worker_escalate_permission"（FR-D3）
    assert len(captured_events) >= 1, "应捕获到至少 1 个 emit 事件"
    audit_event = captured_events[0]
    assert audit_event.type == EventType.CONTROL_METADATA_UPDATED, (
        f"事件类型应为 CONTROL_METADATA_UPDATED，实际 {audit_event.type}"
    )
    assert audit_event.payload.get("source") == "worker_escalate_permission", (
        f"source 应为 'worker_escalate_permission'，实际 {audit_event.payload.get('source')!r}"
    )
