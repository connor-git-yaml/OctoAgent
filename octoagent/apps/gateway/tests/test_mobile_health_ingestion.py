"""F154 mobile health review route 的 Host/proof/store/audit 合同。"""

from __future__ import annotations

import base64
import hashlib
import importlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI

ORACLE = "F154_HEALTH_REVIEW_ROUTE_MISSING"
NOW = datetime(2026, 8, 2, 1, 0, tzinfo=UTC)
MOBILE_ORIGIN = "https://ios.example.test"
REVIEW_PATH = "/api/mobile/v1/health/reviews"
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


def _contracts() -> tuple[Any, Any]:
    try:
        service = importlib.import_module("octoagent.gateway.services.health_ingestion")
        routes = importlib.import_module("octoagent.gateway.routes.mobile_health")
    except ModuleNotFoundError:
        pytest.fail(f"{ORACLE}: health review service/routes are absent", pytrace=False)
    missing = {
        "HealthIngestionError",
        "HealthIngestionService",
        "HealthIngestionServiceOptions",
    } - set(vars(service))
    missing |= {"get_health_ingestion_service", "router"} - set(vars(routes))
    if missing:
        pytest.fail(f"{ORACLE}: missing symbols={','.join(sorted(missing))}", pytrace=False)
    return service, routes


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _key_material() -> tuple[Any, str]:
    private_key = ec.derive_private_key(7, ec.SECP256R1())
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    return private_key, _b64url(public_bytes)


def _sign(private_key: Any, payload: bytes) -> str:
    return _b64url(private_key.sign(payload, ec.ECDSA(hashes.SHA256())))


def _review() -> dict[str, Any]:
    owner_id = hashlib.sha256(b"cloudflare-access:cf-owner-subject").hexdigest()
    return {
        "stage": "review_bundle",
        "bundle_id": "health-review-1",
        "purpose": PURPOSE,
        "provenance": [
            {
                "source_kind": "healthkit",
                "source_object_hash": "1" * 64,
                "owner_id": owner_id,
                "device_id": "device-1",
                "captured_at": NOW.isoformat().replace("+00:00", "Z"),
                "time_range": {
                    "start": (NOW - timedelta(days=1)).isoformat().replace("+00:00", "Z"),
                    "end": NOW.isoformat().replace("+00:00", "Z"),
                },
                "data_types": list(DATA_TYPES),
            }
        ],
        "fact_count": 2,
        "field_manifest": list(FIELD_MANIFEST),
        "preview_hash": "1" * 64,
        "retention": {
            "stage": "review_bundle",
            "created_at": NOW.isoformat().replace("+00:00", "Z"),
            "expires_at": (NOW + timedelta(hours=24)).isoformat().replace("+00:00", "Z"),
            "session_ends_at": None,
        },
    }


def _canonical_body(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _enrollment_request(private_key: Any, public_key: str, secret: str) -> Any:
    protocol = importlib.import_module("octoagent.protocol.device_trust")
    unsigned = protocol.DeviceEnrollmentRequest(
        challenge_id="registration-1",
        challenge_secret=secret,
        display_name="Connor iPhone",
        mobile_origin=MOBILE_ORIGIN,
        public_key_x963=public_key,
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


async def _put_grant(
    group: Any,
    *,
    device: Any,
    capability: str,
    suffix: str,
) -> tuple[str, str]:
    models = importlib.import_module("octoagent.core.models")
    auth = importlib.import_module("octoagent.gateway.services.mobile_device_auth")
    token = "octo_dt1_" + _b64url(hashlib.sha256(suffix.encode()).digest())
    challenge = f"token-challenge-{suffix}"
    challenge_hash = hashlib.sha256(challenge.encode()).hexdigest()
    await group.device_trust_store.create_token_challenge(
        token_challenge_id=challenge,
        device_id=device.device_id,
        challenge_sha256=challenge_hash,
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=2),
    )
    consumed = await group.device_trust_store.consume_token_challenge(
        token_challenge_id=challenge,
        device_id=device.device_id,
        challenge_sha256=challenge_hash,
        consumed_at=NOW,
    )
    assert consumed, ORACLE
    grant = models.CapabilityGrant(
        grant_id=f"grant-{suffix}",
        owner_id=device.owner_id,
        device_id=device.device_id,
        device_key_thumbprint=device.device_key_thumbprint,
        capabilities=(capability,),
        audience="octo-gateway",
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=15),
        token_id=f"token-{suffix}",
    )
    await group.device_trust_store.put_capability_grant(
        token_sha256=auth.opaque_token_sha256(token),
        grant=grant,
        created_from_challenge_id=challenge,
    )
    return token, grant.token_id


async def _registered_device(group: Any) -> tuple[Any, Any, Any]:
    trust = importlib.import_module("octoagent.gateway.services.device_trust")
    ids = iter(("registration-1", "device-1", "device-audit-1"))
    service = trust.DeviceTrustService(
        device_store=group.device_trust_store,
        audit_store=group.privacy_ingestion_store,
        options=trust.DeviceTrustServiceOptions(
            mobile_origin=MOBILE_ORIGIN,
            clock=lambda: NOW,
            random_bytes=lambda size: b"r" * size,
            id_factory=ids.__next__,
        ),
    )
    created = await service.create_registration_challenge(owner_subject="cf-owner-subject")
    private_key, public_key = _key_material()
    await service.submit_enrollment(
        _enrollment_request(private_key, public_key, created.challenge_secret)
    )
    await service.approve_registration(
        owner_subject="cf-owner-subject",
        challenge_id=created.challenge_id,
    )
    device = await group.device_trust_store.get_device("device-1")
    assert device is not None, ORACLE
    return service, device, private_key


async def _prepared_app(tmp_path: Path) -> tuple[FastAPI, Any, Any, str, str]:
    health, routes = _contracts()
    core_store = importlib.import_module("octoagent.core.store")
    access = importlib.import_module("octoagent.gateway.services.mobile_device_access")
    group = await core_store.create_store_group(
        str(tmp_path / "octo.db"),
        tmp_path / "artifacts",
    )
    device_service, device, private_key = await _registered_device(group)
    token, token_id = await _put_grant(
        group,
        device=device,
        capability="health.review.submit",
        suffix="health",
    )
    service = health.HealthIngestionService(
        device_store=group.device_trust_store,
        privacy_store=group.privacy_ingestion_store,
        options=health.HealthIngestionServiceOptions(
            clock=lambda: NOW,
            id_factory=iter(("health-audit-1", "health-audit-2")).__next__,
        ),
    )
    app = FastAPI()
    app.state.device_trust_service = device_service
    app.state.health_ingestion_service = service
    app.state.mobile_device_access_manifest = access.MobileDeviceAccessManifestV1(
        version=1,
        web_hostname="web.example.test",
        mobile_hostname="ios.example.test",
        tunnel_id=UUID("11111111-1111-4111-8111-111111111111"),
        loopback_origin="http://127.0.0.1:8000",
        mobile_path_prefix="/api/mobile/v1/",
        edge_policy="access-bypass-origin-device-proof",
    )
    app.add_middleware(access.MobileDeviceAccessMiddleware)
    app.include_router(routes.router)
    return app, group, private_key, token, token_id


def _proof_headers(
    private_key: Any,
    *,
    token: str,
    token_id: str,
    body: bytes,
    nonce: str,
) -> dict[str, str]:
    models = importlib.import_module("octoagent.core.models")
    proof = models.RequestProofPayload(
        method="POST",
        canonical_path=REVIEW_PATH,
        body_sha256=hashlib.sha256(body).hexdigest(),
        timestamp=NOW,
        nonce=nonce,
        token_id=token_id,
    )
    return {
        "Authorization": f"OctoDevice {token}",
        "Content-Type": "application/json",
        "X-Octo-Device-Timestamp": NOW.isoformat().replace("+00:00", "Z"),
        "X-Octo-Device-Nonce": nonce,
        "X-Octo-Device-Signature": _sign(
            private_key,
            models.canonical_json_bytes(proof),
        ),
    }


async def _post(app: FastAPI, body: bytes, headers: dict[str, str]) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=MOBILE_ORIGIN,
    ) as client:
        return await client.post(REVIEW_PATH, content=body, headers=headers)


async def _review_rows(group: Any) -> list[Any]:
    cursor = await group.conn.execute(
        "SELECT object_hash, content, expires_at FROM privacy_review_bundles"
    )
    return list(await cursor.fetchall())


@pytest.mark.asyncio
async def test_health_review_requires_mobile_host_proof_and_exact_capability(
    tmp_path: Path,
) -> None:
    app, group, private_key, token, token_id = await _prepared_app(tmp_path)
    body = _canonical_body(_review())
    try:
        valid = _proof_headers(
            private_key,
            token=token,
            token_id=token_id,
            body=body,
            nonce="valid-health-review-nonce-000001",
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://web.example.test",
        ) as client:
            wrong_host = await client.post(REVIEW_PATH, content=body, headers=valid)
        assert wrong_host.status_code == 404, ORACLE
        missing_proof = await _post(app, body, {"Content-Type": "application/json"})
        assert missing_proof.status_code == 401, ORACLE

        device = await group.device_trust_store.get_device("device-1")
        assert device is not None, ORACLE
        wrong_token, wrong_token_id = await _put_grant(
            group,
            device=device,
            capability="device.ready.read",
            suffix="wrong-capability",
        )
        wrong_capability = await _post(
            app,
            body,
            _proof_headers(
                private_key,
                token=wrong_token,
                token_id=wrong_token_id,
                body=body,
                nonce="wrong-health-capability-nonce-01",
            ),
        )
        assert wrong_capability.status_code == 401, ORACLE
        assert wrong_capability.json()["detail"]["code"] == "HEALTH_CAPABILITY_DENIED", ORACLE
        assert await _review_rows(group) == [], ORACLE
    finally:
        await group.close()


@pytest.mark.asyncio
async def test_health_review_stores_exact_bundle_and_metadata_only_audit(
    tmp_path: Path,
) -> None:
    app, group, private_key, token, token_id = await _prepared_app(tmp_path)
    body = _canonical_body(_review())
    try:
        response = await _post(
            app,
            body,
            _proof_headers(
                private_key,
                token=token,
                token_id=token_id,
                body=body,
                nonce="stored-health-review-nonce-00001",
            ),
        )
        assert response.status_code == 201, response.text
        payload = response.json()
        assert set(payload) == {"bundle_sha256", "expires_at"}, ORACLE
        rows = await _review_rows(group)
        assert len(rows) == 1 and rows[0]["object_hash"] == payload["bundle_sha256"], ORACLE
        assert rows[0]["expires_at"] == "2026-08-03T01:00:00+00:00", ORACLE
        stored = json.loads(rows[0]["content"])
        assert stored == _review(), ORACLE
        cursor = await group.conn.execute(
            """
            SELECT count, data_types, capabilities, decision, result, reason_code
            FROM privacy_ingestion_audit
            WHERE reason_code = 'HEALTH_REVIEW_STORED'
            """
        )
        audit = await cursor.fetchone()
        assert audit is not None, ORACLE
        assert dict(audit) == {
            "count": 2,
            "data_types": json.dumps(DATA_TYPES, separators=(",", ":")),
            "capabilities": '["health.review.submit"]',
            "decision": "allow",
            "result": "success",
            "reason_code": "HEALTH_REVIEW_STORED",
        }, ORACLE
        assert not any(
            term in rows[0]["content"]
            for term in ("sample_id", "raw_timestamp", "heart_rate", "owner@example")
        ), ORACLE
    finally:
        await group.close()


@pytest.mark.asyncio
async def test_health_review_replay_and_revoked_device_are_zero_write(
    tmp_path: Path,
) -> None:
    app, group, private_key, token, token_id = await _prepared_app(tmp_path)
    body = _canonical_body(_review())
    try:
        headers = _proof_headers(
            private_key,
            token=token,
            token_id=token_id,
            body=body,
            nonce="replayed-health-review-nonce-0001",
        )
        accepted = await _post(app, body, headers)
        assert accepted.status_code == 201, accepted.text
        replayed = await _post(app, body, headers)
        assert replayed.status_code == 401, ORACLE
        assert replayed.json()["detail"]["code"] == "HEALTH_REQUEST_REPLAYED", ORACLE
        assert len(await _review_rows(group)) == 1, ORACLE

        await group.device_trust_store.revoke_device(
            device_id="device-1",
            revoked_at=NOW,
        )
        revoked = await _post(
            app,
            body,
            _proof_headers(
                private_key,
                token=token,
                token_id=token_id,
                body=body,
                nonce="revoked-health-review-nonce-0001",
            ),
        )
        assert revoked.status_code == 401, ORACLE
        assert len(await _review_rows(group)) == 1, ORACLE
    finally:
        await group.close()


@pytest.mark.asyncio
async def test_health_review_rejects_raw_or_broadened_fields_without_writes(
    tmp_path: Path,
) -> None:
    app, group, private_key, token, token_id = await _prepared_app(tmp_path)
    invalid = _review() | {"raw_samples": [{"timestamp": "secret"}]}
    body = _canonical_body(invalid)
    try:
        response = await _post(
            app,
            body,
            _proof_headers(
                private_key,
                token=token,
                token_id=token_id,
                body=body,
                nonce="invalid-health-review-nonce-0001",
            ),
        )
        assert response.status_code == 422, ORACLE
        assert response.json()["detail"]["code"] == "HEALTH_RAW_FIELD_FORBIDDEN", ORACLE

        mismatched = _review()
        mismatched["provenance"][0]["owner_id"] = "different-owner"
        mismatched_body = _canonical_body(mismatched)
        scope_response = await _post(
            app,
            mismatched_body,
            _proof_headers(
                private_key,
                token=token,
                token_id=token_id,
                body=mismatched_body,
                nonce="mismatched-health-scope-nonce-01",
            ),
        )
        assert scope_response.status_code == 403, ORACLE
        assert scope_response.json()["detail"]["code"] == "HEALTH_REVIEW_SCOPE_MISMATCH"
        assert await _review_rows(group) == [], ORACLE
    finally:
        await group.close()
