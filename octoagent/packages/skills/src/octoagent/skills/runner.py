"""SkillRunner 实现。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime
from typing import Any

import structlog
from octoagent.core.models.enums import ActorType, EventType
from octoagent.core.models.event import Event
from octoagent.tooling.models import ExecutionContext
from octoagent.tooling.protocols import EventStoreProtocol, ToolBrokerProtocol
from pydantic import BaseModel, ValidationError
from ulid import ULID

from .exceptions import (
    SkillInputError,
    SkillLoopDetectedError,
    SkillRepeatError,
    SkillToolExecutionError,
    SkillValidationError,
)
from .hooks import NoopSkillRunnerHook, SkillRunnerHook
from .manifest import SkillManifest
from .models import (
    ErrorCategory,
    SkillExecutionContext,
    SkillOutputEnvelope,
    SkillRunResult,
    SkillRunStatus,
    ToolCallSpec,
    ToolFeedbackMessage,
)
from .protocols import StructuredModelClientProtocol

logger = structlog.get_logger(__name__)


class SkillRunner:
    """Skill 执行器。"""

    def __init__(
        self,
        *,
        model_client: StructuredModelClientProtocol,
        tool_broker: ToolBrokerProtocol,
        event_store: EventStoreProtocol | None = None,
        hooks: list[SkillRunnerHook] | None = None,
    ) -> None:
        self._model_client = model_client
        self._tool_broker = tool_broker
        self._event_store = event_store
        self._hooks = hooks or [NoopSkillRunnerHook()]

    async def run(
        self,
        *,
        manifest: SkillManifest,
        execution_context: SkillExecutionContext,
        skill_input: BaseModel | dict[str, object],
        prompt: str,
    ) -> SkillRunResult:
        """执行 Skill。"""
        start_time = time.monotonic()
        attempts = 0
        steps = 0
        retry_failures = 0
        feedback: list[ToolFeedbackMessage] = []
        last_signature: str | None = None
        repeat_count = 0

        try:
            self._coerce_input(manifest, skill_input)
        except SkillInputError as exc:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            result = SkillRunResult(
                status=SkillRunStatus.FAILED,
                attempts=attempts,
                steps=steps,
                duration_ms=duration_ms,
                error_category=ErrorCategory.INPUT_VALIDATION_ERROR,
                error_message=str(exc),
            )
            await self._emit_skill_failed(manifest, execution_context, result)
            return result

        await self._emit_skill_started(manifest, execution_context)
        await self._call_hook("skill_start", manifest, execution_context)

        while steps < manifest.loop_guard.max_steps:
            steps += 1
            attempts += 1

            await self._call_hook("before_llm_call", manifest, attempts, steps)
            await self._emit_model_started(manifest, execution_context, attempts, steps)

            try:
                raw_output = await self._model_client.generate(
                    manifest=manifest,
                    execution_context=execution_context,
                    prompt=prompt,
                    feedback=feedback,
                    attempt=attempts,
                    step=steps,
                )
                await self._emit_model_completed(
                    manifest, execution_context, raw_output, attempts, steps
                )
            except Exception as exc:
                await self._emit_model_failed(
                    manifest, execution_context, str(exc), attempts, steps
                )
                retry_failures += 1
                if retry_failures > manifest.retry_policy.max_attempts:
                    result = await self._fail_result(
                        manifest=manifest,
                        execution_context=execution_context,
                        start_time=start_time,
                        attempts=attempts,
                        steps=steps,
                        category=ErrorCategory.REPEAT_ERROR,
                        error=SkillRepeatError(f"模型调用连续失败: {exc}"),
                    )
                    await self._call_hook("skill_end", manifest, execution_context, result)
                    return result
                await self._backoff(manifest)
                continue

            try:
                validated_output = manifest.output_model.model_validate(raw_output.model_dump())
                output = SkillOutputEnvelope.model_validate(validated_output.model_dump())
            except ValidationError as exc:
                retry_failures += 1
                feedback.append(
                    ToolFeedbackMessage(
                        tool_name="_validation",
                        is_error=True,
                        output="",
                        error=f"输出校验失败: {exc.errors()}",
                        duration_ms=0,
                    )
                )
                if retry_failures > manifest.retry_policy.max_attempts:
                    result = await self._fail_result(
                        manifest=manifest,
                        execution_context=execution_context,
                        start_time=start_time,
                        attempts=attempts,
                        steps=steps,
                        category=ErrorCategory.VALIDATION_ERROR,
                        error=SkillValidationError("输出模型校验连续失败"),
                    )
                    await self._call_hook("skill_end", manifest, execution_context, result)
                    return result
                await self._backoff(manifest)
                continue

            await self._call_hook("after_llm_call", manifest, output)

            if output.tool_calls:
                signature = self._tool_signature(output.tool_calls)
                if signature == last_signature:
                    repeat_count += 1
                else:
                    last_signature = signature
                    repeat_count = 1

                if repeat_count >= manifest.loop_guard.repeat_signature_threshold:
                    result = await self._fail_result(
                        manifest=manifest,
                        execution_context=execution_context,
                        start_time=start_time,
                        attempts=attempts,
                        steps=steps,
                        category=ErrorCategory.LOOP_DETECTED,
                        error=SkillLoopDetectedError("检测到重复 tool_calls 签名循环"),
                    )
                    await self._call_hook("skill_end", manifest, execution_context, result)
                    return result
            else:
                last_signature = None
                repeat_count = 0

            tool_feedbacks: list[ToolFeedbackMessage] = []
            if output.tool_calls:
                try:
                    tool_feedbacks = await self._execute_tool_calls(
                        manifest=manifest,
                        execution_context=execution_context,
                        tool_calls=output.tool_calls,
                        skip_remaining_tools=output.skip_remaining_tools,
                    )
                except SkillToolExecutionError as exc:
                    retry_failures += 1
                    feedback.append(
                        ToolFeedbackMessage(
                            tool_name="_tool",
                            is_error=True,
                            output="",
                            error=str(exc),
                            duration_ms=0,
                        )
                    )
                    if retry_failures > manifest.retry_policy.max_attempts:
                        result = await self._fail_result(
                            manifest=manifest,
                            execution_context=execution_context,
                            start_time=start_time,
                            attempts=attempts,
                            steps=steps,
                            category=ErrorCategory.TOOL_EXECUTION_ERROR,
                            error=exc,
                        )
                        await self._call_hook("skill_end", manifest, execution_context, result)
                        return result
                    await self._backoff(manifest)
                    continue

                feedback.extend(tool_feedbacks)

                if any(item.is_error for item in tool_feedbacks):
                    retry_failures += 1
                    if retry_failures > manifest.retry_policy.max_attempts:
                        result = await self._fail_result(
                            manifest=manifest,
                            execution_context=execution_context,
                            start_time=start_time,
                            attempts=attempts,
                            steps=steps,
                            category=ErrorCategory.TOOL_EXECUTION_ERROR,
                            error=SkillToolExecutionError("工具执行连续失败"),
                        )
                        await self._call_hook("skill_end", manifest, execution_context, result)
                        return result
                    await self._backoff(manifest)
                    continue

                # 当前 step 成功完成，清零连续失败计数。
                retry_failures = 0

            if output.complete or output.skip_remaining_tools:
                retry_failures = 0
                duration_ms = int((time.monotonic() - start_time) * 1000)
                result = SkillRunResult(
                    status=SkillRunStatus.SUCCEEDED,
                    output=output,
                    attempts=attempts,
                    steps=steps,
                    duration_ms=duration_ms,
                )
                await self._emit_skill_completed(manifest, execution_context, result)
                await self._call_hook("skill_end", manifest, execution_context, result)
                return result

            if not output.tool_calls:
                retry_failures += 1
                feedback.append(
                    ToolFeedbackMessage(
                        tool_name="_runner",
                        is_error=True,
                        output="",
                        error="输出既未完成也未请求工具调用",
                        duration_ms=0,
                    )
                )
                if retry_failures > manifest.retry_policy.max_attempts:
                    result = await self._fail_result(
                        manifest=manifest,
                        execution_context=execution_context,
                        start_time=start_time,
                        attempts=attempts,
                        steps=steps,
                        category=ErrorCategory.REPEAT_ERROR,
                        error=SkillRepeatError("输出无进展，超过重试上限"),
                    )
                    await self._call_hook("skill_end", manifest, execution_context, result)
                    return result
                await self._backoff(manifest)

        result = await self._fail_result(
            manifest=manifest,
            execution_context=execution_context,
            start_time=start_time,
            attempts=attempts,
            steps=steps,
            category=ErrorCategory.STEP_LIMIT_EXCEEDED,
            error=SkillLoopDetectedError("超过 max_steps 限制"),
        )
        await self._call_hook("skill_end", manifest, execution_context, result)
        return result

    async def _execute_tool_calls(
        self,
        *,
        manifest: SkillManifest,
        execution_context: SkillExecutionContext,
        tool_calls: list[ToolCallSpec],
        skip_remaining_tools: bool,
    ) -> list[ToolFeedbackMessage]:
        results: list[ToolFeedbackMessage] = []

        for call in tool_calls:
            if manifest.tools_allowed and call.tool_name not in manifest.tools_allowed:
                raise SkillToolExecutionError(
                    f"工具 '{call.tool_name}' 不在 tools_allowed 白名单中"
                )

            await self._call_hook("before_tool_execute", call.tool_name, call.arguments)

            tool_context = ExecutionContext(
                task_id=execution_context.task_id,
                trace_id=execution_context.trace_id,
                caller=execution_context.caller,
                profile=manifest.tool_profile,
            )
            tool_result = await self._tool_broker.execute(
                call.tool_name, call.arguments, tool_context
            )

            feedback = self._build_tool_feedback(
                call.tool_name, tool_result, manifest.context_budget
            )
            results.append(feedback)
            await self._call_hook("after_tool_execute", feedback)

            if skip_remaining_tools:
                break

        return results

    @staticmethod
    def _build_tool_feedback(
        tool_name: str,
        tool_result: Any,
        budget: Any,
    ) -> ToolFeedbackMessage:
        output = tool_result.output or ""
        if len(output) > budget.max_chars:
            prefix = output[: budget.summary_chars]
            if tool_result.artifact_ref:
                output = f"[artifact:{tool_result.artifact_ref}] {prefix}..."
            else:
                output = f"{prefix}..."

        parts: list[dict[str, Any]] = []
        if tool_result.artifact_ref:
            parts.append({"type": "file", "artifact_ref": tool_result.artifact_ref})

        return ToolFeedbackMessage(
            tool_name=tool_name,
            is_error=tool_result.is_error,
            output=output,
            error=tool_result.error,
            duration_ms=int(tool_result.duration * 1000),
            artifact_ref=tool_result.artifact_ref,
            parts=parts,
        )

    @staticmethod
    def _tool_signature(tool_calls: list[ToolCallSpec]) -> str:
        payload = [item.model_dump() for item in tool_calls]
        normalized = json.dumps(payload, ensure_ascii=True, sort_keys=True)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def _coerce_input(
        manifest: SkillManifest, skill_input: BaseModel | dict[str, object]
    ) -> BaseModel:
        try:
            if isinstance(skill_input, manifest.input_model):
                return skill_input
            if isinstance(skill_input, BaseModel):
                return manifest.input_model.model_validate(skill_input.model_dump())
            return manifest.input_model.model_validate(skill_input)
        except ValidationError as exc:
            raise SkillInputError(f"输入校验失败: {exc.errors()}") from exc

    async def _backoff(self, manifest: SkillManifest) -> None:
        if manifest.retry_policy.backoff_ms > 0:
            await asyncio.sleep(manifest.retry_policy.backoff_ms / 1000)

    async def _call_hook(self, method: str, *args: Any) -> None:
        for hook in self._hooks:
            fn = getattr(hook, method, None)
            if fn is None:
                continue
            try:
                await fn(*args)
            except Exception as exc:
                logger.warning("skill_hook_failed", method=method, error=str(exc))

    async def _fail_result(
        self,
        *,
        manifest: SkillManifest,
        execution_context: SkillExecutionContext,
        start_time: float,
        attempts: int,
        steps: int,
        category: ErrorCategory,
        error: Exception,
    ) -> SkillRunResult:
        duration_ms = int((time.monotonic() - start_time) * 1000)
        result = SkillRunResult(
            status=SkillRunStatus.FAILED,
            attempts=attempts,
            steps=steps,
            duration_ms=duration_ms,
            error_category=category,
            error_message=str(error),
        )
        await self._emit_skill_failed(manifest, execution_context, result)
        return result

    async def _emit_event(
        self,
        *,
        execution_context: SkillExecutionContext,
        event_type: EventType,
        payload: dict[str, Any],
    ) -> None:
        if self._event_store is None:
            return

        event = Event(
            event_id=str(ULID()),
            task_id=execution_context.task_id,
            task_seq=await self._event_store.get_next_task_seq(execution_context.task_id),
            ts=datetime.now(UTC),
            type=event_type,
            actor=ActorType.WORKER,
            payload=payload,
            trace_id=execution_context.trace_id,
        )
        append_committed = getattr(self._event_store, "append_event_committed", None)
        if callable(append_committed):
            await append_committed(event, update_task_pointer=True)
            return
        await self._event_store.append_event(event)

    async def _emit_skill_started(
        self,
        manifest: SkillManifest,
        execution_context: SkillExecutionContext,
    ) -> None:
        await self._emit_event(
            execution_context=execution_context,
            event_type=EventType.SKILL_STARTED,
            payload={
                "skill_id": manifest.skill_id,
                "skill_version": manifest.version,
                "model_alias": manifest.model_alias,
                "max_attempts": manifest.retry_policy.max_attempts,
                "max_steps": manifest.loop_guard.max_steps,
            },
        )

    async def _emit_skill_completed(
        self,
        manifest: SkillManifest,
        execution_context: SkillExecutionContext,
        result: SkillRunResult,
    ) -> None:
        await self._emit_event(
            execution_context=execution_context,
            event_type=EventType.SKILL_COMPLETED,
            payload={
                "skill_id": manifest.skill_id,
                "attempts": result.attempts,
                "steps": result.steps,
                "duration_ms": result.duration_ms,
            },
        )

    async def _emit_skill_failed(
        self,
        manifest: SkillManifest,
        execution_context: SkillExecutionContext,
        result: SkillRunResult,
    ) -> None:
        await self._emit_event(
            execution_context=execution_context,
            event_type=EventType.SKILL_FAILED,
            payload={
                "skill_id": manifest.skill_id,
                "attempts": result.attempts,
                "steps": result.steps,
                "duration_ms": result.duration_ms,
                "error_category": result.error_category,
                "error_message": result.error_message,
            },
        )

    async def _emit_model_started(
        self,
        manifest: SkillManifest,
        execution_context: SkillExecutionContext,
        attempt: int,
        step: int,
    ) -> None:
        await self._emit_event(
            execution_context=execution_context,
            event_type=EventType.MODEL_CALL_STARTED,
            payload={
                "skill_id": manifest.skill_id,
                "model_alias": manifest.model_alias,
                "attempt": attempt,
                "step": step,
            },
        )

    async def _emit_model_completed(
        self,
        manifest: SkillManifest,
        execution_context: SkillExecutionContext,
        output: SkillOutputEnvelope,
        attempt: int,
        step: int,
    ) -> None:
        await self._emit_event(
            execution_context=execution_context,
            event_type=EventType.MODEL_CALL_COMPLETED,
            payload={
                "skill_id": manifest.skill_id,
                "model_alias": manifest.model_alias,
                "attempt": attempt,
                "step": step,
                "response_summary": output.content[:200],
                "token_usage": {},
            },
        )

    async def _emit_model_failed(
        self,
        manifest: SkillManifest,
        execution_context: SkillExecutionContext,
        error_message: str,
        attempt: int,
        step: int,
    ) -> None:
        await self._emit_event(
            execution_context=execution_context,
            event_type=EventType.MODEL_CALL_FAILED,
            payload={
                "skill_id": manifest.skill_id,
                "model_alias": manifest.model_alias,
                "attempt": attempt,
                "step": step,
                "error_type": "model_call_failed",
                "error_message": error_message,
                "recoverable": True,
            },
        )
