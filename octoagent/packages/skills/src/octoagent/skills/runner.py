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
    LoopGuardPolicy,
    SkillExecutionContext,
    SkillOutputEnvelope,
    SkillRunResult,
    SkillRunStatus,
    ToolCallSpec,
    ToolFeedbackMessage,
    UsageLimits,
    UsageTracker,
    resolve_effective_tool_allowlist,
)
from .protocols import StructuredModelClientProtocol

logger = structlog.get_logger(__name__)

# 模块级默认值单例，避免每次 run() 重复构造 Pydantic 实例
_DEFAULT_USAGE_LIMITS = UsageLimits()
_DEFAULT_LOOP_GUARD = LoopGuardPolicy()


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

        # --- Feature 062: 多维度资源限制 ---
        limits = execution_context.usage_limits
        # 向后兼容：调用方未自定义 usage_limits 时，从 manifest.loop_guard 转换。
        # 比较整个 LoopGuardPolicy 对象而非仅检查 max_steps，
        # 避免 UsageLimits 默认值变更后导致条件语义漂移。
        if (
            limits == _DEFAULT_USAGE_LIMITS
            and manifest.loop_guard != _DEFAULT_LOOP_GUARD
        ):
            limits = manifest.loop_guard.to_usage_limits()
        tracker = UsageTracker(start_time=start_time)

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

        while tracker.check_limits(limits) is None:
            steps += 1
            attempts += 1
            tracker.steps = steps  # 同步到 tracker

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
                # --- Feature 062: 累加 token/cost 数据 ---
                if hasattr(raw_output, "token_usage") and raw_output.token_usage:
                    tracker.request_tokens += int(
                        raw_output.token_usage.get("prompt_tokens", 0)
                    )
                    tracker.response_tokens += int(
                        raw_output.token_usage.get("completion_tokens", 0)
                    )
                if hasattr(raw_output, "cost_usd"):
                    tracker.cost_usd += float(raw_output.cost_usd or 0.0)
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

                if repeat_count >= limits.repeat_signature_threshold:
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
                tracker.tool_calls += len(output.tool_calls)

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

            # --- Feature 062 Phase 4: StopHook 检查 ---
            if await self._check_stop_hooks(manifest, execution_context, tracker, output):
                duration_ms = int((time.monotonic() - start_time) * 1000)
                result = SkillRunResult(
                    status=SkillRunStatus.STOPPED,
                    output=output,
                    attempts=attempts,
                    steps=steps,
                    duration_ms=duration_ms,
                    usage=tracker.to_dict(),
                    total_cost_usd=tracker.cost_usd,
                )
                await self._emit_usage_report(manifest, execution_context, tracker)
                await self._call_hook("skill_end", manifest, execution_context, result)
                return result

            if output.complete or output.skip_remaining_tools:
                retry_failures = 0
                duration_ms = int((time.monotonic() - start_time) * 1000)
                result = SkillRunResult(
                    status=SkillRunStatus.SUCCEEDED,
                    output=output,
                    attempts=attempts,
                    steps=steps,
                    duration_ms=duration_ms,
                    usage=tracker.to_dict(),
                    total_cost_usd=tracker.cost_usd,
                )
                await self._emit_usage_report(manifest, execution_context, tracker)
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

        # 获取具体的超限类别
        exceeded = tracker.check_limits(limits)
        category = exceeded or ErrorCategory.STEP_LIMIT_EXCEEDED
        result = await self._fail_result(
            manifest=manifest,
            execution_context=execution_context,
            start_time=start_time,
            attempts=attempts,
            steps=steps,
            category=category,
            error=SkillLoopDetectedError(f"资源限制触发: {category.value}"),
        )
        result.usage = tracker.to_dict()
        result.total_cost_usd = tracker.cost_usd
        await self._emit_usage_report(manifest, execution_context, tracker)
        await self._emit_resource_limit_hit(
            manifest, execution_context, tracker, category, limits
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
        allowed_tool_names = resolve_effective_tool_allowlist(
            permission_mode=manifest.permission_mode,
            tools_allowed=list(manifest.tools_allowed),
            metadata=execution_context.metadata,
        )

        for call in tool_calls:
            if allowed_tool_names and call.tool_name not in allowed_tool_names:
                raise SkillToolExecutionError(
                    f"工具 '{call.tool_name}' 不在当前 skill 可用工具集合中"
                )

            await self._call_hook("before_tool_execute", call.tool_name, call.arguments)

            tool_context = ExecutionContext(
                task_id=execution_context.task_id,
                trace_id=execution_context.trace_id,
                caller=execution_context.caller,
                agent_runtime_id=execution_context.agent_runtime_id,
                agent_session_id=execution_context.agent_session_id,
                work_id=execution_context.work_id,
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

    async def _emit_usage_report(
        self,
        manifest: SkillManifest,
        execution_context: SkillExecutionContext,
        tracker: UsageTracker,
    ) -> None:
        """emit SKILL_USAGE_REPORT 事件，记录 Skill 执行资源消耗。"""
        await self._emit_event(
            execution_context=execution_context,
            event_type=EventType.SKILL_USAGE_REPORT,
            payload={
                "skill_id": manifest.skill_id,
                **tracker.to_dict(),
            },
        )

    async def _emit_resource_limit_hit(
        self,
        manifest: SkillManifest,
        execution_context: SkillExecutionContext,
        tracker: UsageTracker,
        error_category: ErrorCategory,
        limits: UsageLimits,
    ) -> None:
        """emit RESOURCE_LIMIT_HIT 告警事件，记录超限详情。"""
        await self._emit_event(
            execution_context=execution_context,
            event_type=EventType.RESOURCE_LIMIT_HIT,
            payload={
                "skill_id": manifest.skill_id,
                "error_category": error_category.value,
                "current_usage": tracker.to_dict(),
                "limits": {
                    "max_steps": limits.max_steps,
                    "max_request_tokens": limits.max_request_tokens,
                    "max_response_tokens": limits.max_response_tokens,
                    "max_tool_calls": limits.max_tool_calls,
                    "max_budget_usd": limits.max_budget_usd,
                    "max_duration_seconds": limits.max_duration_seconds,
                },
            },
        )

    async def _check_stop_hooks(
        self,
        manifest: SkillManifest,
        context: SkillExecutionContext,
        tracker: UsageTracker,
        output: SkillOutputEnvelope,
    ) -> bool:
        """检查所有 hook 的 should_stop()。任一返回 True 即返回 True。

        Phase 4 (StopHook) 实现时会在主循环中调用此方法。
        """
        for hook in self._hooks:
            fn = getattr(hook, "should_stop", None)
            if fn is None:
                continue
            try:
                if await fn(manifest, context, tracker, output):
                    return True
            except Exception as exc:
                logger.warning("stop_hook_failed", error=str(exc))
        return False

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
