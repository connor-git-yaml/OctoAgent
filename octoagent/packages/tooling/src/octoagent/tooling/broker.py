"""ToolBroker 实现 -- Feature 004 Tool Contract + ToolBroker

对齐 spec FR-006/007/008/009/010/010a/012/013, contracts/tooling-api.md §2。
Protocol-based Mediator + Hook Chain 架构。
工具注册、发现、执行的中央中介者。
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

import structlog
from octoagent.core.models.enums import ActorType, EventType
from octoagent.core.models.event import Event
from octoagent.core.models.payloads import (
    ToolCallCompletedPayload,
    ToolCallFailedPayload,
    ToolCallStartedPayload,
)
from ulid import ULID

from .exceptions import (
    ToolRegistrationError,
)
from .models import (
    ExecutionContext,
    FailMode,
    RegisterToolResult,
    RegistryDiagnostic,
    ToolMeta,
    ToolProfile,
    ToolResult,
    profile_allows,
)
from .protocols import AfterHook, ArtifactStoreProtocol, BeforeHook, EventStoreProtocol
from .sanitizer import sanitize_for_event

logger = structlog.get_logger(__name__)


class ToolBroker:
    """ToolBroker -- 工具注册、发现、执行的中央中介者

    实现 ToolBrokerProtocol。所有工具调用必须经过 Broker，
    确保 hook 链路完整执行（FR-010）。

    注册表为进程内存态，进程重启后需重新注册。
    历史工具调用事件通过 EventStore 持久化不丢失。
    """

    def __init__(
        self,
        event_store: EventStoreProtocol,
        artifact_store: ArtifactStoreProtocol | None = None,
    ) -> None:
        """初始化 ToolBroker

        Args:
            event_store: EventStore 实例（用于事件持久化）
            artifact_store: ArtifactStore 实例（可选，用于大输出存储）
        """
        # 注册表：tool_name -> (ToolMeta, handler)
        self._registry: dict[str, tuple[ToolMeta, Callable[..., Any]]] = {}
        # Hook 列表
        self._before_hooks: list[BeforeHook] = []
        self._after_hooks: list[AfterHook] = []
        self._diagnostics: list[RegistryDiagnostic] = []
        # 外部依赖
        self._event_store = event_store
        self._artifact_store = artifact_store

    # ============================================================
    # 注册与发现（Phase 4: US2）
    # ============================================================

    async def register(
        self,
        tool_meta: ToolMeta,
        handler: Callable[..., Any],
    ) -> None:
        """注册工具到 Broker（FR-006）

        Args:
            tool_meta: 工具元数据（由 Schema Reflection 生成）
            handler: 工具执行处理函数

        Raises:
            ToolRegistrationError: 名称冲突
        """
        if tool_meta.name in self._registry:
            raise ToolRegistrationError(
                f"Tool '{tool_meta.name}' already registered. 请先 unregister 再重新注册（EC-7）"
            )
        self._registry[tool_meta.name] = (tool_meta, handler)
        logger.info(
            "tool_registered",
            tool_name=tool_meta.name,
            side_effect_level=tool_meta.side_effect_level,
            tool_profile=tool_meta.tool_profile,
            tool_group=tool_meta.tool_group,
        )

    async def try_register(
        self,
        tool_meta: ToolMeta,
        handler: Callable[..., Any],
    ) -> RegisterToolResult:
        """尝试注册工具（Feature 012 fail-open 语义）

        与 register 的区别：
        - register: 冲突时抛出 ToolRegistrationError（严格模式）
        - try_register: 冲突时返回 ok=False，并写入 registry diagnostics
        """
        try:
            await self.register(tool_meta, handler)
            return RegisterToolResult(
                ok=True,
                tool_name=tool_meta.name,
                message="registered",
            )
        except Exception as exc:
            diagnostic = RegistryDiagnostic(
                tool_name=tool_meta.name,
                error_type=type(exc).__name__,
                message=str(exc),
                timestamp=datetime.now(),
            )
            self._diagnostics.append(diagnostic)
            logger.warning(
                "tool_try_register_failed",
                tool_name=tool_meta.name,
                error_type=diagnostic.error_type,
                message=diagnostic.message,
            )
            return RegisterToolResult(
                ok=False,
                tool_name=tool_meta.name,
                message=diagnostic.message,
                error_type=diagnostic.error_type,
            )

    @property
    def registry_diagnostics(self) -> list[RegistryDiagnostic]:
        """返回诊断列表快照，防止调用方修改内部状态。"""
        return list(self._diagnostics)

    async def discover(
        self,
        profile: ToolProfile | None = None,
        group: str | None = None,
    ) -> list[ToolMeta]:
        """发现可用工具（FR-007/008）

        Args:
            profile: 按 Profile 过滤（含该级别及以下）
            group: 按逻辑分组过滤

        Returns:
            匹配的 ToolMeta 列表
        """
        results: list[ToolMeta] = []
        for meta, _ in self._registry.values():
            # Profile 层级过滤
            if profile is not None and not profile_allows(meta.tool_profile, profile):
                continue
            # Group 过滤
            if group is not None and meta.tool_group != group:
                continue
            results.append(meta)
        return results

    async def get_tool_meta(self, tool_name: str) -> ToolMeta | None:
        """按名称查询工具元数据（含 SideEffectLevel）。

        O(1) 复杂度，从内部 _registry 字典查找。

        Args:
            tool_name: 工具名称

        Returns:
            ToolMeta 或 None（工具未注册时）
        """
        entry = self._registry.get(tool_name)
        return entry[0] if entry else None

    async def unregister(self, tool_name: str) -> bool:
        """注销工具（FR-009）

        Args:
            tool_name: 工具名称

        Returns:
            True 如果成功注销，False 如果工具不存在
        """
        if tool_name in self._registry:
            del self._registry[tool_name]
            logger.info("tool_unregistered", tool_name=tool_name)
            return True
        return False

    # ============================================================
    # Hook 管理（Phase 6: US5）
    # ============================================================

    def add_hook(self, hook: BeforeHook | AfterHook) -> None:
        """注册 Hook 扩展点（FR-019/020）

        根据 hook 类型（BeforeHook/AfterHook）自动分类，
        按 priority 排序插入。

        Args:
            hook: BeforeHook 或 AfterHook 实例
        """
        # 通过检测方法名区分 hook 类型
        if hasattr(hook, "before_execute"):
            self._before_hooks.append(hook)  # type: ignore[arg-type]
            self._before_hooks.sort(key=lambda h: h.priority)
            logger.info(
                "before_hook_added",
                hook_name=hook.name,
                priority=hook.priority,
                fail_mode=hook.fail_mode,
            )
        elif hasattr(hook, "after_execute"):
            self._after_hooks.append(hook)  # type: ignore[arg-type]
            self._after_hooks.sort(key=lambda h: h.priority)
            logger.info(
                "after_hook_added",
                hook_name=hook.name,
                priority=hook.priority,
                fail_mode=hook.fail_mode,
            )

    # ============================================================
    # 工具执行（Phase 5: US3）
    # ============================================================

    async def execute(
        self,
        tool_name: str,
        args: dict[str, Any],
        context: ExecutionContext,
    ) -> ToolResult:
        """执行工具调用（FR-010/012/013 + Feature 061）

        完整执行链路：
        1. 查找工具
        2. 生成 TOOL_CALL_STARTED 事件
        3. 执行 before hook 链（含 PresetBeforeHook 权限检查）
        4. 执行工具（含超时 + sync->async 包装）
        5. 执行 after hook 链
        6. 生成 COMPLETED/FAILED 事件
        7. 返回 ToolResult

        Args:
            tool_name: 目标工具名称
            args: 调用参数
            context: 执行上下文

        Returns:
            ToolResult 结构化结果
        """
        start_time = time.monotonic()

        # 步骤 1: 查找工具
        entry = self._registry.get(tool_name)
        if entry is None:
            return ToolResult(
                output="",
                is_error=True,
                error=f"Tool '{tool_name}' not found",
                duration=0.0,
                tool_name=tool_name,
            )

        meta, handler = entry

        # Feature 061: 权限检查完全由 Hook Chain 驱动
        # （PresetBeforeHook + ApprovalOverrideHook）
        # 不再硬编码 profile_allows() 和 FR-010a 强制拒绝。

        # 步骤 2: 生成 TOOL_CALL_STARTED 事件
        started_ok = await self._emit_started_event(
            tool_name=tool_name, meta=meta, args=args, context=context
        )
        if not started_ok:
            duration = time.monotonic() - start_time
            return ToolResult(
                output="",
                is_error=True,
                error="TOOL_CALL_STARTED 事件写入失败，已拒绝执行以避免无审计副作用",
                duration=duration,
                tool_name=tool_name,
            )

        # 步骤 3: 执行 before hook 链（含 PresetBeforeHook 权限检查）
        current_args = dict(args)
        for hook in self._before_hooks:
            try:
                hook_result = await hook.before_execute(meta, current_args, context)
                if not hook_result.proceed:
                    # Hook 拒绝执行
                    duration = time.monotonic() - start_time
                    reason = hook_result.rejection_reason or f"Rejected by hook '{hook.name}'"
                    await self._emit_failed_event(
                        tool_name=tool_name,
                        context=context,
                        duration=duration,
                        error_type="rejection",
                        error_message=reason,
                    )
                    return ToolResult(
                        output="",
                        is_error=True,
                        error=reason,
                        duration=duration,
                        tool_name=tool_name,
                    )
                # 应用修改后的参数
                if hook_result.modified_args is not None:
                    current_args = hook_result.modified_args
            except Exception as e:
                if hook.fail_mode == FailMode.CLOSED:
                    # fail-closed: 拒绝执行
                    duration = time.monotonic() - start_time
                    error_msg = f"Before hook '{hook.name}' failed (fail_mode=closed): {e}"
                    await self._emit_failed_event(
                        tool_name=tool_name,
                        context=context,
                        duration=duration,
                        error_type="hook_failure",
                        error_message=error_msg,
                    )
                    return ToolResult(
                        output="",
                        is_error=True,
                        error=error_msg,
                        duration=duration,
                        tool_name=tool_name,
                    )
                else:
                    # fail-open: 记录警告并继续
                    logger.warning(
                        "before_hook_failed_open",
                        hook_name=hook.name,
                        error=str(e),
                    )

        # 步骤 4: 执行工具
        try:
            raw_output = await self._invoke_handler(handler, current_args, meta.timeout_seconds)
            output_str = str(raw_output) if raw_output is not None else ""
            duration = time.monotonic() - start_time
            result = ToolResult(
                output=output_str,
                is_error=False,
                duration=duration,
                tool_name=tool_name,
            )
        except TimeoutError:
            duration = time.monotonic() - start_time
            await self._emit_failed_event(
                tool_name=tool_name,
                context=context,
                duration=duration,
                error_type="timeout",
                error_message=f"Tool '{tool_name}' timed out after {meta.timeout_seconds}s",
            )
            return ToolResult(
                output="",
                is_error=True,
                error=f"Tool '{tool_name}' timed out after {meta.timeout_seconds}s",
                duration=duration,
                tool_name=tool_name,
            )
        except Exception as e:
            duration = time.monotonic() - start_time
            await self._emit_failed_event(
                tool_name=tool_name,
                context=context,
                duration=duration,
                error_type="exception",
                error_message=str(e),
            )
            return ToolResult(
                output="",
                is_error=True,
                error=str(e),
                duration=duration,
                tool_name=tool_name,
            )

        # 步骤 5: 执行 after hook 链
        for hook in self._after_hooks:
            try:
                result = await hook.after_execute(meta, result, context)
            except Exception as e:
                if hook.fail_mode == FailMode.CLOSED:
                    # after hook fail-closed: 标记错误但返回结果
                    logger.error(
                        "after_hook_failed_closed",
                        hook_name=hook.name,
                        error=str(e),
                    )
                    result = ToolResult(
                        output=result.output,
                        is_error=True,
                        error=f"After hook '{hook.name}' failed (fail_mode=closed): {e}",
                        duration=result.duration,
                        tool_name=tool_name,
                    )
                else:
                    # fail-open: log-and-continue（FR-022）
                    logger.warning(
                        "after_hook_failed_open",
                        hook_name=hook.name,
                        error=str(e),
                    )

        # 步骤 6: 生成 COMPLETED 事件（如果未出错）
        if not result.is_error:
            await self._emit_completed_event(
                tool_name=tool_name,
                context=context,
                duration=result.duration,
                output_summary=result.output[:200] if result.output else "",
                truncated=result.truncated,
                artifact_ref=result.artifact_ref,
            )

        return result

    # ============================================================
    # 内部方法
    # ============================================================

    def _has_policy_checkpoint(self) -> bool:
        """检查是否有注册的 PolicyCheckpoint hook

        通过检查 before_hooks 中是否有 fail_mode=closed 的 hook 来判断。
        PolicyCheckpoint 强制 fail_mode=closed。
        """
        return any(h.fail_mode == FailMode.CLOSED for h in self._before_hooks)

    async def _invoke_handler(
        self,
        handler: Callable[..., Any],
        args: dict[str, Any],
        timeout_seconds: float | None,
    ) -> Any:
        """执行工具处理函数（含超时和 sync->async 包装）

        Args:
            handler: 工具处理函数
            args: 调用参数
            timeout_seconds: 超时秒数

        Returns:
            工具输出
        """
        # FR-013: sync 函数自动 async 包装
        if inspect.iscoroutinefunction(handler):
            coro = handler(**args)
        else:
            coro = asyncio.to_thread(handler, **args)

        # FR-012: 声明式超时控制
        if timeout_seconds is not None:
            return await asyncio.wait_for(coro, timeout=timeout_seconds)
        else:
            return await coro

    async def _emit_started_event(
        self,
        tool_name: str,
        meta: ToolMeta,
        args: dict[str, Any],
        context: ExecutionContext,
    ) -> bool:
        """生成 TOOL_CALL_STARTED 事件"""
        try:
            # FR-015: 参数摘要经过脱敏处理
            raw_payload = ToolCallStartedPayload(
                tool_name=tool_name,
                tool_group=meta.tool_group,
                side_effect_level=meta.side_effect_level.value,
                args_summary=str(args)[:200],
                agent_runtime_id=context.agent_runtime_id,
                agent_session_id=context.agent_session_id,
                work_id=context.work_id,
                timeout_seconds=meta.timeout_seconds,
            ).model_dump()
            sanitized_payload = sanitize_for_event(raw_payload)
            event = Event(
                event_id=str(ULID()),
                task_id=context.task_id,
                task_seq=await self._event_store.get_next_task_seq(context.task_id),
                ts=datetime.now(),
                type=EventType.TOOL_CALL_STARTED,
                actor=ActorType.TOOL,
                payload=sanitized_payload,
                trace_id=context.trace_id,
            )
            await self._persist_event(event)
            return True
        except Exception as e:
            logger.error("failed_to_emit_started_event", error=str(e))
            return False

    async def _emit_completed_event(
        self,
        tool_name: str,
        context: ExecutionContext,
        duration: float,
        output_summary: str,
        truncated: bool = False,
        artifact_ref: str | None = None,
    ) -> None:
        """生成 TOOL_CALL_COMPLETED 事件"""
        try:
            # FR-015: payload 经过脱敏处理
            raw_payload = ToolCallCompletedPayload(
                tool_name=tool_name,
                duration_ms=int(duration * 1000),
                output_summary=output_summary[:200],
                agent_runtime_id=context.agent_runtime_id,
                agent_session_id=context.agent_session_id,
                work_id=context.work_id,
                truncated=truncated,
                artifact_ref=artifact_ref,
            ).model_dump()
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
            await self._persist_event(event)
        except Exception as e:
            logger.error("failed_to_emit_completed_event", error=str(e))

    async def _emit_failed_event(
        self,
        tool_name: str,
        context: ExecutionContext,
        duration: float,
        error_type: str,
        error_message: str,
    ) -> None:
        """生成 TOOL_CALL_FAILED 事件"""
        try:
            # FR-015: payload 经过脱敏处理
            raw_payload = ToolCallFailedPayload(
                tool_name=tool_name,
                duration_ms=int(duration * 1000),
                error_type=error_type,
                error_message=error_message[:500],
                agent_runtime_id=context.agent_runtime_id,
                agent_session_id=context.agent_session_id,
                work_id=context.work_id,
                recoverable=error_type != "rejection",
            ).model_dump()
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
            await self._persist_event(event)
        except Exception as e:
            logger.error("failed_to_emit_failed_event", error=str(e))

    async def _persist_event(self, event: Event) -> None:
        """持久化事件：优先使用带提交/重试的实现。"""
        append_committed = getattr(self._event_store, "append_event_committed", None)
        if callable(append_committed):
            await append_committed(event, update_task_pointer=True)
            return
        await self._event_store.append_event(event)
