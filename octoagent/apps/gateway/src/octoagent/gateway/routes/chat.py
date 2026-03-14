"""Chat API 路由 -- T040, T041

对齐 contracts/policy-api.md §1.3, §1.4。
POST /api/chat/send -- 发送聊天消息 (FR-023)
GET /stream/task/{task_id} -- SSE 任务事件流（复用已有 stream 路由）(FR-024)
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from octoagent.core.models import RuntimeControlContext
from octoagent.policy.models import ChatSendRequest, ChatSendResponse
from octoagent.provider.dx.control_plane_state import ControlPlaneStateStore

from ..deps import get_store_group
from ..services.runtime_control import RUNTIME_CONTEXT_JSON_KEY, encode_runtime_context

logger = logging.getLogger(__name__)

router = APIRouter()

# 保存后台任务引用，防止 GC 回收
_background_tasks: set[asyncio.Task[None]] = set()


async def _enqueue_or_run(
    request: Request,
    service,
    task_id: str,
    message: str,
    dispatch_metadata: dict[str, Any] | None = None,
) -> None:
    if not (
        hasattr(request.app.state, "llm_service")
        and request.app.state.llm_service
    ):
        return
    task_runner = getattr(request.app.state, "task_runner", None)
    if task_runner is not None:
        await task_runner.enqueue(task_id, message)
        return
    task = asyncio.create_task(
        service.process_task_with_llm(
            task_id,
            message,
            request.app.state.llm_service,
            dispatch_metadata=dispatch_metadata or {},
        )
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def _chat_send_failure(
    *,
    status_code: int,
    code: str,
    message: str,
    task_id: str | None = None,
) -> HTTPException:
    detail: dict[str, Any] = {
        "code": code,
        "message": message,
    }
    if task_id:
        detail["task_id"] = task_id
    return HTTPException(status_code=status_code, detail=detail)


def _resolve_project_root(request: Request) -> Path:
    return Path(getattr(request.app.state, "project_root", Path.cwd()))


async def _resolve_chat_scope_snapshot(
    body: ChatSendRequest,
    request: Request,
    store_group,
) -> tuple[str, str, str]:
    project_id = str(body.project_id or "").strip()
    workspace_id = str(body.workspace_id or "").strip()
    new_conversation_token = str(body.new_conversation_token or "").strip()

    state = ControlPlaneStateStore(_resolve_project_root(request)).load()
    if new_conversation_token and new_conversation_token == state.new_conversation_token:
        project_id = state.new_conversation_project_id.strip() or project_id
        workspace_id = state.new_conversation_workspace_id.strip() or workspace_id
    elif not body.task_id:
        project_id = project_id or state.selected_project_id.strip()
        workspace_id = workspace_id or state.selected_workspace_id.strip()

    project = await store_group.project_store.get_project(project_id) if project_id else None
    workspace = (
        await store_group.project_store.get_workspace(workspace_id) if workspace_id else None
    )
    if workspace is not None and project is None:
        project = await store_group.project_store.get_project(workspace.project_id)
    if project is None and workspace is None:
        return new_conversation_token, "", ""
    if project is None:
        raise _chat_send_failure(
            status_code=400,
            code="CHAT_SCOPE_INVALID",
            message="指定的 project/workspace 无法解析到有效 project。",
        )
    if workspace is not None and workspace.project_id != project.project_id:
        raise _chat_send_failure(
            status_code=400,
            code="CHAT_SCOPE_INVALID",
            message="workspace 不属于指定 project。",
        )
    if workspace is None:
        workspace = await store_group.project_store.get_primary_workspace(project.project_id)
    return (
        new_conversation_token,
        project.project_id,
        workspace.workspace_id if workspace is not None else "",
    )


def _build_workspace_scoped_chat_scope_id(
    *,
    workspace_id: str,
    channel: str,
    thread_id: str,
) -> str:
    return f"workspace:{workspace_id}:chat:{channel}:{thread_id}"


@router.post("/api/chat/send", response_model=ChatSendResponse)
async def send_chat_message(
    body: ChatSendRequest,
    request: Request,
    store_group=Depends(get_store_group),
) -> ChatSendResponse:
    """发送聊天消息

    FR-023: 接收消息，创建/复用 Task，返回 stream_url。
    前端使用 EventSource 连接 stream_url 获取流式输出。
    """
    chat_control_metadata: dict[str, Any] = {}
    requested_agent_profile_id = str(body.agent_profile_id or "").strip()
    if requested_agent_profile_id:
        chat_control_metadata["agent_profile_id"] = requested_agent_profile_id
        chat_control_metadata["requested_worker_profile_id"] = requested_agent_profile_id
    new_conversation_token, project_id, workspace_id = await _resolve_chat_scope_snapshot(
        body,
        request,
        store_group,
    )
    if project_id:
        chat_control_metadata["project_id"] = project_id
    if workspace_id:
        chat_control_metadata["workspace_id"] = workspace_id

    # 确定 task_id（复用已有或创建新的）
    task_id = body.task_id or f"task-{uuid.uuid4().hex[:12]}"
    after_event_id = ""
    from ..services.task_service import TaskService

    service = TaskService(store_group, request.app.state.sse_hub)

    # 创建 Task 记录（如果是新对话）
    if not body.task_id:
        from octoagent.core.models.message import NormalizedMessage

        msg = NormalizedMessage(
            channel="web",
            thread_id=task_id,
            scope_id=(
                _build_workspace_scoped_chat_scope_id(
                    workspace_id=workspace_id,
                    channel="web",
                    thread_id=task_id,
                )
                if workspace_id
                else ""
            ),
            sender_id="owner",
            sender_name="Owner",
            text=body.message,
            control_metadata=chat_control_metadata,
            idempotency_key=f"chat-{task_id}",
        )

        try:
            created_task_id, created = await service.create_task(msg)
        except Exception as exc:
            logger.warning("chat_send_create_failed", exc_info=True)
            raise _chat_send_failure(
                status_code=500,
                code="CHAT_TASK_CREATE_FAILED",
                message="任务未创建或未进入执行主链。",
                task_id=task_id if task_id.startswith("01") else None,
            ) from exc
        if created:
            task_id = created_task_id
            dispatch_metadata = dict(chat_control_metadata)
            dispatch_metadata[RUNTIME_CONTEXT_JSON_KEY] = encode_runtime_context(
                RuntimeControlContext(
                    task_id=task_id,
                    surface="web",
                    scope_id=msg.scope_id,
                    thread_id=msg.thread_id,
                    project_id=project_id,
                    workspace_id=workspace_id,
                    agent_profile_id=requested_agent_profile_id,
                    metadata=(
                        {"new_conversation_token": new_conversation_token}
                        if new_conversation_token
                        else {}
                    ),
                )
            )
            try:
                await _enqueue_or_run(
                    request,
                    service,
                    task_id,
                    body.message,
                    dispatch_metadata=dispatch_metadata,
                )
            except Exception as exc:
                logger.warning("chat_send_enqueue_failed", exc_info=True)
                raise _chat_send_failure(
                    status_code=500,
                    code="CHAT_TASK_ENQUEUE_FAILED",
                    message="任务已创建但未能进入执行主链。",
                    task_id=task_id,
                ) from exc
    else:
        try:
            existing_task = await store_group.task_store.get_task(task_id)
            if existing_task is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Task not found: {task_id}",
                )
            after_event_id = existing_task.pointers.latest_event_id
            await service.append_user_message(
                task_id=task_id,
                text=body.message,
                control_metadata=chat_control_metadata,
            )
            dispatch_metadata = dict(chat_control_metadata)
            if project_id or workspace_id:
                dispatch_metadata[RUNTIME_CONTEXT_JSON_KEY] = encode_runtime_context(
                    RuntimeControlContext(
                        task_id=task_id,
                        surface="web",
                        thread_id=existing_task.thread_id,
                        scope_id=existing_task.scope_id,
                        project_id=project_id,
                        workspace_id=workspace_id,
                        agent_profile_id=requested_agent_profile_id,
                    )
                )
            await _enqueue_or_run(
                request,
                service,
                task_id,
                body.message,
                dispatch_metadata=dispatch_metadata,
            )
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            logger.warning("chat_send_continue_failed", exc_info=True)
            raise _chat_send_failure(
                status_code=500,
                code="CHAT_TASK_ENQUEUE_FAILED",
                message="任务已接收但未能进入执行主链。",
                task_id=task_id,
            ) from exc

    # 构造 stream URL
    stream_url = f"/api/stream/task/{task_id}"
    if after_event_id:
        stream_url = f"{stream_url}?{urlencode({'after_event_id': after_event_id})}"

    return ChatSendResponse(
        task_id=task_id,
        status="accepted",
        stream_url=stream_url,
    )
