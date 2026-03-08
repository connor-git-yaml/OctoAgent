"""任务取消路由 -- 对齐 contracts/rest-api.md §4

POST /api/tasks/{task_id}/cancel: 取消非终态的任务。
- 200: 取消成功
- 404: 任务不存在
- 409: 任务已在终态
"""

from fastapi import APIRouter, Depends, Request
from octoagent.core.models import TaskStatus
from pydantic import BaseModel
from starlette.responses import JSONResponse

from ..deps import get_sse_hub, get_store_group, get_task_scope_guard
from ..services.task_scope import TaskScopeGuardError
from ..services.task_service import TaskService

router = APIRouter()


def _task_scope_error(exc: TaskScopeGuardError) -> JSONResponse:
    return JSONResponse(
        status_code=403,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
            }
        },
    )


class CancelResponse(BaseModel):
    """取消成功响应"""

    task_id: str
    status: str


@router.post("/api/tasks/{task_id}/cancel")
async def cancel_task(
    task_id: str,
    request: Request,
    store_group=Depends(get_store_group),
    sse_hub=Depends(get_sse_hub),
    scope_guard=Depends(get_task_scope_guard),
):
    """取消非终态的任务

    - 非终态任务返回 200 + CANCELLED 状态
    - 终态任务返回 409 Conflict
    - 不存在的任务返回 404
    """
    service = TaskService(store_group, sse_hub)
    existing = await service.get_task(task_id)
    if existing is None:
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
        await scope_guard.ensure_task_visible(existing)
    except TaskScopeGuardError as exc:
        return _task_scope_error(exc)

    task_runner = getattr(request.app.state, "task_runner", None)
    if task_runner is not None:
        await task_runner.cancel_task(task_id)

    try:
        task = await service.cancel_task(task_id)
    except ValueError as e:
        if existing is not None and existing.status == TaskStatus.CANCELLED:
            return JSONResponse(
                status_code=200,
                content=CancelResponse(
                    task_id=existing.task_id,
                    status=existing.status.value,
                ).model_dump(),
            )
        # 任务已在终态
        return JSONResponse(
            status_code=409,
            content={
                "error": {
                    "code": "TASK_ALREADY_TERMINAL",
                    "message": str(e),
                }
            },
        )

    return JSONResponse(
        status_code=200,
        content=CancelResponse(
            task_id=task.task_id,
            status=task.status.value,
        ).model_dump(),
    )
