"""Chat API 路由 -- T040, T041

对齐 contracts/policy-api.md §1.3, §1.4。
POST /api/chat/send -- 发送聊天消息 (FR-023)
GET /stream/task/{task_id} -- SSE 任务事件流（复用已有 stream 路由）(FR-024)
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from octoagent.policy.models import ChatSendRequest, ChatSendResponse

from ..deps import get_store_group

logger = logging.getLogger(__name__)

router = APIRouter()

# 保存后台任务引用，防止 GC 回收
_background_tasks: set[asyncio.Task[None]] = set()


async def _enqueue_or_run(
    request: Request,
    service,
    task_id: str,
    message: str,
    dispatch_metadata: dict[str, str] | None = None,
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
    chat_metadata: dict[str, str] = {}
    requested_agent_profile_id = str(body.agent_profile_id or "").strip()
    if requested_agent_profile_id:
        chat_metadata["agent_profile_id"] = requested_agent_profile_id
        chat_metadata["requested_worker_profile_id"] = requested_agent_profile_id

    # 确定 task_id（复用已有或创建新的）
    task_id = body.task_id or f"task-{uuid.uuid4().hex[:12]}"
    after_event_id = ""
    from ..services.task_service import TaskService

    service = TaskService(store_group, request.app.state.sse_hub)

    # 创建 Task 记录（如果是新对话）
    if not body.task_id:
        try:
            from octoagent.core.models.message import NormalizedMessage

            msg = NormalizedMessage(
                channel="web",
                thread_id=task_id,
                sender_id="owner",
                sender_name="Owner",
                text=body.message,
                metadata=chat_metadata,
                idempotency_key=f"chat-{task_id}",
            )

            created_task_id, created = await service.create_task(msg)
            if created:
                task_id = created_task_id
                await _enqueue_or_run(
                    request,
                    service,
                    task_id,
                    body.message,
                    dispatch_metadata=chat_metadata,
                )
        except Exception:
            # 降级: Task 创建失败时仍返回 task_id，记录日志便于排查
            logger.warning("Task 创建失败，降级返回 task_id", exc_info=True)
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
                metadata=chat_metadata,
            )
            await _enqueue_or_run(
                request,
                service,
                task_id,
                body.message,
                dispatch_metadata=chat_metadata,
            )
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception:
            logger.warning("续对话写入失败", exc_info=True)
            raise

    # 构造 stream URL
    stream_url = f"/api/stream/task/{task_id}"
    if after_event_id:
        stream_url = f"{stream_url}?{urlencode({'after_event_id': after_event_id})}"

    return ChatSendResponse(
        task_id=task_id,
        status="accepted",
        stream_url=stream_url,
    )
