"""SkillRunner 实现。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from octoagent.core.event_helpers import emit_task_event
from octoagent.core.models.enums import ActorType, EventType
from octoagent.tooling.models import ExecutionContext, PermissionPreset, SideEffectLevel
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
    FEEDBACK_SENDER_LOOP_GUARD,
    FEEDBACK_SENDER_RUNNER_ERROR,
    FEEDBACK_SENDER_TOOL_ERROR,
    ErrorCategory,
    FeedbackKind,
    LoopGuardPolicy,
    SkillExecutionContext,
    SkillOutputEnvelope,
    SkillRunResult,
    SkillRunStatus,
    ToolCallSpec,
    ToolFeedbackMessage,
    ToolTargetTracker,
    UsageLimits,
    UsageTracker,
    is_runtime_exempt_tool,
    resolve_effective_tool_allowlist,
)
# Feature 081 P1：从 provider 包直接 import LLMCallError，不再通过 litellm_client。
# ProviderModelClient（Feature 080 主 model_client）抛的也是 ProviderLLMCallError，
# 这里 alias 为 LLMCallError 与现有签名兼容。
from octoagent.provider import ProviderLLMCallError as LLMCallError
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
        on_tool_search_result: Callable[[str, str, str], Awaitable[None]] | None = None,
    ) -> None:
        self._model_client = model_client
        self._tool_broker = tool_broker
        self._event_store = event_store
        self._hooks = hooks or [NoopSkillRunnerHook()]
        # Feature 072: tool_search 结果回调（用于提升 deferred 工具）
        self._on_tool_search_result = on_tool_search_result

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

        # Feature 064 P3 优化 5: 构建 history key 用于 finally 清理
        history_key = f"{execution_context.task_id}:{execution_context.trace_id}"

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
        target_tracker = ToolTargetTracker(
            target_repeat_threshold=limits.repeat_signature_threshold,
        )
        # Feature 079: 连续 N 步 LLM 只发 tool_calls 没 user-facing content 的计数。
        # 任何一步 output.content 非空 / output.complete=True 即重置；超过
        # limits.no_progress_steps_threshold 触发 LOOP_DETECTED 熔断。
        no_progress_steps = 0

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
                # feedback 是"待送给下一轮 generate 的 buffer"，模型调用成功后
                # 视为已消费；若再保留，下一轮会把已发过的 tool_result / warning
                # 再次送给 model_client（model_client 已做去重但 loop_guard / 错误
                # feedback 降级为 user 消息那一支无法按 call_id 去重，会造成
                # 重复噪音进入 prompt，加剧 Agent 无效决策循环）。
                feedback.clear()
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
                # 观测性兜底：部分上游异常（httpx stream 中断、空响应、取消等）
                # 的 str(exc) 为空字符串。直接写入会让 MODEL_CALL_FAILED 事件的
                # error_message 为 ""，排查时无从下手。保证至少留下异常类型名。
                error_msg = str(exc) or f"<empty message> ({type(exc).__name__})"
                await self._emit_model_failed(
                    manifest, execution_context, error_msg, attempts, steps
                )

                # Feature 064 Phase 3: 异常分类差异化处理
                if isinstance(exc, LLMCallError):
                    if exc.error_type == "rate_limit":
                        # 速率限制：等待后重试，不消耗 retry 计数
                        logger.warning("rate_limit_backoff", step=steps, wait_seconds=3)
                        await asyncio.sleep(3)
                        continue
                    if exc.error_type == "context_overflow":
                        # 上下文超长：不可盲目重试，直接标记失败
                        return await self._terminate_with_failure(
                            manifest=manifest,
                            execution_context=execution_context,
                            history_key=history_key,
                            start_time=start_time,
                            attempts=attempts,
                            steps=steps,
                            category=ErrorCategory.TOKEN_LIMIT_EXCEEDED,
                            error=SkillRepeatError(f"上下文超长: {exc}"),
                        )
                    if exc.error_type == "conversation_state_lost":
                        # history 丢失（进程重启/淘汰策略误伤）：盲目重试会让
                        # LLM 看不到已执行的 tool_call 配对，可能重放非幂等工具。
                        # 必须由上层从 checkpoint 恢复后再发起，不在 runner 层重试。
                        return await self._terminate_with_failure(
                            manifest=manifest,
                            execution_context=execution_context,
                            history_key=history_key,
                            start_time=start_time,
                            attempts=attempts,
                            steps=steps,
                            category=ErrorCategory.REPEAT_ERROR,
                            error=SkillRepeatError(
                                f"对话状态已丢失，需从 checkpoint 恢复: {exc}"
                            ),
                        )

                retry_failures += 1
                if retry_failures > manifest.retry_policy.max_attempts:
                    return await self._terminate_with_failure(
                        manifest=manifest,
                        execution_context=execution_context,
                        history_key=history_key,
                        start_time=start_time,
                        attempts=attempts,
                        steps=steps,
                        category=ErrorCategory.REPEAT_ERROR,
                        error=SkillRepeatError(f"模型调用连续失败: {exc}"),
                    )
                await self._backoff(manifest)
                continue

            # raw_output 已经是 SkillOutputEnvelope，直接使用
            # 之前通过 manifest.output_model.model_validate 做额外转换，
            # 但这会在 Qwen 等模型的 tool_call 格式不同时导致 ValidationError，
            # 静默丢弃 tool_calls 进入 retry 循环。
            output = raw_output

            await self._call_hook("after_llm_call", manifest, output)

            # Feature 079: no-progress 检测 —— 连续 N 步 LLM 只发 tool_calls
            # 没产生 user-facing content 且未 complete，判定为"无进展循环"，
            # 立即熔断。覆盖 exact-signature / semantic-target 两个维度抓
            # 不到的"参数微变 + 工具反复成功但 LLM 不收尾"场景。典型案例：
            # 用户问"MCP 可以用了吗"，LLM 把 ask_model 当 connectivity probe
            # 反复发 "Reply with exactly: OK"，每次 message 微调 → signature
            # 不重复 → target 不重复 → 现有维度全部绕过，直到 step_limit
            # 兜底（30 轮约 1-3 min）。本检测在 8 轮内即可终止。
            no_progress_threshold = limits.no_progress_steps_threshold
            if no_progress_threshold is not None and not output.complete:
                if output.content and output.content.strip():
                    no_progress_steps = 0
                elif output.tool_calls:
                    no_progress_steps += 1
                    if no_progress_steps >= no_progress_threshold:
                        tool_names = ", ".join(
                            sorted({call.tool_name for call in output.tool_calls})
                        )
                        logger.warning(
                            "no_progress_loop_detected",
                            consecutive_steps=no_progress_steps,
                            threshold=no_progress_threshold,
                            tool_names=tool_names,
                            step=steps,
                        )
                        return await self._terminate_with_failure(
                            manifest=manifest,
                            execution_context=execution_context,
                            history_key=history_key,
                            start_time=start_time,
                            attempts=attempts,
                            steps=steps,
                            category=ErrorCategory.LOOP_DETECTED,
                            error=SkillLoopDetectedError(
                                f"无进展循环：连续 {no_progress_steps} 步只调用工具 "
                                f"[{tool_names}]，未产生任何回复内容。"
                                f"可能 LLM 误用工具做反复验证而非正常使用。"
                            ),
                        )

            if output.tool_calls:
                signature = self._tool_signature(output.tool_calls)
                if signature == last_signature:
                    repeat_count += 1
                else:
                    last_signature = signature
                    repeat_count = 1

                # Feature follow-up: 到达 warning 阈值时注入 _loop_guard feedback，
                # 向下一轮 LLM 显性提示"这组 tool_calls 已重复 N 轮且结果未变"，
                # 促使 LLM 打破无效循环。不终止任务（loop_detected 兜底仍有效）。
                if (
                    repeat_count == limits.repeat_warning_threshold
                    and repeat_count < limits.repeat_signature_threshold
                ):
                    tool_names = ", ".join(
                        sorted({call.tool_name for call in output.tool_calls})
                    )
                    feedback.append(
                        ToolFeedbackMessage(
                            tool_name=FEEDBACK_SENDER_LOOP_GUARD,
                            kind=FeedbackKind.LOOP_GUARD,
                            is_error=True,
                            output="",
                            error=(
                                f"警告：工具 [{tool_names}] 已连续第 {repeat_count} 轮"
                                f"以相同参数返回相同结果。请停止重复查询，"
                                f"改用已有信息推进任务或尝试其他策略；"
                                f"如需再次查询必须变更参数或换工具。"
                            ),
                            duration_ms=0,
                        )
                    )

                if repeat_count >= limits.repeat_signature_threshold:
                    return await self._terminate_with_failure(
                        manifest=manifest,
                        execution_context=execution_context,
                        history_key=history_key,
                        start_time=start_time,
                        attempts=attempts,
                        steps=steps,
                        category=ErrorCategory.LOOP_DETECTED,
                        error=SkillLoopDetectedError("检测到重复 tool_calls 签名循环"),
                    )

                # 语义级循环检测：同一工具反复操作同一目标
                loop_reason = target_tracker.record(output.tool_calls)
                if loop_reason:
                    logger.warning(
                        "semantic_loop_detected",
                        reason=loop_reason,
                        step=steps,
                        target_summary=target_tracker.summary(),
                    )
                    return await self._terminate_with_failure(
                        manifest=manifest,
                        execution_context=execution_context,
                        history_key=history_key,
                        start_time=start_time,
                        attempts=attempts,
                        steps=steps,
                        category=ErrorCategory.LOOP_DETECTED,
                        error=SkillLoopDetectedError(loop_reason),
                    )
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
                            tool_name=FEEDBACK_SENDER_TOOL_ERROR,
                            kind=FeedbackKind.SYSTEM_NOTICE,
                            is_error=True,
                            output="",
                            error=str(exc),
                            duration_ms=0,
                        )
                    )
                    if retry_failures > manifest.retry_policy.max_attempts:
                        return await self._terminate_with_failure(
                            manifest=manifest,
                            execution_context=execution_context,
                            history_key=history_key,
                            start_time=start_time,
                            attempts=attempts,
                            steps=steps,
                            category=ErrorCategory.TOOL_EXECUTION_ERROR,
                            error=exc,
                        )
                    await self._backoff(manifest)
                    continue

                feedback.extend(tool_feedbacks)
                tracker.tool_calls += len(output.tool_calls)

                if any(item.is_error for item in tool_feedbacks):
                    retry_failures += 1
                    if retry_failures > manifest.retry_policy.max_attempts:
                        return await self._terminate_with_failure(
                            manifest=manifest,
                            execution_context=execution_context,
                            history_key=history_key,
                            start_time=start_time,
                            attempts=attempts,
                            steps=steps,
                            category=ErrorCategory.TOOL_EXECUTION_ERROR,
                            error=SkillToolExecutionError("工具执行连续失败"),
                        )
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
                self._try_clear_history(history_key)
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
                self._try_clear_history(history_key)
                return result

            if not output.tool_calls:
                retry_failures += 1
                feedback.append(
                    ToolFeedbackMessage(
                        tool_name=FEEDBACK_SENDER_RUNNER_ERROR,
                        kind=FeedbackKind.SYSTEM_NOTICE,
                        is_error=True,
                        output="",
                        error="输出既未完成也未请求工具调用",
                        duration_ms=0,
                    )
                )
                if retry_failures > manifest.retry_policy.max_attempts:
                    return await self._terminate_with_failure(
                        manifest=manifest,
                        execution_context=execution_context,
                        history_key=history_key,
                        start_time=start_time,
                        attempts=attempts,
                        steps=steps,
                        category=ErrorCategory.REPEAT_ERROR,
                        error=SkillRepeatError("输出无进展，超过重试上限"),
                    )
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
        self._try_clear_history(history_key)
        return result

    async def _execute_tool_calls(
        self,
        *,
        manifest: SkillManifest,
        execution_context: SkillExecutionContext,
        tool_calls: list[ToolCallSpec],
        skip_remaining_tools: bool,
    ) -> list[ToolFeedbackMessage]:
        # 1. 白名单校验
        allowed_tool_names = resolve_effective_tool_allowlist(
            permission_mode=manifest.permission_mode,
            tools_allowed=list(manifest.tools_allowed),
            metadata=execution_context.metadata,
        )
        for call in tool_calls:
            if allowed_tool_names and call.tool_name not in allowed_tool_names:
                # MCP 动态工具豁免：与 ProviderClient schema 层放行策略保持一致，
                # 避免 LLM 在 schema 中看得见 mcp.* 但执行层拒绝导致孤立 tool_call。
                tool_meta = await self._tool_broker.get_tool_meta(call.tool_name)
                tool_group = tool_meta.tool_group if tool_meta else ""
                tool_metadata = (
                    getattr(tool_meta, "metadata", None) if tool_meta else None
                )
                if is_runtime_exempt_tool(call.tool_name, tool_group, tool_metadata):
                    continue
                raise SkillToolExecutionError(
                    f"工具 '{call.tool_name}' 不在当前 skill 可用工具集合中"
                )

        # 2. 分桶：按 SideEffectLevel 将 tool_calls 分为三组
        bucket_none: list[ToolCallSpec] = []       # 并行
        bucket_reversible: list[ToolCallSpec] = []  # 串行
        bucket_irreversible: list[ToolCallSpec] = []  # 审批串行

        for call in tool_calls:
            meta = await self._tool_broker.get_tool_meta(call.tool_name)
            level = meta.side_effect_level if meta else SideEffectLevel.IRREVERSIBLE
            if level == SideEffectLevel.NONE:
                bucket_none.append(call)
            elif level == SideEffectLevel.REVERSIBLE:
                bucket_reversible.append(call)
            else:
                bucket_irreversible.append(call)

        # 用于按原始顺序重排结果
        results_map: dict[str, ToolFeedbackMessage] = {}
        # 每个 call 的唯一键（用于多次调用同一工具的场景）
        call_keys = [
            f"{c.tool_name}:{c.tool_call_id}:{i}"
            for i, c in enumerate(tool_calls)
        ]

        batch_id: str | None = None
        batch_start_time = time.monotonic()

        # 3. 发射 TOOL_BATCH_STARTED（仅 batch_size > 1）
        if len(tool_calls) > 1:
            batch_id = str(ULID())
            await self._emit_tool_batch_started(
                execution_context=execution_context,
                batch_id=batch_id,
                tool_calls=tool_calls,
                manifest=manifest,
                bucket_none_count=len(bucket_none),
                bucket_reversible_count=len(bucket_reversible),
                bucket_irreversible_count=len(bucket_irreversible),
            )

        # 预建 call -> 原始索引映射，避免 O(n²) 的 .index() 查找
        call_index_map: dict[int, int] = {id(c): i for i, c in enumerate(tool_calls)}

        # 4a. 并行执行 NONE 桶（asyncio.gather + return_exceptions=True）
        if bucket_none:
            none_indices = [call_index_map[id(c)] for c in bucket_none]
            coros = [
                self._execute_single_tool(manifest, execution_context, call)
                for call in bucket_none
            ]
            parallel_results = await asyncio.gather(*coros, return_exceptions=True)
            for call, idx, result in zip(bucket_none, none_indices, parallel_results):
                if isinstance(result, Exception):
                    fb = ToolFeedbackMessage(
                        tool_name=call.tool_name,
                        tool_call_id=call.tool_call_id,
                        is_error=True,
                        output="",
                        error=str(result),
                        duration_ms=0,
                    )
                else:
                    fb = result
                results_map[call_keys[idx]] = fb

        # 4b. 串行执行 REVERSIBLE 桶
        for call in bucket_reversible:
            idx = call_index_map[id(call)]
            fb = await self._execute_single_tool(manifest, execution_context, call)
            results_map[call_keys[idx]] = fb
            if skip_remaining_tools:
                break

        # 4c. 逐个审批执行 IRREVERSIBLE 桶
        for call in bucket_irreversible:
            idx = call_index_map[id(call)]
            fb = await self._execute_single_tool(manifest, execution_context, call)
            results_map[call_keys[idx]] = fb
            if skip_remaining_tools:
                break

        # 5. 发射 TOOL_BATCH_COMPLETED
        if batch_id:
            all_results = [results_map.get(k) for k in call_keys if k in results_map]
            success_count = sum(1 for r in all_results if r and not r.is_error)
            error_count = sum(1 for r in all_results if r and r.is_error)
            duration_ms = int((time.monotonic() - batch_start_time) * 1000)
            await self._emit_tool_batch_completed(
                execution_context=execution_context,
                batch_id=batch_id,
                manifest=manifest,
                duration_ms=duration_ms,
                success_count=success_count,
                error_count=error_count,
                total_count=len(tool_calls),
            )

        # 6. 按原始 tool_calls 顺序返回结果
        ordered_results: list[ToolFeedbackMessage] = []
        for key in call_keys:
            if key in results_map:
                ordered_results.append(results_map[key])
        return ordered_results

    async def _execute_single_tool(
        self,
        manifest: SkillManifest,
        execution_context: SkillExecutionContext,
        call: ToolCallSpec,
    ) -> ToolFeedbackMessage:
        """执行单个工具调用：hook → execute → feedback → hook。

        Feature 070: 审批在 ToolBroker.execute() 内通过 check_permission() 完成，
        不再需要 ask bridge 桥接。
        """
        await self._call_hook("before_tool_execute", call.tool_name, call.arguments)

        try:
            _preset = PermissionPreset(execution_context.permission_preset)
        except ValueError:
            _preset = PermissionPreset.NORMAL
        tool_context = ExecutionContext(
            task_id=execution_context.task_id,
            trace_id=execution_context.trace_id,
            caller=execution_context.caller,
            agent_runtime_id=execution_context.agent_runtime_id,
            agent_session_id=execution_context.agent_session_id,
            work_id=execution_context.work_id,
            permission_preset=_preset,
        )
        tool_result = await self._tool_broker.execute(
            call.tool_name, call.arguments, tool_context
        )

        feedback = self._build_tool_feedback(
            call.tool_name, tool_result, manifest.context_budget,
            tool_call_id=call.tool_call_id,
        )

        # Feature 072: tool_search 结果触发工具提升
        if (
            call.tool_name == "tool_search"
            and not feedback.is_error
            and self._on_tool_search_result is not None
        ):
            try:
                await self._on_tool_search_result(
                    feedback.output,
                    execution_context.task_id or "",
                    execution_context.trace_id or "",
                )
            except Exception:
                logger.warning("tool_search_promotion_failed", exc_info=True)

        await self._call_hook("after_tool_execute", feedback)
        return feedback

    @staticmethod
    def _build_tool_feedback(
        tool_name: str,
        tool_result: Any,
        budget: Any,
        *,
        tool_call_id: str = "",
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
            tool_call_id=tool_call_id,
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

    def _try_clear_history(self, history_key: str) -> None:
        """Feature 064 P3 优化 5: Task 终态时清理 model_client 对话历史。"""
        clear_fn = getattr(self._model_client, "clear_history", None)
        if callable(clear_fn):
            try:
                clear_fn(history_key)
            except Exception:
                pass

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

    async def _terminate_with_failure(
        self,
        *,
        manifest: SkillManifest,
        execution_context: SkillExecutionContext,
        history_key: str,
        start_time: float,
        attempts: int,
        steps: int,
        category: ErrorCategory,
        error: Exception,
    ) -> SkillRunResult:
        """统一的失败终止路径：emit SKILL_FAILED → skill_end hook → 清理 history。

        历史问题：`run()` 内多处 fail 分支手写这三步，两处（TOOL_EXECUTION_ERROR
        分支）漏掉 `_try_clear_history` 导致 `_histories` dict 残留。集中到一处
        后所有失败路径行为一致，也少 4~5 行重复代码。
        """
        result = await self._fail_result(
            manifest=manifest,
            execution_context=execution_context,
            start_time=start_time,
            attempts=attempts,
            steps=steps,
            category=category,
            error=error,
        )
        await self._call_hook("skill_end", manifest, execution_context, result)
        self._try_clear_history(history_key)
        return result

    async def _emit_event(
        self,
        *,
        execution_context: SkillExecutionContext,
        event_type: EventType,
        payload: dict[str, Any],
    ) -> None:
        """发射事件到 Task 事件流（best-effort）。

        Feature 064 P3: 委托给 core 层 emit_task_event()，消除重复逻辑。

        关键不变量：emit 是观测副作用，不能让事件落盘失败中断主业务。具体
        场景：MODEL_CALL_COMPLETED 写 event_store 抛异常时，如果异常冒到
        run() 主循环的 try/except，会被误当作"模型调用失败"触发 retry，
        但实际上 tool 还没来得及执行，下一轮 history 里会出现孤立的
        assistant.tool_calls（没对应 tool_result），LLM 要么重放工具要么
        基于不完整上下文继续决策。统一在此吞掉异常并降级为 warning，
        保证主流程按原轨迹推进。
        """
        if self._event_store is None:
            return

        try:
            await emit_task_event(
                self._event_store,
                task_id=execution_context.task_id,
                event_type=event_type,
                payload=payload,
                actor=ActorType.WORKER,
                trace_id=execution_context.trace_id,
            )
        except Exception:
            logger.warning(
                "skill_event_emit_failed",
                event_type=event_type.value if hasattr(event_type, "value") else str(event_type),
                task_id=execution_context.task_id,
                exc_info=True,
            )

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
        payload: dict[str, object] = {
            "skill_id": manifest.skill_id,
            "model_alias": manifest.model_alias,
            "attempt": attempt,
            "step": step,
            "response_summary": output.content[:200],
            "token_usage": output.token_usage or {},
        }
        if output.tool_calls:
            payload["tool_calls"] = [
                {"tool_name": tc.tool_name, "arguments": tc.arguments}
                for tc in output.tool_calls
            ]
        await self._emit_event(
            execution_context=execution_context,
            event_type=EventType.MODEL_CALL_COMPLETED,
            payload=payload,
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

    async def _emit_tool_batch_started(
        self,
        *,
        execution_context: SkillExecutionContext,
        batch_id: str,
        tool_calls: list[ToolCallSpec],
        manifest: SkillManifest,
        bucket_none_count: int,
        bucket_reversible_count: int,
        bucket_irreversible_count: int,
    ) -> None:
        """发射 TOOL_BATCH_STARTED 事件。"""
        await self._emit_event(
            execution_context=execution_context,
            event_type=EventType.TOOL_BATCH_STARTED,
            payload={
                "batch_id": batch_id,
                "tool_names": [call.tool_name for call in tool_calls],
                "execution_mode": "parallel",
                "batch_size": len(tool_calls),
                "agent_runtime_id": execution_context.agent_runtime_id,
                "skill_id": manifest.skill_id,
                "bucket_none_count": bucket_none_count,
                "bucket_reversible_count": bucket_reversible_count,
                "bucket_irreversible_count": bucket_irreversible_count,
            },
        )

    async def _emit_tool_batch_completed(
        self,
        *,
        execution_context: SkillExecutionContext,
        batch_id: str,
        manifest: SkillManifest,
        duration_ms: int,
        success_count: int,
        error_count: int,
        total_count: int,
    ) -> None:
        """发射 TOOL_BATCH_COMPLETED 事件。"""
        await self._emit_event(
            execution_context=execution_context,
            event_type=EventType.TOOL_BATCH_COMPLETED,
            payload={
                "batch_id": batch_id,
                "duration_ms": duration_ms,
                "success_count": success_count,
                "error_count": error_count,
                "total_count": total_count,
                "agent_runtime_id": execution_context.agent_runtime_id,
                "skill_id": manifest.skill_id,
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
