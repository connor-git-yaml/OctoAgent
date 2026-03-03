"""ResumeEngine 测试 -- Feature 010"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from octoagent.core.models import (
    CheckpointSnapshot,
    CheckpointStatus,
    EventType,
    NormalizedMessage,
    TaskStatus,
)
from octoagent.core.store import create_store_group
from octoagent.gateway.services.resume_engine import ResumeEngine
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.gateway.services.task_service import TaskService


async def _prepare_running_task(tmp_path: Path):
    store_group = await create_store_group(
        str(tmp_path / "resume.db"),
        str(tmp_path / "artifacts"),
    )
    task_service = TaskService(store_group, SSEHub())
    msg = NormalizedMessage(text="resume test", idempotency_key="resume-001")
    task_id, created = await task_service.create_task(msg)
    assert created is True
    await task_service._write_state_transition(
        task_id=task_id,
        from_status=TaskStatus.CREATED,
        to_status=TaskStatus.RUNNING,
        trace_id=f"trace-{task_id}",
    )
    return store_group, task_service, task_id


class TestResumeEngine:
    async def test_try_resume_success(self, tmp_path: Path) -> None:
        store_group, _task_service, task_id = await _prepare_running_task(tmp_path)
        now = datetime.now(UTC)
        checkpoint = CheckpointSnapshot(
            checkpoint_id="cp-resume-ok",
            task_id=task_id,
            node_id="model_call_started",
            status=CheckpointStatus.SUCCESS,
            schema_version=1,
            state_snapshot={"next_node": "response_persisted"},
            created_at=now,
            updated_at=now,
        )
        await store_group.checkpoint_store.save_checkpoint(checkpoint)
        await store_group.conn.commit()

        engine = ResumeEngine(store_group)
        result = await engine.try_resume(task_id, trigger="startup")

        assert result.ok is True
        assert result.checkpoint_id == "cp-resume-ok"
        assert result.resumed_from_node == "model_call_started"

        events = await store_group.event_store.get_events_for_task(task_id)
        event_types = [e.type for e in events]
        assert EventType.RESUME_STARTED in event_types
        assert EventType.RESUME_SUCCEEDED in event_types

        await store_group.conn.close()

    async def test_try_resume_snapshot_corrupt(self, tmp_path: Path) -> None:
        store_group, _task_service, task_id = await _prepare_running_task(tmp_path)
        now = datetime.now(UTC).isoformat()
        await store_group.conn.execute(
            """
            INSERT INTO checkpoints (
                checkpoint_id, task_id, node_id, status, schema_version,
                state_snapshot, side_effect_cursor, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "cp-corrupt",
                task_id,
                "model_call_started",
                "success",
                1,
                "\"corrupted-raw-string\"",
                None,
                now,
                now,
            ),
        )
        await store_group.conn.commit()

        engine = ResumeEngine(store_group)
        result = await engine.try_resume(task_id, trigger="startup")

        assert result.ok is False
        assert result.failure_type is not None
        assert result.failure_type.value == "snapshot_corrupt"

        events = await store_group.event_store.get_events_for_task(task_id)
        event_types = [e.type for e in events]
        assert EventType.RESUME_FAILED in event_types

        await store_group.conn.close()

    async def test_concurrent_resume_returns_lease_conflict(self, tmp_path: Path) -> None:
        store_group, _task_service, task_id = await _prepare_running_task(tmp_path)
        now = datetime.now(UTC)
        checkpoint = CheckpointSnapshot(
            checkpoint_id="cp-resume-conflict",
            task_id=task_id,
            node_id="model_call_started",
            status=CheckpointStatus.SUCCESS,
            schema_version=1,
            state_snapshot={"next_node": "response_persisted"},
            created_at=now,
            updated_at=now,
        )
        await store_group.checkpoint_store.save_checkpoint(checkpoint)
        await store_group.conn.commit()

        engine = ResumeEngine(store_group)
        original_get_latest = store_group.checkpoint_store.get_latest_success

        async def delayed_get_latest(task_id_arg: str):
            await asyncio.sleep(0.15)
            return await original_get_latest(task_id_arg)

        store_group.checkpoint_store.get_latest_success = delayed_get_latest  # type: ignore[attr-defined]

        task1 = asyncio.create_task(engine.try_resume(task_id, trigger="manual"))
        await asyncio.sleep(0.03)
        task2 = asyncio.create_task(engine.try_resume(task_id, trigger="manual"))
        result1, result2 = await asyncio.gather(task1, task2)

        failures = [r for r in [result1, result2] if not r.ok]
        successes = [r for r in [result1, result2] if r.ok]
        assert len(successes) == 1
        assert len(failures) == 1
        assert failures[0].failure_type is not None
        assert failures[0].failure_type.value == "lease_conflict"

        await store_group.conn.close()
