from __future__ import annotations

from pathlib import Path

from octoagent.provider.dx import install_bootstrap


def test_run_install_bootstrap_writes_descriptor(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='octoagent'\n", encoding="utf-8")
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text('{"name":"frontend"}\n', encoding="utf-8")
    commands: list[tuple[list[str], Path]] = []

    def fake_run_command(command: list[str], cwd: Path) -> None:
        commands.append((command, cwd))

    monkeypatch.setattr(install_bootstrap, "_run_command", fake_run_command)

    attempt = install_bootstrap.run_install_bootstrap(tmp_path, skip_frontend=False)

    assert attempt.status == "SUCCEEDED"
    assert "uv sync" in attempt.actions_completed
    assert any(command == ["npm", "install"] for command, _ in commands)
    assert any(command == ["npm", "run", "build"] for command, _ in commands)
    assert Path(attempt.runtime_descriptor_path).exists()


def test_run_install_bootstrap_existing_descriptor_without_force(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='octoagent'\n", encoding="utf-8")
    monkeypatch.setattr(install_bootstrap, "_run_command", lambda _command, _cwd: None)

    first = install_bootstrap.run_install_bootstrap(tmp_path, skip_frontend=True)
    second = install_bootstrap.run_install_bootstrap(tmp_path, skip_frontend=True)

    assert first.status == "SUCCEEDED"
    assert second.status == "SUCCEEDED"
    assert second.warnings


def test_run_install_bootstrap_missing_pyproject_fails(tmp_path: Path) -> None:
    attempt = install_bootstrap.run_install_bootstrap(tmp_path)

    assert attempt.status == "FAILED"
    assert attempt.errors
