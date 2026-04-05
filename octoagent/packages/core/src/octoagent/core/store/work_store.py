"""Feature 030: Work / Skill Pipeline SQLite Store。"""

from __future__ import annotations

import json
from datetime import datetime

import aiosqlite

from ..models import PipelineCheckpoint, SkillPipelineRun, Work


class SqliteWorkStore:
    """works / skill_pipeline_runs / skill_pipeline_checkpoints 访问层。"""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def save_work(self, work: Work) -> Work:
        await self._conn.execute(
            """
            INSERT INTO works (
                work_id, task_id, parent_work_id, title, kind, status, target_kind,
                owner_id, requested_capability, selected_worker_type, route_reason,
                project_id, session_owner_profile_id,
                inherited_context_owner_profile_id, delegation_target_profile_id,
                turn_executor_kind, agent_profile_id, requested_worker_profile_id,
                requested_worker_profile_version, effective_worker_snapshot_id,
                context_frame_id, tool_selection_id, selected_tools, pipeline_run_id,
                delegation_id, runtime_id, retry_count, escalation_count, metadata,
                created_at, updated_at, completed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(work_id) DO UPDATE SET
                task_id = excluded.task_id,
                parent_work_id = excluded.parent_work_id,
                title = excluded.title,
                kind = excluded.kind,
                status = excluded.status,
                target_kind = excluded.target_kind,
                owner_id = excluded.owner_id,
                requested_capability = excluded.requested_capability,
                selected_worker_type = excluded.selected_worker_type,
                route_reason = excluded.route_reason,
                project_id = excluded.project_id,
                session_owner_profile_id = excluded.session_owner_profile_id,
                inherited_context_owner_profile_id = excluded.inherited_context_owner_profile_id,
                delegation_target_profile_id = excluded.delegation_target_profile_id,
                turn_executor_kind = excluded.turn_executor_kind,
                agent_profile_id = excluded.agent_profile_id,
                requested_worker_profile_id = excluded.requested_worker_profile_id,
                requested_worker_profile_version = excluded.requested_worker_profile_version,
                effective_worker_snapshot_id = excluded.effective_worker_snapshot_id,
                context_frame_id = excluded.context_frame_id,
                tool_selection_id = excluded.tool_selection_id,
                selected_tools = excluded.selected_tools,
                pipeline_run_id = excluded.pipeline_run_id,
                delegation_id = excluded.delegation_id,
                runtime_id = excluded.runtime_id,
                retry_count = excluded.retry_count,
                escalation_count = excluded.escalation_count,
                metadata = excluded.metadata,
                updated_at = excluded.updated_at,
                completed_at = excluded.completed_at
            """,
            (
                work.work_id,
                work.task_id,
                work.parent_work_id,
                work.title,
                work.kind.value,
                work.status.value,
                work.target_kind.value,
                work.owner_id,
                work.requested_capability,
                work.selected_worker_type,
                work.route_reason,
                work.project_id,
                work.session_owner_profile_id,
                work.inherited_context_owner_profile_id,
                work.delegation_target_profile_id,
                work.turn_executor_kind.value,
                work.agent_profile_id,
                work.requested_worker_profile_id,
                work.requested_worker_profile_version,
                work.effective_worker_snapshot_id,
                work.context_frame_id,
                work.tool_selection_id,
                json.dumps(work.selected_tools, ensure_ascii=False),
                work.pipeline_run_id,
                work.delegation_id,
                work.runtime_id,
                work.retry_count,
                work.escalation_count,
                json.dumps(work.metadata, ensure_ascii=False),
                work.created_at.isoformat(),
                work.updated_at.isoformat(),
                work.completed_at.isoformat() if work.completed_at else None,
            ),
        )
        return work

    async def get_work(self, work_id: str) -> Work | None:
        cursor = await self._conn.execute(
            "SELECT * FROM works WHERE work_id = ?",
            (work_id,),
        )
        row = await cursor.fetchone()
        return self._row_to_work(row) if row is not None else None

    async def list_works(
        self,
        *,
        task_id: str | None = None,
        statuses: list[str] | None = None,
        parent_work_id: str | None = None,
    ) -> list[Work]:
        clauses: list[str] = []
        args: list[object] = []
        if task_id:
            clauses.append("task_id = ?")
            args.append(task_id)
        if parent_work_id is not None:
            clauses.append("parent_work_id = ?")
            args.append(parent_work_id)
        if statuses:
            placeholders = ",".join("?" * len(statuses))
            clauses.append(f"status IN ({placeholders})")
            args.extend(statuses)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        cursor = await self._conn.execute(
            f"SELECT * FROM works {where} ORDER BY created_at DESC",
            tuple(args),
        )
        rows = await cursor.fetchall()
        return [self._row_to_work(row) for row in rows]

    async def save_pipeline_run(self, run: SkillPipelineRun) -> SkillPipelineRun:
        await self._conn.execute(
            """
            INSERT INTO skill_pipeline_runs (
                run_id, pipeline_id, task_id, work_id, status, current_node_id,
                pause_reason, retry_cursor, state_snapshot, input_request,
                approval_request, metadata, created_at, updated_at, completed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                pipeline_id = excluded.pipeline_id,
                task_id = excluded.task_id,
                work_id = excluded.work_id,
                status = excluded.status,
                current_node_id = excluded.current_node_id,
                pause_reason = excluded.pause_reason,
                retry_cursor = excluded.retry_cursor,
                state_snapshot = excluded.state_snapshot,
                input_request = excluded.input_request,
                approval_request = excluded.approval_request,
                metadata = excluded.metadata,
                updated_at = excluded.updated_at,
                completed_at = excluded.completed_at
            """,
            (
                run.run_id,
                run.pipeline_id,
                run.task_id,
                run.work_id,
                run.status.value,
                run.current_node_id,
                run.pause_reason,
                json.dumps(run.retry_cursor, ensure_ascii=False),
                json.dumps(run.state_snapshot, ensure_ascii=False),
                json.dumps(run.input_request, ensure_ascii=False),
                json.dumps(run.approval_request, ensure_ascii=False),
                json.dumps(run.metadata, ensure_ascii=False),
                run.created_at.isoformat(),
                run.updated_at.isoformat(),
                run.completed_at.isoformat() if run.completed_at else None,
            ),
        )
        return run

    async def get_pipeline_run(self, run_id: str) -> SkillPipelineRun | None:
        cursor = await self._conn.execute(
            "SELECT * FROM skill_pipeline_runs WHERE run_id = ?",
            (run_id,),
        )
        row = await cursor.fetchone()
        return self._row_to_pipeline_run(row) if row is not None else None

    async def get_pipeline_run_by_work(self, work_id: str) -> SkillPipelineRun | None:
        cursor = await self._conn.execute(
            """
            SELECT * FROM skill_pipeline_runs
            WHERE work_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (work_id,),
        )
        row = await cursor.fetchone()
        return self._row_to_pipeline_run(row) if row is not None else None

    async def list_pipeline_runs(
        self,
        *,
        task_id: str | None = None,
        work_id: str | None = None,
        pipeline_id: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 0,
    ) -> list[SkillPipelineRun]:
        clauses: list[str] = []
        args: list[object] = []
        if task_id:
            clauses.append("task_id = ?")
            args.append(task_id)
        if work_id:
            clauses.append("work_id = ?")
            args.append(work_id)
        if pipeline_id:
            clauses.append("pipeline_id = ?")
            args.append(pipeline_id)
        if status:
            clauses.append("status = ?")
            args.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        limit_clause = ""
        if page_size > 0:
            offset = (max(page, 1) - 1) * page_size
            limit_clause = f" LIMIT {page_size} OFFSET {offset}"
        cursor = await self._conn.execute(
            f"SELECT * FROM skill_pipeline_runs {where} ORDER BY created_at DESC{limit_clause}",
            tuple(args),
        )
        rows = await cursor.fetchall()
        return [self._row_to_pipeline_run(row) for row in rows]

    async def count_pipeline_runs(
        self,
        *,
        task_id: str | None = None,
        work_id: str | None = None,
        pipeline_id: str | None = None,
        status: str | None = None,
    ) -> int:
        """统计符合条件的 pipeline run 总数（分页用）。"""
        clauses: list[str] = []
        args: list[object] = []
        if task_id:
            clauses.append("task_id = ?")
            args.append(task_id)
        if work_id:
            clauses.append("work_id = ?")
            args.append(work_id)
        if pipeline_id:
            clauses.append("pipeline_id = ?")
            args.append(pipeline_id)
        if status:
            clauses.append("status = ?")
            args.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        cursor = await self._conn.execute(
            f"SELECT COUNT(*) FROM skill_pipeline_runs {where}",
            tuple(args),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def save_pipeline_checkpoint(self, checkpoint: PipelineCheckpoint) -> PipelineCheckpoint:
        await self._conn.execute(
            """
            INSERT INTO skill_pipeline_checkpoints (
                checkpoint_id, run_id, task_id, node_id, status, state_snapshot,
                side_effect_cursor, replay_summary, retry_count, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(checkpoint_id) DO UPDATE SET
                status = excluded.status,
                state_snapshot = excluded.state_snapshot,
                side_effect_cursor = excluded.side_effect_cursor,
                replay_summary = excluded.replay_summary,
                retry_count = excluded.retry_count,
                updated_at = excluded.updated_at
            """,
            (
                checkpoint.checkpoint_id,
                checkpoint.run_id,
                checkpoint.task_id,
                checkpoint.node_id,
                checkpoint.status.value,
                json.dumps(checkpoint.state_snapshot, ensure_ascii=False),
                checkpoint.side_effect_cursor,
                checkpoint.replay_summary,
                checkpoint.retry_count,
                checkpoint.created_at.isoformat(),
                checkpoint.updated_at.isoformat(),
            ),
        )
        return checkpoint

    async def get_pipeline_checkpoint(self, checkpoint_id: str) -> PipelineCheckpoint | None:
        cursor = await self._conn.execute(
            """
            SELECT * FROM skill_pipeline_checkpoints
            WHERE checkpoint_id = ?
            """,
            (checkpoint_id,),
        )
        row = await cursor.fetchone()
        return self._row_to_pipeline_checkpoint(row) if row is not None else None

    async def list_pipeline_checkpoints(self, run_id: str) -> list[PipelineCheckpoint]:
        cursor = await self._conn.execute(
            """
            SELECT * FROM skill_pipeline_checkpoints
            WHERE run_id = ?
            ORDER BY created_at ASC
            """,
            (run_id,),
        )
        rows = await cursor.fetchall()
        return [self._row_to_pipeline_checkpoint(row) for row in rows]

    async def get_latest_pipeline_checkpoint(self, run_id: str) -> PipelineCheckpoint | None:
        cursor = await self._conn.execute(
            """
            SELECT * FROM skill_pipeline_checkpoints
            WHERE run_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (run_id,),
        )
        row = await cursor.fetchone()
        return self._row_to_pipeline_checkpoint(row) if row is not None else None

    @staticmethod
    def _row_to_work(row: aiosqlite.Row) -> Work:
        return Work(
            work_id=row["work_id"],
            task_id=row["task_id"],
            parent_work_id=row["parent_work_id"],
            title=row["title"],
            kind=row["kind"],
            status=row["status"],
            target_kind=row["target_kind"],
            owner_id=row["owner_id"],
            requested_capability=row["requested_capability"],
            selected_worker_type=row["selected_worker_type"],
            route_reason=row["route_reason"],
            project_id=row["project_id"],
            workspace_id="",
            session_owner_profile_id=row["session_owner_profile_id"],
            inherited_context_owner_profile_id=row["inherited_context_owner_profile_id"],
            delegation_target_profile_id=row["delegation_target_profile_id"],
            turn_executor_kind=row["turn_executor_kind"],
            agent_profile_id=row["agent_profile_id"],
            requested_worker_profile_id=row["requested_worker_profile_id"],
            requested_worker_profile_version=row["requested_worker_profile_version"],
            effective_worker_snapshot_id=row["effective_worker_snapshot_id"],
            context_frame_id=row["context_frame_id"],
            tool_selection_id=row["tool_selection_id"],
            selected_tools=json.loads(row["selected_tools"] or "[]"),
            pipeline_run_id=row["pipeline_run_id"],
            delegation_id=row["delegation_id"],
            runtime_id=row["runtime_id"],
            retry_count=row["retry_count"],
            escalation_count=row["escalation_count"],
            metadata=json.loads(row["metadata"] or "{}"),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            completed_at=(
                datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None
            ),
        )

    @staticmethod
    def _row_to_pipeline_run(row: aiosqlite.Row) -> SkillPipelineRun:
        return SkillPipelineRun(
            run_id=row["run_id"],
            pipeline_id=row["pipeline_id"],
            task_id=row["task_id"],
            work_id=row["work_id"],
            status=row["status"],
            current_node_id=row["current_node_id"],
            pause_reason=row["pause_reason"],
            retry_cursor=json.loads(row["retry_cursor"] or "{}"),
            state_snapshot=json.loads(row["state_snapshot"] or "{}"),
            input_request=json.loads(row["input_request"] or "{}"),
            approval_request=json.loads(row["approval_request"] or "{}"),
            metadata=json.loads(row["metadata"] or "{}"),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            completed_at=(
                datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None
            ),
        )

    @staticmethod
    def _row_to_pipeline_checkpoint(row: aiosqlite.Row) -> PipelineCheckpoint:
        return PipelineCheckpoint(
            checkpoint_id=row["checkpoint_id"],
            run_id=row["run_id"],
            task_id=row["task_id"],
            node_id=row["node_id"],
            status=row["status"],
            state_snapshot=json.loads(row["state_snapshot"] or "{}"),
            side_effect_cursor=row["side_effect_cursor"],
            replay_summary=row["replay_summary"],
            retry_count=row["retry_count"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    async def delete_by_task_ids(self, task_ids: list[str]) -> int:
        """按 task_id 级联删除 works 及其子表（不自动提交）。"""
        if not task_ids:
            return 0
        placeholders = ",".join("?" * len(task_ids))
        await self._conn.execute(
            f"DELETE FROM skill_pipeline_checkpoints WHERE task_id IN ({placeholders})",
            tuple(task_ids),
        )
        await self._conn.execute(
            f"DELETE FROM skill_pipeline_runs WHERE task_id IN ({placeholders})",
            tuple(task_ids),
        )
        await self._conn.execute(
            f"DELETE FROM works WHERE task_id IN ({placeholders})",
            tuple(task_ids),
        )
        cursor = await self._conn.execute("SELECT changes()")
        row = await cursor.fetchone()
        return int(row[0]) if row else 0
