"""Feature 025: active project selector 与 inspect 摘要。"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from octoagent.core.behavior_workspace import materialize_project_behavior_files
from octoagent.core.models import Project, ProjectSecretBinding, ProjectSelectorState, Workspace
from octoagent.core.store import StoreGroup, create_store_group
from pydantic import BaseModel, Field
from ulid import ULID

from .backup_service import resolve_artifacts_dir, resolve_db_path, resolve_project_root
from .control_plane_models import ProjectCandidate, ProjectSelectorDocument
from .project_migration import ProjectWorkspaceMigrationService
from .secret_status_store import SecretStatusStore

_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


class ProjectSelectorError(RuntimeError):
    """project selector / CLI 的结构化错误。"""

    def __init__(self, message: str, *, exit_code: int = 2) -> None:
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code


class ProjectInspectSummary(BaseModel):
    """`octo project inspect` 使用的 redacted 摘要。"""

    project_id: str
    slug: str
    name: str
    description: str = ""
    primary_workspace_id: str | None = None
    primary_workspace_slug: str | None = None
    readiness: str = "ready"
    warnings: list[str] = Field(default_factory=list)
    binding_summary: dict[str, int] = Field(default_factory=dict)
    secret_runtime_summary: dict[str, object] = Field(default_factory=dict)
    selector: ProjectSelectorDocument


class ProjectSelectorService:
    """管理 active project 选择态与 inspect summary。"""

    def __init__(
        self,
        project_root: Path,
        *,
        surface: str = "cli",
        store_group: StoreGroup | None = None,
    ) -> None:
        self._root = resolve_project_root(project_root).resolve()
        self._db_path = resolve_db_path(self._root)
        self._artifacts_dir = resolve_artifacts_dir(self._root)
        self._surface = surface
        self._store_group = store_group
        self._secret_status_store = SecretStatusStore(self._root)

    async def create_project(
        self,
        *,
        name: str,
        slug: str | None = None,
        description: str = "",
        set_active: bool = True,
    ) -> tuple[Project, ProjectSelectorDocument, bool]:
        await self._ensure_migration_ready()
        normalized_slug = self._slugify(slug or name)
        if not normalized_slug:
            raise ProjectSelectorError("project slug 不能为空。")

        async with self._store_group_scope() as store_group:
            existing = await store_group.project_store.get_project_by_slug(normalized_slug)
            if existing is not None:
                raise ProjectSelectorError(f"project slug 已存在: {normalized_slug}")

            now = _utc_now()
            suffix = normalized_slug
            project = Project(
                project_id=f"project-{suffix}-{str(ULID())[-6:].lower()}",
                slug=normalized_slug,
                name=name.strip(),
                description=description.strip(),
                created_at=now,
                updated_at=now,
            )
            workspace = Workspace(
                workspace_id=f"workspace-{suffix}-primary-{str(ULID())[-6:].lower()}",
                project_id=project.project_id,
                slug="primary",
                name=f"{project.name} Primary",
                root_path=str(self._root),
                created_at=now,
                updated_at=now,
            )
            await store_group.project_store.create_project(project)
            await store_group.project_store.create_workspace(workspace)

            # 为新项目创建 project-shared 行为文件和基础设施
            materialize_project_behavior_files(
                self._root,
                project_slug=normalized_slug,
                project_name=name.strip(),
            )

            active_changed = False
            if set_active:
                await self._save_selector_state(
                    store_group=store_group,
                    project=project,
                    workspace=workspace,
                    source="project_create",
                    warnings=[],
                )
                active_changed = True
            await store_group.conn.commit()
            selector = await self._build_selector_document(store_group=store_group)
            return project, selector, active_changed

    async def select_project(self, ref: str) -> ProjectSelectorDocument:
        await self._ensure_migration_ready()
        async with self._store_group_scope() as store_group:
            project = await store_group.project_store.resolve_project(ref)
            if project is None:
                raise ProjectSelectorError(f"未找到 project: {ref}")
            workspace = await store_group.project_store.get_primary_workspace(project.project_id)
            warnings = self._build_project_warnings(project, workspace)
            await self._save_selector_state(
                store_group=store_group,
                project=project,
                workspace=workspace,
                source="project_select",
                warnings=warnings,
            )
            await store_group.conn.commit()
            return await self._build_selector_document(store_group=store_group)

    async def inspect_project(self, ref: str | None = None) -> ProjectInspectSummary:
        await self._ensure_migration_ready()
        async with self._store_group_scope() as store_group:
            project, workspace = await self._resolve_current_project(store_group, ref)
            selector = await self._build_selector_document(store_group=store_group)
            bindings = await store_group.project_store.list_secret_bindings(project.project_id)
            binding_summary = self._summarize_binding_status(bindings)
            secret_runtime_summary = self._build_secret_runtime_summary(
                project.project_id,
                bindings,
            )
            warnings = self._build_project_warnings(project, workspace)
            warnings.extend(secret_runtime_summary.get("warnings", []))
            readiness = "ready" if not warnings else "action_required"
        return ProjectInspectSummary(
            project_id=project.project_id,
            slug=project.slug,
                name=project.name,
                description=project.description,
                primary_workspace_id=workspace.workspace_id if workspace else None,
                primary_workspace_slug=workspace.slug if workspace else None,
                readiness=readiness,
                warnings=warnings,
                binding_summary=binding_summary,
                secret_runtime_summary=secret_runtime_summary,
                selector=selector,
            )

    async def edit_project(
        self,
        *,
        ref: str | None = None,
        name: str | None = None,
        description: str | None = None,
    ) -> Project:
        await self._ensure_migration_ready()
        async with self._store_group_scope() as store_group:
            project, _ = await self._resolve_current_project(store_group, ref)
            updated = project.model_copy(
                update={
                    "name": name.strip() if name is not None else project.name,
                    "description": description.strip()
                    if description is not None
                    else project.description,
                    "updated_at": _utc_now(),
                }
            )
            await store_group.project_store.save_project(updated)
            await store_group.conn.commit()
            return updated

    async def get_active_project(self) -> tuple[Project, Workspace | None]:
        await self._ensure_migration_ready()
        async with self._store_group_scope() as store_group:
            return await self._resolve_current_project(store_group, None)

    async def resolve_project(self, ref: str | None) -> tuple[Project, Workspace | None]:
        await self._ensure_migration_ready()
        async with self._store_group_scope() as store_group:
            return await self._resolve_current_project(store_group, ref)

    async def load_selector_document(self) -> ProjectSelectorDocument:
        await self._ensure_migration_ready()
        async with self._store_group_scope() as store_group:
            return await self._build_selector_document(store_group=store_group)

    async def _ensure_migration_ready(self) -> None:
        migration = ProjectWorkspaceMigrationService(self._root)
        await migration.ensure_default_project()

    async def _resolve_current_project(
        self,
        store_group: StoreGroup,
        ref: str | None,
    ) -> tuple[Project, Workspace | None]:
        if ref:
            project = await store_group.project_store.resolve_project(ref)
            if project is None:
                raise ProjectSelectorError(f"未找到 project: {ref}")
            workspace = await store_group.project_store.get_primary_workspace(project.project_id)
            return project, workspace

        selector = await store_group.project_store.get_selector_state(self._surface)
        project: Project | None = None
        workspace: Workspace | None = None
        if selector is not None:
            project = await store_group.project_store.get_project(selector.active_project_id)
            if selector.active_workspace_id:
                workspace = await store_group.project_store.get_workspace(
                    selector.active_workspace_id
                )
        if project is None:
            project = await store_group.project_store.get_default_project()
            if project is None:
                raise ProjectSelectorError("当前没有可用 project，请先运行 octo project create。")
            workspace = await store_group.project_store.get_primary_workspace(project.project_id)
            await self._save_selector_state(
                store_group=store_group,
                project=project,
                workspace=workspace,
                source="default_fallback",
                warnings=self._build_project_warnings(project, workspace),
            )
            await store_group.conn.commit()
        elif workspace is None:
            workspace = await store_group.project_store.get_primary_workspace(project.project_id)
        return project, workspace

    async def _build_selector_document(
        self,
        *,
        store_group: StoreGroup,
    ) -> ProjectSelectorDocument:
        projects = await store_group.project_store.list_projects()
        selector = await store_group.project_store.get_selector_state(self._surface)
        current_project: Project | None = None
        current_workspace: Workspace | None = None
        if selector is not None:
            current_project = await store_group.project_store.get_project(
                selector.active_project_id
            )
            if selector.active_workspace_id:
                current_workspace = await store_group.project_store.get_workspace(
                    selector.active_workspace_id
                )
        if current_project is None and projects:
            current_project = next((item for item in projects if item.is_default), projects[0])
            current_workspace = await store_group.project_store.get_primary_workspace(
                current_project.project_id
            )

        candidates: list[ProjectCandidate] = []
        warnings: list[str] = []
        for project in projects:
            workspace = await store_group.project_store.get_primary_workspace(project.project_id)
            project_warnings = self._build_project_warnings(project, workspace)
            readiness = "ready" if not project_warnings else "warning"
            candidates.append(
                ProjectCandidate(
                    project_id=project.project_id,
                    slug=project.slug,
                    name=project.name,
                    is_default=project.is_default,
                    workspace_id=workspace.workspace_id if workspace else None,
                    readiness=readiness,
                    warnings=project_warnings,
                )
            )
            if current_project and project.project_id == current_project.project_id:
                warnings.extend(project_warnings)

        readiness = "ready" if not warnings else "warning"
        return ProjectSelectorDocument(
            current_project=(
                ProjectCandidate(
                    project_id=current_project.project_id,
                    slug=current_project.slug,
                    name=current_project.name,
                    is_default=current_project.is_default,
                    workspace_id=current_workspace.workspace_id if current_workspace else None,
                    readiness=readiness,
                    warnings=warnings,
                )
                if current_project is not None
                else None
            ),
            candidate_projects=candidates,
            readiness=readiness,
            warnings=warnings,
        )

    async def _save_selector_state(
        self,
        *,
        store_group: StoreGroup,
        project: Project,
        workspace: Workspace | None,
        source: str,
        warnings: list[str],
    ) -> None:
        state = ProjectSelectorState(
            selector_id=f"selector-{self._surface}",
            surface=self._surface,
            active_project_id=project.project_id,
            active_workspace_id=workspace.workspace_id if workspace else None,
            source=source,
            warnings=warnings,
            updated_at=_utc_now(),
        )
        await store_group.project_store.save_selector_state(state)

    def _summarize_binding_status(
        self,
        bindings: list[ProjectSecretBinding],
    ) -> dict[str, int]:
        summary: dict[str, int] = {}
        for binding in bindings:
            key = binding.status.value
            summary[key] = summary.get(key, 0) + 1
        return summary

    def _build_secret_runtime_summary(
        self,
        project_id: str,
        bindings: list[ProjectSecretBinding],
    ) -> dict[str, object]:
        status_store = self._secret_status_store.for_project(project_id)
        apply_run = status_store.load_apply()
        materialization = status_store.load_materialization()
        warnings: list[str] = []
        latest_applied = max(
            (
                binding.last_applied_at or binding.last_audited_at or binding.updated_at
                for binding in bindings
            ),
            default=None,
        )

        if not bindings:
            status = "not_configured"
        elif any(
            binding.status.value in {"draft", "invalid", "needs_reload", "rotation_pending"}
            for binding in bindings
        ):
            status = "action_required"
            warnings.append("存在未应用或待 reload 的 secret bindings。")
        elif materialization is None:
            status = "action_required"
            warnings.append("secret bindings 已存在，但尚未执行 secrets reload。")
        elif latest_applied is not None and materialization.generated_at < latest_applied:
            status = "action_required"
            warnings.append("最近一次 secret apply 晚于 runtime materialization，需要重新 reload。")
        else:
            status = "ready"

        return {
            "status": status,
            "latest_apply_status": apply_run.status if apply_run is not None else "",
            "delivery_mode": materialization.delivery_mode if materialization else "",
            "resolved_env_count": len(materialization.resolved_env_names)
            if materialization is not None
            else 0,
            "warnings": warnings,
        }

    @staticmethod
    def _build_project_warnings(
        project: Project,
        workspace: Workspace | None,
    ) -> list[str]:
        warnings: list[str] = []
        if not workspace:
            warnings.append("当前 project 缺少 primary workspace。")
        if project.status != "active":
            warnings.append(f"当前 project 状态不是 active: {project.status.value}")
        return warnings

    @staticmethod
    def _slugify(value: str) -> str:
        slug = _SLUG_PATTERN.sub("-", value.strip().lower()).strip("-")
        return slug

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
