"""Slack Web API client（F105 v0.2 FR-B2）。

构造/token 解析模式参照 telegram_client.py（project_root + 惰性 env 解析 +
injectable environ/transport）；与 telegram client 的差异：出站方法自行
捕获配置/HTTP 异常降级返回 None（spec FR-B2——通知/回复链路不抛，
Constitution #6），不向调用方暴露异常层次。
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


class SlackApiClient:
    """项目级 Slack Web API client（httpx 直连，无 SDK）。"""

    def __init__(
        self,
        project_root: Path,
        *,
        environ: Mapping[str, str] | None = None,
        base_url: str = "https://slack.com/api",
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
            log.warning("slack_config_load_failed", exc_info=True)
            return None
        if config is None:
            return None
        token_env = config.channels.slack.bot_token_env
        token = self._environ.get(token_env, "")
        return token or None

    async def post_message(
        self,
        channel: str,
        text: str,
        *,
        thread_ts: str | None = None,
    ) -> dict[str, Any] | None:
        """chat.postMessage（FR-B2）。

        - ``mrkdwn: false`` 显式关闭（OPUS-L3：Slack 默认 mrkdwn=true 会把
          任务结果里的 ``*``/``_``/反引号误渲染；与 meta.markdown_capable=False
          的纯文本声明一致）。
        - token 缺失 / HTTP 异常 / API ok=false → warning + None 降级。
        """
        token = self.load_bot_token()
        if token is None:
            log.warning("slack_post_message_skipped", reason="bot_token_unavailable")
            return None
        payload: dict[str, Any] = {
            "channel": channel,
            "text": text,
            "mrkdwn": False,
        }
        if thread_ts:
            payload["thread_ts"] = thread_ts
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    "/chat.postMessage",
                    json=payload,
                    headers={"Authorization": f"Bearer {token}"},
                )
            data = response.json()
        except Exception:
            log.warning("slack_post_message_failed", channel=channel, exc_info=True)
            return None
        if not isinstance(data, dict) or not data.get("ok", False):
            log.warning(
                "slack_post_message_api_error",
                channel=channel,
                error=str(data.get("error", "")) if isinstance(data, dict) else "non_dict",
            )
            return None
        return data
