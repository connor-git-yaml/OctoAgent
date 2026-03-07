from datetime import UTC, datetime

from octoagent.core.models import (
    OperatorActionKind,
    OperatorActionOutcome,
    OperatorActionRequest,
    OperatorActionResult,
    OperatorActionSource,
    OperatorInboxItem,
    OperatorInboxResponse,
    OperatorInboxSummary,
    OperatorItemKind,
    OperatorItemState,
    OperatorQuickAction,
    RetryLaunchRef,
)


def test_operator_models_serialize_recent_action_result() -> None:
    result = OperatorActionResult(
        item_id="task:task-001",
        kind=OperatorActionKind.RETRY_TASK,
        source=OperatorActionSource.WEB,
        outcome=OperatorActionOutcome.SUCCEEDED,
        message="已创建新的重试任务",
        task_id="task-001",
        retry_launch=RetryLaunchRef(
            source_task_id="task-001",
            result_task_id="task-002",
        ),
        handled_at=datetime(2026, 3, 7, 12, 0, tzinfo=UTC),
    )
    item = OperatorInboxItem(
        item_id="task:task-001",
        kind=OperatorItemKind.RETRYABLE_FAILURE,
        state=OperatorItemState.PENDING,
        title="任务可重试",
        summary="worker 返回 retryable failure",
        task_id="task-001",
        thread_id="thread-001",
        source_ref="event-001",
        created_at=datetime(2026, 3, 7, 11, 59, tzinfo=UTC),
        pending_age_seconds=60.0,
        quick_actions=[
            OperatorQuickAction(
                kind=OperatorActionKind.RETRY_TASK,
                label="重试",
                style="primary",
            )
        ],
        recent_action_result=result,
    )
    response = OperatorInboxResponse(
        summary=OperatorInboxSummary(
            total_pending=1,
            approvals=0,
            alerts=0,
            retryable_failures=1,
            pairing_requests=0,
            degraded_sources=[],
            generated_at=datetime(2026, 3, 7, 12, 0, tzinfo=UTC),
        ),
        items=[item],
    )

    payload = response.model_dump(mode="json")

    assert payload["items"][0]["kind"] == "retryable_failure"
    assert (
        payload["items"][0]["recent_action_result"]["retry_launch"]["result_task_id"]
        == "task-002"
    )


def test_operator_action_request_defaults_to_empty_actor_fields() -> None:
    request = OperatorActionRequest(
        item_id="approval:ap-001",
        kind=OperatorActionKind.APPROVE_ONCE,
        source=OperatorActionSource.TELEGRAM,
    )

    assert request.actor_id == ""
    assert request.actor_label == ""
