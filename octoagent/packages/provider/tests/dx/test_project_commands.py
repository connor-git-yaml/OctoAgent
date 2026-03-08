from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner
from octoagent.provider.dx.cli import main


def test_project_create_select_and_inspect(tmp_path: Path) -> None:
    runner = CliRunner()
    env = {"OCTOAGENT_PROJECT_ROOT": str(tmp_path)}

    created = runner.invoke(
        main,
        ["project", "create", "--name", "Demo Project", "--description", "demo"],
        env=env,
    )
    assert created.exit_code == 0
    assert "Project Create" in created.output
    assert "slug=demo-project" in created.output
    assert "active_project_changed=true" in created.output

    selected = runner.invoke(main, ["project", "select", "default"], env=env)
    assert selected.exit_code == 0
    assert "Project Select" in selected.output
    assert "current_project=default" in selected.output

    inspected = runner.invoke(main, ["project", "inspect"], env=env)
    assert inspected.exit_code == 0
    assert "Project Inspect" in inspected.output
    assert "project=default" in inspected.output
    assert "binding_summary={}" in inspected.output


def test_project_edit_wizard_status_and_apply(tmp_path: Path) -> None:
    runner = CliRunner()
    env = {"OCTOAGENT_PROJECT_ROOT": str(tmp_path)}

    created = runner.invoke(main, ["project", "create", "--name", "Wizard Demo"], env=env)
    assert created.exit_code == 0

    wizard_input = "\n".join(
        [
            "openrouter",
            "OpenRouter",
            "api_key",
            "OPENROUTER_API_KEY",
            "openrouter/auto",
            "openrouter/auto",
            "litellm",
            "http://localhost:4000",
            "LITELLM_MASTER_KEY",
            "n",
        ]
    )
    started = runner.invoke(
        main,
        ["project", "edit", "--wizard"],
        env=env,
        input=wizard_input,
    )
    assert started.exit_code == 0
    assert "Wizard Session" in started.output
    assert "status=ready_for_apply" in started.output
    assert "draft_secret_targets=2" in started.output

    status = runner.invoke(main, ["project", "edit", "--wizard-status"], env=env)
    assert status.exit_code == 0
    assert "status=ready_for_apply" in status.output

    applied = runner.invoke(main, ["project", "edit", "--apply-wizard"], env=env)
    assert applied.exit_code == 0
    assert "status=action_required" in applied.output
    assert "current_step=secrets" in applied.output
    assert (tmp_path / "octoagent.yaml").exists()
    assert (tmp_path / "litellm-config.yaml").exists()
