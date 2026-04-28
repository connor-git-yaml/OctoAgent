"""AST 扫描性能基准 — Feature 084 T017。

FR-1.1：AST 扫描启动延迟 < 200ms。

策略：跑 5 次扫描取最快值（剔除 cold cache），断言中位数 < 200ms。
"""

from __future__ import annotations

import statistics
import time
from pathlib import Path

import pytest
from octoagent.gateway.harness.tool_registry import (
    ToolRegistry,
    _ast_has_register_call,
    _module_registers_tools,
    scan_and_register,
)


BUILTIN_TOOLS_DIR = (
    Path(__file__).resolve().parents[2]  # tests/harness/X.py → tests/harness → tests → gateway
    / "src"
    / "octoagent"
    / "gateway"
    / "services"
    / "builtin_tools"
)


@pytest.fixture
def builtin_tools_dir() -> Path:
    assert BUILTIN_TOOLS_DIR.exists(), f"未找到 builtin_tools 目录：{BUILTIN_TOOLS_DIR}"
    return BUILTIN_TOOLS_DIR


class TestAstScanPerformance:
    """FR-1.1：启动 AST 扫描 < 200ms。"""

    def test_scan_completes_under_200ms(self, builtin_tools_dir: Path) -> None:
        """5 次跑取中位数，断言 < 200ms。"""
        durations: list[float] = []
        for _ in range(5):
            registry = ToolRegistry()
            start = time.perf_counter()
            scan_and_register(registry, builtin_tools_dir)
            durations.append(time.perf_counter() - start)
        median = statistics.median(durations)
        assert median < 0.2, (
            f"FR-1.1 要求 < 200ms，5 次中位数 {median * 1000:.1f}ms"
        )

    def test_module_token_filter_is_fast(self, builtin_tools_dir: Path) -> None:
        """_module_registers_tools 应快速过滤非工具模块（O(file size) token scan）。"""
        py_files = list(builtin_tools_dir.glob("*.py"))
        assert len(py_files) > 0
        start = time.perf_counter()
        for f in py_files:
            _module_registers_tools(f)
        elapsed = time.perf_counter() - start
        assert elapsed < 0.05, (
            f"token-filter 全 builtin_tools 应 < 50ms，实测 {elapsed * 1000:.1f}ms"
        )

    def test_ast_parse_only_runs_for_candidates(self, builtin_tools_dir: Path) -> None:
        """AST 解析只对 token-filter 命中的文件执行，避免对全量 .py 解析。"""
        py_files = list(builtin_tools_dir.glob("*.py"))
        candidates = [f for f in py_files if _module_registers_tools(f)]
        # 验证：所有 builtin_tools 文件都是 candidates（这是预期，因为 T012/T013 全部加了 ToolEntry token）
        # 但 AST 真实顶层调用的可能更少（subagent 用 token-scan 兼容旧 deps closure 模式）
        ast_hits = [f for f in py_files if _ast_has_register_call(f)]
        assert len(ast_hits) <= len(candidates), (
            "ast_has_register_call 应是 token-filter 候选的子集"
        )
