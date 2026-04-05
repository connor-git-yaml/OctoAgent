from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner
from octoagent.provider.dx.cli import main
from octoagent.provider.dx.config_bootstrap import bootstrap_config, build_bootstrap_config
from octoagent.gateway.services.config.config_wizard import load_config


def test_build_bootstrap_config_echo() -> None:
    config = build_bootstrap_config(echo=True)
    assert config.runtime.llm_mode == "echo"
    assert config.providers == []


def test_build_bootstrap_config_interactive() -> None:
    answers = iter(["OpenRouter", "OPENROUTER_API_KEY"])
    config = build_bootstrap_config(
        prompt=lambda _text, _default: next(answers),
        choice_prompt=lambda _text, _choices, _default: "openrouter",
    )
    assert config.providers[0].id == "openrouter"
    assert config.model_aliases["main"].provider == "openrouter"
    assert config.model_aliases["cheap"].provider == "openrouter"


def test_build_bootstrap_config_with_telegram_channel() -> None:
    answers = iter(["OpenRouter", "OPENROUTER_API_KEY"])
    config = build_bootstrap_config(
        prompt=lambda _text, _default: next(answers),
        choice_prompt=lambda _text, _choices, _default: "openrouter",
        enable_telegram=True,
        telegram_mode="polling",
    )
    assert config.channels.telegram.enabled is True
    assert config.channels.telegram.mode == "polling"
    assert config.channels.telegram.bot_token_env == "TELEGRAM_BOT_TOKEN"


def test_build_bootstrap_config_openai_codex_oauth_preset() -> None:
    answers = iter(["", ""])
    config = build_bootstrap_config(
        prompt=lambda _text, default: next(answers) or default,
        choice_prompt=lambda _text, _choices, _default: "openai-codex",
    )
    provider = config.providers[0]
    assert provider.id == "openai-codex"
    assert provider.auth_type == "oauth"
    assert provider.api_key_env == "OPENAI_API_KEY"
    assert config.model_aliases["main"].model == "gpt-5.4"
    assert config.model_aliases["main"].thinking_level == "xhigh"
    assert config.model_aliases["cheap"].thinking_level == "low"


def test_build_bootstrap_config_custom_provider() -> None:
    answers = iter(
        [
            "siliconflow",
            "SiliconFlow",
            "SILICONFLOW_API_KEY",
            "https://api.siliconflow.cn/v1",
            "Qwen/Qwen3-32B",
            "Qwen/Qwen3-14B",
        ]
    )
    config = build_bootstrap_config(
        prompt=lambda _text, _default: next(answers),
        choice_prompt=lambda _text, _choices, _default: "custom",
    )

    provider = config.providers[0]
    assert provider.id == "siliconflow"
    assert provider.base_url == "https://api.siliconflow.cn/v1"
    assert config.model_aliases["main"].model == "Qwen/Qwen3-32B"
    assert config.model_aliases["cheap"].model == "Qwen/Qwen3-14B"


def test_bootstrap_config_writes_files(tmp_path: Path) -> None:
    answers = iter(["OpenRouter", "OPENROUTER_API_KEY"])
    result = bootstrap_config(
        tmp_path,
        prompt=lambda _text, _default: next(answers),
        choice_prompt=lambda _text, _choices, _default: "openrouter",
    )
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


def test_config_init_can_enable_telegram(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "config",
            "init",
            "--enable-telegram",
            "--telegram-mode",
            "webhook",
            "--telegram-webhook-url",
            "https://example.com/api/telegram/webhook",
        ],
        input="openrouter\nOpenRouter\nOPENROUTER_API_KEY\n",
        env={"OCTOAGENT_PROJECT_ROOT": str(tmp_path)},
    )

    assert result.exit_code == 0
    loaded = load_config(tmp_path)
    assert loaded is not None
    assert loaded.channels.telegram.enabled is True
    assert loaded.channels.telegram.mode == "webhook"
    assert loaded.channels.telegram.webhook_url == "https://example.com/api/telegram/webhook"


def test_config_init_supports_openai_codex_oauth_preset(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["config", "init"],
        input="openai-codex\n\n\n",
        env={"OCTOAGENT_PROJECT_ROOT": str(tmp_path)},
    )

    assert result.exit_code == 0
    loaded = load_config(tmp_path)
    assert loaded is not None
    assert loaded.providers[0].id == "openai-codex"
    assert loaded.providers[0].auth_type == "oauth"
    assert loaded.model_aliases["main"].model == "gpt-5.4"
    assert loaded.model_aliases["cheap"].thinking_level == "low"
