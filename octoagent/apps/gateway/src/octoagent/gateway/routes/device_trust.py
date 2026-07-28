"""F153 Web owner registration/approval/revoke 路由。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from octoagent.protocol.device_trust import (
    DeviceEnrollmentRequest,
    DeviceEnrollmentStatusResponse,
    DeviceProofHeaders,
    DeviceTokenChallengeResponse,
    DeviceTokenRequest,
    DeviceTokenResponse,
    MobileDeviceProfileResponse,
    MobileReadyResponse,
    OwnerDeviceProjection,
    OwnerRegistrationChallengeResponse,
)
from pydantic import ValidationError

from ..deps import get_front_door_guard, require_front_door_access
from ..services.device_trust import DeviceTrustService, DeviceTrustServiceError

router = APIRouter(prefix="/api/device-trust/v1", tags=["device-trust"])
mobile_router = APIRouter(prefix="/api/mobile/v1", tags=["mobile-device-trust"])


def get_device_trust_service(request: Request) -> DeviceTrustService:
    service = getattr(request.app.state, "device_trust_service", None)
    if not isinstance(service, DeviceTrustService):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "DEVICE_TRUST_NOT_READY",
                "message": "设备连接服务尚未准备好。",
            },
        )
    return service


async def require_cloudflare_owner(
    request: Request,
    guard=Depends(get_front_door_guard),
) -> str:
    """仅接受真实 Cloudflare Access principal，不把本地/其它 mode 转为设备 owner。"""

    await require_front_door_access(request, guard)
    principal = getattr(request.state, "cloudflare_principal", None)
    subject = getattr(principal, "subject", "")
    if not isinstance(subject, str) or not subject.strip():
        raise HTTPException(
            status_code=403,
            detail={
                "code": "DEVICE_OWNER_ACCESS_REQUIRED",
                "message": "请先通过电脑 Web 的 Cloudflare Access 再连接手机。",
            },
        )
    return subject


OwnerSubject = Annotated[str, Depends(require_cloudflare_owner)]
Service = Annotated[DeviceTrustService, Depends(get_device_trust_service)]


async def require_mobile_transport(
    request: Request,
    service: Service,
) -> DeviceTrustService:
    """绑定 dedicated HTTPS Host，并拒绝 Web Cookie/Cloudflare 机器凭证。"""

    _require_mobile_host(request, service=service, allow_device_authorization=False)
    return service


def _require_mobile_host(
    request: Request,
    *,
    service: DeviceTrustService,
    allow_device_authorization: bool,
) -> None:
    expected_host = service.mobile_origin.removeprefix("https://")
    if request.url.scheme != "https" or request.headers.get("host", "").lower() != expected_host:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "MOBILE_ROUTE_NOT_FOUND",
                "message": "这个地址不是手机连接入口。",
            },
        )
    cookie = request.headers.get("cookie", "")
    forbidden_headers = {
        "cf-access-client-id",
        "cf-access-client-secret",
        "cf-access-jwt-assertion",
        "cf-authorization",
    }
    authorization = request.headers.get("authorization", "")
    invalid_authorization = bool(authorization) and (
        not allow_device_authorization or not authorization.startswith("OctoDevice ")
    )
    if (
        "CF_Authorization=" in cookie
        or forbidden_headers.intersection(request.headers)
        or invalid_authorization
    ):
        raise HTTPException(
            status_code=401,
            detail={
                "code": "MOBILE_WEB_CREDENTIAL_REJECTED",
                "message": "手机连接不能使用 Web 登录或服务令牌。",
            },
        )


MobileService = Annotated[DeviceTrustService, Depends(require_mobile_transport)]


def _device_proof_headers(request: Request) -> DeviceProofHeaders:
    names = {
        "authorization": "Authorization",
        "timestamp": "X-Octo-Device-Timestamp",
        "nonce": "X-Octo-Device-Nonce",
        "signature": "X-Octo-Device-Signature",
    }
    values: dict[str, str] = {}
    for field, header_name in names.items():
        candidates = request.headers.getlist(header_name)
        if len(candidates) != 1:
            raise HTTPException(
                status_code=401,
                detail={
                    "code": "DEVICE_PROOF_HEADERS_INVALID",
                    "message": "设备证明信息缺失或重复。",
                },
            )
        values[field] = candidates[0]
    try:
        return DeviceProofHeaders.model_validate(values)
    except ValidationError as exc:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "DEVICE_PROOF_HEADERS_INVALID",
                "message": "设备证明格式不正确。",
            },
        ) from exc


async def _protected_route_context(
    request: Request,
    *,
    service: DeviceTrustService,
) -> tuple[DeviceProofHeaders, bytes]:
    _require_mobile_host(
        request,
        service=service,
        allow_device_authorization=True,
    )
    if request.url.query:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "DEVICE_REQUEST_PATH_INVALID",
                "message": "设备请求地址包含不允许的查询参数。",
            },
        )
    return _device_proof_headers(request), await request.body()


def _service_error(exc: DeviceTrustServiceError) -> HTTPException:
    messages = {
        "DEVICE_ATTESTATION_INVALID": "这台设备的真实性检查未通过。",
        "DEVICE_ATTESTATION_UNAVAILABLE": "设备真实性检查暂不可用，请稍后重试。",
        "DEVICE_MOBILE_ORIGIN_MISMATCH": "手机连接地址与配对信息不一致。",
        "DEVICE_NOT_FOUND": "没有找到这台设备。",
        "DEVICE_NOT_ACTIVE": "这台设备尚未批准或已经撤销。",
        "DEVICE_REGISTRATION_NOT_FOUND": "没有找到这次连接请求。",
        "DEVICE_REGISTRATION_NOT_CLAIMABLE": "这次连接请求已使用、已过期或不匹配。",
        "DEVICE_REQUEST_EXPIRED": "设备请求已过期，请重试。",
        "DEVICE_SIGNATURE_INVALID": "设备签名不正确。",
        "DEVICE_TOKEN_CHALLENGE_INVALID": "令牌请求已使用、已过期或不匹配。",
        "DEVICE_TOKEN_INVALID": "设备令牌无效或已经过期。",
        "REQUEST_REPLAYED": "这个设备请求已经使用过，请重试。",
        "DEVICE_OWNER_IDENTITY_INVALID": "当前 Web 身份无法用于连接设备。",
    }
    return HTTPException(
        status_code=exc.status_code,
        detail={
            "code": exc.reason_code,
            "message": messages.get(exc.reason_code, "设备连接请求未能完成。"),
        },
    )


@mobile_router.post(
    "/enrollments",
    response_model=DeviceEnrollmentStatusResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_mobile_enrollment(
    enrollment: DeviceEnrollmentRequest,
    service: MobileService,
) -> DeviceEnrollmentStatusResponse:
    try:
        return await service.submit_enrollment(enrollment)
    except DeviceTrustServiceError as exc:
        raise _service_error(exc) from exc


@mobile_router.post(
    "/token-challenges/{device_id}",
    response_model=DeviceTokenChallengeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_mobile_token_challenge(
    device_id: str,
    service: MobileService,
) -> DeviceTokenChallengeResponse:
    try:
        return await service.create_token_challenge(device_id=device_id)
    except DeviceTrustServiceError as exc:
        raise _service_error(exc) from exc


@mobile_router.post(
    "/tokens",
    response_model=DeviceTokenResponse,
    status_code=status.HTTP_201_CREATED,
)
async def issue_mobile_token(
    token_request: DeviceTokenRequest,
    service: MobileService,
) -> DeviceTokenResponse:
    try:
        return await service.issue_token(token_request)
    except DeviceTrustServiceError as exc:
        raise _service_error(exc) from exc


@mobile_router.get(
    "/ready",
    response_model=MobileReadyResponse,
)
async def mobile_ready(
    request: Request,
    service: Service,
) -> MobileReadyResponse:
    headers, raw_body = await _protected_route_context(request, service=service)
    try:
        return await service.protected_ready(
            headers=headers,
            method=request.method,
            canonical_path=request.url.path,
            raw_body=raw_body,
        )
    except DeviceTrustServiceError as exc:
        raise _service_error(exc) from exc


@mobile_router.get(
    "/device-profile",
    response_model=MobileDeviceProfileResponse,
)
async def mobile_device_profile(
    request: Request,
    service: Service,
) -> MobileDeviceProfileResponse:
    headers, raw_body = await _protected_route_context(request, service=service)
    try:
        return await service.protected_device_profile(
            headers=headers,
            method=request.method,
            canonical_path=request.url.path,
            raw_body=raw_body,
        )
    except DeviceTrustServiceError as exc:
        raise _service_error(exc) from exc


@router.post(
    "/challenges",
    response_model=OwnerRegistrationChallengeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_registration_challenge(
    owner_subject: OwnerSubject,
    service: Service,
) -> OwnerRegistrationChallengeResponse:
    try:
        return await service.create_registration_challenge(owner_subject=owner_subject)
    except DeviceTrustServiceError as exc:
        raise _service_error(exc) from exc


@router.get(
    "/challenges/{challenge_id}",
    response_model=DeviceEnrollmentStatusResponse,
)
async def get_registration_status(
    challenge_id: str,
    owner_subject: OwnerSubject,
    service: Service,
) -> DeviceEnrollmentStatusResponse:
    try:
        return await service.get_enrollment_status(
            owner_subject=owner_subject,
            challenge_id=challenge_id,
        )
    except DeviceTrustServiceError as exc:
        raise _service_error(exc) from exc


@router.post(
    "/challenges/{challenge_id}/approve",
    response_model=DeviceEnrollmentStatusResponse,
)
async def approve_registration(
    challenge_id: str,
    owner_subject: OwnerSubject,
    service: Service,
) -> DeviceEnrollmentStatusResponse:
    try:
        return await service.approve_registration(
            owner_subject=owner_subject,
            challenge_id=challenge_id,
        )
    except (DeviceTrustServiceError, ValueError) as exc:
        if isinstance(exc, DeviceTrustServiceError):
            raise _service_error(exc) from exc
        raise HTTPException(
            status_code=409,
            detail={
                "code": "DEVICE_REGISTRATION_NOT_APPROVABLE",
                "message": "这次连接请求当前不能批准，请重新发起。",
            },
        ) from exc


@router.post(
    "/challenges/{challenge_id}/reject",
    response_model=DeviceEnrollmentStatusResponse,
)
async def reject_registration(
    challenge_id: str,
    owner_subject: OwnerSubject,
    service: Service,
) -> DeviceEnrollmentStatusResponse:
    try:
        return await service.reject_registration(
            owner_subject=owner_subject,
            challenge_id=challenge_id,
        )
    except (DeviceTrustServiceError, ValueError) as exc:
        if isinstance(exc, DeviceTrustServiceError):
            raise _service_error(exc) from exc
        raise HTTPException(
            status_code=409,
            detail={
                "code": "DEVICE_REGISTRATION_NOT_REJECTABLE",
                "message": "这次连接请求当前不能拒绝。",
            },
        ) from exc


@router.get("/devices", response_model=list[OwnerDeviceProjection])
async def list_devices(
    owner_subject: OwnerSubject,
    service: Service,
) -> list[OwnerDeviceProjection]:
    return await service.list_devices(owner_subject=owner_subject)


@router.post(
    "/devices/{device_id}/revoke",
    response_model=OwnerDeviceProjection,
)
async def revoke_device(
    device_id: str,
    owner_subject: OwnerSubject,
    service: Service,
) -> OwnerDeviceProjection:
    try:
        return await service.revoke_device(
            owner_subject=owner_subject,
            device_id=device_id,
        )
    except (DeviceTrustServiceError, ValueError) as exc:
        if isinstance(exc, DeviceTrustServiceError):
            raise _service_error(exc) from exc
        raise HTTPException(
            status_code=409,
            detail={
                "code": "DEVICE_REVOKE_CONFLICT",
                "message": "这台设备已经撤销或无法撤销。",
            },
        ) from exc


__all__ = [
    "get_device_trust_service",
    "mobile_router",
    "require_cloudflare_owner",
    "require_mobile_transport",
    "router",
]
