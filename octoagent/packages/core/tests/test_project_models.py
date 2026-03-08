"""Feature 025: Project / Workspace models 测试。"""

from datetime import UTC, datetime

import pytest
from octoagent.core.models import (
    Project,
    ProjectBinding,
    ProjectBindingType,
    ProjectMigrationValidation,
    ProjectSecretBinding,
    ProjectSelectorState,
    SecretRefSourceType,
    SecretTargetKind,
    Workspace,
)


def test_project_model_roundtrip() -> None:
    now = datetime.now(tz=UTC)
    project = Project(
        project_id="project-default",
        slug="default",
        name="Default Project",
        is_default=True,
        created_at=now,
        updated_at=now,
        metadata={"project_root": "/tmp/octo"},
    )
    restored = Project.model_validate(project.model_dump(mode="python"))
    assert restored == project


def test_workspace_binding_requires_workspace_id_for_scope_types() -> None:
    with pytest.raises(ValueError, match="workspace_id"):
        ProjectBinding(
            binding_id="binding-1",
            project_id="project-default",
            workspace_id=None,
            binding_type=ProjectBindingType.SCOPE,
            binding_key="ops/default",
            binding_value="ops/default",
            source="tasks",
            migration_run_id="run-1",
        )


def test_project_migration_validation_derives_ok_from_missing_items() -> None:
    validation = ProjectMigrationValidation(
        missing_binding_keys=["scope:ops/default"],
    )
    assert validation.ok is False


def test_workspace_model_defaults() -> None:
    workspace = Workspace(
        workspace_id="workspace-default-primary",
        project_id="project-default",
        slug="primary",
        name="Primary Workspace",
        root_path="/tmp/octo",
    )
    assert workspace.kind == "primary"


def test_project_secret_binding_roundtrip() -> None:
    binding = ProjectSecretBinding(
        binding_id="secret-binding-1",
        project_id="project-default",
        target_kind=SecretTargetKind.PROVIDER,
        target_key="provider:openrouter",
        env_name="OPENROUTER_API_KEY",
        ref_source_type=SecretRefSourceType.ENV,
        ref_locator={"env_name": "OPENROUTER_API_KEY"},
        display_name="OpenRouter API Key",
        redaction_label="OPENROUTER_API_KEY=***",
    )

    restored = ProjectSecretBinding.model_validate(binding.model_dump(mode="python"))
    assert restored == binding


def test_project_selector_state_roundtrip() -> None:
    state = ProjectSelectorState(
        selector_id="selector-cli",
        surface="cli",
        active_project_id="project-default",
        active_workspace_id="workspace-default-primary",
        source="test",
        warnings=["needs reload"],
    )

    restored = ProjectSelectorState.model_validate(state.model_dump(mode="python"))
    assert restored == state
