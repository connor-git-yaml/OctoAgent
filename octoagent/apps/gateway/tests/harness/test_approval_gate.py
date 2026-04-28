"""ApprovalGate 单元测试（T056）。

Feature 084 Phase 3 — 验收 allowlist 管理、session 清零、审计事件写入、拒绝通知语义。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
import aiosqlite

from octoagent.core.models.enums import ActorType, EventType
from octoagent.core.models.event import Event
from octoagent.core.models.task import RequesterInfo, Task, TaskPointers
from octoagent.core.store import create_store_group
from octoagent.core.store.sqlite_init import init_db
from octoagent.gateway.harness.approval_gate import ApprovalGate, ApprovalHandle
from ulid import ULID


# ---------------------------------------------------------------------------
# 辅助：建立最小可用 store_group
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def store_group(tmp_path: Path):
    """创建完整 schema 的 StoreGroup（内含 EventStore + TaskStore）。"""
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    sg = await create_store_group(
        db_path=str(tmp_path / "test.db"),
        artifacts_dir=str(artifacts_dir),
    )
    yield sg
    await sg.conn.close()


async def _ensure_audit_task(sg, task_id: str) -> None:
    """确保审计用 task 记录存在（外键约束要求）。"""
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


def _make_gate(
    sg=None,
    sse_push_fn: Any | None = None,
) -> ApprovalGate:
    """构建 ApprovalGate（注入 event_store + task_store）。"""
    event_store = sg.event_store if sg else None
    task_store = sg.task_store if sg else None
    return ApprovalGate(
        event_store=event_store,
        task_store=task_store,
        sse_push_fn=sse_push_fn,
    )


def _make_mock_scan_result(
    blocked: bool = False,
    pattern_id: str | None = "PI-001",
    severity: str | None = "WARN",
) -> Any:
    """构建最小 ThreatScanResult mock。"""
    result = MagicMock()
    result.blocked = blocked
    result.pattern_id = pattern_id
    result.severity = severity
    return result


# ---------------------------------------------------------------------------
# T056-1：test_approval_gate_session_allowlist
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approval_gate_session_allowlist(store_group) -> None:
    """同 session 相同操作类型第二次 check_allowlist 返回 True（不再弹卡片）。

    验收：
    - 初始状态 check_allowlist 返回 False
    - add_to_allowlist 后同 session + 同操作类型返回 True
    - 不同操作类型仍返回 False
    - 不同 session 不共享 allowlist
    """
    gate = _make_gate(store_group)
    session_id = "test-session-al-001"
    op_type = "user_profile.replace"

    # 初始状态：不在 allowlist
    assert not gate.check_allowlist(session_id, op_type), \
        "初始状态 allowlist 应为空"

    # 加入 allowlist
    gate.add_to_allowlist(session_id, op_type)

    # 同 session + 同操作类型：True（不再弹卡片）
    assert gate.check_allowlist(session_id, op_type), \
        "add_to_allowlist 后相同操作应命中 allowlist"

    # 不同操作类型：仍为 False
    assert not gate.check_allowlist(session_id, "user_profile.remove"), \
        "不同操作类型不应命中 allowlist"

    # 不同 session：不共享
    assert not gate.check_allowlist("other-session", op_type), \
        "不同 session 不应共享 allowlist"


# ---------------------------------------------------------------------------
# T056-2：test_approval_gate_allowlist_clears_on_session_end
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approval_gate_allowlist_clears_on_session_end(store_group) -> None:
    """session 结束时 clear_session() 清零 allowlist（不跨 session 持久化）。

    验收：
    - add_to_allowlist 后 check_allowlist 为 True
    - clear_session 后同操作类型 check_allowlist 返回 False
    - clear_session 重复调用（幂等）不报错
    """
    gate = _make_gate(store_group)
    session_id = "test-session-al-002"
    op_type = "user_profile.replace"

    gate.add_to_allowlist(session_id, op_type)
    assert gate.check_allowlist(session_id, op_type), \
        "clear 前应命中 allowlist"

    # 模拟 session 结束
    gate.clear_session(session_id)

    assert not gate.check_allowlist(session_id, op_type), \
        "clear_session 后 allowlist 应清零"

    # 幂等：重复 clear 不报错
    gate.clear_session(session_id)
    gate.clear_session("nonexistent-session")


# ---------------------------------------------------------------------------
# T056-3：test_approval_gate_writes_approval_requested_event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approval_gate_writes_approval_requested_event(store_group) -> None:
    """request_approval 写入 APPROVAL_REQUESTED 事件，含 threat_category + pattern_id。

    验收（FR-4.2 / FR-10.2）：
    - 写入 APPROVAL_REQUESTED 事件类型
    - payload 含 handle_id / approval_id / threat_category / pattern_id
    - threat_category 从 pattern_id 前缀正确推断（PI-001 → prompt_injection）
    """
    # 确保审计 task 存在（防 FK 违反）
    await _ensure_audit_task(store_group, "_approval_gate_audit")

    gate = _make_gate(store_group)
    session_id = "test-session-ev-001"
    scan_result = _make_mock_scan_result(blocked=False, pattern_id="PI-001", severity="WARN")

    handle = await gate.request_approval(
        session_id=session_id,
        tool_name="user_profile.replace",
        scan_result=scan_result,
        operation_summary="替换用户档案中的工作状态",
        diff_content="- 原内容\n+ 新内容",
        task_id="_approval_gate_audit",
    )

    # 验证 handle 创建成功
    assert handle is not None
    assert handle.handle_id, "handle_id 不应为空"
    assert handle.decision is None, "初始 decision 应为 None（待决）"

    # 验证事件写入
    events = await store_group.event_store.get_events_for_task("_approval_gate_audit")
    approval_events = [e for e in events if e.type == EventType.APPROVAL_REQUESTED]
    assert approval_events, "应写入 APPROVAL_REQUESTED 事件"

    event = approval_events[0]
    payload = event.payload

    # 验证 payload 字段
    assert payload.get("handle_id") == handle.handle_id, "handle_id 应写入 payload"
    assert payload.get("approval_id") == handle.handle_id, "approval_id（F27 兼容）应写入 payload"
    assert payload.get("threat_category") == "prompt_injection", \
        f"PI-001 应推断为 prompt_injection，实际: {payload.get('threat_category')}"
    assert payload.get("pattern_id") == "PI-001", \
        f"pattern_id 应为 PI-001，实际: {payload.get('pattern_id')}"
    assert payload.get("tool_name") == "user_profile.replace"
    assert payload.get("diff_content") == "- 原内容\n+ 新内容"


@pytest.mark.asyncio
async def test_approval_gate_event_pattern_id_categories(store_group) -> None:
    """不同 pattern_id 前缀映射到正确的 threat_category。

    覆盖：PI / RH / EX / B64 / SO / INVIS / unknown。
    """
    await _ensure_audit_task(store_group, "_approval_gate_audit")
    gate = _make_gate(store_group)

    # 直接测试内部辅助函数（通过写事件后验证 payload）
    from octoagent.gateway.harness.approval_gate import _pattern_id_to_category

    assert _pattern_id_to_category("PI-001") == "prompt_injection"
    assert _pattern_id_to_category("RH-002") == "role_hijacking"
    assert _pattern_id_to_category("EX-001") == "exfiltration"
    assert _pattern_id_to_category("B64-001") == "base64_payload"
    assert _pattern_id_to_category("SO-001") == "system_override"
    assert _pattern_id_to_category("MI-001") == "memory_injection"
    assert _pattern_id_to_category("INVIS-001") == "invisible_unicode"
    assert _pattern_id_to_category("XX-999") == "unknown"
    assert _pattern_id_to_category(None) is None


# ---------------------------------------------------------------------------
# T056-4：test_approval_gate_rejected_notifies_agent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approval_gate_rejected_notifies_agent(store_group) -> None:
    """拒绝时 wait_for_decision 明确返回 'rejected'（不静默，Constitution C7）。

    验收：
    - resolve_approval(decision="rejected") 触发 event.set()
    - wait_for_decision 返回 "rejected"
    - 写入 APPROVAL_DECIDED 事件，decision="rejected"
    - handle 从 pending_handles 中清除
    """
    await _ensure_audit_task(store_group, "_approval_gate_audit")

    gate = _make_gate(store_group)
    session_id = "test-session-rej-001"
    scan_result = _make_mock_scan_result(blocked=False, pattern_id="RH-001")

    # 1. 发起审批请求
    handle = await gate.request_approval(
        session_id=session_id,
        tool_name="user_profile.replace",
        scan_result=scan_result,
        operation_summary="replace 操作",
        task_id="_approval_gate_audit",
    )

    # 2. 后台注入 rejected 决策（模拟 API 端点回调）
    async def _inject_decision():
        await asyncio.sleep(0.05)  # 稍等让 wait_for_decision 先 await
        await gate.resolve_approval(
            handle_id=handle.handle_id,
            decision="rejected",
            operator="web_ui",
            task_id="_approval_gate_audit",
            session_id=session_id,
            operation_type="user_profile.replace",
        )

    task = asyncio.create_task(_inject_decision())

    # 3. Agent 侧等待决策（应收到 "rejected"）
    result = await gate.wait_for_decision(handle, timeout_seconds=5.0)
    await task

    assert result == "rejected", f"期望 rejected，实际: {result}"

    # 4. 验证 APPROVAL_DECIDED 事件写入
    events = await store_group.event_store.get_events_for_task("_approval_gate_audit")
    decided_events = [e for e in events if e.type == EventType.APPROVAL_DECIDED]
    assert decided_events, "应写入 APPROVAL_DECIDED 事件"
    decided = decided_events[0]
    assert decided.payload.get("decision") == "rejected"
    assert decided.payload.get("operator") == "web_ui"

    # 5. 拒绝时不应加入 allowlist
    assert not gate.check_allowlist(session_id, "user_profile.replace"), \
        "拒绝后不应加入 allowlist"

    # 6. handle 应从 pending_handles 清除
    assert handle.handle_id not in gate._pending_handles, \
        "决策后 handle 应从 pending_handles 清除"


@pytest.mark.asyncio
async def test_approval_gate_approved_adds_to_allowlist(store_group) -> None:
    """批准时 allowlist 更新，下次同操作无需再审批（FR-4.3）。"""
    await _ensure_audit_task(store_group, "_approval_gate_audit")

    gate = _make_gate(store_group)
    session_id = "test-session-ok-001"
    op_type = "user_profile.replace"

    handle = await gate.request_approval(
        session_id=session_id,
        tool_name="user_profile.replace",
        scan_result=None,
        operation_summary="replace 操作",
        task_id="_approval_gate_audit",
    )

    async def _approve():
        await asyncio.sleep(0.05)
        await gate.resolve_approval(
            handle_id=handle.handle_id,
            decision="approved",
            operator="web_ui",
            session_id=session_id,
            operation_type=op_type,
        )

    task = asyncio.create_task(_approve())
    result = await gate.wait_for_decision(handle, timeout_seconds=5.0)
    await task

    assert result == "approved"
    # 批准后应加入 allowlist
    assert gate.check_allowlist(session_id, op_type), \
        "批准后应加入 allowlist（FR-4.3）"


@pytest.mark.asyncio
async def test_approval_gate_timeout_returns_rejected(store_group) -> None:
    """超时时 wait_for_decision 返回 'rejected'（不静默，Constitution C7）。"""
    await _ensure_audit_task(store_group, "_approval_gate_audit")

    gate = _make_gate(store_group)
    handle = await gate.request_approval(
        session_id="test-session-timeout",
        tool_name="user_profile.replace",
        scan_result=None,
        operation_summary="超时测试",
        task_id="_approval_gate_audit",
    )

    # 极短 timeout 触发超时路径
    result = await gate.wait_for_decision(handle, timeout_seconds=0.05)
    assert result == "rejected", f"超时应返回 rejected，实际: {result}"
    assert handle.decision == "rejected"
    assert handle.operator == "system_timeout"
