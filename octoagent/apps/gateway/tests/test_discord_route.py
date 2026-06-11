"""F105 v0.2 Phase C: Discord interactions 路由测试（FR-C4 status 映射）。

覆盖 US-3 AC-1（PING→200 PONG）/ AC-2（验签失败必 401——Discord 端点注册
探测硬要求）+ 交互应答一律 200 + response_payload 透传。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from octoagent.gateway.routes import discord


@dataclass
class FakeResult:
    status: str
    detail: str = ""
    task_id: str | None = None
    created: bool = False
    response_payload: dict[str, Any] | None = field(default=None)


class FakeDiscordService:
    def __init__(self, result: FakeResult) -> None:
        self._result = result
        self.calls: list[bytes] = []

    async def handle_interaction_request(self, raw_body: bytes, headers) -> FakeResult:
        self.calls.append(raw_body)
        return self._result


@pytest_asyncio.fixture
async def app() -> FastAPI:
    application = FastAPI()
    application.include_router(discord.router)
    return application


async def _post(app: FastAPI, body: bytes = b"{}") -> object:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post(
            "/api/discord/interactions",
            content=body,
            headers={"content-type": "application/json"},
        )


@pytest.mark.asyncio
async def test_service_unavailable_503(app: FastAPI) -> None:
    resp = await _post(app)
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_ping_pong(app: FastAPI) -> None:
    app.state.discord_service = FakeDiscordService(
        FakeResult(status="pong", response_payload={"type": 1})
    )
    resp = await _post(app)
    assert resp.status_code == 200
    assert resp.json() == {"type": 1}


@pytest.mark.asyncio
async def test_invalid_signature_401(app: FastAPI) -> None:
    """US-3 AC-2：验签失败必须 401（Discord 注册探测）。"""
    app.state.discord_service = FakeDiscordService(
        FakeResult(status="signature_invalid", detail="ed25519_verification_failed")
    )
    resp = await _post(app)
    assert resp.status_code == 401
    assert resp.json()["ok"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "http_code"),
    [("blocked", 403), ("disabled", 503)],
)
async def test_operational_statuses(app: FastAPI, status: str, http_code: int) -> None:
    app.state.discord_service = FakeDiscordService(FakeResult(status=status))
    resp = await _post(app)
    assert resp.status_code == http_code


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["accepted", "duplicate", "unauthorized", "unsupported", "ignored"])
async def test_interaction_responses_200_with_payload(app: FastAPI, status: str) -> None:
    """交互应答一律 200 + interaction response 包体透传（用户级拒绝走
    ephemeral 文案而非传输层 4xx）。"""
    payload = {"type": 4, "data": {"content": "回应文案"}}
    app.state.discord_service = FakeDiscordService(
        FakeResult(status=status, response_payload=payload)
    )
    resp = await _post(app)
    assert resp.status_code == 200
    assert resp.json() == payload
