"""Observation Routine 压测 — 10 次运行完整性（T074）。

Feature 084 Phase 5 验收：
- 10 次 routine 运行 + 不同对话记录
- 事件写入完整性 100%（OBSERVATION_STAGE_COMPLETED 3 个 stage 都写入）
- candidates 总数 ≤ 50（队列上限保护生效）
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest
import pytest_asyncio

from octoagent.core.models.enums import EventType
from octoagent.core.models.task import RequesterInfo, Task, TaskPointers
from octoagent.core.store import create_store_group
from octoagent.core.store.sqlite_init import init_db
from octoagent.gateway.routines.observation_promoter import (
    CANDIDATES_QUEUE_MAX,
    CATEGORIZE_LOW_CONFIDENCE,
    CONFIDENCE_THRESHOLD,
    ObservationRoutine,
    CandidateDraft,
)
from ulid import ULID


# ---------------------------------------------------------------------------
# 辅助函数和 Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def store_group(tmp_path: Path):
    """创建完整 schema 的 StoreGroup。"""
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    sg = await create_store_group(
        db_path=str(tmp_path / "stress_test.db"),
        artifacts_dir=str(artifacts_dir),
    )
    yield sg
    await sg.conn.close()


async def _ensure_audit_task(sg, task_id: str) -> None:
    """确保审计 task 存在（外键约束）。"""
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
        title=f"压测审计占位 task ({task_id})",
        trace_id=task_id,
        requester=RequesterInfo(channel="system", sender_id="system"),
        pointers=TaskPointers(),
    )
    await sg.task_store.create_task(task)


def _make_unique_turn_content(run_idx: int) -> list[dict]:
    """为第 run_idx 次运行生成独特的 turn 内容（避免去重导致候选计数失真）。"""
    # 每次运行用不同的 run_idx 前缀，确保 hash 不同
    return [
        {"payload": {"content": f"run_{run_idx}_fact_A: 用户喜欢早起运动，每天 {6 + run_idx} 点起床"}},
        {"payload": {"content": f"run_{run_idx}_fact_B: 工作专注 AI 工具，已有 {5 + run_idx} 年经验"}},
    ]


async def _insert_unique_turn_events(conn: Any, turns: list[dict], run_idx: int) -> None:
    """写入 run_idx 唯一标识的 turn 事件（避免 event_id 冲突）。

    注意：turn 事件必须写入独立的 task（非 _observation_routine_audit），
    否则 get_events_for_task("_observation_routine_audit") 会返回非标准事件类型
    导致 EventType 枚举验证失败。
    """
    now = datetime.now(timezone.utc)
    # 使用独立的 turns task，与 audit task 分开
    task_id = f"_stress_turns_task_{run_idx}"
    try:
        await conn.execute(
            """
            INSERT OR IGNORE INTO tasks (task_id, created_at, updated_at, title, status, trace_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (task_id, now.isoformat(), now.isoformat(), f"stress turns task {run_idx}", "running", task_id),
        )
    except Exception:
        pass

    for i, turn in enumerate(turns):
        payload_str = json.dumps(turn.get("payload", {}))
        event_id = str(ULID())
        await conn.execute(
            """
            INSERT OR IGNORE INTO events (event_id, task_id, task_seq, ts, type, actor, payload, trace_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                task_id,
                run_idx * 1000 + i + 1,
                now.isoformat(),
                "TASK_USER_MESSAGE",
                "user",
                payload_str,
                task_id,
            ),
        )
    await conn.commit()


def _make_mock_router_with_confidence(confidence: float) -> Any:
    """构建 mock ProviderRouter，返回指定置信度。"""
    router = MagicMock()
    resolved = MagicMock()
    resolved.client = MagicMock()
    resolved.model_name = "mock-stress-model"
    content = json.dumps({"category": "preference", "confidence": confidence})
    resolved.client.call = AsyncMock(return_value=(content, [], {}))
    router.resolve_for_alias = MagicMock(return_value=resolved)
    return router


# ---------------------------------------------------------------------------
# T074 核心：10 次运行完整性压测
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_10x_runs_stage_events_complete(store_group) -> None:
    """10 次 routine 运行，每次的 3 个 stage 事件（extract/dedupe/categorize）全部写入。

    验收：
    - 10 次 _run_once()，每次写入 3 个 OBSERVATION_STAGE_COMPLETED 事件
    - 每次运行后验证 stage 事件总数递增（事件写入完整性 100%）
    - 所有 stage 事件含必填字段：stage_name / input_count / output_count / duration_ms
    """
    conn = store_group.conn
    await _ensure_audit_task(store_group, "_observation_routine_audit")

    router = _make_mock_router_with_confidence(0.90)
    routine = ObservationRoutine(
        conn=conn,
        event_store=store_group.event_store,
        task_store=store_group.task_store,
        provider_router=router,
        feature_enabled=True,
    )

    total_stage_events_expected = 0
    for run_idx in range(10):
        # 每次注入不同的 turn 内容
        turns = _make_unique_turn_content(run_idx)
        await _insert_unique_turn_events(conn, turns, run_idx)

        await routine._run_once()
        total_stage_events_expected += 3  # 每次运行 3 个 stage

        # 验证这次运行的 stage 事件已写入
        events = await store_group.event_store.get_events_for_task("_observation_routine_audit")
        stage_events = [e for e in events if e.type == EventType.OBSERVATION_STAGE_COMPLETED]

        # 本次运行后应有至少 total_stage_events_expected 个 stage 事件
        assert len(stage_events) >= total_stage_events_expected, (
            f"第 {run_idx + 1} 次运行后应有至少 {total_stage_events_expected} 个 stage 事件，"
            f"实际: {len(stage_events)}"
        )

    # 最终验证：10 次运行后共有 ≥ 30 个 stage 事件
    final_events = await store_group.event_store.get_events_for_task("_observation_routine_audit")
    final_stage_events = [e for e in final_events if e.type == EventType.OBSERVATION_STAGE_COMPLETED]

    assert len(final_stage_events) >= 30, (
        f"10 次运行后应有至少 30 个 stage 事件，实际: {len(final_stage_events)}"
    )

    # 验证所有 stage 事件字段完整性
    for event in final_stage_events:
        payload = event.payload
        assert "stage_name" in payload, f"stage 事件缺少 stage_name: {payload}"
        assert "input_count" in payload, f"stage 事件缺少 input_count: {payload}"
        assert "output_count" in payload, f"stage 事件缺少 output_count: {payload}"
        assert "duration_ms" in payload, f"stage 事件缺少 duration_ms: {payload}"
        assert payload["stage_name"] in {"extract", "dedupe", "categorize"}, (
            f"stage_name 应为 extract/dedupe/categorize，实际: {payload['stage_name']}"
        )
        assert isinstance(payload["input_count"], int), "input_count 应为整数"
        assert isinstance(payload["output_count"], int), "output_count 应为整数"
        assert isinstance(payload["duration_ms"], int), "duration_ms 应为整数"


@pytest.mark.asyncio
async def test_10x_runs_candidates_queue_bounded(store_group) -> None:
    """10 次运行后 candidates 总数 ≤ 50（队列上限保护生效）。

    验收（FR-7.4）：
    - candidates 总数永不超过 CANDIDATES_QUEUE_MAX=50
    - 即使每次注入高置信度内容，超限后停止写入
    """
    conn = store_group.conn
    await _ensure_audit_task(store_group, "_observation_routine_audit")

    # 清空现有 candidates（测试隔离）
    await conn.execute("DELETE FROM observation_candidates")
    await conn.commit()

    # F40 修复（Codex independent review medium）：subagent 原版只插 2 turns × 10 runs ≈ 20 个，
    # 永远到不了 CANDIDATES_QUEUE_MAX=50，assertion 即使 cap 完全失效也会通过。
    # 先 seed 49 个 pending candidates，让 routine 后续每次写入都接近 / 越过上限，
    # 真正验证 FR-7.4 队列上限保护是否生效。
    from datetime import datetime, timedelta, timezone as _tz
    from ulid import ULID
    seed_count = CANDIDATES_QUEUE_MAX - 1  # 49
    now = datetime.now(_tz.utc)
    expires = now + timedelta(days=30)
    for i in range(seed_count):
        await conn.execute(
            """
            INSERT INTO observation_candidates (
                id, fact_content, fact_content_hash, category, confidence, status,
                source_turn_id, edited, created_at, expires_at, user_id
            ) VALUES (?, ?, ?, ?, ?, 'pending', ?, 0, ?, ?, 'owner')
            """,
            (
                str(ULID()),
                f"seeded fact {i}",
                f"seed_hash_{i:03d}",
                "seed_category",
                0.85,
                f"seed_turn_{i:03d}",
                now.isoformat(),
                expires.isoformat(),
            ),
        )
    await conn.commit()
    # 验证 seed 成功
    async with conn.execute(
        "SELECT COUNT(*) AS cnt FROM observation_candidates WHERE status = 'pending'"
    ) as cur:
        seed_row = await cur.fetchone()
    assert int(seed_row["cnt"]) == seed_count, f"seed 应为 {seed_count}，实际 {seed_row['cnt']}"

    router = _make_mock_router_with_confidence(0.95)  # 高置信度，确保会写入
    routine = ObservationRoutine(
        conn=conn,
        event_store=store_group.event_store,
        task_store=store_group.task_store,
        provider_router=router,
        feature_enabled=True,
    )

    for run_idx in range(10):
        turns = _make_unique_turn_content(run_idx)
        await _insert_unique_turn_events(conn, turns, run_idx + 100)  # 用 100+ 避免序号冲突
        await routine._run_once()

        # 每次运行后验证队列未超限
        async with conn.execute(
            "SELECT COUNT(*) AS cnt FROM observation_candidates WHERE status = 'pending'"
        ) as cur:
            row = await cur.fetchone()
        current_count = int(row["cnt"])

        assert current_count <= CANDIDATES_QUEUE_MAX, (
            f"第 {run_idx + 1} 次运行后 candidates 数量 {current_count} 超过上限 {CANDIDATES_QUEUE_MAX}"
        )

    # 最终验证
    async with conn.execute(
        "SELECT COUNT(*) AS cnt FROM observation_candidates WHERE status = 'pending'"
    ) as cur:
        final_row = await cur.fetchone()
    final_count = int(final_row["cnt"])

    assert final_count <= CANDIDATES_QUEUE_MAX, (
        f"10 次运行后 candidates 总数 {final_count} 超过队列上限 {CANDIDATES_QUEUE_MAX}，"
        f"队列上限保护未生效（FR-7.4）"
    )


@pytest.mark.asyncio
async def test_10x_runs_no_crashes(store_group) -> None:
    """10 次运行中无论任何情况都不崩溃（Constitution C6 降级保证）。

    验收：
    - 5 次高置信度 + 5 次模型不可用（降级），routine 始终不中断
    """
    conn = store_group.conn
    await _ensure_audit_task(store_group, "_observation_routine_audit")

    errors: list[tuple[int, Exception]] = []

    for run_idx in range(10):
        turns = _make_unique_turn_content(run_idx + 200)
        await _insert_unique_turn_events(conn, turns, run_idx + 200)

        # 奇数次：高置信度 router；偶数次：None（模型不可用，降级路径）
        if run_idx % 2 == 0:
            router = _make_mock_router_with_confidence(0.90)
        else:
            router = None  # 模拟 utility model 不可用

        routine = ObservationRoutine(
            conn=conn,
            event_store=store_group.event_store,
            task_store=store_group.task_store,
            provider_router=router,
            feature_enabled=True,
        )

        try:
            await routine._run_once()
        except Exception as exc:
            errors.append((run_idx, exc))

    assert not errors, (
        f"10 次运行中 {len(errors)} 次崩溃（Constitution C6 违反）：\n"
        + "\n".join(f"  第 {i+1} 次: {type(e).__name__}: {e}" for i, e in errors[:5])
    )


@pytest.mark.asyncio
async def test_10x_runs_stage_names_covered(store_group) -> None:
    """10 次运行后所有 3 个 stage（extract/dedupe/categorize）均有事件记录。"""
    conn = store_group.conn
    await _ensure_audit_task(store_group, "_observation_routine_audit")

    router = _make_mock_router_with_confidence(0.85)
    routine = ObservationRoutine(
        conn=conn,
        event_store=store_group.event_store,
        task_store=store_group.task_store,
        provider_router=router,
        feature_enabled=True,
    )

    for run_idx in range(10):
        turns = _make_unique_turn_content(run_idx + 300)
        await _insert_unique_turn_events(conn, turns, run_idx + 300)
        await routine._run_once()

    # 验证所有 3 个 stage 都有事件记录
    events = await store_group.event_store.get_events_for_task("_observation_routine_audit")
    stage_events = [e for e in events if e.type == EventType.OBSERVATION_STAGE_COMPLETED]
    stage_names = {e.payload.get("stage_name") for e in stage_events}

    expected = {"extract", "dedupe", "categorize"}
    assert expected.issubset(stage_names), (
        f"10 次运行后缺少 stage 事件：期望包含 {expected}，实际: {stage_names}"
    )
