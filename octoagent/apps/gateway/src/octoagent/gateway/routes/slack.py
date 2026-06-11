"""Slack Events API webhook 路由（F105 v0.2 FR-B4）。

经 ingress 契约挂载（SlackChannelAdapter.inbound_router，不带 front-door
protected——v0 HMAC 验签即鉴权）。HTTP status 语义（spec FR-B4）：

- user 级拒绝（unauthorized/ignored）回 **200**：Slack 对非 2xx 会 retry
  乃至 auto-disable event subscription——业务拒绝不是传输层错误。
- 验签失败/时间戳超窗回 401（真鉴权失败，提示 secret 配置问题）。
- secret env 缺失回 403（blocked，运维可见）；渠道未启用回 503。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

# F105 v0.2：tags 收进 router 自描述（ingress 契约挂载方不传 tags）
router = APIRouter(tags=["slack"])

_STATUS_TO_HTTP = {
    "accepted": 200,
    "duplicate": 200,
    "ignored": 200,
    "unauthorized": 200,
    "signature_invalid": 401,
    "timestamp_stale": 401,
    "blocked": 403,
    "disabled": 503,
}


@router.post("/api/slack/events")
async def slack_events(request: Request):
    service = getattr(request.app.state, "slack_service", None)
    if service is None:
        return JSONResponse(
            status_code=503,
            content={"ok": False, "error": "slack_service_unavailable"},
        )

    raw_body = await request.body()
    result = await service.handle_event_request(raw_body, request.headers)

    if result.status == "url_verification":
        # Slack URL 握手：原样回 challenge（验签已在 service 内先行）
        return JSONResponse(
            status_code=200, content={"challenge": result.challenge or ""}
        )

    payload: dict[str, Any] = {
        "ok": result.status in {"accepted", "duplicate", "ignored"},
        "status": result.status,
    }
    if result.detail:
        payload["detail"] = result.detail
    if result.task_id:
        payload["task_id"] = result.task_id
    payload["created"] = result.created
    return JSONResponse(
        status_code=_STATUS_TO_HTTP.get(result.status, 400), content=payload
    )
