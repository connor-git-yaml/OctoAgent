"""F152 隐私采集领域模型与确定性序列化边界。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Nonce = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{32,128}$")]
NonEmptyString = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=256),
]


def _utc_second(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must use UTC")
    if value.microsecond != 0:
        raise ValueError("timestamp must have whole-second precision")
    return value.astimezone(UTC)


UtcSecond = Annotated[datetime, AfterValidator(_utc_second)]


class IngestionStage(StrEnum):
    """敏感数据在系统内允许出现的有限阶段。"""

    RAW_SAMPLE = "raw_sample"
    NORMALIZED_FACT = "normalized_fact"
    REVIEW_BUNDLE = "review_bundle"
    APPROVED_ANALYSIS_PACKET = "approved_analysis_packet"
    ANALYSIS_RESULT = "analysis_result"
    OPTIONAL_MEMORY_CANDIDATE = "optional_memory_candidate"


class SourceKind(StrEnum):
    """Provenance 可声明的数据来源；不是设备 capability。"""

    DEVICE_PROFILE = "device_profile"
    HEALTHKIT = "healthkit"
    EVENTKIT = "eventkit"


class DeviceCapability(StrEnum):
    DEVICE_READY_READ = "device.ready.read"
    DEVICE_PROFILE_READ = "device.profile.read"
    CONVERSATION_READ = "conversation.read"
    CONVERSATION_SEND = "conversation.send"
    TASK_READ = "task.read"
    APPROVAL_READ = "approval.read"
    APPROVAL_DECIDE = "approval.decide"
    MEMORY_CANDIDATE_READ = "memory_candidate.read"
    MEMORY_CANDIDATE_DECIDE = "memory_candidate.decide"
    HEALTH_REVIEW_SUBMIT = "health.review.submit"
    HEALTH_ANALYSIS_RUN = "health.analysis.run"
    HEALTH_SOURCE_DELETE = "health.source.delete"


class DeviceAudience(StrEnum):
    OCTO_GATEWAY = "octo-gateway"


class DeviceStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    ROTATING = "rotating"
    REVOKED = "revoked"


class AttestationState(StrEnum):
    UNSUPPORTED = "unsupported"
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    FAILED = "failed"


class HttpMethod(StrEnum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


class RetentionState(StrEnum):
    EPHEMERAL = "ephemeral"
    RETAINED = "retained"
    DELETION_REQUESTED = "deletion_requested"
    DELETED = "deleted"


class MemoryReviewState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class MemoryCandidateDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class PrivacyAuditEventType(StrEnum):
    DEVICE_REGISTERED = "device_registered"
    CONSENT_APPROVED = "consent_approved"
    PACKET_SENT = "packet_sent"
    ANALYSIS_COMPLETED = "analysis_completed"
    MEMORY_CANDIDATE_PROPOSED = "memory_candidate_proposed"
    DELETION_STARTED = "deletion_started"
    DELETION_COMPLETED = "deletion_completed"
    DELETION_FAILED = "deletion_failed"
    DEVICE_REVOKED = "device_revoked"
    KEY_ROTATED = "key_rotated"
    REJECTED = "rejected"


class AuditDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    NOT_APPLICABLE = "not_applicable"


class AuditResult(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    PENDING = "pending"
    PARTIAL = "partial"


class DeletionStatus(StrEnum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TimeRange(_StrictFrozenModel):
    start: UtcSecond
    end: UtcSecond

    @model_validator(mode="after")
    def validate_order(self) -> TimeRange:
        if self.end < self.start:
            raise ValueError("time range end precedes start")
        return self


class Provenance(_StrictFrozenModel):
    """不含系统 identifier 或正文的最小来源描述。"""

    source_kind: SourceKind
    source_object_hash: Sha256
    owner_id: NonEmptyString
    device_id: NonEmptyString
    captured_at: UtcSecond
    time_range: TimeRange
    data_types: tuple[NonEmptyString, ...]

    @field_validator("data_types")
    @classmethod
    def normalize_data_types(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if not values:
            raise ValueError("data_types must not be empty")
        if len(set(values)) != len(values):
            raise ValueError("data_types must be unique")
        return tuple(sorted(values))


class DeviceIdentity(_StrictFrozenModel):
    """不持有私钥的服务端设备身份投影。"""

    device_id: NonEmptyString
    owner_id: NonEmptyString
    display_name: NonEmptyString
    public_key: NonEmptyString
    device_key_thumbprint: Sha256
    attestation_state: AttestationState
    status: DeviceStatus
    created_at: UtcSecond
    last_seen_at: UtcSecond
    revoked_at: UtcSecond | None = None

    @model_validator(mode="after")
    def validate_lifecycle(self) -> DeviceIdentity:
        if self.last_seen_at < self.created_at:
            raise ValueError("last seen precedes device creation")
        if self.status is DeviceStatus.REVOKED:
            if self.revoked_at is None:
                raise ValueError("revoked device requires revoked_at")
            if self.revoked_at < self.created_at:
                raise ValueError("revoked_at precedes device creation")
        elif self.revoked_at is not None:
            raise ValueError("only revoked device may have revoked_at")
        return self


class CapabilityGrant(_StrictFrozenModel):
    """F153 可签发的短期、设备绑定、有限权限授权。"""

    grant_id: NonEmptyString
    owner_id: NonEmptyString
    device_id: NonEmptyString
    device_key_thumbprint: Sha256
    capabilities: tuple[DeviceCapability, ...]
    audience: DeviceAudience
    issued_at: UtcSecond
    expires_at: UtcSecond
    token_id: NonEmptyString

    @field_validator("capabilities")
    @classmethod
    def normalize_capabilities(
        cls,
        values: tuple[DeviceCapability, ...],
    ) -> tuple[DeviceCapability, ...]:
        if not values:
            raise ValueError("capabilities must not be empty")
        if len(set(values)) != len(values):
            raise ValueError("capabilities must be unique")
        return tuple(sorted(values, key=lambda capability: capability.value))

    @model_validator(mode="after")
    def validate_expiry(self) -> CapabilityGrant:
        if self.expires_at <= self.issued_at:
            raise ValueError("token expiry must be later than issue time")
        if self.expires_at > self.issued_at + timedelta(minutes=15):
            raise ValueError("token expiry exceeds fifteen minutes")
        return self


class ConsentGrant(_StrictFrozenModel):
    """一次预览批准与一个 planned analysis packet 的不可复用绑定。"""

    consent_id: NonEmptyString
    bundle_sha256: Sha256
    approved_packet_sha256: Sha256
    purpose: NonEmptyString
    owner_id: NonEmptyString
    device_id: NonEmptyString
    approved_at: UtcSecond
    expires_at: UtcSecond
    used_at: UtcSecond | None = None

    @model_validator(mode="after")
    def validate_window(self) -> ConsentGrant:
        if self.expires_at <= self.approved_at:
            raise ValueError("consent expiry must be later than approval")
        if self.expires_at > self.approved_at + timedelta(minutes=15):
            raise ValueError("consent expiry exceeds fifteen minutes")
        if self.used_at is not None and not (self.approved_at <= self.used_at < self.expires_at):
            raise ValueError("consent use must occur inside approval window")
        return self


class RequestProofPayload(_StrictFrozenModel):
    """F153 签名所覆盖的唯一请求事实；不包含签名本身。"""

    method: HttpMethod
    canonical_path: NonEmptyString
    body_sha256: Sha256
    timestamp: UtcSecond
    nonce: Nonce
    token_id: NonEmptyString

    @field_validator("canonical_path")
    @classmethod
    def validate_canonical_path(cls, path: str) -> str:
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("canonical path must be absolute and origin-relative")
        if any(marker in path for marker in ("?", "#", "\\", "//")):
            raise ValueError("canonical path cannot contain query, fragment, or ambiguity")
        if any(segment in {".", ".."} for segment in path.split("/")):
            raise ValueError("canonical path cannot contain dot segments")
        try:
            path.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ValueError("canonical path must be ASCII") from exc
        return path


class PrivacyAuditEvent(_StrictFrozenModel):
    """只含非敏感 metadata 的 durable audit 记录。"""

    event_id: NonEmptyString
    event_type: PrivacyAuditEventType
    schema_version: Literal[1] = 1
    owner_hash: Sha256
    device_hash: Sha256
    object_hash: Sha256
    count: Annotated[int, Field(ge=0)]
    data_types: tuple[NonEmptyString, ...] = ()
    capabilities: tuple[DeviceCapability, ...] = ()
    decision: AuditDecision
    result: AuditResult
    reason_code: NonEmptyString
    occurred_at: UtcSecond

    @field_validator("data_types")
    @classmethod
    def normalize_audit_data_types(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(values)) != len(values):
            raise ValueError("audit data_types must be unique")
        return tuple(sorted(values))

    @field_validator("capabilities")
    @classmethod
    def normalize_audit_capabilities(
        cls,
        values: tuple[DeviceCapability, ...],
    ) -> tuple[DeviceCapability, ...]:
        if len(set(values)) != len(values):
            raise ValueError("audit capabilities must be unique")
        return tuple(sorted(values, key=lambda capability: capability.value))


class DeletionReceipt(_StrictFrozenModel):
    request_id: NonEmptyString
    source_hash: Sha256
    deleted_object_hashes: tuple[Sha256, ...] = ()
    retained_audit_hashes: tuple[Sha256, ...] = ()
    status: DeletionStatus
    started_at: UtcSecond
    finished_at: UtcSecond | None = None
    failure_reason: str = ""

    @field_validator("deleted_object_hashes", "retained_audit_hashes")
    @classmethod
    def normalize_hashes(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(values)) != len(values):
            raise ValueError("receipt hashes must be unique")
        return tuple(sorted(values))

    @model_validator(mode="after")
    def validate_status(self) -> DeletionReceipt:
        if self.finished_at is not None and self.finished_at < self.started_at:
            raise ValueError("deletion finish precedes start")
        if self.status is DeletionStatus.STARTED:
            if self.finished_at is not None or self.failure_reason:
                raise ValueError("started deletion cannot have completion fields")
        elif self.status is DeletionStatus.COMPLETED:
            if self.finished_at is None or self.failure_reason:
                raise ValueError("completed deletion requires clean finish")
        elif self.finished_at is None or not self.failure_reason.strip():
            raise ValueError("failed deletion requires finish and reason")
        return self


def retention_deadline(
    stage: IngestionStage,
    *,
    created_at: datetime,
    session_ends_at: datetime | None = None,
) -> datetime:
    """返回敏感临时阶段的最晚删除时间。"""

    created = _utc_second(created_at)
    if stage in {IngestionStage.RAW_SAMPLE, IngestionStage.NORMALIZED_FACT}:
        maximum = created + timedelta(hours=24)
        if session_ends_at is None:
            return maximum
        session_end = _utc_second(session_ends_at)
        if session_end <= created:
            raise ValueError("session end must be later than creation")
        return min(maximum, session_end)
    if session_ends_at is not None:
        raise ValueError("session end is only valid for local draft stages")
    if stage is IngestionStage.REVIEW_BUNDLE:
        return created + timedelta(hours=24)
    if stage is IngestionStage.APPROVED_ANALYSIS_PACKET:
        return created + timedelta(minutes=15)
    raise ValueError(f"stage has no F152 temporary retention window: {stage.value}")


class IngestionRetention(_StrictFrozenModel):
    stage: IngestionStage
    created_at: UtcSecond
    expires_at: UtcSecond
    session_ends_at: UtcSecond | None = None

    @model_validator(mode="after")
    def validate_deadline(self) -> IngestionRetention:
        if self.expires_at <= self.created_at:
            raise ValueError("expiry must be later than creation")
        maximum = retention_deadline(
            self.stage,
            created_at=self.created_at,
            session_ends_at=self.session_ends_at,
        )
        if self.expires_at > maximum:
            raise ValueError("expiry exceeds stage retention limit")
        return self


class _RetainedStage(_StrictFrozenModel):
    retention: IngestionRetention

    @model_validator(mode="after")
    def validate_retention_stage(self) -> _RetainedStage:
        if self.retention.stage != self.stage:
            raise ValueError("retention stage does not match artifact stage")
        return self


class RawSample(_RetainedStage):
    stage: Literal[IngestionStage.RAW_SAMPLE] = IngestionStage.RAW_SAMPLE
    sample_hash: Sha256
    provenance: Provenance


class NormalizedFact(_RetainedStage):
    stage: Literal[IngestionStage.NORMALIZED_FACT] = IngestionStage.NORMALIZED_FACT
    fact_hash: Sha256
    provenance: Provenance


class ReviewBundle(_RetainedStage):
    stage: Literal[IngestionStage.REVIEW_BUNDLE] = IngestionStage.REVIEW_BUNDLE
    bundle_id: NonEmptyString
    purpose: NonEmptyString
    provenance: tuple[Provenance, ...]
    fact_count: Annotated[int, Field(ge=0)]
    field_manifest: tuple[NonEmptyString, ...]
    preview_hash: Sha256


class ApprovedAnalysisPacket(_RetainedStage):
    stage: Literal[IngestionStage.APPROVED_ANALYSIS_PACKET] = (
        IngestionStage.APPROVED_ANALYSIS_PACKET
    )
    packet_id: NonEmptyString
    purpose: NonEmptyString
    facts_sha256: Sha256
    provenance: tuple[Provenance, ...]
    consent_id: NonEmptyString


class AnalysisResult(_StrictFrozenModel):
    stage: Literal[IngestionStage.ANALYSIS_RESULT] = IngestionStage.ANALYSIS_RESULT
    result_id: NonEmptyString
    packet_id: NonEmptyString
    result_sha256: Sha256
    created_at: UtcSecond
    retention_state: RetentionState


class MemoryCandidateConfirmation(_StrictFrozenModel):
    """用户在分析完成后独立作出的 Memory 候选确认。"""

    confirmation_id: NonEmptyString
    candidate_sha256: Sha256
    owner_id: NonEmptyString
    device_id: NonEmptyString
    decision: MemoryCandidateDecision
    confirmed_at: UtcSecond


class OptionalMemoryCandidate(_StrictFrozenModel):
    stage: Literal[IngestionStage.OPTIONAL_MEMORY_CANDIDATE] = (
        IngestionStage.OPTIONAL_MEMORY_CANDIDATE
    )
    candidate_id: NonEmptyString
    result_id: NonEmptyString
    packet_sha256: Sha256
    provenance: tuple[Provenance, ...]
    user_selected_text: NonEmptyString
    review_state: MemoryReviewState


def _canonical_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _canonical_value(value.model_dump(mode="python"))
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, datetime):
        return _utc_second(value).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("canonical object keys must be strings")
        return {key: _canonical_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_canonical_value(item) for item in value]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise TypeError(f"unsupported canonical value: {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    """生成唯一 UTF-8 JSON 表达；拒绝有歧义或非合同类型。"""

    canonical = _canonical_value(value)
    return json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


__all__ = [
    "AnalysisResult",
    "ApprovedAnalysisPacket",
    "AttestationState",
    "AuditDecision",
    "AuditResult",
    "CapabilityGrant",
    "ConsentGrant",
    "DeviceAudience",
    "DeviceCapability",
    "DeviceIdentity",
    "DeviceStatus",
    "DeletionReceipt",
    "DeletionStatus",
    "HttpMethod",
    "IngestionRetention",
    "IngestionStage",
    "MemoryCandidateConfirmation",
    "MemoryCandidateDecision",
    "MemoryReviewState",
    "NormalizedFact",
    "OptionalMemoryCandidate",
    "PrivacyAuditEvent",
    "PrivacyAuditEventType",
    "Provenance",
    "RawSample",
    "RequestProofPayload",
    "RetentionState",
    "ReviewBundle",
    "SourceKind",
    "TimeRange",
    "canonical_json_bytes",
    "canonical_sha256",
    "retention_deadline",
]
