"""F153 device trust SQLite store 的 durable/single-use 合同。"""

from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

ORACLE = "F153_DEVICE_TRUST_STORE_MISSING"
NOW = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
SECRET = "registration-secret-must-not-persist"
TOKEN = "octo_dt1_token-value-must-not-persist"
CHALLENGE_HASH = "a" * 64
TOKEN_HASH = "b" * 64
PUBLIC_KEY = "B" + ("A" * 86)
ROTATED_PUBLIC_KEY = "B" + ("C" * 86)


def _contracts() -> tuple[Any, Any]:
    models = importlib.import_module("octoagent.core.models")
    try:
        store = importlib.import_module("octoagent.core.store.device_trust_store")
    except ModuleNotFoundError:
        pytest.fail(f"{ORACLE}: device trust store module is absent", pytrace=False)
    required = {
        "DeviceKeyRecord",
        "RegistrationChallengeCreate",
        "RegistrationChallengeRecord",
        "RegistrationChallengeState",
        "SqliteDeviceTrustStore",
    }
    missing = sorted(required - set(vars(store)))
    if missing:
        pytest.fail(f"{ORACLE}: missing symbols={','.join(missing)}", pytrace=False)
    return models, store


def _device(models: Any, *, device_id: str = "device-1") -> Any:
    return models.DeviceIdentity(
        device_id=device_id,
        owner_id="owner-1",
        display_name="Connor iPhone",
        public_key=PUBLIC_KEY,
        device_key_thumbprint="1" * 64,
        attestation_state="unsupported",
        status="pending",
        created_at=NOW,
        last_seen_at=NOW,
    )


def _grant(models: Any, *, thumbprint: str = "1" * 64) -> Any:
    return models.CapabilityGrant(
        grant_id="grant-1",
        owner_id="owner-1",
        device_id="device-1",
        device_key_thumbprint=thumbprint,
        capabilities=["device.ready.read", "device.profile.read"],
        audience="octo-gateway",
        issued_at=NOW + timedelta(seconds=20),
        expires_at=NOW + timedelta(minutes=15),
        token_id="token-1",
    )


def _registration_challenge(
    store_module: Any,
    *,
    challenge_id: str = "challenge-1",
    expires_at: datetime = NOW + timedelta(minutes=2),
) -> Any:
    return store_module.RegistrationChallengeCreate(
        challenge_id=challenge_id,
        owner_id="owner-1",
        secret_sha256=CHALLENGE_HASH,
        mobile_origin="https://ios.example.test",
        created_at=NOW,
        expires_at=expires_at,
    )


async def _store_group(tmp_path: Path) -> Any:
    core_store = importlib.import_module("octoagent.core.store")
    return await core_store.create_store_group(
        str(tmp_path / "octo.db"),
        tmp_path / "artifacts",
    )


async def _enroll_active(group: Any, models: Any) -> Any:
    _, store_module = _contracts()
    await group.device_trust_store.create_registration_challenge(
        _registration_challenge(store_module),
    )
    await group.device_trust_store.claim_registration_challenge(
        challenge_id="challenge-1",
        secret_sha256=CHALLENGE_HASH,
        mobile_origin="https://ios.example.test",
        device=_device(models),
        now=NOW + timedelta(seconds=5),
    )
    return await group.device_trust_store.approve_registration(
        challenge_id="challenge-1",
        approved_at=NOW + timedelta(seconds=10),
    )


async def _consume_token_challenge(group: Any) -> None:
    await group.device_trust_store.create_token_challenge(
        token_challenge_id="token-challenge-1",
        device_id="device-1",
        challenge_sha256="d" * 64,
        created_at=NOW + timedelta(seconds=11),
        expires_at=NOW + timedelta(minutes=2, seconds=11),
    )
    assert await group.device_trust_store.consume_token_challenge(
        token_challenge_id="token-challenge-1",
        device_id="device-1",
        challenge_sha256="d" * 64,
        consumed_at=NOW + timedelta(seconds=12),
    ), ORACLE


@pytest.mark.asyncio
async def test_store_group_owns_exact_non_secret_schema(tmp_path: Path) -> None:
    _, store_module = _contracts()
    group = await _store_group(tmp_path)
    try:
        assert isinstance(
            group.device_trust_store,
            store_module.SqliteDeviceTrustStore,
        ), ORACLE
        cursor = await group.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'device_%'"
        )
        tables = {str(row[0]) for row in await cursor.fetchall()}
        assert tables == {
            "device_capability_grants",
            "device_registration_challenges",
            "device_request_replays",
            "device_token_challenges",
        }, ORACLE
        cursor = await group.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'mobile_device%'"
        )
        assert {str(row[0]) for row in await cursor.fetchall()} == {
            "mobile_device_keys",
            "mobile_devices",
        }, ORACLE
        for table in tables | {"mobile_device_keys", "mobile_devices"}:
            columns = await group.conn.execute_fetchall(f"PRAGMA table_info({table})")
            names = {str(row[1]) for row in columns}
            assert not names & {
                "challenge_secret",
                "opaque_token",
                "signature",
                "attestation_object",
                "owner_email",
                "request_body",
            }, ORACLE
    finally:
        await group.close()


@pytest.mark.asyncio
async def test_registration_challenge_is_hashed_bounded_and_single_use(
    tmp_path: Path,
) -> None:
    models, store_module = _contracts()
    group = await _store_group(tmp_path)
    try:
        with pytest.raises(ValueError):
            await group.device_trust_store.create_registration_challenge(
                _registration_challenge(
                    store_module,
                    challenge_id="too-long",
                    expires_at=NOW + timedelta(minutes=2, seconds=1),
                )
            )
        await group.device_trust_store.create_registration_challenge(
            _registration_challenge(store_module),
        )
        claimed = await group.device_trust_store.claim_registration_challenge(
            challenge_id="challenge-1",
            secret_sha256=CHALLENGE_HASH,
            mobile_origin="https://ios.example.test",
            device=_device(models),
            now=NOW + timedelta(seconds=5),
        )
        assert claimed.state.value == "submitted", ORACLE
        assert claimed.pending_device_id == "device-1", ORACLE
        with pytest.raises(ValueError):
            await group.device_trust_store.claim_registration_challenge(
                challenge_id="challenge-1",
                secret_sha256=CHALLENGE_HASH,
                mobile_origin="https://ios.example.test",
                device=_device(models, device_id="device-2"),
                now=NOW + timedelta(seconds=6),
            )
        active = await group.device_trust_store.approve_registration(
            challenge_id="challenge-1",
            approved_at=NOW + timedelta(seconds=10),
        )
        assert active.status.value == "active", ORACLE
        assert (
            await group.device_trust_store.load_registration_challenge("challenge-1")
        ).state.value == "approved", ORACLE
    finally:
        await group.close()


@pytest.mark.asyncio
async def test_expired_or_wrong_registration_claim_does_not_create_device(
    tmp_path: Path,
) -> None:
    models, store_module = _contracts()
    group = await _store_group(tmp_path)
    try:
        await group.device_trust_store.create_registration_challenge(
            _registration_challenge(
                store_module,
                challenge_id="challenge-expired",
                expires_at=NOW + timedelta(seconds=10),
            )
        )
        for patch in (
            {"secret_sha256": "c" * 64, "now": NOW + timedelta(seconds=5)},
            {"secret_sha256": CHALLENGE_HASH, "now": NOW + timedelta(seconds=11)},
        ):
            with pytest.raises(ValueError):
                await group.device_trust_store.claim_registration_challenge(
                    challenge_id="challenge-expired",
                    secret_sha256=patch["secret_sha256"],
                    mobile_origin="https://ios.example.test",
                    device=_device(models),
                    now=patch["now"],
                )
        assert await group.device_trust_store.get_device("device-1") is None, ORACLE
    finally:
        await group.close()


@pytest.mark.asyncio
async def test_token_challenge_and_request_replay_are_durable_single_use(
    tmp_path: Path,
) -> None:
    models, _ = _contracts()
    group = await _store_group(tmp_path)
    try:
        await _enroll_active(group, models)
        await group.device_trust_store.create_token_challenge(
            token_challenge_id="token-challenge-1",
            device_id="device-1",
            challenge_sha256="d" * 64,
            created_at=NOW + timedelta(seconds=11),
            expires_at=NOW + timedelta(minutes=2, seconds=11),
        )
        assert await group.device_trust_store.consume_token_challenge(
            token_challenge_id="token-challenge-1",
            device_id="device-1",
            challenge_sha256="d" * 64,
            consumed_at=NOW + timedelta(seconds=12),
        ), ORACLE
        assert not await group.device_trust_store.consume_token_challenge(
            token_challenge_id="token-challenge-1",
            device_id="device-1",
            challenge_sha256="d" * 64,
            consumed_at=NOW + timedelta(seconds=13),
        ), ORACLE

        assert await group.device_trust_store.consume_request_proof(
            replay_key="e" * 64,
            device_id="device-1",
            token_id="token-1",
            consumed_at=NOW + timedelta(seconds=20),
            expires_at=NOW + timedelta(minutes=5, seconds=20),
        ), ORACLE
        assert not await group.device_trust_store.consume_request_proof(
            replay_key="e" * 64,
            device_id="device-1",
            token_id="token-1",
            consumed_at=NOW + timedelta(seconds=21),
            expires_at=NOW + timedelta(minutes=5, seconds=20),
        ), ORACLE
    finally:
        await group.close()

    reopened = await _store_group(tmp_path)
    try:
        assert not await reopened.device_trust_store.consume_request_proof(
            replay_key="e" * 64,
            device_id="device-1",
            token_id="token-1",
            consumed_at=NOW + timedelta(seconds=22),
            expires_at=NOW + timedelta(minutes=5, seconds=20),
        ), ORACLE
    finally:
        await reopened.close()


@pytest.mark.asyncio
async def test_token_lookup_stores_only_hash_and_revocation_is_immediate(
    tmp_path: Path,
) -> None:
    models, _ = _contracts()
    db_path = tmp_path / "octo.db"
    group = await _store_group(tmp_path)
    try:
        await _enroll_active(group, models)
        await _consume_token_challenge(group)
        grant = _grant(models)
        await group.device_trust_store.put_capability_grant(
            token_sha256=TOKEN_HASH,
            grant=grant,
            created_from_challenge_id="token-challenge-1",
        )
        assert (
            await group.device_trust_store.get_capability_grant(
                token_sha256=TOKEN_HASH,
                now=NOW + timedelta(minutes=1),
            )
            == grant
        ), ORACLE
        await group.device_trust_store.revoke_device(
            device_id="device-1",
            revoked_at=NOW + timedelta(minutes=2),
        )
        assert (
            await group.device_trust_store.get_capability_grant(
                token_sha256=TOKEN_HASH,
                now=NOW + timedelta(minutes=2),
            )
            is None
        ), ORACLE
        assert (await group.device_trust_store.get_device("device-1")).status.value == "revoked", (
            ORACLE
        )
    finally:
        await group.close()

    stored = db_path.read_bytes()
    assert SECRET.encode() not in stored, ORACLE
    assert TOKEN.encode() not in stored, ORACLE


@pytest.mark.asyncio
async def test_key_rotation_has_bounded_overlap_and_invalidates_old_grant(
    tmp_path: Path,
) -> None:
    models, _ = _contracts()
    group = await _store_group(tmp_path)
    try:
        await _enroll_active(group, models)
        await _consume_token_challenge(group)
        old_grant = _grant(models)
        await group.device_trust_store.put_capability_grant(
            token_sha256=TOKEN_HASH,
            grant=old_grant,
            created_from_challenge_id="token-challenge-1",
        )
        with pytest.raises(ValueError):
            await group.device_trust_store.rotate_device_key(
                device_id="device-1",
                public_key_x963=ROTATED_PUBLIC_KEY,
                device_key_thumbprint="2" * 64,
                rotated_at=NOW + timedelta(minutes=1),
                overlap_expires_at=NOW + timedelta(minutes=16, seconds=1),
            )
        keys = await group.device_trust_store.rotate_device_key(
            device_id="device-1",
            public_key_x963=ROTATED_PUBLIC_KEY,
            device_key_thumbprint="2" * 64,
            rotated_at=NOW + timedelta(minutes=1),
            overlap_expires_at=NOW + timedelta(minutes=3),
        )
        assert [key.device_key_thumbprint for key in keys] == ["2" * 64, "1" * 64], ORACLE
        assert (
            await group.device_trust_store.get_capability_grant(
                token_sha256=TOKEN_HASH,
                now=NOW + timedelta(minutes=2),
            )
            == old_grant
        ), ORACLE
        assert (
            await group.device_trust_store.get_capability_grant(
                token_sha256=TOKEN_HASH,
                now=NOW + timedelta(minutes=3, seconds=1),
            )
            is None
        ), ORACLE
        assert [
            key.device_key_thumbprint
            for key in await group.device_trust_store.list_verification_keys(
                device_id="device-1",
                now=NOW + timedelta(minutes=3, seconds=1),
            )
        ] == ["2" * 64], ORACLE
    finally:
        await group.close()
