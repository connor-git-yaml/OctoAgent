from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from octoagent.core.models import (
    ActorType,
    Artifact,
    ArtifactPart,
    Event,
    EventCausality,
    EventType,
    PartType,
    RequesterInfo,
    Task,
    TaskCreatedPayload,
    UserMessagePayload,
)
from octoagent.core.store import create_store_group
from octoagent.core.store.transaction import create_task_with_initial_events
from octoagent.provider.dx.backup_service import BackupService
from octoagent.provider.dx.recovery_status_store import RecoveryStatusStore
from ulid import ULID


async def _seed_project(
    project_root: Path,
    *,
    db_path: Path | None = None,
    artifacts_dir: Path | None = None,
) -> str:
    (project_root / "octoagent.yaml").write_text("config_version: 1\n", encoding="utf-8")
    (project_root / "litellm-config.yaml").write_text("model_list: []\n", encoding="utf-8")
    (project_root / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (project_root / ".env.litellm").write_text("SECRET=1\n", encoding="utf-8")

    resolved_db_path = db_path or project_root / "data" / "sqlite" / "octoagent.db"
    resolved_artifacts_dir = artifacts_dir or project_root / "data" / "artifacts"
    store_group = await create_store_group(
        str(resolved_db_path),
        resolved_artifacts_dir,
    )

    now = datetime.now(tz=UTC)
    task_id = "task-022-001"
    task = Task(
        task_id=task_id,
        created_at=now,
        updated_at=now,
        title="hello backup",
        thread_id="thread-1",
        requester=RequesterInfo(channel="web", sender_id="owner"),
        trace_id="trace-task-022-001",
    )
    events = [
        Event(
            event_id=str(ULID()),
            task_id=task_id,
            task_seq=1,
            ts=now,
            type=EventType.TASK_CREATED,
            actor=ActorType.USER,
            payload=TaskCreatedPayload(
                title=task.title,
                thread_id=task.thread_id,
                scope_id=task.scope_id,
                channel=task.requester.channel,
                sender_id=task.requester.sender_id,
            ).model_dump(mode="json"),
            trace_id=task.trace_id,
            causality=EventCausality(idempotency_key="seed-task-created"),
        ),
        Event(
            event_id=str(ULID()),
            task_id=task_id,
            task_seq=2,
            ts=now,
            type=EventType.USER_MESSAGE,
            actor=ActorType.USER,
            payload=UserMessagePayload(
                text_preview="hello",
                text_length=5,
            ).model_dump(mode="json"),
            trace_id=task.trace_id,
            causality=EventCausality(idempotency_key="seed-user-message"),
        ),
    ]
    await create_task_with_initial_events(
        store_group.conn,
        store_group.task_store,
        store_group.event_store,
        task,
        events,
    )

    artifact = Artifact(
        artifact_id="artifact-001",
        task_id=task_id,
        ts=now,
        name="chat-output",
        parts=[ArtifactPart(type=PartType.TEXT, mime="text/plain", content="hello world")],
        size=0,
        hash="",
    )
    await store_group.artifact_store.put_artifact(artifact, content=b"hello world")
    await store_group.conn.commit()
    await store_group.conn.close()
    return task_id


async def _append_follow_up(
    project_root: Path,
    *,
    ts: datetime,
    db_path: Path | None = None,
    artifacts_dir: Path | None = None,
) -> None:
    resolved_db_path = db_path or project_root / "data" / "sqlite" / "octoagent.db"
    resolved_artifacts_dir = artifacts_dir or project_root / "data" / "artifacts"
    store_group = await create_store_group(str(resolved_db_path), resolved_artifacts_dir)
    task_id = "task-022-001"
    task = await store_group.task_store.get_task(task_id)
    assert task is not None

    event = Event(
        event_id=str(ULID()),
        task_id=task_id,
        task_seq=await store_group.event_store.get_next_task_seq(task_id),
        ts=ts,
        type=EventType.USER_MESSAGE,
        actor=ActorType.USER,
        payload=UserMessagePayload(
            text_preview="follow-up",
            text_length=9,
        ).model_dump(mode="json"),
        trace_id=task.trace_id,
        causality=EventCausality(idempotency_key="seed-follow-up-message"),
    )
    await store_group.event_store.append_event(event)
    artifact = Artifact(
        artifact_id="artifact-002",
        task_id=task_id,
        ts=ts,
        name="chat-output-later",
        parts=[ArtifactPart(type=PartType.TEXT, mime="text/plain", content="follow up")],
        size=0,
        hash="",
    )
    await store_group.artifact_store.put_artifact(artifact, content=b"follow up")
    await store_group.conn.commit()
    await store_group.conn.close()


async def _seed_additional_chat_task(
    project_root: Path,
    *,
    task_id: str,
    thread_id: str,
    text: str,
    db_path: Path | None = None,
    artifacts_dir: Path | None = None,
) -> str:
    resolved_db_path = db_path or project_root / "data" / "sqlite" / "octoagent.db"
    resolved_artifacts_dir = artifacts_dir or project_root / "data" / "artifacts"
    store_group = await create_store_group(str(resolved_db_path), resolved_artifacts_dir)
    now = datetime.now(tz=UTC)
    task = Task(
        task_id=task_id,
        created_at=now,
        updated_at=now,
        title=text,
        thread_id=thread_id,
        requester=RequesterInfo(channel="web", sender_id="owner"),
        trace_id=f"trace-{task_id}",
    )
    events = [
        Event(
            event_id=str(ULID()),
            task_id=task_id,
            task_seq=1,
            ts=now,
            type=EventType.TASK_CREATED,
            actor=ActorType.USER,
            payload=TaskCreatedPayload(
                title=task.title,
                thread_id=task.thread_id,
                scope_id=task.scope_id,
                channel=task.requester.channel,
                sender_id=task.requester.sender_id,
            ).model_dump(mode="json"),
            trace_id=task.trace_id,
            causality=EventCausality(idempotency_key=f"seed-task-created:{task_id}"),
        ),
        Event(
            event_id=str(ULID()),
            task_id=task_id,
            task_seq=2,
            ts=now,
            type=EventType.USER_MESSAGE,
            actor=ActorType.USER,
            payload=UserMessagePayload(
                text_preview=text,
                text_length=len(text),
            ).model_dump(mode="json"),
            trace_id=task.trace_id,
            causality=EventCausality(idempotency_key=f"seed-user-message:{task_id}"),
        ),
    ]
    await create_task_with_initial_events(
        store_group.conn,
        store_group.task_store,
        store_group.event_store,
        task,
        events,
    )
    artifact = Artifact(
        artifact_id=f"artifact-{task_id}",
        task_id=task_id,
        ts=now,
        name=f"{task_id}-artifact",
        parts=[ArtifactPart(type=PartType.TEXT, mime="text/plain", content=text)],
        size=0,
        hash="",
    )
    await store_group.artifact_store.put_artifact(artifact, content=text.encode("utf-8"))
    await store_group.conn.commit()
    await store_group.conn.close()
    return task_id


async def _seed_ops_chat_import_task(project_root: Path) -> str:
    resolved_db_path = project_root / "data" / "sqlite" / "octoagent.db"
    resolved_artifacts_dir = project_root / "data" / "artifacts"
    store_group = await create_store_group(str(resolved_db_path), resolved_artifacts_dir)

    now = datetime.now(tz=UTC)
    task_id = "ops-chat-import"
    task = Task(
        task_id=task_id,
        created_at=now,
        updated_at=now,
        title="系统运维审计（聊天导入）",
        thread_id="ops-chat-import",
        scope_id="ops/chat-import",
        requester=RequesterInfo(channel="system", sender_id="system"),
        trace_id="trace-ops-chat-import",
    )
    events = [
        Event(
            event_id=str(ULID()),
            task_id=task_id,
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
            trace_id=task.trace_id,
            causality=EventCausality(idempotency_key="seed-ops-chat-import-created"),
        ),
        Event(
            event_id=str(ULID()),
            task_id=task_id,
            task_seq=2,
            ts=now,
            type=EventType.CHAT_IMPORT_COMPLETED,
            actor=ActorType.SYSTEM,
            payload={"batch_id": "batch-001", "message": "聊天导入完成。"},
            trace_id=task.trace_id,
            causality=EventCausality(idempotency_key="seed-ops-chat-import-completed"),
        ),
    ]
    await create_task_with_initial_events(
        store_group.conn,
        store_group.task_store,
        store_group.event_store,
        task,
        events,
    )

    artifact = Artifact(
        artifact_id="artifact-ops-import-001",
        task_id=task_id,
        ts=now,
        name="chat-import-window-001.json",
        parts=[ArtifactPart(type=PartType.JSON, mime="application/json", content="{}")],
        size=0,
        hash="",
    )
    await store_group.artifact_store.put_artifact(artifact, content=b"{}")
    await store_group.conn.commit()
    await store_group.conn.close()
    return task_id


@pytest.mark.asyncio
async def test_create_bundle_excludes_plaintext_secrets_and_updates_state(tmp_path: Path) -> None:
    await _seed_project(tmp_path)
    service = BackupService(tmp_path)

    bundle = await service.create_bundle(label="before-upgrade")

    assert Path(bundle.output_path).exists()
    assert ".env" in bundle.manifest.excluded_paths
    assert ".env.litellm" in bundle.manifest.excluded_paths

    with zipfile.ZipFile(bundle.output_path) as archive:
        names = set(archive.namelist())
        assert "manifest.json" in names
        assert "sqlite/octoagent.db" in names
        assert "config/octoagent.yaml" in names
        assert ".env" not in names
        assert ".env.litellm" not in names

    latest = RecoveryStatusStore(tmp_path).load_latest_backup()
    assert latest is not None
    assert latest.bundle_id == bundle.bundle_id

    store_group = await create_store_group(
        str(tmp_path / "data" / "sqlite" / "octoagent.db"),
        tmp_path / "data" / "artifacts",
    )
    audit_events = await store_group.event_store.get_events_for_task("ops-recovery-audit")
    assert [event.type for event in audit_events][-2:] == [
        EventType.BACKUP_STARTED,
        EventType.BACKUP_COMPLETED,
    ]
    await store_group.conn.close()


@pytest.mark.asyncio
async def test_create_bundle_uses_configured_storage_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    custom_data_dir = tmp_path / "runtime-data"
    db_path = custom_data_dir / "sqlite" / "custom.db"
    artifacts_dir = custom_data_dir / "artifacts"
    await _seed_project(tmp_path, db_path=db_path, artifacts_dir=artifacts_dir)
    monkeypatch.setenv("OCTOAGENT_DB_PATH", str(db_path))
    monkeypatch.setenv("OCTOAGENT_ARTIFACTS_DIR", str(artifacts_dir))

    service = BackupService(tmp_path)
    bundle = await service.create_bundle()

    assert Path(bundle.output_path).parent == custom_data_dir / "backups"
    latest = RecoveryStatusStore(tmp_path, data_dir=custom_data_dir).load_latest_backup()
    assert latest is not None
    assert latest.output_path == bundle.output_path

    with tempfile.TemporaryDirectory(prefix="octo-backup-test-") as temp_dir:
        with zipfile.ZipFile(bundle.output_path) as archive:
            archive.extract("sqlite/octoagent.db", path=temp_dir)
        snapshot_db = Path(temp_dir) / "sqlite" / "octoagent.db"
        with sqlite3.connect(snapshot_db) as conn:
            exported_task = conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE task_id = ?",
                ("task-022-001",),
            ).fetchone()[0]
    assert exported_task == 1


@pytest.mark.asyncio
async def test_plan_restore_updates_recovery_drill(tmp_path: Path) -> None:
    await _seed_project(tmp_path)
    service = BackupService(tmp_path)
    bundle = await service.create_bundle()

    clean_target = tmp_path / "restore-clean"
    plan = await service.plan_restore(bundle=bundle.output_path, target_root=clean_target)
    assert plan.compatible is True

    record = RecoveryStatusStore(tmp_path).load_recovery_drill()
    assert record.status == "PASSED"

    conflict_target = tmp_path / "restore-conflict"
    conflict_target.mkdir(parents=True, exist_ok=True)
    (conflict_target / "octoagent.yaml").write_text("existing=true\n", encoding="utf-8")
    failed_plan = await service.plan_restore(bundle=bundle.output_path, target_root=conflict_target)
    assert failed_plan.compatible is False
    assert any(conflict.conflict_type == "path_exists" for conflict in failed_plan.conflicts)

    failed_record = RecoveryStatusStore(tmp_path).load_recovery_drill()
    assert failed_record.status == "FAILED"


@pytest.mark.asyncio
async def test_plan_restore_checks_first_existing_ancestor_for_writability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_project(tmp_path)
    service = BackupService(tmp_path)
    bundle = await service.create_bundle()

    blocked_root = tmp_path / "blocked-root"
    blocked_root.mkdir(parents=True, exist_ok=True)

    real_access = os.access

    def fake_access(path: str | os.PathLike[str], mode: int) -> bool:
        if Path(path) == blocked_root:
            return False
        return real_access(path, mode)

    monkeypatch.setattr("octoagent.provider.dx.backup_service.os.access", fake_access)

    plan = await service.plan_restore(
        bundle=bundle.output_path,
        target_root=blocked_root / "nested" / "restore-target",
    )

    assert plan.compatible is False
    assert any(conflict.conflict_type == "target_unwritable" for conflict in plan.conflicts)


@pytest.mark.asyncio
async def test_export_chats_outputs_manifest_and_payload_file(tmp_path: Path) -> None:
    task_id = await _seed_project(tmp_path)
    service = BackupService(tmp_path)

    manifest = await service.export_chats(thread_id="thread-1")

    assert len(manifest.tasks) == 1
    assert manifest.tasks[0].task_id == task_id
    assert manifest.event_count >= 2
    assert "artifact-001" in manifest.artifact_refs

    payload = json.loads(Path(manifest.output_path).read_text(encoding="utf-8"))
    assert payload["manifest"]["export_id"] == manifest.export_id
    assert task_id in payload["events_by_task"]


@pytest.mark.asyncio
async def test_export_chats_includes_explicit_ops_task_filter(tmp_path: Path) -> None:
    await _seed_project(tmp_path)
    task_id = await _seed_ops_chat_import_task(tmp_path)
    service = BackupService(tmp_path)

    manifest = await service.export_chats(task_id=task_id)

    assert len(manifest.tasks) == 1
    assert manifest.tasks[0].task_id == task_id
    assert manifest.event_count == 2
    assert manifest.artifact_refs == ["artifact-ops-import-001"]

    payload = json.loads(Path(manifest.output_path).read_text(encoding="utf-8"))
    assert task_id in payload["events_by_task"]
    assert task_id in payload["artifacts_by_task"]


@pytest.mark.asyncio
async def test_export_chats_filters_events_and_artifacts_by_time_window(tmp_path: Path) -> None:
    task_id = await _seed_project(tmp_path)
    later_ts = datetime.now(tz=UTC) + timedelta(minutes=1)
    await _append_follow_up(tmp_path, ts=later_ts)
    service = BackupService(tmp_path)

    manifest = await service.export_chats(
        task_id=task_id,
        since=later_ts.isoformat(),
    )

    assert len(manifest.tasks) == 1
    assert manifest.event_count == 1
    assert manifest.artifact_refs == ["artifact-002"]

    payload = json.loads(Path(manifest.output_path).read_text(encoding="utf-8"))
    assert len(payload["events_by_task"][task_id]) == 1
    assert payload["events_by_task"][task_id][0]["payload"]["text_preview"] == "follow-up"
    assert payload["artifacts_by_task"][task_id] == [
        {
            "artifact_id": "artifact-002",
            "name": "chat-output-later",
            "size": 9,
            "storage_ref": None,
        }
    ]


@pytest.mark.asyncio
async def test_export_chats_supports_precise_task_id_list_filter(tmp_path: Path) -> None:
    first_task_id = await _seed_project(tmp_path)
    second_task_id = await _seed_additional_chat_task(
        tmp_path,
        task_id="task-022-002",
        thread_id="thread-1",
        text="second task",
    )
    service = BackupService(tmp_path)

    manifest = await service.export_chats(task_ids=[second_task_id])

    assert len(manifest.tasks) == 1
    assert manifest.tasks[0].task_id == second_task_id
    assert manifest.filters.task_ids == [second_task_id]
    assert first_task_id not in {item.task_id for item in manifest.tasks}

    payload = json.loads(Path(manifest.output_path).read_text(encoding="utf-8"))
    assert set(payload["events_by_task"]) == {second_task_id}
    assert set(payload["artifacts_by_task"]) == {second_task_id}


@pytest.mark.asyncio
async def test_export_chats_rejects_naive_timestamps(tmp_path: Path) -> None:
    await _seed_project(tmp_path)
    service = BackupService(tmp_path)

    with pytest.raises(ValueError, match="时间必须包含时区"):
        await service.export_chats(since="2026-03-07T12:00:00")
