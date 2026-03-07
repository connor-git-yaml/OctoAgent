"""Feature 022 recovery / backup / export API。"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from octoagent.provider.dx.backup_service import BackupService, resolve_project_root
from pydantic import BaseModel
from starlette.responses import JSONResponse

from ..deps import get_store_group

router = APIRouter()


class BackupCreateRequest(BaseModel):
    label: str | None = None


class ExportChatsRequest(BaseModel):
    task_id: str | None = None
    thread_id: str | None = None
    since: datetime | None = None
    until: datetime | None = None


@router.get("/api/ops/recovery")
async def get_recovery_summary(store_group=Depends(get_store_group)):
    """读取最近一次 backup / recovery drill 摘要。"""
    service = BackupService(resolve_project_root(), store_group=store_group)
    return service.get_recovery_summary().model_dump(mode="json")


@router.post("/api/ops/backup/create")
async def create_backup(
    body: BackupCreateRequest,
    store_group=Depends(get_store_group),
):
    """触发 backup create。"""
    try:
        service = BackupService(resolve_project_root(), store_group=store_group)
        bundle = await service.create_bundle(label=body.label)
        return bundle.model_dump(mode="json")
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "BACKUP_CREATE_FAILED",
                    "message": str(exc),
                }
            },
        )


@router.post("/api/ops/export/chats")
async def export_chats(
    body: ExportChatsRequest,
    store_group=Depends(get_store_group),
):
    """触发 chats export。"""
    try:
        service = BackupService(resolve_project_root(), store_group=store_group)
        manifest = await service.export_chats(
            task_id=body.task_id,
            thread_id=body.thread_id,
            since=body.since,
            until=body.until,
        )
        return manifest.model_dump(mode="json")
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "RECOVERY_EXPORT_FAILED",
                    "message": str(exc),
                }
            },
        )
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "RECOVERY_EXPORT_FAILED",
                    "message": str(exc),
                }
            },
        )
