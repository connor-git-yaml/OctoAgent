"""Feature 082 P3：USER.md 动态渲染服务。

历史问题：``behavior/system/USER.md`` 是静态占位符模板（"待引导时填写..."），
从未被任何代码填充——即使 Bootstrap 跑通后 OwnerProfile 已有真实数据，
USER.md 仍是占位符 → system prompt 注入污染 LLM。

P3 修复：``UserMdRenderer`` 服务基于 ``OwnerProfile``（首选）+ 画像数据（次选）
渲染出真实 USER.md 内容；写入唯一规范位置。

调用时机：
- ``BootstrapSessionOrchestrator.complete_bootstrap()`` 完成时（P3 集成）
- ``octo bootstrap rebuild-user-md`` CLI（P4 集成）
- 后续：``OwnerProfileStore.update()`` 写入时（事件订阅；后续 Feature）

设计原则：
- 用纯 Python 字符串拼接（避免引入 jinja2 依赖；模板结构简单）
- 渲染失败时 fallback 到静态模板（保证 system prompt 不空）
- 不强制覆盖用户手写内容——只在 OwnerProfile 实质填充时触发
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from octoagent.core.models.agent_context import OwnerProfile

log = structlog.get_logger()


@dataclass(slots=True)
class RenderResult:
    """渲染产物。"""

    content: str
    """渲染后的 USER.md 内容（含 markdown 结构）。"""

    is_filled: bool
    """是否填入了任何实质数据；False 时调用方应继续 fallback 到模板。"""

    fields_used: list[str]
    """实际使用的 OwnerProfile 字段列表（用于诊断）。"""


def _fmt_or_empty(value: str, fallback: str = "（未设置）") -> str:
    """非空字符串原样返回；空串返回 fallback。"""
    return value.strip() if value and value.strip() else fallback


def _fmt_list_or_empty(items: list[str], fallback: str = "（未设置）") -> str:
    """非空列表渲染为 markdown bullet；空列表返回 fallback。"""
    cleaned = [s.strip() for s in (items or []) if s and s.strip()]
    if not cleaned:
        return fallback
    return "\n".join(f"  - {item}" for item in cleaned)


class UserMdRenderer:
    """从 OwnerProfile 渲染 USER.md。"""

    def __init__(self, project_root: Path) -> None:
        self._root = project_root.resolve()

    def render(
        self,
        owner_profile: OwnerProfile | None,
        profile_data: dict[str, Any] | None = None,
    ) -> RenderResult:
        """基于 OwnerProfile（+ 可选画像 dict）渲染 USER.md 内容。

        Args:
            owner_profile: 当前 OwnerProfile；None 时返回模板（is_filled=False）
            profile_data: 可选 ProfileGeneratorService 输出（如 ``{"职业": "..."}``）；
                目前仅记录到诊断 metadata，不直接进 markdown

        Returns:
            ``RenderResult``——含 ``content`` / ``is_filled`` / ``fields_used``
        """
        if owner_profile is None:
            return RenderResult(content=_default_template(), is_filled=False, fields_used=[])

        fields_used: list[str] = []

        # preferred_address：空 / "你" 都视为未设置
        preferred_address = owner_profile.preferred_address
        if preferred_address and preferred_address != "你":
            fields_used.append("preferred_address")

        timezone = owner_profile.timezone
        if timezone and timezone != "UTC":
            fields_used.append("timezone")

        locale = owner_profile.locale
        if locale and locale != "zh-CN":
            fields_used.append("locale")

        working_style = owner_profile.working_style
        if working_style:
            fields_used.append("working_style")

        interaction_preferences = list(owner_profile.interaction_preferences or [])
        if interaction_preferences:
            fields_used.append("interaction_preferences")

        boundary_notes = list(owner_profile.boundary_notes or [])
        if boundary_notes:
            fields_used.append("boundary_notes")

        # 没有任何字段被实质填充 → 返回模板
        if not fields_used:
            return RenderResult(content=_default_template(), is_filled=False, fields_used=[])

        # 渲染主体
        now = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        owner_id_hint = owner_profile.owner_profile_id[:12]

        content = (
            "## 用户画像\n"
            "\n"
            "此文件由 ``octo bootstrap`` / Bootstrap 完成路径自动渲染，"
            "基于 OwnerProfile 实质字段。手工编辑会被下次 rebuild 覆盖；"
            "稳定事实请通过 Memory 服务持久化。\n"
            "\n"
            "### 基本信息\n"
            "\n"
            f"- **称呼**: {_fmt_or_empty(preferred_address)}\n"
            f"- **时区**: {_fmt_or_empty(timezone)}\n"
            f"- **主要语言**: {_fmt_or_empty(locale)}\n"
            "\n"
            "### 沟通偏好\n"
            "\n"
            f"- **沟通偏好**:\n{_fmt_list_or_empty(interaction_preferences)}\n"
            "\n"
            "### 工作风格\n"
            "\n"
            f"{_fmt_or_empty(working_style)}\n"
            "\n"
            "### 边界与禁忌\n"
            "\n"
            f"- **边界注释**:\n{_fmt_list_or_empty(boundary_notes)}\n"
            "\n"
            "---\n"
            "\n"
            f"*更新时间*: {now}\n"
            f"*同步来源*: OwnerProfile {owner_id_hint}\n"
        )

        return RenderResult(content=content, is_filled=True, fields_used=fields_used)

    def write(self, content: str, *, target: Path | None = None) -> Path:
        """原子写入 USER.md（默认 ``<project_root>/behavior/system/USER.md``）。"""
        target_path = target or (self._root / "behavior" / "system" / "USER.md")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        # 原子写入：临时文件 + os.replace
        fd, tmp_path = tempfile.mkstemp(
            dir=str(target_path.parent), suffix=".tmp", prefix=".user-md-",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_path, str(target_path))
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        log.info(
            "user_md_rendered",
            project_root=str(self._root),
            target=str(target_path),
            size=len(content),
        )
        return target_path

    def render_and_write(
        self,
        owner_profile: OwnerProfile | None,
        profile_data: dict[str, Any] | None = None,
        *,
        target: Path | None = None,
    ) -> tuple[RenderResult, Path | None]:
        """渲染 + 写入；OwnerProfile 实质未填充时不写（避免覆盖用户手工 USER.md）。

        Returns:
            (RenderResult, written_path or None)
        """
        result = self.render(owner_profile, profile_data=profile_data)
        if not result.is_filled:
            log.debug(
                "user_md_render_skipped_not_filled",
                project_root=str(self._root),
            )
            return result, None
        written_path = self.write(result.content, target=target)
        return result, written_path


def _default_template() -> str:
    """USER.md 静态占位模板 fallback——与 ``packages/core/.../behavior_templates/USER.md`` 一致。"""
    return (
        "## 用户画像\n"
        "\n"
        "此文件维护高频引用的用户偏好摘要，作为每次对话的快速参考。\n"
        "\n"
        "**重要存储边界**: 稳定事实应通过 Memory 服务写入持久化存储，此文件仅保留"
        "需要每次对话快速参考的核心偏好摘要。不要把大量用户事实堆积在此文件中。\n"
        "\n"
        "### 基本信息\n"
        "\n"
        "- **称呼**: （待引导时填写——用户希望被称呼的名字或昵称）\n"
        "- **时区/地点**: （待引导时填写——影响时间相关回复的准确性）\n"
        "- **主要语言**: 中文\n"
        "- **职业/领域**: （待了解后补充——帮助调整专业术语的使用深度）\n"
        "\n"
        "### 沟通偏好\n"
        "\n"
        "- **回复风格**: （简洁直接 / 详细解释 / 轻松随意 / 其他——待引导或对话中了解）\n"
        "- **信息组织**: 优先回答——现在发生了什么、对用户有什么影响、下一步做什么。"
        "避免冗长的背景铺垫\n"
        "- **确认偏好**: （用户倾向于你直接执行还是先确认再动手——待了解）\n"
        "\n"
        "### 工作习惯\n"
        "\n"
        "- **活跃时段**: （待了解后补充——帮助安排异步任务的通知时机）\n"
        "- **常用工具/平台**: （待了解后补充——帮助选择合适的集成方式）\n"
        "- **任务偏好**: （偏好一步到位还是渐进迭代——待了解后补充）\n"
        "\n"
        "---\n"
        "\n"
        "*更新原则*: 当对话中获得新的用户偏好信息时，先判断信息稳定性——稳定事实"
        "（如姓名、时区）应优先写入 Memory 服务持久化；高频参考的简要偏好（如回复风格）"
        "可同步更新本文件。用户偏好应来自真实交互中的了解，而不是临时猜测。\n"
    )
