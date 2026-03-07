"""Telegram Bot API client。"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from .config_schema import ConfigParseError
from .config_wizard import load_config


class TelegramBotClientError(RuntimeError):
    """Telegram client 基础错误。"""


class TelegramBotClientConfigError(TelegramBotClientError):
    """Telegram client 配置缺失或无效。"""


class TelegramBotApiError(TelegramBotClientError):
    """Telegram Bot API 返回异常。"""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload or {}


class TelegramUser(BaseModel):
    id: int
    is_bot: bool = False
    username: str | None = None
    first_name: str = ""


class TelegramChat(BaseModel):
    id: int
    type: str
    title: str | None = None
    username: str | None = None


class InlineKeyboardButton(BaseModel):
    text: str
    callback_data: str | None = None


class InlineKeyboardMarkup(BaseModel):
    inline_keyboard: list[list[InlineKeyboardButton]]


class TelegramBotIdentity(TelegramUser):
    """`getMe()` 结果。"""


class TelegramMessage(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    message_id: int
    chat: TelegramChat
    from_user: TelegramUser | None = Field(default=None, alias="from")
    text: str | None = None
    message_thread_id: int | None = None
    reply_to_message: TelegramMessage | None = None


TelegramMessage.model_rebuild()


class TelegramCallbackQuery(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    from_user: TelegramUser = Field(alias="from")
    message: TelegramMessage | None = None
    data: str | None = None


class TelegramUpdate(BaseModel):
    update_id: int
    message: TelegramMessage | None = None
    callback_query: TelegramCallbackQuery | None = None


class TelegramBotClient:
    """项目级 Telegram Bot API client。"""

    def __init__(
        self,
        project_root: Path,
        *,
        environ: Mapping[str, str] | None = None,
        base_url: str = "https://api.telegram.org",
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._project_root = project_root
        self._environ = environ if environ is not None else os.environ
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._transport = transport

    def _load_bot_token(self) -> str:
        try:
            config = load_config(self._project_root)
        except ConfigParseError as exc:
            raise TelegramBotClientConfigError(
                f"octoagent.yaml 无法解析: {exc}"
            ) from exc
        if config is None:
            raise TelegramBotClientConfigError("octoagent.yaml 不存在，无法解析 Telegram bot token")

        token_env = config.channels.telegram.bot_token_env
        token = self._environ.get(token_env, "")
        if not token:
            raise TelegramBotClientConfigError(
                f"未找到 Telegram bot token 环境变量: {token_env}"
            )
        return token

    async def _request(
        self,
        method: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        bot_token = self._load_bot_token()
        base_url = f"{self._base_url}/bot{bot_token}"
        async with httpx.AsyncClient(
            base_url=base_url,
            timeout=self._timeout,
            transport=self._transport,
        ) as client:
            response = await client.post(f"/{method}", json=payload or {})

        try:
            data = response.json()
        except ValueError as exc:
            raise TelegramBotApiError(
                f"Telegram Bot API 返回非 JSON 响应（status={response.status_code}）",
                status_code=response.status_code,
            ) from exc

        if not isinstance(data, dict):
            raise TelegramBotApiError(
                "Telegram Bot API 返回结构非法",
                status_code=response.status_code,
            )

        if response.status_code != 200 or not data.get("ok", False):
            description = str(data.get("description") or response.text[:200])
            raise TelegramBotApiError(
                description,
                status_code=response.status_code,
                payload=data,
            )

        return data.get("result")

    async def get_me(self) -> TelegramBotIdentity:
        result = await self._request("getMe")
        return TelegramBotIdentity.model_validate(result)

    async def send_message(
        self,
        chat_id: str | int,
        text: str,
        *,
        reply_to_message_id: str | int | None = None,
        message_thread_id: str | int | None = None,
        disable_notification: bool = True,
        reply_markup: InlineKeyboardMarkup | dict[str, Any] | None = None,
    ) -> TelegramMessage:
        payload: dict[str, Any] = {
            "chat_id": str(chat_id),
            "text": text,
            "disable_notification": disable_notification,
        }
        if reply_to_message_id is not None:
            payload["reply_to_message_id"] = int(reply_to_message_id)
        if message_thread_id is not None:
            payload["message_thread_id"] = int(message_thread_id)
        if reply_markup is not None:
            payload["reply_markup"] = (
                reply_markup.model_dump(exclude_none=True)
                if isinstance(reply_markup, BaseModel)
                else reply_markup
            )
        result = await self._request(
            "sendMessage",
            payload=payload,
        )
        return TelegramMessage.model_validate(result)

    async def answer_callback_query(
        self,
        callback_query_id: str,
        *,
        text: str = "",
        show_alert: bool = False,
    ) -> bool:
        payload: dict[str, Any] = {
            "callback_query_id": callback_query_id,
            "show_alert": show_alert,
        }
        if text:
            payload["text"] = text
        result = await self._request("answerCallbackQuery", payload=payload)
        return bool(result)

    async def edit_message_text(
        self,
        *,
        chat_id: str | int,
        message_id: str | int,
        text: str,
        reply_markup: InlineKeyboardMarkup | dict[str, Any] | None = None,
    ) -> TelegramMessage:
        payload: dict[str, Any] = {
            "chat_id": str(chat_id),
            "message_id": int(message_id),
            "text": text,
        }
        if reply_markup is not None:
            payload["reply_markup"] = (
                reply_markup.model_dump(exclude_none=True)
                if isinstance(reply_markup, BaseModel)
                else reply_markup
            )
        result = await self._request("editMessageText", payload=payload)
        return TelegramMessage.model_validate(result)

    async def get_updates(
        self,
        *,
        offset: int | None = None,
        timeout: int = 0,
        timeout_s: int | None = None,
        limit: int | None = None,
    ) -> list[TelegramUpdate]:
        payload: dict[str, Any] = {"timeout": timeout_s if timeout_s is not None else timeout}
        if offset is not None:
            payload["offset"] = offset
        if limit is not None:
            payload["limit"] = limit
        result = await self._request("getUpdates", payload=payload)
        if not isinstance(result, list):
            raise TelegramBotApiError("getUpdates 返回结构非法")
        return [TelegramUpdate.model_validate(item) for item in result]
