from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from octoagent.gateway.services.frontdoor_auth import _PROXY_HINT_HEADERS


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

    async def test_loopback_mode_rejects_proxy_forwarding_headers(self, frontdoor_app) -> None:
        async with AsyncClient(
            transport=ASGITransport(app=frontdoor_app),
            base_url="http://test",
        ) as client:
            resp = await client.get(
                "/api/control/snapshot",
                headers={"X-Forwarded-For": "203.0.113.10"},
            )

        assert resp.status_code == 403
        payload = resp.json()
        assert payload["detail"]["code"] == "FRONT_DOOR_LOOPBACK_PROXY_REJECTED"

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


# ---------------------------------------------------------------------------
# F144 交付①：mode × header L3 矩阵补格（吸收 F130 AC-1 语义半边）
#
# 上面的全栈用例（full create_app + lifespan）继续守「guard 挂在真实路由树」
# 的 wiring 层；本矩阵用 guard 聚焦轻量 app（无 lifespan/DB，每格 L4 速度）
# 补 FrontDoorGuard 决策表缺格——尤其「serve 注入转发头时 bearer 放行」此前
# 只有 F130 completion-report §2 的人工复核记录，零机械格。
# ---------------------------------------------------------------------------

#: 每个 proxy hint header 的拟真样例值（Tailscale serve / 反代注入形态）。
#: guard 只判断「非空」，值形态不影响判定——拟真只为可读性。
_PROXY_HEADER_SAMPLES = {
    "forwarded": "for=203.0.113.10;proto=https",
    "x-forwarded-for": "203.0.113.10",
    "x-forwarded-host": "octo.tailnet.ts.net",
    "x-forwarded-proto": "https",
    "x-real-ip": "203.0.113.10",
}


def _sample_header(name: str) -> dict[str, str]:
    return {name: _PROXY_HEADER_SAMPLES.get(name, "attest-matrix-sample")}


@pytest_asyncio.fixture
async def guard_app(tmp_path: Path):
    """guard 聚焦轻量 app：仅 FrontDoorGuard + 两条探针路由（无 lifespan/DB）。

    mode / token 等经既有 env override 机制（``OCTOAGENT_FRONTDOOR_*``）由各
    用例 monkeypatch——与全栈 fixture 同一条 config 解析路径。
    """
    from fastapi import Depends, FastAPI
    from octoagent.gateway.services.frontdoor_auth import FrontDoorGuard

    guard = FrontDoorGuard(tmp_path)
    app = FastAPI(dependencies=[Depends(guard.authorize)])

    @app.get("/api/probe")
    async def probe() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/stream/probe")
    async def stream_probe() -> dict[str, bool]:
        return {"ok": True}

    return app


def _client(app, client_ip: str = "127.0.0.1") -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app, client=(client_ip, 12345)),
        base_url="http://test",
    )


class TestFrontDoorModeHeaderMatrix:
    """FR-A：mode × proxy-hint-header × credential 决策表矩阵（17 格）。"""

    async def test_proxy_hint_header_list_pinned(self) -> None:
        """契约钉住：矩阵按 ``_PROXY_HINT_HEADERS`` 参数化——生产新增 header
        时本断言先红，提醒补 ``_PROXY_HEADER_SAMPLES`` 样例并有意识扩格。"""
        assert set(_PROXY_HINT_HEADERS) == set(_PROXY_HEADER_SAMPLES), (
            "frontdoor_auth._PROXY_HINT_HEADERS 变更——请同步矩阵样例与格子数"
        )

    # ---- A1：loopback × 5 header 逐个（此前仅 x-forwarded-for 一格）----

    @pytest.mark.parametrize("header_name", _PROXY_HINT_HEADERS)
    async def test_loopback_rejects_each_proxy_hint_header(
        self, guard_app, header_name: str
    ) -> None:
        async with _client(guard_app) as client:
            resp = await client.get("/api/probe", headers=_sample_header(header_name))

        assert resp.status_code == 403
        assert resp.json()["detail"]["code"] == "FRONT_DOOR_LOOPBACK_PROXY_REJECTED"

    # ---- A2：bearer + 正确 token × 5 header 逐个（核心缺格：serve+bearer 可行）----

    @pytest.mark.parametrize("header_name", _PROXY_HINT_HEADERS)
    async def test_bearer_valid_token_ignores_each_proxy_hint_header(
        self, guard_app, monkeypatch, header_name: str
    ) -> None:
        """F130 §0.2 硬约束的机械化：bearer 分支不检查任何转发头——
        Tailscale serve 注入 X-Forwarded-* 时手机访问仍放行。"""
        monkeypatch.setenv("OCTOAGENT_FRONTDOOR_MODE", "bearer")
        monkeypatch.setenv("OCTOAGENT_FRONTDOOR_TOKEN", "matrix-secret")

        async with _client(guard_app) as client:
            resp = await client.get(
                "/api/probe",
                headers={
                    "Authorization": "Bearer matrix-secret",
                    **_sample_header(header_name),
                },
            )

        assert resp.status_code == 200

    # ---- A3：bearer 错 token（verdict 由 token 决定，不受转发头扰动）----

    @pytest.mark.parametrize("with_forward_header", [False, True])
    async def test_bearer_wrong_token_rejected_regardless_of_forward_header(
        self, guard_app, monkeypatch, with_forward_header: bool
    ) -> None:
        monkeypatch.setenv("OCTOAGENT_FRONTDOOR_MODE", "bearer")
        monkeypatch.setenv("OCTOAGENT_FRONTDOOR_TOKEN", "matrix-secret")
        headers = {"Authorization": "Bearer wrong-token"}
        if with_forward_header:
            headers.update(_sample_header("x-forwarded-for"))

        async with _client(guard_app) as client:
            resp = await client.get("/api/probe", headers=headers)

        assert resp.status_code == 401
        assert resp.json()["detail"]["code"] == "FRONT_DOOR_TOKEN_INVALID"

    # ---- A4：trusted_proxy 源 IP 不在 CIDR ----

    async def test_trusted_proxy_rejects_client_outside_cidr(
        self, guard_app, monkeypatch
    ) -> None:
        monkeypatch.setenv("OCTOAGENT_FRONTDOOR_MODE", "trusted_proxy")
        monkeypatch.setenv("OCTOAGENT_TRUSTED_PROXY_TOKEN", "proxy-secret")
        monkeypatch.setenv("OCTOAGENT_TRUSTED_PROXY_CIDRS", "10.0.0.0/24")

        async with _client(guard_app, client_ip="203.0.113.10") as client:
            resp = await client.get(
                "/api/probe",
                headers={"X-OctoAgent-Proxy-Auth": "proxy-secret"},
            )

        assert resp.status_code == 403
        assert resp.json()["detail"]["code"] == "FRONT_DOOR_TRUSTED_PROXY_REQUIRED"

    # ---- A5：trusted_proxy 在 CIDR 内 + 错共享 header 值 ----

    async def test_trusted_proxy_rejects_wrong_shared_header_value(
        self, guard_app, monkeypatch
    ) -> None:
        monkeypatch.setenv("OCTOAGENT_FRONTDOOR_MODE", "trusted_proxy")
        monkeypatch.setenv("OCTOAGENT_TRUSTED_PROXY_TOKEN", "proxy-secret")
        monkeypatch.setenv("OCTOAGENT_TRUSTED_PROXY_CIDRS", "10.0.0.0/24")

        async with _client(guard_app, client_ip="10.0.0.8") as client:
            resp = await client.get(
                "/api/probe",
                headers={"X-OctoAgent-Proxy-Auth": "wrong-secret"},
            )

        assert resp.status_code == 403
        assert resp.json()["detail"]["code"] == "FRONT_DOOR_PROXY_TOKEN_INVALID"

    # ---- A6/A7：SSE 路径 query token × XFF（serve 场景 SSE 半边）----

    async def test_bearer_sse_query_token_accepted_with_forward_header(
        self, guard_app, monkeypatch
    ) -> None:
        monkeypatch.setenv("OCTOAGENT_FRONTDOOR_MODE", "bearer")
        monkeypatch.setenv("OCTOAGENT_FRONTDOOR_TOKEN", "matrix-secret")

        async with _client(guard_app) as client:
            resp = await client.get(
                "/api/stream/probe?access_token=matrix-secret",
                headers=_sample_header("x-forwarded-for"),
            )

        assert resp.status_code == 200

    async def test_bearer_sse_wrong_query_token_rejected_with_forward_header(
        self, guard_app, monkeypatch
    ) -> None:
        monkeypatch.setenv("OCTOAGENT_FRONTDOOR_MODE", "bearer")
        monkeypatch.setenv("OCTOAGENT_FRONTDOOR_TOKEN", "matrix-secret")

        async with _client(guard_app) as client:
            resp = await client.get(
                "/api/stream/probe?access_token=wrong-token",
                headers=_sample_header("x-forwarded-for"),
            )

        assert resp.status_code == 401
        assert resp.json()["detail"]["code"] == "FRONT_DOOR_TOKEN_INVALID"

    # ---- A8：query token 只在 /api/stream/ 路径有效 ----

    async def test_bearer_query_token_not_accepted_on_non_stream_path(
        self, guard_app, monkeypatch
    ) -> None:
        monkeypatch.setenv("OCTOAGENT_FRONTDOOR_MODE", "bearer")
        monkeypatch.setenv("OCTOAGENT_FRONTDOOR_TOKEN", "matrix-secret")

        async with _client(guard_app) as client:
            resp = await client.get("/api/probe?access_token=matrix-secret")

        assert resp.status_code == 401
        assert resp.json()["detail"]["code"] == "FRONT_DOOR_TOKEN_REQUIRED"
