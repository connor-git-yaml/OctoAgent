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

    F091 Phase C/Final-review 修复（Codex M2 闭环）：
    `recall_planner_mode` 仅在 `delegation_mode` 已显式时（非 "unspecified"）作为权威。
    当 `delegation_mode == "unspecified"`（默认值）时，必须 fallback 到 metadata flag——
    否则"默认 RuntimeControlContext + metadata['single_loop_executor']=True"在旧逻辑里
    会 skip recall planner，新逻辑因 recall_planner_mode 默认 "full" 反而不 skip，行为变了。

    读取优先级：
    1. runtime_context.delegation_mode != "unspecified"（已显式）→ 看 recall_planner_mode：
       - "skip" → True
       - "full" → False
       - "auto" → raise NotImplementedError（防止 F091 隐式定义；F100 启用）
    2. runtime_context.delegation_mode == "unspecified" 或 runtime_context = None
       → fallback metadata flag（保持旧逻辑等价）

    F100 收口：实施 "auto" 实际语义（依 delegation_mode 自动决议）+ 删除 metadata fallback。
    """
    if runtime_context is not None and runtime_context.delegation_mode != "unspecified":
        if runtime_context.recall_planner_mode == "skip":
            return True
        if runtime_context.recall_planner_mode == "full":
            return False
        # "auto" 显式 fail-fast，防止 F091 时通过 fallback 偷偷固化语义
        raise NotImplementedError(
            'RecallPlannerMode "auto" not implemented in F091; F100 will enable.'
            ' Use "skip" or "full" explicitly.'
        )
    # delegation_mode = unspecified（默认）或 runtime_context = None → fallback metadata flag
    return metadata_flag(metadata, "single_loop_executor")
