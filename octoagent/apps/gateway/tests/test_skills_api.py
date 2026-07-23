"""Skills 管理 API 测试 -- Feature 057 T047

测试内容:
1. GET /api/skills 返回内置 Skill 列表
2. GET /api/skills/{name} 返回 Skill 详情（含 content）
3. GET /api/skills/{name} 不存在时返回 404
4. POST /api/skills 安装新 Skill
5. POST /api/skills 格式错误返回 400
6. DELETE /api/skills/{name} 卸载用户 Skill
7. DELETE /api/skills/{name} 内置 Skill 返回 403
8. DELETE /api/skills/{name} 不存在返回 404
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

SKILL_ROOT_ISOLATION_ORACLE = "F151_SKILL_USER_ROOT_ISOLATION_MISSING"


def _configure_env(tmp_path: Path, monkeypatch):
    """配置测试环境变量。"""
    monkeypatch.setenv("OCTOAGENT_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("OCTOAGENT_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("OCTOAGENT_PROJECT_ROOT", str(tmp_path / "project"))
    monkeypatch.setenv("LOGFIRE_SEND_TO_LOGFIRE", "false")
    # 创建必要的目录
    (tmp_path / "artifacts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "project").mkdir(parents=True, exist_ok=True)


def _isolated_harness_factory(root: Path, user_skills_dir: Path):
    """构造使用独立用户 Skill 根的 full-lifespan harness factory。"""
    from octoagent.gateway.harness.octo_harness import OctoHarness

    def factory() -> OctoHarness:
        try:
            return OctoHarness(
                project_root=root / "project",
                data_dir=root / "data",
                mcp_servers_dir=root / "mcp-servers",
                plugins_dir=root / "plugins",
                user_skills_dir=user_skills_dir,
            )
        except TypeError as exc:
            pytest.fail(
                f"{SKILL_ROOT_ISOLATION_ORACLE}: {exc}",
                pytrace=False,
            )

    return factory


@asynccontextmanager
async def _isolated_skills_client(
    root: Path,
    user_skills_dir: Path,
) -> AsyncIterator[tuple[object, AsyncClient]]:
    """启动一个显式隔离用户 Skill 根的完整 lifespan app。"""
    from octoagent.gateway.main import create_app

    app = create_app(
        harness_factory=_isolated_harness_factory(root, user_skills_dir),
    )
    async with (
        app.router.lifespan_context(app),
        AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client,
    ):
        yield app, client


@pytest_asyncio.fixture
async def skills_app(tmp_path: Path, monkeypatch):
    """创建完整 lifespan 的 FastAPI app，用于测试 Skills API。"""
    _configure_env(tmp_path, monkeypatch)

    from octoagent.gateway.main import create_app

    app = create_app(
        harness_factory=_isolated_harness_factory(
            tmp_path,
            tmp_path / "user-skills",
        ),
    )
    async with app.router.lifespan_context(app):
        yield app


@pytest_asyncio.fixture
async def skills_client(skills_app) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=skills_app),
        base_url="http://test",
    ) as client:
        yield client


# ============================================================
# GET /api/skills -- 列表
# ============================================================


class TestListSkills:
    """GET /api/skills 列表端点。"""

    @pytest.mark.asyncio
    async def test_list_returns_builtin_skills(self, skills_client: AsyncClient):
        """系统启动后应返回内置 Skill 列表。"""
        resp = await skills_client.get("/api/skills")
        assert resp.status_code == 200

        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert data["total"] >= 1

        # 验证返回的 item 结构
        first_item = data["items"][0]
        assert "name" in first_item
        assert "description" in first_item
        assert "source" in first_item
        assert "tags" in first_item

    @pytest.mark.asyncio
    async def test_list_contains_known_builtin(self, skills_client: AsyncClient):
        """列表中应包含已知的内置 Skill（如 coding-agent）。"""
        resp = await skills_client.get("/api/skills")
        assert resp.status_code == 200

        data = resp.json()
        names = [item["name"] for item in data["items"]]
        assert "coding-agent" in names

    @pytest.mark.asyncio
    async def test_list_total_matches_items_count(self, skills_client: AsyncClient):
        """total 字段应与 items 数组长度一致。"""
        resp = await skills_client.get("/api/skills")
        assert resp.status_code == 200

        data = resp.json()
        assert data["total"] == len(data["items"])


# ============================================================
# GET /api/skills/{name} -- 详情
# ============================================================


class TestGetSkill:
    """GET /api/skills/{name} 详情端点。"""

    @pytest.mark.asyncio
    async def test_get_existing_skill(self, skills_client: AsyncClient):
        """获取已存在的 Skill 应返回完整信息。"""
        resp = await skills_client.get("/api/skills/coding-agent")
        assert resp.status_code == 200

        data = resp.json()
        assert data["name"] == "coding-agent"
        assert data["description"]
        assert data["source"] == "builtin"
        # 详情接口应包含 content
        assert "content" in data
        assert len(data["content"]) > 0
        # 详情接口应包含额外字段
        assert "trigger_patterns" in data
        assert "tools_required" in data

    @pytest.mark.asyncio
    async def test_get_nonexistent_skill(self, skills_client: AsyncClient):
        """获取不存在的 Skill 应返回 404。"""
        resp = await skills_client.get("/api/skills/nonexistent-skill-xyz")
        assert resp.status_code == 404

        data = resp.json()
        assert "not found" in data["detail"].lower()


# ============================================================
# POST /api/skills -- 安装
# ============================================================


VALID_SKILL_MD = """---
name: test-install-skill
description: A test skill for API installation
version: "1.0.0"
tags:
  - test
  - api
---

# Test Install Skill

This is the instruction body for the test skill.
"""

INVALID_SKILL_MD_NO_FRONTMATTER = """# Just Markdown

No frontmatter here.
"""

INVALID_SKILL_MD_MISSING_DESCRIPTION = """---
name: bad-skill
---

# Bad Skill
"""


class TestInstallSkill:
    """POST /api/skills 安装端点。"""

    @pytest.mark.asyncio
    async def test_install_success(self, skills_client: AsyncClient, skills_app):
        """成功安装新 Skill。"""
        resp = await skills_client.post(
            "/api/skills",
            json={
                "name": "test-install-skill",
                "content": VALID_SKILL_MD,
            },
        )
        assert resp.status_code == 201

        data = resp.json()
        assert data["name"] == "test-install-skill"
        assert data["source"] == "user"
        assert "installed successfully" in data["message"]

        # 验证安装后可以通过 GET 获取
        get_resp = await skills_client.get("/api/skills/test-install-skill")
        assert get_resp.status_code == 200
        assert get_resp.json()["name"] == "test-install-skill"
        assert get_resp.json()["source"] == "user"

    @pytest.mark.asyncio
    async def test_install_no_frontmatter(self, skills_client: AsyncClient):
        """缺少 frontmatter 的 SKILL.md 应返回 400。"""
        resp = await skills_client.post(
            "/api/skills",
            json={
                "name": "bad-skill",
                "content": INVALID_SKILL_MD_NO_FRONTMATTER,
            },
        )
        assert resp.status_code == 400
        assert "frontmatter" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_install_missing_description(self, skills_client: AsyncClient):
        """缺少必填字段 description 应返回 400。"""
        resp = await skills_client.post(
            "/api/skills",
            json={
                "name": "bad-skill",
                "content": INVALID_SKILL_MD_MISSING_DESCRIPTION,
            },
        )
        assert resp.status_code == 400
        assert "description" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_install_name_mismatch(self, skills_client: AsyncClient):
        """frontmatter 中的 name 与请求 name 不一致应返回 400。"""
        resp = await skills_client.post(
            "/api/skills",
            json={
                "name": "different-name",
                "content": VALID_SKILL_MD,
            },
        )
        assert resp.status_code == 400
        assert "不一致" in resp.json()["detail"]


# ============================================================
# DELETE /api/skills/{name} -- 卸载
# ============================================================


class TestUninstallSkill:
    """DELETE /api/skills/{name} 卸载端点。"""

    @pytest.mark.asyncio
    async def test_uninstall_user_skill(self, skills_client: AsyncClient):
        """卸载用户安装的 Skill 应成功。"""
        # 先安装
        install_resp = await skills_client.post(
            "/api/skills",
            json={
                "name": "test-install-skill",
                "content": VALID_SKILL_MD,
            },
        )
        assert install_resp.status_code == 201

        # 卸载
        resp = await skills_client.delete("/api/skills/test-install-skill")
        assert resp.status_code == 200

        data = resp.json()
        assert data["name"] == "test-install-skill"
        assert "uninstalled successfully" in data["message"]

        # 验证卸载后不存在
        get_resp = await skills_client.get("/api/skills/test-install-skill")
        assert get_resp.status_code == 404

    @pytest.mark.asyncio
    async def test_full_lifespan_apps_isolate_user_skill_roots(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        """两个 full-lifespan app 的同名 Skill 必须互不影响。"""
        _configure_env(tmp_path, monkeypatch)
        root_a = tmp_path / "a"
        root_b = tmp_path / "b"
        skills_a = root_a / "skills"
        skills_b = root_b / "skills"

        async with (
            _isolated_skills_client(root_a, skills_a) as (app_a, client_a),
            _isolated_skills_client(root_b, skills_b) as (app_b, client_b),
        ):
            assert app_a.state.skill_discovery.user_dir == skills_a
            assert app_b.state.skill_discovery.user_dir == skills_b
            assert skills_a != skills_b

            for client in (client_a, client_b):
                response = await client.post(
                    "/api/skills",
                    json={"name": "test-install-skill", "content": VALID_SKILL_MD},
                )
                assert response.status_code == 201

            file_a = skills_a / "test-install-skill" / "SKILL.md"
            file_b = skills_b / "test-install-skill" / "SKILL.md"
            assert file_a.read_text(encoding="utf-8") == VALID_SKILL_MD
            assert file_b.read_text(encoding="utf-8") == VALID_SKILL_MD

            deleted = await client_a.delete("/api/skills/test-install-skill")
            assert deleted.status_code == 200
            assert not file_a.exists()
            assert file_b.read_text(encoding="utf-8") == VALID_SKILL_MD
            assert (await client_a.get("/api/skills/test-install-skill")).status_code == 404
            assert (await client_b.get("/api/skills/test-install-skill")).status_code == 200

    @pytest.mark.asyncio
    async def test_uninstall_builtin_skill(self, skills_client: AsyncClient):
        """卸载非 USER 来源的 Skill 应返回 403。"""
        resp = await skills_client.delete("/api/skills/coding-agent")
        assert resp.status_code == 403

        data = resp.json()
        assert "builtin" in data["detail"]

    @pytest.mark.asyncio
    async def test_uninstall_nonexistent_skill(self, skills_client: AsyncClient):
        """卸载不存在的 Skill 应返回 404。"""
        resp = await skills_client.delete("/api/skills/nonexistent-skill-xyz")
        assert resp.status_code == 404
