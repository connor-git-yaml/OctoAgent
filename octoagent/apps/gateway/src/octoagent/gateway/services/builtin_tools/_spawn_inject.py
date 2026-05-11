"""F099 Phase C: spawn 路径 source_runtime_kind 注入辅助函数。

OD-F099-6 落实（工具层注入，不在 plane 层）：
- 注入决策（什么时候注入、什么值）由工具层（delegate_task_tool / delegation_tools）决定
- plane 层（spawn_child / _launch_child_task）仅透传 extra_control_metadata 参数

plan §4 R5 缓解：提取共用辅助函数 _inject_worker_source_metadata，
delegate_task_tool 和 delegation_tools 调用同一实现，保证两处注入逻辑一致。

FR-C2 + FR-C3 实现：worker→worker dispatch 时在 control_metadata 中注入
source_runtime_kind=worker，修复 F098 已知 LOW §3（spawn 路径注入缺失）。
"""

from __future__ import annotations

from typing import Any

import structlog

from octoagent.core.models.source_kinds import SOURCE_RUNTIME_KIND_WORKER
from octoagent.gateway.services.execution_context import get_current_execution_context

log = structlog.get_logger(__name__)


def inject_worker_source_metadata() -> dict[str, Any]:
    """F099 Phase C: 在 spawn 路径中注入 source_runtime_kind=worker（FR-C2 / FR-C3）。

    F099 Codex Final F1 修复：仅信任显式 is_caller_worker 信号（同 F098 Phase D Post-Review 修法）。
    不使用 runtime_kind 派生注入条件——runtime_kind 是 target 侧字段（owner-self 路径也为 "worker"），
    用它做 caller 身份判断会误将主 Agent owner-self 路径注入为 source=worker。

    注入条件（AC-C2 后向兼容）：
    - 真实 worker dispatch 路径（worker_runtime.py 构造 context）：is_caller_worker=True → 注入
    - owner-self 主 Agent 自执行路径（orchestrator 构造 context）：is_caller_worker=False → 不注入
    - 主 Agent 调用 delegate_task（无 execution_context）：无 context → 不注入 → MAIN 路径不变

    Returns:
        dict：包含 source_runtime_kind=worker + 可选的 source_worker_capability 字段，
              或空 dict（非 worker dispatch 环境）。
    """
    try:
        exec_ctx = get_current_execution_context()
    except RuntimeError:
        # 没有 execution_context（通常是主 Agent 路径或测试环境）→ 不注入
        return {}

    # 仅在真实 worker dispatch 路径注入（is_caller_worker=True）
    # AC-C2：owner-self 路径（is_caller_worker=False）不注入，保持 MAIN baseline
    if not exec_ctx.is_caller_worker:
        return {}

    extra: dict[str, Any] = {
        "source_runtime_kind": SOURCE_RUNTIME_KIND_WORKER,
    }

    # 可选：注入 worker_capability（从 worker_id 推断 capability 标签）
    # worker_id 形如 "worker:<profile_id>" 或直接是 profile_id
    worker_id = exec_ctx.worker_id or ""
    if worker_id:
        # 提取 capability 标签（去掉 "worker:" 前缀）
        capability = worker_id.removeprefix("worker:") if worker_id.startswith("worker:") else worker_id
        if capability:
            extra["source_worker_capability"] = capability

    log.debug(
        "spawn_inject_worker_source_metadata",
        runtime_kind=exec_ctx.runtime_kind,
        worker_id=worker_id,
        injected_keys=list(extra.keys()),
    )
    return extra
