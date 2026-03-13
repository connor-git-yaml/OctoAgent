"""Skills 包测试共享 fixtures。"""

from __future__ import annotations

from collections import deque
from typing import Any

import pytest
from octoagent.core.models.event import Event
from octoagent.skills.manifest import SkillManifest
from octoagent.skills.models import SkillExecutionContext, SkillOutputEnvelope
from octoagent.tooling.models import ToolResult
from pydantic import BaseModel


class EchoInput(BaseModel):
    text: str


class EchoOutput(BaseModel):
    content: str
    complete: bool = False
    skip_remaining_tools: bool = False
    tool_calls: list[dict[str, Any]] = []
    metadata: dict[str, Any] = {}


class MockEventStore:
    """内存 EventStore。"""

    def __init__(self) -> None:
        self.events: list[Event] = []
        self._seq: dict[str, int] = {}

    async def append_event(self, event: Event) -> None:
        self.events.append(event)

    async def get_next_task_seq(self, task_id: str) -> int:
        current = self._seq.get(task_id, 0) + 1
        self._seq[task_id] = current
        return current


class MockToolBroker:
    """可配置结果的 ToolBroker mock。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.contexts: list[Any] = []
        self._results: dict[str, ToolResult] = {}

    def set_result(self, tool_name: str, result: ToolResult) -> None:
        self._results[tool_name] = result

    async def execute(self, tool_name: str, args: dict[str, Any], context: Any) -> ToolResult:
        self.calls.append((tool_name, args))
        self.contexts.append(context)
        if tool_name in self._results:
            return self._results[tool_name]
        return ToolResult(
            output=f"mock:{tool_name}",
            is_error=False,
            error=None,
            duration=0.01,
            artifact_ref=None,
            tool_name=tool_name,
            truncated=False,
        )


class QueueModelClient:
    """按队列返回输出/异常的模型客户端。"""

    def __init__(self, items: list[SkillOutputEnvelope | Exception]) -> None:
        self._queue = deque(items)
        self.calls = 0

    async def generate(
        self,
        *,
        manifest: SkillManifest,
        execution_context: SkillExecutionContext,
        prompt: str,
        feedback: list[Any],
        attempt: int,
        step: int,
    ) -> SkillOutputEnvelope:
        self.calls += 1
        if not self._queue:
            return SkillOutputEnvelope(content="default", complete=True)
        item = self._queue.popleft()
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def execution_context() -> SkillExecutionContext:
    return SkillExecutionContext(
        task_id="task-1",
        trace_id="trace-1",
        caller="worker",
        agent_runtime_id="runtime-worker-1",
        agent_session_id="agent-session-1",
        work_id="work-1",
    )


@pytest.fixture
def event_store() -> MockEventStore:
    return MockEventStore()


@pytest.fixture
def tool_broker() -> MockToolBroker:
    return MockToolBroker()


@pytest.fixture
def echo_manifest() -> SkillManifest:
    return SkillManifest(
        skill_id="demo.echo",
        version="0.1.0",
        input_model=EchoInput,
        output_model=EchoOutput,
        model_alias="main",
        tools_allowed=["system.echo", "system.file_read"],
    )
