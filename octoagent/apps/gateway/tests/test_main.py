"""gateway.main 辅助函数测试。"""

from __future__ import annotations

import importlib
from pathlib import Path

from octoagent.provider.dx.config_schema import (
    ChannelsConfig,
    OctoAgentConfig,
    TelegramChannelConfig,
)
from octoagent.provider.dx.config_wizard import save_config


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
