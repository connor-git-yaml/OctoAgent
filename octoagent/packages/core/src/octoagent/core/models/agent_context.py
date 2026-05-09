"""Feature 033: Agent Profile / Bootstrap / Context Continuity 领域模型。"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


# Feature 090 D2: 显式化 worker kind，消除 _is_worker_behavior_profile 的 metadata 探测
# - "main": 主 Agent（默认）
# - "worker": 来自 WorkerProfile 的镜像 Agent
# - "subagent": 临时 Subagent（F097 启用）
AgentProfileKind = Literal["main", "worker", "subagent"]


# Feature 061: 权限 Preset 系统默认值（单一事实源）
DEFAULT_PERMISSION_PRESET = "normal"


# F094 D1: Worker memory recall 默认偏好集中常量
#
# 之前 baseline gateway 服务层内有一个私有硬编码默认值函数；F094 块 D 把
# 5 个 key 的硬编码挪到 core models 层（与 AgentProfile / MemoryNamespaceKind
# 同文件），让 gateway dispatch 路径与 F107 (WorkerProfile/AgentProfile 合并)
# 都从单一 SoT 读。
#
# 用 MappingProxyType 锁成只读，防止未来调用方意外原地 mutate（Codex Phase D
# LOW-2 闭环：当前唯一调用点用 dict unpacking 不会污染，但加 read-only 防御
# 让 F107 接入时不需要担心 mutation race）。读取方仍可用 dict unpacking
# `{**DEFAULT_...}` 构造可变副本（baseline merge 顺序兼容）。
#
# 行为零变更约束（spec NFR-1 / Codex spec LOW-7 闭环）：
# - 5 个 key 与 baseline 完全一致
# - merge 顺序保留 baseline `{**defaults, **existing}`：existing 覆盖 defaults
from types import MappingProxyType
from typing import Mapping

_DEFAULT_WORKER_MEMORY_RECALL_PREFERENCES_RAW: dict[str, Any] = {
    "prefetch_mode": "hint_first",
    "planner_enabled": True,
    "scope_limit": 4,
    "per_scope_limit": 4,
    "max_hits": 8,
}
DEFAULT_WORKER_MEMORY_RECALL_PREFERENCES: Mapping[str, Any] = MappingProxyType(
    _DEFAULT_WORKER_MEMORY_RECALL_PREFERENCES_RAW
)


def resolve_permission_preset(*profiles: Any, fallback: str = DEFAULT_PERMISSION_PRESET) -> str:
    """从 profile 链中解析 permission_preset。

    遍历传入的 AgentProfile / WorkerProfile（可为 None），
    优先返回第一个在 metadata 中设置了 permission_preset 的值。
    全部未设置时返回 fallback。
    """
    for profile in profiles:
        if profile is None:
            continue
        meta = getattr(profile, "metadata", None) or {}
        if isinstance(meta, dict):
            preset = meta.get("permission_preset", "")
            if preset and isinstance(preset, str) and preset.strip():
                return preset.strip().lower()
    return fallback


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


class AgentProfileScope(StrEnum):
    SYSTEM = "system"
    PROJECT = "project"


class WorkerProfileStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class WorkerProfileOriginKind(StrEnum):
    BUILTIN = "builtin"
    CUSTOM = "custom"
    CLONED = "cloned"
    EXTRACTED = "extracted"


class OwnerOverlayScope(StrEnum):
    PROJECT = "project"
    WORKSPACE = "workspace"


class ContextRequestKind(StrEnum):
    CHAT = "chat"
    AUTOMATION = "automation"
    WORK = "work"
    PIPELINE = "pipeline"
    WORKER = "worker"
    BOOTSTRAP = "bootstrap"


class AgentRuntimeRole(StrEnum):
    MAIN = "main"
    WORKER = "worker"


class AgentRuntimeStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


def normalize_runtime_role(value: str) -> AgentRuntimeRole:
    """旧数据兼容：butler → MAIN。

    数据防御层（Feature 063 引入），处理极端情况下遗留的旧枚举值。
    F091 Phase B 起删除启动 migration（_migrate_butler_naming），此函数作为
    最后一道防御保留——store 读取层若需兜底应显式调用此函数。
    """
    if value == "butler":
        return AgentRuntimeRole.MAIN
    return AgentRuntimeRole(value)


class AgentSessionKind(StrEnum):
    MAIN_BOOTSTRAP = "main_bootstrap"
    WORKER_INTERNAL = "worker_internal"
    DIRECT_WORKER = "direct_worker"
    SUBAGENT_INTERNAL = "subagent_internal"


def normalize_session_kind(value: str) -> AgentSessionKind:
    """旧数据兼容：butler_main → MAIN_BOOTSTRAP。

    数据防御层（Feature 063 引入），处理极端情况下遗留的旧枚举值。
    F091 Phase B 起删除启动 migration（_migrate_butler_naming），此函数作为
    最后一道防御保留——store 读取层若需兜底应显式调用此函数。
    """
    if value == "butler_main":
        return AgentSessionKind.MAIN_BOOTSTRAP
    return AgentSessionKind(value)


class AgentSessionStatus(StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"


class AgentSessionTurnKind(StrEnum):
    USER_MESSAGE = "user_message"
    ASSISTANT_MESSAGE = "assistant_message"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    CONTEXT_SUMMARY = "context_summary"


class MemoryNamespaceKind(StrEnum):
    PROJECT_SHARED = "project_shared"
    AGENT_PRIVATE = "agent_private"
    WORKER_PRIVATE = "worker_private"


class AgentProfile(BaseModel):
    """主 Agent / automation / delegation 可消费的正式 profile。

    Feature 090 D2: 新增 ``kind`` 字段显式标记 Agent 类型，消除
    ``_is_worker_behavior_profile`` 通过 metadata 字符串探测的隐式判断。
    完全合并 WorkerProfile→AgentProfile 留给 M6 F107 Capability Layer Refactor。
    """

    profile_id: str = Field(min_length=1)
    scope: AgentProfileScope = AgentProfileScope.SYSTEM
    project_id: str = Field(default="")
    name: str = Field(min_length=1)
    kind: AgentProfileKind = Field(
        default="main",
        description="Agent 类型：main（主 Agent）/ worker（WorkerProfile 镜像）/ subagent（F097）",
    )
    persona_summary: str = Field(default="")
    instruction_overlays: list[str] = Field(default_factory=list)
    model_alias: str = Field(default="main")
    tool_profile: str = Field(default="standard")
    policy_refs: list[str] = Field(default_factory=list)
    memory_access_policy: dict[str, Any] = Field(default_factory=dict)
    context_budget_policy: dict[str, Any] = Field(default_factory=dict)
    bootstrap_template_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    resource_limits: dict[str, Any] = Field(default_factory=dict, description="资源限制覆盖")
    version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)


class WorkerProfile(BaseModel):
    """Root Agent 的正式静态配置对象。"""

    profile_id: str = Field(min_length=1)
    scope: AgentProfileScope = AgentProfileScope.PROJECT
    project_id: str = Field(default="")
    name: str = Field(min_length=1)
    summary: str = Field(default="")
    model_alias: str = Field(default="main")
    tool_profile: str = Field(default="minimal")
    default_tool_groups: list[str] = Field(default_factory=list)
    selected_tools: list[str] = Field(default_factory=list)
    runtime_kinds: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    resource_limits: dict[str, Any] = Field(default_factory=dict, description="资源限制覆盖")
    status: WorkerProfileStatus = WorkerProfileStatus.DRAFT
    origin_kind: WorkerProfileOriginKind = WorkerProfileOriginKind.CUSTOM
    draft_revision: int = Field(default=0, ge=0)
    active_revision: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    archived_at: datetime | None = None


class WorkerProfileRevision(BaseModel):
    """Root Agent 已发布 revision。"""

    revision_id: str = Field(min_length=1)
    profile_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    change_summary: str = Field(default="")
    snapshot_payload: dict[str, Any] = Field(default_factory=dict)
    created_by: str = Field(default="")
    created_at: datetime = Field(default_factory=_utc_now)


class OwnerProfile(BaseModel):
    """Owner 全局基础身份与协作偏好。

    Feature 082 P0：``preferred_address`` 默认值从 ``"你"`` 改为 ``""``。
    历史 ``"你"`` 是伪默认（Profile 输出永远显示 ``preferred_address: 你`` 让用户
    误以为是脏数据）。空串明确表示"未设置"，由 Agent system prompt 层 fallback
    到适当称呼（如 "Owner"）。
    新增 ``last_synced_from_profile_at`` 用于 P2 ProfileGenerator 回填的时间戳追踪。
    """

    owner_profile_id: str = Field(min_length=1)
    display_name: str = Field(default="Owner")
    preferred_address: str = Field(default="")
    timezone: str = Field(default="UTC")
    locale: str = Field(default="zh-CN")
    working_style: str = Field(default="")
    interaction_preferences: list[str] = Field(default_factory=list)
    boundary_notes: list[str] = Field(default_factory=list)
    main_session_only_fields: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    version: int = Field(default=1, ge=1)
    last_synced_from_profile_at: datetime | None = Field(default=None)
    # F085 T4: 删除 dead 字段 bootstrap_completed / last_synced_from_user_md
    # - DDL 没有这两列（owner_profiles CREATE TABLE 列表 0 命中）
    # - save_owner_profile INSERT 字段列表 0 命中 (永远不持久化)
    # - F35 修复方案让 bootstrap 完成判定直接读 USER.md (_user_md_substantively_filled)，
    #   不依赖 owner_profile.bootstrap_completed → 真消费方 0 处
    # - F42 修复 PERSISTED_FIELDS 元组也明确不含这两字段
    # 字段保留是 model 层 dead 噪音，无任何真实场景使用，本次清理。
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)


class OwnerProfileOverlay(BaseModel):
    """Project / workspace 作用域的 owner 覆盖层。"""

    owner_overlay_id: str = Field(min_length=1)
    owner_profile_id: str = Field(min_length=1)
    scope: OwnerOverlayScope = OwnerOverlayScope.PROJECT
    project_id: str = Field(default="")
    assistant_identity_overrides: dict[str, Any] = Field(default_factory=dict)
    working_style_override: str = Field(default="")
    interaction_preferences_override: list[str] = Field(default_factory=list)
    boundary_notes_override: list[str] = Field(default_factory=list)
    bootstrap_template_ids: list[str] = Field(default_factory=list)
    main_session_only_overrides: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)


class AgentRuntime(BaseModel):
    """主 Agent / Worker 的长期运行体。"""

    agent_runtime_id: str = Field(min_length=1)
    project_id: str = Field(default="")
    agent_profile_id: str = Field(default="")
    worker_profile_id: str = Field(default="")
    role: AgentRuntimeRole = AgentRuntimeRole.MAIN
    name: str = Field(default="")
    persona_summary: str = Field(default="")
    status: AgentRuntimeStatus = AgentRuntimeStatus.ACTIVE
    # Feature 061: 权限 Preset（minimal/normal/full），决定工具调用的 allow/ask 策略
    permission_preset: str = Field(
        default=DEFAULT_PERMISSION_PRESET,
        description="权限 Preset（minimal/normal/full）",
    )
    # Feature 061: 角色卡片，替代 WorkerType 多模板的角色引导
    role_card: str = Field(
        default="",
        description="Agent 角色卡片文本",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    archived_at: datetime | None = None


class AgentSession(BaseModel):
    """绑定到 AgentRuntime 的正式会话对象。"""

    agent_session_id: str = Field(min_length=1)
    agent_runtime_id: str = Field(min_length=1)
    kind: AgentSessionKind = AgentSessionKind.MAIN_BOOTSTRAP
    status: AgentSessionStatus = AgentSessionStatus.ACTIVE
    project_id: str = Field(default="")
    surface: str = Field(default="chat")
    thread_id: str = Field(default="")
    legacy_session_id: str = Field(default="")
    alias: str = Field(default="")
    parent_agent_session_id: str = Field(default="")
    parent_worker_runtime_id: str = Field(default="")
    """Subagent 所属 Worker 的 AgentRuntime ID（仅 SUBAGENT_INTERNAL 类型使用）。"""
    work_id: str = Field(default="")
    a2a_conversation_id: str = Field(default="")
    last_context_frame_id: str = Field(default="")
    last_recall_frame_id: str = Field(default="")
    recent_transcript: list[dict[str, str]] = Field(default_factory=list)
    rolling_summary: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)
    # Feature 067: 记忆提取游标
    memory_cursor_seq: int = Field(
        default=0,
        ge=0,
        description="记忆提取游标，标记已处理到的 turn_seq 位置。"
        "cursor=0 表示尚未进行过任何提取。"
        "cursor=N 表示 turn_seq <= N 的 turns 已被处理。",
    )
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    closed_at: datetime | None = None


class AgentSessionTurn(BaseModel):
    """AgentSession 的正式 turn / tool-turn 持久化记录。"""

    agent_session_turn_id: str = Field(min_length=1)
    agent_session_id: str = Field(min_length=1)
    task_id: str = Field(default="")
    turn_seq: int = Field(default=0, ge=0)
    kind: AgentSessionTurnKind = AgentSessionTurnKind.USER_MESSAGE
    role: str = Field(default="")
    tool_name: str = Field(default="")
    artifact_ref: str = Field(default="")
    summary: str = Field(default="")
    dedupe_key: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)


class MemoryNamespace(BaseModel):
    """Project shared / Agent private / Worker private 记忆命名空间。"""

    namespace_id: str = Field(min_length=1)
    project_id: str = Field(default="")
    agent_runtime_id: str = Field(default="")
    kind: MemoryNamespaceKind = MemoryNamespaceKind.PROJECT_SHARED
    name: str = Field(default="")
    description: str = Field(default="")
    memory_scope_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    archived_at: datetime | None = None


class SessionContextState(BaseModel):
    """短期上下文 durable state。"""

    session_id: str = Field(min_length=1)
    agent_runtime_id: str = Field(default="")
    agent_session_id: str = Field(default="")
    thread_id: str = Field(default="")
    project_id: str = Field(default="")
    task_ids: list[str] = Field(default_factory=list)
    recent_turn_refs: list[str] = Field(default_factory=list)
    recent_artifact_refs: list[str] = Field(default_factory=list)
    rolling_summary: str = Field(default="")
    summary_artifact_id: str = Field(default="")
    last_context_frame_id: str = Field(default="")
    last_recall_frame_id: str = Field(default="")
    updated_at: datetime = Field(default_factory=_utc_now)


class ContextSourceRef(BaseModel):
    """上下文来源引用。"""

    ref_type: str = Field(min_length=1)
    ref_id: str = Field(min_length=1)
    label: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContextResolveRequest(BaseModel):
    """统一 resolver 输入。"""

    request_id: str = Field(min_length=1)
    request_kind: ContextRequestKind
    surface: str = Field(default="chat")
    project_id: str = Field(default="")
    task_id: str | None = None
    session_id: str | None = None
    work_id: str | None = None
    pipeline_run_id: str | None = None
    automation_run_id: str | None = None
    worker_run_id: str | None = None
    agent_runtime_id: str | None = None
    agent_session_id: str | None = None
    agent_profile_id: str | None = None
    owner_overlay_id: str | None = None
    trigger_text: str | None = None
    thread_id: str | None = None
    requester_id: str | None = None
    requester_role: str = Field(default="owner")
    input_artifact_refs: list[str] = Field(default_factory=list)
    delegation_metadata: dict[str, Any] = Field(default_factory=dict)
    runtime_metadata: dict[str, Any] = Field(default_factory=dict)


class ContextFrame(BaseModel):
    """一次真实运行所消费的上下文快照。"""

    context_frame_id: str = Field(min_length=1)
    task_id: str = Field(default="")
    session_id: str = Field(default="")
    agent_runtime_id: str = Field(default="")
    agent_session_id: str = Field(default="")
    project_id: str = Field(default="")
    agent_profile_id: str = Field(default="")
    owner_profile_id: str = Field(default="")
    owner_overlay_id: str = Field(default="")
    owner_profile_revision: int | None = None
    bootstrap_session_id: str | None = None
    recall_frame_id: str | None = None
    system_blocks: list[dict[str, Any]] = Field(default_factory=list)
    recent_summary: str = Field(default="")
    memory_namespace_ids: list[str] = Field(default_factory=list)
    memory_hits: list[dict[str, Any]] = Field(default_factory=list)
    delegation_context: dict[str, Any] = Field(default_factory=dict)
    budget: dict[str, Any] = Field(default_factory=dict)
    degraded_reason: str = Field(default="")
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utc_now)


class RecallFrame(BaseModel):
    """一次 Agent 侧召回的 durable 快照。"""

    recall_frame_id: str = Field(min_length=1)
    agent_runtime_id: str = Field(default="")
    agent_session_id: str = Field(default="")
    context_frame_id: str = Field(default="")
    task_id: str = Field(default="")
    project_id: str = Field(default="")
    query: str = Field(default="")
    recent_summary: str = Field(default="")
    memory_namespace_ids: list[str] = Field(default_factory=list)
    memory_hits: list[dict[str, Any]] = Field(default_factory=list)
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    budget: dict[str, Any] = Field(default_factory=dict)
    degraded_reason: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)
    # F094 C4: 双字段语义区分（Codex MED-5 闭环 / spec §2.2 Gap-4）
    # queried = 本次 recall 实际查询了哪些 namespace kind（去重；从 resolved
    #           namespaces 派生）
    # hit     = 本次 recall 实际有 hit 命中的 namespace kind（从
    #           memory_hits[i].metadata.namespace_kind 归一化生成）
    # F096 audit 双查询模式：「曾查过私有」vs「实际命中私有」语义不同。
    queried_namespace_kinds: list[MemoryNamespaceKind] = Field(default_factory=list)
    hit_namespace_kinds: list[MemoryNamespaceKind] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utc_now)


class ContextResolveResult(BaseModel):
    """统一 resolver 输出。"""

    context_frame_id: str = Field(min_length=1)
    effective_agent_profile_id: str = Field(min_length=1)
    effective_agent_runtime_id: str = Field(default="")
    effective_agent_session_id: str = Field(default="")
    effective_owner_overlay_id: str | None = None
    owner_profile_revision: int | None = None
    bootstrap_session_id: str | None = None
    recall_frame_id: str | None = None
    system_blocks: list[dict[str, Any]] = Field(default_factory=list)
    recent_summary: str = Field(default="")
    memory_hits: list[dict[str, Any]] = Field(default_factory=list)
    degraded_reason: str = Field(default="")
    source_refs: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# F084 Phase 2 T030 / T031：OwnerProfile sync hook
# ---------------------------------------------------------------------------
# 设计：USER.md 是 SoT（FR-9.1），OwnerProfile 退化为派生只读视图。
# - 系统启动时：owner_profile_sync_on_startup() 解析 USER.md 回填字段
# - user_profile.update 写入成功后：sync_owner_profile_from_user_md() 异步触发
# - 解析失败：写 WARN 日志，不抛异常（防 R9 启动失败）

import re as _re  # noqa: E402（模块级 sync hook 区域局部 import）
from pathlib import Path  # noqa: E402

import structlog as _structlog  # noqa: E402

_sync_log = _structlog.get_logger(__name__)

# USER.md 实质填充阈值（FR-9.5）
USER_MD_FILLED_MIN_CHARS = 100


def _user_md_substantively_filled(user_md_path: Path) -> bool:
    """USER.md 是否实质填充：存在 + len(content) > 100（FR-9.5）。"""
    try:
        if not user_md_path.exists():
            return False
        content = user_md_path.read_text(encoding="utf-8")
    except OSError:
        return False
    return len(content) > USER_MD_FILLED_MIN_CHARS


def _parse_user_md_fields(content: str) -> dict[str, str]:
    """从 USER.md 文本中解析常见字段（best-effort）。

    支持的轻量 pattern：
        时区: Asia/Shanghai
        语言: zh-CN
        称呼: 你 / Connor
        风格: ...
    解析失败时返回部分匹配结果，不抛异常。
    """
    fields: dict[str, str] = {}
    if not content:
        return fields

    # 中文/英文 key 都识别（不区分大小写）
    patterns = {
        "timezone": r"(?:时区|timezone)[:：]\s*([\w/+\-]+)",
        "locale": r"(?:语言|locale)[:：]\s*([\w\-]+)",
        "preferred_address": r"(?:称呼|preferred[_ ]address)[:：]\s*([^\n]+)",
        "working_style": r"(?:工作风格|风格|working[_ ]style)[:：]\s*([^\n]+)",
        "display_name": r"(?:姓名|名字|display[_ ]name)[:：]\s*([^\n]+)",
    }
    for field_name, pattern in patterns.items():
        match = _re.search(pattern, content, _re.IGNORECASE)
        if match:
            fields[field_name] = match.group(1).strip()
    return fields


async def sync_owner_profile_from_user_md(user_md_path: Path) -> dict[str, Any] | None:
    """从 USER.md 解析字段更新 OwnerProfile 派生视图（user_profile.update 后异步触发）。

    Args:
        user_md_path: USER.md 绝对路径

    Returns:
        解析得到的字段 dict（供调用方持久化）；解析失败 / 文件不存在时 None。
        本函数本身不直接写库——具体写库由调用方（Phase 3 sync 服务或 lifespan）完成。
    """
    try:
        if not user_md_path.exists():
            _sync_log.debug("user_md_sync_skipped_no_file", path=str(user_md_path))
            return None
        content = user_md_path.read_text(encoding="utf-8")
    except OSError as exc:
        _sync_log.warning(
            "user_md_sync_read_failed",
            path=str(user_md_path),
            error=str(exc),
        )
        return None

    try:
        fields = _parse_user_md_fields(content)
    except Exception as exc:  # noqa: BLE001 — 解析失败不抛
        _sync_log.warning(
            "user_md_sync_parse_failed",
            path=str(user_md_path),
            error=str(exc),
        )
        return None

    fields["bootstrap_completed"] = _user_md_substantively_filled(user_md_path)
    fields["last_synced_from_user_md"] = _utc_now().isoformat()
    fields["__source__"] = str(user_md_path)
    _sync_log.info(
        "user_md_synced",
        path=str(user_md_path),
        fields_extracted=[k for k in fields.keys() if not k.startswith("__")],
    )
    return fields


async def owner_profile_sync_on_startup(user_md_path: Path) -> dict[str, Any] | None:
    """系统启动时同步 OwnerProfile（FR-9.2）。

    与 sync_owner_profile_from_user_md 行为一致；命名不同是为了在 lifespan
    日志/调用栈里清晰区分启动期 vs 写入后触发。
    """
    return await sync_owner_profile_from_user_md(user_md_path)


# ---------------------------------------------------------------------------
# F42 修复（白盒 + E2E review 暴露）：sync hook 真写库
# ---------------------------------------------------------------------------
# 之前：sync_owner_profile_from_user_md 只 return dict，注释说"具体写库由调用方完成"，
# 但 main.py lifespan + user_profile.update 调用方都没真写库 →
# owner_profiles 表的 timezone / locale / preferred_address 等永远是默认值 →
# 系统 prompt 注入 owner_profile.timezone 永远是 "UTC" → 用户感知 LLM 不记偏好。
# 影响范围：agent_context.py:250-265 / 3419-3421 等 5+ 处真消费这些字段。
#
# 修复：本 helper 把 sync return dict merge 进现有 OwnerProfile + save 写库。
# 注意：DDL 不含 bootstrap_completed / last_synced_from_user_md 列（F35 维持
# OwnerProfile 模型字段但表层不持久化，bootstrap 状态由 USER.md 实质填充判定），
# helper 只 merge DDL 真有的字段（timezone / locale / preferred_address /
# display_name / working_style）。

async def apply_user_md_sync_to_owner_profile(
    store_group: Any,  # StoreGroup（避免循环 import）
    sync_fields: dict[str, Any] | None,
    *,
    owner_profile_id: str = "default-owner",
) -> bool:
    """把 sync_owner_profile_from_user_md 解析的字段落库（防 F42 audit gap）。

    Args:
        store_group: StoreGroup 实例（持有 agent_context_store）
        sync_fields: sync_owner_profile_from_user_md 返回的 dict；None 时无操作
        owner_profile_id: owner profile ID（默认 "default-owner"，与 startup_bootstrap 对齐）

    Returns:
        True：sync 成功落库；False：sync_fields 为空 / 写库失败 / store 不可用
    """
    if not sync_fields:
        return False
    if store_group is None:
        return False
    try:
        store = store_group.agent_context_store
    except AttributeError:
        return False

    try:
        existing = await store.get_owner_profile(owner_profile_id)
    except Exception as exc:
        _sync_log.warning(
            "apply_user_md_sync_get_failed",
            owner_profile_id=owner_profile_id,
            error=str(exc),
        )
        return False

    # 构造 update payload — 只 merge DDL 真有的字段（OwnerProfile 模型有的额外字段
    # 如 bootstrap_completed / last_synced_from_user_md 不在 DDL 列里，save 不会写）
    PERSISTED_FIELDS = (
        "timezone", "locale", "preferred_address", "display_name", "working_style",
    )
    if existing is None:
        # 没有就用模型默认值创建
        base = OwnerProfile(
            owner_profile_id=owner_profile_id,
            display_name="",
            preferred_address="",
            timezone="",
            locale="",
        )
    else:
        base = existing
    base_dict = base.model_dump()
    changed = False
    for key in PERSISTED_FIELDS:
        if key in sync_fields and sync_fields[key]:
            new_val = str(sync_fields[key]).strip()
            if new_val and base_dict.get(key) != new_val:
                base_dict[key] = new_val
                changed = True
    if not changed:
        return False  # 内容相同，无需写库

    base_dict["updated_at"] = _utc_now()
    new_profile = OwnerProfile(**base_dict)
    try:
        await store.save_owner_profile(new_profile)
        _sync_log.info(
            "apply_user_md_sync_saved",
            owner_profile_id=owner_profile_id,
            fields_updated=[k for k in PERSISTED_FIELDS if k in sync_fields],
        )
        return True
    except Exception as exc:
        _sync_log.error(
            "apply_user_md_sync_save_failed",
            owner_profile_id=owner_profile_id,
            error=str(exc),
        )
        return False
