"""graph_pipeline 工具 contract 验证。

目的：验证 graph_pipeline 通过 builtin_tools/graph_pipeline_tool.py 注册到
ToolBroker + ToolRegistry，使 LLM 能直接 tool_call（不必绕 tool_search 慢路径）。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


def test_graph_pipeline_entrypoints_agent_runtime_only() -> None:
    """graph_pipeline entrypoints 仅含 agent_runtime（与 delegate_task 同策略）。

    Web UI / Telegram 不直接调 orchestration 工具——应走专用 API / 命令。
    """
    from octoagent.gateway.services.builtin_tools.graph_pipeline_tool import _ENTRYPOINTS

    assert isinstance(_ENTRYPOINTS, frozenset), \
        f"_ENTRYPOINTS 应是 frozenset，实际: {type(_ENTRYPOINTS)}"
    assert frozenset({"agent_runtime"}) == _ENTRYPOINTS, \
        f"graph_pipeline entrypoints 应精确等于 {{agent_runtime}}，实际: {_ENTRYPOINTS}"


def test_graph_pipeline_in_core_tool_set() -> None:
    """graph_pipeline 进入 CoreToolSet.default() — LLM 第一轮就拿到完整 schema。

    回归保护：从 DEFERRED→CORE 是本次架构改造的关键决策。
    若有人不慎从 default() 移除，LLM 又会绕 tool_search 慢路径。
    """
    from octoagent.tooling.models import CoreToolSet

    core_set = CoreToolSet.default()
    assert core_set.is_core("graph_pipeline"), \
        "graph_pipeline 必须在 CoreToolSet.default() 中（治本 1 跳路径要求）"


@pytest.mark.asyncio
async def test_graph_pipeline_handler_rejects_when_tool_unbound() -> None:
    """deps._graph_pipeline_tool 未注入时，handler 返回 rejected GraphPipelineResult。

    保证 lifespan 早期 / pipeline_registry 创建失败场景下 LLM 收到清晰错误，
    不抛裸异常导致整轮失败。
    """
    from octoagent.gateway.services.builtin_tools.graph_pipeline_tool import register

    broker = MagicMock()
    broker.try_register = AsyncMock()

    deps = MagicMock()
    deps._graph_pipeline_tool = None

    captured: dict = {}

    async def _capture(meta, handler) -> None:
        captured["handler"] = handler

    broker.try_register.side_effect = _capture

    await register(broker, deps)
    handler = captured["handler"]

    result = await handler(action="list")
    assert result.status == "rejected"
    assert "not initialized" in (result.preview or "").lower() or \
           "not initialized" in (result.detail or "").lower()
    assert result.reason == "graph_pipeline_tool_unavailable"


@pytest.mark.asyncio
async def test_graph_pipeline_handler_forwards_to_underlying_tool(monkeypatch) -> None:
    """handler 调通时把所有参数 + execution_context.task_id 转发给 underlying.execute。

    F088 followup security 修复后，start / 操作类 action 必须有 task 上下文，
    所以本测试模拟 gateway execution_context 已绑定的场景（即 task_service
    process_task_with_llm 主路径）。
    """
    from octoagent.core.models.tool_results import GraphPipelineResult
    from octoagent.gateway.services.builtin_tools.graph_pipeline_tool import register

    broker = MagicMock()
    broker.try_register = AsyncMock()

    fake_underlying = MagicMock()
    fake_underlying.execute = AsyncMock(
        return_value=GraphPipelineResult(
            status="written",
            target="echo-test",
            preview="ok",
            detail="ok",
            action="start",
        )
    )

    deps = MagicMock()
    deps._graph_pipeline_tool = fake_underlying

    # 模拟 gateway execution_context 已绑定（start 的 task gate 要求）
    fake_ctx = MagicMock()
    fake_ctx.task_id = "parent-task-001"
    monkeypatch.setattr(
        "octoagent.gateway.services.execution_context.get_current_execution_context",
        lambda: fake_ctx,
    )

    captured: dict = {}

    async def _capture(meta, handler) -> None:
        captured["handler"] = handler

    broker.try_register.side_effect = _capture

    await register(broker, deps)
    handler = captured["handler"]

    result = await handler(
        action="start",
        pipeline_id="echo-test",
        params={"input": "hello"},
    )

    assert result.status == "written"
    fake_underlying.execute.assert_awaited_once()
    kwargs = fake_underlying.execute.call_args.kwargs
    assert kwargs["action"] == "start"
    assert kwargs["pipeline_id"] == "echo-test"
    assert kwargs["params"] == {"input": "hello"}
    assert kwargs["task_id"] == "parent-task-001"
    # F088 followup security：approved 永远 None，禁止 LLM 自批
    assert kwargs["approved"] is None


@pytest.mark.asyncio
async def test_graph_pipeline_registers_to_broker_and_registry() -> None:
    """register(broker, deps) 同时注册到 ToolBroker 和 ToolRegistry。"""
    from octoagent.gateway.harness.tool_registry import get_registry
    from octoagent.gateway.services.builtin_tools.graph_pipeline_tool import register

    broker = MagicMock()
    broker.try_register = AsyncMock()

    deps = MagicMock()
    deps._graph_pipeline_tool = None  # 注册时不需要真实实例

    await register(broker, deps)

    # ToolBroker 注册被调用
    broker.try_register.assert_awaited_once()

    # ToolRegistry 内能查到 graph_pipeline
    registry = get_registry()
    assert "graph_pipeline" in registry, \
        "graph_pipeline 应注册到全局 ToolRegistry"
    runtime_tools = registry.list_for_entrypoint("agent_runtime")
    runtime_names = {entry.name for entry in runtime_tools}
    assert "graph_pipeline" in runtime_names, \
        "graph_pipeline 应对 agent_runtime 入口可见"
    web_tools = registry.list_for_entrypoint("web")
    web_names = {entry.name for entry in web_tools}
    assert "graph_pipeline" not in web_names, \
        "graph_pipeline 不应对 web 入口可见（与 delegate_task 同策略）"


@pytest.mark.asyncio
async def test_graph_pipeline_handler_schema_reflection_succeeds() -> None:
    """reflect_tool_schema 能从 graph_pipeline_handler 装饰器生成 ToolMeta。

    覆盖 produces_write WriteResult 契约校验 + 类型注解完整性 + JSON Schema 反射。
    任一失败 => 启动期注册时 fail-fast，不会带病上线。
    """
    from octoagent.gateway.services.builtin_tools.graph_pipeline_tool import register
    from octoagent.tooling import reflect_tool_schema

    broker = MagicMock()
    broker.try_register = AsyncMock()
    deps = MagicMock()
    deps._graph_pipeline_tool = None

    captured: dict = {}

    async def _capture(meta, handler) -> None:
        captured["handler"] = handler

    broker.try_register.side_effect = _capture
    await register(broker, deps)
    handler = captured["handler"]

    meta = reflect_tool_schema(handler)
    assert meta.name == "graph_pipeline"
    assert meta.tool_group == "orchestration"
    assert "action" in meta.parameters_json_schema.get("properties", {}), \
        "graph_pipeline 必须暴露 action 参数给 LLM"
