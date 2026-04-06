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
    ProjectSecretBinding,
    ProjectSelectorState,
    SecretRefSourceType,
    SecretTargetKind,
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
            default_agent_profile_id="agent-profile-default",
        )
        binding = ProjectBinding(
            binding_id="binding-scope-1",
            project_id=project.project_id,
            binding_type=ProjectBindingType.SCOPE,
            binding_key="ops/default",
            binding_value="ops/default",
            source="tasks",
            metadata={"task_ids": ["task-1"]},
            migration_run_id="run-1",
        )

        _, created_project = await store.create_project(project)
        _, created_binding = await store.create_binding(binding)
        await core_db.commit()

        default_project = await store.get_default_project()
        bindings = await store.list_bindings(project.project_id, ProjectBindingType.SCOPE)

        assert default_project is not None
        assert created_project is True
        assert created_binding is True
        assert default_project.project_id == project.project_id
        assert default_project.default_agent_profile_id == "agent-profile-default"
        assert len(bindings) == 1
        assert bindings[0].binding_key == "ops/default"

    async def test_resolve_project_for_scope_supports_non_default_project(self, core_db):
        store = SqliteProjectStore(core_db)
        default_project = Project(
            project_id="project-default",
            slug="default",
            name="Default Project",
            is_default=True,
        )
        beta_project = Project(
            project_id="project-beta",
            slug="beta",
            name="Beta Project",
            is_default=False,
        )
        beta_binding = ProjectBinding(
            binding_id="binding-scope-beta",
            project_id=beta_project.project_id,
            binding_type=ProjectBindingType.SCOPE,
            binding_key="chat:web:thread-beta",
            binding_value="chat:web:thread-beta",
            source="tests",
            migration_run_id="run-beta",
        )

        await store.create_project(default_project)
        await store.create_project(beta_project)
        await store.create_binding(beta_binding)
        await core_db.commit()

        resolved = await store.resolve_project_for_scope("project:project-beta:chat:web:thread-beta")

        assert resolved is not None
        assert resolved.project_id == beta_project.project_id

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

    async def test_save_secret_binding_and_selector_state(self, core_db):
        store = SqliteProjectStore(core_db)
        project = Project(
            project_id="project-default",
            slug="default",
            name="Default Project",
            is_default=True,
        )
        await store.create_project(project)
        binding = ProjectSecretBinding(
            binding_id="secret-binding-1",
            project_id=project.project_id,
            target_kind=SecretTargetKind.RUNTIME,
            target_key="runtime.master_key_env",
            env_name="LITELLM_MASTER_KEY",
            ref_source_type=SecretRefSourceType.ENV,
            ref_locator={"env_name": "LITELLM_MASTER_KEY"},
            display_name="LiteLLM Master Key",
            redaction_label="LITELLM_MASTER_KEY=***",
        )
        selector = ProjectSelectorState(
            selector_id="selector-cli",
            surface="cli",
            active_project_id=project.project_id,
            source="test",
        )

        stored_binding = await store.save_secret_binding(binding)
        stored_selector = await store.save_selector_state(selector)
        await core_db.commit()

        bindings = await store.list_secret_bindings(project.project_id)
        resolved_selector = await store.get_selector_state("cli")

        assert stored_binding.target_key == "runtime.master_key_env"
        assert stored_selector.active_project_id == project.project_id
        assert len(bindings) == 1
        assert bindings[0].redaction_label == "LITELLM_MASTER_KEY=***"
        assert resolved_selector is not None
        assert resolved_selector.active_project_id == project.project_id

    async def test_get_binding_tolerates_project_bindings_schema_with_workspace_id(self, core_db):
        store = SqliteProjectStore(core_db)
        project = Project(
            project_id="project-default",
            slug="default",
            name="Default Project",
            is_default=True,
        )
        await store.create_project(project)
        await core_db.execute("DROP TABLE project_bindings")
        await core_db.execute(
            """
            CREATE TABLE project_bindings (
                binding_id        TEXT PRIMARY KEY,
                project_id        TEXT NOT NULL,
                workspace_id      TEXT,
                binding_type      TEXT NOT NULL,
                binding_key       TEXT NOT NULL,
                binding_value     TEXT NOT NULL DEFAULT '',
                source            TEXT NOT NULL DEFAULT '',
                metadata          TEXT NOT NULL DEFAULT '{}',
                migration_run_id  TEXT NOT NULL,
                created_at        TEXT NOT NULL,
                updated_at        TEXT NOT NULL
            )
            """
        )
        await core_db.execute(
            """
            INSERT INTO project_bindings (
                binding_id, project_id, workspace_id, binding_type, binding_key,
                binding_value, source, metadata, migration_run_id, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            """,
            (
                "binding-scope-legacy",
                project.project_id,
                "workspace-default",
                ProjectBindingType.SCOPE.value,
                "ops/default",
                "ops/default",
                "",
                '{"task_ids":["task-1"]}',
                "run-legacy",
            ),
        )
        await core_db.commit()

        binding = await store.get_binding(
            project.project_id,
            ProjectBindingType.SCOPE,
            "ops/default",
        )

        assert binding is not None
        assert binding.binding_id == "binding-scope-legacy"
        assert binding.source == ""
        assert binding.metadata == {"task_ids": ["task-1"]}
