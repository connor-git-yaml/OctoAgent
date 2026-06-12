from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from importlib import resources

from ..models.behavior import (
    BehaviorEditabilityMode,
    BehaviorLayerKind,
    BehaviorReviewMode,
    BehaviorVisibility,
    BehaviorWorkspaceScope,
)
from ._types import (
    _BEHAVIOR_TEMPLATE_PACKAGE,
    _BEHAVIOR_TEMPLATE_VARIANTS,
    AGENT_PRIVATE_BEHAVIOR_FILE_IDS,
    BOOTSTRAP_COMPLETED_MARKER,
    CORE_BEHAVIOR_FILE_IDS,
)


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
