from __future__ import annotations

import subprocess
from pathlib import Path
from typing import NoReturn

import pytest
from octoagent.core.models import SecretRefSourceType
from octoagent.gateway.services.operations.secret_models import SecretRef
from octoagent.gateway.services.operations.secret_refs import (
    SecretResolutionError,
    resolve_secret_ref,
)

SECRET_RUNNER_ORACLE = "F151_SECRET_REF_RUNNER_SEAM_MISSING"


class _RecordingSecretRunner:
    def __init__(self, outcome: tuple[int, str, str]) -> None:
        self.outcome = outcome
        self.calls: list[tuple[list[str], Path, dict[str, str], int]] = []

    def __call__(
        self,
        command: list[str],
        cwd: Path,
        environ: dict[str, str],
        timeout_seconds: int,
    ) -> tuple[int, str, str]:
        self.calls.append((command, cwd, environ, timeout_seconds))
        return self.outcome


def _forbid_host_subprocess(*args: object, **kwargs: object) -> NoReturn:
    raise AssertionError(f"{SECRET_RUNNER_ORACLE}: host subprocess must not run")


def test_resolve_secret_ref_from_env() -> None:
    resolved = resolve_secret_ref(
        SecretRef(
            source_type=SecretRefSourceType.ENV,
            locator={"env_name": "TEST_SECRET_ENV"},
        ),
        environ={"TEST_SECRET_ENV": "env-secret"},
    )

    assert resolved.value.get_secret_value() == "env-secret"
    assert resolved.resolution_summary == "env:TEST_SECRET_ENV"


def test_resolve_secret_ref_from_env_missing_raises() -> None:
    with pytest.raises(SecretResolutionError) as exc_info:
        resolve_secret_ref(
            SecretRef(
                source_type=SecretRefSourceType.ENV,
                locator={"env_name": "MISSING_SECRET"},
            ),
            environ={},
        )

    assert exc_info.value.code == "SECRET_ENV_NOT_FOUND"


def test_resolve_secret_ref_from_file_and_dotenv(tmp_path: Path) -> None:
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text("file-secret\n", encoding="utf-8")
    dotenv_file = tmp_path / ".env.secret"
    dotenv_file.write_text("TOKEN=dotenv-secret\n", encoding="utf-8")

    text_resolved = resolve_secret_ref(
        SecretRef(
            source_type=SecretRefSourceType.FILE,
            locator={"path": str(secret_file)},
        ),
        cwd=tmp_path,
    )
    dotenv_resolved = resolve_secret_ref(
        SecretRef(
            source_type=SecretRefSourceType.FILE,
            locator={"path": str(dotenv_file), "reader": "dotenv", "key": "TOKEN"},
        ),
        cwd=tmp_path,
    )

    assert text_resolved.value.get_secret_value() == "file-secret"
    assert text_resolved.resolution_summary == "file:secret.txt"
    assert dotenv_resolved.value.get_secret_value() == "dotenv-secret"


def test_resolve_secret_ref_from_exec() -> None:
    runner = _RecordingSecretRunner((0, "exec-secret\n", ""))
    resolved = resolve_secret_ref(
        SecretRef(
            source_type=SecretRefSourceType.EXEC,
            locator={
                "command": ["secret-helper", "--print"],
                "timeout_seconds": 5,
            },
        ),
        command_runner=runner,
    )

    assert resolved.value.get_secret_value() == "exec-secret"
    assert resolved.resolution_summary == "exec:secret-helper --print"


def test_resolve_secret_ref_from_keychain_with_fake_module() -> None:
    class FakeKeyring:
        def get_password(self, service: str, account: str) -> str:
            assert service == "octo"
            assert account == "connor"
            return "keychain-secret"

    resolved = resolve_secret_ref(
        SecretRef(
            source_type=SecretRefSourceType.KEYCHAIN,
            locator={"service": "octo", "account": "connor"},
        ),
        keyring_module=FakeKeyring(),
    )

    assert resolved.value.get_secret_value() == "keychain-secret"
    assert resolved.resolution_summary == "keychain:octo/connor"


def test_resolve_secret_ref_keychain_locator_invalid() -> None:
    with pytest.raises(SecretResolutionError) as exc_info:
        resolve_secret_ref(
            SecretRef(
                source_type=SecretRefSourceType.KEYCHAIN,
                locator={"service": "octo"},
            ),
        )

    assert exc_info.value.code == "SECRET_KEYCHAIN_LOCATOR_INVALID"


def test_exec_reference_uses_injected_runner_without_host_subprocess(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ref = SecretRef(
        source_type=SecretRefSourceType.EXEC,
        locator={"command": ["secret-helper", "--print"], "timeout_seconds": 7},
    )
    runner = _RecordingSecretRunner((0, "runner-secret\n", ""))
    monkeypatch.setattr(subprocess, "run", _forbid_host_subprocess)

    try:
        resolved = resolve_secret_ref(
            ref,
            environ={"RUNNER_ENV": "locked"},
            cwd=tmp_path,
            command_runner=runner,
        )
    except TypeError as exc:
        pytest.fail(f"{SECRET_RUNNER_ORACLE}: {exc}", pytrace=False)

    assert resolved.value.get_secret_value() == "runner-secret"
    assert resolved.resolution_summary == "exec:secret-helper --print"
    assert runner.calls == [(["secret-helper", "--print"], tmp_path, {"RUNNER_ENV": "locked"}, 7)]

    with pytest.raises(SecretResolutionError) as missing_runner:
        resolve_secret_ref(ref, environ={}, cwd=tmp_path)
    assert missing_runner.value.code == "SECRET_EXEC_RUNNER_REQUIRED"

    failed_runner = _RecordingSecretRunner((17, "", "runner denied"))
    with pytest.raises(SecretResolutionError) as failed:
        resolve_secret_ref(ref, environ={}, cwd=tmp_path, command_runner=failed_runner)
    assert failed.value.code == "SECRET_EXEC_FAILED"
    assert failed.value.message == "runner denied"
