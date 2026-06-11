"""F105 v0.2 Phase B: Slack events 路由测试（FR-B4 status 映射）。

形态镜像 test_telegram_route.py：自建最小 app + stub service。
覆盖 US-2 AC-1（challenge 200）/ AC-4（401 映射）/ AC-5（user 级拒绝 200）。
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from octoagent.gateway.routes import slack


@dataclass
class FakeResult:
    status: str
    detail: str = ""
    task_id: str | None = None
    created: bool = False
    challenge: str | None = None


class FakeSlackService:
    def __init__(self, result: FakeResult) -> None:
        self._result = result
        self.calls: list[tuple[bytes, dict[str, str]]] = []

    async def handle_event_request(self, raw_body: bytes, headers) -> FakeResult:
        self.calls.append((raw_body, dict(headers)))
        return self._result


@pytest_asyncio.fixture
async def app() -> FastAPI:
    application = FastAPI()
    application.include_router(slack.router)
    return application


async def _post(app: FastAPI, body: bytes = b"{}") -> object:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post(
            "/api/slack/events",
            content=body,
            headers={"content-type": "application/json"},
        )


@pytest.mark.asyncio
async def test_service_unavailable_503(app: FastAPI) -> None:
    resp = await _post(app)
    assert resp.status_code == 503
    assert resp.json()["error"] == "slack_service_unavailable"


@pytest.mark.asyncio
async def test_url_verification_returns_challenge(app: FastAPI) -> None:
    app.state.slack_service = FakeSlackService(
        FakeResult(status="url_verification", challenge="ch-1")
    )
    resp = await _post(app)
    assert resp.status_code == 200
    assert resp.json() == {"challenge": "ch-1"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "http_code", "ok"),
    [
        ("accepted", 200, True),
        ("duplicate", 200, True),
        ("ignored", 200, True),
        ("unauthorized", 200, False),  # user 级拒绝回 2xx（Slack retry 语义）
        ("signature_invalid", 401, False),
        ("timestamp_stale", 401, False),
        ("blocked", 403, False),
        ("disabled", 503, False),
    ],
)
async def test_status_mapping(
    app: FastAPI, status: str, http_code: int, ok: bool
) -> None:
    app.state.slack_service = FakeSlackService(
        FakeResult(status=status, task_id="task-1" if ok else None)
    )
    resp = await _post(app)
    assert resp.status_code == http_code
    payload = resp.json()
    assert payload["ok"] is ok
    assert payload["status"] == status


@pytest.mark.asyncio
async def test_raw_body_and_headers_passthrough(app: FastAPI) -> None:
    """验签依赖 raw body 原始字节——route 必须透传不重序列化。"""
    service = FakeSlackService(FakeResult(status="ignored"))
    app.state.slack_service = service
    raw = b'{"type":"event_callback",  "spacing": "preserved"}'
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            "/api/slack/events",
            content=raw,
            headers={
                "content-type": "application/json",
                "X-Slack-Signature": "v0=abc",
                "X-Slack-Request-Timestamp": "123",
            },
        )
    body, headers = service.calls[0]
    assert body == raw  # 字节级透传
    assert headers.get("x-slack-signature") == "v0=abc"
