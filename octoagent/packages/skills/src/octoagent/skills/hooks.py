"""SkillRunner 生命周期 hook。"""

from __future__ import annotations

from typing import Any, Protocol

from .manifest import SkillManifest
from .models import (
    SkillExecutionContext,
    SkillOutputEnvelope,
    SkillRunResult,
    ToolFeedbackMessage,
)


class SkillRunnerHook(Protocol):
    """SkillRunner 生命周期扩展点。"""

    async def skill_start(
        self, manifest: SkillManifest, context: SkillExecutionContext
    ) -> None: ...

    async def skill_end(
        self,
        manifest: SkillManifest,
        context: SkillExecutionContext,
        result: SkillRunResult,
    ) -> None: ...

    async def before_llm_call(self, manifest: SkillManifest, attempt: int, step: int) -> None: ...

    async def after_llm_call(
        self, manifest: SkillManifest, output: SkillOutputEnvelope
    ) -> None: ...

    async def before_tool_execute(self, tool_name: str, arguments: dict[str, Any]) -> None: ...

    async def after_tool_execute(self, feedback: ToolFeedbackMessage) -> None: ...


class NoopSkillRunnerHook:
    """默认空实现。"""

    async def skill_start(self, manifest: SkillManifest, context: SkillExecutionContext) -> None:
        return None

    async def skill_end(
        self,
        manifest: SkillManifest,
        context: SkillExecutionContext,
        result: SkillRunResult,
    ) -> None:
        return None

    async def before_llm_call(self, manifest: SkillManifest, attempt: int, step: int) -> None:
        return None

    async def after_llm_call(self, manifest: SkillManifest, output: SkillOutputEnvelope) -> None:
        return None

    async def before_tool_execute(self, tool_name: str, arguments: dict[str, Any]) -> None:
        return None

    async def after_tool_execute(self, feedback: ToolFeedbackMessage) -> None:
        return None
