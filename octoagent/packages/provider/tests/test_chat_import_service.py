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
