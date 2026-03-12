"""Feature 033: Agent context SQLite store。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import aiosqlite

from ..models.agent_context import (
    AgentProfile,
    BootstrapSession,
    ContextFrame,
    OwnerProfile,
    OwnerProfileOverlay,
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
                profile_id, scope, project_id, name, summary, base_archetype,
                instruction_overlays, model_alias, tool_profile, default_tool_groups,
                selected_tools, runtime_kinds, policy_refs, tags, metadata, status,
                origin_kind, draft_revision, active_revision, created_at, updated_at, archived_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(profile_id) DO UPDATE SET
                scope = excluded.scope,
                project_id = excluded.project_id,
                name = excluded.name,
                summary = excluded.summary,
                base_archetype = excluded.base_archetype,
                instruction_overlays = excluded.instruction_overlays,
                model_alias = excluded.model_alias,
                tool_profile = excluded.tool_profile,
                default_tool_groups = excluded.default_tool_groups,
                selected_tools = excluded.selected_tools,
                runtime_kinds = excluded.runtime_kinds,
                policy_refs = excluded.policy_refs,
                tags = excluded.tags,
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
                profile.base_archetype,
                self._dump(profile.instruction_overlays),
                profile.model_alias,
                profile.tool_profile,
                self._dump(profile.default_tool_groups),
                self._dump(profile.selected_tools),
                self._dump(profile.runtime_kinds),
                self._dump(profile.policy_refs),
                self._dump(profile.tags),
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
        await self._conn.execute(
            """
            INSERT INTO owner_profiles (
                owner_profile_id, display_name, preferred_address, timezone, locale,
                working_style, interaction_preferences, boundary_notes,
                main_session_only_fields, metadata, version, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                owner_overlay_id, owner_profile_id, scope, project_id, workspace_id,
                assistant_identity_overrides, working_style_override,
                interaction_preferences_override, boundary_notes_override,
                bootstrap_template_ids, main_session_only_overrides, metadata,
                version, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(owner_overlay_id) DO UPDATE SET
                owner_profile_id = excluded.owner_profile_id,
                scope = excluded.scope,
                project_id = excluded.project_id,
                workspace_id = excluded.workspace_id,
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
                overlay.workspace_id,
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
        workspace_id: str = "",
    ) -> OwnerProfileOverlay | None:
        if workspace_id:
            row = await self._fetchone(
                """
                SELECT * FROM owner_profile_overlays
                WHERE workspace_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (workspace_id,),
            )
            if row is not None:
                return self._row_to_owner_overlay(row)
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
                bootstrap_id, project_id, workspace_id, owner_profile_id,
                owner_overlay_id, agent_profile_id, status, current_step, steps,
                answers, generated_profile_ids, generated_owner_revision,
                blocking_reason, surface, metadata, created_at, updated_at, completed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(bootstrap_id) DO UPDATE SET
                project_id = excluded.project_id,
                workspace_id = excluded.workspace_id,
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
                session.workspace_id,
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
        workspace_id: str = "",
    ) -> BootstrapSession | None:
        if workspace_id:
            row = await self._fetchone(
                """
                SELECT * FROM bootstrap_sessions
                WHERE workspace_id = ?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (workspace_id,),
            )
            if row is not None:
                return self._row_to_bootstrap_session(row)
        row = await self._fetchone(
            """
            SELECT * FROM bootstrap_sessions
            WHERE project_id = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (project_id,),
        )
        return self._row_to_bootstrap_session(row) if row is not None else None

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
                session_id, thread_id, project_id, workspace_id, task_ids,
                recent_turn_refs, recent_artifact_refs, rolling_summary,
                summary_artifact_id, last_context_frame_id, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                thread_id = excluded.thread_id,
                project_id = excluded.project_id,
                workspace_id = excluded.workspace_id,
                task_ids = excluded.task_ids,
                recent_turn_refs = excluded.recent_turn_refs,
                recent_artifact_refs = excluded.recent_artifact_refs,
                rolling_summary = excluded.rolling_summary,
                summary_artifact_id = excluded.summary_artifact_id,
                last_context_frame_id = excluded.last_context_frame_id,
                updated_at = excluded.updated_at
            """,
            (
                state.session_id,
                state.thread_id,
                state.project_id,
                state.workspace_id,
                self._dump(state.task_ids),
                self._dump(state.recent_turn_refs),
                self._dump(state.recent_artifact_refs),
                state.rolling_summary,
                state.summary_artifact_id,
                state.last_context_frame_id,
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
        workspace_id: str | None = None,
    ) -> list[SessionContextState]:
        clauses: list[str] = []
        args: list[object] = []
        if project_id:
            clauses.append("project_id = ?")
            args.append(project_id)
        if workspace_id:
            clauses.append("workspace_id = ?")
            args.append(workspace_id)
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
                context_frame_id, task_id, session_id, project_id, workspace_id,
                agent_profile_id, owner_profile_id, owner_overlay_id,
                owner_profile_revision, bootstrap_session_id, system_blocks,
                recent_summary, memory_hits, delegation_context, budget,
                degraded_reason, source_refs, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(context_frame_id) DO UPDATE SET
                task_id = excluded.task_id,
                session_id = excluded.session_id,
                project_id = excluded.project_id,
                workspace_id = excluded.workspace_id,
                agent_profile_id = excluded.agent_profile_id,
                owner_profile_id = excluded.owner_profile_id,
                owner_overlay_id = excluded.owner_overlay_id,
                owner_profile_revision = excluded.owner_profile_revision,
                bootstrap_session_id = excluded.bootstrap_session_id,
                system_blocks = excluded.system_blocks,
                recent_summary = excluded.recent_summary,
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
                frame.workspace_id,
                frame.agent_profile_id,
                frame.owner_profile_id,
                frame.owner_overlay_id,
                frame.owner_profile_revision,
                frame.bootstrap_session_id,
                self._dump(frame.system_blocks),
                frame.recent_summary,
                self._dump(frame.memory_hits),
                self._dump(frame.delegation_context),
                self._dump(frame.budget),
                frame.degraded_reason,
                self._dump(frame.source_refs),
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

    async def list_context_frames(
        self,
        *,
        session_id: str | None = None,
        task_id: str | None = None,
        project_id: str | None = None,
        workspace_id: str | None = None,
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
        if workspace_id:
            clauses.append("workspace_id = ?")
            args.append(workspace_id)
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
            base_archetype=row["base_archetype"],
            instruction_overlays=cls._load(row["instruction_overlays"], []),
            model_alias=row["model_alias"],
            tool_profile=row["tool_profile"],
            default_tool_groups=cls._load(row["default_tool_groups"], []),
            selected_tools=cls._load(row["selected_tools"], []),
            runtime_kinds=cls._load(row["runtime_kinds"], []),
            policy_refs=cls._load(row["policy_refs"], []),
            tags=cls._load(row["tags"], []),
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
    def _row_to_owner_profile(cls, row: aiosqlite.Row) -> OwnerProfile:
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
            workspace_id=row["workspace_id"],
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
            workspace_id=row["workspace_id"],
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
            thread_id=row["thread_id"],
            project_id=row["project_id"],
            workspace_id=row["workspace_id"],
            task_ids=cls._load(row["task_ids"], []),
            recent_turn_refs=cls._load(row["recent_turn_refs"], []),
            recent_artifact_refs=cls._load(row["recent_artifact_refs"], []),
            rolling_summary=row["rolling_summary"],
            summary_artifact_id=row["summary_artifact_id"],
            last_context_frame_id=row["last_context_frame_id"],
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @classmethod
    def _row_to_context_frame(cls, row: aiosqlite.Row) -> ContextFrame:
        return ContextFrame(
            context_frame_id=row["context_frame_id"],
            task_id=row["task_id"],
            session_id=row["session_id"],
            project_id=row["project_id"],
            workspace_id=row["workspace_id"],
            agent_profile_id=row["agent_profile_id"],
            owner_profile_id=row["owner_profile_id"],
            owner_overlay_id=row["owner_overlay_id"],
            owner_profile_revision=row["owner_profile_revision"],
            bootstrap_session_id=row["bootstrap_session_id"],
            system_blocks=cls._load(row["system_blocks"], []),
            recent_summary=row["recent_summary"],
            memory_hits=cls._load(row["memory_hits"], []),
            delegation_context=cls._load(row["delegation_context"], {}),
            budget=cls._load(row["budget"], {}),
            degraded_reason=row["degraded_reason"],
            source_refs=cls._load(row["source_refs"], []),
            created_at=datetime.fromisoformat(row["created_at"]),
        )
