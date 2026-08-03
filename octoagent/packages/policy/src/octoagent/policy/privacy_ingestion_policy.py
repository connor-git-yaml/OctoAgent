"""F152 原生设备请求的有限权限与防重放策略。"""

from __future__ import annotations

from collections.abc import Set
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from octoagent.core.models import (
    CapabilityGrant,
    DeviceAudience,
    DeviceCapability,
    DeviceIdentity,
    DeviceStatus,
    HttpMethod,
    MemoryCandidateConfirmation,
    MemoryCandidateDecision,
    MemoryReviewState,
    OptionalMemoryCandidate,
    RequestProofPayload,
    canonical_sha256,
)

REQUEST_PROOF_WINDOW = timedelta(minutes=5)


class PrivacyIngestionPolicyError(ValueError):
    """带稳定 reason code 的 fail-closed 策略拒绝。"""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class AuthorizedRequest:
    device_id: str
    token_id: str
    capability: DeviceCapability
    replay_key: str


@dataclass(frozen=True, slots=True)
class DeviceAuthorizationContext:
    """设备授权所需的 endpoint 期望与防重放事实。"""

    required_capability: DeviceCapability
    expected_method: HttpMethod
    expected_path: str
    expected_body_sha256: str
    expected_audience: DeviceAudience
    now: datetime
    used_replay_keys: Set[str]


def _deny(reason_code: str) -> None:
    raise PrivacyIngestionPolicyError(reason_code)


def _require_utc_second(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        _deny("AUTHORIZATION_TIME_INVALID")
    if value.microsecond:
        _deny("AUTHORIZATION_TIME_INVALID")
    return value.astimezone(UTC)


def _validate_device_binding(
    *,
    device: DeviceIdentity,
    grant: CapabilityGrant,
) -> None:
    if device.status is DeviceStatus.REVOKED:
        _deny("DEVICE_REVOKED")
    if device.status is not DeviceStatus.ACTIVE:
        _deny("DEVICE_NOT_ACTIVE")
    if grant.owner_id != device.owner_id:
        _deny("DEVICE_OWNER_MISMATCH")
    if grant.device_id != device.device_id:
        _deny("DEVICE_ID_MISMATCH")
    if grant.device_key_thumbprint != device.device_key_thumbprint:
        _deny("DEVICE_KEY_MISMATCH")


def _validate_grant(
    *,
    grant: CapabilityGrant,
    required_capability: DeviceCapability,
    expected_audience: DeviceAudience,
    now: datetime,
) -> None:
    if grant.audience is not expected_audience:
        _deny("AUDIENCE_MISMATCH")
    if required_capability not in grant.capabilities:
        _deny("CAPABILITY_DENIED")
    if now < grant.issued_at:
        _deny("TOKEN_NOT_YET_VALID")
    if now >= grant.expires_at:
        _deny("TOKEN_EXPIRED")


def _validate_proof(
    *,
    proof: RequestProofPayload,
    grant: CapabilityGrant,
    context: DeviceAuthorizationContext,
) -> None:
    if proof.token_id != grant.token_id:
        _deny("PROOF_TOKEN_MISMATCH")
    if proof.method is not context.expected_method:
        _deny("PROOF_METHOD_MISMATCH")
    if proof.canonical_path != context.expected_path:
        _deny("PROOF_PATH_MISMATCH")
    if proof.body_sha256 != context.expected_body_sha256:
        _deny("PROOF_BODY_MISMATCH")
    if not grant.issued_at <= proof.timestamp < grant.expires_at:
        _deny("REQUEST_TIMESTAMP_OUT_OF_WINDOW")
    if abs(context.now - proof.timestamp) > REQUEST_PROOF_WINDOW:
        _deny("REQUEST_TIMESTAMP_OUT_OF_WINDOW")


def authorize_device_request(
    *,
    device: DeviceIdentity,
    grant: CapabilityGrant,
    proof: RequestProofPayload,
    context: DeviceAuthorizationContext,
) -> AuthorizedRequest:
    """验证设备、token 与请求事实，返回由调用方持久化的唯一 replay key。"""

    checked_now = _require_utc_second(context.now)
    _validate_device_binding(device=device, grant=grant)
    _validate_grant(
        grant=grant,
        required_capability=context.required_capability,
        expected_audience=context.expected_audience,
        now=checked_now,
    )
    _validate_proof(
        proof=proof,
        grant=grant,
        context=DeviceAuthorizationContext(
            required_capability=context.required_capability,
            expected_method=context.expected_method,
            expected_path=context.expected_path,
            expected_body_sha256=context.expected_body_sha256,
            expected_audience=context.expected_audience,
            now=checked_now,
            used_replay_keys=context.used_replay_keys,
        ),
    )
    replay_key = canonical_sha256(
        {
            "nonce": proof.nonce,
            "token_id": proof.token_id,
        }
    )
    if replay_key in context.used_replay_keys:
        _deny("REQUEST_REPLAYED")
    return AuthorizedRequest(
        device_id=device.device_id,
        token_id=grant.token_id,
        capability=context.required_capability,
        replay_key=replay_key,
    )


def review_memory_candidate(
    candidate: object,
    confirmation: MemoryCandidateConfirmation,
) -> OptionalMemoryCandidate:
    """只在独立、精确绑定的第二次确认后改变候选 review 状态。"""

    if not isinstance(candidate, OptionalMemoryCandidate):
        _deny("MEMORY_CANDIDATE_REQUIRED")
    if candidate.review_state is not MemoryReviewState.PENDING:
        _deny("MEMORY_ALREADY_REVIEWED")
    if canonical_sha256(candidate) != confirmation.candidate_sha256:
        _deny("MEMORY_CANDIDATE_HASH_MISMATCH")
    owner_ids = {item.owner_id for item in candidate.provenance}
    device_ids = {item.device_id for item in candidate.provenance}
    if len(owner_ids) != 1 or len(device_ids) != 1:
        _deny("MEMORY_PROVENANCE_SCOPE_MISMATCH")
    if confirmation.owner_id not in owner_ids:
        _deny("MEMORY_OWNER_MISMATCH")
    if confirmation.device_id not in device_ids:
        _deny("MEMORY_DEVICE_MISMATCH")
    review_state = (
        MemoryReviewState.APPROVED
        if confirmation.decision is MemoryCandidateDecision.APPROVE
        else MemoryReviewState.REJECTED
    )
    return OptionalMemoryCandidate.model_validate(
        candidate.model_dump(mode="python") | {"review_state": review_state}
    )


__all__ = [
    "AuthorizedRequest",
    "DeviceAuthorizationContext",
    "PrivacyIngestionPolicyError",
    "REQUEST_PROOF_WINDOW",
    "authorize_device_request",
    "review_memory_candidate",
]
