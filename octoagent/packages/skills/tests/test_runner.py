"""SkillRunner 单元测试。"""

from __future__ import annotations

from typing import Any

from octoagent.skills.manifest import SkillManifest
from octoagent.skills.models import (
    FEEDBACK_SENDER_LOOP_GUARD,
    FEEDBACK_SENDER_RUNNER_ERROR,
    FEEDBACK_SENDER_TOOL_ERROR,
    ErrorCategory,
    FeedbackKind,
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


async def test_runner_emits_fallback_error_message_for_empty_exception(
    echo_manifest, execution_context, tool_broker, event_store
) -> None:
    """回归：httpx stream 中断/空响应这类 str(exc)==""  的异常不能写成 error_message=""。

    现场案例：task 01KPGQGC1J447N1EV5JN9EBK9M 连续 4 条 MODEL_CALL_FAILED
    的 error_message 都是空串，排查时完全不知道是哪类异常。兜底到
    类型名（如 "ConnectError"）后，至少能从事件里看出异常种类。
    """

    class SilentFailure(Exception):
        """str() 返回空串的异常——模拟 httpx 部分无消息异常。"""

        def __str__(self) -> str:
            return ""

    client = QueueModelClient([SilentFailure(), SilentFailure(), SilentFailure(), SilentFailure()])
    runner = SkillRunner(
        model_client=client, tool_broker=tool_broker, event_store=event_store
    )

    await runner.run(
        manifest=echo_manifest,
        execution_context=execution_context,
        skill_input={"text": "hi"},
        prompt="prompt",
    )

    failed_events = [
        e for e in event_store.events if e.type.value == "MODEL_CALL_FAILED"
    ]
    assert failed_events, "应至少发射一次 MODEL_CALL_FAILED"
    for event in failed_events:
        message = event.payload.get("error_message", "")
        assert message, "空 str(exc) 必须走兜底，不能写成空串"
        assert "SilentFailure" in message, (
            f"兜底消息必须保留异常类型名便于排查，实际: {message!r}"
        )


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


async def test_runner_no_progress_loop_breaks_when_content_always_empty(
    echo_manifest, execution_context, tool_broker, event_store
) -> None:
    """Feature 079: 连续 N 步只发 tool_calls 没 content 也不 complete →
    LOOP_DETECTED 熔断。覆盖"参数微变 + 工具反复成功但 LLM 不收尾"场景。

    构造 8 步全部 content="" + tool_calls 但每步 args 微变（避免 exact
    signature 重复）。no_progress_steps_threshold 默认 8 应在第 8 步熔断。
    """
    from octoagent.skills.models import UsageLimits

    context = execution_context.model_copy(deep=True)
    context.usage_limits = UsageLimits(
        max_steps=50,  # 故意远大于 no_progress 阈值，确保是 no_progress 触发
        no_progress_steps_threshold=8,
    )
    # 每步 args 微变（text 数字递增），signature 都不同；content 全空
    outputs = [
        SkillOutputEnvelope(
            content="",
            complete=False,
            tool_calls=[
                {"tool_name": "system.echo", "arguments": {"text": f"probe-{i}"}}
            ],
        )
        for i in range(20)
    ]
    client = QueueModelClient(outputs)
    runner = SkillRunner(model_client=client, tool_broker=tool_broker, event_store=event_store)

    result = await runner.run(
        manifest=echo_manifest,
        execution_context=context,
        skill_input={"text": "hi"},
        prompt="prompt",
    )

    assert result.status == SkillRunStatus.FAILED
    assert result.error_category == ErrorCategory.LOOP_DETECTED
    assert "无进展循环" in (result.error_message or "")
    # 第 8 步触发
    assert result.steps == 8


async def test_runner_no_progress_resets_when_content_appears(
    echo_manifest, execution_context, tool_broker, event_store
) -> None:
    """Feature 079: 任意一步 content 非空即重置计数，正常 multi-step
    research 不会被误杀。模拟 7 步只 tool_calls + 第 8 步带 content。
    """
    from octoagent.skills.models import UsageLimits

    context = execution_context.model_copy(deep=True)
    context.usage_limits = UsageLimits(
        max_steps=50,
        no_progress_steps_threshold=8,
    )
    # 前 7 步只 tool_calls 没 content；第 8 步 LLM 给 content（重置计数）；
    # 第 9 步 complete
    outputs = [
        SkillOutputEnvelope(
            content="",
            complete=False,
            tool_calls=[
                {"tool_name": "system.echo", "arguments": {"text": f"step-{i}"}}
            ],
        )
        for i in range(7)
    ] + [
        SkillOutputEnvelope(
            content="阶段性发现：xxx",
            complete=False,
            tool_calls=[
                {"tool_name": "system.echo", "arguments": {"text": "step-7"}}
            ],
        ),
        SkillOutputEnvelope(content="完成", complete=True),
    ]
    client = QueueModelClient(outputs)
    runner = SkillRunner(model_client=client, tool_broker=tool_broker, event_store=event_store)

    result = await runner.run(
        manifest=echo_manifest,
        execution_context=context,
        skill_input={"text": "hi"},
        prompt="prompt",
    )

    assert result.status == SkillRunStatus.SUCCEEDED


async def test_runner_no_progress_disabled_when_threshold_none(
    echo_manifest, execution_context, tool_broker, event_store
) -> None:
    """Feature 079: no_progress_steps_threshold=None（API 契约"显式不限制"）
    时不启用本检测，向后兼容旧用法。靠 max_steps 兜底。
    """
    from octoagent.skills.models import UsageLimits

    context = execution_context.model_copy(deep=True)
    context.usage_limits = UsageLimits(
        max_steps=5,  # max_steps 兜底
        no_progress_steps_threshold=None,  # 显式禁用 no_progress
    )
    outputs = [
        SkillOutputEnvelope(
            content="",
            complete=False,
            tool_calls=[
                {"tool_name": "system.echo", "arguments": {"text": f"probe-{i}"}}
            ],
        )
        for i in range(20)
    ]
    client = QueueModelClient(outputs)
    runner = SkillRunner(model_client=client, tool_broker=tool_broker, event_store=event_store)

    result = await runner.run(
        manifest=echo_manifest,
        execution_context=context,
        skill_input={"text": "hi"},
        prompt="prompt",
    )

    # max_steps=5 兜底而非 LOOP_DETECTED
    assert result.status == SkillRunStatus.FAILED
    assert result.error_category == ErrorCategory.STEP_LIMIT_EXCEEDED


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


# ─────────────────────────────────────────────────────────────────────
# Agent 决策 / 规划 / 行动回归：防止循环 bug 再发生
# ─────────────────────────────────────────────────────────────────────


async def test_runner_feedback_buffer_only_carries_current_turn_tool_results(
    echo_manifest, execution_context, tool_broker, event_store
) -> None:
    """回归 task 01KPGQGC... 无进展循环 bug：

    feedback 必须是"下一轮要发给 LLM 的 buffer"，generate 成功消费后清空，
    否则下一轮会把已送过的 tool_result 再送一次，LLM prompt 里混入重复
    tool_result，误以为工具在返回相同答案，陷入"再调一次验证"循环。

    步骤：3 步，每步各一个 tool_call。验证 step N 的 feedback 只含
    step N-1 的 tool_result，不含更早的累积。
    """
    outputs = [
        SkillOutputEnvelope(
            content="",
            complete=False,
            tool_calls=[
                {
                    "tool_name": "system.echo",
                    "arguments": {"text": "q1"},
                    "tool_call_id": "call_1",
                }
            ],
        ),
        SkillOutputEnvelope(
            content="",
            complete=False,
            tool_calls=[
                {
                    "tool_name": "system.echo",
                    "arguments": {"text": "q2"},
                    "tool_call_id": "call_2",
                }
            ],
        ),
        SkillOutputEnvelope(content="done", complete=True),
    ]
    client = CaptureFeedbackClient(outputs)
    runner = SkillRunner(
        model_client=client, tool_broker=tool_broker, event_store=event_store
    )

    result = await runner.run(
        manifest=echo_manifest,
        execution_context=execution_context,
        skill_input={"text": "hi"},
        prompt="prompt",
    )

    assert result.status == SkillRunStatus.SUCCEEDED
    assert len(client.feedback_snapshots) == 3

    # step 1: 初始调用，feedback 为空
    assert client.feedback_snapshots[0] == []

    # step 2: 只带 step 1 的 tool_result（call_1），不能带更早的内容
    step2_ids = [fb.tool_call_id for fb in client.feedback_snapshots[1]]
    assert step2_ids == ["call_1"], (
        f"step 2 feedback 应只含 step 1 的 tool_result，实际 ids={step2_ids}"
    )

    # step 3: 只带 step 2 的 tool_result（call_2），不能累积 call_1
    step3_ids = [fb.tool_call_id for fb in client.feedback_snapshots[2]]
    assert step3_ids == ["call_2"], (
        f"step 3 feedback 必须只含 step 2 的 tool_result（call_2），"
        f"不得重新包含已消费过的 call_1。实际 ids={step3_ids}。"
        f"若 call_1 再次出现，说明 feedback buffer 未在 generate 成功后清空，"
        f"LLM 会在下一轮 prompt 里看到重复 tool_result → 触发决策循环。"
    )


async def test_runner_feedback_preserved_across_model_call_failure(
    echo_manifest, execution_context, tool_broker, event_store
) -> None:
    """回归：LLM 调用失败（网络波动 / 上游 500）时 feedback 必须保留，
    下一次 retry 仍要把上一轮 tool_result 送给 LLM；否则 retry 的 LLM
    会在空上下文下再决策一次，可能又发出同样的 tool_call 陷入无效循环。
    """
    outputs: list[Any] = [
        SkillOutputEnvelope(
            content="",
            complete=False,
            tool_calls=[
                {
                    "tool_name": "system.echo",
                    "arguments": {"text": "only"},
                    "tool_call_id": "call_once",
                }
            ],
        ),
        RuntimeError("上游 500"),  # step 2 失败
        SkillOutputEnvelope(content="recovered", complete=True),
    ]
    client = CaptureFeedbackClient(outputs)
    runner = SkillRunner(
        model_client=client, tool_broker=tool_broker, event_store=event_store
    )

    result = await runner.run(
        manifest=echo_manifest,
        execution_context=execution_context,
        skill_input={"text": "hi"},
        prompt="prompt",
    )

    assert result.status == SkillRunStatus.SUCCEEDED
    # 3 次 generate：step1 成功 tool_call / step2 失败 / step3 重试成功
    assert len(client.feedback_snapshots) == 3
    # step 2 失败：应该已经收到 step 1 的 tool_result
    assert [fb.tool_call_id for fb in client.feedback_snapshots[1]] == ["call_once"]
    # step 3 retry：**必须**仍能看到 step 1 的 tool_result（因为 step 2 没成功消费）
    assert [fb.tool_call_id for fb in client.feedback_snapshots[2]] == ["call_once"], (
        "LLM 调用失败后，feedback 必须保留以便 retry 拿到上一轮 tool_result，"
        "否则 retry 时 LLM 在'空上下文'下可能重复发起相同 tool_call"
    )


async def test_runner_loop_guard_warning_not_duplicated_across_rounds(
    echo_manifest, tool_broker, event_store
) -> None:
    """回归：_loop_guard 提示只应作为"当轮警示"一次性发给 LLM，不得累积到后续
    所有轮。否则 LLM 会在 prompt 里看到多条 _loop_guard，影响判断且膨胀上下文。
    """
    from octoagent.skills.models import (
        SkillExecutionContext,
        UsageLimits,
    )

    ctx = SkillExecutionContext(
        task_id="task-loop-guard-single",
        trace_id="trace-loop-guard-single",
        caller="worker",
        usage_limits=UsageLimits(repeat_warning_threshold=2, max_steps=10),
    )

    same_call = [
        {"tool_name": "system.echo", "arguments": {"text": "poll"}, "tool_call_id": "x"}
    ]
    outputs = [
        SkillOutputEnvelope(content="", complete=False, tool_calls=same_call),
        # 第 2 轮相同 signature → runner 注入 _loop_guard，下一轮 generate 可见
        SkillOutputEnvelope(content="", complete=False, tool_calls=same_call),
        # 第 3 轮 LLM 收到警示后切换（但这里仍复用 same_call 仅为 mock，关键检查的是
        # feedback 里 _loop_guard 只在 step=3 出现一次，step=4 不应再带）
        SkillOutputEnvelope(
            content="",
            complete=False,
            tool_calls=[
                {
                    "tool_name": "system.echo",
                    "arguments": {"text": "different"},
                    "tool_call_id": "y",
                }
            ],
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

    loop_guard_hits_per_step = [
        sum(1 for fb in snapshot if fb.tool_name == "_loop_guard")
        for snapshot in client.feedback_snapshots
    ]
    # step=3 应该看到 1 条 _loop_guard（step=2 注入），再之后的轮不应还带着
    assert loop_guard_hits_per_step.count(1) >= 1, (
        f"_loop_guard 警示必须至少在注入后的那一轮可见。实际 per-step 计数="
        f"{loop_guard_hits_per_step}"
    )
    assert all(count <= 1 for count in loop_guard_hits_per_step), (
        f"_loop_guard 警示不得在多轮累积，实际 per-step 计数="
        f"{loop_guard_hits_per_step}。累积会让 LLM 在 prompt 里反复看到同一警告，"
        f"产生噪音。"
    )


async def test_runner_event_store_failure_does_not_abort_main_flow(
    echo_manifest, execution_context, tool_broker
) -> None:
    """回归 Codex adversarial review #3：

    事件写入是观测副作用，绝不能让 event_store 临时异常中断 skill 主链路。
    之前 emit_task_event 在 runner 主 try 内抛错会被外层 except 误判为
    模型调用失败 → retry 但 tool 还没执行 → 下一轮 history 里出现孤立
    assistant.tool_calls，LLM 可能重放工具或基于不完整上下文推理。

    现在 _emit_event 内部吞掉异常并降级为 warning，主流程按原轨迹推进。
    """

    class FlakyEventStore:
        """会间歇性抛错的 event store，模拟 DB 锁、磁盘满等观测链路故障。"""

        def __init__(self) -> None:
            self.successful_events: list[Any] = []
            self.calls = 0

        async def append_event(self, event: Any) -> None:
            self.calls += 1
            if self.calls % 2 == 0:
                # 偶数次失败，奇数次成功
                raise RuntimeError("event store 临时故障")
            self.successful_events.append(event)

        async def get_next_task_seq(self, task_id: str) -> int:
            return self.calls

    flaky_store = FlakyEventStore()
    client = QueueModelClient(
        [
            SkillOutputEnvelope(
                content="call",
                complete=False,
                tool_calls=[
                    {
                        "tool_name": "system.echo",
                        "arguments": {"text": "probe"},
                        "tool_call_id": "call_observability",
                    }
                ],
            ),
            SkillOutputEnvelope(content="done", complete=True),
        ]
    )
    runner = SkillRunner(
        model_client=client, tool_broker=tool_broker, event_store=flaky_store
    )

    result = await runner.run(
        manifest=echo_manifest,
        execution_context=execution_context,
        skill_input={"text": "hi"},
        prompt="prompt",
    )

    # 主流程不受观测故障影响，仍然成功完成
    assert result.status == SkillRunStatus.SUCCEEDED, (
        f"event_store 间歇性失败不得让 skill 失败，实际 status={result.status}"
    )
    # tool 必须正常执行
    assert any(call[0] == "system.echo" for call in tool_broker.calls), (
        "观测失败场景下，tool 仍必须按流程执行 —— 否则下一轮 LLM 会看到孤立的 "
        "assistant.tool_calls 并可能重放工具"
    )
    # 验证 event_store 确实遇到过异常（否则测试不覆盖 bug 场景）
    assert flaky_store.calls > 1


async def test_runner_injected_feedback_uses_kind_enum_not_magic_tool_name(
    echo_manifest, tool_broker, event_store
) -> None:
    """runner 内部三种 feedback（loop_guard / tool error / runner error）必须带
    正确的 FeedbackKind，且 tool_name 必须用模块常量而非硬编码字符串。这样
    下游 model_client 才能按 kind 分派写入策略，而不是靠 tool_name 启发式判断。
    """
    from octoagent.skills.models import SkillExecutionContext, UsageLimits

    # 场景 1：触发 _loop_guard 注入
    ctx = SkillExecutionContext(
        task_id="task-kind",
        trace_id="trace-kind",
        caller="worker",
        usage_limits=UsageLimits(repeat_warning_threshold=2, max_steps=10),
    )
    same_call = [
        {"tool_name": "system.echo", "arguments": {"text": "poll"}, "tool_call_id": "id1"}
    ]
    client = CaptureFeedbackClient(
        [
            SkillOutputEnvelope(content="", complete=False, tool_calls=same_call),
            SkillOutputEnvelope(content="", complete=False, tool_calls=same_call),
            SkillOutputEnvelope(content="done", complete=True),
        ]
    )
    runner = SkillRunner(model_client=client, tool_broker=tool_broker, event_store=event_store)
    await runner.run(
        manifest=echo_manifest,
        execution_context=ctx,
        skill_input={"text": "hi"},
        prompt="prompt",
    )
    for snap in client.feedback_snapshots:
        for fb in snap:
            if fb.tool_name == FEEDBACK_SENDER_LOOP_GUARD:
                assert fb.kind == FeedbackKind.LOOP_GUARD, (
                    f"_loop_guard feedback 必须标记 kind=LOOP_GUARD，实际 {fb.kind}"
                )


async def test_runner_no_progress_loop_detected_on_repeated_probe_pattern(
    echo_manifest, tool_broker, event_store
) -> None:
    """端到端回归：LLM 误把 MCP 工具当 connectivity probe 反复调，每轮 content
    为空，no_progress 熔断必须生效。对应 production task 01KPGQGC... 的场景。
    """
    from octoagent.skills.models import SkillExecutionContext, UsageLimits

    ctx = SkillExecutionContext(
        task_id="task-probe-loop",
        trace_id="trace-probe-loop",
        caller="worker",
        usage_limits=UsageLimits(
            max_steps=30, no_progress_steps_threshold=8
        ),
    )

    # 模拟 LLM 反复用"每次略微不同的 message"调同一个 MCP 工具
    outputs = [
        SkillOutputEnvelope(
            content="",  # 关键：每轮都没有给用户的 user-facing 文本
            complete=False,
            tool_calls=[
                {
                    "tool_name": "mcp.x.ask_model",
                    "arguments": {"message": f"probe query variant {i}"},
                    "tool_call_id": f"call_probe_{i}",
                }
            ],
        )
        for i in range(15)
    ]
    client = QueueModelClient(outputs)
    # 让 mock tool 总是返回成功（模拟 production 里 is_error=false 的情况）
    tool_broker.set_result(
        "mcp.x.ask_model",
        ToolResult(
            output="probe success answer",
            is_error=False,
            error=None,
            duration=0.01,
            artifact_ref=None,
            tool_name="mcp.x.ask_model",
            truncated=False,
        ),
    )
    manifest = SkillManifest(
        skill_id="chat.general.inline",
        version="0.1.0",
        input_model=EchoInput,
        output_model=EchoInput,
        model_alias="main",
        tools_allowed=["mcp.x.ask_model"],
        permission_mode=SkillPermissionMode.INHERIT,
    )
    runner = SkillRunner(model_client=client, tool_broker=tool_broker, event_store=event_store)

    result = await runner.run(
        manifest=manifest,
        execution_context=ctx,
        skill_input={"text": "ping"},
        prompt="MCP 可以用了吗？",
    )

    assert result.status == SkillRunStatus.FAILED
    assert result.error_category == ErrorCategory.LOOP_DETECTED, (
        f"LLM 反复 tool_call 不产生 user-facing content，必须在 no_progress_steps "
        f"阈值（8）触及时熔断为 LOOP_DETECTED，实际 category={result.error_category}"
    )
    # 必须在 8~9 步内熔断，不能让 max_steps 兜底（那意味着 no_progress 检测漏掉了）
    assert result.steps <= 10, (
        f"no_progress 熔断应在 ~8 步内生效，实际消耗 {result.steps} 步。"
        f"如果接近 30，说明 no_progress 检测没起作用，只是 max_steps 硬兜底。"
    )
