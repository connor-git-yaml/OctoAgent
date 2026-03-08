"""raw task routes 的 project/workspace 视图隔离。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from octoagent.core.models import Task
from octoagent.core.store import StoreGroup
from octoagent.provider.dx.control_plane_state import ControlPlaneStateStore


class TaskScopeGuardError(RuntimeError):
    """当前选中 project/workspace 不允许访问该 task。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class TaskScopeGuard:
    """复用 control-plane selection，对 raw task routes 做视图隔离。"""

    def __init__(self, project_root: Path, store_group: StoreGroup) -> None:
        self._project_root = project_root
        self._stores = store_group
        self._state_store = ControlPlaneStateStore(project_root)

    async def filter_visible_tasks(self, tasks: list[Task]) -> list[Task]:
        selected_project, selected_workspace = await self._resolve_selection()
        if selected_project is None:
            return tasks

        default_project = await self._stores.project_store.get_default_project()
        default_workspace = (
            await self._stores.project_store.get_primary_workspace(default_project.project_id)
            if default_project is not None
            else None
        )
        scope_cache: dict[str, tuple[str | None, str | None]] = {}
        visible: list[Task] = []
        for task in tasks:
            task_project_id, task_workspace_id = await self._resolve_task_scope(
                task,
                default_project=default_project,
                default_workspace=default_workspace,
                cache=scope_cache,
            )
            if self._matches_selected_scope(
                item_project_id=task_project_id,
                item_workspace_id=task_workspace_id,
                selected_project=selected_project,
                selected_workspace=selected_workspace,
            ):
                visible.append(task)
        return visible

    async def ensure_task_visible(self, task: Task) -> None:
        selected_project, selected_workspace = await self._resolve_selection()
        if selected_project is None:
            return

        default_project = await self._stores.project_store.get_default_project()
        default_workspace = (
            await self._stores.project_store.get_primary_workspace(default_project.project_id)
            if default_project is not None
            else None
        )
        task_project_id, task_workspace_id = await self._resolve_task_scope(
            task,
            default_project=default_project,
            default_workspace=default_workspace,
            cache={},
        )
        if not self._matches_selected_scope(
            item_project_id=task_project_id,
            item_workspace_id=task_workspace_id,
            selected_project=selected_project,
            selected_workspace=selected_workspace,
        ):
            raise TaskScopeGuardError(
                "TASK_SCOPE_NOT_ALLOWED",
                "当前 task 不属于当前选中的 project/workspace。",
            )

    async def _resolve_selection(self) -> tuple[Any | None, Any | None]:
        state = self._state_store.load()
        selector = await self._stores.project_store.get_selector_state("web")

        project = (
            await self._stores.project_store.get_project(state.selected_project_id)
            if state.selected_project_id
            else None
        )
        if project is None and selector is not None:
            project = await self._stores.project_store.get_project(selector.active_project_id)
        if project is None:
            project = await self._stores.project_store.get_default_project()

        workspace = (
            await self._stores.project_store.get_workspace(state.selected_workspace_id)
            if state.selected_workspace_id
            else None
        )
        if workspace is None and selector is not None and selector.active_workspace_id:
            candidate = await self._stores.project_store.get_workspace(
                selector.active_workspace_id
            )
            if candidate is not None and (
                project is None or candidate.project_id == project.project_id
            ):
                workspace = candidate
        if project is not None and (
            workspace is None or workspace.project_id != project.project_id
        ):
            workspace = await self._stores.project_store.get_primary_workspace(project.project_id)
        return project, workspace

    async def _resolve_task_scope(
        self,
        task: Task,
        *,
        default_project,
        default_workspace,
        cache: dict[str, tuple[str | None, str | None]],
    ) -> tuple[str | None, str | None]:
        scope_id = task.scope_id or ""
        if scope_id in cache:
            return cache[scope_id]

        workspace = await self._stores.project_store.resolve_workspace_for_scope(scope_id)
        if workspace is not None:
            resolved = (workspace.project_id, workspace.workspace_id)
        else:
            resolved = (
                default_project.project_id if default_project is not None else None,
                default_workspace.workspace_id if default_workspace is not None else None,
            )
        cache[scope_id] = resolved
        return resolved

    @staticmethod
    def _matches_selected_scope(
        *,
        item_project_id: str | None,
        item_workspace_id: str | None,
        selected_project: Any | None,
        selected_workspace: Any | None,
    ) -> bool:
        if selected_project is None:
            return True
        if item_project_id and item_project_id != selected_project.project_id:
            return False
        return not (
            selected_workspace is not None
            and item_workspace_id
            and item_workspace_id != selected_workspace.workspace_id
        )
