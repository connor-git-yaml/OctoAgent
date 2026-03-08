"""Feature 025: ProjectStore 测试。"""

from octoagent.core.models import (
    Project,
    ProjectBinding,
    ProjectBindingType,
    ProjectMigrationRollbackPlan,
    ProjectMigrationRun,
    ProjectMigrationStatus,
    ProjectMigrationSummary,
    ProjectMigrationValidation,
    Workspace,
)
from octoagent.core.store.project_store import SqliteProjectStore


class TestProjectStore:
    async def test_create_and_resolve_default_project(self, core_db):
        store = SqliteProjectStore(core_db)
        project = Project(
            project_id="project-default",
            slug="default",
            name="Default Project",
            is_default=True,
        )
        workspace = Workspace(
            workspace_id="workspace-default-primary",
            project_id=project.project_id,
            slug="primary",
            name="Primary Workspace",
            root_path="/tmp/octo",
        )
        binding = ProjectBinding(
            binding_id="binding-scope-1",
            project_id=project.project_id,
            workspace_id=workspace.workspace_id,
            binding_type=ProjectBindingType.SCOPE,
            binding_key="ops/default",
            binding_value="ops/default",
            source="tasks",
            metadata={"task_ids": ["task-1"]},
            migration_run_id="run-1",
        )

        _, created_project = await store.create_project(project)
        _, created_workspace = await store.create_workspace(workspace)
        _, created_binding = await store.create_binding(binding)
        await core_db.commit()

        default_project = await store.get_default_project()
        resolved_workspace = await store.resolve_workspace_for_scope("ops/default")
        bindings = await store.list_bindings(project.project_id, ProjectBindingType.SCOPE)

        assert default_project is not None
        assert created_project is True
        assert created_workspace is True
        assert created_binding is True
        assert default_project.project_id == project.project_id
        assert resolved_workspace is not None
        assert resolved_workspace.workspace_id == workspace.workspace_id
        assert len(bindings) == 1
        assert bindings[0].binding_key == "ops/default"

    async def test_save_and_read_migration_run(self, core_db):
        store = SqliteProjectStore(core_db)
        run = ProjectMigrationRun(
            run_id="run-1",
            project_root="/tmp/octo",
            status=ProjectMigrationStatus.SUCCEEDED,
            summary=ProjectMigrationSummary(binding_counts={"scope": 1}),
            validation=ProjectMigrationValidation(ok=True),
            rollback_plan=ProjectMigrationRollbackPlan(
                run_id="run-1",
                delete_binding_ids=["binding-1"],
            ),
        )

        await store.save_migration_run(run)
        await core_db.commit()

        latest = await store.get_latest_migration_run("/tmp/octo")
        assert latest is not None
        assert latest.run_id == "run-1"
        assert latest.rollback_plan.delete_binding_ids == ["binding-1"]
