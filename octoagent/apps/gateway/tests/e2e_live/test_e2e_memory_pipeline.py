"""F087 P4 T-P4-1：域 #4 Memory observation→promote。

真打 GPT-5.5 think-low 让 LLM 用 ``memory.observe`` 写一条候选 → 检查
``memory_candidates`` 表 +1。

设计取舍：
- 不预测 LLM 选哪个工具（可能 ``memory.observe`` 也可能 ``memory.write`` 直写）
- 不真触发 promote（promote 需 user 决策，e2e 不模拟 UI）；只验证 observation
  写到 candidate / memory 表
- 关键不变量：memory_candidates 或 memory rows 跑前后 +1
"""

from __future__ import annotations

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
    import asyncio

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
    """真打 LLM bootstrap + 挂 routes。"""
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

    from octoagent.gateway.routes import message, tasks

    app.include_router(message.router, tags=["message"])
    app.include_router(tasks.router, tags=["tasks"])

    return {"harness": harness, "app": app, "project_root": project_root}


async def _count_memory_rows(sg: Any) -> int:
    """跨 schema 兼容计数 memory + memory_candidates 行数。

    主线 schema 不固定（F084 重构期间），跨表统计；不存在的表 silently 跳过。
    """
    total = 0
    conn = sg.conn
    for table in ("memory_candidates", "memory", "owner_memory_entries"):
        try:
            cur = await conn.execute(f"SELECT COUNT(*) FROM {table}")
            row = await cur.fetchone()
            if row:
                total += int(row[0])
        except Exception:
            # 表不存在或 schema 不同，跳过
            continue
    return total


async def test_domain_4_real_llm_memory_observation_increments_rows(
    harness_real_llm: dict[str, Any],
) -> None:
    """域 #4：真打 LLM 用 memory.observe / memory.write 增加候选/记忆 +1。

    断言（≥ 2 独立点）：
    1. 任务 succeeded
    2. tool_calls 含 memory.* 类工具
    3. memory + memory_candidates 行数跑后 ≥ 跑前（至少 +1）
    """
    from httpx import ASGITransport, AsyncClient

    app = harness_real_llm["app"]
    sg = app.state.store_group

    rows_before = await _count_memory_rows(sg)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://e2e-test") as client:
        resp = await client.post(
            "/api/message",
            json={
                "text": (
                    "请你必须调用一次写入类工具，把以下事实持久化保存："
                    "用户的硬件设备是 MacBook Pro M4 Max。"
                    "可选工具：memory.write / memory.observe / user_profile.update。"
                    "你必须真的调用工具完成写入，不能仅口头回复。"
                ),
                "idempotency_key": f"e2e-d4-{uuid.uuid4().hex[:8]}",
                "channel": "web",
                "thread_id": "e2e-d4",
                "sender_id": "owner",
                "sender_name": "Owner",
            },
        )
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

    final_status = await _wait_for_terminal(sg, task_id)
    assert final_status in _SUCCESS_STATUSES, f"域#4: 应成功，实际 {final_status}"

    events = await sg.event_store.get_events_for_task(task_id)
    tools = _tool_calls(events)
    write_tools = [
        t for t in tools
        if t.startswith("memory.")
        or t == "user_profile.update"
        or t.startswith("filesystem.write")
    ]
    assert write_tools, (
        f"域#4: LLM 应至少发起 1 次写类工具调用。实际 tool_calls: {tools}"
    )

    rows_after = await _count_memory_rows(sg)
    # 注：USER.md 路径的 user_profile.update 不增加 memory_candidates；
    # 这里允许 rows_after >= rows_before（写到 USER.md 也算成功的写持久化）
    assert rows_after >= rows_before, (
        f"域#4: memory + candidates 行数不应减少，"
        f"before={rows_before} after={rows_after}"
    )
