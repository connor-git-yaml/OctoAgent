"""Feature 021: Chat import CLI 编排服务。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
from octoagent.core.models import (
    ActorType,
    Artifact,
    ArtifactCreatedPayload,
    ArtifactPart,
    ChatImportLifecyclePayload,
    Event,
    EventCausality,
    EventType,
    PartType,
    RequesterInfo,
    Task,
    TaskCreatedPayload,
)
from octoagent.core.store import StoreGroup, create_store_group
from octoagent.core.store.transaction import create_task_with_initial_events
from octoagent.memory import (
    ChatImportProcessor,
    EvidenceRef,
    FragmentRecord,
    ImportBatch,
    ImportCursor,
    ImportDedupeEntry,
    ImportFactDisposition,
    ImportReport,
    ImportSourceFormat,
    ImportStatus,
    ImportWindow,
    MemoryPartition,
    MemoryService,
    SqliteChatImportStore,
    SqliteMemoryStore,
    WriteAction,
    derive_import_source_id,
    init_chat_import_db,
    init_memory_db,
    verify_chat_import_tables,
)
from ulid import ULID

from .backup_service import resolve_artifacts_dir, resolve_db_path, resolve_project_root
from .project_migration import ProjectWorkspaceMigrationService

_AUDIT_TASK_ID = "ops-chat-import"
_AUDIT_TRACE_ID = "trace-ops-chat-import"


class _NullChatImportState:
    """dry-run 且无持久化状态时的空读存储。"""

    async def get_cursor(self, source_id: str, scope_id: str) -> ImportCursor | None:
        _ = source_id, scope_id
        return None

    async def has_dedupe_entry(self, source_id: str, scope_id: str, message_key: str) -> bool:
        _ = source_id, scope_id, message_key
        return False


class ChatImportService:
    """对 project root 暴露的聊天导入服务。"""

    def __init__(
        self,
        project_root: Path,
        *,
        store_group: StoreGroup | None = None,
        processor: ChatImportProcessor | None = None,
    ) -> None:
        self._root = resolve_project_root(project_root).resolve()
        self._db_path = resolve_db_path(self._root)
        self._artifacts_dir = resolve_artifacts_dir(self._root)
        self._store_group = store_group
        self._processor = processor or ChatImportProcessor()
        self._project_migration_ensured = False

    async def import_chats(
        self,
        *,
        input_path: str | Path,
        source_format: str = ImportSourceFormat.NORMALIZED_JSONL.value,
        source_id: str | None = None,
        channel: str | None = None,
        thread_id: str | None = None,
        dry_run: bool = False,
        resume: bool = False,
    ) -> ImportReport:
        normalized_format = ImportSourceFormat(source_format)
        resolved_input_path = Path(input_path).expanduser()
        if not resolved_input_path.is_absolute():
            resolved_input_path = (self._root / resolved_input_path).resolve()
        else:
            resolved_input_path = resolved_input_path.resolve()

        resolved_source_id = source_id or derive_import_source_id(resolved_input_path)
        messages = self._processor.load_messages(
            resolved_input_path,
            source_format=normalized_format,
            channel_override=channel,
            thread_override=thread_id,
        )

        if dry_run:
            prepared = await self._prepare_dry_run_import(
                source_id=resolved_source_id,
                messages=messages,
                resume=resume,
            )
            return self._processor.build_report(
                batch_id=f"dry-run:{resolved_source_id}",
                source_id=resolved_source_id,
                scope_id=prepared.scope_id,
                dry_run=True,
                imported_count=len(prepared.new_messages),
                duplicate_count=prepared.duplicate_count,
                skipped_count=prepared.skipped_count,
                window_count=len(prepared.windows),
                proposal_count=0,
                committed_count=0,
                cursor=prepared.projected_cursor,
                artifact_refs=[],
                warnings=prepared.warnings,
                errors=[],
            )

        async with self._store_group_scope() as store_group:
            await init_memory_db(store_group.conn)
            await init_chat_import_db(store_group.conn)
            import_store = SqliteChatImportStore(store_group.conn)
            memory_store = SqliteMemoryStore(store_group.conn)
            memory_service = MemoryService(store_group.conn, store=memory_store)

            prepared = await self._processor.prepare_import(
                store=import_store,
                source_id=resolved_source_id,
                messages=messages,
                resume=resume,
            )

            return await self._run_persistent_import(
                store_group=store_group,
                import_store=import_store,
                memory_store=memory_store,
                memory_service=memory_service,
                source_format=normalized_format,
                source_id=resolved_source_id,
                input_path=resolved_input_path,
                prepared=prepared,
            )

    async def _run_persistent_import(
        self,
        *,
        store_group: StoreGroup,
        import_store: SqliteChatImportStore,
        memory_store: SqliteMemoryStore,
        memory_service: MemoryService,
        source_format: ImportSourceFormat,
        source_id: str,
        input_path: str | Path,
        prepared,
    ) -> ImportReport:
        batch = self._processor.create_batch(
            source_id=source_id,
            source_format=source_format,
            scope_id=prepared.scope_id,
            channel=prepared.channel,
            thread_id=prepared.thread_id,
            input_path=input_path,
        )
        await import_store.create_batch(batch)
        await store_group.conn.commit()
        await self._append_lifecycle_event(
            store_group,
            event_type=EventType.CHAT_IMPORT_STARTED,
            payload=ChatImportLifecyclePayload(
                batch_id=batch.batch_id,
                source_id=source_id,
                scope_id=prepared.scope_id,
                imported_count=0,
                duplicate_count=prepared.duplicate_count,
                window_count=len(prepared.windows),
                message="开始导入聊天历史。",
            ),
            idempotency_key=f"chat-import:{batch.batch_id}:started",
        )

        warnings = list(prepared.warnings)
        errors: list[str] = []
        artifact_refs: list[str] = []
        proposal_count = 0
        committed_count = 0
        imported_count = 0
        cursor: ImportCursor | None = None

        try:
            for index, window_draft in enumerate(prepared.windows, start=1):
                artifact = await self._write_window_artifact(
                    store_group=store_group,
                    batch=batch,
                    index=index,
                    window_messages=window_draft.messages,
                )

                fragment = FragmentRecord(
                    fragment_id=str(ULID()),
                    scope_id=prepared.scope_id,
                    partition=MemoryPartition.CHAT,
                    content=window_draft.summary_text,
                    metadata={
                        "source": "chat_import_summary",
                        "batch_id": batch.batch_id,
                        "window_index": index,
                        "channel": prepared.channel,
                        "thread_id": prepared.thread_id,
                    },
                    evidence_refs=[
                        EvidenceRef(
                            ref_id=artifact.artifact_id,
                            ref_type="artifact",
                            snippet=window_draft.summary_text[:120],
                        )
                    ],
                    created_at=datetime.now(tz=UTC),
                )
                await memory_store.append_fragment(fragment)

                proposal_ids, disposition, new_warnings, window_proposals, window_committed = (
                    await self._process_fact_hints(
                        memory_service=memory_service,
                        memory_store=memory_store,
                        scope_id=prepared.scope_id,
                        batch=batch,
                        artifact_id=artifact.artifact_id,
                        messages=window_draft.messages,
                    )
                )
                warnings.extend(new_warnings)
                proposal_count += window_proposals
                committed_count += window_committed

                window = ImportWindow(
                    window_id=str(ULID()),
                    batch_id=batch.batch_id,
                    scope_id=prepared.scope_id,
                    first_ts=window_draft.first_ts,
                    last_ts=window_draft.last_ts,
                    message_count=len(window_draft.messages),
                    artifact_id=artifact.artifact_id,
                    summary_fragment_id=fragment.fragment_id,
                    fact_disposition=disposition,
                    proposal_ids=proposal_ids,
                )
                await import_store.save_window(window)
                for message in window_draft.messages:
                    inserted = await import_store.insert_dedupe_entry(
                        ImportDedupeEntry(
                            dedupe_id=str(ULID()),
                            source_id=source_id,
                            scope_id=prepared.scope_id,
                            message_key=self._processor.build_message_key(message),
                            source_message_id=message.source_message_id,
                            imported_at=datetime.now(tz=UTC),
                            batch_id=batch.batch_id,
                        )
                    )
                    if not inserted:
                        message_key = self._processor.build_message_key(message)
                        raise RuntimeError(f"chat import dedupe 冲突: {message_key}")
                await store_group.conn.commit()
                imported_count += len(window_draft.messages)
                artifact_refs.append(artifact.artifact_id)
                await self._append_artifact_created_event(
                    store_group,
                    artifact,
                    batch.batch_id,
                    index,
                )

            cursor = self._processor.build_cursor(
                source_id=source_id,
                scope_id=prepared.scope_id,
                new_messages=prepared.new_messages,
                imported_count=imported_count,
                duplicate_count=prepared.duplicate_count,
                fallback=prepared.existing_cursor,
            )
            if cursor is not None:
                await import_store.upsert_cursor(cursor)

            report = self._processor.build_report(
                batch_id=batch.batch_id,
                source_id=source_id,
                scope_id=prepared.scope_id,
                dry_run=False,
                imported_count=imported_count,
                duplicate_count=prepared.duplicate_count,
                skipped_count=prepared.skipped_count,
                window_count=len(prepared.windows),
                proposal_count=proposal_count,
                committed_count=committed_count,
                cursor=cursor,
                artifact_refs=artifact_refs,
                warnings=warnings,
                errors=errors,
            )
            await import_store.save_report(report)
            batch = batch.model_copy(
                update={
                    "completed_at": datetime.now(tz=UTC),
                    "status": ImportStatus.COMPLETED,
                    "report_id": report.report_id,
                }
            )
            await import_store.update_batch(batch)
            await store_group.conn.commit()
            await self._append_lifecycle_event(
                store_group,
                event_type=EventType.CHAT_IMPORT_COMPLETED,
                payload=ChatImportLifecyclePayload(
                    batch_id=batch.batch_id,
                    source_id=source_id,
                    scope_id=prepared.scope_id,
                    imported_count=imported_count,
                    duplicate_count=prepared.duplicate_count,
                    window_count=len(prepared.windows),
                    report_id=report.report_id,
                    message="聊天导入完成。",
                ),
                idempotency_key=f"chat-import:{batch.batch_id}:completed",
            )
            return report
        except Exception as exc:
            await store_group.conn.rollback()
            errors.append(str(exc))
            report = self._processor.build_report(
                batch_id=batch.batch_id,
                source_id=source_id,
                scope_id=prepared.scope_id,
                dry_run=False,
                imported_count=imported_count,
                duplicate_count=prepared.duplicate_count,
                skipped_count=prepared.skipped_count,
                window_count=len(prepared.windows),
                proposal_count=proposal_count,
                committed_count=committed_count,
                cursor=cursor,
                artifact_refs=artifact_refs,
                warnings=warnings,
                errors=errors,
            )
            await import_store.save_report(report)
            failed_batch = batch.model_copy(
                update={
                    "completed_at": datetime.now(tz=UTC),
                    "status": ImportStatus.FAILED,
                    "error_message": str(exc),
                    "report_id": report.report_id,
                }
            )
            await import_store.update_batch(failed_batch)
            await store_group.conn.commit()
            await self._append_lifecycle_event(
                store_group,
                event_type=EventType.CHAT_IMPORT_FAILED,
                payload=ChatImportLifecyclePayload(
                    batch_id=batch.batch_id,
                    source_id=source_id,
                    scope_id=prepared.scope_id,
                    imported_count=imported_count,
                    duplicate_count=prepared.duplicate_count,
                    window_count=len(prepared.windows),
                    report_id=report.report_id,
                    message=str(exc),
                ),
                idempotency_key=f"chat-import:{batch.batch_id}:failed",
            )
            raise

    async def _write_window_artifact(
        self,
        *,
        store_group: StoreGroup,
        batch: ImportBatch,
        index: int,
        window_messages,
    ) -> Artifact:
        payload = {
            "batch_id": batch.batch_id,
            "source_id": batch.source_id,
            "scope_id": batch.scope_id,
            "channel": batch.channel,
            "thread_id": batch.thread_id,
            "window_index": index,
            "messages": [message.model_dump(mode="json") for message in window_messages],
        }
        content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        artifact = Artifact(
            artifact_id=str(ULID()),
            task_id=_AUDIT_TASK_ID,
            ts=datetime.now(tz=UTC),
            name=f"chat-import-window-{index:03d}.json",
            description=f"Chat import raw window #{index} for {batch.scope_id}",
            parts=[ArtifactPart(type=PartType.JSON, mime="application/json")],
            size=0,
            hash="",
        )
        await store_group.artifact_store.put_artifact(artifact, content=content)
        return artifact

    async def _process_fact_hints(
        self,
        *,
        memory_service: MemoryService,
        memory_store: SqliteMemoryStore,
        scope_id: str,
        batch: ImportBatch,
        artifact_id: str,
        messages,
    ) -> tuple[list[str], ImportFactDisposition, list[str], int, int]:
        warnings: list[str] = []
        proposal_ids: list[str] = []
        proposal_count = 0
        committed_count = 0
        hints = []
        for message in messages:
            hints.extend(message.fact_hints)
        if not hints:
            return (
                proposal_ids,
                ImportFactDisposition.SKIPPED,
                warnings,
                proposal_count,
                committed_count,
            )

        disposition = ImportFactDisposition.FRAGMENT_ONLY
        evidence = [EvidenceRef(ref_id=artifact_id, ref_type="artifact")]
        for hint in hints:
            current = await memory_store.get_current_sor(scope_id, hint.subject_key)
            action = WriteAction.UPDATE if current is not None else WriteAction.ADD
            proposal = await memory_service.propose_write(
                scope_id=scope_id,
                partition=hint.partition,
                action=action,
                subject_key=hint.subject_key,
                content=hint.content,
                rationale=hint.rationale or "chat import fact hint",
                confidence=hint.confidence,
                evidence_refs=evidence,
                expected_version=current.version if current is not None else None,
                is_sensitive=hint.is_sensitive,
                metadata={
                    "source": "chat_import",
                    "batch_id": batch.batch_id,
                    "channel": batch.channel,
                    "thread_id": batch.thread_id,
                },
                autocommit=False,
            )
            proposal_ids.append(proposal.proposal_id)
            proposal_count += 1
            validation = await memory_service.validate_proposal(
                proposal.proposal_id,
                autocommit=False,
            )
            if not validation.accepted:
                warnings.append(
                    f"fact_hints {hint.subject_key} 验证失败：{'；'.join(validation.errors)}"
                )
                continue
            await memory_service.commit_memory(
                proposal.proposal_id,
                autocommit=False,
            )
            committed_count += 1
            disposition = ImportFactDisposition.PROPOSED

        return proposal_ids, disposition, warnings, proposal_count, committed_count

    async def _append_artifact_created_event(
        self,
        store_group: StoreGroup,
        artifact: Artifact,
        batch_id: str,
        index: int,
    ) -> None:
        await self._append_task_event(
            store_group,
            event_type=EventType.ARTIFACT_CREATED,
            payload=ArtifactCreatedPayload(
                artifact_id=artifact.artifact_id,
                name=artifact.name,
                size=artifact.size,
                part_count=len(artifact.parts),
                source="chat_import_window",
            ).model_dump(mode="json"),
            idempotency_key=f"chat-import:{batch_id}:artifact:{index}",
        )

    async def _append_lifecycle_event(
        self,
        store_group: StoreGroup,
        *,
        event_type: EventType,
        payload: ChatImportLifecyclePayload,
        idempotency_key: str,
    ) -> None:
        await self._append_task_event(
            store_group,
            event_type=event_type,
            payload=payload.model_dump(mode="json"),
            idempotency_key=idempotency_key,
        )

    async def _append_task_event(
        self,
        store_group: StoreGroup,
        *,
        event_type: EventType,
        payload: dict,
        idempotency_key: str,
    ) -> None:
        await self._ensure_audit_task(store_group)
        task_seq = await store_group.event_store.get_next_task_seq(_AUDIT_TASK_ID)
        event = Event(
            event_id=str(ULID()),
            task_id=_AUDIT_TASK_ID,
            task_seq=task_seq,
            ts=datetime.now(tz=UTC),
            type=event_type,
            actor=ActorType.SYSTEM,
            payload=payload,
            trace_id=_AUDIT_TRACE_ID,
            causality=EventCausality(idempotency_key=idempotency_key),
        )
        await store_group.event_store.append_event_committed(event)

    async def _ensure_audit_task(self, store_group: StoreGroup) -> None:
        existing = await store_group.task_store.get_task(_AUDIT_TASK_ID)
        if existing is not None:
            return

        now = datetime.now(tz=UTC)
        task = Task(
            task_id=_AUDIT_TASK_ID,
            created_at=now,
            updated_at=now,
            title="系统运维审计（聊天导入）",
            thread_id="ops-chat-import",
            scope_id="ops/chat-import",
            requester=RequesterInfo(channel="system", sender_id="system"),
            trace_id=_AUDIT_TRACE_ID,
        )
        created_event = Event(
            event_id=str(ULID()),
            task_id=_AUDIT_TASK_ID,
            task_seq=1,
            ts=now,
            type=EventType.TASK_CREATED,
            actor=ActorType.SYSTEM,
            payload=TaskCreatedPayload(
                title=task.title,
                thread_id=task.thread_id,
                scope_id=task.scope_id,
                channel=task.requester.channel,
                sender_id=task.requester.sender_id,
                risk_level=task.risk_level.value,
            ).model_dump(mode="json"),
            trace_id=_AUDIT_TRACE_ID,
            causality=EventCausality(idempotency_key="ops-chat-import:create"),
        )
        try:
            await create_task_with_initial_events(
                store_group.conn,
                store_group.task_store,
                store_group.event_store,
                task,
                [created_event],
            )
        except aiosqlite.IntegrityError:
            await store_group.conn.rollback()

    @asynccontextmanager
    async def _store_group_scope(self) -> AsyncIterator[StoreGroup]:
        if self._store_group is not None:
            await self._ensure_project_migration(self._store_group)
            yield self._store_group
            return

        store_group = await create_store_group(str(self._db_path), self._artifacts_dir)
        try:
            await self._ensure_project_migration(store_group)
            yield store_group
        finally:
            await store_group.conn.close()

    async def _ensure_project_migration(self, store_group: StoreGroup) -> None:
        if self._project_migration_ensured:
            return
        service = ProjectWorkspaceMigrationService(
            project_root=self._root,
            store_group=store_group,
        )
        await service.ensure_default_project()
        self._project_migration_ensured = True

    async def _prepare_dry_run_import(
        self,
        *,
        source_id: str,
        messages,
        resume: bool,
    ):
        if self._store_group is not None:
            state = await self._resolve_import_state(self._store_group.conn)
            return await self._processor.prepare_import(
                store=state,
                source_id=source_id,
                messages=messages,
                resume=resume,
            )

        if not self._db_path.exists():
            return await self._processor.prepare_import(
                store=_NullChatImportState(),
                source_id=source_id,
                messages=messages,
                resume=resume,
            )

        conn = await aiosqlite.connect(f"file:{self._db_path}?mode=ro", uri=True)
        conn.row_factory = aiosqlite.Row
        try:
            state = await self._resolve_import_state(conn)
            return await self._processor.prepare_import(
                store=state,
                source_id=source_id,
                messages=messages,
                resume=resume,
            )
        finally:
            await conn.close()

    async def _resolve_import_state(
        self,
        conn: aiosqlite.Connection,
    ) -> SqliteChatImportStore | _NullChatImportState:
        if await verify_chat_import_tables(conn):
            return SqliteChatImportStore(conn)
        return _NullChatImportState()
