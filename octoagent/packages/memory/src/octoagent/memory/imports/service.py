"""Chat import 领域服务。"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from ulid import ULID

from .models import (
    ImportBatch,
    ImportCursor,
    ImportedChatMessage,
    ImportReport,
    ImportSourceFormat,
    ImportStatus,
    ImportSummary,
)

_DEFAULT_WINDOW_SIZE = 10


@dataclass(slots=True)
class ImportWindowDraft:
    """导入窗口草案。"""

    messages: list[ImportedChatMessage]
    summary_text: str
    first_ts: datetime
    last_ts: datetime


@dataclass(slots=True)
class PreparedImport:
    """准备阶段产物。"""

    source_id: str
    scope_id: str
    channel: str
    thread_id: str
    messages: list[ImportedChatMessage]
    new_messages: list[ImportedChatMessage]
    windows: list[ImportWindowDraft]
    duplicate_count: int
    skipped_count: int
    warnings: list[str]
    existing_cursor: ImportCursor | None
    projected_cursor: ImportCursor | None


class ImportStateReader(Protocol):
    """导入准备阶段需要的最小只读状态接口。"""

    async def get_cursor(self, source_id: str, scope_id: str) -> ImportCursor | None: ...

    async def has_dedupe_entry(self, source_id: str, scope_id: str, message_key: str) -> bool: ...


class ChatImportProcessor:
    """导入解析、去重与报告生成。"""

    def __init__(self, *, window_size: int = _DEFAULT_WINDOW_SIZE) -> None:
        self._window_size = window_size

    def load_messages(
        self,
        input_path: str | Path,
        *,
        source_format: ImportSourceFormat | str,
        channel_override: str | None = None,
        thread_override: str | None = None,
    ) -> list[ImportedChatMessage]:
        path = Path(input_path).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"输入文件不存在: {path}")
        source_format = ImportSourceFormat(source_format)
        if source_format is not ImportSourceFormat.NORMALIZED_JSONL:
            raise ValueError(f"暂不支持导入格式: {source_format}")

        messages: list[ImportedChatMessage] = []
        for line_no, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"第 {line_no} 行不是合法 JSON") from exc

            try:
                message = ImportedChatMessage.model_validate(payload)
            except Exception as exc:
                raise ValueError(f"第 {line_no} 行 schema 无效: {exc}") from exc

            if channel_override:
                message = message.model_copy(update={"channel": channel_override})
            if thread_override:
                message = message.model_copy(update={"thread_id": thread_override})
            messages.append(message)

        if not messages:
            raise ValueError("输入文件中没有可导入消息")

        channels = {item.channel for item in messages}
        threads = {item.thread_id for item in messages}
        if len(channels) != 1 or len(threads) != 1:
            raise ValueError("021 MVP 一次仅支持单一 channel/thread，请拆分文件或使用 override")

        messages.sort(key=self._message_sort_key)
        return messages

    async def prepare_import(
        self,
        *,
        store: ImportStateReader,
        source_id: str,
        messages: list[ImportedChatMessage],
        resume: bool,
    ) -> PreparedImport:
        channel = messages[0].channel
        thread_id = messages[0].thread_id
        scope_id = self.derive_scope_id(channel=channel, thread_id=thread_id)
        existing_cursor = await store.get_cursor(source_id, scope_id)
        warnings: list[str] = []

        candidate_messages = messages
        if resume:
            candidate_messages = self._apply_resume_boundary(messages, existing_cursor, warnings)

        new_messages: list[ImportedChatMessage] = []
        duplicate_count = 0
        seen_message_keys: set[str] = set()
        for message in candidate_messages:
            message_key = self.build_message_key(message)
            if message_key in seen_message_keys:
                duplicate_count += 1
                continue
            if await store.has_dedupe_entry(source_id, scope_id, message_key):
                duplicate_count += 1
                continue
            seen_message_keys.add(message_key)
            new_messages.append(message)

        windows = self.build_windows(new_messages)
        projected_cursor = self.build_cursor(
            source_id=source_id,
            scope_id=scope_id,
            new_messages=new_messages,
            imported_count=len(new_messages),
            duplicate_count=duplicate_count,
            fallback=existing_cursor,
        )
        return PreparedImport(
            source_id=source_id,
            scope_id=scope_id,
            channel=channel,
            thread_id=thread_id,
            messages=messages,
            new_messages=new_messages,
            windows=windows,
            duplicate_count=duplicate_count,
            skipped_count=0,
            warnings=warnings,
            existing_cursor=existing_cursor,
            projected_cursor=projected_cursor,
        )

    def create_batch(
        self,
        *,
        source_id: str,
        source_format: ImportSourceFormat,
        scope_id: str,
        channel: str,
        thread_id: str,
        input_path: str | Path,
    ) -> ImportBatch:
        return ImportBatch(
            batch_id=str(ULID()),
            source_id=source_id,
            source_format=source_format,
            scope_id=scope_id,
            channel=channel,
            thread_id=thread_id,
            input_path=str(Path(input_path).expanduser().resolve()),
            started_at=datetime.now(tz=UTC),
            status=ImportStatus.RUNNING,
        )

    def build_report(
        self,
        *,
        batch_id: str,
        source_id: str,
        scope_id: str,
        dry_run: bool,
        imported_count: int,
        duplicate_count: int,
        skipped_count: int,
        window_count: int,
        proposal_count: int,
        committed_count: int,
        cursor: ImportCursor | None,
        artifact_refs: list[str],
        warnings: list[str],
        errors: list[str],
    ) -> ImportReport:
        summary = ImportSummary(
            imported_count=imported_count,
            duplicate_count=duplicate_count,
            skipped_count=skipped_count,
            window_count=window_count,
            proposal_count=proposal_count,
            committed_count=committed_count,
            warning_count=len(warnings),
        )
        next_actions: list[str] = []
        if dry_run:
            if imported_count > 0:
                next_actions.append("移除 --dry-run 后执行真实导入。")
            else:
                next_actions.append("当前没有可导入的新消息。")
            next_actions.append("如需继续增量导入，可使用 --resume。")
        else:
            next_actions.append("可再次执行 --resume 继续增量导入。")
            if errors:
                next_actions.append("先检查 warnings/errors，再决定是否重跑该 source。")

        return ImportReport(
            report_id=str(ULID()),
            batch_id=batch_id,
            source_id=source_id,
            scope_id=scope_id,
            dry_run=dry_run,
            created_at=datetime.now(tz=UTC),
            summary=summary,
            cursor=cursor,
            artifact_refs=artifact_refs,
            warnings=warnings,
            errors=errors,
            next_actions=next_actions,
        )

    def build_windows(self, messages: list[ImportedChatMessage]) -> list[ImportWindowDraft]:
        windows: list[ImportWindowDraft] = []
        for start in range(0, len(messages), self._window_size):
            chunk = messages[start : start + self._window_size]
            if not chunk:
                continue
            windows.append(
                ImportWindowDraft(
                    messages=chunk,
                    summary_text=self.build_window_summary(chunk),
                    first_ts=chunk[0].timestamp,
                    last_ts=chunk[-1].timestamp,
                )
            )
        return windows

    def build_cursor(
        self,
        *,
        source_id: str,
        scope_id: str,
        new_messages: list[ImportedChatMessage],
        imported_count: int,
        duplicate_count: int,
        fallback: ImportCursor | None,
    ) -> ImportCursor | None:
        if not new_messages:
            return fallback
        last_message = new_messages[-1]
        cursor_value = last_message.source_cursor or ""
        if not cursor_value and fallback is not None:
            cursor_value = fallback.cursor_value
        return ImportCursor(
            source_id=source_id,
            scope_id=scope_id,
            cursor_value=cursor_value,
            last_message_ts=last_message.timestamp,
            last_message_key=self.build_message_key(last_message),
            imported_count=imported_count,
            duplicate_count=duplicate_count,
            updated_at=datetime.now(tz=UTC),
        )

    @staticmethod
    def derive_scope_id(*, channel: str, thread_id: str) -> str:
        return f"chat:{channel}:{thread_id}"

    @staticmethod
    def _message_sort_key(message: ImportedChatMessage) -> tuple[str, str, str, str]:
        return (
            message.timestamp.astimezone(UTC).isoformat(),
            message.source_cursor or "",
            message.source_message_id or "",
            message.sender_id,
        )

    @staticmethod
    def build_message_key(message: ImportedChatMessage) -> str:
        if message.source_message_id:
            return f"source:{message.source_message_id.strip()}"
        normalized_text = re.sub(r"\s+", " ", message.text.strip())
        base = (
            f"{message.sender_id}|"
            f"{message.timestamp.astimezone(UTC).isoformat()}|"
            f"{normalized_text}"
        )
        return f"sha256:{hashlib.sha256(base.encode('utf-8')).hexdigest()}"

    def build_window_summary(self, messages: Iterable[ImportedChatMessage]) -> str:
        message_list = list(messages)
        if not message_list:
            return "空窗口"
        speakers: list[str] = []
        for item in message_list:
            label = item.sender_name or item.sender_id
            if label not in speakers:
                speakers.append(label)
        first = message_list[0]
        last = message_list[-1]
        first_preview = self._trim_preview(first.text)
        last_preview = self._trim_preview(last.text)
        return (
            f"{first.timestamp.astimezone(UTC).isoformat()} 至 "
            f"{last.timestamp.astimezone(UTC).isoformat()} 共 {len(message_list)} 条消息；"
            f"参与者: {', '.join(speakers[:4]) or 'unknown'}；"
            f"首条: {first_preview}；末条: {last_preview}"
        )

    def _apply_resume_boundary(
        self,
        messages: list[ImportedChatMessage],
        cursor: ImportCursor | None,
        warnings: list[str],
    ) -> list[ImportedChatMessage]:
        if cursor is None or not cursor.cursor_value:
            return messages
        if not any(message.source_cursor for message in messages):
            warnings.append("输入源未提供 source_cursor，--resume 已退化为 dedupe-only。")
            return messages

        remaining: list[ImportedChatMessage] = []
        boundary_hit = False
        for message in messages:
            if not boundary_hit:
                if message.source_cursor == cursor.cursor_value:
                    boundary_hit = True
                continue
            remaining.append(message)

        if not boundary_hit:
            warnings.append("未在输入中匹配到最近 cursor，--resume 已退化为 dedupe-only。")
            return messages
        return remaining

    @staticmethod
    def _trim_preview(text: str, limit: int = 60) -> str:
        compact = re.sub(r"\s+", " ", text.strip())
        if len(compact) <= limit:
            return compact or "<empty>"
        return f"{compact[: limit - 1]}…"


def derive_import_source_id(input_path: str | Path) -> str:
    """基于输入路径生成稳定 source_id。"""

    resolved = Path(input_path).expanduser().resolve()
    slug = re.sub(r"[^a-z0-9]+", "-", resolved.stem.lower()).strip("-") or "chat-import"
    digest = hashlib.sha256(resolved.as_posix().encode("utf-8")).hexdigest()[:12]
    return f"{slug}-{digest}"
