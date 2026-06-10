"""F105 Phase C: Telegram/Web adapter 行为等价测试。

覆盖 spec US-1 AC-3（telegram inbound NormalizedMessage 字段 == baseline 字面）
/ US-1 AC-4（completion 扇出 web task 不发 telegram）/ FR-C1（adapter 委托 +
notification_channel 构造等价）/ FR-D1/D2（web adapter meta + 工厂字面等价）。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from octoagent.core.models.message import NormalizedMessage
from octoagent.core.store import create_store_group
from octoagent.gateway.channels import (
    PlatformRegistry,
    TelegramChannelAdapter,
    WebChannelAdapter,
    build_web_inbound_message,
)
from octoagent.gateway.services.config.config_schema import (
    ChannelsConfig,
    OctoAgentConfig,
    TelegramChannelConfig,
)
from octoagent.gateway.services.config.config_wizard import save_config
from octoagent.gateway.services.notification import (
    TelegramNotificationChannel,
)
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.gateway.services.task_service import TaskService
from octoagent.gateway.services.telegram import TelegramGatewayService
from octoagent.provider.dx.telegram_pairing import TelegramStateStore

from .test_telegram_service import FakeTaskRunner, FakeTelegramBotClient


def _write_config(project_root: Path, **telegram_overrides: object) -> None:
    telegram_config: dict[str, object] = {
        "enabled": True,
        "mode": "webhook",
        "webhook_url": "https://example.com/api/telegram/webhook",
    }
    telegram_config.update(telegram_overrides)
    save_config(
        OctoAgentConfig(
            updated_at="2026-06-10",
            channels=ChannelsConfig(telegram=TelegramChannelConfig(**telegram_config)),
        ),
        project_root,
    )


def _build_service(
    tmp_path: Path,
    store_group,
    *,
    bot_client=None,
) -> tuple[TelegramGatewayService, TelegramStateStore]:
    state_store = TelegramStateStore(tmp_path)
    service = TelegramGatewayService(
        project_root=tmp_path,
        store_group=store_group,
        sse_hub=SSEHub(),
        task_runner=FakeTaskRunner(),
        state_store=state_store,
        bot_client=bot_client if bot_client is not None else FakeTelegramBotClient(),
    )
    return service, state_store


# ============================================================
# US-1 AC-3: telegram inbound NormalizedMessage 字段 == baseline
# ============================================================


@pytest.mark.asyncio
async def test_telegram_inbound_message_fields_equal_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ingest 产出的 NormalizedMessage 与 baseline 字段逐一相等（行为零变更 pin）。"""
    _write_config(tmp_path, dm_policy="open")
    store_group = await create_store_group(
        str(tmp_path / "gateway.db"), str(tmp_path / "artifacts")
    )
    service, _ = _build_service(tmp_path, store_group)

    captured: list[NormalizedMessage] = []

    class _CapturingTaskService(TaskService):
        async def create_task(self, message: NormalizedMessage):  # type: ignore[override]
            captured.append(message)
            return await super().create_task(message)

    monkeypatch.setattr(
        "octoagent.gateway.services.telegram.TaskService", _CapturingTaskService
    )

    result = await service.handle_webhook_update(
        {
            "update_id": 101,
            "message": {
                "message_id": 7,
                "text": "hello main agent",
                "chat": {"id": 1001, "type": "private"},
                "from": {"id": 2002, "username": "connor", "first_name": "Connor"},
            },
        }
    )

    assert result.status == "accepted"
    assert len(captured) == 1
    msg = captured[0]
    # baseline 字面（telegram.py _ingest_update / _resolve_scope_thread）
    assert msg.channel == "telegram"
    assert msg.scope_id == "chat:telegram:1001"
    assert msg.thread_id == "tg:2002"
    assert msg.sender_id == "2002"
    assert msg.sender_name == "connor"
    assert msg.text == "hello main agent"
    assert msg.idempotency_key == "telegram:101:1001:7"
    assert msg.metadata == {
        "telegram_update_id": "101",
        "telegram_chat_id": "1001",
        "telegram_message_id": "7",
    }

    await store_group.close()


# ============================================================
# FR-D1/D2: web adapter meta + 工厂字面等价
# ============================================================


def test_web_factory_fields_equal_baseline() -> None:
    """工厂产出与 baseline chat.py 内联构造逐字段相等。"""
    control_metadata = {"project_id": "proj-1", "thread_id": "t-1"}
    via_factory = build_web_inbound_message(
        thread_id="t-1",
        scope_id="project:proj-1:chat:web:t-1",
        text="hi",
        control_metadata=control_metadata,
        idempotency_key="chat-task-abc",
    )
    baseline = NormalizedMessage(
        channel="web",
        thread_id="t-1",
        scope_id="project:proj-1:chat:web:t-1",
        sender_id="owner",
        sender_name="Owner",
        text="hi",
        control_metadata=control_metadata,
        idempotency_key="chat-task-abc",
    )
    assert via_factory.model_dump(exclude={"timestamp"}) == baseline.model_dump(
        exclude={"timestamp"}
    )


def test_adapter_meta_values() -> None:
    web = WebChannelAdapter(SSEHub())
    assert web.meta.platform_id == "web"
    assert web.meta.aliases == ("web_sse",)
    assert web.meta.notification_channel_name == "web_sse"
    assert web.meta.supports_interactive_approval is False

    tg = TelegramChannelAdapter(SimpleNamespace())
    assert tg.meta.platform_id == "telegram"
    assert tg.meta.notification_channel_name == "telegram"
    assert tg.meta.supports_interactive_approval is True


# ============================================================
# FR-C1: telegram adapter 委托 + notification_channel 构造等价
# ============================================================


@pytest.mark.asyncio
async def test_telegram_adapter_delegates_lifecycle_and_notify() -> None:
    calls: list[str] = []

    class _FakeService:
        async def notify_task_result(self, task_id: str) -> None:
            calls.append(f"notify:{task_id}")

        async def startup(self) -> None:
            calls.append("startup")

        async def shutdown(self) -> None:
            calls.append("shutdown")

    adapter = TelegramChannelAdapter(_FakeService())
    await adapter.notify_task_result("task-1")
    await adapter.startup()
    await adapter.shutdown()
    assert calls == ["notify:task-1", "startup", "shutdown"]


@pytest.mark.asyncio
async def test_telegram_notification_channel_construction(tmp_path: Path) -> None:
    """bot_client None → None；有 bot + approved user → chat_id 冻结 + 闭包适配。"""
    _write_config(tmp_path)
    store_group = await create_store_group(
        str(tmp_path / "gateway.db"), str(tmp_path / "artifacts")
    )
    bot_client = FakeTelegramBotClient()
    service, state_store = _build_service(tmp_path, store_group, bot_client=bot_client)

    # 无 approved user：channel 仍构造（baseline 行为），chat_id=None（notify 降级跳过）
    channel = TelegramChannelAdapter(service).notification_channel()
    assert isinstance(channel, TelegramNotificationChannel)
    assert channel._chat_id is None

    # 有 approved user：chat_id 冻结为 first_approved_user
    state_store.upsert_approved_user(user_id="2002", chat_id="1001")
    channel2 = TelegramChannelAdapter(service).notification_channel()
    assert channel2 is not None
    assert channel2._chat_id == "1001"

    # 闭包适配：notify 走 bot_client.send_message（reply_markup 关键字传递）
    ok = await channel2.notify("task-x", "TASK_COMPLETED", {"task_title": "t"})
    assert ok is True
    assert len(bot_client.sent_messages) == 1
    assert bot_client.sent_messages[0].chat_id == "1001"

    # bot_client None → notification_channel 返回 None（baseline 不注册语义）
    service_no_bot = TelegramGatewayService(
        project_root=tmp_path,
        store_group=store_group,
        sse_hub=SSEHub(),
        state_store=state_store,
        bot_client=None,
    )
    assert TelegramChannelAdapter(service_no_bot).notification_channel() is None

    await store_group.close()


# ============================================================
# US-1 AC-4 + US-2 AC-3: completion 扇出与通知注册序
# ============================================================


@pytest.mark.asyncio
async def test_completion_fanout_web_task_no_telegram_send(tmp_path: Path) -> None:
    """web task 完成扇出后 telegram bot 零发送（baseline guard 行为等价）。"""
    _write_config(tmp_path, dm_policy="open")
    store_group = await create_store_group(
        str(tmp_path / "gateway.db"), str(tmp_path / "artifacts")
    )
    bot_client = FakeTelegramBotClient()
    service, _ = _build_service(tmp_path, store_group, bot_client=bot_client)

    # 建一个 web 渠道 task
    task_service = TaskService(store_group, SSEHub())
    task_id, created = await task_service.create_task(
        build_web_inbound_message(
            thread_id="t-1",
            scope_id="chat:web:t-1",
            text="web message",
            idempotency_key="chat-task-web-1",
        )
    )
    assert created

    registry = PlatformRegistry()
    registry.register(WebChannelAdapter(SSEHub()))
    registry.register(TelegramChannelAdapter(service))
    await registry.notify_task_completion(task_id)

    assert bot_client.sent_messages == []
    await store_group.close()


@pytest.mark.asyncio
async def test_completion_fanout_telegram_task_replies(tmp_path: Path) -> None:
    """telegram task 完成扇出后 bot 收到回复（与 baseline notify_task_result 同链路）。"""
    _write_config(tmp_path, dm_policy="open")
    store_group = await create_store_group(
        str(tmp_path / "gateway.db"), str(tmp_path / "artifacts")
    )
    bot_client = FakeTelegramBotClient()
    service, _ = _build_service(tmp_path, store_group, bot_client=bot_client)

    result = await service.handle_webhook_update(
        {
            "update_id": 11,
            "message": {
                "message_id": 3,
                "text": "do something",
                "chat": {"id": 1001, "type": "private"},
                "from": {"id": 2002, "username": "connor"},
            },
        }
    )
    assert result.status == "accepted" and result.task_id

    registry = PlatformRegistry()
    registry.register(WebChannelAdapter(SSEHub()))
    registry.register(TelegramChannelAdapter(service))
    await registry.notify_task_completion(result.task_id)

    assert len(bot_client.sent_messages) == 1
    assert bot_client.sent_messages[0].chat_id == "1001"
    await store_group.close()
