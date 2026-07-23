from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner
from octoagent.gateway.cli.cli import main
from octoagent.gateway.services.config.config_schema import OctoAgentConfig, ProviderEntry
from octoagent.gateway.services.config.config_wizard import save_config

ACTIVATE_OPTION_ORACLE = "F151_NOOP_ACTIVATE_OPTION_STILL_ACCEPTED"


def test_provider_add_and_disable_reject_removed_activate_option(
    tmp_path: Path,
) -> None:
    add_root = tmp_path / "add"
    disable_root = tmp_path / "disable"
    add_root.mkdir()
    disable_root.mkdir()
    save_config(
        OctoAgentConfig(
            updated_at="2026-07-22",
            providers=[
                ProviderEntry(
                    id="openrouter",
                    name="OpenRouter",
                    auth_type="api_key",
                    api_key_env="OPENROUTER_API_KEY",
                    enabled=True,
                )
            ],
        ),
        disable_root,
    )
    runner = CliRunner()
    results = (
        runner.invoke(
            main,
            [
                "config",
                "provider",
                "add",
                "openrouter",
                "--auth-type",
                "api_key",
                "--api-key-env",
                "OPENROUTER_API_KEY",
                "--name",
                "OpenRouter",
                "--no-credential",
                "--activate",
            ],
            env={"OCTOAGENT_PROJECT_ROOT": str(add_root)},
        ),
        runner.invoke(
            main,
            ["config", "provider", "disable", "openrouter", "--yes", "--activate"],
            env={"OCTOAGENT_PROJECT_ROOT": str(disable_root)},
        ),
    )

    if any(
        result.exit_code != 2 or "No such option: --activate" not in result.output
        for result in results
    ):
        raise AssertionError(ACTIVATE_OPTION_ORACLE)
