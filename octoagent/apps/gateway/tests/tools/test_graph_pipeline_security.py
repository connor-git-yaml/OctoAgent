"""graph_pipeline F088 followup security 防回归测试。

补充 test_graph_pipeline_contract.py 之外的 security finding 防回归：
- LLM 不能自批 WAITING_APPROVAL：handler 签名不暴露 approved。
- 跨 task run 操作必须先校验 run 归属（防 leaked run_id 越权）。
- start 也必须有 task 上下文（防 subagent 路径下创建 parent_task_id=None 孤儿）。
- 选择链路两道闸必须放行：default profile 工具组含 orchestration；
  profile-first 候选含 graph_pipeline（兜底升级前已存的 stored profile）。
- _resolve_tool_availability 在 _graph_pipeline_tool 未绑定时返回 UNAVAILABLE
  （防降级时 LLM 被引到必然 rejected 的核心工具 → 重试循环）。
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest

from octoagent.core.models.tool_results import GraphPipelineResult


def _make_deps_with_tool(tool: object | None) -> MagicMock:
    deps = MagicMock()
    deps._graph_pipeline_tool = tool
    return deps


async def _capture_handler(deps) -> object:
    """注册一次并返回 handler。"""
    from octoagent.gateway.services.builtin_tools import graph_pipeline_tool

    captured: dict[str, object] = {}

    class _BrokerStub:
        async def try_register(self, schema, handler):
            captured["handler"] = handler

    await graph_pipeline_tool.register(_BrokerStub(), deps)
    handler = captured.get("handler")
    assert handler is not None, "register 未调 broker.try_register"
    return handler


# ---------------------------------------------------------------------------
# Finding 1（high）：LLM 不能自批 WAITING_APPROVAL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_signature_excludes_approved_param() -> None:
    """LLM-visible handler schema 不能含 approved 字段（防 LLM 自批 WAITING_APPROVAL）。

    审批门必须由人类操作员通过 Web UI / Telegram / REST API 完成；
    LLM 工具调用永远不应该携带 approved 参数。
    """
    fake_tool = MagicMock()
    fake_tool.execute = AsyncMock()
    handler = await _capture_handler(_make_deps_with_tool(fake_tool))

    sig = inspect.signature(handler)
    assert "approved" not in sig.parameters, (
        f"graph_pipeline_handler 不应再暴露 approved 参数，"
        f"实际 parameters={list(sig.parameters)}"
        "（Codex F088 followup high — LLM 自批审批门绕过人工 gate）"
    )


@pytest.mark.asyncio
async def test_handler_passes_approved_none_to_underlying(monkeypatch) -> None:
    """handler 永远以 approved=None 调底层 execute（即使 LLM 不能传，也要明确 hardcoded）。"""
    fake_tool = MagicMock()
    fake_tool.execute = AsyncMock(
        return_value=GraphPipelineResult(
            status="pending",
            target="demo",
            preview="ok",
            action="start",
        )
    )

    fake_ctx = MagicMock()
    fake_ctx.task_id = "task-001"
    monkeypatch.setattr(
        "octoagent.gateway.services.execution_context.get_current_execution_context",
        lambda: fake_ctx,
    )

    handler = await _capture_handler(_make_deps_with_tool(fake_tool))

    await handler(action="start", pipeline_id="demo", params={"x": 1})

    fake_tool.execute.assert_awaited_once()
    assert fake_tool.execute.await_args.kwargs["approved"] is None, (
        "handler 必须 hardcoded approved=None，禁止透传 LLM 输入"
    )


# ---------------------------------------------------------------------------
# Finding 2（high）：start / 操作类 action 必须有 task 上下文
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mutating_actions_reject_when_no_execution_context() -> None:
    """无 execution_context 时 start / status / resume / cancel / retry 必须拒绝。

    包含 start：subagent_lifecycle 走独立 SkillRunner、不绑定 gateway
    execution_context，handler 拿不到 task_id。若 start 不拒绝，会创建
    parent_task_id=None 的孤儿 pipeline，后续无法观察/恢复/取消。
    """
    fake_tool = MagicMock()
    fake_tool.execute = AsyncMock()
    handler = await _capture_handler(_make_deps_with_tool(fake_tool))

    for action in ("start", "status", "resume", "cancel", "retry"):
        result = await handler(
            action=action,
            run_id="run-x" if action != "start" else "",
            pipeline_id="demo" if action == "start" else "",
        )
        assert result.status == "rejected", (
            f"action={action} 应在无 task 上下文时 rejected，实际={result.status}"
        )
        assert "task" in (result.reason or "")
        # 底层 execute 不应被调用 —— task gate 提前阻断
        fake_tool.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_allowed_without_execution_context() -> None:
    """list 是只读 + 不创建 run，无 task 上下文也应放行。"""
    expected_result = GraphPipelineResult(
        status="written",
        target="pipeline_registry",
        preview="No pipelines",
        action="start",
    )
    fake_tool = MagicMock()
    fake_tool.execute = AsyncMock(return_value=expected_result)
    handler = await _capture_handler(_make_deps_with_tool(fake_tool))

    result = await handler(action="list")
    assert result is expected_result
    fake_tool.execute.assert_awaited_once()


# ---------------------------------------------------------------------------
# Finding 3（high）：跨 task run 归属校验
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_rejects_cross_task_run(monkeypatch) -> None:
    """run.task_id 对应的 child_task.parent_task_id 不等于当前 task → rejected。"""
    fake_run = MagicMock()
    fake_run.task_id = "child-task-other"

    fake_engine = MagicMock()
    fake_engine.get_pipeline_run = AsyncMock(return_value=fake_run)

    fake_child_task = MagicMock()
    fake_child_task.parent_task_id = "other-task"

    fake_task_store = MagicMock()
    fake_task_store.get_task = AsyncMock(return_value=fake_child_task)

    fake_tool = MagicMock()
    fake_tool._engine = fake_engine
    fake_tool.execute = AsyncMock()

    deps = MagicMock()
    deps._graph_pipeline_tool = fake_tool
    deps.stores = MagicMock()
    deps.stores.task_store = fake_task_store

    fake_ctx = MagicMock()
    fake_ctx.task_id = "current-task"
    monkeypatch.setattr(
        "octoagent.gateway.services.execution_context.get_current_execution_context",
        lambda: fake_ctx,
    )

    handler = await _capture_handler(deps)
    result = await handler(action="resume", run_id="run-other")

    assert result.status == "rejected"
    assert "不属于" in (result.reason or "") or "not" in (result.reason or "").lower()
    fake_tool.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_resume_accepts_when_child_parent_matches_current(monkeypatch) -> None:
    """run 对应的 child_task.parent_task_id == current task → 通过校验，调底层 execute。"""
    fake_run = MagicMock()
    fake_run.task_id = "child-task-mine"

    fake_engine = MagicMock()
    fake_engine.get_pipeline_run = AsyncMock(return_value=fake_run)

    fake_child_task = MagicMock()
    fake_child_task.parent_task_id = "current-task"

    fake_task_store = MagicMock()
    fake_task_store.get_task = AsyncMock(return_value=fake_child_task)

    expected_result = GraphPipelineResult(
        status="written",
        target="child-task-mine",
        action="resume",
        run_id="run-mine",
    )

    fake_tool = MagicMock()
    fake_tool._engine = fake_engine
    fake_tool.execute = AsyncMock(return_value=expected_result)

    deps = MagicMock()
    deps._graph_pipeline_tool = fake_tool
    deps.stores = MagicMock()
    deps.stores.task_store = fake_task_store

    fake_ctx = MagicMock()
    fake_ctx.task_id = "current-task"
    monkeypatch.setattr(
        "octoagent.gateway.services.execution_context.get_current_execution_context",
        lambda: fake_ctx,
    )

    handler = await _capture_handler(deps)
    result = await handler(
        action="resume",
        run_id="run-mine",
        input_data={"foo": "bar"},
    )

    assert result is expected_result
    fake_tool.execute.assert_awaited_once()
    kwargs = fake_tool.execute.await_args.kwargs
    assert kwargs["action"] == "resume"
    assert kwargs["run_id"] == "run-mine"
    assert kwargs["approved"] is None
    assert kwargs["task_id"] == "current-task"


# ---------------------------------------------------------------------------
# Finding 4（high）：选择链路两道闸必须放行 graph_pipeline
# ---------------------------------------------------------------------------


def test_unified_tool_groups_contains_orchestration() -> None:
    """_UNIFIED_TOOL_GROUPS 必须包含 \"orchestration\"，否则 default profile 不选 graph_pipeline。"""
    from octoagent.gateway.services.capability_pack import CapabilityPackService

    profile_src = inspect.getsource(CapabilityPackService._build_worker_profiles)
    assert '"orchestration"' in profile_src, (
        "_UNIFIED_TOOL_GROUPS 必须包含 \"orchestration\"，否则 graph_pipeline "
        "不会被 resolve_profile_first_tools 选中"
    )


def test_graph_pipeline_in_profile_first_candidates() -> None:
    """graph_pipeline 必须在 _profile_first_candidate_tool_names 中（绕开 stored profile 过滤）。

    resolve_worker_binding 对已存储 profile 优先用 stored_profile.default_tool_groups
    （非空时短路）；升级前已存的 profile 没有 orchestration 工具组，desired_tools
    走 default_tool_groups 过滤会丢掉 graph_pipeline。profile-first 候选不依赖
    default_tool_groups，是确保 graph_pipeline 兜底挂载的旁路。
    """
    from octoagent.gateway.services.capability_pack import CapabilityPackService

    candidates = CapabilityPackService._profile_first_candidate_tool_names()
    assert "graph_pipeline" in candidates, (
        "graph_pipeline 必须出现在 _profile_first_candidate_tool_names 中，"
        "否则升级前已存的 stored profile 不会挂载 graph_pipeline"
    )


# ---------------------------------------------------------------------------
# Finding 5（medium）：降级时 graph_pipeline 必须被标记 UNAVAILABLE
# ---------------------------------------------------------------------------


def test_resolve_tool_availability_unavailable_when_pipeline_tool_unbound() -> None:
    """_tool_deps._graph_pipeline_tool=None 时 graph_pipeline 必须 UNAVAILABLE。

    防 LLM 在降级路径下被引到必然 rejected 的核心工具 → 重试循环。
    """
    from octoagent.core.models import BuiltinToolAvailabilityStatus
    from octoagent.gateway.services.capability_pack import CapabilityPackService

    svc = CapabilityPackService.__new__(CapabilityPackService)
    svc._mcp_registry = None
    svc._task_runner = None
    svc._delegation_plane = None
    svc._mcp_installer = None
    svc._browser_sessions = {}
    svc._tool_deps = MagicMock()
    svc._tool_deps._graph_pipeline_tool = None

    status = svc._resolve_tool_availability("graph_pipeline")
    assert status == BuiltinToolAvailabilityStatus.UNAVAILABLE, (
        f"_graph_pipeline_tool=None 时应返回 UNAVAILABLE，实际={status}"
    )

    reason = svc._resolve_tool_availability_reason("graph_pipeline")
    assert reason == "graph_pipeline_tool_unbound", (
        f"未绑定时 reason 应为 graph_pipeline_tool_unbound，实际={reason!r}"
    )


def test_resolve_tool_availability_available_when_pipeline_tool_bound() -> None:
    """_tool_deps._graph_pipeline_tool 非 None 时 graph_pipeline AVAILABLE。"""
    from octoagent.core.models import BuiltinToolAvailabilityStatus
    from octoagent.gateway.services.capability_pack import CapabilityPackService

    svc = CapabilityPackService.__new__(CapabilityPackService)
    svc._mcp_registry = None
    svc._task_runner = None
    svc._delegation_plane = None
    svc._mcp_installer = None
    svc._browser_sessions = {}
    svc._tool_deps = MagicMock()
    svc._tool_deps._graph_pipeline_tool = MagicMock()

    status = svc._resolve_tool_availability("graph_pipeline")
    assert status == BuiltinToolAvailabilityStatus.AVAILABLE


def test_resolve_tool_availability_unavailable_when_tool_deps_missing() -> None:
    """_tool_deps 为 None 时（startup 之前）graph_pipeline 也应 UNAVAILABLE，防 NPE。"""
    from octoagent.core.models import BuiltinToolAvailabilityStatus
    from octoagent.gateway.services.capability_pack import CapabilityPackService

    svc = CapabilityPackService.__new__(CapabilityPackService)
    svc._mcp_registry = None
    svc._task_runner = None
    svc._delegation_plane = None
    svc._mcp_installer = None
    svc._browser_sessions = {}
    svc._tool_deps = None

    status = svc._resolve_tool_availability("graph_pipeline")
    assert status == BuiltinToolAvailabilityStatus.UNAVAILABLE
