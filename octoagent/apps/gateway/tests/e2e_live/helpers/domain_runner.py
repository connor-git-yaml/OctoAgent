"""F087 P2 T-P2-15：domain_runner（CLI 单跑用，``octo e2e <id>`` 复用）。

13 域注册表 + ``run_domain(domain_id)`` 转发到对应 pytest node ID。

P3/P4 case 实际写好后，此处的 node ID 必须存在；P2 阶段先建立注册表骨架
（指向尚未存在的 test 文件路径，但 ``octo e2e --list`` 仍能列出域名）。
"""

from __future__ import annotations

import subprocess
import sys
from typing import NamedTuple


class DomainSpec(NamedTuple):
    """13 能力域规格。"""

    domain_id: int
    name: str
    marker: str  # "e2e_smoke" / "e2e_full"
    pytest_node_id: str  # 转发用的 pytest 节点 ID


# 13 域注册表（与 spec §13 对齐）
DOMAIN_REGISTRY: tuple[DomainSpec, ...] = (
    DomainSpec(1, "工具调用基础", "e2e_smoke",
               "apps/gateway/tests/e2e_live/test_e2e_basic_tool_context.py::test_domain_1"),
    DomainSpec(2, "USER.md 全链路", "e2e_smoke",
               "apps/gateway/tests/e2e_live/test_e2e_basic_tool_context.py::test_domain_2"),
    DomainSpec(3, "Context 冻结快照", "e2e_smoke",
               "apps/gateway/tests/e2e_live/test_e2e_basic_tool_context.py::test_domain_3"),
    DomainSpec(4, "Memory observation→promote", "e2e_full",
               "apps/gateway/tests/e2e_live/test_e2e_memory_pipeline.py::test_domain_4"),
    DomainSpec(5, "真实 Perplexity MCP", "e2e_full",
               "apps/gateway/tests/e2e_live/test_e2e_mcp_skill_pipeline.py::test_domain_5"),
    DomainSpec(6, "Skill 调用", "e2e_full",
               "apps/gateway/tests/e2e_live/test_e2e_mcp_skill_pipeline.py::test_domain_6"),
    DomainSpec(7, "Graph Pipeline", "e2e_full",
               "apps/gateway/tests/e2e_live/test_e2e_mcp_skill_pipeline.py::test_domain_7"),
    DomainSpec(8, "delegate_task", "e2e_full",
               "apps/gateway/tests/e2e_live/test_e2e_delegation_a2a.py::test_domain_8"),
    DomainSpec(9, "Sub-agent max_depth=2", "e2e_full",
               "apps/gateway/tests/e2e_live/test_e2e_delegation_a2a.py::test_domain_9"),
    DomainSpec(10, "A2A 通信", "e2e_full",
               "apps/gateway/tests/e2e_live/test_e2e_delegation_a2a.py::test_domain_10"),
    DomainSpec(11, "ThreatScanner block", "e2e_smoke",
               "apps/gateway/tests/e2e_live/test_e2e_safety_gates.py::test_domain_11"),
    DomainSpec(12, "ApprovalGate SSE", "e2e_smoke",
               "apps/gateway/tests/e2e_live/test_e2e_safety_gates.py::test_domain_12"),
    DomainSpec(13, "Routine cron / webhook", "e2e_full",
               "apps/gateway/tests/e2e_live/test_e2e_routine.py::test_domain_13"),
)


def list_domains() -> list[str]:
    """``octo e2e --list`` 输出格式行。"""
    return [
        f"#{d.domain_id:>2}  [{d.marker:>9}]  {d.name}"
        for d in DOMAIN_REGISTRY
    ]


def find_domain(domain_id: int) -> DomainSpec | None:
    for d in DOMAIN_REGISTRY:
        if d.domain_id == domain_id:
            return d
    return None


def run_domain(domain_id: int) -> int:
    """转发到对应 pytest node。返回 pytest exit code。

    若 domain_id 无效返回 2（pytest "USAGE_ERROR"）。
    """
    spec = find_domain(domain_id)
    if spec is None:
        print(f"[F087 e2e] 未知 domain_id={domain_id}; 用 --list 查看")
        return 2
    cmd = [sys.executable, "-m", "pytest", spec.pytest_node_id, "-q"]
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
