"""F098 Phase E：CONTROL_METADATA_UPDATED 事件引入 + merge_control_metadata 合并两类事件。

主要场景：
1. ControlMetadataUpdatedPayload round-trip（model_dump / model_validate）
2. merge_control_metadata 合并 USER_MESSAGE + CONTROL_METADATA_UPDATED 两类事件
3. CONTROL_METADATA_UPDATED 事件正确 emit + 不污染 conversation history
4. 向后兼容：历史 USER_MESSAGE 含 subagent_delegation 仍可读
5. _emit_subagent_delegation_init_if_needed 改用 CONTROL_METADATA_UPDATED 验证
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from ulid import ULID

from octoagent.core.models import (
    ActorType,
    ControlMetadataUpdatedPayload,
    Event,
    EventCausality,
    EventType,
    UserMessagePayload,
)
from octoagent.gateway.services.connection_metadata import merge_control_metadata


# ---- 1. ControlMetadataUpdatedPayload round-trip ----


def test_control_metadata_updated_payload_round_trip():
    """ControlMetadataUpdatedPayload model_dump / model_validate round-trip。"""
    payload = ControlMetadataUpdatedPayload(
        control_metadata={
            "subagent_delegation": {"delegation_id": "deleg-123"},
            "target_kind": "subagent",
            "spawned_by": "owner",
        },
        source="subagent_delegation_init",
    )
    serialized = payload.model_dump(mode="json")
    assert serialized["control_metadata"]["subagent_delegation"]["delegation_id"] == "deleg-123"
    assert serialized["control_metadata"]["target_kind"] == "subagent"
    assert serialized["source"] == "subagent_delegation_init"

    rebuilt = ControlMetadataUpdatedPayload.model_validate(serialized)
    assert rebuilt.control_metadata == payload.control_metadata
    assert rebuilt.source == payload.source


def test_control_metadata_updated_payload_default_source():
    """source 默认为空字符串。"""
    payload = ControlMetadataUpdatedPayload(control_metadata={"foo": "bar"})
    assert payload.source == ""


def test_control_metadata_updated_payload_no_text_field():
    """ControlMetadataUpdatedPayload 不应有 text / text_preview 字段（避免污染 conversation history）。"""
    payload = ControlMetadataUpdatedPayload(control_metadata={})
    serialized = payload.model_dump()
    assert "text" not in serialized
    assert "text_preview" not in serialized
    assert "text_length" not in serialized
    assert "attachment_count" not in serialized


# ---- 2. merge_control_metadata 合并两类事件 ----


def _build_event(
    *, task_id: str, seq: int, event_type: EventType, control_metadata: dict, text: str = ""
) -> Event:
    """辅助：构造测试事件。"""
    if event_type is EventType.USER_MESSAGE:
        payload = UserMessagePayload(
            text_preview=text[:200] if text else "",
            text_length=len(text),
            text=text,
            control_metadata=control_metadata,
        ).model_dump()
    elif event_type is EventType.CONTROL_METADATA_UPDATED:
        payload = ControlMetadataUpdatedPayload(
            control_metadata=control_metadata,
            source="test",
        ).model_dump()
    else:  # pragma: no cover - 不预期其他类型
        raise AssertionError(f"unexpected event_type {event_type}")
    return Event(
        event_id=str(ULID()),
        task_id=task_id,
        task_seq=seq,
        ts=datetime.now(tz=UTC),
        type=event_type,
        actor=ActorType.SYSTEM,
        payload=payload,
        trace_id=f"trace-{task_id}",
        causality=EventCausality(),
    )


def test_merge_control_metadata_only_user_message():
    """仅 USER_MESSAGE：行为与 baseline 一致（向后兼容）。"""
    events = [
        _build_event(
            task_id="t1",
            seq=1,
            event_type=EventType.USER_MESSAGE,
            control_metadata={"target_kind": "subagent", "spawned_by": "owner"},
        ),
    ]
    merged = merge_control_metadata(events)
    assert merged.get("target_kind") == "subagent"
    assert merged.get("spawned_by") == "owner"


def test_merge_control_metadata_only_control_metadata_updated():
    """仅 CONTROL_METADATA_UPDATED：F098 新路径。"""
    events = [
        _build_event(
            task_id="t1",
            seq=1,
            event_type=EventType.CONTROL_METADATA_UPDATED,
            control_metadata={"target_kind": "subagent", "spawned_by": "owner"},
        ),
    ]
    merged = merge_control_metadata(events)
    assert merged.get("target_kind") == "subagent"
    assert merged.get("spawned_by") == "owner"


def test_merge_control_metadata_mixed_events_latest_wins_turn_scoped():
    """混合事件：CONTROL_METADATA_UPDATED 在 USER_MESSAGE 之后，TURN_SCOPED 取 latest。"""
    events = [
        _build_event(
            task_id="t1",
            seq=1,
            event_type=EventType.USER_MESSAGE,
            control_metadata={"target_kind": "main", "tool_profile": "research"},
        ),
        _build_event(
            task_id="t1",
            seq=2,
            event_type=EventType.CONTROL_METADATA_UPDATED,
            control_metadata={"target_kind": "subagent"},
        ),
    ]
    merged = merge_control_metadata(events)
    # TURN_SCOPED 取最新事件（CONTROL_METADATA_UPDATED）
    assert merged.get("target_kind") == "subagent"


def test_merge_control_metadata_mixed_events_task_scoped_backfilled():
    """混合事件：CONTROL_METADATA_UPDATED 在前，USER_MESSAGE 在后，TASK_SCOPED 倒序回溯。"""
    events = [
        _build_event(
            task_id="t1",
            seq=1,
            event_type=EventType.CONTROL_METADATA_UPDATED,
            control_metadata={"parent_task_id": "parent-abc", "spawned_by": "owner"},
        ),
        _build_event(
            task_id="t1",
            seq=2,
            event_type=EventType.USER_MESSAGE,
            control_metadata={"tool_profile": "research"},
        ),
    ]
    merged = merge_control_metadata(events)
    # TASK_SCOPED 倒序回溯仍能找到（CONTROL_METADATA_UPDATED 也参与回溯）
    assert merged.get("parent_task_id") == "parent-abc"
    assert merged.get("spawned_by") == "owner"
    # latest 事件的 TURN_SCOPED 字段
    assert merged.get("tool_profile") == "research"


def test_merge_control_metadata_empty_events():
    """无相关事件返回空 dict。"""
    assert merge_control_metadata([]) == {}


def test_merge_control_metadata_ignores_other_event_types():
    """STATE_TRANSITION 等其他事件类型不参与合并。"""
    events = [
        _build_event(
            task_id="t1",
            seq=1,
            event_type=EventType.USER_MESSAGE,
            control_metadata={"target_kind": "subagent"},
        ),
        # 模拟一个 STATE_TRANSITION 事件（payload 没有 control_metadata 也不该被取）
        Event(
            event_id=str(ULID()),
            task_id="t1",
            task_seq=2,
            ts=datetime.now(tz=UTC),
            type=EventType.STATE_TRANSITION,
            actor=ActorType.SYSTEM,
            payload={"from_status": "RUNNING", "to_status": "COMPLETED", "reason": ""},
            trace_id="trace-t1",
            causality=EventCausality(),
        ),
    ]
    merged = merge_control_metadata(events)
    assert merged.get("target_kind") == "subagent"


# ---- 3. 向后兼容：历史 USER_MESSAGE 含 subagent_delegation 仍可读 ----


def test_merge_control_metadata_legacy_user_message_with_subagent_delegation():
    """F097 baseline 的 USER_MESSAGE 含 subagent_delegation control_metadata 在 F098 后仍可读。"""
    legacy_events = [
        _build_event(
            task_id="t1",
            seq=1,
            event_type=EventType.USER_MESSAGE,
            control_metadata={
                "subagent_delegation": {
                    "delegation_id": "legacy-deleg",
                    "parent_task_id": "parent",
                    "child_task_id": "child",
                },
                "target_kind": "subagent",
            },
            text="legacy task input",
        ),
    ]
    merged = merge_control_metadata(legacy_events)
    assert merged.get("target_kind") == "subagent"
    assert merged.get("subagent_delegation", {}).get("delegation_id") == "legacy-deleg"


def test_merge_control_metadata_mixed_legacy_and_f098_events():
    """混合：F097 baseline USER_MESSAGE 在前，F098 CONTROL_METADATA_UPDATED 在后。"""
    events = [
        _build_event(
            task_id="t1",
            seq=1,
            event_type=EventType.USER_MESSAGE,
            control_metadata={
                "subagent_delegation": {"delegation_id": "x", "child_agent_session_id": None},
                "target_kind": "subagent",
            },
        ),
        _build_event(
            task_id="t1",
            seq=2,
            event_type=EventType.CONTROL_METADATA_UPDATED,
            control_metadata={
                "subagent_delegation": {
                    "delegation_id": "x",
                    "child_agent_session_id": "session-new",
                },
                "target_kind": "subagent",
            },
        ),
    ]
    merged = merge_control_metadata(events)
    # latest CONTROL_METADATA_UPDATED 写入 child_agent_session_id
    assert merged.get("subagent_delegation", {}).get("child_agent_session_id") == "session-new"
    assert merged.get("target_kind") == "subagent"


# ---- 4. 不污染 conversation history（_load_conversation_turns 集成验证留集成测）----


def test_control_metadata_updated_event_payload_no_pollution_keys():
    """CONTROL_METADATA_UPDATED 事件 payload 不含 text 类字段（防止 _load_conversation_turns 污染）。"""
    payload = ControlMetadataUpdatedPayload(
        control_metadata={"any": "value"},
        source="test",
    ).model_dump()
    # 关键：context_compaction._load_conversation_turns 检查 payload.get("text") 和 text_preview
    # 此 payload 不含这些字段 → 即便 _load_conversation_turns 误处理也不会有 turn
    assert payload.get("text", "") == ""
    assert payload.get("text_preview", "") == ""
