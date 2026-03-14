from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner
from octoagent.provider.dx.cli import main
from octoagent.provider.dx.config_schema import MemoryConfig, OctoAgentConfig
from octoagent.provider.dx.config_wizard import load_config, save_config


def test_config_memory_memu_command_writes_command_bridge(tmp_path: Path) -> None:
    runner = CliRunner()

    result = runner.invoke(
        main,
        [
            "config",
            "memory",
            "memu-command",
            "--command",
            "uv run python scripts/memu_bridge.py",
            "--cwd",
            "/tmp/memu",
            "--timeout",
            "18",
        ],
        env={"OCTOAGENT_PROJECT_ROOT": str(tmp_path)},
    )

    assert result.exit_code == 0
    assert "已更新为 MemU 本地命令配置。" in result.output
    assert "跳过 litellm-config.yaml 同步" in result.output

    config = load_config(tmp_path)
    assert config is not None
    assert config.memory.backend_mode == "memu"
    assert config.memory.bridge_transport == "command"
    assert config.memory.bridge_command == "uv run python scripts/memu_bridge.py"
    assert config.memory.bridge_command_cwd == "/tmp/memu"
    assert config.memory.bridge_command_timeout_seconds == 18.0


def test_config_memory_memu_http_updates_http_bridge_and_preserves_command_fields(
    tmp_path: Path,
) -> None:
    save_config(
        OctoAgentConfig(
            updated_at="2026-03-14",
            memory=MemoryConfig(
                backend_mode="memu",
                bridge_transport="command",
                bridge_command="uv run python scripts/memu_bridge.py",
                bridge_command_cwd="/tmp/memu",
                bridge_command_timeout_seconds=16.0,
            ),
        ),
        tmp_path,
    )
    runner = CliRunner()

    result = runner.invoke(
        main,
        [
            "config",
            "memory",
            "memu-http",
            "--bridge-url",
            "https://memory.example.com",
            "--api-key-env",
            "MEMU_API_KEY",
            "--timeout",
            "8",
        ],
        env={"OCTOAGENT_PROJECT_ROOT": str(tmp_path)},
    )

    assert result.exit_code == 0
    assert "已更新为 MemU HTTP bridge 配置。" in result.output

    config = load_config(tmp_path)
    assert config is not None
    assert config.memory.backend_mode == "memu"
    assert config.memory.bridge_transport == "http"
    assert config.memory.bridge_url == "https://memory.example.com"
    assert config.memory.bridge_api_key_env == "MEMU_API_KEY"
    assert config.memory.bridge_timeout_seconds == 8.0
    assert config.memory.bridge_command == "uv run python scripts/memu_bridge.py"
    assert config.memory.bridge_command_cwd == "/tmp/memu"


def test_config_memory_local_preserves_existing_memu_settings(tmp_path: Path) -> None:
    save_config(
        OctoAgentConfig(
            updated_at="2026-03-14",
            memory=MemoryConfig(
                backend_mode="memu",
                bridge_transport="command",
                bridge_command="uv run python scripts/memu_bridge.py",
                bridge_command_cwd="/tmp/memu",
            ),
        ),
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
    assert config.memory.bridge_transport == "command"
    assert config.memory.bridge_command == "uv run python scripts/memu_bridge.py"
    assert config.memory.bridge_command_cwd == "/tmp/memu"


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
