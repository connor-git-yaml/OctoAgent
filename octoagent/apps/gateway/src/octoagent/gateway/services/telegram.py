"""Telegram 渠道接入服务。"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from octoagent.core.models import OperatorActionOutcome, OperatorInboxItem, TaskStatus
from octoagent.core.models.message import NormalizedMessage
from octoagent.provider.dx.config_wizard import load_config
from octoagent.provider.dx.telegram_client import InlineKeyboardButton, InlineKeyboardMarkup

from .operator_actions import decode_telegram_operator_action, encode_telegram_operator_action
from .task_service import TaskService

logger = logging.getLogger(__name__)


class TelegramPairingRequestLike(Protocol):
    code: str


class TelegramApprovedUserLike(Protocol):
    user_id: str
    chat_id: str


class TelegramStateStoreProtocol(Protocol):
    def is_user_allowed(self, user_id: str) -> bool:
        ...

    def is_group_allowed(self, chat_id: str, sender_id: str) -> bool:
        ...

    def ensure_pairing_request(
        self,
        *,
        user_id: str,
        username: str = "",
        chat_id: str,
        display_name: str = "",
        last_message_text: str = "",
    ) -> TelegramPairingRequestLike:
        ...

    def record_dm_message(
        self,
        *,
        user_id: str,
        chat_id: str,
        username: str = "",
        display_name: str = "",
        message_id: int | None = None,
        text: str = "",
    ) -> None:
        ...

    def list_allowed_groups(self) -> list[str]:
        ...

    def list_group_allow_users(self) -> list[str]:
        ...

    def get_pending_pairing(self, user_id: str) -> TelegramPairingRequestLike | None:
        ...

    def list_pending_pairings(self) -> list[TelegramPairingRequestLike]:
        ...

    def upsert_approved_user(self, **kwargs: Any) -> TelegramApprovedUserLike:
        ...

    def delete_pending_pairing(self, user_id: str) -> None:
        ...

    def first_approved_user(self) -> TelegramApprovedUserLike | None:
        ...

    def resolve_reply_thread_root(
        self,
        *,
        chat_id: str,
        message_id: str,
    ) -> str | None:
        ...

    def remember_reply_thread_root(
        self,
        *,
        chat_id: str,
        message_id: str,
        root_message_id: str,
    ) -> str:
        ...

    def get_polling_offset(self) -> int | None:
        ...

    def set_polling_offset(self, offset: int | None) -> int | None:
        ...


class TelegramBotClientProtocol(Protocol):
    async def send_message(
        self,
        chat_id: str,
        text: str,
        *,
        reply_to_message_id: str | int | None = None,
        message_thread_id: str | int | None = None,
        disable_notification: bool = False,
        reply_markup: InlineKeyboardMarkup | dict[str, Any] | None = None,
    ) -> Any:
        ...

    async def get_updates(
        self,
        *,
        offset: int | None = None,
        timeout_s: int,
    ) -> list[Any]:
        ...

    async def answer_callback_query(
        self,
        callback_query_id: str,
        *,
        text: str = "",
        show_alert: bool = False,
    ) -> Any:
        ...

    async def edit_message_text(
        self,
        *,
        chat_id: str,
        message_id: str | int,
        text: str,
        reply_markup: InlineKeyboardMarkup | dict[str, Any] | None = None,
    ) -> Any:
        ...


@dataclass(slots=True)
class TelegramInboundContext:
    update_id: str
    chat_id: str
    chat_type: str
    sender_id: str
    sender_name: str
    message_id: str
    text: str
    sender_username: str = ""
    reply_to_message_id: str = ""
    message_thread_id: str = ""
    callback_query_id: str = ""
    callback_data: str = ""
    is_callback: bool = False


@dataclass(slots=True)
class TelegramIngestResult:
    status: str
    detail: str = ""
    task_id: str | None = None
    created: bool = False


def _read_nested_attr(obj: object, *names: str) -> object | None:
    current = obj
    for name in names:
        if current is None:
            return None
        current = getattr(current, name, None)
    return current


class TelegramGatewayService:
    """桥接 Telegram update 与现有 Task/Event 管道。"""

    def __init__(
        self,
        *,
        project_root: Path,
        store_group,
        sse_hub,
        task_runner=None,
        state_store: TelegramStateStoreProtocol | None = None,
        bot_client: TelegramBotClientProtocol | None = None,
        polling_timeout_s: int = 15,
    ) -> None:
        self._project_root = project_root
        self._stores = store_group
        self._sse_hub = sse_hub
        self._task_runner = task_runner
        self._state_store = state_store
        self._bot_client = bot_client
        self._polling_timeout_s = polling_timeout_s
        self._polling_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._operator_inbox_service = None
        self._operator_action_service = None
        self._control_plane_service = None

    @property
    def enabled(self) -> bool:
        config = self._get_telegram_config()
        return bool(getattr(config, "enabled", False))

    def bind_task_runner(self, task_runner: Any) -> None:
        self._task_runner = task_runner

    def bind_operator_services(
        self,
        operator_inbox_service: Any,
        operator_action_service: Any,
    ) -> None:
        self._operator_inbox_service = operator_inbox_service
        self._operator_action_service = operator_action_service

    def bind_control_plane_service(self, control_plane_service: Any) -> None:
        self._control_plane_service = control_plane_service

    def _get_telegram_config(self):
        try:
            cfg = load_config(self._project_root)
        except Exception:
            logger.warning("telegram_config_load_failed", exc_info=True)
            return None
        return _read_nested_attr(cfg, "channels", "telegram")

    def _resolve_mode(self) -> str:
        config = self._get_telegram_config()
        mode = getattr(config, "mode", "webhook")
        return str(mode or "webhook")

    def _resolve_secret(self) -> str:
        config = self._get_telegram_config()
        secret_env = getattr(config, "webhook_secret_env", "")
        if not secret_env:
            return ""
        return os.environ.get(str(secret_env), "")

    def _has_configured_secret(self) -> bool:
        config = self._get_telegram_config()
        secret_env = str(getattr(config, "webhook_secret_env", "") or "").strip()
        return bool(secret_env)

    def _resolve_static_allow_users(self) -> set[str]:
        config = self._get_telegram_config()
        allow_users = getattr(config, "allow_users", []) or []
        return {str(item) for item in allow_users if str(item).strip()}

    def _resolve_static_allowed_groups(self) -> set[str]:
        config = self._get_telegram_config()
        allowed_groups = getattr(config, "allowed_groups", []) or []
        return {str(item) for item in allowed_groups if str(item).strip()}

    def _resolve_group_allow_users(self) -> set[str]:
        config = self._get_telegram_config()
        allow_users = getattr(config, "group_allow_users", []) or []
        return {str(item) for item in allow_users if str(item).strip()}

    def _resolve_dm_policy(self) -> str:
        config = self._get_telegram_config()
        return str(getattr(config, "dm_policy", "pairing") or "pairing")

    def _resolve_group_policy(self) -> str:
        config = self._get_telegram_config()
        return str(getattr(config, "group_policy", "allowlist") or "allowlist")

    async def startup(self) -> None:
        self._stop_event.clear()
        if not self.enabled or self._bot_client is None or self._state_store is None:
            return
        if self._resolve_mode() != "polling":
            return
        if self._polling_task is None or self._polling_task.done():
            self._polling_task = asyncio.create_task(self._polling_loop())

    async def shutdown(self) -> None:
        self._stop_event.set()
        if self._polling_task is not None:
            self._polling_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._polling_task
            self._polling_task = None

    async def handle_webhook_update(
        self,
        update: Mapping[str, Any],
        *,
        secret_token: str = "",
    ) -> TelegramIngestResult:
        if not self.enabled:
            return TelegramIngestResult(status="disabled", detail="telegram_disabled")
        if self._resolve_mode() != "webhook":
            return TelegramIngestResult(status="blocked", detail="telegram_not_in_webhook_mode")

        expected_secret = self._resolve_secret()
        if self._has_configured_secret():
            if not expected_secret:
                return TelegramIngestResult(
                    status="blocked",
                    detail="telegram_webhook_secret_unavailable",
                )
            if secret_token != expected_secret:
                return TelegramIngestResult(status="unauthorized", detail="invalid_webhook_secret")

        return await self._ingest_update(update)

    async def _polling_loop(self) -> None:
        while not self._stop_event.is_set():
            assert self._state_store is not None
            assert self._bot_client is not None
            try:
                offset = self._state_store.get_polling_offset()
                updates = await self._bot_client.get_updates(
                    offset=offset,
                    timeout_s=self._polling_timeout_s,
                )
                next_offset = offset
                for update in updates:
                    await self._ingest_update(update)
                    payload = self._coerce_update(update)
                    if payload is None:
                        continue
                    try:
                        candidate = int(payload.get("update_id", 0)) + 1
                    except (TypeError, ValueError):
                        continue
                    next_offset = candidate if next_offset is None else max(next_offset, candidate)
                if next_offset != offset:
                    self._state_store.set_polling_offset(next_offset)
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - 防御性兜底
                logger.warning("telegram_polling_loop_failed", exc_info=True)
                await asyncio.sleep(1.0)

    async def _ingest_update(self, update: Mapping[str, Any] | Any) -> TelegramIngestResult:
        payload = self._coerce_update(update)
        if payload is None:
            return TelegramIngestResult(status="ignored", detail="unsupported_update_payload")

        context = self._extract_context(payload)
        if context is None:
            return TelegramIngestResult(status="ignored", detail="unsupported_or_empty_update")

        if not self._is_allowed(context):
            return await self._handle_unauthorized(context)

        if context.is_callback:
            return await self._handle_callback_query(context)
        if not context.text.strip():
            return TelegramIngestResult(status="ignored", detail="unsupported_or_empty_update")
        if control_result := await self._handle_control_command(context):
            return control_result

        self._record_authorized_private_dm(context)

        scope_id, thread_id, reply_thread_root_id = self._resolve_scope_thread(context)
        metadata = {
            "telegram_update_id": context.update_id,
            "telegram_chat_id": context.chat_id,
            "telegram_message_id": context.message_id,
        }
        if context.reply_to_message_id:
            metadata["telegram_reply_to_message_id"] = context.reply_to_message_id
        if context.message_thread_id:
            metadata["telegram_message_thread_id"] = context.message_thread_id
        if reply_thread_root_id:
            metadata["telegram_reply_thread_root_id"] = reply_thread_root_id

        message = NormalizedMessage(
            channel="telegram",
            thread_id=thread_id,
            scope_id=scope_id,
            sender_id=context.sender_id,
            sender_name=context.sender_name,
            text=context.text,
            metadata=metadata,
            idempotency_key=self._build_idempotency_key(context),
        )

        service = TaskService(self._stores, self._sse_hub)
        task_id, created = await service.create_task(message)
        self._remember_inbound_reply_thread(context, reply_thread_root_id)
        if created and self._task_runner is not None:
            await self._task_runner.enqueue(task_id, context.text)
        return TelegramIngestResult(
            status="accepted" if created else "duplicate",
            task_id=task_id,
            created=created,
        )

    async def _handle_control_command(
        self,
        context: TelegramInboundContext,
    ) -> TelegramIngestResult | None:
        if self._control_plane_service is None:
            return None
        request = self._control_plane_service.build_telegram_action_request(
            context.text,
            actor_id=f"user:telegram:{context.sender_id}",
            actor_label=context.sender_name,
        )
        if request is None:
            return None

        result = await self._control_plane_service.execute_action(request)
        if self._bot_client is not None:
            with contextlib.suppress(Exception):
                sent_message = await self._bot_client.send_message(
                    context.chat_id,
                    self._render_control_plane_result(result),
                    reply_to_message_id=context.message_id,
                    message_thread_id=context.message_thread_id or None,
                )
                self._remember_outbound_reply_thread(
                    {
                        "chat_id": context.chat_id,
                        "reply_thread_root_id": context.reply_to_message_id,
                    },
                    sent_message,
                )
        return TelegramIngestResult(
            status="control_action",
            detail=result.status.value,
            created=result.status.value == "completed",
        )

    def _is_allowed(self, context: TelegramInboundContext) -> bool:
        if self._state_store is None:
            return False

        if context.chat_type == "private":
            policy = self._resolve_dm_policy()
            if policy == "disabled":
                return False
            if policy == "open":
                return True
            static_allow = self._resolve_static_allow_users()
            if context.sender_id in static_allow:
                return True
            return self._state_store.is_user_allowed(context.sender_id)

        policy = self._resolve_group_policy()
        if policy == "disabled":
            return False
        if policy == "open":
            return True

        static_allowed_groups = self._resolve_static_allowed_groups()
        dynamic_allowed_groups = set(self._state_store.list_allowed_groups())
        chat_allowed = (
            "*" in static_allowed_groups
            or context.chat_id in static_allowed_groups
            or context.chat_id in dynamic_allowed_groups
        )
        if not chat_allowed:
            return False

        allowed_users = self._resolve_group_allow_users() | set(
            self._state_store.list_group_allow_users()
        )
        if not allowed_users:
            return True
        if context.sender_id in allowed_users:
            return True
        return self._state_store.is_group_allowed(context.chat_id, context.sender_id)

    def _record_authorized_private_dm(self, context: TelegramInboundContext) -> None:
        if context.chat_type != "private" or self._state_store is None:
            return
        if not self._state_store.is_user_allowed(context.sender_id):
            return
        self._state_store.record_dm_message(
            user_id=context.sender_id,
            chat_id=context.chat_id,
            username=context.sender_username,
            display_name=context.sender_name,
            message_id=int(context.message_id),
            text=context.text,
        )

    async def _handle_unauthorized(self, context: TelegramInboundContext) -> TelegramIngestResult:
        if context.chat_type != "private" or self._state_store is None or self._bot_client is None:
            return TelegramIngestResult(status="blocked", detail="telegram_sender_not_authorized")
        if context.is_callback:
            with contextlib.suppress(Exception):
                await self._bot_client.answer_callback_query(
                    context.callback_query_id,
                    text="当前账号没有 operator 权限",
                    show_alert=False,
                )
            return TelegramIngestResult(status="blocked", detail="telegram_sender_not_authorized")

        policy = self._resolve_dm_policy()
        if policy != "pairing":
            return TelegramIngestResult(status="blocked", detail="telegram_dm_not_authorized")

        request = self._state_store.ensure_pairing_request(
            user_id=context.sender_id,
            username=context.sender_username,
            chat_id=context.chat_id,
            display_name=context.sender_name,
            last_message_text=context.text,
        )
        text = (
            "当前 Telegram 私聊尚未授权。\n"
            f"Pairing Code: {request.code}\n"
            "请由 owner 审批后重新发送消息。"
        )
        try:
            await self._bot_client.send_message(context.chat_id, text)
        except Exception:
            logger.warning(
                "telegram_pairing_notice_failed chat_id=%s user_id=%s",
                context.chat_id,
                context.sender_id,
                exc_info=True,
            )
        await self._notify_pairing_request(context.sender_id)
        return TelegramIngestResult(status="pairing_required", detail=request.code)

    @staticmethod
    def _build_idempotency_key(context: TelegramInboundContext) -> str:
        return f"telegram:{context.update_id}:{context.chat_id}:{context.message_id}"

    def _resolve_scope_thread(self, context: TelegramInboundContext) -> tuple[str, str, str]:
        scope_id = f"chat:telegram:{context.chat_id}"
        if context.chat_type == "private":
            return scope_id, f"tg:{context.sender_id}", ""
        if context.message_thread_id:
            return (
                scope_id,
                f"tg_group:{context.chat_id}:topic:{context.message_thread_id}",
                "",
            )
        if context.reply_to_message_id:
            reply_thread_root_id = context.reply_to_message_id
            if self._state_store is not None:
                resolved = self._state_store.resolve_reply_thread_root(
                    chat_id=context.chat_id,
                    message_id=context.reply_to_message_id,
                )
                if resolved:
                    reply_thread_root_id = resolved
            return (
                scope_id,
                f"tg_group:{context.chat_id}:reply:{reply_thread_root_id}",
                reply_thread_root_id,
            )
        return scope_id, f"tg_group:{context.chat_id}", ""

    @staticmethod
    def _extract_context(update: Mapping[str, Any]) -> TelegramInboundContext | None:
        message = update.get("message")
        callback_query_id = ""
        callback_data = ""
        is_callback = False
        if not isinstance(message, Mapping):
            callback = update.get("callback_query")
            if isinstance(callback, Mapping):
                callback_query_id = str(callback.get("id") or "").strip()
                callback_data = str(callback.get("data") or "").strip()
                is_callback = True
                message = callback.get("message")
                sender = callback.get("from")
            else:
                sender = None
        else:
            sender = message.get("from")
        if not isinstance(message, Mapping):
            return None

        chat = message.get("chat")
        if not isinstance(chat, Mapping) or not isinstance(sender, Mapping):
            return None

        text = str(message.get("text") or "").strip()
        username = str(sender.get("username") or "").strip()
        sender_name = (
            username
            or str(sender.get("first_name") or "").strip()
            or str(sender.get("id") or "").strip()
        )
        reply_to = message.get("reply_to_message")
        reply_to_message_id = ""
        if isinstance(reply_to, Mapping):
            value = reply_to.get("message_id")
            if value is not None:
                reply_to_message_id = str(value)

        message_thread_id = ""
        if message.get("message_thread_id") is not None:
            message_thread_id = str(message.get("message_thread_id"))

        update_id = update.get("update_id")
        message_id = message.get("message_id")
        chat_id = chat.get("id")
        sender_id = sender.get("id")
        if update_id is None or message_id is None or chat_id is None or sender_id is None:
            return None

        return TelegramInboundContext(
            update_id=str(update_id),
            chat_id=str(chat_id),
            chat_type=str(chat.get("type") or "private"),
            sender_id=str(sender_id),
            sender_name=sender_name or str(sender_id),
            message_id=str(message_id),
            text=text,
            sender_username=username,
            reply_to_message_id=reply_to_message_id,
            message_thread_id=message_thread_id,
            callback_query_id=callback_query_id,
            callback_data=callback_data,
            is_callback=is_callback,
        )

    async def notify_task_result(self, task_id: str) -> None:
        if self._bot_client is None:
            return
        task = await self._stores.task_store.get_task(task_id)
        if task is None or task.requester.channel != "telegram":
            return

        target = await self._resolve_reply_target(task_id)
        if target is None:
            return

        events = await self._stores.event_store.get_events_for_task(task_id)
        text = self._build_result_text(self._status_value(task.status), events)
        sent_message = await self._bot_client.send_message(
            target["chat_id"],
            text,
            reply_to_message_id=target.get("reply_to_message_id") or None,
            message_thread_id=target.get("message_thread_id") or None,
        )
        self._remember_outbound_reply_thread(target, sent_message)

    async def notify_approval_event(
        self,
        *,
        event_type: str,
        data: Mapping[str, Any],
        task_id: str | None,
    ) -> None:
        if self._bot_client is None or task_id is None:
            return
        task = await self._stores.task_store.get_task(task_id)
        if task is None:
            return

        target = await self._resolve_operator_target()
        if target is None:
            return

        if event_type == "approval:requested":
            approval_id = str(data.get("approval_id", "")).strip()
            item = None
            if approval_id and self._operator_inbox_service is not None:
                with contextlib.suppress(Exception):
                    item = await self._operator_inbox_service.get_item(f"approval:{approval_id}")
            text = (
                self._build_operator_item_text(item)
                if item is not None
                else (
                    "任务需要审批。\n"
                    f"Approval ID: {data.get('approval_id', '-')}\n"
                    f"Tool: {data.get('tool_name', '-')}"
                )
            )
            reply_markup = self._build_operator_item_markup(item)
        elif event_type == "approval:resolved":
            decision = str(data.get("decision", "unknown"))
            text = f"审批结果已更新：{decision}"
            reply_markup = None
        else:
            return

        sent_message = await self._bot_client.send_message(
            target["chat_id"],
            text,
            disable_notification=True,
            reply_markup=reply_markup,
        )
        self._remember_outbound_reply_thread(target, sent_message)

    async def _handle_callback_query(self, context: TelegramInboundContext) -> TelegramIngestResult:
        if (
            self._operator_action_service is None
            or self._bot_client is None
            or not context.callback_query_id
            or not context.callback_data
        ):
            return TelegramIngestResult(status="ignored", detail="operator_action_unavailable")

        try:
            request = decode_telegram_operator_action(context.callback_data).model_copy(
                update={
                    "actor_id": f"user:telegram:{context.sender_id}",
                    "actor_label": context.sender_name,
                }
            )
        except ValueError as exc:
            await self._bot_client.answer_callback_query(
                context.callback_query_id,
                text=str(exc),
                show_alert=False,
            )
            return TelegramIngestResult(status="blocked", detail="invalid_operator_callback")

        result = await self._operator_action_service.execute(request)
        await self._bot_client.answer_callback_query(
            context.callback_query_id,
            text=self._callback_notice(result),
            show_alert=False,
        )
        with contextlib.suppress(Exception):
            await self._bot_client.edit_message_text(
                chat_id=context.chat_id,
                message_id=context.message_id,
                text=self._render_operator_result_text(result),
                reply_markup=None,
            )
        return TelegramIngestResult(
            status="operator_action",
            detail=result.outcome.value,
            task_id=result.task_id,
            created=result.outcome == OperatorActionOutcome.SUCCEEDED,
        )

    async def _notify_pairing_request(self, user_id: str) -> None:
        if self._operator_inbox_service is None or self._bot_client is None:
            return
        item = None
        with contextlib.suppress(Exception):
            item = await self._operator_inbox_service.get_item(f"pairing:{user_id}")
        if item is None:
            return
        target = await self._resolve_operator_target()
        if target is None:
            return
        await self._bot_client.send_message(
            target["chat_id"],
            self._build_operator_item_text(item),
            disable_notification=True,
            reply_markup=self._build_operator_item_markup(item),
        )

    async def _resolve_operator_target(self) -> dict[str, str] | None:
        if self._state_store is None:
            return None
        approved = self._state_store.first_approved_user()
        if approved is None:
            return None
        return {"chat_id": str(approved.chat_id)}

    def _build_operator_item_text(self, item: OperatorInboxItem | None) -> str:
        if item is None:
            return "存在待处理 operator 工作项。"
        lines = [item.title]
        if item.summary:
            lines.append(item.summary)
        if item.expires_at is not None:
            lines.append(f"过期时间: {item.expires_at.isoformat()}")
        if item.task_id:
            lines.append(f"Task: {item.task_id}")
        code = item.metadata.get("code", "")
        if code:
            lines.append(f"Pairing Code: {code}")
        return "\n".join(lines)

    def _build_operator_item_markup(
        self,
        item: OperatorInboxItem | None,
    ) -> InlineKeyboardMarkup | None:
        if item is None:
            return None
        rows: list[list[InlineKeyboardButton]] = []
        current: list[InlineKeyboardButton] = []
        for action in item.quick_actions:
            if not action.enabled:
                continue
            try:
                callback_data = encode_telegram_operator_action(item.item_id, action.kind)
            except ValueError:
                continue
            current.append(
                InlineKeyboardButton(text=action.label, callback_data=callback_data)
            )
            if len(current) == 2:
                rows.append(current)
                current = []
        if current:
            rows.append(current)
        if not rows:
            return None
        return InlineKeyboardMarkup(inline_keyboard=rows)

    @staticmethod
    def _callback_notice(result) -> str:
        if result.outcome == OperatorActionOutcome.SUCCEEDED:
            return "已处理"
        if result.outcome == OperatorActionOutcome.ALREADY_HANDLED:
            return "已被处理"
        if result.outcome == OperatorActionOutcome.EXPIRED:
            return "已过期"
        if result.outcome == OperatorActionOutcome.STALE_STATE:
            return "状态已变化"
        if result.outcome == OperatorActionOutcome.NOT_ALLOWED:
            return "当前不可操作"
        if result.outcome == OperatorActionOutcome.NOT_FOUND:
            return "目标不存在"
        return "处理失败"

    @staticmethod
    def _render_operator_result_text(result) -> str:
        return (
            "Operator Action\n"
            f"结果: {result.outcome.value}\n"
            f"说明: {result.message}"
        )

    @staticmethod
    def _render_control_plane_result(result) -> str:
        lines = [
            f"Action: {result.action_id}",
            f"状态: {result.status.value}",
            f"代码: {result.code}",
            f"说明: {result.message}",
        ]
        if isinstance(result.data, Mapping):
            summary = result.data.get("overall_status") or result.data.get("project_id")
            if summary:
                lines.append(f"摘要: {summary}")
        return "\n".join(lines)

    async def _resolve_reply_target(self, task_id: str) -> dict[str, str] | None:
        events = await self._stores.event_store.get_events_for_task(task_id)
        for event in events:
            if self._event_type_name(event) != "USER_MESSAGE":
                continue
            metadata = event.payload.get("metadata", {})
            if not isinstance(metadata, Mapping):
                continue
            chat_id = str(metadata.get("telegram_chat_id", "")).strip()
            if not chat_id:
                continue
            result = {"chat_id": chat_id}
            reply_to = str(metadata.get("telegram_message_id", "")).strip()
            if reply_to:
                result["reply_to_message_id"] = reply_to
            thread_id = str(metadata.get("telegram_message_thread_id", "")).strip()
            if thread_id:
                result["message_thread_id"] = thread_id
            reply_thread_root_id = str(
                metadata.get("telegram_reply_thread_root_id", "")
            ).strip()
            if reply_thread_root_id:
                result["reply_thread_root_id"] = reply_thread_root_id
            return result
        return None

    @staticmethod
    def _build_result_text(status: str, events: list[Any]) -> str:
        if status == TaskStatus.SUCCEEDED.value:
            for event in reversed(events):
                if TelegramGatewayService._event_type_name(event) == "MODEL_CALL_COMPLETED":
                    summary = str(event.payload.get("response_summary", "")).strip()
                    if summary:
                        return summary
            return "任务已成功完成。"

        if status == TaskStatus.FAILED.value:
            for event in reversed(events):
                if TelegramGatewayService._event_type_name(event) == "MODEL_CALL_FAILED":
                    message = str(event.payload.get("error_message", "")).strip()
                    if message:
                        return f"任务失败：{message}"
            return "任务失败，请查看系统日志。"

        if status == TaskStatus.CANCELLED.value:
            return "任务已取消。"
        if status == TaskStatus.REJECTED.value:
            return "任务已被拒绝。"
        return f"任务状态已更新：{status}"

    @staticmethod
    def _coerce_update(update: Mapping[str, Any] | Any) -> Mapping[str, Any] | None:
        if isinstance(update, Mapping):
            return update
        model_dump = getattr(update, "model_dump", None)
        if callable(model_dump):
            payload = model_dump(by_alias=True, exclude_none=True)
            if isinstance(payload, Mapping):
                return payload
        return None

    @staticmethod
    def _event_type_name(event: Any) -> str:
        event_type = getattr(event, "type", "")
        return str(getattr(event_type, "value", event_type))

    @staticmethod
    def _status_value(status: Any) -> str:
        return str(getattr(status, "value", status))

    def _remember_inbound_reply_thread(
        self,
        context: TelegramInboundContext,
        reply_thread_root_id: str,
    ) -> None:
        if (
            self._state_store is None
            or context.chat_type == "private"
            or not reply_thread_root_id
        ):
            return
        self._state_store.remember_reply_thread_root(
            chat_id=context.chat_id,
            message_id=context.message_id,
            root_message_id=reply_thread_root_id,
        )

    def _remember_outbound_reply_thread(
        self,
        target: Mapping[str, str],
        sent_message: Any,
    ) -> None:
        if self._state_store is None:
            return
        reply_thread_root_id = str(target.get("reply_thread_root_id", "")).strip()
        if not reply_thread_root_id:
            return
        message_id = self._extract_message_id(sent_message)
        if not message_id:
            return
        self._state_store.remember_reply_thread_root(
            chat_id=target["chat_id"],
            message_id=message_id,
            root_message_id=reply_thread_root_id,
        )

    @staticmethod
    def _extract_message_id(sent_message: Any) -> str:
        value = getattr(sent_message, "message_id", None)
        if value is None and isinstance(sent_message, Mapping):
            value = sent_message.get("message_id")
        return str(value).strip() if value is not None else ""


class CompositeApprovalBroadcaster:
    """同时广播 SSE 与 Telegram 提示。"""

    def __init__(self, *broadcasters) -> None:
        self._broadcasters = [item for item in broadcasters if item is not None]

    async def broadcast(
        self,
        event_type: str,
        data: dict[str, Any],
        task_id: str | None = None,
    ) -> None:
        for broadcaster in self._broadcasters:
            await broadcaster.broadcast(event_type, data, task_id=task_id)


class TelegramApprovalBroadcaster:
    """将审批状态同步到 Telegram 会话。"""

    def __init__(self, telegram_service: TelegramGatewayService) -> None:
        self._telegram_service = telegram_service

    async def broadcast(
        self,
        event_type: str,
        data: dict[str, Any],
        task_id: str | None = None,
    ) -> None:
        await self._telegram_service.notify_approval_event(
            event_type=event_type,
            data=data,
            task_id=task_id,
        )
