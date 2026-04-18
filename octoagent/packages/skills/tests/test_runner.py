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
from octoagent.skills.models import is_runtime_exempt_tool
from octoagent.skills.runner import SkillRunner
from octoagent.tooling.models import SideEffectLevel, ToolMeta, ToolResult
from pydantic import BaseModel
import pytest

from .conftest import EchoInput, QueueModelClient


@pytest.mark.parametrize(
    "tool_name,tool_group,expected",
    [
        # 正向：标准 mcp.<server>.<tool> 格式
        ("mcp.perplexity.search", "mcp", True),
        ("mcp.filesystem.read", "mcp", True),
        # 反向：tool_group 不是 mcp
        ("mcp.perplexity.search", "builtin", False),
        ("mcp.perplexity.search", "", False),
        # 反向：name 不以 mcp. 开头
        ("filesystem.read", "mcp", False),
        # 反向：缺 tool 段（只有 mcp.<server>）
        ("mcp.perplexity", "mcp", False),
        # 反向：只有 "mcp." 前缀无后续
        ("mcp.", "mcp", False),
        ("mcp", "mcp", False),
        # 边界：双点 "mcp..evil"（server 段为空），必须拒绝
        ("mcp..evil", "mcp", False),
        ("mcp...", "mcp", False),
        # 边界：空字符串
        ("", "mcp", False),
        ("", "", False),
    ],
)
def test_is_runtime_exempt_tool_edge_cases(tool_name: str, tool_group: str, expected: bool) -> None:
    """Feature 077: MCP 豁免边界 —— 拒绝双点（mcp..evil）等被误用豁免通道的形式。

    不传 metadata 时按形状判定（向后兼容旧调用方）。
    """
    assert is_runtime_exempt_tool(tool_name, tool_group) is expected


@pytest.mark.parametrize(
    "tool_name,tool_group,metadata,expected",
    [
        # 动态注册的 MCP 工具：metadata.source=="mcp" → 豁免
        ("mcp.perplexity.search", "mcp", {"source": "mcp"}, True),
        ("mcp.openrouter_perplexity.ask_model", "mcp", {"source": "mcp", "title": "X"}, True),
        # builtin 管理工具：tool_group=="mcp" 但 metadata 没 source 标记 → 不豁免
        # 这是关键回归：避免 LLM 看到 mcp.servers.list / mcp.tools.list 反复调
        # 做"验证"触发工具交替循环。
        ("mcp.servers.list", "mcp", {"entrypoints": ["agent_runtime", "web"]}, False),
        ("mcp.tools.list", "mcp", {}, False),
        ("mcp.tools.refresh", "mcp", {"source": ""}, False),
        # 形状非法：即使 source=="mcp" 也不豁免（防御）
        ("mcp.perplexity", "mcp", {"source": "mcp"}, False),
        ("mcp..evil", "mcp", {"source": "mcp"}, False),
        # tool_group 不是 mcp：即使 source=="mcp" 也不豁免
        ("mcp.perplexity.search", "builtin", {"source": "mcp"}, False),
    ],
)
def test_is_runtime_exempt_tool_metadata_source_filter(
    tool_name: str, tool_group: str, metadata: dict, expected: bool
) -> None:
    """Feature 079: 收紧豁免，只放行 metadata.source=="mcp" 的动态 MCP 工具。

    builtin 管理工具（mcp.servers.list 等）即使名字形如 mcp.X.Y 也不被豁免，
    避免 LLM 把它们当作"验证 MCP 状态"的入口反复调用。
    """
    assert is_runtime_exempt_tool(tool_name, tool_group, metadata) is expected


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
    """output_model 验证已在 runner 中跳过（避免 Qwen tool_call 格式差异导致
    静默丢弃 tool_calls），因此 content="bad" 不再触发 VALIDATION_ERROR，
    直接返回 SUCCEEDED。"""
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

    # output_model validation 已跳过，runner 直接返回 complete=True 的结果
    assert result.status == SkillRunStatus.SUCCEEDED


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


async def test_runner_mcp_tool_exempt_from_allowlist(
    echo_manifest, execution_context, tool_broker, event_store
) -> None:
    """Feature 077 + 079: LiteLLM schema 层对**动态注册** MCP 工具
    (metadata.source=="mcp") 放行后，runner 执行层也必须同步放行
    (is_runtime_exempt_tool)，否则 LLM 看得见但调不动，且会在 history
    留下孤立 function_call 触发 Responses API 400。"""
    # 显式锁定前提：echo_manifest 必须处于 RESTRICT 模式，否则白名单校验
    # 被跳过，豁免逻辑未被测试到，会产生假阳性通过
    assert echo_manifest.permission_mode == SkillPermissionMode.RESTRICT
    tool_broker.set_tool_meta(
        "mcp.perplexity.ask_model",
        ToolMeta(
            name="mcp.perplexity.ask_model",
            description="向 Perplexity 提问",
            parameters_json_schema={"type": "object", "properties": {}},
            side_effect_level=SideEffectLevel.NONE,
            tool_group="mcp",
            metadata={"source": "mcp", "mcp_server_name": "perplexity"},
        ),
    )
    outputs = [
        SkillOutputEnvelope(
            content="ask mcp",
            complete=False,
            tool_calls=[{"tool_name": "mcp.perplexity.ask_model", "arguments": {}}],
        ),
        SkillOutputEnvelope(content="done", complete=True),
    ]
    client = QueueModelClient(outputs)
    runner = SkillRunner(model_client=client, tool_broker=tool_broker, event_store=event_store)

    result = await runner.run(
        manifest=echo_manifest,  # tools_allowed=["system.echo", "system.file_read"]，不含 mcp.*
        execution_context=execution_context,
        skill_input={"text": "hi"},
        prompt="prompt",
    )

    assert result.status == SkillRunStatus.SUCCEEDED
    assert any(call[0] == "mcp.perplexity.ask_model" for call in tool_broker.calls)


async def test_runner_mcp_builtin_admin_tool_not_exempted(
    echo_manifest, execution_context, tool_broker, event_store
) -> None:
    """Feature 079 回归保护：builtin 管理工具（mcp.servers.list 等）即使
    tool_group=="mcp" 也不被豁免，避免 LLM 把它们当作"验证 MCP 状态"的
    入口反复调用并触发工具交替循环。

    LLM schema 层与 runner 执行层都不会暴露/放行这类工具；它们走标准
    deferred / tool_search 激活路径。
    """
    assert echo_manifest.permission_mode == SkillPermissionMode.RESTRICT
    tool_broker.set_tool_meta(
        "mcp.servers.list",
        ToolMeta(
            name="mcp.servers.list",
            description="列出已注册的 MCP 服务",
            parameters_json_schema={"type": "object", "properties": {}},
            side_effect_level=SideEffectLevel.NONE,
            tool_group="mcp",
            metadata={"entrypoints": ["agent_runtime", "web"]},  # 无 source="mcp"
        ),
    )
    outputs = [
        SkillOutputEnvelope(
            content="check mcp",
            complete=False,
            tool_calls=[{"tool_name": "mcp.servers.list", "arguments": {}}],
        ),
    ] * 4
    client = QueueModelClient(outputs)
    runner = SkillRunner(model_client=client, tool_broker=tool_broker, event_store=event_store)

    result = await runner.run(
        manifest=echo_manifest,
        execution_context=execution_context,
        skill_input={"text": "hi"},
        prompt="prompt",
    )

    # 白名单拒绝 → 反复重试 → 进入失败终态
    assert result.status == SkillRunStatus.FAILED
    assert result.error_category == ErrorCategory.TOOL_EXECUTION_ERROR


async def test_runner_non_mcp_tool_not_exempted(
    echo_manifest, execution_context, tool_broker, event_store
) -> None:
    """Feature 077 回归保护：豁免仅针对 tool_group=='mcp'，其他 tool_group
    即使名字看似符合格式也不放行。"""
    tool_broker.set_tool_meta(
        "danger.exec",
        ToolMeta(
            name="danger.exec",
            description="执行命令",
            parameters_json_schema={"type": "object"},
            side_effect_level=SideEffectLevel.IRREVERSIBLE,
            tool_group="system",  # 非 mcp
        ),
    )
    outputs = [
        SkillOutputEnvelope(
            content="attempt danger",
            complete=False,
            tool_calls=[{"tool_name": "danger.exec", "arguments": {"cmd": "rm -rf /"}}],
        ),
    ] * 4  # 连续触发超过 max_attempts=3
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
    assert all(call[0] != "danger.exec" for call in tool_broker.calls)


async def test_runner_repeat_warning_injects_loop_guard_feedback(
    echo_manifest, tool_broker, event_store
) -> None:
    """Feature follow-up: 连续相同 tool_calls signature 达到 warning 阈值时，
    runner 向 feedback 注入 _loop_guard 条目，驱动 LLM 打破无效循环。"""
    from octoagent.skills.models import (
        SkillExecutionContext,
        UsageLimits,
    )

    # warning_threshold=2 → 第 2 轮相同 signature 即注入 warning
    ctx = SkillExecutionContext(
        task_id="task-loop",
        trace_id="trace-loop",
        caller="worker",
        usage_limits=UsageLimits(repeat_warning_threshold=2, max_steps=8),
    )

    # 模拟 Agent 连续调同一工具同一参数，第 3 轮让它收手结束（避免用完 max_steps）
    same_call = [{"tool_name": "system.echo", "arguments": {"text": "poll"}}]
    outputs = [
        SkillOutputEnvelope(content="round 1", complete=False, tool_calls=same_call),
        SkillOutputEnvelope(content="round 2", complete=False, tool_calls=same_call),
        SkillOutputEnvelope(content="done", complete=True),
    ]
    client = CaptureFeedbackClient(outputs)
    runner = SkillRunner(model_client=client, tool_broker=tool_broker, event_store=event_store)

    await runner.run(
        manifest=echo_manifest,
        execution_context=ctx,
        skill_input={"text": "hi"},
        prompt="prompt",
    )

    # 第 3 轮 LLM 调用时（complete=True 终结前），feedback 里应包含 _loop_guard
    final_feedback = client.feedback_snapshots[-1]
    loop_guard_msgs = [fb for fb in final_feedback if fb.tool_name == "_loop_guard"]
    assert len(loop_guard_msgs) == 1, (
        f"期望恰好 1 条 _loop_guard feedback，实际得到 {len(loop_guard_msgs)}"
    )
    assert "system.echo" in loop_guard_msgs[0].error
    assert "连续第 2 轮" in loop_guard_msgs[0].error


async def test_runner_different_tool_calls_no_warning(
    echo_manifest, tool_broker, event_store
) -> None:
    """回归保护：不同 tool_calls signature 不累计 repeat_count，不触发 warning。"""
    from octoagent.skills.models import (
        SkillExecutionContext,
        UsageLimits,
    )

    ctx = SkillExecutionContext(
        task_id="task-varied",
        trace_id="trace-varied",
        caller="worker",
        usage_limits=UsageLimits(repeat_warning_threshold=2, max_steps=8),
    )

    outputs = [
        SkillOutputEnvelope(
            content="call A",
            complete=False,
            tool_calls=[{"tool_name": "system.echo", "arguments": {"text": "a"}}],
        ),
        SkillOutputEnvelope(
            content="call B",  # 不同 args → 不同 signature
            complete=False,
            tool_calls=[{"tool_name": "system.echo", "arguments": {"text": "b"}}],
        ),
        SkillOutputEnvelope(
            content="call C",
            complete=False,
            tool_calls=[{"tool_name": "system.echo", "arguments": {"text": "c"}}],
        ),
        SkillOutputEnvelope(content="done", complete=True),
    ]
    client = CaptureFeedbackClient(outputs)
    runner = SkillRunner(model_client=client, tool_broker=tool_broker, event_store=event_store)

    await runner.run(
        manifest=echo_manifest,
        execution_context=ctx,
        skill_input={"text": "hi"},
        prompt="prompt",
    )

    # 整个运行中都不应出现 _loop_guard
    for snapshot in client.feedback_snapshots:
        assert not any(fb.tool_name == "_loop_guard" for fb in snapshot)


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
    client = QueueModelClient([repeated] * 102)
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
