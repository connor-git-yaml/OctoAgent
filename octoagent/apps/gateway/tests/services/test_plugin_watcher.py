"""F106 Phase C PluginWatcher 测试：ignore 过滤 / 降级（无 watchdog）/ debounce→refresh 桥接。

watchdog 未装 → start() 返回 False（降级）。debounce→refresh 链经直接调 _on_event 测（绕真 observer）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from octoagent.gateway.harness.tool_registry import ToolRegistry
from octoagent.gateway.services.plugin_watcher import PluginWatcher
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


def _registry(tmp_path: Path) -> tuple[PluginRegistry, Path]:
    builtin = tmp_path / "builtin"
    builtin.mkdir(exist_ok=True)
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir(exist_ok=True)
    reg = PluginRegistry(
        plugins_dir=plugins_dir,
        skill_discovery=SkillDiscovery(builtin_dir=builtin, user_dir=None, project_dir=None),
        content_scanner=_Scanner(),
        tool_registry=ToolRegistry(),
        event_store=None,
        task_store=None,
    )
    return reg, plugins_dir


def test_ignore_filter() -> None:
    w = PluginWatcher(Path("/plugins"), None, None)
    assert w._is_ignored("/plugins/p/.disabled")
    assert w._is_ignored("/plugins/p/.approved")
    assert w._is_ignored("/plugins/p/.git/HEAD")
    assert w._is_ignored("/plugins/p/__pycache__/x.pyc")
    assert w._is_ignored("")
    assert not w._is_ignored("/plugins/p/skills/s1/SKILL.md")
    assert not w._is_ignored("/plugins/p/tools.py")


def test_start_degrades_without_watchdog(tmp_path: Path) -> None:
    # watchdog 未装（worktree 环境）→ start() 返回 False，不抛
    reg, plugins_dir = _registry(tmp_path)
    w = PluginWatcher(plugins_dir, reg, None)
    assert w.start() is False
    w.stop()  # 幂等，不抛


async def test_debounce_triggers_refresh(tmp_path: Path) -> None:
    """add plugin 文件 → _on_event → debounce → run_coroutine_threadsafe → registry.refresh。"""
    reg, plugins_dir = _registry(tmp_path)
    await reg.discover_and_register()
    assert reg.get_record("newp") is None

    watcher = PluginWatcher(plugins_dir, reg, asyncio.get_running_loop(), debounce_sec=0.1)
    _w(plugins_dir / "newp" / "plugin.yaml", "name: newp\nprovides:\n  skills: [s1]\n")
    _w(plugins_dir / "newp" / "skills" / "s1" / "SKILL.md", "---\nname: s1\ndescription: d\n---\n# s1")

    watcher._on_event(str(plugins_dir / "newp" / "plugin.yaml"))  # 模拟 fs 事件
    await asyncio.sleep(0.5)  # 等 debounce + refresh

    assert reg.get_record("newp") is not None  # watcher 触发了 refresh
    assert reg.get_record("newp").state.value == "enabled"
    watcher.stop()


async def test_ignored_event_no_refresh(tmp_path: Path) -> None:
    reg, plugins_dir = _registry(tmp_path)
    await reg.discover_and_register()
    watcher = PluginWatcher(plugins_dir, reg, asyncio.get_running_loop(), debounce_sec=0.1)
    _w(plugins_dir / "newp" / "plugin.yaml", "name: newp\nprovides:\n  skills: []\n")
    # 仅触发被忽略的 marker 事件 → 不 refresh
    watcher._on_event(str(plugins_dir / "newp" / ".approved"))
    await asyncio.sleep(0.3)
    assert reg.get_record("newp") is None  # 未 refresh
    watcher.stop()
