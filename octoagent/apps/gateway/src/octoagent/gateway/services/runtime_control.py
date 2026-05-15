"""Feature 037: runtime control context helpers。

F091 Phase C: 加 is_single_loop_main_active / is_recall_planner_skip helpers，
统一各服务（orchestrator / llm_service / task_service）读取 single_loop 控制流的入口。
读取优先级：
1. runtime_context.delegation_mode / recall_planner_mode（F090 引入显式字段）
2. metadata flag fallback（F091 兼容期；F100 收口删除）
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from octoagent.core.models import RuntimeControlContext

RUNTIME_CONTEXT_KEY = "runtime_context"
RUNTIME_CONTEXT_JSON_KEY = "runtime_context_json"

_SINGLE_LOOP_DELEGATION_MODES: frozenset[str] = frozenset({"main_inline", "worker_inline"})


def encode_runtime_context(context: RuntimeControlContext) -> str:
    """序列化 runtime context，供 string-only metadata 透传。"""

    return context.model_dump_json(exclude_none=True)


def decode_runtime_context(value: Any) -> RuntimeControlContext | None:
    """从 dict / JSON / model 解析 runtime context。"""

    if value is None:
        return None
    if isinstance(value, RuntimeControlContext):
        return value
    if isinstance(value, Mapping):
        try:
            return RuntimeControlContext.model_validate(dict(value))
        except Exception:
            return None
    if isinstance(value, str) and value.strip():
        try:
            return RuntimeControlContext.model_validate_json(value)
        except Exception:
            return None
    return None


def runtime_context_from_metadata(
    metadata: Mapping[str, Any] | None,
) -> RuntimeControlContext | None:
    """从 work/dispatch metadata 中提取 runtime context。"""

    if metadata is None:
        return None
    parsed = decode_runtime_context(metadata.get(RUNTIME_CONTEXT_KEY))
    if parsed is not None:
        return parsed
    return decode_runtime_context(metadata.get(RUNTIME_CONTEXT_JSON_KEY))


def metadata_flag(metadata: Mapping[str, Any] | None, key: str) -> bool:
    """通用 metadata flag 解析（与各服务内同名 helper 行为一致）。

    Feature 091 Phase C: 抽到 runtime_control 作为单一来源；
    runtime_control 之外的 generic flag 仍可继续用各服务的 _metadata_flag。
    """
    if metadata is None:
        return False
    value = metadata.get(key)
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def is_single_loop_main_active(
    runtime_context: RuntimeControlContext | None,
    metadata: Mapping[str, Any] | None,
) -> bool:
    """当前轮次是否走 single-loop 主路径（main 或 worker 自跑）。

    True → 跳过 recall planner / DelegationPlane / agent decision phase 等标准链路；
    False → 走标准 delegation。

    F091 Phase C 读取优先级：
    1. runtime_context.delegation_mode in {"main_inline", "worker_inline"} → True
    2. runtime_context.delegation_mode in {"main_delegate", "subagent"} → False
    3. delegation_mode == "unspecified" 或 runtime_context = None → fallback metadata flag
    F100 收口：删除 fallback；强制 runtime_context 必填 + delegation_mode 显式。
    """
    if runtime_context is not None and runtime_context.delegation_mode != "unspecified":
        return runtime_context.delegation_mode in _SINGLE_LOOP_DELEGATION_MODES
    return metadata_flag(metadata, "single_loop_executor")


def is_recall_planner_skip(
    runtime_context: RuntimeControlContext | None,
    metadata: Mapping[str, Any] | None,
) -> bool:
    """当前轮次是否跳过 recall planner。

    F100 Phase D 启用 RecallPlannerMode "auto" + force_full_recall override：

    读取优先级：
    1. runtime_context.force_full_recall == True → 始终 False（H1 完整决策环 override）
    2. runtime_context.delegation_mode != "unspecified"（已显式）→ 看 recall_planner_mode：
       - "skip" → True
       - "full" → False
       - "auto" → 依 delegation_mode 自动决议：
         - main_inline / worker_inline → True (skip，兼容 F051 性能优势)
         - main_delegate / subagent → False (full，走完整决策环)
    3. runtime_context.delegation_mode == "unspecified" 或 runtime_context = None
       → return False（F100 v0.3：移除 metadata fallback；与 baseline 默认行为等价——
       baseline 在 metadata 缺 single_loop_executor 时 fallback 也返回 False）

    F100 v0.3 修订（phase-c-audit.md）：原 v0.2 "consumed 时 raise" 会破坏 chat 主链
    （3/4 consumed 时点是 pre-decision），改为 unspecified/None → return False。
    """
    # F100 FR-A2：force_full_recall override 优先级最高
    if runtime_context is not None and runtime_context.force_full_recall:
        return False

    if runtime_context is not None and runtime_context.delegation_mode != "unspecified":
        if runtime_context.recall_planner_mode == "skip":
            return True
        if runtime_context.recall_planner_mode == "full":
            return False
        # F100 FR-A1：AUTO 决议启用——依 delegation_mode 自动决议
        if runtime_context.recall_planner_mode == "auto":
            if runtime_context.delegation_mode in {"main_inline", "worker_inline"}:
                return True  # AUTO + inline → skip（F051 性能兼容）
            if runtime_context.delegation_mode in {"main_delegate", "subagent"}:
                return False  # AUTO + delegate/subagent → full（H1 完整决策环）
            # defense-in-depth：上面已枚举所有 DelegationMode 取值（不含 unspecified）
            raise ValueError(
                f"AUTO recall_planner_mode 遇到未预期的 delegation_mode: "
                f"{runtime_context.delegation_mode}"
            )
        raise ValueError(
            f"Unknown recall_planner_mode: {runtime_context.recall_planner_mode}"
        )
    # F100 Phase D：fallback metadata flag 保留（Phase E2 移除，统一改为 return False）
    return metadata_flag(metadata, "single_loop_executor")
