from __future__ import annotations

import importlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

ORACLE = "F154_HEALTH_PROTOCOL_MISSING"
NOW = datetime(2026, 8, 2, 1, tzinfo=UTC)
PURPOSE = "summarize_recent_activity_and_sleep"
DATA_TYPES = ("apple_health.sleep_analysis", "apple_health.step_count")
FIELD_MANIFEST = (
    "daily_steps[].local_day",
    "daily_steps[].count",
    "daily_steps[].unit",
    "sleep.window_start_utc",
    "sleep.window_end_utc",
    "sleep.total_asleep_minutes",
    "sleep.stage_minutes.awake",
    "sleep.stage_minutes.core",
    "sleep.stage_minutes.deep",
    "sleep.stage_minutes.rem",
    "sleep.stage_minutes.unspecified",
    "sleep.unit",
    "completeness_notice",
)


def _contract() -> tuple[Any, Any, Any, Any]:
    protocol = importlib.import_module("octoagent.protocol.privacy_ingestion")
    required = (
        "F154_HEALTH_DATA_TYPES",
        "F154_HEALTH_FIELD_MANIFEST",
        "F154_HEALTH_PURPOSE",
        "PrivacyConsumer",
        "PrivacyContractName",
        "privacy_contract_bundle",
        "validate_consumer_payload",
    )
    missing = [name for name in required if not hasattr(protocol, name)]
    if missing:
        pytest.fail(f"{ORACLE}: missing symbols={','.join(missing)}", pytrace=False)
    assert protocol.F154_HEALTH_DATA_TYPES == DATA_TYPES, ORACLE
    assert protocol.F154_HEALTH_FIELD_MANIFEST == FIELD_MANIFEST, ORACLE
    assert protocol.F154_HEALTH_PURPOSE == PURPOSE, ORACLE
    return (
        protocol.PrivacyConsumer,
        protocol.PrivacyContractName,
        protocol.privacy_contract_bundle,
        protocol.validate_consumer_payload,
    )


def _provenance() -> dict[str, Any]:
    return {
        "source_kind": "healthkit",
        "source_object_hash": "1" * 64,
        "owner_id": "owner-1",
        "device_id": "device-1",
        "captured_at": NOW,
        "time_range": {
            "start": NOW - timedelta(days=1),
            "end": NOW,
        },
        "data_types": list(DATA_TYPES),
    }


def _review() -> dict[str, Any]:
    return {
        "stage": "review_bundle",
        "bundle_id": "health-review-1",
        "purpose": PURPOSE,
        "provenance": [_provenance()],
        "fact_count": 2,
        "field_manifest": list(FIELD_MANIFEST),
        "preview_hash": "1" * 64,
        "retention": {
            "stage": "review_bundle",
            "created_at": NOW,
            "expires_at": NOW + timedelta(hours=24),
            "session_ends_at": None,
        },
    }


def _packet() -> dict[str, Any]:
    return {
        "stage": "approved_analysis_packet",
        "packet_id": "health-packet-1",
        "purpose": PURPOSE,
        "facts_sha256": "2" * 64,
        "provenance": [_provenance()],
        "consent_id": "health-consent-1",
        "retention": {
            "stage": "approved_analysis_packet",
            "created_at": NOW,
            "expires_at": NOW + timedelta(minutes=15),
            "session_ends_at": None,
        },
    }


def test_f154_consumer_schema_is_health_specific_and_deterministic() -> None:
    consumer, names, bundle, validate = _contract()
    review = validate(consumer.F154, names.REVIEW_BUNDLE, _review())
    packet = validate(consumer.F154, names.APPROVED_ANALYSIS_PACKET, _packet())
    assert review.preview_hash == review.provenance[0].source_object_hash, ORACLE
    assert packet.provenance == review.provenance, ORACLE

    first = bundle(consumer.F154)
    assert first == bundle(consumer.F154), ORACLE
    encoded = json.dumps(first, ensure_ascii=False, sort_keys=True)
    for required in (*DATA_TYPES, *FIELD_MANIFEST, PURPOSE):
        assert required in encoded, ORACLE
    for forbidden in ("sample_id", "source_revision", "raw_timestamp", "heart_rate"):
        assert forbidden not in encoded, ORACLE


@pytest.mark.parametrize(
    ("contract_name", "patch"),
    [
        ("review_bundle", {"purpose": "diagnose_sleep_disorder"}),
        ("review_bundle", {"raw_samples": [{"timestamp": "secret"}]}),
        ("review_bundle", {"field_manifest": ["daily_steps[].raw_timestamp"]}),
        (
            "review_bundle",
            {"provenance": [_provenance() | {"source_kind": "device_profile"}]},
        ),
        (
            "review_bundle",
            {"provenance": [_provenance() | {"data_types": ["apple_health.step_count"]}]},
        ),
        ("approved_analysis_packet", {"purpose": "summarize_any_health_data"}),
        (
            "approved_analysis_packet",
            {"provenance": [_provenance() | {"sample_uuid": "forbidden"}]},
        ),
    ],
)
def test_f154_consumer_rejects_raw_or_broadened_health_payloads(
    contract_name: str,
    patch: dict[str, Any],
) -> None:
    consumer, names, _, validate = _contract()
    payload = _review() if contract_name == "review_bundle" else _packet()
    with pytest.raises(ValidationError):
        validate(consumer.F154, names(contract_name), payload | patch)
