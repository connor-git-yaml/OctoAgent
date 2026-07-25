"""F149 Web 实际消费 REST/snapshot/TaskDetail 合同。"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime

import pytest
from octoagent.gateway.main import create_app
from octoagent.gateway.routes.f149_web_contract import (
    F149_REST_ENDPOINTS,
    F149_SNAPSHOT_RESOURCE_NAMES,
    F149TaskDetailResponse,
    decode_f149_snapshot,
    export_f149_rest_openapi,
)
from pydantic import ValidationError

ORACLE = "F149_REST_CONTRACT_MISSING"

EXPECTED_RESOURCES = (
    "config",
    "project_selector",
    "sessions",
    "agent_profiles",
    "worker_profiles",
    "owner_profile",
    "bootstrap_session",
    "context_continuity",
    "capability_pack",
    "skill_governance",
    "mcp_provider_catalog",
    "setup_governance",
    "delegation",
    "diagnostics",
    "retrieval_platform",
    "memory",
)

EXPECTED_ENDPOINTS = {
    ("GET", "/api/control/snapshot"),
    *{
        ("GET", f"/api/control/resources/{name}")
        for name in (
            "config",
            "project-selector",
            "sessions",
            "agent-profiles",
            "worker-profiles",
            "owner-profile",
            "bootstrap-session",
            "context-frames",
            "capability-pack",
            "skill-governance",
            "mcp-provider-catalog",
            "setup-governance",
            "delegation",
            "automation",
            "diagnostics",
            "retrieval-platform",
            "memory",
            "remote-access",
        )
    },
    ("GET", "/api/control/resources/worker-profile-revisions/{profile_id}"),
    ("GET", "/api/tasks"),
    ("GET", "/api/tasks/{task_id}"),
    ("GET", "/api/approvals"),
    ("GET", "/api/approval-overrides"),
    ("DELETE", "/api/approval-overrides/{agent_runtime_id}/{tool_name}"),
    ("GET", "/api/approval-center/summary"),
    ("GET", "/api/memory/candidates"),
    ("POST", "/api/memory/candidates/{candidate_id}/promote"),
    ("POST", "/api/memory/candidates/{candidate_id}/discard"),
    ("PUT", "/api/memory/candidates/bulk_discard"),
    ("GET", "/api/consolidation/candidates"),
    ("POST", "/api/consolidation/candidates/{candidate_id}/accept"),
    ("POST", "/api/consolidation/candidates/{candidate_id}/reject"),
    ("PUT", "/api/consolidation/candidates/bulk_reject"),
    ("GET", "/api/behavior/compact/candidates"),
    ("POST", "/api/behavior/compact/candidates/{candidate_id}/accept"),
    ("POST", "/api/behavior/compact/candidates/{candidate_id}/reject"),
    ("GET", "/api/behavior-versions/files"),
    ("GET", "/api/behavior-versions/versions"),
    ("GET", "/api/behavior-versions/diff"),
    ("GET", "/api/skills"),
    ("GET", "/api/skills/{name}"),
    ("POST", "/api/skills"),
    ("DELETE", "/api/skills/{name}"),
    ("GET", "/api/files/tasks"),
    ("GET", "/api/files/tasks/{task_id}/logical-files"),
    ("GET", "/api/files/tasks/{task_id}/diff"),
    ("GET", "/api/files/tasks/{task_id}/versions"),
    ("GET", "/api/workspace-git/projects"),
    ("GET", "/api/workspace-git/history"),
    ("GET", "/api/workspace-git/commit"),
    ("GET", "/api/workspace-git/blame"),
    ("GET", "/api/workspace-git/diff"),
    ("POST", "/api/workspace-git/rollback"),
    ("POST", "/api/workspace-git/rollback/{request_id}/approve"),
    ("POST", "/api/workspace-git/rollback/{request_id}/reject"),
    ("GET", "/api/operator/inbox"),
    ("POST", "/api/operator/actions"),
    ("GET", "/api/ops/recovery"),
    ("GET", "/api/ops/update/status"),
    ("POST", "/api/ops/backup/create"),
    ("POST", "/api/ops/update/dry-run"),
    ("POST", "/api/ops/update/apply"),
    ("POST", "/api/ops/restart"),
    ("POST", "/api/ops/verify"),
    ("POST", "/api/ops/export/chats"),
}


def _assert_contract(condition: bool, detail: str) -> None:
    assert condition, f"{ORACLE}: {detail}"


def _raw_resource(resource_type: str) -> dict[str, object]:
    return {
        "resource_type": resource_type,
        "resource_id": f"{resource_type}:current",
        "status": "ready",
        "metadata": {"extension": True},
    }


def _snapshot_payload() -> dict[str, object]:
    return {
        "status": "degraded",
        "contract_version": "1.0.0",
        "resources": {name: _raw_resource(name) for name in EXPECTED_RESOURCES},
        "registry": {
            "resource_type": "action_registry",
            "resource_id": "actions:registry",
            "actions": [],
        },
        "degraded_sections": ["memory"],
        "resource_errors": {
            "memory": {
                "code": "SNAPSHOT_SECTION_UNAVAILABLE",
                "error_type": "RuntimeError",
                "message": "unavailable",
            }
        },
        "generated_at": datetime(2026, 7, 25, tzinfo=UTC).isoformat(),
    }


def _response_schema(operation: dict[str, object]) -> dict[str, object]:
    responses = operation.get("responses")
    if not isinstance(responses, dict):
        return {}
    for status, response in sorted(responses.items()):
        if not str(status).startswith("2") or not isinstance(response, dict):
            continue
        content = response.get("content")
        if not isinstance(content, dict):
            continue
        application_json = content.get("application/json")
        if isinstance(application_json, dict):
            schema = application_json.get("schema")
            if isinstance(schema, dict):
                return schema
    return {}


def test_snapshot_contract_closes_envelope_and_resource_names() -> None:
    _assert_contract(
        F149_SNAPSHOT_RESOURCE_NAMES == EXPECTED_RESOURCES,
        "snapshot resource names drift",
    )
    payload = _snapshot_payload()
    try:
        snapshot = decode_f149_snapshot(payload)
    except (TypeError, ValueError, ValidationError) as exc:
        pytest.fail(f"{ORACLE}: snapshot decoder rejected valid envelope ({type(exc).__name__})")
    _assert_contract(snapshot.status == "degraded", "snapshot status missing")
    _assert_contract(snapshot.degraded_sections == ["memory"], "degraded sections missing")
    _assert_contract(set(snapshot.resources) == set(EXPECTED_RESOURCES), "raw resource map drift")
    for mutation in ("status", "resource"):
        invalid = deepcopy(payload)
        if mutation == "status":
            invalid.pop("status")
        else:
            invalid["resources"].pop("memory")  # type: ignore[union-attr]
        with pytest.raises(ValidationError):
            decode_f149_snapshot(invalid)


def test_resource_endpoint_manifest_exports_finite_response_schemas() -> None:
    _assert_contract(set(F149_REST_ENDPOINTS) == EXPECTED_ENDPOINTS, "endpoint manifest drift")
    exported = export_f149_rest_openapi(create_app())
    _assert_contract(
        exported == export_f149_rest_openapi(create_app()), "export is not deterministic"
    )
    missing: list[str] = []
    overwide: list[str] = []
    for method, path in sorted(EXPECTED_ENDPOINTS):
        path_item = exported.get("paths", {}).get(path, {})
        operation = path_item.get(method.lower(), {}) if isinstance(path_item, dict) else {}
        if not isinstance(operation, dict) or not operation:
            missing.append(f"{method} {path}")
            continue
        schema = _response_schema(operation)
        if not schema:
            missing.append(f"{method} {path}")
        elif schema.get("additionalProperties") is True:
            overwide.append(f"{method} {path}")
    _assert_contract(not missing, f"response schema missing={','.join(missing)}")
    _assert_contract(not overwide, f"response schema overwide={','.join(overwide)}")


def test_task_detail_response_keeps_only_named_raw_event_payload() -> None:
    schema = F149TaskDetailResponse.model_json_schema()
    properties = schema.get("properties", {})
    _assert_contract(set(properties) == {"task", "events", "artifacts"}, "task detail fields drift")
    rendered = str(schema)
    _assert_contract("F149RawEventPayload" in rendered, "event payload raw boundary unnamed")
    _assert_contract("Any" not in rendered, "task detail leaked Any")
