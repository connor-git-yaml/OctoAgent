"""F085 T2 测试：subagents.spawn 接入 DelegationManager 约束验证。

验证 subagents.spawn 在 DelegationManager 约束下行为正确（spec FR-5 安全 gap 修复）：
- depth ≥ MAX_DEPTH (2)：reject，不实际派发
- active_children ≥ MAX_CONCURRENT_CHILDREN (3)：reject
- target_worker 在 blacklist：reject
- 正常路径：通过约束 + launch_child 派发
- 部分成功：批量 objectives 中部分超约束 → 已派发的不撤销，超约束的不派发
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def deps_with_stores(tmp_path: Path):
    """构造真实 ToolDeps（含 store_group），mock pack_service 用于 launch_child。"""
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    store_group = await create_store_group(
        db_path=str(tmp_path / "test.db"),
        artifacts_dir=str(artifacts_dir),
    )

    # 预创建 audit task 防 FK violation
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

    # mock pack_service：launch_child 内部调用
    pack_service_mock = MagicMock()
    pack_service_mock._effective_tool_profile_for_objective.return_value = "default"
    pack_service_mock._launch_child_task = AsyncMock(side_effect=lambda **kwargs: {
        "task_id": f"child-{kwargs.get('objective', 'unknown')[:20]}",
        "work_id": "work-1",
        "session_id": "sess-1",
        "worker_type": kwargs.get("worker_type", "general"),
        "objective": kwargs.get("objective", ""),
        "tool_profile": "default",
        "parent_task_id": "",
        "parent_work_id": "",
        "target_kind": "subagent",
        "title": kwargs.get("title", ""),
        "thread_id": "default",
        "worker_plan_id": "",
    })

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


async def _get_spawn_handler(deps: ToolDeps, monkeypatch=None):
    """注册 delegation_tools 后从 broker 捕获 subagents.spawn handler。

    若传 monkeypatch 则 mock launch_child（避免 execution_context 依赖）；
    返回真实 spawn handler 用于测 DelegationManager 约束逻辑。
    """
    if monkeypatch is not None:
        from octoagent.gateway.services.builtin_tools import delegation_tools as _dt

        async def _mock_launch_child(deps, *, objective, worker_type, target_kind,
                                       tool_profile="minimal", title=""):
            return {
                "task_id": f"child-{objective[:20]}",
                "work_id": "work-1",
                "session_id": "sess-1",
                "worker_type": worker_type,
                "objective": objective,
                "tool_profile": tool_profile,
                "parent_task_id": "",
                "parent_work_id": "",
                "target_kind": target_kind,
                "title": title,
                "thread_id": "default",
                "worker_plan_id": "",
            }
        monkeypatch.setattr(_dt, "launch_child", _mock_launch_child)

    captured: dict[str, Any] = {}

    class _CaptureBroker:
        async def try_register(self, meta, handler):
            captured[meta.name] = handler

    await register(_CaptureBroker(), deps)
    handler = captured.get("subagents.spawn")
    assert handler is not None, "subagents.spawn 未注册"
    return handler


# ---------------------------------------------------------------------------
# T2 验证测试
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_passes_when_no_constraints(deps_with_stores, monkeypatch) -> None:
    """正常路径：无 execution context 时 depth=0 / active_children=[]，约束通过 + 派发成功。"""
    handler = await _get_spawn_handler(deps_with_stores, monkeypatch)
    result = await handler(objective="find latest news")
    assert result.status == "written", f"expected written, got {result.status}: {result.reason}"
    assert result.requested == 1
    assert result.created == 1
    assert len(result.children) == 1
    assert result.children[0].task_id  # 真实派发产生 task_id


@pytest.mark.asyncio
async def test_spawn_partial_reject_keeps_successful(deps_with_stores, monkeypatch) -> None:
    """批量 objectives + active_children 累加：第 4 个会触发 max_concurrent=3 拒绝。

    DelegationManager 看到当前 spawn 已派发 3 个后，第 4 个 reject。
    前 3 个真派发不撤销。
    """
    handler = await _get_spawn_handler(deps_with_stores, monkeypatch)
    objectives = [f"task {i}" for i in range(5)]  # 5 个，第 4-5 应被拒
    result = await handler(objectives=objectives)

    # F085: DelegationManager max_concurrent=3 → 前 3 个派发，后 2 个 reject
    assert result.status == "written", f"部分成功应是 written: {result.status} / {result.reason}"
    assert result.requested == 5
    assert result.created == 3, f"应只创建 3 个（max_concurrent=3），实际 {result.created}"
    # preview 应说明部分约束拒绝
    assert "约束拒绝" in (result.preview or ""), f"preview 应提到约束拒绝: {result.preview}"


@pytest.mark.asyncio
async def test_spawn_all_blocked_returns_rejected(deps_with_stores, monkeypatch) -> None:
    """全部 objective 被约束拒绝时 status=rejected（防 LLM 误以为派发成功用假 task_id）。

    用 monkeypatch 把 MAX_CONCURRENT_CHILDREN 临时改成 0 触发全拒。
    """
    from octoagent.gateway.harness import delegation as _delegation

    monkeypatch.setattr(_delegation.DelegationManager, "MAX_CONCURRENT_CHILDREN", 0)
    handler = await _get_spawn_handler(deps_with_stores, monkeypatch)
    result = await handler(objectives=["a", "b"])

    assert result.status == "rejected", f"全拒应是 rejected: {result.status}"
    assert result.created == 0
    assert len(result.children) == 0
    assert "约束" in (result.reason or "") or "DelegationManager" in (result.reason or "")


@pytest.mark.asyncio
async def test_spawn_blocks_when_target_blacklisted(deps_with_stores, monkeypatch) -> None:
    """blacklist 命中时 reject 该 objective（不实际派发）。

    通过 monkeypatch 在 spawn 内部构造 DelegationManager 时注入 blacklist。
    """
    # 重写 DelegationManager.__init__ 默认带 blacklist={"general"}
    from octoagent.gateway.harness import delegation as _delegation
    _orig_init = _delegation.DelegationManager.__init__

    def _patched_init(self, **kwargs):
        kwargs.setdefault("blacklist", {"general"})
        _orig_init(self, **kwargs)
    monkeypatch.setattr(_delegation.DelegationManager, "__init__", _patched_init)

    handler = await _get_spawn_handler(deps_with_stores, monkeypatch)
    result = await handler(objective="something", worker_type="general")

    assert result.status == "rejected", f"blacklisted worker 应 reject: {result.status}"
    assert "blacklist" in (result.reason or "").lower() or "黑名单" in (result.reason or "")
