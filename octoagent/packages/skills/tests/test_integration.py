"""SkillRunner 集成测试。"""

from __future__ import annotations

from typing import Any

from octoagent.skills.manifest import SkillManifest
from octoagent.skills.models import SkillExecutionContext, SkillOutputEnvelope, SkillRunStatus
from octoagent.skills.registry import SkillRegistry
from octoagent.skills.runner import SkillRunner
from pydantic import BaseModel

from .conftest import QueueModelClient


class EchoInput(BaseModel):
    text: str


class EchoOutput(BaseModel):
    content: str
    complete: bool = False
    skip_remaining_tools: bool = False
    tool_calls: list[dict[str, Any]] = []
    metadata: dict[str, Any] = {}


class MockToolBroker:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, tool_name: str, args: dict[str, Any], context: Any):
        self.calls.append((tool_name, args))
        from octoagent.tooling.models import ToolResult

        return ToolResult(
            output=f"content for {args.get('path', '')}".strip(),
            is_error=False,
            error=None,
            duration=0.01,
            artifact_ref=None,
            tool_name=tool_name,
            truncated=False,
        )


async def test_echo_skill_end_to_end() -> None:
    registry = SkillRegistry()
    manifest = SkillManifest(
        skill_id="demo.echo",
        version="0.1.0",
        input_model=EchoInput,
        output_model=EchoOutput,
        model_alias="main",
        tools_allowed=[],
    )
    registry.register(manifest, "echo prompt")

    client = QueueModelClient([SkillOutputEnvelope(content="hello", complete=True)])
    broker = MockToolBroker()
    runner = SkillRunner(model_client=client, tool_broker=broker)

    ctx = SkillExecutionContext(task_id="t1", trace_id="tr1", caller="worker")
    result = await runner.run(
        manifest=registry.get("demo.echo").manifest,
        execution_context=ctx,
        skill_input={"text": "hi"},
        prompt="echo prompt",
    )

    assert result.status == SkillRunStatus.SUCCEEDED
    assert result.output is not None
    assert result.output.content == "hello"


async def test_file_summary_skill_end_to_end() -> None:
    registry = SkillRegistry()
    manifest = SkillManifest(
        skill_id="demo.file_summary",
        version="0.1.0",
        input_model=EchoInput,
        output_model=EchoOutput,
        model_alias="main",
        tools_allowed=["system.file_read"],
    )
    registry.register(manifest, "summary prompt")

    client = QueueModelClient(
        [
            SkillOutputEnvelope(
                content="need file",
                tool_calls=[{"tool_name": "system.file_read", "arguments": {"path": "README.md"}}],
                complete=False,
            ),
            SkillOutputEnvelope(content="summary done", complete=True),
        ]
    )
    broker = MockToolBroker()
    runner = SkillRunner(model_client=client, tool_broker=broker)

    ctx = SkillExecutionContext(task_id="t2", trace_id="tr2", caller="worker")
    result = await runner.run(
        manifest=registry.get("demo.file_summary").manifest,
        execution_context=ctx,
        skill_input={"text": "summarize"},
        prompt="summary prompt",
    )

    assert result.status == SkillRunStatus.SUCCEEDED
    assert broker.calls and broker.calls[0][0] == "system.file_read"
