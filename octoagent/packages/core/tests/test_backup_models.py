from __future__ import annotations

from datetime import UTC, datetime

from octoagent.core.models import (
    BackupBundle,
    BackupFileEntry,
    BackupManifest,
    BackupScope,
    RecoveryDrillRecord,
    RecoveryDrillStatus,
    RecoverySummary,
    RestoreConflict,
    RestoreConflictSeverity,
    RestoreConflictType,
    RestorePlan,
    SensitivityLevel,
)


def test_restore_plan_blocking_conflict_marks_incompatible() -> None:
    plan = RestorePlan(
        bundle_path="/tmp/demo.zip",
        target_root="/tmp/project",
        compatible=True,
        checked_at=datetime.now(tz=UTC),
        conflicts=[
            RestoreConflict(
                conflict_type=RestoreConflictType.PATH_EXISTS,
                severity=RestoreConflictSeverity.BLOCKING,
                target_path="/tmp/project/octoagent.yaml",
                message="目标路径已存在",
            )
        ],
    )

    assert plan.compatible is False
    assert plan.next_actions


def test_recovery_summary_ready_when_latest_drill_passed() -> None:
    now = datetime.now(tz=UTC)
    bundle = BackupBundle(
        bundle_id="bundle-001",
        output_path="/tmp/demo.zip",
        created_at=now,
        size_bytes=123,
        manifest=BackupManifest(
            bundle_id="bundle-001",
            created_at=now,
            source_project_root="/tmp/project",
            scopes=[BackupScope.SQLITE, BackupScope.CONFIG],
            files=[
                BackupFileEntry(
                    scope=BackupScope.SQLITE,
                    relative_path="sqlite/octoagent.db",
                    kind="file",
                    size_bytes=42,
                    sha256="abc",
                )
            ],
            sensitivity_level=SensitivityLevel.OPERATOR_SENSITIVE,
        ),
    )
    drill = RecoveryDrillRecord(
        status=RecoveryDrillStatus.PASSED,
        checked_at=now,
        bundle_path=bundle.output_path,
        summary="ok",
    )

    summary = RecoverySummary.from_records(bundle, drill)
    assert summary.ready_for_restore is True
    assert summary.latest_backup is not None


def test_recovery_summary_hides_not_run_record() -> None:
    summary = RecoverySummary.from_records(None, RecoveryDrillRecord())
    assert summary.latest_recovery_drill is None
    assert summary.ready_for_restore is False
