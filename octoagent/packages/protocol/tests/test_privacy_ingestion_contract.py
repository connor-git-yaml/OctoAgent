"""F152 向 F153/F154/F155 发布的 exact JSON contract。"""

from __future__ import annotations

import importlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from octoagent.core.models import canonical_sha256

ORACLE = "F152_PROTOCOL_CONTRACT_MISSING"
NOW = datetime(2026, 7, 28, 9, 0, tzinfo=UTC)


def _contract() -> tuple[Any, Any, Any, Any]:
    protocol = importlib.import_module("octoagent.protocol")
    required = (
        "PrivacyConsumer",
        "PrivacyContractName",
        "privacy_contract_bundle",
        "validate_consumer_payload",
    )
    missing = [name for name in required if not hasattr(protocol, name)]
    if missing:
        pytest.fail(f"{ORACLE}: missing symbols={','.join(missing)}", pytrace=False)
    return (
        protocol.PrivacyConsumer,
        protocol.PrivacyContractName,
        protocol.privacy_contract_bundle,
        protocol.validate_consumer_payload,
    )


def _provenance() -> dict[str, Any]:
    return {
        "source_kind": "device_profile",
        "source_object_hash": "1" * 64,
        "owner_id": "owner-1",
        "device_id": "device-1",
        "captured_at": NOW,
        "time_range": {"start": NOW - timedelta(minutes=1), "end": NOW},
        "data_types": ["profile"],
    }


def _identity_fixtures() -> dict[str, dict[str, Any]]:
    return {
        "device_identity": {
            "device_id": "device-1",
            "owner_id": "owner-1",
            "display_name": "Connor iPhone",
            "public_key": "p256-public-key",
            "device_key_thumbprint": "a" * 64,
            "attestation_state": "unverified",
            "status": "active",
            "created_at": NOW - timedelta(days=1),
            "last_seen_at": NOW,
            "revoked_at": None,
        },
        "capability_grant": {
            "grant_id": "grant-1",
            "owner_id": "owner-1",
            "device_id": "device-1",
            "device_key_thumbprint": "a" * 64,
            "capabilities": ["device.profile.read"],
            "audience": "octo-gateway",
            "issued_at": NOW,
            "expires_at": NOW + timedelta(minutes=5),
            "token_id": "token-1",
        },
        "request_proof_payload": {
            "method": "POST",
            "canonical_path": "/v1/device/profile",
            "body_sha256": "b" * 64,
            "timestamp": NOW,
            "nonce": "n" * 32,
            "token_id": "token-1",
        },
    }


def _ingestion_fixtures() -> dict[str, dict[str, Any]]:
    provenance = _provenance()
    bundle = {
        "stage": "review_bundle",
        "bundle_id": "bundle-1",
        "purpose": "summarize profile",
        "provenance": [provenance],
        "fact_count": 1,
        "field_manifest": ["profile"],
        "preview_hash": "2" * 64,
        "retention": {
            "stage": "review_bundle",
            "created_at": NOW,
            "expires_at": NOW + timedelta(hours=1),
            "session_ends_at": None,
        },
    }
    packet = {
        "stage": "approved_analysis_packet",
        "packet_id": "packet-1",
        "purpose": bundle["purpose"],
        "facts_sha256": "3" * 64,
        "provenance": [provenance],
        "consent_id": "consent-1",
        "retention": {
            "stage": "approved_analysis_packet",
            "created_at": NOW,
            "expires_at": NOW + timedelta(minutes=5),
            "session_ends_at": None,
        },
    }
    result = {
        "stage": "analysis_result",
        "result_id": "result-1",
        "packet_id": "packet-1",
        "result_sha256": "4" * 64,
        "created_at": NOW + timedelta(minutes=1),
        "retention_state": "ephemeral",
    }
    candidate = {
        "stage": "optional_memory_candidate",
        "candidate_id": "candidate-1",
        "result_id": "result-1",
        "packet_sha256": canonical_sha256(packet),
        "provenance": [provenance],
        "user_selected_text": "Keep this summary.",
        "review_state": "pending",
    }
    return {
        "review_bundle": bundle,
        "consent_grant": {
            "consent_id": "consent-1",
            "bundle_sha256": canonical_sha256(bundle),
            "approved_packet_sha256": canonical_sha256(packet),
            "purpose": bundle["purpose"],
            "owner_id": "owner-1",
            "device_id": "device-1",
            "approved_at": NOW,
            "expires_at": NOW + timedelta(minutes=5),
            "used_at": None,
        },
        "approved_analysis_packet": packet,
        "analysis_result": result,
        "optional_memory_candidate": candidate,
        "memory_candidate_confirmation": {
            "confirmation_id": "confirmation-1",
            "candidate_sha256": canonical_sha256(candidate),
            "owner_id": "owner-1",
            "device_id": "device-1",
            "decision": "approve",
            "confirmed_at": NOW + timedelta(minutes=2),
        },
        "deletion_receipt": {
            "request_id": "delete-1",
            "source_hash": provenance["source_object_hash"],
            "deleted_object_hashes": ["2" * 64],
            "retained_audit_hashes": ["5" * 64],
            "status": "completed",
            "started_at": NOW,
            "finished_at": NOW + timedelta(minutes=1),
            "failure_reason": "",
        },
    }


def test_consumer_contract_names_are_exact_and_local_stages_are_absent() -> None:
    consumer, _, bundle, _ = _contract()
    expected = {
        consumer.F153: tuple(_identity_fixtures()),
        consumer.F154: tuple(_ingestion_fixtures()),
        consumer.F155: tuple(_ingestion_fixtures()),
    }
    for target, names in expected.items():
        document = bundle(target)
        assert document["version"] == 1
        assert document["consumer"] == target.value
        assert tuple(item["name"] for item in document["contracts"]) == names
    encoded = json.dumps(
        [bundle(target) for target in consumer],
        ensure_ascii=False,
        sort_keys=True,
    )
    assert "raw_sample" not in encoded
    assert "normalized_fact" not in encoded


def test_every_schema_is_exact_deterministic_and_mutation_isolated() -> None:
    consumer, _, bundle, _ = _contract()
    first = bundle(consumer.F154)
    second = bundle(consumer.F154)
    assert first == second
    for item in first["contracts"]:
        schema = item["json_schema"]
        assert schema["additionalProperties"] is False
        assert item["schema_sha256"] == canonical_sha256(schema)
    first["contracts"][0]["json_schema"]["title"] = "mutated"
    assert bundle(consumer.F154) == second


def test_f153_identity_fixtures_roundtrip_without_private_or_service_secret() -> None:
    consumer, names, _, validate = _contract()
    for name, payload in _identity_fixtures().items():
        model = validate(consumer.F153, names(name), payload)
        assert set(model.model_dump()) == set(payload)
    encoded = json.dumps(_identity_fixtures(), default=str, sort_keys=True)
    assert "private_key" not in encoded
    assert "service_token" not in encoded
    assert "signature" not in encoded


@pytest.mark.parametrize("consumer_name", ["F154", "F155"])
def test_ingestion_consumer_fixtures_roundtrip(
    consumer_name: str,
) -> None:
    consumer, names, _, validate = _contract()
    target = consumer(consumer_name)
    for name, payload in _ingestion_fixtures().items():
        model = validate(target, names(name), payload)
        assert set(model.model_dump()) == set(payload)


def test_consumer_cannot_import_another_feature_contract() -> None:
    consumer, names, _, validate = _contract()
    with pytest.raises(ValueError, match="CONTRACT_NOT_ALLOWED"):
        validate(
            consumer.F153,
            names.REVIEW_BUNDLE,
            _ingestion_fixtures()["review_bundle"],
        )
    with pytest.raises(ValueError, match="CONTRACT_NOT_ALLOWED"):
        validate(
            consumer.F154,
            names.REQUEST_PROOF_PAYLOAD,
            _identity_fixtures()["request_proof_payload"],
        )


def test_capability_schema_has_no_health_calendar_or_wildcard() -> None:
    consumer, _, bundle, _ = _contract()
    encoded = json.dumps(bundle(consumer.F153), sort_keys=True)
    assert "health" not in encoded.lower()
    assert "calendar" not in encoded.lower()
    assert '"*"' not in encoded
