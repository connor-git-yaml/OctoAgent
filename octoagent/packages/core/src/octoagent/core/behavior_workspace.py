"""Feature 055: Behavior workspace 文件解析。

Feature 063 扩展: Bootstrap 生命周期管理 + BehaviorLoadProfile 差异化加载。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from functools import cache
from importlib import resources
from pathlib import Path
from typing import TypedDict

import structlog

from .models.agent_context import AgentProfile
from .models.behavior import (
    BehaviorEditabilityMode,
    BehaviorLayerKind,
    BehaviorPackFile,
    BehaviorReviewMode,
    BehaviorVisibility,
    BehaviorWorkspace,
    BehaviorWorkspaceFile,
    BehaviorWorkspaceScope,
    ProjectPathManifest,
    ProjectPathManifestFile,
    StorageBoundaryHints,
)

log = structlog.get_logger(__name__)

SHARED_BEHAVIOR_FILE_IDS = ("AGENTS.md", "USER.md", "TOOLS.md", "BOOTSTRAP.md")
PROJECT_SHARED_BEHAVIOR_FILE_IDS = ("PROJECT.md", "KNOWLEDGE.md")
AGENT_PRIVATE_BEHAVIOR_FILE_IDS = ("IDENTITY.md", "SOUL.md", "HEARTBEAT.md")
PROJECT_AGENT_OVERLAY_FILE_IDS = ("IDENTITY.md", "SOUL.md", "TOOLS.md", "PROJECT.md")
INSTRUCTION_BOOTSTRAP_FILE_IDS = ("README.md",)
CORE_BEHAVIOR_FILE_IDS = (
    "AGENTS.md",
    "USER.md",
    "PROJECT.md",
    "KNOWLEDGE.md",
    "TOOLS.md",
    "BOOTSTRAP.md",
)
ADVANCED_BEHAVIOR_FILE_IDS = AGENT_PRIVATE_BEHAVIOR_FILE_IDS
ALL_BEHAVIOR_FILE_IDS = CORE_BEHAVIOR_FILE_IDS + ADVANCED_BEHAVIOR_FILE_IDS
BEHAVIOR_FILE_BUDGETS = {
    "AGENTS.md": 3200,
    "USER.md": 1800,
    "PROJECT.md": 2400,
    "KNOWLEDGE.md": 2200,
    "TOOLS.md": 4000,
    "BOOTSTRAP.md": 2200,
    "SOUL.md": 1600,
    "IDENTITY.md": 1600,
    "HEARTBEAT.md": 1600,
}
BEHAVIOR_OVERLAY_ORDER = (
    "default_template",
    "system_file",
    "system_local_file",
    "agent_file",
    "agent_local_file",
    "project_file",
    "project_local_file",
    "project_agent_file",
    "project_agent_local_file",
)
_SLUG_RE = re.compile(r"[^a-z0-9]+")

SHARED_BOOTSTRAP_TEMPLATE_IDS = tuple(
    f"behavior:system:{file_id}" for file_id in SHARED_BEHAVIOR_FILE_IDS
)
AGENT_PRIVATE_BOOTSTRAP_TEMPLATE_IDS = tuple(
    f"behavior:agent:{file_id}" for file_id in AGENT_PRIVATE_BEHAVIOR_FILE_IDS
)
PROJECT_SHARED_BOOTSTRAP_TEMPLATE_IDS = tuple(
    [f"behavior:project:{file_id}" for file_id in PROJECT_SHARED_BEHAVIOR_FILE_IDS]
    + [f"behavior:project:instructions/{file_id}" for file_id in INSTRUCTION_BOOTSTRAP_FILE_IDS]
)
PROJECT_AGENT_BOOTSTRAP_TEMPLATE_IDS = tuple(
    f"behavior:project_agent:{file_id}" for file_id in PROJECT_AGENT_OVERLAY_FILE_IDS
)

# Feature 063: Bootstrap 完成标记
BOOTSTRAP_COMPLETED_MARKER = "<!-- COMPLETED -->"

# Feature 063: 行为文件总大小警告阈值（字符）
_BEHAVIOR_SIZE_WARNING_THRESHOLD = 15000
_BEHAVIOR_TEMPLATE_PACKAGE = "octoagent.core.behavior_templates"
_BEHAVIOR_TEMPLATE_VARIANTS = {
    ("IDENTITY.md", False): "IDENTITY.main.md",
    ("IDENTITY.md", True): "IDENTITY.worker.md",
}


# ---------------------------------------------------------------------------
# Feature 063: BehaviorLoadProfile — 差异化加载
# ---------------------------------------------------------------------------


class BehaviorLoadProfile(StrEnum):
    """Agent 角色对应的行为文件加载级别。"""

    FULL = "full"  # 主 Agent：全部 9 个文件
    WORKER = "worker"  # Worker：AGENTS + TOOLS + IDENTITY + PROJECT + KNOWLEDGE
    MINIMAL = "minimal"  # Subagent：AGENTS + TOOLS + IDENTITY + USER


_PROFILE_ALLOWLIST: dict[BehaviorLoadProfile, frozenset[str]] = {
    BehaviorLoadProfile.FULL: frozenset(ALL_BEHAVIOR_FILE_IDS),
    BehaviorLoadProfile.WORKER: frozenset({
        "AGENTS.md", "TOOLS.md", "IDENTITY.md", "PROJECT.md", "KNOWLEDGE.md",
    }),
    BehaviorLoadProfile.MINIMAL: frozenset({
        "AGENTS.md", "TOOLS.md", "IDENTITY.md", "USER.md",
    }),
}


def get_profile_allowlist(profile: BehaviorLoadProfile) -> frozenset[str]:
    """返回指定 load_profile 对应的 file_id 白名单（公共 API）。"""
    return _PROFILE_ALLOWLIST[profile]


# ---------------------------------------------------------------------------
# Feature 063: OnboardingState — Bootstrap 生命周期
# ---------------------------------------------------------------------------


@dataclass
class OnboardingState:
    """Bootstrap 引导状态，持久化到 .onboarding-state.json。"""

    bootstrap_seeded_at: str | None = None
    onboarding_completed_at: str | None = None

    def is_completed(self) -> bool:
        return self.onboarding_completed_at is not None


def _onboarding_state_path(project_root: Path) -> Path:
    """返回 onboarding 状态文件路径。"""
    return project_root.resolve() / "behavior" / ".onboarding-state.json"


def load_onboarding_state(
    project_root: Path,
    *,
    bootstrap_file_path: Path | None = None,
) -> OnboardingState:
    """读取 onboarding 状态，含被动完成检测（路径 B：文件删除触发）。

    如果 bootstrap_seeded_at 存在但 BOOTSTRAP.md 已不在磁盘上，
    自动标记 onboarding 完成。
    """
    state_path = _onboarding_state_path(project_root)
    state = OnboardingState()

    if state_path.exists():
        try:
            raw = json.loads(state_path.read_text(encoding="utf-8"))
            state.bootstrap_seeded_at = raw.get("bootstrap_seeded_at")
            state.onboarding_completed_at = raw.get("onboarding_completed_at")
        except (json.JSONDecodeError, OSError):
            log.warning("onboarding_state_read_failed", path=str(state_path))
    else:
        # Legacy 兼容检测（T1.7）：无 state 文件但项目已在使用
        state = _detect_legacy_onboarding_completion(project_root)
        if state.is_completed():
            save_onboarding_state(project_root, state)
            return state

    # 路径 B（T1.5）：文件删除触发完成
    if state.bootstrap_seeded_at and not state.onboarding_completed_at:
        if bootstrap_file_path is None:
            bootstrap_file_path = (
                project_root.resolve() / "behavior" / "system" / "BOOTSTRAP.md"
            )
        if not bootstrap_file_path.exists():
            state.onboarding_completed_at = datetime.now(UTC).isoformat()
            save_onboarding_state(project_root, state)
            log.info(
                "onboarding_completed_via_file_deletion",
                bootstrap_path=str(bootstrap_file_path),
            )

    return state


def save_onboarding_state(project_root: Path, state: OnboardingState) -> None:
    """原子写入 onboarding 状态文件（先写 .tmp 再 rename）。"""
    state_path = _onboarding_state_path(project_root)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "bootstrap_seeded_at": state.bootstrap_seeded_at,
        "onboarding_completed_at": state.onboarding_completed_at,
    }
    # 原子写入：先写临时文件再 rename
    fd, tmp_path = tempfile.mkstemp(
        dir=str(state_path.parent), suffix=".tmp", prefix=".onboarding-state-",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp_path, str(state_path))
    except Exception:
        # 清理临时文件
        with suppress(OSError):
            os.unlink(tmp_path)
        raise


def mark_onboarding_completed(project_root: Path) -> OnboardingState:
    """将 onboarding 标记为已完成。"""
    state = load_onboarding_state(project_root)
    if not state.onboarding_completed_at:
        state.onboarding_completed_at = datetime.now(UTC).isoformat()
        save_onboarding_state(project_root, state)
        log.info("onboarding_completed_via_marker", project_root=str(project_root))
    return state


def _detect_legacy_onboarding_completion(project_root: Path) -> OnboardingState:
    """Legacy 兼容检测（T1.7）：无 state 文件时推断 onboarding 是否已完成。

    指标 1：IDENTITY.md 内容已被修改（与默认模板不同）
    指标 2：存在历史 session 记录（data/ 目录非空）
    """
    root = project_root.resolve()
    state = OnboardingState()

    # 指标 1：检查 IDENTITY.md 是否已被修改
    identity_paths = [
        root / "behavior" / "agents" / "main" / "IDENTITY.md",
    ]
    identity_modified = False
    for identity_path in identity_paths:
        if identity_path.exists():
            try:
                content = identity_path.read_text(encoding="utf-8").strip()
                # 检查是否仍为默认模板内容
                default_marker = "当前 Agent 名称："
                if content and default_marker not in content:
                    identity_modified = True
                    break
                # 即使包含默认标记，如果长度比默认模板长很多，也视为已修改
                if len(content) > 200:
                    identity_modified = True
                    break
            except OSError:
                pass

    # 指标 2：检查 data/ 目录是否非空（有历史 session）
    data_dir = root / "data"
    has_sessions = False
    if data_dir.exists():
        with suppress(OSError):
            has_sessions = any(data_dir.iterdir())

    if identity_modified or has_sessions:
        now = datetime.now(UTC).isoformat()
        state.bootstrap_seeded_at = now  # 回填
        state.onboarding_completed_at = now
        log.info(
            "legacy_onboarding_completion_detected",
            identity_modified=identity_modified,
            has_sessions=has_sessions,
        )

    return state


# ---------------------------------------------------------------------------
# Feature 063: Head/Tail 截断策略
# ---------------------------------------------------------------------------


def truncate_behavior_content(content: str, budget: int) -> str:
    """按 head/tail 策略截断行为文件内容。

    保留 70% 头部 + 20% 尾部 + 中间插入截断标记。
    最小预算 64 字符——低于此阈值返回空字符串。
    """
    content = content.strip()
    if len(content) <= budget:
        return content
    if budget < 64:
        return ""

    # 截断标记本身需要的空间（估算）
    marker_template = (
        "\n\n[... 中间内容已截断（原文 {total} 字符，预算 {budget} 字符），"
        "完整内容请使用 offset/limit 参数分段读取 ...]\n\n"
    )
    marker = marker_template.format(total=len(content), budget=budget)
    marker_len = len(marker)

    usable = budget - marker_len
    if usable < 40:
        # 预算太紧，只保留头部
        return content[:budget]

    head_len = int(usable * 0.7)
    tail_len = int(usable * 0.2)
    # 剩余给 marker
    head = content[:head_len].rstrip()
    tail = content[-tail_len:].lstrip() if tail_len > 0 else ""

    return head + marker + tail


# ---------------------------------------------------------------------------
# Feature 063: 行为文件总大小测量
# ---------------------------------------------------------------------------


def measure_behavior_total_size(
    project_root: Path,
    agent_slug: str = "main",
) -> dict[str, int]:
    """测量所有行为文件的字符总量。

    Returns:
        {"file_id": char_count, ..., "__total__": total_chars}
    """
    root = project_root.resolve()
    sizes: dict[str, int] = {}
    total = 0

    system_dir = behavior_shared_dir(root)
    agent_dir_path = behavior_agent_dir(root, agent_slug)

    for file_id in ALL_BEHAVIOR_FILE_IDS:
        # 尝试多个可能的路径
        candidates = []
        if file_id in SHARED_BEHAVIOR_FILE_IDS:
            candidates.append(system_dir / file_id)
        if file_id in AGENT_PRIVATE_BEHAVIOR_FILE_IDS:
            candidates.append(agent_dir_path / file_id)
        if file_id in PROJECT_SHARED_BEHAVIOR_FILE_IDS:
            # 默认 project
            candidates.append(root / "projects" / "default" / "behavior" / file_id)

        char_count = 0
        for path in candidates:
            if path.exists():
                try:
                    char_count = len(path.read_text(encoding="utf-8"))
                    break
                except OSError:
                    pass
        sizes[file_id] = char_count
        total += char_count

    sizes["__total__"] = total
    return sizes


@dataclass(frozen=True, slots=True)
class _BehaviorFileTemplate:
    file_id: str
    title: str
    layer: BehaviorLayerKind
    visibility: BehaviorVisibility
    share_with_workers: bool
    is_advanced: bool
    primary_scope: BehaviorWorkspaceScope
    editable_mode: BehaviorEditabilityMode
    review_mode: BehaviorReviewMode


@dataclass(frozen=True, slots=True)
class _ResolvedBehaviorSource:
    path: Path
    scope: BehaviorWorkspaceScope
    source_kind: str


@dataclass(frozen=True, slots=True)
class _BehaviorBudgetResult:
    content: str
    budget_chars: int
    original_char_count: int
    effective_char_count: int
    truncated: bool
    truncation_reason: str


def _slugify(value: str, *, fallback: str) -> str:
    normalized = _SLUG_RE.sub("-", value.strip().lower()).strip("-")
    return normalized or fallback


def _normalize_project_slug(project_slug: str) -> str:
    return _slugify(project_slug, fallback="default")


def normalize_behavior_agent_slug(agent_ref: str) -> str:
    normalized = _SLUG_RE.sub("-", agent_ref.strip().lower()).strip("-")
    if normalized:
        return normalized
    raw = agent_ref.strip()
    if not raw:
        return "agent"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
    return f"agent-{digest}"


def resolve_behavior_agent_slug(agent_profile: AgentProfile) -> str:
    metadata = agent_profile.metadata
    candidates = [
        str(metadata.get("behavior_agent_slug", "")).strip(),
        str(metadata.get("source_worker_profile_id", "")).strip().split(":")[-1],
        agent_profile.name.strip(),
        agent_profile.profile_id.strip().split(":")[-1],
    ]
    for candidate in candidates:
        if candidate:
            return normalize_behavior_agent_slug(candidate)
    return "agent"


def behavior_system_dir(project_root: Path) -> Path:
    return behavior_shared_dir(project_root)


def behavior_shared_dir(project_root: Path) -> Path:
    return project_root.resolve() / "behavior" / "system"


def behavior_agent_dir(project_root: Path, agent_slug: str) -> Path:
    return project_root.resolve() / "behavior" / "agents" / _slugify(
        agent_slug,
        fallback="agent",
    )


def project_root_dir(project_root: Path, project_slug: str) -> Path:
    return project_root.resolve() / "projects" / _normalize_project_slug(project_slug)


def behavior_project_dir(project_root: Path, project_slug: str) -> Path:
    return project_root_dir(project_root, project_slug) / "behavior"


def behavior_legacy_project_dir(project_root: Path, project_slug: str) -> Path:
    slug = project_slug.strip() or _normalize_project_slug(project_slug)
    return project_root.resolve() / "behavior" / "projects" / slug


def behavior_project_agent_dir(
    project_root: Path,
    project_slug: str,
    agent_slug: str,
) -> Path:
    return (
        behavior_project_dir(project_root, project_slug)
        / "agents"
        / _slugify(agent_slug, fallback="agent")
    )


def project_workspace_dir(project_root: Path, project_slug: str) -> Path:
    return project_root_dir(project_root, project_slug) / "workspace"


def project_data_dir(project_root: Path, project_slug: str) -> Path:
    return project_root_dir(project_root, project_slug) / "data"


def project_notes_dir(project_root: Path, project_slug: str) -> Path:
    return project_root_dir(project_root, project_slug) / "notes"


def project_artifacts_dir(project_root: Path, project_slug: str) -> Path:
    return project_root_dir(project_root, project_slug) / "artifacts"


def project_secret_bindings_path(project_root: Path, project_slug: str) -> Path:
    return project_root_dir(project_root, project_slug) / "project.secret-bindings.json"


def project_instructions_dir(project_root: Path, project_slug: str) -> Path:
    return behavior_project_dir(project_root, project_slug) / "instructions"


def ensure_filesystem_skeleton(
    project_root: Path,
    project_slug: str = "default",
    agent_slug: str = "main",
) -> list[str]:
    """在 clean install 后创建 behavior 目录骨架和最小 scaffold 文件。

    返回新创建的路径列表。
    """
    root = project_root.resolve()
    created: list[str] = []

    # 必须存在的目录
    dirs = [
        behavior_shared_dir(root),
        behavior_agent_dir(root, agent_slug),
        behavior_project_dir(root, project_slug),
        behavior_project_agent_dir(root, project_slug, agent_slug),
        project_workspace_dir(root, project_slug),
        project_data_dir(root, project_slug),
        project_notes_dir(root, project_slug),
        project_artifacts_dir(root, project_slug),
        project_instructions_dir(root, project_slug),
    ]
    for d in dirs:
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)
            created.append(str(d))

    # project.secret-bindings.json
    sb = project_secret_bindings_path(root, project_slug)
    if not sb.exists():
        sb.write_text("{}\n", encoding="utf-8")
        created.append(str(sb))

    # instructions/README.md
    readme = project_instructions_dir(root, project_slug) / "README.md"
    if not readme.exists():
        readme.write_text(
            "# Project Instructions\n\n"
            "把项目级自定义指令放在这个目录。\n"
            "文件会按字母序加载到 Agent 的 project-shared 行为层。\n",
            encoding="utf-8",
        )
        created.append(str(readme))

    # 行为文件模板 materialize（writeFileIfMissing）
    # 注意：只写 SYSTEM_SHARED 和 PROJECT_SHARED 文件。
    # AGENT_PRIVATE 文件需要知道真实 agent_slug（来自 AgentProfile），
    # 由 startup_bootstrap.ensure_startup_records 在 profile 确定后调用
    # materialize_agent_behavior_files() 来创建。
    skeleton_file_ids = (*SHARED_BEHAVIOR_FILE_IDS, *PROJECT_SHARED_BEHAVIOR_FILE_IDS)
    for file_id in skeleton_file_ids:
        scope = _template_scope_for_file(file_id)
        target = _default_behavior_file_path(
            project_root=root,
            project_slug=project_slug,
            agent_slug=agent_slug,
            file_id=file_id,
            scope=scope,
        )
        if target.exists():
            continue
        try:
            content = _default_content_for_file(
                file_id=file_id,
                is_worker_profile=False,
                agent_name="Main Agent",
                project_label="当前项目",
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            created.append(str(target))
            # T1.2: 创建 BOOTSTRAP.md 时写入 bootstrap_seeded_at
            if file_id == "BOOTSTRAP.md":
                try:
                    state = load_onboarding_state(root)
                    if not state.bootstrap_seeded_at:
                        state.bootstrap_seeded_at = datetime.now(UTC).isoformat()
                        save_onboarding_state(root, state)
                except Exception:
                    log.warning("onboarding_state_seed_failed", path=str(target))
        except Exception:
            log.warning(
                "behavior_template_materialize_failed",
                file_id=file_id,
                path=str(target),
            )

    return created


def materialize_agent_behavior_files(
    project_root: Path,
    *,
    agent_slug: str,
    agent_name: str = "",
    is_worker_profile: bool = False,
) -> list[str]:
    """为新 Agent 创建 agent-private 行为文件（writeIfMissing）。

    在新 Worker/Agent 实例化时调用，确保 IDENTITY.md / SOUL.md / HEARTBEAT.md
    被写入 ``behavior/agents/{agent_slug}/``。已存在的文件不会被覆盖。

    Returns:
        新创建的文件路径列表。
    """
    root = project_root.resolve()
    slug = normalize_behavior_agent_slug(agent_slug)
    created: list[str] = []

    for file_id in AGENT_PRIVATE_BEHAVIOR_FILE_IDS:
        target = behavior_agent_dir(root, slug) / file_id
        if target.exists():
            continue
        try:
            content = _default_content_for_file(
                file_id=file_id,
                is_worker_profile=is_worker_profile,
                agent_name=agent_name or slug,
                project_label="当前项目",
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            created.append(str(target))
        except Exception:
            log.warning(
                "agent_behavior_materialize_failed",
                file_id=file_id,
                agent_slug=slug,
                path=str(target),
            )

    if created:
        log.info(
            "agent_behavior_files_materialized",
            agent_slug=slug,
            is_worker=is_worker_profile,
            count=len(created),
        )
    return created


def materialize_project_behavior_files(
    project_root: Path,
    *,
    project_slug: str,
    project_name: str = "",
) -> list[str]:
    """为新项目创建 project-shared 行为文件和基础设施（writeIfMissing）。

    在新项目创建时调用，确保 PROJECT.md / KNOWLEDGE.md 以及 instructions/README.md、
    project.secret-bindings.json 和必要目录结构被初始化。已存在的文件不会被覆盖。

    Returns:
        新创建的文件/目录路径列表。
    """
    root = project_root.resolve()
    slug = _normalize_project_slug(project_slug)
    label = project_name.strip() or slug
    created: list[str] = []

    # 确保项目目录结构存在
    dirs = [
        behavior_project_dir(root, slug),
        project_workspace_dir(root, slug),
        project_data_dir(root, slug),
        project_notes_dir(root, slug),
        project_artifacts_dir(root, slug),
        project_instructions_dir(root, slug),
    ]
    for d in dirs:
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)
            created.append(str(d))

    # project.secret-bindings.json
    sb = project_secret_bindings_path(root, slug)
    if not sb.exists():
        sb.write_text(
            build_project_secret_bindings_stub(
                project_name=label, project_slug=slug,
            ),
            encoding="utf-8",
        )
        created.append(str(sb))

    # instructions/README.md
    readme = project_instructions_dir(root, slug) / "README.md"
    if not readme.exists():
        readme.write_text(
            build_project_instruction_readme(
                project_name=label, project_slug=slug,
            ),
            encoding="utf-8",
        )
        created.append(str(readme))

    # 项目级行为文件
    for file_id in PROJECT_SHARED_BEHAVIOR_FILE_IDS:
        target = behavior_project_dir(root, slug) / file_id
        if target.exists():
            continue
        try:
            content = _default_content_for_file(
                file_id=file_id,
                is_worker_profile=False,
                agent_name="Main Agent",
                project_label=label,
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            created.append(str(target))
        except Exception:
            log.warning(
                "project_behavior_materialize_failed",
                file_id=file_id,
                project_slug=slug,
                path=str(target),
            )

    if created:
        log.info(
            "project_behavior_files_materialized",
            project_slug=slug,
            count=len(created),
        )
    return created


def _relative_path_hint(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return ""


def _default_behavior_file_path(
    *,
    project_root: Path,
    project_slug: str,
    agent_slug: str,
    file_id: str,
    scope: BehaviorWorkspaceScope | None,
) -> Path:
    if scope is BehaviorWorkspaceScope.AGENT_PRIVATE:
        return behavior_agent_dir(project_root, agent_slug) / file_id
    if scope is BehaviorWorkspaceScope.PROJECT_SHARED:
        return behavior_project_dir(project_root, project_slug) / file_id
    if scope is BehaviorWorkspaceScope.PROJECT_AGENT:
        return behavior_project_agent_dir(project_root, project_slug, agent_slug) / file_id
    return behavior_shared_dir(project_root) / file_id


def build_behavior_bootstrap_template_ids(
    *,
    include_agent_private: bool = True,
    include_project_shared: bool = True,
    include_project_agent: bool = False,
) -> list[str]:
    template_ids = list(SHARED_BOOTSTRAP_TEMPLATE_IDS)
    if include_agent_private:
        template_ids.extend(AGENT_PRIVATE_BOOTSTRAP_TEMPLATE_IDS)
    if include_project_shared:
        template_ids.extend(PROJECT_SHARED_BOOTSTRAP_TEMPLATE_IDS)
    if include_project_agent:
        template_ids.extend(PROJECT_AGENT_BOOTSTRAP_TEMPLATE_IDS)
    return template_ids


def build_project_instruction_readme(
    *,
    project_name: str = "",
    project_slug: str = "",
) -> str:
    label = project_name.strip() or "当前 Project"
    slug = _normalize_project_slug(project_slug or project_name)
    return (
        f"# {label} Instructions\n\n"
        "## Canonical Roots\n"
        f"- `behavior/`: 共享与 Agent 私有行为文件\n"
        f"- `projects/{slug}/behavior/`: 当前 Project 的共享行为文件\n"
        f"- `projects/{slug}/workspace/`: 代码与主要工作目录\n"
        f"- `projects/{slug}/data/`: 原始或派生数据\n"
        f"- `projects/{slug}/notes/`: 工作笔记与研究过程\n"
        f"- `projects/{slug}/artifacts/`: 生成产物与导出结果\n\n"
        "## Storage Boundaries\n"
        "- 规则 / 人格 / 工具治理 -> behavior files\n"
        "- 事实 / 长期偏好 -> MemoryService\n"
        "- 敏感值 -> SecretService / secret bindings workflow"
        "（`project.secret-bindings.json` 只保存绑定元数据，不保存 secret 值）\n"
        "- 代码 / 数据 / 文档正文 / 笔记 / 产物 -> project workspace roots\n"
    )


def build_project_secret_bindings_stub(
    *,
    project_name: str = "",
    project_slug: str = "",
) -> str:
    slug = _normalize_project_slug(project_slug or project_name)
    label = project_name.strip() or "当前 Project"
    return (
        "{\n"
        f'  "project_slug": "{slug}",\n'
        f'  "project_name": "{label}",\n'
        '  "note": '
        '"这里只记录 project 需要的 secret bindings 元数据，不保存敏感值本身；'
        '真实 secret 值必须走 SecretService / secret bindings workflow。",\n'
        '  "bindings": []\n'
        "}\n"
    )


def _local_override_file_id(file_id: str) -> str:
    base = Path(file_id)
    return f"{base.stem}.local{base.suffix}"


def _budget_for_file(file_id: str) -> int:
    return int(BEHAVIOR_FILE_BUDGETS.get(file_id, 2000))


def _read_behavior_file(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def _apply_behavior_budget(*, file_id: str, content: str) -> _BehaviorBudgetResult:
    """应用字符预算限制，超出时使用 head/tail 截断策略（Feature 063 T2.3）。"""
    normalized = content.strip()
    original_char_count = len(normalized)
    budget_chars = _budget_for_file(file_id)
    if original_char_count <= budget_chars:
        return _BehaviorBudgetResult(
            content=normalized,
            budget_chars=budget_chars,
            original_char_count=original_char_count,
            effective_char_count=original_char_count,
            truncated=False,
            truncation_reason="",
        )
    # Feature 063: 改用 head/tail 截断（70% 头 + 20% 尾 + 中间标记）
    effective = truncate_behavior_content(normalized, budget_chars)
    return _BehaviorBudgetResult(
        content=effective,
        budget_chars=budget_chars,
        original_char_count=original_char_count,
        effective_char_count=len(effective),
        truncated=True,
        truncation_reason="char_budget_exceeded",
    )


def _template_scope_for_file(file_id: str) -> BehaviorWorkspaceScope:
    if file_id in SHARED_BEHAVIOR_FILE_IDS:
        return BehaviorWorkspaceScope.SYSTEM_SHARED
    if file_id in PROJECT_SHARED_BEHAVIOR_FILE_IDS:
        return BehaviorWorkspaceScope.PROJECT_SHARED
    return BehaviorWorkspaceScope.AGENT_PRIVATE


def _default_path_for_file(
    file_id: str,
    *,
    project_slug: str,
    agent_slug: str,
) -> str:
    scope = _template_scope_for_file(file_id)
    if scope is BehaviorWorkspaceScope.SYSTEM_SHARED:
        return f"behavior/system/{file_id}"
    if scope is BehaviorWorkspaceScope.AGENT_PRIVATE:
        return f"behavior/agents/{agent_slug}/{file_id}"
    return f"projects/{project_slug}/behavior/{file_id}"


def _resolve_behavior_source(
    *,
    project_slug: str,
    agent_slug: str,
    project_behavior_dir: Path,
    legacy_project_behavior_dir: Path,
    project_agent_dir: Path,
    agent_dir: Path,
    system_dir: Path,
    file_id: str,
) -> _ResolvedBehaviorSource | None:
    candidates: list[_ResolvedBehaviorSource] = []
    project_behavior_dirs = [project_behavior_dir]
    if legacy_project_behavior_dir != project_behavior_dir:
        project_behavior_dirs.append(legacy_project_behavior_dir)
    if file_id in PROJECT_AGENT_OVERLAY_FILE_IDS:
        project_agent_local_path = project_agent_dir / _local_override_file_id(file_id)
        project_agent_path = project_agent_dir / file_id
        if project_agent_local_path.exists():
            candidates.append(
                _ResolvedBehaviorSource(
                    path=project_agent_local_path,
                    scope=BehaviorWorkspaceScope.PROJECT_AGENT,
                    source_kind="project_agent_local_file",
                )
            )
        if project_agent_path.exists():
            candidates.append(
                _ResolvedBehaviorSource(
                    path=project_agent_path,
                    scope=BehaviorWorkspaceScope.PROJECT_AGENT,
                    source_kind="project_agent_file",
                )
            )
    if file_id in PROJECT_SHARED_BEHAVIOR_FILE_IDS or file_id in {"USER.md", "TOOLS.md"}:
        for project_dir in project_behavior_dirs:
            project_local_path = project_dir / _local_override_file_id(file_id)
            project_path = project_dir / file_id
            if project_local_path.exists():
                candidates.append(
                    _ResolvedBehaviorSource(
                        path=project_local_path,
                        scope=BehaviorWorkspaceScope.PROJECT_SHARED,
                        source_kind="project_local_file",
                    )
                )
            if project_path.exists():
                candidates.append(
                    _ResolvedBehaviorSource(
                        path=project_path,
                        scope=BehaviorWorkspaceScope.PROJECT_SHARED,
                        source_kind="project_file",
                    )
                )
    if file_id in AGENT_PRIVATE_BEHAVIOR_FILE_IDS:
        agent_local_path = agent_dir / _local_override_file_id(file_id)
        agent_path = agent_dir / file_id
        if agent_local_path.exists():
            candidates.append(
                _ResolvedBehaviorSource(
                    path=agent_local_path,
                    scope=BehaviorWorkspaceScope.AGENT_PRIVATE,
                    source_kind="agent_local_file",
                )
            )
        if agent_path.exists():
            candidates.append(
                _ResolvedBehaviorSource(
                    path=agent_path,
                    scope=BehaviorWorkspaceScope.AGENT_PRIVATE,
                    source_kind="agent_file",
                )
            )
    if file_id in SHARED_BEHAVIOR_FILE_IDS:
        system_local_path = system_dir / _local_override_file_id(file_id)
        system_path = system_dir / file_id
        if system_local_path.exists():
            candidates.append(
                _ResolvedBehaviorSource(
                    path=system_local_path,
                    scope=BehaviorWorkspaceScope.SYSTEM_SHARED,
                    source_kind="system_local_file",
                )
            )
        if system_path.exists():
            candidates.append(
                _ResolvedBehaviorSource(
                    path=system_path,
                    scope=BehaviorWorkspaceScope.SYSTEM_SHARED,
                    source_kind="system_file",
                )
            )
    return candidates[0] if candidates else None


def build_default_behavior_pack_files(
    *,
    agent_profile: AgentProfile,
    project_name: str = "",
    project_slug: str = "",
    include_advanced: bool = False,
) -> list[BehaviorPackFile]:
    files = build_default_behavior_workspace_files(
        agent_profile=agent_profile,
        project_name=project_name,
        project_slug=project_slug,
        include_advanced=include_advanced,
    )
    return [
        BehaviorPackFile(
            file_id=item.file_id,
            title=item.title,
            path_hint=item.path,
            layer=item.layer,
            content=item.content,
            visibility=item.visibility,
            share_with_workers=item.share_with_workers,
            source_kind=item.source_kind,
            budget_chars=item.budget_chars,
            original_char_count=item.original_char_count or len(item.content),
            effective_char_count=item.effective_char_count or len(item.content),
            truncated=item.truncated,
            truncation_reason=item.truncation_reason,
            metadata=dict(item.metadata),
        )
        for item in files
    ]


def build_default_behavior_workspace_files(
    *,
    agent_profile: AgentProfile,
    project_name: str = "",
    project_slug: str = "",
    include_advanced: bool = False,
    scope: BehaviorWorkspaceScope | None = None,
) -> list[BehaviorWorkspaceFile]:
    templates = _build_file_templates(include_advanced=include_advanced)
    if scope is not None:
        templates = [item for item in templates if item.primary_scope is scope]
    project_slug_value = _normalize_project_slug(project_slug)
    agent_slug = resolve_behavior_agent_slug(agent_profile)
    project_label = project_name.strip() or "当前项目"
    is_worker_profile = _is_worker_behavior_profile(agent_profile)
    files: list[BehaviorWorkspaceFile] = []

    for template in templates:
        content = _default_content_for_file(
            file_id=template.file_id,
            is_worker_profile=is_worker_profile,
            agent_name=agent_profile.name.strip() or "默认 Agent",
            project_label=project_label,
        ).strip()
        files.append(
            BehaviorWorkspaceFile(
                file_id=template.file_id,
                title=template.title,
                layer=template.layer,
                visibility=template.visibility,
                share_with_workers=template.share_with_workers,
                scope=template.primary_scope,
                path=_default_path_for_file(
                    template.file_id,
                    project_slug=project_slug_value,
                    agent_slug=agent_slug,
                ),
                editable_mode=template.editable_mode,
                review_mode=template.review_mode,
                content=content,
                source_kind="default_template",
                is_advanced=template.is_advanced,
                budget_chars=_budget_for_file(template.file_id),
                original_char_count=len(content),
                effective_char_count=len(content),
                truncated=False,
                truncation_reason="",
                metadata={
                    "is_advanced": template.is_advanced,
                    "primary_scope": template.primary_scope.value,
                    "overlay_order": list(BEHAVIOR_OVERLAY_ORDER),
                },
            )
        )
    return files


def resolve_behavior_workspace(
    *,
    project_root: Path,
    agent_profile: AgentProfile,
    project_name: str = "",
    project_slug: str = "",
    project_runtime_root: Path | str | None = None,
    load_profile: BehaviorLoadProfile = BehaviorLoadProfile.FULL,
    data_root_path: Path | str | None = None,
    notes_root_path: Path | str | None = None,
    artifacts_root_path: Path | str | None = None,
    secret_bindings_metadata_path: Path | str | None = None,
) -> BehaviorWorkspace:
    root = project_root.resolve()
    normalized_project_slug = _normalize_project_slug(project_slug)
    agent_slug = resolve_behavior_agent_slug(agent_profile)
    shared_dir = behavior_shared_dir(root)
    agent_dir = behavior_agent_dir(root, agent_slug)
    legacy_project_behavior_root = behavior_legacy_project_dir(
        root,
        project_slug or normalized_project_slug,
    )
    default_project_root = project_root_dir(root, normalized_project_slug)
    effective_project_root = (
        Path(project_runtime_root).resolve()
        if project_runtime_root is not None and str(project_runtime_root).strip()
        else default_project_root
    )
    project_behavior_root = behavior_project_dir(root, normalized_project_slug)
    project_agent_root = behavior_project_agent_dir(root, normalized_project_slug, agent_slug)
    workspace_dir = project_workspace_dir(root, normalized_project_slug)
    data_dir = (
        Path(data_root_path).resolve()
        if data_root_path is not None and str(data_root_path).strip()
        else project_data_dir(root, normalized_project_slug)
    )
    notes_dir = (
        Path(notes_root_path).resolve()
        if notes_root_path is not None and str(notes_root_path).strip()
        else project_notes_dir(root, normalized_project_slug)
    )
    artifacts_dir = (
        Path(artifacts_root_path).resolve()
        if artifacts_root_path is not None and str(artifacts_root_path).strip()
        else project_artifacts_dir(root, normalized_project_slug)
    )
    secret_bindings = (
        Path(secret_bindings_metadata_path).resolve()
        if secret_bindings_metadata_path is not None
        and str(secret_bindings_metadata_path).strip()
        else project_secret_bindings_path(root, normalized_project_slug)
    )
    project_root_source = (
        "runtime_project_root"
        if project_runtime_root is not None and str(project_runtime_root).strip()
        else "project_centered_default"
    )
    workspace_root_source = "project_centered_default"

    defaults = {
        item.file_id: item
        for item in build_default_behavior_workspace_files(
            agent_profile=agent_profile,
            project_name=project_name,
            project_slug=normalized_project_slug,
            include_advanced=False,
        )
    }
    advanced_defaults = {
        item.file_id: item
        for item in build_default_behavior_workspace_files(
            agent_profile=agent_profile,
            project_name=project_name,
            project_slug=normalized_project_slug,
            include_advanced=True,
        )
        if item.file_id not in defaults
    }

    # Feature 063: 加载 onboarding 状态（用于 BOOTSTRAP.md 跳过判断）
    onboarding_state = load_onboarding_state(root)
    # Feature 063: load_profile 白名单
    profile_allowlist = _PROFILE_ALLOWLIST[load_profile]

    files: list[BehaviorWorkspaceFile] = []
    used_project_agent = False
    used_project_agent_local = False
    used_project = False
    used_project_local = False
    used_legacy_project = False
    used_legacy_project_local = False
    used_agent = False
    used_agent_local = False
    used_system = False
    used_system_local = False
    used_default = False

    for file_id in ALL_BEHAVIOR_FILE_IDS:
        # Feature 063 T2.2: BehaviorLoadProfile 过滤
        if file_id not in profile_allowlist:
            continue

        # Feature 063 T1.3: 跳过已完成的 BOOTSTRAP.md
        if file_id == "BOOTSTRAP.md" and onboarding_state.is_completed():
            continue

        default_file = defaults.get(file_id) or advanced_defaults.get(file_id)
        if default_file is None:
            continue
        selected_source = _resolve_behavior_source(
            project_slug=normalized_project_slug,
            agent_slug=agent_slug,
            project_behavior_dir=project_behavior_root,
            legacy_project_behavior_dir=legacy_project_behavior_root,
            project_agent_dir=project_agent_root,
            agent_dir=agent_dir,
            system_dir=shared_dir,
            file_id=file_id,
        )
        selected_path: Path | None = None
        scope: BehaviorWorkspaceScope | None = default_file.scope
        source_kind = "default_template"
        content = default_file.content
        editable_mode = default_file.editable_mode
        review_mode = default_file.review_mode
        should_include = file_id in defaults

        if selected_source is not None:
            selected_path = selected_source.path
            scope = selected_source.scope
            source_kind = selected_source.source_kind
            content = _read_behavior_file(selected_source.path)
            is_legacy_project_path = bool(
                source_kind in {"project_local_file", "project_file"}
                and selected_path.is_relative_to(legacy_project_behavior_root)
                and not selected_path.is_relative_to(project_behavior_root)
            )
            if source_kind == "project_agent_local_file":
                used_project_agent_local = True
            elif source_kind == "project_agent_file":
                used_project_agent = True
            elif source_kind == "project_local_file":
                if is_legacy_project_path:
                    used_legacy_project_local = True
                else:
                    used_project_local = True
            elif source_kind == "project_file":
                if is_legacy_project_path:
                    used_legacy_project = True
                else:
                    used_project = True
            elif source_kind == "agent_local_file":
                used_agent_local = True
            elif source_kind == "agent_file":
                used_agent = True
            elif source_kind == "system_local_file":
                used_system_local = True
            elif source_kind == "system_file":
                used_system = True
            should_include = True
        elif file_id in defaults:
            used_default = True

        if not should_include:
            continue

        budget = _apply_behavior_budget(file_id=file_id, content=content)
        effective_path = selected_path or _default_behavior_file_path(
            project_root=root,
            project_slug=normalized_project_slug,
            agent_slug=agent_slug,
            file_id=file_id,
            scope=scope,
        )
        path_str = str(effective_path)
        relative_path = _relative_path_hint(effective_path, root)

        files.append(
            default_file.model_copy(
                update={
                    "scope": scope,
                    "path": relative_path or path_str,
                    "editable_mode": editable_mode,
                    "review_mode": review_mode,
                    "content": budget.content,
                    "source_kind": source_kind,
                    "budget_chars": budget.budget_chars,
                    "original_char_count": budget.original_char_count,
                    "effective_char_count": budget.effective_char_count,
                    "truncated": budget.truncated,
                    "truncation_reason": budget.truncation_reason,
                    "metadata": {
                        **dict(default_file.metadata),
                        "effective_path": path_str,
                        "relative_path": relative_path,
                        "exists_on_disk": bool(selected_path),
                        "overlay_order": list(BEHAVIOR_OVERLAY_ORDER),
                    },
                }
            )
        )

    source_chain: list[str] = []
    if used_project_agent_local:
        source_chain.append(
            f"filesystem:projects/{normalized_project_slug}/behavior/agents/{agent_slug}/*.local"
        )
    if used_project_agent:
        source_chain.append(
            f"filesystem:projects/{normalized_project_slug}/behavior/agents/{agent_slug}"
        )
    if used_project_local:
        source_chain.append(f"filesystem:projects/{normalized_project_slug}/behavior/*.local")
    if used_project:
        source_chain.append(f"filesystem:projects/{normalized_project_slug}/behavior")
    if used_legacy_project_local:
        source_chain.append(f"filesystem:{legacy_project_behavior_root.relative_to(root)}/*.local")
    if used_legacy_project:
        source_chain.append(f"filesystem:{legacy_project_behavior_root.relative_to(root)}")
    if used_agent_local:
        source_chain.append(f"filesystem:behavior/agents/{agent_slug}/*.local")
    if used_agent:
        source_chain.append(f"filesystem:behavior/agents/{agent_slug}")
    if used_system_local:
        source_chain.append("filesystem:behavior/system/*.local")
    if used_system:
        source_chain.append("filesystem:behavior/system")
    if used_default:
        source_chain.append("default_behavior_templates")

    path_manifest = ProjectPathManifest(
        repository_root=str(root),
        project_root=str(effective_project_root),
        project_root_source=project_root_source,
        project_behavior_root=str(project_behavior_root),
        project_workspace_root=str(workspace_dir),
        project_workspace_root_source=workspace_root_source,
        workspace_id="",
        workspace_slug="",
        project_data_root=str(data_dir),
        project_notes_root=str(notes_dir),
        project_artifacts_root=str(artifacts_dir),
        shared_behavior_root=str(shared_dir),
        agent_behavior_root=str(agent_dir),
        project_agent_behavior_root=str(project_agent_root),
        secret_bindings_path=str(secret_bindings),
        effective_behavior_files=[
            ProjectPathManifestFile(
                file_id=item.file_id,
                path=item.path,
                scope=item.scope,
                editable_mode=item.editable_mode,
                review_mode=item.review_mode,
                source_kind=item.source_kind,
                exists_on_disk=bool(item.metadata.get("exists_on_disk", False)),
                metadata={"title": item.title, "layer": item.layer.value},
            )
            for item in files
        ],
        metadata={
            "project_slug": normalized_project_slug,
            "agent_slug": agent_slug,
            "project_root_relative": _relative_path_hint(effective_project_root, root),
            "project_behavior_root_relative": _relative_path_hint(project_behavior_root, root),
            "project_workspace_root_relative": _relative_path_hint(workspace_dir, root),
            "project_data_root_relative": _relative_path_hint(data_dir, root),
            "project_notes_root_relative": _relative_path_hint(notes_dir, root),
            "project_artifacts_root_relative": _relative_path_hint(artifacts_dir, root),
            "shared_behavior_root_relative": _relative_path_hint(shared_dir, root),
            "agent_behavior_root_relative": _relative_path_hint(agent_dir, root),
            "project_agent_behavior_root_relative": _relative_path_hint(
                project_agent_root,
                root,
            ),
            "secret_bindings_path_relative": _relative_path_hint(secret_bindings, root),
        },
    )
    storage_boundary_hints = StorageBoundaryHints(
        facts_store="MemoryService",
        facts_access=(
            "通过 MemoryService / memory tools 读取与写入事实，"
            "不把稳定事实写进 behavior files。"
        ),
        secrets_store="SecretService",
        secrets_access=(
            "通过 SecretService / secret bindings workflow 管理敏感值；"
            "project.secret-bindings.json 只保存绑定元数据，不保存 secret 值。"
        ),
        secret_bindings_metadata_path=str(secret_bindings),
        behavior_store="behavior_files",
        workspace_roots=[
            str(workspace_dir),
            str(data_dir),
            str(notes_dir),
            str(artifacts_dir),
        ],
        note=(
            "facts 使用 MemoryService；敏感值使用 SecretService / secret bindings workflow；"
            "规则与人格使用 behavior files；"
            "代码/数据/文档/notes/artifacts 使用 project workspace roots。"
        ),
        metadata={
            "project_slug": normalized_project_slug,
            "agent_slug": agent_slug,
            "workspace_root_source": workspace_root_source,
        },
    )

    return BehaviorWorkspace(
        project_slug=normalized_project_slug,
        system_dir=str(shared_dir.relative_to(root)),
        project_dir=str(project_behavior_root.relative_to(root)),
        agent_slug=agent_slug,
        shared_dir=str(shared_dir.relative_to(root)),
        agent_dir=str(agent_dir.relative_to(root)),
        project_root_dir=_relative_path_hint(effective_project_root, root) or str(
            effective_project_root
        ),
        project_behavior_dir=str(project_behavior_root.relative_to(root)),
        project_agent_dir=str(project_agent_root.relative_to(root)),
        project_workspace_dir=_relative_path_hint(workspace_dir, root) or str(workspace_dir),
        project_data_dir=_relative_path_hint(data_dir, root) or str(data_dir),
        project_notes_dir=_relative_path_hint(notes_dir, root) or str(notes_dir),
        project_artifacts_dir=_relative_path_hint(artifacts_dir, root) or str(artifacts_dir),
        secret_bindings_path=_relative_path_hint(secret_bindings, root) or str(secret_bindings),
        files=files,
        source_chain=source_chain,
        path_manifest=path_manifest,
        storage_boundary_hints=storage_boundary_hints,
        metadata={
            "has_filesystem_sources": (
                used_project_agent
                or used_project_agent_local
                or used_project
                or used_project_local
                or used_legacy_project
                or used_legacy_project_local
                or used_agent
                or used_agent_local
                or used_system
                or used_system_local
            ),
            "has_local_overrides": (
                used_project_agent_local
                or used_project_local
                or used_legacy_project_local
                or used_agent_local
                or used_system_local
            ),
            "shared_file_ids": list(SHARED_BEHAVIOR_FILE_IDS),
            "project_shared_file_ids": list(PROJECT_SHARED_BEHAVIOR_FILE_IDS),
            "agent_private_file_ids": list(AGENT_PRIVATE_BEHAVIOR_FILE_IDS),
            "project_agent_overlay_file_ids": list(PROJECT_AGENT_OVERLAY_FILE_IDS),
            "overlay_order": list(BEHAVIOR_OVERLAY_ORDER),
            "file_budgets": dict(BEHAVIOR_FILE_BUDGETS),
        },
    )


def _build_file_templates(*, include_advanced: bool) -> list[_BehaviorFileTemplate]:
    templates = [
        _BehaviorFileTemplate(
            file_id="AGENTS.md",
            title="行为总约束",
            layer=BehaviorLayerKind.ROLE,
            visibility=BehaviorVisibility.SHARED,
            share_with_workers=True,
            is_advanced=False,
            primary_scope=BehaviorWorkspaceScope.SYSTEM_SHARED,
            editable_mode=BehaviorEditabilityMode.PROPOSAL_REQUIRED,
            review_mode=BehaviorReviewMode.REVIEW_REQUIRED,
        ),
        _BehaviorFileTemplate(
            file_id="USER.md",
            title="用户长期偏好",
            layer=BehaviorLayerKind.COMMUNICATION,
            visibility=BehaviorVisibility.SHARED,
            share_with_workers=True,
            is_advanced=False,
            primary_scope=BehaviorWorkspaceScope.SYSTEM_SHARED,
            editable_mode=BehaviorEditabilityMode.PROPOSAL_REQUIRED,
            review_mode=BehaviorReviewMode.REVIEW_REQUIRED,
        ),
        _BehaviorFileTemplate(
            file_id="PROJECT.md",
            title="项目语境",
            layer=BehaviorLayerKind.SOLVING,
            visibility=BehaviorVisibility.SHARED,
            share_with_workers=True,
            is_advanced=False,
            primary_scope=BehaviorWorkspaceScope.PROJECT_SHARED,
            editable_mode=BehaviorEditabilityMode.PROPOSAL_REQUIRED,
            review_mode=BehaviorReviewMode.REVIEW_REQUIRED,
        ),
        _BehaviorFileTemplate(
            file_id="KNOWLEDGE.md",
            title="知识入口",
            layer=BehaviorLayerKind.SOLVING,
            visibility=BehaviorVisibility.SHARED,
            share_with_workers=True,
            is_advanced=False,
            primary_scope=BehaviorWorkspaceScope.PROJECT_SHARED,
            editable_mode=BehaviorEditabilityMode.PROPOSAL_REQUIRED,
            review_mode=BehaviorReviewMode.REVIEW_REQUIRED,
        ),
        _BehaviorFileTemplate(
            file_id="TOOLS.md",
            title="工具与边界",
            layer=BehaviorLayerKind.TOOL_BOUNDARY,
            visibility=BehaviorVisibility.SHARED,
            share_with_workers=True,
            is_advanced=False,
            primary_scope=BehaviorWorkspaceScope.SYSTEM_SHARED,
            editable_mode=BehaviorEditabilityMode.PROPOSAL_REQUIRED,
            review_mode=BehaviorReviewMode.REVIEW_REQUIRED,
        ),
        _BehaviorFileTemplate(
            file_id="BOOTSTRAP.md",
            title="初始化与引导",
            layer=BehaviorLayerKind.BOOTSTRAP,
            visibility=BehaviorVisibility.SHARED,
            share_with_workers=True,
            is_advanced=False,
            primary_scope=BehaviorWorkspaceScope.SYSTEM_SHARED,
            editable_mode=BehaviorEditabilityMode.PROPOSAL_REQUIRED,
            review_mode=BehaviorReviewMode.REVIEW_REQUIRED,
        ),
    ]
    if not include_advanced:
        return templates
    return templates + [
        _BehaviorFileTemplate(
            file_id="SOUL.md",
            title="表达风格",
            layer=BehaviorLayerKind.COMMUNICATION,
            visibility=BehaviorVisibility.PRIVATE,
            share_with_workers=False,
            is_advanced=True,
            primary_scope=BehaviorWorkspaceScope.AGENT_PRIVATE,
            editable_mode=BehaviorEditabilityMode.PROPOSAL_REQUIRED,
            review_mode=BehaviorReviewMode.REVIEW_REQUIRED,
        ),
        _BehaviorFileTemplate(
            file_id="IDENTITY.md",
            title="身份补充",
            layer=BehaviorLayerKind.ROLE,
            visibility=BehaviorVisibility.PRIVATE,
            share_with_workers=False,
            is_advanced=True,
            primary_scope=BehaviorWorkspaceScope.AGENT_PRIVATE,
            editable_mode=BehaviorEditabilityMode.PROPOSAL_REQUIRED,
            review_mode=BehaviorReviewMode.REVIEW_REQUIRED,
        ),
        _BehaviorFileTemplate(
            file_id="HEARTBEAT.md",
            title="运行节奏",
            layer=BehaviorLayerKind.BOOTSTRAP,
            visibility=BehaviorVisibility.PRIVATE,
            share_with_workers=False,
            is_advanced=True,
            primary_scope=BehaviorWorkspaceScope.AGENT_PRIVATE,
            editable_mode=BehaviorEditabilityMode.PROPOSAL_REQUIRED,
            review_mode=BehaviorReviewMode.REVIEW_REQUIRED,
        ),
    ]


def get_behavior_file_review_modes(
    *, include_advanced: bool = True,
) -> dict[str, BehaviorReviewMode]:
    """返回 file_id -> BehaviorReviewMode 映射表（公开 API）。

    用于外部模块获取各行为文件的审查模式，避免直接导入私有 _build_file_templates。
    """
    return {
        tmpl.file_id: tmpl.review_mode
        for tmpl in _build_file_templates(include_advanced=include_advanced)
    }


@cache
def _load_behavior_template_text(template_name: str) -> str:
    package_files = resources.files(_BEHAVIOR_TEMPLATE_PACKAGE)
    template_path = package_files.joinpath(template_name)
    return template_path.read_text(encoding="utf-8")


def _template_name_for_file(*, file_id: str, is_worker_profile: bool) -> str:
    return _BEHAVIOR_TEMPLATE_VARIANTS.get(
        (file_id, is_worker_profile),
        file_id,
    )


def _render_behavior_template(
    template_name: str,
    *,
    agent_name: str,
    project_label: str,
) -> str:
    replacements = {
        "__AGENT_NAME__": agent_name,
        "__PROJECT_LABEL__": project_label,
        "__BOOTSTRAP_COMPLETED_MARKER__": BOOTSTRAP_COMPLETED_MARKER,
        "__SAFETY_REDLINE_ITEMS__": (
            "- 执行未经审批的不可逆操作（删除数据库、发布上线、资金操作等）\n"
            "- 将 secret 值写入 behavior files、日志或 LLM 上下文\n"
        ),
        "__IDENTITY_WAKEUP_HINT__": (
            "每次被唤醒时，你需要通过行为文件和 Memory 重建上下文——"
            "不要假设你记得之前的对话内容"
        ),
        "__IDENTITY_PROPOSAL_PERMISSION__": (
            "## 修改权限\n\n"
            "你可以通过 behavior.propose_file 提出行为文件变更 proposal。"
            "默认不静默改写关键行为文件（AGENTS.md / SOUL.md / IDENTITY.md）。\n\n"
        ),
    }
    content = _load_behavior_template_text(template_name)
    for placeholder, value in replacements.items():
        content = content.replace(placeholder, value)
    return content


def _default_content_for_file(
    *,
    file_id: str,
    is_worker_profile: bool,
    agent_name: str,
    project_label: str,
) -> str:
    template_name = _template_name_for_file(
        file_id=file_id,
        is_worker_profile=is_worker_profile,
    )
    known_file_ids = {
        *CORE_BEHAVIOR_FILE_IDS,
        *AGENT_PRIVATE_BEHAVIOR_FILE_IDS,
    }
    if file_id not in known_file_ids:
        raise ValueError(f"未支持的 behavior file: {file_id}")
    return _render_behavior_template(
        template_name,
        agent_name=agent_name,
        project_label=project_label,
    )


def _is_worker_behavior_profile(agent_profile: AgentProfile) -> bool:
    metadata = agent_profile.metadata
    return (
        str(metadata.get("source_kind", "")).strip() == "worker_profile_mirror"
        or bool(str(metadata.get("source_worker_profile_id", "")).strip())
    )


# ---------------------------------------------------------------------------
# 共享辅助函数（跨模块复用：capability_pack / control_plane / agent_decision）
# ---------------------------------------------------------------------------


def resolve_write_path_by_file_id(
    project_root: Path,
    file_id: str,
    *,
    agent_slug: str = "main",
    project_slug: str = "default",
) -> Path:
    """根据 file_id 短名自动解析行为文件的磁盘写入路径。

    路由规则：
    - SHARED (AGENTS.md, USER.md, TOOLS.md, BOOTSTRAP.md) → behavior/system/{file_id}
    - PROJECT (PROJECT.md, KNOWLEDGE.md) → projects/{project_slug}/behavior/{file_id}
    - AGENT PRIVATE (IDENTITY.md, SOUL.md, HEARTBEAT.md) → behavior/agents/{agent_slug}/{file_id}

    Args:
        project_root: 项目根目录
        file_id: 文件短名（如 USER.md）
        agent_slug: 当前 Agent slug
        project_slug: 当前 Project slug

    Returns:
        resolved 绝对路径

    Raises:
        ValueError: file_id 不在已知列表中
    """
    if file_id in SHARED_BEHAVIOR_FILE_IDS:
        return behavior_shared_dir(project_root) / file_id
    if file_id in PROJECT_SHARED_BEHAVIOR_FILE_IDS:
        return behavior_project_dir(project_root, project_slug) / file_id
    if file_id in AGENT_PRIVATE_BEHAVIOR_FILE_IDS:
        return behavior_agent_dir(project_root, agent_slug) / file_id
    known_file_ids = (
        SHARED_BEHAVIOR_FILE_IDS
        + PROJECT_SHARED_BEHAVIOR_FILE_IDS
        + AGENT_PRIVATE_BEHAVIOR_FILE_IDS
    )
    raise ValueError(
        f"未知的 file_id: {file_id!r}，"
        f"已知列表: {known_file_ids}"
    )


def validate_behavior_file_path(project_root: Path, file_path: str) -> Path:
    """校验行为文件路径安全性，返回 resolved 绝对路径。

    规则：
    1. file_path 必须是相对路径（不以 / 开头）
    2. 不允许 .. 路径组件（防止 path traversal）
    3. resolve 后必须在 project_root 内
    4. 必须在 behavior 目录体系内（behavior/ 或 projects/*/behavior/）

    Raises:
        ValueError: 路径不合法或超出安全边界时抛出
    """
    stripped = file_path.strip()
    if not stripped:
        raise ValueError("file_path 不能为空")

    # 拒绝绝对路径
    if stripped.startswith("/") or stripped.startswith("\\"):
        raise ValueError(f"不允许绝对路径: {stripped}")

    # 拒绝 .. 组件
    parts = Path(stripped).parts
    if ".." in parts:
        raise ValueError(f"不允许 path traversal (..): {stripped}")

    resolved = (project_root.resolve() / stripped).resolve()
    root_resolved = project_root.resolve()

    # 确保在 project_root 内
    if not str(resolved).startswith(str(root_resolved) + "/") and resolved != root_resolved:
        raise ValueError(f"路径超出项目根目录: {stripped}")

    # 确保在 behavior 目录体系内
    relative = str(resolved.relative_to(root_resolved))
    in_behavior = relative.startswith("behavior/") or relative.startswith("behavior\\")
    in_project_behavior = bool(
        re.match(r"projects/[^/]+/behavior(/|\\)", relative)
    )
    if not (in_behavior or in_project_behavior):
        raise ValueError(f"路径不在 behavior 目录体系内: {stripped}")

    return resolved


def read_behavior_file_content(
    project_root: Path,
    file_path: str,
    *,
    agent_slug: str = "main",
    project_slug: str = "default",
) -> tuple[str, bool, int]:
    """读取行为文件内容，不存在时 fallback 到默认模板。

    Returns:
        (content, exists_on_disk, budget_chars)
    """
    resolved = validate_behavior_file_path(project_root, file_path)
    # 从路径末段提取 file_id
    file_id = Path(file_path).name
    budget_chars = _budget_for_file(file_id)

    if resolved.exists():
        content = resolved.read_text(encoding="utf-8").strip()
        return content, True, budget_chars

    # fallback 到默认模板
    try:
        content = _default_content_for_file(
            file_id=file_id,
            is_worker_profile=False,
            agent_name="Main Agent",
            project_label="当前项目",
        ).strip()
    except ValueError:
        # 非标准 file_id，返回空内容
        content = ""
    return content, False, budget_chars


class BehaviorBudgetResult(TypedDict):
    """check_behavior_file_budget 的返回类型。"""

    within_budget: bool
    current_chars: int
    budget_chars: int
    exceeded_by: int


def check_behavior_file_budget(file_path: str, content: str) -> BehaviorBudgetResult:
    """检查内容是否超出字符预算。

    从 file_path 末段提取 file_id，在 BEHAVIOR_FILE_BUDGETS 中查找预算上限。
    未知 file_id 默认不限制（within_budget=True）。
    """
    file_id = Path(file_path).name
    budget = BEHAVIOR_FILE_BUDGETS.get(file_id)
    current_chars = len(content)

    if budget is None:
        # 未知 file_id，不限制
        return {
            "within_budget": True,
            "current_chars": current_chars,
            "budget_chars": 0,
            "exceeded_by": 0,
        }

    exceeded_by = max(0, current_chars - budget)
    return {
        "within_budget": current_chars <= budget,
        "current_chars": current_chars,
        "budget_chars": budget,
        "exceeded_by": exceeded_by,
    }
