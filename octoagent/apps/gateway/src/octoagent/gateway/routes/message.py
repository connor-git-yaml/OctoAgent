"""消息接收路由 -- 对齐 contracts/rest-api.md §1

POST /api/message: 接收用户消息，创建 Task，异步启动 LLM 处理。
"""

import asyncio

from fastapi import APIRouter, Depends, Request
from octoagent.core.models.message import NormalizedMessage
from pydantic import BaseModel, Field

from ..deps import get_sse_hub, get_store_group
from ..services.task_service import TaskService

router = APIRouter()


class MessageRequest(BaseModel):
    """消息接收请求体"""

    text: str = Field(description="消息文本")
    idempotency_key: str = Field(description="幂等键，用于去重")
    channel: str = Field(default="web", description="渠道标识")
    thread_id: str = Field(default="default", description="线程标识")
    sender_id: str = Field(default="owner", description="发送者 ID")
    sender_name: str = Field(default="Owner", description="发送者名称")


class MessageResponse(BaseModel):
    """消息接收响应"""

    task_id: str
    status: str
    created: bool


@router.post("/api/message", response_model=MessageResponse)
async def receive_message(
    body: MessageRequest,
    request: Request,
    store_group=Depends(get_store_group),
    sse_hub=Depends(get_sse_hub),
):
    """接收用户消息，创建 Task

    - 新消息返回 201 Created
    - idempotency_key 已存在返回 200 OK
    """
    from starlette.responses import JSONResponse

    # 转换为 NormalizedMessage
    msg = NormalizedMessage(
        channel=body.channel,
        thread_id=body.thread_id,
        sender_id=body.sender_id,
        sender_name=body.sender_name,
        text=body.text,
        idempotency_key=body.idempotency_key,
    )

    service = TaskService(store_group, sse_hub)
    task_id, created = await service.create_task(msg)

    if created:
        # 异步启动后台 LLM 处理（如果有）
        if hasattr(request.app.state, "llm_service") and request.app.state.llm_service:
            asyncio.create_task(
                service.process_task_with_llm(
                    task_id,
                    msg.text,
                    request.app.state.llm_service,
                )
            )

        return JSONResponse(
            status_code=201,
            content=MessageResponse(
                task_id=task_id,
                status="CREATED",
                created=True,
            ).model_dump(),
        )
    else:
        # idempotency_key 已存在，返回已有任务信息
        task = await service.get_task(task_id)
        return JSONResponse(
            status_code=200,
            content=MessageResponse(
                task_id=task_id,
                status=task.status if task else "CREATED",
                created=False,
            ).model_dump(),
        )
