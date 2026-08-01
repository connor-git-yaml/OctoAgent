"""F153 P-256 device proof、opaque token 与 F152 policy 编排。"""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from octoagent.core.models import (
    DeviceAudience,
    DeviceCapability,
    DeviceIdentity,
    HttpMethod,
    RequestProofPayload,
    canonical_json_bytes,
)
from octoagent.core.store.device_trust_store import SqliteDeviceTrustStore
from octoagent.policy import (
    DeviceAuthorizationContext,
    PrivacyIngestionPolicyError,
    authorize_device_request,
)
from octoagent.policy.privacy_ingestion_policy import AuthorizedRequest
from octoagent.protocol.device_trust import (
    DeviceEnrollmentRequest,
    DeviceKeyRotationRequest,
    DeviceProofHeaders,
    DeviceTokenRequest,
    enrollment_signature_bytes,
    key_rotation_signature_bytes,
    token_challenge_signature_bytes,
)

_OPAQUE_TOKEN_PATTERN = re.compile(r"^octo_dt1_[A-Za-z0-9_-]{43}$")
_TOKEN_BYTES = 32


class DeviceAuthError(ValueError):
    """不携带 token/key/signature 的稳定设备认证拒绝。"""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def _deny(reason_code: str) -> None:
    raise DeviceAuthError(reason_code)


@dataclass(frozen=True, slots=True)
class MobileAuthorizationRequest:
    """一次 mobile proof 验证所需的完整、不可变请求事实。"""

    headers: DeviceProofHeaders
    method: str
    canonical_path: str
    raw_body: bytes
    required_capability: str
    now: datetime


def _decode_base64url(value: str, *, reason_code: str) -> bytes:
    try:
        decoded = base64.b64decode(
            value + ("=" * (-len(value) % 4)),
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, base64.binascii.Error):
        _deny(reason_code)
    if base64.urlsafe_b64encode(decoded).rstrip(b"=").decode() != value:
        _deny(reason_code)
    return decoded


def generate_opaque_device_token(
    *,
    random_bytes: Callable[[int], bytes] = secrets.token_bytes,
) -> str:
    """生成 256-bit、无 padding、不可从环境读取的短期 token。"""

    material = random_bytes(_TOKEN_BYTES)
    if not isinstance(material, bytes) or len(material) != _TOKEN_BYTES:
        _deny("DEVICE_TOKEN_ENTROPY_INVALID")
    encoded = base64.urlsafe_b64encode(material).rstrip(b"=").decode()
    return f"octo_dt1_{encoded}"


def opaque_token_sha256(token: str) -> str:
    """验证 token transport 后只返回可持久化 SHA-256。"""

    if _OPAQUE_TOKEN_PATTERN.fullmatch(token) is None:
        _deny("DEVICE_TOKEN_INVALID")
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _public_key(public_key_x963: str) -> ec.EllipticCurvePublicKey:
    encoded = _decode_base64url(
        public_key_x963,
        reason_code="DEVICE_PUBLIC_KEY_INVALID",
    )
    if len(encoded) != 65 or encoded[0] != 0x04:
        _deny("DEVICE_PUBLIC_KEY_INVALID")
    try:
        key = ec.EllipticCurvePublicKey.from_encoded_point(
            ec.SECP256R1(),
            encoded,
        )
    except ValueError:
        _deny("DEVICE_PUBLIC_KEY_INVALID")
    return key


def _verify_signature(
    *,
    public_key_x963: str,
    signature_der: str,
    payload: bytes,
) -> None:
    signature = _decode_base64url(
        signature_der,
        reason_code="DEVICE_SIGNATURE_INVALID",
    )
    try:
        _public_key(public_key_x963).verify(
            signature,
            payload,
            ec.ECDSA(hashes.SHA256()),
        )
    except (InvalidSignature, ValueError):
        _deny("DEVICE_SIGNATURE_INVALID")


def verify_enrollment_signature(request: DeviceEnrollmentRequest) -> str:
    """验证 enrollment envelope 并返回 raw X9.63 key thumbprint。"""

    _verify_signature(
        public_key_x963=request.public_key_x963,
        signature_der=request.challenge_signature_der,
        payload=enrollment_signature_bytes(request),
    )
    public_key = _decode_base64url(
        request.public_key_x963,
        reason_code="DEVICE_PUBLIC_KEY_INVALID",
    )
    return hashlib.sha256(public_key).hexdigest()


def verify_token_challenge_signature(
    request: DeviceTokenRequest,
    *,
    public_key_x963: str,
) -> None:
    """验证 active device 换取 token 的 server challenge 签名。"""

    _verify_signature(
        public_key_x963=public_key_x963,
        signature_der=request.challenge_signature_der,
        payload=token_challenge_signature_bytes(request),
    )


def verify_key_rotation_signatures(
    request: DeviceKeyRotationRequest,
    *,
    current_public_key_x963: str,
) -> str:
    """验证 current/new 双密钥持有证明并返回新 key thumbprint。"""

    payload = key_rotation_signature_bytes(request)
    _verify_signature(
        public_key_x963=current_public_key_x963,
        signature_der=request.current_key_signature_der,
        payload=payload,
    )
    _verify_signature(
        public_key_x963=request.new_public_key_x963,
        signature_der=request.new_key_signature_der,
        payload=payload,
    )
    new_public_key = _decode_base64url(
        request.new_public_key_x963,
        reason_code="DEVICE_PUBLIC_KEY_INVALID",
    )
    new_thumbprint = hashlib.sha256(new_public_key).hexdigest()
    if new_thumbprint == request.current_key_thumbprint:
        _deny("DEVICE_KEY_ROTATION_INVALID")
    return new_thumbprint


def _token_from_headers(headers: DeviceProofHeaders) -> str:
    prefix = "OctoDevice "
    if not headers.authorization.startswith(prefix):
        _deny("DEVICE_TOKEN_INVALID")
    token = headers.authorization.removeprefix(prefix)
    opaque_token_sha256(token)
    return token


def _verification_identity(
    *,
    device: DeviceIdentity,
    public_key_x963: str,
    device_key_thumbprint: str,
) -> DeviceIdentity:
    return DeviceIdentity.model_validate(
        device.model_dump(mode="python")
        | {
            "public_key": public_key_x963,
            "device_key_thumbprint": device_key_thumbprint,
        }
    )


async def authorize_mobile_request(
    store: SqliteDeviceTrustStore,
    request: MobileAuthorizationRequest,
) -> AuthorizedRequest:
    """先验 token/key/signature/policy，再 durable consume replay。"""

    token = _token_from_headers(request.headers)
    grant = await store.get_capability_grant(
        token_sha256=opaque_token_sha256(token),
        now=request.now,
    )
    if grant is None:
        _deny("DEVICE_TOKEN_INVALID")
    device = await store.get_device(grant.device_id)
    if device is None:
        _deny("DEVICE_NOT_FOUND")
    keys = await store.list_verification_keys(
        device_id=device.device_id,
        now=request.now,
    )
    selected_key = next(
        (key for key in keys if key.device_key_thumbprint == grant.device_key_thumbprint),
        None,
    )
    if selected_key is None:
        _deny("DEVICE_KEY_MISMATCH")
    try:
        proof = RequestProofPayload(
            method=HttpMethod(request.method.upper()),
            canonical_path=request.canonical_path,
            body_sha256=hashlib.sha256(request.raw_body).hexdigest(),
            timestamp=request.headers.timestamp,
            nonce=request.headers.nonce,
            token_id=grant.token_id,
        )
        capability = DeviceCapability(request.required_capability)
    except ValueError:
        _deny("DEVICE_REQUEST_INVALID")
    _verify_signature(
        public_key_x963=selected_key.public_key_x963,
        signature_der=request.headers.signature,
        payload=canonical_json_bytes(proof),
    )
    verification_device = _verification_identity(
        device=device,
        public_key_x963=selected_key.public_key_x963,
        device_key_thumbprint=selected_key.device_key_thumbprint,
    )
    try:
        authorized = authorize_device_request(
            device=verification_device,
            grant=grant,
            proof=proof,
            context=DeviceAuthorizationContext(
                required_capability=capability,
                expected_method=proof.method,
                expected_path=request.canonical_path,
                expected_body_sha256=proof.body_sha256,
                expected_audience=DeviceAudience.OCTO_GATEWAY,
                now=request.now,
                used_replay_keys=frozenset(),
            ),
        )
    except PrivacyIngestionPolicyError as exc:
        _deny(exc.reason_code)
    replay_expires_at = min(
        request.now + timedelta(minutes=5),
        grant.expires_at,
    )
    if not await store.consume_request_proof(
        replay_key=authorized.replay_key,
        device_id=authorized.device_id,
        token_id=authorized.token_id,
        consumed_at=request.now,
        expires_at=replay_expires_at,
    ):
        _deny("REQUEST_REPLAYED")
    return authorized


__all__ = [
    "DeviceAuthError",
    "MobileAuthorizationRequest",
    "authorize_mobile_request",
    "generate_opaque_device_token",
    "opaque_token_sha256",
    "verify_enrollment_signature",
    "verify_key_rotation_signatures",
    "verify_token_challenge_signature",
]
