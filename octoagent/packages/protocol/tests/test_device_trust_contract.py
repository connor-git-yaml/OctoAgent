"""F153 原生设备注册与请求证明的 exact transport contract。"""

from __future__ import annotations

import hashlib
import importlib
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

ORACLE = "F153_DEVICE_TRUST_CONTRACT_MISSING"
NOW = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
MOBILE_ORIGIN = "https://ios.example.test"
PUBLIC_KEY = "B" + ("A" * 86)
SIGNATURE = "M" + ("A" * 94)
SECRET = "s" * 43


def _contract() -> Any:
    try:
        module = importlib.import_module("octoagent.protocol.device_trust")
    except ModuleNotFoundError:
        pytest.fail(f"{ORACLE}: device trust protocol module is absent", pytrace=False)
    required = (
        "DeviceEnrollmentRequest",
        "DeviceEnrollmentStatus",
        "DeviceEnrollmentStatusResponse",
        "DeviceProofHeaders",
        "DeviceTokenChallengeResponse",
        "DeviceTokenRequest",
        "DeviceTokenResponse",
        "MobileDeviceProfileResponse",
        "MobileReadyResponse",
        "OwnerRegistrationChallengeResponse",
        "enrollment_signature_bytes",
        "token_challenge_signature_bytes",
    )
    missing = [name for name in required if not hasattr(module, name)]
    if missing:
        pytest.fail(f"{ORACLE}: missing symbols={','.join(missing)}", pytrace=False)
    return module


def _challenge(module: Any) -> Any:
    return module.OwnerRegistrationChallengeResponse(
        challenge_id="challenge-1",
        challenge_secret=SECRET,
        mobile_origin=MOBILE_ORIGIN,
        expires_at=NOW + timedelta(minutes=2),
    )


def _enrollment(module: Any) -> Any:
    return module.DeviceEnrollmentRequest(
        challenge_id="challenge-1",
        challenge_secret=SECRET,
        display_name="Connor iPhone",
        mobile_origin=MOBILE_ORIGIN,
        public_key_x963=PUBLIC_KEY,
        challenge_signature_der=SIGNATURE,
        attestation_state="unsupported",
        attestation_object=None,
        timestamp=NOW,
    )


def test_registration_and_enrollment_contract_is_exact_and_canonical() -> None:
    module = _contract()
    challenge = _challenge(module)
    enrollment = _enrollment(module)

    assert set(challenge.model_dump()) == {
        "challenge_id",
        "challenge_secret",
        "mobile_origin",
        "expires_at",
    }
    assert set(enrollment.model_dump()) == {
        "challenge_id",
        "challenge_secret",
        "display_name",
        "mobile_origin",
        "public_key_x963",
        "challenge_signature_der",
        "attestation_state",
        "attestation_object",
        "timestamp",
    }
    expected = (
        b'{"challenge_id":"challenge-1","challenge_secret_sha256":"'
        + hashlib.sha256(SECRET.encode()).hexdigest().encode()
        + b'","display_name":"Connor iPhone","mobile_origin":"https://ios.example.test",'
        + b'"public_key_x963":"'
        + PUBLIC_KEY.encode()
        + b'","timestamp":"2026-07-28T10:00:00Z"}'
    )
    assert module.enrollment_signature_bytes(enrollment) == expected
    assert enrollment.challenge_signature_der not in expected.decode()
    assert enrollment.challenge_secret not in expected.decode()


def test_token_challenge_signature_and_response_reuse_f152_grant() -> None:
    module = _contract()
    challenge = module.DeviceTokenChallengeResponse(
        token_challenge_id="token-challenge-1",
        device_id="device-1",
        server_challenge=SECRET,
        mobile_origin=MOBILE_ORIGIN,
        expires_at=NOW + timedelta(minutes=2),
    )
    request = module.DeviceTokenRequest(
        token_challenge_id=challenge.token_challenge_id,
        device_id=challenge.device_id,
        server_challenge=challenge.server_challenge,
        mobile_origin=challenge.mobile_origin,
        challenge_signature_der=SIGNATURE,
        timestamp=NOW,
    )
    expected = (
        b'{"device_id":"device-1","mobile_origin":"https://ios.example.test",'
        b'"server_challenge_sha256":"'
        + hashlib.sha256(SECRET.encode()).hexdigest().encode()
        + b'","timestamp":"2026-07-28T10:00:00Z",'
        b'"token_challenge_id":"token-challenge-1"}'
    )
    assert module.token_challenge_signature_bytes(request) == expected

    response = module.DeviceTokenResponse(
        opaque_token="octo_dt1_" + ("t" * 43),
        grant={
            "grant_id": "grant-1",
            "owner_id": "owner-1",
            "device_id": "device-1",
            "device_key_thumbprint": "a" * 64,
            "capabilities": ["device.ready.read"],
            "audience": "octo-gateway",
            "issued_at": NOW,
            "expires_at": NOW + timedelta(minutes=15),
            "token_id": "token-1",
        },
    )
    assert response.grant.device_id == "device-1"
    assert response.grant.expires_at - response.grant.issued_at == timedelta(minutes=15)
    assert response.opaque_token not in repr(response)


def test_status_proof_and_ready_projection_are_strict() -> None:
    module = _contract()
    open_challenge = module.DeviceEnrollmentStatusResponse(
        challenge_id="challenge-open",
        state=module.DeviceEnrollmentStatus.PENDING,
    )
    status = module.DeviceEnrollmentStatusResponse(
        challenge_id="challenge-1",
        state=module.DeviceEnrollmentStatus.PENDING,
        device_id="device-1",
        device_key_thumbprint="a" * 64,
        attestation_state="unsupported",
        reason_code="",
    )
    headers = module.DeviceProofHeaders(
        authorization="OctoDevice octo_dt1_" + ("t" * 43),
        timestamp=NOW,
        nonce="n" * 32,
        signature=SIGNATURE,
    )
    ready = module.MobileReadyResponse(
        status="ready",
        device_id="device-1",
        server_time=NOW,
    )
    profile = module.MobileDeviceProfileResponse(
        device_id="device-1",
        display_name="Connor iPhone",
        attestation_state="unsupported",
        capabilities=["device.ready.read", "device.profile.read"],
    )
    assert open_challenge.device_id is None
    assert status.state.value == "pending"
    assert headers.authorization.startswith("OctoDevice ")
    assert ready.status == "ready"
    assert profile.capabilities == (
        "device.profile.read",
        "device.ready.read",
    )
    assert "owner_id" not in profile.model_dump()
    assert "public_key" not in profile.model_dump()
    assert headers.authorization not in repr(headers)
    assert headers.signature not in repr(headers)

    with pytest.raises(ValidationError):
        module.MobileReadyResponse(
            status="ready",
            device_id="device-1",
            server_time=NOW,
            extra=True,
        )


@pytest.mark.parametrize("case", ["short-secret", "http-origin", "wrong-key", "bearer"])
def test_ambiguous_or_weak_transport_values_fail_closed(case: str) -> None:
    module = _contract()
    if case == "short-secret":
        model_name = "OwnerRegistrationChallengeResponse"
        payload: dict[str, Any] = {
            "challenge_id": "challenge-1",
            "challenge_secret": "short",
            "mobile_origin": MOBILE_ORIGIN,
            "expires_at": NOW + timedelta(minutes=2),
        }
    elif case in {"http-origin", "wrong-key"}:
        model_name = "DeviceEnrollmentRequest"
        payload = _enrollment(module).model_dump()
        payload["mobile_origin" if case == "http-origin" else "public_key_x963"] = (
            "http://ios.example.test" if case == "http-origin" else "A" * 87
        )
    else:
        model_name = "DeviceProofHeaders"
        payload = {
            "authorization": "Bearer " + ("t" * 43),
            "timestamp": NOW,
            "nonce": "n" * 32,
            "signature": SIGNATURE,
        }
    with pytest.raises(ValidationError):
        getattr(module, model_name).model_validate(payload)


def test_schema_has_no_web_cookie_service_token_or_health_calendar() -> None:
    module = _contract()
    names = (
        "OwnerRegistrationChallengeResponse",
        "DeviceEnrollmentRequest",
        "DeviceEnrollmentStatusResponse",
        "DeviceTokenChallengeResponse",
        "DeviceTokenRequest",
        "DeviceTokenResponse",
        "DeviceProofHeaders",
        "MobileDeviceProfileResponse",
        "MobileReadyResponse",
    )
    encoded = " ".join(str(getattr(module, name).model_json_schema()) for name in names)
    for forbidden in (
        "CF_Authorization",
        "CF-Access-Client",
        "service_token",
        "healthkit",
        "eventkit",
        "private_key",
    ):
        assert forbidden.lower() not in encoded.lower()
