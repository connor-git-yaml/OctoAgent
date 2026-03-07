from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from octoagent.core.models import (
    ActorType,
    EventType,
    OperatorActionKind,
    OperatorActionRequest,
    OperatorActionSource,
    TaskDriftDetectedPayload,
    TaskStatus,
    WorkerReturnedPayload,
)
from octoagent.core.models.message import NormalizedMessage
from octoagent.core.store import create_store_group
from octoagent.gateway.services.operator_actions import OperatorActionService
from octoagent.gateway.services.operator_inbox import OperatorInboxService
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.gateway.services.task_service import TaskService
from octoagent.gateway.services.watchdog.config import WatchdogConfig
from octoagent.policy.approval_manager import ApprovalManager
from octoagent.policy.models import ApprovalRequest
from octoagent.provider.dx.telegram_pairing import TelegramStateStore
from octoagent.tooling.models import SideEffectLevel


async def _create_task(task_service: TaskService, text: str) -> str:
    task_id, created = await task_service.create_task(
        NormalizedMessage(
            channel="web",
            thread_id=f"thread-{text}",
            scope_id=f"scope-{text}",
            sender_id="owner",
            sender_name="Owner",
            text=text,
            idempotency_key=f"task:{text}",
        )
    )
    assert created is True
    return task_id


@pytest.mark.asyncio
async def test_operator_inbox_aggregates_four_item_types(tmp_path: Path) -> None:
    store_group = await create_store_group(
        str(tmp_path / "gateway.db"),
        str(tmp_path / "artifacts"),
    )
    task_service = TaskService(store_group, SSEHub())
    approval_manager = ApprovalManager(event_store=store_group.event_store)
    state_store = TelegramStateStore(tmp_path)
    watchdog_config = WatchdogConfig(
        scan_interval_seconds=15,
        no_progress_threshold_seconds=1,
        failure_window_seconds=60,
        repeated_failure_threshold=2,
        cooldown_seconds=30,
    )

    approval_task = await _create_task(task_service, "approval")
    await approval_manager.register(
        ApprovalRequest(
            approval_id="ap-001",
            task_id=approval_task,
            tool_name="filesystem.write",
            tool_args_summary="echo hello",
            risk_explanation="需要审批",
            policy_label="global.irreversible",
            side_effect_level=SideEffectLevel.IRREVERSIBLE,
            expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        )
    )

    alert_task = await _create_task(task_service, "alert")
    await task_service._write_state_transition(
        task_id=alert_task,
        from_status=TaskStatus.CREATED,
        to_status=TaskStatus.RUNNING,
        trace_id=f"trace-{alert_task}",
    )
    drift_event = await task_service.append_structured_event(
        task_id=alert_task,
        event_type=EventType.TASK_DRIFT_DETECTED,
        actor=ActorType.SYSTEM,
        payload=TaskDriftDetectedPayload(
            drift_type="no_progress",
            detected_at=datetime.now(tz=UTC).isoformat(),
            task_id=alert_task,
            trace_id=f"trace-{alert_task}",
            stall_duration_seconds=120.0,
            suggested_actions=["check_worker_logs"],
        ).model_dump(mode="json"),
    )

    retry_task = await _create_task(task_service, "retry")
    await task_service._write_state_transition(
        task_id=retry_task,
        from_status=TaskStatus.CREATED,
        to_status=TaskStatus.RUNNING,
        trace_id=f"trace-{retry_task}",
    )
    await task_service.append_structured_event(
        task_id=retry_task,
        event_type=EventType.WORKER_RETURNED,
        actor=ActorType.WORKER,
        payload=WorkerReturnedPayload(
            dispatch_id="dispatch-001",
            worker_id="worker.dev",
            status="failed",
            retryable=True,
            summary="docker timeout",
            error_type="timeout",
            error_message="timeout",
        ).model_dump(mode="json"),
    )
    await task_service._write_state_transition(
        task_id=retry_task,
        from_status=TaskStatus.RUNNING,
        to_status=TaskStatus.FAILED,
        trace_id=f"trace-{retry_task}",
    )

    state_store.ensure_pairing_request(
        user_id="1001",
        chat_id="1001",
        username="guest",
        display_name="Guest",
        last_message_text="hello",
    )

    inbox_service = OperatorInboxService(
        store_group=store_group,
        approval_manager=approval_manager,
        telegram_state_store=state_store,
        watchdog_config=watchdog_config,
    )

    inbox = await inbox_service.get_inbox()

    assert inbox.summary.total_pending == 4
    assert inbox.summary.approvals == 1
    assert inbox.summary.alerts == 1
    assert inbox.summary.retryable_failures == 1
    assert inbox.summary.pairing_requests == 1
    assert any(item.item_id == f"alert:{alert_task}:{drift_event.event_id}" for item in inbox.items)

    await store_group.conn.close()


@pytest.mark.asyncio
async def test_operator_inbox_hides_acknowledged_alert(tmp_path: Path) -> None:
    store_group = await create_store_group(
        str(tmp_path / "gateway.db"),
        str(tmp_path / "artifacts"),
    )
    task_service = TaskService(store_group, SSEHub())
    task_id = await _create_task(task_service, "alert-hide")
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
    action_service = OperatorActionService(store_group=store_group, sse_hub=SSEHub())
    await action_service.execute(
        OperatorActionRequest(
            item_id=f"alert:{task_id}:{drift_event.event_id}",
            kind=OperatorActionKind.ACK_ALERT,
            source=OperatorActionSource.WEB,
            actor_id="user:web",
            actor_label="owner",
        )
    )
    inbox_service = OperatorInboxService(
        store_group=store_group,
        approval_manager=ApprovalManager(event_store=store_group.event_store),
        telegram_state_store=TelegramStateStore(tmp_path),
    )

    inbox = await inbox_service.get_inbox()

    assert inbox.summary.alerts == 0
    assert not any(item.kind == "alert" for item in inbox.items)

    await store_group.conn.close()


@pytest.mark.asyncio
async def test_operator_inbox_hides_retryable_failure_after_retry_succeeds(
    tmp_path: Path,
) -> None:
    store_group = await create_store_group(
        str(tmp_path / "gateway.db"),
        str(tmp_path / "artifacts"),
    )
    task_service = TaskService(store_group, SSEHub())
    task_id = await _create_task(task_service, "retry-hide")
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
            summary="docker timeout",
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
    await store_group.task_job_store.create_job(task_id, "retry-hide")
    action_service = OperatorActionService(store_group=store_group, sse_hub=SSEHub())
    await action_service.execute(
        OperatorActionRequest(
            item_id=f"task:{task_id}",
            kind=OperatorActionKind.RETRY_TASK,
            source=OperatorActionSource.WEB,
            actor_id="user:web",
            actor_label="owner",
        )
    )
    inbox_service = OperatorInboxService(
        store_group=store_group,
        approval_manager=ApprovalManager(event_store=store_group.event_store),
        telegram_state_store=TelegramStateStore(tmp_path),
    )

    inbox = await inbox_service.get_inbox()

    assert inbox.summary.retryable_failures == 0
    assert not any(item.item_id == f"task:{task_id}" for item in inbox.items)

    await store_group.conn.close()
