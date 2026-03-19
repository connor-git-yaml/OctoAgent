"""统一的 Task 事件发射辅助函数 — Feature 064 P3 优化 3。

将 SkillRunner._emit_event() 和 SubagentExecutor._emit_event() 中的
重复逻辑提取为独立函数，消除代码重复。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from ulid import ULID

from .models.enums import ActorType, EventType
from .models.event import Event

log = structlog.get_logger(__name__)


async def emit_task_event(
    event_store: Any,
    *,
    task_id: str,
    event_type: str | EventType,
    payload: dict[str, Any],
    actor: ActorType = ActorType.WORKER,
    trace_id: str = "",
) -> str:
    """统一的 Task 事件发射函数。

    封装 Event 构建、task_seq 获取和 append 逻辑。
    优先使用 append_event_committed（如可用）以保证写入即提交。

    Args:
        event_store: EventStoreProtocol 实例。
        task_id: 关联的 Task ID。
        event_type: 事件类型（EventType 枚举或字符串）。
        payload: 事件负载。
        actor: 事件行为者，默认 WORKER。
        trace_id: 可选追踪 ID。

    Returns:
        生成的 event_id。
    """
    if isinstance(event_type, str):
        event_type = EventType(event_type)

    event_id = str(ULID())
    task_seq = await event_store.get_next_task_seq(task_id)

    event = Event(
        event_id=event_id,
        task_id=task_id,
        task_seq=task_seq,
        ts=datetime.now(UTC),
        type=event_type,
        actor=actor,
        payload=payload,
        trace_id=trace_id,
    )

    # 优先使用 append_event_committed（写入即提交），回退到 append_event
    append_committed = getattr(event_store, "append_event_committed", None)
    if callable(append_committed):
        await append_committed(event, update_task_pointer=True)
    else:
        await event_store.append_event(event)

    return event_id
