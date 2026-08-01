"""F154 mobile health review route 的 Host/proof/store/audit 合同。"""

from __future__ import annotations

import base64
import hashlib
import importlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI

ORACLE = "F154_HEALTH_REVIEW_ROUTE_MISSING"
TRANSPORT_ORACLE = "F154_HEALTH_IOS_TRANSPORT_MISSING"
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


def _analysis_contracts() -> tuple[Any, Any]:
    service, routes = _contracts()
    missing = {
        "HealthAnalysisAccepted",
        "HealthAnalysisRequest",
    } - set(vars(service))
    missing |= {"get_provider_router"} - set(vars(routes))
    if missing or not hasattr(service.HealthIngestionService, "submit_analysis"):
        detail = ",".join(sorted(missing | {"submit_analysis"}))
        pytest.fail(f"F154_HEALTH_ANALYSIS_MISSING: missing symbols={detail}", pytrace=False)
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


def _token_request(private_key: Any, challenge: Any) -> Any:
    protocol = importlib.import_module("octoagent.protocol.device_trust")
    unsigned = protocol.DeviceTokenRequest(
        token_challenge_id=challenge.token_challenge_id,
        device_id=challenge.device_id,
        server_challenge=challenge.server_challenge,
        mobile_origin=challenge.mobile_origin,
        challenge_signature_der=_sign(private_key, b"temporary"),
        timestamp=NOW,
    )
    return unsigned.model_copy(
        update={
            "challenge_signature_der": _sign(
                private_key,
                protocol.token_challenge_signature_bytes(unsigned),
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
    ids = iter(
        (
            "registration-1",
            "device-1",
            "device-audit-1",
            "token-challenge-health-1",
            "grant-health-1",
            "token-health-1",
        )
    )
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


@pytest.mark.asyncio
async def test_device_token_and_profile_expose_exact_health_transport_scope(
    tmp_path: Path,
) -> None:
    core_store = importlib.import_module("octoagent.core.store")
    protocol = importlib.import_module("octoagent.protocol.device_trust")
    group = await core_store.create_store_group(
        str(tmp_path / "octo.db"),
        tmp_path / "artifacts",
    )
    try:
        service, device, private_key = await _registered_device(group)
        challenge = await service.create_token_challenge(device_id=device.device_id)
        token = await service.issue_token(_token_request(private_key, challenge))
        expected_capabilities = (
            "device.profile.read",
            "device.ready.read",
            "health.analysis.run",
            "health.review.submit",
            "health.source.delete",
        )
        signed = _signed_headers(
            private_key,
            token=token.opaque_token,
            token_id=token.grant.token_id,
            method="GET",
            path="/api/mobile/v1/device-profile",
            body=b"",
            nonce="health-profile-scope-nonce-000001",
        )
        profile = await service.protected_device_profile(
            headers=protocol.DeviceProofHeaders(
                authorization=signed["Authorization"],
                timestamp=NOW,
                nonce=signed["X-Octo-Device-Nonce"],
                signature=signed["X-Octo-Device-Signature"],
            ),
            method="GET",
            canonical_path="/api/mobile/v1/device-profile",
            raw_body=b"",
        )
        issues = []
        if tuple(item.value for item in token.grant.capabilities) != expected_capabilities:
            issues.append("issued token omits the exact health capabilities")
        if tuple(item.value for item in profile.capabilities) != expected_capabilities:
            issues.append("device profile omits the exact health capabilities")
        if getattr(profile, "owner_id", None) != device.owner_id:
            issues.append("device profile omits the server-authoritative owner scope")
        assert not issues, f"{TRANSPORT_ORACLE}: {'; '.join(issues)}"
    finally:
        await group.close()


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
    return _signed_headers(
        private_key,
        token=token,
        token_id=token_id,
        method="POST",
        path=REVIEW_PATH,
        body=body,
        nonce=nonce,
    )


def _signed_headers(
    private_key: Any,
    *,
    token: str,
    token_id: str,
    method: str,
    path: str,
    body: bytes,
    nonce: str,
) -> dict[str, str]:
    models = importlib.import_module("octoagent.core.models")
    proof = models.RequestProofPayload(
        method=method,
        canonical_path=path,
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


class _AnalysisClient:
    def __init__(self, outcome: str | Exception) -> None:
        self.outcome = outcome
        self.calls: list[dict[str, Any]] = []

    async def call(self, **kwargs: Any) -> tuple[str, list[Any], dict[str, Any]]:
        self.calls.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome, [], {"usage": {"total_tokens": 24}}


class _AnalysisRouter:
    def __init__(self, outcome: str | Exception) -> None:
        self.client = _AnalysisClient(outcome)
        self.calls: list[tuple[str, str | None]] = []

    def resolve_for_alias(
        self,
        alias: str,
        *,
        task_scope: str | None = None,
    ) -> Any:
        self.calls.append((alias, task_scope))
        return SimpleNamespace(
            client=self.client,
            model_name="fixture-health-model",
            provider_id="fixture-provider",
        )


def _approved_facts() -> dict[str, Any]:
    return {
        "purpose": PURPOSE,
        "time_range": {
            "start": (NOW - timedelta(days=1)).isoformat().replace("+00:00", "Z"),
            "end": NOW.isoformat().replace("+00:00", "Z"),
        },
        "daily_steps": [{"local_day": "2026-08-01", "count": "1234", "unit": "count"}],
        "sleep": {
            "window_start_utc": (NOW - timedelta(hours=8)).isoformat().replace("+00:00", "Z"),
            "window_end_utc": NOW.isoformat().replace("+00:00", "Z"),
            "total_asleep_minutes": "420",
            "stage_minutes": {
                "awake": "20",
                "core": "250",
                "deep": "80",
                "rem": "90",
                "unspecified": "0",
            },
            "unit": "min",
        },
        "completeness_notice": "数据可能不完整。",
    }


def _analysis_payload(source_hash: str) -> dict[str, Any]:
    models = importlib.import_module("octoagent.core.models")
    facts = _approved_facts()
    packet = {
        "stage": "approved_analysis_packet",
        "packet_id": "health-packet-1",
        "purpose": PURPOSE,
        "facts_sha256": models.canonical_sha256(facts),
        "provenance": _review()["provenance"],
        "consent_id": "health-consent-1",
        "retention": {
            "stage": "approved_analysis_packet",
            "created_at": NOW.isoformat().replace("+00:00", "Z"),
            "expires_at": (NOW + timedelta(minutes=15)).isoformat().replace("+00:00", "Z"),
            "session_ends_at": None,
        },
    }
    return {
        "source_hash": source_hash,
        "consent": {
            "consent_id": "health-consent-1",
            "bundle_sha256": source_hash,
            "approved_packet_sha256": models.canonical_sha256(packet),
            "purpose": PURPOSE,
            "owner_id": _review()["provenance"][0]["owner_id"],
            "device_id": "device-1",
            "approved_at": NOW.isoformat().replace("+00:00", "Z"),
            "expires_at": (NOW + timedelta(minutes=15)).isoformat().replace("+00:00", "Z"),
            "used_at": None,
        },
        "packet": packet,
        "approved_facts": facts,
    }


async def _prepared_analysis_app(
    tmp_path: Path,
    outcome: str | Exception,
) -> tuple[FastAPI, Any, Any, str, str, _AnalysisRouter, str]:
    health, routes = _analysis_contracts()
    core_store = importlib.import_module("octoagent.core.store")
    protocol = importlib.import_module("octoagent.protocol.privacy_ingestion")
    access = importlib.import_module("octoagent.gateway.services.mobile_device_access")
    group = await core_store.create_store_group(
        str(tmp_path / "octo.db"),
        tmp_path / "artifacts",
    )
    device_service, device, private_key = await _registered_device(group)
    token, token_id = await _put_grant(
        group,
        device=device,
        capability="health.analysis.run",
        suffix="health-analysis",
    )
    review = protocol.validate_consumer_payload(
        protocol.PrivacyConsumer.F154,
        protocol.PrivacyContractName.REVIEW_BUNDLE,
        _review(),
    )
    source_hash = await group.privacy_ingestion_store.put_review_bundle(review)
    service = health.HealthIngestionService(
        device_store=group.device_trust_store,
        privacy_store=group.privacy_ingestion_store,
        options=health.HealthIngestionServiceOptions(
            clock=lambda: NOW,
            id_factory=iter(
                (
                    "analysis-result-1",
                    "analysis-audit-1",
                    "analysis-reject-audit-1",
                    "deletion-started-audit-1",
                    "deletion-failed-audit-1",
                    "deletion-reentry-audit-1",
                    "deletion-completed-audit-1",
                )
            ).__next__,
        ),
    )
    provider_router = _AnalysisRouter(outcome)
    app = FastAPI()
    app.state.device_trust_service = device_service
    app.state.health_ingestion_service = service
    app.state.provider_router = provider_router
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
    return app, group, private_key, token, token_id, provider_router, source_hash


async def _post_analysis(
    app: FastAPI,
    private_key: Any,
    token: str,
    token_id: str,
    payload: Mapping[str, Any],
    *,
    nonce: str,
) -> httpx.Response:
    body = _canonical_body(payload)
    path = "/api/mobile/v1/health/analyses"
    headers = _signed_headers(
        private_key,
        token=token,
        token_id=token_id,
        method="POST",
        path=path,
        body=body,
        nonce=nonce,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=MOBILE_ORIGIN,
    ) as client:
        return await client.post(
            path,
            content=body,
            headers=headers,
        )


async def _analysis_counts(group: Any) -> tuple[int, int, int]:
    counts = []
    for table in (
        "privacy_approved_packets",
        "privacy_analysis_results",
        "privacy_memory_candidates",
    ):
        cursor = await group.conn.execute(f"SELECT COUNT(*) FROM {table}")
        row = await cursor.fetchone()
        counts.append(int(row[0]))
    return tuple(counts)  # type: ignore[return-value]


def _deletion_contracts() -> None:
    service, _routes = _analysis_contracts()
    if not hasattr(service.HealthIngestionService, "delete_source"):
        pytest.fail(
            "F154_HEALTH_DELETION_MISSING: health source deletion is absent",
            pytrace=False,
        )


async def _seed_memory_candidate(group: Any) -> None:
    models = importlib.import_module("octoagent.core.models")
    packet_cursor = await group.conn.execute(
        "SELECT object_hash, content FROM privacy_approved_packets"
    )
    packet_row = await packet_cursor.fetchone()
    result_cursor = await group.conn.execute(
        "SELECT object_hash, content FROM privacy_analysis_results"
    )
    result_row = await result_cursor.fetchone()
    assert packet_row is not None and result_row is not None, ORACLE
    packet = models.ApprovedAnalysisPacket.model_validate_json(packet_row["content"])
    result = models.AnalysisResult.model_validate_json(result_row["content"])
    candidate = models.OptionalMemoryCandidate(
        candidate_id="health-memory-candidate-1",
        result_id=result.result_id,
        packet_sha256=str(packet_row["object_hash"]),
        provenance=packet.provenance,
        user_selected_text="用户主动选择的分析片段。",
        review_state=models.MemoryReviewState.PENDING,
    )
    await group.privacy_ingestion_store.put_memory_candidate(
        str(result_row["object_hash"]),
        candidate,
    )


async def _prepared_deletion_app(
    tmp_path: Path,
) -> tuple[FastAPI, Any, Any, str, str, str, str]:
    _deletion_contracts()
    prepared = await _prepared_analysis_app(tmp_path, "可删除的健康分析结果。")
    app, group, key, analysis_token, analysis_token_id, _router, source_hash = prepared
    analysis = await _post_analysis(
        app,
        key,
        analysis_token,
        analysis_token_id,
        _analysis_payload(source_hash),
        nonce="health-deletion-seed-analysis-nonce-1",
    )
    assert analysis.status_code == 201, analysis.text
    await _seed_memory_candidate(group)
    device = await group.device_trust_store.get_device("device-1")
    assert device is not None, ORACLE
    delete_token, delete_token_id = await _put_grant(
        group,
        device=device,
        capability="health.source.delete",
        suffix="health-delete",
    )
    return (
        app,
        group,
        key,
        delete_token,
        delete_token_id,
        analysis_token,
        source_hash,
    )


async def _delete_source(
    app: FastAPI,
    key: Any,
    token: str,
    token_id: str,
    source_hash: str,
    *,
    nonce: str,
) -> httpx.Response:
    path = f"/api/mobile/v1/health/sources/{source_hash}"
    headers = _signed_headers(
        key,
        token=token,
        token_id=token_id,
        method="DELETE",
        path=path,
        body=b"",
        nonce=nonce,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=MOBILE_ORIGIN,
    ) as client:
        return await client.request("DELETE", path, content=b"", headers=headers)


async def _chain_counts(group: Any) -> tuple[int, int, int, int]:
    counts = []
    for table in (
        "privacy_review_bundles",
        "privacy_approved_packets",
        "privacy_analysis_results",
        "privacy_memory_candidates",
    ):
        cursor = await group.conn.execute(f"SELECT COUNT(*) FROM {table}")
        row = await cursor.fetchone()
        counts.append(int(row[0]))
    return tuple(counts)  # type: ignore[return-value]


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


@pytest.mark.asyncio
async def test_health_analysis_calls_provider_router_once_with_minimal_approved_prompt(
    tmp_path: Path,
) -> None:
    app, group, key, token, token_id, router, source_hash = await _prepared_analysis_app(
        tmp_path,
        "最近活动与睡眠概览已完成。",
    )
    payload = _analysis_payload(source_hash)
    try:
        response = await _post_analysis(
            app,
            key,
            token,
            token_id,
            payload,
            nonce="health-analysis-success-nonce-0001",
        )
        assert response.status_code == 201, response.text
        result = response.json()
        assert set(result) == {"result", "summary"}, ORACLE
        assert result["summary"] == "最近活动与睡眠概览已完成。", ORACLE
        assert result["result"]["packet_id"] == "health-packet-1", ORACLE
        assert router.calls == [("main", "health-analysis:health-packet-1")], ORACLE
        assert len(router.client.calls) == 1, ORACLE
        provider_call = router.client.calls[0]
        assert provider_call["tools"] == [], ORACLE
        prompt = json.dumps(provider_call, ensure_ascii=False, sort_keys=True)
        assert "1234" in prompt and "420" in prompt, ORACLE
        assert not any(
            secret in prompt
            for secret in (
                "cf-owner-subject",
                "device-1",
                token,
                "raw_samples",
                "sample_id",
                "owner@example",
            )
        ), ORACLE
        assert await _analysis_counts(group) == (1, 1, 0), ORACLE
        cursor = await group.conn.execute(
            "SELECT reason_code, result FROM privacy_ingestion_audit "
            "WHERE reason_code = 'HEALTH_ANALYSIS_COMPLETED'"
        )
        audit = await cursor.fetchone()
        assert dict(audit) == {
            "reason_code": "HEALTH_ANALYSIS_COMPLETED",
            "result": "success",
        }, ORACLE
    finally:
        await group.close()


@pytest.mark.asyncio
async def test_health_analysis_consumes_approval_once_without_second_model_call(
    tmp_path: Path,
) -> None:
    app, group, key, token, token_id, router, source_hash = await _prepared_analysis_app(
        tmp_path,
        "一次分析结果。",
    )
    payload = _analysis_payload(source_hash)
    try:
        first = await _post_analysis(
            app,
            key,
            token,
            token_id,
            payload,
            nonce="health-analysis-first-nonce-000001",
        )
        assert first.status_code == 201, first.text
        second = await _post_analysis(
            app,
            key,
            token,
            token_id,
            payload,
            nonce="health-analysis-second-nonce-00001",
        )
        assert second.status_code == 409, second.text
        assert second.json()["detail"]["code"] == "HEALTH_CONSENT_INVALID", ORACLE
        assert len(router.client.calls) == 1, ORACLE
        assert await _analysis_counts(group) == (1, 1, 0), ORACLE
    finally:
        await group.close()


@pytest.mark.asyncio
async def test_health_analysis_auth_timeout_and_provider_errors_never_echo_success(
    tmp_path: Path,
) -> None:
    exceptions = importlib.import_module("octoagent.provider.exceptions")
    provider_client = importlib.import_module("octoagent.provider.provider_client")
    failures = (
        exceptions.CredentialError("missing credential"),
        provider_client.LLMCallError("timeout", "provider timeout"),
        provider_client.LLMCallError("api_error", "provider failed", retriable=False),
    )
    for ordinal, failure in enumerate(failures):
        prepared = await _prepared_analysis_app(tmp_path / str(ordinal), failure)
        app, group, key, token, token_id, router, source_hash = prepared
        try:
            response = await _post_analysis(
                app,
                key,
                token,
                token_id,
                _analysis_payload(source_hash),
                nonce=f"health-analysis-failure-nonce-{ordinal:04d}",
            )
            assert response.status_code == 502, response.text
            assert response.json()["detail"]["code"] == "HEALTH_ANALYSIS_FAILED", ORACLE
            assert "Echo:" not in response.text, ORACLE
            assert len(router.client.calls) <= 1, ORACLE
            assert await _analysis_counts(group) == (1, 0, 0), ORACLE
            cursor = await group.conn.execute(
                "SELECT COUNT(*) FROM privacy_ingestion_audit "
                "WHERE reason_code = 'HEALTH_ANALYSIS_COMPLETED'"
            )
            row = await cursor.fetchone()
            assert int(row[0]) == 0, ORACLE
        finally:
            await group.close()


@pytest.mark.asyncio
async def test_health_analysis_rejects_raw_or_hash_drift_before_provider_call(
    tmp_path: Path,
) -> None:
    prepared = await _prepared_analysis_app(tmp_path, "must not be called")
    app, group, key, token, token_id, router, source_hash = prepared
    try:
        raw_payload = _analysis_payload(source_hash)
        raw_payload["approved_facts"]["raw_samples"] = [{"uuid": "secret"}]
        raw = await _post_analysis(
            app,
            key,
            token,
            token_id,
            raw_payload,
            nonce="health-analysis-raw-field-nonce-01",
        )
        assert raw.status_code == 422, raw.text
        assert raw.json()["detail"]["code"] == "HEALTH_RAW_FIELD_FORBIDDEN", ORACLE

        drifted_payload = _analysis_payload(source_hash)
        drifted_payload["approved_facts"]["daily_steps"][0]["count"] = "9999"
        drifted = await _post_analysis(
            app,
            key,
            token,
            token_id,
            drifted_payload,
            nonce="health-analysis-hash-drift-nonce-1",
        )
        assert drifted.status_code == 422, drifted.text
        assert drifted.json()["detail"]["code"] == "HEALTH_PREVIEW_HASH_MISMATCH"
        assert router.client.calls == [], ORACLE
        assert await _analysis_counts(group) == (0, 0, 0), ORACLE
    finally:
        await group.close()


@pytest.mark.asyncio
async def test_health_deletion_removes_full_provenance_chain_and_retains_audit(
    tmp_path: Path,
) -> None:
    prepared = await _prepared_deletion_app(tmp_path)
    app, group, key, token, token_id, _analysis_token, source_hash = prepared
    try:
        assert await _chain_counts(group) == (1, 1, 1, 1), ORACLE
        response = await _delete_source(
            app,
            key,
            token,
            token_id,
            source_hash,
            nonce="health-delete-complete-nonce-0001",
        )
        assert response.status_code == 200, response.text
        receipt = response.json()
        assert receipt["status"] == "completed", ORACLE
        assert receipt["source_hash"] == source_hash, ORACLE
        assert len(receipt["deleted_object_hashes"]) == 4, ORACLE
        assert await _chain_counts(group) == (0, 0, 0, 0), ORACLE
        cursor = await group.conn.execute("SELECT COUNT(*) FROM privacy_ingestion_audit")
        row = await cursor.fetchone()
        assert int(row[0]) >= 3, ORACLE
    finally:
        await group.close()


@pytest.mark.asyncio
async def test_health_deletion_completed_receipt_is_idempotent_after_source_is_gone(
    tmp_path: Path,
) -> None:
    prepared = await _prepared_deletion_app(tmp_path)
    app, group, key, token, token_id, _analysis_token, source_hash = prepared
    try:
        first = await _delete_source(
            app,
            key,
            token,
            token_id,
            source_hash,
            nonce="health-delete-first-nonce-0000001",
        )
        second = await _delete_source(
            app,
            key,
            token,
            token_id,
            source_hash,
            nonce="health-delete-second-nonce-000001",
        )
        assert first.status_code == second.status_code == 200, ORACLE
        assert second.json() == first.json(), ORACLE
        assert await _chain_counts(group) == (0, 0, 0, 0), ORACLE
        cursor = await group.conn.execute("SELECT COUNT(*) FROM privacy_deletion_receipts")
        row = await cursor.fetchone()
        assert int(row[0]) == 1, ORACLE
    finally:
        await group.close()


@pytest.mark.asyncio
async def test_health_deletion_partial_failure_is_durable_and_reentrant(
    tmp_path: Path,
) -> None:
    import aiosqlite

    prepared = await _prepared_deletion_app(tmp_path)
    app, group, key, token, token_id, _analysis_token, source_hash = prepared
    store = group.privacy_ingestion_store
    original = store._delete_stage_rows

    async def fail_once(_groups: Any) -> None:
        raise aiosqlite.OperationalError("injected deletion failure")

    try:
        store._delete_stage_rows = fail_once
        failed = await _delete_source(
            app,
            key,
            token,
            token_id,
            source_hash,
            nonce="health-delete-failed-nonce-00001",
        )
        assert failed.status_code == 503, failed.text
        assert failed.json()["detail"]["code"] == "HEALTH_DELETION_INCOMPLETE"
        assert await _chain_counts(group) == (1, 1, 1, 1), ORACLE
        cursor = await group.conn.execute(
            "SELECT request_id, status, started_at FROM privacy_deletion_receipts"
        )
        failed_receipt = await cursor.fetchone()
        assert failed_receipt["status"] == "failed", ORACLE

        store._delete_stage_rows = original
        completed = await _delete_source(
            app,
            key,
            token,
            token_id,
            source_hash,
            nonce="health-delete-reentry-nonce-0001",
        )
        assert completed.status_code == 200, completed.text
        assert completed.json()["request_id"] == failed_receipt["request_id"], ORACLE
        completed_started_at = datetime.fromisoformat(completed.json()["started_at"])
        failed_started_at = datetime.fromisoformat(failed_receipt["started_at"])
        assert completed_started_at == failed_started_at, ORACLE
        assert await _chain_counts(group) == (0, 0, 0, 0), ORACLE
    finally:
        store._delete_stage_rows = original
        await group.close()


@pytest.mark.asyncio
async def test_health_deletion_requires_exact_capability_and_preserves_chain_on_reject(
    tmp_path: Path,
) -> None:
    prepared = await _prepared_deletion_app(tmp_path)
    app, group, key, _delete_token, _delete_token_id, analysis_token, source_hash = prepared
    cursor = await group.conn.execute(
        "SELECT token_id FROM device_capability_grants "
        "WHERE capabilities = '[\"health.analysis.run\"]'"
    )
    row = await cursor.fetchone()
    assert row is not None, ORACLE
    try:
        rejected = await _delete_source(
            app,
            key,
            analysis_token,
            str(row["token_id"]),
            source_hash,
            nonce="health-delete-wrong-capability-001",
        )
        assert rejected.status_code == 401, rejected.text
        assert rejected.json()["detail"]["code"] == "HEALTH_CAPABILITY_DENIED", ORACLE
        assert await _chain_counts(group) == (1, 1, 1, 1), ORACLE
    finally:
        await group.close()
