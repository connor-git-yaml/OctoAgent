"""Behavior workspace package（F108a W1：原单文件 behavior_workspace.py 拆分）。

原 octoagent/core/behavior_workspace.py（1741 行，80 个顶层符号）按职责拆为
8 个子模块：_types / onboarding_state / budget / paths / skeleton / template /
validate / resolver。全部顶层符号（公有 + 私有）在此 re-export，对外 import
路径 ``octoagent.core.behavior_workspace`` 保持不变。原单文件完整内容见 git
历史（本次拆分 commit 的父 commit）。
"""

from ._types import (
    _BEHAVIOR_SIZE_WARNING_THRESHOLD,
    _BEHAVIOR_TEMPLATE_PACKAGE,
    _BEHAVIOR_TEMPLATE_VARIANTS,
    _PROFILE_ALLOWLIST,
    _SLUG_RE,
    ADVANCED_BEHAVIOR_FILE_IDS,
    AGENT_PRIVATE_BEHAVIOR_FILE_IDS,
    AGENT_PRIVATE_BOOTSTRAP_TEMPLATE_IDS,
    ALL_BEHAVIOR_FILE_IDS,
    BEHAVIOR_FILE_BUDGETS,
    BEHAVIOR_OVERLAY_ORDER,
    BOOTSTRAP_COMPLETED_MARKER,
    BehaviorLoadProfile,
    CORE_BEHAVIOR_FILE_IDS,
    INSTRUCTION_BOOTSTRAP_FILE_IDS,
    PROJECT_AGENT_BOOTSTRAP_TEMPLATE_IDS,
    PROJECT_AGENT_OVERLAY_FILE_IDS,
    PROJECT_SHARED_BEHAVIOR_FILE_IDS,
    PROJECT_SHARED_BOOTSTRAP_TEMPLATE_IDS,
    SHARED_BEHAVIOR_FILE_IDS,
    SHARED_BOOTSTRAP_TEMPLATE_IDS,
    get_profile_allowlist,
)
from .budget import (
    _BehaviorBudgetResult,
    _apply_behavior_budget,
    _budget_for_file,
    BehaviorBudgetResult,
    check_behavior_file_budget,
    truncate_behavior_content,
)
from .onboarding_state import (
    OnboardingState,
    _onboarding_state_path,
    load_onboarding_state,
    log,
    mark_onboarding_completed,
    save_onboarding_state,
)
from .paths import (
    _default_behavior_file_path,
    _default_path_for_file,
    _normalize_project_slug,
    _relative_path_hint,
    _slugify,
    _template_scope_for_file,
    behavior_agent_dir,
    behavior_legacy_project_dir,
    behavior_project_agent_dir,
    behavior_project_dir,
    behavior_shared_dir,
    behavior_system_dir,
    normalize_behavior_agent_slug,
    project_artifacts_dir,
    project_data_dir,
    project_instructions_dir,
    project_notes_dir,
    project_root_dir,
    project_secret_bindings_path,
    project_workspace_dir,
    behavior_version_key_for,
    behavior_version_key_from_path,
    resolve_behavior_agent_slug,
    resolve_write_path_by_file_id,
)
from .resolver import (
    _ResolvedBehaviorSource,
    _is_worker_behavior_profile,
    _read_behavior_file,
    _resolve_behavior_source,
    build_behavior_bootstrap_template_ids,
    build_default_behavior_pack_files,
    build_default_behavior_workspace_files,
    build_project_instruction_readme,
    build_project_secret_bindings_stub,
    resolve_behavior_workspace,
)
from .skeleton import (
    ensure_filesystem_skeleton,
    materialize_agent_behavior_files,
    materialize_project_behavior_files,
    measure_behavior_total_size,
    read_behavior_file_content,
)
from .template import (
    _BehaviorFileTemplate,
    _build_file_templates,
    _default_content_for_file,
    _load_behavior_template_text,
    _render_behavior_template,
    _template_name_for_file,
    get_behavior_file_review_modes,
)
from .validate import (
    _local_override_file_id,
    validate_behavior_file_path,
)
from .write import (
    PendingBehaviorWrite,
    commit_behavior_file_write,
    prepare_behavior_file_write,
)
