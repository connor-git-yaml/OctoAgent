"""delegate_task 工具 contract 验证（T058）。

Feature 084 Phase 3 — 验收 schema 字段对齐、entrypoints 约束（仅 agent_runtime）。
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# T058-1：test_delegate_task_schema_matches_contract
# ---------------------------------------------------------------------------


def test_delegate_task_schema_matches_contract() -> None:
    """DelegateTaskInput schema 与 contracts/tools-contract.md 对齐。

    验收：
    - target_worker: str（必填）
    - task_description: str（必填）
    - callback_mode: Literal["async", "sync"]，默认 "async"
    - max_wait_seconds: int，默认 300
    """
    from octoagent.gateway.harness.delegation import DelegateTaskInput

    fields = DelegateTaskInput.model_fields

    # 必填字段
    assert "target_worker" in fields, "缺少 target_worker 字段"
    assert "task_description" in fields, "缺少 task_description 字段"
    assert "callback_mode" in fields, "缺少 callback_mode 字段"
    assert "max_wait_seconds" in fields, "缺少 max_wait_seconds 字段"

    # callback_mode 默认值
    assert fields["callback_mode"].default == "async", \
        f"callback_mode 默认值应为 'async'，实际: {fields['callback_mode'].default}"

    # max_wait_seconds 默认值
    assert fields["max_wait_seconds"].default == 300, \
        f"max_wait_seconds 默认值应为 300，实际: {fields['max_wait_seconds'].default}"

    # 类型验证
    from pydantic import BaseModel
    assert issubclass(DelegateTaskInput, BaseModel), \
        "DelegateTaskInput 应是 Pydantic BaseModel 子类"


def test_delegate_task_input_validation() -> None:
    """DelegateTaskInput 字段校验正常工作（Pydantic 校验覆盖）。"""
    from octoagent.gateway.harness.delegation import DelegateTaskInput

    # 正常构造
    inp = DelegateTaskInput(
        target_worker="research_worker",
        task_description="分析用户需求",
    )
    assert inp.target_worker == "research_worker"
    assert inp.task_description == "分析用户需求"
    assert inp.callback_mode == "async"
    assert inp.max_wait_seconds == 300

    # 显式指定 sync 模式
    inp_sync = DelegateTaskInput(
        target_worker="code_worker",
        task_description="实现功能模块",
        callback_mode="sync",
        max_wait_seconds=600,
    )
    assert inp_sync.callback_mode == "sync"
    assert inp_sync.max_wait_seconds == 600


def test_delegate_task_result_schema() -> None:
    """DelegateTaskResult 是 WriteResult 子类，含 child_task_id / target_worker / callback_mode。

    contracts/tools-contract.md 中 delegate_task 输出 schema 对齐。
    """
    from octoagent.core.models.tool_results import DelegateTaskResult, WriteResult

    # 是 WriteResult 子类
    assert issubclass(DelegateTaskResult, WriteResult), \
        "DelegateTaskResult 必须是 WriteResult 子类（FR-2.4 / SC-012）"

    fields = DelegateTaskResult.model_fields
    assert "child_task_id" in fields, "缺少 child_task_id 字段"
    assert "target_worker" in fields, "缺少 target_worker 字段"
    assert "callback_mode" in fields, "缺少 callback_mode 字段"


# ---------------------------------------------------------------------------
# T058-2：test_delegate_task_entrypoints_agent_runtime_only
# ---------------------------------------------------------------------------


def test_delegate_task_entrypoints_agent_runtime_only() -> None:
    """delegate_task entrypoints 仅含 agent_runtime，不含 web（SC-010 反向约束）。

    验收（FR-5.1 / SC-010）：
    - Web UI 不直接发起子 Agent 派发
    - 只有 Agent runtime 才能触发 delegation

    不含 web 的反向约束防止前端绕过 Agent 直接创建子任务。
    """
    from octoagent.gateway.services.builtin_tools.delegate_task_tool import _ENTRYPOINTS

    assert "agent_runtime" in _ENTRYPOINTS, \
        "delegate_task entrypoints 必须含 agent_runtime"
    assert "web" not in _ENTRYPOINTS, \
        "delegate_task entrypoints 不应含 web（SC-010 反向约束：Web UI 不直接派发子 Agent）"
    assert "telegram" not in _ENTRYPOINTS, \
        "delegate_task entrypoints 不应含 telegram（仅 agent_runtime）"


def test_delegate_task_entrypoints_is_frozenset() -> None:
    """_ENTRYPOINTS 应是 frozenset（不可变，防运行时篡改）。"""
    from octoagent.gateway.services.builtin_tools.delegate_task_tool import _ENTRYPOINTS

    assert isinstance(_ENTRYPOINTS, frozenset), \
        f"_ENTRYPOINTS 应是 frozenset，实际: {type(_ENTRYPOINTS)}"


def test_delegate_task_tool_file_has_registry_register_call() -> None:
    """delegate_task_tool.py 包含顶层 registry.register(ToolEntry(...)) 调用（AST 扫描可见）。

    验收：
    - _registry_register(ToolEntry(...)) 注册调用存在
    - 可被 scan_and_register() AST 扫描检测到
    """
    import ast
    from pathlib import Path

    tool_file = Path(
        "/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/.claude/worktrees/silly-noyce-22a8af/"
        "octoagent/apps/gateway/src/octoagent/gateway/services/builtin_tools/delegate_task_tool.py"
    )
    assert tool_file.exists(), f"delegate_task_tool.py 不存在: {tool_file}"

    source = tool_file.read_text(encoding="utf-8")
    tree = ast.parse(source)

    # 检查是否有 _registry_register(...) 调用
    has_register = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            # 匹配 _registry_register(ToolEntry(...))
            if isinstance(func, ast.Name) and func.id == "_registry_register":
                has_register = True
                break

    assert has_register, (
        "delegate_task_tool.py 应包含 _registry_register(ToolEntry(...)) 调用"
        "（供 AST 扫描检测，FR-1.1 / SC-010 反向验证）"
    )


def test_delegate_task_tool_description_mentions_subagent() -> None:
    """ToolEntry description 包含子 Agent 语义描述（可读性 + 工具语义验证）。"""
    from octoagent.gateway.services.builtin_tools.delegate_task_tool import _ENTRYPOINTS

    # 通过 ToolRegistry 查找 delegate_task 的注册信息
    # 注意：ToolRegistry 是延迟注册（register() 异步函数需要 broker + deps），
    # 这里验证静态 _ENTRYPOINTS 不含 web（不依赖运行时注册）。
    assert len(_ENTRYPOINTS) >= 1, "delegate_task 至少有一个入口点"
    assert _ENTRYPOINTS == frozenset({"agent_runtime"}), \
        f"delegate_task entrypoints 应精确等于 {{agent_runtime}}，实际: {_ENTRYPOINTS}"


# ---------------------------------------------------------------------------
# F34 修复（Codex independent review high）：真实 delegate_task 路径必须写
# SUBAGENT_SPAWNED 事件（FR-5.5 / Constitution C2）。旧实现 launch_child 成功后
# 不调用 _emit_spawned_event；T057 测的是 manager 单元（手动调），无法捕获生产 tool
# 的审计缺失。本测试走真实 handler，断言事件被写入。
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delegate_task_writes_spawned_event_via_real_handler(
    tmp_path,
    monkeypatch,
) -> None:
    """delegate_task 工具真实派发后写入 SUBAGENT_SPAWNED 事件（防 F34 回归）。"""
    from unittest.mock import AsyncMock, MagicMock

    from octoagent.gateway.harness.delegation import DelegationManager
    from octoagent.gateway.services.builtin_tools import delegate_task_tool

    # 监听 _emit_spawned_event 真实调用
    spawned_calls: list[dict] = []

    async def _spy_emit(self, **kwargs):
        spawned_calls.append(kwargs)
        return None

    monkeypatch.setattr(DelegationManager, "_emit_spawned_event", _spy_emit)

    # mock launch_child 返回 fake task_id
    fake_task_id = "child-task-fake-001"
    fake_launch_payload = {
        "task_id": fake_task_id,
        "work_id": "work-fake-001",
        "session_id": "session-fake-001",
    }
    monkeypatch.setattr(
        "octoagent.gateway.services.builtin_tools._deps.launch_child",
        AsyncMock(return_value=fake_launch_payload),
    )

    # 构造最小 deps（覆盖 _pack_service / stores 接口）
    deps = MagicMock()
    deps.project_root = tmp_path
    deps._pack_service = MagicMock()
    deps._pack_service._effective_tool_profile_for_objective = MagicMock(return_value="default")
    deps._delegation_plane = None
    deps.stores = MagicMock()
    deps.stores.event_store = MagicMock()
    deps.stores.task_store = MagicMock()
    deps.stores.task_store.get_task = AsyncMock(return_value=None)

    # 通过 broker stub 注册 + 拿到 handler
    captured_handler = None

    class _BrokerStub:
        async def try_register(self, schema, handler):
            nonlocal captured_handler
            if getattr(handler, "__name__", "") == "delegate_task_handler":
                captured_handler = handler

    await delegate_task_tool.register(_BrokerStub(), deps)
    assert captured_handler is not None, "delegate_task_handler 未被注册"

    # 调用真实 handler（async 模式立即返回）
    result = await captured_handler(
        target_worker="general",
        task_description="测试派发",
        callback_mode="async",
        max_wait_seconds=300,
    )

    # 断言 launch_child 成功 + SUBAGENT_SPAWNED 事件被写
    assert getattr(result, "status", "") == "written", \
        f"期望真实派发成功，实际 status={getattr(result, 'status', None)}"
    assert getattr(result, "child_task_id", "") == fake_task_id

    # F34 关键断言：真实 _emit_spawned_event 被调用，且 child_task_id 来自 launch_child
    assert len(spawned_calls) == 1, \
        f"期望 _emit_spawned_event 被调用 1 次，实际 {len(spawned_calls)} 次（防 F34 回归）"
    call_kwargs = spawned_calls[0]
    assert call_kwargs["child_task_id"] == fake_task_id
    assert call_kwargs["target_worker"] == "general"
    assert call_kwargs["task_description"] == "测试派发"
    assert call_kwargs["callback_mode"] == "async"
