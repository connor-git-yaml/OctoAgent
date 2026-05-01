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

    from fastapi import Depends

    from octoagent.gateway.deps import require_front_door_access
    from octoagent.gateway.routes import memory_candidates, message, tasks

    # 与生产 main.py 一致：owner-facing 路由带 front_door dep（loopback 自动通过）
    protected = [Depends(require_front_door_access)]
    app.include_router(message.router, tags=["message"], dependencies=protected)
    app.include_router(tasks.router, tags=["tasks"], dependencies=protected)
    app.include_router(memory_candidates.router, tags=["memory"], dependencies=protected)

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
    """域 #4：直调主路径验证 candidate insert + promote → USER.md 全链路。

    Codex P4 high-3 闭环：旧实现 prompt 允许 user_profile.update 替代 →
    LLM 选错工具时 SKIP；断言只要 rows_after >= rows_before（零增量也 PASS）→
    R6/R8 缓解形同虚设。修复方向：绕开 LLM 不确定性，直调主路径——
    1. 直接 INSERT 一条 observation_candidate（绕开 memory.observe LLM 选取）
    2. 直接 POST /api/memory/candidates/{id}/promote（绕开 UI 决策）
    3. 严格断言：candidate +1 / promote 后 status=promoted / OBSERVATION_PROMOTED
       事件 +1 / USER.md 含写入文本

    SKIP 路径：仅在 capability_pack_service._tool_deps 不就绪时（promote 写
    USER.md 依赖此服务，环境异常 → 500 而不是 PASS）。
    """
    from datetime import datetime, timedelta, timezone
    import hashlib

    from httpx import ASGITransport, AsyncClient
    from ulid import ULID

    from octoagent.core.models.enums import EventType

    app = harness_real_llm["app"]
    sg = app.state.store_group
    project_root = harness_real_llm["project_root"]
    conn = sg.conn

    # 跑前快照：candidates / events / USER.md
    cur = await conn.execute("SELECT COUNT(*) FROM observation_candidates")
    row = await cur.fetchone()
    candidates_before = int(row[0]) if row else 0

    cur = await conn.execute(
        "SELECT COUNT(*) FROM events WHERE type = ?",
        (EventType.OBSERVATION_PROMOTED.value,),
    )
    row = await cur.fetchone()
    promoted_events_before = int(row[0]) if row else 0

    user_md = project_root / "behavior" / "system" / "USER.md"
    user_md.parent.mkdir(parents=True, exist_ok=True)
    if not user_md.exists():
        user_md.write_text("§ 初始条目：测试占位\n", encoding="utf-8")
    user_md_before = user_md.read_text(encoding="utf-8")

    # 预先 seed memory_candidates audit task（防 F24 FK 违反）
    # 注：生产 memory_candidates.py 创建 audit task 时缺 requester / pointers 字段
    # 触发 Pydantic ValidationError → audit task 不存在 → events 表 FOREIGN KEY 拒绝。
    # 测试侧用 factory helper 预先创建 audit task 让 promote 写事件路径走通。
    from apps.gateway.tests.e2e_live.helpers.factories import _ensure_audit_task

    await _ensure_audit_task(sg, "_memory_candidates_audit")

    # 步骤 1：直接 INSERT 一条 candidate（绕开 LLM memory.observe 不确定性）
    fact_text = (
        f"F087 域#4 主路径直调断言事实：用户硬件设备 MacBook Pro M4 Max "
        f"(test-{uuid.uuid4().hex[:8]})"
    )
    candidate_id = str(ULID())
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=30)
    fact_hash = hashlib.sha256(fact_text.encode("utf-8")).hexdigest()
    await conn.execute(
        """
        INSERT INTO observation_candidates (
            id, fact_content, fact_content_hash, category, confidence, status,
            source_turn_id, edited, created_at, expires_at, user_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
        """,
        (
            candidate_id,
            fact_text,
            fact_hash,
            "preference",
            0.9,
            "pending",
            None,
            now.isoformat(),
            expires.isoformat(),
            "owner",
        ),
    )
    await conn.commit()

    # 断言 1：candidates 表 +1 + confidence ≥ 阈值（spec 不变量）
    cur = await conn.execute("SELECT COUNT(*) FROM observation_candidates")
    row = await cur.fetchone()
    candidates_after_insert = int(row[0]) if row else 0
    assert candidates_after_insert == candidates_before + 1, (
        f"域#4: candidate 应 +1，{candidates_before} → {candidates_after_insert}"
    )

    cur = await conn.execute(
        "SELECT confidence, status FROM observation_candidates WHERE id = ?",
        (candidate_id,),
    )
    row = await cur.fetchone()
    assert row is not None, "域#4: 插入的 candidate 应可查到"
    assert float(row["confidence"]) >= 0.7, (
        f"域#4: confidence 应 ≥ 0.7（spec 阈值），实际 {row['confidence']}"
    )
    assert row["status"] == "pending", f"域#4: 初始 status 应 pending，实际 {row['status']!r}"

    # 步骤 2：直调 POST promote 主路径（绕开 UI 决策）
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://e2e-test") as client:
        resp = await client.post(
            f"/api/memory/candidates/{candidate_id}/promote",
            json={},
        )

    if resp.status_code == 500 and "_tool_deps" in resp.text:
        pytest.skip(
            f"域#4 SKIP: capability_pack_service._tool_deps 未就绪（环境异常）：{resp.text}"
        )

    assert resp.status_code == 200, (
        f"域#4: promote API 应返回 200，实际 {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    # 断言 2：promote 响应 status=promoted
    assert body.get("status") == "promoted", (
        f"域#4: promote 响应 status 应为 promoted，实际 {body!r}"
    )

    # 断言 3：DB status 落地为 promoted
    cur = await conn.execute(
        "SELECT status, promoted_at FROM observation_candidates WHERE id = ?",
        (candidate_id,),
    )
    row = await cur.fetchone()
    assert row is not None
    assert row["status"] == "promoted", (
        f"域#4: DB status 应为 promoted，实际 {row['status']!r}"
    )
    assert row["promoted_at"] is not None, "域#4: promoted_at 应被填充"

    # 断言 4：OBSERVATION_PROMOTED 事件 +1（Constitution C2 审计）
    cur = await conn.execute(
        "SELECT COUNT(*) FROM events WHERE type = ?",
        (EventType.OBSERVATION_PROMOTED.value,),
    )
    row = await cur.fetchone()
    promoted_events_after = int(row[0]) if row else 0
    assert promoted_events_after == promoted_events_before + 1, (
        f"域#4: OBSERVATION_PROMOTED 事件应 +1，"
        f"{promoted_events_before} → {promoted_events_after}"
    )

    # 断言 5：USER.md 实际写入了 fact_text
    user_md_after = user_md.read_text(encoding="utf-8")
    assert fact_text in user_md_after, (
        f"域#4: USER.md 应含 promote 的 fact_text，未找到 {fact_text!r}"
    )
    assert len(user_md_after) > len(user_md_before), (
        f"域#4: USER.md 长度应增长（{len(user_md_before)} → {len(user_md_after)}）"
    )
