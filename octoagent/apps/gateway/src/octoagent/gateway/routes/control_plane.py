"""Feature 026: control-plane canonical routes。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from octoagent.core.models import ActionRequestEnvelope, ControlPlaneActionStatus

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
    return (await control_plane.get_project_selector()).model_dump(
        mode="json", by_alias=True
    )


@router.get("/api/control/resources/sessions")
async def get_control_sessions(control_plane=Depends(get_control_plane_service)):
    return (await control_plane.get_session_projection()).model_dump(
        mode="json", by_alias=True
    )


@router.get("/api/control/resources/automation")
async def get_control_automation(control_plane=Depends(get_control_plane_service)):
    return (await control_plane.get_automation_document()).model_dump(
        mode="json", by_alias=True
    )


@router.get("/api/control/resources/diagnostics")
async def get_control_diagnostics(control_plane=Depends(get_control_plane_service)):
    return (await control_plane.get_diagnostics_summary()).model_dump(
        mode="json", by_alias=True
    )


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
    if code.endswith("_FAILED"):
        return 500
    return 409
