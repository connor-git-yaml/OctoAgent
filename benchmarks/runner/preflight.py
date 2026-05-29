"""benchmarks/runner/preflight.py — Phase B 起点环境自检

Tier 2 benchmark 必须 import tau_bench + datasets，否则 fail-fast。

PoC 决策（phase-0-poc-report.md §1 §3 §10）：
- tau-bench / datasets 不加入 OctoAgent pyproject.toml（避免污染 production 依赖）
- 每次 worktree 重建需手动 `uv pip install` 追加
- Runner 启动时自检 fail-fast 提示，不 silent skip

Usage:
    from benchmarks.runner.preflight import check_or_fail
    check_or_fail()  # 缺包 → SystemExit(2) + 明确 install 命令到 stderr
"""
from __future__ import annotations

import sys
from typing import Final

# PoC §1 实测验证的 install 命令（直接给用户复制粘贴）
INSTALL_COMMAND: Final[str] = (
    'uv pip install "git+https://github.com/sierra-research/tau-bench.git" datasets'
)

# (import_module_name, pip_distribution_name)
REQUIRED_PACKAGES: Final[tuple[tuple[str, str], ...]] = (
    ("tau_bench", "tau-bench"),
    ("datasets", "datasets"),
)


def _missing_packages() -> list[str]:
    """返回缺失的 import_module_name 列表（不抛异常，供 check_or_fail / 单测复用）。"""
    missing: list[str] = []
    for module_name, _ in REQUIRED_PACKAGES:
        try:
            __import__(module_name)
        except ImportError:
            missing.append(module_name)
    return missing


def check_or_fail(*, exit_code: int = 2) -> None:
    """检查 Tier 2 benchmark 必需的第三方包是否可 import。

    缺失时 fail-fast：打印明确 install 命令到 stderr，调用 SystemExit(exit_code)。
    PoC §3 决策：不 silent skip，不进入 silent fallback，让用户看到精确错误。

    Args:
        exit_code: 缺包时调用 SystemExit 的退出码（默认 2，与 GNU misuse-of-shell 一致）。

    Raises:
        SystemExit: 任一必需包缺失时立即退出（带退出码 exit_code）。
    """
    missing = _missing_packages()
    if not missing:
        return

    msg = (
        f"[preflight FAIL] Tier 2 benchmark adapter 缺失必需包: {', '.join(missing)}\n"
        f"  install command:\n"
        f"    {INSTALL_COMMAND}\n"
        f"  PoC 决策（phase-0-poc-report.md §1）: tau-bench / datasets 不加入\n"
        f"  OctoAgent pyproject.toml（避免污染 production）；每次 worktree 重建\n"
        f"  需手动追加。"
    )
    print(msg, file=sys.stderr)
    raise SystemExit(exit_code)


def get_required_packages() -> list[tuple[str, str]]:
    """获取必需包清单（供 unit test / runner 启动日志使用）。"""
    return list(REQUIRED_PACKAGES)


if __name__ == "__main__":
    # 作为 module 直接跑：python -m benchmarks.runner.preflight
    check_or_fail()
    print("[preflight OK] tau_bench + datasets 已就绪", file=sys.stderr)
