from __future__ import annotations

import json
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient


@pytest_asyncio.fixture
async def frontdoor_app(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OCTOAGENT_DB_PATH", str(tmp_path / "data" / "sqlite" / "test.db"))
    monkeypatch.setenv("OCTOAGENT_ARTIFACTS_DIR", str(tmp_path / "data" / "artifacts"))
    monkeypatch.setenv("OCTOAGENT_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("OCTOAGENT_LLM_MODE", "echo")
    monkeypatch.setenv("LOGFIRE_SEND_TO_LOGFIRE", "false")

    from octoagent.gateway.main import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        yield app


class TestFrontDoorAuth:
    async def test_loopback_mode_allows_local_client(self, frontdoor_app) -> None:
        async with AsyncClient(
            transport=ASGITransport(app=frontdoor_app),
            base_url="http://test",
        ) as client:
            resp = await client.get("/api/control/snapshot")

        assert resp.status_code == 200

    async def test_loopback_mode_rejects_non_loopback_client(self, frontdoor_app) -> None:
        async with AsyncClient(
            transport=ASGITransport(app=frontdoor_app, client=("203.0.113.10", 123)),
            base_url="http://test",
        ) as client:
            resp = await client.get("/api/control/snapshot")

        assert resp.status_code == 403
        payload = resp.json()
        assert payload["detail"]["code"] == "FRONT_DOOR_LOOPBACK_ONLY"

    async def test_bearer_mode_requires_token(
        self,
        frontdoor_app,
        monkeypatch,
    ) -> None:
        monkeypatch.setenv("OCTOAGENT_FRONTDOOR_MODE", "bearer")
        monkeypatch.setenv("OCTOAGENT_FRONTDOOR_TOKEN", "frontdoor-secret")

        async with AsyncClient(
            transport=ASGITransport(app=frontdoor_app),
            base_url="http://test",
        ) as client:
            resp = await client.get("/api/control/snapshot")

        assert resp.status_code == 401
        assert resp.headers["www-authenticate"] == "Bearer"
        payload = resp.json()
        assert payload["detail"]["code"] == "FRONT_DOOR_TOKEN_REQUIRED"

    async def test_bearer_mode_accepts_valid_token(
        self,
        frontdoor_app,
        monkeypatch,
    ) -> None:
        monkeypatch.setenv("OCTOAGENT_FRONTDOOR_MODE", "bearer")
        monkeypatch.setenv("OCTOAGENT_FRONTDOOR_TOKEN", "frontdoor-secret")

        async with AsyncClient(
            transport=ASGITransport(app=frontdoor_app),
            base_url="http://test",
        ) as client:
            resp = await client.get(
                "/api/control/snapshot",
                headers={"Authorization": "Bearer frontdoor-secret"},
            )

        assert resp.status_code == 200

    async def test_trusted_proxy_mode_requires_proxy_header(
        self,
        frontdoor_app,
        monkeypatch,
    ) -> None:
        monkeypatch.setenv("OCTOAGENT_FRONTDOOR_MODE", "trusted_proxy")
        monkeypatch.setenv("OCTOAGENT_TRUSTED_PROXY_TOKEN", "proxy-secret")
        monkeypatch.setenv("OCTOAGENT_TRUSTED_PROXY_CIDRS", "10.0.0.0/24")

        async with AsyncClient(
            transport=ASGITransport(app=frontdoor_app, client=("10.0.0.8", 123)),
            base_url="http://test",
        ) as client:
            resp = await client.get("/api/control/snapshot")

        assert resp.status_code == 403
        payload = resp.json()
        assert payload["detail"]["code"] == "FRONT_DOOR_PROXY_TOKEN_REQUIRED"

    async def test_trusted_proxy_mode_accepts_shared_header(
        self,
        frontdoor_app,
        monkeypatch,
    ) -> None:
        monkeypatch.setenv("OCTOAGENT_FRONTDOOR_MODE", "trusted_proxy")
        monkeypatch.setenv("OCTOAGENT_TRUSTED_PROXY_TOKEN", "proxy-secret")
        monkeypatch.setenv("OCTOAGENT_TRUSTED_PROXY_CIDRS", "10.0.0.0/24")

        async with AsyncClient(
            transport=ASGITransport(app=frontdoor_app, client=("10.0.0.8", 123)),
            base_url="http://test",
        ) as client:
            resp = await client.get(
                "/api/control/snapshot",
                headers={"X-OctoAgent-Proxy-Auth": "proxy-secret"},
            )

        assert resp.status_code == 200

    async def test_bearer_mode_allows_sse_query_token(
        self,
        frontdoor_app,
        monkeypatch,
    ) -> None:
        monkeypatch.setenv("OCTOAGENT_FRONTDOOR_MODE", "bearer")
        monkeypatch.setenv("OCTOAGENT_FRONTDOOR_TOKEN", "frontdoor-secret")

        async with AsyncClient(
            transport=ASGITransport(app=frontdoor_app),
            base_url="http://test",
        ) as client:
            create_resp = await client.post(
                "/api/message",
                headers={"Authorization": "Bearer frontdoor-secret"},
                json={
                    "text": "front-door sse",
                    "idempotency_key": "frontdoor-sse-001",
                },
            )
            assert create_resp.status_code == 201
            task_id = create_resp.json()["task_id"]

            async with client.stream(
                "GET",
                f"/api/stream/task/{task_id}?access_token=frontdoor-secret",
            ) as stream_resp:
                assert stream_resp.status_code == 200
                async for line in stream_resp.aiter_lines():
                    if line.startswith("data:"):
                        payload = json.loads(line[len("data:") :].strip())
                        assert payload["task_id"] == task_id
                        break
                else:
                    raise AssertionError("SSE 未返回历史事件")
