from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner
from octoagent.provider.dx.cli import main
from octoagent.provider.dx.config_wizard import save_config
from octoagent.provider.dx.config_schema import OctoAgentConfig


def test_config_memory_show_uses_defaults_when_yaml_missing(tmp_path: Path) -> None:
    runner = CliRunner()

    result = runner.invoke(
        main,
        ["config", "memory", "show"],
        env={"OCTOAGENT_PROJECT_ROOT": str(tmp_path)},
    )

    assert result.exit_code == 0
    assert "默认 Memory 配置" in result.output
    assert "engine:" in result.output
    assert "内建记忆引擎" in result.output


def test_config_memory_show_displays_alias_info(tmp_path: Path) -> None:
    """memory show 子命令显示别名配置信息。"""
    save_config(
        OctoAgentConfig(updated_at="2026-03-14"),
        tmp_path,
    )
    runner = CliRunner()

    result = runner.invoke(
        main,
        ["config", "memory", "show"],
        env={"OCTOAGENT_PROJECT_ROOT": str(tmp_path)},
    )

    assert result.exit_code == 0
    assert "reasoning_alias:" in result.output
    assert "embedding_alias:" in result.output
