"""Skill 系统端到端冒烟测试 -- Feature 057 T061

测试流程:
1. 启动系统（完整 lifespan）
2. GET /api/skills -> 验证返回 8 个内置 Skill
3. GET /api/skills/coding-agent -> 验证详情包含 content
4. POST /api/skills 安装自定义 Skill -> 验证返回 201
5. GET /api/skills -> 验证返回 9 个 Skill
6. DELETE /api/skills/{name} -> 卸载自定义 Skill
7. GET /api/skills -> 验证回到 8 个
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


@pytest_asyncio.fixture
async def e2e_skill_client(tmp_path: Path):
    """完整 lifespan 启动的测试客户端。"""
    os.environ["OCTOAGENT_DB_PATH"] = str(tmp_path / "data" / "sqlite" / "test.db")
    os.environ["OCTOAGENT_ARTIFACTS_DIR"] = str(tmp_path / "data" / "artifacts")
    os.environ["OCTOAGENT_PROJECT_ROOT"] = str(tmp_path)
    os.environ["OCTOAGENT_LLM_MODE"] = "echo"
    os.environ["LOGFIRE_SEND_TO_LOGFIRE"] = "false"

    from octoagent.gateway.main import create_app

    app = create_app()
    async with app.router.lifespan_context(app), AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client

    for key in [
        "OCTOAGENT_DB_PATH",
        "OCTOAGENT_ARTIFACTS_DIR",
        "OCTOAGENT_PROJECT_ROOT",
        "OCTOAGENT_LLM_MODE",
        "LOGFIRE_SEND_TO_LOGFIRE",
    ]:
        os.environ.pop(key, None)


CUSTOM_SKILL_MD = """---
name: e2e-test-skill
description: A custom skill for e2e testing
version: "1.0.0"
tags:
  - e2e
  - test
---

# E2E Test Skill

This is a test skill installed via the management API.
"""


class TestSkillSystemE2E:
    """Skill 系统端到端冒烟测试。"""

    @pytest.mark.asyncio
    async def test_full_lifecycle(self, e2e_skill_client: AsyncClient):
        """完整生命周期：列表 -> 详情 -> 安装 -> 列表（+1）-> 卸载 -> 列表（恢复）。"""
        client = e2e_skill_client

        # 1. 列表 -- 应有 8 个内置 Skill
        resp = await client.get("/api/skills")
        assert resp.status_code == 200
        data = resp.json()
        initial_count = data["total"]
        assert initial_count >= 8, f"期望至少 8 个内置 Skill，实际 {initial_count}"

        # 验证已知的内置 Skill 存在
        names = [item["name"] for item in data["items"]]
        for expected in ["coding-agent", "github", "summarize", "weather"]:
            assert expected in names, f"缺少内置 Skill: {expected}"

        # 2. 详情 -- 获取 coding-agent 完整信息
        resp = await client.get("/api/skills/coding-agent")
        assert resp.status_code == 200
        detail = resp.json()
        assert detail["name"] == "coding-agent"
        assert detail["source"] == "builtin"
        assert len(detail["content"]) > 0, "详情接口应返回 content"

        # 3. 安装自定义 Skill
        resp = await client.post(
            "/api/skills",
            json={"name": "e2e-test-skill", "content": CUSTOM_SKILL_MD},
        )
        assert resp.status_code == 201
        install_data = resp.json()
        assert install_data["name"] == "e2e-test-skill"
        assert install_data["source"] == "user"

        # 4. 列表 -- 应多出 1 个
        resp = await client.get("/api/skills")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == initial_count + 1

        # 5. 验证新 Skill 的详情
        resp = await client.get("/api/skills/e2e-test-skill")
        assert resp.status_code == 200
        custom_detail = resp.json()
        assert custom_detail["source"] == "user"
        assert "E2E Test Skill" in custom_detail["content"]

        # 6. 卸载自定义 Skill
        resp = await client.delete("/api/skills/e2e-test-skill")
        assert resp.status_code == 200

        # 7. 列表 -- 应恢复到初始数量
        resp = await client.get("/api/skills")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == initial_count

        # 8. 验证已卸载的 Skill 不存在
        resp = await client.get("/api/skills/e2e-test-skill")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_builtin_skill_cannot_be_uninstalled(self, e2e_skill_client: AsyncClient):
        """内置 Skill 不可卸载。"""
        resp = await e2e_skill_client.delete("/api/skills/coding-agent")
        assert resp.status_code == 403
        assert "builtin" in resp.json()["detail"].lower()
