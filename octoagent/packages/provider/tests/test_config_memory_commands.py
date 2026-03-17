from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner
from octoagent.provider.dx.cli import main
from octoagent.provider.dx.config_schema import OctoAgentConfig
from octoagent.provider.dx.config_wizard import load_config, save_config


def test_config_memory_local_writes_local_only_mode(tmp_path: Path) -> None:
    """memory local 子命令写入 local_only 模式。"""
    save_config(
        OctoAgentConfig(updated_at="2026-03-14"),
        tmp_path,
    )
    runner = CliRunner()

    result = runner.invoke(
        main,
        ["config", "memory", "local"],
        env={"OCTOAGENT_PROJECT_ROOT": str(tmp_path)},
    )

    assert result.exit_code == 0
    assert "已切换为本地 Memory 模式。" in result.output

    config = load_config(tmp_path)
    assert config is not None
    assert config.memory.backend_mode == "local_only"


def test_config_memory_show_uses_defaults_when_yaml_missing(tmp_path: Path) -> None:
    runner = CliRunner()

    result = runner.invoke(
        main,
        ["config", "memory", "show"],
        env={"OCTOAGENT_PROJECT_ROOT": str(tmp_path)},
    )

    assert result.exit_code == 0
    assert "默认 Memory 配置" in result.output
    assert "backend_mode:       local_only" in result.output
