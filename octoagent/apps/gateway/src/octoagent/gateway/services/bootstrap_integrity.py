"""Feature 082 P1：Bootstrap 完成性综合校验服务。

历史问题：``OnboardingState.is_completed()`` 只看 ``onboarding_completed_at``
是否非空，但该字段会被 ``_detect_legacy_onboarding_completion()`` 误标——导致
Bootstrap 引导从未真实跑过，Profile/USER.md 仍是默认值。

P1 修复策略：
- ``OnboardingState.is_completed()`` 保留简单语义（仅看时间戳；用作快速布尔检查）
- ``BootstrapIntegrityChecker.check_substantive_completion()`` 综合三个证据源判定：
  1. ``onboarding_completed_at`` 非空（必要条件）
  2. OwnerProfile 至少一个非默认字段（实质证据 1）
  3. USER.md 已填充（实质证据 2）
- 任一实质证据缺失 → 视为"未真实完成"（即使时间戳标记为完成）→
  ``resolve_behavior_workspace`` 仍注入 BOOTSTRAP.md 让引导重新跑

调用方：
- ``services/agent_context.py:resolve_behavior_workspace`` 加载前调本服务
- ``builtin_tools/bootstrap.complete()`` (P2) 完成时校验
- ``dx/config_commands.py:octo bootstrap migrate-082`` (P4) 检测误标场景
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from octoagent.core.behavior_workspace import (
    OnboardingState,
    _user_md_is_filled,
    load_onboarding_state,
)

log = structlog.get_logger()


@dataclass(frozen=True)
class IntegrityReport:
    """实质完成校验结果，含每个证据源的细节供调用方诊断。"""

    has_onboarding_marker: bool
    """``.onboarding-state.json`` 中 ``onboarding_completed_at`` 非空。"""

    owner_profile_filled: bool
    """OwnerProfile 至少一个偏好字段非默认值（preferred_address / working_style 等）。"""

    user_md_filled: bool
    """``behavior/system/USER.md`` 不再含 ``"待引导时填写"`` 等占位符。"""

    @property
    def is_substantively_completed(self) -> bool:
        """三重校验：marker + owner_profile + user_md 全部满足。

        Feature 082 P1 严格语义：仅 marker 不够，必须有实质证据；
        缺任一证据视为"未真实完成"，引导应重新跑。
        """
        return (
            self.has_onboarding_marker
            and (self.owner_profile_filled or self.user_md_filled)
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "has_onboarding_marker": self.has_onboarding_marker,
            "owner_profile_filled": self.owner_profile_filled,
            "user_md_filled": self.user_md_filled,
            "is_substantively_completed": self.is_substantively_completed,
        }


def owner_profile_is_filled(owner_profile: Any | None) -> bool:
    """OwnerProfile 是否有任一**非默认**偏好字段。

    Feature 082 P1：非默认 = 用户实际配过的值。
    判定的字段：``preferred_address`` / ``working_style`` / ``interaction_preferences``
    / ``boundary_notes``。``display_name="Owner"`` 是兜底默认，``timezone="UTC"``
    / ``locale="zh-CN"`` 是地理默认——这三个字段视为"系统默认"不算引导证据。

    本函数兼容 Pydantic OwnerProfile 实例 + dict（来自 store row）。
    """
    if owner_profile is None:
        return False

    def _get(field: str) -> Any:
        if isinstance(owner_profile, dict):
            return owner_profile.get(field)
        return getattr(owner_profile, field, None)

    preferred_address = _get("preferred_address")
    working_style = _get("working_style")
    interaction_preferences = _get("interaction_preferences") or []
    boundary_notes = _get("boundary_notes") or []

    # preferred_address：非空 + 非历史伪默认 "你"
    if preferred_address and preferred_address != "你":
        return True
    if working_style:
        return True
    if interaction_preferences:
        return True
    if boundary_notes:
        return True
    return False


class BootstrapIntegrityChecker:
    """综合 onboarding-state.json + OwnerProfile + USER.md 判定 bootstrap 是否实质完成。"""

    def __init__(self, project_root: Path) -> None:
        self._root = project_root.resolve()

    async def build_report(
        self,
        owner_profile: Any | None = None,
    ) -> IntegrityReport:
        """构建完成性报告。

        Args:
            owner_profile: 可选 ``OwnerProfile`` 实例或 dict；为 None 时仅做文件层校验
                （``owner_profile_filled`` 字段保守为 False）。

        Returns:
            ``IntegrityReport``——含每个证据源的命中状态。
        """
        state: OnboardingState = load_onboarding_state(self._root)
        report = IntegrityReport(
            has_onboarding_marker=state.is_completed(),
            owner_profile_filled=owner_profile_is_filled(owner_profile),
            user_md_filled=_user_md_is_filled(self._root),
        )
        log.debug(
            "bootstrap_integrity_report",
            project_root=str(self._root),
            **report.to_payload(),
        )
        return report

    async def check_substantive_completion(
        self,
        owner_profile: Any | None = None,
    ) -> bool:
        """快速布尔检查：是否实质完成 bootstrap。"""
        report = await self.build_report(owner_profile=owner_profile)
        return report.is_substantively_completed
