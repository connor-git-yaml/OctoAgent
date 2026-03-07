"""Telegram webhook 路由。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.post("/api/telegram/webhook")
async def telegram_webhook(
    request: Request,
    body: dict[str, Any],
    telegram_secret: str | None = Header(
        default=None,
        alias="X-Telegram-Bot-Api-Secret-Token",
    ),
):
    service = getattr(request.app.state, "telegram_service", None)
    if service is None:
        return JSONResponse(
            status_code=503,
            content={"ok": False, "error": "telegram_service_unavailable"},
        )

    result = await service.handle_webhook_update(body, secret_token=telegram_secret or "")
    status_to_code = {
        "accepted": 200,
        "duplicate": 200,
        "ignored": 200,
        "pairing_required": 202,
        "blocked": 403,
        "unauthorized": 401,
        "disabled": 503,
    }
    payload: dict[str, Any] = {"ok": result.status in {"accepted", "duplicate", "ignored"}}
    payload["status"] = result.status
    if result.detail:
        payload["detail"] = result.detail
    if result.task_id:
        payload["task_id"] = result.task_id
    payload["created"] = result.created
    return JSONResponse(status_code=status_to_code.get(result.status, 400), content=payload)
