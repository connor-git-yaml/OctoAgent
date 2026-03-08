"""gateway.main 辅助函数测试。"""

from __future__ import annotations

import importlib
from pathlib import Path

from octoagent.provider.dx.config_schema import (
    ChannelsConfig,
    ModelAlias,
    OctoAgentConfig,
    ProviderEntry,
    TelegramChannelConfig,
)
from octoagent.provider.dx.config_wizard import save_config


def _write_litellm_config(tmp_path: Path, content: str) -> None:
    (tmp_path / "litellm-config.yaml").write_text(content, encoding="utf-8")


def test_resolve_telegram_polling_timeout_from_config(
    tmp_path: Path,
    monkeypatch,
) -> None:
    save_config(
        OctoAgentConfig(
            updated_at="2026-03-07",
            channels=ChannelsConfig(
                telegram=TelegramChannelConfig(
                    enabled=True,
                    mode="polling",
                    polling_timeout_seconds=42,
                )
            ),
        ),
        tmp_path,
    )
    monkeypatch.setenv("LOGFIRE_SEND_TO_LOGFIRE", "false")
    gateway_main = importlib.import_module("octoagent.gateway.main")

    assert gateway_main._resolve_telegram_polling_timeout(tmp_path) == 42


def test_resolve_telegram_polling_timeout_falls_back_on_invalid_config(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (tmp_path / "octoagent.yaml").write_text(
        "\n".join(
            [
                "config_version: 1",
                "updated_at: '2026-03-07'",
                "channels:",
                "  telegram:",
                "    enabled: true",
                "    mode: webhook",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("LOGFIRE_SEND_TO_LOGFIRE", "false")
    gateway_main = importlib.import_module("octoagent.gateway.main")

    assert gateway_main._resolve_telegram_polling_timeout(tmp_path) == 15


def test_create_app_loads_dotenv_from_resolved_project_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("LOGFIRE_SEND_TO_LOGFIRE", "false")
    monkeypatch.setenv("OCTOAGENT_PROJECT_ROOT", str(tmp_path))
    gateway_main = importlib.import_module("octoagent.gateway.main")
    calls: list[tuple[Path | None, bool]] = []

    def fake_load_project_dotenv(
        project_root: Path | None = None,
        override: bool = False,
    ) -> bool:
        calls.append((project_root, override))
        return True

    monkeypatch.setattr(gateway_main, "load_project_dotenv", fake_load_project_dotenv)

    gateway_main.create_app()

    assert calls == [(tmp_path, False)]


def test_resolve_stream_model_aliases_from_oauth_provider(
    tmp_path: Path,
    monkeypatch,
) -> None:
    save_config(
        OctoAgentConfig(
            updated_at="2026-03-07",
            providers=[
                ProviderEntry(
                    id="openai-codex",
                    name="OpenAI Codex",
                    auth_type="oauth",
                    api_key_env="OPENAI_API_KEY",
                    enabled=True,
                ),
                ProviderEntry(
                    id="openrouter",
                    name="OpenRouter",
                    auth_type="api_key",
                    api_key_env="OPENROUTER_API_KEY",
                    enabled=True,
                ),
            ],
            model_aliases={
                "main": ModelAlias(
                    provider="openai-codex",
                    model="gpt-5.4",
                ),
                "cheap": ModelAlias(
                    provider="openrouter",
                    model="openrouter/auto",
                ),
            },
        ),
        tmp_path,
    )
    monkeypatch.setenv("LOGFIRE_SEND_TO_LOGFIRE", "false")
    gateway_main = importlib.import_module("octoagent.gateway.main")

    assert gateway_main._resolve_stream_model_aliases(tmp_path) == {"main"}


def test_resolve_stream_model_aliases_falls_back_to_litellm_config(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_litellm_config(
        tmp_path,
        "\n".join(
            [
                "model_list:",
                "  - model_name: main",
                "    litellm_params:",
                "      model: gpt-5.4",
                "      api_base: https://chatgpt.com/backend-api/codex",
                "  - model_name: cheap",
                "    litellm_params:",
                "      model: openrouter/auto",
            ]
        )
        + "\n",
    )
    monkeypatch.setenv("LOGFIRE_SEND_TO_LOGFIRE", "false")
    gateway_main = importlib.import_module("octoagent.gateway.main")

    assert gateway_main._resolve_stream_model_aliases(tmp_path) == {"main"}
