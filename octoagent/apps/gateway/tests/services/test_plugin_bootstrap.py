"""F106 bootstrap 段 7.5 集成测 + plugins_dir DI + 降级隔离（T7/T10/SC-002/SC-010）。

直接驱动 OctoHarness._bootstrap_user_plugins（轻量，不跑全 11 段），验证：
- plugins_dir DI 隔离（不碰宿主 ~/.octoagent）。
- 坏 plugin 隔离降级 + gateway 段不抛（#6 / FR-10.2）。
- skill_discovery 缺失 → plugin_registry=None 降级（不崩）。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from octoagent.gateway.harness.octo_harness import OctoHarness
from octoagent.skills.discovery import SkillDiscovery
from octoagent.skills.plugins.manifest import PluginState


def _w(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def _make_app_with_skill_discovery(tmp_path: Path) -> tuple[FastAPI, SkillDiscovery]:
    builtin = tmp_path / "builtin"
    builtin.mkdir(exist_ok=True)
    app = FastAPI()
    sd = SkillDiscovery(builtin_dir=builtin, user_dir=None, project_dir=None)
    app.state.skill_discovery = sd
    return app, sd


async def test_bootstrap_user_plugins_di_and_degradation(tmp_path: Path) -> None:
    plugins_dir = tmp_path / "plugins"
    # 好 plugin（declarative）+ 坏 plugin（非法 yaml）混装
    _w(
        plugins_dir / "good" / "plugin.yaml",
        "name: good\nprovides:\n  skills: [s1]\n",
    )
    _w(plugins_dir / "good" / "skills" / "s1" / "SKILL.md", "---\nname: s1\ndescription: d\n---\n# s1")
    _w(plugins_dir / "bad" / "plugin.yaml", "name: bad\n : : broken : :\n")

    root = tmp_path / "root"
    root.mkdir()
    harness = OctoHarness(project_root=root, plugins_dir=plugins_dir)
    app, sd = _make_app_with_skill_discovery(tmp_path)

    # _store_group 默认 None → event/task store None（降级路径），不应崩
    await harness._bootstrap_user_plugins(app)

    reg = app.state.plugin_registry
    assert reg is not None  # 未崩
    assert reg.get_record("good").state == PluginState.ENABLED
    assert reg.get_record("bad").state == PluginState.REJECTED
    assert sd.get("s1") is not None  # 好 plugin 的 skill 已注册


async def test_bootstrap_user_plugins_no_skill_discovery_degrades(tmp_path: Path) -> None:
    root = tmp_path / "root2"
    root.mkdir()
    harness = OctoHarness(project_root=root, plugins_dir=tmp_path / "plugins2")
    app = FastAPI()  # 无 skill_discovery
    await harness._bootstrap_user_plugins(app)
    assert app.state.plugin_registry is None  # 降级，未崩


async def test_bootstrap_creates_missing_plugins_dir(tmp_path: Path) -> None:
    plugins_dir = tmp_path / "nonexistent_plugins"
    assert not plugins_dir.exists()
    root = tmp_path / "root3"
    root.mkdir()
    harness = OctoHarness(project_root=root, plugins_dir=plugins_dir)
    app, _sd = _make_app_with_skill_discovery(tmp_path)
    await harness._bootstrap_user_plugins(app)
    assert plugins_dir.exists()  # mkdir parents（FR-1.5）
    assert app.state.plugin_registry is not None
