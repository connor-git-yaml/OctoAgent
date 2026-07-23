"""F103d Phase D T-D-7 — ``octo-bench`` CLI thin wrapper.

仅 entry point；实际逻辑在 ``benchmarks.runner.cli:main``。

Lazy import ``benchmarks.runner.cli``：让 gateway 包不硬依赖 benchmarks/
（FR-H01 零侵入：apps/gateway 仅本文件新增 + pyproject.toml 1 行 entry point）。
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

from octoagent.gateway.cli.install_bootstrap import resolve_managed_source_checkout


def app(argv: Sequence[str] | None = None) -> None:
    """``octo-bench`` entry point。

    Phase D 方案 A：独立命令（不修改 octoagent.gateway.cli.cli:main）；
    spec 文字提到的 ``octo bench daily`` 等价为 ``octo-bench daily``。
    """
    resolve_managed_source_checkout(Path.cwd())

    from benchmarks.runner.cli import main as _main

    sys.exit(_main(argv if argv is not None else sys.argv[1:]))


if __name__ == "__main__":
    app()
