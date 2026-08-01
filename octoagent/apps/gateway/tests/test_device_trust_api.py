"""F153 Web owner registration/approval/revoke API 合同。"""

from __future__ import annotations

import base64
import hashlib
import importlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TypedDict, Unpack

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI

ORACLE = "F153_OWNER_REGISTRATION_ROUTES_MISSING"
MOBILE_ORACLE = "F153_MOBILE_ENROLLMENT_TOKEN_ROUTES_MISSING"
PROTECTED_ORACLE = "F153_PROTECTED_MOBILE_ROUTES_MISSING"
ROTATION_ORACLE = "F153_DEVICE_KEY_ROTATION_FLOW_MISSING"
NOW = datetime(2026, 7, 28, 14, 0, tzinfo=UTC)
PUBLIC_KEY = "B" + ("A" * 86)


class _RequestOptions(TypedDict, total=False):
    json: dict[str, Any] | None
    headers: dict[str, str] | None
    base_url: str


def _contracts() -> tuple[Any, Any]:
    try:
        service = importlib.import_module("octoagent.gateway.services.device_trust")
        routes = importlib.import_module("octoagent.gateway.routes.device_trust")
    except ModuleNotFoundError:
        pytest.fail(f"{ORACLE}: owner registration service/routes are absent", pytrace=False)
    required_service = {"DeviceTrustService", "owner_id_for_subject"}
    required_routes = {"get_device_trust_service", "require_cloudflare_owner", "router"}
    missing = sorted(
        (required_service - set(vars(service))) | (required_routes - set(vars(routes)))
    )
    if missing:
        pytest.fail(f"{ORACLE}: missing symbols={','.join(missing)}", pytrace=False)
    return service, routes


async def _app(tmp_path: Path, *, authenticated: bool) -> tuple[FastAPI, Any, Any]:
    service_module, routes = _contracts()
    core_store = importlib.import_module("octoagent.core.store")
    entropy = iter(range(1, 256))
    group = await core_store.create_store_group(
        str(tmp_path / "octo.db"),
        tmp_path / "artifacts",
    )
    service = service_module.DeviceTrustService(
        device_store=group.device_trust_store,
        audit_store=group.privacy_ingestion_store,
        options=service_module.DeviceTrustServiceOptions(
            mobile_origin="https://ios.example.test",
            clock=lambda: NOW,
            random_bytes=lambda size: bytes([next(entropy)]) * size,
            id_factory=iter(
                (
                    "challenge-1",
                    "device-1",
                    "audit-1",
                    "token-challenge-1",
                    "token-1",
                    "grant-1",
                    "audit-2",
                    "audit-3",
                    "rotation-token-challenge-1",
                    "rotation-grant-1",
                    "rotation-token-1",
                    "audit-4",
                )
            ).__next__,
        ),
    )
    app = FastAPI()
    app.state.store_group = group
    app.state.device_trust_service = service
    app.include_router(routes.router)
    if authenticated:

        async def authenticated_owner() -> str:
            return "cf-owner-subject"

        app.dependency_overrides[routes.require_cloudflare_owner] = authenticated_owner
    return app, group, service


async def _request(
    app: FastAPI,
    method: str,
    path: str,
    **options: Unpack[_RequestOptions],
) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=options.get("base_url", "https://web.example.test"),
    ) as client:
        return await client.request(
            method,
            path,
            json=options.get("json"),
            headers=options.get("headers"),
        )


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


def _enrollment_payload(
    *,
    private_key: Any,
    public_key_x963: str,
    secret: str,
) -> dict[str, Any]:
    protocol = importlib.import_module("octoagent.protocol.device_trust")
    unsigned = protocol.DeviceEnrollmentRequest(
        challenge_id="challenge-1",
        challenge_secret=secret,
        display_name="Connor iPhone",
        mobile_origin="https://ios.example.test",
        public_key_x963=public_key_x963,
        challenge_signature_der=_sign(private_key, b"temporary"),
        attestation_state="unsupported",
        timestamp=NOW,
    )
    request = unsigned.model_copy(
        update={
            "challenge_signature_der": _sign(
                private_key,
                protocol.enrollment_signature_bytes(unsigned),
            )
        }
    )
    return request.model_dump(mode="json")


async def _mobile_app(tmp_path: Path) -> tuple[FastAPI, Any, Any]:
    app, group, service = await _app(tmp_path, authenticated=True)
    routes = importlib.import_module("octoagent.gateway.routes.device_trust")
    mobile_router = getattr(routes, "mobile_router", None)
    if mobile_router is None:
        await group.close()
        pytest.fail(f"{MOBILE_ORACLE}: mobile router is absent", pytrace=False)
    app.include_router(mobile_router)
    return app, group, service


async def _registered_mobile(
    tmp_path: Path,
) -> tuple[FastAPI, Any, Any, Any, str]:
    app, group, service = await _mobile_app(tmp_path)
    created = await _request(app, "POST", "/api/device-trust/v1/challenges")
    private_key, public_key = _key_material()
    enrollment = await _request(
        app,
        "POST",
        "/api/mobile/v1/enrollments",
        json=_enrollment_payload(
            private_key=private_key,
            public_key_x963=public_key,
            secret=created.json()["challenge_secret"],
        ),
        base_url="https://ios.example.test",
    )
    assert enrollment.status_code == 202, enrollment.text
    approved = await _request(
        app,
        "POST",
        "/api/device-trust/v1/challenges/challenge-1/approve",
    )
    assert approved.status_code == 200, approved.text
    return app, group, service, private_key, public_key


async def _issued_mobile(
    tmp_path: Path,
) -> tuple[FastAPI, Any, Any, Any, str, dict[str, Any]]:
    app, group, service, private_key, public_key = await _registered_mobile(tmp_path)
    challenge = await _request(
        app,
        "POST",
        "/api/mobile/v1/token-challenges/device-1",
        base_url="https://ios.example.test",
    )
    assert challenge.status_code == 201, challenge.text
    protocol = importlib.import_module("octoagent.protocol.device_trust")
    unsigned = protocol.DeviceTokenRequest(
        token_challenge_id=challenge.json()["token_challenge_id"],
        device_id="device-1",
        server_challenge=challenge.json()["server_challenge"],
        mobile_origin="https://ios.example.test",
        challenge_signature_der=_sign(private_key, b"temporary"),
        timestamp=NOW,
    )
    signed = unsigned.model_copy(
        update={
            "challenge_signature_der": _sign(
                private_key,
                protocol.token_challenge_signature_bytes(unsigned),
            )
        }
    )
    issued = await _request(
        app,
        "POST",
        "/api/mobile/v1/tokens",
        json=signed.model_dump(mode="json"),
        base_url="https://ios.example.test",
    )
    assert issued.status_code == 201, issued.text
    return app, group, service, private_key, public_key, issued.json()


def _proof_headers(
    *,
    private_key: Any,
    token: str,
    token_id: str,
    path: str,
    nonce: str,
) -> dict[str, str]:
    models = importlib.import_module("octoagent.core.models")
    proof = models.RequestProofPayload(
        method="GET",
        canonical_path=path,
        body_sha256=hashlib.sha256(b"").hexdigest(),
        timestamp=NOW,
        nonce=nonce,
        token_id=token_id,
    )
    return {
        "Authorization": f"OctoDevice {token}",
        "X-Octo-Device-Timestamp": NOW.isoformat().replace("+00:00", "Z"),
        "X-Octo-Device-Nonce": nonce,
        "X-Octo-Device-Signature": _sign(
            private_key,
            models.canonical_json_bytes(proof),
        ),
    }


def _rotation_payload(
    *,
    challenge: dict[str, Any],
    current_private_key: Any,
    current_key_thumbprint: str,
    new_private_key: Any,
    new_public_key: str,
) -> dict[str, Any]:
    protocol = importlib.import_module("octoagent.protocol.device_trust")
    unsigned = protocol.DeviceKeyRotationRequest(
        rotation_challenge_id=challenge["rotation_challenge_id"],
        device_id=challenge["device_id"],
        server_challenge=challenge["server_challenge"],
        mobile_origin=challenge["mobile_origin"],
        current_key_thumbprint=current_key_thumbprint,
        new_public_key_x963=new_public_key,
        current_key_signature_der=_sign(current_private_key, b"temporary-current"),
        new_key_signature_der=_sign(new_private_key, b"temporary-new"),
        timestamp=NOW,
    )
    signed_bytes = protocol.key_rotation_signature_bytes(unsigned)
    return unsigned.model_copy(
        update={
            "current_key_signature_der": _sign(current_private_key, signed_bytes),
            "new_key_signature_der": _sign(new_private_key, signed_bytes),
        }
    ).model_dump(mode="json")


@pytest.mark.asyncio
async def test_owner_routes_reject_request_without_cloudflare_principal(
    tmp_path: Path,
) -> None:
    app, group, _ = await _app(tmp_path, authenticated=False)
    try:
        response = await _request(app, "POST", "/api/device-trust/v1/challenges")
        assert response.status_code in {401, 403, 503}, ORACLE
        assert response.status_code != 200, ORACLE
    finally:
        await group.close()


@pytest.mark.asyncio
async def test_challenge_returns_secret_once_but_persists_only_hash(
    tmp_path: Path,
) -> None:
    app, group, _ = await _app(tmp_path, authenticated=True)
    try:
        created = await _request(app, "POST", "/api/device-trust/v1/challenges")
        assert created.status_code == 201, created.text
        payload = created.json()
        assert set(payload) == {
            "challenge_id",
            "challenge_secret",
            "mobile_origin",
            "expires_at",
        }, ORACLE
        secret = payload["challenge_secret"]
        assert len(secret) == 43, ORACLE

        fetched = await _request(
            app,
            "GET",
            f"/api/device-trust/v1/challenges/{payload['challenge_id']}",
        )
        assert fetched.status_code == 200, fetched.text
        assert fetched.json() == {
            "challenge_id": "challenge-1",
            "state": "pending",
            "device_id": None,
            "device_key_thumbprint": None,
            "attestation_state": None,
            "reason_code": "",
        }, ORACLE
        cursor = await group.conn.execute(
            """
            SELECT secret_sha256, owner_id
            FROM device_registration_challenges
            WHERE challenge_id = 'challenge-1'
            """
        )
        row = await cursor.fetchone()
        assert str(row["secret_sha256"]) != secret, ORACLE
        assert "@" not in str(row["owner_id"]), ORACLE
    finally:
        await group.close()


@pytest.mark.asyncio
async def test_owner_approves_lists_and_revokes_only_own_pending_device(
    tmp_path: Path,
) -> None:
    service_module, _ = _contracts()
    app, group, service = await _app(tmp_path, authenticated=True)
    owner_id = service_module.owner_id_for_subject("cf-owner-subject")
    try:
        created = (await _request(app, "POST", "/api/device-trust/v1/challenges")).json()
        await group.device_trust_store.claim_registration_challenge(
            challenge_id=created["challenge_id"],
            secret_sha256=service.hash_challenge_secret(created["challenge_secret"]),
            mobile_origin=created["mobile_origin"],
            device=importlib.import_module("octoagent.core.models").DeviceIdentity(
                device_id="device-1",
                owner_id=owner_id,
                display_name="Connor iPhone",
                public_key=PUBLIC_KEY,
                device_key_thumbprint="1" * 64,
                attestation_state="unsupported",
                status="pending",
                created_at=NOW,
                last_seen_at=NOW,
            ),
            now=NOW,
        )
        status = await _request(
            app,
            "GET",
            "/api/device-trust/v1/challenges/challenge-1",
        )
        assert status.json()["device_id"] == "device-1", ORACLE

        approved = await _request(
            app,
            "POST",
            "/api/device-trust/v1/challenges/challenge-1/approve",
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["state"] == "active", ORACLE
        listed = await _request(app, "GET", "/api/device-trust/v1/devices")
        assert listed.status_code == 200, listed.text
        assert [item["device"]["device_id"] for item in listed.json()] == ["device-1"], ORACLE
        assert listed.json()[0]["capabilities"] == [
            "device.profile.read",
            "device.ready.read",
        ], ORACLE

        revoked = await _request(
            app,
            "POST",
            "/api/device-trust/v1/devices/device-1/revoke",
        )
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["device"]["status"] == "revoked", ORACLE
        assert "cf-owner-subject" not in revoked.text, ORACLE
    finally:
        await group.close()


@pytest.mark.asyncio
async def test_cross_owner_and_unknown_challenge_fail_without_state_change(
    tmp_path: Path,
) -> None:
    service_module, routes = _contracts()
    app, group, _ = await _app(tmp_path, authenticated=True)
    try:
        created = await _request(app, "POST", "/api/device-trust/v1/challenges")
        before = await group.device_trust_store.load_registration_challenge(
            created.json()["challenge_id"]
        )

        async def another_owner() -> str:
            return "another-owner-subject"

        app.dependency_overrides[routes.require_cloudflare_owner] = another_owner
        cross_owner = await _request(
            app,
            "GET",
            "/api/device-trust/v1/challenges/challenge-1",
        )
        assert cross_owner.status_code == 404, cross_owner.text
        unknown = await _request(
            app,
            "POST",
            "/api/device-trust/v1/challenges/unknown/reject",
        )
        assert unknown.status_code == 404, unknown.text
        after = await group.device_trust_store.load_registration_challenge("challenge-1")
        assert before == after, ORACLE
        assert service_module.owner_id_for_subject("cf-owner-subject") != (
            service_module.owner_id_for_subject("another-owner-subject")
        ), ORACLE
    finally:
        await group.close()


@pytest.mark.asyncio
async def test_mobile_enrollment_verifies_p256_and_consumes_owner_challenge_once(
    tmp_path: Path,
) -> None:
    app, group, _ = await _mobile_app(tmp_path)
    try:
        created = await _request(app, "POST", "/api/device-trust/v1/challenges")
        private_key, public_key = _key_material()
        payload = _enrollment_payload(
            private_key=private_key,
            public_key_x963=public_key,
            secret=created.json()["challenge_secret"],
        )
        response = await _request(
            app,
            "POST",
            "/api/mobile/v1/enrollments",
            json=payload,
            base_url="https://ios.example.test",
        )
        assert response.status_code == 202, response.text
        assert response.json()["state"] == "pending", MOBILE_ORACLE
        assert response.json()["device_id"] == "device-1", MOBILE_ORACLE

        replay = await _request(
            app,
            "POST",
            "/api/mobile/v1/enrollments",
            json=payload,
            base_url="https://ios.example.test",
        )
        assert replay.status_code == 409, replay.text
        database = (tmp_path / "octo.db").read_bytes()
        for secret in (
            created.json()["challenge_secret"],
            payload["challenge_signature_der"],
        ):
            assert secret.encode() not in database, MOBILE_ORACLE
    finally:
        await group.close()


@pytest.mark.asyncio
async def test_mobile_enrollment_wrong_host_or_signature_is_zero_write(
    tmp_path: Path,
) -> None:
    app, group, _ = await _mobile_app(tmp_path)
    try:
        created = await _request(app, "POST", "/api/device-trust/v1/challenges")
        private_key, public_key = _key_material()
        payload = _enrollment_payload(
            private_key=private_key,
            public_key_x963=public_key,
            secret=created.json()["challenge_secret"],
        )
        wrong_host = await _request(
            app,
            "POST",
            "/api/mobile/v1/enrollments",
            json=payload,
            base_url="https://web.example.test",
        )
        assert wrong_host.status_code == 404, wrong_host.text

        wrong_key, _ = _key_material(scalar=11)
        tampered = payload | {"challenge_signature_der": _sign(wrong_key, b"wrong")}
        wrong_signature = await _request(
            app,
            "POST",
            "/api/mobile/v1/enrollments",
            json=tampered,
            base_url="https://ios.example.test",
        )
        assert wrong_signature.status_code == 401, wrong_signature.text
        record = await group.device_trust_store.load_registration_challenge("challenge-1")
        assert record is not None and record.pending_device_id is None, MOBILE_ORACLE
        assert await group.device_trust_store.get_device("device-1") is None, MOBILE_ORACLE
    finally:
        await group.close()


@pytest.mark.asyncio
async def test_active_mobile_gets_single_use_short_token_stored_only_as_hash(
    tmp_path: Path,
) -> None:
    app, group, _, private_key, public_key = await _registered_mobile(tmp_path)
    try:
        challenge = await _request(
            app,
            "POST",
            "/api/mobile/v1/token-challenges/device-1",
            base_url="https://ios.example.test",
        )
        assert challenge.status_code == 201, challenge.text
        protocol = importlib.import_module("octoagent.protocol.device_trust")
        unsigned = protocol.DeviceTokenRequest(
            token_challenge_id=challenge.json()["token_challenge_id"],
            device_id="device-1",
            server_challenge=challenge.json()["server_challenge"],
            mobile_origin="https://ios.example.test",
            challenge_signature_der=_sign(private_key, b"temporary"),
            timestamp=NOW,
        )
        signed = unsigned.model_copy(
            update={
                "challenge_signature_der": _sign(
                    private_key,
                    protocol.token_challenge_signature_bytes(unsigned),
                )
            }
        )
        issued = await _request(
            app,
            "POST",
            "/api/mobile/v1/tokens",
            json=signed.model_dump(mode="json"),
            base_url="https://ios.example.test",
        )
        assert issued.status_code == 201, issued.text
        token = issued.json()["opaque_token"]
        grant = issued.json()["grant"]
        assert grant["device_id"] == "device-1", MOBILE_ORACLE
        assert grant["capabilities"] == [
            "device.profile.read",
            "device.ready.read",
        ], MOBILE_ORACLE
        assert datetime.fromisoformat(grant["expires_at"]) - datetime.fromisoformat(
            grant["issued_at"]
        ) == timedelta(minutes=15), MOBILE_ORACLE
        stored = await group.device_trust_store.get_capability_grant(
            token_sha256=hashlib.sha256(token.encode()).hexdigest(),
            now=NOW,
        )
        assert stored is not None, MOBILE_ORACLE
        assert token.encode() not in (tmp_path / "octo.db").read_bytes(), MOBILE_ORACLE
        assert (
            stored.device_key_thumbprint
            == hashlib.sha256(
                base64.urlsafe_b64decode(public_key + "=" * (-len(public_key) % 4))
            ).hexdigest()
        ), MOBILE_ORACLE

        replay = await _request(
            app,
            "POST",
            "/api/mobile/v1/tokens",
            json=signed.model_dump(mode="json"),
            base_url="https://ios.example.test",
        )
        assert replay.status_code == 409, replay.text
    finally:
        await group.close()


@pytest.mark.asyncio
async def test_mobile_routes_reject_web_cookie_and_cloudflare_service_token(
    tmp_path: Path,
) -> None:
    app, group, _, _, _ = await _registered_mobile(tmp_path)
    try:
        for headers in (
            {"Cookie": "CF_Authorization=browser-session"},
            {
                "CF-Access-Client-Id": "service-id",
                "CF-Access-Client-Secret": "service-secret",
            },
        ):
            response = await _request(
                app,
                "POST",
                "/api/mobile/v1/token-challenges/device-1",
                headers=headers,
                base_url="https://ios.example.test",
            )
            assert response.status_code == 401, response.text
            assert "browser-session" not in response.text, MOBILE_ORACLE
            assert "service-secret" not in response.text, MOBILE_ORACLE
    finally:
        await group.close()


@pytest.mark.asyncio
async def test_active_device_rotates_to_dual_proven_key_with_bounded_overlap(
    tmp_path: Path,
) -> None:
    app, group, _, current_private, _, issued = await _issued_mobile(tmp_path)
    try:
        challenge = await _request(
            app,
            "POST",
            "/api/mobile/v1/key-rotation-challenges/device-1",
            base_url="https://ios.example.test",
        )
        assert challenge.status_code == 201, challenge.text
        new_private, new_public = _key_material(scalar=13)
        payload = _rotation_payload(
            challenge=challenge.json(),
            current_private_key=current_private,
            current_key_thumbprint=issued["grant"]["device_key_thumbprint"],
            new_private_key=new_private,
            new_public_key=new_public,
        )
        rotated = await _request(
            app,
            "POST",
            "/api/mobile/v1/key-rotations",
            json=payload,
            base_url="https://ios.example.test",
        )
        assert rotated.status_code == 201, rotated.text
        response = rotated.json()
        expected_new_thumbprint = hashlib.sha256(
            base64.urlsafe_b64decode(new_public + "=" * (-len(new_public) % 4))
        ).hexdigest()
        assert response == {
            "device_id": "device-1",
            "previous_key_thumbprint": issued["grant"]["device_key_thumbprint"],
            "current_key_thumbprint": expected_new_thumbprint,
            "rotated_at": "2026-07-28T14:00:00Z",
            "overlap_expires_at": "2026-07-28T14:15:00Z",
        }, ROTATION_ORACLE
        keys = await group.device_trust_store.list_verification_keys(
            device_id="device-1",
            now=NOW,
        )
        assert {key.device_key_thumbprint for key in keys} == {
            issued["grant"]["device_key_thumbprint"],
            expected_new_thumbprint,
        }, ROTATION_ORACLE

        old_ready = await _request(
            app,
            "GET",
            "/api/mobile/v1/ready",
            headers=_proof_headers(
                private_key=current_private,
                token=issued["opaque_token"],
                token_id=issued["grant"]["token_id"],
                path="/api/mobile/v1/ready",
                nonce="o" * 32,
            ),
            base_url="https://ios.example.test",
        )
        assert old_ready.status_code == 200, old_ready.text

        token_challenge = await _request(
            app,
            "POST",
            "/api/mobile/v1/token-challenges/device-1",
            base_url="https://ios.example.test",
        )
        protocol = importlib.import_module("octoagent.protocol.device_trust")
        unsigned_token = protocol.DeviceTokenRequest(
            token_challenge_id=token_challenge.json()["token_challenge_id"],
            device_id="device-1",
            server_challenge=token_challenge.json()["server_challenge"],
            mobile_origin="https://ios.example.test",
            challenge_signature_der=_sign(new_private, b"temporary"),
            timestamp=NOW,
        )
        signed_token = unsigned_token.model_copy(
            update={
                "challenge_signature_der": _sign(
                    new_private,
                    protocol.token_challenge_signature_bytes(unsigned_token),
                )
            }
        )
        renewed = await _request(
            app,
            "POST",
            "/api/mobile/v1/tokens",
            json=signed_token.model_dump(mode="json"),
            base_url="https://ios.example.test",
        )
        assert renewed.status_code == 201, renewed.text
        assert renewed.json()["grant"]["device_key_thumbprint"] == expected_new_thumbprint

        replay = await _request(
            app,
            "POST",
            "/api/mobile/v1/key-rotations",
            json=payload,
            base_url="https://ios.example.test",
        )
        assert replay.status_code == 409, replay.text
        assert (
            len(
                await group.device_trust_store.list_verification_keys(
                    device_id="device-1",
                    now=NOW + timedelta(minutes=16),
                )
            )
            == 1
        ), ROTATION_ORACLE
    finally:
        await group.close()


@pytest.mark.asyncio
async def test_rotation_wrong_new_key_proof_is_zero_write(tmp_path: Path) -> None:
    app, group, _, current_private, _, issued = await _issued_mobile(tmp_path)
    try:
        challenge = await _request(
            app,
            "POST",
            "/api/mobile/v1/key-rotation-challenges/device-1",
            base_url="https://ios.example.test",
        )
        assert challenge.status_code == 201, challenge.text
        new_private, new_public = _key_material(scalar=13)
        wrong_private, _ = _key_material(scalar=17)
        payload = _rotation_payload(
            challenge=challenge.json(),
            current_private_key=current_private,
            current_key_thumbprint=issued["grant"]["device_key_thumbprint"],
            new_private_key=new_private,
            new_public_key=new_public,
        )
        payload["new_key_signature_der"] = _sign(wrong_private, b"wrong")
        before = await group.device_trust_store.get_device("device-1")
        rejected = await _request(
            app,
            "POST",
            "/api/mobile/v1/key-rotations",
            json=payload,
            base_url="https://ios.example.test",
        )
        assert rejected.status_code == 401, rejected.text
        assert await group.device_trust_store.get_device("device-1") == before
        keys = await group.device_trust_store.list_verification_keys(
            device_id="device-1",
            now=NOW,
        )
        assert len(keys) == 1, ROTATION_ORACLE
    finally:
        await group.close()


@pytest.mark.asyncio
async def test_protected_ready_requires_exact_device_proof_and_consumes_replay(
    tmp_path: Path,
) -> None:
    app, group, _, private_key, _, issued = await _issued_mobile(tmp_path)
    try:
        headers = _proof_headers(
            private_key=private_key,
            token=issued["opaque_token"],
            token_id=issued["grant"]["token_id"],
            path="/api/mobile/v1/ready",
            nonce="r" * 32,
        )
        ready = await _request(
            app,
            "GET",
            "/api/mobile/v1/ready",
            headers=headers,
            base_url="https://ios.example.test",
        )
        assert ready.status_code == 200, f"{PROTECTED_ORACLE}: {ready.text}"
        assert ready.json() == {
            "status": "ready",
            "device_id": "device-1",
            "server_time": "2026-07-28T14:00:00Z",
        }, PROTECTED_ORACLE

        replay = await _request(
            app,
            "GET",
            "/api/mobile/v1/ready",
            headers=headers,
            base_url="https://ios.example.test",
        )
        assert replay.status_code == 401, replay.text
        assert replay.json()["detail"]["code"] == "REQUEST_REPLAYED", PROTECTED_ORACLE
    finally:
        await group.close()


@pytest.mark.asyncio
async def test_protected_profile_is_minimal_and_capability_bound(
    tmp_path: Path,
) -> None:
    app, group, _, private_key, _, issued = await _issued_mobile(tmp_path)
    try:
        profile = await _request(
            app,
            "GET",
            "/api/mobile/v1/device-profile",
            headers=_proof_headers(
                private_key=private_key,
                token=issued["opaque_token"],
                token_id=issued["grant"]["token_id"],
                path="/api/mobile/v1/device-profile",
                nonce="p" * 32,
            ),
            base_url="https://ios.example.test",
        )
        assert profile.status_code == 200, f"{PROTECTED_ORACLE}: {profile.text}"
        assert profile.json() == {
            "device_id": "device-1",
            "display_name": "Connor iPhone",
            "attestation_state": "unsupported",
            "capabilities": [
                "device.profile.read",
                "device.ready.read",
            ],
        }, PROTECTED_ORACLE
        for forbidden in ("owner_id", "public_key", "token", "signature"):
            assert forbidden not in profile.text, PROTECTED_ORACLE
    finally:
        await group.close()


@pytest.mark.asyncio
async def test_protected_route_rejects_wrong_path_and_revoked_device(
    tmp_path: Path,
) -> None:
    app, group, _, private_key, _, issued = await _issued_mobile(tmp_path)
    try:
        wrong_path = await _request(
            app,
            "GET",
            "/api/mobile/v1/ready",
            headers=_proof_headers(
                private_key=private_key,
                token=issued["opaque_token"],
                token_id=issued["grant"]["token_id"],
                path="/api/mobile/v1/device-profile",
                nonce="w" * 32,
            ),
            base_url="https://ios.example.test",
        )
        assert wrong_path.status_code == 401, wrong_path.text

        revoked = await _request(
            app,
            "POST",
            "/api/device-trust/v1/devices/device-1/revoke",
        )
        assert revoked.status_code == 200, revoked.text
        after_revoke = await _request(
            app,
            "GET",
            "/api/mobile/v1/ready",
            headers=_proof_headers(
                private_key=private_key,
                token=issued["opaque_token"],
                token_id=issued["grant"]["token_id"],
                path="/api/mobile/v1/ready",
                nonce="v" * 32,
            ),
            base_url="https://ios.example.test",
        )
        assert after_revoke.status_code == 401, after_revoke.text
        assert after_revoke.json()["detail"]["code"] == "DEVICE_TOKEN_INVALID", PROTECTED_ORACLE
    finally:
        await group.close()
