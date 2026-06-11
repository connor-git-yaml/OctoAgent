"""F105: Telegram 渠道 adapter（现有 TelegramGatewayService 的 wrap，不重写）。

行为零变更要点：
- ``notification_channel()`` 逐行迁移自 octo_harness 原 L938-968 的内联构造
  （bot_client/state_store 提取 + first_approved_user chat_id 冻结 + send_fn
  闭包适配），构造参数与 baseline 完全相同；bot_client 为 None 时返回 None
  （baseline 不注册，registry 同样跳过）。
- ``notify_task_result`` 委托 service 同名方法——其内部
  ``task.requester.channel != "telegram"`` guard 原样保留。
- inbound（webhook route / polling）不经 adapter：routes/telegram.py 继续
  直读 ``app.state.telegram_service``（spec FR-C2 撤销，D3 inbound 留
  per-platform）；polling loop 由 ``startup()`` 委托 service 管理。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from octoagent.gateway.routes import telegram as telegram_routes
from octoagent.gateway.services.notification import TelegramNotificationChannel

from .adapter import ChannelCapabilityMeta

_TELEGRAM_META = ChannelCapabilityMeta(
    platform_id="telegram",
    label="Telegram",
    aliases=(),
    markdown_capable=False,  # 现状出站纯文本（telegram.py send_message 无 parse_mode）
    supports_interactive_approval=True,  # inline keyboard 审批/dismiss 按钮
    supports_inbound=True,
    notification_channel_name="telegram",
)


class TelegramChannelAdapter:
    """Telegram 渠道 adapter（spec FR-C1）。"""

    def __init__(self, telegram_service: Any) -> None:
        self._service = telegram_service

    @property
    def meta(self) -> ChannelCapabilityMeta:
        return _TELEGRAM_META

    def inbound_router(self) -> APIRouter | None:
        """返回 telegram webhook router（v0.2 ingress 契约，spec D2）。

        routes/telegram.py 模块保留（路由函数/handler 原样，test_telegram_route
        契约不动）——adapter 只接管"挂载自描述"：返回模块单例 router，
        由 harness 统一挂载（原 main.py register_routes 直挂行撤销）。
        route 函数继续直读 ``request.app.state.telegram_service``
        （inbound 解析 per-platform，v0.1 D3 边界不变）。
        """
        return telegram_routes.router

    def notification_channel(self) -> TelegramNotificationChannel | None:
        """构造 Telegram 通知渠道（逐行迁移自 octo_harness L938-968）。

        - bot_client 为 None → 返回 None（baseline 不注册该渠道）
        - chat_id 取 first_approved_user，**bootstrap 时一次性冻结**
          （spec 已知 limitation L1：启动时无 approved user 则通知静默直到
          重启——baseline 既有行为，v0.1 不修）
        """
        bot_client = getattr(self._service, "_bot_client", None)
        state_store = getattr(self._service, "_state_store", None)

        chat_id: str | None = None
        if state_store is not None:
            try:
                approved = state_store.first_approved_user()
                if approved is not None:
                    chat_id = str(getattr(approved, "chat_id", "") or "")
                    if not chat_id:
                        chat_id = None
            except Exception:
                chat_id = None

        if bot_client is None:
            return None

        # 适配闭包：TelegramNotificationChannel.notify 以位置参数调用
        # send_message_fn(chat_id, text, reply_markup)，而 bot_client.send_message
        # 的 reply_markup 是关键字参数（baseline 同款闭包）
        async def _send_fn(chat_id: str, text: str, reply_markup=None):
            await bot_client.send_message(
                chat_id,
                text,
                reply_markup=reply_markup or None,
            )

        return TelegramNotificationChannel(
            send_message_fn=_send_fn,
            chat_id=chat_id,
        )

    async def notify_task_result(self, task_id: str) -> None:
        """任务完成回复——委托 service（内部 requester.channel guard 原样）。"""
        await self._service.notify_task_result(task_id)

    async def startup(self) -> None:
        await self._service.startup()

    async def shutdown(self) -> None:
        await self._service.shutdown()
