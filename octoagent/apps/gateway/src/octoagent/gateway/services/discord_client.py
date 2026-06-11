"""Discord REST API client（F105 v0.2 FR-C2）。

形态镜像 slack_client.py：惰性 token 解析 + 出站方法自捕获异常降级 None
（Constitution #6——完成回复/通知链路不抛）。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx
import structlog

from octoagent.gateway.services.config.config_schema import ConfigParseError
from octoagent.gateway.services.config.config_wizard import load_config

log = structlog.get_logger()


class DiscordApiClient:
    """项目级 Discord REST client（httpx 直连，无 SDK）。"""

    def __init__(
        self,
        project_root: Path,
        *,
        environ: Mapping[str, str] | None = None,
        base_url: str = "https://discord.com/api/v10",
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._project_root = project_root
        self._environ = environ if environ is not None else os.environ
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._transport = transport

    def load_bot_token(self) -> str | None:
        """解析 bot token；缺失/配置异常返回 None（出站面降级语义）。"""
        try:
            config = load_config(self._project_root)
        except ConfigParseError:
            log.warning("discord_config_load_failed", exc_info=True)
            return None
        if config is None:
            return None
        token_env = config.channels.discord.bot_token_env
        token = self._environ.get(token_env, "")
        return token or None

    async def create_message(
        self,
        channel_id: str,
        content: str,
    ) -> dict[str, Any] | None:
        """POST /channels/{id}/messages（FR-C2 出站；spec D6 完成回复路径——
        不走 interaction followup，token 15min 有效期 < 长任务时长）。"""
        token = self.load_bot_token()
        if token is None:
            log.warning(
                "discord_create_message_skipped", reason="bot_token_unavailable"
            )
            return None
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    f"/channels/{channel_id}/messages",
                    json={"content": content},
                    headers={"Authorization": f"Bot {token}"},
                )
            if response.status_code >= 400:
                log.warning(
                    "discord_create_message_api_error",
                    channel_id=channel_id,
                    status_code=response.status_code,
                )
                return None
            data = response.json()
        except Exception:
            log.warning(
                "discord_create_message_failed", channel_id=channel_id, exc_info=True
            )
            return None
        return data if isinstance(data, dict) else None
