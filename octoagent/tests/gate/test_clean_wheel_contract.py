"""F151 clean-wheel 唯一入口的隔离安装与启动合同。"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import sysconfig
import tomllib
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

REPO_ROOT = Path(__file__).resolve().parents[3]
CHECKER = REPO_ROOT / "repo-scripts" / "check-clean-wheel.py"
ORACLE = "F151_CLEAN_WHEEL_CONTRACT_MISSING"
STANDARD_BACKEND_ORACLE = "F151_STANDARD_BACKEND_SCAFFOLD_MISSING"
IMPORT_CLASSIFICATION_ORACLE = "F151_DIRECT_DEPENDENCY_IMPORT_CLASSIFICATION_MISSING"
CHILD_OBSERVATION_ORACLE = "F151_CLEAN_WHEEL_CHILD_OBSERVATION_MISSING"
FULL_ORACLE = "F151_CLEAN_WHEEL_FULL_CONTRACT_MISSING"
FINAL_DEPENDENCY_ORACLE = "F151_FINAL_DIRECT_DEPENDENCY_CLOSURE_MISSING"

IMPORT_CONTEXTS = {
    "runtime-required",
    "optional-lazy",
    "type-checking",
    "test-plugin",
}
IMPORT_OCCURRENCE_FIELDS = {
    "distribution",
    "source_file",
    "line",
    "syntax",
    "import_root",
    "resolved_distribution",
    "context",
    "workspace_owner",
    "ownership_state",
}
PRELIMINARY_OCCURRENCE_FIELDS = IMPORT_OCCURRENCE_FIELDS
PRELIMINARY_INVENTORY_CONTRACT = "preliminary-unowned-v1"
CHILD_OBSERVATION_FIELDS = {
    "cwd",
    "sys_path",
    "environment",
    "enable_user_site",
    "user_site",
    "prefix",
    "base_prefix",
    "workspace_origins",
}
CHILD_ENVIRONMENT_FIELDS = {
    "HOME",
    "XDG_CONFIG_HOME",
    "XDG_CACHE_HOME",
    "TMPDIR",
    "UV_CACHE_DIR",
    "PYTHONNOUSERSITE",
    "PYTHONPATH",
}

CLASSIFICATION_RUNTIME_SOURCE = """from typing import TYPE_CHECKING
import importlib
import requests
import unowned_third_party
from yaml import safe_load
dotenv = importlib.import_module("dotenv")
if TYPE_CHECKING:
    import pydantic
try:
    import lancedb
except (ImportError, ModuleNotFoundError):
    lancedb = None
from octoagent.core import marker
def execute() -> None:
    import httpx
"""

SUPPORTED_HELP = {
    "octo --help",
    "octo auth --help",
    "octo doctor --help",
}
MANIFEST_PATHS = {
    "provider": REPO_ROOT / "octoagent/packages/provider/pyproject.toml",
    "gateway": REPO_ROOT / "octoagent/apps/gateway/pyproject.toml",
}
PROVIDER_CHECKS = {"environment", "provider.import", "provider.requires_dist"}
GATEWAY_RELOCATION_CHECKS = {
    "environment",
    "gateway.cli-help",
    "gateway.import",
    "gateway.level-boundary",
    "gateway.namespace",
    "gateway.requires_dist",
}
FUTURE_CHECK_IDS = {
    "gateway.invalid-startup",
    "gateway.readiness",
    "gateway.sigterm",
    "gateway.startup",
    "source-managed.guard",
    "source-managed.read-only",
}
GATEWAY_FULL_CHECKS = GATEWAY_RELOCATION_CHECKS | {
    "gateway.invalid-startup",
    "gateway.readiness",
    "gateway.sigterm",
    "gateway.startup",
}
ALL_CHECKS = (
    PROVIDER_CHECKS
    | GATEWAY_FULL_CHECKS
    | {
        "dependency.final-closure",
        "source-managed.guard",
        "source-managed.read-only",
    }
)
SOURCE_MANAGED_COMMANDS = {
    "octo service install",
    "octo service uninstall",
    "octo update",
    "octo restart",
    "octo stop",
    "python -m octoagent.gateway.cli.install_bootstrap",
    "octo-bench",
}
SOURCE_READ_ONLY_COMMANDS = {
    "octo --help",
    "octo service --help",
    "octo service status",
    "octo logs",
    "octo update --help",
}
PROVIDER_FINAL_REQUIRES_DIST = {
    "filelock",
    "httpx",
    "litellm",
    "octoagent-core",
    "pydantic",
    "python-ulid",
    "structlog",
}
GATEWAY_FINAL_REQUIRES_DIST = {
    "aiosqlite",
    "apscheduler",
    "click",
    "cryptography",
    "fastapi",
    "filelock",
    "httpx",
    "jieba",
    "keyring",
    "lancedb",
    "logfire",
    "mcp",
    "octoagent-core",
    "octoagent-memory",
    "octoagent-policy",
    "octoagent-protocol",
    "octoagent-provider",
    "octoagent-skills",
    "octoagent-tooling",
    "pyarrow",
    "pydantic",
    "pydantic-graph",
    "python-dotenv",
    "python-ulid",
    "pyyaml",
    "questionary",
    "rich",
    "sse-starlette",
    "starlette",
    "structlog",
    "uvicorn",
    "watchdog",
}
_ACTIVE_ORACLE: ContextVar[str] = ContextVar("f151_clean_wheel_oracle", default=ORACLE)
STANDARD_BACKEND_WORKSPACES = {
    "octoagent-core": "octoagent/packages/core/pyproject.toml",
    "octoagent-provider": "octoagent/packages/provider/pyproject.toml",
    "octoagent-protocol": "octoagent/packages/protocol/pyproject.toml",
    "octoagent-tooling": "octoagent/packages/tooling/pyproject.toml",
    "octoagent-skills": "octoagent/packages/skills/pyproject.toml",
    "octoagent-policy": "octoagent/packages/policy/pyproject.toml",
    "octoagent-memory": "octoagent/packages/memory/pyproject.toml",
    "octoagent-gateway": "octoagent/apps/gateway/pyproject.toml",
}


def _fail(detail: str) -> None:
    pytest.fail(f"{_ACTIVE_ORACLE.get()}: {detail}", pytrace=False)


@contextmanager
def _contract_oracle(value: str) -> Iterator[None]:
    token = _ACTIVE_ORACLE.set(value)
    try:
        yield
    finally:
        _ACTIVE_ORACLE.reset(token)


def _require(condition: bool, detail: str) -> None:
    if not condition:
        _fail(detail)


def _tool_path() -> str:
    directories = {str(Path(sys.executable).resolve().parent), "/usr/bin", "/bin"}
    for tool in ("git", "uv"):
        resolved = shutil.which(tool)
        if resolved is None:
            _fail(f"required tool unavailable: {tool}")
        directories.add(str(Path(resolved).resolve().parent))
    return os.pathsep.join(sorted(directories))


def _install_side_effect_recorders(root: Path) -> Path:
    recorder_bin = root / "side-effect-recorders"
    recorder_bin.mkdir(parents=True)
    script = """#!/bin/sh
printf '%s\\n' "$0 $*" >> "$F151_CLEAN_WHEEL_SIDE_EFFECT_LOG"
exit 97
"""
    for command in ("octo", "octo-bench", "uvicorn"):
        path = recorder_bin / command
        path.write_text(script, encoding="utf-8")
        path.chmod(0o755)
    return recorder_bin


def _isolated_env(root: Path) -> dict[str, str]:
    home = root / "home"
    xdg = root / "xdg"
    tmp_dir = root / "tmp"
    state = root / "state"
    for path in (home, xdg, tmp_dir, state):
        path.mkdir(parents=True, exist_ok=True)
    (state / "side-effect-sentinel.txt").write_text("unchanged\n", encoding="utf-8")
    recorder_bin = _install_side_effect_recorders(root)
    return {
        "ALL_PROXY": "http://127.0.0.1:9",
        "HOME": str(home),
        "HTTPS_PROXY": "http://127.0.0.1:9",
        "HTTP_PROXY": "http://127.0.0.1:9",
        "F151_CLEAN_WHEEL_SIDE_EFFECT_LOG": str(state / "future-side-effects.log"),
        "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        "NO_PROXY": "127.0.0.1,localhost",
        "OCTOAGENT_DATA_DIR": str(state / "data"),
        "OCTOAGENT_INSTANCE_ROOT": str(state),
        "OCTOAGENT_LOG_DIR": str(state / "logs"),
        "OCTOAGENT_PROJECT_ROOT": str(state),
        "PATH": os.pathsep.join((str(recorder_bin), _tool_path())),
        "PIP_NO_INDEX": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": "",
        "TMPDIR": str(tmp_dir),
        "UV_OFFLINE": "1",
        "XDG_CACHE_HOME": str(xdg / "cache"),
        "XDG_CONFIG_HOME": str(xdg / "config"),
        "XDG_DATA_HOME": str(xdg / "data"),
    }


def _tree_fingerprint(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            result[relative] = f"symlink:{path.readlink()}"
        elif path.is_dir():
            result[relative] = "directory"
        elif path.is_file():
            result[relative] = f"file:{hashlib.sha256(path.read_bytes()).hexdigest()}"
        else:
            result[relative] = "other"
    return result


def _static_string_values(node: ast.AST) -> set[str]:
    try:
        value = ast.literal_eval(node)
    except (TypeError, ValueError):
        return set()
    if isinstance(value, str):
        return {value}
    if isinstance(value, (list, set, tuple)):
        return {item for item in value if isinstance(item, str)}
    return set()


def _parse_report(stdout: str) -> dict[str, Any]:
    payloads: list[dict[str, Any]] = []
    human_lines: list[str] = []
    for line in (item.strip() for item in stdout.splitlines() if item.strip()):
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError:
            human_lines.append(line)
            continue
        if isinstance(candidate, dict):
            payloads.append(candidate)
    _require(len(payloads) == 1, "stdout must contain exactly one JSON object line")
    _require(bool(human_lines), "human summary missing")
    return payloads[0]


def _invoke_clean_wheel(
    tmp_path: Path,
    *args: str,
) -> tuple[int, dict[str, Any]]:
    if not CHECKER.is_file():
        _fail(f"missing public checker: {CHECKER.relative_to(REPO_ROOT)}")
    root = tmp_path / "clean-wheel-contract"
    external_cwd = root / "external-cwd"
    external_cwd.mkdir(parents=True)
    env = _isolated_env(root)
    state = root / "state"
    state_before = _tree_fingerprint(state)
    command = [sys.executable, str(CHECKER), *args]
    completed = subprocess.run(
        command,
        cwd=external_cwd,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=600,
    )
    _require("Traceback" not in completed.stderr, "checker leaked a traceback")
    _require(completed.stderr == "", f"checker wrote stderr: {completed.stderr}")
    _require(_tree_fingerprint(state) == state_before, "outer isolated state changed")
    return completed.returncode, _parse_report(completed.stdout)


def _checks(report: dict[str, Any]) -> dict[str, Any]:
    checks = report.get("checks")
    _require(isinstance(checks, dict), "checks is not an object")
    return checks


def _assert_pass_report(
    exit_code: int,
    report: dict[str, Any],
    *,
    command: str,
    level: str | None,
    expected_checks: set[str],
) -> None:
    _require(exit_code == 0, f"{command} preliminary command exited {exit_code}")
    _require(report.get("command") == command, "reported command mismatch")
    _require(report.get("level") == level, "reported level mismatch")
    _require(report.get("status") == "PASS", "top-level status is not PASS")
    _require(report.get("error_code") is None, "PASS report contains an error code")
    _require(
        report.get("inventory_contract") == PRELIMINARY_INVENTORY_CONTRACT,
        "preliminary inventory contract mismatch",
    )
    _require(set(_checks(report)) == expected_checks, "preliminary check set mismatch")


def _assert_full_report(
    exit_code: int,
    report: dict[str, Any],
    *,
    command: str,
    level: str,
) -> None:
    _require(exit_code == 0, f"{command} full command exited {exit_code}")
    _require(report.get("command") == command, "full command mismatch")
    _require(report.get("level") == level, "full level mismatch")
    _require(report.get("status") == "PASS", "full command did not PASS")
    _require(report.get("error_code") is None, "full PASS contains error code")
    _require(
        report.get("inventory_contract") == "final-direct-closure-v1",
        "final inventory contract mismatch",
    )


def _facts(report: dict[str, Any], check_id: str) -> dict[str, Any]:
    check = _checks(report).get(check_id)
    _require(isinstance(check, dict), f"missing check {check_id}")
    _require(check.get("status") == "PASS", f"check {check_id} is not PASS")
    facts = check.get("facts")
    _require(isinstance(facts, dict), f"check {check_id} facts missing")
    return facts


def _assert_isolation(facts: dict[str, Any]) -> None:
    _require(set(facts) == CHILD_OBSERVATION_FIELDS, "child observation fields mismatch")
    environment = facts.get("environment")
    _require(isinstance(environment, dict), "child environment is not an object")
    _require(set(environment) == CHILD_ENVIRONMENT_FIELDS, "child environment fields mismatch")
    _require(environment.get("PYTHONNOUSERSITE") == "1", "child user-site guard missing")
    _require(facts.get("enable_user_site") is False, "child user-site remained enabled")
    _require(isinstance(facts.get("sys_path"), list), "child sys.path is not ordered list")
    _require(isinstance(facts.get("workspace_origins"), dict), "workspace origins missing")
    target = Path(str(environment.get("PYTHONPATH", ""))).resolve()
    _require(target.is_absolute(), "child PYTHONPATH is not an absolute target")
    project_purelib = Path(sysconfig.get_path("purelib")).resolve()
    sys_paths = [Path(value).resolve() for value in facts["sys_path"] if value]
    repo_paths = [path for path in sys_paths if path.is_relative_to(REPO_ROOT)]
    _require(
        all(path == project_purelib for path in repo_paths),
        "child sys.path contains a repo source or arbitrary repo-prefix path",
    )
    _require(project_purelib in sys_paths, "locked project purelib missing from child sys.path")
    _require(
        Path(str(facts.get("user_site"))).resolve() not in sys_paths,
        "child user-site leaked into sys.path",
    )
    _require(
        all(
            Path(value).resolve().is_relative_to(target)
            for value in facts["workspace_origins"].values()
        ),
        "workspace origin escaped target",
    )


def _canonical_dependency_set(values: object, label: str) -> set[str]:
    _require(isinstance(values, list), f"{label} is not a list")
    _require(bool(values), f"{label} is empty")
    result: set[str] = set()
    for value in values:
        _require(isinstance(value, str) and bool(value.strip()), f"{label} contains a non-string")
        try:
            name = canonicalize_name(Requirement(value).name)
        except InvalidRequirement:
            _fail(f"{label} contains an invalid requirement")
        _require(bool(name), f"{label} contains an empty canonical name")
        _require(name not in result, f"{label} contains duplicate canonical names")
        result.add(name)
    return result


def _dependency_set(facts: dict[str, Any], key: str) -> set[str]:
    return _canonical_dependency_set(facts.get(key), key)


def _manifest_dependencies(package: str) -> tuple[set[str], str, str, int]:
    path = MANIFEST_PATHS[package]
    content = path.read_bytes()
    sha256 = hashlib.sha256(content).hexdigest()
    try:
        document = tomllib.loads(content.decode("utf-8"))
    except tomllib.TOMLDecodeError:
        _fail(f"{package} manifest TOML is invalid")
    project = document.get("project")
    _require(isinstance(project, dict), f"{package} project table missing")
    dependencies = project.get("dependencies")
    names = _canonical_dependency_set(dependencies, f"{package} manifest dependencies")
    return names, sha256, path.relative_to(REPO_ROOT).as_posix(), len(names)


def _assert_requires_dist_inventory(facts: dict[str, Any], *, package: str) -> None:
    expected, sha256, manifest_path, expected_count = _manifest_dependencies(package)
    manifest = _dependency_set(facts, "manifest_requires_dist")
    wheel = _dependency_set(facts, "wheel_requires_dist")
    _require(manifest == expected, f"{package} reported manifest differs from source")
    _require(wheel == expected, f"{package} wheel METADATA differs from manifest")
    _require(facts.get("manifest_path") == manifest_path, "manifest path mismatch")
    _require(facts.get("manifest_sha256") == sha256, "manifest SHA mismatch")
    _require(facts.get("requires_dist_count") == expected_count, "dependency count mismatch")
    _require(
        facts.get("wheel_metadata_source") == "real-wheel-archive:METADATA", "wheel source mismatch"
    )
    occurrences = facts.get("import_occurrences")
    _require(isinstance(occurrences, list) and bool(occurrences), "import inventory missing")
    _assert_occurrence_shape(occurrences, package)
    unowned = [
        occurrence
        for occurrence in occurrences
        if isinstance(occurrence, dict) and occurrence.get("ownership_state") == "unowned"
    ]
    _require(
        facts.get("unowned_import_occurrences") == unowned,
        "unowned occurrence projection mismatch",
    )
    _require(
        facts.get("unowned_import_occurrence_count") == len(unowned),
        "unowned occurrence count mismatch",
    )
    runtime = _runtime_distributions(occurrences)
    _require(
        facts.get("manifest_not_runtime_observed") == sorted(expected - runtime),
        "manifest/runtime delta mismatch",
    )
    _require(
        facts.get("runtime_observed_not_manifest") == sorted(runtime - expected),
        "runtime/manifest delta mismatch",
    )
    _require(facts.get("final_verdict") is None, "preliminary inventory claimed final verdict")
    _require(facts.get("final_owner") == "T070", "final dependency owner mismatch")


def _assert_occurrence_shape(occurrences: list[object], package: str) -> None:
    for occurrence in occurrences:
        _require(isinstance(occurrence, dict), f"{package} import occurrence is not an object")
        _require(
            set(occurrence) == PRELIMINARY_OCCURRENCE_FIELDS,
            "import occurrence fields mismatch",
        )
        _require(occurrence.get("context") in IMPORT_CONTEXTS, "import context invalid")
        _require(isinstance(occurrence.get("line"), int), "import line missing")
        state = occurrence.get("ownership_state")
        _require(state in {"resolved", "unowned"}, "import ownership state invalid")
        if state == "resolved":
            _require(bool(occurrence.get("resolved_distribution")), "resolved owner missing")
        else:
            _require(occurrence.get("resolved_distribution") is None, "unowned import faked owner")
            _require(
                occurrence.get("workspace_owner") is None,
                "unowned workspace owner is not null",
            )


def _runtime_distributions(occurrences: list[object]) -> set[str]:
    return {
        canonicalize_name(str(item["resolved_distribution"]))
        for item in occurrences
        if isinstance(item, dict)
        and item.get("context") == "runtime-required"
        and item.get("ownership_state") == "resolved"
    }


def _preliminary_unowned_fixture(root: Path) -> Path:
    target = root / "site-packages"
    _write_installed_distribution(
        target,
        "preliminary-demo",
        "preliminary_demo",
        {"preliminary_demo/__init__.py": "import literal_unowned_dependency\n"},
    )
    return target


def _preliminary_unowned_row() -> dict[str, Any]:
    return {
        "distribution": "preliminary-demo",
        "source_file": "preliminary_demo/__init__.py",
        "line": 1,
        "syntax": "import",
        "import_root": "literal_unowned_dependency",
        "resolved_distribution": None,
        "context": "runtime-required",
        "workspace_owner": None,
        "ownership_state": "unowned",
    }


def _assert_preliminary_unowned_controls(tmp_path: Path) -> None:
    module = _load_contract_checker(ORACLE)
    classifier = _contract_callable(module, "classify_distribution_imports", ORACLE)
    builder = _contract_callable(module, "build_dependency_inventory", ORACLE)
    target = _preliminary_unowned_fixture(tmp_path / "unowned-positive")
    try:
        occurrences = classifier(target, "preliminary-demo")
    except Exception as exc:
        _fail(f"literal unowned classification rejected: {type(exc).__name__}")
    expected = [_preliminary_unowned_row()]
    _require(occurrences == expected, "literal unowned occurrence omitted or changed")
    inventory = builder(["declared-dist"], ["declared-dist"], occurrences)
    _require(inventory.get("unowned_import_occurrences") == expected, "unowned projection missing")
    _require(inventory.get("unowned_import_occurrence_count") == 1, "unowned count missing")
    _assert_preliminary_unowned_rejects(builder, expected)


def _assert_preliminary_unowned_rejects(builder: Any, expected: list[dict[str, Any]]) -> None:
    mutations = []
    fake_resolved = deepcopy(expected)
    fake_resolved[0]["ownership_state"] = "resolved"
    mutations.append(fake_resolved)
    fake_owner = deepcopy(expected)
    fake_owner[0]["workspace_owner"] = "octoagent-core"
    mutations.append(fake_owner)
    for mutation in mutations:
        _require(mutation != expected, "unowned reject fixture has no observable delta")
        try:
            builder(["declared-dist"], ["declared-dist"], mutation)
        except Exception:
            continue
        _fail("invalid unowned ownership state accepted")


def _assert_provider_checks(report: dict[str, Any]) -> None:
    imported = _facts(report, "provider.import")
    _require(imported.get("provider_imported") is True, "Provider import failed")
    _require(imported.get("gateway_find_spec") is None, "Gateway remained discoverable")
    _require(imported.get("gateway_in_sys_modules") is False, "Gateway entered sys.modules")
    origin = str(imported.get("provider_origin", ""))
    _require("site-packages" in origin, "Provider did not import from installed wheel")
    _require(str(REPO_ROOT) not in origin, "Provider imported from source checkout")
    _assert_requires_dist_inventory(_facts(report, "provider.requires_dist"), package="provider")


def _assert_gateway_preliminary_checks(report: dict[str, Any]) -> None:
    imported = _facts(report, "gateway.import")
    _require(imported.get("gateway_imported") is True, "Gateway import failed")
    origin = str(imported.get("gateway_origin", ""))
    _require("site-packages" in origin, "Gateway did not import from installed wheel")
    _require(str(REPO_ROOT) not in origin, "Gateway imported from source checkout")
    _assert_requires_dist_inventory(_facts(report, "gateway.requires_dist"), package="gateway")
    help_facts = _facts(report, "gateway.cli-help")
    commands = help_facts.get("commands")
    _require(isinstance(commands, dict), "supported help results are not an object")
    _require(set(commands) == SUPPORTED_HELP, "supported help set mismatch")
    _require(set(commands.values()) == {0}, "supported help did not all exit zero")
    namespace = _facts(report, "gateway.namespace")
    _require(namespace.get("scan_mode") == "inventory-only", "namespace scan claimed final gate")
    _require(namespace.get("final_verdict") is None, "preliminary scan claimed final verdict")
    _require(namespace.get("scan_complete") is True, "namespace inventory scan incomplete")
    _require(isinstance(namespace.get("findings"), list), "namespace findings missing")


def _assert_relocation_boundary(report: dict[str, Any]) -> None:
    boundary = _facts(report, "gateway.level-boundary")
    _require(boundary.get("level") == "relocation", "executed level mismatch")
    _require(boundary.get("full_checks_executed") == [], "relocation executed full checks")
    _require(
        set(_checks(report)).isdisjoint(FUTURE_CHECK_IDS), "future check leaked into relocation"
    )


def _assert_gateway_runtime_checks(report: dict[str, Any]) -> None:
    startup = _facts(report, "gateway.startup")
    _require(startup.get("module_entry") == "octoagent.gateway.__main__", "entry mismatch")
    _require(startup.get("app_instance") is True, "Uvicorn did not receive app instance")
    _require(startup.get("external_cwd") is True, "Gateway did not start outside repo")
    _require(startup.get("requested_host") == startup.get("observed_host"), "host drift")
    _require(startup.get("requested_port") == startup.get("observed_port"), "port drift")
    readiness = _facts(report, "gateway.readiness")
    _require(readiness.get("status_code") == 200, "readiness HTTP status mismatch")
    _require(readiness.get("structural_ready") is True, "structural readiness failed")
    _require(readiness.get("echo_only") is False, "readiness is only an Echo probe")
    for key in ("dns_calls", "model_calls", "provider_http_calls"):
        _require(readiness.get(key) == 0, f"readiness performed {key}")
    sigterm = _facts(report, "gateway.sigterm")
    _require(sigterm.get("signal") == "SIGTERM", "shutdown signal mismatch")
    _require(sigterm.get("clean_exit") is True, "Gateway did not exit cleanly")
    _require(sigterm.get("orphan_processes") == 0, "Gateway left an orphan process")


def _assert_invalid_startup_checks(report: dict[str, Any]) -> None:
    facts = _facts(report, "gateway.invalid-startup")
    cases = facts.get("cases")
    _require(isinstance(cases, list) and len(cases) == 2, "startup cases mismatch")
    expected = {
        ("runtime", "GATEWAY_RUNTIME_CONFIG_INVALID", 78),
        ("security", "GATEWAY_SECURITY_CONFIG_INVALID", 78),
    }
    actual = {
        (case.get("case"), case.get("error_code"), case.get("exit_code"))
        for case in cases
        if isinstance(case, dict)
    }
    _require(actual == expected, "typed invalid-startup outcomes mismatch")
    for key in ("uvicorn_calls", "task_writes", "work_writes", "event_writes"):
        _require(facts.get(key) == 0, f"invalid startup performed {key}")


def _assert_source_managed_checks(report: dict[str, Any]) -> None:
    guarded = _facts(report, "source-managed.guard")
    outcomes = guarded.get("commands")
    _require(isinstance(outcomes, dict), "source-managed command outcomes missing")
    _require(set(outcomes) == SOURCE_MANAGED_COMMANDS, "source-managed command set mismatch")
    for outcome in outcomes.values():
        _require(isinstance(outcome, dict), "source-managed outcome is not an object")
        _require(outcome.get("exit_code") == 69, "source-managed exit mismatch")
        _require(outcome.get("error_code") == "SOURCE_CHECKOUT_REQUIRED", "guard code mismatch")
        _require(outcome.get("side_effects") == [], "source-managed command caused side effects")
    read_only = _facts(report, "source-managed.read-only").get("commands")
    _require(isinstance(read_only, dict), "read-only command outcomes missing")
    _require(set(read_only) == SOURCE_READ_ONLY_COMMANDS, "read-only command set mismatch")
    _require(set(read_only.values()) == {0}, "read-only command failed")
    _require(guarded.get("sentinel_unchanged") is True, "guard sentinel changed")


def _assert_final_dependency_closure(report: dict[str, Any]) -> None:
    facts = _facts(report, "dependency.final-closure")
    provider = _canonical_dependency_set(facts.get("provider_requires_dist"), "provider final")
    gateway = _canonical_dependency_set(facts.get("gateway_requires_dist"), "gateway final")
    _require(provider == PROVIDER_FINAL_REQUIRES_DIST, "Provider final 1+6 closure mismatch")
    _require(gateway == GATEWAY_FINAL_REQUIRES_DIST, "Gateway final 7+25 closure mismatch")
    _require(facts.get("provider_workspace_count") == 1, "Provider workspace count mismatch")
    _require(facts.get("provider_third_party_count") == 6, "Provider third-party count mismatch")
    _require(facts.get("gateway_workspace_count") == 7, "Gateway workspace count mismatch")
    _require(facts.get("gateway_third_party_count") == 25, "Gateway third-party count mismatch")
    for key in ("unknown", "unowned", "missing", "unexpected"):
        _require(facts.get(key) == [], f"final dependency {key} set is not empty")
    _require(facts.get("final_verdict") == "PASS", "final dependency verdict missing")
    _require(facts.get("final_owner") == "T070", "final dependency owner mismatch")


def _load_contract_checker(oracle: str) -> Any:
    module_name = f"f151_clean_wheel_{hashlib.sha256(oracle.encode()).hexdigest()[:12]}"
    spec = importlib.util.spec_from_file_location(module_name, CHECKER)
    if spec is None or spec.loader is None:
        pytest.fail(f"{oracle}: checker import spec missing", pytrace=False)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        pytest.fail(f"{oracle}: checker import failed: {type(exc).__name__}", pytrace=False)
    finally:
        sys.modules.pop(module_name, None)
    return module


def _contract_callable(module: Any, name: str, oracle: str) -> Any:
    value = getattr(module, name, None)
    if not callable(value):
        pytest.fail(f"{oracle}: missing public seam {name}", pytrace=False)
    return value


def _write_installed_distribution(
    target: Path,
    name: str,
    root: str,
    files: dict[str, str],
    *,
    entry_points: str | None = None,
) -> None:
    owned: list[str] = []
    for relative, content in files.items():
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        owned.append(relative)
    info = target / f"{name.replace('-', '_')}-1.0.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(
        f"Metadata-Version: 2.4\nName: {name}\nVersion: 1.0\n",
        encoding="utf-8",
    )
    owned.append(f"{info.name}/METADATA")
    if entry_points is not None:
        (info / "entry_points.txt").write_text(entry_points, encoding="utf-8")
        owned.append(f"{info.name}/entry_points.txt")
    records = "".join(f"{relative},,\n" for relative in [*owned, f"{info.name}/RECORD"])
    (info / "RECORD").write_text(records, encoding="utf-8")


def _classification_fixture(root: Path) -> Path:
    target = root / "site-packages"
    _write_installed_distribution(
        target,
        "demo-dist",
        "demo_pkg",
        {
            "demo_pkg/__init__.py": "",
            "demo_pkg/runtime.py": CLASSIFICATION_RUNTIME_SOURCE,
            "demo_pkg/pytest_plugin.py": "import pytest\n",
        },
        entry_points="[pytest11]\ndemo = demo_pkg.pytest_plugin\n",
    )
    _write_dependency_distributions(target)
    _write_installed_distribution(
        target,
        "contaminant-dist",
        "contaminant",
        {"contaminant/__init__.py": "import forbidden_dependency\n"},
    )
    return target


def _write_dependency_distributions(target: Path) -> None:
    dependencies = {
        "requests": "requests",
        "fixture-yaml-dist": "yaml",
        "fixture-dotenv-dist": "dotenv",
        "pydantic": "pydantic",
        "lancedb": "lancedb",
        "pytest": "pytest",
        "httpx": "httpx",
        "octoagent-core": "octoagent/core",
        "unused-dist": "unused_dist",
    }
    for name, root in dependencies.items():
        relative = f"{root}/__init__.py"
        _write_installed_distribution(target, name, root, {relative: ""})


def _fixture_import_line(syntax: str, root: str) -> int:
    tree = ast.parse(CLASSIFICATION_RUNTIME_SOURCE)
    for node in ast.walk(tree):
        if (
            syntax == "import"
            and isinstance(node, ast.Import)
            and any(alias.name.split(".", 1)[0] == root for alias in node.names)
        ):
            return node.lineno
        if (
            syntax == "from"
            and isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.split(".", 1)[0] == root
        ):
            return node.lineno
        if (
            syntax == "dynamic"
            and isinstance(node, ast.Call)
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == root
        ):
            return node.lineno
    pytest.fail(f"{IMPORT_CLASSIFICATION_ORACLE}: expected fixture import missing", pytrace=False)


def _expected_classification_occurrences() -> list[dict[str, Any]]:
    facts = (
        ("runtime.py", "import", "requests", "requests", "runtime-required", None),
        (
            "runtime.py",
            "import",
            "unowned_third_party",
            None,
            "runtime-required",
            None,
        ),
        ("runtime.py", "from", "yaml", "fixture-yaml-dist", "runtime-required", None),
        ("runtime.py", "dynamic", "dotenv", "fixture-dotenv-dist", "runtime-required", None),
        ("runtime.py", "import", "pydantic", "pydantic", "type-checking", None),
        ("runtime.py", "import", "lancedb", "lancedb", "optional-lazy", None),
        ("runtime.py", "from", "octoagent", "octoagent-core", "runtime-required", "octoagent-core"),
        ("runtime.py", "import", "httpx", "httpx", "runtime-required", None),
        ("pytest_plugin.py", "import", "pytest", "pytest", "test-plugin", None),
    )
    rows = [
        {
            "distribution": "demo-dist",
            "source_file": f"demo_pkg/{source}",
            "line": 1 if source == "pytest_plugin.py" else _fixture_import_line(syntax, root),
            "syntax": syntax,
            "import_root": root,
            "resolved_distribution": distribution,
            "context": context,
            "workspace_owner": owner,
            "ownership_state": "resolved" if distribution is not None else "unowned",
        }
        for source, syntax, root, distribution, context, owner in facts
    ]
    return sorted(rows, key=lambda item: (item["source_file"], item["line"], item["import_root"]))


def _assert_classification_occurrences(occurrences: object) -> list[dict[str, Any]]:
    if not isinstance(occurrences, list):
        pytest.fail(f"{IMPORT_CLASSIFICATION_ORACLE}: occurrence list missing", pytrace=False)
    typed = [item for item in occurrences if isinstance(item, dict)]
    if len(typed) != len(occurrences):
        pytest.fail(f"{IMPORT_CLASSIFICATION_ORACLE}: non-object occurrence", pytrace=False)
    if any(set(item) != IMPORT_OCCURRENCE_FIELDS for item in typed):
        pytest.fail(f"{IMPORT_CLASSIFICATION_ORACLE}: occurrence fields mismatch", pytrace=False)
    if typed != _expected_classification_occurrences():
        pytest.fail(f"{IMPORT_CLASSIFICATION_ORACLE}: occurrence exact set mismatch", pytrace=False)
    _assert_workspace_ownership_controls(typed)
    return typed


def _classification_accept_control(
    classifier: Any, target: Path, reason: str
) -> list[dict[str, Any]]:
    try:
        occurrences = classifier(target, "demo-dist")
    except Exception as exc:
        pytest.fail(
            f"{IMPORT_CLASSIFICATION_ORACLE}: {reason}: {type(exc).__name__}",
            pytrace=False,
        )
    return _assert_classification_occurrences(occurrences)


def _assert_workspace_ownership_controls(occurrences: list[dict[str, Any]]) -> None:
    workspace = [item for item in occurrences if item.get("import_root") == "octoagent"]
    if len(workspace) != 1 or workspace[0].get("workspace_owner") != "octoagent-core":
        pytest.fail(f"{IMPORT_CLASSIFICATION_ORACLE}: workspace owner missing", pytrace=False)
    third_party = [item for item in occurrences if item.get("import_root") != "octoagent"]
    if any(item.get("workspace_owner") is not None for item in third_party):
        pytest.fail(f"{IMPORT_CLASSIFICATION_ORACLE}: third-party owner is not null", pytrace=False)


def _workspace_control_accepts(classifier: Any, target: Path) -> None:
    occurrences = _classification_accept_control(classifier, target, "workspace control rejected")
    workspace = [item for item in occurrences if item.get("import_root") == "octoagent"]
    if workspace[0].get("context") != "runtime-required":
        pytest.fail(f"{IMPORT_CLASSIFICATION_ORACLE}: workspace context changed", pytrace=False)


def _workspace_owner_controls(classifier: Any, validator: Any, root: Path, case: str) -> None:
    target = _classification_fixture(root)
    _workspace_control_accepts(classifier, target)
    if case == "unowned":
        record = target / "octoagent_core-1.0.dist-info" / "RECORD"
        before = record.read_bytes()
        text = before.decode().replace("octoagent/core/__init__.py,,\n", "")
        record.write_text(text, encoding="utf-8")
        after = record.read_bytes()
    else:
        marker = target / "octoagent_gateway-1.0.dist-info" / "RECORD"
        if marker.exists():
            pytest.fail(
                f"{IMPORT_CLASSIFICATION_ORACLE}: ambiguous fixture preexists", pytrace=False
            )
        before = b""
        _write_installed_distribution(
            target,
            "octoagent-gateway",
            "octoagent/core",
            {"octoagent/core/__init__.py": ""},
        )
        after = marker.read_bytes()
    if before == after:
        pytest.fail(f"{IMPORT_CLASSIFICATION_ORACLE}: owner mutation is a no-op", pytrace=False)
    if case == "unowned":
        observed = classifier(target, "demo-dist")
        workspace = [item for item in observed if item.get("import_root") == "octoagent"]
        if len(workspace) != 1 or workspace[0].get("ownership_state") != "unowned":
            pytest.fail(f"{IMPORT_CLASSIFICATION_ORACLE}: workspace unowned omitted", pytrace=False)
        fake = deepcopy(observed)
        row = next(item for item in fake if item.get("import_root") == "octoagent")
        row.update(
            ownership_state="resolved",
            resolved_distribution="octoagent-core",
            workspace_owner="octoagent-core",
        )
        try:
            validator(target, "demo-dist", fake)
        except Exception:
            return
        pytest.fail(f"{IMPORT_CLASSIFICATION_ORACLE}: fake workspace owner accepted", pytrace=False)
    try:
        classifier(target, "demo-dist")
    except Exception:
        return
    pytest.fail(
        f"{IMPORT_CLASSIFICATION_ORACLE}: ambiguous workspace owner accepted",
        pytrace=False,
    )


def _assert_workspace_owner_controls(classifier: Any, validator: Any, root: Path) -> None:
    _workspace_owner_controls(classifier, validator, root / "unowned", "unowned")
    _workspace_owner_controls(classifier, validator, root / "ambiguous", "ambiguous")


def _assert_target_owner_ignores_host_shadow(
    module: Any,
    classifier: Any,
    target: Path,
    baseline: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hostile = {
        "yaml": ["host-shadow-yaml"],
        "dotenv": ["host-shadow-dotenv"],
        "requests": ["host-shadow-requests"],
    }
    with monkeypatch.context() as context:
        context.setattr(module.importlib.metadata, "packages_distributions", lambda: hostile)
        observed = _classification_accept_control(
            classifier, target, "target-owned metadata rejected under host shadow"
        )
    if observed != baseline:
        pytest.fail(f"{IMPORT_CLASSIFICATION_ORACLE}: host owner shadow accepted", pytrace=False)


def _assert_preliminary_inventory(builder: Any, occurrences: list[dict[str, Any]]) -> None:
    manifest = [
        "requests",
        "fixture-yaml-dist",
        "fixture-dotenv-dist",
        "lancedb",
        "octoagent-core",
        "unused-dist",
    ]
    inventory = builder(manifest, list(manifest), occurrences)
    expected = {
        "manifest_not_runtime_observed": ["lancedb", "unused-dist"],
        "runtime_observed_not_manifest": ["httpx"],
        "final_verdict": None,
        "final_owner": "T070",
        "unowned_import_occurrences": [
            item for item in occurrences if item.get("ownership_state") == "unowned"
        ],
        "unowned_import_occurrence_count": 1,
    }
    if not isinstance(inventory, dict) or any(inventory.get(k) != v for k, v in expected.items()):
        pytest.fail(f"{IMPORT_CLASSIFICATION_ORACLE}: preliminary delta hidden", pytrace=False)


def _assert_classification_rejects_unknown(classifier: Any, root: Path) -> None:
    target = _classification_fixture(root)
    path = target / "demo_pkg/runtime.py"
    path.write_text(
        path.read_text(encoding="utf-8")
        + "\ndef load_unknown(name: str) -> object:\n    return __import__(name)\n",
        encoding="utf-8",
    )
    try:
        classifier(target, "demo-dist")
    except Exception:
        return
    pytest.fail(
        f"{IMPORT_CLASSIFICATION_ORACLE}: unresolved dynamic import accepted", pytrace=False
    )


def _probe_fixture(root: Path) -> tuple[Path, Path, Path]:
    target = root / "site-packages"
    external = root / "external-cwd"
    external.mkdir(parents=True)
    _write_installed_distribution(target, "demo-dist", "demo_pkg", {"demo_pkg/__init__.py": ""})
    return target, root, external


def _assert_child_observation(observation: object, target: Path, external: Path) -> dict[str, Any]:
    if not isinstance(observation, dict) or set(observation) != CHILD_OBSERVATION_FIELDS:
        pytest.fail(f"{CHILD_OBSERVATION_ORACLE}: observed fields mismatch", pytrace=False)
    environment = observation.get("environment")
    if not isinstance(environment, dict) or set(environment) != CHILD_ENVIRONMENT_FIELDS:
        pytest.fail(f"{CHILD_OBSERVATION_ORACLE}: observed environment mismatch", pytrace=False)
    if Path(str(observation.get("cwd"))).resolve() != external.resolve():
        pytest.fail(f"{CHILD_OBSERVATION_ORACLE}: child cwd not observed", pytrace=False)
    if Path(environment["PYTHONPATH"]).resolve() != target.resolve():
        pytest.fail(f"{CHILD_OBSERVATION_ORACLE}: child PYTHONPATH mismatch", pytrace=False)
    return observation


def _child_observation_reject_cases(
    observation: dict[str, Any], repo_root: Path
) -> tuple[dict[str, Any], ...]:
    missing = deepcopy(observation)
    missing.pop("prefix")
    extra = deepcopy(observation)
    extra["parent_expected"] = True
    repo_path = deepcopy(observation)
    repo_path["sys_path"] = [*repo_path["sys_path"], str(repo_root)]
    host_home = deepcopy(observation)
    host_home["environment"]["HOME"] = str(Path.home())
    ambient = deepcopy(observation)
    ambient["environment"]["PYTHONPATH"] = str(repo_root)
    user_site = deepcopy(observation)
    user_site["enable_user_site"] = True
    source = deepcopy(observation)
    source["workspace_origins"]["demo-dist"] = str(repo_root / "demo_pkg/__init__.py")
    return missing, extra, repo_path, host_home, ambient, user_site, source


def _assert_child_reject_controls(
    validator: Any, observation: dict[str, Any], paths: tuple[Path, Path, Path]
) -> None:
    target, transaction_root, external = paths
    validator(observation, REPO_ROOT, transaction_root, target, external, {"demo-dist"})
    for mutation in _child_observation_reject_cases(observation, REPO_ROOT):
        try:
            validator(mutation, REPO_ROOT, transaction_root, target, external, {"demo-dist"})
        except Exception:
            continue
        pytest.fail(
            f"{CHILD_OBSERVATION_ORACLE}: invalid child observation accepted", pytrace=False
        )


class _StdoutPolluter:
    def __init__(self, run: Any) -> None:
        self._run = run

    def __call__(self, *args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        completed = self._run(*args, **kwargs)
        return subprocess.CompletedProcess(
            completed.args,
            completed.returncode,
            stdout=completed.stdout + "unexpected-child-output\n",
            stderr=completed.stderr,
        )


class _JsonFactTamper:
    def __init__(self, run: Any) -> None:
        self._run = run

    def __call__(self, *args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        completed = self._run(*args, **kwargs)
        payload = json.loads(completed.stdout)
        payload["cwd"] = str(REPO_ROOT)
        return subprocess.CompletedProcess(
            completed.args,
            completed.returncode,
            stdout=json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
            stderr=completed.stderr,
        )


def _assert_probe_rejects_stdout_pollution(
    module: Any,
    runner: Any,
    monkeypatch: pytest.MonkeyPatch,
    paths: tuple[Path, Path, Path],
) -> None:
    target, transaction_root, external = paths
    polluter = _StdoutPolluter(module.subprocess.run)
    with monkeypatch.context() as context:
        context.setattr(module.subprocess, "run", polluter)
        try:
            runner(target, transaction_root, external, {"demo-dist": "demo_pkg"})
        except Exception:
            return
    pytest.fail(f"{CHILD_OBSERVATION_ORACLE}: child stdout pollution accepted", pytrace=False)


def _assert_probe_consumes_child_json(
    module: Any,
    runner: Any,
    validator: Any,
    monkeypatch: pytest.MonkeyPatch,
    paths: tuple[Path, Path, Path],
) -> None:
    target, transaction_root, external = paths
    with monkeypatch.context() as context:
        context.setattr(module.subprocess, "run", _JsonFactTamper(module.subprocess.run))
        observed = runner(target, transaction_root, external, {"demo-dist": "demo_pkg"})
    if observed.get("cwd") != str(REPO_ROOT):
        pytest.fail(f"{CHILD_OBSERVATION_ORACLE}: runner reconstructed child facts", pytrace=False)
    try:
        validator(observed, REPO_ROOT, transaction_root, target, external, {"demo-dist"})
    except Exception:
        return
    pytest.fail(f"{CHILD_OBSERVATION_ORACLE}: tampered child JSON accepted", pytrace=False)


def _standard_backend_checker_source() -> str:
    return """from pathlib import Path
import hatchling.build
import os
import subprocess
import zipfile

def build_workspace_wheels(project: Path, wheel_dir: Path) -> str:
    return hatchling.build.build_wheel(str(wheel_dir), {"root": str(project)})

def read_wheel_metadata(archive: zipfile.ZipFile, metadata_name: str) -> bytes:
    return archive.read(metadata_name)

def install_workspace_wheels(transaction_root: Path, target: Path, wheels: list[Path]) -> None:
    env = {
        "HOME": str(transaction_root / "home"),
        "XDG_CACHE_HOME": str(transaction_root / "xdg-cache"),
        "TMPDIR": str(transaction_root / "tmp"),
        "UV_CACHE_DIR": str(transaction_root / "uv-cache"),
        "PYTHONPATH": "",
    }
    subprocess.run(
        ["uv", "pip", "install", "--offline", "--no-deps", "--target", str(target),
         *(str(wheel) for wheel in wheels)], env=env, check=True
    )

def validate_workspace_origins(origins: list[Path], target: Path) -> bool:
    origin_policy = "target-only"
    return (
        origin_policy == "target-only"
        and all(origin.is_relative_to(target) for origin in origins)
    )
"""


def _standard_backend_root_pyproject() -> str:
    members = ",\n    ".join(
        json.dumps(path.rsplit("/pyproject.toml", 1)[0].removeprefix("octoagent/"))
        for path in STANDARD_BACKEND_WORKSPACES.values()
    )
    return (
        '[project]\nname = "octoagent"\nversion = "0.1.0"\ndependencies = []\n\n'
        '[dependency-groups]\ndev = ["hatchling==1.29.0"]\n\n'
        f"[tool.uv.workspace]\nmembers = [\n    {members},\n]\n"
    )


def _standard_backend_lock() -> str:
    packages = (
        ("hatchling", "1.29.0"),
        ("packaging", "25.0"),
        ("pathspec", "0.12.1"),
        ("pluggy", "1.6.0"),
        ("trove-classifiers", "2026.1.14.14"),
    )
    package_text = "\n".join(
        f'[[package]]\nname = "{name}"\nversion = "{version}"\n'
        'source = { registry = "https://pypi.org/simple" }\n'
        for name, version in packages
    )
    workspace_names = ["octoagent", *STANDARD_BACKEND_WORKSPACES]
    manifest_members = ", ".join(json.dumps(name) for name in workspace_names)
    workspace_packages = "\n".join(
        _standard_lock_workspace_package(name) for name in STANDARD_BACKEND_WORKSPACES
    )
    return (
        'version = 1\nrevision = 3\nrequires-python = ">=3.12"\n\n'
        f"[manifest]\nmembers = [{manifest_members}]\n\n"
        '[[package]]\nname = "octoagent"\nversion = "0.1.0"\nsource = { virtual = "." }\n'
        '[package.dev-dependencies]\ndev = [{ name = "hatchling" }]\n'
        "[package.metadata]\nrequires-dev = { dev = ["
        '{ name = "hatchling", specifier = "==1.29.0" }] }\n\n'
        '[[package]]\nname = "hatchling"\nversion = "1.29.0"\n'
        'source = { registry = "https://pypi.org/simple" }\n'
        'dependencies = [{ name = "packaging" }, { name = "pathspec" }, '
        '{ name = "pluggy" }, { name = "trove-classifiers" }]\n\n'
        + package_text.removeprefix(
            '[[package]]\nname = "hatchling"\nversion = "1.29.0"\n'
            'source = { registry = "https://pypi.org/simple" }\n'
        )
        + "\n"
        + workspace_packages
    )


def _standard_lock_workspace_package(name: str) -> str:
    path = STANDARD_BACKEND_WORKSPACES[name]
    editable = path.removesuffix("/pyproject.toml").removeprefix("octoagent/")
    return (
        f'[[package]]\nname = "{name}"\nversion = "0.1.0"\nsource = {{ editable = "{editable}" }}\n'
    )


def _standard_workspace_manifest(name: str) -> str:
    return (
        '[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n\n'
        f'[project]\nname = "{name}"\nversion = "0.1.0"\ndependencies = []\n'
    )


def _standard_backend_fixture(root: Path) -> Path:
    repo = root / "repo"
    (repo / "octoagent").mkdir(parents=True)
    (repo / "octoagent/pyproject.toml").write_text(
        _standard_backend_root_pyproject(), encoding="utf-8"
    )
    (repo / "octoagent/uv.lock").write_text(_standard_backend_lock(), encoding="utf-8")
    checker = repo / "repo-scripts/check-clean-wheel.py"
    checker.parent.mkdir(parents=True)
    checker.write_text(_standard_backend_checker_source(), encoding="utf-8")
    for name, relative in STANDARD_BACKEND_WORKSPACES.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_standard_workspace_manifest(name), encoding="utf-8")
    return repo


def _standard_backend_byte_map(repo: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(repo.rglob("*")):
        if path.is_file():
            result[path.relative_to(repo).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return result


def _standard_backend_validator() -> Any:
    if not CHECKER.is_file():
        pytest.fail(
            f"{STANDARD_BACKEND_ORACLE}: missing public checker seam",
            pytrace=False,
        )
    module_name = "f151_clean_wheel_standard_backend_checker"
    spec = importlib.util.spec_from_file_location(module_name, CHECKER)
    if spec is None or spec.loader is None:
        pytest.fail(f"{STANDARD_BACKEND_ORACLE}: checker import spec missing", pytrace=False)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(module_name, None)
    validator = getattr(module, "validate_standard_backend_scaffold", None)
    if not callable(validator):
        pytest.fail(f"{STANDARD_BACKEND_ORACLE}: typed pure seam missing", pytrace=False)
    return validator


def _standard_backend_report_issues(report: object) -> list[str]:
    if not isinstance(report, dict):
        return ["validator did not return a typed report object"]
    facts = report.get("facts")
    if report.get("status") != "PASS" or not isinstance(facts, dict):
        return ["valid scaffold did not return PASS facts"]
    expected_backends = {name: "hatchling.build" for name in STANDARD_BACKEND_WORKSPACES}
    checks = {
        "dev_requirement": "hatchling==1.29.0",
        "lock_requirement": "hatchling==1.29.0",
        "workspace_build_backends": expected_backends,
        "build_callable": "hatchling.build.build_wheel",
        "wheel_metadata_source": "real-wheel-archive:METADATA",
        "installer_argv_prefix": ["uv", "pip", "install", "--offline", "--no-deps", "--target"],
        "workspace_origin_policy": "target-only",
        "host_state_reads": [],
        "manual_builder_patterns": [],
    }
    return [f"fact {key} mismatch" for key, value in checks.items() if facts.get(key) != value]


def _standard_backend_accepts(validator: Any, repo: Path) -> bool:
    return not _standard_backend_report_issues(validator(repo))


def _standard_replace(path: Path, old: str, new: str, *, count: int = 1) -> None:
    original = path.read_text(encoding="utf-8")
    actual = original.count(old)
    assert actual == count, f"standard backend fixture expected {count} matches, got {actual}"
    changed = original.replace(old, new)
    assert changed != original, "standard backend fixture mutation changed no bytes"
    path.write_text(changed, encoding="utf-8")


def _standard_pin_missing(repo: Path) -> None:
    _standard_replace(repo / "octoagent/pyproject.toml", 'dev = ["hatchling==1.29.0"]', "dev = []")


def _standard_pin_drift(repo: Path) -> None:
    _standard_replace(repo / "octoagent/pyproject.toml", "hatchling==1.29.0", "hatchling==1.28.0")


def _standard_runtime_leak(repo: Path) -> None:
    _standard_replace(
        repo / "octoagent/pyproject.toml",
        "dependencies = []",
        'dependencies = ["hatchling==1.29.0"]',
    )


def _standard_lock_missing(repo: Path) -> None:
    path = repo / "octoagent/uv.lock"
    text_value = path.read_text(encoding="utf-8")
    start = text_value.index('[[package]]\nname = "hatchling"')
    end = text_value.index('[[package]]\nname = "packaging"', start)
    changed = text_value[:start] + text_value[end:]
    assert changed != text_value
    path.write_text(changed, encoding="utf-8")


def _standard_lock_drift(repo: Path) -> None:
    _standard_replace(repo / "octoagent/uv.lock", 'version = "1.29.0"', 'version = "1.28.0"')


def _standard_lock_closure_missing(repo: Path) -> None:
    _standard_replace(repo / "octoagent/uv.lock", ', { name = "pathspec" }', "")


def _standard_backend_mismatch(repo: Path) -> None:
    path = repo / STANDARD_BACKEND_WORKSPACES["octoagent-provider"]
    _standard_replace(
        path,
        'build-backend = "hatchling.build"',
        'build-backend = "setuptools.build_meta"',
    )


def _standard_manual_wheel(repo: Path) -> None:
    _standard_replace(
        repo / "repo-scripts/check-clean-wheel.py",
        "hatchling.build.build_wheel",
        "zipfile.ZipFile",
    )


def _standard_manual_metadata(repo: Path) -> None:
    _standard_replace(
        repo / "repo-scripts/check-clean-wheel.py",
        "archive.read(metadata_name)",
        'Path(metadata_name).write_text("METADATA")',
    )


def _standard_manual_record(repo: Path) -> None:
    path = repo / "repo-scripts/check-clean-wheel.py"
    path.write_text(
        path.read_text(encoding="utf-8") + '\nMANUAL_RECORD = "RECORD"\n',
        encoding="utf-8",
    )


def _standard_source_copy(repo: Path) -> None:
    path = repo / "repo-scripts/check-clean-wheel.py"
    path.write_text(
        path.read_text(encoding="utf-8") + "\nshutil.copytree(source, target)\n",
        encoding="utf-8",
    )


def _standard_host_cache(repo: Path) -> None:
    _standard_replace(
        repo / "repo-scripts/check-clean-wheel.py",
        'str(transaction_root / "uv-cache")',
        'str(Path.home() / ".cache/uv")',
    )


def _standard_ambient_home(repo: Path) -> None:
    _standard_replace(
        repo / "repo-scripts/check-clean-wheel.py",
        'str(transaction_root / "home")',
        'os.environ["HOME"]',
    )


def _standard_ambient_xdg(repo: Path) -> None:
    _standard_replace(
        repo / "repo-scripts/check-clean-wheel.py",
        'str(transaction_root / "xdg-cache")',
        'os.environ["XDG_CACHE_HOME"]',
    )


def _standard_ambient_tmp(repo: Path) -> None:
    _standard_replace(
        repo / "repo-scripts/check-clean-wheel.py",
        'str(transaction_root / "tmp")',
        'os.environ["TMPDIR"]',
    )


def _standard_ambient_pythonpath(repo: Path) -> None:
    _standard_replace(
        repo / "repo-scripts/check-clean-wheel.py",
        '"PYTHONPATH": ""',
        '"PYTHONPATH": os.environ.get("PYTHONPATH", "")',
    )


def _standard_editable_origin(repo: Path) -> None:
    _standard_replace(
        repo / "repo-scripts/check-clean-wheel.py",
        'origin_policy = "target-only"',
        'origin_policy = "editable"',
    )


def _standard_source_origin(repo: Path) -> None:
    _standard_replace(
        repo / "repo-scripts/check-clean-wheel.py",
        "origin.is_relative_to(target)",
        "origin.is_relative_to(project_root)",
    )


def _standard_fake_pass_without_scaffold(repo: Path) -> None:
    (repo / "octoagent/pyproject.toml").unlink()
    (repo / "octoagent/uv.lock").unlink()


def _standard_backend_reject_cases() -> tuple[tuple[str, Any], ...]:
    return (
        ("pin missing", _standard_pin_missing),
        ("pin drift", _standard_pin_drift),
        ("runtime dependency leak", _standard_runtime_leak),
        ("lock package missing", _standard_lock_missing),
        ("lock version drift", _standard_lock_drift),
        ("lock transitive closure missing", _standard_lock_closure_missing),
        ("workspace backend mismatch", _standard_backend_mismatch),
        ("manual wheel builder", _standard_manual_wheel),
        ("manual METADATA builder", _standard_manual_metadata),
        ("manual RECORD builder", _standard_manual_record),
        ("source tree copy installer", _standard_source_copy),
        ("host cache fallback", _standard_host_cache),
        ("ambient HOME fallback", _standard_ambient_home),
        ("ambient XDG fallback", _standard_ambient_xdg),
        ("ambient TMP fallback", _standard_ambient_tmp),
        ("ambient PYTHONPATH fallback", _standard_ambient_pythonpath),
        ("editable workspace origin", _standard_editable_origin),
        ("source checkout origin", _standard_source_origin),
        ("fake PASS without locked scaffold", _standard_fake_pass_without_scaffold),
    )


def _standard_backend_reject_case(
    root: Path, validator: Any, label: str, mutator: Any
) -> str | None:
    repo = _standard_backend_fixture(root)
    if not _standard_backend_accepts(validator, repo):
        return f"{label}: valid accept control rejected"
    before = _standard_backend_byte_map(repo)
    mutator(repo)
    after = _standard_backend_byte_map(repo)
    if before == after:
        return f"{label}: reject fixture has no observable delta"
    if _standard_backend_accepts(validator, repo):
        return f"{label}: invalid scaffold accepted"
    return None


class TestCleanWheelContract:
    def test_provider_wheel_isolated_import_and_requires_dist(self, tmp_path: Path) -> None:
        _assert_preliminary_unowned_controls(tmp_path)
        exit_code, report = _invoke_clean_wheel(tmp_path, "provider")
        _assert_pass_report(
            exit_code,
            report,
            command="provider",
            level=None,
            expected_checks=PROVIDER_CHECKS,
        )
        _assert_isolation(_facts(report, "environment"))
        _assert_provider_checks(report)

    def test_gateway_relocation_level_runs_three_help_and_namespace_checks(
        self, tmp_path: Path
    ) -> None:
        exit_code, report = _invoke_clean_wheel(tmp_path, "gateway", "--level", "relocation")
        _assert_pass_report(
            exit_code,
            report,
            command="gateway",
            level="relocation",
            expected_checks=GATEWAY_RELOCATION_CHECKS,
        )
        _assert_isolation(_facts(report, "environment"))
        _assert_gateway_preliminary_checks(report)
        _assert_relocation_boundary(report)

    def test_gateway_full_runs_external_cwd_host_readiness_and_sigterm(
        self, tmp_path: Path
    ) -> None:
        with _contract_oracle(FULL_ORACLE):
            exit_code, report = _invoke_clean_wheel(tmp_path, "gateway", "--level", "full")
            _assert_full_report(exit_code, report, command="gateway", level="full")
            _require(
                set(_checks(report)) == GATEWAY_FULL_CHECKS,
                "gateway full check set mismatch",
            )
            _assert_isolation(_facts(report, "environment"))
            _assert_gateway_preliminary_checks(report)
            _assert_gateway_runtime_checks(report)

    def test_gateway_full_maps_invalid_startup_to_exit_78_before_uvicorn(
        self, tmp_path: Path
    ) -> None:
        with _contract_oracle(FULL_ORACLE):
            exit_code, report = _invoke_clean_wheel(tmp_path, "gateway", "--level", "full")
            _assert_full_report(exit_code, report, command="gateway", level="full")
            _require(
                set(_checks(report)) == GATEWAY_FULL_CHECKS,
                "gateway full check set mismatch",
            )
            _assert_invalid_startup_checks(report)

    def test_source_managed_commands_exit_69_before_side_effects(self, tmp_path: Path) -> None:
        with _contract_oracle(FULL_ORACLE):
            exit_code, report = _invoke_clean_wheel(tmp_path, "all")
            _assert_full_report(exit_code, report, command="all", level="full")
            _require(set(_checks(report)) == ALL_CHECKS, "all check set mismatch")
            _assert_source_managed_checks(report)

    def test_final_direct_dependency_closure_matches_runtime_required_imports_and_manifests(
        self, tmp_path: Path
    ) -> None:
        with _contract_oracle(FINAL_DEPENDENCY_ORACLE):
            exit_code, report = _invoke_clean_wheel(tmp_path, "all")
            _assert_full_report(exit_code, report, command="all", level="full")
            _require(set(_checks(report)) == ALL_CHECKS, "all check set mismatch")
            _assert_final_dependency_closure(report)

    def test_standard_backend_scaffold_is_locked_offline_and_never_synthesizes_wheels(
        self, tmp_path: Path
    ) -> None:
        validator = _standard_backend_validator()
        issues: list[str] = []
        control = _standard_backend_fixture(tmp_path / "positive")
        issues.extend(_standard_backend_report_issues(validator(control)))
        for ordinal, (label, mutator) in enumerate(_standard_backend_reject_cases()):
            issue = _standard_backend_reject_case(
                tmp_path / f"reject-{ordinal:02d}", validator, label, mutator
            )
            if issue is not None:
                issues.append(issue)
        if issues:
            pytest.fail(
                f"{STANDARD_BACKEND_ORACLE}: " + "; ".join(issues),
                pytrace=False,
            )

    def test_import_inventory_classifies_runtime_optional_type_checking_plugin_and_workspace_ownership(  # noqa: E501
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        module = _load_contract_checker(IMPORT_CLASSIFICATION_ORACLE)
        classifier = _contract_callable(
            module, "classify_distribution_imports", IMPORT_CLASSIFICATION_ORACLE
        )
        builder = _contract_callable(
            module, "build_dependency_inventory", IMPORT_CLASSIFICATION_ORACLE
        )
        validator = _contract_callable(
            module, "validate_classified_occurrences", IMPORT_CLASSIFICATION_ORACLE
        )
        target = _classification_fixture(tmp_path / "positive")
        occurrences = _classification_accept_control(
            classifier, target, "positive classification rejected"
        )
        _assert_target_owner_ignores_host_shadow(
            module, classifier, target, occurrences, monkeypatch
        )
        _assert_preliminary_inventory(builder, occurrences)
        _assert_workspace_owner_controls(classifier, validator, tmp_path / "workspace-owner")
        _assert_classification_rejects_unknown(classifier, tmp_path / "reject-unknown")

    def test_isolated_child_reports_actual_environment_paths_and_workspace_origins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        module = _load_contract_checker(CHILD_OBSERVATION_ORACLE)
        runner = _contract_callable(module, "run_isolated_probe", CHILD_OBSERVATION_ORACLE)
        validator = _contract_callable(
            module, "validate_child_observation", CHILD_OBSERVATION_ORACLE
        )
        paths = _probe_fixture(tmp_path / "positive")
        target, transaction_root, external = paths
        observation = _assert_child_observation(
            runner(target, transaction_root, external, {"demo-dist": "demo_pkg"}),
            target,
            external,
        )
        _assert_child_reject_controls(validator, observation, paths)
        _assert_probe_rejects_stdout_pollution(
            module, runner, monkeypatch, _probe_fixture(tmp_path / "polluted")
        )
        _assert_probe_consumes_child_json(
            module,
            runner,
            validator,
            monkeypatch,
            _probe_fixture(tmp_path / "tampered-json"),
        )
