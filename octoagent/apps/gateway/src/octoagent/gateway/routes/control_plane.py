"""Feature 026: control-plane canonical routes。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from octoagent.core.models import ActionRequestEnvelope, ControlPlaneActionStatus

from ..deps import get_control_plane_service

router = APIRouter()


@router.get("/api/control/snapshot")
async def get_control_snapshot(
    mode: str | None = Query(default=None),
    control_plane=Depends(get_control_plane_service),
):
    return await control_plane.get_snapshot(mode=mode)


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


@router.get("/api/control/resources/worker-profiles")
async def get_control_worker_profiles(control_plane=Depends(get_control_plane_service)):
    return (await control_plane.get_worker_profiles_document()).model_dump(
        mode="json", by_alias=True
    )


@router.get("/api/control/resources/worker-profile-revisions/{profile_id}")
async def get_control_worker_profile_revisions(
    profile_id: str,
    control_plane=Depends(get_control_plane_service),
):
    return (await control_plane.get_worker_profile_revisions_document(profile_id)).model_dump(
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


@router.get("/api/control/resources/mcp-provider-catalog")
async def get_control_mcp_provider_catalog(control_plane=Depends(get_control_plane_service)):
    return (await control_plane.get_mcp_provider_catalog_document()).model_dump(
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


@router.get("/api/control/resources/diagnostics")
async def get_control_diagnostics(control_plane=Depends(get_control_plane_service)):
    return (await control_plane.get_diagnostics_summary()).model_dump(mode="json", by_alias=True)


@router.get("/api/control/resources/retrieval-platform")
async def get_control_retrieval_platform(
    project_id: str | None = Query(default=None),
    control_plane=Depends(get_control_plane_service),
):
    return (
        await control_plane.get_retrieval_platform_document(
            project_id=project_id,
        )
    ).model_dump(mode="json", by_alias=True)


@router.get("/api/control/resources/memory")
async def get_control_memory(
    project_id: str | None = Query(default=None),
    scope_id: str | None = Query(default=None),
    partition: str | None = Query(default=None),
    layer: str | None = Query(default=None),
    query: str | None = Query(default=None),
    include_history: bool = Query(default=False),
    include_vault_refs: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    derived_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    updated_after: str | None = Query(default=None),
    updated_before: str | None = Query(default=None),
    control_plane=Depends(get_control_plane_service),
):
    return (
        await control_plane.get_memory_console(
            project_id=project_id,
            scope_id=scope_id,
            partition=partition,
            layer=layer,
            query=query,
            include_history=include_history,
            include_vault_refs=include_vault_refs,
            limit=limit,
            derived_type=derived_type,
            status=status,
            updated_after=updated_after,
            updated_before=updated_before,
        )
    ).model_dump(mode="json", by_alias=True)


@router.get("/api/control/resources/recall-frames")
async def get_control_recall_frames(
    agent_runtime_id: str | None = Query(default=None),
    agent_session_id: str | None = Query(default=None),
    context_frame_id: str | None = Query(default=None),
    task_id: str | None = Query(default=None),
    project_id: str | None = Query(default=None),
    queried_namespace_kind: str | None = Query(default=None),
    hit_namespace_kind: str | None = Query(default=None),
    created_after: str | None = Query(default=None),
    created_before: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    group_by: str | None = Query(default=None),
    control_plane=Depends(get_control_plane_service),
):
    """F096 块 B + H3 闭环：list_recall_frames audit endpoint。

    7 维过滤 + 时间窗 + 分页 + group_by="agent_runtime_id" 聚合。
    invalid namespace_kind 值返回 400 BadRequest。
    """
    try:
        document = await control_plane.list_recall_frames(
            agent_runtime_id=agent_runtime_id,
            agent_session_id=agent_session_id,
            context_frame_id=context_frame_id,
            task_id=task_id,
            project_id=project_id,
            queried_namespace_kind=queried_namespace_kind,
            hit_namespace_kind=hit_namespace_kind,
            created_after=created_after,
            created_before=created_before,
            limit=limit,
            offset=offset,
            group_by=group_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return document.model_dump(mode="json", by_alias=True)


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
