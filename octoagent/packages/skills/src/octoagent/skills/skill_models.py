"""SKILL.md 文件系统驱动的 Skill 数据模型。

定义 SkillSource 枚举、SkillMdEntry 解析结果模型和 SkillListItem 摘要投影模型。
这些模型服务于 SkillDiscovery 文件系统扫描和 skills tool 的 LLM 交互。
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class SkillSource(StrEnum):
    """Skill 来源分类。

    三级优先级：PROJECT > USER > BUILTIN
    """

    BUILTIN = "builtin"    # 代码仓库 skills/ 目录
    USER = "user"          # ~/.octoagent/skills/ 目录
    PROJECT = "project"    # {project_root}/skills/ 目录


# Skill 名称合法性正则：小写字母、数字、连字符，不能以连字符开头/结尾，不能包含连续连字符
_SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


class SkillMdEntry(BaseModel):
    """SKILL.md 文件解析结果。

    承载 YAML frontmatter 元数据和 Markdown body 内容。
    content 字段在 list 场景中为空，仅在 load 场景中填充完整 body。
    """

    name: str = Field(min_length=1, max_length=64, description="Skill 唯一标识符")
    description: str = Field(min_length=1, max_length=1024, description="Skill 简短描述")
    version: str = Field(default="", description="版本号")
    author: str = Field(default="", description="作者")
    tags: list[str] = Field(default_factory=list, description="标签列表")
    trigger_patterns: list[str] = Field(default_factory=list, description="触发模式列表")
    tools_required: list[str] = Field(default_factory=list, description="依赖的工具列表")
    source: SkillSource = Field(default=SkillSource.BUILTIN, description="来源分类")
    source_path: str = Field(default="", description="SKILL.md 文件的绝对路径")
    content: str = Field(default="", description="Markdown body（仅 load 时填充）")
    raw_frontmatter: dict[str, Any] = Field(
        default_factory=dict, description="原始 YAML frontmatter"
    )
    metadata: dict[str, Any] = Field(default_factory=dict, description="扩展元数据")

    @field_validator("name")
    @classmethod
    def validate_name_format(cls, v: str) -> str:
        """验证 name 字段格式：小写字母/数字/连字符，不以连字符开头结尾，无连续连字符。"""
        if not _SKILL_NAME_PATTERN.match(v):
            msg = (
                f"Skill name '{v}' 格式无效。"
                "必须满足 ^[a-z0-9]+(-[a-z0-9]+)*$（小写字母、数字、连字符）"
            )
            raise ValueError(msg)
        return v

    def to_list_item(self) -> SkillListItem:
        """投影为摘要模型（不含 content）。"""
        return SkillListItem(
            name=self.name,
            description=self.description,
            tags=self.tags,
            source=self.source,
            version=self.version,
        )


class SkillListItem(BaseModel):
    """Skill 摘要投影（list 接口返回给 LLM）。

    轻量级模型，不包含 content，用于减少 LLM 上下文占用。
    """

    name: str = Field(description="Skill 唯一标识符")
    description: str = Field(description="Skill 简短描述")
    tags: list[str] = Field(default_factory=list, description="标签列表")
    source: SkillSource = Field(description="来源分类")
    version: str = Field(default="", description="版本号")
