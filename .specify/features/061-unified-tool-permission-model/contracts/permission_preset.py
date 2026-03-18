"""Feature 061 接口契约: PermissionPreset 枚举 + PresetPolicy 数据类

对齐 spec FR-002/003/004/005/006/007/008。
定义三级权限 Preset 和 Preset × SideEffectLevel → Decision 矩阵。

注意: 此文件是接口契约（specification），不是最终实现。
最终代码位于 packages/tooling/src/octoagent/tooling/models.py。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


# ============================================================
# 枚举定义
# ============================================================


class PermissionPreset(StrEnum):
    """Agent 实例级权限 Preset

    取代现有 ToolProfile(MINIMAL/STANDARD/PRIVILEGED)。
    决定工具调用时的默认 allow/ask 策略。

    分配规则:
    - Butler: 默认 FULL (FR-004)
    - Worker: 创建时指定，默认 NORMAL (FR-005)
    - Subagent: 继承其所属 Worker 的 Preset (FR-006)

    迁移映射:
    - ToolProfile.MINIMAL → PermissionPreset.MINIMAL
    - ToolProfile.STANDARD → PermissionPreset.NORMAL
    - ToolProfile.PRIVILEGED → PermissionPreset.FULL
    """

    MINIMAL = "minimal"
    NORMAL = "normal"
    FULL = "full"


class PresetDecision(StrEnum):
    """Preset 检查决策结果

    ALLOW: 工具调用直接放行
    ASK: 触发 soft deny — 向用户发起审批请求 (FR-007)

    设计要点: 没有 DENY 枚举值。
    所有 Preset 不允许的操作都走 ASK（soft deny），
    用户可通过审批临时提升权限。
    永久拒绝（黑名单）不在 v0.1 范围内。
    """

    ALLOW = "allow"
    ASK = "ask"


class SideEffectLevel(StrEnum):
    """工具副作用等级（引用，不变）

    从 packages/tooling/models.py 引入，此处仅作类型引用。
    """

    NONE = "none"
    REVERSIBLE = "reversible"
    IRREVERSIBLE = "irreversible"


# ============================================================
# 策略矩阵
# ============================================================

# Preset × SideEffectLevel → Decision
# 此矩阵是 Feature 061 的核心决策表，所有权限判断基于此。
PRESET_POLICY: dict[PermissionPreset, dict[SideEffectLevel, PresetDecision]] = {
    PermissionPreset.MINIMAL: {
        SideEffectLevel.NONE: PresetDecision.ALLOW,
        SideEffectLevel.REVERSIBLE: PresetDecision.ASK,
        SideEffectLevel.IRREVERSIBLE: PresetDecision.ASK,
    },
    PermissionPreset.NORMAL: {
        SideEffectLevel.NONE: PresetDecision.ALLOW,
        SideEffectLevel.REVERSIBLE: PresetDecision.ALLOW,
        SideEffectLevel.IRREVERSIBLE: PresetDecision.ASK,
    },
    PermissionPreset.FULL: {
        SideEffectLevel.NONE: PresetDecision.ALLOW,
        SideEffectLevel.REVERSIBLE: PresetDecision.ALLOW,
        SideEffectLevel.IRREVERSIBLE: PresetDecision.ALLOW,
    },
}


def preset_decision(
    preset: PermissionPreset,
    side_effect: SideEffectLevel,
) -> PresetDecision:
    """查询 Preset 策略矩阵

    Args:
        preset: Agent 实例的权限 Preset
        side_effect: 工具声明的副作用等级

    Returns:
        PresetDecision.ALLOW 或 PresetDecision.ASK
    """
    return PRESET_POLICY[preset][side_effect]


# ============================================================
# 数据类
# ============================================================


class PresetCheckResult(BaseModel):
    """Preset 检查结果 — 用于事件记录和 Hook 返回

    记录单次 Preset 检查的完整上下文，便于审计和可观测性。
    """

    agent_runtime_id: str = Field(description="Agent 实例 ID")
    tool_name: str = Field(description="被检查的工具名称")
    side_effect_level: SideEffectLevel = Field(description="工具的副作用等级")
    permission_preset: PermissionPreset = Field(description="Agent 的权限 Preset")
    decision: PresetDecision = Field(description="检查决策: allow 或 ask")
    override_hit: bool = Field(
        default=False,
        description="是否命中 always 覆盖（由 ApprovalOverrideHook 设置）",
    )


class PresetPolicyConfig(BaseModel):
    """Preset 策略配置 — 支持未来自定义 Preset (v0.2+)

    v0.1 仅使用固定的三级 PRESET_POLICY 矩阵。
    此配置类为后续扩展预留。
    """

    default_worker_preset: PermissionPreset = Field(
        default=PermissionPreset.NORMAL,
        description="Worker 创建时的默认 Preset",
    )
    butler_preset: PermissionPreset = Field(
        default=PermissionPreset.FULL,
        description="Butler 的固定 Preset",
    )
    policy_matrix: dict[str, dict[str, str]] = Field(
        default_factory=lambda: {
            preset.value: {
                se.value: PRESET_POLICY[preset][se].value
                for se in SideEffectLevel
            }
            for preset in PermissionPreset
        },
        description="策略矩阵的可序列化表示（配置文件用）",
    )


# ============================================================
# 兼容层（ToolProfile → PermissionPreset 迁移期）
# ============================================================


class _ToolProfileCompat(StrEnum):
    """ToolProfile 兼容类型（废弃，仅迁移期使用）"""

    MINIMAL = "minimal"
    STANDARD = "standard"
    PRIVILEGED = "privileged"


TOOL_PROFILE_TO_PRESET: dict[_ToolProfileCompat, PermissionPreset] = {
    _ToolProfileCompat.MINIMAL: PermissionPreset.MINIMAL,
    _ToolProfileCompat.STANDARD: PermissionPreset.NORMAL,
    _ToolProfileCompat.PRIVILEGED: PermissionPreset.FULL,
}


def migrate_tool_profile_to_preset(profile_value: str) -> PermissionPreset:
    """将旧 ToolProfile 值映射为 PermissionPreset

    Args:
        profile_value: ToolProfile 枚举值字符串（minimal/standard/privileged）

    Returns:
        对应的 PermissionPreset
    """
    try:
        profile = _ToolProfileCompat(profile_value.strip().lower())
        return TOOL_PROFILE_TO_PRESET[profile]
    except (ValueError, KeyError):
        return PermissionPreset.MINIMAL  # 安全默认值
