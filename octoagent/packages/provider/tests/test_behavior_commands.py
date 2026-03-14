from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner
from octoagent.provider.dx.cli import main


def test_behavior_init_creates_project_files(tmp_path: Path) -> None:
    runner = CliRunner()
    env = {"OCTOAGENT_PROJECT_ROOT": str(tmp_path)}

    result = runner.invoke(main, ["behavior", "init"], env=env)

    assert result.exit_code == 0
    assert "Behavior Init" in result.output
    assert "scope=project" in result.output
    target_dir = tmp_path / "behavior" / "projects" / "default"
    assert (target_dir / "AGENTS.md").exists()
    assert (target_dir / "USER.md").exists()
    assert (target_dir / "PROJECT.md").exists()
    assert (target_dir / "TOOLS.md").exists()
    assert "Butler" in (target_dir / "AGENTS.md").read_text(encoding="utf-8")


def test_behavior_ls_and_show_report_effective_sources(tmp_path: Path) -> None:
    runner = CliRunner()
    env = {"OCTOAGENT_PROJECT_ROOT": str(tmp_path)}

    initialized = runner.invoke(main, ["behavior", "init", "--scope", "system"], env=env)
    assert initialized.exit_code == 0

    listed = runner.invoke(main, ["behavior", "ls"], env=env)
    assert listed.exit_code == 0
    assert "Behavior Workspace" in listed.output
    assert "filesystem:behavior/system" in listed.output
    assert "AGENTS.md" in listed.output
    assert "system_file" in listed.output
    assert "SOUL.md" in listed.output
    assert "not_enabled" in listed.output

    shown = runner.invoke(main, ["behavior", "show", "agents"], env=env)
    assert shown.exit_code == 0
    assert "Behavior File" in shown.output
    assert "file=AGENTS.md" in shown.output
    assert "source_kind=system_file" in shown.output
    assert "你是 OctoAgent 的 Butler" in shown.output
