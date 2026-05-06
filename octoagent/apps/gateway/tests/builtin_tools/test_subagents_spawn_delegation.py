"""F085 T2 / F092 Phase C 测试：subagents.spawn → plane.spawn_child 切换后行为。

F092 Phase C 修订：
- 原测试 monkeypatch `launch_child` helper（已删除，工具层不再直接调）
- 改为 mock plane.spawn_child；底层 DelegationManager gate 集成已由
  test_delegation_plane_spawn_child.py（plane 层 16 测试）覆盖
- 工具层测试仅验证：spawn 循环 / skipped_objectives 聚合 / status="written"|"rejected" 决策

验证 subagents.spawn 工具层逻辑（不再覆盖底层 gate，已在 plane 单测覆盖）：
- 单 objective 成功路径 → status=written / created=1
- 批量 partial reject → status=written + created=N + preview 含约束拒绝信息
- 批量 all reject → status=rejected
- emit_audit_event=False 透传给 plane（保持 subagents.spawn 不写审计事件不变量）
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from octoagent.core.models.task import RequesterInfo, Task, TaskPointers
from octoagent.core.store import create_store_group
from octoagent.gateway.services.builtin_tools._deps import ToolDeps
from octoagent.gateway.services.builtin_tools.delegation_tools import register
from octoagent.gateway.services.delegation_plane import SpawnChildResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_written(task_id: str, objective: str = "", title: str = "") -> SpawnChildResult:
    return SpawnChildResult(
        status="written",
        task_id=task_id,
        created=True,
        thread_id=f"thread-{task_id}",
        target_kind="subagent",
        worker_type="general",
        tool_profile="default",
        parent_task_id="parent-task",
        parent_work_id="parent-work",
        title=title,
        objective=objective,
    )


def _make_rejected(reason: str, error_code: str = "CAPACITY_EXCEEDED") -> SpawnChildResult:
    return SpawnChildResult(
        status="rejected",
        error_code=error_code,
        reason=reason,
    )


@pytest_asyncio.fixture
async def deps_with_stores(tmp_path: Path):
    """构造 ToolDeps（含 store_group + mock plane / pack_service）。"""
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    store_group = await create_store_group(
        db_path=str(tmp_path / "test.db"),
        artifacts_dir=str(artifacts_dir),
    )

    # 预创建 audit task 防 FK violation（保留以兼容历史）
    audit_task = Task(
        task_id="_subagents_spawn_audit",
        created_at=datetime.now(tz=timezone.utc),
        updated_at=datetime.now(tz=timezone.utc),
        title="audit",
        requester=RequesterInfo(channel="system", sender_id="system"),
        pointers=TaskPointers(),
        trace_id="_subagents_spawn_audit",
    )
    await store_group.task_store.create_task(audit_task)

    pack_service_mock = MagicMock()
    pack_service_mock._effective_tool_profile_for_objective.return_value = "default"

    deps = ToolDeps(
        project_root=tmp_path,
        stores=store_group,
        tool_broker=MagicMock(),
        tool_index=MagicMock(),
        skill_discovery=MagicMock(),
        memory_console_service=MagicMock(),
        memory_runtime_service=MagicMock(),
        _pack_service=pack_service_mock,
    )

    yield deps

    await store_group.conn.close()


async def _bind_mock_plane_and_capture_handler(
    deps: ToolDeps,
    monkeypatch,
    *,
    spawn_child_side_effect=None,
    spawn_child_return_value=None,
):
    """注入 mock plane + 捕获 subagents.spawn handler。

    设置 deps._delegation_plane = MagicMock；
    spawn_child = AsyncMock with side_effect / return_value；
    同时 monkeypatch current_work_context 提供 parent_task / parent_work
    （用 monkeypatch 而非直接 setattr，避免污染其他测试）。
    """
    mock_plane = MagicMock()
    if spawn_child_side_effect is not None:
        mock_plane.spawn_child = AsyncMock(side_effect=spawn_child_side_effect)
    elif spawn_child_return_value is not None:
        mock_plane.spawn_child = AsyncMock(return_value=spawn_child_return_value)
    else:
        mock_plane.spawn_child = AsyncMock(return_value=_make_written("default-task"))
    deps._delegation_plane = mock_plane

    fake_context = MagicMock(work_id="parent-work-id")
    fake_parent_task = MagicMock(task_id="parent-task-id", depth=0,
                                  thread_id="parent-thread")

    async def _fake_current_work_context(_deps):
        return fake_context, fake_parent_task

    fake_parent_work = MagicMock(work_id="parent-work-id")

    async def _fake_get_work(work_id):
        return fake_parent_work

    deps.stores.work_store.get_work = _fake_get_work  # type: ignore[assignment]

    # 用 monkeypatch.setattr 替换 delegation_tools 模块的 current_work_context 引用
    # （测试结束后自动还原，避免污染 test_capability_pack_tools 等其他测试）
    from octoagent.gateway.services.builtin_tools import delegation_tools as _dt
    monkeypatch.setattr(_dt, "current_work_context", _fake_current_work_context)

    captured: dict[str, Any] = {}

    class _CaptureBroker:
        async def try_register(self, meta, handler):
            captured[meta.name] = handler

    await register(_CaptureBroker(), deps)
    handler = captured.get("subagents.spawn")
    assert handler is not None, "subagents.spawn 未注册"
    return handler, mock_plane


# ---------------------------------------------------------------------------
# 工具层测试（plane 已 mock，不覆盖底层 DelegationManager gate）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_passes_when_no_constraints(deps_with_stores, monkeypatch) -> None:
    """正常路径：plane.spawn_child 返回 written → 工具返回 status=written。"""
    handler, mock_plane = await _bind_mock_plane_and_capture_handler(
        deps_with_stores, monkeypatch,
        spawn_child_return_value=_make_written("child-1"),
    )
    result = await handler(objective="find latest news")

    assert result.status == "written"
    assert result.requested == 1
    assert result.created == 1
    assert len(result.children) == 1
    assert result.children[0].task_id == "child-1"

    # 验证调用 plane.spawn_child 时传了正确的关键参数
    assert mock_plane.spawn_child.await_count == 1
    call_kwargs = mock_plane.spawn_child.await_args.kwargs
    assert call_kwargs["spawned_by"] == "subagents_spawn"
    assert call_kwargs["emit_audit_event"] is False  # 关键：subagents.spawn 不写审计
    assert call_kwargs["audit_task_fallback"] == "_subagents_spawn_audit"


@pytest.mark.asyncio
async def test_spawn_partial_reject_keeps_successful(deps_with_stores, monkeypatch) -> None:
    """批量 5 objectives，前 3 written + 后 2 rejected → status=written + created=3 + preview 含约束拒绝。"""
    side_effect_values = [
        _make_written(f"child-{i}", objective=f"task {i}") for i in range(3)
    ] + [
        _make_rejected("活跃子任务数 3 ≥ 3", error_code="CAPACITY_EXCEEDED")
        for _ in range(2)
    ]

    handler, mock_plane = await _bind_mock_plane_and_capture_handler(
        deps_with_stores, monkeypatch,
        spawn_child_side_effect=side_effect_values,
    )
    result = await handler(objectives=[f"task {i}" for i in range(5)])

    assert result.status == "written", f"部分成功应是 written: {result.status} / {result.reason}"
    assert result.requested == 5
    assert result.created == 3
    assert "约束拒绝" in (result.preview or "")
    assert mock_plane.spawn_child.await_count == 5


@pytest.mark.asyncio
async def test_spawn_all_blocked_returns_rejected(deps_with_stores, monkeypatch) -> None:
    """全部 objective 被 plane.spawn_child 拒绝 → status=rejected（防 LLM 误以为派发成功）。"""
    handler, _ = await _bind_mock_plane_and_capture_handler(
        deps_with_stores, monkeypatch,
        spawn_child_side_effect=[
            _make_rejected("max_concurrent=0", error_code="CAPACITY_EXCEEDED")
            for _ in range(2)
        ],
    )
    result = await handler(objectives=["a", "b"])

    assert result.status == "rejected"
    assert result.created == 0
    assert len(result.children) == 0
    assert "DelegationManager" in (result.reason or "")


@pytest.mark.asyncio
async def test_spawn_blocks_when_target_blacklisted(deps_with_stores, monkeypatch) -> None:
    """blacklist 命中（plane 返回 rejected with blacklist_blocked）→ 工具返回 rejected。"""
    handler, _ = await _bind_mock_plane_and_capture_handler(
        deps_with_stores, monkeypatch,
        spawn_child_return_value=_make_rejected(
            "目标 Worker 'general' 在黑名单中", error_code="blacklist_blocked"
        ),
    )
    result = await handler(objective="something", worker_type="general")

    assert result.status == "rejected"
    assert "黑名单" in (result.reason or "") or "blacklist" in (result.reason or "").lower()


@pytest.mark.asyncio
async def test_spawn_propagates_launch_raise_from_plane(deps_with_stores, monkeypatch) -> None:
    """plane.spawn_child raise（含 enforce / task_runner 错误）→ subagents.spawn
    propagate 给 broker（保持 F085 worker→worker 拒绝 invariant：result.is_error=True）。"""
    handler, _ = await _bind_mock_plane_and_capture_handler(
        deps_with_stores, monkeypatch,
        spawn_child_side_effect=RuntimeError(
            "worker runtime cannot delegate to another worker"
        ),
    )

    # spawn_child raise → subagents.spawn handler propagate（不 catch）
    with pytest.raises(RuntimeError, match="worker runtime cannot delegate"):
        await handler(objective="test enforce raise", target_kind="worker")


@pytest.mark.asyncio
async def test_spawn_batch_propagates_raise_at_second_objective(
    deps_with_stores, monkeypatch
) -> None:
    """batch loop 中第 2 个 spawn_child raise → propagate（不返回 partial payload）。
    Codex Phase C MEDIUM 2 锁定：旧行为是 raise 直接 propagate，已派发的前 N-1 个不会
    出现在工具结果中（因为 raise 跳出 handler）。"""
    handler, mock_plane = await _bind_mock_plane_and_capture_handler(
        deps_with_stores, monkeypatch,
        spawn_child_side_effect=[
            _make_written("child-0", objective="task 0"),
            RuntimeError("worker runtime cannot delegate to another worker"),
            _make_written("child-2", objective="task 2"),  # 不会被调到
        ],
    )

    with pytest.raises(RuntimeError, match="worker runtime cannot delegate"):
        await handler(objectives=[f"task {i}" for i in range(3)], target_kind="subagent")

    # 第 1 个已成功（mock 返回 written），第 2 个 raise propagate；第 3 个未被调
    assert mock_plane.spawn_child.await_count == 2  # 不是 3
