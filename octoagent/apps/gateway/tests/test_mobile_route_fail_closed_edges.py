"""移动端路由的 fail-closed 边界回归。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from octoagent.gateway.routes import device_trust, mobile_health
from octoagent.gateway.services.device_trust import DeviceTrustServiceError
from starlette.requests import Request


def _request(*, state: SimpleNamespace | None = None) -> Request:
    app = SimpleNamespace(state=state or SimpleNamespace())
    scope: dict[str, Any] = {
        "type": "http",
        "app": app,
        "method": "POST",
        "scheme": "https",
        "path": "/api/mobile/v1/health/reviews",
        "raw_path": b"/api/mobile/v1/health/reviews",
        "query_string": b"",
        "headers": [],
        "server": ("mobile.example.test", 443),
        "client": ("127.0.0.1", 12345),
    }
    return Request(scope)


def test_mobile_health_dependencies_fail_closed_when_not_ready() -> None:
    request = _request()

    with pytest.raises(HTTPException) as health_error:
        mobile_health.get_health_ingestion_service(request)
    assert health_error.value.status_code == 503
    assert health_error.value.detail["code"] == "HEALTH_INGESTION_NOT_READY"

    with pytest.raises(HTTPException) as analysis_error:
        mobile_health.get_provider_router(request)
    assert analysis_error.value.status_code == 503
    assert analysis_error.value.detail["code"] == "HEALTH_ANALYSIS_NOT_READY"


@pytest.mark.asyncio
@pytest.mark.parametrize("raw_body", [b"\xff", b"[]"])
async def test_health_review_rejects_non_object_json(
    monkeypatch: pytest.MonkeyPatch,
    raw_body: bytes,
) -> None:
    async def protected_context(*_args: Any, **_kwargs: Any) -> tuple[object, bytes]:
        return object(), raw_body

    monkeypatch.setattr(mobile_health, "_protected_route_context", protected_context)

    with pytest.raises(HTTPException) as error:
        await mobile_health.submit_health_review(_request(), object(), object())  # type: ignore[arg-type]
    assert error.value.status_code == 422
    assert error.value.detail["code"] == "HEALTH_REVIEW_INVALID"


@pytest.mark.asyncio
async def test_health_delete_rejects_request_body(monkeypatch: pytest.MonkeyPatch) -> None:
    async def protected_context(*_args: Any, **_kwargs: Any) -> tuple[object, bytes]:
        return object(), b"unexpected"

    monkeypatch.setattr(mobile_health, "_protected_route_context", protected_context)

    with pytest.raises(HTTPException) as error:
        await mobile_health.delete_health_source(
            _request(),
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            "a" * 64,
        )
    assert error.value.status_code == 422
    assert error.value.detail["code"] == "HEALTH_DELETION_INCOMPLETE"


@pytest.mark.asyncio
async def test_key_rotation_challenge_maps_service_error() -> None:
    class RejectingService:
        async def create_key_rotation_challenge(self, *, device_id: str) -> None:
            assert device_id == "device-1"
            raise DeviceTrustServiceError("DEVICE_NOT_ACTIVE", status_code=409)

    with pytest.raises(HTTPException) as error:
        await device_trust.create_mobile_key_rotation_challenge(
            "device-1",
            RejectingService(),  # type: ignore[arg-type]
        )
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "DEVICE_NOT_ACTIVE"
