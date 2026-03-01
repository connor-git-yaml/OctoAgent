"""SSEHub -- 内存中事件广播器

每个订阅者持有一个 asyncio.Queue，支持 subscribe/unsubscribe/broadcast。
T037 完整实现将在 Phase 5 完成，此处提供可工作的骨架。
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

    async def broadcast(self, task_id: str, event: Event) -> None:
        """向指定任务的所有订阅者广播事件

        Args:
            task_id: 任务 ID
            event: 要广播的事件
        """
        dead_queues = []
        for queue in self._subscribers.get(task_id, set()):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                dead_queues.append(queue)

        # 清理已满的队列
        for q in dead_queues:
            self._subscribers[task_id].discard(q)
        if task_id in self._subscribers and not self._subscribers[task_id]:
            del self._subscribers[task_id]
