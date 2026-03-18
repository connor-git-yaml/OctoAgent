"""SkillRunner 数据模型。"""

from __future__ import annotations

import time
import warnings
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from octoagent.tooling.models import ToolProfile
from pydantic import BaseModel, Field

_MAX_STEPS_HARD_CEILING = 500  # 降级重试 clamp 上限


class SkillRunStatus(StrEnum):
    """Skill 执行终态。"""

    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    STOPPED = "STOPPED"  # 被 StopHook 或用户取消优雅终止


class ErrorCategory(StrEnum):
    """Skill 错误分类。"""

    REPEAT_ERROR = "repeat_error"
    VALIDATION_ERROR = "validation_error"
    TOOL_EXECUTION_ERROR = "tool_execution_error"
    LOOP_DETECTED = "loop_detected"
    STEP_LIMIT_EXCEEDED = "step_limit_exceeded"
    INPUT_VALIDATION_ERROR = "input_validation_error"
    TOKEN_LIMIT_EXCEEDED = "token_limit_exceeded"
    TOOL_CALL_LIMIT_EXCEEDED = "tool_call_limit_exceeded"
    BUDGET_EXCEEDED = "budget_exceeded"
    TIMEOUT_EXCEEDED = "timeout_exceeded"


class SkillPermissionMode(StrEnum):
    """Skill 工具权限模式。"""

    INHERIT = "inherit"
    RESTRICT = "restrict"


class RetryPolicy(BaseModel):
    """重试策略。"""

    max_attempts: int = Field(default=3, ge=1, le=20)
    backoff_ms: int = Field(default=500, ge=0, le=60_000)
    upgrade_model_on_fail: bool = Field(default=False)
    downgrade_scope_on_fail: bool = Field(default=False)
    fallback_model_alias: str = Field(default="")


class LoopGuardPolicy(BaseModel):
    """循环保护策略。

    .. deprecated::
        使用 UsageLimits 替代。将在 6 个月后移除。
    """

    max_steps: int = Field(default=30, ge=1, le=200)
    repeat_signature_threshold: int = Field(default=10, ge=2, le=20)

    def to_usage_limits(self) -> UsageLimits:
        """转换为 UsageLimits。"""
        return UsageLimits(
            max_steps=self.max_steps,
            repeat_signature_threshold=self.repeat_signature_threshold,
        )


class UsageLimits(BaseModel):
    """多维度资源限制。任一维度触发即终止执行。

    max_steps / max_tool_calls 默认 None 表示不限制（与 Claude SDK / Agent Zero 对齐）。
    max_duration_seconds 默认 7200s（2 小时），作为全局安全上限。
    """

    max_steps: int | None = Field(default=None, ge=1)
    max_request_tokens: int | None = Field(default=None, ge=1)
    max_response_tokens: int | None = Field(default=None, ge=1)
    max_tool_calls: int | None = Field(default=None, ge=1)
    max_budget_usd: float | None = Field(default=None, ge=0.0)
    max_duration_seconds: float = Field(default=7200.0, ge=1.0)
    repeat_signature_threshold: int = Field(default=10, ge=2, le=20)


@dataclass
class UsageTracker:
    """运行时资源消耗追踪。高频更新场景使用 dataclass 避免 Pydantic 校验开销。"""

    steps: int = 0
    request_tokens: int = 0
    response_tokens: int = 0
    tool_calls: int = 0
    cost_usd: float = 0.0
    start_time: float = 0.0  # time.monotonic()

    def check_limits(self, limits: UsageLimits) -> ErrorCategory | None:
        """检查是否超限。返回 None 表示未超限，否则返回对应的 ErrorCategory。"""
        if limits.max_steps is not None and self.steps >= limits.max_steps:
            return ErrorCategory.STEP_LIMIT_EXCEEDED
        if limits.max_request_tokens is not None and self.request_tokens >= limits.max_request_tokens:
            return ErrorCategory.TOKEN_LIMIT_EXCEEDED
        if limits.max_response_tokens is not None and self.response_tokens >= limits.max_response_tokens:
            return ErrorCategory.TOKEN_LIMIT_EXCEEDED
        if limits.max_tool_calls is not None and self.tool_calls >= limits.max_tool_calls:
            return ErrorCategory.TOOL_CALL_LIMIT_EXCEEDED
        if limits.max_budget_usd is not None and self.cost_usd >= limits.max_budget_usd - 1e-9:
            return ErrorCategory.BUDGET_EXCEEDED
        elapsed = time.monotonic() - self.start_time
        if elapsed >= limits.max_duration_seconds:
            return ErrorCategory.TIMEOUT_EXCEEDED
        return None

    def to_dict(self) -> dict[str, Any]:
        """序列化为 dict，用于写入 SkillRunResult.usage。"""
        return {
            "steps": self.steps,
            "request_tokens": self.request_tokens,
            "response_tokens": self.response_tokens,
            "tool_calls": self.tool_calls,
            "cost_usd": self.cost_usd,
            "duration_seconds": round(time.monotonic() - self.start_time, 2),
        }


class ContextBudgetPolicy(BaseModel):
    """上下文预算策略。"""

    max_chars: int = Field(default=1500, ge=200, le=50_000)
    summary_chars: int = Field(default=240, ge=50, le=2_000)


class SkillExecutionContext(BaseModel):
    """单次 Skill 执行上下文。"""

    task_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    caller: str = Field(default="worker", min_length=1)
    agent_runtime_id: str = Field(default="")
    agent_session_id: str = Field(default="")
    work_id: str = Field(default="")
    conversation_messages: list[dict[str, str]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    usage_limits: UsageLimits = Field(default_factory=UsageLimits)


class ToolCallSpec(BaseModel):
    """Skill 输出中的工具调用规格。"""

    tool_name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)


class SkillOutputEnvelope(BaseModel):
    """Skill 统一输出封装。"""

    content: str = Field(default="")
    complete: bool = Field(default=False)
    skip_remaining_tools: bool = Field(default=False)
    tool_calls: list[ToolCallSpec] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    token_usage: dict[str, int] = Field(default_factory=dict)
    # 例：{"prompt_tokens": 500, "completion_tokens": 120, "total_tokens": 620}
    cost_usd: float = Field(default=0.0)
    # LLM 调用成本（美元），从 LiteLLM response 提取


class ToolFeedbackMessage(BaseModel):
    """工具执行结果回灌模型。"""

    tool_name: str = Field(min_length=1)
    is_error: bool = Field(default=False)
    output: str = Field(default="")
    error: str | None = Field(default=None)
    duration_ms: int = Field(default=0, ge=0)
    artifact_ref: str | None = Field(default=None)
    parts: list[dict[str, Any]] = Field(default_factory=list)


class SkillRunResult(BaseModel):
    """SkillRunner 最终结果。"""

    status: SkillRunStatus
    output: SkillOutputEnvelope | None = Field(default=None)
    attempts: int = Field(default=0, ge=0)
    steps: int = Field(default=0, ge=0)
    duration_ms: int = Field(default=0, ge=0)
    error_category: ErrorCategory | None = Field(default=None)
    error_message: str | None = Field(default=None)
    usage: dict[str, Any] = Field(default_factory=dict)
    # 由 UsageTracker.to_dict() 生成
    total_cost_usd: float = Field(default=0.0)


class SkillManifestModel(BaseModel):
    """SkillManifest 公共字段模型（不含类型对象）。"""

    skill_id: str = Field(min_length=1)
    version: str = Field(default="0.1.0", min_length=1)
    model_alias: str = Field(default="main", min_length=1)
    permission_mode: SkillPermissionMode = Field(default=SkillPermissionMode.RESTRICT)
    tools_allowed: list[str] = Field(default_factory=list)
    tool_profile: ToolProfile = Field(default=ToolProfile.STANDARD)
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    loop_guard: LoopGuardPolicy = Field(default_factory=LoopGuardPolicy)
    context_budget: ContextBudgetPolicy = Field(default_factory=ContextBudgetPolicy)
    description: str | None = Field(default=None)
    description_md: str | None = Field(default=None)
    metadata: dict[str, Any] = Field(default_factory=dict)
    resource_limits: dict[str, Any] = Field(default_factory=dict)
    # 从 SKILL.md frontmatter 的 resource_limits 字段读取


def extract_mounted_tool_names(metadata: dict[str, Any] | None) -> list[str]:
    """从 runtime metadata 中提取当前实际挂载的工具名。"""

    if not isinstance(metadata, dict):
        return []

    tool_selection = metadata.get("tool_selection")
    if isinstance(tool_selection, dict):
        mounted_tools = tool_selection.get("mounted_tools")
        if isinstance(mounted_tools, list):
            normalized: list[str] = []
            for item in mounted_tools:
                if isinstance(item, dict):
                    tool_name = str(item.get("tool_name", "")).strip()
                else:
                    tool_name = str(item).strip()
                if tool_name and tool_name not in normalized:
                    normalized.append(tool_name)
            if normalized:
                return normalized
        effective_tool_universe = tool_selection.get("effective_tool_universe")
        if isinstance(effective_tool_universe, dict):
            selected_tools = effective_tool_universe.get("selected_tools")
            if isinstance(selected_tools, list):
                normalized = [
                    str(item).strip() for item in selected_tools if str(item).strip()
                ]
                if normalized:
                    return normalized

    raw = metadata.get("selected_tools_json", "[]")
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    if isinstance(raw, str):
        import json

        try:
            payload = json.loads(raw or "[]")
        except json.JSONDecodeError:
            return []
        if isinstance(payload, list):
            return [str(item).strip() for item in payload if str(item).strip()]
    return []


def resolve_effective_tool_allowlist(
    *,
    permission_mode: SkillPermissionMode | str,
    tools_allowed: list[str],
    metadata: dict[str, Any] | None,
) -> list[str]:
    """根据 permission mode 和 runtime metadata 解析本轮真实工具白名单。"""

    try:
        mode = SkillPermissionMode(str(permission_mode).strip().lower() or "restrict")
    except ValueError:
        mode = SkillPermissionMode.RESTRICT
    if mode == SkillPermissionMode.RESTRICT:
        return [str(item).strip() for item in tools_allowed if str(item).strip()]
    inherited = extract_mounted_tool_names(metadata)
    if inherited:
        return inherited
    return [str(item).strip() for item in tools_allowed if str(item).strip()]
