from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

ORACLE = "F154_HEALTH_PROTOCOL_MISSING"
HEALTH_CAPABILITIES = (
    "health.review.submit",
    "health.analysis.run",
    "health.source.delete",
)


def _models() -> Any:
    models = importlib.import_module("octoagent.core.models.privacy_ingestion")
    values = tuple(capability.value for capability in models.DeviceCapability)
    if not set(HEALTH_CAPABILITIES).issubset(values):
        pytest.fail(f"{ORACLE}: exact health capabilities are absent", pytrace=False)
    return models


def test_health_capability_extension_is_exact_and_finite() -> None:
    models = _models()
    assert tuple(capability.value for capability in models.DeviceCapability) == (
        "device.ready.read",
        "device.profile.read",
        "conversation.read",
        "conversation.send",
        "task.read",
        "approval.read",
        "approval.decide",
        "memory_candidate.read",
        "memory_candidate.decide",
        *HEALTH_CAPABILITIES,
    ), ORACLE
    assert all("calendar" not in capability.value for capability in models.DeviceCapability), ORACLE

    now = datetime(2026, 8, 2, tzinfo=UTC)
    grant = models.CapabilityGrant(
        grant_id="grant-health",
        owner_id="owner-1",
        device_id="device-1",
        device_key_thumbprint="a" * 64,
        capabilities=tuple(reversed(HEALTH_CAPABILITIES)),
        audience="octo-gateway",
        issued_at=now,
        expires_at=now + timedelta(minutes=15),
        token_id="token-health",
    )
    assert tuple(capability.value for capability in grant.capabilities) == tuple(
        sorted(HEALTH_CAPABILITIES)
    ), ORACLE

    for invalid in ("health.*", "health.read", "calendar.review.submit"):
        with pytest.raises(ValidationError):
            models.CapabilityGrant.model_validate(
                grant.model_dump(mode="python") | {"capabilities": [invalid]}
            )
