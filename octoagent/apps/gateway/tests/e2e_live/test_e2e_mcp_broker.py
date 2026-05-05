"""F089 L1：``broker.execute("mcp.<server>.<tool>", ...)`` 完整审计链路。

补全 F087 漏覆盖的关键链路——直到 L1 之前，没有任何测试走通：

    LLM-driven broker.execute  →  permission gate
                              →   TOOL_CALL_STARTED 事件持久化（带 task 外键）
                              →   handler ─→ mcp_registry.call_tool
                              →                ─→ mcp_session_pool.get_session
                              →                ─→ session.call_tool（stdio subprocess）
                              →   TOOL_CALL_COMPLETED 事件持久化

历史问题（F089 commit body）：
    旧 ``McpSessionPool`` 在调用方 task 内 enter ``stdio_client`` /
    ``ClientSession`` 的 anyio AsyncExitStack；fixture teardown / lifespan
    shutdown 在不同 task 内 close 时抛
    ``RuntimeError: Attempted to exit cancel scope in a different task``，
    打断 stdio_client finally 块 → stdio 子进程不被 SIGTERM 干净，e2e 反复
    setup/teardown 累积 zombie process / FD 泄漏。

修复路径（``mcp_session_pool.py`` 同 PR 改）：
    每个 server 起一个专属 supervisor asyncio.Task，由它在自己的 task 内
    enter / exit 整个 anyio context；主路径只通过 ``stop_event`` 通知收尾。
    ``ClientSession.send_request``（``call_tool``）走 anyio MemoryObjectStream
    本就 task-safe，主路径在任意 task 内 ``await session.call_tool(...)``
    都能命中 supervisor 内的 background ``_receive_loop``。

本 case 充当**双重 anti-regression**：
1. cross-task 关闭不再抛 cancel scope error（fixture 在自己的 task 内
   ``open()``，主测试在另一 task 调用 ``broker.execute`` 间接触发；
   teardown 时 supervisor 由 stop_event 通知，干净退出）
2. broker → mcp_registry → mcp_session_pool → session.call_tool 全链路
   返回正确响应，且 TOOL_CALL_STARTED / TOOL_CALL_COMPLETED 事件落盘
   且 task_id 外键有效。
"""
from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from octoagent.core.models.enums import EventType
from octoagent.core.models.task import RequesterInfo, Task, TaskPointers
from octoagent.core.store import create_store_group
from octoagent.gateway.services.mcp_registry import McpRegistryService, McpServerConfig
from octoagent.gateway.services.mcp_session_pool import McpSessionPool
from octoagent.policy.approval_manager import ApprovalManager
from octoagent.policy.approval_override_store import (
    ApprovalOverrideCache,
    ApprovalOverrideRepository,
)
from octoagent.tooling.broker import ToolBroker
from octoagent.tooling.models import ExecutionContext, PermissionPreset

# 注：本 case 走真实 stdio subprocess（~50ms 启动 + 单次 call_tool < 50ms），
# 在 e2e_live conftest SIGALRM 30s smoke timeout 内绰绰有余；保留 e2e_live
# marker 让 hermetic env / module reset 生效，e2e_smoke marker 让 pre-commit
# 默认跑到。
pytestmark = [pytest.mark.e2e_smoke, pytest.mark.e2e_live]

_STUB = Path(__file__).resolve().parent / "_mcp_stub_server.py"


@pytest.fixture
async def harness_mcp_broker(tmp_path: Path) -> dict[str, Any]:
    """组装最小 broker 全链路：StoreGroup + ApprovalManager + ToolBroker + Pool + Registry。

    刻意**不走 OctoHarness.bootstrap**：保留最小依赖面，便于精确归因 mcp 链路
    出 bug 时责任边界。完整 OctoHarness 路径已被 e2e_smoke 其它 case 覆盖。
    """
    sg = await create_store_group(
        db_path=str(tmp_path / "test.db"),
        artifacts_dir=tmp_path / "artifacts",
    )

    override_cache = ApprovalOverrideCache()
    override_repo = ApprovalOverrideRepository(conn=sg.conn, cache=override_cache)
    approval_manager = ApprovalManager(
        override_cache=override_cache,
        override_repo=override_repo,
    )

    broker = ToolBroker(
        event_store=sg.event_store,
        artifact_store=sg.artifact_store,
        override_cache=override_cache,
        approval_manager=approval_manager,
    )
    pool = McpSessionPool()
    registry = McpRegistryService(
        project_root=tmp_path,
        tool_broker=broker,
        server_configs=[
            McpServerConfig(
                name="stub",
                command=sys.executable,
                args=[str(_STUB)],
            )
        ],
        session_pool=pool,
    )
    await registry.refresh()

    yield {
        "store_group": sg,
        "broker": broker,
        "pool": pool,
        "registry": registry,
        "approval_manager": approval_manager,
    }

    # teardown：先 registry.shutdown（含 pool.close_all 走 supervisor 干净退出），
    # 再关 db conn——supervisor 的 stdio_client / ClientSession 退出
    # 在 supervisor 自己的 task 内执行（F089 修复点），不再触发 cross-task
    # cancel scope 错误。
    await registry.shutdown()
    await sg.conn.close()


async def _seed_audit_task(sg: Any, task_id: str) -> None:
    """种 audit Task 行，让 events.task_id 外键约束通过。"""
    now = datetime.now(UTC)
    task = Task(
        task_id=task_id,
        created_at=now,
        updated_at=now,
        title=f"F089 L1 audit task ({task_id})",
        trace_id=task_id,
        requester=RequesterInfo(channel="system", sender_id="system"),
        pointers=TaskPointers(),
    )
    await sg.task_store.create_task(task)


async def test_mcp_broker_execute_full_audit_chain(
    harness_mcp_broker: dict[str, Any],
) -> None:
    """完整 broker.execute → mcp tool 审计链：返回值 + 事件 + 状态。

    断言（≥ 4 独立点，对应 F089 spec.md §6.1 验收清单）：

    A. ``broker.execute`` 返回 ``is_error=False``（permission gate 通过 +
       handler 正常返回）
    B. ``output`` 含 stub server echo 文本（链路真接通到 stdio 子进程）
    C. ``TOOL_CALL_STARTED`` 事件已持久化到 EventStore：
       - ``task_id`` 关联到 audit task（外键 OK）
       - ``payload.tool_name`` == ``mcp.stub.echo``
    D. ``TOOL_CALL_COMPLETED`` 事件已持久化：duration_ms > 0
    """
    sg = harness_mcp_broker["store_group"]
    broker = harness_mcp_broker["broker"]

    audit_task_id = "f089-l1-audit-1"
    await _seed_audit_task(sg, audit_task_id)

    # broker 内部应已收到 mcp.stub.echo 注册（registry.refresh 走 pool 持久 session）
    assert "mcp.stub.echo" in broker._registry, (
        f"mcp.stub.echo 未注册到 broker；现注册项: "
        f"{[n for n in broker._registry if n.startswith('mcp.')]}"
    )

    ctx = ExecutionContext(
        task_id=audit_task_id,
        trace_id=audit_task_id,
        caller="f089_l1",
        permission_preset=PermissionPreset.FULL,
    )

    # 在专属 asyncio task 内调 broker.execute——刻意制造 cross-task 场景，
    # 锁定 supervisor 模式正确性。修复前此处 close 期间会触发 cancel scope
    # error；修复后 supervisor 在自己的 task 内退出，全链路干净。
    async def _exec() -> Any:
        return await broker.execute(
            "mcp.stub.echo",
            {"text": "f089-l1-hello"},
            ctx,
        )

    result = await asyncio.wait_for(asyncio.create_task(_exec()), timeout=15.0)

    # ─ 断言 A：result 非错
    assert not result.is_error, (
        f"broker.execute 应非错；error={result.error!r} output={result.output!r}"
    )

    # ─ 断言 B：output 含 stub echo 文本
    assert "f089-l1-hello" in result.output, (
        f"output 应回带 stub echo 文本；output={result.output!r}"
    )
    assert result.duration > 0, "ToolResult.duration 应记录耗时"

    # ─ 断言 C/D：事件已落盘 + 关联 audit task
    events = await sg.event_store.get_events_for_task(audit_task_id)
    types_ = [e.type for e in events]
    assert EventType.TOOL_CALL_STARTED in types_, (
        f"TOOL_CALL_STARTED 应落盘；实际事件 type 序列: {types_}"
    )
    assert EventType.TOOL_CALL_COMPLETED in types_, (
        f"TOOL_CALL_COMPLETED 应落盘；实际事件 type 序列: {types_}"
    )

    started = next(e for e in events if e.type == EventType.TOOL_CALL_STARTED)
    completed = next(e for e in events if e.type == EventType.TOOL_CALL_COMPLETED)

    # task_id 外键关联
    assert started.task_id == audit_task_id
    assert completed.task_id == audit_task_id

    # payload tool_name 字段
    assert (started.payload or {}).get("tool_name") == "mcp.stub.echo", (
        f"STARTED.payload.tool_name 应 == mcp.stub.echo；payload={started.payload!r}"
    )
    assert (completed.payload or {}).get("tool_name") == "mcp.stub.echo"

    # COMPLETED.duration_ms 应 ≥ 0（极快路径下可能 0，但字段必须存在）
    assert "duration_ms" in (completed.payload or {})


async def test_mcp_pool_recovers_after_supervisor_death(
    harness_mcp_broker: dict[str, Any],
) -> None:
    """anti-regression（Codex review medium#3）：supervisor 异常退出后 get_session 自动重连。

    历史漏洞（修前）：``McpSessionPool.get_session`` 仅看 ``status='connected'
    and session is not None``，不检查 supervisor task 是否已 done；当 stdio
    子进程崩 / 远端 transport 异常导致 supervisor 提前退出时，``entry.session``
    仍指向 stale ClientSession，``call_tool`` 在 closed stream 上立即报错或
    永久阻塞，且不会触发自动重连。

    本 case 用 ``supervisor.cancel()`` 模拟 supervisor 异常退出（等价于
    stdio 进程崩溃 → AsyncExitStack.__aexit__ 在 supervisor 内执行）；
    之后 ``get_session`` 应识别 stale 状态并走 ``open()`` 重连路径，再次
    ``broker.execute`` 应正常返回。
    """
    sg = harness_mcp_broker["store_group"]
    broker = harness_mcp_broker["broker"]
    pool = harness_mcp_broker["pool"]

    audit_task_id = "f089-l1-recover-1"
    await _seed_audit_task(sg, audit_task_id)

    ctx = ExecutionContext(
        task_id=audit_task_id,
        trace_id=audit_task_id,
        caller="f089_l1_recover",
        permission_preset=PermissionPreset.FULL,
    )

    # 第 1 次调用：正常路径，session = S1
    r1 = await asyncio.wait_for(
        broker.execute("mcp.stub.echo", {"text": "before-crash"}, ctx),
        timeout=15.0,
    )
    assert not r1.is_error, f"第 1 次调用应成功；error={r1.error!r}"

    entry = pool.get_entry("stub")
    assert entry is not None and entry.supervisor_task is not None
    s1 = entry.session
    sup1 = entry.supervisor_task

    # 模拟 supervisor 异常退出（等价 stdio 进程崩 / transport 断）
    sup1.cancel()
    try:
        await sup1
    except asyncio.CancelledError:
        pass
    assert sup1.done()

    # 第 2 次调用：get_session 应识别 supervisor 死 → 触发 open() 重连，
    # 拿到新 session S2 ≠ S1，broker.execute 仍能成功。
    r2 = await asyncio.wait_for(
        broker.execute("mcp.stub.echo", {"text": "after-recover"}, ctx),
        timeout=20.0,
    )
    assert not r2.is_error, (
        f"第 2 次调用应在 supervisor 死后自动重连；error={r2.error!r}"
    )
    assert "after-recover" in r2.output

    entry2 = pool.get_entry("stub")
    assert entry2 is not None
    s2 = entry2.session
    assert s2 is not None
    assert s2 is not s1, "重连后应是新 ClientSession 实例"
    assert entry2.reconnect_count >= 1, (
        f"reconnect_count 应 ≥ 1；实际 {entry2.reconnect_count}"
    )
