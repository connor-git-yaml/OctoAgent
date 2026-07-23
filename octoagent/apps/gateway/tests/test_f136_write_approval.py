"""F136：behavior.write_file 服务端审批绑定（关闭 LLM 一轮自确认绕过）。

缺陷（F135 Codex P1 + 经验复现）：confirmed 是 LLM 自填参数，首调 confirmed=true（无任何
前置 proposal）即可直接写入 REVIEW_REQUIRED 的 USER.md——人审被一轮自确认绕过。

修复：REVIEW_REQUIRED + confirmed=true 必须经服务端 ApprovalGate 批准
（APPROVAL_DECIDED=approved）才落盘；拒绝/超时/gate 缺失一律不写（fail-closed）。

AC ↔ test 绑定见 spec.md §6。测试用**真 ApprovalGate**（不 mock 审批语义）+ resolver 协程
模拟用户在审批卡片上的决策。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from octoagent.core.models.behavior import BehaviorReviewMode
from octoagent.core.store import create_store_group
from octoagent.core.store.audit_task import ensure_system_audit_task
from octoagent.gateway.harness.approval_gate import ApprovalGate
from octoagent.gateway.services.builtin_tools import misc_tools, write_approval
from octoagent.gateway.services.builtin_tools._deps import ToolDeps
from octoagent.gateway.services.execution_context import (
    ExecutionRuntimeContext,
    bind_execution_context,
)
from octoagent.gateway.services.operations.project_migration import ProjectWorkspaceMigrationService

TASK_ID = "task-f136"
SESSION_ID = "session-f136"
# USER.md 是 SYSTEM_SHARED scope（template.py），解析到 behavior/system/USER.md。
USER_MD_REL = Path("behavior") / "system" / "USER.md"


class _RecordingConsole:
    """记录 WAITING_APPROVAL 状态转移调用（AC-3/AC-4 断言 RUNNING 恢复语义）。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def mark_waiting_approval(self, *, task_id: str, session_id: str = "") -> None:
        self.calls.append(("waiting_approval", task_id))

    async def mark_running_from_waiting_approval(self, *, task_id: str) -> None:
        self.calls.append(("running", task_id))


class _RecordingApprovalManager:
    """记录双注册（AC-9）。"""

    def __init__(self) -> None:
        self.registered: list[Any] = []

    async def register(self, request: Any) -> None:
        self.registered.append(request)


class _RecordingNotificationService:
    """记录审批通知（FR-5）。"""

    def __init__(self) -> None:
        self.notifications: list[dict[str, Any]] = []

    async def notify_approval_request(self, **kwargs: Any) -> None:
        self.notifications.append(kwargs)


async def _setup(tmp_path: Path):
    store_group = await create_store_group(
        str(tmp_path / "gateway.db"),
        str(tmp_path / "artifacts"),
    )
    await ProjectWorkspaceMigrationService(
        project_root=tmp_path,
        store_group=store_group,
    ).ensure_default_project()
    # gate 以真实 task_id 写 APPROVAL_* 事件（FK 需要 task 行存在，与生产一致）。
    await ensure_system_audit_task(store_group.task_store, TASK_ID, title="F136 测试 task")
    return store_group


async def _capture_behavior_tool(
    tmp_path: Path,
    store_group: Any,
    *,
    approval_gate: Any = None,
    approval_manager: Any = None,
    notification_service: Any = None,
    console: Any = None,
    snapshot_store: Any = None,
):
    """misc_tools 注册捕获 handler 直调（test_f135 同款），可注入审批三依赖。"""
    captured: dict[str, Any] = {}

    class _CaptureBroker:
        async def try_register(self, meta: Any, handler: Any) -> None:
            captured[meta.name] = handler

    deps = ToolDeps(
        project_root=tmp_path,
        stores=store_group,
        tool_broker=None,
        tool_index=None,
        skill_discovery=None,
        memory_console_service=None,
        memory_runtime_service=None,
        _approval_gate=approval_gate,
        _approval_manager=approval_manager,
        _notification_service=notification_service,
        _snapshot_store=snapshot_store,
    )
    await misc_tools.register(_CaptureBroker(), deps)
    handler = captured.get("behavior.write_file")
    assert handler is not None, "behavior.write_file handler 应已注册"

    runtime_ctx = ExecutionRuntimeContext(
        task_id=TASK_ID,
        trace_id="trace-f136",
        session_id=SESSION_ID,
        worker_id="worker.general",
        backend="inline",
        console=console,
    )

    async def _call(**kwargs: Any) -> Any:
        with bind_execution_context(runtime_ctx):
            return await handler(**kwargs)

    return _call


async def _resolve_when_pending(
    gate: ApprovalGate,
    decision: str,
    *,
    before_resolve: Any = None,
) -> str:
    """模拟用户在审批卡片上的决策：等 pending handle 出现后 resolve。"""
    for _ in range(500):
        pending = list(gate._pending_handles.keys())
        if pending:
            if before_resolve is not None:
                before_resolve()
            handle_id = pending[0]
            resolved = await gate.resolve_approval(
                handle_id=handle_id,
                decision=decision,  # type: ignore[arg-type]
                operator="user:test",
                task_id=TASK_ID,
            )
            assert resolved is True
            return handle_id
        await asyncio.sleep(0.01)
    raise AssertionError("审批请求从未出现（gate 未被消费）——绕过缝可能重开")


# ---------------------------------------------------------------------------
# AC-1：绕过关闭（安全主场景）
# ---------------------------------------------------------------------------


async def test_first_call_confirmed_true_gated_until_approval(tmp_path: Path) -> None:
    """AC-1a：首调 confirmed=true（无前置 proposal）不再直写——落盘前审批卡片必须先出现，
    且 pending 期间文件不存在；用户批准后才写入。"""
    store_group = await _setup(tmp_path)
    gate = ApprovalGate(event_store=store_group.event_store, task_store=store_group.task_store)
    user_md = tmp_path / USER_MD_REL
    try:
        call = await _capture_behavior_tool(tmp_path, store_group, approval_gate=gate)

        def _assert_not_written_yet() -> None:
            assert not user_md.exists(), (
                "审批未决期间文件绝不可落盘（baseline 缺陷：confirmed=true 直写）"
            )

        result, handle_id = await asyncio.gather(
            call(file_id="USER.md", content="# USER\n注入内容\n", confirmed=True),
            _resolve_when_pending(gate, "approved", before_resolve=_assert_not_written_yet),
        )
        assert result.status == "written"
        assert result.written is True
        assert result.approval_id == handle_id, "写入结果必须携带审批关联 ID（审计链）"
        assert user_md.exists()
    finally:
        await store_group.close()


async def test_reject_leaves_file_untouched(tmp_path: Path) -> None:
    """AC-1b：用户拒绝 → 不落盘，返回 APPROVAL_REJECTED。"""
    store_group = await _setup(tmp_path)
    gate = ApprovalGate(event_store=store_group.event_store, task_store=store_group.task_store)
    user_md = tmp_path / USER_MD_REL
    try:
        call = await _capture_behavior_tool(tmp_path, store_group, approval_gate=gate)
        result, handle_id = await asyncio.gather(
            call(file_id="USER.md", content="# USER\n恶意改写\n", confirmed=True),
            _resolve_when_pending(gate, "rejected"),
        )
        assert result.status == "rejected"
        assert result.written is False
        assert result.reason.startswith("APPROVAL_REJECTED")
        assert result.approval_id == handle_id
        assert not user_md.exists(), "拒绝后文件必须保持原状"
    finally:
        await store_group.close()


# ---------------------------------------------------------------------------
# AC-2：正常流全链路（批准 → 落盘 + F107 版本 + APPROVAL_* 事件 + CRITICAL 通知）
# ---------------------------------------------------------------------------


async def test_approved_write_lands_with_version_and_events(tmp_path: Path) -> None:
    store_group = await _setup(tmp_path)
    gate = ApprovalGate(event_store=store_group.event_store, task_store=store_group.task_store)
    manager = _RecordingApprovalManager()
    notifications = _RecordingNotificationService()
    try:
        call = await _capture_behavior_tool(
            tmp_path,
            store_group,
            approval_gate=gate,
            approval_manager=manager,
            notification_service=notifications,
        )
        content = "# USER\n时区 Asia/Shanghai\n"
        result, _ = await asyncio.gather(
            call(file_id="USER.md", content=content, confirmed=True),
            _resolve_when_pending(gate, "approved"),
        )
        assert result.status == "written"
        assert Path(result.target).read_text(encoding="utf-8") == content

        # F107 版本记录照常（FR-9）。
        from octoagent.core.behavior_workspace import behavior_version_key_from_path

        key = behavior_version_key_from_path(tmp_path, Path(result.target))
        versions = await store_group.behavior_version_store.list_versions(key)
        assert len(versions) >= 1

        # APPROVAL_REQUESTED / APPROVAL_DECIDED 事件链落 event_store（FR-6）。
        events = await store_group.event_store.get_events_for_task(TASK_ID)
        types = [str(e.type) for e in events]
        assert any("approval_requested" in t.lower() for t in types), types
        assert any("approval_decided" in t.lower() for t in types), types

        # 审批请求事件携带 diff（用户批的是具体修改，FR-6/DP-6）。
        requested = [e for e in events if "approval_requested" in str(e.type).lower()]
        assert requested and requested[0].payload.get("diff_content"), (
            "审批卡片必须携带 unified diff"
        )

        # P1（Codex）：diff 必须进 risk_explanation——审批渲染渠道（Web 卡片 /
        # OperatorInbox / Telegram）读的是它而非 diff_content 结构化字段。
        registered = manager.registered[0]
        assert "Asia/Shanghai" in registered.risk_explanation, (
            "risk_explanation 必须含内容 diff，否则各渲染渠道看不到实际变更（P1）"
        )
        assert "diff" in registered.risk_explanation.lower()

        # CRITICAL 审批通知（FR-5）。
        assert len(notifications.notifications) == 1
        notif = notifications.notifications[0]
        assert notif["tool_name"] == "behavior.write_file"
        assert str(notif["priority"]).lower().endswith("critical")
    finally:
        await store_group.close()


# ---------------------------------------------------------------------------
# AC-3 / AC-4：决策语义（DP-4）
# ---------------------------------------------------------------------------


async def test_reject_restores_running(tmp_path: Path) -> None:
    """AC-3：显式拒绝恢复 RUNNING——一次写入被否决不应终结对话（与 escalate 的刻意差异）。"""
    store_group = await _setup(tmp_path)
    gate = ApprovalGate(event_store=store_group.event_store, task_store=store_group.task_store)
    console = _RecordingConsole()
    try:
        call = await _capture_behavior_tool(
            tmp_path, store_group, approval_gate=gate, console=console
        )
        await asyncio.gather(
            call(file_id="USER.md", content="x", confirmed=True),
            _resolve_when_pending(gate, "rejected"),
        )
        assert ("waiting_approval", TASK_ID) in console.calls
        assert ("running", TASK_ID) in console.calls, (
            "显式拒绝必须恢复 RUNNING（对话继续），不可留在 WAITING_APPROVAL 被 monitor 推 FAILED"
        )
    finally:
        await store_group.close()


async def test_timeout_rejects_without_running_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-4：超时 → APPROVAL_TIMEOUT 不落盘、不恢复 RUNNING
    （终态归 task_runner，F101 HIGH-02 v3）。"""
    monkeypatch.setattr(write_approval, "BEHAVIOR_WRITE_APPROVAL_TIMEOUT_SECONDS", 0.05)
    store_group = await _setup(tmp_path)
    gate = ApprovalGate(event_store=store_group.event_store, task_store=store_group.task_store)
    console = _RecordingConsole()
    user_md = tmp_path / USER_MD_REL
    try:
        call = await _capture_behavior_tool(
            tmp_path, store_group, approval_gate=gate, console=console
        )
        result = await call(file_id="USER.md", content="x", confirmed=True)
        assert result.status == "rejected"
        assert result.reason.startswith("APPROVAL_TIMEOUT")
        assert not user_md.exists()
        assert ("waiting_approval", TASK_ID) in console.calls
        assert ("running", TASK_ID) not in console.calls, (
            "超时不得恢复 RUNNING——task_runner monitor 是终态唯一 owner"
        )
    finally:
        await store_group.close()


# ---------------------------------------------------------------------------
# AC-5 / AC-6：fail-closed 与 NONE 直写
# ---------------------------------------------------------------------------


async def test_gate_unavailable_fail_closed(tmp_path: Path) -> None:
    """AC-5：approval_gate 缺失 + REVIEW_REQUIRED + confirmed=true → fail-closed 拒绝不写。"""
    store_group = await _setup(tmp_path)
    user_md = tmp_path / USER_MD_REL
    try:
        call = await _capture_behavior_tool(tmp_path, store_group, approval_gate=None)
        result = await call(file_id="USER.md", content="x", confirmed=True)
        assert result.status == "rejected"
        assert result.written is False
        assert result.reason.startswith("APPROVAL_UNAVAILABLE")
        assert not user_md.exists(), "降级=功能不可用，不是安全绕过（Constitution #6/#7）"
    finally:
        await store_group.close()


async def test_review_mode_none_writes_without_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-6：review_mode=NONE 直写不弹审批（gate=None 也能写 → 证明 NONE 路径不消费 gate）。"""
    monkeypatch.setattr(
        misc_tools,
        "get_behavior_file_review_modes",
        lambda include_advanced=True: {"USER.md": BehaviorReviewMode.NONE},
    )
    store_group = await _setup(tmp_path)
    try:
        call = await _capture_behavior_tool(tmp_path, store_group, approval_gate=None)
        result = await call(file_id="USER.md", content="auto\n", confirmed=False)
        assert result.status == "written"
        assert result.approval_id == ""
        assert Path(result.target).read_text(encoding="utf-8") == "auto\n"
    finally:
        await store_group.close()


# ---------------------------------------------------------------------------
# AC-7：每次写独立审批（无 allowlist 短路）
# ---------------------------------------------------------------------------


async def test_each_confirmed_write_requires_fresh_approval(tmp_path: Path) -> None:
    store_group = await _setup(tmp_path)
    gate = ApprovalGate(event_store=store_group.event_store, task_store=store_group.task_store)
    try:
        call = await _capture_behavior_tool(tmp_path, store_group, approval_gate=gate)
        result1, handle1 = await asyncio.gather(
            call(file_id="USER.md", content="v1\n", confirmed=True),
            _resolve_when_pending(gate, "approved"),
        )
        assert result1.status == "written"
        # 第一次批准不得给本 session 留下 allowlist 豁免（DP-3）。
        assert gate.check_allowlist(SESSION_ID, "behavior.write_file") is False

        result2, handle2 = await asyncio.gather(
            call(file_id="USER.md", content="v2\n", confirmed=True),
            _resolve_when_pending(gate, "approved"),
        )
        assert result2.status == "written"
        assert handle2 != handle1, "第二次写必须发起全新审批（不复用/不豁免）"
    finally:
        await store_group.close()


# ---------------------------------------------------------------------------
# AC-8 / AC-9：proposal 步不消费 gate + ApprovalManager 双注册
# ---------------------------------------------------------------------------


async def test_proposal_step_does_not_consult_gate(tmp_path: Path) -> None:
    """AC-8 补充：confirmed=false proposal 步不产生审批请求（行为不变，不触盘）。"""
    store_group = await _setup(tmp_path)
    gate = ApprovalGate(event_store=store_group.event_store, task_store=store_group.task_store)
    try:
        call = await _capture_behavior_tool(tmp_path, store_group, approval_gate=gate)
        result = await call(file_id="USER.md", content="draft\n", confirmed=False)
        assert result.status == "skipped"
        assert result.proposal is True
        assert not gate._pending_handles, "proposal 步不得发起审批请求"
        assert not (tmp_path / USER_MD_REL).exists()
    finally:
        await store_group.close()


async def test_approval_manager_dual_registration(tmp_path: Path) -> None:
    """AC-9：审批同步注册 ApprovalManager（approval_id=handle_id）——
    Web resolve 依赖，缺注册则 404。"""
    store_group = await _setup(tmp_path)
    gate = ApprovalGate(event_store=store_group.event_store, task_store=store_group.task_store)
    manager = _RecordingApprovalManager()
    try:
        call = await _capture_behavior_tool(
            tmp_path, store_group, approval_gate=gate, approval_manager=manager
        )
        result, handle_id = await asyncio.gather(
            call(file_id="USER.md", content="v\n", confirmed=True),
            _resolve_when_pending(gate, "approved"),
        )
        assert result.status == "written"
        assert len(manager.registered) == 1
        registered = manager.registered[0]
        assert registered.approval_id == handle_id
        assert registered.tool_name == "behavior.write_file"
        assert registered.task_id == TASK_ID
        # DP-3：注册时声明不参与 allow-always（否则一次"总是批准"会短路后续审批）。
        assert registered.allow_always_eligible is False
    finally:
        await store_group.close()


# ---------------------------------------------------------------------------
# AC-11（Codex P2）：allow-always 不破坏后续独立审批（用真 ApprovalManager）
# ---------------------------------------------------------------------------


async def test_allow_always_does_not_shortcircuit_next_write(tmp_path: Path) -> None:
    """Codex P2：即便用户对某次 behavior.write_file 点"总是批准"，下一次写仍必须
    发起独立审批——不能因 ApprovalManager 全局白名单短路（短路会不入 pending →
    Web/Telegram resolve 找不到 approval_id → 404 → 写入超时）。用真 ApprovalManager。"""
    from octoagent.policy.approval_manager import ApprovalManager
    from octoagent.policy.models import ApprovalDecision

    store_group = await _setup(tmp_path)
    gate = ApprovalGate(event_store=store_group.event_store, task_store=store_group.task_store)
    manager = ApprovalManager()  # 真实覆盖逻辑

    async def _resolve_manager_and_gate(decision: ApprovalDecision) -> None:
        """镜像 routes/approvals.py 双 resolve：先 ApprovalManager 后 ApprovalGate。"""
        for _ in range(500):
            pending = manager.get_pending_approvals()
            if pending:
                approval_id = pending[0].request.approval_id
                ok = await manager.resolve(approval_id, decision, resolved_by="user:web")
                assert ok is True, "resolve 必须成功——短路会导致 approval_id 不在 pending"
                gate_decision = (
                    "approved"
                    if decision in (ApprovalDecision.ALLOW_ONCE, ApprovalDecision.ALLOW_ALWAYS)
                    else "rejected"
                )
                await gate.resolve_approval(
                    handle_id=approval_id,
                    decision=gate_decision,  # type: ignore[arg-type]
                    operator="user:web",
                    task_id=TASK_ID,
                )
                return
            await asyncio.sleep(0.01)
        raise AssertionError("ApprovalManager pending 从未出现——register 被 allow-always 短路")

    try:
        call = await _capture_behavior_tool(
            tmp_path, store_group, approval_gate=gate, approval_manager=manager
        )
        # 第一次：用户点"总是批准"。
        result1, _ = await asyncio.gather(
            call(file_id="USER.md", content="v1\n", confirmed=True),
            _resolve_manager_and_gate(ApprovalDecision.ALLOW_ALWAYS),
        )
        assert result1.status == "written"
        # allow-always 不得写入全局白名单（DP-3）。
        assert manager._allow_always.get("behavior.write_file") is not True

        # 第二次：仍须弹出独立审批（若被短路，_resolve_manager_and_gate 会 AssertionError）。
        result2, _ = await asyncio.gather(
            call(file_id="USER.md", content="v2\n", confirmed=True),
            _resolve_manager_and_gate(ApprovalDecision.ALLOW_ONCE),
        )
        assert result2.status == "written"
        assert Path(result2.target).read_text(encoding="utf-8") == "v2\n"
    finally:
        await store_group.close()


# ---------------------------------------------------------------------------
# F146 件②：审批通过写 USER.md 后同步 SnapshotStore live state
# ---------------------------------------------------------------------------


async def test_approved_user_md_write_syncs_live_state(tmp_path: Path) -> None:
    """F146 件②：LLM 工具 behavior.write_file 审批通过落盘 USER.md 后同步 live
    state——notifications quiet hours / user_profile.read 等读点无需重启即读到新内容。"""

    class _RecordingSnapshotStore:
        def __init__(self) -> None:
            self.live: dict[str, str] = {"USER.md": "stale-live-state"}

        def update_live_state(self, key: str, content: str) -> None:
            self.live[key] = content

        def get_live_state(self, key: str) -> str | None:
            return self.live.get(key)

    store_group = await _setup(tmp_path)
    gate = ApprovalGate(event_store=store_group.event_store, task_store=store_group.task_store)
    snapshot_store = _RecordingSnapshotStore()
    try:
        call = await _capture_behavior_tool(
            tmp_path, store_group, approval_gate=gate, snapshot_store=snapshot_store
        )
        content = "# USER\n时区 Asia/Shanghai\n"
        result, _ = await asyncio.gather(
            call(file_id="USER.md", content=content, confirmed=True),
            _resolve_when_pending(gate, "approved"),
        )
        assert result.status == "written"
        assert snapshot_store.live["USER.md"] == content  # live state 已同步
    finally:
        await store_group.close()


async def test_rejected_user_md_write_does_not_touch_live_state(tmp_path: Path) -> None:
    """F146 件②反向锚：审批拒绝不落盘 → live state 保持原样（同步只跟随真实落盘）。"""

    class _RecordingSnapshotStore:
        def __init__(self) -> None:
            self.live: dict[str, str] = {"USER.md": "stale-live-state"}

        def update_live_state(self, key: str, content: str) -> None:
            self.live[key] = content

        def get_live_state(self, key: str) -> str | None:
            return self.live.get(key)

    store_group = await _setup(tmp_path)
    gate = ApprovalGate(event_store=store_group.event_store, task_store=store_group.task_store)
    snapshot_store = _RecordingSnapshotStore()
    try:
        call = await _capture_behavior_tool(
            tmp_path, store_group, approval_gate=gate, snapshot_store=snapshot_store
        )
        result, _ = await asyncio.gather(
            call(file_id="USER.md", content="# USER\n恶意改写\n", confirmed=True),
            _resolve_when_pending(gate, "rejected"),
        )
        assert result.status == "rejected"
        assert snapshot_store.live["USER.md"] == "stale-live-state"  # 未被污染
    finally:
        await store_group.close()
