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


# ---------------------------------------------------------------------------
# F134 交付①：认证失败限流矩阵（spec §1，既有 17 格零触碰只扩不删）
#
# 语义（D1 verify-first）：只有「带了凭证但验证失败」计数；正确凭证恒放行
# 并清计数（serve 场景全远程共享 127.0.0.1 桶，锁定式会 DoS 唯一用户）；
# 缺凭证（SPA 首屏裸请求）不计数。阈值 10 次/60s 窗 → lockout 300s。
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def rate_limit_guard_app(tmp_path: Path):
    """限流矩阵专用轻量 app：同款 guard 聚焦范式 + 暴露 guard 引用。

    guard 实例暴露给测试以便注入 fake clock（AC-R4，无 sleep）；每个测试
    新建 guard → 限流内存态天然隔离（plan §风险）。
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

    return app, guard


def _set_bearer_env(monkeypatch) -> None:
    monkeypatch.setenv("OCTOAGENT_FRONTDOOR_MODE", "bearer")
    monkeypatch.setenv("OCTOAGENT_FRONTDOOR_TOKEN", "matrix-secret")


_WRONG_AUTH = {"Authorization": "Bearer wrong-token"}
_RIGHT_AUTH = {"Authorization": "Bearer matrix-secret"}
#: 与 frontdoor_auth._RATE_LIMIT_MAX_FAILURES 同值（阈值变更时本常量先红）。
_THRESHOLD = 10


class TestFrontDoorRateLimitMatrix:
    """FR-1a..1g：限流决策表矩阵（F134 spec §4 AC-R1..R8）。"""

    async def test_threshold_constant_pinned(self) -> None:
        from octoagent.gateway.services import frontdoor_auth

        assert frontdoor_auth._RATE_LIMIT_MAX_FAILURES == _THRESHOLD, (
            "限流阈值变更——请同步矩阵格子的次数假设"
        )

    # ---- AC-R1：错 token 达阈值 → 429 + Retry-After ----

    async def test_bearer_wrong_token_locks_out_at_threshold(
        self, rate_limit_guard_app, monkeypatch
    ) -> None:
        app, _guard = rate_limit_guard_app
        _set_bearer_env(monkeypatch)

        async with _client(app) as client:
            for _ in range(_THRESHOLD - 1):
                resp = await client.get("/api/probe", headers=_WRONG_AUTH)
                assert resp.status_code == 401
                assert resp.json()["detail"]["code"] == "FRONT_DOOR_TOKEN_INVALID"

            resp = await client.get("/api/probe", headers=_WRONG_AUTH)

        assert resp.status_code == 429
        assert resp.json()["detail"]["code"] == "FRONT_DOOR_RATE_LIMITED"
        retry_after = int(resp.headers["retry-after"])
        assert 1 <= retry_after <= 300

    # ---- AC-R2：lockout 中正确 token 恒放行 + 清计数 ----

    async def test_correct_token_passes_during_lockout_and_resets(
        self, rate_limit_guard_app, monkeypatch
    ) -> None:
        app, _guard = rate_limit_guard_app
        _set_bearer_env(monkeypatch)

        async with _client(app) as client:
            for _ in range(_THRESHOLD):
                await client.get("/api/probe", headers=_WRONG_AUTH)
            locked = await client.get("/api/probe", headers=_WRONG_AUTH)
            assert locked.status_code == 429

            ok = await client.get("/api/probe", headers=_RIGHT_AUTH)
            assert ok.status_code == 200

            # reset 后错 token 回到正常 401（计数从零重新累计）
            after = await client.get("/api/probe", headers=_WRONG_AUTH)

        assert after.status_code == 401
        assert after.json()["detail"]["code"] == "FRONT_DOOR_TOKEN_INVALID"

    # ---- AC-R3：缺凭证不计数不升级 ----

    async def test_missing_token_never_counted_nor_upgraded(
        self, rate_limit_guard_app, monkeypatch
    ) -> None:
        app, _guard = rate_limit_guard_app
        _set_bearer_env(monkeypatch)

        async with _client(app) as client:
            for _ in range(_THRESHOLD + 2):
                resp = await client.get("/api/probe")
                assert resp.status_code == 401
                assert resp.json()["detail"]["code"] == "FRONT_DOOR_TOKEN_REQUIRED"

            # 裸请求刷了 12 次后，第一次带错凭证仍是 401（证明未被计数）
            resp = await client.get("/api/probe", headers=_WRONG_AUTH)

        assert resp.status_code == 401
        assert resp.json()["detail"]["code"] == "FRONT_DOOR_TOKEN_INVALID"

    # ---- AC-R4：lockout 到期恢复（注入 clock 无 sleep）----

    async def test_lockout_expires_with_clock(
        self, rate_limit_guard_app, monkeypatch
    ) -> None:
        from octoagent.gateway.services.frontdoor_auth import _FailureRateLimiter

        app, guard = rate_limit_guard_app
        _set_bearer_env(monkeypatch)

        now = {"t": 1000.0}
        guard._rate_limiter = _FailureRateLimiter(clock=lambda: now["t"])

        async with _client(app) as client:
            for _ in range(_THRESHOLD):
                await client.get("/api/probe", headers=_WRONG_AUTH)
            locked = await client.get("/api/probe", headers=_WRONG_AUTH)
            assert locked.status_code == 429

            now["t"] += 301.0
            resp = await client.get("/api/probe", headers=_WRONG_AUTH)

        assert resp.status_code == 401
        assert resp.json()["detail"]["code"] == "FRONT_DOOR_TOKEN_INVALID"

    # ---- AC-R5：SSE query 错 token 同源同计数 ----

    async def test_sse_query_wrong_token_shares_counter(
        self, rate_limit_guard_app, monkeypatch
    ) -> None:
        app, _guard = rate_limit_guard_app
        _set_bearer_env(monkeypatch)

        async with _client(app) as client:
            for _ in range(_THRESHOLD - 1):
                resp = await client.get("/api/stream/probe?access_token=wrong-token")
                assert resp.status_code == 401

            resp = await client.get("/api/stream/probe?access_token=wrong-token")
            assert resp.status_code == 429

            # 正确 query token 在 lockout 中仍放行（FR-1b 覆盖 SSE 路径）
            ok = await client.get("/api/stream/probe?access_token=matrix-secret")

        assert ok.status_code == 200

    # ---- AC-R6：trusted_proxy 错共享 header 同样限流 ----

    async def test_trusted_proxy_wrong_header_locks_out(
        self, rate_limit_guard_app, monkeypatch
    ) -> None:
        app, _guard = rate_limit_guard_app
        monkeypatch.setenv("OCTOAGENT_FRONTDOOR_MODE", "trusted_proxy")
        monkeypatch.setenv("OCTOAGENT_TRUSTED_PROXY_TOKEN", "proxy-secret")
        monkeypatch.setenv("OCTOAGENT_TRUSTED_PROXY_CIDRS", "10.0.0.0/24")

        async with _client(app, client_ip="10.0.0.8") as client:
            for _ in range(_THRESHOLD - 1):
                resp = await client.get(
                    "/api/probe", headers={"X-OctoAgent-Proxy-Auth": "wrong-secret"}
                )
                assert resp.status_code == 403
                assert resp.json()["detail"]["code"] == "FRONT_DOOR_PROXY_TOKEN_INVALID"

            resp = await client.get(
                "/api/probe", headers={"X-OctoAgent-Proxy-Auth": "wrong-secret"}
            )
            assert resp.status_code == 429
            # Codex 十四轮 P2：proxy 侧限流 code 与 bearer 版区分（前端据此
            # 归 trusted_proxy 指引而非 bearer token 输入框）
            assert resp.json()["detail"]["code"] == "FRONT_DOOR_PROXY_RATE_LIMITED"

            ok = await client.get(
                "/api/probe", headers={"X-OctoAgent-Proxy-Auth": "proxy-secret"}
            )

        assert ok.status_code == 200

    # ---- AC-R7：loopback 模式不接限流（无凭证可爆破）----

    async def test_loopback_mode_never_upgrades_to_429(
        self, rate_limit_guard_app
    ) -> None:
        app, _guard = rate_limit_guard_app

        async with _client(app, client_ip="203.0.113.10") as client:
            for _ in range(_THRESHOLD + 2):
                resp = await client.get("/api/probe")
                assert resp.status_code == 403
                assert resp.json()["detail"]["code"] == "FRONT_DOOR_LOOPBACK_ONLY"

    # ---- AC-R8：双源桶隔离 ----

    async def test_sources_are_isolated(
        self, rate_limit_guard_app, monkeypatch
    ) -> None:
        app, _guard = rate_limit_guard_app
        _set_bearer_env(monkeypatch)

        async with _client(app, client_ip="198.51.100.7") as client_a:
            for _ in range(_THRESHOLD):
                await client_a.get("/api/probe", headers=_WRONG_AUTH)
            locked = await client_a.get("/api/probe", headers=_WRONG_AUTH)
            assert locked.status_code == 429

        async with _client(app, client_ip="198.51.100.8") as client_b:
            resp = await client_b.get("/api/probe", headers=_WRONG_AUTH)

        assert resp.status_code == 401
        assert resp.json()["detail"]["code"] == "FRONT_DOOR_TOKEN_INVALID"


class TestFailureRateLimiterUnit:
    """AC-L1：`_FailureRateLimiter` 纯单元（fake clock，无 HTTP）。"""

    def _limiter(self, now: dict[str, float], **kwargs):
        from octoagent.gateway.services.frontdoor_auth import _FailureRateLimiter

        return _FailureRateLimiter(clock=lambda: now["t"], **kwargs)

    def test_window_slides_failures_out(self) -> None:
        now = {"t": 0.0}
        limiter = self._limiter(now)
        for _ in range(9):
            assert limiter.record_failure("src") is None
        # 窗口滑走旧失败：61s 后再 9 次也不触发 lockout
        now["t"] = 61.0
        for _ in range(9):
            assert limiter.record_failure("src") is None

    def test_lockout_triggers_and_reports_remaining(self) -> None:
        now = {"t": 0.0}
        limiter = self._limiter(now)
        for _ in range(9):
            assert limiter.record_failure("src") is None
        assert limiter.record_failure("src") == 300.0
        now["t"] = 100.0
        assert limiter.record_failure("src") == 200.0

    def test_reset_clears_state(self) -> None:
        now = {"t": 0.0}
        limiter = self._limiter(now)
        for _ in range(10):
            limiter.record_failure("src")
        limiter.reset("src")
        assert limiter.record_failure("src") is None

    def test_lockout_expiry_restarts_counting(self) -> None:
        now = {"t": 0.0}
        limiter = self._limiter(now)
        for _ in range(10):
            limiter.record_failure("src")
        now["t"] = 300.1
        assert limiter.record_failure("src") is None

    def test_max_entries_evicts_oldest_unlocked(self) -> None:
        now = {"t": 0.0}
        limiter = self._limiter(now, max_entries=3)
        assert limiter.record_failure("a") is None
        assert limiter.record_failure("b") is None
        assert limiter.record_failure("c") is None
        # 表满：新源 d 入表逐出最旧未锁定条目（a），d 正常计数
        assert limiter.record_failure("d") is None
        assert "a" not in limiter._entries
        assert "d" in limiter._entries

    def test_full_table_of_locked_entries_admits_nothing(self) -> None:
        now = {"t": 0.0}
        limiter = self._limiter(now, max_entries=2)
        for src in ("a", "b"):
            for _ in range(10):
                limiter.record_failure(src)
        # 全员 lockout：新源不建条目也不升级（可用性优先，不误伤）
        assert limiter.record_failure("c") is None
        assert "c" not in limiter._entries

    def test_stale_entries_pruned_on_admission(self) -> None:
        now = {"t": 0.0}
        limiter = self._limiter(now, max_entries=2)
        limiter.record_failure("a")
        limiter.record_failure("b")
        now["t"] = 61.0  # a/b 的失败都已滑出窗口
        assert limiter.record_failure("c") is None
        assert "c" in limiter._entries
