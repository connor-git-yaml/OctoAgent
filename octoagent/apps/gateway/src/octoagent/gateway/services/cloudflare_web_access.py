"""电脑 Web 的 Cloudflare Access manifest 单一解析边界。"""

from __future__ import annotations

import asyncio
import base64
import binascii
import ipaddress
import json
import re
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import UUID

import httpx
import yaml
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding as asymmetric_padding
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import BaseModel, ConfigDict, Field, field_validator

_HOSTNAME_PATTERN = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)
_TEAM_DOMAIN_PATTERN = re.compile(
    r"^https://[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.cloudflareaccess\.com$"
)
_AUDIENCE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,256}$")
_ORIGIN_PATTERN = re.compile(r"^http://127\.0\.0\.1:([1-9][0-9]{0,4})$")
_SECRET_KEY_PARTS = frozenset(
    {
        "cookie",
        "credential",
        "password",
        "private_key",
        "secret",
        "token",
    }
)
_SECRET_VALUE_MARKERS = (
    "bearer ",
    "cf-access-client-id",
    "cf-access-client-secret",
)
_MAX_MANIFEST_BYTES = 64 * 1024
_LOOPBACK_PEER_NAMES = frozenset({"localhost", "testclient"})
_ACCESS_JWT_HEADER = "cf-access-jwt-assertion"
_JWKS_PATH = "/cdn-cgi/access/certs"
_JWKS_TIMEOUT_SECONDS = 3.0
_JWKS_TTL_SECONDS = 600
_JWKS_MAX_KEYS = 32
_JWT_CLOCK_SKEW_SECONDS = 60
_MAX_JWT_BYTES = 64 * 1024
_MUTATION_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class _ManifestContractError(ValueError):
    """Manifest字节不满足F150冻结合同。"""


class _RequestClassificationError(ValueError):
    """请求不满足Cloudflare loopback回源边界。"""


class _AccessJwtError(ValueError):
    """对外不携带任何claim、token、key或部署事实的稳定拒绝。"""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__("Cloudflare Access identity rejected")


class _CloudflareMutationError(ValueError):
    """无状态browser mutation gate拒绝；不携带header值。"""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__("Cloudflare browser mutation rejected")


class CloudflareWebAccessManifest(BaseModel):
    """F150 Web Access部署事实；不含任何凭证或会话状态。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1]
    hostname: str
    access_team_domain: str
    access_audience: str = Field(min_length=1, max_length=256)
    tunnel_id: UUID
    origin_url: str
    cloudflared_config_path: str = Field(min_length=1)

    @field_validator("hostname", mode="before")
    @classmethod
    def _normalize_hostname(cls, value: object) -> object:
        return value.strip().casefold() if isinstance(value, str) else value

    @field_validator("hostname")
    @classmethod
    def _validate_hostname(cls, value: str) -> str:
        if _HOSTNAME_PATTERN.fullmatch(value) is None:
            raise ValueError("hostname格式无效")
        return value

    @field_validator("access_team_domain", mode="before")
    @classmethod
    def _normalize_team_domain(cls, value: object) -> object:
        return value.strip().casefold().rstrip("/") if isinstance(value, str) else value

    @field_validator("access_team_domain")
    @classmethod
    def _validate_team_domain(cls, value: str) -> str:
        if _TEAM_DOMAIN_PATTERN.fullmatch(value) is None:
            raise ValueError("access_team_domain格式无效")
        return value

    @field_validator("access_audience", "cloudflared_config_path", mode="before")
    @classmethod
    def _strip_nonempty_strings(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("access_audience")
    @classmethod
    def _validate_audience(cls, value: str) -> str:
        if _AUDIENCE_PATTERN.fullmatch(value) is None:
            raise ValueError("access_audience格式无效")
        return value

    @field_validator("origin_url")
    @classmethod
    def _validate_origin_url(cls, value: str) -> str:
        match = _ORIGIN_PATTERN.fullmatch(value)
        if match is None or int(match.group(1)) > 65535:
            raise ValueError("origin_url必须是有效loopback HTTP端口")
        return value


class CloudflarePrincipal(BaseModel):
    """单次电脑Web请求内的已验证Access身份；不得持久化或写入日志。"""

    model_config = ConfigDict(frozen=True)

    subject: str
    email: str
    issued_at: datetime
    expires_at: datetime
    key_id: str


class _RemoteAccessStatus(BaseModel):
    """只由当前config/manifest/probe facts派生的非持久化投影。"""

    model_config = ConfigDict(frozen=True)

    state: Literal["unconfigured", "pending_verification", "ready", "fault"]
    hostname: str | None
    owner_email: str | None
    last_verified_at: datetime | None = None
    reason_code: str | None = None
    recovery_action: str | None = None


class _FrontDoorStatusConfig(Protocol):
    mode: str
    cloudflare_owner_email: str | None


class _RemoteAccessProbeFacts(BaseModel):
    """一次状态投影消费的瞬时探测事实。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    service_ready: bool | None = None
    origin_ready: bool | None = None
    access_ready: bool | None = None
    last_verified_at: datetime | None = None


class _CloudflaredDeploymentFacts(BaseModel):
    """一次doctor检查消费的部署与Access事实。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    config_text: str | None = None
    service_installed: bool | None = None
    service_running: bool | None = None
    access_hostname: str | None = None
    access_audience: str | None = None


def _masked_hostname(value: str) -> str:
    labels = value.split(".")
    first = labels[0]
    masked = f"{first[:1]}***"
    return ".".join([masked, *labels[1:]])


def _masked_email(value: str) -> str:
    local, _, domain = value.partition("@")
    return f"{local[:1]}***@{domain}"


def _remote_fault(
    service_ready: bool | None,
    origin_ready: bool | None,
    access_ready: bool | None,
) -> tuple[str, str] | None:
    failures = (
        (service_ready, "REMOTE_ACCESS_SERVICE_UNAVAILABLE", "restart_remote_access_service"),
        (origin_ready, "REMOTE_ACCESS_ORIGIN_UNAVAILABLE", "restart_gateway"),
        (access_ready, "REMOTE_ACCESS_ACCESS_UNAVAILABLE", "reauthenticate_access"),
    )
    return next(
        ((reason, recovery) for fact, reason, recovery in failures if fact is False),
        None,
    )


def _unconfigured_status() -> _RemoteAccessStatus:
    return _RemoteAccessStatus(
        state="unconfigured",
        hostname=None,
        owner_email=None,
        reason_code="REMOTE_ACCESS_NOT_CONFIGURED",
        recovery_action="configure_remote_access",
    )


def _derive_remote_access_status(
    *,
    front_door: _FrontDoorStatusConfig,
    manifest: CloudflareWebAccessManifest | None,
    probe: _RemoteAccessProbeFacts,
) -> _RemoteAccessStatus:
    """从单一typed manifest和瞬时facts纯派生远程访问状态。"""

    if front_door.mode != "cloudflared":
        return _unconfigured_status()
    owner = _masked_email(front_door.cloudflare_owner_email or "")
    if manifest is None:
        return _RemoteAccessStatus(
            state="fault",
            hostname=None,
            owner_email=owner,
            reason_code="REMOTE_ACCESS_MANIFEST_INVALID",
            recovery_action="review_remote_access_config",
        )
    hostname = _masked_hostname(manifest.hostname)
    failure = _remote_fault(
        probe.service_ready,
        probe.origin_ready,
        probe.access_ready,
    )
    if failure is not None:
        reason, recovery = failure
        return _RemoteAccessStatus(
            state="fault",
            hostname=hostname,
            owner_email=owner,
            reason_code=reason,
            recovery_action=recovery,
        )
    if all(fact is True for fact in (probe.service_ready, probe.origin_ready, probe.access_ready)):
        if probe.last_verified_at is not None and probe.last_verified_at.tzinfo is None:
            raise ValueError("last_verified_at必须是带时区UTC时间")
        return _RemoteAccessStatus(
            state="ready",
            hostname=hostname,
            owner_email=owner,
            last_verified_at=probe.last_verified_at,
        )
    return _RemoteAccessStatus(
        state="pending_verification",
        hostname=hostname,
        owner_email=owner,
        reason_code="REMOTE_ACCESS_VERIFICATION_PENDING",
        recovery_action="verify_remote_access",
    )


def _parse_cloudflared_config(config_text: str | None) -> dict[str, object] | None:
    if not isinstance(config_text, str) or not config_text.strip():
        return None
    try:
        payload = yaml.safe_load(config_text)
    except yaml.YAMLError:
        return None
    if not isinstance(payload, dict) or not all(isinstance(key, str) for key in payload):
        return None
    return payload


def _cloudflared_config_has_secret(config: dict[str, object]) -> bool:
    return any(key != "credentials-file" and _contains_secret_key(key) for key in config)


def _validate_cloudflared_ingress(
    config: dict[str, object],
    manifest: CloudflareWebAccessManifest,
) -> str | None:
    ingress = config.get("ingress")
    failure: str | None = None
    if not isinstance(ingress, list) or len(ingress) != 2:
        failure = "CLOUDFLARED_CATCH_ALL_MISSING"
    else:
        route, catch_all = ingress
        if (
            not isinstance(route, dict)
            or not isinstance(catch_all, dict)
            or set(route) != {"hostname", "service"}
        ):
            failure = "CLOUDFLARED_CONFIG_INVALID"
        elif route.get("hostname") != manifest.hostname:
            failure = "CLOUDFLARED_HOSTNAME_MISMATCH"
        else:
            service = route.get("service")
            if not isinstance(service, str) or _ORIGIN_PATTERN.fullmatch(service) is None:
                failure = "CLOUDFLARED_ORIGIN_NOT_LOOPBACK"
            elif service != manifest.origin_url:
                failure = "CLOUDFLARED_ORIGIN_MISMATCH"
            elif catch_all != {"service": "http_status:404"}:
                failure = "CLOUDFLARED_CATCH_ALL_MISSING"
    return failure


def _validate_cloudflared_service_contract(
    *,
    manifest: CloudflareWebAccessManifest,
    facts: _CloudflaredDeploymentFacts,
) -> str | None:
    config = _parse_cloudflared_config(facts.config_text)
    if config is None:
        return "CLOUDFLARED_CONFIG_INVALID"
    if _cloudflared_config_has_secret(config):
        return "CLOUDFLARED_CREDENTIAL_LEAK"
    if "url" in config or str(config.get("tunnel", "")) != str(manifest.tunnel_id):
        return "CLOUDFLARED_NAMED_TUNNEL_REQUIRED"
    if set(config) != {"tunnel", "credentials-file", "ingress"}:
        return "CLOUDFLARED_CONFIG_INVALID"
    credential_path = config.get("credentials-file")
    if not isinstance(credential_path, str) or not credential_path.strip():
        return "CLOUDFLARED_CONFIG_INVALID"
    ingress_failure = _validate_cloudflared_ingress(config, manifest)
    if ingress_failure is not None:
        return ingress_failure
    return _validate_cloudflared_runtime_facts(
        manifest=manifest,
        service_installed=facts.service_installed,
        service_running=facts.service_running,
        access_hostname=facts.access_hostname,
        access_audience=facts.access_audience,
    )


def _validate_cloudflared_runtime_facts(
    *,
    manifest: CloudflareWebAccessManifest,
    service_installed: bool | None,
    service_running: bool | None,
    access_hostname: str | None,
    access_audience: str | None,
) -> str | None:
    if (access_hostname or "").strip().casefold() != manifest.hostname:
        return "CLOUDFLARED_ACCESS_HOSTNAME_MISMATCH"
    if access_audience != manifest.access_audience:
        return "CLOUDFLARED_ACCESS_AUDIENCE_MISMATCH"
    if service_installed is not True:
        return "CLOUDFLARED_SERVICE_NOT_INSTALLED"
    if service_running is not True:
        return "CLOUDFLARED_SERVICE_NOT_RUNNING"
    return None


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _ManifestContractError(f"manifest字段重复: {key}")
        result[key] = value
    return result


def _contains_secret_key(key: str) -> bool:
    normalized = key.casefold().replace("-", "_")
    return any(part in normalized for part in _SECRET_KEY_PARTS)


def _validate_secret_absence(value: object, *, key: str = "") -> None:
    if key and _contains_secret_key(key):
        raise _ManifestContractError("manifest禁止凭证字段")
    if isinstance(value, dict):
        for nested_key, nested_value in value.items():
            _validate_secret_absence(nested_value, key=str(nested_key))
        return
    if isinstance(value, list):
        for nested_value in value:
            _validate_secret_absence(nested_value)
        return
    if isinstance(value, str):
        normalized = value.casefold()
        if any(marker in normalized for marker in _SECRET_VALUE_MARKERS):
            raise _ManifestContractError("manifest禁止凭证值")


def _resolve_manifest_path(project_root: Path, configured_path: Path) -> Path:
    root = project_root.resolve(strict=True)
    if configured_path.is_absolute():
        raise _ManifestContractError("manifest路径必须相对项目根目录")
    candidate = root
    for part in configured_path.parts:
        candidate /= part
        if candidate.is_symlink():
            raise _ManifestContractError("manifest路径禁止symlink")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise _ManifestContractError("manifest路径越出项目根目录")
    return resolved


def load_cloudflare_web_access_manifest(
    project_root: Path,
    configured_path: Path,
) -> CloudflareWebAccessManifest:
    """从显式项目路径读取一次，并返回可供所有消费者共享的immutable对象。"""

    path = _resolve_manifest_path(project_root, configured_path)
    raw = path.read_bytes()
    if not raw or len(raw) > _MAX_MANIFEST_BYTES:
        raise _ManifestContractError("manifest字节大小无效")
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _ManifestContractError("manifest不是有效UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise _ManifestContractError("manifest根节点必须是object")
    _validate_secret_absence(payload)
    try:
        return CloudflareWebAccessManifest.model_validate(payload)
    except ValueError as exc:
        raise _ManifestContractError("manifest schema无效") from exc


def _is_loopback_peer(client_host: str) -> bool:
    normalized = client_host.strip().casefold()
    if normalized in _LOOPBACK_PEER_NAMES:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _is_cloudflare_marker(header_name: str) -> bool:
    normalized = header_name.strip().casefold()
    return (
        normalized == "forwarded"
        or normalized.startswith("cf-access-")
        or normalized.startswith("cf-")
        or normalized.startswith("x-forwarded-")
    )


def classify_cloudflare_request(
    client_host: str,
    headers: Sequence[tuple[str, str]],
) -> Literal["direct_local", "cloudflare_access"]:
    """按TCP peer与header名称分类；marker只触发认证，绝不赋予身份。"""

    if not _is_loopback_peer(client_host):
        raise _RequestClassificationError("Gateway只接受loopback peer")
    if any(_is_cloudflare_marker(name) for name, _ in headers):
        return "cloudflare_access"
    return "direct_local"


def _decode_jwt_segment(value: str) -> bytes:
    if not value:
        raise _AccessJwtError("JWT_FORMAT_INVALID")
    padded = value + ("=" * (-len(value) % 4))
    try:
        return base64.b64decode(padded, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise _AccessJwtError("JWT_FORMAT_INVALID") from exc


def _decode_jwt_object(value: str) -> dict[str, Any]:
    try:
        payload = json.loads(
            _decode_jwt_segment(value).decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        _ManifestContractError,
    ) as exc:
        raise _AccessJwtError("JWT_FORMAT_INVALID") from exc
    if not isinstance(payload, dict):
        raise _AccessJwtError("JWT_FORMAT_INVALID")
    return payload


def _extract_access_token(headers: Sequence[tuple[str, str]]) -> str:
    values = [
        value.strip() for name, value in headers if name.strip().casefold() == _ACCESS_JWT_HEADER
    ]
    if len(values) != 1 or not values[0] or len(values[0].encode()) > _MAX_JWT_BYTES:
        raise _AccessJwtError("JWT_HEADER_INVALID")
    return values[0]


def _parse_access_token(
    token: str,
) -> tuple[str, dict[str, Any], bytes, bytes]:
    parts = token.split(".")
    if len(parts) != 3:
        raise _AccessJwtError("JWT_FORMAT_INVALID")
    header = _decode_jwt_object(parts[0])
    claims = _decode_jwt_object(parts[1])
    if header.get("alg") != "RS256":
        raise _AccessJwtError("JWT_ALGORITHM_INVALID")
    kid = header.get("kid")
    if not isinstance(kid, str) or not kid.strip():
        raise _AccessJwtError("JWT_KEY_INVALID")
    signature = _decode_jwt_segment(parts[2])
    signing_input = f"{parts[0]}.{parts[1]}".encode("ascii")
    return kid, claims, signature, signing_input


def _decode_jwk_integer(value: object) -> int:
    if not isinstance(value, str):
        raise _AccessJwtError("JWKS_INVALID")
    decoded = _decode_jwt_segment(value)
    if not decoded:
        raise _AccessJwtError("JWKS_INVALID")
    return int.from_bytes(decoded, "big")


def _parse_jwks(payload: dict[str, object]) -> dict[str, rsa.RSAPublicKey]:
    raw_keys = payload.get("keys")
    if not isinstance(raw_keys, list) or not raw_keys or len(raw_keys) > _JWKS_MAX_KEYS:
        raise _AccessJwtError("JWKS_INVALID")
    keys: dict[str, rsa.RSAPublicKey] = {}
    for raw_key in raw_keys:
        if not isinstance(raw_key, dict):
            raise _AccessJwtError("JWKS_INVALID")
        kid = raw_key.get("kid")
        if (
            not isinstance(kid, str)
            or not kid
            or kid in keys
            or raw_key.get("kty") != "RSA"
            or raw_key.get("alg") != "RS256"
            or raw_key.get("use") != "sig"
        ):
            raise _AccessJwtError("JWKS_INVALID")
        try:
            key = rsa.RSAPublicNumbers(
                _decode_jwk_integer(raw_key.get("e")),
                _decode_jwk_integer(raw_key.get("n")),
            ).public_key()
        except ValueError as exc:
            raise _AccessJwtError("JWKS_INVALID") from exc
        if key.key_size < 2048:
            raise _AccessJwtError("JWKS_INVALID")
        keys[kid] = key
    return keys


async def _fetch_cloudflare_jwks(
    url: str,
    timeout: float,
) -> dict[str, object]:
    """用隔离HTTP客户端读取Cloudflare JWKS，不继承宿主代理或凭证环境。"""

    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            payload = response.json()
    except httpx.TimeoutException as exc:
        raise TimeoutError("Cloudflare JWKS请求超时") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise OSError("Cloudflare JWKS不可用") from exc
    if not isinstance(payload, dict):
        raise OSError("Cloudflare JWKS根节点必须是object")
    return payload


def _required_string(claims: dict[str, Any], name: str) -> str:
    value = claims.get(name)
    if not isinstance(value, str) or not value.strip():
        raise _AccessJwtError("JWT_CLAIMS_INVALID")
    return value


def _required_timestamp(claims: dict[str, Any], name: str) -> int:
    value = claims.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise _AccessJwtError("JWT_CLAIMS_INVALID")
    return value


def _audience_matches(value: object, expected: str) -> bool:
    if isinstance(value, str):
        return value == expected
    return (
        isinstance(value, list)
        and all(isinstance(item, str) for item in value)
        and expected in value
    )


def _validate_claims(
    claims: dict[str, Any],
    *,
    manifest: CloudflareWebAccessManifest,
    owner_email: str,
    now: datetime,
    kid: str,
) -> CloudflarePrincipal:
    issuer = _required_string(claims, "iss")
    subject = _required_string(claims, "sub")
    email = _required_string(claims, "email").strip().casefold()
    issued_at = _required_timestamp(claims, "iat")
    expires_at = _required_timestamp(claims, "exp")
    not_before = claims.get("nbf")
    if not_before is not None and (isinstance(not_before, bool) or not isinstance(not_before, int)):
        raise _AccessJwtError("JWT_CLAIMS_INVALID")
    now_timestamp = now.timestamp()
    if (
        issuer != manifest.access_team_domain
        or not _audience_matches(claims.get("aud"), manifest.access_audience)
        or email != owner_email
        or expires_at < now_timestamp - _JWT_CLOCK_SKEW_SECONDS
        or issued_at > now_timestamp + _JWT_CLOCK_SKEW_SECONDS
        or (not_before is not None and not_before > now_timestamp + _JWT_CLOCK_SKEW_SECONDS)
    ):
        raise _AccessJwtError("JWT_CLAIMS_INVALID")
    return CloudflarePrincipal(
        subject=subject,
        email=email,
        issued_at=datetime.fromtimestamp(issued_at, UTC),
        expires_at=datetime.fromtimestamp(expires_at, UTC),
        key_id=kid,
    )


class _CloudflareAccessVerifier:
    def __init__(
        self,
        *,
        manifest: CloudflareWebAccessManifest,
        owner_email: str,
        fetch_jwks: Callable[[str, float], Awaitable[dict[str, object]]],
        clock: Callable[[], datetime],
    ) -> None:
        normalized_owner = owner_email.strip().casefold()
        if not normalized_owner:
            raise _AccessJwtError("OWNER_INVALID")
        self._manifest = manifest
        self._owner_email = normalized_owner
        self._fetch_jwks = fetch_jwks
        self._clock = clock
        self._jwks_url = f"{manifest.access_team_domain}{_JWKS_PATH}"
        self._keys: dict[str, rsa.RSAPublicKey] = {}
        self._loaded_at: datetime | None = None
        self._refresh_lock = asyncio.Lock()

    def _cache_is_fresh(self, now: datetime) -> bool:
        return (
            self._loaded_at is not None
            and 0 <= (now - self._loaded_at).total_seconds() < _JWKS_TTL_SECONDS
        )

    async def _refresh_key(
        self,
        kid: str,
        *,
        force: bool,
    ) -> rsa.RSAPublicKey:
        async with self._refresh_lock:
            now = self._clock()
            if self._cache_is_fresh(now) and (not force or kid in self._keys):
                cached = self._keys.get(kid)
                if cached is not None:
                    return cached
            try:
                payload = await self._fetch_jwks(
                    self._jwks_url,
                    _JWKS_TIMEOUT_SECONDS,
                )
            except (OSError, TimeoutError) as exc:
                raise _AccessJwtError("JWKS_UNAVAILABLE") from exc
            keys = _parse_jwks(payload)
            key = keys.get(kid)
            if key is None:
                raise _AccessJwtError("JWT_KEY_UNKNOWN")
            self._keys = keys
            self._loaded_at = now
            return key

    async def _key_for(self, kid: str) -> rsa.RSAPublicKey:
        now = self._clock()
        if self._cache_is_fresh(now):
            key = self._keys.get(kid)
            if key is not None:
                return key
            return await self._refresh_key(kid, force=True)
        try:
            return await self._refresh_key(kid, force=False)
        except _AccessJwtError as exc:
            if exc.reason_code != "JWT_KEY_UNKNOWN":
                raise
        return await self._refresh_key(kid, force=True)

    async def __call__(
        self,
        headers: Sequence[tuple[str, str]],
    ) -> CloudflarePrincipal:
        token = _extract_access_token(headers)
        kid, claims, signature, signing_input = _parse_access_token(token)
        key = await self._key_for(kid)
        try:
            key.verify(
                signature,
                signing_input,
                asymmetric_padding.PKCS1v15(),
                hashes.SHA256(),
            )
        except InvalidSignature as exc:
            raise _AccessJwtError("JWT_SIGNATURE_INVALID") from exc
        now = self._clock()
        if now.tzinfo is None:
            raise _AccessJwtError("CLOCK_INVALID")
        return _validate_claims(
            claims,
            manifest=self._manifest,
            owner_email=self._owner_email,
            now=now,
            kid=kid,
        )


def verify_cloudflare_access_jwt(
    *,
    manifest: CloudflareWebAccessManifest,
    owner_email: str,
    fetch_jwks: Callable[[str, float], Awaitable[dict[str, object]]],
    clock: Callable[[], datetime],
) -> Callable[[Sequence[tuple[str, str]]], Awaitable[CloudflarePrincipal]]:
    """构造唯一request verifier；同一实例拥有bounded TTL cache与single-flight锁。"""

    verifier = _CloudflareAccessVerifier(
        manifest=manifest,
        owner_email=owner_email,
        fetch_jwks=fetch_jwks,
        clock=clock,
    )
    return verifier


def validate_cloudflare_mutation(
    *,
    method: str,
    host: str | None,
    origin: str | None,
    content_type: str | None,
    hostname: str,
) -> None:
    """对已验证的远程浏览器mutation执行exact Host/Origin/JSON检查。"""

    if method.strip().upper() not in _MUTATION_METHODS:
        return
    normalized_host = (host or "").strip().casefold()
    expected_host = hostname.strip().casefold()
    if normalized_host not in {expected_host, f"{expected_host}:443"}:
        raise _CloudflareMutationError("MUTATION_HOST_INVALID")
    if (origin or "").strip() != f"https://{expected_host}":
        raise _CloudflareMutationError("MUTATION_ORIGIN_INVALID")
    media_type = (content_type or "").partition(";")[0].strip().casefold()
    if media_type != "application/json":
        raise _CloudflareMutationError("MUTATION_MEDIA_TYPE_INVALID")
