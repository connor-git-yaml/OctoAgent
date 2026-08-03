from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

ORACLE = "F154_HEALTH_PROTOCOL_MISSING"
NOW = datetime(2026, 8, 2, 2, tzinfo=UTC)
PURPOSE = "summarize_recent_activity_and_sleep"


def _contracts() -> tuple[Any, Any, Any, Any]:
    models = importlib.import_module("octoagent.core.models")
    state = importlib.import_module("octoagent.core.privacy_ingestion")
    policy = importlib.import_module("octoagent.policy")
    protocol = importlib.import_module("octoagent.protocol")
    required_capabilities = {
        "health.review.submit",
        "health.analysis.run",
        "health.source.delete",
    }
    if not required_capabilities.issubset(
        capability.value for capability in models.DeviceCapability
    ):
        pytest.fail(f"{ORACLE}: health capability vocabulary is absent", pytrace=False)
    return models, state, policy, protocol


def _provenance() -> dict[str, Any]:
    return {
        "source_kind": "healthkit",
        "source_object_hash": "1" * 64,
        "owner_id": "owner-1",
        "device_id": "device-1",
        "captured_at": NOW,
        "time_range": {"start": NOW - timedelta(days=1), "end": NOW},
        "data_types": ["apple_health.sleep_analysis", "apple_health.step_count"],
    }


def _review_payload() -> dict[str, Any]:
    return {
        "bundle_id": "health-review-1",
        "purpose": PURPOSE,
        "provenance": [_provenance()],
        "fact_count": 2,
        "field_manifest": ["daily_steps[].count", "completeness_notice"],
        "preview_hash": "1" * 64,
        "retention": {
            "stage": "review_bundle",
            "created_at": NOW,
            "expires_at": NOW + timedelta(hours=24),
        },
    }


def _packet_payload() -> dict[str, Any]:
    return {
        "packet_id": "health-packet-1",
        "purpose": PURPOSE,
        "facts_sha256": "2" * 64,
        "provenance": [_provenance()],
        "consent_id": "health-consent-1",
        "retention": {
            "stage": "approved_analysis_packet",
            "created_at": NOW,
            "expires_at": NOW + timedelta(minutes=15),
        },
    }


def test_health_capability_uses_existing_device_authorization_policy() -> None:
    models, _, policy, _ = _contracts()
    device = models.DeviceIdentity(
        device_id="device-1",
        owner_id="owner-1",
        display_name="Connor iPhone",
        public_key="p256-public-key",
        device_key_thumbprint="a" * 64,
        attestation_state="unverified",
        status="active",
        created_at=NOW - timedelta(days=1),
        last_seen_at=NOW,
    )
    capability = models.DeviceCapability.HEALTH_REVIEW_SUBMIT
    grant = models.CapabilityGrant(
        grant_id="grant-health",
        owner_id="owner-1",
        device_id="device-1",
        device_key_thumbprint="a" * 64,
        capabilities=(capability,),
        audience="octo-gateway",
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=15),
        token_id="token-health",
    )
    proof = models.RequestProofPayload(
        method="POST",
        canonical_path="/api/mobile/v1/health/reviews",
        body_sha256="b" * 64,
        timestamp=NOW,
        nonce="n" * 32,
        token_id="token-health",
    )
    authorized = policy.authorize_device_request(
        device=device,
        grant=grant,
        proof=proof,
        context=policy.DeviceAuthorizationContext(
            required_capability=capability,
            expected_method=models.HttpMethod.POST,
            expected_path=proof.canonical_path,
            expected_body_sha256=proof.body_sha256,
            expected_audience=models.DeviceAudience.OCTO_GATEWAY,
            now=NOW,
            used_replay_keys=frozenset(),
        ),
    )
    assert authorized.capability is capability, ORACLE

    with pytest.raises(policy.PrivacyIngestionPolicyError) as denied:
        policy.authorize_device_request(
            device=device,
            grant=grant,
            proof=proof,
            context=policy.DeviceAuthorizationContext(
                required_capability=models.DeviceCapability.HEALTH_ANALYSIS_RUN,
                expected_method=models.HttpMethod.POST,
                expected_path=proof.canonical_path,
                expected_body_sha256=proof.body_sha256,
                expected_audience=models.DeviceAudience.OCTO_GATEWAY,
                now=NOW,
                used_replay_keys=frozenset(),
            ),
        )
    assert denied.value.reason_code == "CAPABILITY_DENIED", ORACLE


def test_health_consent_and_provenance_reuse_f152_lineage() -> None:
    models, state, _, protocol = _contracts()
    review = protocol.validate_consumer_payload(
        protocol.PrivacyConsumer.F154,
        protocol.PrivacyContractName.REVIEW_BUNDLE,
        _review_payload(),
    )
    packet = protocol.validate_consumer_payload(
        protocol.PrivacyConsumer.F154,
        protocol.PrivacyContractName.APPROVED_ANALYSIS_PACKET,
        _packet_payload(),
    )
    consent = protocol.validate_consumer_payload(
        protocol.PrivacyConsumer.F154,
        protocol.PrivacyContractName.CONSENT_GRANT,
        {
            "consent_id": "health-consent-1",
            "bundle_sha256": models.canonical_sha256(review),
            "approved_packet_sha256": models.canonical_sha256(packet),
            "purpose": PURPOSE,
            "owner_id": "owner-1",
            "device_id": "device-1",
            "approved_at": NOW,
            "expires_at": NOW + timedelta(minutes=15),
            "used_at": None,
        },
    )
    transition = state.approve_review_bundle(
        review,
        packet,
        consent,
        used_at=NOW + timedelta(minutes=1),
    )
    assert transition.packet == packet, ORACLE
    assert transition.consumed_consent.used_at == NOW + timedelta(minutes=1), ORACLE

    drifted = packet.model_copy(update={"facts_sha256": "3" * 64})
    with pytest.raises(state.PrivacyIngestionError) as rejected:
        state.approve_review_bundle(
            review,
            drifted,
            consent,
            used_at=NOW + timedelta(minutes=1),
        )
    assert rejected.value.code == "APPROVED_PACKET_MISMATCH", ORACLE
