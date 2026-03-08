"""Telegram gateway service 测试。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from octoagent.core.models import (
    ActionRequestEnvelope,
    ControlPlaneActor,
    ControlPlaneSurface,
    TaskStatus,
)
from octoagent.core.models.message import NormalizedMessage
from octoagent.core.store import create_store_group
from octoagent.gateway.services.control_plane import ControlPlaneService
from octoagent.gateway.services.operator_actions import OperatorActionService
from octoagent.gateway.services.operator_inbox import OperatorInboxService
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.gateway.services.task_service import TaskService
from octoagent.gateway.services.telegram import (
    TelegramApprovalBroadcaster,
    TelegramGatewayService,
)
from octoagent.policy.approval_manager import ApprovalManager
from octoagent.policy.models import ApprovalRequest
from octoagent.provider.dx.config_schema import (
    ChannelsConfig,
    OctoAgentConfig,
    TelegramChannelConfig,
)
from octoagent.provider.dx.config_wizard import save_config
from octoagent.provider.dx.project_migration import ProjectWorkspaceMigrationService
from octoagent.provider.dx.telegram_client import (
    InlineKeyboardMarkup,
    TelegramChat,
    TelegramMessage,
    TelegramUpdate,
    TelegramUser,
)
from octoagent.provider.dx.telegram_pairing import TelegramStateStore
from octoagent.tooling.models import SideEffectLevel
from ulid import ULID


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
    reply_to_message_id: str | int | None
    message_thread_id: str | int | None
    disable_notification: bool
    reply_markup: InlineKeyboardMarkup | dict[str, object] | None = None


class FakeTaskRunner:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, str]] = []

    async def enqueue(self, task_id: str, user_text: str, model_alias: str | None = None) -> None:
        del model_alias
        self.enqueued.append((task_id, user_text))


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
        self.sent_messages.append(
            SentMessage(
                chat_id=str(chat_id),
                text=text,
                reply_to_message_id=reply_to_message_id,
                message_thread_id=message_thread_id,
                disable_notification=disable_notification,
                reply_markup=reply_markup,
            )
        )
        return SimpleNamespace(message_id=9001)

    async def get_updates(
        self,
        *,
        offset: int | None = None,
        timeout_s: int,
    ) -> list[dict[str, object]]:
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


class FailingTelegramBotClient(FakeTelegramBotClient):
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
        del (
            chat_id,
            text,
            reply_to_message_id,
            message_thread_id,
            disable_notification,
            reply_markup,
        )
        raise RuntimeError("telegram transport broken")


@pytest.mark.asyncio
async def test_unknown_dm_creates_pairing_request(tmp_path: Path) -> None:
    _write_config(tmp_path)
    store_group = await create_store_group(
        str(tmp_path / "gateway.db"),
        str(tmp_path / "artifacts"),
    )
    state_store = TelegramStateStore(tmp_path)
    bot_client = FakeTelegramBotClient()
    service = TelegramGatewayService(
        project_root=tmp_path,
        store_group=store_group,
        sse_hub=SSEHub(),
        task_runner=FakeTaskRunner(),
        state_store=state_store,
        bot_client=bot_client,
    )

    result = await service.handle_webhook_update(
        {
            "update_id": 101,
            "message": {
                "message_id": 7,
                "text": "hello from stranger",
                "chat": {"id": 1001, "type": "private"},
                "from": {"id": 1001, "username": "stranger", "first_name": "Stranger"},
            },
        }
    )

    assert result.status == "pairing_required"
    pending = state_store.get_pending_pairing("1001")
    assert pending is not None
    assert pending.code == result.detail
    assert bot_client.sent_messages[0].chat_id == "1001"
    assert "Pairing Code" in bot_client.sent_messages[0].text
    assert await store_group.task_store.list_tasks() == []

    await store_group.conn.close()


@pytest.mark.asyncio
async def test_webhook_blocks_when_secret_env_is_configured_but_missing(
    tmp_path: Path,
) -> None:
    _write_config(tmp_path, webhook_secret_env="TELEGRAM_WEBHOOK_SECRET")
    store_group = await create_store_group(
        str(tmp_path / "gateway.db"),
        str(tmp_path / "artifacts"),
    )
    service = TelegramGatewayService(
        project_root=tmp_path,
        store_group=store_group,
        sse_hub=SSEHub(),
        state_store=TelegramStateStore(tmp_path),
        bot_client=FakeTelegramBotClient(),
    )

    result = await service.handle_webhook_update({"update_id": 1}, secret_token="anything")

    assert result.status == "blocked"
    assert result.detail == "telegram_webhook_secret_unavailable"

    await store_group.conn.close()


@pytest.mark.asyncio
async def test_pairing_notice_failure_does_not_raise_500(tmp_path: Path) -> None:
    _write_config(tmp_path)
    store_group = await create_store_group(
        str(tmp_path / "gateway.db"),
        str(tmp_path / "artifacts"),
    )
    state_store = TelegramStateStore(tmp_path)
    service = TelegramGatewayService(
        project_root=tmp_path,
        store_group=store_group,
        sse_hub=SSEHub(),
        task_runner=FakeTaskRunner(),
        state_store=state_store,
        bot_client=FailingTelegramBotClient(),
    )

    result = await service.handle_webhook_update(
        {
            "update_id": 111,
            "message": {
                "message_id": 8,
                "text": "hello from stranger",
                "chat": {"id": 1001, "type": "private"},
                "from": {"id": 1001, "username": "stranger", "first_name": "Stranger"},
            },
        }
    )

    assert result.status == "pairing_required"
    assert state_store.get_pending_pairing("1001") is not None

    await store_group.conn.close()


@pytest.mark.asyncio
async def test_authorized_dm_creates_task_and_dedupes_update(tmp_path: Path) -> None:
    _write_config(tmp_path)
    store_group = await create_store_group(
        str(tmp_path / "gateway.db"),
        str(tmp_path / "artifacts"),
    )
    state_store = TelegramStateStore(tmp_path)
    state_store.upsert_approved_user(user_id="42", chat_id="42", username="owner")
    task_runner = FakeTaskRunner()
    service = TelegramGatewayService(
        project_root=tmp_path,
        store_group=store_group,
        sse_hub=SSEHub(),
        task_runner=task_runner,
        state_store=state_store,
        bot_client=FakeTelegramBotClient(),
    )
    update = {
        "update_id": 202,
        "message": {
            "message_id": 11,
            "text": "run task",
            "chat": {"id": 42, "type": "private"},
            "from": {"id": 42, "username": "owner", "first_name": "Connor"},
        },
    }

    first = await service.handle_webhook_update(update)
    second = await service.handle_webhook_update(update)

    assert first.status == "accepted"
    assert first.created is True
    assert second.status == "duplicate"
    assert second.created is False
    assert second.task_id == first.task_id
    assert task_runner.enqueued == [(first.task_id or "", "run task")]

    task = await store_group.task_store.get_task(first.task_id or "")
    events = await store_group.event_store.get_events_for_task(first.task_id or "")
    approved = state_store.get_approved_user("42")

    assert task is not None
    assert task.requester.channel == "telegram"
    assert task.scope_id == "chat:telegram:42"
    assert task.thread_id == "tg:42"
    assert events[1].payload["metadata"] == {
        "telegram_update_id": "202",
        "telegram_chat_id": "42",
        "telegram_message_id": "11",
    }
    assert approved is not None
    assert approved.last_message_id == 11
    assert len(await store_group.task_store.list_tasks()) == 1

    await store_group.conn.close()


@pytest.mark.asyncio
async def test_control_plane_command_executes_registry_action_without_creating_task(
    tmp_path: Path,
) -> None:
    _write_config(tmp_path, dm_policy="open")
    store_group = await create_store_group(
        str(tmp_path / "gateway.db"),
        str(tmp_path / "artifacts"),
    )
    await ProjectWorkspaceMigrationService(
        project_root=tmp_path,
        store_group=store_group,
    ).ensure_default_project()
    state_store = TelegramStateStore(tmp_path)
    state_store.upsert_approved_user(user_id="42", chat_id="42", username="owner")
    bot_client = FakeTelegramBotClient()
    sse_hub = SSEHub()
    service = TelegramGatewayService(
        project_root=tmp_path,
        store_group=store_group,
        sse_hub=sse_hub,
        task_runner=FakeTaskRunner(),
        state_store=state_store,
        bot_client=bot_client,
    )
    service.bind_control_plane_service(
        ControlPlaneService(
            project_root=tmp_path,
            store_group=store_group,
            sse_hub=sse_hub,
            telegram_state_store=state_store,
        )
    )

    result = await service.handle_webhook_update(
        {
            "update_id": 303,
            "message": {
                "message_id": 19,
                "text": "/status",
                "chat": {"id": 42, "type": "private"},
                "from": {"id": 42, "username": "owner", "first_name": "Connor"},
            },
        }
    )

    assert result.status == "control_action"
    assert bot_client.sent_messages
    assert "Action: diagnostics.refresh" in bot_client.sent_messages[0].text
    tasks = await store_group.task_store.list_tasks()
    assert [task.task_id for task in tasks] == ["ops-control-plane"]

    await store_group.conn.close()


@pytest.mark.asyncio
async def test_web_and_telegram_project_select_share_action_semantics(
    tmp_path: Path,
) -> None:
    _write_config(tmp_path, dm_policy="open")
    store_group = await create_store_group(
        str(tmp_path / "gateway.db"),
        str(tmp_path / "artifacts"),
    )
    await ProjectWorkspaceMigrationService(
        project_root=tmp_path,
        store_group=store_group,
    ).ensure_default_project()
    control_plane = ControlPlaneService(
        project_root=tmp_path,
        store_group=store_group,
        sse_hub=SSEHub(),
        telegram_state_store=TelegramStateStore(tmp_path),
    )
    default_project = await store_group.project_store.get_default_project()
    assert default_project is not None

    web_result = await control_plane.execute_action(
        ActionRequestEnvelope(
            request_id=str(ULID()),
            action_id="project.select",
            surface=ControlPlaneSurface.WEB,
            actor=ControlPlaneActor(
                actor_id="user:web",
                actor_label="Owner",
            ),
            params={"project_id": default_project.project_id},
        )
    )
    telegram_request = control_plane.build_telegram_action_request(
        f"/project select {default_project.project_id}",
        actor_id="user:telegram:42",
        actor_label="Owner",
    )
    assert telegram_request is not None

    telegram_result = await control_plane.execute_action(telegram_request)

    assert telegram_request.action_id == "project.select"
    assert web_result.code == telegram_result.code == "PROJECT_SELECTED"
    assert web_result.data["project_id"] == telegram_result.data["project_id"]

    await store_group.conn.close()


@pytest.mark.asyncio
async def test_telegram_cancel_command_maps_to_session_interrupt(
    tmp_path: Path,
) -> None:
    _write_config(tmp_path, dm_policy="open")
    store_group = await create_store_group(
        str(tmp_path / "gateway.db"),
        str(tmp_path / "artifacts"),
    )
    await ProjectWorkspaceMigrationService(
        project_root=tmp_path,
        store_group=store_group,
    ).ensure_default_project()
    control_plane = ControlPlaneService(
        project_root=tmp_path,
        store_group=store_group,
        sse_hub=SSEHub(),
        telegram_state_store=TelegramStateStore(tmp_path),
    )

    request = control_plane.build_telegram_action_request(
        "/cancel task-123",
        actor_id="user:telegram:42",
        actor_label="Owner",
    )

    assert request is not None
    assert request.action_id == "session.interrupt"
    assert request.params == {"task_id": "task-123"}
    assert (
        control_plane.get_action_definition("operator.task.cancel").surface_aliases.get("telegram")
        is None
    )

    await store_group.conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("telegram_overrides", "update_sender_id", "expected_status"),
    [
        ({"dm_policy": "open"}, 501, "accepted"),
        ({"dm_policy": "allowlist", "allow_users": ["777"]}, 777, "accepted"),
        ({"dm_policy": "disabled"}, 909, "blocked"),
    ],
)
async def test_non_pairing_dm_policies_do_not_create_pending_pairings(
    tmp_path: Path,
    telegram_overrides: dict[str, object],
    update_sender_id: int,
    expected_status: str,
) -> None:
    _write_config(tmp_path, **telegram_overrides)
    store_group = await create_store_group(
        str(tmp_path / "gateway-dm-policy.db"),
        str(tmp_path / "artifacts"),
    )
    state_store = TelegramStateStore(tmp_path)
    task_runner = FakeTaskRunner()
    service = TelegramGatewayService(
        project_root=tmp_path,
        store_group=store_group,
        sse_hub=SSEHub(),
        task_runner=task_runner,
        state_store=state_store,
        bot_client=FakeTelegramBotClient(),
    )

    result = await service.handle_webhook_update(
        {
            "update_id": 244,
            "message": {
                "message_id": 20,
                "text": "dm policy check",
                "chat": {"id": update_sender_id, "type": "private"},
                "from": {
                    "id": update_sender_id,
                    "username": "member",
                    "first_name": "Member",
                },
            },
        }
    )

    assert result.status == expected_status
    assert state_store.get_pending_pairing(str(update_sender_id)) is None

    await store_group.conn.close()


@pytest.mark.asyncio
async def test_group_open_accepts_message_without_allowlist(tmp_path: Path) -> None:
    _write_config(tmp_path, allowed_groups=[], group_policy="open")
    store_group = await create_store_group(
        str(tmp_path / "gateway-group-open.db"),
        str(tmp_path / "artifacts"),
    )
    service = TelegramGatewayService(
        project_root=tmp_path,
        store_group=store_group,
        sse_hub=SSEHub(),
        task_runner=FakeTaskRunner(),
        state_store=TelegramStateStore(tmp_path),
        bot_client=FakeTelegramBotClient(),
    )

    result = await service.handle_webhook_update(
        {
            "update_id": 302,
            "message": {
                "message_id": 12,
                "text": "group hello",
                "chat": {"id": -10099, "type": "supergroup"},
                "from": {"id": 7, "username": "member", "first_name": "Member"},
            },
        }
    )

    task = await store_group.task_store.get_task(result.task_id or "")
    assert result.status == "accepted"
    assert task is not None
    assert task.scope_id == "chat:telegram:-10099"
    assert task.thread_id == "tg_group:-10099"

    await store_group.conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message_patch", "expected_thread"),
    [
        ({"message_thread_id": 77}, "tg_group:-10099:topic:77"),
        ({"reply_to_message": {"message_id": 88}}, "tg_group:-10099:reply:88"),
    ],
)
async def test_group_updates_preserve_stable_thread_routing(
    tmp_path: Path,
    message_patch: dict[str, object],
    expected_thread: str,
) -> None:
    _write_config(tmp_path, allowed_groups=["-10099"], group_policy="allowlist")
    store_group = await create_store_group(
        str(tmp_path / "gateway.db"),
        str(tmp_path / "artifacts"),
    )
    service = TelegramGatewayService(
        project_root=tmp_path,
        store_group=store_group,
        sse_hub=SSEHub(),
        task_runner=FakeTaskRunner(),
        state_store=TelegramStateStore(tmp_path),
        bot_client=FakeTelegramBotClient(),
    )

    update = {
        "update_id": 303,
        "message": {
            "message_id": 12,
            "text": "group hello",
            "chat": {"id": -10099, "type": "supergroup"},
            "from": {"id": 7, "username": "member", "first_name": "Member"},
            **message_patch,
        },
    }
    result = await service.handle_webhook_update(update)

    task = await store_group.task_store.get_task(result.task_id or "")
    assert result.status == "accepted"
    assert task is not None
    assert task.scope_id == "chat:telegram:-10099"
    assert task.thread_id == expected_thread

    await store_group.conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message_kwargs", "expected_thread"),
    [
        ({"message_thread_id": 77}, "tg_group:-10099:topic:77"),
        (
            {
                "reply_to_message": TelegramMessage(
                    message_id=88,
                    chat=TelegramChat(id=-10099, type="supergroup"),
                    from_user=TelegramUser(id=7, username="member", first_name="Member"),
                    text="root",
                )
            },
            "tg_group:-10099:reply:88",
        ),
    ],
)
async def test_polling_update_models_preserve_thread_routing(
    tmp_path: Path,
    message_kwargs: dict[str, object],
    expected_thread: str,
) -> None:
    _write_config(tmp_path, mode="polling", allowed_groups=["-10099"], group_policy="allowlist")
    store_group = await create_store_group(
        str(tmp_path / "gateway-polling-thread.db"),
        str(tmp_path / "artifacts"),
    )
    service = TelegramGatewayService(
        project_root=tmp_path,
        store_group=store_group,
        sse_hub=SSEHub(),
        task_runner=FakeTaskRunner(),
        state_store=TelegramStateStore(tmp_path),
        bot_client=FakeTelegramBotClient(),
    )

    result = await service._ingest_update(
        TelegramUpdate(
            update_id=505,
            message=TelegramMessage(
                message_id=16,
                chat=TelegramChat(id=-10099, type="supergroup"),
                from_user=TelegramUser(id=7, username="member", first_name="Member"),
                text="polling hello",
                **message_kwargs,
            ),
        )
    )

    task = await store_group.task_store.get_task(result.task_id or "")
    assert result.status == "accepted"
    assert task is not None
    assert task.thread_id == expected_thread

    await store_group.conn.close()


@pytest.mark.asyncio
async def test_group_followup_reply_to_bot_message_keeps_same_thread_id(
    tmp_path: Path,
) -> None:
    _write_config(tmp_path, allowed_groups=["-10099"], group_policy="allowlist")
    store_group = await create_store_group(
        str(tmp_path / "gateway.db"),
        str(tmp_path / "artifacts"),
    )
    bot_client = FakeTelegramBotClient()
    service = TelegramGatewayService(
        project_root=tmp_path,
        store_group=store_group,
        sse_hub=SSEHub(),
        task_runner=FakeTaskRunner(),
        state_store=TelegramStateStore(tmp_path),
        bot_client=bot_client,
    )

    first = await service.handle_webhook_update(
        {
            "update_id": 401,
            "message": {
                "message_id": 12,
                "text": "group hello",
                "chat": {"id": -10099, "type": "supergroup"},
                "from": {"id": 7, "username": "member", "first_name": "Member"},
                "reply_to_message": {"message_id": 88},
            },
        }
    )
    task_service = TaskService(store_group, SSEHub())
    await task_service._write_state_transition(
        task_id=first.task_id or "",
        from_status=TaskStatus.CREATED,
        to_status=TaskStatus.RUNNING,
        trace_id=f"trace-{first.task_id}",
    )
    await task_service._write_state_transition(
        task_id=first.task_id or "",
        from_status=TaskStatus.RUNNING,
        to_status=TaskStatus.SUCCEEDED,
        trace_id=f"trace-{first.task_id}",
    )
    first_task = await store_group.task_store.get_task(first.task_id or "")
    await service.notify_task_result(first.task_id or "")

    second = await service.handle_webhook_update(
        {
            "update_id": 402,
            "message": {
                "message_id": 13,
                "text": "follow up",
                "chat": {"id": -10099, "type": "supergroup"},
                "from": {"id": 7, "username": "member", "first_name": "Member"},
                "reply_to_message": {"message_id": 9001},
            },
        }
    )
    second_task = await store_group.task_store.get_task(second.task_id or "")

    assert first_task is not None
    assert second_task is not None
    assert first_task.thread_id == "tg_group:-10099:reply:88"
    assert second_task.thread_id == first_task.thread_id

    await store_group.conn.close()


@pytest.mark.asyncio
async def test_notify_task_result_and_approval_event_reply_to_original_thread(
    tmp_path: Path,
) -> None:
    _write_config(tmp_path)
    store_group = await create_store_group(
        str(tmp_path / "gateway.db"),
        str(tmp_path / "artifacts"),
    )
    bot_client = FakeTelegramBotClient()
    state_store = TelegramStateStore(tmp_path)
    state_store.upsert_approved_user(user_id="42", chat_id="4242", username="owner")
    service = TelegramGatewayService(
        project_root=tmp_path,
        store_group=store_group,
        sse_hub=SSEHub(),
        state_store=state_store,
        bot_client=bot_client,
    )
    approval_manager = ApprovalManager(event_store=store_group.event_store)
    service.bind_operator_services(
        OperatorInboxService(
            store_group=store_group,
            approval_manager=approval_manager,
            telegram_state_store=state_store,
        ),
        OperatorActionService(
            store_group=store_group,
            sse_hub=SSEHub(),
            approval_manager=approval_manager,
            telegram_state_store=state_store,
        ),
    )
    task_service = TaskService(store_group, SSEHub())
    task_id, created = await task_service.create_task(
        NormalizedMessage(
            channel="telegram",
            thread_id="tg:42",
            scope_id="chat:telegram:42",
            sender_id="42",
            sender_name="Owner",
            text="notify me",
            metadata={
                "telegram_chat_id": "42",
                "telegram_message_id": "11",
                "telegram_message_thread_id": "77",
            },
            idempotency_key="tg-result-001",
        )
    )
    assert created is True
    await task_service._write_state_transition(
        task_id=task_id,
        from_status=TaskStatus.CREATED,
        to_status=TaskStatus.RUNNING,
        trace_id=f"trace-{task_id}",
    )
    await task_service._write_state_transition(
        task_id=task_id,
        from_status=TaskStatus.RUNNING,
        to_status=TaskStatus.SUCCEEDED,
        trace_id=f"trace-{task_id}",
    )
    await approval_manager.register(
        ApprovalRequest(
            approval_id="ap-001",
            task_id=task_id,
            tool_name="filesystem.write",
            tool_args_summary="echo hello",
            risk_explanation="需要审批",
            policy_label="global.irreversible",
            side_effect_level=SideEffectLevel.IRREVERSIBLE,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
    )

    await service.notify_task_result(task_id)
    await TelegramApprovalBroadcaster(service).broadcast(
        "approval:requested",
        {"approval_id": "ap-001", "tool_name": "filesystem.write"},
        task_id=task_id,
    )

    assert len(bot_client.sent_messages) == 2
    assert bot_client.sent_messages[0].reply_to_message_id == "11"
    assert bot_client.sent_messages[0].message_thread_id == "77"
    assert bot_client.sent_messages[0].text == "任务已成功完成。"
    assert bot_client.sent_messages[1].chat_id == "4242"
    assert "filesystem.write 需要审批" in bot_client.sent_messages[1].text
    assert bot_client.sent_messages[1].disable_notification is True
    assert bot_client.sent_messages[1].reply_markup is not None

    await store_group.conn.close()
