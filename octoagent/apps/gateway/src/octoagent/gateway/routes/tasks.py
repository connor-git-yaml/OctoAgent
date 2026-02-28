"""任务查询路由 -- 对齐 contracts/rest-api.md §2, §3

GET /api/tasks: 任务列表查询，支持 status 筛选。
GET /api/tasks/{task_id}: 任务详情查询，含 events + artifacts。
"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from starlette.responses import JSONResponse

from ..deps import get_store_group
from ..services.task_service import TaskService

router = APIRouter()


class TaskSummary(BaseModel):
    """任务摘要（列表项）"""

    task_id: str
    created_at: str
    updated_at: str
    status: str
    title: str
    thread_id: str
    scope_id: str
    risk_level: str


class TaskListResponse(BaseModel):
    """任务列表响应"""

    tasks: list[TaskSummary]


@router.get("/api/tasks", response_model=TaskListResponse)
async def list_tasks(
    status: str | None = Query(default=None, description="按状态筛选"),
    store_group=Depends(get_store_group),
):
    """查询任务列表，支持按状态筛选，按 created_at 倒序"""
    service = TaskService(store_group)
    tasks = await service.list_tasks(status)

    return TaskListResponse(
        tasks=[
            TaskSummary(
                task_id=t.task_id,
                created_at=t.created_at.isoformat(),
                updated_at=t.updated_at.isoformat(),
                status=t.status.value if hasattr(t.status, "value") else str(t.status),
                title=t.title,
                thread_id=t.thread_id,
                scope_id=t.scope_id,
                risk_level=(
                    t.risk_level.value
                    if hasattr(t.risk_level, "value")
                    else str(t.risk_level)
                ),
            )
            for t in tasks
        ]
    )


@router.get("/api/tasks/{task_id}")
async def get_task_detail(
    task_id: str,
    store_group=Depends(get_store_group),
):
    """查询任务详情，包含关联的 events 和 artifacts 列表"""
    service = TaskService(store_group)
    task = await service.get_task(task_id)

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

    # 查询关联的事件和 artifacts
    events = await store_group.event_store.get_events_for_task(task_id)
    artifacts = await store_group.artifact_store.list_artifacts_for_task(task_id)

    # 序列化事件
    events_data = [
        {
            "event_id": e.event_id,
            "task_seq": e.task_seq,
            "ts": e.ts.isoformat(),
            "type": e.type.value,
            "actor": e.actor.value,
            "payload": e.payload,
        }
        for e in events
    ]

    # 序列化 artifacts
    artifacts_data = [
        {
            "artifact_id": a.artifact_id,
            "name": a.name,
            "size": a.size,
            "parts": [
                {
                    "type": p.type.value if hasattr(p.type, "value") else str(p.type),
                    "mime": p.mime,
                    "content": p.content,
                }
                for p in a.parts
            ],
        }
        for a in artifacts
    ]

    # 构建任务详情响应
    task_data = {
        "task_id": task.task_id,
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
        "status": task.status.value if hasattr(task.status, "value") else str(task.status),
        "title": task.title,
        "thread_id": task.thread_id,
        "scope_id": task.scope_id,
        "requester": {
            "channel": task.requester.channel,
            "sender_id": task.requester.sender_id,
        },
        "risk_level": (
            task.risk_level.value
            if hasattr(task.risk_level, "value")
            else str(task.risk_level)
        ),
    }

    return {
        "task": task_data,
        "events": events_data,
        "artifacts": artifacts_data,
    }
