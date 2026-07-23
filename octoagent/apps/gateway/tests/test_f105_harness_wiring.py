"""F105 Phase C: harness 装配等价测试（US-2 AC-3）。

按 octo_harness 的装配方式（注册序 web → telegram + 遍历 registry 注册通知渠道）
断言 NotificationService channel_name 序列 == baseline ["web_sse", "telegram"]。

测试前提（spec §9 / OPUS-L2）：telegram bot_client 存在——baseline 在
bot_client None 时同样不注册 telegram 渠道（条件性等价）。
全 app bootstrap 级验证由 e2e_smoke 覆盖（pre-commit gate）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from octoagent.core.store import create_store_group
from octoagent.gateway.channels import (
    PlatformRegistry,
    TelegramChannelAdapter,
    WebChannelAdapter,
)
from octoagent.gateway.services.notification import NotificationService
from octoagent.gateway.services.operations.telegram_pairing import TelegramStateStore
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.gateway.services.telegram import TelegramGatewayService

from .test_telegram_service import FakeTelegramBotClient


@pytest.mark.asyncio
async def test_notification_channel_registration_order_equals_baseline(
    tmp_path: Path,
) -> None:
    """装配序：registry(web→telegram) 产出 ["web_sse", "telegram"]（baseline 同序）。"""
    store_group = await create_store_group(
        str(tmp_path / "gateway.db"), str(tmp_path / "artifacts")
    )
    telegram_service = TelegramGatewayService(
        project_root=tmp_path,
        store_group=store_group,
        sse_hub=SSEHub(),
        state_store=TelegramStateStore(tmp_path),
        bot_client=FakeTelegramBotClient(),  # bot_client-present 前提
    )

    # 与 octo_harness._bootstrap_channels 同序注册
    registry = PlatformRegistry()
    registry.register(WebChannelAdapter(SSEHub()))
    registry.register(TelegramChannelAdapter(telegram_service))

    # 与 octo_harness._bootstrap_executors 同款注册循环
    notification_service = NotificationService()
    for adapter in registry.list_adapters():
        channel = adapter.notification_channel()
        if channel is not None:
            notification_service.register_channel(channel)

    assert [c.channel_name for c in notification_service._channels] == [
        "web_sse",
        "telegram",
    ]
    await store_group.close()


@pytest.mark.asyncio
async def test_registry_resolves_platforms_after_wiring(tmp_path: Path) -> None:
    """装配后 alias 解析可用（platform_id / web_sse / telegram）。"""
    store_group = await create_store_group(
        str(tmp_path / "gateway.db"), str(tmp_path / "artifacts")
    )
    telegram_service = TelegramGatewayService(
        project_root=tmp_path,
        store_group=store_group,
        sse_hub=SSEHub(),
        state_store=TelegramStateStore(tmp_path),
        bot_client=FakeTelegramBotClient(),
    )
    registry = PlatformRegistry()
    web_adapter = WebChannelAdapter(SSEHub())
    tg_adapter = TelegramChannelAdapter(telegram_service)
    registry.register(web_adapter)
    registry.register(tg_adapter)

    assert registry.resolve("web") is web_adapter
    assert registry.resolve("web_sse") is web_adapter
    assert registry.resolve("telegram") is tg_adapter
    assert [a.meta.platform_id for a in registry.list_adapters()] == ["web", "telegram"]
    await store_group.close()
