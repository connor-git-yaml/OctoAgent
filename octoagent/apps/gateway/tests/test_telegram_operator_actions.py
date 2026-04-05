from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from octoagent.core.models import OperatorActionKind
from octoagent.core.models.message import NormalizedMessage
from octoagent.core.store import create_store_group
from octoagent.gateway.services.operator_actions import (
    OperatorActionService,
    encode_telegram_operator_action,
)
from octoagent.gateway.services.operator_inbox import OperatorInboxService
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.gateway.services.task_service import TaskService
from octoagent.gateway.services.telegram import TelegramGatewayService
from octoagent.policy.approval_manager import ApprovalManager
from octoagent.policy.models import ApprovalRequest
from octoagent.gateway.services.config.config_schema import (
    ChannelsConfig,
    OctoAgentConfig,
    TelegramChannelConfig,
)
from octoagent.gateway.services.config.config_wizard import save_config
from octoagent.gateway.services.telegram_client import InlineKeyboardMarkup
from octoagent.provider.dx.telegram_pairing import TelegramStateStore
from octoagent.tooling.models import SideEffectLevel


def _write_config(project_root: Path, **telegram_overrides: object) -> None:
    telegram_config: dict[str, object] = {
        "enabled": True,
        "mode": "webhook",
        "webhook_url": "https://example.com/api/telegram/webhook",
    }
    telegram_config.update(telegram_overrides)
    save_config(
        OctoAgentConfig(
            updated_at="2026-03-07",
            channels=ChannelsConfig(
                telegram=TelegramChannelConfig(**telegram_config)
            ),
        ),
        project_root,
    )


@dataclass
class SentMessage:
    chat_id: str
    text: str
    reply_markup: InlineKeyboardMarkup | dict[str, object] | None


class FakeTelegramBotClient:
    def __init__(self) -> None:
        self.sent_messages: list[SentMessage] = []
        self.answered_callbacks: list[tuple[str, str, bool]] = []
        self.edited_messages: list[tuple[str, str | int, str]] = []

    async def send_message(
        self,
        chat_id: str,
        text: str,
        *,
        reply_to_message_id: str | int | None = None,
        message_thread_id: str | int | None = None,
        disable_notification: bool = False,
        reply_markup: InlineKeyboardMarkup | dict[str, object] | None = None,
    ) -> SimpleNamespace:
        del reply_to_message_id, message_thread_id, disable_notification
        self.sent_messages.append(SentMessage(str(chat_id), text, reply_markup))
        return SimpleNamespace(message_id=9001)

    async def get_updates(self, *, offset: int | None = None, timeout_s: int):
        del offset, timeout_s
        return []

    async def answer_callback_query(
        self,
        callback_query_id: str,
        *,
        text: str = "",
        show_alert: bool = False,
    ) -> bool:
        self.answered_callbacks.append((callback_query_id, text, show_alert))
        return True

    async def edit_message_text(
        self,
        *,
        chat_id: str,
        message_id: str | int,
        text: str,
        reply_markup: InlineKeyboardMarkup | dict[str, object] | None = None,
    ) -> SimpleNamespace:
        del reply_markup
        self.edited_messages.append((str(chat_id), message_id, text))
        return SimpleNamespace(message_id=message_id)


@pytest.mark.asyncio
async def test_telegram_callback_executes_operator_action_and_is_idempotent(
    tmp_path: Path,
) -> None:
    _write_config(tmp_path)
    store_group = await create_store_group(
        str(tmp_path / "gateway.db"),
        str(tmp_path / "artifacts"),
    )
    state_store = TelegramStateStore(tmp_path)
    state_store.upsert_approved_user(user_id="42", chat_id="42", username="owner")
    bot_client = FakeTelegramBotClient()
    approval_manager = ApprovalManager(event_store=store_group.event_store)
    task_service = TaskService(store_group, SSEHub())
    task_id, created = await task_service.create_task(
        NormalizedMessage(
            channel="web",
            thread_id="thread-approval",
            scope_id="scope-approval",
            sender_id="owner",
            sender_name="Owner",
            text="need approval",
            idempotency_key="tg-callback-approval",
        )
    )
    assert created is True
    await approval_manager.register(
        ApprovalRequest(
            approval_id="ap-001",
            task_id=task_id,
            tool_name="filesystem.write",
            tool_args_summary="echo hello",
            risk_explanation="需要审批",
            policy_label="global.irreversible",
            side_effect_level=SideEffectLevel.IRREVERSIBLE,
            expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        )
    )
    inbox_service = OperatorInboxService(
        store_group=store_group,
        approval_manager=approval_manager,
        telegram_state_store=state_store,
    )
    action_service = OperatorActionService(
        store_group=store_group,
        sse_hub=SSEHub(),
        approval_manager=approval_manager,
        telegram_state_store=state_store,
    )
    service = TelegramGatewayService(
        project_root=tmp_path,
        store_group=store_group,
        sse_hub=SSEHub(),
        state_store=state_store,
        bot_client=bot_client,
    )
    service.bind_operator_services(inbox_service, action_service)

    callback_data = encode_telegram_operator_action(
        "approval:ap-001",
        OperatorActionKind.APPROVE_ONCE,
    )
    first = await service.handle_webhook_update(
        {
            "update_id": 301,
            "callback_query": {
                "id": "cb-001",
                "data": callback_data,
                "from": {"id": 42, "username": "owner", "first_name": "Owner"},
                "message": {
                    "message_id": 77,
                    "text": "approval card",
                    "chat": {"id": 42, "type": "private"},
                    "from": {"id": 999, "username": "octo-bot", "first_name": "Octo"},
                },
            },
        }
    )
    second = await service.handle_webhook_update(
        {
            "update_id": 302,
            "callback_query": {
                "id": "cb-002",
                "data": callback_data,
                "from": {"id": 42, "username": "owner", "first_name": "Owner"},
                "message": {
                    "message_id": 77,
                    "text": "approval card",
                    "chat": {"id": 42, "type": "private"},
                    "from": {"id": 999, "username": "octo-bot", "first_name": "Octo"},
                },
            },
        }
    )

    assert first.status == "operator_action"
    assert first.detail == "succeeded"
    assert second.status == "operator_action"
    assert second.detail == "already_handled"
    assert approval_manager.get_approval("ap-001").status.value == "approved"
    assert bot_client.answered_callbacks[0][1] == "已处理"
    assert bot_client.answered_callbacks[1][1] == "已被处理"
    assert "结果: succeeded" in bot_client.edited_messages[0][2]
    assert "结果: already_handled" in bot_client.edited_messages[1][2]

    await store_group.conn.close()
