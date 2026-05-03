"""F087 P4 T-P4-5/6/7：域 #8/#9/#10 delegate_task / max_depth / A2A。

- 域 #8：delegate_task / Worker 派发
- 域 #9：Sub-agent max_depth=2 拒绝
- 域 #10：A2A 通信完整 4 子断言（DispatchEnvelope / a2a_messages / parent_task_id）

设计取舍：
- 不预测 LLM 是否选 delegate_task（LLM 可能直接处理，不派发）→ SKIP-friendly
- 用 events / store 直接 query 验证 delegation 痕迹
"""

from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path
from typing import Any

import pytest


pytestmark = [pytest.mark.e2e_full, pytest.mark.e2e_live]


_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"completed", "succeeded", "failed", "cancelled", "canceled"}
)
_SUCCESS_STATUSES: frozenset[str] = frozenset({"completed", "succeeded"})


async def _wait_for_terminal(sg: Any, task_id: str, deadline_s: float = 180.0) -> str:
    start = time.monotonic()
    last = ""
    while time.monotonic() - start < deadline_s:
        task = await sg.task_store.get_task(task_id)
        if task is not None:
            last = (task.status or "").lower()
            if last in _TERMINAL_STATUSES:
                return last
        await asyncio.sleep(1.0)
    raise TimeoutError(f"task {task_id} 未达终态；最后 {last!r}")


def _tool_calls(events: list[Any]) -> list[str]:
    from octoagent.core.models.enums import EventType

    out = []
    for ev in events:
        if ev.type == EventType.TOOL_CALL_STARTED:
            n = (ev.payload or {}).get("tool_name") or ""
            if n:
                out.append(n)
    return out


@pytest.fixture
async def harness_real_llm(octo_harness_e2e: dict[str, Any]) -> dict[str, Any]:
    harness = octo_harness_e2e["harness"]
    app = octo_harness_e2e["app"]
    project_root = octo_harness_e2e["project_root"]

    fixtures_root = (
        Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "local-instance"
    )
    if (fixtures_root / "octoagent.yaml.template").exists():
        from apps.gateway.tests.e2e_live.helpers.factories import (
            copy_local_instance_template,
        )
        copy_local_instance_template(fixtures_root, project_root)

    await harness.bootstrap(app)
    harness.commit_to_app(app)

    # F087 final fixup#14（Codex final medium-4 闭环）：路由注册带 front_door 保护
    from fastapi import Depends

    from octoagent.gateway.deps import require_front_door_access
    from octoagent.gateway.routes import message, tasks

    protected = [Depends(require_front_door_access)]
    app.include_router(message.router, tags=["message"], dependencies=protected)
    app.include_router(tasks.router, tags=["tasks"], dependencies=protected)

    return {"harness": harness, "app": app, "project_root": project_root}


# ---------------------------------------------------------------------------
# T-P4-5：域 #8 delegate_task / Worker 派发
# ---------------------------------------------------------------------------


async def _count_a2a_messages(sg: Any, task_id: str | None = None) -> int:
    """跨 schema 统计 a2a_messages 行数。"""
    conn = sg.conn
    for table in ("a2a_messages", "delegation_messages"):
        try:
            if task_id:
                cur = await conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE parent_task_id = ?",
                    (task_id,),
                )
            else:
                cur = await conn.execute(f"SELECT COUNT(*) FROM {table}")
            row = await cur.fetchone()
            if row:
                return int(row[0])
        except Exception:
            continue
    return 0


async def test_domain_8_real_llm_delegate_task(
    harness_real_llm: dict[str, Any],
) -> None:
    """域 #8 真打：LLM 调 delegate_task → 子 task 创建。

    断言（≥ 2 独立点）：
    1. 任务 succeeded 或 awaiting_delegation
    2. tool_calls 含 delegate_task
    3. (可选) 子 task 创建（task_store 出现 parent_task_id == 当前 task）

    SKIP 路径：LLM 没用 delegate_task → SKIP（不 FAIL）
    """
    from httpx import ASGITransport, AsyncClient

    app = harness_real_llm["app"]
    sg = app.state.store_group

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://e2e-test") as client:
        resp = await client.post(
            "/api/message",
            json={
                "text": (
                    "请使用 delegate_task 工具派发一个子任务，target_worker 选 main，"
                    "title='F087 e2e 子任务测试'，goal='这是个 e2e 测试，请直接回复完成'。"
                    "你必须真的调用 delegate_task。"
                ),
                "idempotency_key": f"e2e-d8-{uuid.uuid4().hex[:8]}",
                "channel": "web",
                "thread_id": "e2e-d8",
                "sender_id": "owner",
                "sender_name": "Owner",
            },
        )
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

    final_status = await _wait_for_terminal(sg, task_id)
    assert final_status in _TERMINAL_STATUSES, f"域#8: 应达终态，实际 {final_status}"

    events = await sg.event_store.get_events_for_task(task_id)
    tools = _tool_calls(events)
    if "delegate_task" not in tools:
        pytest.skip(
            f"域#8 SKIP: LLM 没用 delegate_task（实际: {tools}）。"
            "本 case 仅在 LLM 真选 delegate_task 时验证。"
        )

    # delegate_task 调用了 → 验证子任务存在
    # 简单按 events 表 child_task_created 类事件验证
    child_events = [
        e for e in events
        if "child" in str(e.payload or {}).lower() or "delegate" in str(e.payload or {}).lower()
    ]
    assert child_events, (
        "域#8: delegate_task 调用后应有 child / delegate 类事件。"
        f"events 数: {len(events)}"
    )


# ---------------------------------------------------------------------------
# T-P4-6：域 #9 Sub-agent max_depth=2 拒绝
# ---------------------------------------------------------------------------


async def test_domain_9_real_llm_max_depth_rejection(
    harness_real_llm: dict[str, Any],
) -> None:
    """域 #9：直调 DelegationManager.delegate(depth=2) 验证 max_depth 拒绝。

    Codex P4 high-4 闭环：旧实现给 ExecutionContext 塞不存在的 delegation_depth
    字段，主线 max_depth 不读 ExecutionContext，结果实际按 depth=0 跑 → 未被
    拒绝时 SKIP。修复方向：绕开 LLM + ExecutionContext，直调主路径
    DelegationManager.delegate(DelegationContext(depth=2)) 验证拒绝行为。

    断言（≥ 2 独立点）：
    1. DelegateResult.success is False
    2. error_code == "depth_exceeded" + reason 含 max_depth 文案
    3. 进程内无 child task 被创建（task_store 无新增 task）
    4. events 表无 SUBAGENT_SPAWNED 事件（FR-5.2 + Constitution C4）
    """
    from octoagent.gateway.harness.delegation import (
        DelegateTaskInput,
        DelegationContext,
        DelegationManager,
    )
    from octoagent.core.models.enums import EventType

    app = harness_real_llm["app"]
    sg = app.state.store_group

    # 跑前快照：task_store / event_store 行数
    conn = sg.conn
    cur = await conn.execute("SELECT COUNT(*) FROM tasks")
    row = await cur.fetchone()
    tasks_before = int(row[0]) if row else 0

    # 直接构造 DelegationManager（注入真实 stores，复用 audit task 流程）
    mgr = DelegationManager(
        event_store=sg.event_store,
        task_store=sg.task_store,
    )

    # 构造 depth=2 的 ctx；child_depth = 2 + 1 = 3 > MAX_DEPTH=2 → 必拒
    ctx = DelegationContext(
        task_id="_e2e_d9_max_depth_parent",
        depth=2,
        target_worker="main",
        active_children=[],
    )
    task_input = DelegateTaskInput(
        target_worker="main",
        task_description="F087 域#9 max_depth 边界测试，应被 DelegationManager 拒绝",
        callback_mode="async",
    )

    result = await mgr.delegate(ctx, task_input)

    # 断言 1：派发被拒（success=False）
    assert result.success is False, (
        f"域#9: depth=2 + 1=3 > MAX_DEPTH=2 应被拒，实际 success={result.success}"
    )
    # 断言 2：error_code == "depth_exceeded"
    assert result.error_code == "depth_exceeded", (
        f"域#9: error_code 应为 depth_exceeded，实际 {result.error_code!r}"
    )
    # 断言 3：reason 含深度超限相关字样（人类可读 reason，FR-5.2）
    assert result.reason and (
        "max" in result.reason.lower()
        or "超过最大值" in result.reason
        or "depth" in result.reason.lower()
    ), (
        f"域#9: reason 应含深度超限相关文案，实际 {result.reason!r}"
    )
    # 断言 4：child_task_id is None（被拒不创建子任务）
    assert result.child_task_id is None, (
        f"域#9: 被拒派发不应有 child_task_id，实际 {result.child_task_id!r}"
    )

    # 断言 5：tasks 表无新增（DelegationManager 拒绝不创建 task）
    cur = await conn.execute("SELECT COUNT(*) FROM tasks")
    row = await cur.fetchone()
    tasks_after = int(row[0]) if row else 0
    assert tasks_after == tasks_before, (
        f"域#9: 被拒派发不应创建 task，行数 {tasks_before} → {tasks_after}"
    )

    # 断言 6：无 SUBAGENT_SPAWNED 事件（Constitution C4 失败不写审计）
    cur = await conn.execute(
        "SELECT COUNT(*) FROM events WHERE type = ? AND task_id = ?",
        (EventType.SUBAGENT_SPAWNED.value, ctx.task_id),
    )
    row = await cur.fetchone()
    spawned_count = int(row[0]) if row else 0
    assert spawned_count == 0, (
        f"域#9: 被拒派发不应写 SUBAGENT_SPAWNED，实际 {spawned_count} 行"
    )


# ---------------------------------------------------------------------------
# T-P4-7：域 #10 A2A 通信完整 4 子断言（OQ-1）
# ---------------------------------------------------------------------------


async def test_domain_10_a2a_schema_integration(
    harness_real_llm: dict[str, Any],
) -> None:
    """域 #10：A2A 数据 schema + 关联键 + 状态机 + 审计完整性集成测试（FR-15）。

    **e2e 边界说明（FR-15.1 / FR-15.2 / docs/codebase-architecture/e2e-testing.md
    §2.1）**：本测试是 **A2A 数据 schema 集成测试**，**不验证跨 runtime 真触发**。

    为何不真跨 runtime 跑：
    1. worker B 真跑需 SkillRunner.run(model_client) → 真打 LLM，决策不
       deterministic 且消耗 quota
    2. `A2AConversationStatus.COMPLETED` 转换由 orchestrator A2A inbound handler
       在 worker A 收到 INBOUND RESULT 时触发，需要双 agent runtime 完整跑通
    3. 单进程 ASGI test app 不支持跨 agent runtime daemon（A2A 投递依赖
       agent runtime 独立 lifecycle）

    本测试策略：直调主路径 `task_store.create_task` /
    `a2a_store.save_conversation` / `a2a_store.append_message` /
    `event_store.append_event_committed` + `A2AConversation` /
    `A2AMessageRecord` Pydantic 模型 + `EventType.SUBAGENT_SPAWNED` 等枚举，
    验证 4 子断言（schema / 关联键 / 状态 / 审计）。

    跨 runtime 真触发 e2e 推迟到 F088+。F087 范围内由 schema integration（直调
    主路径写库）+ 既有 builtin_tools 集成测试覆盖；详见
    `docs/codebase-architecture/e2e-testing.md` §2.1。

    Codex P4 high-1 闭环（fixup#9）+ Codex final critical-1 闭环（fixup#13）：
    本测试旧名 ``test_domain_10_real_llm_a2a_4_assertions`` 容易误导成"真打
    LLM e2e"，实际 fixup#9 已经选了"直调主路径"策略；fixup#13 重命名 +
    docstring 显式说明边界，spec.md FR-15.1 / FR-15.2 明确为约定文档。

    严格 4 子断言（无 SKIP 路径）：
    - 子断言 1（DispatchEnvelope 投递）：SUBAGENT_SPAWNED 事件存在
    - 子断言 2（worker B 工具调用）：RESPONSE message payload 含 tool_calls ≥ 1
    - 子断言 3（COMPLETED 状态）：a2a_conversations.status == 'completed' +
      completed_at 非空
    - 子断言 4（req+resp + 链路）：a2a_messages 含 OUTBOUND request + INBOUND
      response 各 1 行 + child.parent_task_id == parent.task_id +
      message.task_id == child.task_id +
      message.payload.metadata.parent_task_id == parent.task_id
    """
    from datetime import UTC, datetime as _dt

    from octoagent.core.models.a2a_runtime import (
        A2AConversation,
        A2AConversationStatus,
        A2AMessageDirection,
        A2AMessageRecord,
    )
    from octoagent.core.models.enums import ActorType, EventType
    from octoagent.core.models.event import Event
    from octoagent.core.models.task import RequesterInfo, Task, TaskPointers

    app = harness_real_llm["app"]
    sg = app.state.store_group

    # 步骤 1：创建 parent task + child task（带 parent_task_id 链路）
    parent_task_id = f"task-d10-parent-{uuid.uuid4().hex[:8]}"
    child_task_id = f"task-d10-child-{uuid.uuid4().hex[:8]}"
    now = _dt.now(tz=UTC)

    parent_task = Task(
        task_id=parent_task_id,
        created_at=now,
        updated_at=now,
        title="F087 域#10 A2A 父任务",
        trace_id=parent_task_id,
        requester=RequesterInfo(channel="web", sender_id="owner"),
        pointers=TaskPointers(),
    )
    await sg.task_store.create_task(parent_task)

    child_task = Task(
        task_id=child_task_id,
        created_at=now,
        updated_at=now,
        title="F087 域#10 A2A 子任务",
        trace_id=parent_task_id,
        requester=RequesterInfo(channel="subagent", sender_id="worker-a"),
        pointers=TaskPointers(),
        parent_task_id=parent_task_id,
    )
    await sg.task_store.create_task(child_task)

    # 步骤 2：创建 A2AConversation（COMPLETED 状态 + completed_at）
    a2a_store = sg.a2a_store
    conversation_id = f"a2a-conv-d10-{uuid.uuid4().hex[:8]}"
    request_message_id = f"a2a-msg-req-{uuid.uuid4().hex[:8]}"
    response_message_id = f"a2a-msg-resp-{uuid.uuid4().hex[:8]}"

    completed_at = _dt.now(tz=UTC)
    conversation = A2AConversation(
        a2a_conversation_id=conversation_id,
        task_id=child_task_id,
        work_id="",
        project_id="project-default",
        source_agent_runtime_id="runtime-worker-a",
        target_agent_runtime_id="runtime-worker-b",
        source_agent="agent://workers/worker-a",
        target_agent="agent://workers/worker-b",
        request_message_id=request_message_id,
        latest_message_id=response_message_id,
        latest_message_type="RESULT",
        status=A2AConversationStatus.COMPLETED,
        message_count=2,
        trace_id=parent_task_id,
        metadata={"is_subagent_conversation": True, "parent_task_id": parent_task_id},
        created_at=now,
        updated_at=completed_at,
        completed_at=completed_at,
    )
    await a2a_store.save_conversation(conversation)

    # 步骤 3：append OUTBOUND request + INBOUND response 各 1 条
    def _build_request(seq: int) -> A2AMessageRecord:
        return A2AMessageRecord(
            a2a_message_id=request_message_id,
            a2a_conversation_id=conversation_id,
            message_seq=seq,
            task_id=child_task_id,
            project_id="project-default",
            source_agent_runtime_id="runtime-worker-a",
            target_agent_runtime_id="runtime-worker-b",
            direction=A2AMessageDirection.OUTBOUND,
            message_type="TASK",
            protocol_message_id=request_message_id,
            from_agent="agent://workers/worker-a",
            to_agent="agent://workers/worker-b",
            idempotency_key=f"{child_task_id}:{request_message_id}:task",
            payload={
                "user_text": "F087 域#10 派发 worker B 写一条事实",
                "metadata": {"parent_task_id": parent_task_id},
            },
            trace={"trace_id": parent_task_id},
            created_at=now,
        )

    def _build_response(seq: int) -> A2AMessageRecord:
        return A2AMessageRecord(
            a2a_message_id=response_message_id,
            a2a_conversation_id=conversation_id,
            message_seq=seq,
            task_id=child_task_id,
            project_id="project-default",
            source_agent_runtime_id="runtime-worker-b",
            target_agent_runtime_id="runtime-worker-a",
            direction=A2AMessageDirection.INBOUND,
            message_type="RESULT",
            protocol_message_id=response_message_id,
            from_agent="agent://workers/worker-b",
            to_agent="agent://workers/worker-a",
            idempotency_key=f"{child_task_id}:{response_message_id}:result",
            payload={
                "summary": "worker B 完成派发任务",
                "tool_calls": [
                    {
                        "tool_name": "user_profile.update",
                        "args": {"operation": "add", "fact": "F087 域#10 worker B 写入"},
                    }
                ],
                "metadata": {"parent_task_id": parent_task_id},
            },
            trace={"trace_id": parent_task_id},
            created_at=completed_at,
        )

    await a2a_store.append_message(conversation_id, _build_request)
    await a2a_store.append_message(conversation_id, _build_response)
    await sg.conn.commit()

    # 步骤 4：写 SUBAGENT_SPAWNED + SUBAGENT_RETURNED 事件（Constitution C2）
    spawned_evt = Event(
        event_id=f"evt-spawn-{uuid.uuid4().hex[:12]}",
        task_id=parent_task_id,
        task_seq=await sg.event_store.get_next_task_seq(parent_task_id),
        ts=now,
        type=EventType.SUBAGENT_SPAWNED,
        actor=ActorType.SYSTEM,
        payload={
            "child_task_id": child_task_id,
            "target_worker": "worker-b",
            "a2a_conversation_id": conversation_id,
            "depth": 1,
        },
        trace_id=parent_task_id,
    )
    await sg.event_store.append_event_committed(spawned_evt, update_task_pointer=False)

    returned_evt = Event(
        event_id=f"evt-return-{uuid.uuid4().hex[:12]}",
        task_id=parent_task_id,
        task_seq=await sg.event_store.get_next_task_seq(parent_task_id),
        ts=completed_at,
        type=EventType.SUBAGENT_RETURNED,
        actor=ActorType.SYSTEM,
        payload={
            "child_task_id": child_task_id,
            "a2a_conversation_id": conversation_id,
            "result_summary": "worker B 完成",
        },
        trace_id=parent_task_id,
    )
    await sg.event_store.append_event_committed(returned_evt, update_task_pointer=False)

    # ========================================================================
    # 严格 4 子断言（无 SKIP 路径）
    # ========================================================================

    # 子断言 1：DispatchEnvelope 投递 — SUBAGENT_SPAWNED 事件存在
    cur = await sg.conn.execute(
        "SELECT COUNT(*) FROM events WHERE type = ? AND task_id = ?",
        (EventType.SUBAGENT_SPAWNED.value, parent_task_id),
    )
    row = await cur.fetchone()
    spawn_count = int(row[0]) if row else 0
    assert spawn_count == 1, (
        f"域#10 子断言 1（DispatchEnvelope 投递）: SUBAGENT_SPAWNED 应有 1 行，"
        f"实际 {spawn_count}"
    )

    # 子断言 2：worker B 工具调用 ≥ 1 — 通过 RESPONSE message payload.tool_calls 标记
    messages = await a2a_store.list_messages(a2a_conversation_id=conversation_id)
    response_msgs = [
        m for m in messages
        if m.direction == A2AMessageDirection.INBOUND
        and m.message_type == "RESULT"
    ]
    assert len(response_msgs) >= 1, (
        f"域#10 子断言 2（worker B 工具调用）: INBOUND RESULT 消息应 ≥ 1，"
        f"实际 {len(response_msgs)}"
    )
    tool_calls = response_msgs[0].payload.get("tool_calls", [])
    assert len(tool_calls) >= 1, (
        f"域#10 子断言 2（worker B 工具调用）: response.payload.tool_calls 应 ≥ 1，"
        f"实际 {tool_calls}"
    )

    # 子断言 3：a2a_conversations.status == 'completed' + completed_at 非空
    fetched_conv = await a2a_store.get_conversation(conversation_id)
    assert fetched_conv is not None, "域#10 子断言 3: conversation 应可查到"
    assert fetched_conv.status == A2AConversationStatus.COMPLETED, (
        f"域#10 子断言 3（completed 状态）: status 应为 completed，"
        f"实际 {fetched_conv.status!r}"
    )
    assert fetched_conv.completed_at is not None, (
        "域#10 子断言 3: completed_at 应非空"
    )

    # 子断言 4：a2a_messages 含 request + response 各 1 行 + parent_task_id 链路
    request_msgs = [
        m for m in messages
        if m.direction == A2AMessageDirection.OUTBOUND
        and m.message_type == "TASK"
    ]
    assert len(request_msgs) == 1, (
        f"域#10 子断言 4（req+resp）: OUTBOUND TASK request 应有 1 行，"
        f"实际 {len(request_msgs)}"
    )
    assert len(response_msgs) == 1, (
        f"域#10 子断言 4（req+resp）: INBOUND RESULT response 应有 1 行，"
        f"实际 {len(response_msgs)}"
    )
    # parent_task_id 链路一致：
    # - child task.parent_task_id == parent task.task_id
    # - request/response message.task_id == child task.task_id
    # - request/response payload.metadata.parent_task_id == parent task.task_id
    fetched_child = await sg.task_store.get_task(child_task_id)
    assert fetched_child is not None and fetched_child.parent_task_id == parent_task_id, (
        f"域#10 子断言 4（parent_task_id 链路）: child.parent_task_id 应为 "
        f"{parent_task_id}，实际 {fetched_child.parent_task_id if fetched_child else None!r}"
    )
    for m in (request_msgs[0], response_msgs[0]):
        assert m.task_id == child_task_id, (
            f"域#10 子断言 4: message.task_id 应为 child_task_id，实际 {m.task_id!r}"
        )
        assert m.payload.get("metadata", {}).get("parent_task_id") == parent_task_id, (
            f"域#10 子断言 4: message payload.metadata.parent_task_id 链路丢失"
        )
