"""Feature 030: Skill Pipeline Engine。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from octoagent.core.models import (
    EventType,
    PipelineCheckpoint,
    PipelineCheckpointSavedPayload,
    PipelineReplayFrame,
    PipelineRunStatus,
    PipelineRunUpdatedPayload,
    SkillPipelineDefinition,
    SkillPipelineNode,
    SkillPipelineRun,
)
from octoagent.core.store import StoreGroup
from pydantic import BaseModel, Field
from ulid import ULID

EventRecorder = Callable[[str, EventType, dict[str, Any]], Awaitable[None]]


class PipelineExecutionError(RuntimeError):
    """Pipeline 执行异常。"""


class PipelineNodeOutcome(BaseModel):
    """节点执行结果。"""

    status: PipelineRunStatus = PipelineRunStatus.RUNNING
    summary: str = Field(default="")
    next_node_id: str | None = None
    state_patch: dict[str, Any] = Field(default_factory=dict)
    input_request: dict[str, Any] = Field(default_factory=dict)
    approval_request: dict[str, Any] = Field(default_factory=dict)
    metadata_patch: dict[str, Any] = Field(default_factory=dict)
    side_effect_cursor: str | None = None


class PipelineNodeHandler(Protocol):
    """节点处理器协议。"""

    async def __call__(
        self,
        *,
        run: SkillPipelineRun,
        node: SkillPipelineNode,
        state: dict[str, Any],
    ) -> PipelineNodeOutcome:
        """执行节点。"""


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


class SkillPipelineEngine:
    """Deterministic pipeline 执行器。"""

    def __init__(
        self,
        *,
        store_group: StoreGroup,
        event_recorder: EventRecorder | None = None,
    ) -> None:
        self._stores = store_group
        self._event_recorder = event_recorder
        self._handlers: dict[str, PipelineNodeHandler] = {}

    def register_handler(self, handler_id: str, handler: PipelineNodeHandler) -> None:
        self._handlers[handler_id] = handler

    async def start_run(
        self,
        *,
        definition: SkillPipelineDefinition,
        task_id: str,
        work_id: str,
        initial_state: dict[str, Any] | None = None,
        run_id: str | None = None,
    ) -> SkillPipelineRun:
        now = _utc_now()
        run = SkillPipelineRun(
            run_id=run_id or str(ULID()),
            pipeline_id=definition.pipeline_id,
            task_id=task_id,
            work_id=work_id,
            status=PipelineRunStatus.CREATED,
            current_node_id=definition.entry_node_id,
            state_snapshot=initial_state or {},
            created_at=now,
            updated_at=now,
        )
        await self._stores.work_store.save_pipeline_run(run)
        await self._stores.conn.commit()
        await self._emit_run_event(run, summary="pipeline run created")
        return await self._drive(definition=definition, run=run)

    async def resume_run(
        self,
        *,
        definition: SkillPipelineDefinition,
        run_id: str,
        state_patch: dict[str, Any] | None = None,
    ) -> SkillPipelineRun:
        run = await self._load_run(run_id)
        resume_node_id = str(run.metadata.get("resume_next_node_id", "")).strip()
        next_node_id = resume_node_id or run.current_node_id or definition.entry_node_id
        run = run.model_copy(
            update={
                "status": PipelineRunStatus.RUNNING,
                "pause_reason": "",
                "current_node_id": next_node_id,
                "state_snapshot": {
                    **run.state_snapshot,
                    **(state_patch or {}),
                },
                "metadata": {
                    **run.metadata,
                    "resume_next_node_id": "",
                },
                "updated_at": _utc_now(),
            }
        )
        await self._stores.work_store.save_pipeline_run(run)
        await self._stores.conn.commit()
        await self._emit_run_event(run, summary="pipeline run resumed")
        return await self._drive(definition=definition, run=run)

    async def retry_current_node(
        self,
        *,
        definition: SkillPipelineDefinition,
        run_id: str,
    ) -> SkillPipelineRun:
        run = await self._load_run(run_id)
        current_node_id = run.current_node_id or definition.entry_node_id
        retry_cursor = dict(run.retry_cursor)
        retry_cursor[current_node_id] = retry_cursor.get(current_node_id, 0) + 1
        run = run.model_copy(
            update={
                "status": PipelineRunStatus.RUNNING,
                "pause_reason": "",
                "retry_cursor": retry_cursor,
                "updated_at": _utc_now(),
            }
        )
        await self._stores.work_store.save_pipeline_run(run)
        await self._stores.conn.commit()
        await self._emit_run_event(
            run,
            summary=f"pipeline node retry: {current_node_id}",
        )
        return await self._drive(definition=definition, run=run)

    async def cancel_run(self, run_id: str, *, reason: str = "cancelled") -> SkillPipelineRun:
        run = await self._load_run(run_id)
        cancelled = run.model_copy(
            update={
                "status": PipelineRunStatus.CANCELLED,
                "pause_reason": reason,
                "completed_at": _utc_now(),
                "updated_at": _utc_now(),
            }
        )
        await self._stores.work_store.save_pipeline_run(cancelled)
        await self._stores.conn.commit()
        await self._emit_run_event(cancelled, summary=reason)
        return cancelled

    async def list_replay_frames(self, run_id: str) -> list[PipelineReplayFrame]:
        checkpoints = await self._stores.work_store.list_pipeline_checkpoints(run_id)
        return [
            PipelineReplayFrame(
                frame_id=f"frame:{item.checkpoint_id}",
                run_id=item.run_id,
                node_id=item.node_id,
                status=item.status,
                summary=item.replay_summary,
                checkpoint_id=item.checkpoint_id,
                ts=item.updated_at,
            )
            for item in checkpoints
        ]

    async def _drive(
        self,
        *,
        definition: SkillPipelineDefinition,
        run: SkillPipelineRun,
    ) -> SkillPipelineRun:
        current = run
        while True:
            node_id = current.current_node_id or definition.entry_node_id
            node = definition.get_node(node_id)
            handler = self._handlers.get(node.handler_id)
            if handler is None:
                raise PipelineExecutionError(f"pipeline handler 未注册: {node.handler_id}")

            current = current.model_copy(
                update={
                    "status": PipelineRunStatus.RUNNING,
                    "current_node_id": node.node_id,
                    "updated_at": _utc_now(),
                }
            )
            await self._stores.work_store.save_pipeline_run(current)
            await self._stores.conn.commit()
            await self._emit_run_event(current, summary=f"pipeline node running: {node.node_id}")

            outcome = await handler(
                run=current,
                node=node,
                state=dict(current.state_snapshot),
            )
            next_state = {**current.state_snapshot, **outcome.state_patch}
            next_metadata = {**current.metadata, **outcome.metadata_patch}
            checkpoint_status = outcome.status
            checkpoint = PipelineCheckpoint(
                checkpoint_id=str(ULID()),
                run_id=current.run_id,
                task_id=current.task_id,
                node_id=node.node_id,
                status=checkpoint_status,
                state_snapshot=next_state,
                side_effect_cursor=outcome.side_effect_cursor,
                replay_summary=outcome.summary,
                retry_count=current.retry_cursor.get(node.node_id, 0),
            )
            await self._stores.work_store.save_pipeline_checkpoint(checkpoint)
            await self._emit_checkpoint_event(checkpoint)

            if outcome.status in {
                PipelineRunStatus.WAITING_INPUT,
                PipelineRunStatus.WAITING_APPROVAL,
                PipelineRunStatus.PAUSED,
            }:
                current = current.model_copy(
                    update={
                        "status": outcome.status,
                        "pause_reason": outcome.summary,
                        "state_snapshot": next_state,
                        "input_request": outcome.input_request,
                        "approval_request": outcome.approval_request,
                        "metadata": {
                            **next_metadata,
                            "resume_next_node_id": outcome.next_node_id or node.next_node_id or "",
                        },
                        "updated_at": _utc_now(),
                    }
                )
                await self._stores.work_store.save_pipeline_run(current)
                await self._stores.conn.commit()
                await self._emit_run_event(current, summary=outcome.summary)
                return current

            if outcome.status in {
                PipelineRunStatus.FAILED,
                PipelineRunStatus.CANCELLED,
            }:
                current = current.model_copy(
                    update={
                        "status": outcome.status,
                        "pause_reason": outcome.summary
                        if outcome.status == PipelineRunStatus.CANCELLED
                        else "",
                        "state_snapshot": next_state,
                        "metadata": next_metadata,
                        "completed_at": _utc_now(),
                        "updated_at": _utc_now(),
                    }
                )
                await self._stores.work_store.save_pipeline_run(current)
                await self._stores.conn.commit()
                await self._emit_run_event(current, summary=outcome.summary)
                return current

            next_node_id = outcome.next_node_id or node.next_node_id or ""
            is_terminal = not next_node_id
            current = current.model_copy(
                update={
                    "status": (
                        PipelineRunStatus.SUCCEEDED if is_terminal else PipelineRunStatus.RUNNING
                    ),
                    "current_node_id": next_node_id,
                    "state_snapshot": next_state,
                    "input_request": {},
                    "approval_request": {},
                    "metadata": next_metadata,
                    "completed_at": _utc_now() if is_terminal else None,
                    "updated_at": _utc_now(),
                }
            )
            await self._stores.work_store.save_pipeline_run(current)
            await self._stores.conn.commit()
            await self._emit_run_event(current, summary=outcome.summary)
            if is_terminal:
                return current

    async def _load_run(self, run_id: str) -> SkillPipelineRun:
        run = await self._stores.work_store.get_pipeline_run(run_id)
        if run is None:
            raise PipelineExecutionError(f"pipeline run 不存在: {run_id}")
        return run

    async def _emit_run_event(self, run: SkillPipelineRun, *, summary: str) -> None:
        if self._event_recorder is None:
            return
        retry_count = run.retry_cursor.get(run.current_node_id, 0) if run.current_node_id else 0
        await self._event_recorder(
            run.task_id,
            EventType.PIPELINE_RUN_UPDATED,
            PipelineRunUpdatedPayload(
                run_id=run.run_id,
                pipeline_id=run.pipeline_id,
                task_id=run.task_id,
                work_id=run.work_id,
                status=run.status.value,
                current_node_id=run.current_node_id,
                pause_reason=run.pause_reason,
                retry_count=retry_count,
                summary=summary,
                metadata=run.metadata,
            ).model_dump(mode="json"),
        )

    async def _emit_checkpoint_event(self, checkpoint: PipelineCheckpoint) -> None:
        if self._event_recorder is None:
            return
        await self._event_recorder(
            checkpoint.task_id,
            EventType.PIPELINE_CHECKPOINT_SAVED,
            PipelineCheckpointSavedPayload(
                checkpoint_id=checkpoint.checkpoint_id,
                run_id=checkpoint.run_id,
                task_id=checkpoint.task_id,
                node_id=checkpoint.node_id,
                status=checkpoint.status.value,
                retry_count=checkpoint.retry_count,
                replay_summary=checkpoint.replay_summary,
            ).model_dump(mode="json"),
        )
