"""Feature 017: Unified Operator Inbox API。"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from octoagent.core.models import OperatorActionRequest, OperatorActionResult, OperatorInboxResponse

router = APIRouter()


@router.get("/api/operator/inbox", response_model=OperatorInboxResponse)
async def get_operator_inbox(request: Request) -> OperatorInboxResponse:
    service = request.app.state.operator_inbox_service
    return await service.get_inbox()


@router.post("/api/operator/actions", response_model=OperatorActionResult)
async def post_operator_action(
    body: OperatorActionRequest,
    request: Request,
):
    service = request.app.state.operator_action_service
    try:
        return await service.execute(body)
    except Exception as exc:  # pragma: no cover - 防御性兜底
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "OPERATOR_ACTION_FAILED",
                    "message": str(exc),
                }
            },
        )
