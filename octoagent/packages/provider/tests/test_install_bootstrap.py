from __future__ import annotations

from pathlib import Path

from octoagent.provider.dx import install_bootstrap
from octoagent.provider.dx.update_status_store import UpdateStatusStore


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
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text('{"name":"frontend"}\n', encoding="utf-8")
    commands: list[tuple[list[str], Path]] = []

    def fake_run_command(command: list[str], cwd: Path) -> None:
        commands.append((command, cwd))

    monkeypatch.setattr(install_bootstrap, "_run_command", fake_run_command)

    first = install_bootstrap.run_install_bootstrap(tmp_path, skip_frontend=False)
    commands.clear()
    second = install_bootstrap.run_install_bootstrap(tmp_path, skip_frontend=False)

    assert first.status == "SUCCEEDED"
    assert second.status == "SUCCEEDED"
    assert second.warnings
    assert any(command == ["uv", "sync"] for command, _ in commands)
    assert any(command == ["npm", "install"] for command, _ in commands)
    assert any(command == ["npm", "run", "build"] for command, _ in commands)


def test_run_install_bootstrap_missing_pyproject_fails(tmp_path: Path) -> None:
    attempt = install_bootstrap.run_install_bootstrap(tmp_path)

    assert attempt.status == "FAILED"
    assert attempt.errors


def test_run_install_bootstrap_bootstraps_home_instance(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='octoagent'\n", encoding="utf-8")
    monkeypatch.setattr(install_bootstrap, "_run_command", lambda _command, _cwd: None)
    instance_root = tmp_path / "home-instance"

    attempt = install_bootstrap.run_install_bootstrap(
        tmp_path,
        skip_frontend=True,
        instance_root=instance_root,
    )

    assert attempt.status == "SUCCEEDED"
    assert (instance_root / "octoagent.yaml").exists()
    assert (instance_root / "litellm-config.yaml").exists()
    assert (instance_root / "data" / "sqlite").exists()
    assert (instance_root / "data" / "artifacts").exists()
    assert (instance_root / "bin" / "octo").exists()
    assert (instance_root / "bin" / "octo-start").exists()
    assert (instance_root / "bin" / "octo-doctor").exists()
    content = (instance_root / "octoagent.yaml").read_text(encoding="utf-8")
    assert "llm_mode: echo" in content
    assert any("prepare instance root" in item for item in attempt.actions_completed)
    assert any("octo-start" in item for item in attempt.next_actions)
    assert not any("uvicorn octoagent.gateway.main:app" in item for item in attempt.next_actions)
    descriptor = UpdateStatusStore(tmp_path).load_runtime_descriptor()
    assert descriptor is not None
    assert descriptor.start_command == [
        "/bin/bash",
        str(tmp_path / "scripts" / "run-octo-home.sh"),
    ]
    assert descriptor.environment_overrides["OCTOAGENT_INSTANCE_ROOT"] == str(instance_root)
    assert descriptor.environment_overrides["OCTOAGENT_PROJECT_ROOT"] == str(instance_root)
    assert descriptor.environment_overrides["OCTOAGENT_DATA_DIR"] == str(instance_root / "data")
    instance_descriptor = UpdateStatusStore(instance_root).load_runtime_descriptor()
    assert instance_descriptor is not None
    assert instance_descriptor.start_command == descriptor.start_command


def test_run_install_bootstrap_preserves_existing_home_instance_without_force(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='octoagent'\n", encoding="utf-8")
    monkeypatch.setattr(install_bootstrap, "_run_command", lambda _command, _cwd: None)
    instance_root = tmp_path / "home-instance"
    instance_root.mkdir()
    (instance_root / "octoagent.yaml").write_text(
        "config_version: 1\nupdated_at: '2026-03-10'\nruntime:\n  llm_mode: litellm\n",
        encoding="utf-8",
    )

    attempt = install_bootstrap.run_install_bootstrap(
        tmp_path,
        skip_frontend=True,
        instance_root=instance_root,
    )

    assert attempt.status == "SUCCEEDED"
    content = (instance_root / "octoagent.yaml").read_text(encoding="utf-8")
    assert "llm_mode: litellm" in content
    assert any("保留现有 octoagent.yaml" in item for item in attempt.warnings)
