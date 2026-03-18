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


class ApprovalBridgeProtocol(Protocol):
    """Feature 061: ask 信号桥接协议

    当 ToolBroker 返回 "ask:" 前缀错误时，
    调用此协议桥接到 ApprovalManager 审批流。
    """

    async def handle_ask(
        self,
        *,
        tool_name: str,
        ask_reason: str,
        agent_runtime_id: str,
        task_id: str,
    ) -> str:
        """处理 ask 信号，返回审批决策

        Returns:
            "approve": 本次允许（重新执行工具）
            "always": 永久允许（写入 override + 重新执行）
            "deny": 拒绝
            "timeout": 超时（等价于 deny）
        """
        ...


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
