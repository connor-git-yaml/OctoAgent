"""Feature 022 recovery / backup / export API。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from octoagent.core.models import UpdateTriggerSource
from octoagent.provider.dx.backup_service import BackupService, resolve_project_root
from octoagent.provider.dx.update_service import UpdateActionError
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


class UpdateApplyRequest(BaseModel):
    wait: bool = False


def _model_dump(payload: Any) -> dict[str, Any]:
    if payload is None:
        return {}
    model_dump = getattr(payload, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    if isinstance(payload, dict):
        return payload
    raise TypeError(f"不支持的响应对象类型: {type(payload).__name__}")


def _get_update_status_store(request: Request) -> Any | None:
    store = getattr(request.app.state, "update_status_store", None)
    if store is not None:
        return store

    try:
        from octoagent.provider.dx.update_status_store import UpdateStatusStore
    except Exception:
        return None

    try:
        store = UpdateStatusStore(resolve_project_root())
    except TypeError:
        store = UpdateStatusStore(resolve_project_root(), data_dir=None)
    request.app.state.update_status_store = store
    return store


def _load_latest_update_summary(request: Request) -> dict[str, Any]:
    store = _get_update_status_store(request)
    if store is None:
        return {}
    method = getattr(store, "load_summary", None)
    if not callable(method):
        return {}
    return _model_dump(method())


def _get_update_service(request: Request) -> Any | None:
    service = getattr(request.app.state, "update_service", None)
    if service is not None:
        return service

    try:
        from octoagent.provider.dx.update_service import UpdateService
    except Exception:
        return None

    kwargs: dict[str, Any] = {}
    if (status_store := _get_update_status_store(request)) is not None:
        kwargs["status_store"] = status_store

    try:
        service = UpdateService(resolve_project_root(), **kwargs)
    except TypeError:
        service = UpdateService(resolve_project_root())
    request.app.state.update_service = service
    return service


def _ops_error_response(
    *,
    default_code: str,
    exc: Exception,
) -> JSONResponse:
    if isinstance(exc, UpdateActionError):
        status_code = exc.status_code
        error_code = exc.code
        message = exc.message
    else:
        message = str(exc)
        status_code = 500
        error_code = default_code
        if isinstance(exc, ValueError):
            status_code = 400

    content: dict[str, Any] = {
        "error": {
            "code": error_code,
            "message": message,
        }
    }
    attempt_id = getattr(exc, "attempt_id", None)
    if attempt_id:
        content["error"]["attempt_id"] = attempt_id
    return JSONResponse(status_code=status_code, content=content)


def _ops_summary_failure_response(
    *,
    error_code: str,
    status_code: int,
    summary: Any,
) -> JSONResponse | None:
    payload = _model_dump(summary)
    failure_report = payload.get("failure_report")
    if not isinstance(failure_report, dict):
        return None
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": error_code,
                "message": failure_report.get("message", "操作失败"),
                "attempt_id": payload.get("attempt_id"),
            },
            "summary": payload,
        },
    )


@router.get("/api/ops/recovery")
async def get_recovery_summary(store_group=Depends(get_store_group)):
    """读取最近一次 backup / recovery drill 摘要。"""
    service = BackupService(resolve_project_root(), store_group=store_group)
    return service.get_recovery_summary().model_dump(mode="json")


def _build_auth_diagnostics(
    profiles: list[Any],
    *,
    cli_available: bool,
    now: Any | None = None,
) -> dict[str, Any]:
    """构建诊断响应（可独立测试）。

    所有字段都是脱敏元信息：access_token / refresh_token / account_id 不会出现。
    """
    from datetime import UTC, datetime

    from octoagent.provider import OAuthCredential
    from octoagent.provider.auth.pkce_oauth_adapter import REFRESH_BUFFER_SECONDS

    ts_now = now or datetime.now(tz=UTC)
    items: list[dict[str, Any]] = []
    for profile in profiles:
        entry: dict[str, Any] = {
            "name": profile.name,
            "provider": profile.provider,
            "auth_mode": profile.auth_mode,
            "is_default": profile.is_default,
            "last_refresh_at": profile.updated_at.isoformat() if profile.updated_at else None,
            "codex_cli_external_available": (
                cli_available if profile.provider == "openai-codex" else False
            ),
        }
        if profile.auth_mode == "oauth" and isinstance(profile.credential, OAuthCredential):
            expires_at = profile.credential.expires_at
            remaining = (expires_at - ts_now).total_seconds()
            entry.update(
                {
                    "expires_at": expires_at.isoformat(),
                    "expires_in_seconds": int(remaining),
                    # 5-min buffer gate 触发即 is_expired=true（与 PkceOAuthAdapter 一致）
                    "is_expired": remaining <= REFRESH_BUFFER_SECONDS,
                }
            )
        else:
            entry.update(
                {
                    "expires_at": None,
                    "expires_in_seconds": None,
                    "is_expired": False,
                }
            )
        items.append(entry)
    return {"profiles": items}


@router.get("/api/ops/auth/diagnostics")
async def get_auth_diagnostics():
    """OAuth 凭证只读诊断 -- Feature 078 Phase 4。

    返回所有 profile 的过期状态 / 外部 CLI 可用性等元信息。
    **绝对不**返回 access_token / refresh_token / account_id 原值，仅脱敏后的元数据。
    """
    from octoagent.provider import CredentialStore
    from octoagent.provider.auth.codex_cli_bridge import read_codex_cli_auth

    store = CredentialStore()
    try:
        profiles = store.list_profiles()
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "CREDENTIAL_STORE_UNREADABLE",
                    "message": f"无法读取凭证存储: {type(exc).__name__}",
                }
            },
        )

    # 只在本次请求尝试一次外挂 CLI 读取，避免每个 profile 都 stat
    try:
        cli_cred = read_codex_cli_auth()
    except Exception:
        cli_cred = None

    return _build_auth_diagnostics(profiles, cli_available=cli_cred is not None)


@router.get("/api/ops/tool-registry/diagnostics")
async def get_tool_registry_diagnostics(request: Request):
    """ToolBroker 注册失败明细。

    /ready 只暴露 diagnostics_count，排查 try_register 失败时拿不到具体 message。
    本端点把 ToolBroker.registry_diagnostics 列表直出（含 tool_name/error_type/
    message/timestamp），用于 MCP 注册冲突等场景的现场取证。
    """
    tool_broker = getattr(request.app.state, "tool_broker", None)
    if tool_broker is None:
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "TOOL_BROKER_UNAVAILABLE",
                    "message": "ToolBroker 未挂载。",
                }
            },
        )
    diagnostics = getattr(tool_broker, "registry_diagnostics", [])
    if callable(diagnostics):
        diagnostics = diagnostics()
    items = [_model_dump(item) for item in (diagnostics or [])]
    return {"count": len(items), "items": items}


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


@router.get("/api/ops/update/status")
async def get_update_status(request: Request):
    """读取最近一次升级摘要。"""
    return _load_latest_update_summary(request)


@router.post("/api/ops/update/dry-run")
async def update_dry_run(request: Request):
    """执行 update dry-run。"""
    service = _get_update_service(request)
    if service is None:
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "UPDATE_SERVICE_UNAVAILABLE",
                    "message": "当前环境未启用 update service。",
                }
            },
        )

    try:
        summary = await service.preview(trigger_source=UpdateTriggerSource.WEB)
        return _model_dump(summary)
    except Exception as exc:
        return _ops_error_response(default_code="UPDATE_DRY_RUN_FAILED", exc=exc)


@router.post("/api/ops/update/apply")
async def update_apply(
    body: UpdateApplyRequest,
    request: Request,
):
    """触发真实 update。"""
    service = _get_update_service(request)
    if service is None:
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "UPDATE_SERVICE_UNAVAILABLE",
                    "message": "当前环境未启用 update service。",
                }
            },
        )

    try:
        summary = await service.apply(
            trigger_source=UpdateTriggerSource.WEB,
            wait=body.wait,
        )
        if body.wait and (
            failure_response := _ops_summary_failure_response(
                error_code="UPDATE_APPLY_FAILED",
                status_code=500,
                summary=summary,
            )
        ) is not None:
            return failure_response
        return JSONResponse(status_code=202, content=_model_dump(summary))
    except Exception as exc:
        return _ops_error_response(default_code="UPDATE_APPLY_FAILED", exc=exc)


@router.post("/api/ops/restart")
async def restart_runtime(request: Request):
    """触发独立 restart。"""
    service = _get_update_service(request)
    if service is None:
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "UPDATE_SERVICE_UNAVAILABLE",
                    "message": "当前环境未启用 update service。",
                }
            },
        )

    try:
        summary = await service.restart(trigger_source=UpdateTriggerSource.WEB)
        if (
            failure_response := _ops_summary_failure_response(
                error_code="RESTART_FAILED",
                status_code=500,
                summary=summary,
            )
        ) is not None:
            return failure_response
        return JSONResponse(status_code=202, content=_model_dump(summary))
    except Exception as exc:
        return _ops_error_response(default_code="RESTART_UNAVAILABLE", exc=exc)


@router.post("/api/ops/verify")
async def verify_runtime(request: Request):
    """触发独立 verify。"""
    service = _get_update_service(request)
    if service is None:
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "UPDATE_SERVICE_UNAVAILABLE",
                    "message": "当前环境未启用 update service。",
                }
            },
        )

    try:
        summary = await service.verify(trigger_source=UpdateTriggerSource.WEB)
        if (
            failure_response := _ops_summary_failure_response(
                error_code="VERIFY_FAILED",
                status_code=500,
                summary=summary,
            )
        ) is not None:
            return failure_response
        return _model_dump(summary)
    except Exception as exc:
        return _ops_error_response(default_code="VERIFY_FAILED", exc=exc)


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
