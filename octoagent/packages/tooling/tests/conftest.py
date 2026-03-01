"""共享测试 Fixtures -- Feature 004 Tool Contract + ToolBroker

提供 mock EventStore、mock ArtifactStore、mock ExecutionContext 等测试基础设施。
"""

from __future__ import annotations

import pytest
from octoagent.core.models.artifact import Artifact
from octoagent.core.models.event import Event


class MockEventStore:
    """Mock EventStore -- 内存实现，用于测试事件生成"""

    def __init__(self) -> None:
        self.events: list[Event] = []
        self._seq_counter: dict[str, int] = {}

    async def append_event(self, event: Event) -> None:
        """追加事件到内存列表"""
        self.events.append(event)

    async def get_events_for_task(self, task_id: str) -> list[Event]:
        """查询指定任务的所有事件"""
        return [e for e in self.events if e.task_id == task_id]

    async def get_events_after(self, task_id: str, after_event_id: str) -> list[Event]:
        """查询指定事件之后的增量事件"""
        task_events = [e for e in self.events if e.task_id == task_id]
        found = False
        result = []
        for e in task_events:
            if found:
                result.append(e)
            if e.event_id == after_event_id:
                found = True
        return result

    async def get_next_task_seq(self, task_id: str) -> int:
        """获取下一个 task_seq"""
        current = self._seq_counter.get(task_id, 0)
        self._seq_counter[task_id] = current + 1
        return current + 1

    async def check_idempotency_key(self, key: str) -> str | None:
        """检查幂等键"""
        for e in self.events:
            if e.causality.idempotency_key == key:
                return e.task_id
        return None


class MockArtifactStore:
    """Mock ArtifactStore -- 内存实现，用于测试大输出裁切"""

    def __init__(self, *, fail: bool = False) -> None:
        self.artifacts: dict[str, Artifact] = {}
        self.contents: dict[str, bytes] = {}
        self._fail = fail

    async def put_artifact(
        self,
        artifact: Artifact,
        content: bytes | None = None,
    ) -> None:
        """存储 Artifact"""
        if self._fail:
            raise RuntimeError("MockArtifactStore: 模拟存储失败")
        self.artifacts[artifact.artifact_id] = artifact
        if content is not None:
            self.contents[artifact.artifact_id] = content

    async def get_artifact(self, artifact_id: str) -> Artifact | None:
        """查询 Artifact"""
        return self.artifacts.get(artifact_id)

    async def list_artifacts_for_task(self, task_id: str) -> list[Artifact]:
        """查询指定任务的所有 Artifact"""
        return [a for a in self.artifacts.values() if a.task_id == task_id]

    async def get_artifact_content(self, artifact_id: str) -> bytes | None:
        """获取 Artifact 内容"""
        return self.contents.get(artifact_id)


@pytest.fixture
def mock_event_store() -> MockEventStore:
    """提供 MockEventStore 实例"""
    return MockEventStore()


@pytest.fixture
def mock_artifact_store() -> MockArtifactStore:
    """提供 MockArtifactStore 实例"""
    return MockArtifactStore()


@pytest.fixture
def failing_artifact_store() -> MockArtifactStore:
    """提供会失败的 MockArtifactStore 实例"""
    return MockArtifactStore(fail=True)
