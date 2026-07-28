"""F153 生产实现前必须先通过的精确 architecture authority 合同。"""

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

ORACLE = "F153_ARCHITECTURE_AUTHORITY_MISSING"
REPO_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_REL = Path(
    ".specify/features/153-ios-device-trust-secure-transport/"
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
OVERLAP_DUPLICATES = {
    "octoagent/apps/gateway/src/octoagent/gateway/services/config/config_schema.py": (
        "    mobile_device_access: MobileDeviceAccessConfig = Field(\n",
    ),
    "octoagent/apps/gateway/src/octoagent/gateway/harness/octo_harness.py": (
        "        app.state.mobile_device_access_manifest = None\n",
    ),
    "octoagent/apps/gateway/src/octoagent/gateway/services/operations/doctor.py": (
        "        checks.append(await self.check_mobile_device_access())\n",
    ),
    "octoagent/apps/gateway/src/octoagent/gateway/main.py": (
        "    app.add_middleware(MobileDeviceAccessMiddleware)\n",
    ),
}


def _load_checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("f153_runtime_checker", REPO_ROOT / CHECKER_REL)
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
    assert validator(repo, "F153"), f"{ORACLE}: mutated authority was accepted"


def test_f153_exact_architecture_authority_is_fail_closed(tmp_path: Path) -> None:
    validator = _validator()
    checker = _load_checker()
    inventory = json.loads((REPO_ROOT / INVENTORY_REL).read_text(encoding="utf-8"))

    assert inventory["approved_scope_sha256"] == _canonical_scope_sha256(inventory)
    assert validator(REPO_ROOT, "F153") == [], f"{ORACLE}: canonical authority rejected"
    production_paths = {record["path"] for record in inventory["production_paths"]}
    assert set(OVERLAP_DUPLICATES) <= production_paths
    strip_overlay = getattr(checker, "_strip_f153_f150_overlay", None)
    assert callable(strip_overlay), f"{ORACLE}: missing F153/F150 overlap validator"
    for relative, (needle,) in OVERLAP_DUPLICATES.items():
        current = (REPO_ROOT / relative).read_text(encoding="utf-8")
        stripped = strip_overlay(relative, current)
        assert isinstance(stripped, str) and stripped
        assert needle.strip() not in stripped
        duplicate = current.replace(needle, needle + needle, 1)
        assert duplicate != current
        with pytest.raises(checker.GateFailure):
            strip_overlay(relative, duplicate)

    cases: tuple[Callable[[dict[str, Any]], None], ...] = (
        lambda item: item.update(feature_id="F154"),
        lambda item: item["production_paths"].append(copy.deepcopy(item["production_paths"][0])),
        lambda item: item["production_paths"][0].update(path="octoagent/**/device_trust.py"),
        lambda item: item["production_paths"][0].update(owner="frontend"),
        lambda item: item["production_paths"].append(
            {
                "path": "octoagent/frontend/src/deviceTrust.ts",
                "state": "declared_new",
                "owner": "gateway",
                "role": "mobile_auth_in_web",
            }
        ),
        lambda item: item["production_paths"].append(
            {
                "path": "octoagent/apps/ios/OctoAgent/HealthKit/HealthStore.swift",
                "state": "declared_new",
                "owner": "ios",
                "role": "early_healthkit",
            }
        ),
        lambda item: item["test_paths"].remove(
            "octoagent/tests/gate/test_f153_architecture_authority.py"
        ),
        lambda item: item["artifact_paths"].append(".specify/features/999-escape/spec.md"),
        lambda item: item.update(unreviewed_escape=True),
    )
    for ordinal, mutate in enumerate(cases):
        _rejects(validator, tmp_path / str(ordinal), inventory, mutate)
