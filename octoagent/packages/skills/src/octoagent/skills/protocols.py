"""Skills 包的协议接口。"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from .manifest import SkillManifest
from .models import (
    SkillExecutionContext,
    SkillOutputEnvelope,
    SkillRunResult,
    ToolFeedbackMessage,
)
from .registry import RegisteredSkill


class StructuredModelClientProtocol(Protocol):
    """结构化模型调用协议。"""

    async def generate(
        self,
        *,
        manifest: SkillManifest,
        execution_context: SkillExecutionContext,
        prompt: str,
        feedback: list[ToolFeedbackMessage],
        attempt: int,
        step: int,
    ) -> SkillOutputEnvelope:
        """生成单步 Skill 输出。"""


class SkillRunnerProtocol(Protocol):
    """Skill 执行协议。"""

    async def run(
        self,
        *,
        manifest: SkillManifest,
        execution_context: SkillExecutionContext,
        skill_input: BaseModel | dict[str, object],
        prompt: str,
    ) -> SkillRunResult:
        """执行 Skill。"""


class SkillRegistryProtocol(Protocol):
    """Registry 协议。"""

    def register(self, manifest: SkillManifest, prompt_template: str) -> None:
        """注册 Skill。"""

    def get(self, skill_id: str) -> RegisteredSkill:
        """读取 Skill。"""

    def list_skills(self) -> list[SkillManifest]:
        """列出 Skill。"""

    def unregister(self, skill_id: str) -> bool:
        """注销 Skill。"""
