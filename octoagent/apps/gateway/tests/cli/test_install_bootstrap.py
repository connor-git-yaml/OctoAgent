from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest
from octoagent.core.models import ManagedRuntimeDescriptor, utc_now
from octoagent.gateway.cli import bench_commands, install_bootstrap
from octoagent.gateway.services.operations.update_status_store import UpdateStatusStore


def _source_checkout(tmp_path: Path) -> Path:
    checkout = tmp_path / "checkout"
    project = checkout / "octoagent"
    (checkout / ".git").mkdir(parents=True)
    (checkout / "benchmarks" / "runner").mkdir(parents=True)
    (checkout / "benchmarks" / "runner" / "cli.py").write_text("", encoding="utf-8")
    (project / "scripts").mkdir(parents=True)
    (project / "pyproject.toml").write_text("[project]\nname='octoagent'\n", encoding="utf-8")
    (project / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (project / "scripts" / "install-octo.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    return project


def test_install_bootstrap_requires_source_checkout_before_side_effects(
    tmp_path: Path,
    monkeypatch,
) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(
        install_bootstrap,
        "_run_command",
        lambda command, _cwd: commands.append(command),
    )
    source_root = _source_checkout(tmp_path)
    accepted = install_bootstrap.run_install_bootstrap(source_root, skip_frontend=True)
    assert accepted.status == "SUCCEEDED"
    accepted_command_count = len(commands)

    wheel_root = tmp_path / "installed-wheel"
    wheel_root.mkdir()
    (wheel_root / "pyproject.toml").write_text(
        "[project]\nname='octoagent-gateway'\n", encoding="utf-8"
    )
    failures: list[str] = []
    try:
        install_bootstrap.run_install_bootstrap(wheel_root, skip_frontend=True)
    except SystemExit as exc:
        if exc.code != 69:
            failures.append(f"exit={exc.code}")
        if "SOURCE_CHECKOUT_REQUIRED" not in str(exc):
            failures.append("missing typed error")
    else:
        failures.append("wheel invocation accepted")
    if len(commands) != accepted_command_count:
        failures.append("subprocess side effect reached")
    if (wheel_root / "data").exists():
        failures.append("filesystem side effect reached")
    assert not failures, "F151_INSTALL_SOURCE_GUARD_MISSING: " + "; ".join(failures)


def test_bench_entrypoint_resolves_source_and_forwards_argv(monkeypatch) -> None:
    resolved: list[Path] = []
    received: list[list[str]] = []
    monkeypatch.setattr(
        bench_commands,
        "resolve_managed_source_checkout",
        lambda candidate: resolved.append(candidate) or candidate,
    )

    benchmarks = types.ModuleType("benchmarks")
    benchmarks.__path__ = []
    runner = types.ModuleType("benchmarks.runner")
    runner.__path__ = []
    cli = types.ModuleType("benchmarks.runner.cli")
    cli.main = lambda argv: received.append(list(argv)) or 23
    monkeypatch.setitem(sys.modules, "benchmarks", benchmarks)
    monkeypatch.setitem(sys.modules, "benchmarks.runner", runner)
    monkeypatch.setitem(sys.modules, "benchmarks.runner.cli", cli)

    with pytest.raises(SystemExit, match="23"):
        bench_commands.app(["daily", "--json"])

    assert resolved == [Path.cwd()]
    assert received == [["daily", "--json"]]


def test_run_install_bootstrap_writes_descriptor(tmp_path: Path, monkeypatch) -> None:
    project_root = _source_checkout(tmp_path)
    frontend = project_root / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text('{"name":"frontend"}\n', encoding="utf-8")
    commands: list[tuple[list[str], Path]] = []

    def fake_run_command(command: list[str], cwd: Path) -> None:
        commands.append((command, cwd))

    monkeypatch.setattr(install_bootstrap, "_run_command", fake_run_command)

    attempt = install_bootstrap.run_install_bootstrap(project_root, skip_frontend=False)

    assert attempt.status == "SUCCEEDED"
    assert "uv sync" in attempt.actions_completed
    assert any(command == ["npm", "install"] for command, _ in commands)
    assert any(command == ["npm", "run", "build"] for command, _ in commands)
    assert Path(attempt.runtime_descriptor_path).exists()


def test_run_install_bootstrap_existing_descriptor_without_force(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = _source_checkout(tmp_path)
    frontend = project_root / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text('{"name":"frontend"}\n', encoding="utf-8")
    commands: list[tuple[list[str], Path]] = []

    def fake_run_command(command: list[str], cwd: Path) -> None:
        commands.append((command, cwd))

    monkeypatch.setattr(install_bootstrap, "_run_command", fake_run_command)

    first = install_bootstrap.run_install_bootstrap(project_root, skip_frontend=False)
    commands.clear()
    second = install_bootstrap.run_install_bootstrap(project_root, skip_frontend=False)

    assert first.status == "SUCCEEDED"
    assert second.status == "SUCCEEDED"
    assert second.warnings
    assert any(command == ["uv", "sync"] for command, _ in commands)
    assert any(command == ["npm", "install"] for command, _ in commands)
    assert any(command == ["npm", "run", "build"] for command, _ in commands)


def test_run_install_bootstrap_missing_pyproject_fails(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(install_bootstrap, "require_source_checkout", lambda root: root)
    attempt = install_bootstrap.run_install_bootstrap(tmp_path)

    assert attempt.status == "FAILED"
    assert attempt.errors


def test_run_install_bootstrap_bootstraps_home_instance(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = _source_checkout(tmp_path)
    monkeypatch.setattr(install_bootstrap, "_run_command", lambda _command, _cwd: None)
    instance_root = tmp_path / "home-instance"

    attempt = install_bootstrap.run_install_bootstrap(
        project_root,
        skip_frontend=True,
        instance_root=instance_root,
    )

    assert attempt.status == "SUCCEEDED"
    assert (instance_root / "octoagent.yaml").exists()
    # Feature 081 P3b：litellm-config.yaml 不再产出（generate_litellm_config no-op）
    assert (instance_root / "data" / "sqlite").exists()
    assert (instance_root / "data" / "artifacts").exists()
    assert (instance_root / "bin" / "octo").exists()
    assert (instance_root / "bin" / "octo-start").exists()
    assert (instance_root / "bin" / "octo-doctor").exists()
    # F081 cleanup：RuntimeConfig 已退化为空块，runtime.llm_mode 已删除。
    assert any("prepare instance root" in item for item in attempt.actions_completed)
    assert any("octo-start" in item for item in attempt.next_actions)
    assert not any("uvicorn octoagent.gateway.main:app" in item for item in attempt.next_actions)
    descriptor = UpdateStatusStore(project_root).load_runtime_descriptor()
    assert descriptor is not None
    assert descriptor.start_command == [
        "/bin/bash",
        str(project_root / "scripts" / "run-octo-home.sh"),
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
    project_root = _source_checkout(tmp_path)
    monkeypatch.setattr(install_bootstrap, "_run_command", lambda _command, _cwd: None)
    instance_root = tmp_path / "home-instance"
    instance_root.mkdir()
    (instance_root / "octoagent.yaml").write_text(
        "config_version: 1\nupdated_at: '2026-03-10'\nruntime:\n  llm_mode: litellm\n",
        encoding="utf-8",
    )

    attempt = install_bootstrap.run_install_bootstrap(
        project_root,
        skip_frontend=True,
        instance_root=instance_root,
    )

    assert attempt.status == "SUCCEEDED"
    content = (instance_root / "octoagent.yaml").read_text(encoding="utf-8")
    assert "llm_mode: litellm" in content
    assert any("保留现有 octoagent.yaml" in item for item in attempt.warnings)


def _byte_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_explicit_install_atomically_migrates_legacy_descriptor_but_plain_load_does_not_write(  # noqa: E501
    tmp_path: Path,
    monkeypatch,
) -> None:
    oracle = "F151_SINGLE_PRODUCTION_STARTUP_ENTRY_MISSING"
    source_root = _source_checkout(tmp_path / "source")
    instance_root = tmp_path / "instance"
    legacy_root = instance_root / "app" / "octoagent"
    now = utc_now()
    legacy_descriptor = ManagedRuntimeDescriptor(
        project_root=str(legacy_root),
        start_command=["/bin/bash", str(source_root / "scripts/run-octo-home.sh")],
        verify_url="http://127.0.0.1:8123/ready?profile=core",
        workspace_sync_command=["/bin/bash", "-lc", "git pull && uv sync"],
        frontend_build_command=["/bin/bash", "-lc", "npm install && npm run build"],
        created_at=now,
        updated_at=now,
    )
    legacy_store = UpdateStatusStore(legacy_root)
    legacy_store.save_runtime_descriptor(legacy_descriptor)
    store = UpdateStatusStore(instance_root)
    before_plain = _byte_snapshot(instance_root)
    issues: list[str] = []
    if store.load_runtime_descriptor() is not None:
        issues.append("plain load accepted legacy descriptor")
    if _byte_snapshot(instance_root) != before_plain:
        issues.append("plain load mutated legacy descriptor tree")

    monkeypatch.setattr(install_bootstrap, "_run_command", lambda _command, _cwd: None)
    attempt = install_bootstrap.run_install_bootstrap(
        source_root,
        skip_frontend=True,
        instance_root=instance_root,
    )
    migrated = store.load_runtime_descriptor()
    if attempt.status != "SUCCEEDED":
        issues.append(f"install status={attempt.status}")
    if migrated is None:
        issues.append("explicit install did not create canonical descriptor")
    elif migrated.verify_url != legacy_descriptor.verify_url:
        issues.append("explicit migration replaced legacy descriptor identity")
    if (
        legacy_store.descriptor_path.read_bytes()
        != before_plain[legacy_store.descriptor_path.relative_to(instance_root).as_posix()]
    ):
        issues.append("explicit migration rewrote legacy source bytes")
    if issues:
        pytest.fail(f"{oracle}: {'; '.join(issues)}", pytrace=False)
