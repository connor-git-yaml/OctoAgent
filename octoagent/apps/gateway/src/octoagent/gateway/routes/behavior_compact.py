"""F111 Behavior Compactor — 精简提议候选人审 + 手动触发 REST（C7 用户面）。

GET  /api/behavior/compact/candidates              — 列出 pending 精简候选（含服务端 diff）
POST /api/behavior/compact/candidates/{id}/accept  — 接受 → 覆写落盘 + F107 版本
POST /api/behavior/compact/candidates/{id}/reject  — 拒绝 → 标 REJECTED（文件零触碰）
POST /api/behavior/compact/trigger                 — 手动触发发现端（同步，CLI 消费）

★ C7 用户面：候选 accept 是精简提议**唯一**落盘入口（H1：不是 Agent 在对话里逼问，
是用户经通知/CLI 引导主动审查）。实际落盘的 C4 红线逻辑全在
``BehaviorCompactApprovalService``——本路由是薄 HTTP 壳（仿 F127 consolidation 路由）。

diff 语义：服务端 difflib 生成 unified diff（当前盘上内容 vs 候选内容）——候选不存
原文全文，盘上内容若已漂移，diff 展示的是"现在 apply 会改什么"（诚实），且 accept
会因 source_hash 失配走 CONFLICT。
"""

from __future__ import annotations

import difflib
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..deps import get_store_group

log = structlog.get_logger(__name__)

router = APIRouter()

# Codex round5 P3：root 占位常量/ensure 单一事实源在 behavior_compact_root.py
# （零 apscheduler 依赖，路由可直接 import）——路由与编排服务共用同一创建路径，
# 降级启动期也不会落下通用形态 root task 歪斜后续 spawn 的 audit lineage。
from ..services.behavior_compact_root import (  # noqa: E402
    BEHAVIOR_COMPACT_ROOT_TASK_ID as _BEHAVIOR_COMPACT_ROOT_TASK_ID,
)
from ..services.behavior_compact_root import (  # noqa: E402
    ensure_behavior_compact_root as _ensure_behavior_compact_root,
)

#: diff 病理护栏上限（Codex round8 P1：截断会破坏"人审看全量改动再 apply"的
#: 保障）。数学上不可达：发现端输入硬顶 COMPACT_INPUT_CHAR_BUDGET=8000 字符 +
#: H1 保证候选更小 → unified diff 最坏 ~2×8000+行头 ≈ 20k，远低于本上限——
#: 可采纳输入下 diff 恒完整，本值仅防未来演化出的病理输入撑爆响应。
_DIFF_MAX_CHARS = 120_000


# ---------------------------------------------------------------------------
# Response/Request schema
# ---------------------------------------------------------------------------


class BehaviorCompactCandidateItem(BaseModel):
    """单条精简候选（响应 schema，给用户审查）。"""

    candidate_id: str
    run_id: str
    file_id: str
    agent_slug: str
    project_slug: str
    rationale: str
    size_before: int
    size_after: int
    status: str
    created_at: str
    diff: str = Field(description="unified diff（当前盘上内容 vs 候选内容，截断展示）")


class BehaviorCompactCandidatesListResponse(BaseModel):
    candidates: list[BehaviorCompactCandidateItem]
    pending_count: int


class BehaviorCompactTriggerRequest(BaseModel):
    """手动触发请求：file_id 为空 → 扫默认 SHARED eligible 集。"""

    file_id: str = Field(default="", description="目标文件短名（空=默认集）")
    project_slug: str = Field(
        default="default", description="PROJECT scope 文件的 project slug"
    )


class BehaviorCompactTriggerFileOutcome(BaseModel):
    file_id: str
    status: str  # proposed / skipped / fallback
    reason: str = ""
    candidate_id: str = ""
    size_before: int = 0
    size_after: int = 0
    diff: str = ""


class BehaviorCompactTriggerResponse(BaseModel):
    run_id: str
    outcomes: list[BehaviorCompactTriggerFileOutcome]
    proposals_made: int


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _ensure_root_task_or_500(store_group: Any) -> None:
    """副作用前 ensure compact root（FK 安全，C2 审计不变量保护）。

    与编排服务共用 ``ensure_behavior_compact_root`` 单一创建路径（Codex round5
    P3）——不再走通用 ``ensure_system_audit_task``（其 thread_id/status 形态与
    服务不同，降级期先创建会永久歪斜后续 spawn 的 parent lineage）。
    """
    try:
        await _ensure_behavior_compact_root(
            store_group.task_store, store_group.work_store
        )
    except Exception:
        log.exception(
            "behavior_compact_root_task_ensure_failed",
            task_id=_BEHAVIOR_COMPACT_ROOT_TASK_ID,
        )
        raise HTTPException(
            status_code=500,
            detail="behavior compact root ensure 失败；操作取消以保护 C2 审计不变量",
        ) from None


def _build_approval(request: Request, store_group: Any) -> Any:
    from pathlib import Path

    from ..services.behavior_compact_approval import BehaviorCompactApprovalService

    return BehaviorCompactApprovalService(
        project_root=Path(request.app.state.project_root),
        compact_store=store_group.behavior_compact_store,
        event_store=store_group.event_store,
        stores=store_group,
        root_task_id=_BEHAVIOR_COMPACT_ROOT_TASK_ID,
        # Codex round9 P2：USER.md accept 后同步 live state（harness 装配的真件）
        snapshot_store=getattr(request.app.state, "snapshot_store", None),
    )


def _current_disk_content(request: Request, candidate: Any) -> str:
    """读候选目标文件当前盘上内容（diff 基线；缺失/异常 → ''）。"""
    from pathlib import Path

    from octoagent.core.behavior_workspace import resolve_write_path_by_file_id

    try:
        resolved = resolve_write_path_by_file_id(
            Path(request.app.state.project_root),
            candidate.file_id,
            agent_slug=candidate.agent_slug,
            project_slug=candidate.project_slug,
        )
        if not resolved.exists():
            return ""
        return resolved.read_text(encoding="utf-8")
    except Exception:
        return ""


def _unified_diff(current: str, proposed: str, file_id: str) -> str:
    diff_text = "".join(
        difflib.unified_diff(
            current.splitlines(keepends=True),
            proposed.splitlines(keepends=True),
            fromfile=f"{file_id}（当前）",
            tofile=f"{file_id}（精简提议）",
        )
    )
    if not diff_text:
        diff_text = "（无行级差异）\n"
    if len(diff_text) > _DIFF_MAX_CHARS:
        diff_text = diff_text[:_DIFF_MAX_CHARS] + "\n…（diff 超长已截断）"
    return diff_text


def _approval_result_to_http(result: Any) -> JSONResponse:
    """CompactApprovalResult → HTTP（conflict→409 / not_found→404 / pending[回滚]→409）。"""
    if result.ok:
        return JSONResponse(
            status_code=200,
            content={
                "ok": True,
                "status": result.status,
                "candidate_id": result.candidate_id,
                "file_id": result.file_id,
            },
        )
    status_map = {"not_found": 404, "conflict": 409, "pending": 409}
    return JSONResponse(
        status_code=status_map.get(result.status, 400),
        content={
            "ok": False,
            "status": result.status,
            "candidate_id": result.candidate_id,
            "file_id": result.file_id,
            "detail": result.detail,
        },
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/api/behavior/compact/candidates")
async def list_behavior_compact_candidates(
    request: Request,
    limit: int = 200,
    store_group=Depends(get_store_group),
) -> BehaviorCompactCandidatesListResponse:
    """列出 pending 精简候选（含服务端 unified diff 供人审）。

    ``limit``（默认 200，上限 1000，Codex round7 P2）：单用户 nightly ≤3 提议 +
    输入幂等去重下 200 实际难以触顶，但顶到时旧候选不可达是硬伤——调 limit 可达。
    """
    from octoagent.core.models.behavior_compact import BehaviorCompactCandidateStatus

    bounded = max(1, min(limit, 1000))
    pending = await store_group.behavior_compact_store.list_candidates(
        status=BehaviorCompactCandidateStatus.PENDING, limit=bounded
    )
    # Codex round15 P3：pending_count 报**真实总数**（COUNT 查询）而非页大小——
    # 消费方据此可知响应是否被 limit 截断。
    total_pending = await store_group.behavior_compact_store.count_candidates(
        status=BehaviorCompactCandidateStatus.PENDING
    )
    items = [
        BehaviorCompactCandidateItem(
            candidate_id=c.candidate_id,
            run_id=c.run_id,
            file_id=c.file_id,
            agent_slug=c.agent_slug,
            project_slug=c.project_slug,
            rationale=c.rationale,
            size_before=c.size_before,
            size_after=c.size_after,
            status=c.status.value,
            created_at=c.created_at.isoformat(),
            diff=_unified_diff(
                _current_disk_content(request, c), c.compacted_content, c.file_id
            ),
        )
        for c in pending
    ]
    return BehaviorCompactCandidatesListResponse(
        candidates=items, pending_count=total_pending
    )


@router.post("/api/behavior/compact/candidates/{candidate_id}/accept")
async def accept_behavior_compact_candidate(
    candidate_id: str,
    request: Request,
    store_group=Depends(get_store_group),
) -> JSONResponse:
    """接受精简提议 → 覆写落盘 + F107 版本（C4 经 approval atomic claim）。"""
    await _ensure_root_task_or_500(store_group)
    approval = _build_approval(request, store_group)
    result = await approval.accept(candidate_id)
    return _approval_result_to_http(result)


@router.post("/api/behavior/compact/candidates/{candidate_id}/reject")
async def reject_behavior_compact_candidate(
    candidate_id: str,
    request: Request,
    store_group=Depends(get_store_group),
) -> JSONResponse:
    """拒绝精简提议 → 标 REJECTED（行为文件零触碰，C7）。"""
    await _ensure_root_task_or_500(store_group)
    approval = _build_approval(request, store_group)
    result = await approval.reject(candidate_id)
    return _approval_result_to_http(result)


@router.post("/api/behavior/compact/trigger")
async def trigger_behavior_compact(
    body: BehaviorCompactTriggerRequest,
    request: Request,
    store_group=Depends(get_store_group),
) -> BehaviorCompactTriggerResponse:
    """手动触发发现端（同步跑，秒级；CLI 主消费）。

    - ``compact_active=False`` **不拦**手动触发（active 只门 cron——用户显式动作，
      spec DP-2）。
    - 与 cron 共享单飞：编排服务运行中 → 409。
    - 服务未装配（gateway 降级启动）→ 503。
    """
    service = getattr(request.app.state, "behavior_compaction_service", None)
    if service is None:
        raise HTTPException(
            status_code=503,
            detail="behavior compaction 服务不可用（gateway 降级启动）",
        )
    await _ensure_root_task_or_500(store_group)

    file_ids: list[str] | None = None
    if body.file_id.strip():
        file_ids = [body.file_id.strip()]
    result = await service.run_manual(
        file_ids=file_ids, project_slug=body.project_slug or "default"
    )
    if result.skipped_reason == "already_running":
        raise HTTPException(
            status_code=409, detail="compact 正在运行中（cron 或另一次手动触发）"
        )
    if result.error:
        # Codex round3 P2：发现端异常显式 500——不与"真无提议"混淆成空 200
        raise HTTPException(status_code=500, detail=result.error)

    outcomes = []
    for o in result.outcomes:
        diff = ""
        if o.status == "proposed" and o.candidate_id:
            candidate = await store_group.behavior_compact_store.get_candidate(
                o.candidate_id
            )
            if candidate is not None:
                diff = _unified_diff(
                    _current_disk_content(request, candidate),
                    candidate.compacted_content,
                    candidate.file_id,
                )
        outcomes.append(
            BehaviorCompactTriggerFileOutcome(
                file_id=o.file_id,
                status=o.status,
                reason=o.reason,
                candidate_id=o.candidate_id,
                size_before=o.size_before,
                size_after=o.size_after,
                diff=diff,
            )
        )
    return BehaviorCompactTriggerResponse(
        run_id=result.run_id,
        outcomes=outcomes,
        proposals_made=sum(1 for o in result.outcomes if o.status == "proposed"),
    )
