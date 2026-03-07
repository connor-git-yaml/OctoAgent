from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from octoagent.core.models import (
    ActorType,
    EventType,
    OperatorActionKind,
    OperatorActionOutcome,
    OperatorActionRequest,
    OperatorActionSource,
    TaskDriftDetectedPayload,
    TaskStatus,
    WorkerReturnedPayload,
)
from octoagent.core.models.message import NormalizedMessage
from octoagent.core.store import create_store_group
from octoagent.gateway.services.operator_actions import OperatorActionService
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.gateway.services.task_service import TaskService
from octoagent.policy.approval_manager import ApprovalManager
from octoagent.policy.models import ApprovalRequest
from octoagent.provider.dx.telegram_pairing import TelegramStateStore
from octoagent.tooling.models import SideEffectLevel


class FakeTaskRunner:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, str, str | None]] = []
        self.cancelled: list[str] = []
        self.cancel_return = True

    async def enqueue(self, task_id: str, user_text: str, model_alias: str | None = None) -> None:
        self.enqueued.append((task_id, user_text, model_alias))

    async def cancel_task(self, task_id: str) -> bool:
        self.cancelled.append(task_id)
        return self.cancel_return


async def _create_base_task(tmp_path: Path, text: str = "hello") -> tuple:
    store_group = await create_store_group(
        str(tmp_path / "gateway.db"),
        str(tmp_path / "artifacts"),
    )
    task_service = TaskService(store_group, SSEHub())
    task_id, created = await task_service.create_task(
        NormalizedMessage(
            channel="web",
            thread_id="thread-001",
            scope_id="scope-001",
            sender_id="owner",
            sender_name="Owner",
            text=text,
            idempotency_key=f"task:{text}",
        )
    )
    assert created is True
    return store_group, task_service, task_id


@pytest.mark.asyncio
async def test_approval_action_resolves_and_records_audit(tmp_path: Path) -> None:
    store_group, _, task_id = await _create_base_task(tmp_path, text="approval")
    approval_manager = ApprovalManager(event_store=store_group.event_store)
    request = ApprovalRequest(
        approval_id="ap-001",
        task_id=task_id,
        tool_name="filesystem.write",
        tool_args_summary="echo hello",
        risk_explanation="需要人工确认不可逆写入",
        policy_label="global.irreversible",
        side_effect_level=SideEffectLevel.IRREVERSIBLE,
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
    )
    await approval_manager.register(request)
    service = OperatorActionService(
        store_group=store_group,
        sse_hub=SSEHub(),
        approval_manager=approval_manager,
    )

    result = await service.execute(
        OperatorActionRequest(
            item_id="approval:ap-001",
            kind=OperatorActionKind.APPROVE_ONCE,
            source=OperatorActionSource.WEB,
            actor_id="user:web",
            actor_label="owner",
        )
    )

    events = await store_group.event_store.get_events_for_task(task_id)

    assert result.outcome == OperatorActionOutcome.SUCCEEDED
    assert approval_manager.get_approval("ap-001").status.value == "approved"
    assert events[-1].type == EventType.OPERATOR_ACTION_RECORDED
    assert events[-1].payload["action_kind"] == "approve_once"

    await store_group.conn.close()


@pytest.mark.asyncio
async def test_retry_task_creates_successor_and_records_audit(tmp_path: Path) -> None:
    store_group, task_service, task_id = await _create_base_task(tmp_path, text="retry me")
    await task_service._write_state_transition(
        task_id=task_id,
        from_status=TaskStatus.CREATED,
        to_status=TaskStatus.RUNNING,
        trace_id=f"trace-{task_id}",
    )
    await task_service.append_structured_event(
        task_id=task_id,
        event_type=EventType.WORKER_RETURNED,
        actor=ActorType.WORKER,
        payload=WorkerReturnedPayload(
            dispatch_id="dispatch-001",
            worker_id="worker.dev",
            status="failed",
            retryable=True,
            summary="docker backend timeout",
            error_type="timeout",
            error_message="timeout",
        ).model_dump(mode="json"),
    )
    await task_service._write_state_transition(
        task_id=task_id,
        from_status=TaskStatus.RUNNING,
        to_status=TaskStatus.FAILED,
        trace_id=f"trace-{task_id}",
    )
    await store_group.task_job_store.create_job(task_id, "retry me")
    runner = FakeTaskRunner()
    service = OperatorActionService(
        store_group=store_group,
        sse_hub=SSEHub(),
        task_runner=runner,
    )

    result = await service.execute(
        OperatorActionRequest(
            item_id=f"task:{task_id}",
            kind=OperatorActionKind.RETRY_TASK,
            source=OperatorActionSource.WEB,
            actor_id="user:web",
            actor_label="owner",
        )
    )

    events = await store_group.event_store.get_events_for_task(task_id)
    assert result.outcome == OperatorActionOutcome.SUCCEEDED
    assert result.retry_launch is not None
    assert result.retry_launch.source_task_id == task_id
    assert runner.enqueued[0][0] == result.retry_launch.result_task_id
    assert events[-1].type == EventType.OPERATOR_ACTION_RECORDED
    assert events[-1].payload["result_task_id"] == result.retry_launch.result_task_id

    await store_group.conn.close()


@pytest.mark.asyncio
async def test_retry_task_is_idempotent_after_first_success(tmp_path: Path) -> None:
    store_group, task_service, task_id = await _create_base_task(tmp_path, text="retry once")
    await task_service._write_state_transition(
        task_id=task_id,
        from_status=TaskStatus.CREATED,
        to_status=TaskStatus.RUNNING,
        trace_id=f"trace-{task_id}",
    )
    await task_service.append_structured_event(
        task_id=task_id,
        event_type=EventType.WORKER_RETURNED,
        actor=ActorType.WORKER,
        payload=WorkerReturnedPayload(
            dispatch_id="dispatch-001",
            worker_id="worker.dev",
            status="failed",
            retryable=True,
            summary="docker backend timeout",
            error_type="timeout",
            error_message="timeout",
        ).model_dump(mode="json"),
    )
    await task_service._write_state_transition(
        task_id=task_id,
        from_status=TaskStatus.RUNNING,
        to_status=TaskStatus.FAILED,
        trace_id=f"trace-{task_id}",
    )
    await store_group.task_job_store.create_job(task_id, "retry once")
    runner = FakeTaskRunner()
    service = OperatorActionService(
        store_group=store_group,
        sse_hub=SSEHub(),
        task_runner=runner,
    )
    request = OperatorActionRequest(
        item_id=f"task:{task_id}",
        kind=OperatorActionKind.RETRY_TASK,
        source=OperatorActionSource.WEB,
        actor_id="user:web",
        actor_label="owner",
    )

    first = await service.execute(request)
    second = await service.execute(request)

    assert first.outcome == OperatorActionOutcome.SUCCEEDED
    assert first.retry_launch is not None
    assert second.outcome == OperatorActionOutcome.ALREADY_HANDLED
    assert second.retry_launch == first.retry_launch
    assert runner.enqueued == [
        (
            first.retry_launch.result_task_id,
            "retry once",
            None,
        )
    ]

    await store_group.conn.close()


@pytest.mark.asyncio
async def test_ack_alert_is_idempotent_after_first_success(tmp_path: Path) -> None:
    store_group, task_service, task_id = await _create_base_task(tmp_path, text="drift me")
    await task_service._write_state_transition(
        task_id=task_id,
        from_status=TaskStatus.CREATED,
        to_status=TaskStatus.RUNNING,
        trace_id=f"trace-{task_id}",
    )
    drift_event = await task_service.append_structured_event(
        task_id=task_id,
        event_type=EventType.TASK_DRIFT_DETECTED,
        actor=ActorType.SYSTEM,
        payload=TaskDriftDetectedPayload(
            drift_type="no_progress",
            detected_at=datetime.now(tz=UTC).isoformat(),
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            stall_duration_seconds=120.0,
            suggested_actions=["check_worker_logs"],
        ).model_dump(mode="json"),
    )
    service = OperatorActionService(
        store_group=store_group,
        sse_hub=SSEHub(),
    )
    request = OperatorActionRequest(
        item_id=f"alert:{task_id}:{drift_event.event_id}",
        kind=OperatorActionKind.ACK_ALERT,
        source=OperatorActionSource.WEB,
        actor_id="user:web",
        actor_label="owner",
    )

    first = await service.execute(request)
    second = await service.execute(request)

    assert first.outcome == OperatorActionOutcome.SUCCEEDED
    assert second.outcome == OperatorActionOutcome.ALREADY_HANDLED

    await store_group.conn.close()


@pytest.mark.asyncio
async def test_pairing_action_uses_operational_audit_task(tmp_path: Path) -> None:
    store_group = await create_store_group(
        str(tmp_path / "gateway.db"),
        str(tmp_path / "artifacts"),
    )
    state_store = TelegramStateStore(tmp_path)
    state_store.ensure_pairing_request(
        user_id="1001",
        chat_id="1001",
        username="guest",
        display_name="Guest",
        last_message_text="hello",
    )
    service = OperatorActionService(
        store_group=store_group,
        sse_hub=SSEHub(),
        telegram_state_store=state_store,
    )

    result = await service.execute(
        OperatorActionRequest(
            item_id="pairing:1001",
            kind=OperatorActionKind.APPROVE_PAIRING,
            source=OperatorActionSource.WEB,
            actor_id="user:web",
            actor_label="owner",
        )
    )

    audit_task = await store_group.task_store.get_task("ops-operator-inbox")
    audit_events = await store_group.event_store.get_events_for_task("ops-operator-inbox")

    assert result.outcome == OperatorActionOutcome.SUCCEEDED
    assert state_store.get_approved_user("1001") is not None
    assert audit_task is not None
    assert audit_events[-1].type == EventType.OPERATOR_ACTION_RECORDED
    assert audit_events[-1].payload["item_id"] == "pairing:1001"

    await store_group.conn.close()
