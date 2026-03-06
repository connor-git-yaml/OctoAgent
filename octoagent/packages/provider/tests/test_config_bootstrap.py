from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner
from octoagent.provider.dx.cli import main
from octoagent.provider.dx.config_bootstrap import bootstrap_config, build_bootstrap_config
from octoagent.provider.dx.config_wizard import load_config


def test_build_bootstrap_config_echo() -> None:
    config = build_bootstrap_config(echo=True)
    assert config.runtime.llm_mode == "echo"
    assert config.providers == []


def test_build_bootstrap_config_interactive() -> None:
    answers = iter(["openrouter", "OpenRouter", "OPENROUTER_API_KEY"])
    config = build_bootstrap_config(prompt=lambda _text, _default: next(answers))
    assert config.providers[0].id == "openrouter"
    assert config.model_aliases["main"].provider == "openrouter"
    assert config.model_aliases["cheap"].provider == "openrouter"


def test_bootstrap_config_writes_files(tmp_path: Path) -> None:
    answers = iter(["openrouter", "OpenRouter", "OPENROUTER_API_KEY"])
    result = bootstrap_config(tmp_path, prompt=lambda _text, _default: next(answers))
    loaded = load_config(tmp_path)
    assert result.source == "interactive"
    assert loaded is not None
    assert (tmp_path / "litellm-config.yaml").exists()


def test_config_init_reuses_bootstrap(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["config", "init"],
        input="openrouter\nOpenRouter\nOPENROUTER_API_KEY\n",
        env={"OCTOAGENT_PROJECT_ROOT": str(tmp_path)},
    )
    assert result.exit_code == 0
    loaded = load_config(tmp_path)
    assert loaded is not None
    assert loaded.providers[0].id == "openrouter"
