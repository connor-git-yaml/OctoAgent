"""F105: Multi-Platform Gateway —— 渠道 adapter 抽象层。

- ``ChannelAdapter`` / ``ChannelCapabilityMeta``：渠道 adapter Protocol + 能力元数据（OC-1）
- ``PlatformRegistry``：中央注册表（注册/解析/完成回复扇出/生命周期）
"""

from .adapter import ChannelAdapter, ChannelCapabilityMeta
from .registry import PlatformRegistry

__all__ = [
    "ChannelAdapter",
    "ChannelCapabilityMeta",
    "PlatformRegistry",
]
