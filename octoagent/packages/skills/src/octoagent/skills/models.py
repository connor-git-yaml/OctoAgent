"""SkillRunner 数据模型。"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from octoagent.tooling.models import ToolProfile
from pydantic import BaseModel, Field


class SkillRunStatus(StrEnum):
    """Skill 执行终态。"""

    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class ErrorCategory(StrEnum):
    """Skill 错误分类。"""

    REPEAT_ERROR = "repeat_error"
    VALIDATION_ERROR = "validation_error"
    TOOL_EXECUTION_ERROR = "tool_execution_error"
    LOOP_DETECTED = "loop_detected"
    STEP_LIMIT_EXCEEDED = "step_limit_exceeded"
    INPUT_VALIDATION_ERROR = "input_validation_error"


class RetryPolicy(BaseModel):
    """重试策略。"""

    max_attempts: int = Field(default=3, ge=1, le=20)
    backoff_ms: int = Field(default=500, ge=0, le=60_000)
    upgrade_model_on_fail: bool = Field(default=False)


class LoopGuardPolicy(BaseModel):
    """循环保护策略。"""

    max_steps: int = Field(default=8, ge=1, le=200)
    repeat_signature_threshold: int = Field(default=3, ge=2, le=20)


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


class SkillManifestModel(BaseModel):
    """SkillManifest 公共字段模型（不含类型对象）。"""

    skill_id: str = Field(min_length=1)
    version: str = Field(default="0.1.0", min_length=1)
    model_alias: str = Field(default="main", min_length=1)
    tools_allowed: list[str] = Field(default_factory=list)
    tool_profile: ToolProfile = Field(default=ToolProfile.STANDARD)
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    loop_guard: LoopGuardPolicy = Field(default_factory=LoopGuardPolicy)
    context_budget: ContextBudgetPolicy = Field(default_factory=ContextBudgetPolicy)
    description: str | None = Field(default=None)
    description_md: str | None = Field(default=None)
    metadata: dict[str, Any] = Field(default_factory=dict)
