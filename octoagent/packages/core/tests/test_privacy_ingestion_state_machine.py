from __future__ import annotations

import importlib
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

ORACLE = "F152_CONSENT_GRANT_CONTRACT_MISSING"
STATE_ORACLE = "F152_INGESTION_STATE_MACHINE_MISSING"


def _utc(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 7, 28, hour, minute, tzinfo=UTC)


def _contracts() -> tuple[Any, Any]:
    models = importlib.import_module("octoagent.core.models.privacy_ingestion")
    try:
        state = importlib.import_module("octoagent.core.privacy_ingestion")
    except ModuleNotFoundError:
        pytest.fail(f"{ORACLE}: consent state machine is absent", pytrace=False)
    required_models = {"ConsentGrant"}
    required_state = {"PrivacyIngestionError", "consume_consent"}
    missing = sorted((required_models - set(vars(models))) | (required_state - set(vars(state))))
    if missing:
        pytest.fail(f"{ORACLE}: missing symbols={','.join(missing)}", pytrace=False)
    return models, state


def _state_contracts() -> tuple[Any, Any]:
    models, state = _contracts()
    required = {
        "ApprovedPacketTransition",
        "accept_analysis_result",
        "approve_review_bundle",
        "propose_memory_candidate",
    }
    missing = sorted(required - set(vars(state)))
    if missing:
        pytest.fail(
            f"{STATE_ORACLE}: missing symbols={','.join(missing)}",
            pytrace=False,
        )
    return models, state


def _consent_payload() -> dict[str, object]:
    return {
        "consent_id": "consent-1",
        "bundle_sha256": "a" * 64,
        "approved_packet_sha256": "b" * 64,
        "purpose": "summarize recent wellness pattern",
        "owner_id": "owner-1",
        "device_id": "device-1",
        "approved_at": _utc(10),
        "expires_at": _utc(10, 15),
        "used_at": None,
    }


def _provenance(models: Any, *, device_id: str = "device-1") -> Any:
    return models.Provenance.model_validate(
        {
            "source_kind": "device_profile",
            "source_object_hash": "1" * 64,
            "owner_id": "owner-1",
            "device_id": device_id,
            "captured_at": _utc(10),
            "time_range": {"start": _utc(9), "end": _utc(10)},
            "data_types": ["activity_summary"],
        }
    )


def _review_bundle(models: Any, *, expires_at: datetime | None = None) -> Any:
    return models.ReviewBundle.model_validate(
        {
            "bundle_id": "bundle-1",
            "purpose": "summarize recent wellness pattern",
            "provenance": [_provenance(models)],
            "fact_count": 1,
            "field_manifest": ["activity_summary"],
            "preview_hash": "2" * 64,
            "retention": {
                "stage": "review_bundle",
                "created_at": _utc(10),
                "expires_at": expires_at or _utc(11),
            },
        }
    )


def _analysis_packet(models: Any, bundle: Any) -> Any:
    return models.ApprovedAnalysisPacket.model_validate(
        {
            "packet_id": "packet-1",
            "purpose": bundle.purpose,
            "facts_sha256": "3" * 64,
            "provenance": bundle.provenance,
            "consent_id": "consent-1",
            "retention": {
                "stage": "approved_analysis_packet",
                "created_at": _utc(10, 2),
                "expires_at": _utc(10, 12),
            },
        }
    )


def _flow_consent(models: Any, bundle: Any, packet: Any) -> Any:
    return models.ConsentGrant.model_validate(
        {
            "consent_id": "consent-1",
            "bundle_sha256": models.canonical_sha256(bundle),
            "approved_packet_sha256": models.canonical_sha256(packet),
            "purpose": bundle.purpose,
            "owner_id": "owner-1",
            "device_id": "device-1",
            "approved_at": _utc(10, 2),
            "expires_at": _utc(10, 12),
        }
    )


def test_consent_grant_is_exact_short_lived_and_frozen() -> None:
    models, _ = _contracts()
    grant = models.ConsentGrant.model_validate(_consent_payload())
    assert set(models.ConsentGrant.model_fields) == {
        "consent_id",
        "bundle_sha256",
        "approved_packet_sha256",
        "purpose",
        "owner_id",
        "device_id",
        "approved_at",
        "expires_at",
        "used_at",
    }, ORACLE
    assert models.ConsentGrant.model_config["extra"] == "forbid", ORACLE
    assert models.ConsentGrant.model_config["frozen"] is True, ORACLE
    assert grant.used_at is None, ORACLE

    for patch in (
        {"expires_at": _utc(10)},
        {"expires_at": _utc(10, 16)},
        {"used_at": _utc(9)},
        {"used_at": _utc(10, 16)},
        {"bundle_sha256": "A" * 64},
        {"token": "must-not-be-stored"},
    ):
        with pytest.raises(ValidationError):
            models.ConsentGrant.model_validate(_consent_payload() | patch)


def test_consent_is_consumed_once_without_mutating_original() -> None:
    models, state = _contracts()
    grant = models.ConsentGrant.model_validate(_consent_payload())
    consumed = state.consume_consent(
        grant,
        state.ConsentConsumption(
            bundle_sha256="a" * 64,
            approved_packet_sha256="b" * 64,
            purpose="summarize recent wellness pattern",
            owner_id="owner-1",
            device_id="device-1",
            used_at=_utc(10, 1),
        ),
    )
    assert grant.used_at is None, ORACLE
    assert consumed.used_at == _utc(10, 1), ORACLE
    assert consumed.consent_id == grant.consent_id, ORACLE

    with pytest.raises(state.PrivacyIngestionError) as repeated:
        state.consume_consent(
            consumed,
            state.ConsentConsumption(
                bundle_sha256="a" * 64,
                approved_packet_sha256="b" * 64,
                purpose="summarize recent wellness pattern",
                owner_id="owner-1",
                device_id="device-1",
                used_at=_utc(10, 2),
            ),
        )
    assert repeated.value.code == "CONSENT_ALREADY_USED", ORACLE


@pytest.mark.parametrize(
    ("patch", "used_at", "code"),
    [
        ({"bundle_sha256": "c" * 64}, _utc(10, 1), "CONSENT_SCOPE_MISMATCH"),
        (
            {"approved_packet_sha256": "c" * 64},
            _utc(10, 1),
            "CONSENT_SCOPE_MISMATCH",
        ),
        ({"purpose": "different purpose"}, _utc(10, 1), "CONSENT_SCOPE_MISMATCH"),
        ({"owner_id": "owner-2"}, _utc(10, 1), "CONSENT_SCOPE_MISMATCH"),
        ({"device_id": "device-2"}, _utc(10, 1), "CONSENT_SCOPE_MISMATCH"),
        ({}, _utc(9), "CONSENT_TIME_INVALID"),
        ({}, _utc(10, 15), "CONSENT_EXPIRED"),
    ],
)
def test_consent_rejects_scope_reuse_or_invalid_time(
    patch: dict[str, str],
    used_at: datetime,
    code: str,
) -> None:
    models, state = _contracts()
    grant = models.ConsentGrant.model_validate(_consent_payload())
    call = {
        "bundle_sha256": "a" * 64,
        "approved_packet_sha256": "b" * 64,
        "purpose": "summarize recent wellness pattern",
        "owner_id": "owner-1",
        "device_id": "device-1",
    } | patch
    with pytest.raises(state.PrivacyIngestionError) as rejected:
        state.consume_consent(
            grant,
            state.ConsentConsumption(**call, used_at=used_at),
        )
    assert rejected.value.code == code, ORACLE


def test_ingestion_flow_is_one_way_and_memory_requires_explicit_candidate() -> None:
    models, state = _state_contracts()
    bundle = _review_bundle(models)
    packet = _analysis_packet(models, bundle)
    consent = _flow_consent(models, bundle, packet)

    transition = state.approve_review_bundle(
        bundle,
        packet,
        consent,
        used_at=_utc(10, 3),
    )
    assert transition.packet == packet, STATE_ORACLE
    assert transition.consumed_consent.used_at == _utc(10, 3), STATE_ORACLE

    result = models.AnalysisResult.model_validate(
        {
            "result_id": "result-1",
            "packet_id": packet.packet_id,
            "result_sha256": "4" * 64,
            "created_at": _utc(10, 4),
            "retention_state": "ephemeral",
        }
    )
    accepted = state.accept_analysis_result(packet, result)
    assert accepted == result, STATE_ORACLE
    assert "memory" not in type(accepted).model_fields, STATE_ORACLE

    candidate = models.OptionalMemoryCandidate.model_validate(
        {
            "candidate_id": "candidate-1",
            "result_id": result.result_id,
            "packet_sha256": models.canonical_sha256(packet),
            "provenance": packet.provenance,
            "user_selected_text": "User selected summary only",
            "review_state": "pending",
        }
    )
    assert state.propose_memory_candidate(packet, result, candidate) == candidate, STATE_ORACLE


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        ("bundle_hash", "REVIEW_BUNDLE_MISMATCH"),
        ("packet_hash", "APPROVED_PACKET_MISMATCH"),
        ("consent_id", "CONSENT_SCOPE_MISMATCH"),
        ("provenance", "PROVENANCE_MISMATCH"),
        ("expired_bundle", "REVIEW_BUNDLE_EXPIRED"),
    ],
)
def test_review_approval_rejects_cross_stage_or_mutated_scope(
    mutate: str,
    code: str,
) -> None:
    models, state = _state_contracts()
    bundle = _review_bundle(
        models,
        expires_at=_utc(10, 2) if mutate == "expired_bundle" else None,
    )
    packet = _analysis_packet(models, bundle)
    consent = _flow_consent(models, bundle, packet)
    if mutate == "bundle_hash":
        consent = consent.model_copy(update={"bundle_sha256": "9" * 64})
    elif mutate == "packet_hash":
        consent = consent.model_copy(update={"approved_packet_sha256": "9" * 64})
    elif mutate == "consent_id":
        packet = packet.model_copy(update={"consent_id": "consent-2"})
        consent = consent.model_copy(
            update={"approved_packet_sha256": models.canonical_sha256(packet)}
        )
    elif mutate == "provenance":
        packet = packet.model_copy(
            update={"provenance": (_provenance(models, device_id="device-2"),)}
        )
        consent = consent.model_copy(
            update={"approved_packet_sha256": models.canonical_sha256(packet)}
        )

    with pytest.raises(state.PrivacyIngestionError) as rejected:
        state.approve_review_bundle(
            bundle,
            packet,
            consent,
            used_at=_utc(10, 3),
        )
    assert rejected.value.code == code, STATE_ORACLE


def test_result_and_memory_candidate_reject_wrong_lineage_or_auto_approval() -> None:
    models, state = _state_contracts()
    bundle = _review_bundle(models)
    packet = _analysis_packet(models, bundle)
    result = models.AnalysisResult.model_validate(
        {
            "result_id": "result-1",
            "packet_id": "packet-2",
            "result_sha256": "4" * 64,
            "created_at": _utc(10, 4),
            "retention_state": "ephemeral",
        }
    )
    with pytest.raises(state.PrivacyIngestionError) as wrong_result:
        state.accept_analysis_result(packet, result)
    assert wrong_result.value.code == "ANALYSIS_RESULT_LINEAGE_MISMATCH", STATE_ORACLE

    valid_result = result.model_copy(update={"packet_id": packet.packet_id})
    candidate = models.OptionalMemoryCandidate.model_validate(
        {
            "candidate_id": "candidate-1",
            "result_id": valid_result.result_id,
            "packet_sha256": models.canonical_sha256(packet),
            "provenance": packet.provenance,
            "user_selected_text": "User selected summary only",
            "review_state": "approved",
        }
    )
    with pytest.raises(state.PrivacyIngestionError) as auto_approved:
        state.propose_memory_candidate(packet, valid_result, candidate)
    assert auto_approved.value.code == "MEMORY_REVIEW_STATE_INVALID", STATE_ORACLE
