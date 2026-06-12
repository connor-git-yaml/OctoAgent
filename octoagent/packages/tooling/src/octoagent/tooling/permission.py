"""Feature 070: 统一权限决策 — 替代三套 Hook 体系

一个函数 check_permission() 完成所有权限判断：
1. always 覆盖快速路径
2. Preset × SideEffectLevel 矩阵查表
3. 需要时发起审批等待

不再使用 Hook Chain 做权限决策。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import structlog

from .models import (
    ExecutionContext,
    PermissionPreset,
    PresetDecision,
    SideEffectLevel,
    ToolMeta,
    preset_decision,
)

logger = structlog.get_logger(__name__)

# ------------------------------------------------------------------
# Protocol 接口（解耦 tooling 包和 policy 包）
# ------------------------------------------------------------------


class ApprovalOverrideCacheProtocol(Protocol):
    """always 覆盖缓存查询接口"""

    def has(self, agent_runtime_id: str, tool_name: str) -> bool: ...

    def set(self, agent_runtime_id: str, tool_name: str) -> None: ...


class _ApprovalOverrideMemoryCache:
    """Feature 061: ApprovalOverride 内存缓存

    实现 ApprovalOverrideCacheProtocol（hooks 依赖的接口），
    运行时 O(1) 查询，避免每次工具调用都查 SQLite。
    key = (agent_runtime_id, tool_name) → True
    """

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str], bool] = {}

    def has(self, agent_runtime_id: str, tool_name: str) -> bool:
        """检查缓存中是否存在 always 覆盖"""
        return self._cache.get((agent_runtime_id, tool_name), False)

    def set(self, agent_runtime_id: str, tool_name: str) -> None:
        """设置缓存条目"""
        self._cache[(agent_runtime_id, tool_name)] = True

    def remove(self, agent_runtime_id: str, tool_name: str) -> None:
        """移除缓存条目"""
        self._cache.pop((agent_runtime_id, tool_name), None)

    def load_from_records(self, records: list) -> None:
        """从 ApprovalOverride 记录批量加载缓存"""
        for record in records:
            self._cache[(record.agent_runtime_id, record.tool_name)] = True

    def clear_agent(self, agent_runtime_id: str) -> None:
        """清除指定 Agent 的所有缓存条目"""
        keys_to_remove = [k for k in self._cache if k[0] == agent_runtime_id]
        for key in keys_to_remove:
            del self._cache[key]

    def clear_tool(self, tool_name: str) -> None:
        """清除指定工具的所有缓存条目"""
        keys_to_remove = [k for k in self._cache if k[1] == tool_name]
        for key in keys_to_remove:
            del self._cache[key]

    @property
    def size(self) -> int:
        """缓存条目总数"""
        return len(self._cache)

    def list_for_agent(self, agent_runtime_id: str) -> list[str]:
        """列出指定 Agent 的所有 always 授权工具名"""
        return [tn for (rid, tn) in self._cache if rid == agent_runtime_id]


class ApprovalManagerProtocol(Protocol):
    """审批管理器接口 — 对齐 ApprovalManager 的 register + wait_for_decision"""

    async def register(self, request: Any) -> Any:
        """注册审批请求（Phase 1）"""
        ...

    async def wait_for_decision(
        self, approval_id: str, timeout_s: float | None = None,
    ) -> Any:
        """等待用户决策（Phase 2），返回 ApprovalDecision 或 None"""
        ...


# ------------------------------------------------------------------
# 结果模型
# ------------------------------------------------------------------


@dataclass(frozen=True)
class PermissionResult:
    """权限检查结果"""

    allowed: bool
    reason: str  # 决策路径标签，用于事件记录和调试


# ------------------------------------------------------------------
# 核心函数
# ------------------------------------------------------------------


async def check_permission(
    tool_meta: ToolMeta,
    args: dict[str, Any],
    ctx: ExecutionContext,
    override_cache: ApprovalOverrideCacheProtocol | None,
    approval_manager: ApprovalManagerProtocol | None,
) -> PermissionResult:
    """统一权限决策。

    四步短路，命中即返回：
    1. always 覆盖 → 放行
    2. 有效副作用等级（含路径升级）+ Preset 矩阵 → ALLOW → 放行
    3. 矩阵返回 ASK → 发起审批等待
    4. 审批结果 → 放行 / 拒绝
    """
    # Step 1: always 覆盖快速路径
    if (
        override_cache is not None
        and ctx.agent_runtime_id
        and override_cache.has(ctx.agent_runtime_id, tool_meta.name)
    ):
        logger.debug(
            "permission_override_hit",
            tool_name=tool_meta.name,
            agent_runtime_id=ctx.agent_runtime_id,
        )
        return PermissionResult(allowed=True, reason="always_override")

    # Step 2: Preset 矩阵查表
    effective_sel = effective_side_effect(tool_meta, args, ctx)
    decision = preset_decision(ctx.permission_preset, effective_sel)

    if decision == PresetDecision.ALLOW:
        return PermissionResult(allowed=True, reason="preset_allow")

    # Step 3: 需要审批
    if approval_manager is None:
        logger.warning(
            "permission_ask_no_manager",
            tool_name=tool_meta.name,
        )
        return PermissionResult(allowed=False, reason="no_approval_manager")

    try:
        approval_decision = await _request_approval(
            approval_manager=approval_manager,
            tool_name=tool_meta.name,
            args=args,
            effective_sel=effective_sel,
            ctx=ctx,
        )
    except Exception:
        logger.warning(
            "permission_approval_failed",
            tool_name=tool_meta.name,
            exc_info=True,
        )
        return PermissionResult(allowed=False, reason="approval_error")

    # Step 4: 审批结��
    if approval_decision is None:
        return PermissionResult(allowed=False, reason="denied:timeout")

    # ApprovalDecision 是 StrEnum: "allow-once" / "allow-always" / "deny"
    decision_str = str(approval_decision)
    if decision_str == "allow-once":
        return PermissionResult(allowed=True, reason="approved_once")
    elif decision_str == "allow-always":
        if override_cache is not None and ctx.agent_runtime_id:
            override_cache.set(ctx.agent_runtime_id, tool_meta.name)
        return PermissionResult(allowed=True, reason="approved_always")
    else:
        return PermissionResult(allowed=False, reason=f"denied:{decision_str}")


def effective_side_effect(
    tool_meta: ToolMeta,
    args: dict[str, Any],
    ctx: ExecutionContext,
) -> SideEffectLevel:
    """计算参数感知的有效 SideEffectLevel。

    当前规则：filesystem 工具访问 workspace 外路径 → 升级为 IRREVERSIBLE。
    后续如有新规则，在此函数内追加。
    """
    sel = tool_meta.side_effect_level

    # 路径感知升级（从 PresetBeforeHook._escalate_for_outside_workspace 迁移）
    if (
        tool_meta.path_escalation
        and ctx.permission_preset != PermissionPreset.FULL
    ):
        sel = _escalate_for_outside_workspace(args, sel)

    return sel


# ------------------------------------------------------------------
# 内部辅助
# ------------------------------------------------------------------


def _escalate_for_outside_workspace(
    args: dict[str, Any],
    current_level: SideEffectLevel,
) -> SideEffectLevel:
    """检测 filesystem 工具是否访问 workspace 外路径。

    若路径在 workspace 外，将 effective side_effect_level 升级为
    IRREVERSIBLE，使 NORMAL preset 触发 ASK。

    workspace root 默认 ~/.octoagent（OCTOAGENT_HOME 环境变量可覆盖）。
    """
    raw_path = str(args.get("path", "") or args.get("cwd", "") or args.get("directory", "")).strip()
    if not raw_path:
        return current_level

    workspace_root_str = os.environ.get(
        "OCTOAGENT_HOME", str(Path.home() / ".octoagent")
    )

    try:
        candidate = Path(raw_path)
        if str(candidate).startswith("~"):
            candidate = candidate.expanduser()
        if not candidate.is_absolute():
            candidate = Path(workspace_root_str) / candidate
        resolved = candidate.resolve()
        workspace_resolved = Path(workspace_root_str).resolve()

        if resolved != workspace_resolved and not resolved.is_relative_to(
            workspace_resolved
        ):
            logger.debug(
                "permission_path_escalated",
                path=raw_path,
                workspace_root=workspace_root_str,
                from_level=current_level.value,
                to_level=SideEffectLevel.IRREVERSIBLE.value,
            )
            return SideEffectLevel.IRREVERSIBLE
    except Exception:
        # 路径解析失败 → 保守升级
        return SideEffectLevel.IRREVERSIBLE

    return current_level


async def _request_approval(
    *,
    approval_manager: ApprovalManagerProtocol,
    tool_name: str,
    args: dict[str, Any],
    effective_sel: SideEffectLevel,
    ctx: ExecutionContext,
) -> Any:
    """发起审批请求并等待结果。

    对齐 ApprovalManager 的 register + wait_for_decision 两阶段接口。
    """
    import uuid
    from datetime import UTC, datetime, timedelta

    approval_id = str(uuid.uuid4())
    now = datetime.now(tz=UTC)
    timeout_s = 120.0

    # 传 dict 而非 ApprovalRequest，避免 tooling→policy 循环导入。
    # ApprovalManager.register() 接受 dict | ApprovalRequest 并在入口做转换。
    request_data: dict[str, Any] = {
        "approval_id": approval_id,
        "task_id": ctx.task_id or "",
        "tool_name": tool_name,
        "tool_args_summary": _summarize_args(args),
        "risk_explanation": f"工具 {tool_name} 需要用户审批（{effective_sel.value}）",
        "policy_label": "permission_check",
        "side_effect_level": effective_sel,
        "agent_runtime_id": ctx.agent_runtime_id or "",
        "expires_at": now + timedelta(seconds=timeout_s),
        "created_at": now,
    }

    record = await approval_manager.register(request_data)

    # 如果 register 已自动批准（always 覆盖命中），直接返回 decision
    if record.decision is not None:
        return record.decision

    # Phase 2: 等待用户决策
    return await approval_manager.wait_for_decision(
        approval_id, timeout_s=timeout_s
    )


def _summarize_args(args: dict[str, Any], max_len: int = 200) -> str:
    """生成工具参数摘要（用于审批展示）"""
    parts = []
    for k, v in args.items():
        s = str(v)
        if len(s) > 60:
            s = s[:57] + "..."
        parts.append(f"{k}={s}")
    summary = ", ".join(parts)
    if len(summary) > max_len:
        summary = summary[: max_len - 3] + "..."
    return summary
