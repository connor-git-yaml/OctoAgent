"""任务查询与恢复路由 -- 对齐 contracts/rest-api.md §2, §3

GET /api/tasks: 任务列表查询，支持 status 筛选。
GET /api/tasks/{task_id}: 任务详情查询，含 events + artifacts。
POST /api/tasks/{task_id}/resume: 手动触发恢复。
GET /api/tasks/{task_id}/checkpoints: 查询 checkpoint 时间线。
"""

from fastapi import APIRouter, Depends, Query, Request
from octoagent.core.models import ResumeFailureType, Task
from octoagent.core.store import StoreGroup
from pydantic import BaseModel
from starlette.responses import JSONResponse

from ..deps import get_store_group
from ..services.resume_engine import ResumeEngine
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


class ResumeTaskResponse(BaseModel):
    """手动恢复响应"""

    ok: bool
    task_id: str
    checkpoint_id: str | None = None
    resumed_from_node: str | None = None
    failure_type: str | None = None
    message: str


class CheckpointItem(BaseModel):
    """checkpoint 列表项"""

    checkpoint_id: str
    task_id: str
    node_id: str
    status: str
    schema_version: int
    state_snapshot: dict
    side_effect_cursor: str | None = None
    created_at: str
    updated_at: str


class CheckpointListResponse(BaseModel):
    """checkpoint 列表响应"""

    checkpoints: list[CheckpointItem]


async def _resolve_task_session_alias(task: Task, store_group: StoreGroup) -> str:
    project = await store_group.project_store.resolve_project_for_scope(task.scope_id)
    project_id = project.project_id if project is not None else None

    related_sessions = []
    seen_agent_session_ids: set[str] = set()

    def _append(agent_session) -> None:
        if agent_session is None or agent_session.agent_session_id in seen_agent_session_ids:
            return
        seen_agent_session_ids.add(agent_session.agent_session_id)
        related_sessions.append(agent_session)

    candidate_legacy_session_ids = {str(task.thread_id).strip()}

    session_states = await store_group.agent_context_store.list_session_contexts(
        project_id=project_id,
        workspace_id=None,
    )
    for item in session_states:
        if str(item.thread_id).strip() != str(task.thread_id).strip():
            continue
        if item.agent_session_id:
            _append(
                await store_group.agent_context_store.get_agent_session(item.agent_session_id)
            )
        if item.session_id:
            candidate_legacy_session_ids.add(str(item.session_id).strip())

    for legacy_session_id in candidate_legacy_session_ids:
        if not legacy_session_id:
            continue
        candidates = await store_group.agent_context_store.list_agent_sessions(
            legacy_session_id=legacy_session_id,
            project_id=project_id,
            workspace_id=None,
            limit=200,
        )
        for item in candidates:
            _append(item)

    related_sessions.sort(key=lambda item: item.updated_at, reverse=True)
    for item in related_sessions:
        alias = item.alias.strip()
        if alias:
            return alias
    return ""


@router.get("/api/tasks", response_model=TaskListResponse)
async def list_tasks(
    status: str | None = Query(default=None, description="按状态筛选"),
    store_group=Depends(get_store_group),
):
    """查询任务列表，支持按状态筛选，按 created_at 倒序。

    不做 project scope 过滤——单用户系统中 project 是组织维度而非访问控制边界。
    """
    service = TaskService(store_group)
    tasks = await service.list_tasks(status)

    return TaskListResponse(
        tasks=[
            TaskSummary(
                task_id=t.task_id,
                created_at=t.created_at.isoformat(),
                updated_at=t.updated_at.isoformat(),
                status=t.status.value,
                title=t.title,
                thread_id=t.thread_id,
                scope_id=t.scope_id,
                risk_level=t.risk_level.value,
            )
            for t in tasks
        ]
    )


@router.get("/api/tasks/{task_id}")
async def get_task_detail(
    task_id: str,
    store_group=Depends(get_store_group),
):
    """查询任务详情，包含关联的 events 和 artifacts 列表。

    不做 project scope 隔离——聊天恢复需要跨项目读取 task detail，
    用户在侧边栏点击了 session 即代表有意查看该 task。
    """
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
    session_alias = await _resolve_task_session_alias(task, store_group)

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
                    "type": p.type.value,
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
        "status": task.status.value,
        "title": task.title,
        "alias": session_alias,
        "thread_id": task.thread_id,
        "scope_id": task.scope_id,
        "requester": {
            "channel": task.requester.channel,
            "sender_id": task.requester.sender_id,
        },
        "risk_level": task.risk_level.value,
    }

    return {
        "task": task_data,
        "events": events_data,
        "artifacts": artifacts_data,
    }


@router.post("/api/tasks/{task_id}/resume", response_model=ResumeTaskResponse)
async def resume_task(
    task_id: str,
    request: Request,
    store_group=Depends(get_store_group),
):
    """手动触发恢复。"""
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
        result = await task_runner.resume_task(task_id, trigger="manual")
    else:
        engine = ResumeEngine(store_group)
        result = await engine.try_resume(task_id, trigger="manual")

    if result.ok:
        return ResumeTaskResponse(
            ok=True,
            task_id=result.task_id,
            checkpoint_id=result.checkpoint_id,
            resumed_from_node=result.resumed_from_node,
            failure_type=None,
            message=result.message,
        )

    failure_type = result.failure_type.value if result.failure_type else "unknown"
    if result.failure_type in {
        ResumeFailureType.TERMINAL_TASK,
        ResumeFailureType.LEASE_CONFLICT,
    }:
        status_code = 409
    elif result.failure_type in {
        ResumeFailureType.DEPENDENCY_MISSING,
        ResumeFailureType.SNAPSHOT_CORRUPT,
        ResumeFailureType.VERSION_MISMATCH,
    }:
        status_code = 422
    else:
        status_code = 422

    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": "TASK_RESUME_FAILED",
                "failure_type": failure_type,
                "message": result.message,
            }
        },
    )


@router.get("/api/tasks/{task_id}/checkpoints", response_model=CheckpointListResponse)
async def list_task_checkpoints(
    task_id: str,
    store_group=Depends(get_store_group),
):
    """查询任务 checkpoint 时间线（created_at 倒序）。"""
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

    checkpoints = await store_group.checkpoint_store.list_checkpoints(task_id)
    return CheckpointListResponse(
        checkpoints=[
            CheckpointItem(
                checkpoint_id=cp.checkpoint_id,
                task_id=cp.task_id,
                node_id=cp.node_id,
                status=cp.status.value,
                schema_version=cp.schema_version,
                state_snapshot=cp.state_snapshot,
                side_effect_cursor=cp.side_effect_cursor,
                created_at=cp.created_at.isoformat(),
                updated_at=cp.updated_at.isoformat(),
            )
            for cp in checkpoints
        ]
    )
