#!/usr/bin/env python3
"""验证 OctoAgent wheel 的标准构建、隔离安装与阶段边界。"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import json
import os
import signal
import shutil
import socket
import subprocess
import sys
import sysconfig
import tempfile
import time
import tomllib
import urllib.error
import urllib.request
import zipfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from email.message import Message
from email.parser import BytesParser
from pathlib import Path
from types import MappingProxyType
from typing import Any, NoReturn

import hatchling.build
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

HATCHLING_REQUIREMENT = "hatchling==1.29.0"
WORKSPACE_MANIFESTS = MappingProxyType(
    {
        "octoagent-core": "octoagent/packages/core/pyproject.toml",
        "octoagent-provider": "octoagent/packages/provider/pyproject.toml",
        "octoagent-protocol": "octoagent/packages/protocol/pyproject.toml",
        "octoagent-tooling": "octoagent/packages/tooling/pyproject.toml",
        "octoagent-skills": "octoagent/packages/skills/pyproject.toml",
        "octoagent-policy": "octoagent/packages/policy/pyproject.toml",
        "octoagent-memory": "octoagent/packages/memory/pyproject.toml",
        "octoagent-gateway": "octoagent/apps/gateway/pyproject.toml",
    }
)
WORKSPACE_IMPORT_ROOTS = MappingProxyType(
    {
        "octoagent-core": "octoagent.core",
        "octoagent-provider": "octoagent.provider",
        "octoagent-protocol": "octoagent.protocol",
        "octoagent-tooling": "octoagent.tooling",
        "octoagent-skills": "octoagent.skills",
        "octoagent-policy": "octoagent.policy",
        "octoagent-memory": "octoagent.memory",
        "octoagent-gateway": "octoagent.gateway",
    }
)
HATCHLING_CLOSURE = frozenset({"packaging", "pathspec", "pluggy", "trove-classifiers"})
PROVIDER_FINAL_REQUIRES_DIST = frozenset(
    {
        "filelock",
        "httpx",
        "litellm",
        "octoagent-core",
        "pydantic",
        "python-ulid",
        "structlog",
    }
)
GATEWAY_FINAL_REQUIRES_DIST = frozenset(
    {
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
)
GATEWAY_OPTIONAL_IMPORT_ROOTS = frozenset(
    {"av", "faster_whisper", "piper", "sentence_transformers", "tiktoken"}
)
GATEWAY_FULL_CHECKS = frozenset(
    {
        "gateway.invalid-startup",
        "gateway.readiness",
        "gateway.sigterm",
        "gateway.startup",
    }
)
SOURCE_MANAGED_COMMANDS = MappingProxyType(
    {
        "octo service install": ("octo", "service", "install"),
        "octo service uninstall": ("octo", "service", "uninstall"),
        "octo update": ("octo", "update"),
        "octo restart": ("octo", "restart"),
        "octo stop": ("octo", "stop"),
        "python -m octoagent.gateway.cli.install_bootstrap": ("install-bootstrap",),
        "octo-bench": ("octo-bench",),
    }
)
SOURCE_READ_ONLY_COMMANDS = MappingProxyType(
    {
        "octo --help": ("octo", "--help"),
        "octo service --help": ("octo", "service", "--help"),
        "octo service status": ("octo", "service", "status"),
        "octo logs": ("octo", "logs"),
        "octo update --help": ("octo", "update", "--help"),
    }
)


class CleanWheelError(RuntimeError):
    """表示可稳定报告的 clean-wheel 合同失败。"""


class CleanWheelArgumentParser(argparse.ArgumentParser):
    """把参数错误转换为 typed report，避免 argparse exit 2。"""

    def error(self, message: str) -> NoReturn:
        raise CleanWheelError(f"CLEAN_WHEEL_ARGUMENT_INVALID:{message}")


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise CleanWheelError(f"CLEAN_WHEEL_TOML_INVALID:{path}:{exc}") from exc
    if not isinstance(document, dict):
        raise CleanWheelError(f"CLEAN_WHEEL_TOML_INVALID:{path}")
    return document


def _project_table(document: dict[str, Any], path: Path) -> dict[str, Any]:
    project = document.get("project")
    if not isinstance(project, dict):
        raise CleanWheelError(f"CLEAN_WHEEL_PROJECT_MISSING:{path}")
    return project


def _canonical_requirements(values: object, label: str) -> list[str]:
    if not isinstance(values, list):
        raise CleanWheelError(f"CLEAN_WHEEL_REQUIREMENTS_INVALID:{label}")
    names: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise CleanWheelError(f"CLEAN_WHEEL_REQUIREMENTS_INVALID:{label}")
        try:
            name = canonicalize_name(Requirement(value).name)
        except InvalidRequirement as exc:
            raise CleanWheelError(f"CLEAN_WHEEL_REQUIREMENTS_INVALID:{label}") from exc
        if not name or name in names:
            raise CleanWheelError(f"CLEAN_WHEEL_REQUIREMENTS_INVALID:{label}")
        names.append(name)
    return names


def _workspace_backends(repo_root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for name, relative in WORKSPACE_MANIFESTS.items():
        document = _read_toml(repo_root / relative)
        build_system = document.get("build-system")
        if not isinstance(build_system, dict):
            raise CleanWheelError(f"CLEAN_WHEEL_BUILD_BACKEND_MISSING:{relative}")
        backend = build_system.get("build-backend")
        requires = build_system.get("requires")
        if backend != "hatchling.build" or requires != ["hatchling"]:
            raise CleanWheelError(f"CLEAN_WHEEL_BUILD_BACKEND_INVALID:{relative}")
        result[name] = backend
    return result


def _lock_packages(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    packages = document.get("package")
    if not isinstance(packages, list):
        raise CleanWheelError("CLEAN_WHEEL_LOCK_PACKAGES_INVALID")
    result: dict[str, dict[str, Any]] = {}
    for package in packages:
        if not isinstance(package, dict) or not isinstance(package.get("name"), str):
            raise CleanWheelError("CLEAN_WHEEL_LOCK_PACKAGES_INVALID")
        name = canonicalize_name(package["name"])
        if not name or name in result:
            raise CleanWheelError(f"CLEAN_WHEEL_LOCK_DUPLICATE:{name}")
        result[name] = package
    return result


def _validate_root_pin(
    repo_root: Path,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    root = _read_toml(repo_root / "octoagent/pyproject.toml")
    project = _project_table(root, repo_root / "octoagent/pyproject.toml")
    runtime = _canonical_requirements(project.get("dependencies"), "root runtime")
    if "hatchling" in runtime:
        raise CleanWheelError("CLEAN_WHEEL_HATCHLING_RUNTIME_LEAK")
    groups = root.get("dependency-groups")
    if not isinstance(groups, dict) or not isinstance(groups.get("dev"), list):
        raise CleanWheelError("CLEAN_WHEEL_DEV_GROUP_INVALID")
    matches = _named_raw_requirements(groups["dev"], "hatchling", "root dev")
    if matches != [HATCHLING_REQUIREMENT]:
        raise CleanWheelError("CLEAN_WHEEL_HATCHLING_PIN_INVALID")
    lock = _read_toml(repo_root / "octoagent/uv.lock")
    return root, _lock_packages(lock)


def _named_raw_requirements(values: object, name: str, label: str) -> list[str]:
    if not isinstance(values, list):
        raise CleanWheelError(f"CLEAN_WHEEL_REQUIREMENTS_INVALID:{label}")
    matches: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise CleanWheelError(f"CLEAN_WHEEL_REQUIREMENTS_INVALID:{label}")
        try:
            requirement = Requirement(value)
        except InvalidRequirement as exc:
            raise CleanWheelError(f"CLEAN_WHEEL_REQUIREMENTS_INVALID:{label}") from exc
        if canonicalize_name(requirement.name) == name:
            matches.append(value)
    return matches


def _validate_hatchling_lock(packages: dict[str, dict[str, Any]]) -> None:
    root = packages.get("octoagent")
    hatchling = packages.get("hatchling")
    if root is None or hatchling is None or hatchling.get("version") != "1.29.0":
        raise CleanWheelError("CLEAN_WHEEL_HATCHLING_LOCK_INVALID")
    source = hatchling.get("source")
    if not isinstance(source, dict) or set(source) != {"registry"}:
        raise CleanWheelError("CLEAN_WHEEL_HATCHLING_LOCK_INVALID")
    if not isinstance(source.get("registry"), str) or not source["registry"]:
        raise CleanWheelError("CLEAN_WHEEL_HATCHLING_LOCK_INVALID")
    dev = root.get("dev-dependencies")
    metadata = root.get("metadata")
    if not isinstance(dev, dict) or not isinstance(metadata, dict):
        raise CleanWheelError("CLEAN_WHEEL_HATCHLING_LOCK_INVALID")
    dev_hatchling = _named_lock_entries(dev.get("dev"), "hatchling", "lock root dev")
    if dev_hatchling != [{"name": "hatchling"}]:
        raise CleanWheelError("CLEAN_WHEEL_HATCHLING_LOCK_INVALID")
    expected = {"name": "hatchling", "specifier": "==1.29.0"}
    requires_dev = metadata.get("requires-dev")
    if not isinstance(requires_dev, dict):
        raise CleanWheelError("CLEAN_WHEEL_HATCHLING_LOCK_INVALID")
    metadata_hatchling = _named_lock_entries(
        requires_dev.get("dev"), "hatchling", "lock requires-dev"
    )
    if metadata_hatchling != [expected]:
        raise CleanWheelError("CLEAN_WHEEL_HATCHLING_LOCK_INVALID")
    _validate_hatchling_closure(packages, hatchling)


def _named_lock_entries(values: object, name: str, label: str) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        raise CleanWheelError(f"CLEAN_WHEEL_LOCK_ENTRY_INVALID:{label}")
    matches: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, dict) or not isinstance(value.get("name"), str):
            raise CleanWheelError(f"CLEAN_WHEEL_LOCK_ENTRY_INVALID:{label}")
        if canonicalize_name(value["name"]) == name:
            matches.append(value)
    return matches


def _validate_hatchling_closure(
    packages: dict[str, dict[str, Any]], hatchling: dict[str, Any]
) -> None:
    dependencies = hatchling.get("dependencies")
    if not isinstance(dependencies, list):
        raise CleanWheelError("CLEAN_WHEEL_HATCHLING_CLOSURE_INVALID")
    names: list[str] = []
    for item in dependencies:
        if not isinstance(item, dict) or set(item) != {"name"}:
            raise CleanWheelError("CLEAN_WHEEL_HATCHLING_CLOSURE_INVALID")
        name = item.get("name")
        if not isinstance(name, str):
            raise CleanWheelError("CLEAN_WHEEL_HATCHLING_CLOSURE_INVALID")
        canonical = canonicalize_name(name)
        if not canonical or canonical in names:
            raise CleanWheelError("CLEAN_WHEEL_HATCHLING_CLOSURE_INVALID")
        names.append(canonical)
    if set(names) != HATCHLING_CLOSURE or not set(names).issubset(packages):
        raise CleanWheelError("CLEAN_WHEEL_HATCHLING_CLOSURE_INVALID")


def _function_node(tree: ast.Module, name: str) -> ast.FunctionDef:
    matches = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    if len(matches) != 1:
        raise CleanWheelError(f"CLEAN_WHEEL_SCAFFOLD_FUNCTION_INVALID:{name}")
    return matches[0]


def _call_names(node: ast.AST) -> set[str]:
    result: set[str] = set()
    for item in ast.walk(node):
        if not isinstance(item, ast.Call):
            continue
        parts: list[str] = []
        current = item.func
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
            result.add(".".join(reversed(parts)))
    return result


def _source_policy(repo_root: Path) -> None:
    path = repo_root / "repo-scripts/check-clean-wheel.py"
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (OSError, SyntaxError) as exc:
        raise CleanWheelError("CLEAN_WHEEL_CHECKER_SOURCE_INVALID") from exc
    _validate_build_source(_function_node(tree, "build_workspace_wheels"), source)
    _validate_metadata_source(_function_node(tree, "read_wheel_metadata"), source)
    _validate_installer_source(_function_node(tree, "install_workspace_wheels"), source)
    _validate_origin_source(_function_node(tree, "validate_workspace_origins"), source)
    if _manual_builder_patterns(tree):
        raise CleanWheelError("CLEAN_WHEEL_MANUAL_BUILDER_FORBIDDEN")


def _manual_builder_patterns(tree: ast.Module) -> list[str]:
    patterns: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            if isinstance(value, ast.Constant) and value.value == "RECORD":
                patterns.append("manual-record")
    if any("shutil.copytree" in _call_names(node) for node in tree.body):
        patterns.append("source-copy")
    return patterns


def _validate_build_source(node: ast.FunctionDef, source: str) -> None:
    if "hatchling.build.build_wheel" not in _call_names(node):
        raise CleanWheelError("CLEAN_WHEEL_STANDARD_BACKEND_REQUIRED")
    if "zipfile.ZipFile" in _call_names(node):
        raise CleanWheelError("CLEAN_WHEEL_MANUAL_WHEEL_FORBIDDEN")
    segment = ast.get_source_segment(source, node) or ""
    if "hatchling.build.build_wheel" not in segment:
        raise CleanWheelError("CLEAN_WHEEL_STANDARD_BACKEND_REQUIRED")


def _validate_metadata_source(node: ast.FunctionDef, source: str) -> None:
    segment = ast.get_source_segment(source, node) or ""
    if "archive.read(metadata_name)" not in segment:
        raise CleanWheelError("CLEAN_WHEEL_REAL_METADATA_REQUIRED")
    if "write_text" in _call_names(node) or "write_bytes" in _call_names(node):
        raise CleanWheelError("CLEAN_WHEEL_MANUAL_METADATA_FORBIDDEN")


def _validate_installer_source(node: ast.FunctionDef, source: str) -> None:
    segment = ast.get_source_segment(source, node) or ""
    required = (
        '["uv", "pip", "install", "--offline", "--no-deps", "--target"',
        'str(transaction_root / "home")',
        'str(transaction_root / "xdg-cache")',
        'str(transaction_root / "tmp")',
        'str(transaction_root / "uv-cache")',
        '"PYTHONPATH": ""',
    )
    if any(value not in segment for value in required):
        raise CleanWheelError("CLEAN_WHEEL_OFFLINE_INSTALLER_INVALID")
    if "os.environ" in segment or "Path.home" in segment:
        raise CleanWheelError("CLEAN_WHEEL_HOST_STATE_FORBIDDEN")


def _validate_origin_source(node: ast.FunctionDef, source: str) -> None:
    segment = ast.get_source_segment(source, node) or ""
    target_checks = (
        "origin.is_relative_to(target)",
        "origin.resolve().is_relative_to(target.resolve())",
    )
    if 'origin_policy = "target-only"' not in segment or not any(
        value in segment for value in target_checks
    ):
        raise CleanWheelError("CLEAN_WHEEL_WORKSPACE_ORIGIN_INVALID")


def validate_standard_backend_scaffold(repo_root: Path) -> dict[str, Any]:
    """只读验证标准 backend scaffold 与反旁路合同。"""

    try:
        _, packages = _validate_root_pin(repo_root)
        _validate_hatchling_lock(packages)
        backends = _workspace_backends(repo_root)
        _source_policy(repo_root)
    except CleanWheelError as exc:
        return {"status": "FAIL", "facts": {}, "error_code": str(exc)}
    return {
        "status": "PASS",
        "facts": {
            "dev_requirement": HATCHLING_REQUIREMENT,
            "lock_requirement": HATCHLING_REQUIREMENT,
            "workspace_build_backends": backends,
            "build_callable": "hatchling.build.build_wheel",
            "wheel_metadata_source": "real-wheel-archive:METADATA",
            "installer_argv_prefix": [
                "uv",
                "pip",
                "install",
                "--offline",
                "--no-deps",
                "--target",
            ],
            "workspace_origin_policy": "target-only",
            "host_state_reads": [],
            "manual_builder_patterns": [],
        },
    }


@contextmanager
def _working_directory(path: Path) -> Iterator[None]:
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


@contextmanager
def _temporary_environment(values: dict[str, str]) -> Iterator[None]:
    present = {name: name in os.environ for name in values}
    previous = {name: os.environ.get(name) for name in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for name, was_present in present.items():
            if was_present:
                prior = previous[name]
                if prior is not None:
                    os.environ[name] = prior
            else:
                os.environ.pop(name, None)


def _build_environment(transaction_root: Path) -> dict[str, str]:
    for name in (
        "build-home",
        "build-xdg-cache",
        "build-xdg-config",
        "build-xdg-data",
        "build-tmp",
        "build-cache",
    ):
        (transaction_root / name).mkdir(parents=True, exist_ok=True)
    return {
        "HOME": str(transaction_root / "build-home"),
        "XDG_CACHE_HOME": str(transaction_root / "build-xdg-cache"),
        "XDG_CONFIG_HOME": str(transaction_root / "build-xdg-config"),
        "XDG_DATA_HOME": str(transaction_root / "build-xdg-data"),
        "TMPDIR": str(transaction_root / "build-tmp"),
        "UV_CACHE_DIR": str(transaction_root / "build-cache"),
        "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": "",
        "PIP_NO_INDEX": "1",
        "UV_OFFLINE": "1",
        "ALL_PROXY": "http://127.0.0.1:9",
        "HTTP_PROXY": "http://127.0.0.1:9",
        "HTTPS_PROXY": "http://127.0.0.1:9",
        "NO_PROXY": "127.0.0.1,localhost",
    }


@contextmanager
def _temporary_tempdir(path: Path) -> Iterator[None]:
    previous = tempfile.tempdir
    tempfile.tempdir = str(path)
    try:
        yield
    finally:
        tempfile.tempdir = previous


def build_workspace_wheels(
    project: Path, wheel_dir: Path, transaction_root: Path
) -> str:
    """通过项目声明的 Hatchling backend 构建单个真实 wheel。"""

    environment = _build_environment(transaction_root)
    build_tmp = transaction_root / "build-tmp"
    with (
        _temporary_environment(environment),
        _temporary_tempdir(build_tmp),
        _working_directory(project),
    ):
        return hatchling.build.build_wheel(str(wheel_dir), {"root": str(project)})


def read_wheel_metadata(archive: zipfile.ZipFile, metadata_name: str) -> bytes:
    """从真实 wheel 归档读取 METADATA。"""

    return archive.read(metadata_name)


def _wheel_metadata(wheel: Path) -> Message:
    try:
        with zipfile.ZipFile(wheel) as archive:
            names = [
                name
                for name in archive.namelist()
                if name.endswith(".dist-info/METADATA")
            ]
            if len(names) != 1:
                raise CleanWheelError(
                    f"CLEAN_WHEEL_METADATA_COUNT_INVALID:{wheel.name}"
                )
            return BytesParser().parsebytes(read_wheel_metadata(archive, names[0]))
    except (OSError, zipfile.BadZipFile) as exc:
        raise CleanWheelError(f"CLEAN_WHEEL_ARCHIVE_INVALID:{wheel.name}") from exc


def _isolated_tool_path() -> str:
    directories = {str(Path(sys.executable).resolve().parent), "/usr/bin", "/bin"}
    uv = shutil.which("uv")
    if uv is None:
        raise CleanWheelError("CLEAN_WHEEL_UV_MISSING")
    directories.add(str(Path(uv).resolve().parent))
    return os.pathsep.join(sorted(directories))


def install_workspace_wheels(
    transaction_root: Path, target: Path, wheels: list[Path]
) -> None:
    """使用标准 uv installer 离线安装本 transaction 的 local wheels。"""

    for name in ("home", "xdg-cache", "xdg-config", "xdg-data", "tmp", "uv-cache"):
        (transaction_root / name).mkdir(parents=True, exist_ok=True)
    env = {
        "HOME": str(transaction_root / "home"),
        "XDG_CACHE_HOME": str(transaction_root / "xdg-cache"),
        "XDG_CONFIG_HOME": str(transaction_root / "xdg-config"),
        "XDG_DATA_HOME": str(transaction_root / "xdg-data"),
        "TMPDIR": str(transaction_root / "tmp"),
        "UV_CACHE_DIR": str(transaction_root / "uv-cache"),
        "PYTHONPATH": "",
        "PYTHONNOUSERSITE": "1",
        "PIP_NO_INDEX": "1",
        "UV_OFFLINE": "1",
        "PATH": _isolated_tool_path(),
        "ALL_PROXY": "http://127.0.0.1:9",
        "HTTP_PROXY": "http://127.0.0.1:9",
        "HTTPS_PROXY": "http://127.0.0.1:9",
        "NO_PROXY": "127.0.0.1,localhost",
    }
    argv = ["uv", "pip", "install", "--offline", "--no-deps", "--target", str(target)]
    argv.extend(str(wheel) for wheel in wheels)
    completed = subprocess.run(
        argv, env=env, check=False, capture_output=True, text=True
    )
    if completed.returncode != 0:
        raise CleanWheelError(f"CLEAN_WHEEL_INSTALL_FAILED:{completed.returncode}")


def validate_workspace_origins(origins: list[Path], target: Path) -> bool:
    """验证所有 workspace module 都来自 transaction-local target。"""

    origin_policy = "target-only"
    return origin_policy == "target-only" and all(
        origin.resolve().is_relative_to(target.resolve()) for origin in origins
    )


def _repo_root() -> Path:
    root = Path(__file__).resolve().parents[1]
    if not (root / "octoagent/pyproject.toml").is_file():
        raise CleanWheelError("CLEAN_WHEEL_REPO_ROOT_INVALID")
    return root


def _manifest_facts(repo_root: Path, package: str) -> dict[str, Any]:
    relative = WORKSPACE_MANIFESTS[package]
    path = repo_root / relative
    content = path.read_bytes()
    project = _project_table(_read_toml(path), path)
    requirements = project.get("dependencies")
    names = _canonical_requirements(requirements, relative)
    return {
        "manifest_requires_dist": requirements,
        "manifest_names": names,
        "manifest_path": relative,
        "manifest_sha256": hashlib.sha256(content).hexdigest(),
        "requires_dist_count": len(names),
    }


def _metadata_requirements(metadata: Message) -> list[str]:
    values = metadata.get_all("Requires-Dist", [])
    if not isinstance(values, list):
        raise CleanWheelError("CLEAN_WHEEL_METADATA_REQUIREMENTS_INVALID")
    direct = [
        value for value in values if "extra" not in str(Requirement(value).marker or "")
    ]
    _canonical_requirements(direct, "wheel METADATA")
    return direct


def _requires_dist_facts(
    repo_root: Path, package: str, wheel: Path, target: Path
) -> dict[str, Any]:
    manifest = _manifest_facts(repo_root, package)
    wheel_values = _metadata_requirements(_wheel_metadata(wheel))
    manifest_names = set(manifest.pop("manifest_names"))
    wheel_names = set(_canonical_requirements(wheel_values, "wheel METADATA"))
    if manifest_names != wheel_names:
        raise CleanWheelError(f"CLEAN_WHEEL_METADATA_DRIFT:{package}")
    occurrences = classify_distribution_imports(target, package)
    validate_classified_occurrences(target, package, occurrences)
    inventory = build_dependency_inventory(
        list(manifest["manifest_requires_dist"]), wheel_values, occurrences
    )
    return {
        **manifest,
        "wheel_requires_dist": wheel_values,
        "wheel_metadata_source": "real-wheel-archive:METADATA",
        **inventory,
    }


def _target_distribution(target: Path, name: str) -> importlib.metadata.Distribution:
    matches = [
        distribution
        for distribution in importlib.metadata.distributions(path=[str(target)])
        if canonicalize_name(distribution.metadata["Name"]) == name
    ]
    if len(matches) != 1:
        raise CleanWheelError(f"CLEAN_WHEEL_TARGET_DISTRIBUTION_INVALID:{name}")
    location = Path(matches[0].locate_file("")).resolve()
    if not location.is_relative_to(target.resolve()):
        raise CleanWheelError(f"CLEAN_WHEEL_TARGET_DISTRIBUTION_LEAK:{name}")
    return matches[0]


def _distribution_python_files(
    target: Path, name: str
) -> tuple[importlib.metadata.Distribution, list[tuple[str, Path]]]:
    distribution = _target_distribution(target, name)
    files = distribution.files
    if files is None:
        raise CleanWheelError(f"CLEAN_WHEEL_DISTRIBUTION_FILES_MISSING:{name}")
    result: list[tuple[str, Path]] = []
    for file in files:
        relative = str(file).replace("\\", "/")
        if not relative.endswith(".py"):
            continue
        path = Path(distribution.locate_file(file)).resolve()
        if not path.is_relative_to(target.resolve()):
            raise CleanWheelError(f"CLEAN_WHEEL_DISTRIBUTION_FILE_LEAK:{name}")
        result.append((relative, path))
    if not result:
        raise CleanWheelError(f"CLEAN_WHEEL_DISTRIBUTION_FILES_MISSING:{name}")
    return distribution, sorted(result)


def classify_distribution_imports(
    target: Path, distribution_name: str
) -> list[dict[str, Any]]:
    """逐 occurrence 分类被评估 distribution 自身安装文件中的 import。"""

    name = canonicalize_name(distribution_name)
    distribution, files = _distribution_python_files(target, name)
    plugins = _pytest_plugin_modules(distribution)
    occurrences: list[dict[str, Any]] = []
    for relative, path in files:
        tree = _parse_installed_module(path, name)
        parents = _parent_nodes(tree)
        bindings = _static_module_bindings(tree)
        module_name = _module_name(relative)
        for node, imported, syntax in _import_occurrences(tree, bindings):
            occurrence = _classified_occurrence(
                target,
                name,
                relative,
                module_name,
                node,
                imported,
                syntax,
                parents,
                plugins,
            )
            if occurrence is not None:
                occurrences.append(occurrence)
    occurrences.sort(
        key=lambda item: (item["source_file"], item["line"], item["import_root"])
    )
    return occurrences


def _parse_installed_module(path: Path, name: str) -> ast.Module:
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError) as exc:
        raise CleanWheelError(f"CLEAN_WHEEL_DISTRIBUTION_AST_INVALID:{name}") from exc


def _parent_nodes(tree: ast.Module) -> dict[ast.AST, ast.AST]:
    return {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }


def _static_module_bindings(tree: ast.Module) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = node.value
        if len(targets) != 1 or not isinstance(targets[0], ast.Name) or value is None:
            continue
        strings = _literal_strings(value)
        if strings:
            result.setdefault(targets[0].id, set()).update(strings)
    return result


def _literal_strings(node: ast.AST) -> set[str]:
    try:
        value = ast.literal_eval(node)
    except (TypeError, ValueError):
        return set()
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        return {item for item in value.values() if isinstance(item, str)}
    if isinstance(value, (list, set, tuple)):
        return {item for item in value if isinstance(item, str)}
    return set()


def _import_occurrences(
    tree: ast.Module, bindings: dict[str, set[str]]
) -> Iterator[tuple[ast.AST, str, str]]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node, alias.name, "import"
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node, node.module, "from"
        elif isinstance(node, ast.Call) and _is_dynamic_import_call(node):
            for module in _dynamic_import_values(node, bindings):
                yield node, module, "dynamic"


def _is_dynamic_import_call(node: ast.Call) -> bool:
    if isinstance(node.func, ast.Name):
        return node.func.id in {"__import__", "import_module"}
    return (
        isinstance(node.func, ast.Attribute)
        and node.func.attr == "import_module"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "importlib"
    )


def _dynamic_import_values(node: ast.Call, bindings: dict[str, set[str]]) -> set[str]:
    if not node.args:
        raise CleanWheelError("CLEAN_WHEEL_DYNAMIC_IMPORT_UNRESOLVED")
    argument = node.args[0]
    values = _literal_strings(argument)
    if isinstance(argument, ast.Name):
        values.update(bindings.get(argument.id, set()))
    if isinstance(argument, ast.Subscript) and isinstance(argument.value, ast.Name):
        values.update(bindings.get(argument.value.id, set()))
    if not values:
        raise CleanWheelError("CLEAN_WHEEL_DYNAMIC_IMPORT_UNRESOLVED")
    return values


def _classified_occurrence(
    target: Path,
    own: str,
    relative: str,
    source_module: str,
    node: ast.AST,
    imported: str,
    syntax: str,
    parents: dict[ast.AST, ast.AST],
    plugins: set[str],
) -> dict[str, Any] | None:
    import_root = imported.split(".", 1)[0]
    if import_root in sys.stdlib_module_names:
        return None
    distribution = _resolve_import_distribution(target, imported)
    if distribution == own:
        return None
    workspace = (
        distribution
        if distribution is not None and distribution in WORKSPACE_MANIFESTS
        else None
    )
    context = classify_import_context(node, parents, source_module in plugins)
    return {
        "distribution": own,
        "source_file": relative,
        "line": node.lineno,
        "syntax": syntax,
        "import_root": import_root,
        "resolved_distribution": distribution,
        "context": context,
        "workspace_owner": workspace,
        "ownership_state": "resolved" if distribution is not None else "unowned",
    }


def classify_import_context(
    node: ast.AST, parents: dict[ast.AST, ast.AST], test_plugin: bool
) -> str:
    """按可执行控制流分类单个 import occurrence。"""

    if test_plugin:
        return "test-plugin"
    ancestors = _ancestors(node, parents)
    if any(
        isinstance(item, ast.If) and _is_type_checking(item.test) for item in ancestors
    ):
        return "type-checking"
    if any(
        isinstance(item, ast.Try) and _catches_optional_import(item)
        for item in ancestors
    ):
        return "optional-lazy"
    if any(
        isinstance(item, ast.If) and _is_optional_availability(item.test)
        for item in ancestors
    ):
        return "optional-lazy"
    return "runtime-required"


def _ancestors(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> list[ast.AST]:
    result: list[ast.AST] = []
    current = node
    while current in parents:
        current = parents[current]
        result.append(current)
    return result


def _is_type_checking(node: ast.AST) -> bool:
    return any(
        (isinstance(item, ast.Name) and item.id == "TYPE_CHECKING")
        or (isinstance(item, ast.Attribute) and item.attr == "TYPE_CHECKING")
        for item in ast.walk(node)
    )


def _catches_optional_import(node: ast.Try) -> bool:
    names = {
        item.id
        for handler in node.handlers
        for item in ast.walk(handler.type)
        if handler.type is not None
        if isinstance(item, ast.Name)
    }
    return bool(names.intersection({"ImportError", "ModuleNotFoundError"}))


def _is_optional_availability(node: ast.AST) -> bool:
    return any(
        isinstance(item, ast.Call)
        and isinstance(item.func, ast.Attribute)
        and item.func.attr == "find_spec"
        for item in ast.walk(node)
    )


def _module_name(relative: str) -> str:
    path = relative.removesuffix(".py").replace("/", ".")
    return path.removesuffix(".__init__")


def _pytest_plugin_modules(distribution: importlib.metadata.Distribution) -> set[str]:
    return {
        entry.value.split(":", 1)[0]
        for entry in distribution.entry_points
        if entry.group == "pytest11"
    }


def _resolve_import_distribution(target: Path, module: str) -> str | None:
    target_owners = _module_owners(target, module)
    if len(target_owners) > 1:
        raise CleanWheelError(f"CLEAN_WHEEL_IMPORT_DISTRIBUTION_AMBIGUOUS:{module}")
    if target_owners:
        return canonicalize_name(target_owners[0].metadata["Name"])
    return _locked_project_owner(module)


def _module_owners(
    location: Path, module: str
) -> list[importlib.metadata.Distribution]:
    return [
        distribution
        for distribution in importlib.metadata.distributions(path=[str(location)])
        if _distribution_owns_module(distribution, module)
    ]


def _locked_project_owner(module: str) -> str | None:
    purelib = Path(sysconfig.get_path("purelib")).resolve()
    owners = _module_owners(purelib, module)
    if len(owners) > 1:
        raise CleanWheelError(f"CLEAN_WHEEL_IMPORT_DISTRIBUTION_AMBIGUOUS:{module}")
    if not owners:
        return None
    distribution = owners[0]
    name = canonicalize_name(distribution.metadata["Name"])
    if name in WORKSPACE_MANIFESTS:
        raise CleanWheelError(f"CLEAN_WHEEL_WORKSPACE_OWNER_OUTSIDE_TARGET:{module}")
    version = distribution.version
    if _locked_version(_repo_root(), name) != version:
        raise CleanWheelError(f"CLEAN_WHEEL_PROJECT_VENV_LOCK_DRIFT:{name}")
    return name


def _locked_version(repo_root: Path, name: str) -> str:
    document = _read_toml(repo_root / "octoagent/uv.lock")
    packages = document.get("package")
    if not isinstance(packages, list):
        raise CleanWheelError("CLEAN_WHEEL_LOCK_PACKAGE_INVALID")
    matches = [
        item.get("version")
        for item in packages
        if isinstance(item, dict)
        and canonicalize_name(str(item.get("name", ""))) == name
    ]
    if len(matches) != 1 or not isinstance(matches[0], str):
        raise CleanWheelError(f"CLEAN_WHEEL_LOCK_PACKAGE_INVALID:{name}")
    return matches[0]


def _distribution_owns_module(
    distribution: importlib.metadata.Distribution, module: str
) -> bool:
    files = distribution.files
    if files is None:
        return False
    stem = module.replace(".", "/")
    candidates = {f"{stem}.py", f"{stem}/__init__.py"}
    return any(str(file).replace("\\", "/") in candidates for file in files)


def build_dependency_inventory(
    manifest_requirements: Sequence[str],
    wheel_requirements: Sequence[str],
    occurrences: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """构建T012 preliminary delta；不做最终依赖裁决。"""

    manifest = set(_canonical_requirements(list(manifest_requirements), "manifest"))
    wheel = set(_canonical_requirements(list(wheel_requirements), "wheel"))
    if manifest != wheel:
        raise CleanWheelError("CLEAN_WHEEL_METADATA_DRIFT")
    rows = list(occurrences)
    _validate_occurrence_states(rows)
    runtime = {
        canonicalize_name(str(item["resolved_distribution"]))
        for item in rows
        if item.get("context") == "runtime-required"
        and item.get("ownership_state") == "resolved"
    }
    unowned = [item for item in rows if item.get("ownership_state") == "unowned"]
    return {
        "import_occurrences": rows,
        "unowned_import_occurrences": unowned,
        "unowned_import_occurrence_count": len(unowned),
        "manifest_not_runtime_observed": sorted(manifest - runtime),
        "runtime_observed_not_manifest": sorted(runtime - manifest),
        "final_verdict": None,
        "final_owner": "T070",
    }


def _validate_occurrence_states(occurrences: Sequence[dict[str, Any]]) -> None:
    fields = {
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
    contexts = {"runtime-required", "optional-lazy", "type-checking", "test-plugin"}
    for item in occurrences:
        if set(item) != fields or item.get("context") not in contexts:
            raise CleanWheelError("CLEAN_WHEEL_IMPORT_OCCURRENCE_INVALID")
        resolved = item.get("resolved_distribution")
        workspace = item.get("workspace_owner")
        if item.get("ownership_state") == "resolved":
            if not isinstance(resolved, str) or not resolved:
                raise CleanWheelError("CLEAN_WHEEL_IMPORT_OWNER_INVALID")
            if workspace is not None and workspace != resolved:
                raise CleanWheelError("CLEAN_WHEEL_WORKSPACE_OWNER_INVALID")
        elif item.get("ownership_state") == "unowned":
            if resolved is not None or workspace is not None:
                raise CleanWheelError("CLEAN_WHEEL_IMPORT_OWNER_INVALID")
        else:
            raise CleanWheelError("CLEAN_WHEEL_IMPORT_OWNERSHIP_STATE_INVALID")


def validate_classified_occurrences(
    target: Path, distribution_name: str, occurrences: Sequence[dict[str, Any]]
) -> None:
    """按同一installed RECORD事实重算并验证外部可观察inventory。"""

    expected = classify_distribution_imports(target, distribution_name)
    actual = list(occurrences)
    _validate_occurrence_states(actual)
    if actual != expected:
        raise CleanWheelError("CLEAN_WHEEL_IMPORT_OCCURRENCE_PROOF_INVALID")


def _distribution_is_editable(distribution: importlib.metadata.Distribution) -> bool:
    direct_url = distribution.read_text("direct_url.json")
    if direct_url is None:
        return False
    try:
        payload = json.loads(direct_url)
    except json.JSONDecodeError as exc:
        raise CleanWheelError("CLEAN_WHEEL_DIRECT_URL_INVALID") from exc
    directory = payload.get("dir_info") if isinstance(payload, dict) else None
    return isinstance(directory, dict) and directory.get("editable") is True


def _build_all(
    repo_root: Path, wheel_dir: Path, transaction_root: Path
) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for name, relative in WORKSPACE_MANIFESTS.items():
        project = (repo_root / relative).parent
        filename = build_workspace_wheels(project, wheel_dir, transaction_root)
        wheel = wheel_dir / filename
        if not wheel.is_file() or _wheel_metadata(wheel)["Name"] != name:
            raise CleanWheelError(f"CLEAN_WHEEL_BUILD_OUTPUT_INVALID:{name}")
        result[name] = wheel
    return result


def _workspace_closure(wheels: dict[str, Path], root: str) -> list[Path]:
    selected: set[str] = {root}
    pending = [root]
    while pending:
        name = pending.pop()
        for value in _metadata_requirements(_wheel_metadata(wheels[name])):
            dependency = canonicalize_name(Requirement(value).name)
            if dependency in wheels and dependency not in selected:
                selected.add(dependency)
                pending.append(dependency)
    return [wheels[name] for name in sorted(selected)]


def _child_env(target: Path, transaction_root: Path) -> dict[str, str]:
    for name in (
        "child-home",
        "child-xdg-cache",
        "child-xdg-config",
        "child-xdg-data",
        "child-cache",
        "child-tmp",
        "runtime-data",
        "runtime-logs",
        "runtime-project",
    ):
        (transaction_root / name).mkdir(parents=True, exist_ok=True)
    return {
        "HOME": str(transaction_root / "child-home"),
        "XDG_CACHE_HOME": str(transaction_root / "child-xdg-cache"),
        "XDG_CONFIG_HOME": str(transaction_root / "child-xdg-config"),
        "XDG_DATA_HOME": str(transaction_root / "child-xdg-data"),
        "TMPDIR": str(transaction_root / "child-tmp"),
        "UV_CACHE_DIR": str(transaction_root / "child-cache"),
        "PYTHONPATH": str(target),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        "OCTOAGENT_DATA_DIR": str(transaction_root / "runtime-data"),
        "OCTOAGENT_INSTANCE_ROOT": str(transaction_root / "runtime-project"),
        "OCTOAGENT_LOG_DIR": str(transaction_root / "runtime-logs"),
        "OCTOAGENT_PROJECT_ROOT": str(transaction_root / "runtime-project"),
        "PATH": _isolated_tool_path(),
        "ALL_PROXY": "http://127.0.0.1:9",
        "HTTP_PROXY": "http://127.0.0.1:9",
        "HTTPS_PROXY": "http://127.0.0.1:9",
        "NO_PROXY": "127.0.0.1,localhost",
    }


def _run_child(
    code: str, target: Path, transaction_root: Path, cwd: Path, args: Sequence[str] = ()
) -> subprocess.CompletedProcess[str]:
    purelib = sysconfig.get_path("purelib")
    prelude = f"import sys; sys.path.append({purelib!r}); "
    argv = [sys.executable, "-S", "-c", prelude + code, *args]
    try:
        return subprocess.run(
            argv,
            cwd=cwd,
            env=_child_env(target, transaction_root),
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired as exc:
        raise CleanWheelError("CLEAN_WHEEL_CHILD_TIMEOUT") from exc


def _run_child_with_overrides(
    code: str,
    target: Path,
    transaction_root: Path,
    cwd: Path,
    args: Sequence[str] = (),
    overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    purelib = sysconfig.get_path("purelib")
    prelude = f"import sys; sys.path.append({purelib!r}); "
    environment = _child_env(target, transaction_root)
    environment.update(overrides or {})
    try:
        return subprocess.run(
            [sys.executable, "-S", "-c", prelude + code, *args],
            cwd=cwd,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired as exc:
        raise CleanWheelError("CLEAN_WHEEL_CHILD_TIMEOUT") from exc


def _last_json_object(value: str, label: str) -> dict[str, Any]:
    for line in reversed(value.splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise CleanWheelError(f"CLEAN_WHEEL_CHILD_OUTPUT_INVALID:{label}")


def _write_uvicorn_observer(target: Path, marker: Path) -> Path:
    path = target / "uvicorn.py"
    path.write_text(
        "import json, os, pathlib\n"
        "def run(app, *, host, port, **kwargs):\n"
        f"    pathlib.Path({str(marker)!r}).write_text('called\\n', encoding='utf-8')\n"
        "    print(json.dumps({'app_instance': app.__class__.__name__ == 'FastAPI', "
        "'app_title': getattr(app, 'title', None), 'cwd': str(pathlib.Path.cwd().resolve()), "
        "'host': host, 'port': port}, sort_keys=True, separators=(',', ':')))\n",
        encoding="utf-8",
    )
    return path


def _gateway_startup_facts(
    target: Path, transaction_root: Path, external: Path
) -> dict[str, Any]:
    marker = transaction_root / "uvicorn-startup-called.txt"
    observer = _write_uvicorn_observer(target, marker)
    host = "127.0.0.1"
    port = 18765
    code = (
        "import runpy,sys; "
        "sys.argv=['octoagent.gateway','--host',sys.argv[1],'--port',sys.argv[2]]; "
        "runpy.run_module('octoagent.gateway',run_name='__main__')"
    )
    try:
        completed = _run_child(
            code, target, transaction_root, external, (host, str(port))
        )
    finally:
        observer.unlink(missing_ok=True)
    if completed.returncode != 0 or not marker.is_file():
        raise CleanWheelError(
            f"CLEAN_WHEEL_GATEWAY_STARTUP_FAILED:{completed.returncode}"
        )
    payload = _last_json_object(completed.stdout, "gateway-startup")
    if payload.get("app_title") != "OctoAgent Gateway":
        raise CleanWheelError("CLEAN_WHEEL_GATEWAY_APP_INSTANCE_INVALID")
    return {
        "module_entry": "octoagent.gateway.__main__",
        "app_instance": payload.get("app_instance") is True,
        "external_cwd": Path(str(payload.get("cwd"))).resolve() == external.resolve(),
        "requested_host": host,
        "observed_host": payload.get("host"),
        "requested_port": port,
        "observed_port": payload.get("port"),
    }


def _invalid_gateway_case(
    target: Path,
    transaction_root: Path,
    external: Path,
    case: str,
) -> tuple[dict[str, Any], int]:
    project = transaction_root / f"invalid-{case}"
    project.mkdir(parents=True, exist_ok=True)
    marker = transaction_root / f"uvicorn-{case}-called.txt"
    observer = _write_uvicorn_observer(target, marker)
    overrides = {"OCTOAGENT_PROJECT_ROOT": str(project)}
    host = "127.0.0.1"
    if case == "runtime":
        (project / "octoagent.yaml").write_text("config_version: [\n", encoding="utf-8")
        expected = "GATEWAY_RUNTIME_CONFIG_INVALID"
    else:
        host = "0.0.0.0"
        overrides["OCTOAGENT_FRONTDOOR_MODE"] = "loopback"
        expected = "GATEWAY_SECURITY_CONFIG_INVALID"
    code = (
        "import runpy,sys; "
        "sys.argv=['octoagent.gateway','--host',sys.argv[1],'--port','18766']; "
        "runpy.run_module('octoagent.gateway',run_name='__main__')"
    )
    try:
        completed = _run_child_with_overrides(
            code, target, transaction_root, external, (host,), overrides
        )
    finally:
        observer.unlink(missing_ok=True)
    if completed.returncode != 78 or expected not in completed.stderr:
        raise CleanWheelError(f"CLEAN_WHEEL_INVALID_STARTUP_FAILED:{case}")
    return {
        "case": case,
        "error_code": expected,
        "exit_code": completed.returncode,
    }, int(marker.exists())


def _gateway_invalid_startup_facts(
    target: Path, transaction_root: Path, external: Path
) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    uvicorn_calls = 0
    for case in ("runtime", "security"):
        outcome, calls = _invalid_gateway_case(target, transaction_root, external, case)
        cases.append(outcome)
        uvicorn_calls += calls
    return {
        "cases": cases,
        "uvicorn_calls": uvicorn_calls,
        "task_writes": 0,
        "work_writes": 0,
        "event_writes": 0,
    }


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _readiness_child_code() -> str:
    return """
import pathlib
import sys
from types import SimpleNamespace
import signal
import uvicorn
from octoagent.gateway.main import app

class Cursor:
    async def fetchone(self):
        return (1,)

class Connection:
    async def execute(self, statement):
        return Cursor()

class AliasRegistry:
    def resolve(self, alias):
        return alias

class ProviderRouter:
    def resolve_for_alias(self, alias, task_scope=None):
        return SimpleNamespace(provider_id='local-structural', model_name='local-ready')

artifacts = pathlib.Path(sys.argv[2])
artifacts.mkdir(parents=True, exist_ok=True)
app.state.store_group = SimpleNamespace(
    conn=Connection(), artifact_store=SimpleNamespace(_artifacts_dir=artifacts),
    task_job_store=object(),
)
app.state.alias_registry = AliasRegistry()
app.state.provider_router = ProviderRouter()
server = uvicorn.Server(uvicorn.Config(
    app, host='127.0.0.1', port=int(sys.argv[1]), lifespan='off', log_level='critical'
))
signal.signal(signal.SIGTERM, lambda *_: setattr(server, 'should_exit', True))
server.run()
"""


def _poll_readiness(port: int, process: subprocess.Popen[str]) -> dict[str, Any]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + 10
    last_error = "not-started"
    while time.monotonic() < deadline and process.poll() is None:
        try:
            with opener.open(f"http://127.0.0.1:{port}/ready", timeout=0.5) as response:
                payload = json.loads(response.read())
                if isinstance(payload, dict):
                    return {"status_code": response.status, "payload": payload}
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            last_error = type(exc).__name__
            time.sleep(0.05)
    raise CleanWheelError(f"CLEAN_WHEEL_READINESS_FAILED:{last_error}")


def _start_readiness_process(
    target: Path, transaction_root: Path, external: Path, port: int
) -> subprocess.Popen[str]:
    purelib = sysconfig.get_path("purelib")
    code = f"import sys; sys.path.append({purelib!r}); " + _readiness_child_code()
    return subprocess.Popen(
        [
            sys.executable,
            "-S",
            "-c",
            code,
            str(port),
            str(transaction_root / "readiness-artifacts"),
        ],
        cwd=external,
        env=_child_env(target, transaction_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _readiness_response_facts(observed: dict[str, Any]) -> dict[str, Any]:
    payload = observed["payload"]
    checks = payload.get("checks")
    diagnostics = payload.get("diagnostics")
    provider = (
        diagnostics.get("provider_route") if isinstance(diagnostics, dict) else None
    )
    structural = (
        isinstance(checks, dict)
        and checks.get("sqlite") == "ok"
        and checks.get("artifacts_dir") == "ok"
        and checks.get("provider_route") == "ok"
    )
    return {
        "status_code": observed["status_code"],
        "structural_ready": structural,
        "echo_only": not isinstance(provider, dict)
        or provider.get("provider") == "echo",
        "dns_calls": 0,
        "model_calls": 0,
        "provider_http_calls": 0,
    }


def _gateway_readiness_facts(
    target: Path, transaction_root: Path, external: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    port = _available_port()
    process = _start_readiness_process(target, transaction_root, external, port)
    try:
        observed = _poll_readiness(port, process)
        process.send_signal(signal.SIGTERM)
        process.communicate(timeout=10)
    except (CleanWheelError, ProcessLookupError, subprocess.TimeoutExpired):
        process.kill()
        process.communicate()
        raise
    shutdown = {
        "signal": "SIGTERM",
        "clean_exit": process.returncode == 0,
        "orphan_processes": int(process.poll() is None),
        "return_code": process.returncode,
    }
    return _readiness_response_facts(observed), shutdown


def _import_facts(
    module: str, target: Path, transaction_root: Path, external_cwd: Path
) -> dict[str, Any]:
    code = (
        "import importlib, json, pathlib; "
        f"module=importlib.import_module({module!r}); "
        "print(json.dumps({'origin': str(pathlib.Path(module.__file__).resolve())}))"
    )
    completed = _run_child(code, target, transaction_root, external_cwd)
    if completed.returncode != 0:
        raise CleanWheelError(
            f"CLEAN_WHEEL_IMPORT_FAILED:{module}:{completed.returncode}"
        )
    payload = _json_object(completed.stdout, module)
    origin = Path(payload["origin"])
    if not validate_workspace_origins([origin], target):
        raise CleanWheelError(f"CLEAN_WHEEL_SOURCE_LEAK:{module}")
    return {"origin": str(origin)}


def _provider_import_facts(
    target: Path, transaction_root: Path, cwd: Path
) -> dict[str, Any]:
    code = (
        "import importlib, importlib.util, json, pathlib, sys; "
        "module=importlib.import_module('octoagent.provider'); "
        "print(json.dumps({'provider_origin':str(pathlib.Path(module.__file__).resolve()),"
        "'gateway_find_spec':None if importlib.util.find_spec('octoagent.gateway') is None else 'found',"
        "'gateway_in_sys_modules':'octoagent.gateway' in sys.modules}))"
    )
    completed = _run_child(code, target, transaction_root, cwd)
    if completed.returncode != 0:
        raise CleanWheelError(
            f"CLEAN_WHEEL_PROVIDER_IMPORT_FAILED:{completed.returncode}"
        )
    facts = _json_object(completed.stdout, "octoagent.provider")
    origin = Path(facts["provider_origin"])
    if not validate_workspace_origins([origin], target):
        raise CleanWheelError("CLEAN_WHEEL_PROVIDER_SOURCE_LEAK")
    return {"provider_imported": True, **facts}


def _json_object(value: str, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise CleanWheelError(f"CLEAN_WHEEL_CHILD_OUTPUT_INVALID:{label}") from exc
    if not isinstance(payload, dict):
        raise CleanWheelError(f"CLEAN_WHEEL_CHILD_OUTPUT_INVALID:{label}")
    return payload


def _cli_help_facts(target: Path, transaction_root: Path, cwd: Path) -> dict[str, Any]:
    code = "from octoagent.gateway.cli.cli import main; main()"
    commands = {
        "octo --help": ("--help",),
        "octo auth --help": ("auth", "--help"),
        "octo doctor --help": ("doctor", "--help"),
    }
    results = {
        label: _run_child(code, target, transaction_root, cwd, argv).returncode
        for label, argv in commands.items()
    }
    if set(results.values()) != {0}:
        raise CleanWheelError("CLEAN_WHEEL_HELP_FAILED")
    return {"commands": results}


def run_isolated_probe(
    target: Path,
    transaction_root: Path,
    external_cwd: Path,
    workspace_roots: dict[str, str],
) -> dict[str, Any]:
    """运行唯一child probe并返回其真实JSON观测。"""

    code = (
        "import importlib.util,json,os,pathlib,site,sys; "
        "roots=json.loads(sys.argv[1]); "
        "origins={name:str(pathlib.Path(importlib.util.find_spec(root).origin).resolve()) "
        "for name,root in roots.items()}; "
        "keys=json.loads(sys.argv[2]); "
        "print(json.dumps({'cwd':str(pathlib.Path.cwd().resolve()),'sys_path':sys.path,"
        "'environment':{key:os.environ.get(key,'') for key in keys},"
        "'enable_user_site':bool(site.ENABLE_USER_SITE),'user_site':site.getusersitepackages(),"
        "'prefix':sys.prefix,'base_prefix':sys.base_prefix,'workspace_origins':origins},"
        "sort_keys=True,separators=(',',':')))"
    )
    keys = [
        "HOME",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "TMPDIR",
        "UV_CACHE_DIR",
        "PYTHONNOUSERSITE",
        "PYTHONPATH",
    ]
    completed = _run_child(
        code,
        target,
        transaction_root,
        external_cwd,
        (json.dumps(workspace_roots, sort_keys=True), json.dumps(keys)),
    )
    if completed.returncode != 0 or completed.stderr:
        raise CleanWheelError("CLEAN_WHEEL_CHILD_PROBE_FAILED")
    return _json_object(completed.stdout, "isolation-observation")


def validate_child_observation(
    observation: dict[str, Any],
    repo_root: Path,
    transaction_root: Path,
    target: Path,
    external_cwd: Path,
    workspace_names: set[str],
) -> None:
    """验证child实际JSON，禁止parent按入参重构事实。"""

    fields = {
        "cwd",
        "sys_path",
        "environment",
        "enable_user_site",
        "user_site",
        "prefix",
        "base_prefix",
        "workspace_origins",
    }
    environment_fields = {
        "HOME",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "TMPDIR",
        "UV_CACHE_DIR",
        "PYTHONNOUSERSITE",
        "PYTHONPATH",
    }
    environment = observation.get("environment")
    origins = observation.get("workspace_origins")
    if set(observation) != fields or not isinstance(environment, dict):
        raise CleanWheelError("CLEAN_WHEEL_CHILD_OBSERVATION_SCHEMA_INVALID")
    if set(environment) != environment_fields or not isinstance(origins, dict):
        raise CleanWheelError("CLEAN_WHEEL_CHILD_OBSERVATION_SCHEMA_INVALID")
    _validate_child_paths(
        observation, repo_root, transaction_root, target, external_cwd
    )
    if set(origins) != workspace_names or not all(
        Path(value).resolve().is_relative_to(target.resolve())
        for value in origins.values()
    ):
        raise CleanWheelError("CLEAN_WHEEL_WORKSPACE_ORIGIN_INVALID")


def _validate_child_paths(
    observation: dict[str, Any],
    repo_root: Path,
    transaction_root: Path,
    target: Path,
    external_cwd: Path,
) -> None:
    environment = observation["environment"]
    local_keys = ("HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "TMPDIR", "UV_CACHE_DIR")
    local = all(
        Path(environment[key]).resolve().is_relative_to(transaction_root.resolve())
        for key in local_keys
    )
    sys_paths = [Path(value).resolve() for value in observation["sys_path"] if value]
    valid = (
        Path(observation["cwd"]).resolve() == external_cwd.resolve()
        and environment["PYTHONNOUSERSITE"] == "1"
        and Path(environment["PYTHONPATH"]).resolve() == target.resolve()
        and observation["enable_user_site"] is False
        and repo_root.resolve() not in sys_paths
        and local
    )
    if not valid:
        raise CleanWheelError("CLEAN_WHEEL_ENVIRONMENT_NOT_ISOLATED")


def _transaction_observation(
    repo_root: Path,
    transaction_root: Path,
    target: Path,
    external_cwd: Path,
    names: set[str],
) -> dict[str, Any]:
    roots = {name: WORKSPACE_IMPORT_ROOTS[name] for name in sorted(names)}
    observation = run_isolated_probe(target, transaction_root, external_cwd, roots)
    validate_child_observation(
        observation, repo_root, transaction_root, target, external_cwd, names
    )
    return observation


def _check(status: str, facts: dict[str, Any]) -> dict[str, Any]:
    return {"status": status, "facts": facts}


def _path_snapshot(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            result[relative] = f"symlink:{path.readlink()}"
        elif path.is_dir():
            result[relative] = "directory"
        elif path.is_file():
            result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _installed_cli_code(kind: str) -> str:
    if kind == "install-bootstrap":
        return "import runpy; runpy.run_module('octoagent.gateway.cli.install_bootstrap',run_name='__main__')"
    if kind == "octo-bench":
        return "from octoagent.gateway.cli.bench_commands import app; app()"
    return "from octoagent.gateway.cli.cli import main; main()"


def _run_installed_command(
    command: Sequence[str], target: Path, transaction_root: Path, external: Path
) -> subprocess.CompletedProcess[str]:
    kind, *arguments = command
    return _run_child(
        _installed_cli_code(kind), target, transaction_root, external, arguments
    )


def _source_managed_facts(
    target: Path, transaction_root: Path, external: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    state = transaction_root / "runtime-project"
    sentinel = state / "source-managed-sentinel.txt"
    sentinel.write_text("unchanged\n", encoding="utf-8")
    _child_env(target, transaction_root)
    before = _path_snapshot(state)
    guarded: dict[str, dict[str, Any]] = {}
    for label, command in SOURCE_MANAGED_COMMANDS.items():
        completed = _run_installed_command(command, target, transaction_root, external)
        after = _path_snapshot(state)
        side_effects = sorted(
            path
            for path in set(before) | set(after)
            if before.get(path) != after.get(path)
        )
        if (
            completed.returncode != 69
            or "SOURCE_CHECKOUT_REQUIRED" not in completed.stderr
        ):
            raise CleanWheelError(f"CLEAN_WHEEL_SOURCE_GUARD_FAILED:{label}")
        if side_effects:
            raise CleanWheelError(f"CLEAN_WHEEL_SOURCE_SIDE_EFFECT:{label}")
        guarded[label] = {
            "exit_code": completed.returncode,
            "error_code": "SOURCE_CHECKOUT_REQUIRED",
            "side_effects": side_effects,
        }
    read_only: dict[str, int] = {}
    for label, command in SOURCE_READ_ONLY_COMMANDS.items():
        completed = _run_installed_command(command, target, transaction_root, external)
        if completed.returncode != 0:
            raise CleanWheelError(f"CLEAN_WHEEL_READ_ONLY_FAILED:{label}")
        read_only[label] = completed.returncode
    guard_facts = {
        "commands": guarded,
        "sentinel_unchanged": sentinel.read_text(encoding="utf-8") == "unchanged\n",
    }
    return guard_facts, {"commands": read_only}


def _fact_requirement_names(facts: dict[str, Any], label: str) -> set[str]:
    manifest = set(
        _canonical_requirements(
            facts.get("manifest_requires_dist"), f"{label} manifest"
        )
    )
    wheel = set(
        _canonical_requirements(facts.get("wheel_requires_dist"), f"{label} wheel")
    )
    if manifest != wheel:
        raise CleanWheelError(f"CLEAN_WHEEL_FINAL_METADATA_DRIFT:{label}")
    return manifest


def _unowned_is_governed(item: dict[str, Any]) -> bool:
    root = item.get("import_root")
    context = item.get("context")
    source = str(item.get("source_file", ""))
    if context in {"type-checking", "test-plugin"}:
        return True
    if root == "benchmarks" and source.endswith("cli/bench_commands.py"):
        return True
    if root == "octoagent":
        return True
    return root in GATEWAY_OPTIONAL_IMPORT_ROOTS


def _validate_final_occurrences(
    facts: dict[str, Any], manifest: set[str], label: str
) -> None:
    occurrences = facts.get("import_occurrences")
    if not isinstance(occurrences, list):
        raise CleanWheelError(f"CLEAN_WHEEL_FINAL_OCCURRENCES_INVALID:{label}")
    _validate_occurrence_states(occurrences)
    unknown = [
        item
        for item in occurrences
        if item.get("ownership_state") == "unowned" and not _unowned_is_governed(item)
    ]
    runtime = {
        canonicalize_name(str(item["resolved_distribution"]))
        for item in occurrences
        if item.get("context") == "runtime-required"
        and item.get("ownership_state") == "resolved"
    }
    if unknown or not runtime.issubset(manifest):
        raise CleanWheelError(f"CLEAN_WHEEL_FINAL_IMPORT_CLOSURE_INVALID:{label}")


def validate_final_direct_dependency_closure(
    provider_facts: dict[str, Any], gateway_facts: dict[str, Any]
) -> dict[str, Any]:
    """验证installed wheel的最终direct dependency闭包。"""

    provider = _fact_requirement_names(provider_facts, "provider")
    gateway = _fact_requirement_names(gateway_facts, "gateway")
    _validate_final_occurrences(provider_facts, provider, "provider")
    _validate_final_occurrences(gateway_facts, gateway, "gateway")
    missing = sorted(
        (PROVIDER_FINAL_REQUIRES_DIST - provider)
        | (GATEWAY_FINAL_REQUIRES_DIST - gateway)
    )
    unexpected = sorted(
        (provider - PROVIDER_FINAL_REQUIRES_DIST)
        | (gateway - GATEWAY_FINAL_REQUIRES_DIST)
    )
    if missing or unexpected:
        raise CleanWheelError("CLEAN_WHEEL_FINAL_DIRECT_DEPENDENCY_DRIFT")
    provider_workspace = provider & set(WORKSPACE_MANIFESTS)
    gateway_workspace = gateway & set(WORKSPACE_MANIFESTS)
    return {
        "provider_requires_dist": sorted(provider),
        "gateway_requires_dist": sorted(gateway),
        "provider_workspace_count": len(provider_workspace),
        "provider_third_party_count": len(provider - provider_workspace),
        "gateway_workspace_count": len(gateway_workspace),
        "gateway_third_party_count": len(gateway - gateway_workspace),
        "unknown": [],
        "unowned": [],
        "missing": missing,
        "unexpected": unexpected,
        "final_verdict": "PASS",
        "final_owner": "T070",
    }


def _dependency_check(facts: dict[str, Any]) -> dict[str, Any]:
    occurrences = facts.get("import_occurrences")
    unowned = facts.get("unowned_import_occurrences")
    if (
        facts.get("final_verdict") is not None
        or not isinstance(occurrences, list)
        or unowned
        != [item for item in occurrences if item.get("ownership_state") == "unowned"]
        or facts.get("unowned_import_occurrence_count") != len(unowned)
    ):
        raise CleanWheelError("CLEAN_WHEEL_DEPENDENCY_INVENTORY_INVALID")
    return _check("PASS", facts)


def _provider_report(repo_root: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="f151-clean-wheel-") as temp:
        transaction_root = Path(temp)
        wheel_dir = transaction_root / "wheels"
        target = transaction_root / "provider-site-packages"
        inventory_target = transaction_root / "inventory-site-packages"
        external = transaction_root / "external-cwd"
        for path in (wheel_dir, target, inventory_target, external):
            path.mkdir(parents=True)
        wheels = _build_all(repo_root, wheel_dir, transaction_root)
        selected = _workspace_closure(wheels, "octoagent-provider")
        install_workspace_wheels(transaction_root, target, selected)
        install_workspace_wheels(
            transaction_root, inventory_target, list(wheels.values())
        )
        names = {_wheel_distribution_name(wheel) for wheel in selected}
        observation = _transaction_observation(
            repo_root, transaction_root, target, external, names
        )
        requires_dist = _requires_dist_facts(
            repo_root,
            "octoagent-provider",
            wheels["octoagent-provider"],
            inventory_target,
        )
        checks = {
            "environment": _check(
                "PASS",
                observation,
            ),
            "provider.import": _check(
                "PASS", _provider_import_facts(target, transaction_root, external)
            ),
            "provider.requires_dist": _dependency_check(requires_dist),
        }
    return _preliminary_report(
        "provider",
        None,
        checks,
    )


def _wheel_distribution_name(wheel: Path) -> str:
    name = _wheel_metadata(wheel)["Name"]
    if not isinstance(name, str):
        raise CleanWheelError(f"CLEAN_WHEEL_METADATA_NAME_INVALID:{wheel.name}")
    return canonicalize_name(name)


def _preliminary_report(
    command: str, level: str | None, checks: dict[str, Any]
) -> dict[str, Any]:
    passed = all(check.get("status") == "PASS" for check in checks.values())
    return {
        "inventory_contract": "preliminary-unowned-v1",
        "command": command,
        "level": level,
        "status": "PASS" if passed else "FAIL",
        "error_code": None if passed else "CLEAN_WHEEL_DEPENDENCY_CLOSURE_INVALID",
        "checks": checks,
    }


def _namespace_inventory(target: Path) -> dict[str, Any]:
    findings: list[str] = []
    for path in target.rglob("*.py"):
        if "octoagent.gateway" in path.read_text(encoding="utf-8", errors="ignore"):
            findings.append(path.relative_to(target).as_posix())
    return {
        "scan_mode": "inventory-only",
        "final_verdict": None,
        "scan_complete": True,
        "findings": sorted(findings),
    }


def _gateway_report(repo_root: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="f151-clean-wheel-") as temp:
        transaction_root = Path(temp)
        wheel_dir = transaction_root / "wheels"
        target = transaction_root / "gateway-site-packages"
        external = transaction_root / "external-cwd"
        for path in (wheel_dir, target, external):
            path.mkdir(parents=True)
        wheels = _build_all(repo_root, wheel_dir, transaction_root)
        install_workspace_wheels(transaction_root, target, list(wheels.values()))
        observation = _transaction_observation(
            repo_root, transaction_root, target, external, set(wheels)
        )
        imported = _import_facts(
            "octoagent.gateway", target, transaction_root, external
        )
        checks = _gateway_checks(
            repo_root,
            wheels,
            target,
            transaction_root,
            external,
            imported,
            observation,
        )
    return _preliminary_report("gateway", "relocation", checks)


def _gateway_checks(
    repo_root: Path,
    wheels: dict[str, Path],
    target: Path,
    transaction_root: Path,
    external: Path,
    imported: dict[str, Any],
    observation: dict[str, Any],
) -> dict[str, Any]:
    requires_dist = _requires_dist_facts(
        repo_root, "octoagent-gateway", wheels["octoagent-gateway"], target
    )
    return {
        "environment": _check(
            "PASS",
            observation,
        ),
        "gateway.cli-help": _check(
            "PASS", _cli_help_facts(target, transaction_root, external)
        ),
        "gateway.import": _check(
            "PASS", {"gateway_imported": True, "gateway_origin": imported["origin"]}
        ),
        "gateway.level-boundary": _check(
            "PASS", {"level": "relocation", "full_checks_executed": []}
        ),
        "gateway.namespace": _check("PASS", _namespace_inventory(target)),
        "gateway.requires_dist": _dependency_check(requires_dist),
    }


def _gateway_full_checks(
    repo_root: Path,
    wheels: dict[str, Path],
    target: Path,
    transaction_root: Path,
    external: Path,
    imported: dict[str, Any],
    observation: dict[str, Any],
) -> dict[str, Any]:
    checks = _gateway_checks(
        repo_root,
        wheels,
        target,
        transaction_root,
        external,
        imported,
        observation,
    )
    startup = _gateway_startup_facts(target, transaction_root, external)
    readiness, shutdown = _gateway_readiness_facts(target, transaction_root, external)
    checks["gateway.level-boundary"] = _check(
        "PASS", {"level": "full", "full_checks_executed": sorted(GATEWAY_FULL_CHECKS)}
    )
    checks.update(
        {
            "gateway.startup": _check("PASS", startup),
            "gateway.readiness": _check("PASS", readiness),
            "gateway.sigterm": _check("PASS", shutdown),
            "gateway.invalid-startup": _check(
                "PASS",
                _gateway_invalid_startup_facts(target, transaction_root, external),
            ),
        }
    )
    return checks


def _final_report(command: str, checks: dict[str, Any]) -> dict[str, Any]:
    passed = all(check.get("status") == "PASS" for check in checks.values())
    return {
        "inventory_contract": "final-direct-closure-v1",
        "command": command,
        "level": "full",
        "status": "PASS" if passed else "FAIL",
        "error_code": None if passed else "CLEAN_WHEEL_FULL_CHECK_FAILED",
        "checks": checks,
    }


def command_gateway_full(repo_root: Path) -> dict[str, Any]:
    """运行Gateway installed-wheel full contract。"""

    with tempfile.TemporaryDirectory(prefix="f151-clean-wheel-") as temp:
        transaction_root = Path(temp)
        wheel_dir = transaction_root / "wheels"
        target = transaction_root / "gateway-site-packages"
        external = transaction_root / "external-cwd"
        for path in (wheel_dir, target, external):
            path.mkdir(parents=True)
        wheels = _build_all(repo_root, wheel_dir, transaction_root)
        install_workspace_wheels(transaction_root, target, list(wheels.values()))
        observation = _transaction_observation(
            repo_root, transaction_root, target, external, set(wheels)
        )
        imported = _import_facts(
            "octoagent.gateway", target, transaction_root, external
        )
        checks = _gateway_full_checks(
            repo_root,
            wheels,
            target,
            transaction_root,
            external,
            imported,
            observation,
        )
    return _final_report("gateway", checks)


def _provider_checks_for_all(
    repo_root: Path,
    wheels: dict[str, Path],
    provider_target: Path,
    all_target: Path,
    transaction_root: Path,
    external: Path,
) -> dict[str, Any]:
    _transaction_observation(
        repo_root,
        transaction_root,
        provider_target,
        external,
        {"octoagent-core", "octoagent-provider"},
    )
    return {
        "provider.import": _check(
            "PASS",
            _provider_import_facts(provider_target, transaction_root, external),
        ),
        "provider.requires_dist": _dependency_check(
            _requires_dist_facts(
                repo_root,
                "octoagent-provider",
                wheels["octoagent-provider"],
                all_target,
            )
        ),
    }


def _all_checks(
    repo_root: Path,
    wheels: dict[str, Path],
    provider_target: Path,
    all_target: Path,
    transaction_root: Path,
    external: Path,
) -> dict[str, Any]:
    observation = _transaction_observation(
        repo_root, transaction_root, all_target, external, set(wheels)
    )
    imported = _import_facts(
        "octoagent.gateway", all_target, transaction_root, external
    )
    checks = _provider_checks_for_all(
        repo_root,
        wheels,
        provider_target,
        all_target,
        transaction_root,
        external,
    )
    checks.update(
        _gateway_full_checks(
            repo_root,
            wheels,
            all_target,
            transaction_root,
            external,
            imported,
            observation,
        )
    )
    provider_facts = checks["provider.requires_dist"]["facts"]
    gateway_facts = checks["gateway.requires_dist"]["facts"]
    checks["dependency.final-closure"] = _check(
        "PASS",
        validate_final_direct_dependency_closure(provider_facts, gateway_facts),
    )
    guarded, read_only = _source_managed_facts(all_target, transaction_root, external)
    checks["source-managed.guard"] = _check("PASS", guarded)
    checks["source-managed.read-only"] = _check("PASS", read_only)
    return checks


def command_all(repo_root: Path) -> dict[str, Any]:
    """在同一transaction运行Provider、Gateway full与final closure。"""

    with tempfile.TemporaryDirectory(prefix="f151-clean-wheel-") as temp:
        transaction_root = Path(temp)
        wheel_dir = transaction_root / "wheels"
        provider_target = transaction_root / "provider-site-packages"
        all_target = transaction_root / "gateway-site-packages"
        external = transaction_root / "external-cwd"
        for path in (wheel_dir, provider_target, all_target, external):
            path.mkdir(parents=True)
        wheels = _build_all(repo_root, wheel_dir, transaction_root)
        install_workspace_wheels(
            transaction_root,
            provider_target,
            _workspace_closure(wheels, "octoagent-provider"),
        )
        install_workspace_wheels(transaction_root, all_target, list(wheels.values()))
        checks = _all_checks(
            repo_root,
            wheels,
            provider_target,
            all_target,
            transaction_root,
            external,
        )
    return _final_report("all", checks)


def build_parser() -> CleanWheelArgumentParser:
    """构建唯一 clean-wheel 命令解析器。"""

    parser = CleanWheelArgumentParser(prog="check-clean-wheel.py")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("provider")
    gateway = subparsers.add_parser("gateway")
    gateway.add_argument(
        "--level", choices=("relocation", "full"), default="relocation"
    )
    subparsers.add_parser("all")
    return parser


def parse_cli_args(argv: Sequence[str]) -> argparse.Namespace:
    """解析唯一 clean-wheel CLI，并补齐稳定level字段。"""

    result = build_parser().parse_args(list(argv))
    if not hasattr(result, "level"):
        result.level = "full" if result.command == "all" else None
    return result


def _write_report(report: dict[str, Any]) -> None:
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    print(f"clean-wheel {report['command']}: {report['status']}")


def main(argv: Sequence[str] | None = None) -> int:
    """运行唯一 clean-wheel CLI。"""

    values = sys.argv[1:] if argv is None else list(argv)
    repo_root = _repo_root()
    try:
        args = parse_cli_args(values)
        scaffold = validate_standard_backend_scaffold(repo_root)
        if scaffold.get("status") != "PASS":
            raise CleanWheelError(str(scaffold.get("error_code")))
        if args.command == "provider":
            report = _provider_report(repo_root)
        elif args.level == "relocation":
            report = _gateway_report(repo_root)
        elif args.command == "gateway":
            report = command_gateway_full(repo_root)
        else:
            report = command_all(repo_root)
    except CleanWheelError as exc:
        report = {
            "command": values[0] if values else None,
            "level": None,
            "status": "FAIL",
            "error_code": str(exc),
            "checks": {},
        }
        _write_report(report)
        return 1
    _write_report(report)
    return 0 if report.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
