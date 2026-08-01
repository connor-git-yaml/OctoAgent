"""F153 原生设备信任的唯一 SQLite 持久化 owner。"""

from __future__ import annotations

import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

import aiosqlite
from octoagent.core.models.privacy_ingestion import (
    AttestationState,
    CapabilityGrant,
    DeviceIdentity,
    DeviceStatus,
)


class RegistrationChallengeState(StrEnum):
    OPEN = "open"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CONSUMED = "consumed"


class DeviceKeyState(StrEnum):
    CURRENT = "current"
    OVERLAP = "overlap"
    REVOKED = "revoked"


@dataclass(frozen=True, slots=True)
class RegistrationChallengeRecord:
    challenge_id: str
    owner_id: str
    secret_sha256: str
    mobile_origin: str
    state: RegistrationChallengeState
    pending_device_id: str | None
    created_at: datetime
    expires_at: datetime
    decided_at: datetime | None


@dataclass(frozen=True, slots=True)
class DeviceKeyRecord:
    device_key_thumbprint: str
    device_id: str
    public_key_x963: str
    state: DeviceKeyState
    valid_from: datetime
    valid_until: datetime | None


@dataclass(frozen=True, slots=True)
class DeviceKeyRotation:
    """一次设备公钥轮换中必须共同提交的不可分割事实。"""

    device_id: str
    current_key_thumbprint: str
    public_key_x963: str
    device_key_thumbprint: str
    rotated_at: datetime
    overlap_expires_at: datetime


@dataclass(frozen=True, slots=True)
class RegistrationChallengeCreate:
    challenge_id: str
    owner_id: str
    secret_sha256: str
    mobile_origin: str
    created_at: datetime
    expires_at: datetime


def _parse_time(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


def _validate_window(
    *,
    created_at: datetime,
    expires_at: datetime,
    maximum: timedelta,
    label: str,
) -> None:
    if expires_at <= created_at:
        raise ValueError(f"{label} expiry must be later than creation")
    if expires_at > created_at + maximum:
        raise ValueError(f"{label} expiry exceeds maximum")


class SqliteDeviceTrustStore:
    """challenge、device、token hash 与 replay 的单一事务边界。"""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def create_registration_challenge(
        self,
        command: RegistrationChallengeCreate,
    ) -> RegistrationChallengeRecord:
        _validate_window(
            created_at=command.created_at,
            expires_at=command.expires_at,
            maximum=timedelta(minutes=2),
            label="registration challenge",
        )
        await self._conn.execute(
            """
            INSERT INTO device_registration_challenges (
                challenge_id, owner_id, secret_sha256, mobile_origin, state,
                pending_device_id, created_at, expires_at, decided_at
            )
            VALUES (?, ?, ?, ?, 'open', NULL, ?, ?, NULL)
            """,
            (
                command.challenge_id,
                command.owner_id,
                command.secret_sha256,
                command.mobile_origin,
                command.created_at.isoformat(),
                command.expires_at.isoformat(),
            ),
        )
        await self._conn.commit()
        record = await self.load_registration_challenge(command.challenge_id)
        if record is None:
            raise RuntimeError("registration challenge write was not durable")
        return record

    async def load_registration_challenge(
        self,
        challenge_id: str,
    ) -> RegistrationChallengeRecord | None:
        cursor = await self._conn.execute(
            """
            SELECT challenge_id, owner_id, secret_sha256, mobile_origin, state,
                   pending_device_id, created_at, expires_at, decided_at
            FROM device_registration_challenges
            WHERE challenge_id = ?
            """,
            (challenge_id,),
        )
        row = await cursor.fetchone()
        return None if row is None else self._row_to_registration_challenge(row)

    async def claim_registration_challenge(
        self,
        *,
        challenge_id: str,
        secret_sha256: str,
        mobile_origin: str,
        device: DeviceIdentity,
        now: datetime,
    ) -> RegistrationChallengeRecord:
        await self._conn.execute("BEGIN IMMEDIATE")
        try:
            record = await self.load_registration_challenge(challenge_id)
            if record is None:
                raise ValueError("unknown registration challenge")
            if record.state is not RegistrationChallengeState.OPEN:
                raise ValueError("registration challenge is not open")
            if now >= record.expires_at:
                raise ValueError("registration challenge expired")
            if not hmac.compare_digest(record.secret_sha256, secret_sha256):
                raise ValueError("registration challenge secret mismatch")
            if record.mobile_origin != mobile_origin:
                raise ValueError("registration challenge origin mismatch")
            if device.owner_id != record.owner_id or device.status is not DeviceStatus.PENDING:
                raise ValueError("pending device does not match registration owner")
            await self._insert_pending_device(device)
            await self._conn.execute(
                """
                UPDATE device_registration_challenges
                SET state = 'submitted', pending_device_id = ?
                WHERE challenge_id = ? AND state = 'open'
                """,
                (device.device_id, challenge_id),
            )
            await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            raise
        claimed = await self.load_registration_challenge(challenge_id)
        if claimed is None:
            raise RuntimeError("claimed registration challenge disappeared")
        return claimed

    async def _insert_pending_device(self, device: DeviceIdentity) -> None:
        await self._conn.execute(
            """
            INSERT INTO mobile_devices (
                device_id, owner_id, display_name, current_key_thumbprint,
                attestation_state, status, created_at, last_seen_at, revoked_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                device.device_id,
                device.owner_id,
                device.display_name,
                device.device_key_thumbprint,
                device.attestation_state.value,
                device.status.value,
                device.created_at.isoformat(),
                device.last_seen_at.isoformat(),
            ),
        )
        await self._conn.execute(
            """
            INSERT INTO mobile_device_keys (
                device_key_thumbprint, device_id, public_key_x963, state,
                valid_from, valid_until
            )
            VALUES (?, ?, ?, 'current', ?, NULL)
            """,
            (
                device.device_key_thumbprint,
                device.device_id,
                device.public_key,
                device.created_at.isoformat(),
            ),
        )

    async def approve_registration(
        self,
        *,
        challenge_id: str,
        approved_at: datetime,
    ) -> DeviceIdentity:
        await self._conn.execute("BEGIN IMMEDIATE")
        try:
            record = await self.load_registration_challenge(challenge_id)
            if record is None or record.pending_device_id is None:
                raise ValueError("registration challenge has no pending device")
            if record.state is RegistrationChallengeState.APPROVED:
                await self._conn.commit()
                device = await self.get_device(record.pending_device_id)
                if device is None:
                    raise RuntimeError("approved device is missing")
                return device
            if record.state is not RegistrationChallengeState.SUBMITTED:
                raise ValueError("registration challenge is not submitted")
            if approved_at >= record.expires_at:
                raise ValueError("registration approval is too late")
            await self._conn.execute(
                """
                UPDATE mobile_devices
                SET status = 'active', last_seen_at = ?
                WHERE device_id = ? AND status = 'pending'
                """,
                (approved_at.isoformat(), record.pending_device_id),
            )
            await self._conn.execute(
                """
                UPDATE device_registration_challenges
                SET state = 'approved', decided_at = ?
                WHERE challenge_id = ? AND state = 'submitted'
                """,
                (approved_at.isoformat(), challenge_id),
            )
            await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            raise
        device = await self.get_device(record.pending_device_id)
        if device is None:
            raise RuntimeError("approved device is missing")
        return device

    async def reject_registration(
        self,
        *,
        challenge_id: str,
        rejected_at: datetime,
    ) -> None:
        await self._conn.execute("BEGIN IMMEDIATE")
        try:
            record = await self.load_registration_challenge(challenge_id)
            if record is None or record.state is not RegistrationChallengeState.SUBMITTED:
                raise ValueError("registration challenge is not submitted")
            if record.pending_device_id is None:
                raise ValueError("registration challenge has no pending device")
            await self._conn.execute(
                "DELETE FROM mobile_device_keys WHERE device_id = ?",
                (record.pending_device_id,),
            )
            await self._conn.execute(
                "DELETE FROM mobile_devices WHERE device_id = ? AND status = 'pending'",
                (record.pending_device_id,),
            )
            await self._conn.execute(
                """
                UPDATE device_registration_challenges
                SET state = 'rejected', decided_at = ?
                WHERE challenge_id = ?
                """,
                (rejected_at.isoformat(), challenge_id),
            )
            await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            raise

    async def get_device(self, device_id: str) -> DeviceIdentity | None:
        cursor = await self._conn.execute(
            """
            SELECT d.device_id, d.owner_id, d.display_name, k.public_key_x963,
                   d.current_key_thumbprint, d.attestation_state, d.status,
                   d.created_at, d.last_seen_at, d.revoked_at
            FROM mobile_devices AS d
            JOIN mobile_device_keys AS k
              ON k.device_key_thumbprint = d.current_key_thumbprint
            WHERE d.device_id = ?
            """,
            (device_id,),
        )
        row = await cursor.fetchone()
        return None if row is None else self._row_to_device(row)

    async def list_owner_devices(self, owner_id: str) -> list[DeviceIdentity]:
        cursor = await self._conn.execute(
            """
            SELECT d.device_id, d.owner_id, d.display_name, k.public_key_x963,
                   d.current_key_thumbprint, d.attestation_state, d.status,
                   d.created_at, d.last_seen_at, d.revoked_at
            FROM mobile_devices AS d
            JOIN mobile_device_keys AS k
              ON k.device_key_thumbprint = d.current_key_thumbprint
            WHERE d.owner_id = ?
            ORDER BY d.created_at ASC, d.device_id ASC
            """,
            (owner_id,),
        )
        return [self._row_to_device(row) for row in await cursor.fetchall()]

    async def create_token_challenge(
        self,
        *,
        token_challenge_id: str,
        device_id: str,
        challenge_sha256: str,
        created_at: datetime,
        expires_at: datetime,
    ) -> None:
        _validate_window(
            created_at=created_at,
            expires_at=expires_at,
            maximum=timedelta(minutes=2),
            label="token challenge",
        )
        device = await self.get_device(device_id)
        if device is None or device.status is not DeviceStatus.ACTIVE:
            raise ValueError("token challenge requires active device")
        await self._conn.execute(
            """
            INSERT INTO device_token_challenges (
                token_challenge_id, device_id, challenge_sha256,
                created_at, expires_at, used_at
            )
            VALUES (?, ?, ?, ?, ?, NULL)
            """,
            (
                token_challenge_id,
                device_id,
                challenge_sha256,
                created_at.isoformat(),
                expires_at.isoformat(),
            ),
        )
        await self._conn.commit()

    async def consume_token_challenge(
        self,
        *,
        token_challenge_id: str,
        device_id: str,
        challenge_sha256: str,
        consumed_at: datetime,
    ) -> bool:
        cursor = await self._conn.execute(
            """
            UPDATE device_token_challenges
            SET used_at = ?
            WHERE token_challenge_id = ?
              AND device_id = ?
              AND challenge_sha256 = ?
              AND used_at IS NULL
              AND expires_at > ?
            """,
            (
                consumed_at.isoformat(),
                token_challenge_id,
                device_id,
                challenge_sha256,
                consumed_at.isoformat(),
            ),
        )
        await self._conn.commit()
        return cursor.rowcount == 1

    async def put_capability_grant(
        self,
        *,
        token_sha256: str,
        grant: CapabilityGrant,
        created_from_challenge_id: str,
    ) -> None:
        cursor = await self._conn.execute(
            """
            SELECT used_at
            FROM device_token_challenges
            WHERE token_challenge_id = ? AND device_id = ?
            """,
            (created_from_challenge_id, grant.device_id),
        )
        challenge = await cursor.fetchone()
        if challenge is None or challenge["used_at"] is None:
            raise ValueError("capability grant requires consumed token challenge")
        valid_keys = await self.list_verification_keys(
            device_id=grant.device_id,
            now=grant.issued_at,
        )
        if grant.device_key_thumbprint not in {key.device_key_thumbprint for key in valid_keys}:
            raise ValueError("capability grant key is not active")
        await self._conn.execute(
            """
            INSERT INTO device_capability_grants (
                token_id, token_sha256, grant_id, owner_id, device_id,
                device_key_thumbprint, capabilities, audience, issued_at,
                expires_at, created_from_challenge_id, revoked_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                grant.token_id,
                token_sha256,
                grant.grant_id,
                grant.owner_id,
                grant.device_id,
                grant.device_key_thumbprint,
                json.dumps(
                    [capability.value for capability in grant.capabilities],
                    separators=(",", ":"),
                ),
                grant.audience.value,
                grant.issued_at.isoformat(),
                grant.expires_at.isoformat(),
                created_from_challenge_id,
            ),
        )
        await self._conn.commit()

    async def get_capability_grant(
        self,
        *,
        token_sha256: str,
        now: datetime,
    ) -> CapabilityGrant | None:
        cursor = await self._conn.execute(
            """
            SELECT token_id, grant_id, owner_id, device_id,
                   device_key_thumbprint, capabilities, audience,
                   issued_at, expires_at
            FROM device_capability_grants
            WHERE token_sha256 = ? AND revoked_at IS NULL AND expires_at > ?
            """,
            (token_sha256, now.isoformat()),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        keys = await self.list_verification_keys(
            device_id=str(row["device_id"]),
            now=now,
        )
        if str(row["device_key_thumbprint"]) not in {key.device_key_thumbprint for key in keys}:
            return None
        return CapabilityGrant.model_validate(
            {
                "grant_id": row["grant_id"],
                "owner_id": row["owner_id"],
                "device_id": row["device_id"],
                "device_key_thumbprint": row["device_key_thumbprint"],
                "capabilities": json.loads(row["capabilities"]),
                "audience": row["audience"],
                "issued_at": row["issued_at"],
                "expires_at": row["expires_at"],
                "token_id": row["token_id"],
            }
        )

    async def consume_request_proof(
        self,
        *,
        replay_key: str,
        device_id: str,
        token_id: str,
        consumed_at: datetime,
        expires_at: datetime,
    ) -> bool:
        _validate_window(
            created_at=consumed_at,
            expires_at=expires_at,
            maximum=timedelta(minutes=5),
            label="request replay",
        )
        cursor = await self._conn.execute(
            """
            INSERT OR IGNORE INTO device_request_replays (
                replay_key, device_id, token_id, expires_at, consumed_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                replay_key,
                device_id,
                token_id,
                expires_at.isoformat(),
                consumed_at.isoformat(),
            ),
        )
        await self._conn.commit()
        return cursor.rowcount == 1

    async def purge_expired_replays(self, now: datetime) -> int:
        cursor = await self._conn.execute(
            "DELETE FROM device_request_replays WHERE expires_at <= ?",
            (now.isoformat(),),
        )
        await self._conn.commit()
        return cursor.rowcount

    async def rotate_device_key(
        self,
        *,
        device_id: str,
        public_key_x963: str,
        device_key_thumbprint: str,
        rotated_at: datetime,
        overlap_expires_at: datetime,
    ) -> list[DeviceKeyRecord]:
        await self._conn.execute("BEGIN IMMEDIATE")
        try:
            device = await self.get_device(device_id)
            if device is None or device.status is not DeviceStatus.ACTIVE:
                raise ValueError("key rotation requires active device")
            rotation = DeviceKeyRotation(
                device_id=device_id,
                current_key_thumbprint=device.device_key_thumbprint,
                public_key_x963=public_key_x963,
                device_key_thumbprint=device_key_thumbprint,
                rotated_at=rotated_at,
                overlap_expires_at=overlap_expires_at,
            )
            self._validate_key_rotation_window(rotation)
            await self._rotate_device_key_rows(rotation)
            await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            raise
        return await self.list_verification_keys(device_id=device_id, now=rotated_at)

    async def consume_rotation_challenge_and_rotate_key(
        self,
        *,
        rotation_challenge_id: str,
        challenge_sha256: str,
        rotation: DeviceKeyRotation,
    ) -> list[DeviceKeyRecord]:
        """原子消费单次 challenge，并把新 key 切为 current。"""

        self._validate_key_rotation_window(rotation)
        await self._conn.execute("BEGIN IMMEDIATE")
        try:
            challenge = await self._conn.execute(
                """
                UPDATE device_token_challenges
                SET used_at = ?
                WHERE token_challenge_id = ?
                  AND device_id = ?
                  AND challenge_sha256 = ?
                  AND used_at IS NULL
                  AND expires_at > ?
                """,
                (
                    rotation.rotated_at.isoformat(),
                    rotation_challenge_id,
                    rotation.device_id,
                    challenge_sha256,
                    rotation.rotated_at.isoformat(),
                ),
            )
            if challenge.rowcount != 1:
                raise ValueError("key rotation challenge is invalid")
            await self._rotate_device_key_rows(rotation)
            await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            raise
        return await self.list_verification_keys(
            device_id=rotation.device_id,
            now=rotation.rotated_at,
        )

    @staticmethod
    def _validate_key_rotation_window(rotation: DeviceKeyRotation) -> None:
        if rotation.overlap_expires_at <= rotation.rotated_at:
            raise ValueError("key overlap must end after rotation")
        if rotation.overlap_expires_at > rotation.rotated_at + timedelta(minutes=15):
            raise ValueError("key overlap exceeds fifteen minutes")

    async def _rotate_device_key_rows(
        self,
        rotation: DeviceKeyRotation,
    ) -> None:
        device = await self.get_device(rotation.device_id)
        if device is None or device.status is not DeviceStatus.ACTIVE:
            raise ValueError("key rotation requires active device")
        if device.device_key_thumbprint != rotation.current_key_thumbprint:
            raise ValueError("key rotation current key mismatch")
        if rotation.device_key_thumbprint == rotation.current_key_thumbprint:
            raise ValueError("key rotation must replace current key")
        overlap = await self._conn.execute(
            """
            UPDATE mobile_device_keys
            SET state = 'overlap', valid_until = ?
            WHERE device_key_thumbprint = ?
              AND device_id = ?
              AND state = 'current'
            """,
            (
                rotation.overlap_expires_at.isoformat(),
                rotation.current_key_thumbprint,
                rotation.device_id,
            ),
        )
        if overlap.rowcount != 1:
            raise ValueError("key rotation current key is unavailable")
        await self._conn.execute(
            """
            INSERT INTO mobile_device_keys (
                device_key_thumbprint, device_id, public_key_x963,
                state, valid_from, valid_until
            )
            VALUES (?, ?, ?, 'current', ?, NULL)
            """,
            (
                rotation.device_key_thumbprint,
                rotation.device_id,
                rotation.public_key_x963,
                rotation.rotated_at.isoformat(),
            ),
        )
        updated = await self._conn.execute(
            """
            UPDATE mobile_devices
            SET current_key_thumbprint = ?, last_seen_at = ?
            WHERE device_id = ?
              AND current_key_thumbprint = ?
              AND status = 'active'
            """,
            (
                rotation.device_key_thumbprint,
                rotation.rotated_at.isoformat(),
                rotation.device_id,
                rotation.current_key_thumbprint,
            ),
        )
        if updated.rowcount != 1:
            raise ValueError("key rotation device update failed")

    async def list_verification_keys(
        self,
        *,
        device_id: str,
        now: datetime,
    ) -> list[DeviceKeyRecord]:
        cursor = await self._conn.execute(
            """
            SELECT k.device_key_thumbprint, k.device_id, k.public_key_x963,
                   k.state, k.valid_from, k.valid_until
            FROM mobile_device_keys AS k
            JOIN mobile_devices AS d ON d.device_id = k.device_id
            WHERE k.device_id = ?
              AND d.status = 'active'
              AND (
                    k.state = 'current'
                    OR (k.state = 'overlap' AND k.valid_until > ?)
                  )
            ORDER BY CASE k.state WHEN 'current' THEN 0 ELSE 1 END,
                     k.valid_from DESC
            """,
            (device_id, now.isoformat()),
        )
        return [self._row_to_device_key(row) for row in await cursor.fetchall()]

    async def revoke_device(
        self,
        *,
        device_id: str,
        revoked_at: datetime,
    ) -> None:
        await self._conn.execute("BEGIN IMMEDIATE")
        try:
            cursor = await self._conn.execute(
                """
                UPDATE mobile_devices
                SET status = 'revoked', revoked_at = ?, last_seen_at = ?
                WHERE device_id = ? AND status != 'revoked'
                """,
                (revoked_at.isoformat(), revoked_at.isoformat(), device_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("unknown or already revoked device")
            await self._conn.execute(
                """
                UPDATE mobile_device_keys
                SET state = 'revoked', valid_until = ?
                WHERE device_id = ? AND state != 'revoked'
                """,
                (revoked_at.isoformat(), device_id),
            )
            await self._conn.execute(
                """
                UPDATE device_capability_grants
                SET revoked_at = ?
                WHERE device_id = ? AND revoked_at IS NULL
                """,
                (revoked_at.isoformat(), device_id),
            )
            await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            raise

    @staticmethod
    def _row_to_registration_challenge(
        row: aiosqlite.Row,
    ) -> RegistrationChallengeRecord:
        return RegistrationChallengeRecord(
            challenge_id=str(row["challenge_id"]),
            owner_id=str(row["owner_id"]),
            secret_sha256=str(row["secret_sha256"]),
            mobile_origin=str(row["mobile_origin"]),
            state=RegistrationChallengeState(str(row["state"])),
            pending_device_id=(
                None if row["pending_device_id"] is None else str(row["pending_device_id"])
            ),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            expires_at=datetime.fromisoformat(str(row["expires_at"])),
            decided_at=_parse_time(row["decided_at"]),
        )

    @staticmethod
    def _row_to_device(row: aiosqlite.Row) -> DeviceIdentity:
        return DeviceIdentity(
            device_id=row["device_id"],
            owner_id=row["owner_id"],
            display_name=row["display_name"],
            public_key=row["public_key_x963"],
            device_key_thumbprint=row["current_key_thumbprint"],
            attestation_state=AttestationState(str(row["attestation_state"])),
            status=DeviceStatus(str(row["status"])),
            created_at=row["created_at"],
            last_seen_at=row["last_seen_at"],
            revoked_at=row["revoked_at"],
        )

    @staticmethod
    def _row_to_device_key(row: aiosqlite.Row) -> DeviceKeyRecord:
        return DeviceKeyRecord(
            device_key_thumbprint=str(row["device_key_thumbprint"]),
            device_id=str(row["device_id"]),
            public_key_x963=str(row["public_key_x963"]),
            state=DeviceKeyState(str(row["state"])),
            valid_from=datetime.fromisoformat(str(row["valid_from"])),
            valid_until=_parse_time(row["valid_until"]),
        )


__all__ = [
    "DeviceKeyRecord",
    "DeviceKeyRotation",
    "DeviceKeyState",
    "RegistrationChallengeCreate",
    "RegistrationChallengeRecord",
    "RegistrationChallengeState",
    "SqliteDeviceTrustStore",
]
