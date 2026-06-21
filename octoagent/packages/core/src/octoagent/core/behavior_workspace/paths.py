from __future__ import annotations

import hashlib
from pathlib import Path

from ..models.agent_context import AgentProfile
from ..models.behavior import BehaviorWorkspaceScope
from ..models.behavior_version import BehaviorFileKey
from ._types import (
    _SLUG_RE,
    AGENT_PRIVATE_BEHAVIOR_FILE_IDS,
    PROJECT_SHARED_BEHAVIOR_FILE_IDS,
    SHARED_BEHAVIOR_FILE_IDS,
)


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


def behavior_version_key_from_path(
    project_root: Path,
    resolved_path: Path,
) -> BehaviorFileKey:
    """从 behavior 文件的**实际 resolved 磁盘路径**派生版本 key（F107 W1，Opus H1 修正）。

    版本 key 的权威来源是文件**真实落盘的 `behavior/agents/<slug>/` 等路径段**，而非 runtime
    上下文里的 raw agent_profile_id（`_extract_agent_slug` 给的值会被 `behavior_agent_dir` 二次
    slugify，与磁盘目录名不一致）。前端读侧也从同一路径结构（`deriveAgentSlug` 取 `behavior/
    agents/<slug>/` 段）派生 → 写 key（本函数）与读 key（`behavior_version_key_for(file_id,
    agent_slug=<同段>)`）逐字一致，AGENT_PRIVATE 版本历史对自定义 Worker 也能命中。

    支持 4 scope（与 resolve_write_path_by_file_id 的落盘结构对称）：
    - ``behavior/system/<file>`` → SYSTEM_SHARED
    - ``behavior/agents/<slug>/<file>`` → AGENT_PRIVATE（agent_slug=<slug>）
    - ``projects/<proj>/behavior/agents/<slug>/<file>`` → PROJECT_AGENT
    - ``projects/<proj>/behavior/<file>`` → PROJECT_SHARED（project_slug=<proj>）
    """
    rel = resolved_path.resolve().relative_to(project_root.resolve())
    parts = rel.parts
    file_id = parts[-1] if parts else ""
    # behavior/agents/<slug>/<file>
    if len(parts) >= 4 and parts[0] == "behavior" and parts[1] == "agents":
        return BehaviorFileKey(
            scope=BehaviorWorkspaceScope.AGENT_PRIVATE.value,
            agent_slug=parts[2],
            file_id=file_id,
        )
    # behavior/system/<file>
    if len(parts) >= 3 and parts[0] == "behavior" and parts[1] == "system":
        return BehaviorFileKey(
            scope=BehaviorWorkspaceScope.SYSTEM_SHARED.value, file_id=file_id
        )
    # projects/<proj>/behavior/agents/<slug>/<file>
    if (
        len(parts) >= 6
        and parts[0] == "projects"
        and parts[2] == "behavior"
        and parts[3] == "agents"
    ):
        return BehaviorFileKey(
            scope=BehaviorWorkspaceScope.PROJECT_AGENT.value,
            project_slug=parts[1],
            agent_slug=parts[4],
            file_id=file_id,
        )
    # projects/<proj>/behavior/<file>
    if len(parts) >= 4 and parts[0] == "projects" and parts[2] == "behavior":
        return BehaviorFileKey(
            scope=BehaviorWorkspaceScope.PROJECT_SHARED.value,
            project_slug=parts[1],
            file_id=file_id,
        )
    # 兜底：按 file_id 路由（与 behavior_version_key_for 一致）
    return behavior_version_key_for(file_id)


def behavior_version_key_for(
    file_id: str,
    *,
    agent_slug: str = "",
    project_slug: str = "",
) -> BehaviorFileKey:
    """派生 behavior 版本 key（F107 W1）。

    与 ``resolve_write_path_by_file_id`` **同 scope 路由**，并按 scope **归零无关字段**——
    保证同一物理文件映射到唯一 key（Codex MED-4：避免按盘上路径反推 scope 的脆弱性 +
    避免同文件因 agent_slug/project_slug 噪声裂成多 key）：
    - SHARED → SYSTEM_SHARED，agent_slug/project_slug 均 ''（全局唯一文件）
    - PROJECT_SHARED → 仅 project_slug 生效，agent_slug ''
    - AGENT_PRIVATE → 仅 agent_slug 生效，project_slug ''
    """
    if file_id in SHARED_BEHAVIOR_FILE_IDS:
        return BehaviorFileKey(
            scope=BehaviorWorkspaceScope.SYSTEM_SHARED.value, file_id=file_id
        )
    if file_id in PROJECT_SHARED_BEHAVIOR_FILE_IDS:
        return BehaviorFileKey(
            scope=BehaviorWorkspaceScope.PROJECT_SHARED.value,
            project_slug=project_slug,
            file_id=file_id,
        )
    if file_id in AGENT_PRIVATE_BEHAVIOR_FILE_IDS:
        return BehaviorFileKey(
            scope=BehaviorWorkspaceScope.AGENT_PRIVATE.value,
            agent_slug=agent_slug,
            file_id=file_id,
        )
    known_file_ids = (
        SHARED_BEHAVIOR_FILE_IDS
        + PROJECT_SHARED_BEHAVIOR_FILE_IDS
        + AGENT_PRIVATE_BEHAVIOR_FILE_IDS
    )
    raise ValueError(f"未知的 file_id: {file_id!r}，已知列表: {known_file_ids}")
