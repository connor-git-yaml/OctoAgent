"""F092 Phase A/C: DelegationPlane.spawn_child 统一编排入口集成测试。

验收点（Codex MEDIUM 3 修订要求：集成层必须覆盖 gate，不能 mock 掉）：
- gate 通过 + launch 成功 → status="written"
- gate 拒绝（depth/capacity/blacklist）→ status="rejected" + error_code/reason 透传
- gate 通过但 launch raise → 直接 propagate（不再有 launch_raised 状态——
  Phase C 修订：launch error 由调用方各自 try/except 处理，保持原 builtin_tools
  对 launch error 的差异化处理）
- emit_audit_event=True → 调用 _emit_spawned_event
- emit_audit_event=False → 不调用 _emit_spawned_event
- depth/active_children 推断容错（list_descendant_works 失败时降级为空列表）
- audit_task_fallback 透传到 DelegationContext.task_id
- additional_active_children 累加 + 真实 DelegationManager batch capacity gate（HIGH 3 修订）
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from octoagent.gateway.harness.delegation import (
    DelegateResult,
    DelegationManager,
)
from octoagent.gateway.services.delegation_plane import (
    DelegationPlaneService,
    SpawnChildResult,
)


class _StubTask:
    """模拟 parent_task（仅含 spawn_child 需要的字段）。"""

    def __init__(self, task_id: str, depth: int = 0) -> None:
        self.task_id = task_id
        self.depth = depth


class _StubWork:
    """模拟 parent_work。"""

    def __init__(self, work_id: str) -> None:
        self.work_id = work_id


class _StubManager:
    """记录 delegate / _emit_spawned_event 调用的桩。"""

    def __init__(
        self,
        *,
        delegate_result: DelegateResult,
    ) -> None:
        self._delegate_result = delegate_result
        self.delegate_calls: list[tuple[Any, Any]] = []
        self.emit_calls: list[dict[str, Any]] = []

    async def delegate(self, ctx, gate_input):
        self.delegate_calls.append((ctx, gate_input))
        return self._delegate_result

    async def _emit_spawned_event(self, **kwargs):
        self.emit_calls.append(kwargs)


class _CapabilityPackStub:
    def __init__(
        self,
        *,
        launch_payload: dict[str, Any] | None = None,
        launch_exception: Exception | None = None,
    ) -> None:
        self._launch_payload = launch_payload
        self._launch_exception = launch_exception
        self.launch_calls: list[dict[str, Any]] = []

    async def _launch_child_task(self, **kwargs):
        self.launch_calls.append(kwargs)
        if self._launch_exception is not None:
            raise self._launch_exception
        parent_task = kwargs.get("parent_task")
        parent_work = kwargs.get("parent_work")
        # task_id 基于 objective 生成，避免 batch 测试 dedupe 导致 active_children 永远只有 1 个
        objective = kwargs.get("objective", "default")
        task_id_for_objective = f"child-{objective[:30].replace(' ', '-')}"
        base = {
            "task_id": task_id_for_objective,
            "created": True,
            "thread_id": f"thread-{task_id_for_objective}",
            "target_kind": kwargs.get("target_kind", ""),
            "worker_type": kwargs.get("worker_type", ""),
            "tool_profile": kwargs.get("tool_profile", ""),
            "parent_task_id": getattr(parent_task, "task_id", ""),
            "parent_work_id": getattr(parent_work, "work_id", ""),
            "title": kwargs.get("title", ""),
            "objective": kwargs.get("objective", ""),
            "worker_plan_id": kwargs.get("plan_id", ""),
        }
        if self._launch_payload:
            base.update(self._launch_payload)
        return base


def _make_plane_with_capability_pack_stub(
    *,
    launch_payload: dict[str, Any] | None = None,
    launch_exception: Exception | None = None,
    descendants: list | None = None,
    descendants_exc: Exception | None = None,
) -> DelegationPlaneService:
    """构造 plane（capability_pack / list_descendant_works 用 stub 替换）。"""
    plane = DelegationPlaneService.__new__(DelegationPlaneService)
    plane._stores = type("S", (), {"event_store": None, "task_store": None})()
    plane._capability_pack = _CapabilityPackStub(
        launch_payload=launch_payload,
        launch_exception=launch_exception,
    )

    async def _list_descendant_works(work_id: str):
        if descendants_exc is not None:
            raise descendants_exc
        return descendants or []

    plane.list_descendant_works = _list_descendant_works  # type: ignore[assignment]
    return plane


# ============================================================
# gate 拒绝路径
# ============================================================


@pytest.mark.parametrize(
    "error_code,reason",
    [
        ("depth_exceeded", "派发深度超过最大值"),
        ("CAPACITY_EXCEEDED", "活跃子任务数 ≥ 3"),
        ("blacklist_blocked", "目标 Worker 在黑名单"),
    ],
)
async def test_spawn_child_returns_rejected_when_gate_fails(
    error_code: str, reason: str, tmp_path: Path
) -> None:
    plane = _make_plane_with_capability_pack_stub()
    mgr = _StubManager(
        delegate_result=DelegateResult(
            success=False, child_task_id=None, error_code=error_code, reason=reason
        )
    )

    result = await plane.spawn_child(
        parent_task=_StubTask("parent-task-id"),
        parent_work=_StubWork("parent-work-id"),
        objective="测试目标",
        worker_type="general",
        target_kind="subagent",
        tool_profile="default",
        title="test",
        spawned_by="test_spawn",
        delegation_manager=mgr,  # type: ignore[arg-type]
    )

    assert result.status == "rejected"
    assert result.error_code == error_code
    assert result.reason == reason
    assert mgr.emit_calls == []  # gate 失败时不写 audit


# ============================================================
# gate 通过 + launch 成功路径
# ============================================================


async def test_spawn_child_status_written_on_success(tmp_path: Path) -> None:
    plane = _make_plane_with_capability_pack_stub()
    mgr = _StubManager(
        delegate_result=DelegateResult(
            success=True, child_task_id=None, error_code=None, reason=None
        )
    )

    result = await plane.spawn_child(
        parent_task=_StubTask("parent-task-id", depth=0),
        parent_work=_StubWork("parent-work-id"),
        objective="测试目标",
        worker_type="general",
        target_kind="subagent",
        tool_profile="default",
        title="test",
        spawned_by="test_spawn",
        delegation_manager=mgr,  # type: ignore[arg-type]
    )

    assert result.status == "written"
    assert result.task_id == "child-测试目标"  # stub 按 objective 生成 task_id
    assert result.created is True
    assert result.thread_id == "thread-child-测试目标"
    assert result.target_kind == "subagent"
    assert result.worker_type == "general"
    assert result.parent_task_id == "parent-task-id"
    assert result.parent_work_id == "parent-work-id"


# ============================================================
# launch raise 路径
# ============================================================


async def test_spawn_child_propagates_launch_raise_without_catching(
    tmp_path: Path,
) -> None:
    """spawn_child 不捕获 _launch_child_task raise——直接 propagate 给调用方
    （保持 F085 worker→worker 拒绝 invariant：subagents.spawn 必须 propagate
    给 broker → result.is_error=True；delegate_task 自己 try/except 包成 rejected）。"""
    plane = _make_plane_with_capability_pack_stub(
        launch_exception=RuntimeError("worker runtime cannot delegate to another worker"),
    )
    mgr = _StubManager(
        delegate_result=DelegateResult(
            success=True, child_task_id=None, error_code=None, reason=None
        )
    )

    with pytest.raises(RuntimeError, match="worker runtime cannot delegate"):
        await plane.spawn_child(
            parent_task=_StubTask("parent-task-id"),
            parent_work=_StubWork("parent-work-id"),
            objective="测试目标",
            worker_type="general",
            target_kind="worker",  # 触发 enforce raise
            tool_profile="default",
            title="test",
            spawned_by="test_spawn",
            delegation_manager=mgr,  # type: ignore[arg-type]
        )

    # gate 通过但 launch raise 时 emit 不会被调用（自然顺序，因为 raise 跳到顶层）
    assert mgr.emit_calls == []


# ============================================================
# emit_audit_event 区分（Codex HIGH 2 关键回归）
# ============================================================


async def test_emit_audit_event_false_does_not_call_emit_spawned_event(
    tmp_path: Path,
) -> None:
    """subagents.spawn 路径：当前不写 SUBAGENT_SPAWNED 事件，spawn_child 必须保持。"""
    plane = _make_plane_with_capability_pack_stub()
    mgr = _StubManager(
        delegate_result=DelegateResult(
            success=True, child_task_id=None, error_code=None, reason=None
        )
    )

    await plane.spawn_child(
        parent_task=_StubTask("parent-task-id"),
        parent_work=_StubWork("parent-work-id"),
        objective="测试目标",
        worker_type="general",
        target_kind="subagent",
        tool_profile="default",
        title="test",
        spawned_by="subagents_spawn",
        emit_audit_event=False,  # 关键
        delegation_manager=mgr,  # type: ignore[arg-type]
    )

    assert mgr.emit_calls == []


async def test_emit_audit_event_true_calls_emit_spawned_event(tmp_path: Path) -> None:
    """delegate_task 路径：当前写 SUBAGENT_SPAWNED 事件，spawn_child 必须保持。"""
    plane = _make_plane_with_capability_pack_stub()
    mgr = _StubManager(
        delegate_result=DelegateResult(
            success=True, child_task_id=None, error_code=None, reason=None
        )
    )

    await plane.spawn_child(
        parent_task=_StubTask("parent-task-id", depth=1),
        parent_work=_StubWork("parent-work-id"),
        objective="测试目标 audit",
        worker_type="general",
        target_kind="subagent",
        tool_profile="default",
        title="audit-test",
        spawned_by="delegate_task_tool",
        emit_audit_event=True,
        callback_mode="async",
        delegation_manager=mgr,  # type: ignore[arg-type]
    )

    assert len(mgr.emit_calls) == 1
    call = mgr.emit_calls[0]
    assert call["task_id"] == "parent-task-id"
    assert call["child_task_id"] == "child-测试目标-audit"
    assert call["target_worker"] == "general"
    assert call["depth"] == 1
    assert call["task_description"] == "测试目标 audit"
    assert call["callback_mode"] == "async"


# ============================================================
# depth / active_children 容错
# ============================================================


async def test_depth_inferred_from_parent_task_when_no_task_store(
    tmp_path: Path,
) -> None:
    """无 task_store 时 depth 回退到 parent_task.depth。"""
    plane = _make_plane_with_capability_pack_stub()
    mgr = _StubManager(
        delegate_result=DelegateResult(
            success=True, child_task_id=None, error_code=None, reason=None
        )
    )

    await plane.spawn_child(
        parent_task=_StubTask("parent-task-id", depth=2),
        parent_work=_StubWork("parent-work-id"),
        objective="深度测试",
        worker_type="general",
        target_kind="subagent",
        tool_profile="default",
        title="",
        spawned_by="test",
        delegation_manager=mgr,  # type: ignore[arg-type]
    )

    assert len(mgr.delegate_calls) == 1
    ctx, _ = mgr.delegate_calls[0]
    assert ctx.depth == 2


async def test_depth_refreshed_from_task_store_when_available(
    tmp_path: Path,
) -> None:
    """有 task_store 时 depth 用 canonical 值刷新（行为零变更不变量）。"""

    class _FakeRefreshedTask:
        depth = 5  # canonical 值与 parent_task 不同

    class _StoreStub:
        async def get_task(self, task_id):
            assert task_id == "parent-task-id"
            return _FakeRefreshedTask()

    plane = _make_plane_with_capability_pack_stub()
    plane._stores = type("S", (), {"event_store": None, "task_store": _StoreStub()})()
    mgr = _StubManager(
        delegate_result=DelegateResult(
            success=True, child_task_id=None, error_code=None, reason=None
        )
    )

    await plane.spawn_child(
        parent_task=_StubTask("parent-task-id", depth=0),  # 错误的 stale 值
        parent_work=_StubWork("parent-work-id"),
        objective="canonical depth",
        worker_type="general",
        target_kind="subagent",
        tool_profile="default",
        title="",
        spawned_by="test",
        delegation_manager=mgr,  # type: ignore[arg-type]
    )

    ctx, _ = mgr.delegate_calls[0]
    assert ctx.depth == 5  # canonical 值优先于 parent_task.depth=0


async def test_depth_falls_back_when_task_store_get_raises(tmp_path: Path) -> None:
    """task_store.get_task 失败时降级到 parent_task.depth。"""

    class _FlakyStore:
        async def get_task(self, task_id):
            raise RuntimeError("store unavailable")

    plane = _make_plane_with_capability_pack_stub()
    plane._stores = type("S", (), {"event_store": None, "task_store": _FlakyStore()})()
    mgr = _StubManager(
        delegate_result=DelegateResult(
            success=True, child_task_id=None, error_code=None, reason=None
        )
    )

    await plane.spawn_child(
        parent_task=_StubTask("parent-task-id", depth=1),
        parent_work=_StubWork("parent-work-id"),
        objective="降级",
        worker_type="general",
        target_kind="subagent",
        tool_profile="default",
        title="",
        spawned_by="test",
        delegation_manager=mgr,  # type: ignore[arg-type]
    )

    ctx, _ = mgr.delegate_calls[0]
    assert ctx.depth == 1


async def test_active_children_falls_back_to_empty_when_list_descendant_raises(
    tmp_path: Path,
) -> None:
    """list_descendant_works 失败时必须降级为 []，与 builtin_tools 容错一致。"""
    plane = _make_plane_with_capability_pack_stub(
        descendants_exc=RuntimeError("store unavailable"),
    )
    mgr = _StubManager(
        delegate_result=DelegateResult(
            success=True, child_task_id=None, error_code=None, reason=None
        )
    )

    await plane.spawn_child(
        parent_task=_StubTask("parent-task-id"),
        parent_work=_StubWork("parent-work-id"),
        objective="降级测试",
        worker_type="general",
        target_kind="subagent",
        tool_profile="default",
        title="",
        spawned_by="test",
        delegation_manager=mgr,  # type: ignore[arg-type]
    )

    ctx, _ = mgr.delegate_calls[0]
    assert ctx.active_children == []


async def test_additional_active_children_merged_into_active_children(
    tmp_path: Path,
) -> None:
    """batch loop 累加：plane 把 additional_active_children 合并到 active_children（去重）。"""
    plane = _make_plane_with_capability_pack_stub(descendants=[])
    mgr = _StubManager(
        delegate_result=DelegateResult(
            success=True, child_task_id=None, error_code=None, reason=None
        )
    )

    await plane.spawn_child(
        parent_task=_StubTask("parent-task-id"),
        parent_work=_StubWork("parent-work-id"),
        objective="batch test",
        worker_type="general",
        target_kind="subagent",
        tool_profile="default",
        title="",
        spawned_by="subagents_spawn",
        additional_active_children=["batched-1", "batched-2"],
        delegation_manager=mgr,  # type: ignore[arg-type]
    )

    ctx, _ = mgr.delegate_calls[0]
    assert sorted(ctx.active_children) == ["batched-1", "batched-2"]


async def test_additional_active_children_dedupes_against_store_results(
    tmp_path: Path,
) -> None:
    """已经从 store 查到的 task_id 不会因为再传 additional 重复。"""
    from octoagent.core.models import WorkStatus

    class _D:
        def __init__(self, task_id, work_id, status, parent_work_id):
            self.task_id = task_id
            self.work_id = work_id
            self.status = status
            self.parent_work_id = parent_work_id

    descendants = [
        _D("alive-1", "w1", WorkStatus.RUNNING, "parent-work-id"),
    ]
    plane = _make_plane_with_capability_pack_stub(descendants=descendants)
    mgr = _StubManager(
        delegate_result=DelegateResult(
            success=True, child_task_id=None, error_code=None, reason=None
        )
    )

    await plane.spawn_child(
        parent_task=_StubTask("parent-task-id"),
        parent_work=_StubWork("parent-work-id"),
        objective="dedupe",
        worker_type="general",
        target_kind="subagent",
        tool_profile="default",
        title="",
        spawned_by="subagents_spawn",
        additional_active_children=["alive-1", "batched-1"],  # alive-1 与 store 重叠
        delegation_manager=mgr,  # type: ignore[arg-type]
    )

    ctx, _ = mgr.delegate_calls[0]
    # alive-1 来自 store，batched-1 来自 additional，alive-1 不重复
    assert sorted(ctx.active_children) == ["alive-1", "batched-1"]


async def test_active_children_filters_terminal_status_descendants(
    tmp_path: Path,
) -> None:
    """active_children 必须排除终态 work（与 WORK_TERMINAL_STATUSES 一致）。"""
    from octoagent.core.models import WorkStatus

    class _D:
        def __init__(self, task_id, work_id, status, parent_work_id):
            self.task_id = task_id
            self.work_id = work_id
            self.status = status
            self.parent_work_id = parent_work_id

    descendants = [
        _D("alive-1", "w1", WorkStatus.RUNNING, "parent-work-id"),
        _D("done-1", "w2", WorkStatus.SUCCEEDED, "parent-work-id"),
        _D("alive-2", "w3", WorkStatus.ASSIGNED, "parent-work-id"),
        _D("merged-1", "w4", WorkStatus.MERGED, "parent-work-id"),
        _D("orphan-1", "w5", WorkStatus.RUNNING, "other-parent"),  # 不同 parent
    ]
    plane = _make_plane_with_capability_pack_stub(descendants=descendants)
    mgr = _StubManager(
        delegate_result=DelegateResult(
            success=True, child_task_id=None, error_code=None, reason=None
        )
    )

    await plane.spawn_child(
        parent_task=_StubTask("parent-task-id"),
        parent_work=_StubWork("parent-work-id"),
        objective="过滤测试",
        worker_type="general",
        target_kind="subagent",
        tool_profile="default",
        title="",
        spawned_by="test",
        delegation_manager=mgr,  # type: ignore[arg-type]
    )

    ctx, _ = mgr.delegate_calls[0]
    # 仅 alive-1 / alive-2 通过；done-1 / merged-1 终态过滤；orphan-1 非本 parent 过滤
    assert sorted(ctx.active_children) == ["alive-1", "alive-2"]


# ============================================================
# audit_task_fallback 透传
# ============================================================


async def test_real_delegation_manager_batch_capacity_gate_with_accumulation(
    tmp_path: Path,
) -> None:
    """F085 T2 batch capacity 真实 gate 集成测试（Codex Phase C HIGH 3 修订）：
    用真实 DelegationManager（非 stub）触发 CAPACITY_EXCEEDED，
    验证 additional_active_children 累加到达 MAX_CONCURRENT_CHILDREN=3 时第 4 次拒绝。"""
    from octoagent.gateway.harness.delegation import DelegationManager

    plane = _make_plane_with_capability_pack_stub()
    real_mgr = DelegationManager(event_store=None, task_store=None)

    # 第 1 次：accumulated=[] → 通过（active=0）
    r1 = await plane.spawn_child(
        parent_task=_StubTask("parent-task-id"),
        parent_work=_StubWork("parent-work-id"),
        objective="batch-1",
        worker_type="general",
        target_kind="subagent",
        tool_profile="default",
        title="",
        spawned_by="subagents_spawn",
        additional_active_children=[],
        delegation_manager=real_mgr,
    )
    assert r1.status == "written"

    # 第 2 次：accumulated=[t1] → 通过（active=1）
    r2 = await plane.spawn_child(
        parent_task=_StubTask("parent-task-id"),
        parent_work=_StubWork("parent-work-id"),
        objective="batch-2",
        worker_type="general",
        target_kind="subagent",
        tool_profile="default",
        title="",
        spawned_by="subagents_spawn",
        additional_active_children=[r1.task_id],
        delegation_manager=real_mgr,
    )
    assert r2.status == "written"

    # 第 3 次：accumulated=[t1, t2] → 通过（active=2）
    r3 = await plane.spawn_child(
        parent_task=_StubTask("parent-task-id"),
        parent_work=_StubWork("parent-work-id"),
        objective="batch-3",
        worker_type="general",
        target_kind="subagent",
        tool_profile="default",
        title="",
        spawned_by="subagents_spawn",
        additional_active_children=[r1.task_id, r2.task_id],
        delegation_manager=real_mgr,
    )
    assert r3.status == "written"

    # 第 4 次：accumulated=[t1, t2, t3] → CAPACITY_EXCEEDED（max_concurrent=3）
    r4 = await plane.spawn_child(
        parent_task=_StubTask("parent-task-id"),
        parent_work=_StubWork("parent-work-id"),
        objective="batch-4",
        worker_type="general",
        target_kind="subagent",
        tool_profile="default",
        title="",
        spawned_by="subagents_spawn",
        additional_active_children=[r1.task_id, r2.task_id, r3.task_id],
        delegation_manager=real_mgr,
    )
    assert r4.status == "rejected"
    assert r4.error_code == "CAPACITY_EXCEEDED"


async def test_audit_task_fallback_default_aligns_with_delegate_task_tool(
    tmp_path: Path,
) -> None:
    """默认 fallback 与 delegate_task_tool 历史 _DELEGATE_AUDIT_TASK_ID 对齐
    （Codex MEDIUM 2 修订：默认 `_delegate_task_audit`，subagents.spawn 必须显式覆盖）。"""
    plane = _make_plane_with_capability_pack_stub()
    mgr = _StubManager(
        delegate_result=DelegateResult(
            success=True, child_task_id=None, error_code=None, reason=None
        )
    )

    await plane.spawn_child(
        parent_task=_StubTask("", depth=0),  # task_id 为空 → fallback 触发
        parent_work=_StubWork("parent-work-id"),
        objective="fallback 默认",
        worker_type="general",
        target_kind="subagent",
        tool_profile="default",
        title="",
        spawned_by="delegate_task_tool",
        delegation_manager=mgr,  # type: ignore[arg-type]
    )

    ctx, _ = mgr.delegate_calls[0]
    assert ctx.task_id == "_delegate_task_audit"


async def test_audit_task_fallback_explicit_override_for_subagents_spawn(
    tmp_path: Path,
) -> None:
    """subagents.spawn 路径必须显式传 audit_task_fallback='_subagents_spawn_audit'
    才能与原历史路径对齐（避免审计 task_id 默默漂移到 _delegate_task_audit）。"""
    plane = _make_plane_with_capability_pack_stub()
    mgr = _StubManager(
        delegate_result=DelegateResult(
            success=True, child_task_id=None, error_code=None, reason=None
        )
    )

    await plane.spawn_child(
        parent_task=_StubTask("", depth=0),
        parent_work=_StubWork("parent-work-id"),
        objective="subagents.spawn fallback",
        worker_type="general",
        target_kind="subagent",
        tool_profile="default",
        title="",
        spawned_by="subagents_spawn",
        audit_task_fallback="_subagents_spawn_audit",
        delegation_manager=mgr,  # type: ignore[arg-type]
    )

    ctx, _ = mgr.delegate_calls[0]
    assert ctx.task_id == "_subagents_spawn_audit"
