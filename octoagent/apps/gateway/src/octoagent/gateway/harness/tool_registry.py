"""tool_registry.py：ToolRegistry + ToolEntry + AST 扫描（Feature 084 Phase 1）。

架构决策（plan.md D2）：
- AST 扫描 + module-level register() 调用模式（Hermes 移植，约 450 行）
- entrypoints 字段解决 D1 断层（web 入口工具可见性问题）
- ToolEntry schema 为 Constitution C3 单一事实源

FR 覆盖：FR-1.1（AST 扫描 < 200ms），FR-1.2（ToolEntry 字段），FR-1.3（entrypoints 过滤），FR-1.5（deregister 热更新）
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import sys
import threading
import time
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, ConfigDict

log = structlog.get_logger()

# 合法的入口点集合
VALID_ENTRYPOINTS: frozenset[str] = frozenset({"web", "agent_runtime", "telegram"})


class SideEffectLevel(str, Enum):
    """工具副作用级别（Constitution C3 要求声明）。"""

    NONE = "none"
    REVERSIBLE = "reversible"
    IRREVERSIBLE = "irreversible"


class ToolEntry(BaseModel):
    """工具注册单元，ToolRegistry 的基本管理单元（data-model.md）。

    Constitution C3：schema 字段是工具接口的单一事实源。
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    """工具唯一标识符，如 "user_profile.update"。"""

    entrypoints: frozenset[str]
    """工具可见的入口点子集，元素来自 {"web", "agent_runtime", "telegram"}。"""

    toolset: str
    """所属 toolset，如 "core"、"agent_only"。"""

    handler: Callable
    """可调用对象（不序列化到 JSON）。"""

    schema: type[BaseModel]
    """Pydantic BaseModel，Constitution C3 单一事实源。"""

    side_effect_level: SideEffectLevel
    """副作用级别（Constitution C3 要求声明）。"""

    description: str = ""
    """工具描述，LLM 可见。"""


class ToolNotFoundError(KeyError):
    """dispatch 不存在的工具时抛出。"""

    def __init__(self, tool_name: str) -> None:
        super().__init__(f"工具不存在：{tool_name!r}")
        self.tool_name = tool_name


class ToolRegistry:
    """工具注册表：管理 ToolEntry 的注册、查询、派发（FR-1.3/1.5）。

    线程安全：内部使用 threading.RLock 保护并发读写。
    """

    def __init__(self) -> None:
        self._entries: dict[str, ToolEntry] = {}
        self._lock = threading.RLock()

    def register(self, entry: ToolEntry) -> None:
        """注册工具。若工具名已存在则覆盖（热更新语义）。

        Args:
            entry: 要注册的 ToolEntry。
        """
        with self._lock:
            self._entries[entry.name] = entry
        log.debug("tool_registered", tool_name=entry.name, entrypoints=list(entry.entrypoints))

    def deregister(self, name: str) -> None:
        """热卸载工具（FR-1.5）。

        Args:
            name: 工具名称。若不存在则静默忽略。
        """
        with self._lock:
            removed = self._entries.pop(name, None)
        if removed is not None:
            log.debug("tool_deregistered", tool_name=name)

    def dispatch(self, name: str, args: dict[str, Any]) -> Any:
        """派发工具调用（FR-1.3）。

        Args:
            name: 工具名称。
            args: 工具参数字典。

        Returns:
            工具 handler 的返回值。

        Raises:
            ToolNotFoundError: 工具不存在时。
        """
        with self._lock:
            entry = self._entries.get(name)
        if entry is None:
            raise ToolNotFoundError(name)
        return entry.handler(**args)

    def list_for_entrypoint(self, entrypoint: str) -> list[ToolEntry]:
        """返回指定入口点可见的工具列表（FR-1.3 entrypoints 过滤）。

        Args:
            entrypoint: 入口点名称，如 "web"、"agent_runtime"、"telegram"。

        Returns:
            entrypoints 中包含 entrypoint 的 ToolEntry 列表。
        """
        with self._lock:
            entries = list(self._entries.values())
        return [e for e in entries if entrypoint in e.entrypoints]

    def _snapshot_entries(self) -> list[ToolEntry]:
        """返回所有已注册工具的无锁读副本。

        Returns:
            当前已注册的 ToolEntry 列表（快照，不持有锁）。
        """
        with self._lock:
            return list(self._entries.values())

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def __contains__(self, name: str) -> bool:
        with self._lock:
            return name in self._entries


# ---------------------------------------------------------------------------
# 全局 ToolRegistry 单例 + module-level register()
# ---------------------------------------------------------------------------

_REGISTRY: ToolRegistry = ToolRegistry()


def register(entry: ToolEntry) -> None:
    """Module-level 注册函数：将 ToolEntry 注册到全局 _REGISTRY 单例。

    设计意图：工具模块在顶层调用 ``register(ToolEntry(...))`，
    使 AST 扫描可以通过检测该调用模式快速识别含工具注册的模块。

    Args:
        entry: 要注册的 ToolEntry。
    """
    _REGISTRY.register(entry)


def get_registry() -> ToolRegistry:
    """获取全局 ToolRegistry 单例。

    Returns:
        全局 ToolRegistry 实例。
    """
    return _REGISTRY


# ---------------------------------------------------------------------------
# AST 扫描 + 自动注册（FR-1.1 < 200ms）
# ---------------------------------------------------------------------------


def _module_registers_tools(filepath: Path) -> bool:
    """快速过滤：检查文件是否包含 register(ToolEntry(...)) 调用 token。

    通过字符串搜索快速跳过无关模块，避免完整 AST 解析开销。

    Args:
        filepath: 要检查的 Python 文件路径。

    Returns:
        若文件可能含工具注册调用则返回 True，否则 False。
    """
    try:
        content = filepath.read_text(encoding="utf-8", errors="ignore")
        # 快速 token 检测：包含 "register(" 且包含 "ToolEntry(" 的文件才做 AST 解析
        return "register(" in content and "ToolEntry(" in content
    except OSError:
        return False


def _ast_has_register_call(filepath: Path) -> bool:
    """使用 AST 解析确认文件是否含顶层 register(ToolEntry(...)) 调用。

    只有通过 _module_registers_tools 快速过滤的文件才进入此函数。

    Args:
        filepath: 已通过快速过滤的 Python 文件路径。

    Returns:
        若存在顶层 register(ToolEntry(...)) 调用则返回 True，否则 False。
    """
    try:
        source = filepath.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError:
        return False

    # 只检测模块顶层语句（不检测函数/类内部的注册）
    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.Expr):
            continue
        call = node.value
        if not isinstance(call, ast.Call):
            continue
        # 检测 register(...) 或 module.register(...)
        func = call.func
        is_register = (
            (isinstance(func, ast.Name) and func.id == "register")
            or (isinstance(func, ast.Attribute) and func.attr == "register")
        )
        if not is_register:
            continue
        # 检测第一个参数是否是 ToolEntry(...)
        if call.args:
            first_arg = call.args[0]
            if isinstance(first_arg, ast.Call):
                inner_func = first_arg.func
                is_tool_entry = (
                    (isinstance(inner_func, ast.Name) and inner_func.id == "ToolEntry")
                    or (isinstance(inner_func, ast.Attribute) and inner_func.attr == "ToolEntry")
                )
                if is_tool_entry:
                    return True
    return False


def _filepath_to_module_name(filepath: Path, tools_dir: Path) -> str:
    """将文件路径转换为 Python 模块名（相对于 tools_dir）。

    Args:
        filepath: .py 文件的绝对路径。
        tools_dir: 工具目录的根路径。

    Returns:
        Python 模块名，如 "octoagent.gateway.services.builtin_tools.memory_tools"。
    """
    try:
        rel = filepath.relative_to(tools_dir.parent)
        parts = list(rel.with_suffix("").parts)
        return ".".join(parts)
    except ValueError:
        # 无法计算相对路径，直接用文件名
        return filepath.stem


def scan_and_register(registry: ToolRegistry, tools_dir: Path) -> int:
    """AST 扫描 tools_dir，检测含 register(ToolEntry(...)) 调用的模块并动态 import。

    扫描流程：
    1. 遍历 tools_dir 所有 .py 文件
    2. _module_registers_tools() 快速 token 过滤
    3. _ast_has_register_call() AST 精确确认
    4. importlib 动态导入模块（模块顶层执行 register(ToolEntry(...))）
    5. 记录计时，超 200ms 写 WARN 日志（FR-1.1 R8 缓解）

    Args:
        registry: 目标 ToolRegistry，模块 import 时会向此 registry 注册工具。
                  注意：当前实现工具模块调用全局 register()，因此 registry 参数
                  作为语义标记保留，Phase 2 可改为依赖注入。
        tools_dir: 要扫描的目录路径。

    Returns:
        本次扫描新注册的工具数量。
    """
    if not tools_dir.is_dir():
        log.warning("scan_and_register_dir_not_found", tools_dir=str(tools_dir))
        return 0

    start_ns = time.perf_counter_ns()
    registered_before = len(registry)
    scanned_files = 0
    imported_modules = 0
    errors: list[str] = []

    py_files = list(tools_dir.rglob("*.py"))
    for filepath in py_files:
        if filepath.name.startswith("_") and filepath.name != "__init__.py":
            continue
        if "__pycache__" in filepath.parts:
            continue

        scanned_files += 1

        # 快速 token 过滤
        if not _module_registers_tools(filepath):
            continue

        # AST 精确确认
        if not _ast_has_register_call(filepath):
            continue

        # 动态 import 模块（模块顶层会执行 register(ToolEntry(...))）
        module_name = _filepath_to_module_name(filepath, tools_dir)
        if module_name in sys.modules:
            # 模块已加载，触发模块级代码重新执行可能有副作用，跳过
            imported_modules += 1
            continue

        try:
            spec = importlib.util.spec_from_file_location(module_name, filepath)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            imported_modules += 1
        except Exception as exc:
            errors.append(f"{filepath.name}: {exc}")
            log.warning(
                "scan_and_register_import_error",
                filepath=str(filepath),
                error=str(exc),
            )

    elapsed_ms = (time.perf_counter_ns() - start_ns) / 1_000_000
    registered_now = len(registry) - registered_before

    log.info(
        "scan_and_register_complete",
        tools_dir=str(tools_dir),
        scanned_files=scanned_files,
        imported_modules=imported_modules,
        newly_registered=registered_now,
        elapsed_ms=round(elapsed_ms, 2),
        errors=len(errors),
    )

    if elapsed_ms > 200:
        log.warning(
            "scan_and_register_slow",
            elapsed_ms=round(elapsed_ms, 2),
            threshold_ms=200,
            tools_dir=str(tools_dir),
        )

    return registered_now
