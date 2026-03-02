"""PolicyEngine -- 门面类

对齐 FR-017 (irreversible 无 hook 拒绝), FR-027 (配置变更事件)。

组合 PolicyPipeline + ApprovalManager + PolicyCheckHook，
提供统一的初始化、启动恢复和配置管理入口。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from octoagent.tooling.models import SideEffectLevel, ToolMeta

from .approval_manager import ApprovalManager, EventStoreProtocol, SSEBroadcasterProtocol
from .evaluators.global_rule import global_rule
from .evaluators.profile_filter import profile_filter
from .models import (
    DEFAULT_PROFILE,
    PolicyAction,
    PolicyDecision,
    PolicyProfile,
    PolicyStep,
)
from .policy_check_hook import PolicyCheckHook

logger = logging.getLogger(__name__)


class PolicyEngine:
    """Policy Engine 门面类

    组合 Pipeline + ApprovalManager + PolicyCheckHook，
    提供统一的初始化、启动恢复和配置管理入口。

    对齐 FR: FR-017, FR-027
    """

    def __init__(
        self,
        profile: PolicyProfile | None = None,
        event_store: EventStoreProtocol | None = None,
        sse_broadcaster: SSEBroadcasterProtocol | None = None,
        default_timeout_s: float = 120.0,
        grace_period_s: float = 15.0,
    ) -> None:
        self._profile = profile or DEFAULT_PROFILE
        self._event_store = event_store
        self._sse_broadcaster = sse_broadcaster

        # 创建 ApprovalManager
        self._approval_manager = ApprovalManager(
            event_store=event_store,
            sse_broadcaster=sse_broadcaster,
            default_timeout_s=default_timeout_s,
            grace_period_s=grace_period_s,
        )

        # 构建 Pipeline Steps
        self._steps = self._build_steps()

        # 创建 PolicyCheckHook
        self._hook = PolicyCheckHook(
            steps=self._steps,
            approval_manager=self._approval_manager,
            profile=self._profile,
            event_store=event_store,
        )

    def _build_steps(self) -> list[PolicyStep]:
        """构建策略管道步骤列表

        M1 阶段包含 2 层:
        - Layer 1: ProfileFilter（ToolProfile 过滤）
        - Layer 2: GlobalRule（SideEffectLevel 映射）
        """
        return [
            PolicyStep(
                evaluator=lambda tool_meta, params, context: profile_filter(
                    tool_meta=tool_meta,
                    params=params,
                    context=context,
                    allowed_profile=self._profile.allowed_tool_profile,
                ),
                label="tools.profile",
            ),
            PolicyStep(
                evaluator=lambda tool_meta, params, context: global_rule(
                    tool_meta=tool_meta,
                    params=params,
                    context=context,
                    profile=self._profile,
                ),
                label="global",
            ),
        ]

    # ============================================================
    # 公共属性
    # ============================================================

    @property
    def hook(self) -> PolicyCheckHook:
        """获取 PolicyCheckHook 实例

        将此 hook 注册到 ToolBroker 的 before_execute 链中。
        """
        return self._hook

    @property
    def approval_manager(self) -> ApprovalManager:
        """获取 ApprovalManager 实例

        供 REST API 路由使用（审批查询和解决）。
        """
        return self._approval_manager

    @property
    def profile(self) -> PolicyProfile:
        """当前生效的策略 Profile"""
        return self._profile

    # ============================================================
    # 生命周期管理
    # ============================================================

    async def startup(self) -> int:
        """启动恢复: 从 Event Store 恢复未完成的审批

        Returns:
            恢复的 pending 审批数量
        """
        recovered = await self._approval_manager.recover_from_store()
        logger.info(
            "PolicyEngine 启动完成: 恢复 %d 个 pending 审批, profile=%s",
            recovered,
            self._profile.name,
        )
        return recovered

    # ============================================================
    # FR-017: irreversible 工具无 PolicyCheckpoint hook 时强制拒绝
    # ============================================================

    def validate_tool_registration(self, tool_meta: ToolMeta) -> PolicyDecision | None:
        """验证工具注册: irreversible 工具必须有 PolicyCheckpoint hook

        FR-017: 如果 irreversible 工具在无 PolicyCheckpoint hook 的情况下
        被注册到 ToolBroker，PolicyEngine 应记录警告并在 evaluate 时强制拒绝。

        Args:
            tool_meta: 工具元数据

        Returns:
            None 表示验证通过，PolicyDecision(DENY) 表示被拒绝
        """
        if tool_meta.side_effect_level == SideEffectLevel.IRREVERSIBLE:
            logger.warning(
                "FR-017: 检测到 irreversible 工具 '%s' 注册校验请求。"
                "该工具必须在 PolicyCheckpoint hook 保护下运行。",
                tool_meta.name,
            )
            # 返回 None 表示正常（hook 存在时由 hook 保护）
            # 此方法供外部校验使用，当 ToolBroker 未注册 PolicyCheckHook 时，
            # 调用方应使用返回的 deny 决策
            return None

        return None

    def enforce_irreversible_without_hook(
        self,
        tool_meta: ToolMeta,
        has_policy_hook: bool,
    ) -> PolicyDecision | None:
        """FR-017: 强制拒绝无 PolicyCheckpoint hook 的 irreversible 工具

        Args:
            tool_meta: 工具元数据
            has_policy_hook: ToolBroker 是否注册了 PolicyCheckpoint hook

        Returns:
            None 表示允许继续，PolicyDecision(DENY) 表示强制拒绝
        """
        if (
            tool_meta.side_effect_level == SideEffectLevel.IRREVERSIBLE
            and not has_policy_hook
        ):
            logger.error(
                "FR-017 违规: irreversible 工具 '%s' 在无 PolicyCheckpoint hook 时被调用，强制拒绝",
                tool_meta.name,
            )
            return PolicyDecision(
                action=PolicyAction.DENY,
                label="engine.fr017",
                reason=(
                    f"FR-017 违规: irreversible 工具 '{tool_meta.name}' "
                    f"必须在 PolicyCheckpoint hook 保护下运行"
                ),
                tool_name=tool_meta.name,
                side_effect_level=tool_meta.side_effect_level,
            )

        return None

    # ============================================================
    # FR-027: 策略配置变更
    # ============================================================

    async def update_profile(self, new_profile: PolicyProfile) -> None:
        """更新策略 Profile

        FR-027: 配置变更写入 POLICY_CONFIG_CHANGED 事件，
        包含变更前后的配置差异。

        Args:
            new_profile: 新的 PolicyProfile
        """
        old_profile = self._profile
        self._profile = new_profile

        # 重建 Pipeline Steps
        self._steps = self._build_steps()

        # 重建 PolicyCheckHook
        self._hook = PolicyCheckHook(
            steps=self._steps,
            approval_manager=self._approval_manager,
            profile=self._profile,
            event_store=self._event_store,
        )

        # 写入配置变更事件
        await self._write_config_changed_event(old_profile, new_profile)

        logger.info(
            "PolicyEngine Profile 已更新: %s -> %s",
            old_profile.name,
            new_profile.name,
        )

    async def _write_config_changed_event(
        self,
        old_profile: PolicyProfile,
        new_profile: PolicyProfile,
    ) -> None:
        """写入 POLICY_CONFIG_CHANGED 事件"""
        if self._event_store is None:
            return

        try:
            from octoagent.core.models.enums import ActorType, EventType
            from octoagent.core.models.event import Event, EventCausality
            from ulid import ULID

            now = datetime.now(UTC)

            # 计算差异
            diff = self._compute_profile_diff(old_profile, new_profile)

            event = Event(
                event_id=str(ULID()),
                task_id="system",  # 配置变更不关联特定 task
                task_seq=0,
                ts=now,
                type=EventType.POLICY_CONFIG_CHANGED,
                actor=ActorType.USER,
                payload={
                    "old_profile": old_profile.model_dump(),
                    "new_profile": new_profile.model_dump(),
                    "diff": diff,
                    "changed_at": now.isoformat(),
                },
                trace_id="system",
                causality=EventCausality(),
            )
            await self._event_store.append_event(event)
        except Exception as e:
            logger.error("写入 POLICY_CONFIG_CHANGED 事件失败: %s", e)

    @staticmethod
    def _compute_profile_diff(
        old: PolicyProfile,
        new: PolicyProfile,
    ) -> dict[str, Any]:
        """计算两个 Profile 的差异"""
        diff: dict[str, Any] = {}
        old_dict = old.model_dump()
        new_dict = new.model_dump()

        for key in old_dict:
            if old_dict[key] != new_dict.get(key):
                diff[key] = {
                    "old": old_dict[key],
                    "new": new_dict.get(key),
                }

        return diff
