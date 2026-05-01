"""Routes 层 promote endpoint 真实主路径测试（F088 修复验证）。

之前的 test_observation_promote.py 是"直接模拟 promote 路径"——绕过 FastAPI app
直接操作 DB / SnapshotStore，无法验证 routes/memory_candidates.py 的 _emit_event
+ ensure audit task 真实链路。

本文件覆盖 F088 修复：之前 routes/memory_candidates.py:128-138 创建 audit Task 时
漏 requester / pointers 必填字段 → pydantic ValidationError → audit task 创建失败
→ events 表 FK 违反 → MEMORY_ENTRY_ADDED / OBSERVATION_PROMOTED 事件 silent 丢失
→ Constitution C2「Everything is an Event」违反。

测试不预 seed _memory_candidates_audit 任务，强制走 ensure_system_audit_task helper
路径，验证：
1. promote endpoint 返回 200
2. tasks 表里 _memory_candidates_audit 真实落库（含合法 requester/pointers）
3. events 表里 MEMORY_ENTRY_ADDED + OBSERVATION_PROMOTED 事件成功写入
4. USER.md 真实被写入 fact_content
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from ulid import ULID

from octoagent.core.models.enums import EventType
from octoagent.core.store import create_store_group
from octoagent.gateway.harness.snapshot_store import SnapshotStore
from octoagent.gateway.routes import memory_candidates as memory_candidates_router
from octoagent.gateway.services.builtin_tools._deps import ToolDeps


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def store_group(tmp_path: Path):
    """完整 schema 的 StoreGroup（EventStore + TaskStore + conn）。"""
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    sg = await create_store_group(
        db_path=str(tmp_path / "test.db"),
        artifacts_dir=str(artifacts_dir),
    )
    yield sg
    await sg.conn.close()


@pytest_asyncio.fixture
async def project_root(tmp_path: Path) -> Path:
    """临时 project_root，含 behavior/system/USER.md 占位文件。"""
    user_md = tmp_path / "behavior" / "system" / "USER.md"
    user_md.parent.mkdir(parents=True, exist_ok=True)
    user_md.write_text("", encoding="utf-8")
    return tmp_path


@pytest_asyncio.fixture
async def test_app(store_group, project_root: Path) -> FastAPI:
    """构造 mini FastAPI app + capability_pack_service mock，承载 routes 主路径。"""
    user_md = project_root / "behavior" / "system" / "USER.md"
    snap_store = SnapshotStore(conn=store_group.conn)
    await snap_store.load_snapshot(
        session_id="test-routes-audit",
        files={"USER.md": user_md},
    )

    deps = ToolDeps(
        project_root=project_root,
        stores=store_group,
        tool_broker=MagicMock(),
        tool_index=MagicMock(),
        skill_discovery=MagicMock(),
        memory_console_service=MagicMock(),
        memory_runtime_service=MagicMock(),
        _snapshot_store=snap_store,
    )

    cap_mock = MagicMock()
    cap_mock._tool_deps = deps

    app = FastAPI()
    app.include_router(memory_candidates_router.router, tags=["memory"])
    app.state.store_group = store_group
    app.state.capability_pack_service = cap_mock
    return app


@pytest_asyncio.fixture
async def client(test_app: FastAPI):
    async with AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://test",
    ) as ac:
        yield ac


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _insert_candidate(conn: Any, *, fact_content: str) -> str:
    """向 observation_candidates 表插入一条 pending 候选，返回 id。"""
    candidate_id = str(ULID())
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=30)
    await conn.execute(
        """
        INSERT INTO observation_candidates (
            id, fact_content, fact_content_hash, category, confidence, status,
            source_turn_id, edited, created_at, expires_at, user_id
        ) VALUES (?, ?, ?, ?, ?, 'pending', ?, 0, ?, ?, 'owner')
        """,
        (
            candidate_id,
            fact_content,
            hashlib.sha256(fact_content.encode()).hexdigest(),
            "preference",
            0.85,
            str(ULID()),
            now.isoformat(),
            expires.isoformat(),
        ),
    )
    await conn.commit()
    return candidate_id


# ---------------------------------------------------------------------------
# 主路径：promote endpoint 真实链路（核心 F088 验证）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_promote_route_auto_creates_audit_task_and_emits_events(
    client: AsyncClient,
    store_group,
    project_root: Path,
):
    """F088 核心验证：调真实 promote endpoint，audit task 自动落库 + 事件写入。

    覆盖之前 routes/memory_candidates.py:128-138 漏 requester/pointers
    导致 audit task 创建 ValidationError → 事件 FK violation 全部丢失的回归。

    断言路径：
    1. 不预 seed _memory_candidates_audit 任务
    2. 调 POST /api/memory/candidates/{id}/promote 返回 200
    3. tasks 表里 _memory_candidates_audit 真实落库
       (validate Task model 时 requester/pointers 字段合法)
    4. events 表里 MEMORY_ENTRY_ADDED + OBSERVATION_PROMOTED 都成功写入
    5. USER.md 真实被 append 写入 fact_content
    """
    conn = store_group.conn
    fact_content = "用户偏好早晨完成深度工作，专注力最强"
    candidate_id = await _insert_candidate(conn, fact_content=fact_content)

    # 前置断言：audit task 不存在（确认主路径必须自动创建，不依赖 pre-seed）
    pre_existing = await store_group.task_store.get_task("_memory_candidates_audit")
    assert pre_existing is None, "前置假设：audit task 不应预先存在"

    response = await client.post(
        f"/api/memory/candidates/{candidate_id}/promote",
        json={},
    )
    assert response.status_code == 200, (
        f"promote 应返回 200, 实际: {response.status_code} body={response.text}"
    )
    body = response.json()
    assert body["status"] == "promoted"
    assert body["candidate_id"] == candidate_id

    # 1. audit task 真实落库（核心 F088 验证：之前 ValidationError 会让此处 None）
    audit_task = await store_group.task_store.get_task("_memory_candidates_audit")
    assert audit_task is not None, (
        "F088 回归：audit task 必须自动创建（之前漏 requester/pointers 字段 → "
        "pydantic ValidationError → silent 失败 → 此处 None）"
    )
    assert audit_task.requester.channel == "system", (
        "ensure_system_audit_task helper 必须设置 requester.channel='system'"
    )
    assert audit_task.requester.sender_id == "_memory_candidates_audit", (
        "ensure_system_audit_task helper 把 sender_id 设为 task_id"
    )

    # 2. events 表里两条事件成功写入（FK 不再 violation）
    events = await store_group.event_store.get_events_for_task(
        "_memory_candidates_audit"
    )
    event_types = [e.type for e in events]
    assert EventType.MEMORY_ENTRY_ADDED in event_types, (
        "F088 回归：MEMORY_ENTRY_ADDED 应被写入 events 表（之前 FK violation silent 丢失）"
    )
    assert EventType.OBSERVATION_PROMOTED in event_types, (
        "F088 回归：OBSERVATION_PROMOTED 应被写入 events 表（之前 FK violation silent 丢失）"
    )

    promoted_events = [e for e in events if e.type == EventType.OBSERVATION_PROMOTED]
    assert promoted_events[-1].payload.get("candidate_id") == candidate_id

    # 3. USER.md 真实写入
    user_md = project_root / "behavior" / "system" / "USER.md"
    actual = user_md.read_text(encoding="utf-8")
    assert fact_content in actual, "promote 主路径必须写入 USER.md"

    # 4. 候选状态 = promoted
    async with conn.execute(
        "SELECT status FROM observation_candidates WHERE id = ?",
        (candidate_id,),
    ) as cur:
        row = await cur.fetchone()
    assert row["status"] == "promoted"


@pytest.mark.asyncio
async def test_discard_route_auto_creates_audit_task_and_emits_event(
    client: AsyncClient,
    store_group,
):
    """discard 路径同样验证 audit task 自动创建 + OBSERVATION_DISCARDED 事件落库。"""
    conn = store_group.conn
    candidate_id = await _insert_candidate(
        conn, fact_content="用户不想要保存的信息"
    )

    pre_existing = await store_group.task_store.get_task("_memory_candidates_audit")
    assert pre_existing is None

    response = await client.post(f"/api/memory/candidates/{candidate_id}/discard")
    assert response.status_code == 200, response.text

    audit_task = await store_group.task_store.get_task("_memory_candidates_audit")
    assert audit_task is not None, (
        "F088 回归：discard 路径同样依赖 ensure_system_audit_task 自动创建"
    )

    events = await store_group.event_store.get_events_for_task(
        "_memory_candidates_audit"
    )
    discarded = [e for e in events if e.type == EventType.OBSERVATION_DISCARDED]
    assert discarded, "OBSERVATION_DISCARDED 事件应成功写入（FK 不再 violation）"
    assert discarded[-1].payload.get("candidate_id") == candidate_id


@pytest.mark.asyncio
async def test_bulk_discard_route_auto_creates_audit_task_and_emits_event(
    client: AsyncClient,
    store_group,
):
    """bulk_discard 路径同样验证 audit task 自动创建 + 批量 OBSERVATION_DISCARDED 事件落库。"""
    conn = store_group.conn
    cid_a = await _insert_candidate(conn, fact_content="批量丢弃候选 A")
    cid_b = await _insert_candidate(conn, fact_content="批量丢弃候选 B")

    pre_existing = await store_group.task_store.get_task("_memory_candidates_audit")
    assert pre_existing is None

    response = await client.put(
        "/api/memory/candidates/bulk_discard",
        json={"candidate_ids": [cid_a, cid_b]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["discarded_count"] == 2

    audit_task = await store_group.task_store.get_task("_memory_candidates_audit")
    assert audit_task is not None

    events = await store_group.event_store.get_events_for_task(
        "_memory_candidates_audit"
    )
    discarded = [e for e in events if e.type == EventType.OBSERVATION_DISCARDED]
    assert discarded, "批量 OBSERVATION_DISCARDED 事件应成功写入"
    assert discarded[-1].payload.get("count") == 2


# ---------------------------------------------------------------------------
# 失败路径（F088 followup / Codex review）：audit ensure 失败时副作用应回滚
# ---------------------------------------------------------------------------


def _wrap_task_store_with_failing_create(real_task_store):
    """构造一个 task_store wrapper，让 create_task 抛 IntegrityError 模拟 audit ensure 失败。

    get_task 仍然透传到真实 store（让 helper 走到 create_task 路径而非 hit cache）。
    """
    wrapper = MagicMock()
    wrapper.get_task = AsyncMock(side_effect=real_task_store.get_task)
    wrapper.create_task = AsyncMock(side_effect=RuntimeError("simulated audit task creation failure"))
    return wrapper


@pytest.mark.asyncio
async def test_promote_route_500_when_audit_ensure_fails_rolls_back_claim(
    client: AsyncClient,
    test_app: FastAPI,
    store_group,
    project_root: Path,
):
    """audit ensure 失败时 promote 必须 return 500 + claim 回滚 + USER.md 未写。

    F088 followup（Codex review）核心防御：之前 _emit_event 内部 silent except
    会让 audit 链断裂时 promote 仍照常写 USER.md + 标 promoted。
    现在前置 ensure：失败立即 fail-loud，副作用未发生。
    """
    conn = store_group.conn
    fact_content = "应该不会被写入的事实"
    candidate_id = await _insert_candidate(conn, fact_content=fact_content)
    user_md = project_root / "behavior" / "system" / "USER.md"
    baseline_user_md = user_md.read_text(encoding="utf-8")

    # 替换 task_store 让 ensure 阶段必失败
    real_ts = store_group.task_store
    store_group.task_store = _wrap_task_store_with_failing_create(real_ts)

    try:
        response = await client.post(
            f"/api/memory/candidates/{candidate_id}/promote",
            json={},
        )
        assert response.status_code == 500, (
            f"audit ensure 失败时必须 return 500，实际: {response.status_code}"
        )
        assert "audit task ensure 失败" in response.text or "audit" in response.text.lower()

        # 1. 候选回滚到 pending（claim 不应悬挂在 promoting）
        async with conn.execute(
            "SELECT status FROM observation_candidates WHERE id = ?",
            (candidate_id,),
        ) as cur:
            row = await cur.fetchone()
        assert row["status"] == "pending", (
            f"audit ensure 失败必须回滚 claim 让候选回 pending，实际: {row['status']}"
        )

        # 2. USER.md 未被写入
        assert user_md.read_text(encoding="utf-8") == baseline_user_md, (
            "audit ensure 失败时 USER.md 必须保持不变（防止 fail-soft 副作用泄漏）"
        )

        # 3. audit task 没有真实落库（create_task 被 mock 抛了）
        assert await real_ts.get_task("_memory_candidates_audit") is None, (
            "失败路径下不应存在 audit task"
        )
    finally:
        store_group.task_store = real_ts


@pytest.mark.asyncio
async def test_discard_route_500_when_audit_ensure_fails_keeps_pending(
    client: AsyncClient,
    store_group,
):
    """audit ensure 失败时 discard 必须 return 500 + 候选保持 pending（status 未改 rejected）。"""
    conn = store_group.conn
    candidate_id = await _insert_candidate(conn, fact_content="不应被改 rejected 的候选")

    real_ts = store_group.task_store
    store_group.task_store = _wrap_task_store_with_failing_create(real_ts)

    try:
        response = await client.post(f"/api/memory/candidates/{candidate_id}/discard")
        assert response.status_code == 500, (
            f"audit ensure 失败时 discard 必须 return 500，实际: {response.status_code}"
        )

        async with conn.execute(
            "SELECT status FROM observation_candidates WHERE id = ?",
            (candidate_id,),
        ) as cur:
            row = await cur.fetchone()
        assert row["status"] == "pending", (
            f"audit ensure 失败时 discard 必须保留 pending，实际: {row['status']}"
        )
    finally:
        store_group.task_store = real_ts


@pytest.mark.asyncio
async def test_bulk_discard_route_500_when_audit_ensure_fails_keeps_pending(
    client: AsyncClient,
    store_group,
):
    """audit ensure 失败时 bulk_discard 必须 return 500 + 所有候选保持 pending。"""
    conn = store_group.conn
    cid_a = await _insert_candidate(conn, fact_content="批量保留 pending A")
    cid_b = await _insert_candidate(conn, fact_content="批量保留 pending B")

    real_ts = store_group.task_store
    store_group.task_store = _wrap_task_store_with_failing_create(real_ts)

    try:
        response = await client.put(
            "/api/memory/candidates/bulk_discard",
            json={"candidate_ids": [cid_a, cid_b]},
        )
        assert response.status_code == 500, (
            f"audit ensure 失败时 bulk_discard 必须 return 500，实际: {response.status_code}"
        )

        async with conn.execute(
            "SELECT id, status FROM observation_candidates WHERE id IN (?, ?)",
            (cid_a, cid_b),
        ) as cur:
            rows = await cur.fetchall()
        statuses = {r["id"]: r["status"] for r in rows}
        assert statuses[cid_a] == "pending"
        assert statuses[cid_b] == "pending", (
            f"audit ensure 失败时所有候选必须保持 pending，实际: {statuses}"
        )
    finally:
        store_group.task_store = real_ts
