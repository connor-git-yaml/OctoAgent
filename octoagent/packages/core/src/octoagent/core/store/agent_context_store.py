"""Feature 033: Agent context SQLite store。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import aiosqlite

from ..models.agent_context import (
    AgentProfile,
    AgentRuntime,
    AgentRuntimeRole,
    AgentRuntimeStatus,
    AgentSession,
    AgentSessionKind,
    AgentSessionStatus,
    AgentSessionTurn,
    AgentSessionTurnKind,
    BootstrapSession,
    ContextFrame,
    MemoryNamespace,
    MemoryNamespaceKind,
    OwnerProfile,
    OwnerProfileOverlay,
    RecallFrame,
    SessionContextState,
    WorkerProfile,
    WorkerProfileOriginKind,
    WorkerProfileRevision,
    WorkerProfileStatus,
)


class SqliteAgentContextStore:
    """agent profile / bootstrap / context frame 访问层。"""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def save_agent_profile(self, profile: AgentProfile) -> AgentProfile:
        await self._conn.execute(
            """
            INSERT INTO agent_profiles (
                profile_id, scope, project_id, name, persona_summary,
                instruction_overlays, model_alias, tool_profile, policy_refs,
                memory_access_policy, context_budget_policy, bootstrap_template_ids,
                metadata, version, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(profile_id) DO UPDATE SET
                scope = excluded.scope,
                project_id = excluded.project_id,
                name = excluded.name,
                persona_summary = excluded.persona_summary,
                instruction_overlays = excluded.instruction_overlays,
                model_alias = excluded.model_alias,
                tool_profile = excluded.tool_profile,
                policy_refs = excluded.policy_refs,
                memory_access_policy = excluded.memory_access_policy,
                context_budget_policy = excluded.context_budget_policy,
                bootstrap_template_ids = excluded.bootstrap_template_ids,
                metadata = excluded.metadata,
                version = excluded.version,
                updated_at = excluded.updated_at
            """,
            (
                profile.profile_id,
                profile.scope.value,
                profile.project_id,
                profile.name,
                profile.persona_summary,
                self._dump(profile.instruction_overlays),
                profile.model_alias,
                profile.tool_profile,
                self._dump(profile.policy_refs),
                self._dump(profile.memory_access_policy),
                self._dump(profile.context_budget_policy),
                self._dump(profile.bootstrap_template_ids),
                self._dump(profile.metadata),
                profile.version,
                profile.created_at.isoformat(),
                profile.updated_at.isoformat(),
            ),
        )
        return profile

    async def get_agent_profile(self, profile_id: str) -> AgentProfile | None:
        row = await self._fetchone(
            "SELECT * FROM agent_profiles WHERE profile_id = ?",
            (profile_id,),
        )
        return self._row_to_agent_profile(row) if row is not None else None

    async def list_agent_profiles(
        self,
        *,
        project_id: str | None = None,
    ) -> list[AgentProfile]:
        if project_id:
            rows = await self._fetchall(
                """
                SELECT * FROM agent_profiles
                WHERE project_id = ? OR scope = 'system'
                ORDER BY scope ASC, created_at ASC
                """,
                (project_id,),
            )
        else:
            rows = await self._fetchall(
                "SELECT * FROM agent_profiles ORDER BY scope ASC, created_at ASC",
                (),
            )
        return [self._row_to_agent_profile(row) for row in rows]

    async def save_worker_profile(self, profile: WorkerProfile) -> WorkerProfile:
        await self._conn.execute(
            """
            INSERT INTO worker_profiles (
                profile_id, scope, project_id, name, summary,
                model_alias, tool_profile, default_tool_groups,
                selected_tools, runtime_kinds, metadata, status,
                origin_kind, draft_revision, active_revision, created_at, updated_at, archived_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(profile_id) DO UPDATE SET
                scope = excluded.scope,
                project_id = excluded.project_id,
                name = excluded.name,
                summary = excluded.summary,
                model_alias = excluded.model_alias,
                tool_profile = excluded.tool_profile,
                default_tool_groups = excluded.default_tool_groups,
                selected_tools = excluded.selected_tools,
                runtime_kinds = excluded.runtime_kinds,
                metadata = excluded.metadata,
                status = excluded.status,
                origin_kind = excluded.origin_kind,
                draft_revision = excluded.draft_revision,
                active_revision = excluded.active_revision,
                updated_at = excluded.updated_at,
                archived_at = excluded.archived_at
            """,
            (
                profile.profile_id,
                profile.scope.value,
                profile.project_id,
                profile.name,
                profile.summary,
                profile.model_alias,
                profile.tool_profile,
                self._dump(profile.default_tool_groups),
                self._dump(profile.selected_tools),
                self._dump(profile.runtime_kinds),
                self._dump(profile.metadata),
                profile.status.value,
                profile.origin_kind.value,
                profile.draft_revision,
                profile.active_revision,
                profile.created_at.isoformat(),
                profile.updated_at.isoformat(),
                profile.archived_at.isoformat() if profile.archived_at else None,
            ),
        )
        return profile

    async def get_worker_profile(self, profile_id: str) -> WorkerProfile | None:
        row = await self._fetchone(
            "SELECT * FROM worker_profiles WHERE profile_id = ?",
            (profile_id,),
        )
        return self._row_to_worker_profile(row) if row is not None else None

    async def list_worker_profiles(
        self,
        *,
        project_id: str | None = None,
        include_archived: bool = True,
    ) -> list[WorkerProfile]:
        clauses: list[str] = []
        args: list[object] = []
        if project_id:
            clauses.append("(project_id = ? OR scope = 'system')")
            args.append(project_id)
        if not include_archived:
            clauses.append("status != 'archived'")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = await self._fetchall(
            f"""
            SELECT * FROM worker_profiles
            {where}
            ORDER BY scope ASC, updated_at DESC, created_at DESC
            """,
            tuple(args),
        )
        return [self._row_to_worker_profile(row) for row in rows]

    async def save_worker_profile_revision(
        self,
        revision: WorkerProfileRevision,
    ) -> WorkerProfileRevision:
        await self._conn.execute(
            """
            INSERT INTO worker_profile_revisions (
                revision_id, profile_id, revision, change_summary,
                snapshot_payload, created_by, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(revision_id) DO UPDATE SET
                profile_id = excluded.profile_id,
                revision = excluded.revision,
                change_summary = excluded.change_summary,
                snapshot_payload = excluded.snapshot_payload,
                created_by = excluded.created_by,
                created_at = excluded.created_at
            """,
            (
                revision.revision_id,
                revision.profile_id,
                revision.revision,
                revision.change_summary,
                self._dump(revision.snapshot_payload),
                revision.created_by,
                revision.created_at.isoformat(),
            ),
        )
        return revision

    async def list_worker_profile_revisions(
        self,
        profile_id: str,
    ) -> list[WorkerProfileRevision]:
        rows = await self._fetchall(
            """
            SELECT * FROM worker_profile_revisions
            WHERE profile_id = ?
            ORDER BY revision DESC, created_at DESC
            """,
            (profile_id,),
        )
        return [self._row_to_worker_profile_revision(row) for row in rows]

    async def save_owner_profile(self, profile: OwnerProfile) -> OwnerProfile:
        # Feature 082 P0：新增 last_synced_from_profile_at 字段（P2 ProfileGenerator 回填用）
        await self._conn.execute(
            """
            INSERT INTO owner_profiles (
                owner_profile_id, display_name, preferred_address, timezone, locale,
                working_style, interaction_preferences, boundary_notes,
                main_session_only_fields, metadata, version,
                last_synced_from_profile_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(owner_profile_id) DO UPDATE SET
                display_name = excluded.display_name,
                preferred_address = excluded.preferred_address,
                timezone = excluded.timezone,
                locale = excluded.locale,
                working_style = excluded.working_style,
                interaction_preferences = excluded.interaction_preferences,
                boundary_notes = excluded.boundary_notes,
                main_session_only_fields = excluded.main_session_only_fields,
                metadata = excluded.metadata,
                version = excluded.version,
                last_synced_from_profile_at = excluded.last_synced_from_profile_at,
                updated_at = excluded.updated_at
            """,
            (
                profile.owner_profile_id,
                profile.display_name,
                profile.preferred_address,
                profile.timezone,
                profile.locale,
                profile.working_style,
                self._dump(profile.interaction_preferences),
                self._dump(profile.boundary_notes),
                self._dump(profile.main_session_only_fields),
                self._dump(profile.metadata),
                profile.version,
                profile.last_synced_from_profile_at.isoformat()
                if profile.last_synced_from_profile_at is not None
                else None,
                profile.created_at.isoformat(),
                profile.updated_at.isoformat(),
            ),
        )
        return profile

    async def get_owner_profile(self, owner_profile_id: str) -> OwnerProfile | None:
        row = await self._fetchone(
            "SELECT * FROM owner_profiles WHERE owner_profile_id = ?",
            (owner_profile_id,),
        )
        return self._row_to_owner_profile(row) if row is not None else None

    async def list_owner_profiles(self) -> list[OwnerProfile]:
        rows = await self._fetchall(
            "SELECT * FROM owner_profiles ORDER BY created_at ASC",
            (),
        )
        return [self._row_to_owner_profile(row) for row in rows]

    async def save_owner_overlay(self, overlay: OwnerProfileOverlay) -> OwnerProfileOverlay:
        await self._conn.execute(
            """
            INSERT INTO owner_profile_overlays (
                owner_overlay_id, owner_profile_id, scope, project_id,
                assistant_identity_overrides, working_style_override,
                interaction_preferences_override, boundary_notes_override,
                bootstrap_template_ids, main_session_only_overrides, metadata,
                version, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(owner_overlay_id) DO UPDATE SET
                owner_profile_id = excluded.owner_profile_id,
                scope = excluded.scope,
                project_id = excluded.project_id,
                assistant_identity_overrides = excluded.assistant_identity_overrides,
                working_style_override = excluded.working_style_override,
                interaction_preferences_override = excluded.interaction_preferences_override,
                boundary_notes_override = excluded.boundary_notes_override,
                bootstrap_template_ids = excluded.bootstrap_template_ids,
                main_session_only_overrides = excluded.main_session_only_overrides,
                metadata = excluded.metadata,
                version = excluded.version,
                updated_at = excluded.updated_at
            """,
            (
                overlay.owner_overlay_id,
                overlay.owner_profile_id,
                overlay.scope.value,
                overlay.project_id,
                self._dump(overlay.assistant_identity_overrides),
                overlay.working_style_override,
                self._dump(overlay.interaction_preferences_override),
                self._dump(overlay.boundary_notes_override),
                self._dump(overlay.bootstrap_template_ids),
                self._dump(overlay.main_session_only_overrides),
                self._dump(overlay.metadata),
                overlay.version,
                overlay.created_at.isoformat(),
                overlay.updated_at.isoformat(),
            ),
        )
        return overlay

    async def get_owner_overlay(self, owner_overlay_id: str) -> OwnerProfileOverlay | None:
        row = await self._fetchone(
            "SELECT * FROM owner_profile_overlays WHERE owner_overlay_id = ?",
            (owner_overlay_id,),
        )
        return self._row_to_owner_overlay(row) if row is not None else None

    async def get_owner_overlay_for_scope(
        self,
        *,
        project_id: str,
    ) -> OwnerProfileOverlay | None:
        row = await self._fetchone(
            """
            SELECT * FROM owner_profile_overlays
            WHERE project_id = ? AND scope = 'project'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (project_id,),
        )
        return self._row_to_owner_overlay(row) if row is not None else None

    async def list_owner_overlays(
        self,
        *,
        project_id: str | None = None,
    ) -> list[OwnerProfileOverlay]:
        if project_id:
            rows = await self._fetchall(
                """
                SELECT * FROM owner_profile_overlays
                WHERE project_id = ?
                ORDER BY created_at ASC
                """,
                (project_id,),
            )
        else:
            rows = await self._fetchall(
                "SELECT * FROM owner_profile_overlays ORDER BY created_at ASC",
                (),
            )
        return [self._row_to_owner_overlay(row) for row in rows]

    async def save_bootstrap_session(self, session: BootstrapSession) -> BootstrapSession:
        await self._conn.execute(
            """
            INSERT INTO bootstrap_sessions (
                bootstrap_id, project_id, owner_profile_id,
                owner_overlay_id, agent_profile_id, status, current_step, steps,
                answers, generated_profile_ids, generated_owner_revision,
                blocking_reason, surface, metadata, created_at, updated_at, completed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(bootstrap_id) DO UPDATE SET
                project_id = excluded.project_id,
                owner_profile_id = excluded.owner_profile_id,
                owner_overlay_id = excluded.owner_overlay_id,
                agent_profile_id = excluded.agent_profile_id,
                status = excluded.status,
                current_step = excluded.current_step,
                steps = excluded.steps,
                answers = excluded.answers,
                generated_profile_ids = excluded.generated_profile_ids,
                generated_owner_revision = excluded.generated_owner_revision,
                blocking_reason = excluded.blocking_reason,
                surface = excluded.surface,
                metadata = excluded.metadata,
                updated_at = excluded.updated_at,
                completed_at = excluded.completed_at
            """,
            (
                session.bootstrap_id,
                session.project_id,
                session.owner_profile_id,
                session.owner_overlay_id,
                session.agent_profile_id,
                session.status.value,
                session.current_step,
                self._dump(session.steps),
                self._dump(session.answers),
                self._dump(session.generated_profile_ids),
                session.generated_owner_revision,
                session.blocking_reason,
                session.surface,
                self._dump(session.metadata),
                session.created_at.isoformat(),
                session.updated_at.isoformat(),
                session.completed_at.isoformat() if session.completed_at else None,
            ),
        )
        return session

    async def save_agent_runtime(self, runtime: AgentRuntime) -> AgentRuntime:
        await self._conn.execute(
            """
            INSERT INTO agent_runtimes (
                agent_runtime_id, project_id, agent_profile_id,
                worker_profile_id, role, name, persona_summary, status,
                permission_preset, role_card,
                metadata, created_at, updated_at, archived_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(agent_runtime_id) DO UPDATE SET
                project_id = excluded.project_id,
                agent_profile_id = excluded.agent_profile_id,
                worker_profile_id = excluded.worker_profile_id,
                role = excluded.role,
                name = excluded.name,
                persona_summary = excluded.persona_summary,
                status = excluded.status,
                permission_preset = excluded.permission_preset,
                role_card = excluded.role_card,
                metadata = excluded.metadata,
                updated_at = excluded.updated_at,
                archived_at = excluded.archived_at
            """,
            (
                runtime.agent_runtime_id,
                runtime.project_id,
                runtime.agent_profile_id,
                runtime.worker_profile_id,
                runtime.role.value,
                runtime.name,
                runtime.persona_summary,
                runtime.status.value,
                runtime.permission_preset,
                runtime.role_card,
                self._dump(runtime.metadata),
                runtime.created_at.isoformat(),
                runtime.updated_at.isoformat(),
                runtime.archived_at.isoformat() if runtime.archived_at else None,
            ),
        )
        return runtime

    async def get_agent_runtime(self, agent_runtime_id: str) -> AgentRuntime | None:
        row = await self._fetchone(
            "SELECT * FROM agent_runtimes WHERE agent_runtime_id = ?",
            (agent_runtime_id,),
        )
        return self._row_to_agent_runtime(row) if row is not None else None

    async def list_agent_runtimes(
        self,
        *,
        project_id: str | None = None,
        role: AgentRuntimeRole | None = None,
    ) -> list[AgentRuntime]:
        clauses: list[str] = []
        args: list[object] = []
        if project_id:
            clauses.append("project_id = ?")
            args.append(project_id)
        if role is not None:
            clauses.append("role = ?")
            args.append(role.value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = await self._fetchall(
            f"""
            SELECT * FROM agent_runtimes
            {where}
            ORDER BY updated_at DESC, created_at DESC
            """,
            tuple(args),
        )
        return [self._row_to_agent_runtime(row) for row in rows]

    async def find_active_runtime(
        self,
        *,
        project_id: str,
        role: AgentRuntimeRole,
        worker_profile_id: str = "",
        agent_profile_id: str = "",
    ) -> AgentRuntime | None:
        """按 (project, role, worker/agent profile) 查找最新活跃 Runtime。

        用于消除 composite-key fallback：Path B 在 request 没带 agent_runtime_id
        时走这里反查 Path A 已创建的 ULID runtime，而不是再建一条 composite row。
        """
        clauses = ["project_id = ?", "role = ?", "status = 'active'"]
        args: list[object] = [project_id, role.value]
        if role is AgentRuntimeRole.WORKER:
            if worker_profile_id:
                clauses.append("worker_profile_id = ?")
                args.append(worker_profile_id)
        else:
            if agent_profile_id:
                clauses.append("agent_profile_id = ?")
                args.append(agent_profile_id)
        where = " AND ".join(clauses)
        row = await self._fetchone(
            f"""
            SELECT * FROM agent_runtimes
            WHERE {where}
            ORDER BY updated_at DESC, created_at DESC
            LIMIT 1
            """,
            tuple(args),
        )
        return self._row_to_agent_runtime(row) if row is not None else None

    async def save_agent_session(self, session: AgentSession) -> AgentSession:
        # 防护 partial UNIQUE index: 同一 project 只允许一个 active main_bootstrap session。
        # 如果要创建新的 active main_bootstrap，先关闭旧的。
        if (
            session.status.value == "active"
            and session.kind.value == "main_bootstrap"
            and session.project_id
        ):
            await self._conn.execute(
                """
                UPDATE agent_sessions
                SET status = 'closed', closed_at = ?, updated_at = ?
                WHERE project_id = ? AND status = 'active' AND kind = 'main_bootstrap'
                  AND agent_session_id != ?
                """,
                (
                    session.created_at.isoformat(),
                    session.created_at.isoformat(),
                    session.project_id,
                    session.agent_session_id,
                ),
            )
        await self._conn.execute(
            """
            INSERT INTO agent_sessions (
                agent_session_id, agent_runtime_id, kind, status, project_id,
                surface, thread_id, legacy_session_id, alias,
                parent_agent_session_id, work_id, a2a_conversation_id,
                last_context_frame_id, last_recall_frame_id, recent_transcript,
                rolling_summary, metadata, created_at, updated_at, closed_at,
                parent_worker_runtime_id, memory_cursor_seq
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(agent_session_id) DO UPDATE SET
                agent_runtime_id = excluded.agent_runtime_id,
                kind = excluded.kind,
                status = excluded.status,
                project_id = excluded.project_id,
                surface = excluded.surface,
                thread_id = excluded.thread_id,
                legacy_session_id = excluded.legacy_session_id,
                alias = excluded.alias,
                parent_agent_session_id = excluded.parent_agent_session_id,
                work_id = excluded.work_id,
                a2a_conversation_id = excluded.a2a_conversation_id,
                last_context_frame_id = excluded.last_context_frame_id,
                last_recall_frame_id = excluded.last_recall_frame_id,
                recent_transcript = excluded.recent_transcript,
                rolling_summary = excluded.rolling_summary,
                metadata = excluded.metadata,
                updated_at = excluded.updated_at,
                closed_at = excluded.closed_at,
                parent_worker_runtime_id = excluded.parent_worker_runtime_id,
                memory_cursor_seq = excluded.memory_cursor_seq
            """,
            (
                session.agent_session_id,
                session.agent_runtime_id,
                session.kind.value,
                session.status.value,
                session.project_id,
                session.surface,
                session.thread_id,
                session.legacy_session_id,
                session.alias,
                session.parent_agent_session_id,
                session.work_id,
                session.a2a_conversation_id,
                session.last_context_frame_id,
                session.last_recall_frame_id,
                self._dump(session.recent_transcript),
                session.rolling_summary,
                self._dump(session.metadata),
                session.created_at.isoformat(),
                session.updated_at.isoformat(),
                session.closed_at.isoformat() if session.closed_at else None,
                session.parent_worker_runtime_id,
                session.memory_cursor_seq,
            ),
        )
        return session

    async def get_agent_session(self, agent_session_id: str) -> AgentSession | None:
        row = await self._fetchone(
            "SELECT * FROM agent_sessions WHERE agent_session_id = ?",
            (agent_session_id,),
        )
        return self._row_to_agent_session(row) if row is not None else None

    async def list_agent_sessions(
        self,
        *,
        agent_runtime_id: str | None = None,
        legacy_session_id: str | None = None,
        project_id: str | None = None,
        kind: AgentSessionKind | None = None,
        limit: int = 20,
    ) -> list[AgentSession]:
        clauses: list[str] = []
        args: list[object] = []
        if agent_runtime_id:
            clauses.append("agent_runtime_id = ?")
            args.append(agent_runtime_id)
        if legacy_session_id:
            clauses.append("legacy_session_id = ?")
            args.append(legacy_session_id)
        if project_id:
            clauses.append("project_id = ?")
            args.append(project_id)
        if kind is not None:
            clauses.append("kind = ?")
            args.append(kind.value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = await self._fetchall(
            f"""
            SELECT * FROM agent_sessions
            {where}
            ORDER BY updated_at DESC, created_at DESC
            LIMIT ?
            """,
            tuple([*args, limit]),
        )
        return [self._row_to_agent_session(row) for row in rows]

    async def close_active_sessions_for_project(self, project_id: str) -> int:
        """关闭指定 Project 的所有活跃 Session（保证 Project-Session 一一对应）。

        返回关闭的 Session 数量。
        """
        now = datetime.now(tz=UTC).isoformat()
        await self._conn.execute(
            """
            UPDATE agent_sessions
            SET status = 'closed', closed_at = ?, updated_at = ?
            WHERE project_id = ? AND status = 'active'
            """,
            (now, now, project_id),
        )
        cursor = await self._conn.execute("SELECT changes()")
        row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def get_active_session_for_project(
        self,
        project_id: str,
        kind: AgentSessionKind | None = None,
    ) -> AgentSession | None:
        """获取指定 Project 的活跃 Session。"""
        if kind is not None:
            row = await self._fetchone(
                """
                SELECT * FROM agent_sessions
                WHERE project_id = ? AND status = 'active' AND kind = ?
                ORDER BY updated_at DESC LIMIT 1
                """,
                (project_id, kind.value),
            )
        else:
            row = await self._fetchone(
                """
                SELECT * FROM agent_sessions
                WHERE project_id = ? AND status = 'active'
                ORDER BY updated_at DESC LIMIT 1
                """,
                (project_id,),
            )
        return self._row_to_agent_session(row) if row is not None else None

    async def list_subagent_sessions(
        self,
        parent_worker_runtime_id: str,
        *,
        status: AgentSessionStatus | None = None,
    ) -> list[AgentSession]:
        """列出指定 Worker 的 Subagent Session。"""
        if status is not None:
            rows = await self._fetchall(
                """
                SELECT * FROM agent_sessions
                WHERE parent_worker_runtime_id = ? AND status = ?
                ORDER BY created_at DESC
                """,
                (parent_worker_runtime_id, status.value),
            )
        else:
            rows = await self._fetchall(
                """
                SELECT * FROM agent_sessions
                WHERE parent_worker_runtime_id = ?
                ORDER BY created_at DESC
                """,
                (parent_worker_runtime_id,),
            )
        return [self._row_to_agent_session(row) for row in rows]

    async def save_agent_session_turn(self, turn: AgentSessionTurn) -> AgentSessionTurn:
        await self._conn.execute(
            """
            INSERT INTO agent_session_turns (
                agent_session_turn_id, agent_session_id, task_id, turn_seq,
                kind, role, tool_name, artifact_ref, summary, dedupe_key,
                metadata, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(agent_session_turn_id) DO UPDATE SET
                agent_session_id = excluded.agent_session_id,
                task_id = excluded.task_id,
                turn_seq = excluded.turn_seq,
                kind = excluded.kind,
                role = excluded.role,
                tool_name = excluded.tool_name,
                artifact_ref = excluded.artifact_ref,
                summary = excluded.summary,
                dedupe_key = excluded.dedupe_key,
                metadata = excluded.metadata,
                created_at = excluded.created_at
            """,
            (
                turn.agent_session_turn_id,
                turn.agent_session_id,
                turn.task_id,
                turn.turn_seq,
                turn.kind.value,
                turn.role,
                turn.tool_name,
                turn.artifact_ref,
                turn.summary,
                turn.dedupe_key,
                self._dump(turn.metadata),
                turn.created_at.isoformat(),
            ),
        )
        return turn

    async def get_agent_session_turn_by_dedupe_key(
        self,
        *,
        agent_session_id: str,
        dedupe_key: str,
    ) -> AgentSessionTurn | None:
        if not dedupe_key.strip():
            return None
        row = await self._fetchone(
            """
            SELECT * FROM agent_session_turns
            WHERE agent_session_id = ? AND dedupe_key = ?
            LIMIT 1
            """,
            (agent_session_id, dedupe_key),
        )
        return self._row_to_agent_session_turn(row) if row is not None else None

    async def get_next_agent_session_turn_seq(self, agent_session_id: str) -> int:
        row = await self._fetchone(
            """
            SELECT COALESCE(MAX(turn_seq), 0) AS max_turn_seq
            FROM agent_session_turns
            WHERE agent_session_id = ?
            """,
            (agent_session_id,),
        )
        return int(row["max_turn_seq"] or 0) + 1 if row is not None else 1

    async def list_agent_session_turns(
        self,
        *,
        agent_session_id: str,
        limit: int = 50,
    ) -> list[AgentSessionTurn]:
        rows = await self._fetchall(
            """
            SELECT * FROM agent_session_turns
            WHERE agent_session_id = ?
            ORDER BY turn_seq DESC, created_at DESC
            LIMIT ?
            """,
            (agent_session_id, limit),
        )
        return [self._row_to_agent_session_turn(row) for row in reversed(rows)]

    async def list_turns_after_seq(
        self,
        agent_session_id: str,
        after_seq: int,
        limit: int = 200,
    ) -> list[AgentSessionTurn]:
        """查询 turn_seq > after_seq 的 turns，按 turn_seq ASC 排序。

        Feature 067: Session 驱动统一记忆管线 -- 增量读取 cursor 之后的新增 turns。
        """
        rows = await self._fetchall(
            """
            SELECT * FROM agent_session_turns
            WHERE agent_session_id = ? AND turn_seq > ?
            ORDER BY turn_seq ASC
            LIMIT ?
            """,
            (agent_session_id, after_seq, limit),
        )
        return [self._row_to_agent_session_turn(row) for row in rows]

    async def update_memory_cursor(
        self,
        agent_session_id: str,
        new_cursor_seq: int,
    ) -> None:
        """更新 AgentSession 的 memory_cursor_seq。

        Feature 067: Session 驱动统一记忆管线 -- SoR 写入成功后推进游标。
        """
        await self._conn.execute(
            "UPDATE agent_sessions SET memory_cursor_seq = ? WHERE agent_session_id = ?",
            (new_cursor_seq, agent_session_id),
        )
        await self._conn.commit()

    async def delete_agent_session_turns(self, *, agent_session_id: str) -> None:
        await self._conn.execute(
            "DELETE FROM agent_session_turns WHERE agent_session_id = ?",
            (agent_session_id,),
        )

    async def save_memory_namespace(self, namespace: MemoryNamespace) -> MemoryNamespace:
        await self._conn.execute(
            """
            INSERT INTO memory_namespaces (
                namespace_id, project_id, agent_runtime_id,
                kind, name, description, memory_scope_ids, metadata,
                created_at, updated_at, archived_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(namespace_id) DO UPDATE SET
                project_id = excluded.project_id,
                agent_runtime_id = excluded.agent_runtime_id,
                kind = excluded.kind,
                name = excluded.name,
                description = excluded.description,
                memory_scope_ids = excluded.memory_scope_ids,
                metadata = excluded.metadata,
                updated_at = excluded.updated_at,
                archived_at = excluded.archived_at
            """,
            (
                namespace.namespace_id,
                namespace.project_id,
                namespace.agent_runtime_id,
                namespace.kind.value,
                namespace.name,
                namespace.description,
                self._dump(namespace.memory_scope_ids),
                self._dump(namespace.metadata),
                namespace.created_at.isoformat(),
                namespace.updated_at.isoformat(),
                namespace.archived_at.isoformat() if namespace.archived_at else None,
            ),
        )
        return namespace

    async def get_memory_namespace(self, namespace_id: str) -> MemoryNamespace | None:
        row = await self._fetchone(
            "SELECT * FROM memory_namespaces WHERE namespace_id = ?",
            (namespace_id,),
        )
        return self._row_to_memory_namespace(row) if row is not None else None

    async def list_memory_namespaces(
        self,
        *,
        project_id: str | None = None,
        agent_runtime_id: str | None = None,
        kind: MemoryNamespaceKind | None = None,
    ) -> list[MemoryNamespace]:
        clauses: list[str] = []
        args: list[object] = []
        if project_id:
            clauses.append("project_id = ?")
            args.append(project_id)
        if agent_runtime_id:
            clauses.append("agent_runtime_id = ?")
            args.append(agent_runtime_id)
        if kind is not None:
            clauses.append("kind = ?")
            args.append(kind.value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = await self._fetchall(
            f"""
            SELECT * FROM memory_namespaces
            {where}
            ORDER BY updated_at DESC, created_at DESC
            """,
            tuple(args),
        )
        return [self._row_to_memory_namespace(row) for row in rows]

    async def get_bootstrap_session(self, bootstrap_id: str) -> BootstrapSession | None:
        row = await self._fetchone(
            "SELECT * FROM bootstrap_sessions WHERE bootstrap_id = ?",
            (bootstrap_id,),
        )
        return self._row_to_bootstrap_session(row) if row is not None else None

    async def get_latest_bootstrap_session(
        self,
        *,
        project_id: str,
    ) -> BootstrapSession | None:
        if project_id:
            row = await self._fetchone(
                """
                SELECT * FROM bootstrap_sessions
                WHERE project_id = ?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (project_id,),
            )
            if row is not None:
                return self._row_to_bootstrap_session(row)
        return None

    async def list_bootstrap_sessions(
        self,
        *,
        project_id: str | None = None,
    ) -> list[BootstrapSession]:
        if project_id:
            rows = await self._fetchall(
                """
                SELECT * FROM bootstrap_sessions
                WHERE project_id = ?
                ORDER BY updated_at DESC
                """,
                (project_id,),
            )
        else:
            rows = await self._fetchall(
                "SELECT * FROM bootstrap_sessions ORDER BY updated_at DESC",
                (),
            )
        return [self._row_to_bootstrap_session(row) for row in rows]

    async def save_session_context(self, state: SessionContextState) -> SessionContextState:
        await self._conn.execute(
            """
            INSERT INTO session_context_states (
                session_id, agent_runtime_id, agent_session_id, thread_id,
                project_id, task_ids,
                recent_turn_refs, recent_artifact_refs, rolling_summary,
                summary_artifact_id, last_context_frame_id, last_recall_frame_id, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                agent_runtime_id = excluded.agent_runtime_id,
                agent_session_id = excluded.agent_session_id,
                thread_id = excluded.thread_id,
                project_id = excluded.project_id,
                task_ids = excluded.task_ids,
                recent_turn_refs = excluded.recent_turn_refs,
                recent_artifact_refs = excluded.recent_artifact_refs,
                rolling_summary = excluded.rolling_summary,
                summary_artifact_id = excluded.summary_artifact_id,
                last_context_frame_id = excluded.last_context_frame_id,
                last_recall_frame_id = excluded.last_recall_frame_id,
                updated_at = excluded.updated_at
            """,
            (
                state.session_id,
                state.agent_runtime_id,
                state.agent_session_id,
                state.thread_id,
                state.project_id,
                self._dump(state.task_ids),
                self._dump(state.recent_turn_refs),
                self._dump(state.recent_artifact_refs),
                state.rolling_summary,
                state.summary_artifact_id,
                state.last_context_frame_id,
                state.last_recall_frame_id,
                state.updated_at.isoformat(),
            ),
        )
        return state

    async def get_session_context(self, session_id: str) -> SessionContextState | None:
        row = await self._fetchone(
            "SELECT * FROM session_context_states WHERE session_id = ?",
            (session_id,),
        )
        return self._row_to_session_context(row) if row is not None else None

    async def delete_session_context(self, session_id: str) -> None:
        await self._conn.execute(
            "DELETE FROM session_context_states WHERE session_id = ?",
            (session_id,),
        )

    async def list_session_contexts(
        self,
        *,
        project_id: str | None = None,
    ) -> list[SessionContextState]:
        clauses: list[str] = []
        args: list[object] = []
        if project_id:
            clauses.append("project_id = ?")
            args.append(project_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = await self._fetchall(
            f"""
            SELECT * FROM session_context_states
            {where}
            ORDER BY updated_at DESC
            """,
            tuple(args),
        )
        return [self._row_to_session_context(row) for row in rows]

    async def save_context_frame(self, frame: ContextFrame) -> ContextFrame:
        await self._conn.execute(
            """
            INSERT INTO context_frames (
                context_frame_id, task_id, session_id, project_id,
                agent_runtime_id, agent_session_id,
                agent_profile_id, owner_profile_id, owner_overlay_id,
                owner_profile_revision, bootstrap_session_id, recall_frame_id,
                system_blocks, recent_summary, memory_namespace_ids, memory_hits,
                delegation_context, budget, degraded_reason, source_refs, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(context_frame_id) DO UPDATE SET
                task_id = excluded.task_id,
                session_id = excluded.session_id,
                project_id = excluded.project_id,
                agent_runtime_id = excluded.agent_runtime_id,
                agent_session_id = excluded.agent_session_id,
                agent_profile_id = excluded.agent_profile_id,
                owner_profile_id = excluded.owner_profile_id,
                owner_overlay_id = excluded.owner_overlay_id,
                owner_profile_revision = excluded.owner_profile_revision,
                bootstrap_session_id = excluded.bootstrap_session_id,
                recall_frame_id = excluded.recall_frame_id,
                system_blocks = excluded.system_blocks,
                recent_summary = excluded.recent_summary,
                memory_namespace_ids = excluded.memory_namespace_ids,
                memory_hits = excluded.memory_hits,
                delegation_context = excluded.delegation_context,
                budget = excluded.budget,
                degraded_reason = excluded.degraded_reason,
                source_refs = excluded.source_refs
            """,
            (
                frame.context_frame_id,
                frame.task_id,
                frame.session_id,
                frame.project_id,
                frame.agent_runtime_id,
                frame.agent_session_id,
                frame.agent_profile_id,
                frame.owner_profile_id,
                frame.owner_overlay_id,
                frame.owner_profile_revision,
                frame.bootstrap_session_id,
                frame.recall_frame_id,
                self._dump(frame.system_blocks),
                frame.recent_summary,
                self._dump(frame.memory_namespace_ids),
                self._dump(frame.memory_hits),
                self._dump(frame.delegation_context),
                self._dump(frame.budget),
                frame.degraded_reason,
                self._dump(frame.source_refs),
                frame.created_at.isoformat(),
            ),
        )
        return frame

    async def save_recall_frame(self, frame: RecallFrame) -> RecallFrame:
        await self._conn.execute(
            """
            INSERT INTO recall_frames (
                recall_frame_id, agent_runtime_id, agent_session_id,
                context_frame_id, task_id, project_id, query,
                recent_summary, memory_namespace_ids, memory_hits, source_refs,
                budget, degraded_reason, metadata, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(recall_frame_id) DO UPDATE SET
                agent_runtime_id = excluded.agent_runtime_id,
                agent_session_id = excluded.agent_session_id,
                context_frame_id = excluded.context_frame_id,
                task_id = excluded.task_id,
                project_id = excluded.project_id,
                query = excluded.query,
                recent_summary = excluded.recent_summary,
                memory_namespace_ids = excluded.memory_namespace_ids,
                memory_hits = excluded.memory_hits,
                source_refs = excluded.source_refs,
                budget = excluded.budget,
                degraded_reason = excluded.degraded_reason,
                metadata = excluded.metadata,
                created_at = excluded.created_at
            """,
            (
                frame.recall_frame_id,
                frame.agent_runtime_id,
                frame.agent_session_id,
                frame.context_frame_id,
                frame.task_id,
                frame.project_id,
                frame.query,
                frame.recent_summary,
                self._dump(frame.memory_namespace_ids),
                self._dump(frame.memory_hits),
                self._dump(frame.source_refs),
                self._dump(frame.budget),
                frame.degraded_reason,
                self._dump(frame.metadata),
                frame.created_at.isoformat(),
            ),
        )
        return frame

    async def get_context_frame(self, context_frame_id: str) -> ContextFrame | None:
        row = await self._fetchone(
            "SELECT * FROM context_frames WHERE context_frame_id = ?",
            (context_frame_id,),
        )
        return self._row_to_context_frame(row) if row is not None else None

    async def get_recall_frame(self, recall_frame_id: str) -> RecallFrame | None:
        row = await self._fetchone(
            "SELECT * FROM recall_frames WHERE recall_frame_id = ?",
            (recall_frame_id,),
        )
        return self._row_to_recall_frame(row) if row is not None else None

    async def list_context_frames(
        self,
        *,
        session_id: str | None = None,
        task_id: str | None = None,
        project_id: str | None = None,
        limit: int = 20,
    ) -> list[ContextFrame]:
        clauses: list[str] = []
        args: list[object] = []
        if session_id:
            clauses.append("session_id = ?")
            args.append(session_id)
        if task_id:
            clauses.append("task_id = ?")
            args.append(task_id)
        if project_id:
            clauses.append("project_id = ?")
            args.append(project_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = await self._fetchall(
            f"""
            SELECT * FROM context_frames
            {where}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            tuple([*args, limit]),
        )
        return [self._row_to_context_frame(row) for row in rows]

    async def list_recall_frames(
        self,
        *,
        agent_session_id: str | None = None,
        context_frame_id: str | None = None,
        task_id: str | None = None,
        project_id: str | None = None,
        limit: int = 20,
    ) -> list[RecallFrame]:
        clauses: list[str] = []
        args: list[object] = []
        if agent_session_id:
            clauses.append("agent_session_id = ?")
            args.append(agent_session_id)
        if context_frame_id:
            clauses.append("context_frame_id = ?")
            args.append(context_frame_id)
        if task_id:
            clauses.append("task_id = ?")
            args.append(task_id)
        if project_id:
            clauses.append("project_id = ?")
            args.append(project_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = await self._fetchall(
            f"""
            SELECT * FROM recall_frames
            {where}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            tuple([*args, limit]),
        )
        return [self._row_to_recall_frame(row) for row in rows]

    async def _fetchone(self, query: str, params: tuple[object, ...]) -> aiosqlite.Row | None:
        cursor = await self._conn.execute(query, params)
        return await cursor.fetchone()

    async def _fetchall(self, query: str, params: tuple[object, ...]) -> list[aiosqlite.Row]:
        cursor = await self._conn.execute(query, params)
        return await cursor.fetchall()

    @staticmethod
    def _dump(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False)

    @staticmethod
    def _load(value: str | None, default: Any) -> Any:
        if not value:
            return default
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default

    @classmethod
    def _row_to_agent_profile(cls, row: aiosqlite.Row) -> AgentProfile:
        return AgentProfile(
            profile_id=row["profile_id"],
            scope=row["scope"],
            project_id=row["project_id"],
            name=row["name"],
            persona_summary=row["persona_summary"],
            instruction_overlays=cls._load(row["instruction_overlays"], []),
            model_alias=row["model_alias"],
            tool_profile=row["tool_profile"],
            policy_refs=cls._load(row["policy_refs"], []),
            memory_access_policy=cls._load(row["memory_access_policy"], {}),
            context_budget_policy=cls._load(row["context_budget_policy"], {}),
            bootstrap_template_ids=cls._load(row["bootstrap_template_ids"], []),
            metadata=cls._load(row["metadata"], {}),
            version=row["version"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @classmethod
    def _row_to_worker_profile(cls, row: aiosqlite.Row) -> WorkerProfile:
        return WorkerProfile(
            profile_id=row["profile_id"],
            scope=row["scope"],
            project_id=row["project_id"],
            name=row["name"],
            summary=row["summary"],
            model_alias=row["model_alias"],
            tool_profile=row["tool_profile"],
            default_tool_groups=cls._load(row["default_tool_groups"], []),
            selected_tools=cls._load(row["selected_tools"], []),
            runtime_kinds=cls._load(row["runtime_kinds"], []),
            metadata=cls._load(row["metadata"], {}),
            status=WorkerProfileStatus(row["status"]),
            origin_kind=WorkerProfileOriginKind(row["origin_kind"]),
            draft_revision=row["draft_revision"],
            active_revision=row["active_revision"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            archived_at=(
                datetime.fromisoformat(row["archived_at"]) if row["archived_at"] else None
            ),
        )

    @classmethod
    def _row_to_worker_profile_revision(
        cls,
        row: aiosqlite.Row,
    ) -> WorkerProfileRevision:
        return WorkerProfileRevision(
            revision_id=row["revision_id"],
            profile_id=row["profile_id"],
            revision=row["revision"],
            change_summary=row["change_summary"],
            snapshot_payload=cls._load(row["snapshot_payload"], {}),
            created_by=row["created_by"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @classmethod
    def _row_to_agent_runtime(cls, row: aiosqlite.Row) -> AgentRuntime:
        return AgentRuntime(
            agent_runtime_id=row["agent_runtime_id"],
            project_id=row["project_id"],
            agent_profile_id=row["agent_profile_id"],
            worker_profile_id=row["worker_profile_id"],
            role=AgentRuntimeRole(row["role"]),
            name=row["name"],
            persona_summary=row["persona_summary"],
            status=AgentRuntimeStatus(row["status"]),
            permission_preset=row["permission_preset"] if "permission_preset" in row.keys() else "normal",
            role_card=row["role_card"] if "role_card" in row.keys() else "",
            metadata=cls._load(row["metadata"], {}),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            archived_at=(
                datetime.fromisoformat(row["archived_at"]) if row["archived_at"] else None
            ),
        )

    @classmethod
    def _row_to_agent_session(cls, row: aiosqlite.Row) -> AgentSession:
        return AgentSession(
            agent_session_id=row["agent_session_id"],
            agent_runtime_id=row["agent_runtime_id"],
            kind=AgentSessionKind(row["kind"]),
            status=AgentSessionStatus(row["status"]),
            project_id=row["project_id"],
            surface=row["surface"],
            thread_id=row["thread_id"],
            legacy_session_id=row["legacy_session_id"],
            alias=row["alias"] if "alias" in row.keys() else "",
            parent_agent_session_id=row["parent_agent_session_id"],
            work_id=row["work_id"],
            a2a_conversation_id=row["a2a_conversation_id"],
            last_context_frame_id=row["last_context_frame_id"],
            last_recall_frame_id=row["last_recall_frame_id"],
            recent_transcript=cls._load(
                row["recent_transcript"],
                cls._load(row["metadata"], {}).get("recent_transcript", []),
            ),
            rolling_summary=(
                str(row["rolling_summary"] or "").strip()
                or str(cls._load(row["metadata"], {}).get("rolling_summary", "")).strip()
            ),
            metadata=cls._load(row["metadata"], {}),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            closed_at=(
                datetime.fromisoformat(row["closed_at"]) if row["closed_at"] else None
            ),
            parent_worker_runtime_id=(
                row["parent_worker_runtime_id"]
                if "parent_worker_runtime_id" in row.keys()
                else ""
            ),
            memory_cursor_seq=(
                int(row["memory_cursor_seq"] or 0)
                if "memory_cursor_seq" in row.keys()
                else 0
            ),
        )

    @classmethod
    def _row_to_agent_session_turn(cls, row: aiosqlite.Row) -> AgentSessionTurn:
        return AgentSessionTurn(
            agent_session_turn_id=row["agent_session_turn_id"],
            agent_session_id=row["agent_session_id"],
            task_id=row["task_id"],
            turn_seq=int(row["turn_seq"] or 0),
            kind=AgentSessionTurnKind(row["kind"]),
            role=row["role"],
            tool_name=row["tool_name"],
            artifact_ref=row["artifact_ref"],
            summary=row["summary"],
            dedupe_key=row["dedupe_key"],
            metadata=cls._load(row["metadata"], {}),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @classmethod
    def _row_to_memory_namespace(cls, row: aiosqlite.Row) -> MemoryNamespace:
        return MemoryNamespace(
            namespace_id=row["namespace_id"],
            project_id=row["project_id"],
            agent_runtime_id=row["agent_runtime_id"],
            kind=MemoryNamespaceKind(row["kind"]),
            name=row["name"],
            description=row["description"],
            memory_scope_ids=cls._load(row["memory_scope_ids"], []),
            metadata=cls._load(row["metadata"], {}),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            archived_at=(
                datetime.fromisoformat(row["archived_at"]) if row["archived_at"] else None
            ),
        )

    @classmethod
    def _row_to_owner_profile(cls, row: aiosqlite.Row) -> OwnerProfile:
        # Feature 082 P0：last_synced_from_profile_at 可能不存在于老表（迁移前）→ 用 keys() 兜底
        last_synced_value = (
            row["last_synced_from_profile_at"]
            if "last_synced_from_profile_at" in row.keys()
            else None
        )
        return OwnerProfile(
            owner_profile_id=row["owner_profile_id"],
            display_name=row["display_name"],
            preferred_address=row["preferred_address"],
            timezone=row["timezone"],
            locale=row["locale"],
            working_style=row["working_style"],
            interaction_preferences=cls._load(row["interaction_preferences"], []),
            boundary_notes=cls._load(row["boundary_notes"], []),
            main_session_only_fields=cls._load(row["main_session_only_fields"], []),
            metadata=cls._load(row["metadata"], {}),
            version=row["version"],
            last_synced_from_profile_at=(
                datetime.fromisoformat(last_synced_value) if last_synced_value else None
            ),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @classmethod
    def _row_to_owner_overlay(cls, row: aiosqlite.Row) -> OwnerProfileOverlay:
        return OwnerProfileOverlay(
            owner_overlay_id=row["owner_overlay_id"],
            owner_profile_id=row["owner_profile_id"],
            scope=row["scope"],
            project_id=row["project_id"],
            assistant_identity_overrides=cls._load(
                row["assistant_identity_overrides"], {}
            ),
            working_style_override=row["working_style_override"],
            interaction_preferences_override=cls._load(
                row["interaction_preferences_override"], []
            ),
            boundary_notes_override=cls._load(row["boundary_notes_override"], []),
            bootstrap_template_ids=cls._load(row["bootstrap_template_ids"], []),
            main_session_only_overrides=cls._load(
                row["main_session_only_overrides"], []
            ),
            metadata=cls._load(row["metadata"], {}),
            version=row["version"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @classmethod
    def _row_to_bootstrap_session(cls, row: aiosqlite.Row) -> BootstrapSession:
        return BootstrapSession(
            bootstrap_id=row["bootstrap_id"],
            project_id=row["project_id"],
            owner_profile_id=row["owner_profile_id"],
            owner_overlay_id=row["owner_overlay_id"],
            agent_profile_id=row["agent_profile_id"],
            status=row["status"],
            current_step=row["current_step"],
            steps=cls._load(row["steps"], []),
            answers=cls._load(row["answers"], {}),
            generated_profile_ids=cls._load(row["generated_profile_ids"], []),
            generated_owner_revision=row["generated_owner_revision"],
            blocking_reason=row["blocking_reason"],
            surface=row["surface"],
            metadata=cls._load(row["metadata"], {}),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            completed_at=(
                datetime.fromisoformat(row["completed_at"])
                if row["completed_at"]
                else None
            ),
        )

    @classmethod
    def _row_to_session_context(cls, row: aiosqlite.Row) -> SessionContextState:
        return SessionContextState(
            session_id=row["session_id"],
            agent_runtime_id=row["agent_runtime_id"],
            agent_session_id=row["agent_session_id"],
            thread_id=row["thread_id"],
            project_id=row["project_id"],
            task_ids=cls._load(row["task_ids"], []),
            recent_turn_refs=cls._load(row["recent_turn_refs"], []),
            recent_artifact_refs=cls._load(row["recent_artifact_refs"], []),
            rolling_summary=row["rolling_summary"],
            summary_artifact_id=row["summary_artifact_id"],
            last_context_frame_id=row["last_context_frame_id"],
            last_recall_frame_id=row["last_recall_frame_id"],
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @classmethod
    def _row_to_context_frame(cls, row: aiosqlite.Row) -> ContextFrame:
        return ContextFrame(
            context_frame_id=row["context_frame_id"],
            task_id=row["task_id"],
            session_id=row["session_id"],
            agent_runtime_id=row["agent_runtime_id"],
            agent_session_id=row["agent_session_id"],
            project_id=row["project_id"],
            agent_profile_id=row["agent_profile_id"],
            owner_profile_id=row["owner_profile_id"],
            owner_overlay_id=row["owner_overlay_id"],
            owner_profile_revision=row["owner_profile_revision"],
            bootstrap_session_id=row["bootstrap_session_id"],
            recall_frame_id=row["recall_frame_id"],
            system_blocks=cls._load(row["system_blocks"], []),
            recent_summary=row["recent_summary"],
            memory_namespace_ids=cls._load(row["memory_namespace_ids"], []),
            memory_hits=cls._load(row["memory_hits"], []),
            delegation_context=cls._load(row["delegation_context"], {}),
            budget=cls._load(row["budget"], {}),
            degraded_reason=row["degraded_reason"],
            source_refs=cls._load(row["source_refs"], []),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @classmethod
    def _row_to_recall_frame(cls, row: aiosqlite.Row) -> RecallFrame:
        return RecallFrame(
            recall_frame_id=row["recall_frame_id"],
            agent_runtime_id=row["agent_runtime_id"],
            agent_session_id=row["agent_session_id"],
            context_frame_id=row["context_frame_id"],
            task_id=row["task_id"],
            project_id=row["project_id"],
            query=row["query"],
            recent_summary=row["recent_summary"],
            memory_namespace_ids=cls._load(row["memory_namespace_ids"], []),
            memory_hits=cls._load(row["memory_hits"], []),
            source_refs=cls._load(row["source_refs"], []),
            budget=cls._load(row["budget"], {}),
            degraded_reason=row["degraded_reason"],
            metadata=cls._load(row["metadata"], {}),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    async def delete_agent_sessions_by_ids(self, agent_session_ids: list[str]) -> int:
        """按 agent_session_id 批量删除 agent_sessions（不自动提交）。"""
        if not agent_session_ids:
            return 0
        placeholders = ",".join("?" * len(agent_session_ids))
        await self._conn.execute(
            f"DELETE FROM agent_sessions WHERE agent_session_id IN ({placeholders})",
            tuple(agent_session_ids),
        )
        cursor = await self._conn.execute("SELECT changes()")
        row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def delete_agent_session_turns_by_session_ids(self, agent_session_ids: list[str]) -> int:
        """按 agent_session_id 批量删除 turns（不自动提交）。"""
        if not agent_session_ids:
            return 0
        placeholders = ",".join("?" * len(agent_session_ids))
        await self._conn.execute(
            f"DELETE FROM agent_session_turns WHERE agent_session_id IN ({placeholders})",
            tuple(agent_session_ids),
        )
        cursor = await self._conn.execute("SELECT changes()")
        row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def delete_context_frames_by_session_id(self, session_id: str) -> int:
        """按 session_id 删除 context_frames（不自动提交）。"""
        await self._conn.execute(
            "DELETE FROM context_frames WHERE session_id = ?",
            (session_id,),
        )
        cursor = await self._conn.execute("SELECT changes()")
        row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def delete_recall_frames_by_agent_session_ids(self, agent_session_ids: list[str]) -> int:
        """按 agent_session_id 批量删除 recall_frames（不自动提交）。"""
        if not agent_session_ids:
            return 0
        placeholders = ",".join("?" * len(agent_session_ids))
        await self._conn.execute(
            f"DELETE FROM recall_frames WHERE agent_session_id IN ({placeholders})",
            tuple(agent_session_ids),
        )
        cursor = await self._conn.execute("SELECT changes()")
        row = await cursor.fetchone()
        return int(row[0]) if row else 0
