"""F149 Task SSE Gateway/web adapter 有限帧合同。"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Literal

from octoagent.core.models import Event, TaskStatus
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

MAX_DIAGNOSTIC_DEPTH = 4
MAX_DIAGNOSTIC_ITEMS = 16
MAX_DIAGNOSTIC_STRING_LENGTH = 256
MAX_DIAGNOSTIC_BYTES = 4096
MAX_MODEL_ID_LENGTH = 512
MAX_MODEL_RESPONSE_LENGTH = 8192
MAX_MODEL_ERROR_LENGTH = 512
REDACTED_VALUE = "[REDACTED]"
TRUNCATED_VALUE = "[TRUNCATED]"

_SENSITIVE_KEY_PARTS = (
    "api_key",
    "authorization",
    "cookie",
    "credential",
    "password",
    "private_key",
    "secret",
    "token",
)
_SENSITIVE_TEXT_PATTERN = re.compile(
    r"(?i)(?:secret|token|password|api[_-]?key|authorization|credential|"
    r"cookie|private[_-]?key|bearer\s+\S+|sk-[a-z0-9_-]{8,})"
)

RawEventPayload = dict[str, JsonValue]


class F149StateTransitionPayload(BaseModel):
    """TaskDetail 实际消费的状态变更字段。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["state_transition"] = "state_transition"
    to_status: TaskStatus


class F149ArtifactRefreshPayload(BaseModel):
    """Artifact 事件只触发重新读取详情，不转发原始 payload。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["artifact_refresh"] = "artifact_refresh"
    refresh_artifacts: Literal[True] = True


class F149ModelCallStartedPayload(BaseModel):
    """Chat 只消费调用归属，不接收原始请求、模型或 token 数据。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["model_call_started"] = "model_call_started"
    skill_id: str | None = Field(default=None, max_length=MAX_MODEL_ID_LENGTH)
    artifact_ref: str | None = Field(default=None, max_length=MAX_MODEL_ID_LENGTH)


class F149ModelCallCompletedPayload(BaseModel):
    """Chat 用户可见回复的有限投影。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["model_call_completed"] = "model_call_completed"
    skill_id: str | None = Field(default=None, max_length=MAX_MODEL_ID_LENGTH)
    artifact_ref: str | None = Field(default=None, max_length=MAX_MODEL_ID_LENGTH)
    response_summary: str = Field(min_length=1, max_length=MAX_MODEL_RESPONSE_LENGTH)


class F149ModelCallFailedPayload(BaseModel):
    """Chat 失败提示的有限投影。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["model_call_failed"] = "model_call_failed"
    skill_id: str | None = Field(default=None, max_length=MAX_MODEL_ID_LENGTH)
    artifact_ref: str | None = Field(default=None, max_length=MAX_MODEL_ID_LENGTH)
    error: str = Field(min_length=1, max_length=MAX_MODEL_ERROR_LENGTH)


class F149SafeDiagnosticPayload(BaseModel):
    """已净化且有界的 Advanced 诊断数据。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["diagnostic"] = "diagnostic"
    source_type: str
    diagnostic: JsonValue
    truncated: bool


F149TaskSSEPayload = Annotated[
    F149StateTransitionPayload
    | F149ArtifactRefreshPayload
    | F149ModelCallStartedPayload
    | F149ModelCallCompletedPayload
    | F149ModelCallFailedPayload
    | F149SafeDiagnosticPayload,
    Field(discriminator="kind"),
]


class F149TaskSSEFrame(BaseModel):
    """Gateway 对 Web 输出的唯一 Task SSE frame。"""

    model_config = ConfigDict(extra="forbid")

    event_id: str
    task_id: str
    task_seq: int = Field(ge=0)
    ts: str
    type: str
    actor: str
    payload: F149TaskSSEPayload
    final: bool


@dataclass
class _DiagnosticState:
    truncated: bool = False


def _is_sensitive_key(key: str) -> bool:
    normalized = key.casefold().replace("-", "_")
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def _sanitize_string(value: str, state: _DiagnosticState) -> str:
    if _SENSITIVE_TEXT_PATTERN.search(value):
        return REDACTED_VALUE
    if len(value) <= MAX_DIAGNOSTIC_STRING_LENGTH:
        return value
    state.truncated = True
    return value[:MAX_DIAGNOSTIC_STRING_LENGTH] + TRUNCATED_VALUE


def _sanitize_diagnostic(
    value: JsonValue,
    *,
    depth: int,
    state: _DiagnosticState,
) -> JsonValue:
    if isinstance(value, str):
        sanitized: JsonValue = _sanitize_string(value, state)
    elif value is None or isinstance(value, bool | int):
        sanitized = value
    elif isinstance(value, float):
        if math.isfinite(value):
            sanitized = value
        else:
            state.truncated = True
            sanitized = TRUNCATED_VALUE
    elif depth >= MAX_DIAGNOSTIC_DEPTH:
        state.truncated = True
        sanitized = TRUNCATED_VALUE
    elif isinstance(value, list):
        if len(value) > MAX_DIAGNOSTIC_ITEMS:
            state.truncated = True
        sanitized = [
            _sanitize_diagnostic(item, depth=depth + 1, state=state)
            for item in value[:MAX_DIAGNOSTIC_ITEMS]
        ]
    elif isinstance(value, dict):
        keys = sorted(value)[:MAX_DIAGNOSTIC_ITEMS]
        if len(value) > MAX_DIAGNOSTIC_ITEMS:
            state.truncated = True
        sanitized = {
            key: (
                REDACTED_VALUE
                if _is_sensitive_key(key)
                else _sanitize_diagnostic(
                    value[key],
                    depth=depth + 1,
                    state=state,
                )
            )
            for key in keys
        }
    else:
        state.truncated = True
        sanitized = TRUNCATED_VALUE
    return sanitized


def _safe_diagnostic(
    event_type: str,
    raw_payload: RawEventPayload,
) -> F149SafeDiagnosticPayload:
    state = _DiagnosticState()
    diagnostic = _sanitize_diagnostic(raw_payload, depth=0, state=state)
    payload = F149SafeDiagnosticPayload(
        source_type=event_type,
        diagnostic=diagnostic,
        truncated=state.truncated,
    )
    if (
        len(json.dumps(payload.model_dump(mode="json"), ensure_ascii=False).encode())
        <= MAX_DIAGNOSTIC_BYTES
    ):
        return payload
    return F149SafeDiagnosticPayload(
        source_type=event_type,
        diagnostic={
            "redacted": REDACTED_VALUE,
            "summary": TRUNCATED_VALUE,
        },
        truncated=True,
    )


def _optional_model_field(raw_payload: RawEventPayload, key: str) -> str | None:
    value = raw_payload.get(key)
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized[:MAX_MODEL_ID_LENGTH]


def _model_response_summary(raw_payload: RawEventPayload) -> str | None:
    value = raw_payload.get("response_summary")
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized[:MAX_MODEL_RESPONSE_LENGTH]


def _model_error(raw_payload: RawEventPayload) -> str | None:
    for key in ("user_message", "error", "error_message", "message"):
        value = raw_payload.get(key)
        if not isinstance(value, str):
            continue
        normalized = value.strip()
        if not normalized:
            continue
        if _SENSITIVE_TEXT_PATTERN.search(normalized):
            return "模型调用未能完成"
        return normalized[:MAX_MODEL_ERROR_LENGTH]
    return None


def _state_transition_payload(
    raw_payload: RawEventPayload,
) -> F149StateTransitionPayload | None:
    try:
        return F149StateTransitionPayload(
            to_status=raw_payload["to_status"],
        )
    except (KeyError, ValidationError):
        return None


def _model_completed_payload(
    raw_payload: RawEventPayload,
) -> F149ModelCallCompletedPayload | None:
    response_summary = _model_response_summary(raw_payload)
    if response_summary is None:
        return None
    return F149ModelCallCompletedPayload(
        skill_id=_optional_model_field(raw_payload, "skill_id"),
        artifact_ref=_optional_model_field(raw_payload, "artifact_ref"),
        response_summary=response_summary,
    )


def _model_failed_payload(
    raw_payload: RawEventPayload,
) -> F149ModelCallFailedPayload | None:
    error = _model_error(raw_payload)
    if error is None:
        return None
    return F149ModelCallFailedPayload(
        skill_id=_optional_model_field(raw_payload, "skill_id"),
        artifact_ref=_optional_model_field(raw_payload, "artifact_ref"),
        error=error,
    )


def _known_task_sse_payload(
    event_type: str,
    raw_payload: RawEventPayload,
) -> F149TaskSSEPayload | None:
    payload: F149TaskSSEPayload | None = None
    if event_type == "STATE_TRANSITION":
        payload = _state_transition_payload(raw_payload)
    elif event_type == "ARTIFACT_CREATED":
        payload = F149ArtifactRefreshPayload()
    elif event_type == "MODEL_CALL_STARTED":
        payload = F149ModelCallStartedPayload(
            skill_id=_optional_model_field(raw_payload, "skill_id"),
            artifact_ref=_optional_model_field(raw_payload, "artifact_ref"),
        )
    elif event_type == "MODEL_CALL_COMPLETED":
        payload = _model_completed_payload(raw_payload)
    elif event_type == "MODEL_CALL_FAILED":
        payload = _model_failed_payload(raw_payload)
    return payload


def decode_task_sse_payload(
    event_type: str,
    raw_payload: RawEventPayload,
) -> F149TaskSSEPayload:
    """把 core raw payload 投影为 F149 有限业务 payload 或安全诊断。"""

    projected = _known_task_sse_payload(event_type, raw_payload)
    if projected is None:
        return _safe_diagnostic(event_type, raw_payload)
    return projected


def encode_task_sse_event(event: Event, *, is_final: bool) -> F149TaskSSEFrame:
    """把唯一 core Event 输入编码为 Gateway/web frame。"""

    event_type = event.type.value
    return F149TaskSSEFrame(
        event_id=event.event_id,
        task_id=event.task_id,
        task_seq=event.task_seq,
        ts=event.ts.isoformat(),
        type=event_type,
        actor=event.actor.value,
        payload=decode_task_sse_payload(event_type, event.payload),
        final=is_final,
    )


def decode_task_sse_frame(raw_frame: Mapping[str, JsonValue]) -> F149TaskSSEFrame:
    """验证 Gateway/web frame 及 event type 与 payload kind 的对应关系。"""

    frame = F149TaskSSEFrame.model_validate(raw_frame)
    payload_kind = frame.payload.kind
    if frame.type == "STATE_TRANSITION":
        if payload_kind not in {"state_transition", "diagnostic"}:
            raise ValueError("STATE_TRANSITION payload kind 不合法")
    elif frame.type == "ARTIFACT_CREATED":
        if payload_kind != "artifact_refresh":
            raise ValueError("ARTIFACT_CREATED payload kind 不合法")
    elif frame.type in {
        "MODEL_CALL_STARTED",
        "MODEL_CALL_COMPLETED",
        "MODEL_CALL_FAILED",
    }:
        expected_kind = {
            "MODEL_CALL_STARTED": "model_call_started",
            "MODEL_CALL_COMPLETED": "model_call_completed",
            "MODEL_CALL_FAILED": "model_call_failed",
        }[frame.type]
        if payload_kind not in {expected_kind, "diagnostic"}:
            raise ValueError(f"{frame.type} payload kind 不合法")
    elif payload_kind != "diagnostic":
        raise ValueError("非业务事件只能输出 diagnostic payload")
    return frame
