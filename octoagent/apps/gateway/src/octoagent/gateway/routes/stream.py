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

router = APIRouter()


def _event_to_sse_data(event: Event, is_final: bool = False) -> dict:
    """将 Event 模型转换为 SSE data JSON"""
    data = {
        "event_id": event.event_id,
        "task_id": event.task_id,
        "task_seq": event.task_seq,
        "ts": event.ts.isoformat(),
        "type": event.type,
        "actor": event.actor,
        "payload": event.payload,
        "final": is_final,
    }
    return data


def _is_terminal_event(event: Event) -> bool:
    """判断事件是否标识任务到达终态"""
    if event.type == "STATE_TRANSITION" and "to_status" in event.payload:
        to_status = event.payload["to_status"]
        try:
            return TaskStatus(to_status) in TERMINAL_STATES
        except ValueError:
            return False
    return False


@router.get("/api/stream/task/{task_id}")
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

    async def event_generator():
        # 先订阅再读历史，避免 publish-before-subscribe 竞态导致的事件丢失：
        # 若先读历史后订阅，在这两步之间并发 broadcast 的事件既进不了历史快照，
        # 也进不了队列，会永久丢失（典型表现：前端 placeholder 一直转圈，刷新后才出现最终回复）。
        queue = await sse_hub.subscribe(task_id)
        try:
            # 获取历史事件
            if last_event_id:
                # 断线重连：从 last_event_id 之后查询
                events = await store_group.event_store.get_events_after(
                    task_id, last_event_id
                )
            else:
                # 新连接：查询所有历史事件
                events = await store_group.event_store.get_events_for_task(task_id)

            # 记录已推送过的 event_id，用于订阅后 drain 时去重
            seen_event_ids: set[str] = set()
            # 历史回放阶段：直接按事件本身判断是否终态，而不是依赖入口时读取的
            # 旧 task 快照。否则当 task 在 subscribe 之后、读历史期间转入终态时，
            # 快照仍是非终态导致 final=false，同一事件又会在订阅侧 drain 时被
            # dedup 跳过，前端永远收不到 final=true。
            for event in events:
                seen_event_ids.add(event.event_id)
                is_final = _is_terminal_event(event)
                data = _event_to_sse_data(event, is_final=is_final)
                yield {
                    "id": event.event_id,
                    "event": event.type,
                    "data": json.dumps(data, ensure_ascii=False),
                }
                if is_final:
                    return

            # 历史里没有终态事件：再读一次 task/job 状态，兜底处理"入口非终态、
            # 读历史期间转入终态但历史里不含 STATE_TRANSITION"这种极端场景。
            fresh_task = await store_group.task_store.get_task(task_id)
            fresh_job = await store_group.task_job_store.get_job(task_id)
            has_pending_execution = (
                fresh_job is not None and fresh_job.status in {"QUEUED", "RUNNING"}
            )
            if (
                fresh_task is not None
                and fresh_task.status in TERMINAL_STATES
                and not has_pending_execution
            ):
                return

            while True:
                try:
                    # 等待新事件（带心跳超时）
                    event = await asyncio.wait_for(
                        queue.get(), timeout=SSE_HEARTBEAT_INTERVAL
                    )
                    # 去重：与读历史并发 broadcast 的事件可能同时出现在快照与队列中
                    if event.event_id in seen_event_ids:
                        continue
                    seen_event_ids.add(event.event_id)
                    is_final = _is_terminal_event(event)
                    data = _event_to_sse_data(event, is_final=is_final)
                    yield {
                        "id": event.event_id,
                        "event": event.type,
                        "data": json.dumps(data, ensure_ascii=False),
                    }
                    if is_final:
                        return
                except TimeoutError:
                    # 心跳保活
                    yield {"comment": "heartbeat"}
        finally:
            await sse_hub.unsubscribe(task_id, queue)

    return EventSourceResponse(event_generator())
