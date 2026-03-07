"""A2A-Lite protocol models for Feature 018."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, Literal, Self

from octoagent.core.models import TaskStatus
from pydantic import BaseModel, ConfigDict, Field, model_validator

SUPPORTED_SCHEMA_VERSIONS = frozenset({"0.1"})
_AGENT_URI_RE = re.compile(r"^agent://[A-Za-z0-9._/-]+$")
_IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")


class A2AMessageType(StrEnum):
    """A2A-Lite message type constants."""

    TASK = "TASK"
    UPDATE = "UPDATE"
    CANCEL = "CANCEL"
    RESULT = "RESULT"
    ERROR = "ERROR"
    HEARTBEAT = "HEARTBEAT"


class A2ATaskState(StrEnum):
    """Canonical A2A task states."""

    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input-required"
    COMPLETED = "completed"
    CANCELED = "canceled"
    FAILED = "failed"
    REJECTED = "rejected"


TERMINAL_A2A_STATES = frozenset(
    {
        A2ATaskState.COMPLETED,
        A2ATaskState.CANCELED,
        A2ATaskState.FAILED,
        A2ATaskState.REJECTED,
    }
)


class A2ATraceContext(BaseModel):
    """Trace context attached to A2A-Lite messages."""

    trace_id: str = Field(description="链路 trace_id")
    parent_span_id: str | None = Field(default=None, description="父级 span ID")


class A2ATextPart(BaseModel):
    """Text part with optional URI fallback for large text bodies."""

    kind: Literal["text"] = "text"
    text: str | None = Field(default=None, description="inline text content")
    uri: str | None = Field(default=None, description="large text reference")
    mime: str = Field(default="text/plain", description="MIME type")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_body(self) -> Self:
        if not self.text and not self.uri:
            raise ValueError("text part requires text or uri")
        return self


class A2AFilePart(BaseModel):
    """File part encoded as URI or inline base64 payload."""

    kind: Literal["file"] = "file"
    uri: str | None = Field(default=None, description="file URI")
    data: str | None = Field(default=None, description="base64 encoded data")
    mime: str = Field(default="application/octet-stream", description="MIME type")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_body(self) -> Self:
        if not self.uri and not self.data:
            raise ValueError("file part requires uri or data")
        return self


class A2ADataPart(BaseModel):
    """Structured data part."""

    kind: Literal["data"] = "data"
    data: Any = Field(description="structured JSON data")
    metadata: dict[str, Any] = Field(default_factory=dict)


type A2APart = A2ATextPart | A2AFilePart | A2ADataPart


class A2AArtifact(BaseModel):
    """A2A-compatible artifact model."""

    model_config = ConfigDict(populate_by_name=True)

    artifact_id: str | None = Field(default=None, alias="artifactId")
    name: str = Field(description="artifact name")
    description: str = Field(default="", description="artifact description")
    parts: list[A2APart] = Field(default_factory=list)
    append: bool = Field(default=False)
    last_chunk: bool = Field(default=False, alias="lastChunk")
    metadata: dict[str, Any] = Field(default_factory=dict)


class A2ATaskPayload(BaseModel):
    """Payload for TASK messages."""

    user_text: str = Field(description="normalized user text")
    metadata: dict[str, str] = Field(default_factory=dict, description="routing metadata")
    resume_from_node: str | None = Field(default=None)
    resume_state_snapshot: dict[str, Any] | None = Field(default=None)


class A2AUpdatePayload(BaseModel):
    """Payload for UPDATE messages."""

    state: A2ATaskState = Field(description="current canonical A2A state")
    summary: str = Field(default="", description="update summary")
    requested_input: str | None = Field(default=None, description="requested human input")


class A2ACancelPayload(BaseModel):
    """Payload for CANCEL messages."""

    reason: str = Field(default="", description="cancel reason")


class A2AResultPayload(BaseModel):
    """Payload for RESULT messages."""

    state: A2ATaskState = Field(
        default=A2ATaskState.COMPLETED,
        description="terminal A2A state",
    )
    worker_id: str = Field(description="worker identifier")
    summary: str = Field(default="", description="result summary")
    artifacts: list[A2AArtifact] = Field(default_factory=list)
    retryable: bool = Field(default=False)
    backend: str = Field(default="inline")
    tool_profile: str = Field(default="standard")

    @model_validator(mode="after")
    def _validate_state(self) -> Self:
        if self.state not in TERMINAL_A2A_STATES:
            raise ValueError("result state must be terminal")
        return self


class A2AErrorPayload(BaseModel):
    """Payload for ERROR messages."""

    state: A2ATaskState = Field(
        default=A2ATaskState.FAILED,
        description="error terminal state",
    )
    error_type: str = Field(description="error classifier")
    error_message: str = Field(description="error message")
    retryable: bool = Field(default=False)

    @model_validator(mode="after")
    def _validate_state(self) -> Self:
        if self.state not in TERMINAL_A2A_STATES:
            raise ValueError("error state must be terminal")
        return self


class A2AHeartbeatPayload(BaseModel):
    """Payload for HEARTBEAT messages."""

    state: A2ATaskState = Field(default=A2ATaskState.WORKING, description="heartbeat state")
    worker_id: str = Field(description="worker identifier")
    loop_step: int = Field(default=0, ge=0)
    max_steps: int = Field(default=0, ge=0)
    summary: str = Field(default="", description="current progress summary")
    backend: str = Field(default="inline")

    @model_validator(mode="after")
    def _validate_state(self) -> Self:
        if self.state in TERMINAL_A2A_STATES:
            raise ValueError("heartbeat state must be non-terminal")
        return self


type A2APayload = (
    A2ATaskPayload
    | A2AUpdatePayload
    | A2ACancelPayload
    | A2AResultPayload
    | A2AErrorPayload
    | A2AHeartbeatPayload
)

_PAYLOAD_MODEL_BY_TYPE: dict[A2AMessageType, type[BaseModel]] = {
    A2AMessageType.TASK: A2ATaskPayload,
    A2AMessageType.UPDATE: A2AUpdatePayload,
    A2AMessageType.CANCEL: A2ACancelPayload,
    A2AMessageType.RESULT: A2AResultPayload,
    A2AMessageType.ERROR: A2AErrorPayload,
    A2AMessageType.HEARTBEAT: A2AHeartbeatPayload,
}


class A2AMessageMetadata(BaseModel):
    """OctoAgent-specific metadata carried alongside A2A core fields."""

    hop_count: int = Field(default=0, ge=0)
    max_hops: int = Field(default=3, ge=1)
    route_reason: str = Field(default="")
    worker_capability: str | None = Field(default=None)
    tool_profile: str | None = Field(default=None)
    model_alias: str | None = Field(default=None)
    internal_status: TaskStatus | None = Field(default=None)
    retryable: bool | None = Field(default=None)
    backend: str | None = Field(default=None)
    loop_step: int | None = Field(default=None, ge=0)
    max_steps: int | None = Field(default=None, ge=0)
    final: bool | None = Field(default=None)
    extensions: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_hops(self) -> Self:
        if self.hop_count > self.max_hops:
            raise ValueError(
                f"hop_count({self.hop_count}) cannot exceed max_hops({self.max_hops})"
            )
        return self


class A2AMessage(BaseModel):
    """A2A-Lite message envelope."""

    model_config = ConfigDict(populate_by_name=True)

    schema_version: str = Field(default="0.1")
    message_id: str = Field(description="message id")
    task_id: str = Field(description="task id")
    context_id: str = Field(description="conversation context id")
    from_agent: str = Field(alias="from", description="sender agent URI")
    to_agent: str = Field(alias="to", description="receiver agent URI")
    type: A2AMessageType = Field(description="A2A-Lite message type")
    idempotency_key: str = Field(description="idempotency key")
    timestamp_ms: int = Field(ge=0, description="unix timestamp ms")
    payload: A2APayload = Field(description="typed payload")
    trace: A2ATraceContext = Field(description="trace context")
    metadata: A2AMessageMetadata = Field(default_factory=A2AMessageMetadata)

    @model_validator(mode="before")
    @classmethod
    def _coerce_payload(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        raw_type = data.get("type")
        if raw_type is None:
            return data
        try:
            message_type = (
                raw_type
                if isinstance(raw_type, A2AMessageType)
                else A2AMessageType(raw_type)
            )
        except ValueError as exc:
            raise ValueError(f"unsupported message type: {raw_type}") from exc
        payload_model = _PAYLOAD_MODEL_BY_TYPE[message_type]

        payload = data.get("payload", {})
        if not isinstance(payload, payload_model):
            coerced = dict(data)
            coerced["type"] = message_type
            coerced["payload"] = payload_model.model_validate(payload)
            return coerced
        return data

    @model_validator(mode="after")
    def _validate_message(self) -> Self:
        if self.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError(f"unsupported schema_version: {self.schema_version}")
        if not _AGENT_URI_RE.match(self.from_agent):
            raise ValueError(f"invalid sender URI: {self.from_agent}")
        if not _AGENT_URI_RE.match(self.to_agent):
            raise ValueError(f"invalid receiver URI: {self.to_agent}")
        if not _IDEMPOTENCY_KEY_RE.match(self.idempotency_key):
            raise ValueError("invalid idempotency_key format")
        return self

    @property
    def is_final(self) -> bool:
        if self.metadata.final is not None:
            return self.metadata.final
        state = getattr(self.payload, "state", None)
        return state in TERMINAL_A2A_STATES

    def ensure_supported_version(self, versions: set[str] | frozenset[str] | None = None) -> None:
        supported = versions or SUPPORTED_SCHEMA_VERSIONS
        if self.schema_version not in supported:
            raise ValueError(
                f"schema_version {self.schema_version} is not in supported set {sorted(supported)}"
            )
