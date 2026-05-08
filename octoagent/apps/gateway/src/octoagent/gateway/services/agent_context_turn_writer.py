"""Feature 093 Phase C: AgentSessionTurn 写入能力 mixin。

从 AgentContextService 抽出 turn 写入路径，便于后续 Worker session 路径
与 main session 共享同一段实现。行为与原始实现完全一致（行为零变更）。

依赖约定（由继承类提供）：
- ``self._stores``：StoreGroup，至少暴露 ``agent_context_store`` 与 ``conn``。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from octoagent.core.models import (
    AgentSessionTurn,
    AgentSessionTurnKind,
)
from ulid import ULID

from .context_compaction import truncate_chars


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
        return turn

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
