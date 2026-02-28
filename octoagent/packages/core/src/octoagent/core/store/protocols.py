"""Store Protocol 接口定义 -- 对齐 Blueprint §9.2

定义 TaskStore、EventStore、ArtifactStore 的抽象接口，
使用 Python Protocol 实现结构化子类型（duck typing）。
"""

from typing import Protocol

from ..models.artifact import Artifact
from ..models.event import Event
from ..models.task import Task


class TaskStore(Protocol):
    """Task 存储接口 -- 对齐 Blueprint §9.2"""

    async def create_task(self, task: Task) -> None:
        """创建任务记录"""
        ...

    async def get_task(self, task_id: str) -> Task | None:
        """根据 task_id 查询任务"""
        ...

    async def list_tasks(self, status: str | None = None) -> list[Task]:
        """查询任务列表，支持按状态筛选"""
        ...

    async def update_task_status(
        self,
        task_id: str,
        status: str,
        updated_at: str,
        latest_event_id: str,
    ) -> None:
        """更新任务状态（仅通过事件触发）"""
        ...


class EventStore(Protocol):
    """Event 存储接口 -- 对齐 Blueprint §9.2

    事件表 append-only：只允许插入，不允许更新或删除。
    """

    async def append_event(self, event: Event) -> None:
        """追加事件（append-only）"""
        ...

    async def get_events_for_task(self, task_id: str) -> list[Event]:
        """查询指定任务的所有事件"""
        ...

    async def get_events_after(
        self,
        task_id: str,
        after_event_id: str,
    ) -> list[Event]:
        """查询指定事件之后的增量事件（用于 SSE 断线重连）"""
        ...

    async def get_next_task_seq(self, task_id: str) -> int:
        """获取指定任务的下一个 task_seq（MAX+1）"""
        ...

    async def check_idempotency_key(self, key: str) -> str | None:
        """检查幂等键是否已存在，返回关联的 task_id 或 None"""
        ...


class ArtifactStore(Protocol):
    """Artifact 存储接口 -- 对齐 Blueprint §9.2"""

    async def put_artifact(
        self,
        artifact: Artifact,
        content: bytes | None = None,
    ) -> None:
        """存储 Artifact（元数据 + 可选内容）"""
        ...

    async def get_artifact(self, artifact_id: str) -> Artifact | None:
        """根据 artifact_id 查询 Artifact 元数据"""
        ...

    async def list_artifacts_for_task(self, task_id: str) -> list[Artifact]:
        """查询指定任务的所有 Artifact"""
        ...

    async def get_artifact_content(self, artifact_id: str) -> bytes | None:
        """获取 Artifact 内容（inline 直接返回 + 文件路径读取）"""
        ...
