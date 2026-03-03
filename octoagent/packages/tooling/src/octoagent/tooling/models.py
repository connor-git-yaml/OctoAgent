"""数据模型与枚举 -- Feature 004 Tool Contract + ToolBroker

对齐 data-model.md 定义。包含：
- 枚举：SideEffectLevel、ToolProfile、HookType、FailMode
- 数据模型：ToolMeta、ToolResult、ToolCall、ExecutionContext、BeforeHookResult、CheckResult
"""

from __future__ import annotations

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
    """工具权限 Profile -- 对齐 spec FR-001, FR-007

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
    """检查 context profile 是否允许访问 tool profile

    Args:
        tool_profile: 工具声明的 profile 级别
        context_profile: 当前执行上下文的 profile 级别

    Returns:
        True 如果 context profile >= tool profile
    """
    return PROFILE_LEVELS[tool_profile] <= PROFILE_LEVELS[context_profile]


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
    profile: ToolProfile = Field(
        default=ToolProfile.MINIMAL,
        description="当前执行上下文的 ToolProfile（决定可用工具集）",
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
