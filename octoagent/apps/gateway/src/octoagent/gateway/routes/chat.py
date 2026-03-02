"""Chat API 路由 -- T040, T041

对齐 contracts/policy-api.md §1.3, §1.4。
POST /api/chat/send -- 发送聊天消息 (FR-023)
GET /stream/task/{task_id} -- SSE 任务事件流（复用已有 stream 路由）(FR-024)
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import APIRouter, Depends, Request
from octoagent.policy.models import ChatSendRequest, ChatSendResponse

from ..deps import get_store_group

logger = logging.getLogger(__name__)

router = APIRouter()

# 保存后台任务引用，防止 GC 回收
_background_tasks: set[asyncio.Task[None]] = set()


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
    # 确定 task_id（复用已有或创建新的）
    task_id = body.task_id or f"task-{uuid.uuid4().hex[:12]}"

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
                idempotency_key=f"chat-{task_id}",
            )

            from ..services.task_service import TaskService

            service = TaskService(store_group, request.app.state.sse_hub)
            created_task_id, created = await service.create_task(msg)
            if created:
                task_id = created_task_id

                # 异步启动 LLM 处理
                if (
                    hasattr(request.app.state, "llm_service")
                    and request.app.state.llm_service
                ):
                    task = asyncio.create_task(
                        service.process_task_with_llm(
                            task_id,
                            body.message,
                            request.app.state.llm_service,
                        )
                    )
                    _background_tasks.add(task)
                    task.add_done_callback(_background_tasks.discard)
        except Exception:
            # 降级: Task 创建失败时仍返回 task_id，记录日志便于排查
            logger.warning("Task 创建失败，降级返回 task_id", exc_info=True)

    # 构造 stream URL
    stream_url = f"/api/stream/task/{task_id}"

    return ChatSendResponse(
        task_id=task_id,
        status="accepted",
        stream_url=stream_url,
    )
