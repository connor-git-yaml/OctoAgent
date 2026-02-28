"""任务取消路由 -- 对齐 contracts/rest-api.md §4

POST /api/tasks/{task_id}/cancel: 取消非终态的任务。
- 200: 取消成功
- 404: 任务不存在
- 409: 任务已在终态
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from starlette.responses import JSONResponse

from ..deps import get_sse_hub, get_store_group
from ..services.task_service import TaskService

router = APIRouter()


class CancelResponse(BaseModel):
    """取消成功响应"""

    task_id: str
    status: str


@router.post("/api/tasks/{task_id}/cancel")
async def cancel_task(
    task_id: str,
    store_group=Depends(get_store_group),
    sse_hub=Depends(get_sse_hub),
):
    """取消非终态的任务

    - 非终态任务返回 200 + CANCELLED 状态
    - 终态任务返回 409 Conflict
    - 不存在的任务返回 404
    """
    service = TaskService(store_group, sse_hub)

    try:
        task = await service.cancel_task(task_id)
    except ValueError as e:
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

    return JSONResponse(
        status_code=200,
        content=CancelResponse(
            task_id=task.task_id,
            status=task.status.value,
        ).model_dump(),
    )
