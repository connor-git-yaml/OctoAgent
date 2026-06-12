from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import structlog

from ._types import (
    AGENT_PRIVATE_BEHAVIOR_FILE_IDS,
    ALL_BEHAVIOR_FILE_IDS,
    PROJECT_SHARED_BEHAVIOR_FILE_IDS,
    SHARED_BEHAVIOR_FILE_IDS,
)
from .budget import _budget_for_file
from .onboarding_state import load_onboarding_state, save_onboarding_state
from .paths import (
    _default_behavior_file_path,
    _normalize_project_slug,
    _template_scope_for_file,
    behavior_agent_dir,
    behavior_project_agent_dir,
    behavior_project_dir,
    behavior_shared_dir,
    normalize_behavior_agent_slug,
    project_artifacts_dir,
    project_data_dir,
    project_instructions_dir,
    project_notes_dir,
    project_secret_bindings_path,
    project_workspace_dir,
)
from .resolver import build_project_instruction_readme, build_project_secret_bindings_stub
from .template import _default_content_for_file
from .validate import validate_behavior_file_path

log = structlog.get_logger(__name__)


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
