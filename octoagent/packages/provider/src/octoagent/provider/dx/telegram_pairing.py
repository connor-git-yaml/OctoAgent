"""Telegram pairing / state 持久化。"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from filelock import FileLock
from pydantic import BaseModel, Field

_PAIRING_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_PAIRING_CODE_LENGTH = 6
_PAIRING_REQUEST_TTL = timedelta(days=7)


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _generate_pairing_code() -> str:
    return "".join(
        secrets.choice(_PAIRING_CODE_ALPHABET)
        for _ in range(_PAIRING_CODE_LENGTH)
    )


class TelegramPendingPairing(BaseModel):
    """待批准的 Telegram pairing 请求。"""

    code: str = Field(default_factory=_generate_pairing_code)
    user_id: str
    chat_id: str
    username: str = ""
    display_name: str = ""
    requested_at: datetime = Field(default_factory=_now)
    expires_at: datetime = Field(default_factory=lambda: _now() + _PAIRING_REQUEST_TTL)
    status: Literal["pending", "approved", "expired", "rejected"] = "pending"
    last_message_text: str = ""


class TelegramApprovedUser(BaseModel):
    """已批准的 Telegram DM 用户。"""

    user_id: str
    chat_id: str
    username: str = ""
    display_name: str = ""
    approved_at: datetime = Field(default_factory=_now)
    last_message_at: datetime | None = None
    last_message_id: int | None = None


class TelegramState(BaseModel):
    """telegram-state.json 的结构化表示。"""

    approved_users: dict[str, TelegramApprovedUser] = Field(default_factory=dict)
    allowed_groups: list[str] = Field(default_factory=list)
    pending_pairings: dict[str, TelegramPendingPairing] = Field(default_factory=dict)
    polling_offset: int | None = None
    group_allow_users: list[str] = Field(default_factory=list)
    reply_thread_roots: dict[str, str] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=_now)

    def first_approved_user(self) -> TelegramApprovedUser | None:
        if not self.approved_users:
            return None
        return min(self.approved_users.values(), key=lambda item: item.approved_at)


class TelegramStateStore:
    """项目级 Telegram state store。

    暴露给 verifier 与 gateway 复用，避免各自维护第三套状态。
    """

    def __init__(self, project_root: Path) -> None:
        self._root = project_root
        self._path = project_root / "data" / "telegram-state.json"
        self._lock = FileLock(str(self._path) + ".lock")
        self.last_issue: str | None = None

    @property
    def path(self) -> Path:
        return self._path

    @staticmethod
    def _reply_thread_key(chat_id: str | int, message_id: str | int) -> str:
        return f"{chat_id}:{message_id}"

    def load(self) -> TelegramState:
        self.last_issue = None
        if not self._path.exists():
            return TelegramState()

        with self._lock:
            try:
                raw = self._path.read_text(encoding="utf-8")
                data = json.loads(raw)
                return TelegramState.model_validate(data)
            except Exception:
                backup = self._path.with_suffix(self._path.suffix + ".corrupted")
                shutil.copy2(self._path, backup)
                self.last_issue = "corrupted"
                return TelegramState()

    def save(self, state: TelegramState) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        state.updated_at = _now()
        payload = state.model_dump(mode="json")
        text = json.dumps(payload, ensure_ascii=False, indent=2)

        with self._lock:
            fd, tmp_path = tempfile.mkstemp(dir=str(self._path.parent), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(text)
                Path(tmp_path).replace(self._path)
            finally:
                tmp = Path(tmp_path)
                if tmp.exists():
                    tmp.unlink(missing_ok=True)

    def list_approved_users(self) -> list[TelegramApprovedUser]:
        return sorted(
            self.load().approved_users.values(),
            key=lambda item: item.approved_at,
        )

    def get_approved_user(self, user_id: str) -> TelegramApprovedUser | None:
        return self.load().approved_users.get(str(user_id))

    def first_approved_user(self) -> TelegramApprovedUser | None:
        return self.load().first_approved_user()

    def is_user_allowed(self, user_id: str | int) -> bool:
        return str(user_id) in self.load().approved_users

    def upsert_approved_user(
        self,
        *,
        user_id: str | int,
        chat_id: str | int,
        username: str = "",
        display_name: str = "",
        message_id: int | None = None,
    ) -> TelegramApprovedUser:
        state = self.load()
        key = str(user_id)
        existing = state.approved_users.get(key)
        approved = TelegramApprovedUser(
            user_id=key,
            chat_id=str(chat_id),
            username=username or (existing.username if existing is not None else ""),
            display_name=display_name or (existing.display_name if existing is not None else ""),
            approved_at=existing.approved_at if existing is not None else _now(),
            last_message_at=_now(),
            last_message_id=message_id,
        )
        state.approved_users[key] = approved
        state.pending_pairings.pop(key, None)
        self.save(state)
        return approved

    def delete_approved_user(self, user_id: str | int) -> None:
        state = self.load()
        state.approved_users.pop(str(user_id), None)
        self.save(state)

    def list_allowed_groups(self) -> list[str]:
        return list(self.load().allowed_groups)

    def set_allowed_groups(self, group_ids: list[str | int]) -> list[str]:
        state = self.load()
        state.allowed_groups = [str(group_id) for group_id in group_ids]
        self.save(state)
        return list(state.allowed_groups)

    def list_group_allow_users(self) -> list[str]:
        return list(self.load().group_allow_users)

    def set_group_allow_users(self, user_ids: list[str | int]) -> list[str]:
        state = self.load()
        state.group_allow_users = [str(user_id) for user_id in user_ids]
        self.save(state)
        return list(state.group_allow_users)

    def is_group_allowed(
        self,
        group_id: str | int,
        sender_id: str | int | None = None,
    ) -> bool:
        state = self.load()
        group_key = str(group_id)
        if group_key not in set(state.allowed_groups):
            return False
        if sender_id is None or not state.group_allow_users:
            return True
        return str(sender_id) in set(state.group_allow_users)

    def resolve_reply_thread_root(
        self,
        *,
        chat_id: str | int,
        message_id: str | int,
    ) -> str | None:
        return self.load().reply_thread_roots.get(
            self._reply_thread_key(chat_id, message_id)
        )

    def remember_reply_thread_root(
        self,
        *,
        chat_id: str | int,
        message_id: str | int,
        root_message_id: str | int,
    ) -> str:
        state = self.load()
        root = str(root_message_id)
        state.reply_thread_roots[
            self._reply_thread_key(chat_id, message_id)
        ] = root
        self.save(state)
        return root

    def get_pending_pairing(self, user_id: str | int) -> TelegramPendingPairing | None:
        return self.load().pending_pairings.get(str(user_id))

    def list_pending_pairings(self) -> list[TelegramPendingPairing]:
        return sorted(
            self.load().pending_pairings.values(),
            key=lambda item: item.requested_at,
        )

    def upsert_pending_pairing(
        self,
        *,
        user_id: str | int,
        chat_id: str | int,
        username: str = "",
        display_name: str = "",
        last_message_text: str = "",
    ) -> TelegramPendingPairing:
        state = self.load()
        key = str(user_id)
        existing = state.pending_pairings.get(key)
        now = _now()
        keep_existing_code = (
            existing is not None
            and existing.status == "pending"
            and existing.expires_at > now
        )
        pending = TelegramPendingPairing(
            code=(
                existing.code
                if keep_existing_code and existing is not None
                else _generate_pairing_code()
            ),
            user_id=key,
            chat_id=str(chat_id),
            username=username or (existing.username if existing is not None else ""),
            display_name=display_name or (existing.display_name if existing is not None else ""),
            requested_at=(
                existing.requested_at
                if keep_existing_code and existing is not None
                else now
            ),
            expires_at=(
                existing.expires_at
                if keep_existing_code and existing is not None
                else now + _PAIRING_REQUEST_TTL
            ),
            status="pending",
            last_message_text=(
                last_message_text
                or (existing.last_message_text if existing is not None else "")
            ),
        )
        state.pending_pairings[key] = pending
        self.save(state)
        return pending

    def ensure_pairing_request(
        self,
        *,
        user_id: str | int,
        chat_id: str | int,
        username: str = "",
        display_name: str = "",
        last_message_text: str = "",
    ) -> TelegramPendingPairing:
        return self.upsert_pending_pairing(
            user_id=user_id,
            chat_id=chat_id,
            username=username,
            display_name=display_name,
            last_message_text=last_message_text,
        )

    def delete_pending_pairing(self, user_id: str | int) -> None:
        state = self.load()
        state.pending_pairings.pop(str(user_id), None)
        self.save(state)

    def record_dm_message(
        self,
        *,
        user_id: str | int,
        chat_id: str | int,
        username: str = "",
        display_name: str = "",
        message_id: int | None = None,
        text: str = "",
    ) -> None:
        approved = self.get_approved_user(str(user_id))
        if approved is not None:
            self.upsert_approved_user(
                user_id=user_id,
                chat_id=chat_id,
                username=username or approved.username,
                display_name=display_name or approved.display_name,
                message_id=message_id,
            )
            return
        self.upsert_pending_pairing(
            user_id=user_id,
            chat_id=chat_id,
            username=username,
            display_name=display_name,
            last_message_text=text,
        )

    def get_polling_offset(self) -> int | None:
        return self.load().polling_offset

    def set_polling_offset(self, offset: int | None) -> int | None:
        state = self.load()
        state.polling_offset = offset
        self.save(state)
        return state.polling_offset
