"""F105: Multi-Platform Gateway —— 渠道 adapter 抽象层。

- ``ChannelAdapter`` / ``ChannelCapabilityMeta``：渠道 adapter Protocol + 能力元数据（OC-1）
- ``PlatformRegistry``：中央注册表（注册/解析/完成回复扇出/生命周期）
- ``TelegramChannelAdapter`` / ``WebChannelAdapter``：现有双渠道的 adapter 化（v0.1）
- ``build_web_inbound_message``：web inbound 消息构造工厂（chat.py module-level import）
"""

from .adapter import ChannelAdapter, ChannelCapabilityMeta
from .registry import PlatformRegistry
from .telegram_adapter import TelegramChannelAdapter
from .web_adapter import WebChannelAdapter, build_web_inbound_message

__all__ = [
    "ChannelAdapter",
    "ChannelCapabilityMeta",
    "PlatformRegistry",
    "TelegramChannelAdapter",
    "WebChannelAdapter",
    "build_web_inbound_message",
]
