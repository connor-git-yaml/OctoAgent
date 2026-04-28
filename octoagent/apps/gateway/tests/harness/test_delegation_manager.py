"""DelegationManager 单元测试（T057）。

Feature 084 Phase 3 — 验收深度限制、并发限制、黑名单、成功路径事件写入。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from octoagent.core.models.enums import EventType
from octoagent.core.models.task import RequesterInfo, Task, TaskPointers
from octoagent.core.store import create_store_group
from octoagent.gateway.harness.delegation import (
    DelegateTaskInput,
    DelegationContext,
    DelegationManager,
)
from ulid import ULID


# ---------------------------------------------------------------------------
# 辅助 fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def store_group(tmp_path: Path):
    """创建完整 schema 的 StoreGroup（EventStore + TaskStore）。"""
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    sg = await create_store_group(
        db_path=str(tmp_path / "test.db"),
        artifacts_dir=str(artifacts_dir),
    )
    yield sg
    await sg.conn.close()


async def _ensure_audit_task(sg, task_id: str) -> None:
    """确保审计 task 存在（外键约束要求）。"""
    try:
        existing = await sg.task_store.get_task(task_id)
        if existing is not None:
            return
    except Exception:
        pass
    now = datetime.now(timezone.utc)
    task = Task(
        task_id=task_id,
        created_at=now,
        updated_at=now,
        title=f"审计占位 task ({task_id})",
        trace_id=task_id,
        requester=RequesterInfo(channel="system", sender_id="system"),
        pointers=TaskPointers(),
    )
    await sg.task_store.create_task(task)


def _make_mgr(sg=None, blacklist: set[str] | None = None) -> DelegationManager:
    """构建 DelegationManager（可选注入 store_group）。"""
    event_store = sg.event_store if sg else None
    task_store = sg.task_store if sg else None
    return DelegationManager(
        blacklist=blacklist,
        event_store=event_store,
        task_store=task_store,
    )


def _make_ctx(
    task_id: str = "test-task-001",
    depth: int = 0,
    active_children: list[str] | None = None,
    target_worker: str = "research_worker",
) -> DelegationContext:
    return DelegationContext(
        task_id=task_id,
        depth=depth,
        target_worker=target_worker,
        active_children=active_children or [],
    )


def _make_input(
    target_worker: str = "research_worker",
    task_description: str = "分析用户需求并输出报告",
    callback_mode: str = "async",
) -> DelegateTaskInput:
    return DelegateTaskInput(
        target_worker=target_worker,
        task_description=task_description,
        callback_mode=callback_mode,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# T057-1：test_delegate_task_depth_exceeded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delegate_task_depth_exceeded() -> None:
    """depth >= MAX_DEPTH 时返回 depth_exceeded 错误，不写 SUBAGENT_SPAWNED 事件。

    验收（FR-5.2）：
    - ctx.depth = MAX_DEPTH = 2 → child_depth = 3 > MAX_DEPTH → 拒绝
    - result.success = False
    - result.error_code = "depth_exceeded"
    - SUBAGENT_SPAWNED 事件不写入（避免假数据）
    """
    mgr = _make_mgr()  # 无 event_store，验证不写事件

    # depth == MAX_DEPTH (2)，child_depth = 3 → 超限
    ctx = _make_ctx(depth=DelegationManager.MAX_DEPTH)
    inp = _make_input()

    result = await mgr.delegate(ctx, inp)

    assert not result.success, "depth 超限时应返回 success=False"
    assert result.error_code == "depth_exceeded", \
        f"error_code 应为 depth_exceeded，实际: {result.error_code}"
    assert result.child_task_id is None, "depth 超限时不应返回 child_task_id"
    assert result.reason is not None and "FR-5.2" in result.reason, \
        "reason 应包含 FR-5.2 引用"


@pytest.mark.asyncio
async def test_delegate_task_depth_within_limit() -> None:
    """depth < MAX_DEPTH 时约束通过（深度检查不误报）。"""
    mgr = _make_mgr()

    # depth = MAX_DEPTH - 1 = 1，child_depth = 2 == MAX_DEPTH → 通过
    ctx = _make_ctx(depth=DelegationManager.MAX_DEPTH - 1)
    inp = _make_input()

    result = await mgr.delegate(ctx, inp)

    assert result.success, \
        f"depth={DelegationManager.MAX_DEPTH - 1} 应通过约束，实际: {result.error_code}"
    assert result.error_code is None


# ---------------------------------------------------------------------------
# T057-2：test_delegate_task_capacity_exceeded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delegate_task_capacity_exceeded() -> None:
    """active_children >= MAX_CONCURRENT_CHILDREN 时返回 CAPACITY_EXCEEDED 错误。

    验收（FR-5.3）：
    - active_children = [id1, id2, id3]（3 个 = MAX_CONCURRENT_CHILDREN）→ 拒绝
    - result.success = False
    - result.error_code = "CAPACITY_EXCEEDED"
    """
    mgr = _make_mgr()
    max_c = DelegationManager.MAX_CONCURRENT_CHILDREN

    ctx = _make_ctx(
        depth=0,
        active_children=[f"task-child-{i}" for i in range(max_c)],
    )
    inp = _make_input()

    result = await mgr.delegate(ctx, inp)

    assert not result.success, "活跃子任务 = MAX 时应返回 success=False"
    assert result.error_code == "CAPACITY_EXCEEDED", \
        f"error_code 应为 CAPACITY_EXCEEDED，实际: {result.error_code}"
    assert result.reason is not None and "FR-5.3" in result.reason, \
        "reason 应包含 FR-5.3 引用"


@pytest.mark.asyncio
async def test_delegate_task_capacity_within_limit() -> None:
    """active_children < MAX 时并发检查通过（不误报）。"""
    mgr = _make_mgr()
    max_c = DelegationManager.MAX_CONCURRENT_CHILDREN

    ctx = _make_ctx(
        depth=0,
        active_children=[f"task-child-{i}" for i in range(max_c - 1)],
    )
    inp = _make_input()

    result = await mgr.delegate(ctx, inp)

    assert result.success, \
        f"active_children={max_c - 1} 应通过约束，实际: {result.error_code}"


# ---------------------------------------------------------------------------
# T057-3：test_delegate_task_blacklist_blocks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delegate_task_blacklist_blocks() -> None:
    """黑名单 Worker 被拒绝，SUBAGENT_SPAWNED 事件不写入（FR-5.4）。

    验收：
    - target_worker 在 blacklist → result.success = False
    - result.error_code = "blacklist_blocked"
    - 无 event_store 时不写事件（避免假数据）
    """
    blacklisted = "evil_worker"
    mgr = _make_mgr(blacklist={blacklisted})

    ctx = _make_ctx(depth=0, target_worker=blacklisted)
    inp = _make_input(target_worker=blacklisted)

    result = await mgr.delegate(ctx, inp)

    assert not result.success, "黑名单 Worker 应被拒绝"
    assert result.error_code == "blacklist_blocked", \
        f"error_code 应为 blacklist_blocked，实际: {result.error_code}"
    assert result.reason is not None and "FR-5.4" in result.reason, \
        "reason 应包含 FR-5.4 引用"


@pytest.mark.asyncio
async def test_delegate_task_blacklist_add_remove() -> None:
    """动态加入/移除黑名单（运行时配置扩展）。"""
    mgr = _make_mgr()
    worker = "dangerous_worker"

    ctx = _make_ctx(depth=0, target_worker=worker)
    inp = _make_input(target_worker=worker)

    # 初始不在黑名单：通过
    result_before = await mgr.delegate(ctx, inp)
    assert result_before.success, "初始不在黑名单时应通过"

    # 加入黑名单
    mgr.add_to_blacklist(worker)

    result_after = await mgr.delegate(ctx, inp)
    assert not result_after.success, "加入黑名单后应被拒绝"
    assert result_after.error_code == "blacklist_blocked"

    # 移除黑名单
    mgr.remove_from_blacklist(worker)

    result_restored = await mgr.delegate(ctx, inp)
    assert result_restored.success, "移除黑名单后应再次通过"


# ---------------------------------------------------------------------------
# T057-4：test_delegate_task_success_writes_event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delegate_task_success_writes_event(store_group) -> None:
    """通过所有约束时约束检查成功，_emit_spawned_event 可写 SUBAGENT_SPAWNED 事件。

    验收（FR-5.5）：
    - 手动调用 _emit_spawned_event（因为 DelegationManager.delegate 通过时不自动写，
      由 delegate_task_tool 负责）
    - SUBAGENT_SPAWNED 事件写入，payload 含 child_task_id / target_worker / depth
    - Constitution C2 合规
    """
    await _ensure_audit_task(store_group, "_delegation_manager_audit")

    mgr = _make_mgr(store_group)
    ctx = _make_ctx(depth=0, task_id="_delegation_manager_audit")
    inp = _make_input(target_worker="research_worker")

    # DelegationManager.delegate 约束检查通过
    result = await mgr.delegate(ctx, inp)
    assert result.success, f"约束检查应通过，实际: {result.error_code}"

    # 手动调用 _emit_spawned_event（模拟 delegate_task_tool 在真实派发后写事件）
    child_task_id = str(ULID())
    await mgr._emit_spawned_event(
        task_id="_delegation_manager_audit",
        child_task_id=child_task_id,
        target_worker="research_worker",
        depth=1,
        task_description="分析用户需求并输出报告",
        callback_mode="async",
    )

    # 验证 SUBAGENT_SPAWNED 事件写入
    events = await store_group.event_store.get_events_for_task("_delegation_manager_audit")
    spawned_events = [e for e in events if e.type == EventType.SUBAGENT_SPAWNED]
    assert spawned_events, "应写入 SUBAGENT_SPAWNED 事件（Constitution C2）"

    spawned = spawned_events[0]
    payload = spawned.payload
    assert payload.get("child_task_id") == child_task_id
    assert payload.get("target_worker") == "research_worker"
    assert payload.get("depth") == 1
    assert payload.get("callback_mode") == "async"


@pytest.mark.asyncio
async def test_delegate_task_all_checks_order() -> None:
    """约束检查顺序：depth → capacity → blacklist（按 spec 顺序，不可调换）。

    当 depth 超限 AND 黑名单命中时，应先报 depth_exceeded（depth 先检查）。
    """
    mgr = _make_mgr(blacklist={"evil_worker"})

    # depth 超限 + 黑名单命中
    ctx = _make_ctx(
        depth=DelegationManager.MAX_DEPTH,
        target_worker="evil_worker",
    )
    inp = _make_input(target_worker="evil_worker")

    result = await mgr.delegate(ctx, inp)

    assert result.error_code == "depth_exceeded", \
        f"depth 应先于 blacklist 检查，实际: {result.error_code}"
