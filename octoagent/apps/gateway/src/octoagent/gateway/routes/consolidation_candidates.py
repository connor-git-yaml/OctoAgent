"""F127 Sleep-Time Memory Consolidation — 巩固合并候选人审 REST（Phase D，C7 用户面）。

GET  /api/consolidation/candidates              — 列出 pending 合并候选（"建议把这几条记忆
                                                   合并为一条"给用户审查）
POST /api/consolidation/candidates/{id}/accept  — 接受 → commit MERGE（源 SUPERSEDED）
POST /api/consolidation/candidates/{id}/reject  — 拒绝 → 标 REJECTED（不碰 SOR）
PUT  /api/consolidation/candidates/bulk_reject  — 批量拒绝

★ C7 用户面：这是用户**主动查看 + 决策**破坏性 MERGE 的唯一入口（H1：不是 Agent 在对话里
逼问，是用户经 Web 红点/通知引导主动审查）。实际 commit MERGE 的 C4 红线逻辑全在
``ConsolidationApprovalService``（accept 唯一 commit 入口）——本路由是薄 HTTP 壳。

继承宪法（与 memory_candidates 路由同范式）：
- C2：每次 accept/reject emit MEMORY_CONSOLIDATION_APPROVED/REJECTED（approval 服务内）。
- C4：commit MERGE 走 approval.accept（atomic claim 防双 commit）。
- C7：用户对每条提议 accept/reject。
- FK 安全：副作用前 ensure consolidation root task（巩固未跑过时也能审批历史候选）。
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..deps import get_store_group

log = structlog.get_logger(__name__)

router = APIRouter()

# 巩固 root task（与 MemoryConsolidationService.CONSOLIDATION_ROOT_TASK_ID 一致——
# approval 服务 emit APPROVED/REJECTED 的 FK 占位）。字面量避免 import apscheduler 链。
_CONSOLIDATION_ROOT_TASK_ID = "_memory_consolidation_root"


# ---------------------------------------------------------------------------
# Response/Request schema
# ---------------------------------------------------------------------------


class ConsolidationCandidateItem(BaseModel):
    """单条巩固合并候选（响应 schema，给用户审查）。"""

    candidate_id: str
    run_id: str
    partition: str
    subject_key: str
    source_count: int
    merged_content: str
    rationale: str
    confidence: float
    is_sensitive: bool
    status: str
    created_at: str


class ConsolidationCandidatesListResponse(BaseModel):
    """GET /api/consolidation/candidates 响应。"""

    candidates: list[ConsolidationCandidateItem]
    pending_count: int


class BulkRejectRequest(BaseModel):
    """PUT bulk_reject 请求 body。"""

    candidate_ids: list[str]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _conn_of(store_group: Any) -> Any:
    conn = getattr(store_group, "conn", None)
    if conn is None:
        raise HTTPException(status_code=500, detail="store_group.conn 不可用")
    return conn


async def _ensure_root_task_or_500(store_group: Any) -> None:
    """副作用前 ensure consolidation root task（FK 安全，C2 审计不变量保护）。"""
    task_store = getattr(store_group, "task_store", None)
    from octoagent.core.store.audit_task import ensure_system_audit_task

    ok = await ensure_system_audit_task(
        task_store,
        _CONSOLIDATION_ROOT_TASK_ID,
        title="F127 记忆巩固根任务占位",
    )
    if not ok:
        log.error(
            "consolidation_root_task_ensure_failed",
            task_id=_CONSOLIDATION_ROOT_TASK_ID,
        )
        raise HTTPException(
            status_code=500,
            detail="consolidation root task ensure 失败；操作取消以保护 C2 审计不变量",
        )


def _build_approval(store_group: Any) -> Any:
    """构造 ConsolidationApprovalService（memory 表与 core 同 store_group.conn）。"""
    from octoagent.memory.service import MemoryService
    from octoagent.memory.store import ConsolidationStore

    from ..services.consolidation_approval import ConsolidationApprovalService

    conn = _conn_of(store_group)
    return ConsolidationApprovalService(
        memory_service=MemoryService(conn),
        consolidation_store=ConsolidationStore(conn),
        event_store=store_group.event_store,
        root_task_id=_CONSOLIDATION_ROOT_TASK_ID,
    )


def _approval_result_to_http(result: Any) -> JSONResponse:
    """ApprovalResult → HTTP 响应（conflict→409 / not_found→404 / pending[回滚]→409）。"""
    if result.ok:
        return JSONResponse(
            status_code=200,
            content={
                "ok": True,
                "status": result.status,
                "candidate_id": result.candidate_id,
                "new_sor_id": result.new_sor_id,
                "superseded_count": result.superseded_count,
            },
        )
    status_map = {"not_found": 404, "conflict": 409, "pending": 409}
    return JSONResponse(
        status_code=status_map.get(result.status, 400),
        content={
            "ok": False,
            "status": result.status,
            "candidate_id": result.candidate_id,
            "detail": result.detail,
        },
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/api/consolidation/candidates")
async def list_consolidation_candidates(
    request: Request,
    store_group=Depends(get_store_group),
) -> ConsolidationCandidatesListResponse:
    """列出 pending 巩固合并候选（给用户审查"建议合并这几条记忆"）。"""
    from octoagent.memory.models import ConsolidationCandidateStatus
    from octoagent.memory.store import ConsolidationStore

    consol_store = ConsolidationStore(_conn_of(store_group))
    pending = await consol_store.list_candidates(
        status=ConsolidationCandidateStatus.PENDING, limit=200
    )
    items = [
        ConsolidationCandidateItem(
            candidate_id=c.candidate_id,
            run_id=c.run_id,
            partition=c.partition.value,
            subject_key=c.subject_key,
            source_count=len(c.source_sor_ids),
            merged_content=c.merged_content,
            rationale=c.rationale,
            confidence=c.confidence,
            is_sensitive=c.is_sensitive,
            status=c.status.value,
            created_at=c.created_at.isoformat(),
        )
        for c in pending
    ]
    return ConsolidationCandidatesListResponse(
        candidates=items, pending_count=len(items)
    )


@router.post("/api/consolidation/candidates/{candidate_id}/accept")
async def accept_consolidation_candidate(
    candidate_id: str,
    request: Request,
    store_group=Depends(get_store_group),
) -> JSONResponse:
    """接受合并提议 → commit MERGE（源 SUPERSEDED，C4 经 approval atomic claim）。"""
    await _ensure_root_task_or_500(store_group)
    approval = _build_approval(store_group)
    result = await approval.accept(candidate_id)
    return _approval_result_to_http(result)


@router.post("/api/consolidation/candidates/{candidate_id}/reject")
async def reject_consolidation_candidate(
    candidate_id: str,
    request: Request,
    store_group=Depends(get_store_group),
) -> JSONResponse:
    """拒绝合并提议 → 标 REJECTED（不碰 SOR，C7）。"""
    await _ensure_root_task_or_500(store_group)
    approval = _build_approval(store_group)
    result = await approval.reject(candidate_id)
    return _approval_result_to_http(result)


@router.put("/api/consolidation/candidates/bulk_reject")
async def bulk_reject_consolidation_candidates(
    body: BulkRejectRequest,
    request: Request,
    store_group=Depends(get_store_group),
) -> JSONResponse:
    """批量拒绝合并提议（逐条走 approval.reject，单条失败不影响其他）。"""
    await _ensure_root_task_or_500(store_group)
    approval = _build_approval(store_group)
    rejected: list[str] = []
    skipped: list[str] = []
    for cid in body.candidate_ids:
        result = await approval.reject(cid)
        if result.ok:
            rejected.append(cid)
        else:
            skipped.append(cid)
    return JSONResponse(
        status_code=200,
        content={"rejected": rejected, "skipped": skipped},
    )
