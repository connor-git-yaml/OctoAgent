from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from octoagent.core.models.message import NormalizedMessage
from octoagent.core.store import create_store_group
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.gateway.services.task_service import TaskService
from octoagent.provider.dx.config_schema import (
    ChannelsConfig,
    OctoAgentConfig,
    TelegramChannelConfig,
)
from octoagent.provider.dx.config_wizard import save_config
from octoagent.provider.dx.onboarding_models import OnboardingStepStatus
from octoagent.provider.dx.telegram_client import TelegramBotClient
from octoagent.provider.dx.telegram_pairing import TelegramStateStore
from octoagent.provider.dx.telegram_verifier import TelegramOnboardingVerifier


def _write_config(
    project_root: Path,
    *,
    telegram: TelegramChannelConfig,
) -> None:
    save_config(
        OctoAgentConfig(
            updated_at="2026-03-07",
            channels=ChannelsConfig(telegram=telegram),
        ),
        project_root,
    )


def _transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getMe"):
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": {
                        "id": 1,
                        "is_bot": True,
                        "username": "octobot",
                        "first_name": "Octo",
                    },
                },
            )
        if request.url.path.endswith("/sendMessage"):
            payload = {
                "ok": True,
                "result": {
                    "message_id": 11,
                    "chat": {"id": 456, "type": "private"},
                    "text": "sent",
                    "from": {
                        "id": 1,
                        "is_bot": True,
                        "username": "octobot",
                        "first_name": "Octo",
                    },
                },
            }
            return httpx.Response(200, json=payload)
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": [
                        {
                            "update_id": 99,
                            "message": {
                                "message_id": 7,
                                "chat": {"id": 456, "type": "private"},
                                "text": "/start",
                                "from": {
                                    "id": 456,
                                    "is_bot": False,
                                    "username": "alice",
                                    "first_name": "Alice",
                                },
                            },
                        }
                    ],
                },
            )
        return httpx.Response(404, json={"ok": False, "description": "not found"})

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_telegram_bot_client_minimal_api(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        telegram=TelegramChannelConfig(
            enabled=True,
            mode="polling",
        ),
    )
    client = TelegramBotClient(
        tmp_path,
        environ={"TELEGRAM_BOT_TOKEN": "test-token"},
        transport=_transport(),
    )

    me = await client.get_me()
    sent = await client.send_message(456, "hello")
    updates = await client.get_updates(offset=10, timeout=1)

    assert me.username == "octobot"
    assert sent.message_id == 11
    assert updates[0].update_id == 99
    assert updates[0].message is not None
    assert updates[0].message.text == "/start"


def test_verifier_availability_blocks_disabled_channel(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        telegram=TelegramChannelConfig(enabled=False),
    )
    verifier = TelegramOnboardingVerifier()

    availability = verifier.availability(tmp_path)
    assert availability.available is False
    assert "enabled=false" in availability.actions[0].description


@pytest.mark.asyncio
async def test_verifier_run_readiness_uses_real_client_and_store(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        telegram=TelegramChannelConfig(
            enabled=True,
            mode="polling",
        ),
    )
    store = TelegramStateStore(tmp_path)
    store.upsert_pending_pairing(user_id=123, chat_id=123, username="alice")

    verifier = TelegramOnboardingVerifier(
        environ={"TELEGRAM_BOT_TOKEN": "test-token"},
        client_factory=lambda root: TelegramBotClient(
            root,
            environ={"TELEGRAM_BOT_TOKEN": "test-token"},
            transport=_transport(),
        ),
    )

    result = await verifier.run_readiness(tmp_path, session=None)
    assert result.status == OnboardingStepStatus.COMPLETED
    assert "approved_users=0" in result.summary
    assert "pending_pairings=1" in result.summary


@pytest.mark.asyncio
async def test_verifier_first_message_requires_approved_user(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        telegram=TelegramChannelConfig(
            enabled=True,
            mode="polling",
        ),
    )
    verifier = TelegramOnboardingVerifier(
        environ={"TELEGRAM_BOT_TOKEN": "test-token"},
        client_factory=lambda root: TelegramBotClient(
            root,
            environ={"TELEGRAM_BOT_TOKEN": "test-token"},
            transport=_transport(),
        ),
    )

    result = await verifier.verify_first_message(tmp_path, session=None)
    assert result.status == OnboardingStepStatus.ACTION_REQUIRED
    assert "已批准" in result.summary


@pytest.mark.asyncio
async def test_verifier_first_message_sends_to_first_approved_user(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        telegram=TelegramChannelConfig(
            enabled=True,
            mode="polling",
        ),
    )
    store = TelegramStateStore(tmp_path)
    store.upsert_approved_user(user_id=456, chat_id=456, username="alice")

    verifier = TelegramOnboardingVerifier(
        environ={"TELEGRAM_BOT_TOKEN": "test-token"},
        client_factory=lambda root: TelegramBotClient(
            root,
            environ={"TELEGRAM_BOT_TOKEN": "test-token"},
            transport=_transport(),
        ),
    )

    result = await verifier.verify_first_message(tmp_path, session=None)
    approved = store.get_approved_user("456")
    assert result.status == OnboardingStepStatus.ACTION_REQUIRED
    assert "尚未检测到入站任务" in result.summary
    assert approved is not None
    assert approved.last_message_id == 11


@pytest.mark.asyncio
async def test_verifier_first_message_completes_after_detecting_inbound_task(
    tmp_path: Path,
) -> None:
    _write_config(
        tmp_path,
        telegram=TelegramChannelConfig(
            enabled=True,
            mode="polling",
        ),
    )
    store = TelegramStateStore(tmp_path)
    store.upsert_approved_user(user_id=456, chat_id=456, username="alice")

    store_group = await create_store_group(
        str(tmp_path / "data" / "sqlite" / "octoagent.db"),
        tmp_path / "data" / "artifacts",
    )
    try:
        task_service = TaskService(store_group, SSEHub())
        task_id, created = await task_service.create_task(
            NormalizedMessage(
                channel="telegram",
                thread_id="tg:456",
                scope_id="chat:telegram:456",
                sender_id="456",
                sender_name="Alice",
                text="hello inbound",
                idempotency_key="tg-verifier-inbound-001",
            )
        )
        assert created is True
    finally:
        await store_group.conn.close()

    verifier = TelegramOnboardingVerifier(
        environ={"TELEGRAM_BOT_TOKEN": "test-token"},
        client_factory=lambda root: TelegramBotClient(
            root,
            environ={"TELEGRAM_BOT_TOKEN": "test-token"},
            transport=_transport(),
        ),
    )

    result = await verifier.verify_first_message(tmp_path, session=None)

    assert result.status == OnboardingStepStatus.COMPLETED
    assert task_id in result.summary


@pytest.mark.asyncio
async def test_verifier_first_message_ignores_group_task_from_same_user(
    tmp_path: Path,
) -> None:
    _write_config(
        tmp_path,
        telegram=TelegramChannelConfig(
            enabled=True,
            mode="polling",
        ),
    )
    store = TelegramStateStore(tmp_path)
    store.upsert_approved_user(user_id=456, chat_id=456, username="alice")

    store_group = await create_store_group(
        str(tmp_path / "data" / "sqlite" / "octoagent.db"),
        tmp_path / "data" / "artifacts",
    )
    try:
        task_service = TaskService(store_group, SSEHub())
        task_id, created = await task_service.create_task(
            NormalizedMessage(
                channel="telegram",
                thread_id="tg_group:999",
                scope_id="chat:telegram:999",
                sender_id="456",
                sender_name="Alice",
                text="hello group",
                idempotency_key="tg-verifier-group-001",
            )
        )
        assert created is True
    finally:
        await store_group.conn.close()

    verifier = TelegramOnboardingVerifier(
        environ={"TELEGRAM_BOT_TOKEN": "test-token"},
        client_factory=lambda root: TelegramBotClient(
            root,
            environ={"TELEGRAM_BOT_TOKEN": "test-token"},
            transport=_transport(),
        ),
    )

    result = await verifier.verify_first_message(tmp_path, session=None)
    approved = store.get_approved_user("456")

    assert result.status == OnboardingStepStatus.ACTION_REQUIRED
    assert "尚未检测到入站任务" in result.summary
    assert task_id not in result.summary
    assert approved is not None
    assert approved.last_message_id == 11
