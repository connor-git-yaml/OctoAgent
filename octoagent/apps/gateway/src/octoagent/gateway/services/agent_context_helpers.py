"""F113：agent_context module-level 常量 / dataclass / 自由函数。

从 agent_context.py 抽出的拆分叶子模块：不依赖 AgentContextService 与任何 mixin
（对外部包 octoagent.core/memory 及同目录 agent_decision 的依赖与拆分前一致），
供主文件与各 mixin 单向 import（打破 mixin ↔ 主文件循环依赖）。
对外 import 路径保持不变：agent_context.py 对本模块全部名字做 re-export。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog
from octoagent.core.models import (
    AgentProfile,
    AgentRuntime,
    AgentRuntimeRole,
    AgentSession,
    AgentSessionKind,
    ContextRequestKind,
    ContextResolveRequest,
    MemoryNamespace,
    MemoryNamespaceKind,
    is_private_namespace,
    OwnerProfile,
    OwnerProfileOverlay,
    Project,
    ProjectBindingType,
    RuntimeControlContext,
    SessionContextState,
    Task,
)
from octoagent.memory import (
    MemoryAccessPolicy,
    MemoryRecallHit,
    MemoryRecallHookOptions,
    MemoryRecallPostFilterMode,
    MemoryRecallRerankMode,
)

from .agent_decision import (
    is_worker_behavior_profile,
)

log = structlog.get_logger()

_MEMORY_BINDING_TYPES = {
    ProjectBindingType.SCOPE,
    ProjectBindingType.MEMORY_SCOPE,
    ProjectBindingType.IMPORT_SCOPE,
}

_WEEKDAY_NAMES_ZH = {
    0: "星期一",
    1: "星期二",
    2: "星期三",
    3: "星期四",
    4: "星期五",
    5: "星期六",
    6: "星期日",
}

_WEEKDAY_NAMES_EN = {
    0: "Monday",
    1: "Tuesday",
    2: "Wednesday",
    3: "Thursday",
    4: "Friday",
    5: "Saturday",
    6: "Sunday",
}


@dataclass
class SystemPromptContext:
    """_build_system_blocks 的输入上下文。"""

    project: Project | None
    task: Task
    current_user_text: str
    agent_profile: AgentProfile
    owner_profile: OwnerProfile
    owner_overlay: OwnerProfileOverlay | None
    # F084 Phase 4 T067：bootstrap_completed 替代已退役的 bootstrap_session 状态字段
    bootstrap_completed: bool
    recent_summary: str
    session_replay: SessionReplayProjection | None
    memory_hits: list[MemoryRecallHit]
    memory_scope_ids: list[str]
    memory_prefetch_mode: str
    worker_capability: str | None
    dispatch_metadata: dict[str, Any]
    runtime_context: RuntimeControlContext | None
    include_runtime_context: bool = True
    loaded_skills_content: str = ""
    skill_injection_budget: int = 0
    progress_notes: list[dict] | None = None
    deferred_tools_text: str = ""
    role_card: str = ""
    pipeline_catalog_content: str = ""


_SESSION_TRANSCRIPT_LIMIT_DEFAULT = 20  # 兼容回退值
_SESSION_TRANSCRIPT_LIMIT_MAX = 80  # 绝对上限
_SESSION_TRANSCRIPT_LIMIT_MIN = 4   # 绝对下限


def _dynamic_transcript_limit(conversation_budget_tokens: int | None = None) -> int:
    """根据对话 token 预算动态计算 transcript 保留条数。

    每条 entry 平均约 200-400 token（含工具调用约 300）。
    短对话全保留，长对话按预算扩展到最多 80 条。
    """
    if conversation_budget_tokens is None or conversation_budget_tokens <= 0:
        return _SESSION_TRANSCRIPT_LIMIT_DEFAULT
    estimated_per_entry = 300
    limit = conversation_budget_tokens // estimated_per_entry
    return max(_SESSION_TRANSCRIPT_LIMIT_MIN, min(limit, _SESSION_TRANSCRIPT_LIMIT_MAX))


def _memory_recall_preferences(agent_profile: AgentProfile | None) -> dict[str, Any]:
    if agent_profile is None or not isinstance(agent_profile.context_budget_policy, dict):
        return {}
    raw = agent_profile.context_budget_policy.get("memory_recall", {})
    return raw if isinstance(raw, dict) else {}


# F094 D3: 私有硬编码 worker memory recall 默认值函数已删除（5 个 key 迁移到
# packages/core/src/octoagent/core/models/agent_context.py 的
# DEFAULT_WORKER_MEMORY_RECALL_PREFERENCES module-level 常量，单一 SoT）。
# 唯一调用点 `_ensure_agent_profile_from_worker_profile` 已改用 import 常量；
# merge 顺序 `{**defaults, **existing}` 保持 baseline 一致。


def _memory_recall_planner_enabled(agent_profile: AgentProfile | None) -> bool:
    prefs = _memory_recall_preferences(agent_profile)
    raw = prefs.get("planner_enabled", False)
    if isinstance(raw, str):
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return bool(raw)


def _resolve_memory_prefetch_mode(
    *,
    request: ContextResolveRequest,
    agent_profile: AgentProfile | None,
    agent_runtime: AgentRuntime,
) -> str:
    prefs = _memory_recall_preferences(agent_profile)
    explicit = str(prefs.get("prefetch_mode", "")).strip().lower()
    if explicit in {"detailed_prefetch", "hint_first", "agent_led_hint_first"}:
        return explicit
    if (
        request.request_kind is ContextRequestKind.WORKER
        or agent_runtime.role is AgentRuntimeRole.WORKER
        or is_worker_behavior_profile(agent_profile)
    ):
        return "hint_first"
    return "agent_led_hint_first"


def _bounded_int(
    value: Any,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def build_ambient_runtime_facts(
    *,
    owner_profile: OwnerProfile | None,
    surface: str = "",
    now: datetime | None = None,
) -> tuple[dict[str, str], list[str]]:
    """构建主 Agent / child worker 可复用的当前环境事实。"""

    degraded_reasons: list[str] = []
    resolved_now = now or datetime.now(tz=UTC)
    timezone = (
        owner_profile.timezone.strip()
        if owner_profile is not None and owner_profile.timezone.strip()
        else "UTC"
    )
    if owner_profile is None or not owner_profile.timezone.strip():
        degraded_reasons.append("owner_timezone_missing")
    try:
        zone = ZoneInfo(timezone)
    except ZoneInfoNotFoundError:
        degraded_reasons.append("owner_timezone_invalid")
        timezone = "UTC"
        zone = ZoneInfo("UTC")

    locale = (
        owner_profile.locale.strip()
        if owner_profile is not None and owner_profile.locale.strip()
        else "zh-CN"
    )
    if owner_profile is None or not owner_profile.locale.strip():
        degraded_reasons.append("owner_locale_missing")

    localized = resolved_now.astimezone(zone)
    weekday_map = _WEEKDAY_NAMES_ZH if locale.lower().startswith("zh") else _WEEKDAY_NAMES_EN
    weekday = weekday_map.get(localized.weekday(), str(localized.weekday()))
    offset = localized.strftime("%z")
    if len(offset) == 5:
        offset = f"{offset[:3]}:{offset[3:]}"

    payload = {
        "current_datetime_local": localized.strftime("%Y-%m-%d %H:%M:%S"),
        "current_date_local": localized.strftime("%Y-%m-%d"),
        "current_time_local": localized.strftime("%H:%M:%S"),
        "current_weekday_local": weekday,
        "timezone": timezone,
        "utc_offset": offset or "+00:00",
        "locale": locale,
        "surface": surface.strip() or "chat",
        "source": "system_clock",
    }
    return payload, list(dict.fromkeys(degraded_reasons))


def effective_memory_access_policy(agent_profile: AgentProfile | None) -> MemoryAccessPolicy:
    raw = agent_profile.memory_access_policy if agent_profile is not None else {}
    if not isinstance(raw, dict):
        raw = {}
    return MemoryAccessPolicy.model_validate(raw)


def build_default_memory_recall_hook_options(
    *,
    subject_hint: str = "",
    agent_profile: AgentProfile | None = None,
) -> MemoryRecallHookOptions:
    prefs = _memory_recall_preferences(agent_profile)
    return MemoryRecallHookOptions(
        post_filter_mode=MemoryRecallPostFilterMode(
            str(
                prefs.get(
                    "post_filter_mode",
                    MemoryRecallPostFilterMode.KEYWORD_OVERLAP.value,
                )
            ).strip()
            or MemoryRecallPostFilterMode.KEYWORD_OVERLAP.value
        ),
        rerank_mode=MemoryRecallRerankMode(
            str(
                prefs.get(
                    "rerank_mode",
                    MemoryRecallRerankMode.HEURISTIC.value,
                )
            ).strip()
            or MemoryRecallRerankMode.HEURISTIC.value
        ),
        subject_hint=subject_hint,
        min_keyword_overlap=_bounded_int(
            prefs.get("min_keyword_overlap"),
            default=1,
            minimum=1,
            maximum=8,
        ),
    )


def memory_recall_scope_limit(
    agent_profile: AgentProfile | None,
    *,
    default: int,
) -> int:
    prefs = _memory_recall_preferences(agent_profile)
    return _bounded_int(prefs.get("scope_limit"), default=default, minimum=1, maximum=8)


def memory_recall_per_scope_limit(
    agent_profile: AgentProfile | None,
    *,
    default: int,
) -> int:
    prefs = _memory_recall_preferences(agent_profile)
    return _bounded_int(prefs.get("per_scope_limit"), default=default, minimum=1, maximum=12)


def memory_recall_max_hits(
    agent_profile: AgentProfile | None,
    *,
    default: int,
) -> int:
    prefs = _memory_recall_preferences(agent_profile)
    return _bounded_int(prefs.get("max_hits"), default=default, minimum=1, maximum=20)


def legacy_session_id_for_task(task: Task) -> str:
    return task.thread_id or task.task_id


def build_projected_session_id(
    *,
    thread_id: str,
    surface: str,
    scope_id: str = "",
    project_id: str = "",
) -> str:
    resolved_thread_id = thread_id.strip() or "task"
    resolved_surface = surface.strip() or "unknown"
    resolved_scope_id = scope_id.strip()
    parts = [f"surface:{resolved_surface}"]
    if resolved_scope_id:
        parts.append(f"scope:{resolved_scope_id}")
    if project_id:
        parts.append(f"project:{project_id}")
    elif not resolved_scope_id:
        parts.append(f"project:{project_id or 'default'}")
    parts.append(f"thread:{resolved_thread_id}")
    return "|".join(parts)


def build_scope_aware_session_id(
    task: Task,
    *,
    project_id: str = "",
) -> str:
    return build_projected_session_id(
        thread_id=legacy_session_id_for_task(task).strip() or task.task_id,
        surface=task.requester.channel,
        scope_id=task.scope_id,
        project_id=project_id,
    )


def _parse_scope_id(scope_id: str) -> tuple[str, str]:
    """解析 scope_id，兼容新旧格式。

    旧格式: ``workspace:{wid}:chat:web:{tid}`` → 返回 (wid, tid)
    新格式: ``project:{pid}:chat:web:{tid}``  → 返回 (pid, tid)
    """
    if not scope_id:
        return ("", "")
    parts = scope_id.split(":")
    # 至少需要 prefix:value:...:tid 共 5 段
    if len(parts) >= 5 and parts[0] in ("workspace", "project"):
        return (parts[1], parts[-1])
    # 无法识别的格式，原样返回空 project hint
    return ("", scope_id)


def build_agent_runtime_id(
    *,
    role: AgentRuntimeRole,
    project_id: str,
    agent_profile_id: str,
    worker_profile_id: str,
    worker_capability: str,
) -> str:
    parts = [f"role:{role.value}", f"project:{project_id or 'default'}"]
    if role is AgentRuntimeRole.WORKER:
        if worker_profile_id:
            parts.append(f"worker_profile:{worker_profile_id}")
        else:
            parts.append(f"worker_capability:{worker_capability or 'general'}")
    else:
        parts.append(f"agent_profile:{agent_profile_id or 'default'}")
    return "|".join(parts)


def build_agent_session_id(
    *,
    agent_runtime_id: str,
    kind: AgentSessionKind,
    legacy_session_id: str,
    work_id: str,
    task_id: str,
) -> str:
    parts = [f"runtime:{agent_runtime_id}", f"kind:{kind.value}"]
    if kind is AgentSessionKind.WORKER_INTERNAL:
        parts.append(f"work:{work_id or task_id}")
    else:
        parts.append(f"legacy:{legacy_session_id or task_id}")
    return "|".join(parts)


def build_memory_namespace_id(
    *,
    kind: MemoryNamespaceKind,
    project_id: str,
    agent_runtime_id: str = "",
) -> str:
    parts = [f"memory_namespace:{kind.value}", f"project:{project_id or 'default'}"]
    if agent_runtime_id:
        parts.append(f"runtime:{agent_runtime_id}")
    return "|".join(parts)


def build_private_memory_scope_ids(
    *,
    kind: MemoryNamespaceKind,
    agent_runtime_id: str,
    agent_session_id: str = "",
) -> list[str]:
    if not is_private_namespace(kind):
        return []
    # WORKER_PRIVATE 写路径已死（F094）；owner 派生保留以正确读出既有 worker_private records
    owner = "worker" if kind is MemoryNamespaceKind.WORKER_PRIVATE else "main"
    scope_ids: list[str] = []
    if agent_session_id:
        scope_ids.append(f"memory/private/{owner}/session:{agent_session_id}")
    if agent_runtime_id:
        scope_ids.append(f"memory/private/{owner}/runtime:{agent_runtime_id}")
    return scope_ids


def session_state_matches_scope(
    state: SessionContextState,
    *,
    task: Task,
    project_id: str = "",
) -> bool:
    thread_id = legacy_session_id_for_task(task)
    if thread_id and state.thread_id and state.thread_id != thread_id:
        return False
    if project_id and state.project_id and state.project_id != project_id:
        return False
    return True


@dataclass(slots=True)
class ResolvedContextBundle:
    """resolver 运行时内部结果。"""

    request: ContextResolveRequest
    project: Project | None
    agent_profile: AgentProfile
    owner_profile: OwnerProfile
    owner_overlay: OwnerProfileOverlay | None
    # F084 Phase 4 T067：bootstrap_completed 替代已退役的 bootstrap_session
    bootstrap_completed: bool
    agent_runtime: AgentRuntime
    agent_session: AgentSession
    session_state: SessionContextState
    memory_namespaces: list[MemoryNamespace]
    memory_hits: list[MemoryRecallHit]
    memory_scope_ids: list[str]
    degraded_reasons: list[str]
    memory_recall: dict[str, Any]


@dataclass(slots=True)
class RecallPlanningContext:
    """agent-led recall planner 的最小上下文。"""

    request: ContextResolveRequest
    project: Project | None
    agent_profile: AgentProfile
    agent_runtime: AgentRuntime
    agent_session: AgentSession
    prefetch_mode: str
    planner_enabled: bool
    query: str
    recent_summary: str
    memory_scope_ids: list[str]
    transcript_entries: list[dict[str, str]]


@dataclass(slots=True)
class SessionReplayProjection:
    """从 AgentSession turn store 重建出的可 replay/sanitize 投影。"""

    transcript_entries: list[dict[str, str]] = field(default_factory=list)
    tool_exchange_lines: list[str] = field(default_factory=list)
    latest_context_summary: str = ""
    latest_model_reply_preview: str = ""
    source: str = "empty"
    dropped_orphan_tool_calls: int = 0
    dropped_orphan_tool_results: int = 0
