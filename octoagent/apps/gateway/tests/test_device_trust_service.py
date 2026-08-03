"""F153 P-256、opaque token、F152 policy 与 durable replay 合同。"""

from __future__ import annotations

import base64
import hashlib
import importlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

ORACLE = "F153_DEVICE_PROOF_VERIFIER_MISSING"
NOW = datetime(2026, 7, 28, 13, 0, tzinfo=UTC)
ORIGIN = "https://ios.example.test"
SECRET = "s" * 43
TOKEN = "octo_dt1_" + ("t" * 43)


def _api() -> Any:
    try:
        module = importlib.import_module("octoagent.gateway.services.mobile_device_auth")
    except ModuleNotFoundError:
        pytest.fail(f"{ORACLE}: mobile device auth service is absent", pytrace=False)
    required = {
        "DeviceAuthError",
        "authorize_mobile_request",
        "generate_opaque_device_token",
        "opaque_token_sha256",
        "verify_enrollment_signature",
        "verify_token_challenge_signature",
    }
    missing = sorted(required - set(vars(module)))
    if missing:
        pytest.fail(f"{ORACLE}: missing symbols={','.join(missing)}", pytrace=False)
    return module


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _key_material(*, scalar: int = 7) -> tuple[Any, str]:
    private_key = ec.derive_private_key(scalar, ec.SECP256R1())
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    return private_key, _b64url(public_bytes)


def _sign(private_key: Any, payload: bytes) -> str:
    return _b64url(private_key.sign(payload, ec.ECDSA(hashes.SHA256())))


def _protocol() -> Any:
    return importlib.import_module("octoagent.protocol.device_trust")


def _models() -> Any:
    return importlib.import_module("octoagent.core.models")


async def _group(tmp_path: Path) -> Any:
    store = importlib.import_module("octoagent.core.store")
    return await store.create_store_group(
        str(tmp_path / "octo.db"),
        tmp_path / "artifacts",
    )


def _enrollment(private_key: Any, public_key_x963: str) -> Any:
    protocol = _protocol()
    unsigned = protocol.DeviceEnrollmentRequest(
        challenge_id="challenge-1",
        challenge_secret=SECRET,
        display_name="Connor iPhone",
        mobile_origin=ORIGIN,
        public_key_x963=public_key_x963,
        challenge_signature_der=_sign(private_key, b"temporary"),
        attestation_state="unsupported",
        timestamp=NOW,
    )
    return unsigned.model_copy(
        update={
            "challenge_signature_der": _sign(
                private_key,
                protocol.enrollment_signature_bytes(unsigned),
            )
        }
    )


def _token_request(private_key: Any, public_key_x963: str) -> Any:
    del public_key_x963
    protocol = _protocol()
    unsigned = protocol.DeviceTokenRequest(
        token_challenge_id="token-challenge-1",
        device_id="device-1",
        server_challenge=SECRET,
        mobile_origin=ORIGIN,
        challenge_signature_der=_sign(private_key, b"temporary"),
        timestamp=NOW + timedelta(seconds=20),
    )
    return unsigned.model_copy(
        update={
            "challenge_signature_der": _sign(
                private_key,
                protocol.token_challenge_signature_bytes(unsigned),
            )
        }
    )


async def _active_grant(
    group: Any,
    *,
    public_key_x963: str,
    capabilities: tuple[str, ...] = ("device.ready.read",),
) -> Any:
    models = _models()
    store_module = importlib.import_module("octoagent.core.store.device_trust_store")
    thumbprint = hashlib.sha256(
        base64.urlsafe_b64decode(public_key_x963 + "=" * (-len(public_key_x963) % 4))
    ).hexdigest()
    await group.device_trust_store.create_registration_challenge(
        store_module.RegistrationChallengeCreate(
            challenge_id="challenge-1",
            owner_id="owner-1",
            secret_sha256="a" * 64,
            mobile_origin=ORIGIN,
            created_at=NOW,
            expires_at=NOW + timedelta(minutes=2),
        )
    )
    await group.device_trust_store.claim_registration_challenge(
        challenge_id="challenge-1",
        secret_sha256="a" * 64,
        mobile_origin=ORIGIN,
        device=models.DeviceIdentity(
            device_id="device-1",
            owner_id="owner-1",
            display_name="Connor iPhone",
            public_key=public_key_x963,
            device_key_thumbprint=thumbprint,
            attestation_state="unsupported",
            status="pending",
            created_at=NOW,
            last_seen_at=NOW,
        ),
        now=NOW + timedelta(seconds=5),
    )
    await group.device_trust_store.approve_registration(
        challenge_id="challenge-1",
        approved_at=NOW + timedelta(seconds=10),
    )
    await group.device_trust_store.create_token_challenge(
        token_challenge_id="token-challenge-1",
        device_id="device-1",
        challenge_sha256="b" * 64,
        created_at=NOW + timedelta(seconds=11),
        expires_at=NOW + timedelta(minutes=2, seconds=11),
    )
    assert await group.device_trust_store.consume_token_challenge(
        token_challenge_id="token-challenge-1",
        device_id="device-1",
        challenge_sha256="b" * 64,
        consumed_at=NOW + timedelta(seconds=20),
    )
    grant = models.CapabilityGrant(
        grant_id="grant-1",
        owner_id="owner-1",
        device_id="device-1",
        device_key_thumbprint=thumbprint,
        capabilities=capabilities,
        audience="octo-gateway",
        issued_at=NOW + timedelta(seconds=20),
        expires_at=NOW + timedelta(minutes=15),
        token_id="token-1",
    )
    await group.device_trust_store.put_capability_grant(
        token_sha256=hashlib.sha256(TOKEN.encode()).hexdigest(),
        grant=grant,
        created_from_challenge_id="token-challenge-1",
    )
    return grant


def _proof_headers(
    private_key: Any,
    *,
    path: str = "/api/mobile/v1/ready",
    body: bytes = b"",
    signature_override: str | None = None,
) -> Any:
    models = _models()
    protocol = _protocol()
    proof = models.RequestProofPayload(
        method="GET",
        canonical_path=path,
        body_sha256=hashlib.sha256(body).hexdigest(),
        timestamp=NOW + timedelta(minutes=1),
        nonce="n" * 32,
        token_id="token-1",
    )
    signature = _sign(
        private_key,
        models.canonical_json_bytes(proof),
    )
    return protocol.DeviceProofHeaders(
        authorization=f"OctoDevice {TOKEN}",
        timestamp=proof.timestamp,
        nonce=proof.nonce,
        signature=signature_override or signature,
    )


def test_real_p256_enrollment_and_token_challenge_vectors() -> None:
    api = _api()
    private_key, public_key = _key_material()
    enrollment = _enrollment(private_key, public_key)
    token_request = _token_request(private_key, public_key)

    assert (
        api.verify_enrollment_signature(enrollment)
        == hashlib.sha256(base64.urlsafe_b64decode(public_key + "=")).hexdigest()
    ), ORACLE
    api.verify_token_challenge_signature(
        token_request,
        public_key_x963=public_key,
    )

    wrong_key, _ = _key_material(scalar=11)
    wrong = enrollment.model_copy(
        update={
            "challenge_signature_der": _sign(
                wrong_key,
                _protocol().enrollment_signature_bytes(enrollment),
            )
        }
    )
    with pytest.raises(api.DeviceAuthError, match="DEVICE_SIGNATURE_INVALID"):
        api.verify_enrollment_signature(wrong)


def test_opaque_token_codec_is_exact_injectable_and_hash_only() -> None:
    api = _api()
    token = api.generate_opaque_device_token(random_bytes=lambda size: b"\x01" * size)
    assert token == "octo_dt1_AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE", ORACLE
    assert api.opaque_token_sha256(token) == hashlib.sha256(token.encode()).hexdigest(), ORACLE
    for invalid in (
        "Bearer " + ("t" * 43),
        "octo_dt1_short",
        "octo_dt2_" + ("t" * 43),
    ):
        with pytest.raises(api.DeviceAuthError, match="DEVICE_TOKEN_INVALID"):
            api.opaque_token_sha256(invalid)


@pytest.mark.asyncio
async def test_request_proof_authorizes_once_then_durable_replay_rejects(
    tmp_path: Path,
) -> None:
    api = _api()
    private_key, public_key = _key_material()
    group = await _group(tmp_path)
    try:
        await _active_grant(group, public_key_x963=public_key)
        headers = _proof_headers(private_key)
        authorized = await api.authorize_mobile_request(
            group.device_trust_store,
            api.MobileAuthorizationRequest(
                headers=headers,
                method="GET",
                canonical_path="/api/mobile/v1/ready",
                raw_body=b"",
                required_capability="device.ready.read",
                now=NOW + timedelta(minutes=1),
            ),
        )
        assert authorized.device_id == "device-1", ORACLE
        assert authorized.token_id == "token-1", ORACLE
        with pytest.raises(api.DeviceAuthError, match="REQUEST_REPLAYED"):
            await api.authorize_mobile_request(
                group.device_trust_store,
                api.MobileAuthorizationRequest(
                    headers=headers,
                    method="GET",
                    canonical_path="/api/mobile/v1/ready",
                    raw_body=b"",
                    required_capability="device.ready.read",
                    now=NOW + timedelta(minutes=1),
                ),
            )
    finally:
        await group.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    ["body", "path", "signature", "capability", "revoked"],
)
async def test_wrong_request_fact_capability_or_device_fails_closed(
    tmp_path: Path,
    case: str,
) -> None:
    api = _api()
    private_key, public_key = _key_material()
    group = await _group(tmp_path)
    try:
        await _active_grant(group, public_key_x963=public_key)
        headers = _proof_headers(
            private_key,
            path="/api/mobile/v1/other" if case == "path" else "/api/mobile/v1/ready",
            body=b"changed" if case == "body" else b"",
            signature_override=("M" + ("A" * 94)) if case == "signature" else None,
        )
        if case == "revoked":
            await group.device_trust_store.revoke_device(
                device_id="device-1",
                revoked_at=NOW + timedelta(seconds=30),
            )
        request_kwargs: dict[str, Any] = {
            "headers": headers,
            "method": "GET",
            "canonical_path": "/api/mobile/v1/ready",
            "raw_body": b"",
            "required_capability": (
                "device.profile.read" if case == "capability" else "device.ready.read"
            ),
            "now": NOW + timedelta(minutes=1),
        }
        with pytest.raises(api.DeviceAuthError) as exc_info:
            await api.authorize_mobile_request(
                group.device_trust_store,
                api.MobileAuthorizationRequest(**request_kwargs),
            )
        assert TOKEN not in str(exc_info.value), ORACLE
        assert exc_info.value.reason_code in {
            "CAPABILITY_DENIED",
            "DEVICE_REVOKED",
            "DEVICE_SIGNATURE_INVALID",
            "PROOF_BODY_MISMATCH",
            "PROOF_PATH_MISMATCH",
            "DEVICE_TOKEN_INVALID",
        }, ORACLE
    finally:
        await group.close()


@pytest.mark.asyncio
async def test_expired_token_and_stale_timestamp_fail_without_consuming_replay(
    tmp_path: Path,
) -> None:
    api = _api()
    private_key, public_key = _key_material()
    group = await _group(tmp_path)
    try:
        await _active_grant(group, public_key_x963=public_key)
        headers = _proof_headers(private_key)
        with pytest.raises(api.DeviceAuthError) as exc_info:
            await api.authorize_mobile_request(
                group.device_trust_store,
                api.MobileAuthorizationRequest(
                    headers=headers,
                    method="GET",
                    canonical_path="/api/mobile/v1/ready",
                    raw_body=b"",
                    required_capability="device.ready.read",
                    now=NOW + timedelta(minutes=16),
                ),
            )
        assert exc_info.value.reason_code in {
            "DEVICE_TOKEN_INVALID",
            "REQUEST_TIMESTAMP_OUT_OF_WINDOW",
            "TOKEN_EXPIRED",
        }, ORACLE
        cursor = await group.conn.execute("SELECT COUNT(*) FROM device_request_replays")
        assert int((await cursor.fetchone())[0]) == 0, ORACLE
    finally:
        await group.close()
