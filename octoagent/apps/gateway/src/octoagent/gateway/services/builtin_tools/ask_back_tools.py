"""F099 Phase B: ask_back 三工具（worker.ask_back / worker.request_input / worker.escalate_permission）。

三工具均在 Worker 上下文中调用，允许 Worker 向调用方（用户/主 Agent/Automation）反问或申请权限。

设计决策（GATE_DESIGN 锁定）：
- OD-F099-1 B: 复用 CONTROL_METADATA_UPDATED + ControlMetadataUpdatedPayload 承载 audit（不新建 event type）
- OD-F099-2 B: 三工具各自独立 handler（不继承 BaseDelegation）
- OD-F099-4 B: 所有 agent kind 均可调用（worker. 前缀是惯例，非访问控制）
- OD-F099-5 A: escalate_permission 复用 ApprovalGate SSE 路径（P-VAL-1 确认超时机制）
- OD-F099-7 A: ask_back / request_input 走 tool_result 路径（execution_context.request_input()）

FR-B1: ask_back 不 raise（任何异常 → 返回空字符串）
FR-B2: request_input 返回用户输入文本
FR-B3: escalate_permission 不 raise（approved/rejected/timeout 均 return 字符串）
FR-B4: 三工具均 emit CONTROL_METADATA_UPDATED（Constitution C2 合规）
FR-B5: ask_back 描述向 LLM 说明"向当前工作来源提问"
FR-B6: entrypoints 含 "agent_runtime"

Constitution 合规：
- C4 Side-effect Must be Two-Phase: escalate_permission 通过 ApprovalGate 走用户审批
- C6 Degrade Gracefully: approval_gate is None → 降级返回 "rejected"（不 raise）
- C7 User-in-Control: ask_back 在 WAITING_INPUT 状态等待用户输入
- C10 Policy-Driven Access: 通过 _ENTRYPOINTS 限制访问入口
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from pydantic import BaseModel

from octoagent.core.models.enums import ActorType, EventType, TaskStatus
from octoagent.core.models.event import Event, EventCausality
from octoagent.core.models.payloads import ControlMetadataUpdatedPayload
from octoagent.core.models.source_kinds import (
    CONTROL_METADATA_SOURCE_ASK_BACK,
    CONTROL_METADATA_SOURCE_ESCALATE_PERMISSION,
    CONTROL_METADATA_SOURCE_REQUEST_INPUT,
)
from octoagent.gateway.harness.tool_registry import ToolEntry
from octoagent.gateway.harness.tool_registry import register as _registry_register
from octoagent.tooling import SideEffectLevel, reflect_tool_schema, tool_contract

from ._deps import ToolDeps
from ..execution_context import get_current_execution_context

log = structlog.get_logger(__name__)

# 三工具共用 entrypoints（FR-B6 / OD-F099-4）
# agent_runtime: Worker LLM 调用；web: 管理台（Phase 3 开放）
_ENTRYPOINTS = frozenset({"agent_runtime", "web"})


# ---------------------------------------------------------------------------
# 内部辅助：_emit_ask_back_audit
# ---------------------------------------------------------------------------


async def _emit_ask_back_audit(
    deps: ToolDeps,
    source: str,
    control_metadata: dict[str, Any],
    tool_name: str = "",
) -> None:
    """emit CONTROL_METADATA_UPDATED 事件（FR-B4 / Constitution C2）。

    OD-F099-1 B 落实：复用已有 CONTROL_METADATA_UPDATED + ControlMetadataUpdatedPayload。

    F099 Codex Final F4 修复：失败路径加结构化 trace（task_id + tool_name + error），
    确保 AC-G4 audit trace 失败可观测（不 silent fail）。

    失败时 log warning 不 raise——audit emit 失败不阻断工具主流程
    （Constitution C6 Degrade Gracefully：外部系统不可用时工具仍可运行）。

    Args:
        deps: ToolDeps 共享依赖容器（获取 task_id + event_store）。
        source: 事件来源操作字符串（CONTROL_METADATA_SOURCE_* 常量）。
        control_metadata: 要承载的元数据（ask_back_question 等）。
        tool_name: 调用方工具名称（用于失败 trace，AC-G4 可观测性）。
    """
    task_id: str = "unknown"
    try:
        # 从当前 execution context 获取 task_id（工具在 worker 调用链中运行时有效）
        exec_ctx = get_current_execution_context()
        task_id = exec_ctx.task_id
    except RuntimeError:
        # 测试环境或非 worker context → log warning 不 raise（AC-G4：可观测失败）
        log.warning(
            "ask_back_audit_no_execution_context",
            source=source,
            tool_name=tool_name,
            hint="execution_context 不可用，跳过 CONTROL_METADATA_UPDATED emit（AC-G4 降级）",
        )
        return

    try:
        event_store = deps.stores.event_store
        next_seq = await event_store.get_next_task_seq(task_id)

        # 构建幂等 key（防止重放 emit 重复写入）
        idempotency_key = f"{source}:{task_id}:{next_seq}"

        audit_event = Event(
            event_id=str(uuid.uuid4()).replace("-", ""),
            task_id=task_id,
            task_seq=next_seq,
            ts=datetime.now(tz=UTC),
            type=EventType.CONTROL_METADATA_UPDATED,
            actor=ActorType.SYSTEM,
            payload=ControlMetadataUpdatedPayload(
                source=source,
                control_metadata=control_metadata,
            ).model_dump(),
            trace_id=f"trace-{task_id}",
            causality=EventCausality(idempotency_key=idempotency_key),
        )

        await event_store.append_event_committed(audit_event, update_task_pointer=False)

        log.debug(
            "ask_back_audit_emitted",
            source=source,
            task_id=task_id,
            tool_name=tool_name,
            event_id=audit_event.event_id,
        )

    except Exception as exc:
        # emit 失败不阻断工具主流程（Constitution C6）
        # F099 Codex Final F4 修复：加结构化 task_id + tool_name + error（AC-G4 可观测）
        log.warning(
            "ask_back_audit_emit_failed",
            source=source,
            task_id=task_id,
            tool_name=tool_name,
            error=str(exc),
            hint="CONTROL_METADATA_UPDATED emit 失败，AC-G4 audit trace 缺失",
        )


# ---------------------------------------------------------------------------
# T-B-2: worker.ask_back handler
# ---------------------------------------------------------------------------


async def register(broker: Any, deps: ToolDeps) -> None:
    """注册三工具（ask_back / request_input / escalate_permission）。"""

    @tool_contract(
        name="worker.ask_back",
        side_effect_level=SideEffectLevel.REVERSIBLE,
        tool_group="delegation",
        tags=["ask_back", "worker", "input", "delegation"],
        manifest_ref="builtin://worker.ask_back",
        metadata={
            "entrypoints": list(_ENTRYPOINTS),
        },
    )
    async def ask_back_handler(question: str, context: str = "") -> str:
        """向当前工作来源（用户/主 Agent）提问，等待回答后继续执行。

        当 Worker 在执行任务中需要额外信息时调用此工具。工具将：
        1. emit CONTROL_METADATA_UPDATED 审计事件（FR-B4）
        2. 发起 request_input，使任务进入 WAITING_INPUT 状态（FR-B1）
        3. 等待来源提供回答后恢复执行，将回答作为 tool_result 返回

        调用方提示：如果不确定问题来源，请使用 context 参数说明当前任务背景。

        Args:
            question: 要向来源提问的问题文本（MUST 清晰具体）。
            context: 可选：当前任务背景信息，帮助来源理解问题背景。

        Returns:
            来源提供的回答文本（字符串）。若发生异常则返回空字符串（FR-B1 不 raise）。
        """
        try:
            # F099 Codex Final F5 修复：在入口 guard 检查 task.status == RUNNING
            # 防止非 RUNNING task 创建无法兑现的 waiter（F5 / Codex Domain 2）
            try:
                _guard_ctx = get_current_execution_context()
                if getattr(_guard_ctx, "is_caller_worker", False):
                    _guard_task = await deps.stores.task_store.get_task(_guard_ctx.task_id)
                    if _guard_task is not None and _guard_task.status != TaskStatus.RUNNING:
                        log.warning(
                            "ask_back_called_in_non_running_state",
                            task_id=_guard_ctx.task_id,
                            status=str(_guard_task.status),
                            tool_name="worker.ask_back",
                        )
                        return ""
            except Exception:
                pass  # 无 execution_context 或 task_store 不可用 → guard 跳过，继续原有流程

            # FR-B4: emit CONTROL_METADATA_UPDATED（source=worker_ask_back）
            await _emit_ask_back_audit(
                deps,
                source=CONTROL_METADATA_SOURCE_ASK_BACK,
                control_metadata={
                    "ask_back_question": question,
                    "ask_back_context": context,
                    "created_at": datetime.now(tz=UTC).isoformat(),
                },
                tool_name="worker.ask_back",
            )

            # OD-F099-7 A: tool_result 路径——调用 execution_context.request_input()
            # 任务状态: RUNNING → WAITING_INPUT → RUNNING（状态机由 task_runner 管理）
            exec_ctx = get_current_execution_context()
            result = await exec_ctx.request_input(
                prompt=question,
                # approval_required=False: ask_back 不走 ApprovalGate（FR-B1 语义不同）
                approval_required=False,
            )
            return result or ""

        except Exception as exc:
            # FR-B1: 不 raise——任何异常（包括 request_input 失败）均返回空字符串
            log.warning(
                "ask_back_handler_error",
                error=str(exc),
                question_preview=question[:100] if question else "",
            )
            return ""

    _registry_register(
        ToolEntry(
            name="worker.ask_back",
            description="向当前工作来源（用户/主 Agent）提问，等待回答后继续执行",
            schema=BaseModel,
            entrypoints=_ENTRYPOINTS,
            toolset="core",
            handler=ask_back_handler,
            side_effect_level=SideEffectLevel.REVERSIBLE,
        )
    )
    await broker.try_register(reflect_tool_schema(ask_back_handler), ask_back_handler)

    # ---------------------------------------------------------------------------
    # T-B-3: worker.request_input handler
    # ---------------------------------------------------------------------------

    @tool_contract(
        name="worker.request_input",
        side_effect_level=SideEffectLevel.REVERSIBLE,
        tool_group="delegation",
        tags=["request_input", "worker", "input", "delegation"],
        manifest_ref="builtin://worker.request_input",
        metadata={
            "entrypoints": list(_ENTRYPOINTS),
        },
    )
    async def request_input_handler(prompt: str, expected_format: str = "") -> str:
        """请求额外结构化输入，等待来源提供后继续执行。

        与 worker.ask_back 类似，但语义为"请求结构化额外输入"——
        适合需要特定格式数据（JSON/配置/代码片段）的场景。

        Args:
            prompt: 请求输入的提示文本（MUST 清晰说明需要什么）。
            expected_format: 可选：期望的输入格式描述（如 "JSON", "Python 代码" 等）。

        Returns:
            来源提供的输入文本（FR-B2）。若发生异常则返回空字符串。
        """
        try:
            # F099 Codex Final F5 修复：在入口 guard 检查 task.status == RUNNING
            try:
                _guard_ctx = get_current_execution_context()
                if getattr(_guard_ctx, "is_caller_worker", False):
                    _guard_task = await deps.stores.task_store.get_task(_guard_ctx.task_id)
                    if _guard_task is not None and _guard_task.status != TaskStatus.RUNNING:
                        log.warning(
                            "request_input_called_in_non_running_state",
                            task_id=_guard_ctx.task_id,
                            status=str(_guard_task.status),
                            tool_name="worker.request_input",
                        )
                        return ""
            except Exception:
                pass  # 无 execution_context 或 task_store 不可用 → guard 跳过

            # FR-B4: emit CONTROL_METADATA_UPDATED（source=worker_request_input）
            await _emit_ask_back_audit(
                deps,
                source=CONTROL_METADATA_SOURCE_REQUEST_INPUT,
                control_metadata={
                    "request_input_prompt": prompt,
                    "expected_format": expected_format,
                    "created_at": datetime.now(tz=UTC).isoformat(),
                },
                tool_name="worker.request_input",
            )

            # OD-F099-7 A: tool_result 路径——组合 prompt + expected_format
            full_prompt = prompt
            if expected_format:
                full_prompt = f"{prompt}\n期望格式：{expected_format}"

            exec_ctx = get_current_execution_context()
            result = await exec_ctx.request_input(
                prompt=full_prompt,
                approval_required=False,
            )
            return result or ""

        except Exception as exc:
            log.warning(
                "request_input_handler_error",
                error=str(exc),
                prompt_preview=prompt[:100] if prompt else "",
            )
            return ""

    _registry_register(
        ToolEntry(
            name="worker.request_input",
            description="请求额外结构化输入，等待来源提供后继续执行",
            schema=BaseModel,
            entrypoints=_ENTRYPOINTS,
            toolset="core",
            handler=request_input_handler,
            side_effect_level=SideEffectLevel.REVERSIBLE,
        )
    )
    await broker.try_register(reflect_tool_schema(request_input_handler), request_input_handler)

    # ---------------------------------------------------------------------------
    # T-B-4: worker.escalate_permission handler
    # ---------------------------------------------------------------------------

    @tool_contract(
        name="worker.escalate_permission",
        side_effect_level=SideEffectLevel.IRREVERSIBLE,
        tool_group="delegation",
        tags=["escalate_permission", "worker", "approval", "delegation"],
        manifest_ref="builtin://worker.escalate_permission",
        metadata={
            "entrypoints": list(_ENTRYPOINTS),
        },
    )
    async def escalate_permission_handler(action: str, scope: str, reason: str) -> str:
        """申请执行某项需要用户授权的操作，等待用户审批后继续。

        当 Worker 需要执行敏感或不可逆操作（如写入文件、调用外部 API、修改配置）
        时调用此工具向用户申请权限。

        Constitution C4 合规：不可逆操作必须 Plan → Gate → Execute。
        Constitution C7 合规：高风险动作必须可审批。

        Args:
            action: 请求授权的动作描述（如 "写入 /etc/hosts 文件"）。
            scope: 影响范围（如 "系统级文件", "生产环境", "用户数据"）。
            reason: 申请原因和必要性说明（帮助用户做出授权决策）。

        Returns:
            "approved" 表示用户授权；"rejected" 表示用户拒绝或超时。
            不会 raise（FR-B3）。
        """
        try:
            # F099 Codex Final F5 修复：在入口 guard 检查 task.status == RUNNING
            try:
                _guard_ctx = get_current_execution_context()
                if getattr(_guard_ctx, "is_caller_worker", False):
                    _guard_task = await deps.stores.task_store.get_task(_guard_ctx.task_id)
                    if _guard_task is not None and _guard_task.status != TaskStatus.RUNNING:
                        log.warning(
                            "escalate_permission_called_in_non_running_state",
                            task_id=_guard_ctx.task_id,
                            status=str(_guard_task.status),
                            tool_name="worker.escalate_permission",
                        )
                        return "rejected"
            except Exception:
                pass  # 无 execution_context 或 task_store 不可用 → guard 跳过

            # FR-D3: emit CONTROL_METADATA_UPDATED（source=worker_escalate_permission）
            await _emit_ask_back_audit(
                deps,
                source=CONTROL_METADATA_SOURCE_ESCALATE_PERMISSION,
                control_metadata={
                    "escalate_action": action,
                    "escalate_scope": scope,
                    "escalate_reason": reason,
                    "created_at": datetime.now(tz=UTC).isoformat(),
                },
                tool_name="worker.escalate_permission",
            )

            # OD-F099-5 A: 复用 ApprovalGate SSE 路径
            # Constitution C6 降级: approval_gate 不可用 → 拒绝（不 raise）
            approval_gate = getattr(deps, "_approval_gate", None)
            if approval_gate is None:
                log.warning(
                    "escalate_permission_gate_unavailable",
                    action=action,
                    scope=scope,
                    hint="approval_gate is None，降级返回 rejected（Constitution C6）",
                )
                return "rejected"

            # 从 execution context 获取 task_id 和 session_id（审批记录用）
            try:
                exec_ctx = get_current_execution_context()
                task_id_for_approval = exec_ctx.task_id
                session_id_for_approval = exec_ctx.session_id
            except RuntimeError:
                # 非 worker context → 降级
                log.warning(
                    "escalate_permission_no_context",
                    action=action,
                    hint="execution_context 不可用，降级返回 rejected",
                )
                return "rejected"

            operation_summary = (
                f"Worker 申请权限：{action}\n"
                f"影响范围：{scope}\n"
                f"申请原因：{reason}"
            )

            # request_approval：创建 ApprovalHandle + 写 APPROVAL_REQUESTED 事件 + SSE 推送
            handle = await approval_gate.request_approval(
                session_id=session_id_for_approval,
                tool_name="worker.escalate_permission",
                scan_result=None,  # 非 ThreatScanner 触发（worker 主动申请）
                operation_summary=operation_summary,
                task_id=task_id_for_approval,
            )

            # wait_for_decision: 最长 300s 等待用户决策（P-VAL-1 确认超时返回 "rejected" 不 raise）
            decision = await approval_gate.wait_for_decision(handle, timeout_seconds=300.0)
            return decision  # "approved" 或 "rejected"

        except Exception as exc:
            # FR-B3: 不 raise——任何异常均返回 "rejected"（Constitution C6 降级）
            log.warning(
                "escalate_permission_handler_error",
                error=str(exc),
                action=action,
            )
            return "rejected"

    _registry_register(
        ToolEntry(
            name="worker.escalate_permission",
            description="申请执行需要用户授权的操作，等待用户审批后继续",
            schema=BaseModel,
            entrypoints=_ENTRYPOINTS,
            toolset="core",
            handler=escalate_permission_handler,
            side_effect_level=SideEffectLevel.IRREVERSIBLE,
        )
    )
    await broker.try_register(reflect_tool_schema(escalate_permission_handler), escalate_permission_handler)
