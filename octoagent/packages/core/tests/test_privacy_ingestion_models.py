from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, get_args

import pytest
from pydantic import ValidationError

ORACLE = "F152_PRIVACY_INGESTION_MODELS_MISSING"
CAPABILITY_ORACLE = "F152_DEVICE_CAPABILITY_CONTRACT_MISSING"
PROOF_ORACLE = "F152_REQUEST_PROOF_PAYLOAD_MISSING"


def _models() -> Any:
    try:
        return importlib.import_module("octoagent.core.models.privacy_ingestion")
    except ModuleNotFoundError:
        pytest.fail(f"{ORACLE}: production models are absent", pytrace=False)


def _capability_models() -> Any:
    models = _models()
    required = {"CapabilityGrant", "DeviceAudience", "DeviceCapability"}
    missing = sorted(required - set(vars(models)))
    if missing:
        pytest.fail(
            f"{CAPABILITY_ORACLE}: missing symbols={','.join(missing)}",
            pytrace=False,
        )
    return models


def _proof_models() -> Any:
    models = _models()
    required = {"HttpMethod", "RequestProofPayload"}
    missing = sorted(required - set(vars(models)))
    if missing:
        pytest.fail(
            f"{PROOF_ORACLE}: missing symbols={','.join(missing)}",
            pytrace=False,
        )
    return models


def _utc(hour: int, *, microsecond: int = 0) -> datetime:
    return datetime(2026, 7, 28, hour, tzinfo=UTC, microsecond=microsecond)


def _valid_provenance_payload() -> dict[str, object]:
    return {
        "source_kind": "device_profile",
        "source_object_hash": "a" * 64,
        "owner_id": "owner-1",
        "device_id": "device-1",
        "captured_at": _utc(10),
        "time_range": {
            "start": _utc(9),
            "end": _utc(10),
        },
        "data_types": ["step_count", "heart_rate"],
    }


def _assert_rejected(model: Any, payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_ingestion_stages_have_distinct_exact_models() -> None:
    models = _models()
    assert [stage.value for stage in models.IngestionStage] == [
        "raw_sample",
        "normalized_fact",
        "review_bundle",
        "approved_analysis_packet",
        "analysis_result",
        "optional_memory_candidate",
    ], ORACLE

    expected = {
        models.RawSample: models.IngestionStage.RAW_SAMPLE,
        models.NormalizedFact: models.IngestionStage.NORMALIZED_FACT,
        models.ReviewBundle: models.IngestionStage.REVIEW_BUNDLE,
        models.ApprovedAnalysisPacket: models.IngestionStage.APPROVED_ANALYSIS_PACKET,
        models.AnalysisResult: models.IngestionStage.ANALYSIS_RESULT,
        models.OptionalMemoryCandidate: models.IngestionStage.OPTIONAL_MEMORY_CANDIDATE,
    }
    assert len(expected) == 6, ORACLE
    for model, stage in expected.items():
        stage_field = model.model_fields["stage"]
        assert get_args(stage_field.annotation) == (stage,), ORACLE
        assert stage_field.default == stage, ORACLE
        assert model.model_config["extra"] == "forbid", ORACLE
        assert model.model_config["frozen"] is True, ORACLE
        assert not {"payload", "data", "metadata", "blob"} & model.model_fields.keys(), ORACLE


def test_provenance_is_minimal_exact_and_canonical() -> None:
    models = _models()
    provenance = models.Provenance.model_validate(_valid_provenance_payload())
    assert provenance.source_object_hash == "a" * 64, ORACLE
    assert provenance.data_types == ("heart_rate", "step_count"), ORACLE
    assert set(models.Provenance.model_fields) == {
        "source_kind",
        "source_object_hash",
        "owner_id",
        "device_id",
        "captured_at",
        "time_range",
        "data_types",
    }, ORACLE

    extra = _valid_provenance_payload() | {"raw_identifier": "secret"}
    _assert_rejected(models.Provenance, extra)
    _assert_rejected(
        models.Provenance,
        _valid_provenance_payload() | {"source_object_hash": "A" * 64},
    )
    _assert_rejected(
        models.Provenance,
        _valid_provenance_payload() | {"captured_at": datetime(2026, 7, 28, 10)},
    )
    _assert_rejected(
        models.Provenance,
        _valid_provenance_payload() | {"captured_at": _utc(10, microsecond=1)},
    )
    _assert_rejected(
        models.Provenance,
        _valid_provenance_payload() | {"time_range": {"start": _utc(10), "end": _utc(9)}},
    )
    _assert_rejected(
        models.Provenance,
        _valid_provenance_payload() | {"data_types": ["step_count", "step_count"]},
    )
    _assert_rejected(
        models.Provenance,
        _valid_provenance_payload() | {"data_types": []},
    )


def test_sensitive_stage_ttl_is_bounded_and_fail_closed() -> None:
    models = _models()
    created_at = _utc(10)

    assert models.retention_deadline(
        models.IngestionStage.RAW_SAMPLE,
        created_at=created_at,
        session_ends_at=created_at + timedelta(hours=2),
    ) == created_at + timedelta(hours=2), ORACLE
    assert models.retention_deadline(
        models.IngestionStage.NORMALIZED_FACT,
        created_at=created_at,
        session_ends_at=created_at + timedelta(days=2),
    ) == created_at + timedelta(hours=24), ORACLE
    assert models.retention_deadline(
        models.IngestionStage.RAW_SAMPLE,
        created_at=created_at,
    ) == created_at + timedelta(hours=24), ORACLE
    assert models.retention_deadline(
        models.IngestionStage.REVIEW_BUNDLE,
        created_at=created_at,
    ) == created_at + timedelta(hours=24), ORACLE
    assert models.retention_deadline(
        models.IngestionStage.APPROVED_ANALYSIS_PACKET,
        created_at=created_at,
    ) == created_at + timedelta(minutes=15), ORACLE

    retention = models.IngestionRetention.model_validate(
        {
            "stage": "approved_analysis_packet",
            "created_at": created_at,
            "expires_at": created_at + timedelta(minutes=15),
        }
    )
    assert retention.expires_at == created_at + timedelta(minutes=15), ORACLE
    _assert_rejected(
        models.IngestionRetention,
        {
            "stage": "approved_analysis_packet",
            "created_at": created_at,
            "expires_at": created_at + timedelta(minutes=16),
        },
    )
    with pytest.raises(ValueError):
        models.retention_deadline(
            models.IngestionStage.ANALYSIS_RESULT,
            created_at=created_at,
        )
    with pytest.raises(ValueError):
        models.retention_deadline(
            models.IngestionStage.RAW_SAMPLE,
            created_at=created_at,
            session_ends_at=created_at,
        )
    with pytest.raises(ValueError):
        models.retention_deadline(
            models.IngestionStage.REVIEW_BUNDLE,
            created_at=created_at,
            session_ends_at=created_at + timedelta(minutes=1),
        )
    _assert_rejected(
        models.IngestionRetention,
        {
            "stage": "raw_sample",
            "created_at": created_at,
            "expires_at": created_at,
        },
    )

    provenance = models.Provenance.model_validate(_valid_provenance_payload())
    wrong_retention = {
        "stage": "review_bundle",
        "created_at": created_at,
        "expires_at": created_at + timedelta(hours=24),
    }
    _assert_rejected(
        models.RawSample,
        {
            "sample_hash": "b" * 64,
            "provenance": provenance,
            "retention": wrong_retention,
        },
    )


def test_canonical_hash_is_stable_and_rejects_ambiguous_values() -> None:
    models = _models()
    first = {
        "z": "终",
        "stage": models.IngestionStage.RAW_SAMPLE,
        "captured_at": _utc(10),
        "count": 2,
        "enabled": True,
    }
    second = {
        "enabled": True,
        "count": 2,
        "captured_at": _utc(10),
        "stage": models.IngestionStage.RAW_SAMPLE,
        "z": "终",
    }
    expected = (
        b'{"captured_at":"2026-07-28T10:00:00Z","count":2,'
        b'"enabled":true,"stage":"raw_sample","z":"\xe7\xbb\x88"}'
    )
    assert models.canonical_json_bytes(first) == expected, ORACLE
    assert models.canonical_json_bytes(second) == expected, ORACLE
    assert models.canonical_sha256(first) == models.canonical_sha256(second), ORACLE
    assert len(models.canonical_sha256(first)) == 64, ORACLE

    for rejected in (
        {"value": 1.5},
        {"values": {"a", "b"}},
        {1: "non-string-key"},
        {"captured_at": datetime(2026, 7, 28, 10)},
        {"captured_at": _utc(10, microsecond=1)},
    ):
        with pytest.raises((TypeError, ValueError)):
            models.canonical_json_bytes(rejected)

    assert get_args(Literal[models.IngestionStage.RAW_SAMPLE]) == (
        models.IngestionStage.RAW_SAMPLE,
    ), ORACLE


def test_device_audit_and_deletion_lifecycle_fail_closed() -> None:
    models = _models()
    identity: dict[str, object] = {
        "device_id": "device-1",
        "owner_id": "owner-1",
        "display_name": "iPhone",
        "public_key": "public-key",
        "device_key_thumbprint": "a" * 64,
        "attestation_state": "verified",
        "status": "active",
        "created_at": _utc(10),
        "last_seen_at": _utc(10),
    }
    _assert_rejected(models.DeviceIdentity, identity | {"last_seen_at": _utc(9)})
    _assert_rejected(
        models.DeviceIdentity,
        identity | {"status": "revoked", "revoked_at": _utc(9)},
    )

    audit: dict[str, object] = {
        "event_id": "event-1",
        "event_type": "device_registered",
        "owner_hash": "a" * 64,
        "device_hash": "b" * 64,
        "object_hash": "c" * 64,
        "count": 1,
        "decision": "allow",
        "result": "success",
        "reason_code": "DEVICE_REGISTERED",
        "occurred_at": _utc(10),
    }
    _assert_rejected(
        models.PrivacyAuditEvent,
        audit | {"data_types": ["step_count", "step_count"]},
    )
    _assert_rejected(
        models.PrivacyAuditEvent,
        audit | {"capabilities": ["task.read", "task.read"]},
    )
    _assert_rejected(
        models.DeletionReceipt,
        {
            "request_id": "delete-1",
            "source_hash": "d" * 64,
            "status": "completed",
            "started_at": _utc(10),
            "finished_at": _utc(9),
        },
    )


def test_device_capability_vocabulary_is_finite_through_f154() -> None:
    models = _capability_models()
    assert [capability.value for capability in models.DeviceCapability] == [
        "device.ready.read",
        "device.profile.read",
        "conversation.read",
        "conversation.send",
        "task.read",
        "approval.read",
        "approval.decide",
        "memory_candidate.read",
        "memory_candidate.decide",
        "health.review.submit",
        "health.analysis.run",
        "health.source.delete",
    ], CAPABILITY_ORACLE
    assert [audience.value for audience in models.DeviceAudience] == ["octo-gateway"], (
        CAPABILITY_ORACLE
    )
    assert all(
        "calendar" not in capability.value and "*" not in capability.value
        for capability in models.DeviceCapability
    ), CAPABILITY_ORACLE


def test_capability_grant_binds_device_owner_audience_and_short_expiry() -> None:
    models = _capability_models()
    grant = models.CapabilityGrant.model_validate(
        {
            "grant_id": "grant-1",
            "owner_id": "owner-1",
            "device_id": "device-1",
            "device_key_thumbprint": "c" * 64,
            "capabilities": ["task.read", "device.ready.read"],
            "audience": "octo-gateway",
            "issued_at": _utc(10),
            "expires_at": _utc(10) + timedelta(minutes=15),
            "token_id": "token-1",
        }
    )
    assert grant.capabilities == (
        models.DeviceCapability.DEVICE_READY_READ,
        models.DeviceCapability.TASK_READ,
    ), CAPABILITY_ORACLE
    assert set(models.CapabilityGrant.model_fields) == {
        "grant_id",
        "owner_id",
        "device_id",
        "device_key_thumbprint",
        "capabilities",
        "audience",
        "issued_at",
        "expires_at",
        "token_id",
    }, CAPABILITY_ORACLE
    assert models.CapabilityGrant.model_config["extra"] == "forbid", CAPABILITY_ORACLE
    assert models.CapabilityGrant.model_config["frozen"] is True, CAPABILITY_ORACLE


@pytest.mark.parametrize(
    "patch",
    [
        {"capabilities": ["*"]},
        {"capabilities": ["health.read"]},
        {"capabilities": ["task.read", "task.read"]},
        {"capabilities": []},
        {"audience": "octo-web"},
        {"device_key_thumbprint": "C" * 64},
        {"token_id": " "},
        {"owner_id": ""},
        {"expires_at": _utc(10)},
        {"expires_at": _utc(10) + timedelta(minutes=16)},
    ],
)
def test_capability_grant_rejects_ambient_or_broadened_authority(
    patch: dict[str, object],
) -> None:
    models = _capability_models()
    payload: dict[str, object] = {
        "grant_id": "grant-1",
        "owner_id": "owner-1",
        "device_id": "device-1",
        "device_key_thumbprint": "c" * 64,
        "capabilities": ["device.ready.read"],
        "audience": "octo-gateway",
        "issued_at": _utc(10),
        "expires_at": _utc(10) + timedelta(minutes=15),
        "token_id": "token-1",
    }
    _assert_rejected(models.CapabilityGrant, payload | patch)


def test_request_proof_payload_has_exact_canonical_fields() -> None:
    models = _proof_models()
    proof = models.RequestProofPayload.model_validate(
        {
            "method": "POST",
            "canonical_path": "/v1/device/messages",
            "body_sha256": "d" * 64,
            "timestamp": _utc(10),
            "nonce": "nonce_0123456789abcdef0123456789abcdef",
            "token_id": "token-1",
        }
    )
    assert set(models.RequestProofPayload.model_fields) == {
        "method",
        "canonical_path",
        "body_sha256",
        "timestamp",
        "nonce",
        "token_id",
    }, PROOF_ORACLE
    assert models.RequestProofPayload.model_config["extra"] == "forbid", PROOF_ORACLE
    assert models.RequestProofPayload.model_config["frozen"] is True, PROOF_ORACLE
    assert models.canonical_json_bytes(proof) == (
        b'{"body_sha256":"'
        + b"d" * 64
        + b'","canonical_path":"/v1/device/messages","method":"POST",'
        b'"nonce":"nonce_0123456789abcdef0123456789abcdef",'
        b'"timestamp":"2026-07-28T10:00:00Z","token_id":"token-1"}'
    ), PROOF_ORACLE


@pytest.mark.parametrize(
    "patch",
    [
        {"method": "post"},
        {"method": "CONNECT"},
        {"canonical_path": "v1/device/messages"},
        {"canonical_path": "https://octo.invalid/v1/device/messages"},
        {"canonical_path": "/v1//device/messages"},
        {"canonical_path": "/v1/device/../messages"},
        {"canonical_path": "/v1/device/messages?admin=true"},
        {"canonical_path": "/v1/device/messages#fragment"},
        {"canonical_path": "/v1/设备/messages"},
        {"body_sha256": "D" * 64},
        {"timestamp": datetime(2026, 7, 28, 10)},
        {"timestamp": _utc(10, microsecond=1)},
        {"nonce": "short"},
        {"token_id": " "},
        {"signature": "must-not-be-part-of-payload"},
    ],
)
def test_request_proof_payload_rejects_noncanonical_or_crypto_fields(
    patch: dict[str, object],
) -> None:
    models = _proof_models()
    payload: dict[str, object] = {
        "method": "POST",
        "canonical_path": "/v1/device/messages",
        "body_sha256": "d" * 64,
        "timestamp": _utc(10),
        "nonce": "nonce_0123456789abcdef0123456789abcdef",
        "token_id": "token-1",
    }
    _assert_rejected(models.RequestProofPayload, payload | patch)
