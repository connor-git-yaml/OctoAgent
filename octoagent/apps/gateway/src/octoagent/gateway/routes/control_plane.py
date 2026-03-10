"""Feature 026: control-plane canonical routes。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from octoagent.core.models import ActionRequestEnvelope, ControlPlaneActionStatus
from octoagent.provider.dx.import_workbench_service import ImportWorkbenchError

from ..deps import get_control_plane_service

router = APIRouter()


@router.get("/api/control/snapshot")
async def get_control_snapshot(control_plane=Depends(get_control_plane_service)):
    return await control_plane.get_snapshot()


@router.get("/api/control/resources/wizard")
async def get_control_wizard(control_plane=Depends(get_control_plane_service)):
    return (await control_plane.get_wizard_session()).model_dump(mode="json", by_alias=True)


@router.get("/api/control/resources/config")
async def get_control_config(control_plane=Depends(get_control_plane_service)):
    return (await control_plane.get_config_schema()).model_dump(mode="json", by_alias=True)


@router.get("/api/control/resources/project-selector")
async def get_control_project_selector(control_plane=Depends(get_control_plane_service)):
    return (await control_plane.get_project_selector()).model_dump(mode="json", by_alias=True)


@router.get("/api/control/resources/sessions")
async def get_control_sessions(control_plane=Depends(get_control_plane_service)):
    return (await control_plane.get_session_projection()).model_dump(mode="json", by_alias=True)


@router.get("/api/control/resources/agent-profiles")
async def get_control_agent_profiles(control_plane=Depends(get_control_plane_service)):
    return (await control_plane.get_agent_profiles_document()).model_dump(
        mode="json", by_alias=True
    )


@router.get("/api/control/resources/owner-profile")
async def get_control_owner_profile(control_plane=Depends(get_control_plane_service)):
    return (await control_plane.get_owner_profile_document()).model_dump(mode="json", by_alias=True)


@router.get("/api/control/resources/bootstrap-session")
async def get_control_bootstrap_session(control_plane=Depends(get_control_plane_service)):
    return (await control_plane.get_bootstrap_session_document()).model_dump(
        mode="json", by_alias=True
    )


@router.get("/api/control/resources/context-frames")
async def get_control_context_continuity(control_plane=Depends(get_control_plane_service)):
    return (await control_plane.get_context_continuity_document()).model_dump(
        mode="json", by_alias=True
    )


@router.get("/api/control/resources/policy-profiles")
async def get_control_policy_profiles(control_plane=Depends(get_control_plane_service)):
    return (await control_plane.get_policy_profiles_document()).model_dump(
        mode="json", by_alias=True
    )


@router.get("/api/control/resources/capability-pack")
async def get_control_capability_pack(control_plane=Depends(get_control_plane_service)):
    return (await control_plane.get_capability_pack_document()).model_dump(
        mode="json", by_alias=True
    )


@router.get("/api/control/resources/skill-governance")
async def get_control_skill_governance(control_plane=Depends(get_control_plane_service)):
    return (await control_plane.get_skill_governance_document()).model_dump(
        mode="json", by_alias=True
    )


@router.get("/api/control/resources/setup-governance")
async def get_control_setup_governance(control_plane=Depends(get_control_plane_service)):
    return (await control_plane.get_setup_governance_document()).model_dump(
        mode="json", by_alias=True
    )


@router.get("/api/control/resources/delegation")
async def get_control_delegation(control_plane=Depends(get_control_plane_service)):
    return (await control_plane.get_delegation_document()).model_dump(mode="json", by_alias=True)


@router.get("/api/control/resources/pipelines")
async def get_control_pipelines(control_plane=Depends(get_control_plane_service)):
    return (await control_plane.get_skill_pipeline_document()).model_dump(
        mode="json", by_alias=True
    )


@router.get("/api/control/resources/automation")
async def get_control_automation(control_plane=Depends(get_control_plane_service)):
    return (await control_plane.get_automation_document()).model_dump(mode="json", by_alias=True)


@router.get("/api/control/resources/diagnostics")
async def get_control_diagnostics(control_plane=Depends(get_control_plane_service)):
    return (await control_plane.get_diagnostics_summary()).model_dump(mode="json", by_alias=True)


@router.get("/api/control/resources/memory")
async def get_control_memory(
    project_id: str | None = Query(default=None),
    workspace_id: str | None = Query(default=None),
    scope_id: str | None = Query(default=None),
    partition: str | None = Query(default=None),
    layer: str | None = Query(default=None),
    query: str | None = Query(default=None),
    include_history: bool = Query(default=False),
    include_vault_refs: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    control_plane=Depends(get_control_plane_service),
):
    return (
        await control_plane.get_memory_console(
            project_id=project_id,
            workspace_id=workspace_id,
            scope_id=scope_id,
            partition=partition,
            layer=layer,
            query=query,
            include_history=include_history,
            include_vault_refs=include_vault_refs,
            limit=limit,
        )
    ).model_dump(mode="json", by_alias=True)


@router.get("/api/control/resources/import-workbench")
async def get_control_import_workbench(
    project_id: str | None = Query(default=None),
    workspace_id: str | None = Query(default=None),
    control_plane=Depends(get_control_plane_service),
):
    return (
        await control_plane.get_import_workbench(
            project_id=project_id,
            workspace_id=workspace_id,
        )
    ).model_dump(mode="json", by_alias=True)


@router.get("/api/control/resources/import-sources/{source_id}")
async def get_control_import_source(
    source_id: str,
    control_plane=Depends(get_control_plane_service),
):
    try:
        document = await control_plane.get_import_source(source_id)
    except ImportWorkbenchError as exc:
        status_code = 403 if exc.code.endswith("_NOT_ALLOWED") else 404
        return JSONResponse(
            status_code=status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )
    return document.model_dump(mode="json", by_alias=True)


@router.get("/api/control/resources/import-runs/{run_id}")
async def get_control_import_run(
    run_id: str,
    control_plane=Depends(get_control_plane_service),
):
    try:
        document = await control_plane.get_import_run(run_id)
    except ImportWorkbenchError as exc:
        status_code = 403 if exc.code.endswith("_NOT_ALLOWED") else 404
        return JSONResponse(
            status_code=status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )
    return document.model_dump(mode="json", by_alias=True)


@router.get("/api/control/resources/memory-subjects/{subject_key}")
async def get_control_memory_subject_history(
    subject_key: str,
    project_id: str | None = Query(default=None),
    workspace_id: str | None = Query(default=None),
    scope_id: str | None = Query(default=None),
    control_plane=Depends(get_control_plane_service),
):
    return (
        await control_plane.get_memory_subject_history(
            subject_key,
            project_id=project_id,
            workspace_id=workspace_id,
            scope_id=scope_id,
        )
    ).model_dump(mode="json", by_alias=True)


@router.get("/api/control/resources/memory-proposals")
async def get_control_memory_proposals(
    project_id: str | None = Query(default=None),
    workspace_id: str | None = Query(default=None),
    scope_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    source: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    control_plane=Depends(get_control_plane_service),
):
    return (
        await control_plane.get_memory_proposal_audit(
            project_id=project_id,
            workspace_id=workspace_id,
            scope_id=scope_id,
            status=status,
            source=source,
            limit=limit,
        )
    ).model_dump(mode="json", by_alias=True)


@router.get("/api/control/resources/vault-authorization")
async def get_control_vault_authorization(
    project_id: str | None = Query(default=None),
    workspace_id: str | None = Query(default=None),
    scope_id: str | None = Query(default=None),
    subject_key: str | None = Query(default=None),
    control_plane=Depends(get_control_plane_service),
):
    return (
        await control_plane.get_vault_authorization(
            project_id=project_id,
            workspace_id=workspace_id,
            scope_id=scope_id,
            subject_key=subject_key,
        )
    ).model_dump(mode="json", by_alias=True)


@router.get("/api/control/actions")
async def get_control_actions(control_plane=Depends(get_control_plane_service)):
    return control_plane.get_action_registry().model_dump(mode="json", by_alias=True)


@router.post("/api/control/actions")
async def post_control_action(
    body: ActionRequestEnvelope,
    control_plane=Depends(get_control_plane_service),
):
    result = await control_plane.execute_action(body)
    status_code = _resolve_action_status_code(result.code, result.status)
    return JSONResponse(
        status_code=status_code,
        content={
            "contract_version": result.contract_version,
            "result": result.model_dump(mode="json", by_alias=True),
        },
    )


@router.get("/api/control/events")
async def get_control_events(
    after: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    control_plane=Depends(get_control_plane_service),
):
    events = await control_plane.list_events(after=after, limit=limit)
    registry = control_plane.get_action_registry()
    return {
        "contract_version": registry.contract_version,
        "events": [event.model_dump(mode="json", by_alias=True) for event in events],
    }


def _resolve_action_status_code(code: str, status: ControlPlaneActionStatus) -> int:
    if status == ControlPlaneActionStatus.DEFERRED:
        return 202
    if status == ControlPlaneActionStatus.COMPLETED:
        return 200

    if code.endswith("_REQUIRED") or code.endswith("_INVALID"):
        return 400
    if code.endswith("_NOT_FOUND") or code == "ACTION_NOT_FOUND":
        return 404
    if code.endswith("_UNAVAILABLE"):
        return 503
    if code.endswith("_NOT_ALLOWED"):
        return 403
    if code.endswith("_MISMATCH"):
        return 409
    if code.endswith("_FAILED"):
        return 500
    return 409
