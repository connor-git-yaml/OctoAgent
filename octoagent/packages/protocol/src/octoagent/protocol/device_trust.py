"""F153 原生设备注册、短期凭证与请求证明的 transport DTO。"""

from __future__ import annotations

import base64
import hashlib
from datetime import timedelta
from enum import StrEnum
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from octoagent.core.models import (
    AttestationState,
    CapabilityGrant,
    DeviceCapability,
    DeviceIdentity,
    canonical_json_bytes,
)
from octoagent.core.models.privacy_ingestion import (
    Nonce,
    NonEmptyString,
    Sha256,
    UtcSecond,
)
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

Base64Url43 = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{43}$")]
Base64Url = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=8, max_length=16384),
    Field(pattern=r"^[A-Za-z0-9_-]+$"),
]
PublicKeyX963 = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{87}$")]
OpaqueDeviceToken = Annotated[str, Field(pattern=r"^octo_dt1_[A-Za-z0-9_-]{43}$")]
DeviceAuthorization = Annotated[
    str,
    Field(pattern=r"^OctoDevice octo_dt1_[A-Za-z0-9_-]{43}$"),
]


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _decode_base64url(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.b64decode(
            value + padding,
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError("value must use canonical base64url") from exc


def _validate_https_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("mobile origin must be an HTTPS origin without path or port")
    try:
        value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("mobile origin must be ASCII") from exc
    canonical = f"https://{parsed.hostname.lower()}"
    if value.rstrip("/") != canonical:
        raise ValueError("mobile origin must be canonical")
    return canonical


def _validate_public_key_transport(value: str) -> str:
    decoded = _decode_base64url(value)
    if len(decoded) != 65 or decoded[0] != 0x04:
        raise ValueError("public key must be uncompressed P-256 X9.63")
    return value


def _validate_signature_transport(value: str) -> str:
    decoded = _decode_base64url(value)
    if not 8 <= len(decoded) <= 80:
        raise ValueError("signature must be bounded DER bytes")
    return value


class DeviceEnrollmentStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    REJECTED = "rejected"
    EXPIRED = "expired"
    REVOKED = "revoked"


class OwnerRegistrationChallengeResponse(_StrictFrozenModel):
    challenge_id: NonEmptyString
    challenge_secret: Base64Url43 = Field(repr=False)
    mobile_origin: NonEmptyString
    expires_at: UtcSecond

    _mobile_origin = field_validator("mobile_origin")(_validate_https_origin)


class DeviceEnrollmentRequest(_StrictFrozenModel):
    challenge_id: NonEmptyString
    challenge_secret: Base64Url43 = Field(repr=False)
    display_name: NonEmptyString
    mobile_origin: NonEmptyString
    public_key_x963: PublicKeyX963
    challenge_signature_der: Base64Url = Field(repr=False)
    attestation_state: AttestationState
    attestation_object: Base64Url | None = Field(default=None, repr=False)
    timestamp: UtcSecond

    _mobile_origin = field_validator("mobile_origin")(_validate_https_origin)

    @field_validator("public_key_x963")
    @classmethod
    def validate_public_key(cls, value: str) -> str:
        return _validate_public_key_transport(value)

    @field_validator("challenge_signature_der")
    @classmethod
    def validate_signature_transport(cls, value: str) -> str:
        return _validate_signature_transport(value)

    @model_validator(mode="after")
    def validate_attestation(self) -> DeviceEnrollmentRequest:
        if self.attestation_state is AttestationState.UNSUPPORTED:
            if self.attestation_object is not None:
                raise ValueError("unsupported attestation cannot include an object")
        elif self.attestation_state is AttestationState.VERIFIED:
            if self.attestation_object is None:
                raise ValueError("verified attestation requires an object")
        elif self.attestation_state is AttestationState.FAILED:
            raise ValueError("failed attestation cannot be submitted for enrollment")
        return self


class DeviceEnrollmentStatusResponse(_StrictFrozenModel):
    challenge_id: NonEmptyString
    state: DeviceEnrollmentStatus
    device_id: NonEmptyString | None = None
    device_key_thumbprint: Sha256 | None = None
    attestation_state: AttestationState | None = None
    reason_code: Annotated[str, StringConstraints(max_length=128)] = ""

    @model_validator(mode="after")
    def validate_state_projection(self) -> DeviceEnrollmentStatusResponse:
        has_identity = all(
            value is not None
            for value in (
                self.device_id,
                self.device_key_thumbprint,
                self.attestation_state,
            )
        )
        if self.state is DeviceEnrollmentStatus.PENDING:
            supplied = sum(
                value is not None
                for value in (
                    self.device_id,
                    self.device_key_thumbprint,
                    self.attestation_state,
                )
            )
            if supplied not in {0, 3} or self.reason_code:
                raise ValueError("pending state requires zero or one complete identity")
        elif self.state in {
            DeviceEnrollmentStatus.ACTIVE,
            DeviceEnrollmentStatus.REVOKED,
        }:
            if not has_identity or self.reason_code:
                raise ValueError("device state requires identity and no reason")
        elif has_identity or not self.reason_code.strip():
            raise ValueError("terminal rejection requires only a reason")
        return self


class OwnerDeviceProjection(_StrictFrozenModel):
    device: DeviceIdentity
    capabilities: tuple[DeviceCapability, ...]

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(
        cls,
        values: tuple[DeviceCapability, ...],
    ) -> tuple[DeviceCapability, ...]:
        if not values or len(values) != len(set(values)):
            raise ValueError("device capabilities must be non-empty and unique")
        return tuple(sorted(values, key=lambda value: value.value))


class DeviceKeyRotationChallengeResponse(_StrictFrozenModel):
    rotation_challenge_id: NonEmptyString
    device_id: NonEmptyString
    server_challenge: Base64Url43 = Field(repr=False)
    mobile_origin: NonEmptyString
    expires_at: UtcSecond

    _mobile_origin = field_validator("mobile_origin")(_validate_https_origin)


class DeviceKeyRotationRequest(_StrictFrozenModel):
    rotation_challenge_id: NonEmptyString
    device_id: NonEmptyString
    server_challenge: Base64Url43 = Field(repr=False)
    mobile_origin: NonEmptyString
    current_key_thumbprint: Sha256
    new_public_key_x963: PublicKeyX963
    current_key_signature_der: Base64Url = Field(repr=False)
    new_key_signature_der: Base64Url = Field(repr=False)
    timestamp: UtcSecond

    _mobile_origin = field_validator("mobile_origin")(_validate_https_origin)

    @field_validator("new_public_key_x963")
    @classmethod
    def validate_public_key(cls, value: str) -> str:
        return _validate_public_key_transport(value)

    @field_validator("current_key_signature_der", "new_key_signature_der")
    @classmethod
    def validate_signature_transport(cls, value: str) -> str:
        return _validate_signature_transport(value)


class DeviceKeyRotationResponse(_StrictFrozenModel):
    device_id: NonEmptyString
    previous_key_thumbprint: Sha256
    current_key_thumbprint: Sha256
    rotated_at: UtcSecond
    overlap_expires_at: UtcSecond

    @model_validator(mode="after")
    def validate_rotation_window(self) -> DeviceKeyRotationResponse:
        if self.current_key_thumbprint == self.previous_key_thumbprint:
            raise ValueError("rotation must replace the current key")
        if not self.rotated_at < self.overlap_expires_at:
            raise ValueError("rotation overlap must end after rotation")
        if self.overlap_expires_at > self.rotated_at + timedelta(minutes=15):
            raise ValueError("rotation overlap exceeds fifteen minutes")
        return self


class DeviceTokenChallengeResponse(_StrictFrozenModel):
    token_challenge_id: NonEmptyString
    device_id: NonEmptyString
    server_challenge: Base64Url43 = Field(repr=False)
    mobile_origin: NonEmptyString
    expires_at: UtcSecond

    _mobile_origin = field_validator("mobile_origin")(_validate_https_origin)


class DeviceTokenRequest(_StrictFrozenModel):
    token_challenge_id: NonEmptyString
    device_id: NonEmptyString
    server_challenge: Base64Url43 = Field(repr=False)
    mobile_origin: NonEmptyString
    challenge_signature_der: Base64Url = Field(repr=False)
    timestamp: UtcSecond

    _mobile_origin = field_validator("mobile_origin")(_validate_https_origin)

    @field_validator("challenge_signature_der")
    @classmethod
    def validate_signature_transport(cls, value: str) -> str:
        return _validate_signature_transport(value)


class DeviceTokenResponse(_StrictFrozenModel):
    opaque_token: OpaqueDeviceToken = Field(repr=False)
    grant: CapabilityGrant


class DeviceProofHeaders(_StrictFrozenModel):
    authorization: DeviceAuthorization = Field(repr=False)
    timestamp: UtcSecond
    nonce: Nonce
    signature: Base64Url = Field(repr=False)

    @field_validator("signature")
    @classmethod
    def validate_signature_transport(cls, value: str) -> str:
        return _validate_signature_transport(value)


class MobileReadyResponse(_StrictFrozenModel):
    status: Literal["ready"] = "ready"
    device_id: NonEmptyString
    server_time: UtcSecond


class MobileDeviceProfileResponse(_StrictFrozenModel):
    device_id: NonEmptyString
    owner_id: NonEmptyString
    display_name: NonEmptyString
    attestation_state: AttestationState
    capabilities: tuple[DeviceCapability, ...]

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(
        cls,
        values: tuple[DeviceCapability, ...],
    ) -> tuple[DeviceCapability, ...]:
        if not values or len(values) != len(set(values)):
            raise ValueError("device capabilities must be non-empty and unique")
        return tuple(sorted(values, key=lambda value: value.value))


class DeviceTrustErrorResponse(_StrictFrozenModel):
    code: NonEmptyString
    message: NonEmptyString
    retryable: bool


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def enrollment_signature_bytes(request: DeviceEnrollmentRequest) -> bytes:
    """返回不含 secret/signature/attestation object 的 enrollment 签名 bytes。"""

    payload: dict[str, Any] = {
        "challenge_id": request.challenge_id,
        "challenge_secret_sha256": _sha256_text(request.challenge_secret),
        "display_name": request.display_name,
        "mobile_origin": request.mobile_origin,
        "public_key_x963": request.public_key_x963,
        "timestamp": request.timestamp,
    }
    return canonical_json_bytes(payload)


def token_challenge_signature_bytes(request: DeviceTokenRequest) -> bytes:
    """返回 active device 换取短 token 时签名的唯一 bytes。"""

    payload: dict[str, Any] = {
        "device_id": request.device_id,
        "mobile_origin": request.mobile_origin,
        "server_challenge_sha256": _sha256_text(request.server_challenge),
        "timestamp": request.timestamp,
        "token_challenge_id": request.token_challenge_id,
    }
    return canonical_json_bytes(payload)


def key_rotation_signature_bytes(request: DeviceKeyRotationRequest) -> bytes:
    """返回 current/new device key 必须共同签名的 rotation bytes。"""

    payload: dict[str, Any] = {
        "current_key_thumbprint": request.current_key_thumbprint,
        "device_id": request.device_id,
        "mobile_origin": request.mobile_origin,
        "new_public_key_x963": request.new_public_key_x963,
        "rotation_challenge_id": request.rotation_challenge_id,
        "server_challenge_sha256": _sha256_text(request.server_challenge),
        "timestamp": request.timestamp,
    }
    return canonical_json_bytes(payload)


__all__ = [
    "DeviceEnrollmentRequest",
    "DeviceEnrollmentStatus",
    "DeviceEnrollmentStatusResponse",
    "DeviceKeyRotationChallengeResponse",
    "DeviceKeyRotationRequest",
    "DeviceKeyRotationResponse",
    "DeviceProofHeaders",
    "DeviceTokenChallengeResponse",
    "DeviceTokenRequest",
    "DeviceTokenResponse",
    "DeviceTrustErrorResponse",
    "MobileDeviceProfileResponse",
    "MobileReadyResponse",
    "OwnerDeviceProjection",
    "OwnerRegistrationChallengeResponse",
    "enrollment_signature_bytes",
    "key_rotation_signature_bytes",
    "token_challenge_signature_bytes",
]
