"""F113：AgentContextService 的 Session-replay 职责簇 mixin。

职责边界：会话重放投影（AgentSessionTurn → transcript 归一化 / projection 构建 /
裁剪 / 渲染）+ 响应与压缩上下文记录（record_response_context /
record_compaction_context 落 SessionContextState 与 transcript）。新增"会话
重放/transcript/响应记录"类方法放这里；turn 写入见 agent_context_turn_writer，
prompt 组装见 agent_context_prompt_assembly，防止职责再次堆回单文件。

依赖约定（由继承类 AgentContextService 提供）：
- ``self._stores``：StoreGroup
- 跨簇方法（同实例 MRO 提供）：``self._ensure_session_context`` /
  ``self._load_session_context``（entity_ensure 簇）、
  ``self._spawn_session_memory_extraction``（memory_services 簇）、
  ``self._append_unique_tail`` / ``self._summarize_turns``（prompt_assembly 簇）
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from octoagent.core.models import (
    AgentSession,
    AgentSessionTurn,
    AgentSessionTurnKind,
    ContextFrame,
    RecallFrame,
)
from octoagent.tooling.security_render import (  # F124 D2
    render_persisted_tool_turn_for_llm,
)
from ulid import ULID

# 路径不变（含 orchestrator 引用的 _dynamic_transcript_limit 等私有名）。redundant-alias
# 形式（X as X）向 ruff/类型检查器声明显式 re-export。
from .agent_context_helpers import (
    _SESSION_TRANSCRIPT_LIMIT_DEFAULT,
    SessionReplayProjection,
    _dynamic_transcript_limit,
)
from .context_compaction import (
    truncate_chars,
)

log = structlog.get_logger()


@dataclass(frozen=True)
class ResponseContextStorageRequest:
    """一次响应上下文持久化所需的不可变输入。"""

    task_id: str
    context_frame_id: str
    request_artifact_id: str
    response_artifact_id: str
    latest_user_text: str
    model_response: str
    recent_summary: str = ""
    session_lock: asyncio.Lock | None = None


@dataclass(frozen=True)
class _PrecomputedContextEntities:
    """预计算上下文帧所依赖的已解析实体。"""

    project: Any
    profile: Any
    runtime: Any
    session: AgentSession
    state: Any


class AgentContextSessionReplayMixin:
    """SessionReplay 职责簇：见模块 docstring。

    本 mixin 不持有状态，所有依赖（self._stores 等）由继承类 AgentContextService 提供。
    方法签名、返回值与副作用与拆分前完全等价（F113 行为零变更）。
    """

    _stores: Any

    async def record_response_context(
        self,
        *,
        task_id: str,
        context_frame_id: str,
        request_artifact_id: str,
        response_artifact_id: str,
        latest_user_text: str,
        model_response: str,
        recent_summary: str = "",
        session_lock: asyncio.Lock | None = None,
    ) -> None:
        self._require_runtime_mode("record_response_context")
        agent_session, project = await self.persist_response_context_storage(
            ResponseContextStorageRequest(
                task_id=task_id,
                context_frame_id=context_frame_id,
                request_artifact_id=request_artifact_id,
                response_artifact_id=response_artifact_id,
                latest_user_text=latest_user_text,
                model_response=model_response,
                recent_summary=recent_summary,
                session_lock=session_lock,
            )
        )
        if agent_session is not None:
            self._spawn_session_memory_extraction(
                agent_session=agent_session,
                project=project,
            )

    async def persist_response_context_storage(
        self,
        request: ResponseContextStorageRequest,
    ) -> tuple[AgentSession | None, Any]:
        task_id = request.task_id
        context_frame_id = request.context_frame_id
        request_artifact_id = request.request_artifact_id
        response_artifact_id = request.response_artifact_id
        latest_user_text = request.latest_user_text
        model_response = request.model_response
        recent_summary = request.recent_summary
        session_lock = request.session_lock
        task = await self._stores.task_store.get_task(task_id)
        if task is None:
            return None, None

        frame = await self._stores.agent_context_store.get_context_frame(context_frame_id)
        project = None
        state = None
        if frame is None and not context_frame_id:
            frame, project, state = await self._ensure_precomputed_context_frame(
                task=task,
                latest_user_text=latest_user_text,
            )
            context_frame_id = frame.context_frame_id
        if frame is not None:
            project = (
                await self._stores.project_store.get_project(frame.project_id)
                if frame.project_id
                else None
            )
            if frame.session_id:
                state = await self._stores.agent_context_store.get_session_context(frame.session_id)

        if state is None:
            project, _ = await self._resolve_project_scope(
                task=task,
                surface=task.requester.channel,
                project_id=frame.project_id if frame is not None else "",
            )
            state = await self._load_session_context(
                task=task,
                project=project,
                session_id_hint=frame.session_id if frame is not None else "",
            )
        if state is None:
            state = await self._ensure_session_context(
                task=task,
                project=project,
                session_id_hint=frame.session_id if frame is not None else "",
            )

        response_summary = self._summarize_turns(
            latest_user_text=latest_user_text,
            model_response=model_response,
        )
        merged_summary = recent_summary.strip() or state.rolling_summary.strip()
        if merged_summary:
            merged_summary = f"{merged_summary}\n{response_summary}".strip()
        else:
            merged_summary = response_summary
        merged_summary = merged_summary[-1800:]

        recent_artifact_refs = self._append_unique_tail(
            state.recent_artifact_refs,
            [item for item in (request_artifact_id, response_artifact_id) if item],
            limit=6,
        )
        updated = state.model_copy(
            update={
                "task_ids": self._append_unique_tail(state.task_ids, [task_id], limit=20),
                "recent_turn_refs": self._append_unique_tail(
                    state.recent_turn_refs,
                    [task_id],
                    limit=12,
                ),
                "recent_artifact_refs": recent_artifact_refs,
                "rolling_summary": merged_summary,
                "last_context_frame_id": context_frame_id,
                "last_recall_frame_id": (
                    frame.recall_frame_id if frame is not None and frame.recall_frame_id else ""
                ),
                "updated_at": datetime.now(tz=UTC),
            }
        )
        await self._stores.agent_context_store.save_session_context(updated)
        agent_session = None
        if frame is not None and frame.agent_session_id:
            # Feature 060 Phase 4: 获取 per-session 锁，防止与后台压缩并发写入 rolling_summary
            async def _update_agent_session() -> None:
                nonlocal agent_session
                agent_session = await self._stores.agent_context_store.get_agent_session(
                    frame.agent_session_id
                )
                if agent_session is not None:
                    response_preview = truncate_chars(" ".join(model_response.split()), 240)
                    await self._append_agent_session_turn(
                        agent_session_id=agent_session.agent_session_id,
                        task_id=task_id,
                        kind=AgentSessionTurnKind.USER_MESSAGE,
                        role="user",
                        summary=truncate_chars(" ".join(latest_user_text.split()), 480),
                        metadata={"source": "task_response_context"},
                        dedupe_key=(
                            f"request:{request_artifact_id}:user"
                            if request_artifact_id
                            else f"task:{task_id}:user:{response_artifact_id}"
                        ),
                    )
                    await self._append_agent_session_turn(
                        agent_session_id=agent_session.agent_session_id,
                        task_id=task_id,
                        kind=AgentSessionTurnKind.ASSISTANT_MESSAGE,
                        role="assistant",
                        summary=truncate_chars(" ".join(model_response.split()), 720),
                        artifact_ref=response_artifact_id,
                        metadata={
                            "source": "task_response_context",
                            "request_artifact_ref": request_artifact_id,
                            "response_artifact_ref": response_artifact_id,
                        },
                        dedupe_key=(
                            f"response:{response_artifact_id}:assistant"
                            if response_artifact_id
                            else f"task:{task_id}:assistant:{request_artifact_id}"
                        ),
                    )
                    replay = await self.build_agent_session_replay_projection(
                        agent_session=agent_session
                    )
                    recent_transcript = list(replay.transcript_entries)
                    if not recent_transcript:
                        recent_transcript = self._append_session_transcript_entries(
                            existing_entries=(
                                agent_session.recent_transcript
                                or agent_session.metadata.get("recent_transcript", [])
                            ),
                            task_id=task_id,
                            latest_user_text=latest_user_text,
                            model_response=model_response,
                        )
                    agent_session = agent_session.model_copy(
                        update={
                            "last_context_frame_id": context_frame_id,
                            "last_recall_frame_id": frame.recall_frame_id or "",
                            "recent_transcript": recent_transcript,
                            "rolling_summary": merged_summary,
                            "metadata": {
                                **dict(agent_session.metadata),
                                "recent_transcript": recent_transcript,
                                "rolling_summary": merged_summary,
                                "latest_model_reply_summary": response_summary,
                                "latest_model_reply_preview": (
                                    replay.latest_model_reply_preview or response_preview
                                ),
                                "session_replay_source": replay.source,
                                "session_replay_tool_lines": list(replay.tool_exchange_lines),
                                "session_replay_sanitize_notes": {
                                    "dropped_orphan_tool_calls": replay.dropped_orphan_tool_calls,
                                    "dropped_orphan_tool_results": (
                                        replay.dropped_orphan_tool_results
                                    ),
                                },
                            },
                            "updated_at": datetime.now(tz=UTC),
                        }
                    )
                    await self._stores.agent_context_store.save_agent_session(agent_session)

            if session_lock is not None:
                async with session_lock:
                    await _update_agent_session()
            else:
                await _update_agent_session()
        await self._stores.conn.commit()
        return agent_session, project

    async def _ensure_precomputed_context_frame(
        self,
        *,
        task: Any,
        latest_user_text: str,
    ) -> tuple[ContextFrame, Any, Any]:
        project, _ = await self._resolve_project_scope(
            task=task,
            surface=task.requester.channel,
        )
        state = await self._ensure_session_context(task=task, project=project)
        request = self._build_context_request(
            task=task,
            trigger_text=latest_user_text,
            dispatch_metadata={},
            worker_capability=None,
            runtime_context=None,
        )
        profile, _ = await self._resolve_agent_profile(project=project)
        runtime = await self._ensure_agent_runtime(
            request=request,
            project=project,
            agent_profile=profile,
        )
        session = await self._ensure_agent_session(
            request=request,
            task=task,
            project=project,
            agent_runtime=runtime,
            session_state=state,
        )
        frame = self._build_precomputed_context_frame(
            task=task,
            entities=_PrecomputedContextEntities(
                project=project,
                profile=profile,
                runtime=runtime,
                session=session,
                state=state,
            ),
        )
        await self._stores.agent_context_store.save_recall_frame(
            self._build_precomputed_recall_frame(
                frame=frame,
                latest_user_text=latest_user_text,
            )
        )
        await self._stores.agent_context_store.save_context_frame(frame)
        await self._stores.agent_context_store.save_agent_session(
            session.model_copy(
                update={
                    "last_context_frame_id": frame.context_frame_id,
                    "last_recall_frame_id": frame.recall_frame_id or "",
                    "updated_at": datetime.now(tz=UTC),
                }
            )
        )
        state = state.model_copy(
            update={
                "agent_runtime_id": runtime.agent_runtime_id,
                "agent_session_id": session.agent_session_id,
                "last_context_frame_id": frame.context_frame_id,
                "last_recall_frame_id": frame.recall_frame_id or "",
                "updated_at": datetime.now(tz=UTC),
            }
        )
        await self._stores.agent_context_store.save_session_context(state)
        return frame, project, state

    @staticmethod
    def _build_precomputed_context_frame(
        *,
        task: Any,
        entities: _PrecomputedContextEntities,
    ) -> ContextFrame:
        context_frame_id = str(ULID())
        return ContextFrame(
            context_frame_id=context_frame_id,
            task_id=task.task_id,
            session_id=entities.state.session_id,
            agent_runtime_id=entities.runtime.agent_runtime_id,
            agent_session_id=entities.session.agent_session_id,
            project_id=entities.project.project_id if entities.project is not None else "",
            agent_profile_id=entities.profile.profile_id,
            recall_frame_id=str(ULID()),
            recent_summary=entities.state.rolling_summary,
            degraded_reason="precomputed_storage_only",
            delegation_context={"source": "precomputed_completion"},
            budget={"memory_recall": {"mode": "skip", "hit_count": 0}},
            source_refs=[{"ref_type": "task", "ref_id": task.task_id}],
        )

    @staticmethod
    def _build_precomputed_recall_frame(
        *,
        frame: ContextFrame,
        latest_user_text: str,
    ) -> RecallFrame:
        return RecallFrame(
            recall_frame_id=frame.recall_frame_id or str(ULID()),
            agent_runtime_id=frame.agent_runtime_id,
            agent_session_id=frame.agent_session_id,
            context_frame_id=frame.context_frame_id,
            task_id=frame.task_id,
            project_id=frame.project_id,
            query=latest_user_text,
            recent_summary=frame.recent_summary,
            source_refs=list(frame.source_refs),
            budget=dict(frame.budget),
            degraded_reason=frame.degraded_reason,
            metadata={"request_kind": "precomputed", "source": "precomputed_completion"},
        )

    @staticmethod
    def _normalize_session_transcript_entries(
        raw_entries: Any,
        *,
        limit: int | None = _SESSION_TRANSCRIPT_LIMIT_DEFAULT,
    ) -> list[dict[str, str]]:
        if not isinstance(raw_entries, list):
            return []
        normalized: list[dict[str, str]] = []
        for item in raw_entries:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "")).strip()
            content = str(item.get("content", "")).strip()
            if role not in {"user", "assistant"} or not content:
                continue
            normalized.append(
                {
                    "role": role,
                    "content": content,
                    "task_id": str(item.get("task_id", "")).strip(),
                }
            )
        if limit is None:
            return normalized
        return normalized[-limit:]

    @classmethod
    def _agent_session_transcript_entries(
        cls,
        session: AgentSession | None,
    ) -> list[dict[str, str]]:
        if session is None:
            return []
        return cls._normalize_session_transcript_entries(
            session.recent_transcript or session.metadata.get("recent_transcript", [])
        )

    @staticmethod
    def _agent_session_turn_to_transcript_entry(
        turn: AgentSessionTurn,
    ) -> dict[str, str] | None:
        if turn.kind is AgentSessionTurnKind.USER_MESSAGE:
            role = "user"
        elif turn.kind is AgentSessionTurnKind.ASSISTANT_MESSAGE:
            role = "assistant"
        else:
            return None
        content = str(turn.summary).strip()
        if not content:
            return None
        return {
            "role": role,
            "content": content,
            "task_id": turn.task_id,
        }

    async def _list_agent_session_turn_transcript_entries(
        self,
        *,
        agent_session_id: str,
        limit: int = _SESSION_TRANSCRIPT_LIMIT_DEFAULT * 4,
    ) -> list[dict[str, str]]:
        if not agent_session_id.strip():
            return []
        turns = await self._stores.agent_context_store.list_agent_session_turns(
            agent_session_id=agent_session_id,
            limit=limit,
        )
        entries = [
            entry
            for turn in turns
            if (entry := self._agent_session_turn_to_transcript_entry(turn)) is not None
        ]
        return self._normalize_session_transcript_entries(entries)

    async def build_agent_session_replay_projection(
        self,
        *,
        agent_session: AgentSession | None = None,
        agent_session_id: str = "",
        turn_limit: int = _SESSION_TRANSCRIPT_LIMIT_DEFAULT * 8,
    ) -> SessionReplayProjection:
        resolved_session = agent_session
        resolved_session_id = (
            str(agent_session.agent_session_id).strip() if agent_session is not None else ""
        ) or str(agent_session_id).strip()
        if resolved_session is None and resolved_session_id:
            resolved_session = await self._stores.agent_context_store.get_agent_session(
                resolved_session_id
            )
        if not resolved_session_id:
            return SessionReplayProjection()

        turns = await self._stores.agent_context_store.list_agent_session_turns(
            agent_session_id=resolved_session_id,
            limit=max(turn_limit, _SESSION_TRANSCRIPT_LIMIT_DEFAULT * 2),
        )
        if not turns:
            transcript_entries = self._agent_session_transcript_entries(resolved_session)
            return SessionReplayProjection(
                transcript_entries=transcript_entries,
                latest_model_reply_preview=(
                    str(
                        (resolved_session.metadata if resolved_session is not None else {}).get(
                            "latest_model_reply_preview",
                            "",
                        )
                    ).strip()
                ),
                latest_context_summary=(
                    str(
                        (resolved_session.metadata if resolved_session is not None else {}).get(
                            "latest_compaction_summary",
                            "",
                        )
                    ).strip()
                    or (resolved_session.rolling_summary.strip() if resolved_session else "")
                ),
                source="agent_session_projection",
            )

        transcript_entries: list[dict[str, str]] = []
        tool_exchange_lines: list[str] = []
        pending_tool_calls: dict[str, list[AgentSessionTurn]] = {}
        latest_context_summary = ""
        latest_model_reply_preview = ""
        dropped_orphan_tool_calls = 0
        dropped_orphan_tool_results = 0
        previous_signature = ""

        for turn in turns:
            summary = self._normalize_turn_summary(turn.summary)
            signature = "|".join(
                [
                    turn.kind.value,
                    turn.role,
                    turn.tool_name,
                    turn.artifact_ref,
                    turn.dedupe_key,
                    summary,
                ]
            )
            if signature == previous_signature:
                continue
            previous_signature = signature

            if turn.kind is AgentSessionTurnKind.USER_MESSAGE:
                if summary:
                    transcript_entries.append(
                        {
                            "role": "user",
                            "content": truncate_chars(summary, 480),
                            "task_id": turn.task_id,
                        }
                    )
                continue

            if turn.kind is AgentSessionTurnKind.ASSISTANT_MESSAGE:
                if summary:
                    preview = truncate_chars(summary, 720)
                    transcript_entries.append(
                        {
                            "role": "assistant",
                            "content": preview,
                            "task_id": turn.task_id,
                        }
                    )
                    latest_model_reply_preview = preview
                continue

            if turn.kind is AgentSessionTurnKind.CONTEXT_SUMMARY:
                if summary:
                    latest_context_summary = truncate_chars(summary, 720)
                continue

            if turn.kind is AgentSessionTurnKind.TOOL_CALL:
                tool_name = str(turn.tool_name).strip() or "tool"
                pending_tool_calls.setdefault(tool_name, []).append(turn)
                continue

            if turn.kind is AgentSessionTurnKind.TOOL_RESULT:
                tool_name = str(turn.tool_name).strip() or "tool"
                queue = pending_tool_calls.get(tool_name) or []
                paired_call = queue.pop(0) if queue else None
                if not queue and tool_name in pending_tool_calls:
                    pending_tool_calls.pop(tool_name, None)
                if paired_call is None:
                    dropped_orphan_tool_results += 1
                    if summary:
                        # F124 D2：先截断内容再 render（防 [security-warning] 被截掉），
                        # 从持久化 finding 重渲染——replay 后标注不丢（FR-3.4）。
                        tool_exchange_lines.append(
                            f"- {tool_name}: "
                            + render_persisted_tool_turn_for_llm(
                                truncate_chars(summary, 200), turn.metadata
                            )
                        )
                    continue
                result_preview = summary or "[empty tool result]"
                tool_exchange_lines.append(
                    f"- {tool_name}: "
                    + render_persisted_tool_turn_for_llm(
                        truncate_chars(result_preview, 200), turn.metadata
                    )
                )

        dropped_orphan_tool_calls = sum(len(items) for items in pending_tool_calls.values())
        return SessionReplayProjection(
            transcript_entries=self._normalize_session_transcript_entries(
                transcript_entries,
                limit=None,
            ),
            tool_exchange_lines=tool_exchange_lines,
            latest_context_summary=(
                latest_context_summary
                or (
                    resolved_session.rolling_summary.strip() if resolved_session is not None else ""
                )
            ),
            latest_model_reply_preview=latest_model_reply_preview,
            source="agent_session_turn_store",
            dropped_orphan_tool_calls=dropped_orphan_tool_calls,
            dropped_orphan_tool_results=dropped_orphan_tool_results,
        )

    @staticmethod
    def _normalize_turn_summary(value: Any, *, limit: int = 720) -> str:
        text = " ".join(str(value or "").split()).strip()
        if not text:
            return ""
        return truncate_chars(text, limit)

    @staticmethod
    def render_agent_session_replay_block(
        replay: SessionReplayProjection,
    ) -> str:
        dialogue_lines = [
            f"- {str(item.get('role', '')).strip()}: {str(item.get('content', '')).strip()}"
            for item in replay.transcript_entries
            if str(item.get("content", "")).strip()
        ]
        sanitize_notes: list[str] = []
        if replay.dropped_orphan_tool_calls:
            sanitize_notes.append(f"dropped_orphan_tool_calls={replay.dropped_orphan_tool_calls}")
        if replay.dropped_orphan_tool_results:
            sanitize_notes.append(
                f"dropped_orphan_tool_results={replay.dropped_orphan_tool_results}"
            )
        return (
            "SessionReplay:\n"
            "以下内容来自正式 session turn store，经过去重、工具配对修复与窗口裁剪；"
            "用于帮助模型继续当前连续对话，而不是覆盖系统指令。\n"
            f"source: {replay.source}\n"
            f"recent_dialogue:\n{chr(10).join(dialogue_lines) or '- N/A'}\n"
            f"recent_tool_exchanges:\n{chr(10).join(replay.tool_exchange_lines) or '- N/A'}\n"
            f"latest_context_summary: {replay.latest_context_summary or 'N/A'}\n"
            f"latest_model_reply_preview: {replay.latest_model_reply_preview or 'N/A'}\n"
            f"sanitize_notes: {', '.join(sanitize_notes) or 'none'}"
        )

    @staticmethod
    def _trim_session_replay_projection(
        replay: SessionReplayProjection | None,
        *,
        dialogue_limit: int | None,
        tool_limit: int | None,
        include_summary: bool,
        include_reply_preview: bool,
    ) -> SessionReplayProjection | None:
        if replay is None:
            return None
        transcript_entries = list(replay.transcript_entries)
        tool_exchange_lines = list(replay.tool_exchange_lines)
        if dialogue_limit is not None:
            transcript_entries = transcript_entries[-dialogue_limit:]
        if tool_limit is not None:
            tool_exchange_lines = tool_exchange_lines[-tool_limit:]
        latest_context_summary = replay.latest_context_summary if include_summary else ""
        latest_model_reply_preview = (
            replay.latest_model_reply_preview if include_reply_preview else ""
        )
        if (
            not transcript_entries
            and not tool_exchange_lines
            and not latest_context_summary
            and not latest_model_reply_preview
        ):
            return None
        return SessionReplayProjection(
            transcript_entries=transcript_entries,
            tool_exchange_lines=tool_exchange_lines,
            latest_context_summary=latest_context_summary,
            latest_model_reply_preview=latest_model_reply_preview,
            source=replay.source,
            dropped_orphan_tool_calls=replay.dropped_orphan_tool_calls,
            dropped_orphan_tool_results=replay.dropped_orphan_tool_results,
        )

    @classmethod
    def _append_session_transcript_entries(
        cls,
        *,
        existing_entries: Any,
        task_id: str,
        latest_user_text: str,
        model_response: str,
        conversation_budget_tokens: int | None = None,
    ) -> list[dict[str, str]]:
        normalized = cls._normalize_session_transcript_entries(existing_entries)
        user_entry = {
            "role": "user",
            "content": truncate_chars(" ".join(latest_user_text.split()), 480),
            "task_id": task_id,
        }
        assistant_entry = {
            "role": "assistant",
            "content": truncate_chars(" ".join(model_response.split()), 720),
            "task_id": task_id,
        }
        if normalized[-2:] == [user_entry, assistant_entry]:
            return normalized[-_dynamic_transcript_limit(conversation_budget_tokens) :]
        if normalized and normalized[-1] == user_entry:
            normalized = normalized[:-1]
        if (
            len(normalized) >= 2
            and normalized[-2].get("task_id") == task_id
            and normalized[-2].get("role") == "user"
            and normalized[-1].get("task_id") == task_id
            and normalized[-1].get("role") == "assistant"
        ):
            normalized = normalized[:-2]
        normalized.extend([user_entry, assistant_entry])
        return normalized[-_dynamic_transcript_limit(conversation_budget_tokens) :]

    @classmethod
    def _replace_session_transcript_entries_from_messages(
        cls,
        *,
        messages: list[dict[str, str]],
        task_id: str,
        existing_entries: Any,
        conversation_budget_tokens: int | None = None,
    ) -> list[dict[str, str]]:
        normalized = cls._normalize_session_transcript_entries(existing_entries)
        replaced = [
            {
                "role": role,
                "content": truncate_chars(
                    " ".join(content.split()), 720 if role == "assistant" else 480
                ),
                "task_id": task_id,
            }
            for item in messages
            if (role := str(item.get("role", "")).strip()) in {"user", "assistant"}
            and (content := str(item.get("content", "")).strip())
        ]
        effective_limit = _dynamic_transcript_limit(conversation_budget_tokens)
        if not replaced:
            return normalized[-effective_limit:]
        return replaced[-effective_limit:]

    async def record_compaction_context(
        self,
        *,
        task_id: str,
        context_frame_id: str,
        summary_text: str,
        summary_artifact_id: str,
        compacted_messages: list[dict[str, str]],
        compaction_version: str = "",
        compressed_layers: list[dict[str, Any]] | None = None,
    ) -> None:
        if not context_frame_id or not summary_text.strip():
            return
        frame = await self._stores.agent_context_store.get_context_frame(context_frame_id)
        if frame is None:
            return
        if frame.session_id:
            state = await self._stores.agent_context_store.get_session_context(frame.session_id)
            if state is not None:
                updated_state = state.model_copy(
                    update={
                        "task_ids": self._append_unique_tail(state.task_ids, [task_id], limit=20),
                        "recent_turn_refs": self._append_unique_tail(
                            state.recent_turn_refs,
                            [task_id],
                            limit=12,
                        ),
                        "rolling_summary": summary_text.strip(),
                        "summary_artifact_id": summary_artifact_id,
                        "last_context_frame_id": context_frame_id,
                        "last_recall_frame_id": frame.recall_frame_id or "",
                        "updated_at": datetime.now(tz=UTC),
                    }
                )
                await self._stores.agent_context_store.save_session_context(updated_state)
        if frame.agent_session_id:
            agent_session = await self._stores.agent_context_store.get_agent_session(
                frame.agent_session_id
            )
            if agent_session is not None:
                await self._append_agent_session_turn(
                    agent_session_id=agent_session.agent_session_id,
                    task_id=task_id,
                    kind=AgentSessionTurnKind.CONTEXT_SUMMARY,
                    role="system",
                    summary=truncate_chars(summary_text.strip(), 720),
                    artifact_ref=summary_artifact_id,
                    metadata={
                        "summary_artifact_id": summary_artifact_id,
                        "source": "context_compaction",
                    },
                    dedupe_key=f"compaction:{summary_artifact_id}",
                )
                metadata = dict(agent_session.metadata)
                replay = await self.build_agent_session_replay_projection(
                    agent_session=agent_session
                )
                recent_transcript = list(replay.transcript_entries)
                if not recent_transcript:
                    recent_transcript = self._replace_session_transcript_entries_from_messages(
                        messages=compacted_messages,
                        task_id=task_id,
                        existing_entries=(
                            agent_session.recent_transcript or metadata.get("recent_transcript", [])
                        ),
                    )
                metadata.update(
                    {
                        "recent_transcript": recent_transcript,
                        "rolling_summary": summary_text.strip(),
                        "latest_compaction_summary": summary_text.strip(),
                        "latest_compaction_summary_artifact_id": summary_artifact_id,
                        "session_replay_source": replay.source,
                        "session_replay_tool_lines": list(replay.tool_exchange_lines),
                        "session_replay_sanitize_notes": {
                            "dropped_orphan_tool_calls": replay.dropped_orphan_tool_calls,
                            "dropped_orphan_tool_results": replay.dropped_orphan_tool_results,
                        },
                    }
                )
                # Feature 060 Phase 3: 持久化三层压缩状态
                if compaction_version:
                    metadata["compaction_version"] = compaction_version
                if compressed_layers is not None:
                    metadata["compressed_layers"] = compressed_layers
                await self._stores.agent_context_store.save_agent_session(
                    agent_session.model_copy(
                        update={
                            "last_context_frame_id": context_frame_id,
                            "last_recall_frame_id": frame.recall_frame_id or "",
                            "recent_transcript": recent_transcript,
                            "rolling_summary": summary_text.strip(),
                            "metadata": metadata,
                            "updated_at": datetime.now(tz=UTC),
                        }
                    )
                )
