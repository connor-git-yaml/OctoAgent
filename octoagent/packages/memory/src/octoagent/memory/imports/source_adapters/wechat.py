"""029 WeChat offline import adapter。"""

from __future__ import annotations

import json
import mimetypes
import re
import sqlite3
from collections import defaultdict
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from html import unescape
from pathlib import Path
from typing import Any

from octoagent.core.models import MessageAttachment

from ..models import (
    DetectedConversation,
    DetectedParticipant,
    ImportedChatMessage,
    ImportInputRef,
    ImportMappingProfile,
    ImportSourceType,
)
from .base import ImportSourceAdapter, ImportSourceDetection

_HTML_MESSAGE_PATTERN = re.compile(
    r"<article[^>]*data-conversation=\"(?P<conversation>[^\"]+)\""
    r"[^>]*data-id=\"(?P<message_id>[^\"]*)\""
    r"[^>]*data-sender-id=\"(?P<sender_id>[^\"]+)\""
    r"[^>]*data-sender-name=\"(?P<sender_name>[^\"]*)\""
    r"[^>]*data-ts=\"(?P<timestamp>[^\"]+)\"[^>]*>(?P<body>.*?)</article>",
    re.DOTALL,
)
_HTML_ATTACHMENT_PATTERN = re.compile(
    r"<a[^>]*data-attachment-path=\"(?P<path>[^\"]+)\""
    r"[^>]*data-mime=\"(?P<mime>[^\"]*)\""
    r"[^>]*data-filename=\"(?P<filename>[^\"]*)\"[^>]*>",
    re.DOTALL,
)
_JSONL_TYPE_HEADER = "header"
_JSONL_TYPE_MEMBER = "member"
_JSONL_TYPE_MESSAGE = "message"


class WeChatImportAdapter(ImportSourceAdapter):
    """首个 source-specific adapter：WeChat 离线导出物。"""

    source_type = ImportSourceType.WECHAT

    async def detect(self, input_ref: ImportInputRef) -> ImportSourceDetection:
        payload = self._load_export(input_ref)
        conversations = [
            DetectedConversation(
                conversation_key=item["conversation_key"],
                label=item.get("label", ""),
                message_count=len(item["messages"]),
                attachment_count=sum(len(msg.get("attachments", [])) for msg in item["messages"]),
                last_message_at=self._last_message_at(item["messages"]),
                participants=sorted(
                    {
                        str(msg.get("sender_id", "")).strip()
                        for msg in item["messages"]
                        if str(msg.get("sender_id", "")).strip()
                    }
                ),
                metadata={
                    "source_label": item.get("label", ""),
                    "message_count": len(item["messages"]),
                },
            )
            for item in payload["conversations"]
        ]
        participant_counts: dict[str, int] = defaultdict(int)
        participant_labels: dict[str, str] = {}
        for conversation in payload["conversations"]:
            for message in conversation["messages"]:
                sender_id = str(message.get("sender_id", "")).strip()
                if not sender_id:
                    continue
                participant_counts[sender_id] += 1
                participant_labels.setdefault(
                    sender_id,
                    str(message.get("sender_name", "")).strip(),
                )
        participants = [
            DetectedParticipant(
                source_sender_id=sender_id,
                label=participant_labels.get(sender_id, ""),
                message_count=count,
            )
            for sender_id, count in sorted(participant_counts.items())
        ]
        return ImportSourceDetection(
            source_type=self.source_type,
            input_ref=input_ref.model_copy(update={"source_type": self.source_type}),
            detected_conversations=conversations,
            detected_participants=participants,
            attachment_roots=payload["attachment_roots"],
            warnings=payload["warnings"],
            errors=payload["errors"],
            metadata=payload["metadata"],
        )

    async def preview(
        self,
        input_ref: ImportInputRef,
        mapping: ImportMappingProfile | None = None,
    ) -> list[ImportedChatMessage]:
        return [message async for message in self.materialize(input_ref, mapping)]

    async def materialize(
        self,
        input_ref: ImportInputRef,
        mapping: ImportMappingProfile | None = None,
    ) -> AsyncIterator[ImportedChatMessage]:
        _ = mapping
        payload = self._load_export(input_ref)
        for conversation in payload["conversations"]:
            conversation_key = conversation["conversation_key"]
            label = conversation.get("label", "") or conversation_key
            for raw in conversation["messages"]:
                attachments = [
                    self._build_attachment(input_ref, item) for item in raw.get("attachments", [])
                ]
                yield ImportedChatMessage(
                    source_message_id=self._clean_text(
                        raw.get("source_message_id") or raw.get("id")
                    ),
                    source_cursor=self._clean_text(raw.get("source_cursor") or raw.get("cursor")),
                    channel="wechat_import",
                    thread_id=conversation_key,
                    sender_id=self._clean_text(raw.get("sender_id")) or "wechat:unknown",
                    sender_name=self._clean_text(raw.get("sender_name")),
                    timestamp=self._parse_timestamp(raw.get("timestamp")),
                    text=self._clean_text(raw.get("text") or raw.get("content")),
                    attachments=[item for item in attachments if item is not None],
                    metadata={
                        "source_type": self.source_type.value,
                        "conversation_key": conversation_key,
                        "conversation_label": label,
                        "account_label": str(payload["metadata"].get("account_label", "")),
                    },
                )

    def _load_export(self, input_ref: ImportInputRef) -> dict[str, Any]:
        input_path = Path(input_ref.input_path).expanduser()
        if not input_path.is_absolute():
            input_path = input_path.resolve()
        if not input_path.exists():
            raise FileNotFoundError(f"WeChat 导出物不存在: {input_path}")

        media_root = self._resolve_media_root(input_path, input_ref.media_root)
        warnings: list[str] = []
        errors: list[str] = []
        metadata: dict[str, Any] = {"input_path": str(input_path)}

        resolved_source = self._resolve_export_file(input_path, input_ref.format_hint)
        if resolved_source is None:
            raise ValueError("无法识别 WeChat 导出物格式，请提供 JSON/HTML/SQLite 导出。")
        metadata["resolved_source"] = str(resolved_source)

        if resolved_source.suffix.lower() in {".json"}:
            conversations, account_label = self._load_from_json(resolved_source)
            metadata["format"] = "json"
        elif resolved_source.suffix.lower() in {".jsonl"}:
            conversations, account_label = self._load_from_jsonl(resolved_source)
            metadata["format"] = "jsonl"
        elif resolved_source.suffix.lower() in {".html", ".htm"}:
            conversations, account_label = self._load_from_html(resolved_source)
            metadata["format"] = "html"
        else:
            conversations, account_label = self._load_from_sqlite(resolved_source)
            metadata["format"] = "sqlite"

        if media_root is not None:
            metadata["media_root"] = str(media_root)
        elif any(msg.get("attachments") for conv in conversations for msg in conv["messages"]):
            warnings.append("检测到附件引用，但未显式提供 media_root；将按输入根目录解析。")

        attachment_roots = [str(media_root)] if media_root is not None else [str(input_path.parent)]
        metadata["account_label"] = account_label
        return {
            "conversations": conversations,
            "attachment_roots": attachment_roots,
            "warnings": warnings,
            "errors": errors,
            "metadata": metadata,
        }

    def _resolve_export_file(self, input_path: Path, format_hint: str | None) -> Path | None:
        if input_path.is_file():
            return input_path

        preferred_suffixes: list[str] = []
        if format_hint:
            hint = format_hint.lower().strip().lstrip(".")
            preferred_suffixes.append(f".{hint}")
        preferred_suffixes.extend([".json", ".jsonl", ".html", ".htm", ".sqlite", ".db"])

        candidates = sorted(path for path in input_path.rglob("*") if path.is_file())
        for suffix in preferred_suffixes:
            for candidate in candidates:
                if candidate.suffix.lower() == suffix:
                    return candidate
        return candidates[0] if candidates else None

    def _resolve_media_root(self, input_path: Path, media_root: str | None) -> Path | None:
        if media_root:
            resolved = Path(media_root).expanduser()
            if not resolved.is_absolute():
                resolved = (input_path.parent / resolved).resolve()
            return resolved.resolve()
        if input_path.is_dir():
            for name in ("media", "attachments", "files"):
                candidate = input_path / name
                if candidate.exists() and candidate.is_dir():
                    return candidate.resolve()
        return None

    def _load_from_json(self, path: Path) -> tuple[list[dict[str, Any]], str]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        account_label = ""
        if isinstance(payload, dict):
            account = payload.get("account")
            if isinstance(account, dict):
                account_label = self._clean_text(account.get("label") or account.get("name"))
            if isinstance(payload.get("conversations"), list):
                return self._normalize_json_conversations(payload["conversations"]), account_label
            if isinstance(payload.get("messages"), list):
                return self._group_flat_messages(payload["messages"]), account_label
        if isinstance(payload, list):
            return self._group_flat_messages(payload), account_label
        raise ValueError(f"WeChat JSON 导出格式无法识别: {path}")

    def _load_from_html(self, path: Path) -> tuple[list[dict[str, Any]], str]:
        text = path.read_text(encoding="utf-8")
        grouped: dict[str, dict[str, Any]] = {}
        for match in _HTML_MESSAGE_PATTERN.finditer(text):
            conversation_key = (
                self._clean_text(match.group("conversation")) or "wechat:conversation"
            )
            grouped.setdefault(
                conversation_key,
                {
                    "conversation_key": conversation_key,
                    "label": conversation_key,
                    "messages": [],
                },
            )
            body = match.group("body")
            attachments = [
                {
                    "path": self._clean_text(item.group("path")),
                    "mime": self._clean_text(item.group("mime")),
                    "filename": self._clean_text(item.group("filename")),
                }
                for item in _HTML_ATTACHMENT_PATTERN.finditer(body)
            ]
            text_content = unescape(re.sub(r"<[^>]+>", " ", body))
            grouped[conversation_key]["messages"].append(
                {
                    "id": self._clean_text(match.group("message_id")),
                    "sender_id": self._clean_text(match.group("sender_id")),
                    "sender_name": self._clean_text(match.group("sender_name")),
                    "timestamp": self._clean_text(match.group("timestamp")),
                    "text": self._clean_text(text_content),
                    "attachments": attachments,
                }
            )
        if not grouped:
            raise ValueError(f"WeChat HTML 导出中未解析到消息: {path}")
        return list(grouped.values()), ""

    def _load_from_jsonl(self, path: Path) -> tuple[list[dict[str, Any]], str]:
        members: dict[str, str] = {}
        messages: list[dict[str, Any]] = []
        conversation_key = self._clean_text(path.stem) or "wechat:conversation"
        conversation_label = conversation_key
        account_label = ""

        for line_number, raw_line in enumerate(
            path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            stripped = raw_line.strip()
            if not stripped:
                continue
            row = json.loads(stripped)
            if not isinstance(row, dict):
                continue
            row_type = self._clean_text(row.get("_type"))
            if row_type == _JSONL_TYPE_HEADER:
                meta = row.get("meta")
                if isinstance(meta, dict):
                    conversation_label = (
                        self._clean_text(meta.get("name")) or conversation_label
                    )
                    account_label = (
                        self._clean_text(meta.get("ownerName"))
                        or self._clean_text(meta.get("owner"))
                        or account_label
                    )
                continue
            if row_type == _JSONL_TYPE_MEMBER:
                member_id = self._clean_text(row.get("platformId"))
                if member_id:
                    members[member_id] = self._clean_text(row.get("accountName")) or member_id
                continue
            if row_type != _JSONL_TYPE_MESSAGE:
                continue

            sender_id = self._clean_text(row.get("sender")) or "wechat:unknown"
            sender_name = (
                self._clean_text(row.get("accountName"))
                or members.get(sender_id, "")
            )
            timestamp = self._normalize_jsonl_timestamp(
                row.get("timestamp"),
                line_number=line_number,
            )
            messages.append(
                {
                    "source_message_id": (
                        self._clean_text(row.get("id"))
                        or f"jsonl:{path.stem}:{line_number}"
                    ),
                    "source_cursor": (
                        self._clean_text(row.get("cursor"))
                        or f"{timestamp}:{line_number}"
                    ),
                    "sender_id": sender_id,
                    "sender_name": sender_name,
                    "timestamp": timestamp,
                    "text": self._clean_text(row.get("content")),
                    "attachments": [],
                    "metadata": {
                        "jsonl_message_type": row.get("type"),
                    },
                }
            )

        if not messages:
            raise ValueError(f"WeChat JSONL 导出中未解析到消息: {path}")

        return (
            [
                {
                    "conversation_key": conversation_key,
                    "label": conversation_label,
                    "messages": messages,
                }
            ],
            account_label,
        )

    def _load_from_sqlite(self, path: Path) -> tuple[list[dict[str, Any]], str]:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "messages" not in tables:
                raise ValueError("WeChat SQLite snapshot 缺少 messages 表。")
            rows = conn.execute(
                """
                SELECT conversation_key, conversation_label, source_message_id, source_cursor,
                       sender_id, sender_name, timestamp, text,
                       attachment_path, attachment_mime, attachment_filename
                FROM messages
                ORDER BY timestamp ASC, source_cursor ASC, source_message_id ASC
                """
            ).fetchall()
        finally:
            conn.close()
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            conversation_key = self._clean_text(row["conversation_key"]) or "wechat:conversation"
            grouped.setdefault(
                conversation_key,
                {
                    "conversation_key": conversation_key,
                    "label": self._clean_text(row["conversation_label"]) or conversation_key,
                    "messages": [],
                },
            )
            attachments = []
            if self._clean_text(row["attachment_path"]):
                attachments.append(
                    {
                        "path": self._clean_text(row["attachment_path"]),
                        "mime": self._clean_text(row["attachment_mime"]),
                        "filename": self._clean_text(row["attachment_filename"]),
                    }
                )
            grouped[conversation_key]["messages"].append(
                {
                    "source_message_id": self._clean_text(row["source_message_id"]),
                    "source_cursor": self._clean_text(row["source_cursor"]),
                    "sender_id": self._clean_text(row["sender_id"]),
                    "sender_name": self._clean_text(row["sender_name"]),
                    "timestamp": self._clean_text(row["timestamp"]),
                    "text": self._clean_text(row["text"]),
                    "attachments": attachments,
                }
            )
        if not grouped:
            raise ValueError(f"WeChat SQLite snapshot 中未解析到消息: {path}")
        return list(grouped.values()), ""

    def _normalize_json_conversations(self, rows: list[object]) -> list[dict[str, Any]]:
        conversations: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            messages = row.get("messages")
            if not isinstance(messages, list):
                continue
            conversation_key = (
                self._clean_text(row.get("conversation_key"))
                or self._clean_text(row.get("id"))
                or "wechat:conversation"
            )
            conversations.append(
                {
                    "conversation_key": conversation_key,
                    "label": (
                        self._clean_text(row.get("label") or row.get("name")) or conversation_key
                    ),
                    "messages": [item for item in messages if isinstance(item, dict)],
                }
            )
        if not conversations:
            raise ValueError("WeChat JSON 导出缺少 conversations/messages。")
        return conversations

    def _group_flat_messages(self, rows: list[object]) -> list[dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            conversation_key = (
                self._clean_text(row.get("conversation_key")) or "wechat:conversation"
            )
            grouped.setdefault(
                conversation_key,
                {
                    "conversation_key": conversation_key,
                    "label": (self._clean_text(row.get("conversation_label")) or conversation_key),
                    "messages": [],
                },
            )
            grouped[conversation_key]["messages"].append(row)
        if not grouped:
            raise ValueError("WeChat JSON 导出中未解析到任何消息。")
        return list(grouped.values())

    def _build_attachment(
        self,
        input_ref: ImportInputRef,
        raw: dict[str, Any],
    ) -> MessageAttachment | None:
        path_value = self._clean_text(raw.get("path") or raw.get("storage_ref"))
        if not path_value:
            return None
        root = Path(input_ref.media_root).expanduser() if input_ref.media_root else None
        candidate = Path(path_value).expanduser()
        if not candidate.is_absolute():
            base = root or Path(input_ref.input_path).expanduser().parent
            candidate = (base / candidate).resolve()
        mime = (
            self._clean_text(raw.get("mime"))
            or mimetypes.guess_type(candidate.name)[0]
            or "application/octet-stream"
        )
        size = 0
        if candidate.exists() and candidate.is_file():
            size = candidate.stat().st_size
        attachment_id = self._clean_text(raw.get("id")) or f"attachment:{candidate.name}"
        return MessageAttachment(
            id=attachment_id,
            mime=mime,
            filename=self._clean_text(raw.get("filename")) or candidate.name,
            size=size,
            storage_ref=str(candidate),
        )

    @staticmethod
    def _parse_timestamp(value: object) -> datetime:
        rendered = str(value or "").strip()
        if not rendered:
            return datetime.now(tz=UTC)
        candidate = rendered.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(candidate)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    @staticmethod
    def _normalize_jsonl_timestamp(
        value: object,
        *,
        line_number: int,
    ) -> str:
        rendered = str(value or "").strip()
        if not rendered:
            fallback = datetime(1970, 1, 1, tzinfo=UTC)
            return fallback.isoformat()
        try:
            seconds = int(rendered)
        except ValueError:
            try:
                return WeChatImportAdapter._parse_timestamp(rendered).isoformat()
            except ValueError as exc:
                raise ValueError(
                    f"WeChat JSONL 时间戳无法解析（line {line_number}）: {rendered}"
                ) from exc
        return datetime.fromtimestamp(seconds, tz=UTC).isoformat()

    @staticmethod
    def _last_message_at(messages: list[dict[str, Any]]) -> datetime | None:
        if not messages:
            return None
        timestamps = []
        for item in messages:
            try:
                timestamps.append(WeChatImportAdapter._parse_timestamp(item.get("timestamp")))
            except Exception:
                continue
        return max(timestamps) if timestamps else None

    @staticmethod
    def _clean_text(value: object) -> str:
        return str(value or "").strip()
