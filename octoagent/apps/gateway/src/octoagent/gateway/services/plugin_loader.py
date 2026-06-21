"""code plugin 专用工具/hooks 加载 path（F106 Phase B，review H1/H2/H3 + MED-1）。

**不 reuse `scan_and_register`**——它 `exec_module` 执行代码 + 忽略 `registry` 参数（写全局
单例）+ 覆盖语义无冲突拒绝（tool_registry.py:124/305/317）。专用 path：

- **仅对 ENABLED + code_hash 匹配的 plugin 调用**（never import unless approved，FR-2.1）。
- importlib **namespaced** 模块 `octoagent_plugins.<name>.*`（避免 sys.modules 撞名污染兄弟 plugin，H3）。
- 约定：plugin 工具模块暴露 module-level `PLUGIN_TOOLS: list[ToolEntry]`。
- **MED-1 防护**：import 前后快照 registry——若 plugin import 期**直接调全局 `register()`**
  篡改了既有/新工具（绕过 staging 冲突预检），还原 registry + 拒载（合作型 plugin 仍守"reject not overwrite"）。
- staging 预检名冲突（现有 registry + 本 plugin 内）→ 冲突拒该工具（不覆盖）。
- 事务性：记已注册名；任一步失败 → deregister 已注册 + `sys.modules.pop`（防半注册孤儿，H3/M4）。
- hooks（FR-3.4）：已审批 code plugin 的 `hooks.py` on_load/on_unload lifecycle，隔离调用。

诚实边界（spec §0.3）：`exec_module` 执行 plugin 顶层代码 = 进程内任意 Python。MED-1 只闭合
"import 自动 register 篡改"；on_load/on_unload 等显式回调内的 monkeypatch 属已审批代码残余（需 v0.2 沙箱）。
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from ..harness.tool_registry import ToolEntry, ToolRegistry

log = structlog.get_logger(__name__)

PLUGIN_MODULE_PREFIX = "octoagent_plugins"
PLUGIN_TOOLS_ATTR = "PLUGIN_TOOLS"
PLUGIN_HOOKS_FILE = "hooks.py"


class PluginLoadError(Exception):
    """plugin 代码加载/注册失败。collision 非 None 表示工具名冲突（registry 映射 NAME_COLLISION）。"""

    def __init__(self, message: str, *, collision: str | None = None) -> None:
        super().__init__(message)
        self.collision = collision


@dataclass
class LoadedPluginCode:
    """一个已加载 code plugin 的运行态（用于卸载/回滚 + on_unload hook）。"""

    plugin_name: str
    module_names: list[str] = field(default_factory=list)
    registered_tool_names: list[str] = field(default_factory=list)
    hooks_module: Any | None = None


def load_plugin_tools(
    plugin_name: str,
    plugin_dir: Path,
    tool_modules: list[str],
    registry: ToolRegistry,
    *,
    load_hooks: bool = False,
) -> LoadedPluginCode:
    """加载 plugin 工具/hooks 模块（importlib namespaced）+ MED-1 防护 + 事务注册 + on_load。

    **调用前必须已确认 plugin 处于 ENABLED 且 code_hash 匹配审批**（FR-2.1）。
    """
    loaded = LoadedPluginCode(plugin_name=plugin_name)
    staged: list[ToolEntry] = []
    resolved_root = plugin_dir.resolve()
    safe_ns = plugin_name.replace("-", "_")
    # MED-1：import 前快照 registry（name -> entry 对象身份），用于检测 import 期直接 register() 篡改
    before = {e.name: e for e in registry._snapshot_entries()}
    try:
        # 1) import 全部工具模块 + hooks 模块（执行 plugin 顶层代码）
        for mod_rel in tool_modules:
            module = _import_module(plugin_name, plugin_dir, resolved_root, safe_ns, mod_rel, loaded)
            tools = getattr(module, PLUGIN_TOOLS_ATTR, None)
            if tools is None:
                continue
            if not isinstance(tools, (list, tuple)):
                raise PluginLoadError(f"{mod_rel} 的 {PLUGIN_TOOLS_ATTR} 须为 list[ToolEntry]")
            for entry in tools:
                if not isinstance(entry, ToolEntry):
                    raise PluginLoadError(f"{mod_rel} 的 {PLUGIN_TOOLS_ATTR} 含非 ToolEntry 项")
                staged.append(entry)
        if load_hooks:
            hooks_path = (plugin_dir / PLUGIN_HOOKS_FILE)
            if hooks_path.is_file():
                loaded.hooks_module = _import_module(
                    plugin_name, plugin_dir, resolved_root, safe_ns, PLUGIN_HOOKS_FILE, loaded
                )

        # 2) MED-1：检测 import 期是否直接调全局 register() 篡改既有/新工具 → 还原 + 拒载
        after = {e.name: e for e in registry._snapshot_entries()}
        unauthorized = [n for n, e in after.items() if before.get(n) is not e]
        if unauthorized:
            for n in unauthorized:
                if n in before:
                    registry.register(before[n])  # 还原被覆盖的原工具
                else:
                    registry.deregister(n)  # 移除 plugin 注入的新工具
            raise PluginLoadError(
                f"plugin import 期直接调用全局 register() 篡改工具 {unauthorized}（须经 PLUGIN_TOOLS）",
                collision=unauthorized[0],
            )

        # 3) staging 冲突预检（现有 registry + 本 plugin 内）
        seen: set[str] = set()
        for entry in staged:
            if entry.name in registry:
                raise PluginLoadError(f"工具名与现有冲突: {entry.name}", collision=entry.name)
            if entry.name in seen:
                raise PluginLoadError(f"plugin 内工具名重复: {entry.name}", collision=entry.name)
            seen.add(entry.name)

        # 4) 事务注册
        for entry in staged:
            registry.register(entry)
            loaded.registered_tool_names.append(entry.name)

        # 5) on_load lifecycle hook（隔离；失败不拖垮 plugin）
        _call_hook(loaded, "on_load")

        log.info("plugin_tools_loaded", plugin=plugin_name, tools=loaded.registered_tool_names)
        return loaded
    except Exception:
        _rollback(loaded, registry)
        raise


def _import_module(
    plugin_name: str,
    plugin_dir: Path,
    resolved_root: Path,
    safe_ns: str,
    mod_rel: str,
    loaded: LoadedPluginCode,
):
    # 拒模块路径含 symlink（文件本身或中间目录）——code 模块须 plugin 树内真实文件
    # （review H-1 defense：symlink resolve 后执行的字节可能 ≠ 审批 hash）。
    cur = plugin_dir / mod_rel
    while cur != plugin_dir and cur.parent != cur:
        if cur.is_symlink():
            raise PluginLoadError(f"模块路径含 symlink（拒）: {mod_rel}")
        cur = cur.parent
    mod_path = (plugin_dir / mod_rel).resolve()
    if mod_path != resolved_root and resolved_root not in mod_path.parents:
        raise PluginLoadError(f"模块逃逸 plugin 目录: {mod_rel}")
    if not mod_path.is_file():
        raise PluginLoadError(f"模块不存在: {mod_rel}")
    module_name = f"{PLUGIN_MODULE_PREFIX}.{safe_ns}.{mod_path.stem}"
    module = _import_isolated(module_name, mod_path)
    loaded.module_names.append(module_name)
    return module


def _import_isolated(module_name: str, filepath: Path):
    spec = importlib.util.spec_from_file_location(module_name, filepath)
    if spec is None or spec.loader is None:
        raise PluginLoadError(f"无法构造模块 spec: {filepath}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)  # 清理半初始化模块（review M4）
        raise
    return module


def _call_hook(loaded: LoadedPluginCode, hook_name: str) -> None:
    if loaded.hooks_module is None:
        return
    hook = getattr(loaded.hooks_module, hook_name, None)
    if callable(hook):
        try:
            hook()
        except Exception:
            log.warning("plugin_hook_failed", plugin=loaded.plugin_name, hook=hook_name, exc_info=True)


def _rollback(loaded: LoadedPluginCode, registry: ToolRegistry) -> None:
    for name in loaded.registered_tool_names:
        registry.deregister(name)
    for mod in loaded.module_names:
        sys.modules.pop(mod, None)
    loaded.registered_tool_names.clear()
    loaded.module_names.clear()
    loaded.hooks_module = None


def unload_plugin_code(loaded: LoadedPluginCode, registry: ToolRegistry) -> None:
    """卸载 plugin 代码：on_unload + deregister 工具 + sys.modules evict（disable / 卸载 / 换码，FR-10.3）。"""
    _call_hook(loaded, "on_unload")
    _rollback(loaded, registry)
