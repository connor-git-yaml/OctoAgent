"""Feature 030: WorkStore 持久化测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from octoagent.core.models import (
    DelegationTargetKind,
    PipelineCheckpoint,
    PipelineRunStatus,
    RequesterInfo,
    SkillPipelineRun,
    Task,
    Work,
    WorkKind,
    WorkStatus,
)
from octoagent.core.store import create_store_group
from octoagent.core.store.task_store import TaskPointers


async def _create_task(store_group, task_id: str) -> None:
    now = datetime.now(UTC)
    await store_group.task_store.create_task(
        Task(
            task_id=task_id,
            created_at=now,
            updated_at=now,
            title=f"task {task_id}",
            requester=RequesterInfo(channel="web", sender_id="owner"),
            pointers=TaskPointers(),
            trace_id=f"trace-{task_id}",
        )
    )


async def test_work_store_roundtrip_and_filters(tmp_path: Path) -> None:
    store_group = await create_store_group(
        str(tmp_path / "work.db"),
        str(tmp_path / "artifacts"),
    )
    parent = Work(
        work_id="work-parent",
        task_id="task-1",
        title="parent",
        status=WorkStatus.ASSIGNED,
        agent_profile_id="agent-profile-parent",
        context_frame_id="context-frame-parent",
    )
    child = Work(
        work_id="work-child",
        task_id="task-1",
        parent_work_id="work-parent",
        title="child",
        status=WorkStatus.WAITING_APPROVAL,
        kind=WorkKind.DELEGATION,
        target_kind=DelegationTargetKind.SUBAGENT,
        agent_profile_id="agent-profile-child",
        requested_worker_profile_id="worker-profile-alpha",
        requested_worker_profile_version=2,
        effective_worker_snapshot_id="worker-snapshot:worker-profile-alpha:2",
        selected_worker_type="research",
        selected_tools=["web.search"],
        context_frame_id="context-frame-child",
    )

    await _create_task(store_group, "task-1")
    await store_group.work_store.save_work(parent)
    await store_group.work_store.save_work(child)
    await store_group.conn.commit()

    stored = await store_group.work_store.get_work("work-child")
    assert stored is not None
    assert stored.parent_work_id == "work-parent"
    assert stored.status == WorkStatus.WAITING_APPROVAL
    assert stored.agent_profile_id == "agent-profile-child"
    assert stored.context_frame_id == "context-frame-child"
    assert stored.requested_worker_profile_id == "worker-profile-alpha"
    assert stored.requested_worker_profile_version == 2
    assert stored.effective_worker_snapshot_id == "worker-snapshot:worker-profile-alpha:2"
    assert stored.selected_tools == ["web.search"]

    by_task = await store_group.work_store.list_works(task_id="task-1")
    assert [item.work_id for item in by_task] == ["work-child", "work-parent"]

    by_status = await store_group.work_store.list_works(statuses=["assigned"])
    assert [item.work_id for item in by_status] == ["work-parent"]

    by_parent = await store_group.work_store.list_works(parent_work_id="work-parent")
    assert [item.work_id for item in by_parent] == ["work-child"]

    await store_group.conn.close()


async def test_work_store_persists_pipeline_run_and_checkpoints(tmp_path: Path) -> None:
    store_group = await create_store_group(
        str(tmp_path / "pipeline.db"),
        str(tmp_path / "artifacts"),
    )
    run = SkillPipelineRun(
        run_id="run-1",
        pipeline_id="delegation:preflight",
        task_id="task-1",
        work_id="work-1",
        status=PipelineRunStatus.WAITING_INPUT,
        current_node_id="gate.review",
        pause_reason="need operator input",
        state_snapshot={"route_reason": "ops"},
    )
    checkpoint = PipelineCheckpoint(
        checkpoint_id="checkpoint-1",
        run_id="run-1",
        task_id="task-1",
        node_id="gate.review",
        status=PipelineRunStatus.WAITING_INPUT,
        state_snapshot={"route_reason": "ops"},
        replay_summary="waiting input",
    )

    await _create_task(store_group, "task-1")
    await store_group.work_store.save_work(
        Work(
            work_id="work-1",
            task_id="task-1",
            title="pipeline owner",
        )
    )
    await store_group.work_store.save_pipeline_run(run)
    await store_group.work_store.save_pipeline_checkpoint(checkpoint)
    await store_group.conn.commit()

    stored_run = await store_group.work_store.get_pipeline_run("run-1")
    assert stored_run is not None
    assert stored_run.pause_reason == "need operator input"

    listed_runs = await store_group.work_store.list_pipeline_runs(task_id="task-1")
    assert [item.run_id for item in listed_runs] == ["run-1"]

    latest_checkpoint = await store_group.work_store.get_latest_pipeline_checkpoint("run-1")
    assert latest_checkpoint is not None
    assert latest_checkpoint.checkpoint_id == "checkpoint-1"

    listed_checkpoints = await store_group.work_store.list_pipeline_checkpoints("run-1")
    assert [item.checkpoint_id for item in listed_checkpoints] == ["checkpoint-1"]

    await store_group.conn.close()
