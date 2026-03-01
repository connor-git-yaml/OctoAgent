"""Codex OAuth Device Flow -- 对齐 contracts/auth-adapter-api.md SS7, FR-005

RFC 8628 Device Authorization Grant 实现。
基于 httpx 的轻量实现（约 100 行），不引入第三方 OAuth 库。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import structlog
from pydantic import BaseModel, Field, SecretStr

from ..exceptions import OAuthFlowError
from .credentials import OAuthCredential

log = structlog.get_logger()


class DeviceFlowConfig(BaseModel):
    """Device Flow 配置"""

    authorization_endpoint: str = Field(
        default="https://auth0.openai.com/oauth/device/code",
        description="设备授权端点",
    )
    token_endpoint: str = Field(
        default="https://auth0.openai.com/oauth/token",
        description="Token 端点",
    )
    client_id: str = Field(description="OAuth Client ID")
    scope: str = Field(
        default="openid profile email offline_access",
        description="请求 scope",
    )
    poll_interval_s: int = Field(default=5, ge=1, description="轮询间隔（秒）")
    timeout_s: int = Field(default=300, ge=30, description="授权超时（秒）")


class DeviceAuthResponse(BaseModel):
    """设备授权响应"""

    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str | None = None
    expires_in: int
    interval: int


async def start_device_flow(config: DeviceFlowConfig) -> DeviceAuthResponse:
    """发起 Device Flow 授权请求

    Returns:
        DeviceAuthResponse，包含 user_code 和 verification_uri

    Raises:
        OAuthFlowError: 授权端点不可达
    """
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                config.authorization_endpoint,
                data={
                    "client_id": config.client_id,
                    "scope": config.scope,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return DeviceAuthResponse.model_validate(data)
    except httpx.HTTPStatusError as exc:
        raise OAuthFlowError(
            f"OAuth 授权端点返回错误: {exc.response.status_code}",
            provider="openai-codex",
        ) from exc
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        raise OAuthFlowError(
            f"OAuth 授权端点不可达: {exc}",
            provider="openai-codex",
        ) from exc


async def poll_for_token(
    config: DeviceFlowConfig,
    device_code: str,
    interval: int = 5,
    timeout: int = 300,
) -> OAuthCredential:
    """轮询 Token 端点等待用户授权

    Returns:
        OAuthCredential，包含 access_token 和 expires_at

    Raises:
        OAuthFlowError: 授权超时或被拒绝
    """
    elapsed = 0
    poll_interval = max(interval, config.poll_interval_s)
    effective_timeout = min(timeout, config.timeout_s)

    async with httpx.AsyncClient(timeout=30) as client:
        while elapsed < effective_timeout:
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

            try:
                resp = await client.post(
                    config.token_endpoint,
                    data={
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                        "device_code": device_code,
                        "client_id": config.client_id,
                    },
                )
                data = resp.json()

                if resp.status_code == 200:
                    # 授权成功
                    expires_in = data.get("expires_in", 3600)
                    return OAuthCredential(
                        provider="openai-codex",
                        access_token=SecretStr(data["access_token"]),
                        refresh_token=SecretStr(data.get("refresh_token", "")),
                        expires_at=datetime.now(tz=UTC)
                        + timedelta(seconds=expires_in),
                    )

                error = data.get("error", "")
                if error == "authorization_pending":
                    # 用户尚未授权，继续轮询
                    log.debug("oauth_poll_pending", elapsed=elapsed)
                    continue
                elif error == "slow_down":
                    # 增加轮询间隔
                    poll_interval += 5
                    continue
                elif error in ("access_denied", "expired_token"):
                    raise OAuthFlowError(
                        f"OAuth 授权被拒绝或已过期: {error}",
                        provider="openai-codex",
                    )
                else:
                    raise OAuthFlowError(
                        f"OAuth Token 端点返回未知错误: {error}",
                        provider="openai-codex",
                    )

            except httpx.HTTPError as exc:
                raise OAuthFlowError(
                    f"OAuth Token 端点请求失败: {exc}",
                    provider="openai-codex",
                ) from exc

    raise OAuthFlowError(
        f"OAuth 授权超时（{effective_timeout}s），请重试或切换到 API Key 模式",
        provider="openai-codex",
    )
