"""F106 /api/plugins REST 契约测试（T8 / FR-8 / SC-009）。

最小 app（仅 plugins.router + app.state.plugin_registry），httpx AsyncClient 驱动。
"""

from __future__ import annotations

from pathlib import Path

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from octoagent.gateway.harness.tool_registry import ToolRegistry
from octoagent.gateway.routes import plugins as plugins_routes
from octoagent.gateway.services.plugin_registry import PluginRegistry
from octoagent.skills.discovery import SkillDiscovery


class _Result:
    blocked = False
    pattern_id = None


class _Scanner:
    def scan_memory(self, content: str):
        return _Result()


def _w(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


@pytest_asyncio.fixture
async def api(tmp_path: Path):
    plugins_dir = tmp_path / "plugins"
    _w(plugins_dir / "decl" / "plugin.yaml", "name: decl\nprovides:\n  skills: [s1]\n")
    _w(plugins_dir / "decl" / "skills" / "s1" / "SKILL.md", "---\nname: s1\ndescription: d\n---\n# s1")
    _w(plugins_dir / "codep" / "plugin.yaml", "name: codep\nprovides:\n  tools: [tools.py]\n")
    _w(plugins_dir / "codep" / "tools.py", "PLUGIN_TOOLS = []\n")

    builtin = tmp_path / "builtin"
    builtin.mkdir()
    reg = PluginRegistry(
        plugins_dir=plugins_dir,
        skill_discovery=SkillDiscovery(builtin_dir=builtin, user_dir=None, project_dir=None),
        content_scanner=_Scanner(),
        tool_registry=ToolRegistry(),
        event_store=None,
        task_store=None,
    )
    await reg.discover_and_register()

    app = FastAPI()
    app.include_router(plugins_routes.router)
    app.state.plugin_registry = reg
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, reg, plugins_dir


async def test_list(api) -> None:
    client, _reg, _pd = api
    r = await client.get("/api/plugins")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    names = {it["name"] for it in body["items"]}
    assert names == {"decl", "codep"}


async def test_get_and_404(api) -> None:
    client, _reg, _pd = api
    assert (await client.get("/api/plugins/decl")).status_code == 200
    assert (await client.get("/api/plugins/ghost")).status_code == 404


async def test_toggle(api) -> None:
    client, _reg, _pd = api
    r = await client.post("/api/plugins/decl/toggle", json={"enabled": False})
    assert r.status_code == 200
    assert r.json()["state"] == "disabled"


async def test_approve_code_plugin(api) -> None:
    client, _reg, _pd = api
    assert (await client.get("/api/plugins/codep")).json()["state"] == "pending_approval"
    r = await client.post("/api/plugins/codep/approve")
    assert r.status_code == 200
    body = r.json()
    assert body["plugin"]["state"] == "enabled"
    # 风险披露存在，且不含"安全/已扫描"措辞（review M1）
    assert body["risk_disclosure"]
    assert "安全扫描" not in body["risk_disclosure"] or "未做" in body["risk_disclosure"]


async def test_approve_declarative_returns_400(api) -> None:
    client, _reg, _pd = api
    assert (await client.post("/api/plugins/decl/approve")).status_code == 400


async def test_delete_and_refresh(api) -> None:
    client, _reg, _pd = api
    r = await client.request("DELETE", "/api/plugins/decl")
    assert r.status_code == 204
    # 删后 refresh 计数
    r2 = await client.post("/api/plugins/refresh")
    assert r2.status_code == 200
    assert r2.json()["total"] == 1


async def test_delete_404(api) -> None:
    client, _reg, _pd = api
    assert (await client.request("DELETE", "/api/plugins/ghost")).status_code == 404


async def test_install_banned_url_400(api) -> None:
    client, _reg, _pd = api
    r = await client.post("/api/plugins/install", json={"repo_url": "file:///etc/passwd"})
    assert r.status_code == 400  # GitError（禁 file://）→ 400


async def test_update_non_git_400(api) -> None:
    client, _reg, _pd = api
    # 'decl' 是本地（非 git）plugin → update 拒
    assert (await client.post("/api/plugins/decl/update")).status_code == 400


async def test_registry_unavailable_503() -> None:
    app = FastAPI()
    app.include_router(plugins_routes.router)
    app.state.plugin_registry = None
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/api/plugins")).status_code == 503
