"""F153 原生 iOS dedicated hostname 的部署事实与路由隔离边界。"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi.responses import JSONResponse
from octoagent.core.store import StoreGroup
from pydantic import BaseModel, ConfigDict, model_validator
from starlette.types import ASGIApp, Receive, Scope, Send

from .device_trust import DeviceTrustService, DeviceTrustServiceOptions

_HOSTNAME_PATTERN = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)
_LOOPBACK_ORIGIN_PATTERN = re.compile(r"^http://127\.0\.0\.1:([1-9][0-9]{0,4})$")
_MAX_MANIFEST_BYTES = 64 * 1024
_OWNER_PREFIX = "/api/device-trust/v1/"
_MOBILE_PREFIX = "/api/mobile/v1/"
_MOBILE_STATIC_PATHS = frozenset(
    {
        "/api/mobile/v1/enrollments",
        "/api/mobile/v1/key-rotations",
        "/api/mobile/v1/tokens",
        "/api/mobile/v1/ready",
        "/api/mobile/v1/device-profile",
    }
)
_MOBILE_TOKEN_CHALLENGE_PATTERN = re.compile(r"^/api/mobile/v1/token-challenges/[^/]+$")
_MOBILE_ROTATION_CHALLENGE_PATTERN = re.compile(
    r"^/api/mobile/v1/key-rotation-challenges/[^/]+$"
)


class MobileDeviceAccessContractError(ValueError):
    """mobile access manifest/config 不满足冻结合同。"""


class MobileDeviceAccessManifestV1(BaseModel):
    """同一 named tunnel 上 Web 与原生 iOS 的非敏感部署事实。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1]
    web_hostname: str
    mobile_hostname: str
    tunnel_id: UUID
    loopback_origin: str
    mobile_path_prefix: Literal["/api/mobile/v1/"]
    edge_policy: Literal["access-bypass-origin-device-proof"]

    @model_validator(mode="before")
    @classmethod
    def normalize_hostnames(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        normalized = dict(value)
        for name in ("web_hostname", "mobile_hostname"):
            hostname = normalized.get(name)
            if isinstance(hostname, str):
                normalized[name] = hostname.strip().casefold()
        return normalized

    @model_validator(mode="after")
    def validate_deployment_boundary(self) -> MobileDeviceAccessManifestV1:
        for name, hostname in (
            ("web_hostname", self.web_hostname),
            ("mobile_hostname", self.mobile_hostname),
        ):
            if _HOSTNAME_PATTERN.fullmatch(hostname) is None:
                raise ValueError(f"{name}格式无效")
        if self.web_hostname == self.mobile_hostname:
            raise ValueError("Web 与 mobile hostname 必须不同")
        origin = _LOOPBACK_ORIGIN_PATTERN.fullmatch(self.loopback_origin)
        if origin is None or int(origin.group(1)) > 65535:
            raise ValueError("loopback_origin必须是有效127.0.0.1 HTTP端口")
        return self


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise MobileDeviceAccessContractError("manifest包含重复字段")
        result[key] = value
    return result


def _resolve_manifest_path(project_root: Path, configured_path: Path) -> Path:
    root = project_root.resolve(strict=True)
    if configured_path.is_absolute():
        raise MobileDeviceAccessContractError("manifest路径必须相对项目根目录")
    candidate = root
    for part in configured_path.parts:
        candidate /= part
        if candidate.is_symlink():
            raise MobileDeviceAccessContractError("manifest路径禁止symlink")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise MobileDeviceAccessContractError("manifest路径越出项目根目录")
    return resolved


def load_mobile_device_access_manifest(
    project_root: Path,
    configured_path: Path,
) -> MobileDeviceAccessManifestV1:
    """只从项目内显式路径加载一次 immutable mobile manifest。"""

    path = _resolve_manifest_path(project_root, configured_path)
    raw = path.read_bytes()
    if not raw or len(raw) > _MAX_MANIFEST_BYTES:
        raise MobileDeviceAccessContractError("manifest字节大小无效")
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MobileDeviceAccessContractError("manifest不是有效UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise MobileDeviceAccessContractError("manifest根节点必须是object")
    try:
        return MobileDeviceAccessManifestV1.model_validate(payload)
    except ValueError as exc:
        raise MobileDeviceAccessContractError("manifest schema无效") from exc


def build_device_trust_service(
    *,
    manifest: MobileDeviceAccessManifestV1,
    store_group: StoreGroup,
) -> DeviceTrustService:
    """用生产 StoreGroup 与同一 manifest 构造唯一 device-trust service。"""

    return DeviceTrustService(
        device_store=store_group.device_trust_store,
        audit_store=store_group.privacy_ingestion_store,
        options=DeviceTrustServiceOptions(
            mobile_origin=f"https://{manifest.mobile_hostname}",
        ),
    )


def masked_hostname(value: str) -> str:
    """仅保留域后缀的诊断投影，避免在普通输出暴露完整部署地址。"""

    labels = value.split(".")
    return ".".join([f"{labels[0][:1]}***", *labels[1:]])


def _mobile_path_allowed(path: str) -> bool:
    return path in _MOBILE_STATIC_PATHS or any(
        pattern.fullmatch(path)
        for pattern in (
            _MOBILE_TOKEN_CHALLENGE_PATTERN,
            _MOBILE_ROTATION_CHALLENGE_PATTERN,
        )
    )


def _host_header(scope: Scope) -> str:
    values = [
        value.decode("latin-1").strip().casefold()
        for key, value in scope.get("headers", [])
        if key.decode("latin-1").casefold() == "host"
    ]
    return values[0] if len(values) == 1 else ""


def _uses_external_https(scope: Scope) -> bool:
    scheme = str(scope.get("scheme", "")).casefold()
    if scheme == "https":
        return True
    if scheme != "http":
        return False
    client = scope.get("client")
    if not isinstance(client, (tuple, list)) or not client or client[0] != "127.0.0.1":
        return False
    forwarded_proto = [
        value.decode("latin-1").strip().casefold()
        for key, value in scope.get("headers", [])
        if key.decode("latin-1").casefold() == "x-forwarded-proto"
    ]
    return forwarded_proto == ["https"]


async def _not_found(scope: Scope, receive: Receive, send: Send) -> None:
    response = JSONResponse(
        status_code=404,
        content={
            "detail": {
                "code": "MOBILE_ROUTE_NOT_FOUND",
                "message": "这个地址没有可用的服务。",
            }
        },
    )
    await response(scope, receive, send)


class MobileDeviceAccessMiddleware:
    """在路由匹配前执行 Web/mobile Host 与路径的 fail-closed 隔离。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        app = scope.get("app")
        manifest = getattr(
            getattr(app, "state", None),
            "mobile_device_access_manifest",
            None,
        )
        path = str(scope.get("path", ""))
        is_device_route = path.startswith(_MOBILE_PREFIX) or path.startswith(_OWNER_PREFIX)
        if not isinstance(manifest, MobileDeviceAccessManifestV1):
            if is_device_route:
                await _not_found(scope, receive, send)
                return
            await self.app(scope, receive, send)
            return

        host = _host_header(scope)
        if host == manifest.mobile_hostname:
            if not _uses_external_https(scope) or not _mobile_path_allowed(path):
                await _not_found(scope, receive, send)
                return
            if str(scope.get("scheme", "")).casefold() == "http":
                scope = {**scope, "scheme": "https"}
        elif path.startswith(_MOBILE_PREFIX) or (
            path.startswith(_OWNER_PREFIX) and host != manifest.web_hostname
        ):
            await _not_found(scope, receive, send)
            return
        await self.app(scope, receive, send)
