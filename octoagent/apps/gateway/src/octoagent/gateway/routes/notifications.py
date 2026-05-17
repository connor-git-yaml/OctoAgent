"""F101 Phase C v2 H-4：Web Notification REST API 路由。

GET  /api/notifications?session_id=...  -- 获取未 dismiss 的通知列表（FR-B5 H3 Web refresh）
POST /api/notifications/{id}/dismiss    -- dismiss 指定通知（FR-B5 AC-B6）
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path, Query, Request
from fastapi.responses import JSONResponse

router = APIRouter()


def _get_notification_service(request: Request) -> Any | None:
    """从 app.state 获取 NotificationService 实例。"""
    return getattr(request.app.state, "notification_service", None)


@router.get("/api/notifications")
async def list_notifications(
    request: Request,
    session_id: str = Query(default="", description="会话 ID，用于过滤通知"),
) -> JSONResponse:
    """获取未 dismiss 的通知列表（FR-B5 H3 Web refresh）。

    返回当前 session_id 下所有未 dismiss 的通知。
    若 NotificationService 不可用，返回空列表（Constitution #6 降级）。

    Args:
        session_id: 会话 ID（可选；为空时返回全局通知）

    Returns:
        JSON 对象：{"notifications": [...]}
    """
    notification_service = _get_notification_service(request)
    if notification_service is None:
        return JSONResponse({"notifications": []})

    try:
        items = notification_service.list_active(session_id)
        return JSONResponse({"notifications": items})
    except Exception:
        return JSONResponse({"notifications": []})


@router.post("/api/notifications/{notification_id}/dismiss")
async def dismiss_notification(
    request: Request,
    notification_id: str = Path(description="通知 ID（sha256 前 16 位）"),
) -> JSONResponse:
    """dismiss 指定通知（FR-B5 AC-B6）。

    幂等：同一 notification_id 重复 dismiss 不报错。
    dismiss 语义：Web 下次刷新不再返回该通知；Telegram 已推送消息不撤回。

    Args:
        notification_id: 通知 ID（sha256 前 16 位）

    Returns:
        JSON 对象：{"ok": true, "notification_id": "..."}
    """
    notification_service = _get_notification_service(request)
    if notification_service is None:
        # NotificationService 不可用时仍返回 200（Constitution #6 降级）
        return JSONResponse({"ok": True, "notification_id": notification_id, "note": "service_unavailable"})

    notification_service.dismiss(notification_id, source="web")
    return JSONResponse({"ok": True, "notification_id": notification_id})
