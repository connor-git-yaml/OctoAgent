"""SSEHub -- 内存中事件广播器

每个订阅者持有一个 asyncio.Queue，支持 subscribe/unsubscribe/broadcast。

Feature 064 P1-B 扩展：broadcast() 支持 parent_task_id 双路广播。
"""

import asyncio
from collections import defaultdict

from octoagent.core.models.event import Event


class SSEHub:
    """SSE 事件广播器 -- 基于 asyncio.Queue 的发布/订阅模式"""

    def __init__(self, queue_maxsize: int = 100) -> None:
        # task_id -> set of asyncio.Queue
        self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._queue_maxsize = queue_maxsize

    async def subscribe(self, task_id: str) -> asyncio.Queue:
        """订阅指定任务的事件流

        Args:
            task_id: 要订阅的任务 ID

        Returns:
            asyncio.Queue 实例，新事件会被推送到此队列
        """
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._queue_maxsize)
        self._subscribers[task_id].add(queue)
        return queue

    async def unsubscribe(self, task_id: str, queue: asyncio.Queue) -> None:
        """取消订阅

        Args:
            task_id: 任务 ID
            queue: 之前订阅时返回的队列
        """
        self._subscribers[task_id].discard(queue)
        if not self._subscribers[task_id]:
            del self._subscribers[task_id]

    async def broadcast(
        self,
        task_id: str,
        event: Event,
        parent_task_id: str | None = None,
    ) -> None:
        """向指定任务的所有订阅者广播事件

        Feature 064 P1-B 扩展：当 parent_task_id 非 None 时，
        事件同时广播到 task_id 和 parent_task_id 的订阅者（事件冒泡）。

        Args:
            task_id: 任务 ID
            event: 要广播的事件
            parent_task_id: 父任务 ID（Subagent 事件冒泡用，可选）
        """
        # 收集所有需要广播的 task_id
        target_ids = [task_id]
        if parent_task_id and parent_task_id != task_id:
            target_ids.append(parent_task_id)

        for tid in target_ids:
            dead_queues = []
            for queue in self._subscribers.get(tid, set()):
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    dead_queues.append(queue)

            # 清理已满的队列
            for q in dead_queues:
                self._subscribers[tid].discard(q)
            if tid in self._subscribers and not self._subscribers[tid]:
                del self._subscribers[tid]
