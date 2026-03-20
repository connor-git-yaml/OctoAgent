from __future__ import annotations

from pathlib import Path

from octoagent.provider.dx.runtime_activation import RuntimeActivationService


def test_build_compose_up_command_uses_force_recreate(tmp_path: Path) -> None:
    (tmp_path / "docker-compose.litellm.yml").write_text("services: {}\n", encoding="utf-8")
    (tmp_path / ".env.litellm").write_text("LITELLM_PROXY_URL=http://localhost:4000\n", encoding="utf-8")

    service = RuntimeActivationService(tmp_path)

    command = service.build_compose_up_command()

    assert "docker compose" in command
    assert "up -d --force-recreate" in command
