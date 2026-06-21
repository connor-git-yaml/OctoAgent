"""F106 full-lifespan e2e：真 11 段 bootstrap（含段 7.5）装载 declarative plugin（SC-001/002）。

证明生产路径（create_app + lifespan）真的发现/注册 plugin，且坏 plugin 隔离不崩——
非段 7.5 silent-degrade。OCTOAGENT_PLUGINS_DIR 隔离宿主。
"""

from __future__ import annotations

from pathlib import Path

import pytest_asyncio

from octoagent.skills.plugins.manifest import PluginState


def _w(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


@pytest_asyncio.fixture
async def lifespan_app(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OCTOAGENT_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("OCTOAGENT_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("OCTOAGENT_PROJECT_ROOT", str(tmp_path / "project"))
    monkeypatch.setenv("OCTOAGENT_PLUGINS_DIR", str(tmp_path / "plugins"))
    monkeypatch.setenv("LOGFIRE_SEND_TO_LOGFIRE", "false")
    (tmp_path / "artifacts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "project").mkdir(parents=True, exist_ok=True)

    # 好 declarative plugin + 坏 plugin（非法 yaml）
    plugins = tmp_path / "plugins"
    _w(plugins / "weather-helper" / "plugin.yaml", "name: weather-helper\nprovides:\n  skills: [forecast]\n")
    _w(
        plugins / "weather-helper" / "skills" / "forecast" / "SKILL.md",
        "---\nname: forecast\ndescription: weather forecast skill\n---\n# forecast\nlook up weather",
    )
    _w(plugins / "broken" / "plugin.yaml", "name: broken\n : : not valid : :\n")

    from octoagent.gateway.main import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        yield app


async def test_full_bootstrap_loads_declarative_plugin(lifespan_app) -> None:
    app = lifespan_app
    reg = getattr(app.state, "plugin_registry", None)
    assert reg is not None, "段 7.5 未装配 plugin_registry（疑似 silent degrade）"

    good = reg.get_record("weather-helper")
    assert good is not None and good.state == PluginState.ENABLED

    broken = reg.get_record("broken")
    assert broken is not None and broken.state == PluginState.REJECTED

    # plugin skill 进 SkillDiscovery（PLUGIN source + provenance）
    sd = app.state.skill_discovery
    entry = sd.get("forecast")
    assert entry is not None
    assert entry.provenance == "weather-helper"
