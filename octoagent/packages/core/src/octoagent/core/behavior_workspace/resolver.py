from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..models.agent_context import AgentProfile
from ..models.behavior import (
    BehaviorPackFile,
    BehaviorWorkspace,
    BehaviorWorkspaceFile,
    BehaviorWorkspaceScope,
    ProjectPathManifest,
    ProjectPathManifestFile,
    StorageBoundaryHints,
)
from ._types import (
    _PROFILE_ALLOWLIST,
    AGENT_PRIVATE_BEHAVIOR_FILE_IDS,
    AGENT_PRIVATE_BOOTSTRAP_TEMPLATE_IDS,
    ALL_BEHAVIOR_FILE_IDS,
    BEHAVIOR_FILE_BUDGETS,
    BEHAVIOR_OVERLAY_ORDER,
    BehaviorLoadProfile,
    PROJECT_AGENT_BOOTSTRAP_TEMPLATE_IDS,
    PROJECT_AGENT_OVERLAY_FILE_IDS,
    PROJECT_SHARED_BEHAVIOR_FILE_IDS,
    PROJECT_SHARED_BOOTSTRAP_TEMPLATE_IDS,
    SHARED_BEHAVIOR_FILE_IDS,
    SHARED_BOOTSTRAP_TEMPLATE_IDS,
)
from .budget import _apply_behavior_budget, _budget_for_file
from .onboarding_state import load_onboarding_state
from .paths import (
    _default_behavior_file_path,
    _default_path_for_file,
    _normalize_project_slug,
    _relative_path_hint,
    behavior_agent_dir,
    behavior_legacy_project_dir,
    behavior_project_agent_dir,
    behavior_project_dir,
    behavior_shared_dir,
    project_artifacts_dir,
    project_data_dir,
    project_notes_dir,
    project_root_dir,
    project_secret_bindings_path,
    project_workspace_dir,
    resolve_behavior_agent_slug,
)
from .template import _build_file_templates, _default_content_for_file
from .validate import _local_override_file_id


@dataclass(frozen=True, slots=True)
class _ResolvedBehaviorSource:
    path: Path
    scope: BehaviorWorkspaceScope
    source_kind: str


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


def _read_behavior_file(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


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


def _is_worker_behavior_profile(agent_profile: AgentProfile) -> bool:
    """判断 AgentProfile 是否为 worker（kind=worker 行）。

    Feature 090 D2: 优先读 ``agent_profile.kind == "worker"``（显式标记）；
    metadata 探测保留为 schema-lag 实例的 fallback——尚未填充 kind 字段的旧
    AgentProfile（agent_profiles 无 kind 列）仍可正确识别为 worker。
    F117 W4-7 真迁移补 kind 列后可移除 fallback 路径。
    """
    if agent_profile.kind == "worker":
        return True
    metadata = agent_profile.metadata
    return (
        str(metadata.get("source_kind", "")).strip() == "worker_profile_mirror"
        or bool(str(metadata.get("source_worker_profile_id", "")).strip())
    )
