"""Feature 022 backup / restore dry-run / chats export 服务。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import tempfile
import zipfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from octoagent.core.config import get_artifacts_dir, get_db_path
from octoagent.core.models import (
    Artifact,
    BackupBundle,
    BackupFileEntry,
    BackupManifest,
    BackupScope,
    Event,
    ExportFilter,
    ExportManifest,
    ExportTaskRef,
    RecoveryDrillRecord,
    RecoveryDrillStatus,
    RecoverySummary,
    RestoreConflict,
    RestoreConflictSeverity,
    RestoreConflictType,
    RestorePlan,
    SensitivityLevel,
    Task,
)
from octoagent.core.store import StoreGroup, create_store_group
from ulid import ULID

from .backup_audit import BackupAuditRecorder
from .project_migration import ProjectWorkspaceMigrationService
from .recovery_status_store import RecoveryStatusStore

DEFAULT_EXCLUDED_PATHS = [
    ".env",
    ".env.litellm",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
]
_BUNDLE_MANIFEST_VERSION = 1


def resolve_project_root(project_root: Path | None = None) -> Path:
    if project_root is not None:
        return project_root
    if env_root := os.environ.get("OCTOAGENT_PROJECT_ROOT"):
        return Path(env_root)
    return Path.cwd()


def _resolve_path_from_root(path: str | Path, project_root: Path) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = (project_root / candidate).resolve()
    else:
        candidate = candidate.resolve()
    return candidate


def resolve_db_path(project_root: Path | None = None) -> Path:
    root = resolve_project_root(project_root).resolve()
    return _resolve_path_from_root(get_db_path(), root)


def resolve_artifacts_dir(project_root: Path | None = None) -> Path:
    root = resolve_project_root(project_root).resolve()
    return _resolve_path_from_root(get_artifacts_dir(), root)


def resolve_data_dir(project_root: Path | None = None) -> Path:
    root = resolve_project_root(project_root).resolve()
    if env_data_dir := os.environ.get("OCTOAGENT_DATA_DIR"):
        return _resolve_path_from_root(env_data_dir, root)

    db_path = resolve_db_path(root)
    if db_path.parent.name == "sqlite":
        return db_path.parent.parent.resolve()

    artifacts_dir = resolve_artifacts_dir(root)
    if artifacts_dir.name == "artifacts":
        return artifacts_dir.parent.resolve()

    return (root / "data").resolve()


class BackupService:
    """Provider DX 层的 recovery 相关服务。"""

    def __init__(
        self,
        project_root: Path,
        *,
        store_group: StoreGroup | None = None,
        status_store: RecoveryStatusStore | None = None,
    ) -> None:
        self._root = resolve_project_root(project_root).resolve()
        self._data_dir = resolve_data_dir(self._root)
        self._db_path = resolve_db_path(self._root)
        self._artifacts_dir = resolve_artifacts_dir(self._root)
        self._backups_dir = self._data_dir / "backups"
        self._exports_dir = self._data_dir / "exports"
        self._store_group = store_group
        self._project_migration_ensured = False
        self._status_store = status_store or RecoveryStatusStore(
            self._root,
            data_dir=self._data_dir,
        )

    def get_recovery_summary(self) -> RecoverySummary:
        return self._status_store.load_summary()

    async def create_bundle(
        self,
        *,
        output: str | Path | None = None,
        label: str | None = None,
    ) -> BackupBundle:
        created_at = datetime.now(tz=UTC)
        bundle_id = str(ULID())
        scopes = [
            BackupScope.SQLITE,
            BackupScope.ARTIFACTS,
            BackupScope.CONFIG,
            BackupScope.CHATS,
        ]
        output_path = self._resolve_bundle_output_path(output, created_at, label)

        async with self._store_group_scope() as store_group:
            audit = BackupAuditRecorder(store_group)
            await audit.record_started(
                bundle_id=bundle_id,
                output_path=str(output_path.resolve()),
                scopes=scopes,
            )
            try:
                bundle = await asyncio.to_thread(
                    self._create_bundle_sync,
                    output_path,
                    bundle_id,
                    created_at,
                    scopes,
                )
                self._status_store.save_latest_backup(bundle)
            except Exception as exc:
                await audit.record_failed(
                    bundle_id=bundle_id,
                    output_path=str(output_path.resolve()),
                    scopes=scopes,
                    message=str(exc),
                )
                raise

            await audit.record_completed(bundle)
            return bundle

    async def plan_restore(
        self,
        *,
        bundle: str | Path,
        target_root: str | Path | None = None,
    ) -> RestorePlan:
        bundle_path = Path(bundle).expanduser()
        if not bundle_path.is_absolute():
            bundle_path = (self._root / bundle_path).resolve()
        if not bundle_path.exists():
            raise FileNotFoundError(f"bundle 不存在: {bundle_path}")

        if target_root is None:
            target = self._root
        else:
            target = Path(target_root).expanduser()
            if not target.is_absolute():
                target = (self._root / target).resolve()
            else:
                target = target.resolve()
        plan = await asyncio.to_thread(self._plan_restore_sync, bundle_path, target)
        self._status_store.save_recovery_drill(self._record_from_plan(plan))
        return plan

    async def export_chats(
        self,
        *,
        task_id: str | None = None,
        thread_id: str | None = None,
        since: str | datetime | None = None,
        until: str | datetime | None = None,
        output: str | Path | None = None,
    ) -> ExportManifest:
        filters = ExportFilter(
            task_id=task_id,
            thread_id=thread_id,
            since=self._normalize_datetime(since),
            until=self._normalize_datetime(until),
        )

        async with self._store_group_scope() as store_group:
            tasks = await store_group.task_store.list_tasks()
            selected = [task for task in tasks if self._match_export_task(task, filters)]

            created_at = datetime.now(tz=UTC)
            output_path = self._resolve_export_output_path(output, created_at)
            events_by_task: dict[str, list[dict[str, Any]]] = {}
            artifacts_by_task: dict[str, list[dict[str, Any]]] = {}
            task_refs: list[ExportTaskRef] = []
            event_count = 0
            artifact_refs: list[str] = []

            for task in selected:
                events = await store_group.event_store.get_events_for_task(task.task_id)
                filtered_events = [
                    event for event in events if self._match_export_timestamp(event.ts, filters)
                ]
                artifacts = await store_group.artifact_store.list_artifacts_for_task(task.task_id)
                filtered_artifacts = [
                    artifact
                    for artifact in artifacts
                    if self._match_export_timestamp(artifact.ts, filters)
                ]

                has_matching_content = bool(filtered_events or filtered_artifacts)
                if self._has_time_window(filters) and not has_matching_content:
                    has_matching_content = self._match_export_timestamp(
                        task.created_at,
                        filters,
                    )
                if self._has_time_window(filters) and not has_matching_content:
                    continue

                task_refs.append(
                    ExportTaskRef(
                        task_id=task.task_id,
                        thread_id=task.thread_id,
                        title=task.title,
                        status=task.status.value,
                        created_at=task.created_at,
                    )
                )
                events_by_task[task.task_id] = [
                    self._serialize_event(event) for event in filtered_events
                ]
                artifacts_by_task[task.task_id] = [
                    self._serialize_artifact(artifact) for artifact in filtered_artifacts
                ]
                event_count += len(filtered_events)
                artifact_refs.extend(artifact.artifact_id for artifact in filtered_artifacts)

            manifest = ExportManifest(
                export_id=str(ULID()),
                created_at=created_at,
                output_path=str(output_path.resolve()),
                filters=filters,
                tasks=task_refs,
                event_count=event_count,
                artifact_refs=artifact_refs,
            )
            payload = {
                "manifest": manifest.model_dump(mode="json"),
                "events_by_task": events_by_task,
                "artifacts_by_task": artifacts_by_task,
            }
            await asyncio.to_thread(self._write_json_atomic, output_path, payload)
            return manifest

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

    def _create_bundle_sync(
        self,
        output_path: Path,
        bundle_id: str,
        created_at: datetime,
        scopes: list[BackupScope],
    ) -> BackupBundle:
        output_path.parent.mkdir(parents=True, exist_ok=True)

        warnings: list[str] = []
        notes = [
            "chats / session 记录包含在 SQLite snapshot 中，无单独数据文件。",
            "默认排除明文 secrets 与本地缓存目录。",
        ]

        with tempfile.TemporaryDirectory(prefix="octo-backup-") as tmp_dir:
            tmp_root = Path(tmp_dir)
            snapshot_path = tmp_root / "sqlite" / "octoagent.db"
            snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            self._snapshot_sqlite(snapshot_path)

            entries: list[BackupFileEntry] = []
            sources: dict[str, Path] = {}

            sqlite_rel = "sqlite/octoagent.db"
            entries.append(self._entry_from_path(BackupScope.SQLITE, sqlite_rel, snapshot_path))
            sources[sqlite_rel] = snapshot_path

            config_sources = {
                "config/octoagent.yaml": self._root / "octoagent.yaml",
                "config/litellm-config.yaml": self._root / "litellm-config.yaml",
            }
            for relative_path, source_path in config_sources.items():
                if source_path.exists():
                    entries.append(
                        self._entry_from_path(BackupScope.CONFIG, relative_path, source_path)
                    )
                    sources[relative_path] = source_path
                else:
                    warnings.append(f"未发现 {source_path.name}，bundle 中不会包含该配置文件。")

            entries.append(
                BackupFileEntry(
                    scope=BackupScope.ARTIFACTS,
                    relative_path="artifacts",
                    kind="directory",
                    required=False,
                )
            )
            if self._artifacts_dir.exists():
                for artifact_path in sorted(self._artifacts_dir.rglob("*")):
                    if not artifact_path.is_file():
                        continue
                    relative_path = (
                        Path("artifacts") / artifact_path.relative_to(self._artifacts_dir)
                    ).as_posix()
                    entries.append(
                        self._entry_from_path(
                            BackupScope.ARTIFACTS,
                            relative_path,
                            artifact_path,
                            required=False,
                        )
                    )
                    sources[relative_path] = artifact_path
            else:
                warnings.append("artifacts 目录不存在，bundle 仅包含空 artifacts 目录。")

            manifest = BackupManifest(
                manifest_version=_BUNDLE_MANIFEST_VERSION,
                bundle_id=bundle_id,
                created_at=created_at,
                source_project_root=str(self._root),
                scopes=scopes,
                files=entries,
                warnings=warnings,
                excluded_paths=DEFAULT_EXCLUDED_PATHS.copy(),
                sensitivity_level=SensitivityLevel.OPERATOR_SENSITIVE,
                notes=notes,
            )

            with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("artifacts/", b"")
                for relative_path, source_path in sources.items():
                    archive.write(source_path, arcname=relative_path)
                archive.writestr(
                    "manifest.json",
                    manifest.model_dump_json(indent=2),
                )

        return BackupBundle(
            bundle_id=bundle_id,
            output_path=str(output_path.resolve()),
            created_at=created_at,
            size_bytes=output_path.stat().st_size,
            manifest=manifest,
        )

    def _plan_restore_sync(self, bundle_path: Path, target_root: Path) -> RestorePlan:
        checked_at = datetime.now(tz=UTC)
        conflicts: list[RestoreConflict] = []
        warnings: list[str] = []
        restore_items: list[BackupFileEntry] = []
        manifest_version: int | None = None

        try:
            with zipfile.ZipFile(bundle_path) as archive:
                archive_entries = set(archive.namelist())
                if "manifest.json" not in archive_entries:
                    conflicts.append(
                        RestoreConflict(
                            conflict_type=RestoreConflictType.INVALID_BUNDLE,
                            severity=RestoreConflictSeverity.BLOCKING,
                            target_path=str(bundle_path),
                            message="bundle 缺少 manifest.json。",
                            suggested_action="确认 bundle 是否来自 octo backup create。",
                        )
                    )
                else:
                    try:
                        manifest = BackupManifest.model_validate(
                            json.loads(archive.read("manifest.json").decode("utf-8"))
                        )
                        manifest_version = manifest.manifest_version
                        restore_items = manifest.files
                        warnings.extend(manifest.warnings)
                    except Exception:
                        manifest = None
                        conflicts.append(
                            RestoreConflict(
                                conflict_type=RestoreConflictType.INVALID_BUNDLE,
                                severity=RestoreConflictSeverity.BLOCKING,
                                target_path=str(bundle_path),
                                message="manifest.json 解析失败。",
                                suggested_action="重新生成 backup bundle 后再执行 dry-run。",
                            )
                        )

                    if manifest is not None:
                        if manifest.manifest_version != _BUNDLE_MANIFEST_VERSION:
                            conflicts.append(
                                RestoreConflict(
                                    conflict_type=RestoreConflictType.SCHEMA_VERSION_MISMATCH,
                                    severity=RestoreConflictSeverity.BLOCKING,
                                    target_path=str(bundle_path),
                                    message=(
                                        "bundle manifest_version 不兼容："
                                        f"{manifest.manifest_version}"
                                    ),
                                    suggested_action="使用兼容版本重新导出 backup bundle。",
                                )
                            )

                        for item in manifest.files:
                            if item.kind == "directory":
                                continue
                            if item.relative_path not in archive_entries:
                                if item.required:
                                    conflicts.append(
                                        RestoreConflict(
                                            conflict_type=(
                                                RestoreConflictType.MISSING_REQUIRED_FILE
                                            ),
                                            severity=RestoreConflictSeverity.BLOCKING,
                                            target_path=item.relative_path,
                                            message=f"bundle 缺少必需文件: {item.relative_path}",
                                            suggested_action="重新生成完整 backup bundle。",
                                        )
                                    )
                                continue

                            if item.sha256:
                                actual_hash = hashlib.sha256(
                                    archive.read(item.relative_path)
                                ).hexdigest()
                                if actual_hash != item.sha256:
                                    conflicts.append(
                                        RestoreConflict(
                                            conflict_type=RestoreConflictType.CHECKSUM_MISMATCH,
                                            severity=RestoreConflictSeverity.BLOCKING,
                                            target_path=item.relative_path,
                                            message=f"文件校验失败: {item.relative_path}",
                                            suggested_action="重新获取未损坏的 bundle 后重试。",
                                        )
                                    )

                            target_path = self._map_restore_target(item.relative_path, target_root)
                            if target_path.exists():
                                conflicts.append(
                                    RestoreConflict(
                                        conflict_type=RestoreConflictType.PATH_EXISTS,
                                        severity=RestoreConflictSeverity.BLOCKING,
                                        target_path=str(target_path),
                                        message=f"目标路径已存在: {target_path}",
                                        suggested_action="改用空目录或先手动备份现有文件。",
                                    )
                                )

                writable_probe = self._first_existing_ancestor(target_root)
                if writable_probe.exists() and not os.access(writable_probe, os.W_OK):
                    conflicts.append(
                        RestoreConflict(
                            conflict_type=RestoreConflictType.TARGET_UNWRITABLE,
                            severity=RestoreConflictSeverity.BLOCKING,
                            target_path=str(writable_probe),
                            message=f"目标路径不可写: {writable_probe}",
                            suggested_action="修复目录权限后重试。",
                        )
                    )
        except zipfile.BadZipFile:
            conflicts.append(
                RestoreConflict(
                    conflict_type=RestoreConflictType.INVALID_BUNDLE,
                    severity=RestoreConflictSeverity.BLOCKING,
                    target_path=str(bundle_path),
                    message="bundle 不是有效 ZIP 文件。",
                    suggested_action="确认输入文件是否为 backup bundle。",
                )
            )

        return RestorePlan(
            bundle_path=str(bundle_path.resolve()),
            target_root=str(target_root.resolve()),
            compatible=True,
            checked_at=checked_at,
            manifest_version=manifest_version,
            restore_items=restore_items,
            conflicts=conflicts,
            warnings=warnings,
        )

    def _record_from_plan(self, plan: RestorePlan) -> RecoveryDrillRecord:
        if plan.compatible:
            summary = "最近一次 dry-run 无阻塞冲突。"
            failure_reason = ""
            status = RecoveryDrillStatus.PASSED
        else:
            blocking = [
                conflict
                for conflict in plan.conflicts
                if conflict.severity == RestoreConflictSeverity.BLOCKING
            ]
            summary = f"最近一次 dry-run 检测到 {len(blocking)} 个阻塞冲突。"
            failure_reason = blocking[0].message if blocking else "bundle 校验失败"
            status = RecoveryDrillStatus.FAILED

        return RecoveryDrillRecord(
            status=status,
            checked_at=plan.checked_at,
            bundle_path=plan.bundle_path,
            summary=summary,
            failure_reason=failure_reason,
            remediation=plan.next_actions,
            plan=plan,
        )

    def _resolve_bundle_output_path(
        self,
        output: str | Path | None,
        created_at: datetime,
        label: str | None,
    ) -> Path:
        filename = self._default_bundle_filename(created_at, label)
        return self._resolve_output_path(output, self._backups_dir, filename, ".zip")

    def _resolve_export_output_path(
        self,
        output: str | Path | None,
        created_at: datetime,
    ) -> Path:
        filename = f"octoagent-chats-export-{created_at.strftime('%Y%m%d-%H%M%S')}.json"
        return self._resolve_output_path(output, self._exports_dir, filename, ".json")

    def _resolve_output_path(
        self,
        output: str | Path | None,
        default_dir: Path,
        default_filename: str,
        required_suffix: str,
    ) -> Path:
        if output is None:
            return (default_dir / default_filename).resolve()

        candidate = Path(output).expanduser()
        if not candidate.is_absolute():
            candidate = (self._root / candidate).resolve()
        if candidate.suffix.lower() == required_suffix:
            return candidate
        return (candidate / default_filename).resolve()

    def _default_bundle_filename(self, created_at: datetime, label: str | None) -> str:
        suffix = ""
        if label:
            normalized = "".join(
                ch.lower() if ch.isalnum() else "-"
                for ch in label.strip()
            ).strip("-")
            if normalized:
                suffix = f"-{normalized}"
        return f"octoagent-backup-{created_at.strftime('%Y%m%d-%H%M%S')}{suffix}.zip"

    def _snapshot_sqlite(self, snapshot_path: Path) -> None:
        source_path = self._db_path
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source = sqlite3.connect(str(source_path))
        target = sqlite3.connect(str(snapshot_path))
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()

    def _entry_from_path(
        self,
        scope: BackupScope,
        relative_path: str,
        source_path: Path,
        *,
        required: bool = True,
    ) -> BackupFileEntry:
        return BackupFileEntry(
            scope=scope,
            relative_path=relative_path,
            kind="file",
            required=required,
            size_bytes=source_path.stat().st_size,
            sha256=self._hash_file(source_path),
        )

    def _hash_file(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    def _map_restore_target(self, relative_path: str, target_root: Path) -> Path:
        bundle_path = Path(relative_path)
        if bundle_path.parts[:1] == ("sqlite",):
            return target_root / "data" / "sqlite" / bundle_path.name
        if bundle_path.parts[:1] == ("artifacts",):
            tail = bundle_path.relative_to("artifacts")
            return target_root / "data" / "artifacts" / tail
        if bundle_path.parts[:1] == ("config",):
            return target_root / bundle_path.name
        return target_root / bundle_path

    def _has_time_window(self, filters: ExportFilter) -> bool:
        return filters.since is not None or filters.until is not None

    def _match_export_timestamp(self, timestamp: datetime, filters: ExportFilter) -> bool:
        if filters.since and timestamp < filters.since:
            return False
        return not (filters.until and timestamp > filters.until)

    def _first_existing_ancestor(self, path: Path) -> Path:
        current = path
        while not current.exists() and current != current.parent:
            current = current.parent
        return current

    def _match_export_task(self, task: Task, filters: ExportFilter) -> bool:
        explicitly_selected = (
            (filters.task_id is not None and task.task_id == filters.task_id)
            or (filters.thread_id is not None and task.thread_id == filters.thread_id)
        )
        if (
            task.requester.channel == "system"
            and task.scope_id.startswith("ops/")
            and not explicitly_selected
        ):
            return False
        if filters.task_id and task.task_id != filters.task_id:
            return False
        return not (filters.thread_id and task.thread_id != filters.thread_id)

    def _normalize_datetime(self, value: str | datetime | None) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            normalized = value
        else:
            normalized_text = value.strip()
            if normalized_text.endswith("Z"):
                normalized_text = normalized_text[:-1] + "+00:00"
            try:
                normalized = datetime.fromisoformat(normalized_text)
            except ValueError as exc:
                raise ValueError(f"时间格式无效: {value}") from exc

        if normalized.tzinfo is None or normalized.utcoffset() is None:
            raise ValueError(f"时间必须包含时区: {value}")
        return normalized.astimezone(UTC)

    def _serialize_event(self, event: Event) -> dict[str, Any]:
        return {
            "event_id": event.event_id,
            "task_seq": event.task_seq,
            "ts": event.ts.isoformat(),
            "type": event.type.value,
            "actor": event.actor.value,
            "payload": event.payload,
        }

    def _serialize_artifact(self, artifact: Artifact) -> dict[str, Any]:
        return {
            "artifact_id": artifact.artifact_id,
            "name": artifact.name,
            "size": artifact.size,
            "storage_ref": artifact.storage_ref,
        }

    def _write_json_atomic(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        with tempfile.NamedTemporaryFile(
            "w",
            dir=path.parent,
            suffix=".tmp",
            encoding="utf-8",
            delete=False,
        ) as handle:
            handle.write(text)
            tmp_path = Path(handle.name)
        tmp_path.replace(path)
