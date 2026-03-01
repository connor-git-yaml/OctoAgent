"""Hook 实现 -- Feature 004 Tool Contract + ToolBroker

内置 Hook：
- LargeOutputHandler（after hook）: 大输出自动裁切 -- FR-016/017/018
- EventGenerationHook（after hook）: 事件生成 -- FR-014（Phase 8 实现）
"""

from __future__ import annotations

from datetime import datetime

import structlog
from octoagent.core.models.artifact import Artifact, ArtifactPart
from octoagent.core.models.enums import ActorType, EventType, PartType
from octoagent.core.models.event import Event
from ulid import ULID

from .models import (
    ExecutionContext,
    FailMode,
    ToolMeta,
    ToolResult,
)
from .protocols import ArtifactStoreProtocol, EventStoreProtocol
from .sanitizer import sanitize_for_event

logger = structlog.get_logger(__name__)


# ============================================================
# LargeOutputHandler -- 大输出自动裁切
# ============================================================


class LargeOutputHandler:
    """大输出自动裁切 -- 对齐 spec FR-016/017/018, C11 Context Hygiene

    作为 after hook 运行，对工具输出超过阈值时：
    1. 完整输出存入 ArtifactStore
    2. ToolResult.output 替换为引用摘要
    3. ToolResult.artifact_ref 设置为 Artifact ID

    降级策略：ArtifactStore 不可用时保留原始输出（FR-018）。
    """

    DEFAULT_THRESHOLD = 500  # 默认裁切阈值（字符）

    def __init__(
        self,
        artifact_store: ArtifactStoreProtocol,
        default_threshold: int = DEFAULT_THRESHOLD,
    ) -> None:
        """初始化 LargeOutputHandler

        Args:
            artifact_store: ArtifactStore 实例
            default_threshold: 全局默认裁切阈值（字符数）
        """
        self._artifact_store = artifact_store
        self._default_threshold = default_threshold

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

        # 确定阈值（FR-017: 工具级 > 全局默认）
        threshold = tool_meta.output_truncate_threshold or self._default_threshold

        # 未超阈值不裁切
        if len(result.output) <= threshold:
            return result

        # 超阈值：尝试存入 ArtifactStore
        try:
            artifact_id = await self._store_as_artifact(
                output=result.output,
                tool_name=tool_meta.name,
                context=context,
            )
            prefix = result.output[:200]
            return result.model_copy(
                update={
                    "output": (
                        f"[Output truncated. Full content: artifact:{artifact_id}]\n{prefix}..."
                    ),
                    "artifact_ref": artifact_id,
                    "truncated": True,
                }
            )
        except Exception as e:
            # FR-018: ArtifactStore 不可用 -> 降级：保留原始输出
            logger.warning(
                "artifact_store_unavailable",
                tool_name=tool_meta.name,
                output_length=len(result.output),
                error=str(e),
            )
            return result

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
            parts=[
                ArtifactPart(
                    type=PartType.TEXT,
                    mime="text/plain",
                    content=None,  # 内容通过 content 参数传递
                )
            ],
        )

        await self._artifact_store.put_artifact(artifact, content_bytes)
        return artifact_id


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
        raw_payload = {
            "tool_name": tool_meta.name,
            "tool_group": tool_meta.tool_group,
            "duration_ms": int(result.duration * 1000),
            "output_summary": result.output[:200] if result.output else "",
            "truncated": result.truncated,
            "artifact_ref": result.artifact_ref,
        }
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
        await self._event_store.append_event(event)

    async def _emit_failed_event(
        self,
        tool_meta: ToolMeta,
        result: ToolResult,
        context: ExecutionContext,
    ) -> None:
        """生成 TOOL_CALL_FAILED 事件（脱敏 payload）"""
        raw_payload = {
            "tool_name": tool_meta.name,
            "tool_group": tool_meta.tool_group,
            "duration_ms": int(result.duration * 1000),
            "error_type": "exception",
            "error_message": (result.error or "")[:500],
            "recoverable": True,
        }
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
        await self._event_store.append_event(event)
