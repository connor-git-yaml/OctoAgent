"""SkillRunner 数据模型。"""

from __future__ import annotations

import re
import time
import warnings
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from octoagent.core.models.agent_context import DEFAULT_PERMISSION_PRESET
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


# ---------------------------------------------------------------------------
# 语义级循环检测器
# ---------------------------------------------------------------------------

# 从工具参数中提取操作目标（文件路径、关键词等）的正则
_PATH_LIKE_RE = re.compile(
    r"""(?:^|[\s"'=:])"""           # 前缀分隔符
    r"""(/?(?:[\w.\-]+/)+[\w.\-]+)""",  # 路径片段
    re.VERBOSE,
)


@dataclass
class ToolTargetTracker:
    """语义级循环检测：追踪每个工具对同一「目标」的重复操作次数。

    解决 exact signature match 无法覆盖的场景：
    Worker 用略微不同的参数反复调用 terminal.exec 读取同一文件。
    例如 `rg -n 'keyword' file.yaml` 和 `sed -n '1,220p' file.yaml`
    签名不同，但目标（file.yaml）相同。

    策略：从 tool_calls 的参数中提取「目标关键词」（文件路径、URL、
    搜索词等），按 (tool_name, target) 维度统计。单个目标在
    ``target_repeat_threshold`` 步内被同一工具操作超过阈值即判定循环。
    """

    target_repeat_threshold: int = 5
    # (tool_name, target_key) → 出现次数
    _target_counts: dict[tuple[str, str], int] = field(
        default_factory=lambda: defaultdict(int),
    )
    # 最近 N 步的 tool_name 序列，用于检测 A→B→A→B 交替循环
    _recent_tools: list[str] = field(default_factory=list)
    _alternation_window: int = 10

    def record(self, tool_calls: list[Any]) -> str | None:
        """记录本步 tool_calls，返回循环描述（None 表示正常）。"""
        for call in tool_calls:
            tool_name: str = getattr(call, "tool_name", "")
            args: dict[str, Any] = getattr(call, "arguments", {})
            targets = self._extract_targets(tool_name, args)
            for t in targets:
                key = (tool_name, t)
                self._target_counts[key] += 1
                if self._target_counts[key] >= self.target_repeat_threshold:
                    return (
                        f"工具 {tool_name} 对目标 '{t}' "
                        f"重复操作 {self._target_counts[key]} 次"
                    )
            # 交替循环检测
            self._recent_tools.append(tool_name)

        alt = self._check_alternation()
        if alt:
            return alt
        return None

    def _extract_targets(self, tool_name: str, args: dict[str, Any]) -> list[str]:
        """从工具参数中提取操作目标（去重）。"""
        seen: set[str] = set()
        targets: list[str] = []
        args_str = " ".join(str(v) for v in args.values())

        def _add(t: str) -> None:
            if t not in seen:
                seen.add(t)
                targets.append(t)

        # 1. 常见参数名直接提取（优先，精度高）
        for key in ("path", "file", "filename", "url", "query", "command"):
            val = args.get(key)
            if isinstance(val, str) and val.strip():
                _add(val.strip()[:120])

        # 2. 文件路径正则（补充提取）
        for m in _PATH_LIKE_RE.finditer(args_str):
            _add(m.group(1))

        # 3. 如果未提取到任何目标，用参数值的前 80 字符做粗粒度指纹
        if not targets and args_str.strip():
            _add(args_str.strip()[:80])

        return targets

    def _check_alternation(self) -> str | None:
        """检测 A→B→A→B... 类型的交替循环。

        窗口内仅出现 <=2 个不同工具且交替次数 >= 窗口的 80% 即判定。
        """
        window = self._recent_tools[-self._alternation_window:]
        if len(window) < self._alternation_window:
            return None
        unique = set(window)
        if len(unique) > 2:
            return None
        # 统计「方向变化」次数
        changes = sum(
            1 for a, b in zip(window, window[1:]) if a != b
        )
        # 交替循环的特征：方向变化次数 >= 窗口 - 1 的 70%
        if changes >= (self._alternation_window - 1) * 0.7:
            tool_list = ", ".join(sorted(unique))
            return f"检测到工具交替循环: {tool_list}（最近 {self._alternation_window} 步）"
        return None

    def summary(self) -> dict[str, int]:
        """返回当前统计（用于调试日志）。"""
        top = Counter(self._target_counts).most_common(5)
        return {f"{tn}:{tgt}": cnt for (tn, tgt), cnt in top}


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
    # Feature 061: Agent 实例级权限 Preset（决定工具调用的 allow/ask 策略）
    permission_preset: str = Field(
        default=DEFAULT_PERMISSION_PRESET,
        description="Agent 权限 Preset（minimal/normal/full）",
    )
    conversation_messages: list[dict[str, str]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    usage_limits: UsageLimits = Field(default_factory=UsageLimits)
    # Feature 064: Subagent 关联父 Task
    parent_task_id: str | None = Field(
        default=None,
        description="父任务 ID。Subagent 的 Child Task 通过此字段关联父 Task。",
    )


class ToolCallSpec(BaseModel):
    """Skill 输出中的工具调用规格。"""

    tool_name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    tool_call_id: str = Field(
        default="",
        description=(
            "LLM 返回的 function call ID。"
            "Chat Completions 路径从 tool_calls[].id 填充；"
            "Responses API 路径从 function_call.call_id 填充。"
            "为空时回退到自然语言回填模式（FR-064-12 向后兼容）。"
        ),
    )


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
    tool_call_id: str = Field(
        default="",
        description="对应 ToolCallSpec.tool_call_id，用于回填标准 tool role message",
    )


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

    # Feature 064 P1-A: Subagent 心跳和并发控制
    heartbeat_interval_steps: int = Field(
        default=5,
        ge=1,
        le=100,
        description="Subagent 心跳上报间隔（每 N 个 step 上报一次）",
    )
    max_concurrent_subagents: int = Field(
        default=5,
        ge=1,
        le=20,
        description="单个 Worker 最大并发 Subagent 数",
    )

    # Feature 064 P2-A: 上下文压缩配置
    compaction_model_alias: str | None = Field(
        default=None,
        description=(
            "上下文压缩摘要生成使用的模型别名。"
            "默认 None 时使用 'compaction' alias（需在 LiteLLM Proxy 预配置）。"
        ),
    )
    compaction_threshold_ratio: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description=(
            "上下文压缩触发阈值比例（token 数 / max_tokens）。"
            "设为 1.0 则永不触发（回滚方案）。"
        ),
    )
    compaction_recent_turns: int = Field(
        default=8,
        ge=1,
        le=50,
        description="上下文压缩 Level 2 保留最近 N 轮对话。",
    )


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
