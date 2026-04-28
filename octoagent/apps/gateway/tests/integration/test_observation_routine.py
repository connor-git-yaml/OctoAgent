"""集成测试：ObservationRoutine → candidates 写入（T060）。

Feature 084 Phase 3 — 验收提取去重、低置信度丢弃、utility model 降级、stage 事件写入。

测试策略：
- 使用真实 SQLite store
- 使用真实 ObservationRoutine（直接调用 _run_once 跳过 30min 等待）
- mock：ProviderRouter（控制 utility model 返回 confidence）
- 不 mock：SQLite DB 操作、事件写入
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
import aiosqlite

from octoagent.core.models.enums import EventType
from octoagent.core.models.task import RequesterInfo, Task, TaskPointers
from octoagent.core.store import create_store_group
from octoagent.core.store.sqlite_init import init_db
from octoagent.gateway.routines.observation_promoter import (
    CONFIDENCE_THRESHOLD,
    CATEGORIZE_LOW_CONFIDENCE,
    ObservationRoutine,
    CandidateDraft,
)
from ulid import ULID


# ---------------------------------------------------------------------------
# 辅助 fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_conn(tmp_path: Path):
    """创建内含完整 schema 的 aiosqlite 连接。"""
    db_path = str(tmp_path / "test.db")
    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
    await init_db(conn)
    yield conn
    await conn.close()


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
        title=f"审计占位 task ({task_id})",
        trace_id=task_id,
        requester=RequesterInfo(channel="system", sender_id="system"),
        pointers=TaskPointers(),
    )
    await sg.task_store.create_task(task)


async def _insert_turn_events(conn: Any, turns: list[dict]) -> None:
    """向 events 表写入模拟 turn 事件（ObservationRoutine._fetch_recent_turns 使用的数据源）。"""
    # 确保有 task 表记录（events FK 到 tasks）
    now = datetime.now(timezone.utc)
    try:
        await conn.execute(
            """
            INSERT OR IGNORE INTO tasks (task_id, created_at, updated_at, title, status, trace_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("_routine_turns_task", now.isoformat(), now.isoformat(),
             "routine turns test task", "running", "_routine_turns_task"),
        )
    except Exception:
        pass

    for i, turn in enumerate(turns):
        payload_str = json.dumps(turn.get("payload", {}))
        await conn.execute(
            """
            INSERT OR IGNORE INTO events (event_id, task_id, task_seq, ts, type, actor, payload, trace_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(ULID()),
                "_routine_turns_task",
                i + 1,
                now.isoformat(),
                "TASK_USER_MESSAGE",
                "USER",
                payload_str,
                "_routine_turns_task",
            ),
        )
    await conn.commit()


def _make_mock_provider_router(confidence: float = 0.85) -> Any:
    """构建 mock ProviderRouter，返回指定 confidence 的分类结果。"""
    router = MagicMock()
    resolved = MagicMock()
    resolved.client = MagicMock()
    resolved.model_name = "mock-cheap-model"

    # call() 返回 (content, tool_calls, meta)
    content = json.dumps({"category": "preference", "confidence": confidence})
    resolved.client.call = AsyncMock(return_value=(content, [], {}))
    router.resolve_for_alias = MagicMock(return_value=resolved)

    return router


def _make_routine(
    conn: Any,
    sg: Any,
    provider_router: Any | None = None,
    feature_enabled: bool = True,
) -> ObservationRoutine:
    """构建 ObservationRoutine（注入依赖）。"""
    return ObservationRoutine(
        conn=conn,
        event_store=sg.event_store,
        task_store=sg.task_store,
        provider_router=provider_router,
        feature_enabled=feature_enabled,
    )


# ---------------------------------------------------------------------------
# T060-1：test_routine_extracts_and_dedupes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_routine_extracts_and_dedupes(store_group) -> None:
    """含新事实的对话触发 routine，candidates 表有候选，重复内容被去重。

    验收：
    - 写入 2 条不同 turn 事件
    - 运行 routine，observation_candidates 表有对应候选
    - 重复写入相同内容时去重（第二次运行不重复写入）
    - confidence 字段正确（由 mock provider router 返回）
    """
    conn = store_group.conn
    await _ensure_audit_task(store_group, "_observation_routine_audit")

    # 准备 2 条不同 turn 事件
    turns = [
        {"payload": {"content": "用户喜欢早起运动，每天 6 点起床跑步"}},
        {"payload": {"content": "工作方向：专注 AI 工具产品开发，有 5 年经验"}},
    ]
    await _insert_turn_events(conn, turns)

    provider_router = _make_mock_provider_router(confidence=0.85)
    routine = _make_routine(conn, store_group, provider_router=provider_router)

    # 直接运行一次 pipeline（跳过 30min 等待）
    await routine._run_once()

    # 验证 candidates 表有写入
    async with conn.execute(
        "SELECT id, fact_content, category, confidence, status FROM observation_candidates WHERE status = 'pending'"
    ) as cur:
        rows = await cur.fetchall()

    assert len(rows) >= 1, (
        f"运行 routine 后应有至少 1 条候选写入，实际: {len(rows)} 条"
    )

    # 验证 confidence 字段正确（≥ CONFIDENCE_THRESHOLD）
    for row in rows:
        assert float(row["confidence"]) >= CONFIDENCE_THRESHOLD, \
            f"候选 confidence {row['confidence']} 应 >= {CONFIDENCE_THRESHOLD}"
        assert row["status"] == "pending"

    # 第二次运行：相同 turn 内容应被去重，不增加新候选
    count_before = len(rows)
    await routine._run_once()

    async with conn.execute(
        "SELECT COUNT(*) AS cnt FROM observation_candidates WHERE status = 'pending'"
    ) as cur:
        row_after = await cur.fetchone()
    count_after = int(row_after["cnt"])

    assert count_after == count_before, (
        f"第二次运行后候选数量应不增加（去重），实际: before={count_before}, after={count_after}"
    )


# ---------------------------------------------------------------------------
# T060-2：test_routine_low_confidence_discarded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_routine_low_confidence_discarded(store_group) -> None:
    """confidence < 0.7 的候选不写入 DB（仲裁 2 / spec 不变量）。

    验收：
    - mock provider router 返回 confidence=0.3（< CONFIDENCE_THRESHOLD=0.7）
    - 运行 routine 后 candidates 表无新记录
    """
    conn = store_group.conn
    await _ensure_audit_task(store_group, "_observation_routine_audit")

    # 准备 turn 事件
    turns = [
        {"payload": {"content": "某些模糊的内容描述，分类置信度很低"}},
    ]
    await _insert_turn_events(conn, turns)

    # mock 返回低置信度
    provider_router = _make_mock_provider_router(confidence=0.3)
    routine = _make_routine(conn, store_group, provider_router=provider_router)

    # 运行前清空 candidates 表（确保测试隔离）
    await conn.execute("DELETE FROM observation_candidates")
    await conn.commit()

    await routine._run_once()

    # 低置信度应被丢弃，不写入
    async with conn.execute(
        "SELECT COUNT(*) AS cnt FROM observation_candidates WHERE status = 'pending'"
    ) as cur:
        row = await cur.fetchone()

    count = int(row["cnt"])
    assert count == 0, (
        f"confidence=0.3 < CONFIDENCE_THRESHOLD={CONFIDENCE_THRESHOLD}，候选不应写入，实际: {count} 条"
    )


# ---------------------------------------------------------------------------
# T060-3：test_routine_utility_model_unavailable_degrades
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_routine_utility_model_unavailable_degrades(store_group) -> None:
    """utility model 不可用时候选以低置信度入队，routine 不中断（Constitution C6）。

    验收：
    - provider_router=None（模拟不可用）
    - 运行 routine 不抛异常（不中断）
    - 候选以 CATEGORIZE_LOW_CONFIDENCE（0.4）< CONFIDENCE_THRESHOLD（0.7）处理
    - 因为 confidence=0.4 < 0.7，候选不入库（符合仲裁 2）
    - routine 能继续正常运行（不 crash）
    """
    conn = store_group.conn
    await _ensure_audit_task(store_group, "_observation_routine_audit")

    # 准备 turn 事件（有意义内容）
    turns = [
        {"payload": {"content": "用户喜欢深夜写代码，效率最高"}},
    ]
    await _insert_turn_events(conn, turns)

    # provider_router = None 模拟不可用
    routine = _make_routine(conn, store_group, provider_router=None)

    # 清空 candidates 表
    await conn.execute("DELETE FROM observation_candidates")
    await conn.commit()

    # 运行 routine 不应抛异常（Constitution C6）
    try:
        await routine._run_once()
    except Exception as exc:
        pytest.fail(f"utility model 不可用时 routine 不应抛异常（Constitution C6），实际: {exc!r}")

    # 降级 confidence=0.4 < 0.7，不写入（仲裁 2）
    async with conn.execute(
        "SELECT COUNT(*) AS cnt FROM observation_candidates WHERE status = 'pending'"
    ) as cur:
        row = await cur.fetchone()

    count = int(row["cnt"])
    # 降级路径：low_confidence_fallback=True，confidence=0.4 < 0.7 → 不入库
    assert count == 0, (
        f"降级置信度 {CATEGORIZE_LOW_CONFIDENCE} < {CONFIDENCE_THRESHOLD}，"
        f"候选不应写入，实际: {count} 条"
    )


@pytest.mark.asyncio
async def test_routine_utility_model_error_degrades(store_group) -> None:
    """utility model 调用抛异常时降级，routine 继续运行（Constitution C6）。"""
    conn = store_group.conn
    await _ensure_audit_task(store_group, "_observation_routine_audit")

    turns = [{"payload": {"content": "某条用户事实信息，utility model 会报错"}}]
    await _insert_turn_events(conn, turns)

    # mock provider 抛异常
    failing_router = MagicMock()
    resolved = MagicMock()
    resolved.client = MagicMock()
    resolved.model_name = "failing-model"
    resolved.client.call = AsyncMock(side_effect=RuntimeError("utility model 网络错误"))
    failing_router.resolve_for_alias = MagicMock(return_value=resolved)

    routine = _make_routine(conn, store_group, provider_router=failing_router)

    # 清空 candidates
    await conn.execute("DELETE FROM observation_candidates")
    await conn.commit()

    # 不应抛异常
    try:
        await routine._run_once()
    except Exception as exc:
        pytest.fail(f"utility model 抛异常时 routine 不应崩溃，实际: {exc!r}")


# ---------------------------------------------------------------------------
# T060-4：test_routine_stage_events_written
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_routine_stage_events_written(store_group) -> None:
    """每个 stage 完成时 OBSERVATION_STAGE_COMPLETED 事件正确写入（FR-6.3 / Constitution C2）。

    验收：
    - 运行 _run_once
    - events 表中有 OBSERVATION_STAGE_COMPLETED 事件
    - payload 含 stage_name / input_count / output_count / duration_ms
    - 覆盖 extract / dedupe / categorize 三个 stage
    """
    conn = store_group.conn
    await _ensure_audit_task(store_group, "_observation_routine_audit")

    # 准备 turn 事件
    turns = [
        {"payload": {"content": "用户有早起习惯，每天 6 点起床"}},
        {"payload": {"content": "工作领域：AI 基础设施和平台工程"}},
    ]
    await _insert_turn_events(conn, turns)

    provider_router = _make_mock_provider_router(confidence=0.85)
    routine = _make_routine(conn, store_group, provider_router=provider_router)

    await routine._run_once()

    # 验证 OBSERVATION_STAGE_COMPLETED 事件写入
    events = await store_group.event_store.get_events_for_task("_observation_routine_audit")
    stage_events = [e for e in events if e.type == EventType.OBSERVATION_STAGE_COMPLETED]

    assert len(stage_events) >= 3, \
        f"应有至少 3 个 stage 事件（extract/dedupe/categorize），实际: {len(stage_events)}"

    stage_names = {e.payload.get("stage_name") for e in stage_events}
    expected_stages = {"extract", "dedupe", "categorize"}
    assert expected_stages.issubset(stage_names), \
        f"缺少 stage 事件，已有: {stage_names}，期望包含: {expected_stages}"

    # 验证 payload 字段完整性
    for event in stage_events:
        payload = event.payload
        assert "stage_name" in payload, f"stage 事件缺少 stage_name 字段: {payload}"
        assert "input_count" in payload, f"stage 事件缺少 input_count 字段: {payload}"
        assert "output_count" in payload, f"stage 事件缺少 output_count 字段: {payload}"
        assert "duration_ms" in payload, f"stage 事件缺少 duration_ms 字段: {payload}"
        assert isinstance(payload["input_count"], int), "input_count 应为整数"
        assert isinstance(payload["output_count"], int), "output_count 应为整数"
        assert isinstance(payload["duration_ms"], int), "duration_ms 应为整数"


@pytest.mark.asyncio
async def test_routine_not_started_when_disabled(store_group) -> None:
    """feature_enabled=False 时 start() 不创建 asyncio.Task（FR-6.4）。"""
    conn = store_group.conn
    routine = _make_routine(conn, store_group, feature_enabled=False)

    assert routine._task is None, "初始状态 _task 应为 None"
    await routine.start()
    assert routine._task is None, "feature_enabled=False 时 start() 不应创建 Task"


@pytest.mark.asyncio
async def test_routine_start_stop_lifecycle(store_group) -> None:
    """start() 创建 asyncio.Task，stop() 取消并清理（Constitution C7）。"""
    conn = store_group.conn
    routine = _make_routine(conn, store_group, feature_enabled=True)
    # 使用极短 interval 避免等待
    routine.INTERVAL_SECONDS = 3600  # 设为大值，让 loop 等待 sleep

    await routine.start()
    assert routine._task is not None, "start() 后应有 asyncio.Task"
    assert not routine._task.done(), "Task 应正在运行"

    await routine.stop()
    assert routine._task is None or routine._task.done(), \
        "stop() 后 Task 应已停止"
