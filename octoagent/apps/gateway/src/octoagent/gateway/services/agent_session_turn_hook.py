"""SkillRunner -> AgentSession turn store hook。"""

from __future__ import annotations

from typing import Any

from octoagent.skills import (
    SkillExecutionContext,
    SkillManifest,
    SkillOutputEnvelope,
    SkillRunResult,
    ToolFeedbackMessage,
)

from .agent_context import AgentContextService


class AgentSessionTurnHook:
    """把 tool call / tool result 写入 AgentSession 正式 turn store。"""

    def __init__(self, store_group) -> None:
        self._agent_context = AgentContextService(store_group)
        self._current_context: SkillExecutionContext | None = None

    async def skill_start(
        self,
        manifest: SkillManifest,
        context: SkillExecutionContext,
    ) -> None:
        del manifest
        self._current_context = context

    async def skill_end(
        self,
        manifest: SkillManifest,
        context: SkillExecutionContext,
        result: SkillRunResult,
    ) -> None:
        del manifest, context, result
        self._current_context = None

    async def before_llm_call(
        self,
        manifest: SkillManifest,
        attempt: int,
        step: int,
    ) -> None:
        del manifest, attempt, step
        return None

    async def after_llm_call(
        self,
        manifest: SkillManifest,
        output: SkillOutputEnvelope,
    ) -> None:
        del manifest, output
        return None

    async def before_tool_execute(self, tool_name: str, arguments: dict[str, Any]) -> None:
        context = self._current_context
        if context is None or not context.agent_session_id:
            return
        await self._agent_context.record_tool_call_turn(
            agent_session_id=context.agent_session_id,
            task_id=context.task_id,
            tool_name=tool_name,
            arguments=arguments,
        )

    async def after_tool_execute(self, feedback: ToolFeedbackMessage) -> None:
        context = self._current_context
        if context is None or not context.agent_session_id:
            return
        await self._agent_context.record_tool_result_turn(
            agent_session_id=context.agent_session_id,
            task_id=context.task_id,
            tool_name=feedback.tool_name,
            output=feedback.output,
            is_error=feedback.is_error,
            error=feedback.error,
            artifact_ref=feedback.artifact_ref,
            duration_ms=feedback.duration_ms,
        )
