"""Gateway front-door auth / trusted proxy 边界。"""

from __future__ import annotations

import ipaddress
import os
import secrets
from pathlib import Path

import structlog
from fastapi import HTTPException, Request
from octoagent.provider.dx.config_schema import FrontDoorConfig
from octoagent.provider.dx.config_wizard import load_config
from pydantic import ValidationError

log = structlog.get_logger()

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}
_SSE_QUERY_TOKEN_PARAM = "access_token"


def _http_error(
    status_code: int,
    code: str,
    message: str,
    *,
    hint: str | None = None,
    headers: dict[str, str] | None = None,
) -> HTTPException:
    detail = {"code": code, "message": message}
    if hint:
        detail["hint"] = hint
    return HTTPException(status_code=status_code, detail=detail, headers=headers)


class FrontDoorGuard:
    """统一保护 owner-facing API。"""

    def __init__(self, project_root: Path) -> None:
        self._project_root = project_root

    async def authorize(self, request: Request) -> None:
        config = self._load_front_door_config()
        client_host = self._resolve_client_host(request)

        if config.mode == "loopback":
            if self._is_loopback_host(client_host):
                return
            raise _http_error(
                403,
                "FRONT_DOOR_LOOPBACK_ONLY",
                "当前实例仅允许本机直连访问 owner-facing API。",
                hint="如需通过公网或反向代理访问，请切换到 bearer 或 trusted_proxy 模式。",
            )

        if config.mode == "bearer":
            expected = self._read_secret(
                config.bearer_token_env,
                code="FRONT_DOOR_TOKEN_ENV_MISSING",
                hint="请设置 bearer_token_env 指向的环境变量后重试。",
            )
            presented = self._extract_bearer_token(
                request,
                allow_query_token=request.url.path.startswith("/api/stream/"),
            )
            if not presented:
                raise _http_error(
                    401,
                    "FRONT_DOOR_TOKEN_REQUIRED",
                    "当前实例要求 Bearer Token。",
                    hint="Web 控制台可在页面中输入 token；SSE 连接使用 access_token 查询参数。",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            if not secrets.compare_digest(presented, expected):
                raise _http_error(
                    401,
                    "FRONT_DOOR_TOKEN_INVALID",
                    "Bearer Token 无效。",
                    hint="请确认本地保存的 token 与服务端环境变量一致。",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            return

        if config.mode == "trusted_proxy":
            if not self._is_trusted_proxy_client(client_host, config.trusted_proxy_cidrs):
                raise _http_error(
                    403,
                    "FRONT_DOOR_TRUSTED_PROXY_REQUIRED",
                    "当前实例仅接受受信代理转发的请求。",
                    hint="请确认 Gateway 仅暴露给反向代理，并正确配置 trusted_proxy_cidrs。",
                )
            expected = self._read_secret(
                config.trusted_proxy_token_env,
                code="FRONT_DOOR_PROXY_TOKEN_ENV_MISSING",
                hint="请设置 trusted_proxy_token_env 指向的环境变量后重试。",
            )
            header_value = request.headers.get(config.trusted_proxy_header, "").strip()
            if not header_value:
                raise _http_error(
                    403,
                    "FRONT_DOOR_PROXY_TOKEN_REQUIRED",
                    "缺少 trusted proxy 共享鉴权 header。",
                    hint=(
                        f"请让反向代理注入 {config.trusted_proxy_header}，"
                        "并保证 Gateway 只接受代理来源地址。"
                    ),
                )
            if not secrets.compare_digest(header_value, expected):
                raise _http_error(
                    403,
                    "FRONT_DOOR_PROXY_TOKEN_INVALID",
                    "trusted proxy 共享鉴权 header 无效。",
                    hint="请确认代理注入的共享 token 与服务端环境变量一致。",
                )
            return

        raise _http_error(
            503,
            "FRONT_DOOR_MODE_UNSUPPORTED",
            f"不支持的 front-door 模式：{config.mode}",
        )

    def _load_front_door_config(self) -> FrontDoorConfig:
        base_data: dict[str, object] = {}
        try:
            loaded = load_config(self._project_root)
            if loaded:
                base_data = loaded.front_door.model_dump(mode="python")
        except ValueError as exc:
            log.warning(
                "front_door_config_source_invalid_fallback",
                error_type=type(exc).__name__,
                project_root=str(self._project_root),
            )
        env_overrides = self._read_env_overrides()
        try:
            return FrontDoorConfig.model_validate({**base_data, **env_overrides})
        except (ValidationError, ValueError) as exc:
            log.warning(
                "front_door_config_invalid",
                error_type=type(exc).__name__,
                project_root=str(self._project_root),
            )
            raise _http_error(
                503,
                "FRONT_DOOR_CONFIG_INVALID",
                "front-door 配置无效，已拒绝开放 owner-facing API。",
                hint="请检查 octoagent.yaml 的 front_door 配置或对应环境变量。",
            ) from exc

    def _read_env_overrides(self) -> dict[str, object]:
        overrides: dict[str, object] = {}
        if mode := os.environ.get("OCTOAGENT_FRONTDOOR_MODE"):
            overrides["mode"] = mode.strip()
        if bearer_token_env := os.environ.get("OCTOAGENT_FRONTDOOR_TOKEN_ENV"):
            overrides["bearer_token_env"] = bearer_token_env.strip()
        if trusted_proxy_header := os.environ.get("OCTOAGENT_TRUSTED_PROXY_HEADER"):
            overrides["trusted_proxy_header"] = trusted_proxy_header.strip()
        if trusted_proxy_token_env := os.environ.get("OCTOAGENT_TRUSTED_PROXY_TOKEN_ENV"):
            overrides["trusted_proxy_token_env"] = trusted_proxy_token_env.strip()
        if trusted_proxy_cidrs := os.environ.get("OCTOAGENT_TRUSTED_PROXY_CIDRS"):
            overrides["trusted_proxy_cidrs"] = trusted_proxy_cidrs
        return overrides

    def _read_secret(self, env_name: str, *, code: str, hint: str) -> str:
        value = os.environ.get(env_name, "").strip()
        if value:
            return value
        raise _http_error(
            503,
            code,
            f"front-door 凭证环境变量未设置：{env_name}",
            hint=hint,
        )

    @staticmethod
    def _extract_bearer_token(request: Request, *, allow_query_token: bool) -> str | None:
        authorization = request.headers.get("authorization", "").strip()
        if authorization:
            scheme, _, credentials = authorization.partition(" ")
            if scheme.lower() == "bearer" and credentials.strip():
                return credentials.strip()
        if allow_query_token:
            query_token = request.query_params.get(_SSE_QUERY_TOKEN_PARAM, "").strip()
            if query_token:
                return query_token
        return None

    @staticmethod
    def _resolve_client_host(request: Request) -> str:
        client = request.client
        if client is None or not client.host:
            return ""
        return client.host.strip()

    @staticmethod
    def _is_loopback_host(host: str) -> bool:
        if host in _LOOPBACK_HOSTS:
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False

    @classmethod
    def _is_trusted_proxy_client(cls, host: str, cidrs: list[str]) -> bool:
        if not host:
            return False
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            return False
        for cidr in cidrs:
            if ip in ipaddress.ip_network(cidr, strict=False):
                return True
        return False
