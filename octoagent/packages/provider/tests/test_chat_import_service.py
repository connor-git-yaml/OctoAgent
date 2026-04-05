from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from octoagent.core.store import create_store_group
from octoagent.memory import (
    SqliteChatImportStore,
    SqliteMemoryStore,
    init_chat_import_db,
    init_memory_db,
)
from octoagent.provider.dx.chat_import_service import ChatImportService
from octoagent.provider.dx.memory_runtime_service import MemoryRuntimeService


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows),
        encoding="utf-8",
    )


def _build_rows(now: datetime, *, include_hint: bool = False) -> list[dict]:
    rows = [
        {
            "source_message_id": "m1",
            "source_cursor": "c1",
            "channel": "wechat_import",
            "thread_id": "project-alpha",
            "sender_id": "alice",
            "sender_name": "Alice",
            "timestamp": now.isoformat(),
            "text": "Project Alpha kickoff complete",
        },
        {
            "source_message_id": "m2",
            "source_cursor": "c2",
            "channel": "wechat_import",
            "thread_id": "project-alpha",
            "sender_id": "bob",
            "sender_name": "Bob",
            "timestamp": (now + timedelta(minutes=1)).isoformat(),
            "text": "Project Alpha now in development",
        },
    ]
    if include_hint:
        rows[1]["fact_hints"] = [
            {
                "subject_key": "project.alpha.status",
                "content": "development",
                "confidence": 0.9,
            }
        ]
    return rows


def _build_rows_with_attachment(now: datetime, attachment_path: Path) -> list[dict]:
    return [
        {
            "source_message_id": "m1",
            "source_cursor": "c1",
            "channel": "wechat_import",
            "thread_id": "project-alpha",
            "sender_id": "alice",
            "sender_name": "Alice",
            "timestamp": now.isoformat(),
            "text": "binary attachment incoming",
            "attachments": [
                {
                    "id": "attachment-1",
                    "mime": "image/jpeg",
                    "filename": attachment_path.name,
                    "size": attachment_path.stat().st_size,
                    "storage_ref": str(attachment_path),
                }
            ],
        }
    ]


@pytest.mark.asyncio
async def test_chat_import_dry_run_has_no_side_effects(tmp_path: Path) -> None:
    input_path = tmp_path / "messages.jsonl"
    _write_jsonl(input_path, _build_rows(datetime.now(tz=UTC)))

    service = ChatImportService(tmp_path)
    report = await service.import_chats(input_path=input_path, dry_run=True)

    assert report.dry_run is True
    assert report.summary.imported_count == 2
    assert not (tmp_path / "data").exists()


@pytest.mark.asyncio
async def test_chat_import_persists_artifact_fragment_and_fact_commit(tmp_path: Path) -> None:
    input_path = tmp_path / "messages.jsonl"
    _write_jsonl(input_path, _build_rows(datetime.now(tz=UTC), include_hint=True))

    service = ChatImportService(tmp_path)
    report = await service.import_chats(input_path=input_path)

    assert report.summary.imported_count == 2
    assert report.summary.window_count == 1
    assert report.summary.proposal_count == 1
    assert report.summary.committed_count == 1

    store_group = await create_store_group(
        str(tmp_path / "data" / "sqlite" / "octoagent.db"),
        tmp_path / "data" / "artifacts",
    )
    try:
        await init_memory_db(store_group.conn)
        await init_chat_import_db(store_group.conn)
        import_store = SqliteChatImportStore(store_group.conn)
        memory_store = SqliteMemoryStore(store_group.conn)
        windows = await import_store.list_windows_for_batch(report.batch_id)
        audit_events = await store_group.event_store.get_events_for_task("ops-chat-import")
        current = await memory_store.get_current_sor(report.scope_id, "project.alpha.status")
        artifacts = await store_group.artifact_store.list_artifacts_for_task("ops-chat-import")

        assert len(windows) == 1
        assert windows[0].summary_fragment_id is not None
        assert current is not None
        assert current.content == "development"
        assert [event.type for event in audit_events][-3:] == [
            "CHAT_IMPORT_STARTED",
            "ARTIFACT_CREATED",
            "CHAT_IMPORT_COMPLETED",
        ]
        assert len(artifacts) == 1
    finally:
        await store_group.conn.close()


@pytest.mark.asyncio
async def test_chat_import_uses_project_scoped_memory_runtime_service(
    tmp_path: Path,
    monkeypatch,
) -> None:
    input_path = tmp_path / "messages.jsonl"
    _write_jsonl(input_path, _build_rows(datetime.now(tz=UTC), include_hint=True))

    calls: list[dict[str, str]] = []
    original = MemoryRuntimeService.memory_service_for_scope

    async def tracking_memory_service_for_scope(self, *, project):
        calls.append(
            {
                "project_id": project.project_id if project is not None else "",
            }
        )
        return await original(self, project=project)

    monkeypatch.setattr(
        MemoryRuntimeService,
        "memory_service_for_scope",
        tracking_memory_service_for_scope,
    )

    service = ChatImportService(tmp_path)
    report = await service.import_chats(input_path=input_path)

    assert report.summary.imported_count == 2
    assert calls
    assert calls[0]["project_id"] == "project-default"


@pytest.mark.asyncio
async def test_chat_import_resume_only_imports_new_messages(tmp_path: Path) -> None:
    now = datetime.now(tz=UTC)
    initial_input = tmp_path / "initial.jsonl"
    _write_jsonl(initial_input, _build_rows(now))
    service = ChatImportService(tmp_path)
    source_id = "wechat-project-alpha"
    first = await service.import_chats(input_path=initial_input, source_id=source_id)
    assert first.summary.imported_count == 2

    resume_input = tmp_path / "resume.jsonl"
    rows = _build_rows(now)
    rows.append(
        {
            "source_message_id": "m3",
            "source_cursor": "c3",
            "channel": "wechat_import",
            "thread_id": "project-alpha",
            "sender_id": "alice",
            "timestamp": (now + timedelta(minutes=2)).isoformat(),
            "text": "one more update",
        }
    )
    _write_jsonl(resume_input, rows)

    resumed = await service.import_chats(
        input_path=resume_input,
        source_id=source_id,
        resume=True,
    )

    assert resumed.summary.imported_count == 1
    assert resumed.summary.duplicate_count == 0

    repeated = await service.import_chats(
        input_path=initial_input,
        source_id=source_id,
    )
    assert repeated.summary.imported_count == 0
    assert repeated.summary.duplicate_count == 2


@pytest.mark.asyncio
async def test_chat_import_preserves_small_binary_attachment_bytes(tmp_path: Path) -> None:
    attachment_path = tmp_path / "tiny.jpg"
    attachment_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01"
    attachment_path.write_bytes(attachment_bytes)

    input_path = tmp_path / "messages.jsonl"
    _write_jsonl(
        input_path,
        _build_rows_with_attachment(datetime.now(tz=UTC), attachment_path),
    )

    service = ChatImportService(tmp_path)
    report = await service.import_chats(input_path=input_path)

    assert report.summary.attachment_count == 1
    assert report.summary.attachment_artifact_count == 1

    store_group = await create_store_group(
        str(tmp_path / "data" / "sqlite" / "octoagent.db"),
        tmp_path / "data" / "artifacts",
    )
    try:
        artifacts = await store_group.artifact_store.list_artifacts_for_task("ops-chat-import")
        attachment_artifact = next(item for item in artifacts if item.name == attachment_path.name)
        assert attachment_artifact.storage_ref is not None
        restored = await store_group.artifact_store.get_artifact_content(
            attachment_artifact.artifact_id
        )
        assert restored == attachment_bytes
    finally:
        await store_group.conn.close()


class _FailOnceChatImportService(ChatImportService):
    def __init__(self, project_root: Path) -> None:
        super().__init__(project_root)
        self._has_failed = False

    async def _process_fact_hints(self, **kwargs):  # type: ignore[override]
        result = await super()._process_fact_hints(**kwargs)
        if not self._has_failed:
            self._has_failed = True
            raise RuntimeError("模拟窗口提交前失败")
        return result


@pytest.mark.asyncio
async def test_chat_import_retry_after_mid_window_failure_does_not_duplicate(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "messages.jsonl"
    _write_jsonl(input_path, _build_rows(datetime.now(tz=UTC)))

    failing_service = _FailOnceChatImportService(tmp_path)
    with pytest.raises(RuntimeError, match="模拟窗口提交前失败"):
        await failing_service.import_chats(input_path=input_path)

    service = ChatImportService(tmp_path)
    report = await service.import_chats(input_path=input_path)
    assert report.summary.imported_count == 2

    store_group = await create_store_group(
        str(tmp_path / "data" / "sqlite" / "octoagent.db"),
        tmp_path / "data" / "artifacts",
    )
    try:
        await init_memory_db(store_group.conn)
        await init_chat_import_db(store_group.conn)
        import_store = SqliteChatImportStore(store_group.conn)
        artifacts = await store_group.artifact_store.list_artifacts_for_task("ops-chat-import")
        windows = await import_store.list_windows_for_batch(report.batch_id)
        assert len(artifacts) == 1
        assert len(windows) == 1
    finally:
        await store_group.conn.close()
