"""Discord Interactions webhook 路由（F105 v0.2 FR-C4）。

经 ingress 契约挂载（DiscordChannelAdapter.inbound_router，不带 front-door
protected——Ed25519 验签即鉴权）。HTTP status 语义：

- **验签失败必须 401**：Discord 注册 Interactions Endpoint 时主动探测
  bad-signature 行为，非 401 端点注册不通过（recon §6）。
- 交互应答（pong/受理/拒绝/不支持）一律 200 + interaction response 包体
  ——用户级拒绝走 ephemeral 文案（type 4 flags=64），传输层 4xx 会显示
  "interaction failed"。
- public_key 未配置 403（blocked，运维可见）；渠道未启用 503。
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

# F105 v0.2：tags 收进 router 自描述（ingress 契约挂载方不传 tags）
router = APIRouter(tags=["discord"])


@router.post("/api/discord/interactions")
async def discord_interactions(request: Request):
    service = getattr(request.app.state, "discord_service", None)
    if service is None:
        return JSONResponse(
            status_code=503,
            content={"ok": False, "error": "discord_service_unavailable"},
        )

    raw_body = await request.body()
    result = await service.handle_interaction_request(raw_body, request.headers)

    if result.status == "signature_invalid":
        return JSONResponse(
            status_code=401,
            content={"ok": False, "status": result.status, "detail": result.detail},
        )
    if result.status == "blocked":
        return JSONResponse(
            status_code=403,
            content={"ok": False, "status": result.status, "detail": result.detail},
        )
    if result.status == "disabled":
        return JSONResponse(
            status_code=503,
            content={"ok": False, "status": result.status, "detail": result.detail},
        )

    # pong / accepted / duplicate / unauthorized / unsupported / ignored：
    # 200 + interaction response 包体（Discord 客户端据此渲染应答）
    return JSONResponse(status_code=200, content=result.response_payload or {})
