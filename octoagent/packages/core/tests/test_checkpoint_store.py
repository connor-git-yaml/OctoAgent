"""Feature 010: checkpoint/ledger/store 事务测试"""

from datetime import UTC, datetime, timedelta

import pytest
from octoagent.core.models import (
    ActorType,
    CheckpointSnapshot,
    CheckpointStatus,
    Event,
    EventType,
    RequesterInfo,
    Task,
    TaskStatus,
)
from octoagent.core.store.checkpoint_store import SqliteCheckpointStore
from octoagent.core.store.event_store import SqliteEventStore
from octoagent.core.store.side_effect_ledger_store import SqliteSideEffectLedgerStore
from octoagent.core.store.task_store import SqliteTaskStore
from octoagent.core.store.transaction import append_event_and_save_checkpoint


async def _create_task(conn, task_id: str) -> None:
    now = datetime.now(UTC)
    task = Task(
        task_id=task_id,
        created_at=now,
        updated_at=now,
        status=TaskStatus.CREATED,
        title="checkpoint-test",
        thread_id="default",
        scope_id="chat:web:default",
        requester=RequesterInfo(channel="web", sender_id="owner"),
    )
    task_store = SqliteTaskStore(conn)
    await task_store.create_task(task)
    await conn.commit()


class TestCheckpointStore:
    async def test_save_get_latest_success_and_list(self, core_db) -> None:
        await _create_task(core_db, "task-cp-001")
        store = SqliteCheckpointStore(core_db)
        now = datetime.now(UTC)

        cp1 = CheckpointSnapshot(
            checkpoint_id="cp-001",
            task_id="task-cp-001",
            node_id="state_running",
            status=CheckpointStatus.SUCCESS,
            schema_version=1,
            state_snapshot={"next_node": "model_call_started"},
            created_at=now,
            updated_at=now,
        )
        cp2 = CheckpointSnapshot(
            checkpoint_id="cp-002",
            task_id="task-cp-001",
            node_id="model_call_started",
            status=CheckpointStatus.SUCCESS,
            schema_version=1,
            state_snapshot={"next_node": "response_persisted"},
            created_at=now + timedelta(microseconds=1),
            updated_at=now + timedelta(microseconds=1),
        )
        await store.save_checkpoint(cp1)
        await store.save_checkpoint(cp2)
        await core_db.commit()

        latest = await store.get_latest_success("task-cp-001")
        assert latest is not None
        assert latest.checkpoint_id == "cp-002"
        assert latest.node_id == "model_call_started"

        checkpoints = await store.list_checkpoints("task-cp-001")
        assert [cp.checkpoint_id for cp in checkpoints] == ["cp-002", "cp-001"]

    async def test_mark_status_transition_rules(self, core_db) -> None:
        await _create_task(core_db, "task-cp-002")
        store = SqliteCheckpointStore(core_db)
        now = datetime.now(UTC)
        cp = CheckpointSnapshot(
            checkpoint_id="cp-transition",
            task_id="task-cp-002",
            node_id="n1",
            status=CheckpointStatus.CREATED,
            schema_version=1,
            state_snapshot={},
            created_at=now,
            updated_at=now,
        )
        await store.save_checkpoint(cp)
        await core_db.commit()

        await store.mark_status("cp-transition", CheckpointStatus.PENDING.value)
        await store.mark_status("cp-transition", CheckpointStatus.RUNNING.value)
        await store.mark_status("cp-transition", CheckpointStatus.SUCCESS.value)
        await core_db.commit()

        updated = await store.get_checkpoint("cp-transition")
        assert updated is not None
        assert updated.status == CheckpointStatus.SUCCESS

        with pytest.raises(ValueError):
            await store.mark_status("cp-transition", CheckpointStatus.RUNNING.value)

    async def test_append_event_and_save_checkpoint_atomic(self, core_db) -> None:
        await _create_task(core_db, "task-cp-003")
        task_store = SqliteTaskStore(core_db)
        event_store = SqliteEventStore(core_db)
        checkpoint_store = SqliteCheckpointStore(core_db)

        now = datetime.now(UTC)
        event = Event(
            event_id="evt-cp-001",
            task_id="task-cp-003",
            task_seq=1,
            ts=now,
            type=EventType.CHECKPOINT_SAVED,
            actor=ActorType.SYSTEM,
            payload={"checkpoint_id": "cp-atomic", "node_id": "state_running"},
            trace_id="trace-task-cp-003",
        )
        checkpoint = CheckpointSnapshot(
            checkpoint_id="cp-atomic",
            task_id="task-cp-003",
            node_id="state_running",
            status=CheckpointStatus.SUCCESS,
            schema_version=1,
            state_snapshot={"next_node": "model_call_started"},
            created_at=now,
            updated_at=now,
        )

        await append_event_and_save_checkpoint(
            core_db,
            event_store,
            task_store,
            checkpoint_store,
            event,
            checkpoint,
        )

        task = await task_store.get_task("task-cp-003")
        assert task is not None
        assert task.pointers.latest_event_id == "evt-cp-001"
        assert task.pointers.latest_checkpoint_id == "cp-atomic"


class TestSideEffectLedgerStore:
    async def test_try_record_is_idempotent(self, core_db) -> None:
        await _create_task(core_db, "task-ledger-001")
        store = SqliteSideEffectLedgerStore(core_db)

        first = await store.try_record(
            task_id="task-ledger-001",
            step_key="llm_call",
            idempotency_key="idem-001",
            effect_type="tool_call",
        )
        second = await store.try_record(
            task_id="task-ledger-001",
            step_key="llm_call",
            idempotency_key="idem-001",
            effect_type="tool_call",
        )

        assert first is True
        assert second is False
        assert await store.exists("idem-001") is True

    async def test_get_entry_and_set_result_ref(self, core_db) -> None:
        await _create_task(core_db, "task-ledger-002")
        store = SqliteSideEffectLedgerStore(core_db)

        created = await store.try_record(
            task_id="task-ledger-002",
            step_key="llm_call",
            idempotency_key="idem-002",
            effect_type="tool_call",
        )
        assert created is True

        entry = await store.get_entry("idem-002")
        assert entry is not None
        assert entry.result_ref is None

        await store.set_result_ref("idem-002", "artifact-002")
        updated = await store.get_entry("idem-002")
        assert updated is not None
        assert updated.result_ref == "artifact-002"

    async def test_idempotency_conflict_semantics(self, core_db) -> None:
        await _create_task(core_db, "task-ledger-003a")
        await _create_task(core_db, "task-ledger-003b")
        store = SqliteSideEffectLedgerStore(core_db)

        # 同 task + 同 step + 不同 idempotency_key：由于 UNIQUE(task_id, step_key) 冲突，应拒绝
        first = await store.try_record(
            task_id="task-ledger-003a",
            step_key="llm_call",
            idempotency_key="idem-003a-1",
        )
        second = await store.try_record(
            task_id="task-ledger-003a",
            step_key="llm_call",
            idempotency_key="idem-003a-2",
        )

        # 不同 task + 相同 idempotency_key：由于 UNIQUE(idempotency_key) 冲突，应拒绝
        third = await store.try_record(
            task_id="task-ledger-003b",
            step_key="llm_call",
            idempotency_key="idem-003a-1",
        )

        assert first is True
        assert second is False
        assert third is False
