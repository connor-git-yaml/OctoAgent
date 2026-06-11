"""F105 v0.2: Discord 渠道 adapter（FR-C5，与 SlackChannelAdapter 同构）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from octoagent.gateway.routes import discord as discord_routes
from octoagent.gateway.services.notification import DiscordNotificationChannel

from .adapter import ChannelCapabilityMeta

_DISCORD_META = ChannelCapabilityMeta(
    platform_id="discord",
    label="Discord",
    aliases=(),
    markdown_capable=False,  # v0.2 纯文本（Discord content 默认 markdown 但不主动构造）
    supports_interactive_approval=False,  # message components 推 v0.3
    supports_inbound=True,
    notification_channel_name="discord",
)


class DiscordChannelAdapter:
    """Discord 渠道 adapter（spec FR-C5）。"""

    def __init__(self, discord_service: Any) -> None:
        self._service = discord_service

    @property
    def meta(self) -> ChannelCapabilityMeta:
        return _DISCORD_META

    def inbound_router(self) -> APIRouter | None:
        """返回 Discord interactions webhook router（ingress 契约自描述）。"""
        return discord_routes.router

    def notification_channel(self) -> DiscordNotificationChannel | None:
        """enabled 且 bot token 可解析才返回实例（spec D10——默认环境 None，
        保 v0.1 harness wiring 序断言）。"""
        if not self._service.notification_send_available():
            return None
        api_client = getattr(self._service, "_api_client", None)
        binding_store = getattr(
            getattr(self._service, "_stores", None),
            "conversation_binding_store",
            None,
        )
        if api_client is None or binding_store is None:
            return None

        async def _send_fn(conversation_id: str, text: str) -> Any:
            return await api_client.create_message(conversation_id, text)

        return DiscordNotificationChannel(
            send_fn=_send_fn,
            binding_store=binding_store,
        )

    async def notify_task_result(self, task_id: str) -> None:
        """任务完成回复——委托 service（内部 requester.channel guard）。"""
        await self._service.notify_task_result(task_id)

    async def startup(self) -> None:
        await self._service.startup()

    async def shutdown(self) -> None:
        await self._service.shutdown()
