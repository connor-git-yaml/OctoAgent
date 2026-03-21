"""Hook 实现 -- Feature 004 Tool Contract + ToolBroker

内置 Hook：
- LargeOutputHandler（after hook）: 大输出自动裁切 -- FR-016/017/018
- EventGenerationHook（after hook）: 事件生成 -- FR-014（Phase 8 实现）
"""

from __future__ import annotations

import re
from datetime import datetime

import structlog
from octoagent.core.models.artifact import Artifact, ArtifactPart
from octoagent.core.models.enums import ActorType, EventType, PartType
from octoagent.core.models.event import Event
from octoagent.core.models.payloads import (
    ArtifactCreatedPayload,
    ToolCallCompletedPayload,
    ToolCallFailedPayload,
)
from ulid import ULID

from .models import (
    ExecutionContext,
    FailMode,
    ToolMeta,
    ToolResult,
)
from .protocols import (
    ArtifactStoreProtocol,
    EventBroadcasterProtocol,
    EventStoreProtocol,
)
from .sanitizer import sanitize_for_event

logger = structlog.get_logger(__name__)


# ============================================================
# LargeOutputHandler -- 大输出自动裁切
# ============================================================

# 参考 OpenClaw：单个工具结果最多占上下文窗口的指定比例。
# 参考 Agent Zero：工具结果首次返回时尽量保留完整内容，
# 只在极端超长时截断。上下文预算管理在更上层（历史压缩）处理。
_CONTEXT_SHARE_RATIO = 0.5  # 单工具结果最多占上下文窗口的 50%
_CHARS_PER_TOKEN = 4  # 粗略估计：1 token ≈ 4 字符
_HARD_MAX_CHARS = 400_000  # 硬上限，防止极端情况撑爆内存
_MIN_THRESHOLD = 2_000  # 最低阈值，即使上下文窗口很小也不低于此值
_DEFAULT_CONTEXT_WINDOW_TOKENS = 128_000  # 默认上下文窗口（未配置时的 fallback）

# Head + Tail 截断时检测"重要尾部"的关键词
_TAIL_IMPORTANCE_KEYWORDS = re.compile(
    r"error|exception|failed|fatal|traceback|panic|stack.?trace|errno|exit.?code"
    r"|total|summary|result|complete|finished|done",
    re.IGNORECASE,
)
_TAIL_BUDGET_RATIO = 0.3  # 重要尾部占截断预算的 30%
_TAIL_MAX_CHARS = 4_000  # 重要尾部最大字符数


def calculate_max_tool_result_chars(context_window_tokens: int) -> int:
    """根据上下文窗口大小计算单个工具结果的最大字符数。

    参考 OpenClaw 的 calculateMaxToolResultChars：
    - 按上下文窗口的 50% 计算
    - 受硬上限 400K 字符约束
    - 不低于 2K 字符
    """
    max_tokens = int(context_window_tokens * _CONTEXT_SHARE_RATIO)
    max_chars = max_tokens * _CHARS_PER_TOKEN
    return max(
        _MIN_THRESHOLD,
        min(max_chars, _HARD_MAX_CHARS),
    )


def head_tail_truncate(text: str, max_chars: int) -> str:
    """Head + Tail 智能截断：保留头尾，中间加占位符。

    参考 OpenClaw 的 truncateToolResultText：
    1. 检测尾部是否包含错误/总结等重要信息
    2. 有重要尾部时，按 70/30 分配头尾预算
    3. 无重要尾部时，只保留头部
    4. 尽量在换行符边界切割，避免截断不完整的行

    截断标记引导 LLM 使用 offset/limit 参数重读特定区段。
    """
    if len(text) <= max_chars:
        return text

    omitted_chars = len(text) - max_chars
    # 粗略估计省略的 token 数
    omitted_tokens_est = omitted_chars // _CHARS_PER_TOKEN

    # 检测尾部是否有"重要信息"
    tail_check_len = min(2000, len(text))
    tail_snippet = text[-tail_check_len:]
    has_important_tail = bool(_TAIL_IMPORTANCE_KEYWORDS.search(tail_snippet))

    if has_important_tail:
        # 尾部预算：30% 的可用空间，最多 _TAIL_MAX_CHARS
        tail_budget = min(int(max_chars * _TAIL_BUDGET_RATIO), _TAIL_MAX_CHARS)
        marker = (
            f"\n\n⚠️ [... 中间省略约 {omitted_tokens_est} tokens ..."
            f" 使用 offset/limit 参数分段读取完整内容 ...]\n\n"
        )
        head_budget = max_chars - tail_budget - len(marker)
        if head_budget < 200:
            head_budget = 200
            tail_budget = max_chars - head_budget - len(marker)

        # 尽量在换行符边界切割
        head = text[:head_budget]
        last_nl = head.rfind("\n")
        if last_nl > head_budget * 0.7:
            head = head[:last_nl + 1]

        tail = text[-tail_budget:]
        first_nl = tail.find("\n")
        if 0 < first_nl < tail_budget * 0.3:
            tail = tail[first_nl + 1:]

        return head + marker + tail
    else:
        # 无重要尾部：只保留头部
        marker = (
            f"\n\n⚠️ [内容已截断 — 原文 {len(text)} 字符（约 {len(text) // _CHARS_PER_TOKEN} tokens）。"
            f" 以上为部分内容。如需更多，请使用 offset/limit 参数分段读取。]"
        )
        head_budget = max_chars - len(marker)
        head = text[:head_budget]
        last_nl = head.rfind("\n")
        if last_nl > head_budget * 0.7:
            head = head[:last_nl + 1]

        return head + marker


class LargeOutputHandler:
    """大输出自动裁切 -- 对齐 spec FR-016/017/018, C11 Context Hygiene

    参考 OpenClaw / Agent Zero 的设计：
    - 阈值按上下文窗口的 50% 动态计算（而非硬编码 500 字符）
    - Head + Tail 智能截断（保留头部上下文 + 尾部错误/总结）
    - 截断标记引导 LLM 用 offset/limit 重读
    - ArtifactStore 仅用于审计存档，不作为 LLM 恢复完整内容的途径

    降级策略：ArtifactStore 不可用时仍执行截断（FR-018）。
    """

    def __init__(
        self,
        artifact_store: ArtifactStoreProtocol,
        event_store: EventStoreProtocol | None = None,
        event_broadcaster: EventBroadcasterProtocol | None = None,
        context_window_tokens: int = _DEFAULT_CONTEXT_WINDOW_TOKENS,
    ) -> None:
        """初始化 LargeOutputHandler

        Args:
            artifact_store: ArtifactStore 实例
            event_store: EventStore 实例（可选，用于补写 ARTIFACT_CREATED 审计）
            event_broadcaster: 事件广播器（可选，用于实时推送增量事件）
            context_window_tokens: 模型上下文窗口大小（token 数），用于动态计算截断阈值
        """
        self._artifact_store = artifact_store
        self._event_store = event_store
        self._event_broadcaster = event_broadcaster
        self._context_window_tokens = context_window_tokens
        self._default_threshold = calculate_max_tool_result_chars(context_window_tokens)

    @property
    def name(self) -> str:
        return "large_output_handler"

    @property
    def priority(self) -> int:
        return 50

    @property
    def fail_mode(self) -> FailMode:
        return FailMode.OPEN  # 裁切失败不影响工具结果

    async def after_execute(
        self,
        tool_meta: ToolMeta,
        result: ToolResult,
        context: ExecutionContext,
    ) -> ToolResult:
        """执行后裁切检查

        Args:
            tool_meta: 工具元数据
            result: 工具执行结果
            context: 执行上下文

        Returns:
            可能被裁切的 ToolResult
        """
        # 错误结果不裁切
        if result.is_error:
            return result

        # 确定阈值（FR-017: 工具级 > 动态默认）
        threshold = tool_meta.output_truncate_threshold or self._default_threshold

        # 未超阈值不裁切
        if len(result.output) <= threshold:
            return result

        # Head + Tail 智能截断
        truncated_output = head_tail_truncate(result.output, threshold)

        # 尝试将完整输出存入 ArtifactStore（用于审计，非 LLM 恢复）
        artifact_id: str | None = None
        try:
            artifact_id = await self._store_as_artifact(
                output=result.output,
                tool_name=tool_meta.name,
                context=context,
            )
        except Exception as e:
            # FR-018: ArtifactStore 不可用不影响截断
            logger.warning(
                "artifact_store_unavailable",
                tool_name=tool_meta.name,
                output_length=len(result.output),
                error=str(e),
            )

        return result.model_copy(
            update={
                "output": truncated_output,
                "artifact_ref": artifact_id,
                "truncated": True,
            }
        )

    async def _store_as_artifact(
        self,
        output: str,
        tool_name: str,
        context: ExecutionContext,
    ) -> str:
        """将完整输出存入 ArtifactStore

        Args:
            output: 完整输出内容
            tool_name: 工具名称
            context: 执行上下文

        Returns:
            Artifact ID
        """
        artifact_id = str(ULID())
        content_bytes = output.encode("utf-8")

        artifact = Artifact(
            artifact_id=artifact_id,
            task_id=context.task_id,
            ts=datetime.now(),
            name=f"tool_output:{tool_name}",
            description=f"工具 {tool_name} 的完整输出（{len(output)} 字符）",
            size=len(content_bytes),
            parts=[
                ArtifactPart(
                    type=PartType.TEXT,
                    mime="text/plain",
                    content=None,  # 内容通过 content 参数传递
                )
            ],
        )

        await self._artifact_store.put_artifact(artifact, content_bytes)
        await self._emit_artifact_created_event(
            artifact=artifact,
            context=context,
            source=f"tool_output:{tool_name}",
        )
        return artifact_id

    async def _emit_artifact_created_event(
        self,
        *,
        artifact: Artifact,
        context: ExecutionContext,
        source: str,
    ) -> None:
        if self._event_store is None:
            return
        try:
            event = Event(
                event_id=str(ULID()),
                task_id=context.task_id,
                task_seq=await self._event_store.get_next_task_seq(context.task_id),
                ts=datetime.now(),
                type=EventType.ARTIFACT_CREATED,
                actor=ActorType.SYSTEM,
                payload=ArtifactCreatedPayload(
                    artifact_id=artifact.artifact_id,
                    name=artifact.name,
                    size=artifact.size,
                    part_count=len(artifact.parts),
                    session_id=context.agent_session_id or None,
                    source=source,
                ).model_dump(),
                trace_id=context.trace_id,
            )
            stored_event = await self._persist_event(event)
            if self._event_broadcaster is not None:
                await self._event_broadcaster.broadcast(context.task_id, stored_event)
        except Exception as e:
            logger.warning(
                "large_output_artifact_event_failed",
                task_id=context.task_id,
                artifact_id=artifact.artifact_id,
                error=str(e),
            )

    async def _persist_event(self, event: Event) -> Event:
        if self._event_store is None:
            return event
        append_committed = getattr(self._event_store, "append_event_committed", None)
        if callable(append_committed):
            return await append_committed(event, update_task_pointer=True)
        await self._event_store.append_event(event)
        return event


# ============================================================
# EventGenerationHook -- 事件生成（Phase 8 实现）
# ============================================================


class EventGenerationHook:
    """事件生成 Hook -- 对齐 spec FR-014, FR-015

    作为 after hook（priority=0）运行，在 ToolResult 返回前
    生成 TOOL_CALL_COMPLETED / TOOL_CALL_FAILED 事件写入 EventStore。

    与 Broker 内联事件生成的区别：
    - EventGenerationHook 使用 Sanitizer 对 payload 脱敏（FR-015）
    - 作为可选增强：注册后提供脱敏事件，未注册时 Broker 内联事件仍可用

    降级策略：EventStore 不可用时 log-and-continue（fail_mode=open）。
    """

    def __init__(self, event_store: EventStoreProtocol) -> None:
        """初始化 EventGenerationHook

        Args:
            event_store: EventStore 实例（用于事件持久化）
        """
        self._event_store = event_store

    @property
    def name(self) -> str:
        return "event_generation_hook"

    @property
    def priority(self) -> int:
        return 0  # 最高优先级，最先执行

    @property
    def fail_mode(self) -> FailMode:
        return FailMode.OPEN  # 事件生成失败不影响工具结果

    async def after_execute(
        self,
        tool_meta: ToolMeta,
        result: ToolResult,
        context: ExecutionContext,
    ) -> ToolResult:
        """执行后生成事件

        根据 ToolResult.is_error 生成 COMPLETED 或 FAILED 事件，
        payload 经过 Sanitizer 脱敏处理。

        Args:
            tool_meta: 工具元数据
            result: 工具执行结果
            context: 执行上下文

        Returns:
            原始 ToolResult（不修改）
        """
        try:
            if result.is_error:
                await self._emit_failed_event(tool_meta, result, context)
            else:
                await self._emit_completed_event(tool_meta, result, context)
        except Exception as e:
            # fail_mode=open: 记录警告并继续
            logger.warning(
                "event_generation_hook_failed",
                tool_name=tool_meta.name,
                error=str(e),
            )

        # 不修改 ToolResult
        return result

    async def _emit_completed_event(
        self,
        tool_meta: ToolMeta,
        result: ToolResult,
        context: ExecutionContext,
    ) -> None:
        """生成 TOOL_CALL_COMPLETED 事件（脱敏 payload）"""
        raw_payload = ToolCallCompletedPayload(
            tool_name=tool_meta.name,
            duration_ms=int(result.duration * 1000),
            output_summary=result.output[:200] if result.output else "",
            agent_runtime_id=context.agent_runtime_id,
            agent_session_id=context.agent_session_id,
            work_id=context.work_id,
            truncated=result.truncated,
            artifact_ref=result.artifact_ref,
        ).model_dump()
        # FR-015: 脱敏处理
        sanitized_payload = sanitize_for_event(raw_payload)

        event = Event(
            event_id=str(ULID()),
            task_id=context.task_id,
            task_seq=await self._event_store.get_next_task_seq(context.task_id),
            ts=datetime.now(),
            type=EventType.TOOL_CALL_COMPLETED,
            actor=ActorType.TOOL,
            payload=sanitized_payload,
            trace_id=context.trace_id,
        )
        append_committed = getattr(self._event_store, "append_event_committed", None)
        if callable(append_committed):
            await append_committed(event, update_task_pointer=True)
            return
        await self._event_store.append_event(event)

    async def _emit_failed_event(
        self,
        tool_meta: ToolMeta,
        result: ToolResult,
        context: ExecutionContext,
    ) -> None:
        """生成 TOOL_CALL_FAILED 事件（脱敏 payload）"""
        raw_payload = ToolCallFailedPayload(
            tool_name=tool_meta.name,
            duration_ms=int(result.duration * 1000),
            error_type="exception",
            error_message=(result.error or "")[:500],
            agent_runtime_id=context.agent_runtime_id,
            agent_session_id=context.agent_session_id,
            work_id=context.work_id,
            recoverable=True,
        ).model_dump()
        # FR-015: 脱敏处理
        sanitized_payload = sanitize_for_event(raw_payload)

        event = Event(
            event_id=str(ULID()),
            task_id=context.task_id,
            task_seq=await self._event_store.get_next_task_seq(context.task_id),
            ts=datetime.now(),
            type=EventType.TOOL_CALL_FAILED,
            actor=ActorType.TOOL,
            payload=sanitized_payload,
            trace_id=context.trace_id,
        )
        append_committed = getattr(self._event_store, "append_event_committed", None)
        if callable(append_committed):
            await append_committed(event, update_task_pointer=True)
            return
        await self._event_store.append_event(event)
