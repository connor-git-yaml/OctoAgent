"""F105 v0.2: Slack 渠道 adapter（FR-B5）。

实现 ChannelAdapter Protocol + register 即自动获得：通知扇出 / 任务完成
回复派发 / 生命周期 / route 挂载（v0.2 ingress 契约）——验证 v0.1 抽象的
扩张承诺（spec SC-4）。inbound 解析在 SlackGatewayService（D3 边界）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from octoagent.gateway.routes import slack as slack_routes
from octoagent.gateway.services.notification import SlackNotificationChannel

from .adapter import ChannelCapabilityMeta

_SLACK_META = ChannelCapabilityMeta(
    platform_id="slack",
    label="Slack",
    aliases=(),
    markdown_capable=False,  # v0.2 纯文本（post_message 显式 mrkdwn:false）
    supports_interactive_approval=False,  # interactive components 推 v0.3
    supports_inbound=True,
    notification_channel_name="slack",
)


class SlackChannelAdapter:
    """Slack 渠道 adapter（spec FR-B5）。"""

    def __init__(self, slack_service: Any) -> None:
        self._service = slack_service

    @property
    def meta(self) -> ChannelCapabilityMeta:
        return _SLACK_META

    def inbound_router(self) -> APIRouter | None:
        """返回 Slack events webhook router（ingress 契约自描述）。"""
        return slack_routes.router

    def notification_channel(self) -> SlackNotificationChannel | None:
        """enabled 且 bot token 可解析才返回实例（spec D10）。

        默认（未配置）环境返回 None——不注册无用渠道，且保 v0.1 harness
        wiring 序断言 ``["web_sse", "telegram"]`` 成立。
        """
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
            return await api_client.post_message(conversation_id, text)

        return SlackNotificationChannel(
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
