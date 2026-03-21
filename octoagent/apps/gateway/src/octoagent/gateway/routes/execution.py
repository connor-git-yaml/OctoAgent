"""Execution console routes for Feature 019."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from starlette.responses import JSONResponse

from ..deps import get_execution_console_service, get_store_group
from ..services.execution_console import ExecutionInputError

router = APIRouter()


class AttachInputRequest(BaseModel):
    """Attach input request body."""

    text: str
    approval_id: str | None = None
    actor: str = "user:web"


@router.get("/api/tasks/{task_id}/execution")
async def get_execution_session(
    task_id: str,
    request: Request,
    execution_console=Depends(get_execution_console_service),
    store_group=Depends(get_store_group),
):
    """Return the latest execution session projection for a task."""
    task = await store_group.task_store.get_task(task_id)
    if task is None:
        return JSONResponse(
            status_code=404,
            content={
                "error": {
                    "code": "TASK_NOT_FOUND",
                    "message": f"Task with id {task_id} does not exist",
                }
            },
        )
    task_runner = getattr(request.app.state, "task_runner", None)
    if task_runner is not None:
        session = await task_runner.get_execution_session(task_id)
    else:
        session = await execution_console.get_session(task_id)
    if session is None:
        return JSONResponse(
            status_code=404,
            content={
                "error": {
                    "code": "EXECUTION_SESSION_NOT_FOUND",
                    "message": f"Task {task_id} has no execution session",
                }
            },
        )
    return {"session": session.model_dump(mode="json")}


@router.get("/api/tasks/{task_id}/execution/events")
async def get_execution_events(
    task_id: str,
    execution_console=Depends(get_execution_console_service),
    store_group=Depends(get_store_group),
):
    """Return normalized execution events for the latest session."""
    task = await store_group.task_store.get_task(task_id)
    if task is None:
        return JSONResponse(
            status_code=404,
            content={
                "error": {
                    "code": "TASK_NOT_FOUND",
                    "message": f"Task with id {task_id} does not exist",
                }
            },
        )
    session = await execution_console.get_session(task_id)
    if session is None:
        return {"session_id": None, "events": []}
    events = await execution_console.list_execution_events(task_id, session_id=session.session_id)
    return {
        "session_id": session.session_id,
        "events": [event.model_dump(mode="json") for event in events],
    }


@router.post("/api/tasks/{task_id}/execution/input")
async def attach_execution_input(
    task_id: str,
    body: AttachInputRequest,
    request: Request,
    execution_console=Depends(get_execution_console_service),
    store_group=Depends(get_store_group),
):
    """Attach human input to a waiting execution session."""
    task = await store_group.task_store.get_task(task_id)
    if task is None:
        return JSONResponse(
            status_code=404,
            content={
                "error": {
                    "code": "TASK_NOT_FOUND",
                    "message": f"Task with id {task_id} does not exist",
                }
            },
        )
    try:
        task_runner = getattr(request.app.state, "task_runner", None)
        if task_runner is not None:
            result = await task_runner.attach_input(
                task_id,
                body.text,
                actor=body.actor,
                approval_id=body.approval_id,
            )
            session = await task_runner.get_execution_session(task_id)
        else:
            result = await execution_console.attach_input(
                task_id=task_id,
                text=body.text,
                actor=body.actor,
                approval_id=body.approval_id,
            )
            session = await execution_console.get_session(task_id)
    except ExecutionInputError as exc:
        status_code = 409
        error_code = exc.code
        if exc.code == "TASK_NOT_FOUND":
            status_code = 404
        elif exc.code == "INPUT_APPROVAL_REQUIRED":
            status_code = 403
        return JSONResponse(
            status_code=status_code,
            content={
                "error": {
                    "code": error_code,
                    "message": str(exc),
                    "approval_id": exc.approval_id,
                }
            },
        )
    result_payload = asdict(result) if is_dataclass(result) else result.model_dump(mode="json")
    return {
        "result": result_payload,
        "session": session.model_dump(mode="json") if session is not None else None,
    }
