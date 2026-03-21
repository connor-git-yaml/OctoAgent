"""Feature 061: PresetBeforeHook — Preset × SideEffectLevel 权限决策

BeforeHook 实现（priority=20），从 ExecutionContext 读取 permission_preset，
从 ToolMeta 读取 side_effect_level，查询 PRESET_POLICY 矩阵。

ALLOW → BeforeHookResult(proceed=True)
ASK → BeforeHookResult(proceed=False, rejection_reason="ask:preset_denied:...")
"""

from __future__ import annotations

from typing import Any, Protocol

import structlog

from pathlib import Path

from ..models import (
    BeforeHookResult,
    ExecutionContext,
    FailMode,
    PermissionPreset,
    PresetCheckResult,
    PresetDecision,
    SideEffectLevel,
    ToolMeta,
    preset_decision,
)

logger = structlog.get_logger(__name__)

# 需要路径感知升级的内置 filesystem 工具名
_FILESYSTEM_PATH_TOOLS = frozenset({
    "filesystem.list_dir",
    "filesystem.read_text",
    "filesystem.write_text",
})


class ApprovalOverrideCacheProtocol(Protocol):
    """ApprovalOverrideCache 接口（解耦 tooling 和 policy 包）"""

    def has(
        self, agent_runtime_id: str, tool_name: str
    ) -> bool: ...


class PresetBeforeHook:
    """Preset 权限检查 Hook — priority=20, fail_mode=CLOSED

    基于 PRESET_POLICY 矩阵做出 allow/ask 决策。
    如果 ApprovalOverrideHook 已命中 always 覆盖，则跳过检查直接放行。
    每次检查生成 PRESET_CHECK 事件（通过 event_store，可选）。
    """

    def __init__(
        self,
        event_store: Any | None = None,
        override_cache: ApprovalOverrideCacheProtocol | None = None,
    ) -> None:
        """初始化 PresetBeforeHook

        Args:
            event_store: EventStore 实例（可选，用于 PRESET_CHECK 事件记录）
            override_cache: ApprovalOverrideCache（可选，用于跳过已覆盖的工具）
        """
        self._event_store = event_store
        self._override_cache = override_cache

    @property
    def name(self) -> str:
        return "preset_before_hook"

    @property
    def priority(self) -> int:
        return 20

    @property
    def fail_mode(self) -> FailMode:
        return FailMode.CLOSED  # 权限检查失败 = 拒绝执行

    async def before_execute(
        self,
        tool_meta: ToolMeta,
        args: dict[str, Any],
        context: ExecutionContext,
    ) -> BeforeHookResult:
        """执行 Preset 权限检查

        Args:
            tool_meta: 工具元数据
            args: 调用参数（不使用）
            context: 执行上下文（含 permission_preset）

        Returns:
            BeforeHookResult — proceed=True（allow）或
            proceed=False + rejection_reason（ask）
        """
        # 检查是否已被 ApprovalOverrideHook 覆盖
        override_hit = False
        if (
            self._override_cache is not None
            and context.agent_runtime_id
        ):
            override_hit = self._override_cache.has(
                context.agent_runtime_id,
                tool_meta.name,
            )

        if override_hit:
            # always 覆盖命中 → 直接放行，不做 Preset 检查
            check_result = PresetCheckResult(
                agent_runtime_id=context.agent_runtime_id,
                tool_name=tool_meta.name,
                side_effect_level=tool_meta.side_effect_level,
                permission_preset=context.permission_preset,
                decision=PresetDecision.ALLOW,
                override_hit=True,
            )
            await self._emit_preset_check_event(
                check_result, context
            )
            return BeforeHookResult(proceed=True)

        # 路径感知升级：filesystem 工具访问 workspace 外路径时，
        # 将 effective side_effect_level 升级为 IRREVERSIBLE，
        # 使 NORMAL preset 触发 ASK（弹审批框），FULL preset 仍为 ALLOW。
        # 对齐 OpenClaw security=allowlist 语义。
        effective_side_effect = tool_meta.side_effect_level
        if (
            tool_meta.name in _FILESYSTEM_PATH_TOOLS
            and context.permission_preset != PermissionPreset.FULL
        ):
            effective_side_effect = self._escalate_for_outside_workspace(
                args, effective_side_effect, context,
            )

        decision = preset_decision(
            context.permission_preset,
            effective_side_effect,
        )

        # 构建检查结果（用于事件记录）
        check_result = PresetCheckResult(
            agent_runtime_id=context.agent_runtime_id,
            tool_name=tool_meta.name,
            side_effect_level=tool_meta.side_effect_level,
            permission_preset=context.permission_preset,
            decision=decision,
        )

        # 记录日志
        logger.debug(
            "preset_check",
            tool_name=tool_meta.name,
            preset=context.permission_preset.value,
            side_effect=tool_meta.side_effect_level.value,
            decision=decision.value,
        )

        # 生成 PRESET_CHECK 事件（最佳努力）
        await self._emit_preset_check_event(check_result, context)

        if decision == PresetDecision.ALLOW:
            return BeforeHookResult(proceed=True)

        # ASK: soft deny — 上层通过 "ask:" 前缀识别
        reason = (
            f"ask:preset_denied:{tool_meta.name}"
            f":{tool_meta.side_effect_level.value}"
        )
        return BeforeHookResult(
            proceed=False,
            rejection_reason=reason,
        )

    async def _emit_preset_check_event(
        self,
        check_result: PresetCheckResult,
        context: ExecutionContext,
    ) -> None:
        """生成 PRESET_CHECK 事件（最佳努力，不阻塞主逻辑）"""
        if self._event_store is None:
            return
        try:
            from datetime import datetime

            from octoagent.core.models.enums import ActorType, EventType
            from octoagent.core.models.event import Event
            from ulid import ULID

            event = Event(
                event_id=str(ULID()),
                task_id=context.task_id,
                task_seq=await self._event_store.get_next_task_seq(
                    context.task_id
                ),
                ts=datetime.now(),
                type=EventType.PRESET_CHECK,
                actor=ActorType.SYSTEM,
                payload=check_result.model_dump(),
                trace_id=context.trace_id,
            )
            append_committed = getattr(
                self._event_store, "append_event_committed", None
            )
            if callable(append_committed):
                await append_committed(
                    event, update_task_pointer=True
                )
            else:
                await self._event_store.append_event(event)
        except Exception as e:
            logger.warning(
                "preset_check_event_failed",
                error=str(e),
            )

    @staticmethod
    def _escalate_for_outside_workspace(
        args: dict[str, Any],
        current_level: SideEffectLevel,
        context: ExecutionContext,
    ) -> SideEffectLevel:
        """检测 filesystem 工具是否访问 workspace 外路径。

        若路径在 workspace 外，将 effective side_effect_level 升级为
        IRREVERSIBLE，使 NORMAL preset 触发 ASK（弹审批框）。
        对齐 OpenClaw security=allowlist 语义：
        - FULL preset → ALLOW（任意路径，不审批）
        - NORMAL preset + workspace 内 → ALLOW
        - NORMAL preset + workspace 外 → ASK（弹审批）
        - MINIMAL preset → ASK（任何 reversible+ 都审批）

        workspace root 默认 ~/.octoagent（OCTOAGENT_HOME 环境变量可覆盖）。
        """
        import os

        raw_path = str(args.get("path", "") or args.get("cwd", "")).strip()
        if not raw_path:
            return current_level

        # workspace root: 环境变量 > 默认 ~/.octoagent
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
                    "preset_path_escalated",
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
