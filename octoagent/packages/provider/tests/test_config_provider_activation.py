from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner
from octoagent.provider.dx.cli import main
from octoagent.gateway.services.config.config_schema import ModelAlias, OctoAgentConfig, ProviderEntry
from octoagent.gateway.services.config.config_wizard import save_config


def test_config_provider_add_supports_activate_flag(tmp_path: Path, monkeypatch) -> None:
    import octoagent.provider.dx.config_commands as config_module

    activation_calls: list[tuple[Path, list[str]]] = []

    def fake_activate(project_root: Path, config: OctoAgentConfig) -> None:
        activation_calls.append(
            (project_root, [provider.id for provider in config.providers if provider.enabled])
        )

    monkeypatch.setattr(config_module, "_activate_runtime_for_config", fake_activate)

    runner = CliRunner()
    result = runner.invoke(
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
        env={"OCTOAGENT_PROJECT_ROOT": str(tmp_path)},
    )

    assert result.exit_code == 0
    assert activation_calls == [(tmp_path, ["openrouter"])]


def test_config_provider_disable_supports_activate_flag(tmp_path: Path, monkeypatch) -> None:
    import octoagent.provider.dx.config_commands as config_module

    save_config(
        OctoAgentConfig(
            updated_at="2026-03-20",
            providers=[
                ProviderEntry(
                    id="openrouter",
                    name="OpenRouter",
                    auth_type="api_key",
                    api_key_env="OPENROUTER_API_KEY",
                    enabled=True,
                ),
                ProviderEntry(
                    id="openai",
                    name="OpenAI",
                    auth_type="api_key",
                    api_key_env="OPENAI_API_KEY",
                    enabled=True,
                ),
            ],
            model_aliases={
                "main": ModelAlias(
                    provider="openai",
                    model="gpt-5.4",
                    description="主模型",
                ),
                "cheap": ModelAlias(
                    provider="openrouter",
                    model="openrouter/auto",
                    description="低成本模型",
                ),
            },
        ),
        tmp_path,
    )

    activation_calls: list[tuple[Path, list[str]]] = []

    def fake_activate(project_root: Path, config: OctoAgentConfig) -> None:
        activation_calls.append(
            (project_root, [provider.id for provider in config.providers if provider.enabled])
        )

    monkeypatch.setattr(config_module, "_activate_runtime_for_config", fake_activate)

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "config",
            "provider",
            "disable",
            "openrouter",
            "--yes",
            "--activate",
        ],
        env={"OCTOAGENT_PROJECT_ROOT": str(tmp_path)},
    )

    assert result.exit_code == 0
    assert activation_calls == [(tmp_path, ["openai"])]
