"""Approvals REST API 路由 -- T036, T037

对齐 contracts/policy-api.md §1.1, §1.2。
GET /api/approvals -- 获取待审批列表 (FR-018)
POST /api/approve/{approval_id} -- 提交审批决策 (FR-019)
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from octoagent.policy.models import (
    ApprovalListItem,
    ApprovalResolveRequest,
    ApprovalResolveResponse,
    ApprovalsListResponse,
)

from ..deps import get_approval_manager

router = APIRouter()


@router.get("/api/approvals", response_model=ApprovalsListResponse)
async def list_approvals(
    approval_manager=Depends(get_approval_manager),
) -> ApprovalsListResponse:
    """获取待审批列表

    FR-018: 仅返回 status=PENDING 的审批请求，
    remaining_seconds 由服务端实时计算。
    """
    pending_records = approval_manager.get_pending_approvals()
    now = datetime.now(UTC)

    items: list[ApprovalListItem] = []
    for record in pending_records:
        req = record.request
        remaining = (req.expires_at - now).total_seconds()
        if remaining < 0:
            remaining = 0.0

        items.append(
            ApprovalListItem(
                approval_id=req.approval_id,
                task_id=req.task_id,
                tool_name=req.tool_name,
                tool_args_summary=req.tool_args_summary,
                risk_explanation=req.risk_explanation,
                policy_label=req.policy_label,
                side_effect_level=req.side_effect_level.value,
                remaining_seconds=round(remaining, 1),
                created_at=req.created_at,
            )
        )

    # 按 created_at 升序排列（最早的在前）
    items.sort(key=lambda x: x.created_at)

    return ApprovalsListResponse(
        approvals=items,
        total=len(items),
    )


@router.post("/api/approve/{approval_id}", response_model=ApprovalResolveResponse)
async def resolve_approval(
    approval_id: str,
    body: ApprovalResolveRequest,
    approval_manager=Depends(get_approval_manager),
) -> JSONResponse:
    """提交审批决策

    FR-019: 接收决策，调用 ApprovalManager.resolve()。
    成功/404/409 响应分别对应不同场景。
    """
    # 先尝试解决（resolve 内部自行查找，避免双重查询）
    result = await approval_manager.resolve(
        approval_id=approval_id,
        decision=body.decision,
        resolved_by="user:web",
    )

    if not result:
        # 区分 404（不存在）和 409（已解决）
        record = approval_manager.get_approval(approval_id)
        if record is None:
            return JSONResponse(
                status_code=404,
                content=ApprovalResolveResponse(
                    success=False,
                    error="approval_not_found",
                    message=f"Approval '{approval_id}' not found",
                ).model_dump(),
            )

        # 已解决（竞态或重复请求）
        return JSONResponse(
            status_code=409,
            content=ApprovalResolveResponse(
                success=False,
                error="approval_already_resolved",
                message=f"Approval '{approval_id}' has already been resolved",
                current_status=record.status.value,
            ).model_dump(),
        )

    return JSONResponse(
        status_code=200,
        content=ApprovalResolveResponse(
            success=True,
            approval_id=approval_id,
            decision=body.decision.value,
            message="Approval resolved successfully",
        ).model_dump(),
    )
