"""Feature 093 Phase C / Phase A: AgentSessionTurn 写入能力 mixin。

从 AgentContextService 抽出 turn 写入路径，让 Worker session 路径与 main
session 共享同一段实现。Phase C 行为与原始实现完全一致；Phase A 在写入
成功后追加 ``AGENT_SESSION_TURN_PERSISTED`` 事件 emit，让 control_plane
可观测 main / worker session turn 写入。

依赖约定（由继承类提供）：
- ``self._stores``：StoreGroup，至少暴露 ``agent_context_store`` /
  ``event_store`` / ``conn``。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import structlog
from octoagent.core.models import (
    ActorType,
    AgentSessionTurn,
    AgentSessionTurnKind,
    EventType,
)
from octoagent.core.models.event import Event
from ulid import ULID

from .context_compaction import truncate_chars

log = structlog.get_logger(__name__)

# 没有真实 task_id 时使用的占位 task_id（与 user_profile_tools._emit_event 同模式）
_TURN_PERSISTED_AUDIT_TASK_ID = "_agent_session_turn_audit"


class AgentContextTurnWriterMixin:
    """提供 AgentSessionTurn 写入能力，由 AgentContextService 继承。

    本 mixin 不持有状态，所有 store 访问均通过 ``self._stores``，由继承
    类负责注入。方法签名、返回值与副作用与 F092 baseline 完全等价。
    """

    _stores: Any

    async def _append_agent_session_turn(
        self,
        *,
        agent_session_id: str,
        task_id: str,
        kind: AgentSessionTurnKind,
        role: str,
        summary: str,
        tool_name: str = "",
        artifact_ref: str = "",
        metadata: dict[str, Any] | None = None,
        dedupe_key: str = "",
    ) -> AgentSessionTurn | None:
        summary = str(summary).strip()
        if not agent_session_id.strip() or not summary:
            return None
        if dedupe_key.strip():
            existing = await self._stores.agent_context_store.get_agent_session_turn_by_dedupe_key(
                agent_session_id=agent_session_id,
                dedupe_key=dedupe_key,
            )
            if existing is not None:
                return existing
        turn_seq = await self._stores.agent_context_store.get_next_agent_session_turn_seq(
            agent_session_id
        )
        turn = AgentSessionTurn(
            agent_session_turn_id=str(ULID()),
            agent_session_id=agent_session_id,
            task_id=task_id,
            turn_seq=turn_seq,
            kind=kind,
            role=role,
            tool_name=tool_name,
            artifact_ref=artifact_ref,
            summary=summary,
            dedupe_key=dedupe_key,
            metadata=dict(metadata or {}),
            created_at=datetime.now(tz=UTC),
        )
        await self._stores.agent_context_store.save_agent_session_turn(turn)
        await self._emit_turn_persisted_event(turn)
        return turn

    async def _emit_turn_persisted_event(self, turn: AgentSessionTurn) -> None:
        """Feature 093 Phase A: 把 turn 持久化事件写进 EventStore。

        让 control_plane 能观测 main / worker session 的 turn 写入。``payload``
        含 ``agent_session_id`` / ``task_id`` / ``turn_seq`` / ``kind`` /
        ``agent_session_kind`` 五项；最后一项需查 AgentSession 拿 kind，便于
        control_plane 区分 main_bootstrap / worker_internal / direct_worker /
        subagent_internal。

        事务安全（Codex Phase A finding-HIGH 闭环）：emit 走
        ``event_store.append_event``（**不 commit / 不 rollback**），与外层
        ``record_tool_call_turn`` / ``record_tool_result_turn`` 末尾的
        ``self._stores.conn.commit()`` 共享同一事务一起 commit。如果改用
        ``append_event_committed`` 会 commit/rollback 整个 conn——emit 失败
        时会回滚已写入但未 commit 的 turn，把审计 drop 升级为数据 drop。

        emit 失败不阻断主路径——审计写入失败用 ``log.error`` 让 drop 显眼
        （与 ``user_profile_tools._emit_event`` 同级别）。
        """
        try:
            agent_session = await self._stores.agent_context_store.get_agent_session(
                turn.agent_session_id
            )
            agent_session_kind = (
                agent_session.kind.value if agent_session is not None else "unknown"
            )
            event_task_id = turn.task_id or _TURN_PERSISTED_AUDIT_TASK_ID
            task_seq = await self._stores.event_store.get_next_task_seq(event_task_id)
            # 与 task_service / trace_mw 一致：trace_id 形如 ``trace-{task_id}``，
            # 让同一 task 的所有 events 共享同一 trace_id（test_us4_llm_echo 不变量）
            event = Event(
                event_id=str(ULID()),
                task_id=event_task_id,
                task_seq=task_seq,
                ts=datetime.now(tz=UTC),
                type=EventType.AGENT_SESSION_TURN_PERSISTED,
                actor=ActorType.SYSTEM,
                payload={
                    "agent_session_id": turn.agent_session_id,
                    "task_id": turn.task_id,
                    "turn_seq": turn.turn_seq,
                    "kind": turn.kind.value,
                    "agent_session_kind": agent_session_kind,
                },
                trace_id=f"trace-{event_task_id}",
            )
            # 不 commit：与外层 caller 同一事务一起落盘
            await self._stores.event_store.append_event(event)
        except Exception as exc:  # noqa: BLE001
            log.error(
                "agent_session_turn_event_emit_failed",
                agent_session_id=turn.agent_session_id,
                task_id=turn.task_id,
                error_type=type(exc).__name__,
                error=str(exc),
                hint=(
                    "审计事件写入失败：control_plane 将丢失该 turn 的 "
                    "AGENT_SESSION_TURN_PERSISTED 记录"
                ),
            )

    async def record_tool_call_turn(
        self,
        *,
        agent_session_id: str,
        task_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> None:
        tool_name = str(tool_name).strip()
        if not agent_session_id.strip() or not tool_name:
            return
        summary = truncate_chars(
            f"{tool_name}({json.dumps(arguments, ensure_ascii=False, sort_keys=True)})",
            720,
        )
        await self._append_agent_session_turn(
            agent_session_id=agent_session_id,
            task_id=task_id,
            kind=AgentSessionTurnKind.TOOL_CALL,
            role="assistant",
            tool_name=tool_name,
            summary=summary,
            metadata={"arguments": dict(arguments)},
        )
        await self._stores.conn.commit()

    async def record_tool_result_turn(
        self,
        *,
        agent_session_id: str,
        task_id: str,
        tool_name: str,
        output: str,
        is_error: bool,
        error: str | None = None,
        artifact_ref: str | None = None,
        duration_ms: int = 0,
    ) -> None:
        tool_name = str(tool_name).strip()
        if not agent_session_id.strip() or not tool_name:
            return
        result_preview = output if not is_error else (error or output)
        summary = truncate_chars(" ".join(str(result_preview).split()), 720)
        if not summary:
            summary = "[empty tool result]"
        await self._append_agent_session_turn(
            agent_session_id=agent_session_id,
            task_id=task_id,
            kind=AgentSessionTurnKind.TOOL_RESULT,
            role="tool",
            tool_name=tool_name,
            artifact_ref=str(artifact_ref or "").strip(),
            summary=summary,
            metadata={
                "is_error": bool(is_error),
                "error": str(error or "").strip(),
                "duration_ms": int(duration_ms or 0),
            },
        )
        await self._stores.conn.commit()
