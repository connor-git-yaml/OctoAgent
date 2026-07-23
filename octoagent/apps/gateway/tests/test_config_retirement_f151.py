"""F151 retired runtime configuration behavior contracts."""

from __future__ import annotations

import ast
import os
import shutil
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner
from octoagent.core.models import BackupScope
from octoagent.gateway.harness.octo_harness import OctoHarness
from octoagent.gateway.services.config import config_bootstrap
from octoagent.gateway.services.config.config_bootstrap import ConfigBootstrapError
from octoagent.gateway.services.config.dotenv_loader import load_project_dotenv
from octoagent.gateway.services.operations.backup_service import BackupService

ENV_TOMBSTONE_ORACLE = "F151_ENV_TOMBSTONE_MISSING"
LEGACY_FILE_ORACLE = "F151_LEGACY_FILE_FAIL_CLOSED_MISSING"
LEGACY_EARLY_PREFLIGHT_ORACLE = "F151_LEGACY_FILE_EARLY_PREFLIGHT_MISSING"
RECOVERY_ORACLE = "F151_RECOVERY_NO_SECRET_MIGRATION_MISSING"
CONFIG_SYNC_TOOL_ORACLE = "F151_RETIRED_CONFIG_SYNC_TOOL_STILL_EXPORTED"
RETIRED_RUNTIME_ENV_KEYS = (
    "LITELLM_PROXY_URL",
    "LITELLM_PROXY_KEY",
    "LITELLM_MASTER_KEY",
    "LITELLM_PORT",
    "OCTOAGENT_WORKER_DOCKER_MODE",
    "OCTOAGENT_WORKER_DOCKER_INFO_CHECK",
)
LEGACY_RUNTIME_FILES = (
    (".env.litellm", "LEGACY_LITELLM_ENV_FILE_FOUND"),
    ("litellm-config.yaml", "LEGACY_LITELLM_CONFIG_FOUND"),
)


async def _invoke_bootstrap_paths(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    calls: list[str],
) -> None:
    from octoagent.gateway import main as gateway_main

    status_store = object()

    def record(name: str) -> None:
        calls.append(name)

    monkeypatch.setattr(
        gateway_main,
        "_warn_duplicate_instance_roots",
        lambda root: record("warn"),
    )
    monkeypatch.setattr(
        gateway_main,
        "_build_update_status_store",
        lambda root: (record("store"), status_store)[1],
    )
    monkeypatch.setattr(
        gateway_main,
        "_build_update_service",
        lambda root, *, status_store: (record("service"), object())[1],
    )
    monkeypatch.setattr(
        gateway_main,
        "_persist_runtime_state",
        lambda root, *, store: record("persist"),
    )
    app = SimpleNamespace(state=SimpleNamespace())
    await OctoHarness(project_root=project_root)._bootstrap_paths(app)


def _guard_legacy_content_io(
    monkeypatch: pytest.MonkeyPatch,
    legacy_path: Path,
    calls: list[str],
) -> None:
    original_open = Path.open
    original_read_text = Path.read_text
    original_read_bytes = Path.read_bytes
    original_rename = Path.rename
    original_replace = Path.replace
    original_copy = shutil.copy
    original_copy2 = shutil.copy2

    def reject(operation: str, candidate: object) -> None:
        if Path(candidate) == legacy_path:
            calls.append(operation)
            raise AssertionError(f"legacy content IO: {operation}")

    monkeypatch.setattr(
        Path,
        "open",
        lambda path, *args, **kwargs: reject("open", path) or original_open(path, *args, **kwargs),
    )
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda path, *args, **kwargs: (
            reject("read_text", path) or original_read_text(path, *args, **kwargs)
        ),
    )
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda path, *args, **kwargs: (
            reject("read_bytes", path) or original_read_bytes(path, *args, **kwargs)
        ),
    )
    monkeypatch.setattr(
        Path,
        "rename",
        lambda path, target: reject("rename", path) or original_rename(path, target),
    )
    monkeypatch.setattr(
        Path,
        "replace",
        lambda path, target: reject("replace", path) or original_replace(path, target),
    )
    monkeypatch.setattr(
        shutil,
        "copy",
        lambda source, target, *args, **kwargs: (
            reject("copy", source) or original_copy(source, target, *args, **kwargs)
        ),
    )
    monkeypatch.setattr(
        shutil,
        "copy2",
        lambda source, target, *args, **kwargs: (
            reject("copy2", source) or original_copy2(source, target, *args, **kwargs)
        ),
    )


class _RecordingCredentialStore:
    def __init__(self, profiles: list[object]) -> None:
        self._profiles = profiles

    def set_profile(self, profile: object) -> None:
        self._profiles.append(profile)


class _RecordingSetupAdapter:
    def __init__(
        self,
        project_root: Path,
        *,
        expected_root: Path,
        drafts: list[dict[str, object]],
    ) -> None:
        assert project_root == expected_root
        self._drafts = drafts

    async def prepare_wizard_draft(
        self,
        draft: dict[str, object],
    ) -> dict[str, object]:
        self._drafts.append(draft)
        return draft

    async def quick_connect(self, draft: dict[str, object]) -> SimpleNamespace:
        assert draft is self._drafts[-1]
        return SimpleNamespace(data={"review": {}, "activation": {}})

    async def connect_openai_codex_oauth(self, **kwargs: object) -> None:
        raise AssertionError("openrouter recovery不得触发OAuth")


@pytest.mark.asyncio
async def test_loaded_environment_rejects_retired_keys_but_preserves_supported_echo_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # python-dotenv 直接写入 os.environ；先登记外层恢复点，避免本测试把退役键
    # 泄漏给同一 pytest 进程中的后续用例。
    for key in (*RETIRED_RUNTIME_ENV_KEYS, "OCTOAGENT_LLM_MODE"):
        original = os.environ.get(key)
        monkeypatch.setenv(key, original or "")
        monkeypatch.delenv(key, raising=False)

    for retired_key in RETIRED_RUNTIME_ENV_KEYS:
        with monkeypatch.context() as scenario:
            for key in (*RETIRED_RUNTIME_ENV_KEYS, "OCTOAGENT_LLM_MODE"):
                scenario.delenv(key, raising=False)
            (tmp_path / ".env").write_text(
                f"{retired_key}=\nOCTOAGENT_LLM_MODE=echo\n",
                encoding="utf-8",
            )
            assert load_project_dotenv(project_root=tmp_path, override=False) is True
            assert retired_key in os.environ
            assert os.environ["OCTOAGENT_LLM_MODE"] == "echo"
            calls: list[str] = []
            try:
                await _invoke_bootstrap_paths(tmp_path, scenario, calls)
            except ConfigBootstrapError as exc:
                if str(exc) != f"RUNTIME_CONFIG_RETIRED: {retired_key}" or calls:
                    pytest.fail(ENV_TOMBSTONE_ORACLE, pytrace=False)
            else:
                pytest.fail(ENV_TOMBSTONE_ORACLE, pytrace=False)

    with monkeypatch.context() as scenario:
        for key in RETIRED_RUNTIME_ENV_KEYS:
            scenario.delenv(key, raising=False)
        scenario.setenv("OCTOAGENT_LLM_MODE", "echo")
        (tmp_path / ".env").write_text(
            "OCTOAGENT_LLM_MODE=litellm\n",
            encoding="utf-8",
        )
        assert load_project_dotenv(project_root=tmp_path, override=False) is True
        assert os.environ["OCTOAGENT_LLM_MODE"] == "echo"
        calls = []
        await _invoke_bootstrap_paths(tmp_path, scenario, calls)
        assert calls == ["warn", "store", "service", "persist"]


@pytest.mark.asyncio
async def test_legacy_runtime_files_are_detected_by_name_without_open_read_copy_or_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    detector = getattr(config_bootstrap, "detect_legacy_runtime_files", None)
    if not callable(detector):
        pytest.fail(LEGACY_FILE_ORACLE, pytrace=False)

    clean_root = tmp_path / "clean"
    clean_root.mkdir()
    calls: list[str] = []
    await _invoke_bootstrap_paths(clean_root, monkeypatch, calls)
    assert calls == ["warn", "store", "service", "persist"]

    for filename, expected_code in LEGACY_RUNTIME_FILES:
        root = tmp_path / filename.replace(".", "_")
        root.mkdir()
        legacy_path = root / filename
        legacy_bytes = b"SECRET_SHOULD_NEVER_BE_READ\n"
        legacy_path.write_bytes(legacy_bytes)
        forbidden_calls: list[str] = []
        original_open = Path.open
        original_read_text = Path.read_text
        original_read_bytes = Path.read_bytes
        original_rename = Path.rename
        original_replace = Path.replace
        original_copy = shutil.copy
        original_copy2 = shutil.copy2

        def reject_path(
            operation: str,
            candidate: object,
            expected_path: Path = legacy_path,
            calls_seen: list[str] = forbidden_calls,
        ) -> None:
            if Path(candidate) == expected_path:
                calls_seen.append(operation)
                raise AssertionError(f"legacy content IO: {operation}")

        with monkeypatch.context() as scenario:
            scenario.setattr(
                Path,
                "open",
                lambda path, *args, _open=original_open, **kwargs: (
                    reject_path("open", path) or _open(path, *args, **kwargs)
                ),
            )
            scenario.setattr(
                Path,
                "read_text",
                lambda path, *args, _read_text=original_read_text, **kwargs: (
                    reject_path("read_text", path) or _read_text(path, *args, **kwargs)
                ),
            )
            scenario.setattr(
                Path,
                "read_bytes",
                lambda path, *args, _read_bytes=original_read_bytes, **kwargs: (
                    reject_path("read_bytes", path) or _read_bytes(path, *args, **kwargs)
                ),
            )
            scenario.setattr(
                Path,
                "rename",
                lambda path, target, _rename=original_rename: (
                    reject_path("rename", path) or _rename(path, target)
                ),
            )
            scenario.setattr(
                Path,
                "replace",
                lambda path, target, _replace=original_replace: (
                    reject_path("replace", path) or _replace(path, target)
                ),
            )
            scenario.setattr(
                shutil,
                "copy",
                lambda source, target, *args, _copy=original_copy, **kwargs: (
                    reject_path("copy", source) or _copy(source, target, *args, **kwargs)
                ),
            )
            scenario.setattr(
                shutil,
                "copy2",
                lambda source, target, *args, _copy2=original_copy2, **kwargs: (
                    reject_path("copy2", source) or _copy2(source, target, *args, **kwargs)
                ),
            )
            calls = []
            try:
                await _invoke_bootstrap_paths(root, scenario, calls)
            except ConfigBootstrapError as exc:
                if str(exc) != expected_code or calls:
                    pytest.fail(LEGACY_FILE_ORACLE, pytrace=False)
            except AssertionError:
                pytest.fail(LEGACY_FILE_ORACLE, pytrace=False)
            else:
                pytest.fail(LEGACY_FILE_ORACLE, pytrace=False)

        assert forbidden_calls == []
        assert legacy_path.read_bytes() == legacy_bytes

    backup_root = tmp_path / "backup"
    backup_root.mkdir()
    legacy_config = backup_root / "litellm-config.yaml"
    legacy_config.write_bytes(b"model_list: [secret]\n")
    (backup_root / "octoagent.yaml").write_text("providers: []\n", encoding="utf-8")
    data_dir = backup_root / "data"
    monkeypatch.setenv("OCTOAGENT_DATA_DIR", str(data_dir))
    monkeypatch.setenv("OCTOAGENT_DB_PATH", str(data_dir / "sqlite" / "octoagent.db"))
    monkeypatch.setenv("OCTOAGENT_ARTIFACTS_DIR", str(data_dir / "artifacts"))
    service = BackupService(backup_root)
    bundle_path = backup_root / "bundle.zip"
    bundle = service._create_bundle_sync(
        bundle_path,
        "bundle-test",
        datetime(2026, 1, 1, tzinfo=UTC),
        [BackupScope.CONFIG],
    )
    with zipfile.ZipFile(bundle.output_path) as archive:
        assert "config/octoagent.yaml" in archive.namelist()
        assert "config/litellm-config.yaml" not in archive.namelist()


def test_legacy_runtime_files_fail_before_dotenv_content_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from octoagent.gateway import main as gateway_main

    for filename, expected_code in LEGACY_RUNTIME_FILES:
        root = tmp_path / f"legacy-{filename.replace('.', '-')}"
        root.mkdir()
        legacy_path = root / filename
        legacy_bytes = b"SECRET_MUST_NOT_BE_READ\n"
        legacy_path.write_bytes(legacy_bytes)
        content_io: list[str] = []
        side_effects: list[str] = []

        def forbidden(name: str, calls: list[str] = side_effects) -> None:
            calls.append(name)
            raise AssertionError(f"early side effect: {name}")

        with monkeypatch.context() as scenario:
            _guard_legacy_content_io(scenario, legacy_path, content_io)
            scenario.setattr(gateway_main, "_resolve_project_root", lambda root=root: root)
            scenario.setattr(
                gateway_main,
                "load_project_dotenv",
                lambda **kwargs: forbidden("dotenv"),
            )
            scenario.setattr(
                gateway_main,
                "_enforce_front_door_exposure",
                lambda project_root: forbidden("exposure"),
            )
            scenario.setattr(
                gateway_main,
                "_make_harness_lifespan",
                lambda factory: forbidden("harness"),
            )
            scenario.setattr(
                gateway_main,
                "FastAPI",
                lambda **kwargs: forbidden("app"),
            )
            scenario.setattr(
                BackupService,
                "_create_bundle_sync",
                lambda *args, **kwargs: forbidden("backup"),
            )
            try:
                gateway_main.create_app(harness_factory=lambda: object())
            except ConfigBootstrapError as exc:
                if str(exc) != expected_code or side_effects or content_io:
                    pytest.fail(LEGACY_EARLY_PREFLIGHT_ORACLE, pytrace=False)
            except (AssertionError, TypeError):
                pytest.fail(LEGACY_EARLY_PREFLIGHT_ORACLE, pytrace=False)
            else:
                pytest.fail(LEGACY_EARLY_PREFLIGHT_ORACLE, pytrace=False)

        assert legacy_path.read_bytes() == legacy_bytes

    clean_root = tmp_path / "clean-create-app"
    clean_root.mkdir()
    clean_calls: list[str] = []
    harness_calls: list[str] = []
    with monkeypatch.context() as scenario:
        scenario.setattr(gateway_main, "_resolve_project_root", lambda: clean_root)
        scenario.setattr(
            gateway_main,
            "load_project_dotenv",
            lambda *, project_root, override: (
                clean_calls.append(f"dotenv:{project_root}:{override}") or False
            ),
        )
        scenario.setattr(
            gateway_main,
            "_enforce_front_door_exposure",
            lambda project_root: clean_calls.append(f"exposure:{project_root}"),
        )
        app = gateway_main.create_app(
            harness_factory=lambda: harness_calls.append("harness") or object()
        )

    assert app is not None
    assert clean_calls == [f"dotenv:{clean_root}:False", f"exposure:{clean_root}"]
    assert harness_calls == []


def test_auth_and_setup_recovery_never_read_or_migrate_legacy_runtime_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from octoagent.gateway.cli import auth_commands
    from octoagent.gateway.cli import cli as gateway_cli
    from octoagent.gateway.services.operations import setup_governance_adapter

    recovery_seams = (
        getattr(config_bootstrap, "build_canonical_reauth_config", None),
        getattr(auth_commands, "canonical_reauth_command", None),
        getattr(gateway_cli, "setup_recovery_composition", None),
    )
    if not all(callable(seam) for seam in recovery_seams):
        pytest.fail(RECOVERY_ORACLE, pytrace=False)

    for filename, _expected_code in LEGACY_RUNTIME_FILES:
        root = tmp_path / f"recovery-{filename.replace('.', '-')}"
        root.mkdir()
        legacy_path = root / filename
        legacy_bytes = b"LEGACY_SECRET_MUST_NOT_MIGRATE\n"
        legacy_path.write_bytes(legacy_bytes)
        content_io: list[str] = []
        auth_profiles: list[object] = []
        setup_drafts: list[dict[str, object]] = []

        with monkeypatch.context() as scenario:
            _guard_legacy_content_io(scenario, legacy_path, content_io)
            scenario.setattr(
                auth_commands,
                "CredentialStore",
                lambda profiles=auth_profiles: _RecordingCredentialStore(profiles),
            )
            scenario.setattr(
                setup_governance_adapter,
                "LocalSetupGovernanceAdapter",
                lambda project_root, expected_root=root, drafts=setup_drafts: (
                    _RecordingSetupAdapter(
                        project_root,
                        expected_root=expected_root,
                        drafts=drafts,
                    )
                ),
            )
            runner = CliRunner()
            auth_result = runner.invoke(
                auth_commands.auth,
                ["paste-token", "--provider", "anthropic-claude"],
                input=(
                    "sk-ant-oat01-valid-access-token-long-enough\n"
                    "sk-ant-ort01-valid-refresh-token-long-enough\n"
                ),
                env={"OCTOAGENT_PROJECT_ROOT": str(root)},
            )
            setup_result = runner.invoke(
                gateway_cli.main,
                ["setup", "--provider", "openrouter", "--skip-live-verify"],
                input="\n\nsk-provider-value\n",
                env={"OCTOAGENT_PROJECT_ROOT": str(root)},
            )

        if auth_result.exit_code != 0 or setup_result.exit_code != 0:
            pytest.fail(RECOVERY_ORACLE, pytrace=False)
        assert filename in auth_result.output
        assert filename in setup_result.output
        assert len(auth_profiles) == 1
        assert len(setup_drafts) == 1
        assert content_io == []
        assert legacy_path.read_bytes() == legacy_bytes
        assert not (root / "octoagent.yaml").exists()
        assert not (root / ".env").exists()


def test_config_sync_builtin_result_export_and_manifest_are_absent() -> None:
    from octoagent.core import models

    repo_root = Path(__file__).parents[4]
    sources = {
        "tools": repo_root
        / "octoagent/apps/gateway/src/octoagent/gateway/services/builtin_tools/config_tools.py",
        "results": repo_root / "octoagent/packages/core/src/octoagent/core/models/tool_results.py",
        "exports": repo_root / "octoagent/packages/core/src/octoagent/core/models/__init__.py",
    }
    parsed = {name: ast.parse(path.read_text(encoding="utf-8")) for name, path in sources.items()}
    class_names = {
        node.name for node in ast.walk(parsed["results"]) if isinstance(node, ast.ClassDef)
    }
    function_names = {
        node.name
        for node in ast.walk(parsed["tools"])
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    retired_present = (
        "ConfigSyncResult" in class_names
        or "config_sync" in function_names
        or hasattr(models, "ConfigSyncResult")
        or "config.sync" in sources["tools"].read_text(encoding="utf-8")
        or "ConfigSyncResult" in sources["exports"].read_text(encoding="utf-8")
    )
    if retired_present:
        pytest.fail(CONFIG_SYNC_TOOL_ORACLE, pytrace=False)
