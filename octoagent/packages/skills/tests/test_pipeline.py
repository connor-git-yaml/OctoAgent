"""Feature 030: Skill Pipeline Engine 单元测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from octoagent.core.models import (
    EventType,
    PipelineNodeType,
    PipelineRunStatus,
    RequesterInfo,
    SkillPipelineDefinition,
    SkillPipelineNode,
    Task,
    Work,
)
from octoagent.core.store import create_store_group
from octoagent.core.store.task_store import TaskPointers
from octoagent.skills import PipelineNodeOutcome, SkillPipelineEngine


def _definition() -> SkillPipelineDefinition:
    return SkillPipelineDefinition(
        pipeline_id="pipeline:test",
        label="Test Pipeline",
        version="1.0.0",
        entry_node_id="route",
        nodes=[
            SkillPipelineNode(
                node_id="route",
                label="route",
                node_type=PipelineNodeType.TRANSFORM,
                handler_id="route",
                next_node_id="gate",
            ),
            SkillPipelineNode(
                node_id="gate",
                label="gate",
                node_type=PipelineNodeType.GATE,
                handler_id="gate",
                next_node_id="finalize",
            ),
            SkillPipelineNode(
                node_id="finalize",
                label="finalize",
                node_type=PipelineNodeType.TRANSFORM,
                handler_id="finalize",
            ),
        ],
    )


async def _seed_task_and_work(store_group, *, task_id: str, work_id: str) -> None:
    now = datetime.now(UTC)
    await store_group.task_store.create_task(
        Task(
            task_id=task_id,
            created_at=now,
            updated_at=now,
            title="pipeline task",
            requester=RequesterInfo(channel="web", sender_id="owner"),
            pointers=TaskPointers(),
            trace_id=f"trace-{task_id}",
        )
    )
    await store_group.work_store.save_work(
        Work(
            work_id=work_id,
            task_id=task_id,
            title="pipeline work",
        )
    )
    await store_group.conn.commit()


async def test_pipeline_engine_checkpoint_replay_and_resume(tmp_path: Path) -> None:
    store_group = await create_store_group(
        str(tmp_path / "pipeline.db"),
        str(tmp_path / "artifacts"),
    )
    events: list[tuple[EventType, dict[str, object]]] = []
    engine = SkillPipelineEngine(
        store_group=store_group,
        event_recorder=lambda task_id, event_type, payload: _capture_event(
            events, event_type, payload
        ),
    )

    async def route_handler(*, run, node, state):
        return PipelineNodeOutcome(
            summary="route resolved",
            state_patch={"selected_worker_type": "ops"},
        )

    async def gate_handler(*, run, node, state):
        return PipelineNodeOutcome(
            status=PipelineRunStatus.WAITING_APPROVAL,
            summary="waiting approval",
            next_node_id="finalize",
            approval_request={"kind": "delegation_approval"},
        )

    async def finalize_handler(*, run, node, state):
        return PipelineNodeOutcome(
            status=PipelineRunStatus.SUCCEEDED,
            summary="pipeline done",
        )

    engine.register_handler("route", route_handler)
    engine.register_handler("gate", gate_handler)
    engine.register_handler("finalize", finalize_handler)

    await _seed_task_and_work(store_group, task_id="task-1", work_id="work-1")
    run = await engine.start_run(
        definition=_definition(),
        task_id="task-1",
        work_id="work-1",
        initial_state={"user_text": "ops diagnose"},
    )
    assert run.status == PipelineRunStatus.WAITING_APPROVAL
    assert run.approval_request["kind"] == "delegation_approval"

    frames = await engine.list_replay_frames(run.run_id)
    assert [item.node_id for item in frames] == ["route", "gate"]
    assert frames[-1].status == PipelineRunStatus.WAITING_APPROVAL

    resumed = await engine.resume_run(
        definition=_definition(),
        run_id=run.run_id,
        state_patch={"approved": True},
    )
    assert resumed.status == PipelineRunStatus.SUCCEEDED
    assert resumed.state_snapshot["approved"] is True

    event_types = [item[0] for item in events]
    assert EventType.PIPELINE_RUN_UPDATED in event_types
    assert EventType.PIPELINE_CHECKPOINT_SAVED in event_types

    await store_group.conn.close()


async def test_pipeline_engine_retry_current_node_increments_retry_cursor(
    tmp_path: Path,
) -> None:
    store_group = await create_store_group(
        str(tmp_path / "retry.db"),
        str(tmp_path / "artifacts"),
    )
    engine = SkillPipelineEngine(store_group=store_group)

    async def route_handler(*, run, node, state):
        return PipelineNodeOutcome(summary="route resolved")

    async def gate_handler(*, run, node, state):
        return PipelineNodeOutcome(
            status=PipelineRunStatus.PAUSED,
            summary="manual pause",
            next_node_id="finalize",
        )

    async def finalize_handler(*, run, node, state):
        return PipelineNodeOutcome(status=PipelineRunStatus.SUCCEEDED, summary="done")

    engine.register_handler("route", route_handler)
    engine.register_handler("gate", gate_handler)
    engine.register_handler("finalize", finalize_handler)

    await _seed_task_and_work(store_group, task_id="task-2", work_id="work-2")
    run = await engine.start_run(
        definition=_definition(),
        task_id="task-2",
        work_id="work-2",
    )
    assert run.status == PipelineRunStatus.PAUSED

    retried = await engine.retry_current_node(
        definition=_definition(),
        run_id=run.run_id,
    )
    assert retried.status == PipelineRunStatus.PAUSED
    assert retried.retry_cursor["gate"] == 1

    await store_group.conn.close()


async def _capture_event(
    events: list[tuple[EventType, dict[str, object]]],
    event_type: EventType,
    payload: dict[str, object],
) -> None:
    events.append((event_type, payload))
