from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner
from octoagent.gateway.cli.cli import main
from octoagent.gateway.services.config.config_schema import OctoAgentConfig
from octoagent.gateway.services.config.config_wizard import save_config

CONFIG_SYNC_ORACLE = "F151_RETIRED_CONFIG_SYNC_CLI_STILL_REGISTERED"


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


def test_config_sync_command_is_not_registered() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["config", "sync", "--help"])

    if result.exit_code != 2 or "No such command 'sync'" not in result.output:
        raise AssertionError(CONFIG_SYNC_ORACLE)
