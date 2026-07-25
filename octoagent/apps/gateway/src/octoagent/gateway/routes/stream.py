"""SSE 事件流路由 -- 对齐 contracts/sse-protocol.md

GET /api/stream/task/{task_id}: SSE 实时推送指定任务的事件。
支持历史事件推送、实时新事件推送、Last-Event-ID 断线重连、心跳保活。
"""

import asyncio
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from octoagent.core.config import SSE_HEARTBEAT_INTERVAL
from octoagent.core.models import TERMINAL_STATES, TaskStatus
from octoagent.core.models.event import Event
from sse_starlette.sse import EventSourceResponse

from ..deps import get_sse_hub, get_store_group
from .task_sse_contract import encode_task_sse_event

router = APIRouter()


def _event_to_sse_data(event: Event, is_final: bool = False) -> dict:
    """将 Event 模型转换为 SSE data JSON"""
    return encode_task_sse_event(event, is_final=is_final).model_dump(mode="json")


def _is_terminal_event(event: Event) -> bool:
    """判断事件是否标识任务到达终态"""
    if event.type == "STATE_TRANSITION" and "to_status" in event.payload:
        to_status = event.payload["to_status"]
        try:
            return TaskStatus(to_status) in TERMINAL_STATES
        except ValueError:
            return False
    return False


def _sse_event(event: Event, *, is_final: bool) -> dict:
    data = _event_to_sse_data(event, is_final=is_final)
    return {
        "id": event.event_id,
        "event": event.type,
        "data": json.dumps(data, ensure_ascii=False),
    }


async def _has_finished_without_pending_job(store_group, task_id: str) -> bool:
    fresh_task = await store_group.task_store.get_task(task_id)
    fresh_job = await store_group.task_job_store.get_job(task_id)
    has_pending_execution = fresh_job is not None and fresh_job.status in {
        "QUEUED",
        "RUNNING",
    }
    return (
        fresh_task is not None
        and fresh_task.status in TERMINAL_STATES
        and not has_pending_execution
    )


async def _task_event_generator(task_id, last_event_id, store_group, sse_hub):
    # 先订阅再读历史，避免 publish-before-subscribe 竞态导致的事件丢失。
    queue = await sse_hub.subscribe(task_id)
    try:
        if last_event_id:
            events = await store_group.event_store.get_events_after(
                task_id,
                last_event_id,
            )
        else:
            events = await store_group.event_store.get_events_for_task(task_id)

        seen_event_ids: set[str] = set()
        # 历史回放按事件本身判断终态，避免旧 task 快照把 final 错标为 false。
        for event in events:
            seen_event_ids.add(event.event_id)
            is_final = _is_terminal_event(event)
            yield _sse_event(event, is_final=is_final)
            if is_final:
                return

        # 历史未含终态事件时重读 task/job，兜底处理 projection 落库竞态。
        if await _has_finished_without_pending_job(store_group, task_id):
            return

        while True:
            try:
                event = await asyncio.wait_for(
                    queue.get(),
                    timeout=SSE_HEARTBEAT_INTERVAL,
                )
                if event.event_id in seen_event_ids:
                    continue
                seen_event_ids.add(event.event_id)
                is_final = _is_terminal_event(event)
                yield _sse_event(event, is_final=is_final)
                if is_final:
                    return
            except TimeoutError:
                yield {"comment": "heartbeat"}
    finally:
        await sse_hub.unsubscribe(task_id, queue)


@router.get(
    "/api/stream/task/{task_id}",
    response_class=EventSourceResponse,
    responses={
        200: {
            "content": {
                "text/event-stream": {
                    "schema": {},
                }
            }
        }
    },
)
async def stream_task_events(
    task_id: str,
    request: Request,
    store_group=Depends(get_store_group),
    sse_hub=Depends(get_sse_hub),
):
    """SSE 事件流端点

    1. 先推送历史事件
    2. 注册到 SSEHub 监听新事件
    3. 实时推送新事件
    4. 终态时携带 final: true
    5. 支持 Last-Event-ID 断线重连
    6. 15 秒心跳保活
    """
    # 检查任务是否存在
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

    # 解析事件游标：query 参数优先，其次 Last-Event-ID（断线重连）
    last_event_id = (
        request.query_params.get("after_event_id", "").strip()
        or request.headers.get("last-event-id", "").strip()
        or None
    )

    return EventSourceResponse(
        _task_event_generator(
            task_id,
            last_event_id,
            store_group,
            sse_hub,
        )
    )
