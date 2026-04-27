"""Feature 025: Project / Workspace migration gate。"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha1
from pathlib import Path
from typing import Any

import aiosqlite
from dotenv import dotenv_values
from octoagent.core.config import get_artifacts_dir, get_db_path
from enum import StrEnum
from typing import Any as _Any

from pydantic import BaseModel as _BaseModel, Field as _Field

from octoagent.core.models import (
    Project,
    ProjectBinding,
    ProjectBindingType,
    ProjectMigrationRollbackPlan,
    ProjectMigrationRun,
    ProjectMigrationStatus,
    ProjectMigrationSummary,
    ProjectMigrationValidation,
)


# DEPRECATED: workspace 概念已废弃，本地保留仅供历史迁移工具使用
class WorkspaceKind(StrEnum):
    PRIMARY = "primary"
    CHAT = "chat"
    OPS = "ops"
    LEGACY = "legacy"


class Workspace(_BaseModel):
    """迁移工具本地使用的 Workspace 兼容类（workspace 概念已废弃）。"""

    workspace_id: str = _Field(min_length=1)
    project_id: str = _Field(min_length=1)
    slug: str = _Field(min_length=1)
    name: str = _Field(min_length=1)
    kind: WorkspaceKind = WorkspaceKind.PRIMARY
    root_path: str = ""
    created_at: _Any = None
    updated_at: _Any = None
    metadata: dict[str, _Any] = _Field(default_factory=dict)
from octoagent.core.store import StoreGroup, create_store_group
from ulid import ULID

from octoagent.gateway.services.config.config_wizard import load_config
from .recovery_status_store import RecoveryStatusStore

DEFAULT_PROJECT_ID = "project-default"
DEFAULT_PROJECT_SLUG = "default"
DEFAULT_WORKSPACE_ID = "workspace-default-primary"
DEFAULT_WORKSPACE_SLUG = "primary"

_MEMORY_TABLES = (
    "memory_fragments",
    "memory_sor",
    "memory_write_proposals",
    "memory_vault",
)
_IMPORT_TABLES = (
    "chat_import_batches",
    "chat_import_cursors",
    "chat_import_dedupe",
    "chat_import_windows",
    "chat_import_reports",
)


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


def _resolve_path_from_root(path: str | Path, project_root: Path) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        return (project_root / candidate).resolve()
    return candidate.resolve()


def _resolve_db_path(project_root: Path) -> Path:
    return _resolve_path_from_root(get_db_path(), project_root)


def _resolve_artifacts_dir(project_root: Path) -> Path:
    return _resolve_path_from_root(get_artifacts_dir(), project_root)


def _resolve_data_dir(project_root: Path) -> Path:
    if env_data_dir := os.environ.get("OCTOAGENT_DATA_DIR"):
        return _resolve_path_from_root(env_data_dir, project_root)

    db_path = _resolve_db_path(project_root)
    if db_path.parent.name == "sqlite":
        return db_path.parent.parent.resolve()

    artifacts_dir = _resolve_artifacts_dir(project_root)
    if artifacts_dir.name == "artifacts":
        return artifacts_dir.parent.resolve()

    return (project_root / "data").resolve()


def _normalize_jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _normalize_jsonable(val)
            for key, val in value.items()
            if val not in ({}, [], None, "")
        }
    if isinstance(value, set):
        return sorted(_normalize_jsonable(item) for item in value if item not in ("", None))
    if isinstance(value, list):
        return [_normalize_jsonable(item) for item in value if item not in ("", None)]
    return value


def _merge_metadata(target: dict[str, Any], updates: dict[str, Any]) -> None:
    for key, value in updates.items():
        if value in ("", None, [], {}, set()):
            continue
        existing = target.get(key)
        if existing is None:
            target[key] = value
            continue
        if isinstance(existing, dict) and isinstance(value, dict):
            _merge_metadata(existing, value)
            continue
        if isinstance(existing, set):
            if isinstance(value, set):
                existing.update(value)
            elif isinstance(value, list):
                existing.update(item for item in value if item not in ("", None))
            else:
                existing.add(value)
            continue
        if isinstance(existing, list):
            merged = set(existing)
            if isinstance(value, set | list):
                merged.update(value)
            else:
                merged.add(value)
            target[key] = merged
            continue
        if isinstance(value, set):
            target[key] = {existing, *value}
            continue
        if isinstance(value, list):
            target[key] = {existing, *value}
            continue
        if existing != value:
            target[key] = {existing, value}


@dataclass
class _BindingDraft:
    binding_type: ProjectBindingType
    binding_key: str
    binding_value: str
    source: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class _LegacyDiscovery:
    drafts: dict[tuple[ProjectBindingType, str], _BindingDraft] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    legacy_counts: dict[str, int] = field(default_factory=dict)
    tables: set[str] = field(default_factory=set)

    def add_binding(
        self,
        *,
        binding_type: ProjectBindingType,
        binding_key: str,
        binding_value: str,
        source: str,
        category: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        normalized_key = binding_key.strip()
        if not normalized_key:
            return
        draft_key = (binding_type, normalized_key)
        if draft_key not in self.drafts:
            self.drafts[draft_key] = _BindingDraft(
                binding_type=binding_type,
                binding_key=normalized_key,
                binding_value=binding_value,
                source=source,
                metadata={"sources": {source}},
            )
            self.legacy_counts[category] = self.legacy_counts.get(category, 0) + 1
        else:
            self.drafts[draft_key].metadata.setdefault("sources", set()).add(source)

        if metadata:
            _merge_metadata(self.drafts[draft_key].metadata, metadata)


@dataclass
class _MigrationDraft:
    run: ProjectMigrationRun
    project: Project
    workspace: Workspace
    bindings_to_create: list[ProjectBinding]
    default_project_exists: bool
    primary_workspace_exists: bool


class ProjectWorkspaceMigrationService:
    """Project / Workspace migration orchestration。"""

    def __init__(
        self,
        project_root: Path,
        *,
        store_group: StoreGroup | None = None,
    ) -> None:
        self._root = project_root.resolve()
        self._data_dir = _resolve_data_dir(self._root)
        self._db_path = _resolve_db_path(self._root)
        self._artifacts_dir = _resolve_artifacts_dir(self._root)
        self._store_group = store_group

    async def plan(self) -> ProjectMigrationRun:
        async with self._store_group_scope() as store_group:
            draft = await self._build_draft(
                store_group=store_group,
                status=ProjectMigrationStatus.DRY_RUN,
            )
            draft.run.completed_at = _utc_now()
            return draft.run

    async def apply(self) -> ProjectMigrationRun:
        async with self._store_group_scope() as store_group:
            draft = await self._build_draft(
                store_group=store_group,
                status=ProjectMigrationStatus.PENDING,
            )
            run = draft.run
            await store_group.project_store.save_migration_run(run)
            await store_group.conn.commit()

            try:
                created_project_ids: list[str] = []
                created_workspace_ids: list[str] = []
                created_binding_ids: list[str] = []
                if not draft.default_project_exists:
                    _, created = await store_group.project_store.create_project(draft.project)
                    if created:
                        created_project_ids.append(draft.project.project_id)
                for binding in draft.bindings_to_create:
                    stored_binding, created = await store_group.project_store.create_binding(
                        binding
                    )
                    if created and stored_binding.migration_run_id == run.run_id:
                        created_binding_ids.append(stored_binding.binding_id)

                run.rollback_plan.delete_project_ids = created_project_ids
                run.rollback_plan.delete_workspace_ids = created_workspace_ids
                run.rollback_plan.delete_binding_ids = created_binding_ids

                run.validation = await self._validate_state(
                    store_group=store_group,
                    project=draft.project,
                    workspace=draft.workspace,
                    discovery=await self._discover_legacy_metadata(store_group.conn),
                    planned_bindings=[],
                    project_will_exist=True,
                    workspace_will_exist=True,
                )
                if not run.validation.ok:
                    await store_group.conn.rollback()
                    run.status = ProjectMigrationStatus.FAILED
                    run.completed_at = _utc_now()
                    run.error_message = self._validation_error_message(run.validation)
                    run.rollback_plan.notes.append("validation 未通过，已回滚本次未提交写入。")
                    await store_group.project_store.save_migration_run(run)
                    await store_group.conn.commit()
                    return run

                run.status = ProjectMigrationStatus.SUCCEEDED
                run.completed_at = _utc_now()
                await store_group.project_store.save_migration_run(run)
                await store_group.conn.commit()
                return run
            except Exception as exc:
                await store_group.conn.rollback()
                run.status = ProjectMigrationStatus.FAILED
                run.completed_at = _utc_now()
                run.error_message = str(exc)
                run.rollback_plan.notes.append("apply 异常，已回滚本次未提交写入。")
                await store_group.project_store.save_migration_run(run)
                await store_group.conn.commit()
                return run

    async def ensure_default_project(self) -> ProjectMigrationRun:
        plan = await self.plan()
        if not self._has_pending_changes(plan):
            if not plan.validation.ok:
                raise RuntimeError(self._validation_error_message(plan.validation))
            return plan

        run = await self.apply()
        if run.status != ProjectMigrationStatus.SUCCEEDED:
            raise RuntimeError(run.error_message or self._validation_error_message(run.validation))
        return run

    async def rollback(self, run_id: str = "latest") -> ProjectMigrationRun:
        async with self._store_group_scope() as store_group:
            target_run = await self._resolve_rollback_run(store_group, run_id)
            if target_run is None:
                raise ValueError("未找到可回滚的 migration run")
            if target_run.status != ProjectMigrationStatus.SUCCEEDED:
                raise ValueError("仅允许回滚成功 apply 的 migration run")

            await store_group.project_store.delete_bindings_for_run(
                target_run.run_id,
                target_run.rollback_plan.delete_binding_ids
            )
            # workspace 概念已废弃，跳过 delete_workspaces
            await store_group.project_store.delete_projects(
                target_run.rollback_plan.delete_project_ids
            )
            target_run.status = ProjectMigrationStatus.ROLLED_BACK
            target_run.completed_at = _utc_now()
            target_run.rollback_plan.notes.append("已按 rollback_plan 删除当前 run 创建的记录。")
            await store_group.project_store.save_migration_run(target_run)
            await store_group.conn.commit()
            return target_run

    async def _resolve_rollback_run(
        self,
        store_group: StoreGroup,
        run_id: str,
    ) -> ProjectMigrationRun | None:
        if run_id != "latest":
            return await store_group.project_store.get_migration_run(run_id)

        return await store_group.project_store.get_latest_migration_run(
            str(self._root),
            statuses=(ProjectMigrationStatus.SUCCEEDED,),
        )

    async def _build_draft(
        self,
        *,
        store_group: StoreGroup,
        status: ProjectMigrationStatus,
    ) -> _MigrationDraft:
        existing_project = await store_group.project_store.get_project(DEFAULT_PROJECT_ID)
        if existing_project is None:
            existing_project = await store_group.project_store.get_project_by_slug(
                DEFAULT_PROJECT_SLUG
            )

        project = existing_project or Project(
            project_id=DEFAULT_PROJECT_ID,
            slug=DEFAULT_PROJECT_SLUG,
            name="Default Project",
            description="M2 legacy instance auto-migrated default project",
            is_default=True,
            metadata={
                "migration_source": "m2_legacy_instance",
                "project_root": str(self._root),
            },
        )

        # workspace 概念已废弃，迁移时仍创建 placeholder 保持数据兼容
        workspace = Workspace(
            workspace_id=DEFAULT_WORKSPACE_ID,
            project_id=project.project_id,
            slug=DEFAULT_WORKSPACE_SLUG,
            name="Primary Workspace",
            kind=WorkspaceKind.PRIMARY,
            root_path=str(self._root),
            metadata={
                "project_root": str(self._root),
                "data_dir": str(self._data_dir),
                "artifacts_dir": str(self._artifacts_dir),
            },
        )

        discovery = await self._discover_legacy_metadata(store_group.conn)
        run_id = str(ULID())
        bindings_to_create = await self._build_bindings_to_create(
            store_group=store_group,
            project=project,
            workspace=workspace,
            discovery=discovery,
            run_id=run_id,
        )

        summary = ProjectMigrationSummary(
            created_project=existing_project is None,
            created_workspace=False,  # workspace 概念已废弃
            binding_counts=self._count_bindings(bindings_to_create),
            legacy_counts=discovery.legacy_counts,
        )
        rollback_plan = ProjectMigrationRollbackPlan(
            run_id=run_id,
            delete_binding_ids=[binding.binding_id for binding in bindings_to_create],
            delete_workspace_ids=[],  # workspace 概念已废弃
            delete_project_ids=[project.project_id] if existing_project is None else [],
            notes=[
                "仅删除本次 migration 创建的 project/workspace/binding 记录。",
                "legacy tasks/events/artifacts/memory/import/backup 数据保持不变。",
            ],
        )
        validation = await self._validate_state(
            store_group=store_group,
            project=project,
            workspace=workspace,
            discovery=discovery,
            planned_bindings=bindings_to_create,
            project_will_exist=True,
            workspace_will_exist=True,
        )
        validation.warnings.extend(discovery.warnings)

        run = ProjectMigrationRun(
            run_id=run_id,
            project_root=str(self._root),
            status=status,
            summary=summary,
            validation=validation,
            rollback_plan=rollback_plan,
        )
        return _MigrationDraft(
            run=run,
            project=project,
            workspace=workspace,
            bindings_to_create=bindings_to_create,
            default_project_exists=existing_project is not None,
            primary_workspace_exists=True,  # workspace 概念已废弃
        )

    async def _build_bindings_to_create(
        self,
        *,
        store_group: StoreGroup,
        project: Project,
        workspace: Workspace,
        discovery: _LegacyDiscovery,
        run_id: str,
    ) -> list[ProjectBinding]:
        bindings: list[ProjectBinding] = []
        for (binding_type, binding_key), draft in sorted(
            discovery.drafts.items(),
            key=lambda item: (item[0][0].value, item[0][1]),
        ):
            existing = await store_group.project_store.get_binding(
                project.project_id,
                binding_type,
                binding_key,
            )
            if existing is not None:
                continue
            workspace_id = (
                workspace.workspace_id
                if binding_type in {
                    ProjectBindingType.SCOPE,
                    ProjectBindingType.MEMORY_SCOPE,
                    ProjectBindingType.IMPORT_SCOPE,
                }
                else None
            )
            bindings.append(
                ProjectBinding(
                    binding_id=self._binding_id(
                        project_id=project.project_id,
                        binding_type=binding_type,
                        binding_key=binding_key,
                    ),
                    project_id=project.project_id,
                    workspace_id=workspace_id,
                    binding_type=binding_type,
                    binding_key=binding_key,
                    binding_value=draft.binding_value,
                    source=draft.source,
                    metadata=_normalize_jsonable(draft.metadata),
                    migration_run_id=run_id,
                )
            )
        return bindings

    async def _validate_state(
        self,
        *,
        store_group: StoreGroup,
        project: Project,
        workspace: Workspace,
        discovery: _LegacyDiscovery,
        planned_bindings: list[ProjectBinding],
        project_will_exist: bool,
        workspace_will_exist: bool,
    ) -> ProjectMigrationValidation:
        validation = ProjectMigrationValidation()
        projects = await store_group.project_store.list_projects()
        default_count = sum(1 for item in projects if item.is_default)
        if project_will_exist and not any(
            item.project_id == project.project_id for item in projects
        ):
            default_count += 1 if project.is_default else 0
        if default_count != 1:
            validation.blocking_issues.append(
                f"default project 数量非法: 预期 1，实际 {default_count}"
            )

        # NOTE: workspace 概念已废弃，跳过 workspace 校验

        expected_keys = {
            (binding_type.value, binding_key)
            for binding_type, binding_key in discovery.drafts
        }
        existing_bindings = await store_group.project_store.list_bindings(project.project_id)
        actual_keys = {
            (binding.binding_type.value, binding.binding_key)
            for binding in existing_bindings
        }
        actual_keys.update(
            (binding.binding_type.value, binding.binding_key) for binding in planned_bindings
        )
        missing = sorted(
            f"{binding_type}:{binding_key}"
            for binding_type, binding_key in expected_keys
            if (binding_type, binding_key) not in actual_keys
        )
        validation.missing_binding_keys.extend(missing)

        cursor = await store_group.conn.execute("PRAGMA integrity_check;")
        row = await cursor.fetchone()
        if row is None or str(row[0]).lower() != "ok":
            validation.blocking_issues.append("SQLite integrity_check 未通过")
        validation.integrity_checks.append(f"integrity_check={row[0] if row else 'missing'}")

        cursor = await store_group.conn.execute("PRAGMA foreign_key_check;")
        fk_rows = await cursor.fetchall()
        if fk_rows:
            validation.blocking_issues.append("SQLite foreign_key_check 未通过")
            validation.integrity_checks.append(f"foreign_key_check={len(fk_rows)} errors")
        else:
            validation.integrity_checks.append("foreign_key_check=ok")

        return validation

    async def _discover_legacy_metadata(self, conn: aiosqlite.Connection) -> _LegacyDiscovery:
        discovery = _LegacyDiscovery()
        discovery.tables = await self._list_tables(conn)
        await self._discover_task_metadata(conn, discovery)
        await self._discover_memory_metadata(conn, discovery)
        await self._discover_import_metadata(conn, discovery)
        self._discover_backup_metadata(discovery)
        self._discover_env_metadata(discovery)
        return discovery

    async def _list_tables(self, conn: aiosqlite.Connection) -> set[str]:
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
        rows = await cursor.fetchall()
        return {str(row[0]) for row in rows}

    async def _discover_task_metadata(
        self,
        conn: aiosqlite.Connection,
        discovery: _LegacyDiscovery,
    ) -> None:
        if "tasks" not in discovery.tables:
            return

        cursor = await conn.execute(
            "SELECT task_id, scope_id, thread_id, requester FROM tasks"
        )
        rows = await cursor.fetchall()
        for row in rows:
            task_id = str(row[0])
            scope_id = str(row[1] or "").strip()
            thread_id = str(row[2] or "").strip()
            channel = ""
            requester_raw = row[3] or "{}"
            try:
                requester = json.loads(requester_raw)
                channel = str(requester.get("channel", "")).strip()
            except Exception:
                discovery.warnings.append(f"tasks.requester JSON 无法解析，task_id={task_id}")

            if scope_id:
                discovery.add_binding(
                    binding_type=ProjectBindingType.SCOPE,
                    binding_key=scope_id,
                    binding_value=scope_id,
                    source="tasks",
                    category="task_scopes",
                    metadata={
                        "task_ids": {task_id},
                        "thread_ids": {thread_id} if thread_id else set(),
                        "channels": {channel} if channel else set(),
                    },
                )
            if channel:
                discovery.add_binding(
                    binding_type=ProjectBindingType.CHANNEL,
                    binding_key=channel,
                    binding_value=channel,
                    source="tasks",
                    category="channels",
                    metadata={
                        "task_ids": {task_id},
                        "thread_ids": {thread_id} if thread_id else set(),
                        "scope_ids": {scope_id} if scope_id else set(),
                    },
                )

    async def _discover_memory_metadata(
        self,
        conn: aiosqlite.Connection,
        discovery: _LegacyDiscovery,
    ) -> None:
        present_tables = [table for table in _MEMORY_TABLES if table in discovery.tables]
        if not present_tables:
            discovery.warnings.append("memory 子系统未初始化，跳过 memory scope 扫描。")
            return

        for table in present_tables:
            cursor = await conn.execute(
                f"SELECT DISTINCT scope_id FROM {table} WHERE scope_id != ''"
            )
            rows = await cursor.fetchall()
            for row in rows:
                scope_id = str(row[0]).strip()
                if not scope_id:
                    continue
                discovery.add_binding(
                    binding_type=ProjectBindingType.MEMORY_SCOPE,
                    binding_key=scope_id,
                    binding_value=scope_id,
                    source=table,
                    category="memory_scopes",
                    metadata={"tables": {table}},
                )

    async def _discover_import_metadata(
        self,
        conn: aiosqlite.Connection,
        discovery: _LegacyDiscovery,
    ) -> None:
        present_tables = [table for table in _IMPORT_TABLES if table in discovery.tables]
        if not present_tables:
            discovery.warnings.append("chat import 子系统未初始化，跳过 import scope 扫描。")
            return

        if "chat_import_batches" in discovery.tables:
            cursor = await conn.execute(
                """
                SELECT batch_id, scope_id, channel, thread_id, source_id, input_path, report_id
                FROM chat_import_batches
                """
            )
            rows = await cursor.fetchall()
            for row in rows:
                batch_id = str(row[0] or "").strip()
                scope_id = str(row[1] or "").strip()
                channel = str(row[2] or "").strip()
                thread_id = str(row[3] or "").strip()
                source_id = str(row[4] or "").strip()
                input_path = str(row[5] or "").strip()
                report_id = str(row[6] or "").strip()
                if scope_id:
                    discovery.add_binding(
                        binding_type=ProjectBindingType.IMPORT_SCOPE,
                        binding_key=scope_id,
                        binding_value=scope_id,
                        source="chat_import_batches",
                        category="import_scopes",
                        metadata={
                            "batch_ids": {batch_id} if batch_id else set(),
                            "channels": {channel} if channel else set(),
                            "thread_ids": {thread_id} if thread_id else set(),
                            "source_ids": {source_id} if source_id else set(),
                            "input_paths": {input_path} if input_path else set(),
                            "report_ids": {report_id} if report_id else set(),
                        },
                    )
                if channel:
                    discovery.add_binding(
                        binding_type=ProjectBindingType.CHANNEL,
                        binding_key=channel,
                        binding_value=channel,
                        source="chat_import_batches",
                        category="channels",
                        metadata={
                            "import_scopes": {scope_id} if scope_id else set(),
                            "thread_ids": {thread_id} if thread_id else set(),
                            "source_ids": {source_id} if source_id else set(),
                        },
                    )

        if "chat_import_cursors" in discovery.tables:
            cursor = await conn.execute(
                "SELECT source_id, scope_id FROM chat_import_cursors"
            )
            rows = await cursor.fetchall()
            for row in rows:
                source_id = str(row[0] or "").strip()
                scope_id = str(row[1] or "").strip()
                if scope_id:
                    discovery.add_binding(
                        binding_type=ProjectBindingType.IMPORT_SCOPE,
                        binding_key=scope_id,
                        binding_value=scope_id,
                        source="chat_import_cursors",
                        category="import_scopes",
                        metadata={"cursor_source_ids": {source_id} if source_id else set()},
                    )

        if "chat_import_dedupe" in discovery.tables:
            cursor = await conn.execute(
                "SELECT source_id, scope_id, batch_id FROM chat_import_dedupe"
            )
            rows = await cursor.fetchall()
            for row in rows:
                source_id = str(row[0] or "").strip()
                scope_id = str(row[1] or "").strip()
                batch_id = str(row[2] or "").strip()
                if scope_id:
                    discovery.add_binding(
                        binding_type=ProjectBindingType.IMPORT_SCOPE,
                        binding_key=scope_id,
                        binding_value=scope_id,
                        source="chat_import_dedupe",
                        category="import_scopes",
                        metadata={
                            "dedupe_source_ids": {source_id} if source_id else set(),
                            "batch_ids": {batch_id} if batch_id else set(),
                        },
                    )

        if "chat_import_windows" in discovery.tables:
            cursor = await conn.execute(
                "SELECT scope_id, batch_id, artifact_id FROM chat_import_windows"
            )
            rows = await cursor.fetchall()
            for row in rows:
                scope_id = str(row[0] or "").strip()
                batch_id = str(row[1] or "").strip()
                artifact_id = str(row[2] or "").strip()
                if scope_id:
                    discovery.add_binding(
                        binding_type=ProjectBindingType.IMPORT_SCOPE,
                        binding_key=scope_id,
                        binding_value=scope_id,
                        source="chat_import_windows",
                        category="import_scopes",
                        metadata={
                            "batch_ids": {batch_id} if batch_id else set(),
                            "artifact_ids": {artifact_id} if artifact_id else set(),
                        },
                    )

        if "chat_import_reports" in discovery.tables:
            cursor = await conn.execute(
                """
                SELECT report_id, batch_id, source_id, scope_id, artifact_refs
                FROM chat_import_reports
                """
            )
            rows = await cursor.fetchall()
            for row in rows:
                report_id = str(row[0] or "").strip()
                batch_id = str(row[1] or "").strip()
                source_id = str(row[2] or "").strip()
                scope_id = str(row[3] or "").strip()
                artifact_refs = set()
                try:
                    artifact_refs = set(json.loads(row[4] or "[]"))
                except Exception:
                    artifact_refs = set()
                if scope_id:
                    discovery.add_binding(
                        binding_type=ProjectBindingType.IMPORT_SCOPE,
                        binding_key=scope_id,
                        binding_value=scope_id,
                        source="chat_import_reports",
                        category="import_scopes",
                        metadata={
                            "report_ids": {report_id} if report_id else set(),
                            "batch_ids": {batch_id} if batch_id else set(),
                            "source_ids": {source_id} if source_id else set(),
                            "artifact_ids": artifact_refs,
                        },
                    )

    def _discover_backup_metadata(self, discovery: _LegacyDiscovery) -> None:
        latest_backup_path = self._data_dir / "ops" / "latest-backup.json"
        recovery_drill_path = self._data_dir / "ops" / "recovery-drill.json"
        backups_dir = self._data_dir / "backups"
        exports_dir = self._data_dir / "exports"

        metadata: dict[str, Any] = {
            "project_root": str(self._root),
            "data_dir": str(self._data_dir),
        }
        has_backup_state = False
        if backups_dir.exists():
            metadata["backups_dir"] = str(backups_dir)
            has_backup_state = True
        if exports_dir.exists():
            metadata["exports_dir"] = str(exports_dir)
            has_backup_state = True
        if latest_backup_path.exists():
            metadata["latest_backup_path"] = str(latest_backup_path)
            has_backup_state = True
        if recovery_drill_path.exists():
            metadata["recovery_drill_path"] = str(recovery_drill_path)
            has_backup_state = True

        status_store = RecoveryStatusStore(self._root, data_dir=self._data_dir)
        latest_backup = status_store.load_latest_backup()
        if latest_backup is not None:
            has_backup_state = True
            metadata["latest_backup_bundle_id"] = latest_backup.bundle_id
            metadata["latest_backup_output_path"] = latest_backup.output_path
            metadata["latest_backup_scopes"] = {
                scope.value for scope in latest_backup.manifest.scopes
            }
            metadata["latest_backup_manifest_root"] = latest_backup.manifest.source_project_root

        recovery_drill = status_store.load_recovery_drill()
        if recovery_drill.checked_at is not None or recovery_drill.bundle_path:
            has_backup_state = True
            metadata["recovery_drill_status"] = recovery_drill.status.value
            metadata["recovery_drill_bundle_path"] = recovery_drill.bundle_path

        if has_backup_state:
            discovery.add_binding(
                binding_type=ProjectBindingType.BACKUP_ROOT,
                binding_key=str(self._data_dir),
                binding_value=str(self._data_dir),
                source="backup_service",
                category="backup_roots",
                metadata=metadata,
            )

    def _discover_env_metadata(self, discovery: _LegacyDiscovery) -> None:
        env_paths = [
            self._root / ".env",
            self._root / ".env.litellm",
        ]
        for env_path in env_paths:
            if not env_path.exists():
                continue
            relative_path = env_path.relative_to(self._root).as_posix()
            discovery.add_binding(
                binding_type=ProjectBindingType.ENV_FILE,
                binding_key=relative_path,
                binding_value=str(env_path),
                source=relative_path,
                category="env_files",
                metadata={"project_root": str(self._root)},
            )
            try:
                env_items = dotenv_values(env_path)
            except Exception as exc:
                discovery.warnings.append(f"{relative_path} 解析失败: {exc}")
                continue
            for env_name in env_items:
                if not env_name:
                    continue
                discovery.add_binding(
                    binding_type=ProjectBindingType.ENV_REF,
                    binding_key=str(env_name),
                    binding_value=str(env_name),
                    source=relative_path,
                    category="env_refs",
                    metadata={"files": {relative_path}},
                )

        yaml_path = self._root / "octoagent.yaml"
        if not yaml_path.exists():
            return

        try:
            cfg = load_config(self._root)
        except Exception as exc:
            discovery.warnings.append(f"octoagent.yaml 解析失败，跳过 env bridge 扫描: {exc}")
            return

        if cfg is None:
            return
        # F081 cleanup：移除 master_key_env 注册（runtime.master_key_env 已删除）
        for provider in cfg.providers:
            self._add_yaml_env_ref(
                discovery,
                provider.api_key_env,
                f"providers.{provider.id}.api_key_env",
            )
        telegram = cfg.channels.telegram
        self._add_yaml_env_ref(
            discovery,
            telegram.bot_token_env,
            "channels.telegram.bot_token_env",
        )
        self._add_yaml_env_ref(
            discovery,
            telegram.webhook_secret_env,
            "channels.telegram.webhook_secret_env",
        )
        if telegram.enabled:
            discovery.add_binding(
                binding_type=ProjectBindingType.CHANNEL,
                binding_key="telegram",
                binding_value="telegram",
                source="octoagent.yaml",
                category="channels",
                metadata={
                    "mode": telegram.mode,
                    "allow_users": set(telegram.allow_users),
                    "allowed_groups": set(telegram.allowed_groups),
                    "group_allow_users": set(telegram.group_allow_users),
                },
            )

    def _add_yaml_env_ref(
        self,
        discovery: _LegacyDiscovery,
        env_name: str,
        config_path: str,
    ) -> None:
        if not env_name:
            return
        discovery.add_binding(
            binding_type=ProjectBindingType.ENV_REF,
            binding_key=env_name,
            binding_value=env_name,
            source="octoagent.yaml",
            category="env_refs",
            metadata={"config_paths": {config_path}, "files": {"octoagent.yaml"}},
        )

    def _binding_id(
        self,
        *,
        project_id: str,
        binding_type: ProjectBindingType,
        binding_key: str,
    ) -> str:
        digest = sha1(
            f"{project_id}:{binding_type.value}:{binding_key}".encode()
        ).hexdigest()[:20]
        return f"binding-{binding_type.value}-{digest}"

    def _count_bindings(self, bindings: list[ProjectBinding]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for binding in bindings:
            key = binding.binding_type.value
            counts[key] = counts.get(key, 0) + 1
        return counts

    def _has_pending_changes(self, run: ProjectMigrationRun) -> bool:
        if run.summary.created_project or run.summary.created_workspace:
            return True
        return any(count > 0 for count in run.summary.binding_counts.values())

    def _validation_error_message(self, validation: ProjectMigrationValidation) -> str:
        parts: list[str] = []
        if validation.blocking_issues:
            parts.extend(validation.blocking_issues)
        if validation.missing_binding_keys:
            parts.append(
                "缺失 bindings: " + ", ".join(validation.missing_binding_keys[:10])
            )
        if not parts:
            return "project/workspace migration validation 失败"
        return "；".join(parts)

    @asynccontextmanager
    async def _store_group_scope(self) -> AsyncIterator[StoreGroup]:
        if self._store_group is not None:
            yield self._store_group
            return

        store_group = await create_store_group(str(self._db_path), self._artifacts_dir)
        try:
            yield store_group
        finally:
            await store_group.conn.close()
