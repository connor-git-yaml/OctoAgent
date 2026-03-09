"""Feature 025: Project / Workspace SQLite Store。"""

from __future__ import annotations

import json
from datetime import datetime

import aiosqlite

from ..models.project import (
    Project,
    ProjectBinding,
    ProjectBindingType,
    ProjectMigrationRun,
    ProjectMigrationStatus,
    ProjectSecretBinding,
    ProjectSelectorState,
    SecretTargetKind,
    Workspace,
    WorkspaceKind,
)


class SqliteProjectStore:
    """project/workspace/binding/migration_run 访问层。"""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def save_project(self, project: Project) -> Project:
        await self._conn.execute(
            """
            INSERT INTO projects (
                project_id, slug, name, description, status, is_default,
                default_agent_profile_id, metadata, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id) DO UPDATE SET
                slug = excluded.slug,
                name = excluded.name,
                description = excluded.description,
                status = excluded.status,
                is_default = excluded.is_default,
                default_agent_profile_id = excluded.default_agent_profile_id,
                metadata = excluded.metadata,
                updated_at = excluded.updated_at
            """,
            (
                project.project_id,
                project.slug,
                project.name,
                project.description,
                project.status.value,
                1 if project.is_default else 0,
                project.default_agent_profile_id,
                json.dumps(project.metadata, ensure_ascii=False),
                project.created_at.isoformat(),
                project.updated_at.isoformat(),
            ),
        )
        return project

    async def create_project(self, project: Project) -> tuple[Project, bool]:
        await self._conn.execute(
            """
            INSERT OR IGNORE INTO projects (
                project_id, slug, name, description, status, is_default,
                default_agent_profile_id, metadata, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project.project_id,
                project.slug,
                project.name,
                project.description,
                project.status.value,
                1 if project.is_default else 0,
                project.default_agent_profile_id,
                json.dumps(project.metadata, ensure_ascii=False),
                project.created_at.isoformat(),
                project.updated_at.isoformat(),
            ),
        )
        created = await self._get_changes()
        if created:
            return project, True
        existing = await self.get_project(project.project_id)
        if existing is not None:
            return existing, False
        existing = await self.get_project_by_slug(project.slug)
        if existing is not None:
            return existing, False
        raise RuntimeError(f"project 创建失败且无法回读: {project.project_id}")

    async def get_project(self, project_id: str) -> Project | None:
        cursor = await self._conn.execute(
            "SELECT * FROM projects WHERE project_id = ?",
            (project_id,),
        )
        row = await cursor.fetchone()
        return self._row_to_project(row) if row is not None else None

    async def get_project_by_slug(self, slug: str) -> Project | None:
        cursor = await self._conn.execute(
            "SELECT * FROM projects WHERE slug = ?",
            (slug,),
        )
        row = await cursor.fetchone()
        return self._row_to_project(row) if row is not None else None

    async def get_default_project(self) -> Project | None:
        cursor = await self._conn.execute(
            "SELECT * FROM projects WHERE is_default = 1 ORDER BY created_at ASC LIMIT 1"
        )
        row = await cursor.fetchone()
        return self._row_to_project(row) if row is not None else None

    async def list_projects(self) -> list[Project]:
        cursor = await self._conn.execute(
            "SELECT * FROM projects ORDER BY created_at ASC"
        )
        rows = await cursor.fetchall()
        return [self._row_to_project(row) for row in rows]

    async def create_workspace(self, workspace: Workspace) -> tuple[Workspace, bool]:
        await self._conn.execute(
            """
            INSERT OR IGNORE INTO workspaces (
                workspace_id, project_id, slug, name, kind, root_path,
                metadata, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                workspace.workspace_id,
                workspace.project_id,
                workspace.slug,
                workspace.name,
                workspace.kind.value,
                workspace.root_path,
                json.dumps(workspace.metadata, ensure_ascii=False),
                workspace.created_at.isoformat(),
                workspace.updated_at.isoformat(),
            ),
        )
        created = await self._get_changes()
        if created:
            return workspace, True
        existing = await self.get_workspace(workspace.workspace_id)
        if existing is not None:
            return existing, False
        existing = await self.get_workspace_by_slug(workspace.project_id, workspace.slug)
        if existing is not None:
            return existing, False
        raise RuntimeError(f"workspace 创建失败且无法回读: {workspace.workspace_id}")

    async def get_workspace(self, workspace_id: str) -> Workspace | None:
        cursor = await self._conn.execute(
            "SELECT * FROM workspaces WHERE workspace_id = ?",
            (workspace_id,),
        )
        row = await cursor.fetchone()
        return self._row_to_workspace(row) if row is not None else None

    async def get_workspace_by_slug(
        self,
        project_id: str,
        slug: str,
    ) -> Workspace | None:
        cursor = await self._conn.execute(
            "SELECT * FROM workspaces WHERE project_id = ? AND slug = ?",
            (project_id, slug),
        )
        row = await cursor.fetchone()
        return self._row_to_workspace(row) if row is not None else None

    async def get_primary_workspace(self, project_id: str) -> Workspace | None:
        cursor = await self._conn.execute(
            """
            SELECT * FROM workspaces
            WHERE project_id = ? AND kind = ?
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (project_id, WorkspaceKind.PRIMARY.value),
        )
        row = await cursor.fetchone()
        return self._row_to_workspace(row) if row is not None else None

    async def list_workspaces(self, project_id: str) -> list[Workspace]:
        cursor = await self._conn.execute(
            "SELECT * FROM workspaces WHERE project_id = ? ORDER BY created_at ASC",
            (project_id,),
        )
        rows = await cursor.fetchall()
        return [self._row_to_workspace(row) for row in rows]

    async def resolve_project(self, ref: str) -> Project | None:
        project = await self.get_project(ref)
        if project is not None:
            return project
        return await self.get_project_by_slug(ref)

    async def get_binding(
        self,
        project_id: str,
        binding_type: ProjectBindingType,
        binding_key: str,
    ) -> ProjectBinding | None:
        cursor = await self._conn.execute(
            """
            SELECT * FROM project_bindings
            WHERE project_id = ? AND binding_type = ? AND binding_key = ?
            """,
            (project_id, binding_type.value, binding_key),
        )
        row = await cursor.fetchone()
        return self._row_to_binding(row) if row is not None else None

    async def create_binding(self, binding: ProjectBinding) -> tuple[ProjectBinding, bool]:
        await self._conn.execute(
            """
            INSERT OR IGNORE INTO project_bindings (
                binding_id, project_id, workspace_id, binding_type, binding_key,
                binding_value, source, metadata, migration_run_id, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                binding.binding_id,
                binding.project_id,
                binding.workspace_id,
                binding.binding_type.value,
                binding.binding_key,
                binding.binding_value,
                binding.source,
                json.dumps(binding.metadata, ensure_ascii=False),
                binding.migration_run_id,
                binding.created_at.isoformat(),
                binding.updated_at.isoformat(),
            ),
        )
        created = await self._get_changes()
        if created:
            return binding, True
        existing = await self.get_binding(
            binding.project_id,
            binding.binding_type,
            binding.binding_key,
        )
        if existing is not None:
            return existing, False
        raise RuntimeError(
            "binding 创建失败且无法回读: "
            f"{binding.binding_type}:{binding.binding_key}"
        )

    async def list_bindings(
        self,
        project_id: str,
        binding_type: ProjectBindingType | None = None,
    ) -> list[ProjectBinding]:
        if binding_type is None:
            cursor = await self._conn.execute(
                """
                SELECT * FROM project_bindings
                WHERE project_id = ?
                ORDER BY binding_type ASC, binding_key ASC
                """,
                (project_id,),
            )
        else:
            cursor = await self._conn.execute(
                """
                SELECT * FROM project_bindings
                WHERE project_id = ? AND binding_type = ?
                ORDER BY binding_key ASC
                """,
                (project_id, binding_type.value),
            )
        rows = await cursor.fetchall()
        return [self._row_to_binding(row) for row in rows]

    async def list_bindings_by_run(self, run_id: str) -> list[ProjectBinding]:
        cursor = await self._conn.execute(
            """
            SELECT * FROM project_bindings
            WHERE migration_run_id = ?
            ORDER BY created_at ASC
            """,
            (run_id,),
        )
        rows = await cursor.fetchall()
        return [self._row_to_binding(row) for row in rows]

    async def get_secret_binding(
        self,
        project_id: str,
        target_kind: SecretTargetKind,
        target_key: str,
    ) -> ProjectSecretBinding | None:
        cursor = await self._conn.execute(
            """
            SELECT * FROM project_secret_bindings
            WHERE project_id = ? AND target_kind = ? AND target_key = ?
            """,
            (project_id, target_kind.value, target_key),
        )
        row = await cursor.fetchone()
        return self._row_to_secret_binding(row) if row is not None else None

    async def save_secret_binding(
        self,
        binding: ProjectSecretBinding,
    ) -> ProjectSecretBinding:
        await self._conn.execute(
            """
            INSERT INTO project_secret_bindings (
                binding_id, project_id, workspace_id, target_kind, target_key,
                env_name, ref_source_type, ref_locator, display_name, redaction_label,
                status, last_audited_at, last_applied_at, last_reloaded_at,
                metadata, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, target_kind, target_key) DO UPDATE SET
                workspace_id = excluded.workspace_id,
                env_name = excluded.env_name,
                ref_source_type = excluded.ref_source_type,
                ref_locator = excluded.ref_locator,
                display_name = excluded.display_name,
                redaction_label = excluded.redaction_label,
                status = excluded.status,
                last_audited_at = excluded.last_audited_at,
                last_applied_at = excluded.last_applied_at,
                last_reloaded_at = excluded.last_reloaded_at,
                metadata = excluded.metadata,
                updated_at = excluded.updated_at
            """,
            (
                binding.binding_id,
                binding.project_id,
                binding.workspace_id,
                binding.target_kind.value,
                binding.target_key,
                binding.env_name,
                binding.ref_source_type.value,
                json.dumps(binding.ref_locator, ensure_ascii=False),
                binding.display_name,
                binding.redaction_label,
                binding.status.value,
                binding.last_audited_at.isoformat() if binding.last_audited_at else None,
                binding.last_applied_at.isoformat() if binding.last_applied_at else None,
                binding.last_reloaded_at.isoformat() if binding.last_reloaded_at else None,
                json.dumps(binding.metadata, ensure_ascii=False),
                binding.created_at.isoformat(),
                binding.updated_at.isoformat(),
            ),
        )
        stored = await self.get_secret_binding(
            binding.project_id,
            binding.target_kind,
            binding.target_key,
        )
        if stored is None:
            raise RuntimeError(
                "secret binding 保存失败且无法回读: "
                f"{binding.target_kind}:{binding.target_key}"
            )
        return stored

    async def list_secret_bindings(
        self,
        project_id: str,
        target_kind: SecretTargetKind | None = None,
    ) -> list[ProjectSecretBinding]:
        if target_kind is None:
            cursor = await self._conn.execute(
                """
                SELECT * FROM project_secret_bindings
                WHERE project_id = ?
                ORDER BY target_kind ASC, target_key ASC
                """,
                (project_id,),
            )
        else:
            cursor = await self._conn.execute(
                """
                SELECT * FROM project_secret_bindings
                WHERE project_id = ? AND target_kind = ?
                ORDER BY target_key ASC
                """,
                (project_id, target_kind.value),
            )
        rows = await cursor.fetchall()
        return [self._row_to_secret_binding(row) for row in rows]

    async def save_selector_state(
        self,
        state: ProjectSelectorState,
    ) -> ProjectSelectorState:
        await self._conn.execute(
            """
            INSERT INTO project_selector_state (
                selector_id, surface, active_project_id, active_workspace_id,
                source, warnings, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(surface) DO UPDATE SET
                active_project_id = excluded.active_project_id,
                active_workspace_id = excluded.active_workspace_id,
                source = excluded.source,
                warnings = excluded.warnings,
                updated_at = excluded.updated_at
            """,
            (
                state.selector_id,
                state.surface,
                state.active_project_id,
                state.active_workspace_id,
                state.source,
                json.dumps(state.warnings, ensure_ascii=False),
                state.updated_at.isoformat(),
            ),
        )
        stored = await self.get_selector_state(state.surface)
        if stored is None:
            raise RuntimeError(f"selector state 保存失败且无法回读: {state.surface}")
        return stored

    async def get_selector_state(self, surface: str) -> ProjectSelectorState | None:
        cursor = await self._conn.execute(
            """
            SELECT * FROM project_selector_state
            WHERE surface = ?
            LIMIT 1
            """,
            (surface,),
        )
        row = await cursor.fetchone()
        return self._row_to_selector_state(row) if row is not None else None

    async def save_migration_run(self, run: ProjectMigrationRun) -> None:
        await self._conn.execute(
            """
            INSERT INTO project_migration_runs (
                run_id, project_root, status, started_at, completed_at,
                summary, validation, rollback_plan, error_message
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                status = excluded.status,
                completed_at = excluded.completed_at,
                summary = excluded.summary,
                validation = excluded.validation,
                rollback_plan = excluded.rollback_plan,
                error_message = excluded.error_message
            """,
            (
                run.run_id,
                run.project_root,
                run.status.value,
                run.started_at.isoformat(),
                run.completed_at.isoformat() if run.completed_at else None,
                run.summary.model_dump_json(),
                run.validation.model_dump_json(),
                run.rollback_plan.model_dump_json(),
                run.error_message,
            ),
        )

    async def get_migration_run(self, run_id: str) -> ProjectMigrationRun | None:
        cursor = await self._conn.execute(
            "SELECT * FROM project_migration_runs WHERE run_id = ?",
            (run_id,),
        )
        row = await cursor.fetchone()
        return self._row_to_migration_run(row) if row is not None else None

    async def get_latest_migration_run(
        self,
        project_root: str,
        *,
        statuses: tuple[ProjectMigrationStatus, ...] | None = None,
    ) -> ProjectMigrationRun | None:
        params: list[str] = [project_root]
        sql = """
            SELECT * FROM project_migration_runs
            WHERE project_root = ?
        """
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            sql += f" AND status IN ({placeholders})"
            params.extend(status.value for status in statuses)
        sql += " ORDER BY started_at DESC LIMIT 1"
        cursor = await self._conn.execute(sql, tuple(params))
        row = await cursor.fetchone()
        return self._row_to_migration_run(row) if row is not None else None

    async def delete_bindings(self, binding_ids: list[str]) -> None:
        if not binding_ids:
            return
        placeholders = ",".join("?" for _ in binding_ids)
        await self._conn.execute(
            f"DELETE FROM project_bindings WHERE binding_id IN ({placeholders})",
            tuple(binding_ids),
        )

    async def delete_bindings_for_run(
        self,
        run_id: str,
        binding_ids: list[str],
    ) -> None:
        if not binding_ids:
            return
        placeholders = ",".join("?" for _ in binding_ids)
        await self._conn.execute(
            (
                "DELETE FROM project_bindings "
                f"WHERE migration_run_id = ? AND binding_id IN ({placeholders})"
            ),
            (run_id, *binding_ids),
        )

    async def delete_workspaces(self, workspace_ids: list[str]) -> None:
        if not workspace_ids:
            return
        placeholders = ",".join("?" for _ in workspace_ids)
        await self._conn.execute(
            f"DELETE FROM workspaces WHERE workspace_id IN ({placeholders})",
            tuple(workspace_ids),
        )

    async def delete_projects(self, project_ids: list[str]) -> None:
        if not project_ids:
            return
        placeholders = ",".join("?" for _ in project_ids)
        await self._conn.execute(
            f"DELETE FROM projects WHERE project_id IN ({placeholders})",
            tuple(project_ids),
        )

    async def delete_run_artifacts(self, run_id: str) -> None:
        run = await self.get_migration_run(run_id)
        if run is None:
            return
        await self.delete_bindings_for_run(run.run_id, run.rollback_plan.delete_binding_ids)
        await self.delete_workspaces(run.rollback_plan.delete_workspace_ids)
        await self.delete_projects(run.rollback_plan.delete_project_ids)

    async def resolve_workspace_for_scope(
        self,
        scope_id: str,
        *,
        binding_types: tuple[ProjectBindingType, ...] = (
            ProjectBindingType.SCOPE,
            ProjectBindingType.MEMORY_SCOPE,
            ProjectBindingType.IMPORT_SCOPE,
        ),
    ) -> Workspace | None:
        default_project = await self.get_default_project()
        if scope_id:
            placeholders = ",".join("?" for _ in binding_types)
            values: list[str] = [binding_type.value for binding_type in binding_types]
            values.append(scope_id)
            default_project_id = default_project.project_id if default_project is not None else ""
            values.append(default_project_id)
            cursor = await self._conn.execute(
                f"""
                SELECT w.*
                FROM project_bindings pb
                JOIN workspaces w ON w.workspace_id = pb.workspace_id
                WHERE pb.binding_type IN ({placeholders})
                  AND pb.binding_key = ?
                ORDER BY
                  CASE
                    WHEN pb.project_id = ? THEN 0
                    ELSE 1
                  END,
                  pb.created_at ASC
                LIMIT 1
                """,
                tuple(values),
            )
            row = await cursor.fetchone()
            if row is not None:
                return self._row_to_workspace(row)

        if default_project is None:
            return None
        return await self.get_primary_workspace(default_project.project_id)

    async def _get_changes(self) -> int:
        cursor = await self._conn.execute("SELECT changes()")
        row = await cursor.fetchone()
        return int(row[0]) if row is not None else 0

    @staticmethod
    def _row_to_project(row: aiosqlite.Row) -> Project:
        return Project(
            project_id=row[0],
            slug=row[1],
            name=row[2],
            description=row[3],
            status=row[4],
            is_default=bool(row[5]),
            default_agent_profile_id=row[6],
            metadata=json.loads(row[7]),
            created_at=datetime.fromisoformat(row[8]),
            updated_at=datetime.fromisoformat(row[9]),
        )

    @staticmethod
    def _row_to_workspace(row: aiosqlite.Row) -> Workspace:
        return Workspace(
            workspace_id=row[0],
            project_id=row[1],
            slug=row[2],
            name=row[3],
            kind=row[4],
            root_path=row[5],
            metadata=json.loads(row[6]),
            created_at=datetime.fromisoformat(row[7]),
            updated_at=datetime.fromisoformat(row[8]),
        )

    @staticmethod
    def _row_to_binding(row: aiosqlite.Row) -> ProjectBinding:
        return ProjectBinding(
            binding_id=row[0],
            project_id=row[1],
            workspace_id=row[2],
            binding_type=row[3],
            binding_key=row[4],
            binding_value=row[5],
            source=row[6],
            metadata=json.loads(row[7]),
            migration_run_id=row[8],
            created_at=datetime.fromisoformat(row[9]),
            updated_at=datetime.fromisoformat(row[10]),
        )

    @staticmethod
    def _row_to_migration_run(row: aiosqlite.Row) -> ProjectMigrationRun:
        return ProjectMigrationRun(
            run_id=row[0],
            project_root=row[1],
            status=row[2],
            started_at=datetime.fromisoformat(row[3]),
            completed_at=datetime.fromisoformat(row[4]) if row[4] else None,
            summary=json.loads(row[5]),
            validation=json.loads(row[6]),
            rollback_plan=json.loads(row[7]),
            error_message=row[8],
        )

    @staticmethod
    def _row_to_secret_binding(row: aiosqlite.Row) -> ProjectSecretBinding:
        return ProjectSecretBinding(
            binding_id=row[0],
            project_id=row[1],
            workspace_id=row[2],
            target_kind=row[3],
            target_key=row[4],
            env_name=row[5],
            ref_source_type=row[6],
            ref_locator=json.loads(row[7]),
            display_name=row[8],
            redaction_label=row[9],
            status=row[10],
            last_audited_at=datetime.fromisoformat(row[11]) if row[11] else None,
            last_applied_at=datetime.fromisoformat(row[12]) if row[12] else None,
            last_reloaded_at=datetime.fromisoformat(row[13]) if row[13] else None,
            metadata=json.loads(row[14]),
            created_at=datetime.fromisoformat(row[15]),
            updated_at=datetime.fromisoformat(row[16]),
        )

    @staticmethod
    def _row_to_selector_state(row: aiosqlite.Row) -> ProjectSelectorState:
        return ProjectSelectorState(
            selector_id=row[0],
            surface=row[1],
            active_project_id=row[2],
            active_workspace_id=row[3],
            source=row[4],
            warnings=json.loads(row[5]),
            updated_at=datetime.fromisoformat(row[6]),
        )
