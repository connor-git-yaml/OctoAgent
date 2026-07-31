"""F158 Milestone closure 的精确 Web 与运行真值生产权限合同。"""

from __future__ import annotations

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

ORACLE = "F158_ARCHITECTURE_AUTHORITY_MISSING"
REPO_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_REL = Path(
    ".specify/features/158-milestone-product-closure/inventories/architecture-authority.v1.json"
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


def _load_checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "f158_runtime_checker",
        REPO_ROOT / CHECKER_REL,
    )
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


def _prepare_repo(repo: Path, inventory: dict[str, Any]) -> None:
    _write_inventory(repo, inventory)
    for record in inventory["production_paths"]:
        if record["state"] != "existing":
            continue
        source = REPO_ROOT / record["path"]
        target = repo / record["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())


def _rejects(
    validator: Callable[[Path, str], list[str]],
    tmp_path: Path,
    baseline: dict[str, Any],
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    candidate = copy.deepcopy(baseline)
    mutate(candidate)
    candidate["approved_scope_sha256"] = _canonical_scope_sha256(candidate)
    repo = tmp_path / "repo"
    _prepare_repo(repo, candidate)
    assert validator(repo, "F158"), f"{ORACLE}: mutated authority was accepted"


def test_f158_exact_web_architecture_authority_is_fail_closed(tmp_path: Path) -> None:
    validator = _validator()
    inventory = json.loads((REPO_ROOT / INVENTORY_REL).read_text(encoding="utf-8"))

    assert inventory["approved_scope_sha256"] == _canonical_scope_sha256(inventory)
    assert validator(REPO_ROOT, "F158") == [], f"{ORACLE}: canonical authority rejected"
    production = {record["path"] for record in inventory["production_paths"]}
    assert production == {
        "octoagent/apps/gateway/src/octoagent/gateway/services/operations/doctor.py",
        "octoagent/apps/gateway/src/octoagent/gateway/services/task_service.py",
        "octoagent/frontend/src/components/shell/WorkbenchLayout.tsx",
        "octoagent/frontend/src/domains/settings/RemoteAccessSettings.css",
        "octoagent/frontend/src/domains/settings/RemoteAccessSettings.tsx",
        "octoagent/frontend/src/domains/settings/SettingsCenter.tsx",
        "octoagent/frontend/src/domains/settings/SettingsPage.tsx",
        "octoagent/frontend/src/main.tsx",
        "octoagent/frontend/src/styles/claude-surfaces.css",
        "octoagent/frontend/src/styles/claude-workbench.css",
        "octoagent/packages/provider/src/octoagent/provider/__init__.py",
        "octoagent/packages/provider/src/octoagent/provider/exceptions.py",
        "octoagent/packages/provider/src/octoagent/provider/fallback.py",
    }
    checker = _load_checker()
    checker.validate_f150_implementation_scope(REPO_ROOT, "origin/master")
    checker.validate_f150_implementation_scope(REPO_ROOT, "HEAD")
    doctor_path = "octoagent/apps/gateway/src/octoagent/gateway/services/operations/doctor.py"
    doctor_source = (REPO_ROOT / doctor_path).read_text(encoding="utf-8")
    stripped = checker._strip_f158_f150_overlay(doctor_path, doctor_source)
    assert "check_model_live" not in stripped
    assert "async def check_credential_expiry" not in stripped
    mutated_doctor = doctor_source.replace(
        "模型返回空响应",
        "模型返回空响应 drift",
        1,
    )
    with pytest.raises(
        checker.GateFailure,
        match="F158_F150_AUTHORITY_OVERLAP_INVALID",
    ):
        checker._strip_f158_f150_overlay(doctor_path, mutated_doctor)
    mutated_expiry = doctor_source.replace(
        "不代表远端授权可用",
        "远端授权可用",
        1,
    )
    with pytest.raises(
        checker.GateFailure,
        match="F158_F150_AUTHORITY_OVERLAP_INVALID",
    ):
        checker._strip_f158_f150_overlay(doctor_path, mutated_expiry)

    cases: tuple[Callable[[dict[str, Any]], None], ...] = (
        lambda item: item.update(feature_id="F159"),
        lambda item: item["production_paths"].append(copy.deepcopy(item["production_paths"][0])),
        lambda item: item["production_paths"][5].update(path="octoagent/frontend/src/**"),
        lambda item: item["production_paths"][0].update(owner="gateway"),
        lambda item: item["production_paths"].append(
            {
                "path": "octoagent/apps/gateway/src/octoagent/gateway/f158.py",
                "state": "declared_new",
                "owner": "frontend",
                "role": "backend_escape",
            }
        ),
        lambda item: item["production_paths"].append(
            {
                "path": "octoagent/apps/ios/OctoAgent/F158View.swift",
                "state": "declared_new",
                "owner": "frontend",
                "role": "ios_escape",
            }
        ),
        lambda item: item["test_paths"].remove(
            "octoagent/tests/gate/test_f158_architecture_authority.py"
        ),
        lambda item: item["artifact_paths"].append(".specify/features/999-escape/spec.md"),
        lambda item: item.update(unreviewed_escape=True),
    )
    for ordinal, mutate in enumerate(cases):
        _rejects(validator, tmp_path / str(ordinal), inventory, mutate)
