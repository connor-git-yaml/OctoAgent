"""F152 在修改生产代码前必须取得的精确 architecture authority 合同。"""

from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
import json
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ORACLE = "F152_ARCHITECTURE_AUTHORITY_MISSING"
REPO_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_REL = Path(
    ".specify/features/152-privacy-identity-ingestion-contract/"
    "inventories/architecture-authority.v1.json"
)
CHECKER_REL = Path("repo-scripts/check-runtime-architecture.py")
SCOPE_FIELDS = (
    "feature_id",
    "production_paths",
    "test_paths",
    "artifact_paths",
    "forbidden_prefixes",
    "forbidden_terms",
)
F152_PRODUCTION_FILES = (
    "octoagent/packages/core/src/octoagent/core/models/privacy_ingestion.py",
    "octoagent/packages/core/src/octoagent/core/privacy_ingestion.py",
    "octoagent/packages/core/src/octoagent/core/store/privacy_ingestion_store.py",
    "octoagent/packages/policy/src/octoagent/policy/privacy_ingestion_policy.py",
    "octoagent/packages/protocol/src/octoagent/protocol/privacy_ingestion.py",
)


def _load_checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("f152_runtime_checker", REPO_ROOT / CHECKER_REL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _canonical_scope_sha256(inventory: dict[str, Any]) -> str:
    payload = {field: inventory[field] for field in SCOPE_FIELDS}
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _validator() -> Callable[[Path, str], list[str]]:
    checker = _load_checker()
    validator = getattr(checker, "validate_feature_authority", None)
    if not callable(validator):
        pytest.fail(f"{ORACLE}: existing checker has no feature authority seam", pytrace=False)
    return validator


def _write_inventory(repo: Path, inventory: dict[str, Any]) -> None:
    path = repo / INVENTORY_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _prepare_repo(tmp_path: Path, inventory: dict[str, Any]) -> Path:
    repo = tmp_path / "repo"
    _write_inventory(repo, inventory)
    for record in inventory["production_paths"]:
        if record["state"] != "existing":
            continue
        source = REPO_ROOT / record["path"]
        target = repo / record["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    return repo


def _rejects(
    validator: Callable[[Path, str], list[str]],
    tmp_path: Path,
    baseline: dict[str, Any],
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    candidate = copy.deepcopy(baseline)
    mutate(candidate)
    candidate["approved_scope_sha256"] = _canonical_scope_sha256(candidate)
    repo = _prepare_repo(tmp_path, candidate)
    assert validator(repo, "F152"), f"{ORACLE}: mutated authority was accepted"


def test_f152_exact_architecture_authority_is_fail_closed(tmp_path: Path) -> None:
    validator = _validator()
    inventory = json.loads((REPO_ROOT / INVENTORY_REL).read_text(encoding="utf-8"))

    assert inventory["approved_scope_sha256"] == _canonical_scope_sha256(inventory)
    assert validator(REPO_ROOT, "F152") == []

    cases: tuple[Callable[[dict[str, Any]], None], ...] = (
        lambda item: item.update(feature_id="F153"),
        lambda item: item["production_paths"].append(copy.deepcopy(item["production_paths"][0])),
        lambda item: item["production_paths"][0].update(path="octoagent/**/privacy.py"),
        lambda item: item["production_paths"].append(
            {
                "path": "octoagent/ios/OctoAgent/App.swift",
                "state": "declared_new",
                "owner": "ios",
                "role": "early_ios_production",
            }
        ),
        lambda item: item["production_paths"].append(
            {
                "path": ("octoagent/apps/gateway/src/octoagent/gateway/routes/healthkit.py"),
                "state": "declared_new",
                "owner": "gateway",
                "role": "early_health_route",
            }
        ),
        lambda item: item["production_paths"].append(
            {
                "path": (
                    "octoagent/apps/gateway/src/octoagent/gateway/services/device_registry.py"
                ),
                "state": "declared_new",
                "owner": "gateway",
                "role": "second_device_registry",
            }
        ),
        lambda item: item["test_paths"].remove(
            "octoagent/tests/gate/test_f152_architecture_authority.py"
        ),
        lambda item: item.update(unreviewed_escape=True),
    )
    for ordinal, mutate in enumerate(cases):
        _rejects(validator, tmp_path / str(ordinal), inventory, mutate)


def _module_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def _definitions(path: Path) -> tuple[set[str], dict[str, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    classes = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
    functions = {
        node.name: node.end_lineno - node.lineno + 1
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    return classes, functions


def test_f152_layers_single_owners_and_complexity_stay_bounded() -> None:
    paths = [REPO_ROOT / relative for relative in F152_PRODUCTION_FILES]
    imports = {path: _module_imports(path) for path in paths}
    forbidden_edges = {
        paths[0]: ("octoagent.policy", "octoagent.protocol", "octoagent.gateway"),
        paths[1]: ("octoagent.policy", "octoagent.protocol", "octoagent.gateway"),
        paths[2]: ("octoagent.policy", "octoagent.protocol", "octoagent.gateway"),
        paths[3]: ("octoagent.protocol", "octoagent.gateway"),
        paths[4]: ("octoagent.policy", "octoagent.gateway"),
    }
    for path, prefixes in forbidden_edges.items():
        assert not any(
            imported.startswith(prefix) for imported in imports[path] for prefix in prefixes
        ), f"F152_IMPORT_DIRECTION_INVALID: {path}"

    class_owners: dict[str, list[Path]] = {}
    function_owners: dict[str, list[Path]] = {}
    for path in paths:
        classes, functions = _definitions(path)
        for name in classes:
            class_owners.setdefault(name, []).append(path)
        for name, physical_lines in functions.items():
            function_owners.setdefault(name, []).append(path)
            assert physical_lines <= 50, (
                f"F152_FUNCTION_SIZE_RATCHET_EXCEEDED: {path.name}:{name}:{physical_lines}"
            )

    assert class_owners["SqlitePrivacyIngestionStore"] == [paths[2]]
    assert class_owners["PrivacyAuditEvent"] == [paths[0]]
    assert function_owners["review_memory_candidate"] == [paths[3]]
    assert function_owners["privacy_contract_bundle"] == [paths[4]]

    combined = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    assert "MemoryStore" not in combined
    assert "memory_store" not in combined
    assert "except Exception" not in combined

    sqlite_source = (
        REPO_ROOT / "octoagent/packages/core/src/octoagent/core/store/sqlite_init.py"
    ).read_text(encoding="utf-8")
    for table in (
        "privacy_ingestion_audit",
        "privacy_review_bundles",
        "privacy_approved_packets",
        "privacy_analysis_results",
        "privacy_memory_candidates",
        "privacy_deletion_receipts",
    ):
        marker = f"CREATE TABLE IF NOT EXISTS {table}"
        assert sqlite_source.count(marker) == 1, f"F152_SCHEMA_OWNER_INVALID: {table}"
