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

    仅在 worker 执行环境中注入：
    - 通过 get_current_execution_context() 检测当前是否在 worker context 下运行
    - runtime_kind == "worker" 时注入 source_runtime_kind=worker
    - 主 Agent dispatch（无 execution_context 或 runtime_kind != "worker"）时返回空 dict

    注入条件（AC-C2 后向兼容）：
    - 主 Agent 调用 delegate_task：无 execution_context 或 runtime_kind 非 worker → 不注入 → MAIN 路径不变
    - Worker 调用 delegate_task：runtime_kind == "worker" → 注入 → WORKER 路径正确派生

    Returns:
        dict：包含 source_runtime_kind=worker + 可选的 source_worker_capability 字段，
              或空 dict（非 worker 环境）。
    """
    try:
        exec_ctx = get_current_execution_context()
    except RuntimeError:
        # 没有 execution_context（通常是主 Agent 路径或测试环境）→ 不注入
        return {}

    # 仅在 worker 环境注入（AC-C2：主 Agent 不误注入）
    if exec_ctx.runtime_kind != "worker":
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
