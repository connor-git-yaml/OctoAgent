"""F152 设备授权与请求防重放的对抗性合同。"""

from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from octoagent.core.models import (
    CapabilityGrant,
    DeviceAudience,
    DeviceCapability,
    HttpMethod,
    IngestionRetention,
    RequestProofPayload,
)
from pydantic import ValidationError

ORACLE = "F152_PRIVACY_POLICY_MISSING"
NOW = datetime(2026, 7, 28, 8, 0, tzinfo=UTC)
HASH_A = "a" * 64
HASH_B = "b" * 64


def _contract() -> tuple[Any, Any, Any, Any]:
    policy_module = importlib.import_module("octoagent.policy")
    model_module = importlib.import_module("octoagent.core.models")
    required = (
        (policy_module, "AuthorizedRequest"),
        (policy_module, "DeviceAuthorizationContext"),
        (policy_module, "PrivacyIngestionPolicyError"),
        (policy_module, "authorize_device_request"),
        (model_module, "AttestationState"),
        (model_module, "DeviceIdentity"),
        (model_module, "DeviceStatus"),
    )
    missing = [name for module, name in required if not hasattr(module, name)]
    if missing:
        pytest.fail(f"{ORACLE}: missing public contract {','.join(missing)}", pytrace=False)
    return (
        policy_module.AuthorizedRequest,
        policy_module.PrivacyIngestionPolicyError,
        policy_module.authorize_device_request,
        model_module,
    )


def _device(*, status: str = "active") -> Any:
    _, _, _, models = _contract()
    return models.DeviceIdentity(
        device_id="device-1",
        owner_id="owner-1",
        display_name="Connor iPhone",
        public_key="p256-public-key",
        device_key_thumbprint=HASH_A,
        attestation_state="unverified",
        status=status,
        created_at=NOW - timedelta(days=1),
        last_seen_at=NOW - timedelta(seconds=10),
        revoked_at=NOW - timedelta(minutes=1) if status == "revoked" else None,
    )


def _grant(
    *,
    issued_at: datetime = NOW - timedelta(minutes=1),
    expires_at: datetime = NOW + timedelta(minutes=5),
) -> CapabilityGrant:
    return CapabilityGrant(
        grant_id="grant-1",
        owner_id="owner-1",
        device_id="device-1",
        device_key_thumbprint=HASH_A,
        capabilities=(
            DeviceCapability.DEVICE_PROFILE_READ,
            DeviceCapability.CONVERSATION_READ,
        ),
        audience=DeviceAudience.OCTO_GATEWAY,
        issued_at=issued_at,
        expires_at=expires_at,
        token_id="token-1",
    )


def _proof(
    *,
    timestamp: datetime = NOW,
    nonce: str = "n" * 32,
) -> RequestProofPayload:
    return RequestProofPayload(
        method=HttpMethod.POST,
        canonical_path="/v1/device/profile",
        body_sha256=HASH_B,
        timestamp=timestamp,
        nonce=nonce,
        token_id="token-1",
    )


def _authorize(
    *,
    device: Any | None = None,
    grant: CapabilityGrant | None = None,
    proof: RequestProofPayload | None = None,
    used_replay_keys: frozenset[str] = frozenset(),
) -> Any:
    _, _, authorize, _ = _contract()
    return authorize(
        device=device or _device(),
        grant=grant or _grant(),
        proof=proof or _proof(),
        context=importlib.import_module("octoagent.policy").DeviceAuthorizationContext(
            required_capability=DeviceCapability.DEVICE_PROFILE_READ,
            expected_method=HttpMethod.POST,
            expected_path="/v1/device/profile",
            expected_body_sha256=HASH_B,
            expected_audience=DeviceAudience.OCTO_GATEWAY,
            now=NOW,
            used_replay_keys=used_replay_keys,
        ),
    )


def _assert_rejected(reason_code: str, **kwargs: Any) -> None:
    _, error_type, _, _ = _contract()
    with pytest.raises(error_type) as captured:
        _authorize(**kwargs)
    assert captured.value.reason_code == reason_code


def test_active_device_authorizes_exact_request_and_returns_replay_key() -> None:
    authorized_type, _, _, _ = _contract()
    decision = _authorize()
    assert isinstance(decision, authorized_type)
    assert decision.device_id == "device-1"
    assert decision.token_id == "token-1"
    assert decision.capability is DeviceCapability.DEVICE_PROFILE_READ
    assert len(decision.replay_key) == 64


def test_device_identity_status_timestamps_are_exact() -> None:
    _, _, _, models = _contract()
    active = _device()
    assert active.status is models.DeviceStatus.ACTIVE
    assert active.attestation_state is models.AttestationState.UNVERIFIED
    with pytest.raises(ValidationError, match="revoked"):
        active.model_copy(update={"revoked_at": NOW}).model_validate(
            {**active.model_dump(), "revoked_at": NOW}
        )
    with pytest.raises(ValidationError, match="revoked"):
        models.DeviceIdentity(**{**active.model_dump(), "status": "revoked"})


def test_revocation_precedes_expiry_failure() -> None:
    expired = _grant(
        issued_at=NOW - timedelta(minutes=10),
        expires_at=NOW - timedelta(minutes=1),
    )
    _assert_rejected("DEVICE_REVOKED", device=_device(status="revoked"), grant=expired)


@pytest.mark.parametrize(
    ("field", "reason_code"),
    [
        ("owner_id", "DEVICE_OWNER_MISMATCH"),
        ("device_id", "DEVICE_ID_MISMATCH"),
        ("device_key_thumbprint", "DEVICE_KEY_MISMATCH"),
    ],
)
def test_cross_identity_binding_is_rejected(field: str, reason_code: str) -> None:
    replacement = HASH_B if field == "device_key_thumbprint" else f"other-{field}"
    grant = _grant().model_copy(update={field: replacement})
    _assert_rejected(reason_code, grant=grant)


@pytest.mark.parametrize(
    ("grant", "reason_code"),
    [
        (
            _grant(
                issued_at=NOW - timedelta(minutes=10),
                expires_at=NOW - timedelta(seconds=1),
            ),
            "TOKEN_EXPIRED",
        ),
        (
            _grant(
                issued_at=NOW + timedelta(seconds=1),
                expires_at=NOW + timedelta(minutes=1),
            ),
            "TOKEN_NOT_YET_VALID",
        ),
    ],
)
def test_token_time_window_is_fail_closed(
    grant: CapabilityGrant,
    reason_code: str,
) -> None:
    _assert_rejected(reason_code, grant=grant)


def test_nonce_replay_is_rejected_without_mutating_caller_state() -> None:
    first = _authorize()
    used = frozenset({first.replay_key})
    _assert_rejected("REQUEST_REPLAYED", used_replay_keys=used)
    assert used == frozenset({first.replay_key})


@pytest.mark.parametrize(
    ("proof", "reason_code"),
    [
        (_proof().model_copy(update={"method": HttpMethod.GET}), "PROOF_METHOD_MISMATCH"),
        (
            _proof().model_copy(update={"canonical_path": "/v1/tasks"}),
            "PROOF_PATH_MISMATCH",
        ),
        (
            _proof().model_copy(update={"body_sha256": HASH_A}),
            "PROOF_BODY_MISMATCH",
        ),
        (
            _proof().model_copy(update={"token_id": "other-token"}),
            "PROOF_TOKEN_MISMATCH",
        ),
    ],
)
def test_proof_is_bound_to_exact_request(
    proof: RequestProofPayload,
    reason_code: str,
) -> None:
    _assert_rejected(reason_code, proof=proof)


@pytest.mark.parametrize(
    "timestamp",
    [NOW - timedelta(minutes=5, seconds=1), NOW + timedelta(minutes=5, seconds=1)],
)
def test_stale_or_future_request_timestamp_is_rejected(timestamp: datetime) -> None:
    _assert_rejected("REQUEST_TIMESTAMP_OUT_OF_WINDOW", proof=_proof(timestamp=timestamp))


def test_missing_capability_is_rejected_without_prefix_fallback() -> None:
    grant = _grant().model_copy(update={"capabilities": (DeviceCapability.CONVERSATION_READ,)})
    _assert_rejected("CAPABILITY_DENIED", grant=grant)


def test_unknown_stage_and_capability_are_schema_errors() -> None:
    _contract()
    with pytest.raises(ValidationError):
        IngestionRetention(
            stage="future_stage",
            created_at=NOW,
            expires_at=NOW + timedelta(minutes=1),
        )
    with pytest.raises(ValidationError):
        CapabilityGrant(
            **{
                **_grant().model_dump(),
                "capabilities": ("health.read",),
            }
        )


def _memory_contract() -> tuple[Any, Any, Any]:
    policy_module = importlib.import_module("octoagent.policy")
    model_module = importlib.import_module("octoagent.core.models")
    required = (
        (policy_module, "review_memory_candidate"),
        (model_module, "MemoryCandidateConfirmation"),
        (model_module, "MemoryCandidateDecision"),
    )
    missing = [name for module, name in required if not hasattr(module, name)]
    if missing:
        pytest.fail(
            f"F152_MEMORY_SECOND_CONFIRMATION_MISSING: {','.join(missing)}",
            pytrace=False,
        )
    return policy_module, model_module, policy_module.review_memory_candidate


def _memory_candidate(*, second_device: bool = False) -> Any:
    _, models, _ = _memory_contract()
    provenance = [
        {
            "source_kind": "device_profile",
            "source_object_hash": "1" * 64,
            "owner_id": "owner-1",
            "device_id": "device-1",
            "captured_at": NOW - timedelta(minutes=2),
            "time_range": {
                "start": NOW - timedelta(minutes=3),
                "end": NOW - timedelta(minutes=2),
            },
            "data_types": ["profile"],
        }
    ]
    if second_device:
        provenance.append(
            {
                **provenance[0],
                "source_object_hash": "2" * 64,
                "device_id": "device-2",
            }
        )
    return models.OptionalMemoryCandidate.model_validate(
        {
            "candidate_id": "candidate-1",
            "result_id": "result-1",
            "packet_sha256": "3" * 64,
            "provenance": provenance,
            "user_selected_text": "Keep only this user-selected summary.",
            "review_state": "pending",
        }
    )


def _confirmation(
    candidate: Any,
    *,
    decision: str = "approve",
) -> Any:
    _, models, _ = _memory_contract()
    return models.MemoryCandidateConfirmation(
        confirmation_id="confirmation-1",
        candidate_sha256=models.canonical_sha256(candidate),
        owner_id="owner-1",
        device_id="device-1",
        decision=decision,
        confirmed_at=NOW,
    )


@pytest.mark.parametrize(
    ("decision", "expected_state"),
    [("approve", "approved"), ("reject", "rejected")],
)
def test_memory_candidate_requires_explicit_second_confirmation(
    decision: str,
    expected_state: str,
) -> None:
    _, models, review = _memory_contract()
    candidate = _memory_candidate()
    reviewed = review(candidate, _confirmation(candidate, decision=decision))
    assert candidate.review_state is models.MemoryReviewState.PENDING
    assert reviewed.review_state.value == expected_state
    assert reviewed.candidate_id == candidate.candidate_id
    assert reviewed.user_selected_text == candidate.user_selected_text


def test_analysis_result_cannot_be_reviewed_or_written_as_memory() -> None:
    policy, models, review = _memory_contract()
    result = models.AnalysisResult(
        result_id="result-1",
        packet_id="packet-1",
        result_sha256="4" * 64,
        created_at=NOW,
        retention_state="ephemeral",
    )
    candidate = _memory_candidate()
    with pytest.raises(policy.PrivacyIngestionPolicyError) as rejected:
        review(result, _confirmation(candidate))
    assert rejected.value.reason_code == "MEMORY_CANDIDATE_REQUIRED"
    assert "memory" not in type(result).model_fields


@pytest.mark.parametrize(
    ("patch", "reason_code"),
    [
        ({"candidate_sha256": "5" * 64}, "MEMORY_CANDIDATE_HASH_MISMATCH"),
        ({"owner_id": "owner-2"}, "MEMORY_OWNER_MISMATCH"),
        ({"device_id": "device-2"}, "MEMORY_DEVICE_MISMATCH"),
    ],
)
def test_memory_confirmation_is_bound_to_candidate_and_identity(
    patch: dict[str, str],
    reason_code: str,
) -> None:
    policy, _, review = _memory_contract()
    candidate = _memory_candidate()
    confirmation = _confirmation(candidate).model_copy(update=patch)
    with pytest.raises(policy.PrivacyIngestionPolicyError) as rejected:
        review(candidate, confirmation)
    assert rejected.value.reason_code == reason_code


def test_memory_confirmation_cannot_be_replayed() -> None:
    policy, _, review = _memory_contract()
    candidate = _memory_candidate()
    confirmation = _confirmation(candidate)
    approved = review(candidate, confirmation)
    with pytest.raises(policy.PrivacyIngestionPolicyError) as rejected:
        review(approved, confirmation)
    assert rejected.value.reason_code == "MEMORY_ALREADY_REVIEWED"


def test_memory_candidate_rejects_mixed_provenance_scope() -> None:
    policy, _, review = _memory_contract()
    candidate = _memory_candidate(second_device=True)
    with pytest.raises(policy.PrivacyIngestionPolicyError) as rejected:
        review(candidate, _confirmation(candidate))
    assert rejected.value.reason_code == "MEMORY_PROVENANCE_SCOPE_MISMATCH"


def test_memory_confirmation_schema_is_exact_and_finite() -> None:
    _, models, _ = _memory_contract()
    candidate = _memory_candidate()
    payload = _confirmation(candidate).model_dump()
    assert set(payload) == {
        "confirmation_id",
        "candidate_sha256",
        "owner_id",
        "device_id",
        "decision",
        "confirmed_at",
    }
    with pytest.raises(ValidationError):
        models.MemoryCandidateConfirmation(**{**payload, "decision": "pending"})
    with pytest.raises(ValidationError):
        models.MemoryCandidateConfirmation(**{**payload, "write_memory": True})
