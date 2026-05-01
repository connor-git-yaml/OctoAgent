"""Memory Candidates REST API 路由（Feature 084 Phase 3 T050-T051）。

GET  /api/memory/candidates              — 返回 pending 状态候选列表（FR-8.1）
POST /api/memory/candidates/{id}/promote — accept / edit+accept，写入 USER.md（FR-8.2）
POST /api/memory/candidates/{id}/discard — reject，状态改 rejected（FR-8.2）
PUT  /api/memory/candidates/bulk_discard — 批量 reject（FR-8.3）
GET  /api/snapshots/{tool_call_id}       — 查询 SnapshotRecord（FR-2.3）

promote 流程：
- ThreatScanner 扫描 fact_content（Constitution C10）
- PolicyGate.check() 统一入口
- 调用 user_profile.update 写入 USER.md
- 写 OBSERVATION_PROMOTED 事件（Constitution C2）

Constitution 合规：
- C2 每次 promote / discard 都写审计事件
- C10 promote 路径统一经 ThreatScanner + PolicyGate
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from ulid import ULID

from octoagent.core.models.enums import ActorType, EventType
from octoagent.core.models.event import Event

from ..deps import get_store_group

log = structlog.get_logger(__name__)

router = APIRouter()

# 审计占位 task_id（防 F24 FK 违反）
_CANDIDATES_AUDIT_TASK_ID = "_memory_candidates_audit"


# ---------------------------------------------------------------------------
# Response/Request schema
# ---------------------------------------------------------------------------


class CandidateItem(BaseModel):
    """单条候选事实（响应 schema）。"""

    id: str
    fact_content: str
    category: str | None
    confidence: float | None
    created_at: str
    expires_at: str
    source_turn_id: str | None
    edited: bool
    status: str


class CandidatesListResponse(BaseModel):
    """GET /api/memory/candidates 响应 schema（FR-8.1）。"""

    candidates: list[CandidateItem]
    total: int
    pending_count: int


class PromoteRequest(BaseModel):
    """POST /api/memory/candidates/{id}/promote 请求 body。"""

    fact_content: str | None = None
    """如果提供，则为 edit+accept；否则直接 accept（使用原始 fact_content）。"""


class BulkDiscardRequest(BaseModel):
    """PUT /api/memory/candidates/bulk_discard 请求 body（FR-8.3）。"""

    candidate_ids: list[str]


# ---------------------------------------------------------------------------
# 辅助：事件写入（防 F22 回归）+ promote 状态回滚（防 F28 回归）
# ---------------------------------------------------------------------------


async def _rollback_promoting_to_pending(conn: Any, candidate_id: str) -> None:
    """F28 修复：promote 中途失败时把 promoting → pending 让候选重新可用。

    使用 best-effort 模式：回滚失败只 log，不向上抛（避免覆盖原始错误）。
    """
    try:
        await conn.execute(
            "UPDATE observation_candidates SET status = 'pending' "
            "WHERE id = ? AND status = 'promoting'",
            (candidate_id,),
        )
        await conn.commit()
    except Exception as exc:  # noqa: BLE001
        log.error(
            "memory_candidates_promote_rollback_failed",
            candidate_id=candidate_id,
            error=str(exc),
            hint="候选可能悬挂在 promoting 状态，需人工修复",
        )


async def _emit_event(
    event_store: Any,
    task_store: Any | None,
    *,
    event_type: EventType,
    payload: dict[str, Any],
    _ensured_set: set[str] | None = None,
) -> None:
    """写审计事件（防 F22：使用真实 schema 字段 + append_event_committed）。"""
    if event_store is None:
        return

    # 确保 audit task 存在（防 F24 FK 违反）
    # F087 final Codex high-3 闭环：Task 模型 requester 是 required（无 default），
    # 原实现缺 RequesterInfo / TaskPointers 导致 Pydantic 验证错误 → 创建失败
    # → 后续 events 写入因 FK (task_id) 拒绝。线上首次 promote 实际丢 audit 事件
    # （Constitution C2「Everything is an Event」违反）。
    if _ensured_set is not None and _CANDIDATES_AUDIT_TASK_ID not in _ensured_set:
        if task_store is not None:
            try:
                existing = await task_store.get_task(_CANDIDATES_AUDIT_TASK_ID)
                if existing is None:
                    from octoagent.core.models.task import (
                        RequesterInfo,
                        Task,
                        TaskPointers,
                    )

                    now = datetime.now(timezone.utc)
                    audit_task = Task(
                        task_id=_CANDIDATES_AUDIT_TASK_ID,
                        created_at=now,
                        updated_at=now,
                        title="Memory Candidates 审计占位 Task（F084 Phase 3）",
                        # 系统占位 task，requester 标系统渠道（Task.requester 必填）
                        requester=RequesterInfo(
                            channel="system",
                            sender_id="memory_candidates_audit",
                        ),
                        pointers=TaskPointers(),  # 默认空指针
                        trace_id=_CANDIDATES_AUDIT_TASK_ID,
                    )
                    await task_store.create_task(audit_task)
                _ensured_set.add(_CANDIDATES_AUDIT_TASK_ID)
            except Exception as exc:
                log.error(
                    "memory_candidates_audit_task_ensure_failed",
                    error=str(exc),
                )

    try:
        task_seq = await event_store.get_next_task_seq(_CANDIDATES_AUDIT_TASK_ID)
        event = Event(
            event_id=str(ULID()),
            task_id=_CANDIDATES_AUDIT_TASK_ID,
            task_seq=task_seq,
            ts=datetime.now(timezone.utc),
            type=event_type,
            actor=ActorType.SYSTEM,
            payload=payload,
            trace_id=_CANDIDATES_AUDIT_TASK_ID,
        )
        await event_store.append_event_committed(event, update_task_pointer=False)
    except Exception as exc:
        log.error(
            "memory_candidates_event_emit_failed",
            event_type=str(event_type),
            error_type=type(exc).__name__,
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# T050：GET /api/memory/candidates
# ---------------------------------------------------------------------------


@router.get("/api/memory/candidates", response_model=CandidatesListResponse)
async def list_memory_candidates(
    request: Request,
    store_group=Depends(get_store_group),
) -> CandidatesListResponse:
    """返回 pending 状态候选列表（T050 / FR-8.1）。"""
    conn = store_group.conn

    try:
        async with conn.execute(
            """
            SELECT id, fact_content, category, confidence, status,
                   created_at, expires_at, source_turn_id, edited
            FROM observation_candidates
            WHERE status = 'pending'
            ORDER BY created_at DESC
            """
        ) as cur:
            rows = await cur.fetchall()

        async with conn.execute(
            "SELECT COUNT(*) AS cnt FROM observation_candidates WHERE status = 'pending'"
        ) as cur:
            row = await cur.fetchone()
            pending_count = int(row["cnt"]) if row else 0

    except Exception as exc:
        log.error(
            "memory_candidates_list_failed",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        raise HTTPException(status_code=500, detail=f"查询候选列表失败: {exc}") from exc

    candidates = [
        CandidateItem(
            id=str(r["id"]),
            fact_content=str(r["fact_content"]),
            category=r["category"],
            confidence=float(r["confidence"]) if r["confidence"] is not None else None,
            created_at=str(r["created_at"]),
            expires_at=str(r["expires_at"]),
            source_turn_id=r["source_turn_id"],
            edited=bool(r["edited"]),
            status=str(r["status"]),
        )
        for r in rows
    ]
    return CandidatesListResponse(
        candidates=candidates,
        total=len(candidates),
        pending_count=pending_count,
    )


# ---------------------------------------------------------------------------
# T051：POST /api/memory/candidates/{id}/promote
# ---------------------------------------------------------------------------


@router.post("/api/memory/candidates/{candidate_id}/promote")
async def promote_candidate(
    candidate_id: str,
    body: PromoteRequest,
    request: Request,
    store_group=Depends(get_store_group),
) -> JSONResponse:
    """accept / edit+accept，经 ThreatScanner + PolicyGate + user_profile.update 写入 USER.md（T051 / FR-8.2）。"""
    conn = store_group.conn
    event_store = getattr(store_group, "event_store", None)
    task_store = getattr(store_group, "task_store", None)
    _ensured: set[str] = set()

    # 查找候选（仅取 fact_content 等只读字段；status 检查通过 atomic claim 完成）
    try:
        async with conn.execute(
            "SELECT * FROM observation_candidates WHERE id = ?",
            (candidate_id,),
        ) as cur:
            row = await cur.fetchone()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"查询候选失败: {exc}") from exc

    if row is None:
        raise HTTPException(status_code=404, detail=f"候选 {candidate_id} 不存在")

    if row["status"] != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"候选 {candidate_id} 状态为 {row['status']}，不可 promote",
        )

    # F28 修复 (Codex high)：atomic claim—在写 USER.md 前用条件 UPDATE 把候选
    # status 从 pending 改为 promoting，rowcount=0 说明并发竞态/二次重试已抢到，
    # 立刻 409 拒绝，避免两个 promote 都看到 pending → 都 append USER.md → 重复记忆。
    try:
        cursor = await conn.execute(
            "UPDATE observation_candidates SET status = 'promoting' "
            "WHERE id = ? AND status = 'pending'",
            (candidate_id,),
        )
        await conn.commit()
        claimed = (cursor.rowcount or 0) > 0
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"原子 claim 失败: {exc}") from exc
    if not claimed:
        raise HTTPException(
            status_code=409,
            detail=f"候选 {candidate_id} 已被并发 promote 抢占（atomic claim 失败）",
        )

    # 决定最终 fact_content（edit+accept 时使用 body.fact_content）
    fact_content = body.fact_content if body.fact_content is not None else str(row["fact_content"])
    edited = body.fact_content is not None and body.fact_content != str(row["fact_content"])

    # ThreatScanner + PolicyGate 统一内容安全扫描（Constitution C10）
    # F43 修复（F085 T7 Codex high）：之前 PolicyGate 拒绝直接 raise，候选永远卡 promoting
    # 状态——后续 promote 永远 409、discard 只允许 pending、UI 无法清理。
    # 现在拒绝/异常路径都先回滚 promoting → pending 让候选可重试 / 清理。
    try:
        from octoagent.gateway.services.policy import PolicyGate

        _gate = PolicyGate(event_store=event_store, task_store=task_store)
        _check = await _gate.check(
            content=fact_content,
            tool_name="memory_candidates.promote",
            task_id="",
            extra_payload={"candidate_id": candidate_id},
        )
        if not _check.allowed:
            await _rollback_promoting_to_pending(conn, candidate_id)
            raise HTTPException(
                status_code=422,
                detail=f"内容安全扫描拒绝：{_check.reason}",
            )
    except HTTPException:
        raise
    except Exception as exc:
        await _rollback_promoting_to_pending(conn, candidate_id)
        log.error(
            "memory_candidates_promote_threat_scan_failed",
            candidate_id=candidate_id,
            error=str(exc),
        )
        raise HTTPException(status_code=500, detail=f"安全扫描失败: {exc}") from exc

    # 调用 user_profile.update(add) 写入 USER.md（FR-8.2 / plan.md 3.4）
    try:
        # 通过 CapabilityPackService 的 ToolDeps 访问 user_profile.update
        cap = getattr(request.app.state, "capability_pack_service", None)
        _tool_deps = getattr(cap, "_tool_deps", None)
        promote_success = False
        promote_error = None

        if _tool_deps is not None:
            # 优先通过 ToolDeps 直接调用 user_profile.update 的底层路径
            snapshot_store = getattr(_tool_deps, "_snapshot_store", None)
            project_root = getattr(_tool_deps, "project_root", None)

            if snapshot_store is not None and project_root is not None:
                from octoagent.gateway.harness.snapshot_store import CharLimitExceeded
                from pathlib import Path

                USER_MD_CHAR_LIMIT = 50_000
                ENTRY_SEPARATOR = "\n\n§ "
                user_md = project_root / "behavior" / "system" / "USER.md"

                try:
                    _new_content, _bytes = await snapshot_store.append_entry(
                        user_md,
                        fact_content,
                        entry_separator=ENTRY_SEPARATOR,
                        first_entry_prefix="§ ",
                        char_limit=USER_MD_CHAR_LIMIT,
                        live_state_key="USER.md",
                    )
                    promote_success = True
                except CharLimitExceeded as exc:
                    promote_error = str(exc)
                except Exception as exc:
                    promote_error = str(exc)
            else:
                promote_error = "snapshot_store 或 project_root 未绑定"
        else:
            promote_error = "capability_pack_service._tool_deps 未就绪"

        if not promote_success:
            # F28 修复：写 USER.md 失败时回滚 status 让候选重新可被 promote
            await _rollback_promoting_to_pending(conn, candidate_id)
            raise HTTPException(
                status_code=500,
                detail=f"写入 USER.md 失败: {promote_error}",
            )

    except HTTPException:
        raise
    except Exception as exc:
        # F28 修复：异常路径也要回滚 status，否则候选悬挂在 promoting
        await _rollback_promoting_to_pending(conn, candidate_id)
        log.error(
            "memory_candidates_promote_write_failed",
            candidate_id=candidate_id,
            error=str(exc),
        )
        raise HTTPException(status_code=500, detail=f"写入 USER.md 失败: {exc}") from exc

    # 更新候选状态为 promoted
    # F28 修复：DB 状态更新失败必须返回 500 + 回滚（不能返回 200 成功）—
    # 否则 USER.md 已写入但 candidate 仍 promoting，悬挂状态会让重试看到 promoting
    # 永远拒绝（409），用户视角是"已写入但永远显示未处理"
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        cursor = await conn.execute(
            "UPDATE observation_candidates SET status = 'promoted', promoted_at = ?, edited = ? "
            "WHERE id = ? AND status = 'promoting'",
            (now_iso, 1 if edited else 0, candidate_id),
        )
        await conn.commit()
        if (cursor.rowcount or 0) == 0:
            log.error(
                "memory_candidates_promote_status_lost_promoting",
                candidate_id=candidate_id,
                hint="status 不再是 promoting（外部并发改了？）— USER.md 可能已写入但候选状态不一致",
            )
            raise HTTPException(
                status_code=500,
                detail=f"候选 {candidate_id} status 异常：写入 USER.md 后无法标记 promoted",
            )
    except HTTPException:
        raise
    except Exception as exc:
        log.error(
            "memory_candidates_promote_status_update_failed",
            candidate_id=candidate_id,
            error=str(exc),
            hint="USER.md 已写入但候选状态未落库为 promoted——需要人工修复",
        )
        # 不能 silent return success（防 F28 假成功）
        raise HTTPException(
            status_code=500,
            detail=(
                f"USER.md 已写入但候选状态更新失败: {exc}; "
                f"请检查 candidate {candidate_id} 状态并手动修复"
            ),
        ) from exc

    # SnapshotRecord 持久化（FR-2.3）+ MEMORY_ENTRY_ADDED 事件（Constitution C2）
    # F1 修复：promote 路径应与 user_profile.update add 路径一致，
    # 写入 SnapshotRecord + MEMORY_ENTRY_ADDED，保证审计完整性
    cap = getattr(request.app.state, "capability_pack_service", None)
    _tool_deps = getattr(cap, "_tool_deps", None)
    snapshot_store = getattr(_tool_deps, "_snapshot_store", None) if _tool_deps else None
    if snapshot_store is not None:
        tool_call_id = str(ULID())
        result_summary = f"promote candidate {candidate_id}: {fact_content[:480]}"
        try:
            await snapshot_store.persist_snapshot_record(
                tool_call_id=tool_call_id,
                result_summary=result_summary,
            )
        except Exception as exc:
            log.warning(
                "memory_candidates_promote_snapshot_record_failed",
                candidate_id=candidate_id,
                error=str(exc),
            )

    await _emit_event(
        event_store,
        task_store,
        event_type=EventType.MEMORY_ENTRY_ADDED,
        payload={
            "tool": "memory_candidates.promote",
            "candidate_id": candidate_id,
            "operation": "add",
            "preview": fact_content[:200],
            "edited": edited,
        },
        _ensured_set=_ensured,
    )

    # 写 OBSERVATION_PROMOTED 事件（Constitution C2）
    await _emit_event(
        event_store,
        task_store,
        event_type=EventType.OBSERVATION_PROMOTED,
        payload={
            "candidate_id": candidate_id,
            "fact_content_preview": fact_content[:200],
            "edited": edited,
        },
        _ensured_set=_ensured,
    )

    # F45 修复（F085 T7 Codex medium）：promote 路径之前直接 append_entry 写 USER.md
    # 但跳过了 user_profile.update 触发的 sync_owner_profile_from_user_md +
    # apply_user_md_sync_to_owner_profile（F42 修复）→ 用户在 Web UI accept
    # 候选 → USER.md 更新但 owner_profiles 表的 timezone/locale 不变 →
    # 系统 prompt 注入用 owner_profile.timezone 仍是默认值 →
    # 用户感知"已接受但 LLM 不记偏好"（与 F42 修复方向一致的状态漂移）。
    # 修复：promote 成功后异步触发同 sync 路径让 OwnerProfile 派生视图刷新。
    try:
        from octoagent.core.models.agent_context import (
            apply_user_md_sync_to_owner_profile,
            sync_owner_profile_from_user_md,
        )
        cap = getattr(request.app.state, "capability_pack_service", None)
        _tool_deps = getattr(cap, "_tool_deps", None)
        _project_root = getattr(_tool_deps, "project_root", None) if _tool_deps else None
        if _project_root is not None:
            user_md = _project_root / "behavior" / "system" / "USER.md"

            async def _sync_after_promote() -> None:
                try:
                    fields = await sync_owner_profile_from_user_md(user_md)
                    await apply_user_md_sync_to_owner_profile(store_group, fields)
                except Exception as _exc:  # noqa: BLE001
                    log.warning(
                        "memory_candidate_promote_sync_failed",
                        candidate_id=candidate_id,
                        error=str(_exc),
                    )

            import asyncio
            asyncio.create_task(_sync_after_promote())
    except Exception as _exc:  # noqa: BLE001
        # sync 触发失败不阻断 promote return（OBSERVATION_PROMOTED 已写入）
        log.warning(
            "memory_candidate_promote_sync_trigger_failed",
            candidate_id=candidate_id,
            error=str(_exc),
        )

    log.info(
        "memory_candidate_promoted",
        candidate_id=candidate_id,
        edited=edited,
    )
    return JSONResponse(
        content={
            "status": "promoted",
            "candidate_id": candidate_id,
            "edited": edited,
        }
    )


# ---------------------------------------------------------------------------
# T051：POST /api/memory/candidates/{id}/discard
# ---------------------------------------------------------------------------


@router.post("/api/memory/candidates/{candidate_id}/discard")
async def discard_candidate(
    candidate_id: str,
    request: Request,
    store_group=Depends(get_store_group),
) -> JSONResponse:
    """reject 候选，状态改 rejected + 写 OBSERVATION_DISCARDED 事件（T051 / FR-8.2）。"""
    conn = store_group.conn
    event_store = getattr(store_group, "event_store", None)
    task_store = getattr(store_group, "task_store", None)
    _ensured: set[str] = set()

    try:
        async with conn.execute(
            "SELECT id, status FROM observation_candidates WHERE id = ?",
            (candidate_id,),
        ) as cur:
            row = await cur.fetchone()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"查询候选失败: {exc}") from exc

    if row is None:
        raise HTTPException(status_code=404, detail=f"候选 {candidate_id} 不存在")

    if row["status"] not in ("pending",):
        raise HTTPException(
            status_code=409,
            detail=f"候选 {candidate_id} 状态为 {row['status']}，不可 discard",
        )

    try:
        await conn.execute(
            "UPDATE observation_candidates SET status = 'rejected' WHERE id = ?",
            (candidate_id,),
        )
        await conn.commit()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"更新候选状态失败: {exc}") from exc

    # 写 OBSERVATION_DISCARDED 事件（Constitution C2）
    await _emit_event(
        event_store,
        task_store,
        event_type=EventType.OBSERVATION_DISCARDED,
        payload={
            "candidate_id": candidate_id,
            "reason": "user_rejected",
        },
        _ensured_set=_ensured,
    )

    log.info("memory_candidate_discarded", candidate_id=candidate_id)
    return JSONResponse(
        content={"status": "discarded", "candidate_id": candidate_id}
    )


# ---------------------------------------------------------------------------
# T051：PUT /api/memory/candidates/bulk_discard
# ---------------------------------------------------------------------------


@router.put("/api/memory/candidates/bulk_discard")
async def bulk_discard_candidates(
    body: BulkDiscardRequest,
    request: Request,
    store_group=Depends(get_store_group),
) -> JSONResponse:
    """批量 reject（T051 / FR-8.3）。

    request body：{"candidate_ids": ["id1", "id2", ...]}
    """
    if not body.candidate_ids:
        return JSONResponse(content={"status": "ok", "discarded_count": 0})

    conn = store_group.conn
    event_store = getattr(store_group, "event_store", None)
    task_store = getattr(store_group, "task_store", None)
    _ensured: set[str] = set()

    # F29 修复 (Codex medium)：先 SELECT 真实 pending IDs，再 UPDATE，
    # 然后用 cursor.rowcount 校验。响应只回传实际被 reject 的候选；
    # 无法处理的（已 promoted/已 rejected/不存在）以 skipped_ids 返回，
    # 让调用方知道哪些 ID 没生效。
    try:
        placeholders = ",".join("?" for _ in body.candidate_ids)
        async with conn.execute(
            f"SELECT id FROM observation_candidates "
            f"WHERE id IN ({placeholders}) AND status = 'pending'",
            body.candidate_ids,
        ) as cur:
            actual_pending_rows = await cur.fetchall()
        actual_pending_ids = [r["id"] for r in actual_pending_rows]
        skipped_ids = [
            cid for cid in body.candidate_ids if cid not in set(actual_pending_ids)
        ]

        if actual_pending_ids:
            update_placeholders = ",".join("?" for _ in actual_pending_ids)
            cursor = await conn.execute(
                f"""
                UPDATE observation_candidates
                SET status = 'rejected'
                WHERE id IN ({update_placeholders}) AND status = 'pending'
                """,
                actual_pending_ids,
            )
            await conn.commit()
            actual_discarded = cursor.rowcount or 0
        else:
            actual_discarded = 0
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"批量 discard 失败: {exc}") from exc

    # 写批量 OBSERVATION_DISCARDED 事件（用真实计数，不用请求长度）
    if actual_discarded > 0:
        await _emit_event(
            event_store,
            task_store,
            event_type=EventType.OBSERVATION_DISCARDED,
            payload={
                "candidate_ids": actual_pending_ids,
                "reason": "user_bulk_rejected",
                "count": actual_discarded,
            },
            _ensured_set=_ensured,
        )

    log.info(
        "memory_candidates_bulk_discarded",
        requested=len(body.candidate_ids),
        discarded=actual_discarded,
        skipped=len(skipped_ids),
    )
    return JSONResponse(
        content={
            "status": "ok",
            "discarded_count": actual_discarded,
            "skipped_ids": skipped_ids,  # F29: 调用方可见哪些 ID 没生效
        }
    )


# ---------------------------------------------------------------------------
# T051：GET /api/snapshots/{tool_call_id}
# ---------------------------------------------------------------------------


@router.get("/api/snapshots/{tool_call_id}")
async def get_snapshot(
    tool_call_id: str,
    request: Request,
    store_group=Depends(get_store_group),
) -> JSONResponse:
    """查询 SnapshotRecord（T051 / FR-2.3）。

    404 when tool_call_id not found.
    """
    conn = store_group.conn

    try:
        async with conn.execute(
            """
            SELECT id, tool_call_id, result_summary, timestamp, ttl_days, expires_at, created_at
            FROM snapshot_records
            WHERE tool_call_id = ?
            """,
            (tool_call_id,),
        ) as cur:
            row = await cur.fetchone()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"查询 snapshot 失败: {exc}") from exc

    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"SnapshotRecord tool_call_id={tool_call_id!r} 不存在",
        )

    return JSONResponse(
        content={
            "id": str(row["id"]),
            "tool_call_id": str(row["tool_call_id"]),
            "result_summary": str(row["result_summary"]),
            "timestamp": str(row["timestamp"]),
            "ttl_days": int(row["ttl_days"]),
            "expires_at": str(row["expires_at"]),
            "created_at": str(row["created_at"]) if row["created_at"] else None,
        }
    )
