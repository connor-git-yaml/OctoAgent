from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from octoagent.core.models import (
    BackupBundle,
    BackupFileEntry,
    BackupManifest,
    BackupScope,
    RecoveryDrillRecord,
    RecoveryDrillStatus,
)
from octoagent.provider.dx.recovery_status_store import RecoveryStatusStore


def _bundle(tmp_path: Path) -> BackupBundle:
    now = datetime.now(tz=UTC)
    return BackupBundle(
        bundle_id="bundle-001",
        output_path=str(tmp_path / "data" / "backups" / "bundle.zip"),
        created_at=now,
        size_bytes=123,
        manifest=BackupManifest(
            bundle_id="bundle-001",
            created_at=now,
            source_project_root=str(tmp_path),
            scopes=[BackupScope.SQLITE],
            files=[
                BackupFileEntry(
                    scope=BackupScope.SQLITE,
                    relative_path="sqlite/octoagent.db",
                    kind="file",
                    size_bytes=42,
                    sha256="abc",
                )
            ],
        ),
    )


def test_load_defaults(tmp_path: Path) -> None:
    store = RecoveryStatusStore(tmp_path)
    assert store.load_latest_backup() is None
    drill = store.load_recovery_drill()
    assert drill.status == RecoveryDrillStatus.NOT_RUN
    assert store.load_summary().ready_for_restore is False


def test_roundtrip_latest_backup_and_recovery_drill(tmp_path: Path) -> None:
    store = RecoveryStatusStore(tmp_path)
    bundle = _bundle(tmp_path)
    store.save_latest_backup(bundle)
    store.save_recovery_drill(
        RecoveryDrillRecord(
            status=RecoveryDrillStatus.PASSED,
            checked_at=datetime.now(tz=UTC),
            bundle_path=bundle.output_path,
            summary="ok",
        )
    )

    assert store.load_latest_backup() is not None
    summary = store.load_summary()
    assert summary.ready_for_restore is True
    assert summary.latest_recovery_drill is not None


def test_corrupted_recovery_record_falls_back_to_default(tmp_path: Path) -> None:
    store = RecoveryStatusStore(tmp_path)
    store.recovery_drill_path.parent.mkdir(parents=True, exist_ok=True)
    store.recovery_drill_path.write_text("{bad json", encoding="utf-8")

    drill = store.load_recovery_drill()
    assert drill.status == RecoveryDrillStatus.NOT_RUN
    assert Path(str(store.recovery_drill_path) + ".corrupted").exists()


def test_custom_data_dir_changes_ops_paths(tmp_path: Path) -> None:
    data_dir = tmp_path / "runtime-data"
    store = RecoveryStatusStore(tmp_path, data_dir=data_dir)

    assert store.latest_backup_path == data_dir / "ops" / "latest-backup.json"
    assert store.recovery_drill_path == data_dir / "ops" / "recovery-drill.json"
