"""SkillRegistry 实现。"""

from __future__ import annotations

from dataclasses import dataclass

from .exceptions import SkillNotFoundError, SkillRegistrationError
from .manifest import SkillManifest


@dataclass(slots=True)
class RegisteredSkill:
    """注册后的 Skill 元信息。"""

    manifest: SkillManifest
    prompt_template: str


class SkillRegistry:
    """Skill 注册/发现容器。"""

    def __init__(self) -> None:
        self._skills: dict[str, RegisteredSkill] = {}

    def register(self, manifest: SkillManifest, prompt_template: str) -> None:
        """注册 Skill。"""
        if manifest.skill_id in self._skills:
            raise SkillRegistrationError(f"Skill '{manifest.skill_id}' 已注册")
        if not prompt_template.strip():
            raise SkillRegistrationError("prompt_template 不能为空")
        self._skills[manifest.skill_id] = RegisteredSkill(
            manifest=manifest,
            prompt_template=prompt_template,
        )

    def get(self, skill_id: str) -> RegisteredSkill:
        """获取指定 Skill。"""
        item = self._skills.get(skill_id)
        if item is None:
            raise SkillNotFoundError(f"Skill '{skill_id}' 未找到")
        return item

    def list_skills(self) -> list[SkillManifest]:
        """列出所有 manifest。"""
        return [item.manifest for item in self._skills.values()]

    def unregister(self, skill_id: str) -> bool:
        """注销 Skill。"""
        return self._skills.pop(skill_id, None) is not None
