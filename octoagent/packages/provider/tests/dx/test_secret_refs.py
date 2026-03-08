from __future__ import annotations

import sys
from pathlib import Path

import pytest
from octoagent.core.models import SecretRefSourceType
from octoagent.provider.dx.secret_models import SecretRef
from octoagent.provider.dx.secret_refs import SecretResolutionError, resolve_secret_ref


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
    resolved = resolve_secret_ref(
        SecretRef(
            source_type=SecretRefSourceType.EXEC,
            locator={
                "command": [sys.executable, "-c", "print('exec-secret')"],
                "timeout_seconds": 5,
            },
        ),
    )

    assert resolved.value.get_secret_value() == "exec-secret"
    assert resolved.resolution_summary.startswith("exec:")


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
