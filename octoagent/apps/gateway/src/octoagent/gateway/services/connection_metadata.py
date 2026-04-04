"""Feature 043: connection metadata trust-boundary helpers。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from octoagent.core.models import Event, EventType, TurnExecutorKind

TURN_SCOPED_CONTROL_KEYS = frozenset(
    {
        "agent_profile_id",
        "requested_worker_profile_id",
        "delegation_target_profile_id",
        "turn_executor_kind",
        "requested_worker_profile_version",
        "effective_worker_snapshot_id",
        "tool_profile",
        "requested_worker_type",
        "target_kind",
        "project_id",
        "approval_id",
        "approval_token",
        "delegation_pause",
    }
)

TASK_SCOPED_CONTROL_KEYS = frozenset(
    {
        "session_owner_profile_id",
        "inherited_context_owner_profile_id",
        "session_id",
        "thread_id",
        "parent_task_id",
        "parent_work_id",
        "spawned_by",
        "source_agent_runtime_id",
        "source_agent_session_id",
        "child_title",
        "worker_plan_id",
        "retry_source_task_id",
        "retry_action_source",
        "retry_actor_id",
    }
)

CONTROL_METADATA_KEYS = TURN_SCOPED_CONTROL_KEYS | TASK_SCOPED_CONTROL_KEYS

PROMPT_SAFE_CONTROL_KEYS = frozenset(
    {
        "agent_profile_id",
        "session_owner_profile_id",
        "requested_worker_profile_id",
        "delegation_target_profile_id",
        "turn_executor_kind",
        "requested_worker_type",
        "selected_worker_type",
        "target_kind",
        "tool_profile",
        "project_id",
        "work_id",
        "parent_task_id",
        "parent_work_id",
        "pipeline_run_id",
        "route_reason",
    }
)


def normalize_input_metadata(raw: Mapping[str, Any] | None) -> dict[str, str]:
    """将输入 metadata 归一化为字符串字典。"""

    if raw is None:
        return {}
    normalized: dict[str, str] = {}
    for key, value in raw.items():
        name = str(key).strip()
        if not name or value is None:
            continue
        normalized[name] = str(value)
    return normalized


def normalize_control_metadata(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    """对 trusted control metadata 做轻量归一化。"""

    if raw is None:
        return {}
    normalized: dict[str, Any] = {}
    for key, value in raw.items():
        name = str(key).strip()
        if not name or name not in CONTROL_METADATA_KEYS:
            continue
        if value is None:
            normalized[name] = None
            continue
        if name == "requested_worker_profile_version":
            if isinstance(value, bool):
                normalized[name] = int(value)
                continue
            try:
                normalized[name] = int(value)
            except (TypeError, ValueError):
                continue
            continue
        if isinstance(value, str):
            normalized[name] = value.strip()
            continue
        normalized[name] = value
    return normalized


def input_metadata_from_payload(payload: Mapping[str, Any] | None) -> dict[str, str]:
    if payload is None:
        return {}
    raw = payload.get("metadata", {})
    if not isinstance(raw, Mapping):
        return {}
    return normalize_input_metadata(raw)


def control_metadata_from_payload(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        return {}
    raw = payload.get("control_metadata", {})
    if not isinstance(raw, Mapping):
        return {}
    return normalize_control_metadata(raw)


def merge_control_metadata(events: Iterable[Event]) -> dict[str, Any]:
    """按 turn/task 生命周期合并 USER_MESSAGE control metadata。"""

    user_events = [event for event in events if event.type == EventType.USER_MESSAGE]
    if not user_events:
        return {}

    latest_payload = user_events[-1].payload
    latest_control = control_metadata_from_payload(latest_payload)

    merged: dict[str, Any] = {}
    cleared_turn: set[str] = set()
    cleared_task: set[str] = set()

    for key in TURN_SCOPED_CONTROL_KEYS:
        if key not in latest_control:
            continue
        value = latest_control[key]
        if _is_clear_marker(value):
            cleared_turn.add(key)
            continue
        merged[key] = value

    for event in reversed(user_events):
        control = control_metadata_from_payload(event.payload)
        if not control:
            continue
        pending = TASK_SCOPED_CONTROL_KEYS - merged.keys() - cleared_task
        if not pending:
            break
        for key in pending:
            if key not in control:
                continue
            value = control[key]
            if _is_clear_marker(value):
                cleared_task.add(key)
                continue
            merged[key] = value

    return merged


def summarize_control_metadata_for_prompt(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """生成可进入 runtime system block 的安全摘要。"""

    if metadata is None:
        return {}
    summary: dict[str, Any] = {}
    for key in PROMPT_SAFE_CONTROL_KEYS:
        if key not in metadata:
            continue
        value = metadata[key]
        if _is_clear_marker(value):
            continue
        if isinstance(value, Mapping):
            summary[key] = {
                "keys": sorted(str(item) for item in value)[:8],
                "count": len(value),
            }
            continue
        if isinstance(value, list):
            normalized = [str(item).strip() for item in value if str(item).strip()]
            summary[key] = normalized[:8]
            continue
        if isinstance(value, str):
            text = value.strip()
            if not text:
                continue
            summary[key] = text
            continue
        summary[key] = value
    return summary


def resolve_session_owner_profile_id(metadata: Mapping[str, Any] | None) -> str:
    """解析当前会话 owner profile。"""

    if metadata is None:
        return ""
    return (
        resolve_explicit_session_owner_profile_id(metadata)
        or str(metadata.get("agent_profile_id", "")).strip()
    )


def resolve_explicit_session_owner_profile_id(metadata: Mapping[str, Any] | None) -> str:
    """只解析新语义中的会话 owner profile，不回退 legacy 字段。"""

    if metadata is None:
        return ""
    return str(metadata.get("session_owner_profile_id", "")).strip()


def resolve_delegation_target_profile_id(metadata: Mapping[str, Any] | None) -> str:
    """解析本轮显式 delegation target profile。"""

    if metadata is None:
        return ""
    return (
        resolve_explicit_delegation_target_profile_id(metadata)
        or str(metadata.get("requested_worker_profile_id", "")).strip()
    )


def resolve_explicit_delegation_target_profile_id(
    metadata: Mapping[str, Any] | None,
) -> str:
    """只解析新语义中的显式 delegation target，不回退 legacy 字段。"""

    if metadata is None:
        return ""
    return str(metadata.get("delegation_target_profile_id", "")).strip()


def resolve_turn_executor_kind(
    metadata: Mapping[str, Any] | None,
) -> TurnExecutorKind | None:
    """解析当前轮次执行者语义。"""

    if metadata is None:
        return None
    value = metadata.get("turn_executor_kind")
    if isinstance(value, TurnExecutorKind):
        return value
    normalized = str(value or "").strip().lower()
    if not normalized:
        return None
    try:
        return TurnExecutorKind(normalized)
    except ValueError:
        return None


def _is_clear_marker(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())
