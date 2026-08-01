"""F154 生产实现前必须先通过的精确 architecture authority 合同。"""

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

ORACLE = "F154_ARCHITECTURE_AUTHORITY_MISSING"
REPO_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_REL = Path(
    ".specify/features/154-healthkit-read-only-vertical-slice/"
    "inventories/architecture-authority.v1.json"
)
CHECKER_REL = Path("repo-scripts/check-runtime-architecture.py")


def _load_checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("f154_runtime_checker", REPO_ROOT / CHECKER_REL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _canonical_scope_sha256(inventory: dict[str, Any]) -> str:
    payload = {key: value for key, value in inventory.items() if key != "approved_scope_sha256"}
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
        json.dumps(inventory, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _prepare_repo(repo: Path, inventory: dict[str, Any]) -> None:
    _write_inventory(repo, inventory)
    for record in inventory["production_paths"]:
        if not str(record["state"]).startswith("existing"):
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
    assert validator(repo, "F154"), f"{ORACLE}: mutated authority was accepted"


def test_f154_exact_architecture_authority_is_fail_closed(tmp_path: Path) -> None:
    validator = _validator()
    inventory = json.loads((REPO_ROOT / INVENTORY_REL).read_text(encoding="utf-8"))

    assert inventory["approved_scope_sha256"] == _canonical_scope_sha256(inventory)
    assert validator(REPO_ROOT, "F154") == [], f"{ORACLE}: canonical authority rejected"
    assert inventory["precondition"] == {
        "feature_id": "F153",
        "required_gate": "GATE_VERIFY",
        "current_state": True,
    }
    assert inventory["allowed_health_types"] == ["stepCount", "sleepAnalysis"]
    assert inventory["allowed_capabilities"] == [
        "health.review.submit",
        "health.analysis.run",
        "health.source.delete",
    ]
    production_roles = {record["path"]: record["role"] for record in inventory["production_paths"]}
    assert (
        production_roles.get(
            "octoagent/apps/gateway/src/octoagent/gateway/services/mobile_device_access.py"
        )
        == "existing_mobile_health_path_boundary"
    ), f"{ORACLE}: mobile health route cannot pass the existing Host/path middleware"
    assert (
        production_roles.get(
            "octoagent/packages/core/src/octoagent/core/store/privacy_ingestion_store.py"
        )
        == "existing_review_packet_read_boundary"
    ), f"{ORACLE}: analysis cannot consume the stored review through the single F152 store"
    assert (
        production_roles.get("octoagent/apps/ios/OctoAgent/DeviceTrust/DeviceTrustClient.swift")
        == "single_health_signed_transport_client"
    ), f"{ORACLE}: health cannot create a second signed URLSession client"
    assert (
        production_roles.get(
            "octoagent/apps/gateway/src/octoagent/gateway/services/device_trust.py"
        )
        == "existing_health_capability_issuance"
    ), f"{ORACLE}: health capabilities must come from the existing device token issuer"
    assert (
        production_roles.get("octoagent/apps/ios/OctoAgent/App/RegistrationView.swift")
        == "connected_health_transport_composition"
    ), f"{ORACLE}: connected UI must inject the existing device transport"

    cases: tuple[Callable[[dict[str, Any]], None], ...] = (
        lambda item: item.update(feature_id="F155"),
        lambda item: item.update(authority_status="DESIGN_TASKS_APPROVED_IMPLEMENT_LOCKED"),
        lambda item: item["precondition"].update(current_state=False),
        lambda item: item["production_paths"].append(copy.deepcopy(item["production_paths"][0])),
        lambda item: item["production_paths"][0].update(
            path="octoagent/**/health.py", state="planned_locked"
        ),
        lambda item: item["production_paths"][0].update(owner="frontend"),
        lambda item: item["production_paths"].append(
            {
                "path": "octoagent/apps/ios/OctoAgent/EventKit/HealthBridge.swift",
                "state": "planned_locked",
                "owner": "ios",
                "role": "eventkit_escape",
            }
        ),
        lambda item: item["production_paths"].append(
            {
                "path": "octoagent/apps/ios/OctoAgent/WebView/HealthPage.swift",
                "state": "planned_locked",
                "owner": "ios",
                "role": "webview_escape",
            }
        ),
        lambda item: item["allowed_health_types"].append("heartRate"),
        lambda item: item["allowed_capabilities"].append("health.*"),
        lambda item: item["test_paths"].remove(
            "octoagent/tests/gate/test_f154_architecture_authority.py"
        ),
        lambda item: item["artifact_paths"].append(".specify/features/999-escape/spec.md"),
        lambda item: item.update(unreviewed_escape=True),
    )
    for ordinal, mutate in enumerate(cases):
        _rejects(validator, tmp_path / str(ordinal), inventory, mutate)
