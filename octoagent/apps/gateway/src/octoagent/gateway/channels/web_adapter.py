"""F105: Web 渠道 adapter（SSE 出站面 wrap + inbound 消息构造工厂）。

- ``notification_channel()`` 返回与 baseline 同参的 SSENotificationChannel。
- ``notify_task_result`` no-op：Web 的任务结果经 SSE 流（/api/stream/task/...）
  送达；baseline 中 telegram 的 notify_task_result 对 web task 也是 guard
  return——no-op 即等价。
- ``build_web_inbound_message`` 是 **module-level 工厂**（OPUS-M2 定案）：
  chat.py 直接 import 调用，不经 app.state/registry 查找，无 fallback 双轨。
  只收敛 channel="web" / sender 默认值等构造字面量；scope_id 构造留在
  call site（OPUS-L1 边界，v0.2 评估收口）。
"""

from __future__ import annotations

from typing import Any

from octoagent.core.models.message import NormalizedMessage
from octoagent.gateway.services.notification import SSENotificationChannel

from .adapter import ChannelCapabilityMeta

_WEB_META = ChannelCapabilityMeta(
    platform_id="web",
    label="Web",
    aliases=("web_sse",),
    markdown_capable=True,
    supports_interactive_approval=False,  # SSE 渠道不支持交互式审批推送
    supports_inbound=True,
    notification_channel_name="web_sse",
)

WEB_CHANNEL = "web"
_WEB_DEFAULT_SENDER_ID = "owner"
_WEB_DEFAULT_SENDER_NAME = "Owner"


def build_web_inbound_message(
    *,
    thread_id: str,
    scope_id: str,
    text: str,
    idempotency_key: str,
    control_metadata: dict[str, Any] | None = None,
    sender_id: str = _WEB_DEFAULT_SENDER_ID,
    sender_name: str = _WEB_DEFAULT_SENDER_NAME,
) -> NormalizedMessage:
    """构造 web 渠道 NormalizedMessage（spec FR-D2）。

    产出字段与 baseline chat.py 内联构造逐一相等
    （channel="web" / sender_id="owner" / sender_name="Owner"）。
    """
    return NormalizedMessage(
        channel=WEB_CHANNEL,
        thread_id=thread_id,
        scope_id=scope_id,
        sender_id=sender_id,
        sender_name=sender_name,
        text=text,
        control_metadata=control_metadata or {},
        idempotency_key=idempotency_key,
    )


class WebChannelAdapter:
    """Web 渠道 adapter（spec FR-D1）。"""

    def __init__(self, sse_hub: Any) -> None:
        self._sse_hub = sse_hub

    @property
    def meta(self) -> ChannelCapabilityMeta:
        return _WEB_META

    def notification_channel(self) -> SSENotificationChannel:
        return SSENotificationChannel(self._sse_hub)

    async def notify_task_result(self, task_id: str) -> None:
        """no-op：Web 任务结果走 SSE 流（等价论证见 module docstring）。"""
        return None

    async def startup(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None
