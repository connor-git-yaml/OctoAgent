"""F105 v0.2 Phase A: ingress 契约测试。

覆盖 spec v0.2 US-1 AC-3（同 router 经 adapter 挂载等价）/ US-1 AC-4
（未 bootstrap 的 app 上 webhook 404——R1 唯一语义差异归档）/ SC-4
（harness 挂载循环集成证明）/ FR-A2（web adapter 无 HTTP inbound）。
"""

from __future__ import annotations

import os
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


@pytest.mark.asyncio
async def test_unbootstrapped_app_webhook_404_documented(app: FastAPI) -> None:
    """US-1 AC-4（R1 归档）：未跑 lifespan 的 create_app() app 上 webhook 404
    （baseline 为 503 service-unavailable）——grep 实证零消费者处此状态，
    本测试把该差异显式固化为受控行为。"""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
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
async def bootstrapped_client(tmp_path: Path):
    """create_app + 真跑 lifespan（test_control_plane_e2e 同款隔离范式）。"""
    os.environ["OCTOAGENT_DB_PATH"] = str(tmp_path / "data" / "sqlite" / "test.db")
    os.environ["OCTOAGENT_ARTIFACTS_DIR"] = str(tmp_path / "data" / "artifacts")
    os.environ["OCTOAGENT_PROJECT_ROOT"] = str(tmp_path)
    os.environ["OCTOAGENT_LLM_MODE"] = "echo"
    os.environ["LOGFIRE_SEND_TO_LOGFIRE"] = "false"
    _write_minimal_config(tmp_path)

    from octoagent.gateway.main import create_app

    application = create_app()
    async with (
        application.router.lifespan_context(application),
        AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as client,
    ):
        yield client

    for key in [
        "OCTOAGENT_DB_PATH",
        "OCTOAGENT_ARTIFACTS_DIR",
        "OCTOAGENT_PROJECT_ROOT",
        "OCTOAGENT_LLM_MODE",
        "LOGFIRE_SEND_TO_LOGFIRE",
    ]:
        os.environ.pop(key, None)


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
