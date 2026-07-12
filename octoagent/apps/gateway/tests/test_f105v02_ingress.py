"""F105 v0.2 Phase A: ingress 契约测试。

覆盖 spec v0.2 US-1 AC-3（同 router 经 adapter 挂载等价）/ US-1 AC-4
（未 bootstrap 的 app 上 webhook 404——R1 唯一语义差异归档）/ SC-4
（harness 挂载循环集成证明）/ FR-A2（web adapter 无 HTTP inbound）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from octoagent.gateway.channels.telegram_adapter import TelegramChannelAdapter
from octoagent.gateway.channels.web_adapter import WebChannelAdapter
from octoagent.gateway.routes import telegram as telegram_routes


@dataclass
class _FakeResult:
    status: str
    detail: str = ""
    task_id: str | None = None
    created: bool = False


class _FakeTelegramService:
    """最小 stub（与 test_telegram_route.py 同形态，不跨文件 import）。"""

    def __init__(self, result: _FakeResult) -> None:
        self._result = result
        self.calls: list[tuple[dict[str, object], str]] = []

    async def handle_webhook_update(
        self, body: dict[str, object], *, secret_token: str = ""
    ) -> _FakeResult:
        self.calls.append((body, secret_token))
        return self._result


def test_telegram_adapter_returns_routes_router_identity() -> None:
    """EQ-A1 最强形式：adapter 返回的就是 routes/telegram.py 模块单例 router。"""
    adapter = TelegramChannelAdapter(object())
    assert adapter.inbound_router() is telegram_routes.router


def test_web_adapter_inbound_router_none() -> None:
    """FR-A2：web inbound 是 front-door 保护的产品 API 面，不进 ingress 契约。"""
    adapter = WebChannelAdapter(object())
    assert adapter.inbound_router() is None


@pytest.mark.asyncio
async def test_telegram_webhook_via_adapter_router_equals_baseline() -> None:
    """US-1 AC-3：经 adapter.inbound_router() 挂载后，webhook 响应字段与
    baseline（直挂 routes.telegram.router）逐一相等。"""
    service = _FakeTelegramService(
        _FakeResult(status="accepted", task_id="task-1", created=True)
    )
    application = FastAPI()
    router = TelegramChannelAdapter(object()).inbound_router()
    assert router is not None
    application.include_router(router)
    application.state.telegram_service = service

    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/telegram/webhook",
            json={"update_id": 1},
            headers={"X-Telegram-Bot-Api-Secret-Token": "sek"},
        )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload == {
        "ok": True,
        "status": "accepted",
        "task_id": "task-1",
        "created": True,
    }
    assert service.calls == [({"update_id": 1}, "sek")]


def _patch_no_frontend_dist(monkeypatch: pytest.MonkeyPatch) -> None:
    """把 SPA 挂载的单一事实源 `_resolve_frontend_dist` patch 成「无 dist」，
    使 create_app + lifespan 确定性地不挂 Mount("/")——让 bootstrapped ingress
    测试与宿主机 `frontend/dist` 状态解耦，聚焦「挂载循环真实执行」这一意图。

    背景：F105 v0.2 潜伏 bug（SPA catch-all 遮蔽 lifespan 期挂载的 telegram
    webhook 路由 → 405）已在 main.py 根治——SPA 挂载迁到 lifespan（commit 之后）
    恒为最后一条路由。本 patch 不再是 bug 掩盖，「dist 存在时 webhook 仍可达」的
    正向回归由 test_frontend_spa_mount_does_not_shadow_webhook_when_dist_exists
    确定性覆盖。改 patch `_resolve_frontend_dist`（单一 seam）而非全局劫持
    stdlib `Path.exists`。
    """
    from octoagent.gateway import main as gateway_main

    monkeypatch.setattr(gateway_main, "_resolve_frontend_dist", lambda: None)


def _prepare_hermetic_bootstrap_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """真跑 lifespan 的 gateway 测试的宿主 ~/.octoagent 隔离（test_control_plane_e2e
    同款范式）。两条互补路径缺一不可，否则脏实例失败：
    - call-time `Path.home()` → tmp（pipelines / plugins / skills / USER.md /
      octo_harness mcp_servers_dir 回退等运行期调用）；
    - import-time 常量 `_DEFAULT_STORE_DIR` + `_DEFAULT_MCP_SERVERS_DIR`（模块加载期
      已捕获 Path.home()，patch Path.home 对其无效，须显式重定向）。
    """
    home = tmp_path / "home"
    home.mkdir()
    (home / ".octoagent").mkdir()
    monkeypatch.setenv("OCTOAGENT_DB_PATH", str(tmp_path / "data" / "sqlite" / "test.db"))
    monkeypatch.setenv("OCTOAGENT_ARTIFACTS_DIR", str(tmp_path / "data" / "artifacts"))
    monkeypatch.setenv("OCTOAGENT_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("OCTOAGENT_LLM_MODE", "echo")
    monkeypatch.setenv("LOGFIRE_SEND_TO_LOGFIRE", "false")
    _write_minimal_config(tmp_path)
    # 隔离宿主 ~/.octoagent 运行时回退路径（mcp-servers / pipelines 在 bootstrap 期调 Path.home()）
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    # CredentialStore._DEFAULT_STORE_DIR 是 import-time 常量（auth/store.py），
    # patch Path.home 对它无效——须显式重定向，否则 bootstrap 仍读宿主
    # auth-profiles.json（脏实例失败源）
    import octoagent.provider.auth.store as _auth_store

    monkeypatch.setattr(_auth_store, "_DEFAULT_STORE_DIR", home / ".octoagent")
    # 同理 McpInstallerService._DEFAULT_MCP_SERVERS_DIR 也是 import-time 常量
    # （mcp_installer.py）：lifespan 以 mcp_servers_dir=None 构造 McpInstallerService，
    # 内部 fallback 到该常量——patch Path.home 同样无效，须显式重定向才彻底隔离宿主。
    import octoagent.gateway.services.mcp_installer as _mcp_installer

    monkeypatch.setattr(
        _mcp_installer, "_DEFAULT_MCP_SERVERS_DIR", home / ".octoagent" / "mcp-servers"
    )


@pytest_asyncio.fixture
async def unbootstrapped_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """hermetic fixture：仅 create_app()，不跑 lifespan。

    F105 修复后 create_app 构造期不再挂 SPA catch-all（迁到 lifespan），故无需抑制
    frontend/dist——不跑 lifespan 即不挂 SPA，webhook 路由本就未注册 → 404。
    """
    monkeypatch.setenv("OCTOAGENT_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("OCTOAGENT_DB_PATH", str(tmp_path / "sqlite" / "test.db"))
    monkeypatch.setenv("OCTOAGENT_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("LOGFIRE_SEND_TO_LOGFIRE", "false")
    from octoagent.gateway.main import create_app

    yield create_app()


@pytest.mark.asyncio
async def test_unbootstrapped_app_webhook_404_documented(unbootstrapped_app: FastAPI) -> None:
    """US-1 AC-4（R1 归档）：未跑 lifespan 的 create_app() app 上 webhook 404
    （baseline 为 503 service-unavailable）——grep 实证零消费者处此状态，
    本测试把该差异显式固化为受控行为。"""
    async with AsyncClient(
        transport=ASGITransport(app=unbootstrapped_app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/telegram/webhook", json={"update_id": 1})
    assert resp.status_code == 404


def _write_minimal_config(project_root: Path) -> None:
    from octoagent.gateway.services.config.config_schema import (
        ChannelsConfig,
        OctoAgentConfig,
        TelegramChannelConfig,
    )
    from octoagent.gateway.services.config.config_wizard import save_config

    save_config(
        OctoAgentConfig(
            updated_at="2026-06-12",
            channels=ChannelsConfig(
                telegram=TelegramChannelConfig(
                    enabled=True,
                    mode="webhook",
                    webhook_url="https://example.com/api/telegram/webhook",
                    dm_policy="open",
                    group_policy="open",
                )
            ),
        ),
        project_root,
    )


@pytest_asyncio.fixture
async def bootstrapped_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """create_app + 真跑 lifespan（test_control_plane_e2e 同款隔离范式）。
    使用 monkeypatch 替代手动 os.environ 赋值（F083 P2 范式），自动清理。
    """
    _prepare_hermetic_bootstrap_env(tmp_path, monkeypatch)
    # 让 SPA 挂载确定性缺席，使本 fixture 与宿主 frontend/dist 状态解耦（F105 修复
    # 后 SPA 恒为最后一条路由不再遮蔽 webhook；「dist 存在仍可达」由专门回归覆盖）。
    _patch_no_frontend_dist(monkeypatch)

    from octoagent.gateway.main import create_app

    application = create_app()
    async with (
        application.router.lifespan_context(application),
        AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as client,
    ):
        yield client


@pytest.mark.asyncio
async def test_harness_bootstrap_mounts_adapter_routers(
    bootstrapped_client: AsyncClient,
) -> None:
    """SC-4：harness bootstrap 的 ingress 挂载循环真实执行——经完整
    create_app + lifespan 后，telegram webhook 路由可达（非 404），
    空 update 走真实 service 链路返回 ignored/200。"""
    resp = await bootstrapped_client.post("/api/telegram/webhook", json={})
    assert resp.status_code != 404
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


@pytest.mark.asyncio
async def test_frontend_spa_mount_does_not_shadow_webhook_when_dist_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """回归（F105 v0.2 潜伏 production bug）：`frontend/dist` 存在时，SPA catch-all
    （Mount("/")）不得遮蔽 lifespan 期挂载的 telegram inbound router。

    根因：Starlette 按注册序匹配，`Mount("/")` 全前缀匹配一切。修复前 SPA 在
    create_app 构造期挂载，排在 lifespan 期挂载的 webhook 路由**之前** → POST
    /api/telegram/webhook 落到 StaticFiles（只收 GET/HEAD）→ 405。修复后 SPA 挂载
    迁到 lifespan（commit 之后）恒为最后一条路由。

    本测试经**完整 create_app + 真 lifespan**（生产路径）注入临时 dist，确定性重放
    「dist 存在」这一此前零覆盖的环境敏感场景，同时锁死路由顺序不变量与端到端行为
    ——若 SPA 回到构造期或漏挂，本测试即失败（路由末位断言 / webhook 405 / SPA 404）。
    """
    _prepare_hermetic_bootstrap_env(tmp_path, monkeypatch)

    from octoagent.gateway import main as gateway_main

    dist = tmp_path / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text(
        "<html><body>octo chat shell</body></html>", encoding="utf-8"
    )
    monkeypatch.setattr(gateway_main, "_resolve_frontend_dist", lambda: dist)

    application = gateway_main.create_app()
    async with (
        application.router.lifespan_context(application),
        AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as client,
    ):
        # 结构不变量：SPA Mount("/") 是最后一条路由（排在 lifespan 期挂载的 webhook 路由之后）。
        assert getattr(application.router.routes[-1], "name", None) == "frontend"
        # 行为 1（本 bug 核心）：dist 存在时 POST webhook 仍可达（非 405，走真实 service）。
        webhook = await client.post("/api/telegram/webhook", json={})
        assert webhook.status_code == 200
        assert webhook.json()["status"] == "ignored"
        # 行为 2：未知前端路由仍由 SPA fallback 到 index.html（SPA 本身可用）。
        spa = await client.get("/chat")
        assert spa.status_code == 200
        assert "octo chat shell" in spa.text
