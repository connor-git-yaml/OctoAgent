from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from octoagent.core.models import (
    BackupBundle,
    BackupManifest,
    BackupScope,
    ProjectBindingType,
    ProjectMigrationStatus,
    RecoveryDrillRecord,
    RecoveryDrillStatus,
    RequesterInfo,
    Task,
)
from octoagent.core.store import create_store_group
from octoagent.memory import init_chat_import_db, init_memory_db
from octoagent.provider.dx.config_schema import (
    ChannelsConfig,
    OctoAgentConfig,
    ProviderEntry,
    RuntimeConfig,
    TelegramChannelConfig,
)
from octoagent.provider.dx.config_wizard import save_config
from octoagent.provider.dx.project_migration import ProjectWorkspaceMigrationService
from octoagent.provider.dx.recovery_status_store import RecoveryStatusStore


def _db_path(project_root: Path) -> Path:
    return project_root / "data" / "sqlite" / "octoagent.db"


def _artifacts_dir(project_root: Path) -> Path:
    return project_root / "data" / "artifacts"


async def _seed_legacy_instance(project_root: Path) -> None:
    save_config(
        OctoAgentConfig(
            updated_at="2026-03-08",
            providers=[
                ProviderEntry(
                    id="openrouter",
                    name="OpenRouter",
                    auth_type="api_key",
                    api_key_env="OPENROUTER_API_KEY",
                    enabled=True,
                )
            ],
            runtime=RuntimeConfig(master_key_env="LITELLM_MASTER_KEY"),
            channels=ChannelsConfig(
                telegram=TelegramChannelConfig(
                    enabled=True,
                    mode="polling",
                    bot_token_env="TELEGRAM_BOT_TOKEN",
                )
            ),
        ),
        project_root,
    )
    (project_root / ".env").write_text(
        "TELEGRAM_BOT_TOKEN=test-bot\nCUSTOM_VALUE=1\n",
        encoding="utf-8",
    )
    (project_root / ".env.litellm").write_text(
        "LITELLM_MASTER_KEY=master-key\nOPENROUTER_API_KEY=or-key\n",
        encoding="utf-8",
    )

    store_group = await create_store_group(
        str(_db_path(project_root)),
        _artifacts_dir(project_root),
    )
    now = datetime.now(tz=UTC)
    task = Task(
        task_id="task-025-legacy",
        created_at=now,
        updated_at=now,
        title="legacy task",
        thread_id="thread-legacy",
        scope_id="ops/default",
        requester=RequesterInfo(channel="telegram", sender_id="10001"),
        trace_id="trace-task-025-legacy",
    )
    await store_group.task_store.create_task(task)

    await init_memory_db(store_group.conn)
    await store_group.conn.execute(
        """
        INSERT INTO memory_fragments (
            fragment_id, scope_id, partition, content, created_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            "fragment-025-1",
            "memory/project-alpha",
            "chat",
            "legacy memory content",
            now.isoformat(),
        ),
    )
    await store_group.conn.execute(
        """
        INSERT INTO memory_sor (
            memory_id, scope_id, partition, subject_key, content, version,
            status, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "memory-025-1",
            "memory/project-alpha",
            "profile",
            "project.alpha.status",
            "active",
            1,
            "current",
            now.isoformat(),
            now.isoformat(),
        ),
    )

    await init_chat_import_db(store_group.conn)
    await store_group.conn.execute(
        """
        INSERT INTO chat_import_batches (
            batch_id, source_id, source_format, scope_id, channel, thread_id,
            input_path, started_at, completed_at, status, error_message, report_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "batch-025-1",
            "wechat-alpha",
            "normalized_jsonl",
            "import/wechat/project-alpha",
            "wechat_import",
            "project-alpha",
            str(project_root / "imports" / "wechat-alpha.jsonl"),
            now.isoformat(),
            now.isoformat(),
            "COMPLETED",
            "",
            "report-025-1",
        ),
    )
    await store_group.conn.execute(
        """
        INSERT INTO chat_import_cursors (
            source_id, scope_id, cursor_value, last_message_ts, last_message_key,
            imported_count, duplicate_count, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "wechat-alpha",
            "import/wechat/project-alpha",
            "cursor-001",
            now.isoformat(),
            "message-001",
            10,
            1,
            now.isoformat(),
        ),
    )
    await store_group.conn.execute(
        """
        INSERT INTO chat_import_dedupe (
            dedupe_id, source_id, scope_id, message_key, source_message_id,
            imported_at, batch_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "dedupe-025-1",
            "wechat-alpha",
            "import/wechat/project-alpha",
            "message-001",
            "message-001",
            now.isoformat(),
            "batch-025-1",
        ),
    )
    await store_group.conn.execute(
        """
        INSERT INTO chat_import_windows (
            window_id, batch_id, scope_id, first_ts, last_ts, message_count,
            artifact_id, summary_fragment_id, fact_disposition, proposal_ids
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "window-025-1",
            "batch-025-1",
            "import/wechat/project-alpha",
            now.isoformat(),
            now.isoformat(),
            2,
            "artifact-import-025-1",
            "fragment-025-1",
            "fragment_only",
            "[]",
        ),
    )
    await store_group.conn.execute(
        """
        INSERT INTO chat_import_reports (
            report_id, batch_id, source_id, scope_id, dry_run, created_at,
            summary_json, cursor_json, artifact_refs, warnings, errors, next_actions
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "report-025-1",
            "batch-025-1",
            "wechat-alpha",
            "import/wechat/project-alpha",
            0,
            now.isoformat(),
            "{}",
            "{}",
            "[\"artifact-import-025-1\"]",
            "[]",
            "[]",
            "[]",
        ),
    )
    await store_group.conn.commit()
    await store_group.conn.close()

    (project_root / "data" / "backups").mkdir(parents=True, exist_ok=True)
    (project_root / "data" / "exports").mkdir(parents=True, exist_ok=True)
    status_store = RecoveryStatusStore(project_root)
    status_store.save_latest_backup(
        BackupBundle(
            bundle_id="bundle-025-1",
            output_path=str(project_root / "data" / "backups" / "bundle-025-1.zip"),
            created_at=now,
            size_bytes=1024,
            manifest=BackupManifest(
                bundle_id="bundle-025-1",
                created_at=now,
                source_project_root=str(project_root),
                scopes=[BackupScope.SQLITE],
                files=[],
            ),
        )
    )
    status_store.save_recovery_drill(
        RecoveryDrillRecord(
            status=RecoveryDrillStatus.PASSED,
            checked_at=now,
            bundle_path=str(project_root / "data" / "backups" / "bundle-025-1.zip"),
            summary="legacy recovery ok",
        )
    )


class _FailingMigrationService(ProjectWorkspaceMigrationService):
    async def _validate_state(self, **kwargs):  # type: ignore[override]
        validation = await super()._validate_state(**kwargs)
        payload = validation.model_dump(mode="python")
        payload["blocking_issues"] = ["forced validation failure"]
        return validation.__class__.model_validate(payload)


async def test_migration_apply_creates_default_project_for_empty_instance(tmp_path: Path) -> None:
    service = ProjectWorkspaceMigrationService(tmp_path)

    run = await service.apply()

    assert run.status == ProjectMigrationStatus.SUCCEEDED
    store_group = await create_store_group(str(_db_path(tmp_path)), _artifacts_dir(tmp_path))
    try:
        default_project = await store_group.project_store.get_default_project()
        assert default_project is not None
        # workspace 概念已废弃，不再验证 primary workspace
    finally:
        await store_group.conn.close()


async def test_migration_backfills_legacy_metadata_bindings(tmp_path: Path) -> None:
    await _seed_legacy_instance(tmp_path)
    service = ProjectWorkspaceMigrationService(tmp_path)

    run = await service.apply()

    assert run.status == ProjectMigrationStatus.SUCCEEDED
    assert run.summary.binding_counts["scope"] >= 1
    assert run.summary.binding_counts["memory_scope"] >= 1
    assert run.summary.binding_counts["import_scope"] >= 1
    store_group = await create_store_group(str(_db_path(tmp_path)), _artifacts_dir(tmp_path))
    try:
        project = await store_group.project_store.get_default_project()
        assert project is not None
        bindings = await store_group.project_store.list_bindings(project.project_id)
        binding_index = {
            (binding.binding_type, binding.binding_key): binding
            for binding in bindings
        }
        assert (ProjectBindingType.SCOPE, "ops/default") in binding_index
        assert (ProjectBindingType.MEMORY_SCOPE, "memory/project-alpha") in binding_index
        assert (
            ProjectBindingType.IMPORT_SCOPE,
            "import/wechat/project-alpha",
        ) in binding_index
        assert (ProjectBindingType.CHANNEL, "telegram") in binding_index
        assert (ProjectBindingType.CHANNEL, "wechat_import") in binding_index
        assert (ProjectBindingType.ENV_FILE, ".env") in binding_index
        assert (ProjectBindingType.ENV_FILE, ".env.litellm") in binding_index
        assert (ProjectBindingType.ENV_REF, "LITELLM_MASTER_KEY") in binding_index
        assert (ProjectBindingType.ENV_REF, "OPENROUTER_API_KEY") in binding_index
        assert (ProjectBindingType.ENV_REF, "TELEGRAM_BOT_TOKEN") in binding_index
        assert (ProjectBindingType.BACKUP_ROOT, str((tmp_path / "data").resolve())) in binding_index
    finally:
        await store_group.conn.close()


async def test_migration_is_idempotent(tmp_path: Path) -> None:
    await _seed_legacy_instance(tmp_path)
    service = ProjectWorkspaceMigrationService(tmp_path)

    first = await service.apply()
    second = await service.apply()

    assert first.status == ProjectMigrationStatus.SUCCEEDED
    assert second.status == ProjectMigrationStatus.SUCCEEDED
    assert second.summary.created_project is False
    assert second.summary.created_workspace is False
    assert second.summary.binding_counts == {}

    store_group = await create_store_group(str(_db_path(tmp_path)), _artifacts_dir(tmp_path))
    try:
        projects = await store_group.project_store.list_projects()
        assert len(projects) == 1
    finally:
        await store_group.conn.close()


async def test_validation_failure_rolls_back_new_records(tmp_path: Path) -> None:
    await _seed_legacy_instance(tmp_path)
    service = _FailingMigrationService(tmp_path)

    run = await service.apply()

    assert run.status == ProjectMigrationStatus.FAILED
    store_group = await create_store_group(str(_db_path(tmp_path)), _artifacts_dir(tmp_path))
    try:
        assert await store_group.project_store.get_default_project() is None
        latest = await store_group.project_store.get_latest_migration_run(str(tmp_path.resolve()))
        assert latest is not None
        assert latest.status == ProjectMigrationStatus.FAILED
        task = await store_group.task_store.get_task("task-025-legacy")
        assert task is not None
    finally:
        await store_group.conn.close()


async def test_rollback_latest_removes_current_run_records_only(tmp_path: Path) -> None:
    await _seed_legacy_instance(tmp_path)
    service = ProjectWorkspaceMigrationService(tmp_path)

    applied = await service.apply()
    rolled_back = await service.rollback("latest")

    assert applied.status == ProjectMigrationStatus.SUCCEEDED
    assert rolled_back.status == ProjectMigrationStatus.ROLLED_BACK

    store_group = await create_store_group(str(_db_path(tmp_path)), _artifacts_dir(tmp_path))
    try:
        assert await store_group.project_store.get_default_project() is None
        task = await store_group.task_store.get_task("task-025-legacy")
        assert task is not None
    finally:
        await store_group.conn.close()


async def test_rollback_latest_skips_failed_run_and_failed_run_is_not_revertible(
    tmp_path: Path,
) -> None:
    await _seed_legacy_instance(tmp_path)
    service = ProjectWorkspaceMigrationService(tmp_path)

    succeeded = await service.apply()
    failed = await _FailingMigrationService(tmp_path).apply()

    assert succeeded.status == ProjectMigrationStatus.SUCCEEDED
    assert failed.status == ProjectMigrationStatus.FAILED

    with pytest.raises(ValueError, match="成功 apply"):
        await service.rollback(failed.run_id)

    store_group = await create_store_group(str(_db_path(tmp_path)), _artifacts_dir(tmp_path))
    try:
        assert await store_group.project_store.get_default_project() is not None
    finally:
        await store_group.conn.close()

    rolled_back = await service.rollback("latest")
    assert rolled_back.run_id == succeeded.run_id
    assert rolled_back.status == ProjectMigrationStatus.ROLLED_BACK
