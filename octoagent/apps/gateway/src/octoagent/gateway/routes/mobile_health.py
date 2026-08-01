"""F154 原生 iOS 健康预览提交路由。"""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from octoagent.core.models import DeletionReceipt
from octoagent.provider import ProviderRouter
from pydantic import ValidationError

from ..services.health_ingestion import (
    HealthAnalysisAccepted,
    HealthAnalysisRequest,
    HealthIngestionError,
    HealthIngestionService,
    HealthRequestContext,
    HealthReviewAccepted,
)
from .device_trust import (
    Service as DeviceTrustServiceDependency,
)
from .device_trust import (
    _protected_route_context,
)

router = APIRouter(prefix="/api/mobile/v1/health", tags=["mobile-health"])


def get_health_ingestion_service(request: Request) -> HealthIngestionService:
    service = getattr(request.app.state, "health_ingestion_service", None)
    if not isinstance(service, HealthIngestionService):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "HEALTH_INGESTION_NOT_READY",
                "message": "健康数据服务尚未准备好。",
            },
        )
    return service


HealthService = Annotated[HealthIngestionService, Depends(get_health_ingestion_service)]


def get_provider_router(request: Request) -> ProviderRouter:
    router = getattr(request.app.state, "provider_router", None)
    if router is None or not callable(getattr(router, "resolve_for_alias", None)):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "HEALTH_ANALYSIS_NOT_READY",
                "message": "健康分析服务尚未准备好。",
            },
        )
    return router


AnalysisRouter = Annotated[ProviderRouter, Depends(get_provider_router)]


def _health_error(exc: HealthIngestionError) -> HTTPException:
    messages = {
        "DEVICE_NOT_FOUND": "没有找到这台设备。",
        "DEVICE_REQUEST_EXPIRED": "设备请求已过期，请重试。",
        "DEVICE_SIGNATURE_INVALID": "设备签名不正确。",
        "DEVICE_TOKEN_INVALID": "设备令牌无效或已经过期。",
        "HEALTH_ANALYSIS_FAILED": "健康分析暂时没有完成，请稍后再试。",
        "HEALTH_CAPABILITY_DENIED": "这次设备授权不允许提交健康预览。",
        "HEALTH_CONSENT_INVALID": "这次健康分析批准无效或已经使用。",
        "HEALTH_DELETION_INCOMPLETE": "健康数据删除尚未完成，请重试。",
        "HEALTH_PREVIEW_HASH_MISMATCH": "健康预览已变化，请重新检查。",
        "HEALTH_RAW_FIELD_FORBIDDEN": "健康预览包含不允许发送的内容。",
        "HEALTH_REVIEW_INVALID": "健康预览格式不正确。",
        "HEALTH_REVIEW_SCOPE_MISMATCH": "健康预览与当前设备不匹配。",
        "HEALTH_REQUEST_REPLAYED": "这个健康预览请求已经使用过，请重试。",
    }
    return HTTPException(
        status_code=exc.status_code,
        detail={
            "code": exc.reason_code,
            "message": messages.get(exc.reason_code, "健康预览未能提交。"),
        },
    )


@router.post(
    "/reviews",
    response_model=HealthReviewAccepted,
    status_code=status.HTTP_201_CREATED,
)
async def submit_health_review(
    request: Request,
    service: HealthService,
    device_service: DeviceTrustServiceDependency,
) -> HealthReviewAccepted:
    headers, raw_body = await _protected_route_context(
        request,
        service=device_service,
    )
    try:
        payload: Any = json.loads(raw_body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _health_error(HealthIngestionError("HEALTH_REVIEW_INVALID", status_code=422)) from exc
    if not isinstance(payload, dict):
        raise _health_error(HealthIngestionError("HEALTH_REVIEW_INVALID", status_code=422))
    try:
        return await service.submit_review(
            headers=headers,
            method=request.method,
            canonical_path=request.url.path,
            raw_body=raw_body,
            payload=payload,
        )
    except HealthIngestionError as exc:
        raise _health_error(exc) from exc


@router.post(
    "/analyses",
    response_model=HealthAnalysisAccepted,
    status_code=status.HTTP_201_CREATED,
)
async def submit_health_analysis(
    request: Request,
    service: HealthService,
    device_service: DeviceTrustServiceDependency,
    provider_router: AnalysisRouter,
) -> HealthAnalysisAccepted:
    headers, raw_body = await _protected_route_context(
        request,
        service=device_service,
    )
    try:
        payload = json.loads(raw_body)
        analysis_request = HealthAnalysisRequest.model_validate(payload)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
        reason = "HEALTH_REVIEW_INVALID"
        if isinstance(exc, ValidationError) and any(
            error.get("type") == "extra_forbidden" for error in exc.errors()
        ):
            reason = "HEALTH_RAW_FIELD_FORBIDDEN"
        raise _health_error(HealthIngestionError(reason, status_code=422)) from exc
    try:
        return await service.submit_analysis(
            context=HealthRequestContext(
                headers=headers,
                method=request.method,
                canonical_path=request.url.path,
                raw_body=raw_body,
            ),
            request=analysis_request,
            provider_router=provider_router,
        )
    except HealthIngestionError as exc:
        raise _health_error(exc) from exc


@router.delete(
    "/sources/{source_hash}",
    response_model=DeletionReceipt,
)
async def delete_health_source(
    request: Request,
    service: HealthService,
    device_service: DeviceTrustServiceDependency,
    source_hash: Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")],
) -> DeletionReceipt:
    headers, raw_body = await _protected_route_context(
        request,
        service=device_service,
    )
    if raw_body:
        raise _health_error(HealthIngestionError("HEALTH_DELETION_INCOMPLETE", status_code=422))
    try:
        return await service.delete_source(
            context=HealthRequestContext(
                headers=headers,
                method=request.method,
                canonical_path=request.url.path,
                raw_body=raw_body,
            ),
            source_hash=source_hash,
        )
    except HealthIngestionError as exc:
        raise _health_error(exc) from exc


__all__ = ["get_health_ingestion_service", "get_provider_router", "router"]
