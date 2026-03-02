"""SSE 审批事件集成 -- T038

对齐 FR-022: SSE 实时更新。
监听 ApprovalManager 事件，推送 approval:requested/resolved/expired SSE 事件。

SSEApprovalBroadcaster 实现 SSEBroadcasterProtocol，
作为 ApprovalManager 与 SSEHub 之间的桥接层。
"""

from __future__ import annotations

import logging
from datetime import UTC
from typing import Any

from ..services.sse_hub import SSEHub

logger = logging.getLogger(__name__)


class SSEApprovalBroadcaster:
    """SSE 审批事件广播器

    桥接 ApprovalManager 的 SSEBroadcasterProtocol 和
    Gateway 的 SSEHub。

    将结构化审批事件转换为 SSE 格式并广播到订阅者。
    """

    def __init__(self, sse_hub: SSEHub) -> None:
        self._sse_hub = sse_hub

    async def broadcast(
        self,
        event_type: str,
        data: dict[str, Any],
        task_id: str | None = None,
    ) -> None:
        """广播 SSE 审批事件

        将审批事件包装为 SSEHub 兼容的 Event 对象后广播。

        Args:
            event_type: SSE event type (如 'approval:requested')
            data: 事件数据
            task_id: 关联的 task ID
        """
        if task_id is None:
            logger.warning(
                "SSE 审批事件缺少 task_id，无法广播: event_type=%s",
                event_type,
            )
            return

        # 构造一个轻量级事件对象，与 SSEHub 的 broadcast 签名兼容
        # SSEHub.broadcast 期望接收 Event 对象，但审批事件是独立格式
        # 这里使用 SimpleNamespace 包装，使其具有 SSEHub 需要的属性
        from datetime import datetime
        from types import SimpleNamespace

        from ulid import ULID

        event_obj = SimpleNamespace(
            event_id=str(ULID()),
            task_id=task_id,
            task_seq=0,
            ts=datetime.now(UTC),
            type=event_type,
            actor="system",
            payload=data,
        )

        try:
            await self._sse_hub.broadcast(task_id, event_obj)
            logger.debug(
                "SSE 审批事件已广播: event_type=%s, task_id=%s",
                event_type,
                task_id,
            )
        except Exception as e:
            logger.error(
                "SSE 审批事件广播失败: event_type=%s, task_id=%s, error=%s",
                event_type,
                task_id,
                e,
            )
