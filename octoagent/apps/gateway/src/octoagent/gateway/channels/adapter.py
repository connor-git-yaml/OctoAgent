"""F105: ChannelAdapter Protocol 与 capability meta（OC-1）。

把"渠道"从形态各异的硬编码（Telegram 巨型 service / Web 散落 routes）收敛为
显式 adapter 抽象。v0.1 的 Protocol 只收公共面（capability meta / outbound
通知面 / 任务完成回复 / 生命周期）：

- inbound 解析**不进** Protocol 统一签名（spec D3）——telegram 的 ingest
  含 pairing/callback/control 分流返回 IngestResult，web 是 HTTP route 驱动，
  强行统一 ``parse(raw) -> msg`` 会压扁语义成假抽象。adapter 具体类自持
  平台特定 inbound 方法，route/poller 直接调具体类型。
- outbound 通知面**组合**现有 NotificationChannelProtocol（spec D1）——
  channel_name（"telegram"/"web_sse"）已是用户可见契约（USER.md
  summary_channels + NOTIFICATION_DISPATCHED 事件字段），不可改名。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class ChannelCapabilityMeta(BaseModel):
    """渠道能力元数据（OC-1 capability meta）。

    platform_id 是 registry 主键；aliases ∪ notification_channel_name 进入
    alias 解析空间（registry 注册时校验冲突）。
    """

    platform_id: str = Field(min_length=1, description="平台标识（registry 主键）")
    label: str = Field(default="", description="用户可见显示名")
    aliases: tuple[str, ...] = Field(default=(), description="解析别名")
    markdown_capable: bool = Field(
        default=False,
        description="outbound 是否支持 markdown 渲染",
    )
    supports_interactive_approval: bool = Field(
        default=False,
        description="是否支持交互式审批推送（inline 按钮等）",
    )
    supports_inbound: bool = Field(
        default=True,
        description="是否有 inbound 消息面（纯通知渠道为 False）",
    )
    notification_channel_name: str = Field(
        default="",
        description=(
            "对应 NotificationChannelProtocol.channel_name 的值"
            "（用户可见契约，如 'telegram' / 'web_sse'；空=无通知通道）"
        ),
    )


@runtime_checkable
class ChannelAdapter(Protocol):
    """渠道 adapter 公共面（v0.1 最小集，spec FR-A1）。

    v0.2 新平台接入 = 实现本 Protocol + ``PlatformRegistry.register``，
    通知扇出 / 任务完成回复 / 生命周期自动获得，无需改 harness 装配。
    """

    @property
    def meta(self) -> ChannelCapabilityMeta:
        """渠道能力元数据。"""
        ...

    def notification_channel(self) -> Any | None:
        """返回该渠道的 NotificationChannelProtocol 实现。

        返回 None 表示该渠道当前无可用通知通道（如 telegram bot 未配置），
        registry 装配时跳过注册——与 baseline "bot_client 为 None 不注册"
        语义一致。
        """
        ...

    async def notify_task_result(self, task_id: str) -> None:
        """任务完成回复。

        adapter 自行判断该 task 是否属于本渠道（如 telegram 的
        ``task.requester.channel != "telegram"`` guard），不属于则 no-op。
        registry 按注册序扇出调用（spec D4）。
        """
        ...

    async def startup(self) -> None:
        """渠道生命周期启动（如 telegram polling loop）。"""
        ...

    async def shutdown(self) -> None:
        """渠道生命周期停止。"""
        ...
