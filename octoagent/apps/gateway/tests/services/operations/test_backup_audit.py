from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest
from octoagent.core.models import (
    BackupBundle,
    BackupLifecyclePayload,
    BackupManifest,
    BackupScope,
    Event,
    EventType,
)
from octoagent.core.store import StoreGroup, create_store_group
from octoagent.gateway.services.operations.backup_audit import BackupAuditRecorder

_ORACLE = "F151_BACKUP_AUDIT_DIRECT_DURABILITY_CONTRACT_MISSING"
_AUDIT_TASK_ID = "ops-recovery-audit"


async def _stores(tmp_path: Path, count: int = 1) -> list[StoreGroup]:
    database = tmp_path / "data" / "octoagent.db"
    artifacts = tmp_path / "artifacts"
    return [await create_store_group(str(database), artifacts) for _ in range(count)]


def _bundle(bundle_id: str, output_path: str) -> BackupBundle:
    created_at = datetime.now(tz=UTC)
    return BackupBundle(
        bundle_id=bundle_id,
        output_path=output_path,
        created_at=created_at,
        size_bytes=128,
        manifest=BackupManifest(
            bundle_id=bundle_id,
            created_at=created_at,
            source_project_root="/tmp/project",
            scopes=[BackupScope.SQLITE, BackupScope.CONFIG],
            files=[],
        ),
    )


async def _lifecycle_events(
    recorder: BackupAuditRecorder,
    bundle_id: str,
) -> list[Event]:
    try:
        return await recorder.list_lifecycle_events(bundle_id)
    except AttributeError:
        pytest.fail(_ORACLE, pytrace=False)


@pytest.mark.asyncio
async def test_recorder_roundtrips_started_completed_failed_events(tmp_path: Path) -> None:
    store = (await _stores(tmp_path))[0]
    recorder = BackupAuditRecorder(store)
    bundle_id = "bundle-roundtrip"
    output_path = "/tmp/bundle-roundtrip.zip"
    try:
        started = await recorder.record_started(
            bundle_id=bundle_id,
            output_path=output_path,
            scopes=[BackupScope.SQLITE, BackupScope.CONFIG],
        )
        completed = await recorder.record_completed(_bundle(bundle_id, output_path))
        failed = await recorder.record_failed(
            bundle_id=bundle_id,
            output_path=output_path,
            scopes=[BackupScope.SQLITE, BackupScope.CONFIG],
            message="verification failed",
        )
        assert all(isinstance(item, Event) for item in (started, completed, failed)), _ORACLE
    finally:
        await store.close()

    reopened = (await _stores(tmp_path))[0]
    try:
        events = await _lifecycle_events(BackupAuditRecorder(reopened), bundle_id)
        assert [event.type for event in events] == [
            EventType.BACKUP_STARTED,
            EventType.BACKUP_COMPLETED,
            EventType.BACKUP_FAILED,
        ]
        assert [event.task_seq for event in events] == [2, 3, 4]
        assert [event.causality.idempotency_key for event in events] == [
            f"backup:{bundle_id}:started",
            f"backup:{bundle_id}:completed",
            f"backup:{bundle_id}:failed",
        ]
        payloads = [BackupLifecyclePayload.model_validate(event.payload) for event in events]
        assert [payload.status for payload in payloads] == ["started", "completed", "failed"]
        assert all(payload.bundle_id == bundle_id for payload in payloads)
        assert payloads[-1].message == "verification failed"
    finally:
        await reopened.close()


@pytest.mark.asyncio
async def test_recorder_retry_is_idempotent_across_instances(tmp_path: Path) -> None:
    first, second = await _stores(tmp_path, count=2)
    bundle_id = "bundle-retry"
    kwargs = {
        "bundle_id": bundle_id,
        "output_path": "/tmp/bundle-retry.zip",
        "scopes": [BackupScope.SQLITE],
    }
    try:
        initial = await BackupAuditRecorder(first).record_started(**kwargs)
        try:
            retried = await BackupAuditRecorder(second).record_started(**kwargs)
        except aiosqlite.IntegrityError:
            pytest.fail(_ORACLE, pytrace=False)
        assert isinstance(initial, Event) and isinstance(retried, Event), _ORACLE
        assert retried.event_id == initial.event_id
        assert retried.task_seq == initial.task_seq
        events = await _lifecycle_events(BackupAuditRecorder(second), bundle_id)
        assert [event.event_id for event in events] == [initial.event_id]
    finally:
        await first.close()
        await second.close()


@pytest.mark.asyncio
async def test_recorder_store_error_leaves_no_partial_event(tmp_path: Path) -> None:
    store = (await _stores(tmp_path))[0]
    recorder = BackupAuditRecorder(store)
    bundle_id = "bundle-store-error"
    try:
        started = await recorder.record_started(
            bundle_id=bundle_id,
            output_path="/tmp/bundle-store-error.zip",
            scopes=[BackupScope.SQLITE],
        )
        assert isinstance(started, Event), _ORACLE
        before_events = await _lifecycle_events(recorder, bundle_id)
        before_task = await store.task_store.get_task(_AUDIT_TASK_ID)
        assert before_task is not None

        await store.conn.execute(
            """
            CREATE TRIGGER reject_backup_failed
            BEFORE INSERT ON events
            WHEN NEW.type = 'BACKUP_FAILED'
            BEGIN SELECT RAISE(ABORT, 'injected audit store error'); END
            """
        )
        await store.conn.commit()
        with pytest.raises(aiosqlite.IntegrityError, match="injected audit store error"):
            await recorder.record_failed(
                bundle_id=bundle_id,
                output_path="/tmp/bundle-store-error.zip",
                scopes=[BackupScope.SQLITE],
                message="must roll back",
            )

        after_events = await _lifecycle_events(recorder, bundle_id)
        after_task = await store.task_store.get_task(_AUDIT_TASK_ID)
        assert after_task is not None
        assert [event.event_id for event in after_events] == [
            event.event_id for event in before_events
        ]
        assert after_task.pointers == before_task.pointers
    finally:
        await store.close()
