"""Approvals REST API 路由 -- T036, T037, Feature 061

对齐 contracts/policy-api.md §1.1, §1.2。
GET /api/approvals -- 获取待审批列表 (FR-018)
POST /api/approve/{approval_id} -- 提交审批决策 (FR-019)

Feature 061 T-016:
GET /api/approval-overrides -- 获取 always 覆盖列表
DELETE /api/approval-overrides/{override_id} -- 撤销单条覆盖
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from octoagent.policy.models import (
    ApprovalListItem,
    ApprovalOverrideDeleteResponse,
    ApprovalOverrideListResponse,
    ApprovalResolveRequest,
    ApprovalResolveResponse,
    ApprovalsListResponse,
)

from ..deps import get_approval_manager, get_approval_override_cache, get_approval_override_repo

logger = logging.getLogger(__name__)

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


# ============================================================
# Feature 061 T-016: 审批覆盖管理 API
# ============================================================


@router.get("/api/approval-overrides", response_model=ApprovalOverrideListResponse)
async def list_approval_overrides(
    agent_runtime_id: str | None = Query(
        default=None,
        description="按 Agent 实例 ID 过滤（可选）",
    ),
    override_repo=Depends(get_approval_override_repo),
) -> ApprovalOverrideListResponse:
    """获取 always 覆盖列表

    Feature 061 T-016: 展示当前所有（或指定 Agent 的）always 授权记录。
    支持通过 ?agent_runtime_id=xxx 按 Agent 过滤。
    """
    if agent_runtime_id:
        overrides = await override_repo.load_overrides(agent_runtime_id)
    else:
        overrides = await override_repo.load_all_overrides()

    return ApprovalOverrideListResponse(
        overrides=overrides,
        total=len(overrides),
    )


@router.delete(
    "/api/approval-overrides/{agent_runtime_id}/{tool_name}",
    response_model=ApprovalOverrideDeleteResponse,
)
async def delete_approval_override(
    agent_runtime_id: str,
    tool_name: str,
    override_repo=Depends(get_approval_override_repo),
    override_cache=Depends(get_approval_override_cache),
) -> JSONResponse:
    """撤销单条 always 覆盖

    Feature 061 T-016: 通过 agent_runtime_id + tool_name 复合键撤销。
    同时清除内存缓存，下次工具调用将重新触发审批。
    """
    removed = await override_repo.remove_override(agent_runtime_id, tool_name)

    if not removed:
        return JSONResponse(
            status_code=404,
            content=ApprovalOverrideDeleteResponse(
                success=False,
                message=f"Override not found for agent='{agent_runtime_id}', tool='{tool_name}'",
                error="override_not_found",
            ).model_dump(),
        )

    logger.info(
        "approval_override_revoked_via_api",
        extra={
            "agent_runtime_id": agent_runtime_id,
            "tool_name": tool_name,
        },
    )

    return JSONResponse(
        status_code=200,
        content=ApprovalOverrideDeleteResponse(
            success=True,
            message=f"Override for agent='{agent_runtime_id}', tool='{tool_name}' has been revoked",
        ).model_dump(),
    )
