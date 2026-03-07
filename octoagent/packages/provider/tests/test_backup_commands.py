from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from click.testing import CliRunner
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
from octoagent.provider.dx.cli import main
from ulid import ULID


async def _seed_project(project_root: Path) -> None:
    (project_root / "octoagent.yaml").write_text("config_version: 1\n", encoding="utf-8")
    (project_root / "litellm-config.yaml").write_text("model_list: []\n", encoding="utf-8")
    store_group = await create_store_group(
        str(project_root / "data" / "sqlite" / "octoagent.db"),
        project_root / "data" / "artifacts",
    )
    now = datetime.now(tz=UTC)
    task = Task(
        task_id="task-cli-001",
        created_at=now,
        updated_at=now,
        title="hello cli",
        thread_id="thread-cli",
        requester=RequesterInfo(channel="web", sender_id="owner"),
        trace_id="trace-task-cli-001",
    )
    await create_task_with_initial_events(
        store_group.conn,
        store_group.task_store,
        store_group.event_store,
        task,
        [
            Event(
                event_id=str(ULID()),
                task_id=task.task_id,
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
                causality=EventCausality(idempotency_key="cli-task-created"),
            ),
            Event(
                event_id=str(ULID()),
                task_id=task.task_id,
                task_seq=2,
                ts=now,
                type=EventType.USER_MESSAGE,
                actor=ActorType.USER,
                payload=UserMessagePayload(
                    text_preview="hello",
                    text_length=5,
                ).model_dump(mode="json"),
                trace_id=task.trace_id,
                causality=EventCausality(idempotency_key="cli-task-message"),
            ),
        ],
    )
    artifact = Artifact(
        artifact_id="artifact-cli-001",
        task_id=task.task_id,
        ts=now,
        name="cli-artifact",
        parts=[ArtifactPart(type=PartType.TEXT, mime="text/plain", content="hello world")],
        size=0,
        hash="",
    )
    await store_group.artifact_store.put_artifact(artifact, content=b"hello world")
    await store_group.conn.commit()
    await store_group.conn.close()


def test_backup_create_command_writes_zip(tmp_path: Path) -> None:
    asyncio.run(_seed_project(tmp_path))
    runner = CliRunner()

    output = tmp_path / "custom" / "manual.zip"
    result = runner.invoke(
        main,
        ["backup", "create", "--output", str(output)],
        env={"OCTOAGENT_PROJECT_ROOT": str(tmp_path)},
    )

    assert result.exit_code == 0
    assert output.exists()


def test_restore_dry_run_exit_codes(tmp_path: Path) -> None:
    asyncio.run(_seed_project(tmp_path))
    service = BackupService(tmp_path)
    bundle = asyncio.run(service.create_bundle())
    runner = CliRunner()

    clean_target = tmp_path / "restore-clean"
    ok = runner.invoke(
        main,
        ["restore", "dry-run", "--bundle", bundle.output_path, "--target-root", str(clean_target)],
        env={"OCTOAGENT_PROJECT_ROOT": str(tmp_path)},
    )
    assert ok.exit_code == 0

    conflict_target = tmp_path / "restore-conflict"
    conflict_target.mkdir(parents=True, exist_ok=True)
    (conflict_target / "octoagent.yaml").write_text("existing=true\n", encoding="utf-8")
    blocked = runner.invoke(
        main,
        [
            "restore",
            "dry-run",
            "--bundle",
            bundle.output_path,
            "--target-root",
            str(conflict_target),
        ],
        env={"OCTOAGENT_PROJECT_ROOT": str(tmp_path)},
    )
    assert blocked.exit_code == 1
    assert (tmp_path / "data" / "ops" / "recovery-drill.json").exists()


def test_export_chats_empty_result_returns_zero(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["export", "chats", "--thread-id", "missing-thread"],
        env={"OCTOAGENT_PROJECT_ROOT": str(tmp_path)},
    )

    assert result.exit_code == 0
    exports_dir = tmp_path / "data" / "exports"
    assert exports_dir.exists()
    assert any(path.suffix == ".json" for path in exports_dir.iterdir())
