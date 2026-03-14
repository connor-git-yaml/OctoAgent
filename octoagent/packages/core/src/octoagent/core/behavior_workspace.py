"""Feature 049: Behavior workspace 文件解析。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .models.agent_context import AgentProfile
from .models.behavior import (
    BehaviorLayerKind,
    BehaviorPackFile,
    BehaviorVisibility,
    BehaviorWorkspace,
    BehaviorWorkspaceFile,
    BehaviorWorkspaceScope,
)

CORE_BEHAVIOR_FILE_IDS = ("AGENTS.md", "USER.md", "PROJECT.md", "TOOLS.md")
ADVANCED_BEHAVIOR_FILE_IDS = ("SOUL.md", "IDENTITY.md", "HEARTBEAT.md")
ALL_BEHAVIOR_FILE_IDS = CORE_BEHAVIOR_FILE_IDS + ADVANCED_BEHAVIOR_FILE_IDS
BEHAVIOR_FILE_BUDGETS = {
    "AGENTS.md": 3200,
    "USER.md": 1800,
    "PROJECT.md": 2400,
    "TOOLS.md": 3200,
    "SOUL.md": 1600,
    "IDENTITY.md": 1600,
    "HEARTBEAT.md": 1600,
}
BEHAVIOR_OVERLAY_ORDER = (
    "default_template",
    "system_file",
    "system_local_file",
    "project_file",
    "project_local_file",
)


@dataclass(frozen=True, slots=True)
class _BehaviorFileTemplate:
    file_id: str
    title: str
    layer: BehaviorLayerKind
    visibility: BehaviorVisibility
    share_with_workers: bool
    is_advanced: bool


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


def behavior_system_dir(project_root: Path) -> Path:
    return project_root.resolve() / "behavior" / "system"


def behavior_project_dir(project_root: Path, project_slug: str) -> Path:
    slug = project_slug.strip()
    if not slug:
        return behavior_system_dir(project_root)
    return project_root.resolve() / "behavior" / "projects" / slug


def _local_override_file_id(file_id: str) -> str:
    base = Path(file_id)
    return f"{base.stem}.local{base.suffix}"


def _budget_for_file(file_id: str) -> int:
    return int(BEHAVIOR_FILE_BUDGETS.get(file_id, 2000))


def _read_behavior_file(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def _apply_behavior_budget(*, file_id: str, content: str) -> _BehaviorBudgetResult:
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
    effective = normalized[:budget_chars].rstrip()
    return _BehaviorBudgetResult(
        content=effective,
        budget_chars=budget_chars,
        original_char_count=original_char_count,
        effective_char_count=len(effective),
        truncated=True,
        truncation_reason="char_budget_exceeded",
    )


def _resolve_behavior_source(
    *,
    project_dir: Path,
    system_dir: Path,
    project_slug: str,
    file_id: str,
) -> _ResolvedBehaviorSource | None:
    candidates: list[_ResolvedBehaviorSource] = []
    if project_slug.strip():
        project_local_path = project_dir / _local_override_file_id(file_id)
        project_path = project_dir / file_id
        if project_local_path.exists():
            candidates.append(
                _ResolvedBehaviorSource(
                    path=project_local_path,
                    scope=BehaviorWorkspaceScope.PROJECT,
                    source_kind="project_local_file",
                )
            )
        if project_path.exists():
            candidates.append(
                _ResolvedBehaviorSource(
                    path=project_path,
                    scope=BehaviorWorkspaceScope.PROJECT,
                    source_kind="project_file",
                )
            )
    system_local_path = system_dir / _local_override_file_id(file_id)
    system_path = system_dir / file_id
    if system_local_path.exists():
        candidates.append(
            _ResolvedBehaviorSource(
                path=system_local_path,
                scope=BehaviorWorkspaceScope.SYSTEM,
                source_kind="system_local_file",
            )
        )
    if system_path.exists():
        candidates.append(
            _ResolvedBehaviorSource(
                path=system_path,
                scope=BehaviorWorkspaceScope.SYSTEM,
                source_kind="system_file",
            )
        )
    return candidates[0] if candidates else None


def build_default_behavior_pack_files(
    *,
    agent_profile: AgentProfile,
    project_name: str = "",
    include_advanced: bool = False,
) -> list[BehaviorPackFile]:
    files = _build_default_behavior_files(
        agent_profile=agent_profile,
        project_name=project_name,
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


def resolve_behavior_workspace(
    *,
    project_root: Path,
    agent_profile: AgentProfile,
    project_name: str = "",
    project_slug: str = "",
) -> BehaviorWorkspace:
    root = project_root.resolve()
    system_dir = behavior_system_dir(root)
    project_dir = behavior_project_dir(root, project_slug)
    defaults = {
        item.file_id: item
        for item in _build_default_behavior_files(
            agent_profile=agent_profile,
            project_name=project_name,
            include_advanced=False,
        )
    }
    advanced_defaults = {
        item.file_id: item
        for item in _build_default_behavior_files(
            agent_profile=agent_profile,
            project_name=project_name,
            include_advanced=True,
        )
        if item.file_id not in defaults
    }

    files: list[BehaviorWorkspaceFile] = []
    used_project = False
    used_project_local = False
    used_system = False
    used_system_local = False
    used_default = False

    for file_id in ALL_BEHAVIOR_FILE_IDS:
        default_file = defaults.get(file_id) or advanced_defaults.get(file_id)
        if default_file is None:
            continue
        selected_source = _resolve_behavior_source(
            project_dir=project_dir,
            system_dir=system_dir,
            project_slug=project_slug,
            file_id=file_id,
        )
        selected_path: Path | None = None
        scope: BehaviorWorkspaceScope | None = None
        source_kind = "default_template"
        content = default_file.content
        should_include = file_id in defaults

        if selected_source is not None:
            selected_path = selected_source.path
            scope = selected_source.scope
            source_kind = selected_source.source_kind
            content = _read_behavior_file(selected_source.path)
            if source_kind == "project_local_file":
                used_project_local = True
            elif source_kind == "project_file":
                used_project = True
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
        if selected_path is not None:
            path_str = str(selected_path.relative_to(root))
        else:
            path_str = default_file.path

        files.append(
            default_file.model_copy(
                update={
                    "scope": scope,
                    "path": path_str,
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
                        "exists_on_disk": bool(selected_path),
                        "overlay_order": list(BEHAVIOR_OVERLAY_ORDER),
                    },
                }
            )
        )

    source_chain: list[str] = []
    if used_project_local and project_slug.strip():
        source_chain.append(f"filesystem:behavior/projects/{project_slug.strip()}/*.local")
    if used_project and project_slug.strip():
        source_chain.append(f"filesystem:behavior/projects/{project_slug.strip()}")
    if used_system_local:
        source_chain.append("filesystem:behavior/system/*.local")
    if used_system:
        source_chain.append("filesystem:behavior/system")
    if used_default:
        source_chain.append("default_behavior_templates")

    return BehaviorWorkspace(
        project_slug=project_slug.strip(),
        system_dir=str(system_dir.relative_to(root)),
        project_dir=str(project_dir.relative_to(root)),
        files=files,
        source_chain=source_chain,
        metadata={
            "has_filesystem_sources": (
                used_project or used_project_local or used_system or used_system_local
            ),
            "has_local_overrides": used_project_local or used_system_local,
            "core_file_ids": list(CORE_BEHAVIOR_FILE_IDS),
            "advanced_file_ids": list(ADVANCED_BEHAVIOR_FILE_IDS),
            "overlay_order": list(BEHAVIOR_OVERLAY_ORDER),
            "file_budgets": dict(BEHAVIOR_FILE_BUDGETS),
        },
    )


def _build_default_behavior_files(
    *,
    agent_profile: AgentProfile,
    project_name: str,
    include_advanced: bool,
) -> list[BehaviorWorkspaceFile]:
    templates = _build_file_templates(include_advanced=include_advanced)
    project_label = project_name.strip() or agent_profile.name.strip() or "当前项目"
    is_worker_profile = _is_worker_behavior_profile(agent_profile)
    files: list[BehaviorWorkspaceFile] = []

    for template in templates:
        content = _default_content_for_file(
            file_id=template.file_id,
            is_worker_profile=is_worker_profile,
            agent_name=agent_profile.name.strip() or "Butler",
            project_label=project_label,
        ).strip()
        files.append(
            BehaviorWorkspaceFile(
                file_id=template.file_id,
                title=template.title,
                layer=template.layer,
                visibility=template.visibility,
                share_with_workers=template.share_with_workers,
                scope=None,
                path=_default_path_for_file(template.file_id),
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
                    "overlay_order": list(BEHAVIOR_OVERLAY_ORDER),
                },
            )
        )
    return files


def _build_file_templates(*, include_advanced: bool) -> list[_BehaviorFileTemplate]:
    templates = [
        _BehaviorFileTemplate(
            file_id="AGENTS.md",
            title="行为总约束",
            layer=BehaviorLayerKind.ROLE,
            visibility=BehaviorVisibility.SHARED,
            share_with_workers=True,
            is_advanced=False,
        ),
        _BehaviorFileTemplate(
            file_id="USER.md",
            title="Owner 偏好",
            layer=BehaviorLayerKind.COMMUNICATION,
            visibility=BehaviorVisibility.PRIVATE,
            share_with_workers=False,
            is_advanced=False,
        ),
        _BehaviorFileTemplate(
            file_id="PROJECT.md",
            title="Project 语境",
            layer=BehaviorLayerKind.SOLVING,
            visibility=BehaviorVisibility.SHARED,
            share_with_workers=True,
            is_advanced=False,
        ),
        _BehaviorFileTemplate(
            file_id="TOOLS.md",
            title="工具与边界",
            layer=BehaviorLayerKind.TOOL_BOUNDARY,
            visibility=BehaviorVisibility.SHARED,
            share_with_workers=True,
            is_advanced=False,
        ),
    ]
    if not include_advanced:
        return templates
    return templates + [
        _BehaviorFileTemplate(
            file_id="SOUL.md",
            title="沟通风格",
            layer=BehaviorLayerKind.COMMUNICATION,
            visibility=BehaviorVisibility.PRIVATE,
            share_with_workers=False,
            is_advanced=True,
        ),
        _BehaviorFileTemplate(
            file_id="IDENTITY.md",
            title="身份补充",
            layer=BehaviorLayerKind.ROLE,
            visibility=BehaviorVisibility.PRIVATE,
            share_with_workers=False,
            is_advanced=True,
        ),
        _BehaviorFileTemplate(
            file_id="HEARTBEAT.md",
            title="运行节奏",
            layer=BehaviorLayerKind.BOOTSTRAP,
            visibility=BehaviorVisibility.PRIVATE,
            share_with_workers=False,
            is_advanced=True,
        ),
    ]


def _default_path_for_file(file_id: str) -> str:
    return f"behavior/system/{file_id}"


def _default_content_for_file(
    *,
    file_id: str,
    is_worker_profile: bool,
    agent_name: str,
    project_label: str,
) -> str:
    if file_id == "AGENTS.md":
        if is_worker_profile:
            return (
                f"你是 OctoAgent 的 Worker「{agent_name}」。"
                "先在职责边界内完成当前工作，不替代 Butler 进行全局总控；"
                "遇到前提不足时，明确缺口和下一步。"
            )
        return (
            "你是 OctoAgent 的 Butler。"
            "默认先综合显式行为文件、runtime hints、会话事实和工具能力，"
            "再决定直接回答、补问一次、委派或 best-effort。"
            "当当前挂载的 web / filesystem / terminal 等受治理工具已经足够解决问题时，"
            "优先自己完成，不要为了形式上的分层强行委派。"
            "如果识别到问题会跨多轮持续推进、涉及复杂外部调研/代码实现/运维操作，"
            "则应主动建立稳定的 specialist worker lane，"
            "让后续同题材问题继续沿用同一条 worker 上下文。"
        )
    if file_id == "USER.md":
        return (
            "优先回答：现在发生了什么、对用户有什么影响、下一步做什么。"
            "除非进入 advanced/诊断场景，否则不要默认展开大量内部实现细节。"
        )
    if file_id == "PROJECT.md":
        return (
            f"当前 project：{project_label}。"
            "先围绕当前项目目标、术语和交付标准组织回答，不要把问题误降级成通用 demo。"
        )
    if file_id == "TOOLS.md":
        return (
            "先区分已知事实、合理推断和待确认信息。"
            "当用户显式要求联网或实时信息时，优先结合已有线索做判断；若缺关键条件，只补最关键的一次。"
            "当问题是有界且可直接解决的，优先使用当前已挂载的受治理 web / filesystem / terminal 工具。"
            "当问题转向长期、复杂、跨多轮协作时，先由 Butler 重写委派目标、上下文摘要、工具边界与返回契约，"
            "再把任务交给合适的 worker，不要把用户原话原封不动转发过去。"
        )
    if file_id == "SOUL.md":
        return (
            "保持长期协作助手的语气：结论优先，信息不足时先承认边界，再给下一步，不装懂。"
        )
    if file_id == "IDENTITY.md":
        return (
            "你代表 OctoAgent 的主行为系统。"
            "允许提出行为文件 patch proposal，但默认不得静默改写行为文件。"
        )
    if file_id == "HEARTBEAT.md":
        return (
            "每轮优先稳住协作节奏：先识别目标和边界，再决定是否需要补问、委派或升级治理。"
            "如果已经进入 specialist worker lane，优先保持同一条 lane 的连续性，并在 Butler 侧明确说明当前是直接处理、等待 worker 结果，还是正在最终收口。"
        )
    raise ValueError(f"未支持的 behavior file: {file_id}")


def _is_worker_behavior_profile(agent_profile: AgentProfile) -> bool:
    metadata = agent_profile.metadata
    return (
        str(metadata.get("source_kind", "")).strip() == "worker_profile_mirror"
        or bool(str(metadata.get("source_worker_profile_id", "")).strip())
    )
