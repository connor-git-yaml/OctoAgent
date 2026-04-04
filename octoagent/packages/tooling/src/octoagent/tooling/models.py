"""数据模型与枚举 -- Feature 004 Tool Contract + ToolBroker

对齐 data-model.md 定义。包含：
- 枚举：SideEffectLevel、ToolProfile、HookType、FailMode
- Feature 061 新增枚举：PermissionPreset、PresetDecision、ToolTier
- 数据模型：ToolMeta、ToolResult、ToolCall、ExecutionContext、BeforeHookResult、CheckResult
- Feature 061 新增模型：PresetCheckResult、DeferredToolEntry、CoreToolSet 等
"""

from __future__ import annotations

import warnings
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

# ============================================================
# 枚举定义
# ============================================================


class SideEffectLevel(StrEnum):
    """工具副作用等级 -- 对齐 spec FR-001, Blueprint §8.5.2

    枚举值已锁定（FR-025a），变更需经 005/006 利益方评审。
    """

    NONE = "none"  # 纯读取，无副作用
    REVERSIBLE = "reversible"  # 可回滚的副作用
    IRREVERSIBLE = "irreversible"  # 不可逆操作


class ToolProfile(StrEnum):
    """[DEPRECATED] 工具权限 Profile — 已被 PermissionPreset 取代

    Feature 061: 保留此枚举仅供迁移期兼容，后续版本将删除。
    请使用 PermissionPreset 和 preset_decision() 替代。

    过滤规则（FR-007）：
    - minimal 查询 -> 仅返回 minimal 工具
    - standard 查询 -> 返回 minimal + standard 工具
    - privileged 查询 -> 返回所有工具

    枚举值已锁定（FR-025a）。
    """

    MINIMAL = "minimal"  # 最小权限集（只读工具）
    STANDARD = "standard"  # 标准权限（读写工具）
    PRIVILEGED = "privileged"  # 特权操作（exec, docker, 外部 API）


# ToolProfile 层级比较映射
PROFILE_LEVELS: dict[ToolProfile, int] = {
    ToolProfile.MINIMAL: 0,
    ToolProfile.STANDARD: 1,
    ToolProfile.PRIVILEGED: 2,
}


def profile_allows(tool_profile: ToolProfile, context_profile: ToolProfile) -> bool:
    """[DEPRECATED] 检查 context profile 是否允许访问 tool profile

    Feature 061: 此函数已废弃，内部委托到 preset_decision()。
    请直接使用 preset_decision() 替代。

    Args:
        tool_profile: 工具声明的 profile 级别
        context_profile: 当前执行上下文的 profile 级别

    Returns:
        True 如果 preset_decision 返回 ALLOW
    """
    warnings.warn(
        "profile_allows() 已废弃，请使用 preset_decision() 替代",
        DeprecationWarning,
        stacklevel=2,
    )
    # Feature 061 T-044: 内部委托到 preset_decision()
    # 将 ToolProfile 映射为 PermissionPreset，将 tool_profile 映射为 SideEffectLevel 近似
    # context_profile 映射为 Preset，tool_profile 按层级映射为副作用等级
    context_preset = TOOL_PROFILE_TO_PRESET.get(context_profile, PermissionPreset.MINIMAL)
    # tool_profile 层级映射: minimal→NONE, standard→REVERSIBLE, privileged→IRREVERSIBLE
    _PROFILE_TO_SIDE_EFFECT: dict[ToolProfile, SideEffectLevel] = {
        ToolProfile.MINIMAL: SideEffectLevel.NONE,
        ToolProfile.STANDARD: SideEffectLevel.REVERSIBLE,
        ToolProfile.PRIVILEGED: SideEffectLevel.IRREVERSIBLE,
    }
    tool_side_effect = _PROFILE_TO_SIDE_EFFECT.get(tool_profile, SideEffectLevel.IRREVERSIBLE)
    decision = preset_decision(context_preset, tool_side_effect)
    return decision == PresetDecision.ALLOW


# ============================================================
# Feature 061: 权限 Preset 枚举 + 策略矩阵
# ============================================================


class PermissionPreset(StrEnum):
    """Agent 实例级权限 Preset — 取代 ToolProfile

    决定工具调用时的默认 allow/ask 策略。
    基于工具的 SideEffectLevel 做出决策，不再基于工具的 ToolProfile。

    分配规则:
    - Butler: 默认 FULL (FR-004)
    - Worker: 创建时指定，默认 NORMAL (FR-005)
    - Subagent: 继承其所属 Worker 的 Preset (FR-006)

    迁移映射:
    - ToolProfile.MINIMAL → PermissionPreset.MINIMAL
    - ToolProfile.STANDARD → PermissionPreset.NORMAL
    - ToolProfile.PRIVILEGED → PermissionPreset.FULL
    """

    MINIMAL = "minimal"   # 保守：仅 none=allow，其余 ask
    NORMAL = "normal"     # 标准：none+reversible=allow，irreversible=ask
    FULL = "full"         # 完全：所有 allow


class PresetDecision(StrEnum):
    """Preset 检查决策结果

    注意：没有 DENY — 所有 Preset 不允许的操作都走 ASK（soft deny），
    用户可通过审批临时提升权限（Constitution 原则 7: User-in-Control）。
    """

    ALLOW = "allow"   # 直接放行
    ASK = "ask"       # 触发审批请求（soft deny）


class ToolTier(StrEnum):
    """工具层级标记 — 决定初始 context 中的呈现方式

    CORE: 完整 JSON Schema 始终加载到 LLM context（~10 个高频工具）
    DEFERRED: 仅暴露 {name, one_line_desc} 列表，通过 tool_search 按需加载
    """

    CORE = "core"
    DEFERRED = "deferred"


# Preset × SideEffectLevel → Decision 矩阵
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
# Feature 061: ToolProfile → PermissionPreset 兼容映射
# ============================================================


TOOL_PROFILE_TO_PRESET: dict[ToolProfile, PermissionPreset] = {
    ToolProfile.MINIMAL: PermissionPreset.MINIMAL,
    ToolProfile.STANDARD: PermissionPreset.NORMAL,
    ToolProfile.PRIVILEGED: PermissionPreset.FULL,
}


def migrate_tool_profile_to_preset(profile_value: str) -> PermissionPreset:
    """将旧 ToolProfile 值映射为 PermissionPreset

    Args:
        profile_value: ToolProfile 枚举值字符串（minimal/standard/privileged）

    Returns:
        对应的 PermissionPreset；未知值回退到 MINIMAL
    """
    try:
        profile = ToolProfile(profile_value.strip().lower())
        return TOOL_PROFILE_TO_PRESET[profile]
    except (ValueError, KeyError):
        return PermissionPreset.MINIMAL  # 安全默认值


class HookType(StrEnum):
    """Hook 类型"""

    BEFORE = "before"  # 执行前 hook
    AFTER = "after"  # 执行后 hook


class FailMode(StrEnum):
    """Hook 失败模式 -- 对齐 spec FR-019, CLR-005

    默认值: open（FR-025a），PolicyCheckpoint 强制 closed。
    """

    CLOSED = "closed"  # 失败时拒绝执行（安全类 hook）
    OPEN = "open"  # 失败时记录警告并继续（可观测类 hook）


# ============================================================
# 数据模型
# ============================================================


class ToolMeta(BaseModel):
    """工具元数据 -- 对齐 spec FR-001/003/004/005, data-model.md §2.1

    ToolMeta 是工具在系统中的身份证，由 Schema Reflection 自动生成。
    工具开发者通过 @tool_contract 装饰器声明元数据，
    系统从函数签名 + type hints + docstring 生成 parameters_json_schema。
    """

    # 必填字段
    name: str = Field(description="工具名称（全局唯一）")
    description: str = Field(description="工具描述（来自函数 docstring）")
    parameters_json_schema: dict[str, Any] = Field(description="参数 JSON Schema（自动反射生成）")
    side_effect_level: SideEffectLevel = Field(description="副作用等级（必须声明，无默认值）")
    tool_profile: ToolProfile = Field(description="权限 Profile 级别")
    tool_group: str = Field(description="逻辑分组（如 'system', 'filesystem', 'network'）")

    # 可选字段
    version: str = Field(default="1.0.0", description="工具版本号")
    timeout_seconds: float | None = Field(
        default=None,
        description="声明式超时（秒），None 表示不超时",
    )
    is_async: bool = Field(default=False, description="标记工具是否为异步函数")
    output_truncate_threshold: int | None = Field(
        default=None,
        description="工具级输出裁切阈值（字符数），None 表示使用全局默认值",
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Feature 030: ToolIndex 检索标签",
    )
    worker_types: list[str] = Field(
        default_factory=list,
        description="Feature 030: 推荐 worker type",
    )
    manifest_ref: str = Field(
        default="",
        description="Feature 030: 工具声明来源引用",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Feature 030: 可检索扩展元数据",
    )
    # Feature 061: 工具层级标记
    tier: ToolTier = Field(
        default=ToolTier.DEFERRED,
        description="工具层级: CORE（始终加载 schema）或 DEFERRED（按需加载）",
    )


class ToolResult(BaseModel):
    """工具执行结果 -- 对齐 spec FR-011, data-model.md §2.2

    是 ToolBroker execute() 的标准返回格式，
    也是 Feature 005 SkillRunner 结果回灌的输入。
    必含字段已锁定（FR-025a）。
    """

    # 锁定字段（FR-025a）
    output: str = Field(description="输出内容（原文或 artifact 引用摘要）")
    is_error: bool = Field(default=False, description="是否为错误结果")
    error: str | None = Field(
        default=None,
        description="错误信息（仅 is_error=True 时有值）",
    )
    duration: float = Field(description="执行耗时（秒）")
    artifact_ref: str | None = Field(
        default=None,
        description="Artifact 引用 ID（仅大输出裁切时有值）",
    )

    # 扩展字段
    tool_name: str = Field(default="", description="执行的工具名称")
    truncated: bool = Field(default=False, description="输出是否被裁切")


class RegisterToolResult(BaseModel):
    """工具注册结果 -- Feature 012 fail-open 注册返回体"""

    ok: bool = Field(description="是否注册成功")
    tool_name: str = Field(description="工具名称")
    message: str = Field(default="", description="结果说明")
    error_type: str | None = Field(default=None, description="错误类型（失败时）")


class RegistryDiagnostic(BaseModel):
    """注册诊断项 -- 记录一次工具注册失败或告警"""

    tool_name: str = Field(description="工具名称")
    error_type: str = Field(description="错误类型")
    message: str = Field(description="错误说明")
    timestamp: datetime = Field(description="记录时间")


class ToolCall(BaseModel):
    """工具调用请求 -- 对齐 data-model.md §2.3

    封装工具调用的名称和参数，
    是 ToolBroker.execute() 的结构化输入。
    """

    tool_name: str = Field(description="目标工具名称")
    arguments: dict[str, Any] = Field(
        default_factory=dict,
        description="调用参数（JSON 可序列化）",
    )


class ExecutionContext(BaseModel):
    """工具执行上下文 -- 对齐 spec CLR-002, data-model.md §2.4

    作为 ToolBroker.execute() 和 Hook 的上下文参数传递，
    用于事件生成和 Policy 决策。
    字段设计对齐现有 Event 模型的 trace_id / task_id 字段。
    """

    task_id: str = Field(description="关联任务 ID")
    trace_id: str = Field(description="追踪标识（同一 task 共享）")
    caller: str = Field(default="system", description="调用者标识（如 Worker ID）")
    agent_runtime_id: str = Field(default="", description="当前 agent runtime ID")
    agent_session_id: str = Field(default="", description="当前 agent session ID")
    work_id: str = Field(default="", description="当前 work ID")
    profile: ToolProfile = Field(
        default=ToolProfile.MINIMAL,
        description="[DEPRECATED] 使用 permission_preset 替代",
    )
    # Feature 061: Agent 实例级权限 Preset
    permission_preset: PermissionPreset = Field(
        default=PermissionPreset.MINIMAL,
        description="当前 Agent 的权限 Preset（决定工具调用的 allow/ask 策略）",
    )


class BeforeHookResult(BaseModel):
    """before hook 执行结果 -- 对齐 data-model.md §3.1"""

    proceed: bool = Field(
        default=True,
        description="是否继续执行工具（False 表示拒绝）",
    )
    rejection_reason: str | None = Field(
        default=None,
        description="拒绝原因（仅 proceed=False 时）",
    )
    modified_args: dict[str, Any] | None = Field(
        default=None,
        description="修改后的参数（None 表示不修改）",
    )


class CheckResult(BaseModel):
    """PolicyCheckpoint 检查结果 -- 对齐 data-model.md §3.2"""

    allowed: bool = Field(description="是否允许执行")
    reason: str = Field(default="", description="决策原因")
    requires_approval: bool = Field(
        default=False,
        description="是否需要人工审批（Feature 006 使用）",
    )


# ============================================================
# Feature 061: 新增数据模型
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


class DeferredToolEntry(BaseModel):
    """Deferred 工具的精简表示 — 用于 system prompt 注入

    仅包含名称和单行描述，不包含完整 schema。
    LLM 通过 tool_search 获取完整信息。
    """

    name: str = Field(description="工具名称")
    one_line_desc: str = Field(
        max_length=80,
        description="单行描述（≤80 字符）",
    )
    tool_group: str = Field(default="", description="工具分组")
    side_effect_level: str = Field(default="", description="副作用等级标记")


class CoreToolSet(BaseModel):
    """Core Tools 配置 — 定义始终加载完整 schema 的工具清单

    Core Tools 清单可通过配置文件或 Event Store 使用频率统计确定。
    至少必须包含 tool_search 自身（FR-018）。
    """

    tool_names: list[str] = Field(
        description="Core 工具名称列表",
        min_length=1,
    )

    @classmethod
    def default(cls) -> CoreToolSet:
        """默认 Core Tools 清单 — 始终以完整 schema 注入 LLM。

        选择标准：日常对话高频使用的工具。
        其余工具通过 tool_search 按需加载。
        """
        return cls(tool_names=[
            "tool_search",          # 入口：搜索和激活 deferred 工具
            "filesystem.list_dir",  # 文件浏览
            "filesystem.read_text", # 文件读取
            "filesystem.write_text",# 文件写入
            "terminal.exec",       # 命令执行
            "memory.recall",       # 记忆检索
            "web.search",          # 联网搜索
            "web.fetch",           # 网页读取
            "skills",              # 技能调用
        ])

    def is_core(self, tool_name: str) -> bool:
        """判断工具是否为 Core"""
        return tool_name in self.tool_names

    def classify(self, tool_name: str) -> ToolTier:
        """返回工具的层级"""
        return ToolTier.CORE if self.is_core(tool_name) else ToolTier.DEFERRED


class ToolSearchHit(BaseModel):
    """单个工具搜索命中"""

    tool_name: str = Field(description="工具名称")
    description: str = Field(description="工具描述")
    parameters_schema: dict[str, Any] = Field(description="完整参数 JSON Schema")
    score: float = Field(default=0.0, description="匹配得分")
    side_effect_level: str = Field(default="", description="副作用等级")
    tool_group: str = Field(default="", description="工具分组")
    tags: list[str] = Field(default_factory=list, description="检索标签")


class ToolSearchResult(BaseModel):
    """tool_search 工具的返回结果"""

    query: str = Field(description="原始查询")
    results: list[ToolSearchHit] = Field(default_factory=list, description="匹配结果")
    total_deferred: int = Field(default=0, description="Deferred Tools 总数")
    is_fallback: bool = Field(default=False, description="是否为降级模式（全量返回）")
    backend: str = Field(default="", description="使用的检索后端")
    latency_ms: int = Field(default=0, description="检索延迟（毫秒）")


class ToolPromotionState(BaseModel):
    """当前 session 的工具提升状态

    维护 tool_name → sources 的引用计数，
    确保 Skill 卸载时正确判断工具是否应回退。
    """

    promoted_tools: dict[str, list[str]] = Field(
        default_factory=dict,
        description="tool_name → 提升来源列表",
    )

    def promote(self, tool_name: str, source: str) -> bool:
        """提升工具，返回 True 如果是新增提升（之前不在 active 集合中）"""
        sources = self.promoted_tools.setdefault(tool_name, [])
        was_empty = len(sources) == 0
        if source not in sources:
            sources.append(source)
        return was_empty  # 仅在首次提升时返回 True

    def demote(self, tool_name: str, source: str) -> bool:
        """移除提升来源，返回 True 如果工具应回退到 Deferred"""
        sources = self.promoted_tools.get(tool_name, [])
        if source in sources:
            sources.remove(source)
        if not sources:
            self.promoted_tools.pop(tool_name, None)
            return True  # 无其他来源，应回退
        return False

    def is_promoted(self, tool_name: str) -> bool:
        """判断工具是否处于 Active 状态"""
        return tool_name in self.promoted_tools

    @property
    def active_tool_names(self) -> list[str]:
        """当前所有 Active 状态的工具名称"""
        return list(self.promoted_tools.keys())


# ============================================================
# Deferred Tools system prompt 模板
# ============================================================

DEFERRED_TOOLS_PROMPT_TEMPLATE = """## Available Tools (Deferred)

以下工具可通过 tool_search 搜索后使用。如需使用，请先调用 tool_search 查询。

{deferred_tools_list}

共 {total_count} 个 deferred 工具可用。"""


def format_deferred_tools_list(entries: list[DeferredToolEntry]) -> str:
    """格式化 Deferred Tools 列表为 system prompt 注入文本

    每个工具以 `- {name}: {one_line_desc}` 格式输出，
    并附带总数提示和 tool_search 使用引导。

    Args:
        entries: Deferred 工具条目列表

    Returns:
        格式化后的文本（空列表返回空字符串）
    """
    if not entries:
        return ""
    lines = [f"- {entry.name}: {entry.one_line_desc}" for entry in entries]
    return DEFERRED_TOOLS_PROMPT_TEMPLATE.format(
        deferred_tools_list="\n".join(lines),
        total_count=len(entries),
    )
