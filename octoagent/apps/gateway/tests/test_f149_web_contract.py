"""F149 Web 实际消费 REST/snapshot/TaskDetail 合同。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from octoagent.core.models import (
    ActionRequestEnvelope,
    ControlPlaneActor,
    ControlPlaneSurface,
)
from octoagent.core.store import create_store_group
from octoagent.gateway.main import create_app
from octoagent.gateway.routes.f149_web_contract import (
    F149_REST_ENDPOINTS,
    F149_SNAPSHOT_RESOURCE_NAMES,
    F149TaskDetailResponse,
    decode_f149_snapshot,
    export_f149_rest_openapi,
)
from octoagent.gateway.services.control_plane import ControlPlaneService
from octoagent.gateway.services.control_plane import action_registry as action_registry_module
from octoagent.gateway.services.operations.project_migration import (
    ProjectWorkspaceMigrationService,
)
from octoagent.gateway.services.operations.telegram_pairing import TelegramStateStore
from octoagent.gateway.services.sse_hub import SSEHub
from pydantic import ValidationError
from ulid import ULID

ORACLE = "F149_REST_CONTRACT_MISSING"
ACTION_ORACLE = "F149_ACTION_CONTRACT_MISSING"

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

EXPECTED_F149_ACTION_IDS = (
    "agent_profile.update_resource_limits",
    "behavior.read_file",
    "behavior.write_file",
    "behavior.restore_version",
    "memory.consolidate",
    "mcp_provider.install",
    "mcp_provider.install_status",
)

F149_ACTION_SAMPLES: dict[str, tuple[dict[str, object], dict[str, object]]] = {
    "agent_profile.update_resource_limits": (
        {
            "target_type": "agent_profile",
            "profile_id": "profile-main",
            "resource_limits": {"max_steps": 20},
        },
        {
            "profile_id": "profile-main",
            "target_type": "agent_profile",
            "resource_limits": {"max_steps": 20},
        },
    ),
    "behavior.read_file": (
        {"file_path": "USER.md"},
        {"file_path": "USER.md", "content": "hello", "exists": True},
    ),
    "behavior.write_file": (
        {"file_id": "USER.md", "content": "hello"},
        {"file_id": "USER.md", "resolved_path": "/tmp/USER.md"},
    ),
    "behavior.restore_version": (
        {"file_id": "USER.md", "target_version": 1, "confirmed": True},
        {"file_id": "USER.md", "restored_from_version": 1},
    ),
    "memory.consolidate": (
        {"project_id": "project-main"},
        {
            "consolidated_count": 1,
            "skipped_count": 0,
            "errors": [],
            "model_alias": "main",
            "message": "已整理 1 条事实",
        },
    ),
    "mcp_provider.install": (
        {"install_source": "npm", "package_name": "@example/mcp"},
        {"task_id": "install-1", "server_id": "npm-example-mcp"},
    ),
    "mcp_provider.install_status": (
        {"task_id": "install-1"},
        {
            "task_id": "install-1",
            "status": "running",
            "progress_message": "installing",
            "error": None,
            "result": None,
        },
    ),
}


def _assert_contract(condition: bool, detail: str) -> None:
    assert condition, f"{ORACLE}: {detail}"


def _assert_action_contract(condition: bool, detail: str) -> None:
    assert condition, f"{ACTION_ORACLE}: {detail}"


async def _control_plane(tmp_path: Path) -> tuple[ControlPlaneService, Any]:
    store_group = await create_store_group(
        str(tmp_path / "gateway.db"),
        str(tmp_path / "artifacts"),
    )
    await ProjectWorkspaceMigrationService(
        project_root=tmp_path,
        store_group=store_group,
    ).ensure_default_project()
    return (
        ControlPlaneService(
            project_root=tmp_path,
            store_group=store_group,
            sse_hub=SSEHub(),
            telegram_state_store=TelegramStateStore(tmp_path),
        ),
        store_group,
    )


def _action_contract_api(control_plane: ControlPlaneService) -> tuple[Any, Any]:
    contract_getter = getattr(control_plane, "get_action_contracts", None)
    artifact_exporter = getattr(control_plane, "export_f149_action_contract", None)
    contract_type = getattr(action_registry_module, "ActionContractDefinition", None)
    _assert_action_contract(callable(contract_getter), "action contract getter missing")
    _assert_action_contract(callable(artifact_exporter), "action artifact exporter missing")
    _assert_action_contract(contract_type is not None, "ActionContractDefinition missing")
    return contract_getter, artifact_exporter


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


@pytest.mark.asyncio
async def test_action_registry_contracts_share_handler_validation_and_artifact(
    tmp_path: Path,
) -> None:
    control_plane, store_group = await _control_plane(tmp_path)
    try:
        contract_getter, artifact_exporter = _action_contract_api(control_plane)
        contracts = {item.action_id: item for item in contract_getter()}
        registry = {item.action_id: item for item in control_plane.get_action_registry().actions}
        artifact = artifact_exporter()
        artifact_actions = {item["action_id"]: item for item in artifact.get("actions", [])}
        _assert_action_contract(
            tuple(sorted(artifact_actions)) == tuple(sorted(EXPECTED_F149_ACTION_IDS)),
            "F149 action artifact set drift",
        )
        for action_id in EXPECTED_F149_ACTION_IDS:
            contract = contracts[action_id]
            params, result = F149_ACTION_SAMPLES[action_id]
            _assert_action_contract(
                contract.handler is control_plane._action_dispatch[action_id],
                f"{action_id} handler not sourced from contract",
            )
            _assert_action_contract(
                contract.definition == registry[action_id],
                f"{action_id} registry not sourced from contract",
            )
            _assert_action_contract(
                artifact_actions[action_id]["params_schema"] == contract.definition.params_schema,
                f"{action_id} params artifact drift",
            )
            _assert_action_contract(
                artifact_actions[action_id]["result_schema"] == contract.definition.result_schema,
                f"{action_id} result artifact drift",
            )
            contract.validate_params(params)
            contract.validate_result(result)
            with pytest.raises(ValidationError):
                contract.validate_params({})
    finally:
        await store_group.close()


@pytest.mark.asyncio
async def test_action_contract_rejects_invalid_params_before_owner(tmp_path: Path) -> None:
    control_plane, store_group = await _control_plane(tmp_path)
    try:
        _action_contract_api(control_plane)
        owner_called = False

        async def owner_must_not_run(
            request: ActionRequestEnvelope,
        ) -> object:
            del request
            nonlocal owner_called
            owner_called = True
            raise AssertionError("invalid params reached owner")

        original = control_plane._action_contract_by_id["mcp_provider.install"]
        control_plane._action_contract_by_id["mcp_provider.install"] = replace(
            original,
            handler=owner_must_not_run,
        )
        result = await control_plane.execute_action(
            ActionRequestEnvelope(
                request_id=str(ULID()),
                action_id="mcp_provider.install",
                params={},
                surface=ControlPlaneSurface.WEB,
                actor=ControlPlaneActor(actor_id="user:web", actor_label="Owner"),
            )
        )
        _assert_action_contract(
            result.code == "MCP_INSTALL_SOURCE_INVALID",
            f"invalid params reached owner ({result.code})",
        )
        _assert_action_contract(not owner_called, "invalid params invoked owner")
    finally:
        await store_group.close()
