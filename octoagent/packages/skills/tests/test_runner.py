"""SkillRunner 单元测试。"""

from __future__ import annotations

from typing import Any

from octoagent.skills.manifest import SkillManifest
from octoagent.skills.models import (
    ErrorCategory,
    SkillOutputEnvelope,
    SkillPermissionMode,
    SkillRunStatus,
)
from octoagent.skills.runner import SkillRunner
from octoagent.tooling.models import ToolResult
from pydantic import BaseModel

from .conftest import EchoInput, QueueModelClient


class StrictOutput(BaseModel):
    answer: str


class CaptureFeedbackClient(QueueModelClient):
    def __init__(self, items: list[SkillOutputEnvelope | Exception]) -> None:
        super().__init__(items)
        self.feedback_snapshots: list[list[Any]] = []

    async def generate(self, **kwargs: Any) -> SkillOutputEnvelope:
        self.feedback_snapshots.append(list(kwargs["feedback"]))
        return await super().generate(**kwargs)


async def test_runner_success_complete(
    echo_manifest, execution_context, tool_broker, event_store
) -> None:
    client = QueueModelClient([SkillOutputEnvelope(content="ok", complete=True)])
    runner = SkillRunner(
        model_client=client,
        tool_broker=tool_broker,
        event_store=event_store,
    )

    result = await runner.run(
        manifest=echo_manifest,
        execution_context=execution_context,
        skill_input=EchoInput(text="hello"),
        prompt="echo",
    )

    assert result.status == SkillRunStatus.SUCCEEDED
    assert result.output is not None
    assert result.output.content == "ok"
    event_types = [e.type.value for e in event_store.events]
    assert "SKILL_STARTED" in event_types
    assert "SKILL_COMPLETED" in event_types


async def test_runner_tool_call_success(
    echo_manifest, execution_context, tool_broker, event_store
) -> None:
    client = QueueModelClient(
        [
            SkillOutputEnvelope(
                content="call tool",
                complete=False,
                tool_calls=[{"tool_name": "system.echo", "arguments": {"text": "a"}}],
            ),
            SkillOutputEnvelope(content="done", complete=True),
        ]
    )
    runner = SkillRunner(model_client=client, tool_broker=tool_broker, event_store=event_store)

    result = await runner.run(
        manifest=echo_manifest,
        execution_context=execution_context,
        skill_input={"text": "hi"},
        prompt="prompt",
    )

    assert result.status == SkillRunStatus.SUCCEEDED
    assert len(tool_broker.calls) == 1
    assert tool_broker.calls[0][0] == "system.echo"
    assert len(tool_broker.contexts) == 1
    assert tool_broker.contexts[0].agent_runtime_id == "runtime-worker-1"
    assert tool_broker.contexts[0].agent_session_id == "agent-session-1"
    assert tool_broker.contexts[0].work_id == "work-1"


async def test_runner_output_validation_retry(execution_context, tool_broker, event_store) -> None:
    manifest = SkillManifest(
        skill_id="demo.strict",
        version="0.1.0",
        input_model=EchoInput,
        output_model=StrictOutput,
        model_alias="main",
        tools_allowed=[],
    )
    client = QueueModelClient([SkillOutputEnvelope(content="bad", complete=True)])
    runner = SkillRunner(model_client=client, tool_broker=tool_broker, event_store=event_store)

    result = await runner.run(
        manifest=manifest,
        execution_context=execution_context,
        skill_input={"text": "hi"},
        prompt="prompt",
    )

    assert result.status == SkillRunStatus.FAILED
    assert result.error_category == ErrorCategory.VALIDATION_ERROR


async def test_runner_model_failure_retry_to_fail(
    echo_manifest, execution_context, tool_broker, event_store
) -> None:
    client = QueueModelClient(
        [RuntimeError("boom"), RuntimeError("boom"), RuntimeError("boom"), RuntimeError("boom")]
    )
    runner = SkillRunner(model_client=client, tool_broker=tool_broker, event_store=event_store)

    result = await runner.run(
        manifest=echo_manifest,
        execution_context=execution_context,
        skill_input={"text": "hi"},
        prompt="prompt",
    )

    assert result.status == SkillRunStatus.FAILED
    assert result.error_category == ErrorCategory.REPEAT_ERROR


async def test_runner_disallowed_tool(
    echo_manifest, execution_context, tool_broker, event_store
) -> None:
    outputs = [
        SkillOutputEnvelope(
            content="call tool",
            complete=False,
            tool_calls=[{"tool_name": "danger.exec", "arguments": {"cmd": "rm -rf /1"}}],
        ),
        SkillOutputEnvelope(
            content="call tool",
            complete=False,
            tool_calls=[{"tool_name": "danger.exec", "arguments": {"cmd": "rm -rf /2"}}],
        ),
        SkillOutputEnvelope(
            content="call tool",
            complete=False,
            tool_calls=[{"tool_name": "danger.exec", "arguments": {"cmd": "rm -rf /3"}}],
        ),
        SkillOutputEnvelope(
            content="call tool",
            complete=False,
            tool_calls=[{"tool_name": "danger.exec", "arguments": {"cmd": "rm -rf /4"}}],
        ),
    ]
    # 连续触发超过默认 max_attempts=3，确保进入失败终态。
    client = QueueModelClient(outputs)
    runner = SkillRunner(model_client=client, tool_broker=tool_broker, event_store=event_store)

    result = await runner.run(
        manifest=echo_manifest,
        execution_context=execution_context,
        skill_input={"text": "hi"},
        prompt="prompt",
    )

    assert result.status == SkillRunStatus.FAILED
    assert result.error_category == ErrorCategory.TOOL_EXECUTION_ERROR


async def test_runner_inherit_mode_uses_runtime_mounted_tools(
    echo_manifest, execution_context, tool_broker, event_store
) -> None:
    manifest = echo_manifest.model_copy(
        update={
            "permission_mode": SkillPermissionMode.INHERIT,
            "tools_allowed": [],
        }
    )
    client = QueueModelClient(
        [
            SkillOutputEnvelope(
                content="call tool",
                complete=False,
                tool_calls=[{"tool_name": "system.echo", "arguments": {"text": "hello"}}],
            ),
            SkillOutputEnvelope(content="done", complete=True),
        ]
    )
    runner = SkillRunner(model_client=client, tool_broker=tool_broker, event_store=event_store)
    context = execution_context.model_copy(
        update={
            "metadata": {
                "tool_selection": {
                    "mounted_tools": [{"tool_name": "system.echo"}],
                }
            }
        }
    )

    result = await runner.run(
        manifest=manifest,
        execution_context=context,
        skill_input={"text": "hi"},
        prompt="prompt",
    )

    assert result.status == SkillRunStatus.SUCCEEDED
    assert tool_broker.calls == [("system.echo", {"text": "hello"})]


async def test_runner_restrict_mode_does_not_expand_from_runtime_metadata(
    echo_manifest, execution_context, tool_broker, event_store
) -> None:
    manifest = echo_manifest.model_copy(
        update={
            "permission_mode": SkillPermissionMode.RESTRICT,
            "tools_allowed": ["system.echo"],
        }
    )
    client = QueueModelClient(
        [
            SkillOutputEnvelope(
                content="call tool",
                complete=False,
                tool_calls=[{"tool_name": "system.file_read", "arguments": {"path": "a"}}],
            ),
            SkillOutputEnvelope(
                content="call tool",
                complete=False,
                tool_calls=[{"tool_name": "system.file_read", "arguments": {"path": "b"}}],
            ),
            SkillOutputEnvelope(
                content="call tool",
                complete=False,
                tool_calls=[{"tool_name": "system.file_read", "arguments": {"path": "c"}}],
            ),
            SkillOutputEnvelope(
                content="call tool",
                complete=False,
                tool_calls=[{"tool_name": "system.file_read", "arguments": {"path": "d"}}],
            ),
        ]
    )
    runner = SkillRunner(model_client=client, tool_broker=tool_broker, event_store=event_store)
    context = execution_context.model_copy(
        update={
            "metadata": {
                "tool_selection": {
                    "mounted_tools": [{"tool_name": "system.file_read"}],
                }
            }
        }
    )

    result = await runner.run(
        manifest=manifest,
        execution_context=context,
        skill_input={"text": "hi"},
        prompt="prompt",
    )

    assert result.status == SkillRunStatus.FAILED
    assert result.error_category == ErrorCategory.TOOL_EXECUTION_ERROR
    assert tool_broker.calls == []


async def test_runner_loop_detected(
    echo_manifest, execution_context, tool_broker, event_store
) -> None:
    repeated = SkillOutputEnvelope(
        content="loop",
        complete=False,
        tool_calls=[{"tool_name": "system.echo", "arguments": {"text": "x"}}],
    )
    client = QueueModelClient([repeated, repeated, repeated, repeated])
    runner = SkillRunner(model_client=client, tool_broker=tool_broker, event_store=event_store)

    result = await runner.run(
        manifest=echo_manifest,
        execution_context=execution_context,
        skill_input={"text": "hi"},
        prompt="prompt",
    )

    assert result.status == SkillRunStatus.FAILED
    assert result.error_category == ErrorCategory.LOOP_DETECTED


async def test_runner_step_limit(
    echo_manifest, execution_context, tool_broker, event_store
) -> None:
    manifest = echo_manifest.model_copy(deep=True)
    manifest.loop_guard.max_steps = 2
    client = QueueModelClient(
        [
            SkillOutputEnvelope(
                content="step1",
                complete=False,
                tool_calls=[{"tool_name": "system.echo", "arguments": {"text": "1"}}],
            ),
            SkillOutputEnvelope(
                content="step2",
                complete=False,
                tool_calls=[{"tool_name": "system.file_read", "arguments": {"path": "a"}}],
            ),
            SkillOutputEnvelope(content="step3", complete=False),
        ]
    )
    runner = SkillRunner(model_client=client, tool_broker=tool_broker, event_store=event_store)

    result = await runner.run(
        manifest=manifest,
        execution_context=execution_context,
        skill_input={"text": "hi"},
        prompt="prompt",
    )

    assert result.status == SkillRunStatus.FAILED
    assert result.error_category == ErrorCategory.STEP_LIMIT_EXCEEDED


async def test_runner_context_budget_guard(
    echo_manifest, execution_context, tool_broker, event_store
) -> None:
    long_output = "x" * 500
    tool_broker.set_result(
        "system.echo",
        ToolResult(
            output=long_output,
            is_error=False,
            error=None,
            duration=0.01,
            artifact_ref="artifact-1",
            tool_name="system.echo",
            truncated=True,
        ),
    )

    manifest = echo_manifest.model_copy(deep=True)
    manifest.context_budget.max_chars = 100
    manifest.context_budget.summary_chars = 30

    client = CaptureFeedbackClient(
        [
            SkillOutputEnvelope(
                content="tool",
                complete=False,
                tool_calls=[{"tool_name": "system.echo", "arguments": {"text": "x"}}],
            ),
            SkillOutputEnvelope(content="done", complete=True),
        ]
    )
    runner = SkillRunner(model_client=client, tool_broker=tool_broker, event_store=event_store)

    result = await runner.run(
        manifest=manifest,
        execution_context=execution_context,
        skill_input={"text": "hi"},
        prompt="prompt",
    )

    assert result.status == SkillRunStatus.SUCCEEDED
    # 第二次调用应拿到第一次工具反馈
    assert len(client.feedback_snapshots) >= 2
    second_feedback = client.feedback_snapshots[1]
    assert second_feedback
    tool_feedback = second_feedback[0]
    assert "artifact:artifact-1" in tool_feedback.output
