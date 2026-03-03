"""GET /api/tasks/journal 端点集成测试 -- Feature 011 T031

使用 mini FastAPI + 手动初始化 store_group（不依赖完整 lifespan），
覆盖：
- 正常返回 200 及分组结构验证
- 空数据库时全零统计
- 降级响应 503 JOURNAL_DEGRADED
- 路由注册顺序：/api/tasks/journal 不被 /api/tasks/{task_id} 截获
"""

import os
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from octoagent.core.store import create_store_group
from octoagent.gateway.routes import watchdog as watchdog_router
from octoagent.gateway.routes import tasks as tasks_router


@pytest_asyncio.fixture
async def test_app(tmp_path: Path):
    """构造含 watchdog + tasks 路由的 mini FastAPI app（手动初始化 store_group）"""
    db_path = str(tmp_path / "test.db")
    artifacts_dir = str(tmp_path / "artifacts")
    Path(artifacts_dir).mkdir(parents=True, exist_ok=True)

    store_group = await create_store_group(db_path, artifacts_dir)

    app = FastAPI()
    # 注册顺序：watchdog 在前（/api/tasks/journal），tasks 在后（/api/tasks/{task_id}）
    app.include_router(watchdog_router.router, tags=["watchdog"])
    app.include_router(tasks_router.router, tags=["tasks"])

    # 手动设置 app.state（对齐真实 lifespan 的初始化）
    app.state.store_group = store_group
    # watchdog_config 默认使用 WatchdogConfig()，无需注入

    yield app

    await store_group.conn.close()


@pytest_asyncio.fixture
async def client(test_app: FastAPI):
    """提供 httpx AsyncClient"""
    async with AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://test",
    ) as ac:
        yield ac


class TestJournalApiRouteOrder:
    """路由注册顺序验证（contracts/rest-api.md 要求）"""

    @pytest.mark.asyncio
    async def test_journal_route_not_captured_by_task_id_route(self, client: AsyncClient):
        """GET /api/tasks/journal 不被 /api/tasks/{task_id} 截获"""
        response = await client.get("/api/tasks/journal")
        # 正确注册时应返回 200（Journal 成功）
        # 若路由顺序错误，会返回 404（task_id="journal" 找不到任务）
        assert response.status_code == 200
        # 确认不是 task detail 的响应格式（task detail 有 task_id 字段而非 summary）
        data = response.json()
        assert "summary" in data  # Journal 特有字段
        assert "groups" in data   # Journal 特有字段


class TestJournalApiSuccess:
    """正常返回 200 的测试"""

    @pytest.mark.asyncio
    async def test_returns_200_with_correct_structure(self, client: AsyncClient):
        """返回 200 且响应结构符合契约（contracts/rest-api.md）"""
        response = await client.get("/api/tasks/journal")
        assert response.status_code == 200

        data = response.json()
        # 必要顶层字段
        assert "generated_at" in data
        assert "summary" in data
        assert "groups" in data

        # summary 字段完整性
        summary = data["summary"]
        for key in ("total", "running", "stalled", "drifted", "waiting_approval"):
            assert key in summary, f"summary 缺少字段: {key}"

        # groups 字段完整性
        groups = data["groups"]
        for key in ("running", "stalled", "drifted", "waiting_approval"):
            assert key in groups, f"groups 缺少字段: {key}"
            assert isinstance(groups[key], list), f"groups.{key} 应为 list"

    @pytest.mark.asyncio
    async def test_empty_db_returns_zero_counts(self, client: AsyncClient):
        """空数据库时，所有计数为 0"""
        response = await client.get("/api/tasks/journal")
        assert response.status_code == 200

        data = response.json()
        assert data["summary"]["total"] == 0
        assert data["summary"]["running"] == 0
        assert data["summary"]["stalled"] == 0
        assert data["summary"]["drifted"] == 0
        assert data["summary"]["waiting_approval"] == 0

    @pytest.mark.asyncio
    async def test_generated_at_is_iso_timestamp(self, client: AsyncClient):
        """generated_at 字段是合法的 ISO 8601 时间戳"""
        from datetime import datetime

        response = await client.get("/api/tasks/journal")
        assert response.status_code == 200

        data = response.json()
        generated_at = data["generated_at"]
        dt = datetime.fromisoformat(generated_at)
        assert dt is not None


class TestJournalApiDegradation:
    """降级响应测试（503 JOURNAL_DEGRADED）"""

    @pytest.mark.asyncio
    async def test_degraded_response_structure(self, test_app: FastAPI):
        """模拟 TaskJournalService.get_journal 抛出异常，验证 503 降级响应结构"""
        from unittest.mock import AsyncMock, patch

        from octoagent.gateway.services.task_journal import TaskJournalService

        with patch.object(
            TaskJournalService,
            "get_journal",
            new_callable=AsyncMock,
            side_effect=RuntimeError("DB connection failed"),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=test_app),
                base_url="http://test",
            ) as client:
                response = await client.get("/api/tasks/journal")

        assert response.status_code == 503
        data = response.json()
        assert "error" in data
        error = data["error"]
        assert error["code"] == "JOURNAL_DEGRADED"
        assert "message" in error
        assert "generated_at" in error
