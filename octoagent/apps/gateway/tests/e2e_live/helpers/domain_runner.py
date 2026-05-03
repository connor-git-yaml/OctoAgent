"""F087 P2 T-P2-15：domain_runner（CLI 单跑用，``octo e2e <id>`` 复用）。

F087 final fixup#14（Codex final medium-5 闭环）：DOMAIN_REGISTRY 收敛到
``octoagent.gateway.cli.e2e_command`` 单一事实源。本模块改为 thin wrapper，
重新 export 兼容现有 ``test_domain_runner.py`` 测试。

历史漂移修复：
- 旧 ``DomainSpec.pytest_node_id`` 用精确 node ID（如 ``::test_domain_1``）
  pytest **不能匹配带后缀**的 test 函数（``test_domain_1_basic_*``）
- 新 ``DomainSpec`` 5 字段（含 ``file_path`` + ``pytest_keyword``），
  ``run_domain`` 用 ``pytest <file> -k <keyword>`` 真匹配
"""

from __future__ import annotations

import subprocess
import sys

# 单一事实源：cli/e2e_command.py
from octoagent.gateway.cli.e2e_command import (  # noqa: F401
    DOMAIN_REGISTRY,
    DomainSpec,
    find_domain,
)


def list_domains() -> list[str]:
    """``octo e2e --list`` 输出格式行。"""
    return [
        f"#{d.domain_id:>2}  [{d.marker:>9}]  {d.name}"
        for d in DOMAIN_REGISTRY
    ]


def run_domain(domain_id: int) -> int:
    """转发到对应 pytest node。返回 pytest exit code。

    若 domain_id 无效返回 2（pytest "USAGE_ERROR"）。
    用 ``pytest <file> -k <keyword>`` 模式（与 CLI 一致），可匹配带后缀
    的 test 函数（修复旧 pytest_node_id 漂移问题）。
    """
    spec = find_domain(domain_id)
    if spec is None:
        print(f"[F087 e2e] 未知 domain_id={domain_id}; 用 --list 查看")
        return 2
    cmd = [sys.executable, "-m", "pytest", spec.file_path, "-k", spec.pytest_keyword, "-q"]
    print(f"[F087 e2e] 跑 domain #{spec.domain_id}: {spec.name}")
    print(f"[F087 e2e] cmd: {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    return proc.returncode


__all__ = [
    "DomainSpec",
    "DOMAIN_REGISTRY",
    "list_domains",
    "find_domain",
    "run_domain",
]
