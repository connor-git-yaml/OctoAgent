"""F153 Web owner registration、approval 与 revoke 编排。"""

from __future__ import annotations

import base64
import hashlib
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from octoagent.core.models import (
    AttestationState,
    AuditDecision,
    AuditResult,
    CapabilityGrant,
    DeviceAudience,
    DeviceCapability,
    DeviceIdentity,
    DeviceStatus,
    PrivacyAuditEvent,
    PrivacyAuditEventType,
)
from octoagent.core.store.device_trust_store import (
    RegistrationChallengeCreate,
    RegistrationChallengeRecord,
    RegistrationChallengeState,
    SqliteDeviceTrustStore,
)
from octoagent.core.store.privacy_ingestion_store import SqlitePrivacyIngestionStore
from octoagent.policy.privacy_ingestion_policy import AuthorizedRequest
from octoagent.protocol.device_trust import (
    DeviceEnrollmentRequest,
    DeviceEnrollmentStatus,
    DeviceEnrollmentStatusResponse,
    DeviceProofHeaders,
    DeviceTokenChallengeResponse,
    DeviceTokenRequest,
    DeviceTokenResponse,
    MobileDeviceProfileResponse,
    MobileReadyResponse,
    OwnerDeviceProjection,
    OwnerRegistrationChallengeResponse,
)

from .mobile_device_auth import (
    DeviceAuthError,
    MobileAuthorizationRequest,
    authorize_mobile_request,
    generate_opaque_device_token,
    opaque_token_sha256,
    verify_enrollment_signature,
    verify_token_challenge_signature,
)


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _new_id() -> str:
    return str(uuid4())


@dataclass(frozen=True, slots=True)
class DeviceTrustServiceOptions:
    """Device trust 编排的 deployment 与可注入运行时依赖。"""

    mobile_origin: str
    clock: Callable[[], datetime] = _utc_now
    random_bytes: Callable[[int], bytes] = secrets.token_bytes
    id_factory: Callable[[], str] = _new_id
    attestation_verifier: Callable[[DeviceEnrollmentRequest], bool] | None = None


@dataclass(frozen=True, slots=True)
class _DeviceAuditContext:
    device_id: str
    owner_id: str
    object_hash: str
    event_type: PrivacyAuditEventType
    decision: AuditDecision
    result: AuditResult
    reason_code: str


class DeviceTrustServiceError(ValueError):
    """对外只暴露稳定 reason code 的 owner orchestration 拒绝。"""

    def __init__(self, reason_code: str, *, status_code: int = 400) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


def owner_id_for_subject(subject: str) -> str:
    """把瞬时 Access subject 投影为不可逆、不可识别的 durable owner id。"""

    normalized = subject.strip()
    if not normalized:
        raise DeviceTrustServiceError("DEVICE_OWNER_IDENTITY_INVALID", status_code=403)
    return hashlib.sha256(f"cloudflare-access:{normalized}".encode()).hexdigest()


def _identifier_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class DeviceTrustService:
    """同一 store 上编排 owner challenge/decision；不接触 Cloudflare 凭证。"""

    def __init__(
        self,
        *,
        device_store: SqliteDeviceTrustStore,
        audit_store: SqlitePrivacyIngestionStore,
        options: DeviceTrustServiceOptions,
    ) -> None:
        self._device_store = device_store
        self._audit_store = audit_store
        self._mobile_origin = options.mobile_origin
        self._clock = options.clock
        self._random_bytes = options.random_bytes
        self._id_factory = options.id_factory
        self._attestation_verifier = options.attestation_verifier

    @property
    def mobile_origin(self) -> str:
        """返回本 deployment 的 canonical mobile origin。"""

        return self._mobile_origin

    @staticmethod
    def hash_challenge_secret(secret: str) -> str:
        return hashlib.sha256(secret.encode("ascii")).hexdigest()

    async def create_registration_challenge(
        self,
        *,
        owner_subject: str,
    ) -> OwnerRegistrationChallengeResponse:
        now = self._clock()
        material = self._random_bytes(32)
        if not isinstance(material, bytes) or len(material) != 32:
            raise DeviceTrustServiceError("DEVICE_CHALLENGE_ENTROPY_INVALID")
        secret = base64.urlsafe_b64encode(material).rstrip(b"=").decode()
        challenge_id = self._id_factory()
        expires_at = now + timedelta(minutes=2)
        await self._device_store.create_registration_challenge(
            RegistrationChallengeCreate(
                challenge_id=challenge_id,
                owner_id=owner_id_for_subject(owner_subject),
                secret_sha256=self.hash_challenge_secret(secret),
                mobile_origin=self._mobile_origin,
                created_at=now,
                expires_at=expires_at,
            )
        )
        return OwnerRegistrationChallengeResponse(
            challenge_id=challenge_id,
            challenge_secret=secret,
            mobile_origin=self._mobile_origin,
            expires_at=expires_at,
        )

    async def get_enrollment_status(
        self,
        *,
        owner_subject: str,
        challenge_id: str,
    ) -> DeviceEnrollmentStatusResponse:
        record = await self._owned_challenge(
            owner_subject=owner_subject,
            challenge_id=challenge_id,
        )
        now = self._clock()
        if now >= record.expires_at and record.state in {
            RegistrationChallengeState.OPEN,
            RegistrationChallengeState.SUBMITTED,
        }:
            return DeviceEnrollmentStatusResponse(
                challenge_id=record.challenge_id,
                state=DeviceEnrollmentStatus.EXPIRED,
                reason_code="DEVICE_REGISTRATION_EXPIRED",
            )
        device = (
            await self._device_store.get_device(record.pending_device_id)
            if record.pending_device_id
            else None
        )
        state = {
            RegistrationChallengeState.OPEN: DeviceEnrollmentStatus.PENDING,
            RegistrationChallengeState.SUBMITTED: DeviceEnrollmentStatus.PENDING,
            RegistrationChallengeState.APPROVED: DeviceEnrollmentStatus.ACTIVE,
            RegistrationChallengeState.REJECTED: DeviceEnrollmentStatus.REJECTED,
            RegistrationChallengeState.EXPIRED: DeviceEnrollmentStatus.EXPIRED,
            RegistrationChallengeState.CONSUMED: DeviceEnrollmentStatus.ACTIVE,
        }[record.state]
        if state in {DeviceEnrollmentStatus.REJECTED, DeviceEnrollmentStatus.EXPIRED}:
            return DeviceEnrollmentStatusResponse(
                challenge_id=record.challenge_id,
                state=state,
                reason_code=(
                    "DEVICE_REGISTRATION_REJECTED"
                    if state is DeviceEnrollmentStatus.REJECTED
                    else "DEVICE_REGISTRATION_EXPIRED"
                ),
            )
        return DeviceEnrollmentStatusResponse(
            challenge_id=record.challenge_id,
            state=state,
            device_id=device.device_id if device else None,
            device_key_thumbprint=device.device_key_thumbprint if device else None,
            attestation_state=device.attestation_state if device else None,
        )

    async def submit_enrollment(
        self,
        request: DeviceEnrollmentRequest,
    ) -> DeviceEnrollmentStatusResponse:
        """验证原生设备 envelope，再一次性把 owner challenge 变为 pending device。"""

        now = self._clock()
        self._require_current_request(request.timestamp, now=now)
        if request.mobile_origin != self._mobile_origin:
            raise DeviceTrustServiceError("DEVICE_MOBILE_ORIGIN_MISMATCH", status_code=404)
        if request.attestation_state is AttestationState.VERIFIED:
            if self._attestation_verifier is None:
                raise DeviceTrustServiceError("DEVICE_ATTESTATION_UNAVAILABLE", status_code=409)
            if not self._attestation_verifier(request):
                raise DeviceTrustServiceError("DEVICE_ATTESTATION_INVALID", status_code=401)
        try:
            thumbprint = verify_enrollment_signature(request)
        except DeviceAuthError as exc:
            raise DeviceTrustServiceError(exc.reason_code, status_code=401) from exc
        challenge = await self._device_store.load_registration_challenge(request.challenge_id)
        if challenge is None:
            raise DeviceTrustServiceError("DEVICE_REGISTRATION_NOT_FOUND", status_code=404)
        device = DeviceIdentity(
            device_id=self._id_factory(),
            owner_id=challenge.owner_id,
            display_name=request.display_name,
            public_key=request.public_key_x963,
            device_key_thumbprint=thumbprint,
            attestation_state=request.attestation_state,
            status=DeviceStatus.PENDING,
            created_at=now,
            last_seen_at=now,
        )
        try:
            claimed = await self._device_store.claim_registration_challenge(
                challenge_id=request.challenge_id,
                secret_sha256=self.hash_challenge_secret(request.challenge_secret),
                mobile_origin=request.mobile_origin,
                device=device,
                now=now,
            )
        except ValueError as exc:
            raise DeviceTrustServiceError(
                "DEVICE_REGISTRATION_NOT_CLAIMABLE",
                status_code=409,
            ) from exc
        return DeviceEnrollmentStatusResponse(
            challenge_id=claimed.challenge_id,
            state=DeviceEnrollmentStatus.PENDING,
            device_id=device.device_id,
            device_key_thumbprint=device.device_key_thumbprint,
            attestation_state=device.attestation_state,
        )

    async def create_token_challenge(
        self,
        *,
        device_id: str,
    ) -> DeviceTokenChallengeResponse:
        """为 active device 返回一次 raw server challenge，数据库只保存 hash。"""

        now = self._clock()
        material = self._random_bytes(32)
        if not isinstance(material, bytes) or len(material) != 32:
            raise DeviceTrustServiceError("DEVICE_CHALLENGE_ENTROPY_INVALID")
        server_challenge = base64.urlsafe_b64encode(material).rstrip(b"=").decode()
        token_challenge_id = self._id_factory()
        expires_at = now + timedelta(minutes=2)
        try:
            await self._device_store.create_token_challenge(
                token_challenge_id=token_challenge_id,
                device_id=device_id,
                challenge_sha256=self.hash_challenge_secret(server_challenge),
                created_at=now,
                expires_at=expires_at,
            )
        except ValueError as exc:
            raise DeviceTrustServiceError("DEVICE_NOT_ACTIVE", status_code=404) from exc
        return DeviceTokenChallengeResponse(
            token_challenge_id=token_challenge_id,
            device_id=device_id,
            server_challenge=server_challenge,
            mobile_origin=self._mobile_origin,
            expires_at=expires_at,
        )

    async def issue_token(
        self,
        request: DeviceTokenRequest,
    ) -> DeviceTokenResponse:
        """验签并原子采用 single-use challenge；只持久化 opaque token hash。"""

        now = self._clock()
        self._require_current_request(request.timestamp, now=now)
        if request.mobile_origin != self._mobile_origin:
            raise DeviceTrustServiceError("DEVICE_MOBILE_ORIGIN_MISMATCH", status_code=404)
        device = await self._device_store.get_device(request.device_id)
        if device is None or device.status is not DeviceStatus.ACTIVE:
            raise DeviceTrustServiceError("DEVICE_NOT_ACTIVE", status_code=404)
        keys = await self._device_store.list_verification_keys(
            device_id=device.device_id,
            now=now,
        )
        selected_key = None
        for key in keys:
            try:
                verify_token_challenge_signature(
                    request,
                    public_key_x963=key.public_key_x963,
                )
            except DeviceAuthError:
                continue
            selected_key = key
            break
        if selected_key is None:
            raise DeviceTrustServiceError("DEVICE_SIGNATURE_INVALID", status_code=401)
        challenge_sha256 = self.hash_challenge_secret(request.server_challenge)
        consumed = await self._device_store.consume_token_challenge(
            token_challenge_id=request.token_challenge_id,
            device_id=request.device_id,
            challenge_sha256=challenge_sha256,
            consumed_at=now,
        )
        if not consumed:
            raise DeviceTrustServiceError("DEVICE_TOKEN_CHALLENGE_INVALID", status_code=409)
        token = generate_opaque_device_token(random_bytes=self._random_bytes)
        grant = CapabilityGrant(
            grant_id=self._id_factory(),
            owner_id=device.owner_id,
            device_id=device.device_id,
            device_key_thumbprint=selected_key.device_key_thumbprint,
            capabilities=(
                DeviceCapability.DEVICE_PROFILE_READ,
                DeviceCapability.DEVICE_READY_READ,
            ),
            audience=DeviceAudience.OCTO_GATEWAY,
            issued_at=now,
            expires_at=now + timedelta(minutes=15),
            token_id=self._id_factory(),
        )
        await self._device_store.put_capability_grant(
            token_sha256=opaque_token_sha256(token),
            grant=grant,
            created_from_challenge_id=request.token_challenge_id,
        )
        return DeviceTokenResponse(opaque_token=token, grant=grant)

    async def protected_ready(
        self,
        *,
        headers: DeviceProofHeaders,
        method: str,
        canonical_path: str,
        raw_body: bytes,
    ) -> MobileReadyResponse:
        authorized = await self._authorize_protected(
            headers=headers,
            method=method,
            canonical_path=canonical_path,
            raw_body=raw_body,
            required_capability=DeviceCapability.DEVICE_READY_READ,
        )
        return MobileReadyResponse(
            device_id=authorized.device_id,
            server_time=self._clock(),
        )

    async def protected_device_profile(
        self,
        *,
        headers: DeviceProofHeaders,
        method: str,
        canonical_path: str,
        raw_body: bytes,
    ) -> MobileDeviceProfileResponse:
        authorized = await self._authorize_protected(
            headers=headers,
            method=method,
            canonical_path=canonical_path,
            raw_body=raw_body,
            required_capability=DeviceCapability.DEVICE_PROFILE_READ,
        )
        device = await self._device_store.get_device(authorized.device_id)
        if device is None or device.status is not DeviceStatus.ACTIVE:
            raise DeviceTrustServiceError("DEVICE_NOT_ACTIVE", status_code=401)
        return MobileDeviceProfileResponse(
            device_id=device.device_id,
            display_name=device.display_name,
            attestation_state=device.attestation_state,
            capabilities=(
                DeviceCapability.DEVICE_PROFILE_READ,
                DeviceCapability.DEVICE_READY_READ,
            ),
        )

    async def approve_registration(
        self,
        *,
        owner_subject: str,
        challenge_id: str,
    ) -> DeviceEnrollmentStatusResponse:
        record = await self._owned_challenge(
            owner_subject=owner_subject,
            challenge_id=challenge_id,
        )
        device = await self._device_store.approve_registration(
            challenge_id=record.challenge_id,
            approved_at=self._clock(),
        )
        await self._append_device_audit(
            _DeviceAuditContext(
                device_id=device.device_id,
                owner_id=device.owner_id,
                object_hash=device.device_key_thumbprint,
                event_type=PrivacyAuditEventType.DEVICE_REGISTERED,
                decision=AuditDecision.ALLOW,
                result=AuditResult.SUCCESS,
                reason_code="DEVICE_REGISTRATION_APPROVED",
            )
        )
        return await self.get_enrollment_status(
            owner_subject=owner_subject,
            challenge_id=challenge_id,
        )

    async def reject_registration(
        self,
        *,
        owner_subject: str,
        challenge_id: str,
    ) -> DeviceEnrollmentStatusResponse:
        record = await self._owned_challenge(
            owner_subject=owner_subject,
            challenge_id=challenge_id,
        )
        device_id = record.pending_device_id or record.challenge_id
        await self._device_store.reject_registration(
            challenge_id=record.challenge_id,
            rejected_at=self._clock(),
        )
        await self._append_device_audit(
            _DeviceAuditContext(
                device_id=device_id,
                owner_id=record.owner_id,
                object_hash=_identifier_hash(record.challenge_id),
                event_type=PrivacyAuditEventType.REJECTED,
                decision=AuditDecision.DENY,
                result=AuditResult.SUCCESS,
                reason_code="DEVICE_REGISTRATION_REJECTED",
            )
        )
        return await self.get_enrollment_status(
            owner_subject=owner_subject,
            challenge_id=challenge_id,
        )

    async def list_devices(
        self,
        *,
        owner_subject: str,
    ) -> list[OwnerDeviceProjection]:
        devices = await self._device_store.list_owner_devices(owner_id_for_subject(owner_subject))
        capabilities = (
            DeviceCapability.DEVICE_PROFILE_READ,
            DeviceCapability.DEVICE_READY_READ,
        )
        return [
            OwnerDeviceProjection(device=device, capabilities=capabilities) for device in devices
        ]

    async def revoke_device(
        self,
        *,
        owner_subject: str,
        device_id: str,
    ) -> OwnerDeviceProjection:
        owner_id = owner_id_for_subject(owner_subject)
        device = await self._device_store.get_device(device_id)
        if device is None or device.owner_id != owner_id:
            raise DeviceTrustServiceError("DEVICE_NOT_FOUND", status_code=404)
        await self._device_store.revoke_device(
            device_id=device_id,
            revoked_at=self._clock(),
        )
        revoked = await self._device_store.get_device(device_id)
        if revoked is None:
            raise RuntimeError("revoked device disappeared")
        await self._append_device_audit(
            _DeviceAuditContext(
                device_id=device_id,
                owner_id=owner_id,
                object_hash=revoked.device_key_thumbprint,
                event_type=PrivacyAuditEventType.DEVICE_REVOKED,
                decision=AuditDecision.ALLOW,
                result=AuditResult.SUCCESS,
                reason_code="DEVICE_REVOKED_BY_OWNER",
            )
        )
        return OwnerDeviceProjection(
            device=revoked,
            capabilities=(
                DeviceCapability.DEVICE_PROFILE_READ,
                DeviceCapability.DEVICE_READY_READ,
            ),
        )

    async def _owned_challenge(
        self,
        *,
        owner_subject: str,
        challenge_id: str,
    ) -> RegistrationChallengeRecord:
        record = await self._device_store.load_registration_challenge(challenge_id)
        if record is None or record.owner_id != owner_id_for_subject(owner_subject):
            raise DeviceTrustServiceError("DEVICE_REGISTRATION_NOT_FOUND", status_code=404)
        return record

    @staticmethod
    def _require_current_request(timestamp: datetime, *, now: datetime) -> None:
        if abs(now - timestamp) > timedelta(minutes=2):
            raise DeviceTrustServiceError("DEVICE_REQUEST_EXPIRED", status_code=401)

    async def _authorize_protected(
        self,
        *,
        headers: DeviceProofHeaders,
        method: str,
        canonical_path: str,
        raw_body: bytes,
        required_capability: DeviceCapability,
    ) -> AuthorizedRequest:
        try:
            return await authorize_mobile_request(
                self._device_store,
                MobileAuthorizationRequest(
                    headers=headers,
                    method=method,
                    canonical_path=canonical_path,
                    raw_body=raw_body,
                    required_capability=required_capability.value,
                    now=self._clock(),
                ),
            )
        except DeviceAuthError as exc:
            raise DeviceTrustServiceError(exc.reason_code, status_code=401) from exc

    async def _append_device_audit(
        self,
        context: _DeviceAuditContext,
    ) -> None:
        await self._audit_store.append_audit(
            PrivacyAuditEvent(
                event_id=self._id_factory(),
                event_type=context.event_type,
                owner_hash=_identifier_hash(context.owner_id),
                device_hash=_identifier_hash(context.device_id),
                object_hash=context.object_hash,
                count=1,
                capabilities=(
                    DeviceCapability.DEVICE_PROFILE_READ,
                    DeviceCapability.DEVICE_READY_READ,
                ),
                decision=context.decision,
                result=context.result,
                reason_code=context.reason_code,
                occurred_at=self._clock(),
            )
        )


__all__ = [
    "DeviceTrustService",
    "DeviceTrustServiceError",
    "DeviceTrustServiceOptions",
    "owner_id_for_subject",
]
