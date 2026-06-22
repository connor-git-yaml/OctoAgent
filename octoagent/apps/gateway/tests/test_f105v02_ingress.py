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
    """精准 patch：让 frontend/dist 判定为不存在，阻止 create_app 挂 SPA catch-all。

    create_app 在 frontend/dist 存在时 `app.mount("/", SpaStaticFiles(html=True))`
    （main.py:385），这是 catch-all——POST /api/telegram/webhook 落到 StaticFiles
    返回 405（不接受 POST），把"路由未注册"（404）/"路由已挂载"（200）都污染成 405。
    脏环境（主仓 build 过前端 / pre-commit 跑 master 主仓）下两个 ingress 测试的
    405 同源根因。其余 Path.exists 调用走真实判定，不受影响（仅双条件 name+parent 命中）。
    """
    real_exists = Path.exists

    def _no_frontend_dist(self: Path) -> bool:
        if self.name == "dist" and self.parent.name == "frontend":
            return False
        return real_exists(self)

    monkeypatch.setattr(Path, "exists", _no_frontend_dist)


@pytest_asyncio.fixture
async def unbootstrapped_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """hermetic fixture：精准 patch frontend/dist 不存在，防 SPA catch-all 把 404 变 405。
    不跑 lifespan，仅 create_app()。
    """
    monkeypatch.setenv("OCTOAGENT_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("OCTOAGENT_DB_PATH", str(tmp_path / "sqlite" / "test.db"))
    monkeypatch.setenv("OCTOAGENT_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("LOGFIRE_SEND_TO_LOGFIRE", "false")
    _patch_no_frontend_dist(monkeypatch)
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

    宿主 ~/.octoagent 隔离走两条互补路径（缺一不可，否则脏实例失败）：
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
    # patch Path.home 对它无效——须显式重定向，否则 bootstrap
    # 仍读宿主 auth-profiles.json（脏实例失败源）
    import octoagent.provider.auth.store as _auth_store

    monkeypatch.setattr(_auth_store, "_DEFAULT_STORE_DIR", home / ".octoagent")
    # 同理 McpInstallerService._DEFAULT_MCP_SERVERS_DIR 也是 import-time 常量（mcp_installer.py）：
    # lifespan 以 mcp_servers_dir=None 构造 McpInstallerService（octo_harness._bootstrap_mcp），
    # 内部 fallback 到该常量——patch Path.home 同样无效，须显式重定向才彻底隔离宿主
    # ~/.octoagent/mcp-servers（当前虽为空属良性，但留作真实例状态依赖的隐患）。
    import octoagent.gateway.services.mcp_installer as _mcp_installer

    monkeypatch.setattr(
        _mcp_installer, "_DEFAULT_MCP_SERVERS_DIR", home / ".octoagent" / "mcp-servers"
    )
    # frontend/dist 存在时 create_app 挂 SPA catch-all，会把 lifespan 挂载的 telegram
    # 路由 200 污染成 405（脏环境/主仓 build 前端下 SC-4 失败根因，与 2a 同源）。
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
