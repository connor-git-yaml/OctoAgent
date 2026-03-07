"""Telegram webhook 路由测试。"""

from __future__ import annotations

from dataclasses import dataclass

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from octoagent.gateway.routes import telegram


@dataclass
class FakeResult:
    status: str
    detail: str = ""
    task_id: str | None = None
    created: bool = False


class FakeTelegramService:
    def __init__(self, result: FakeResult) -> None:
        self._result = result
        self.calls: list[tuple[dict[str, object], str]] = []

    async def handle_webhook_update(
        self,
        body: dict[str, object],
        *,
        secret_token: str = "",
    ) -> FakeResult:
        self.calls.append((body, secret_token))
        return self._result


@pytest_asyncio.fixture
async def app() -> FastAPI:
    application = FastAPI()
    application.include_router(telegram.router)
    return application


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


async def test_webhook_forwards_body_and_secret(app: FastAPI, client: AsyncClient) -> None:
    service = FakeTelegramService(
        FakeResult(status="accepted", task_id="01TESTTASK0000000000000000", created=True)
    )
    app.state.telegram_service = service

    response = await client.post(
        "/api/telegram/webhook",
        json={"update_id": 1, "message": {"text": "hello"}},
        headers={"X-Telegram-Bot-Api-Secret-Token": "secret-123"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "status": "accepted",
        "task_id": "01TESTTASK0000000000000000",
        "created": True,
    }
    assert service.calls == [
        (
            {"update_id": 1, "message": {"text": "hello"}},
            "secret-123",
        )
    ]


async def test_webhook_maps_pairing_required_to_202(
    app: FastAPI,
    client: AsyncClient,
) -> None:
    app.state.telegram_service = FakeTelegramService(
        FakeResult(status="pairing_required", detail="ABC123")
    )

    response = await client.post("/api/telegram/webhook", json={"update_id": 2})

    assert response.status_code == 202
    assert response.json() == {
        "ok": False,
        "status": "pairing_required",
        "detail": "ABC123",
        "created": False,
    }
