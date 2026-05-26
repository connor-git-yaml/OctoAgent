"""F103c Worker Audit Logger — Phase B unit tests.

覆盖 Codex pre-impl review 4 项 finding 的验收：
- PH1：agent_runtime_id 派生 + degraded_reason 必填断言
- PM1：audit_worker_error 将 event_id 传给 state_transition_event_id 做幂等
- PM3：helper 入参 TaskService（不是 StoreGroup），SSE 广播路径成立
- 容错：emit / notify 失败不抛
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from octoagent.core.models.enums import ActorType, EventType
from octoagent.core.models.event import Event, EventCausality
from octoagent.gateway.services.notification import NotificationPriority
from octoagent.gateway.services.worker_audit_logger import (
    DEGRADED_REASON_UNAVAILABLE,
    audit_worker_error,
    audit_worker_log,
    derive_agent_runtime_id,
)


def _fake_event(event_id: str, event_type: EventType, task_id: str) -> Event:
    """构造满足 Event schema 的最小 mock event。"""
    return Event(
        event_id=event_id,
        task_id=task_id,
        task_seq=1,
        ts=datetime.now(UTC),
        type=event_type,
        actor=ActorType.WORKER,
        payload={},
        trace_id=f"trace-{task_id}",
        causality=EventCausality(idempotency_key=None),
    )


# ============================================================
# derive_agent_runtime_id 派生工具（PH1）
# ============================================================


class TestDeriveAgentRuntimeId:
    def test_first_priority_agent_runtime_id(self) -> None:
        result, reason = derive_agent_runtime_id({"agent_runtime_id": "ar-1"})
        assert result == "ar-1"
        assert reason is None

    def test_second_priority_target_agent_runtime_id(self) -> None:
        result, reason = derive_agent_runtime_id(
            {"agent_runtime_id": "", "target_agent_runtime_id": "ar-target"}
        )
        assert result == "ar-target"
        assert reason is None

    def test_third_priority_source_agent_runtime_id(self) -> None:
        result, reason = derive_agent_runtime_id(
            {
                "agent_runtime_id": "",
                "target_agent_runtime_id": "",
                "source_agent_runtime_id": "ar-source",
            }
        )
        assert result == "ar-source"
        assert reason is None

    def test_all_empty_returns_degraded_reason(self) -> None:
        result, reason = derive_agent_runtime_id(
            {
                "agent_runtime_id": "",
                "target_agent_runtime_id": "",
                "source_agent_runtime_id": "",
            }
        )
        assert result == ""
        assert reason == DEGRADED_REASON_UNAVAILABLE

    def test_none_metadata_returns_degraded(self) -> None:
        result, reason = derive_agent_runtime_id(None)
        assert result == ""
        assert reason == DEGRADED_REASON_UNAVAILABLE


# ============================================================
# audit_worker_log（PH1 断言 + 正常路径 + 容错）
# ============================================================


@pytest.mark.asyncio
class TestAuditWorkerLog:
    async def test_normal_path_emits_event(self) -> None:
        mock_event = _fake_event("evt-1", EventType.WORKER_LOG_EMITTED, "task-1")
        task_service = MagicMock()
        task_service.append_structured_event = AsyncMock(return_value=mock_event)

        result = await audit_worker_log(
            task_service,
            task_id="task-1",
            agent_runtime_id="runtime-abc",
            level="warning",
            key="worker_runtime_a2a_heartbeat_failed",
            payload={"loop_step": 3, "error_type": "TimeoutError"},
        )

        assert result is mock_event
        task_service.append_structured_event.assert_awaited_once()
        call_kwargs = task_service.append_structured_event.call_args.kwargs
        assert call_kwargs["event_type"] == EventType.WORKER_LOG_EMITTED
        assert call_kwargs["actor"] == ActorType.WORKER
        assert call_kwargs["payload"]["agent_runtime_id"] == "runtime-abc"
        assert call_kwargs["payload"]["key"] == "worker_runtime_a2a_heartbeat_failed"
        assert call_kwargs["payload"]["payload"]["loop_step"] == 3

    async def test_assertion_when_empty_runtime_id_without_degraded(self) -> None:
        with pytest.raises(AssertionError, match="degraded_reason 必填"):
            await audit_worker_log(
                None,
                task_id="task-x",
                agent_runtime_id="",
                level="warning",
                key="k",
                payload={},
            )

    async def test_empty_runtime_id_with_degraded_reason_ok(self) -> None:
        mock_event = _fake_event("evt-2", EventType.WORKER_LOG_EMITTED, "task-2")
        task_service = MagicMock()
        task_service.append_structured_event = AsyncMock(return_value=mock_event)

        result = await audit_worker_log(
            task_service,
            task_id="task-2",
            agent_runtime_id="",
            degraded_reason=DEGRADED_REASON_UNAVAILABLE,
            level="info",
            key="k",
            payload={},
        )

        assert result is mock_event
        call_kwargs = task_service.append_structured_event.call_args.kwargs
        assert call_kwargs["payload"]["agent_runtime_id"] == ""
        assert call_kwargs["payload"]["degraded_reason"] == DEGRADED_REASON_UNAVAILABLE

    async def test_emit_failure_returns_none_without_raise(self) -> None:
        task_service = MagicMock()
        task_service.append_structured_event = AsyncMock(
            side_effect=RuntimeError("emit boom")
        )

        result = await audit_worker_log(
            task_service,
            task_id="task-3",
            agent_runtime_id="runtime-3",
            level="warning",
            key="k",
            payload={},
        )

        assert result is None

    async def test_none_task_service_only_structlog(self) -> None:
        result = await audit_worker_log(
            None,
            task_id="task-4",
            agent_runtime_id="runtime-4",
            level="warning",
            key="k",
            payload={},
        )
        assert result is None


# ============================================================
# audit_worker_error（PM1 event_id 幂等 + 容错）
# ============================================================


@pytest.mark.asyncio
class TestAuditWorkerError:
    async def test_emits_and_notifies_high_with_event_id(self) -> None:
        mock_event = _fake_event("evt-err-1", EventType.WORKER_ERROR, "task-err-1")
        task_service = MagicMock()
        task_service.append_structured_event = AsyncMock(return_value=mock_event)
        notification_service = MagicMock()
        notification_service.notify_task_state_change = AsyncMock()

        result = await audit_worker_error(
            task_service,
            task_id="task-err-1",
            agent_runtime_id="runtime-err-1",
            error_class="RuntimeError",
            error_summary="dispatch_exception:RuntimeError:something failed",
            notification_service=notification_service,
            task_title="测试任务",
        )

        assert result is mock_event
        task_service.append_structured_event.assert_awaited_once()
        emit_kwargs = task_service.append_structured_event.call_args.kwargs
        assert emit_kwargs["event_type"] == EventType.WORKER_ERROR
        assert emit_kwargs["payload"]["error_class"] == "RuntimeError"

        # PM1 闭环：state_transition_event_id == event.event_id
        notification_service.notify_task_state_change.assert_awaited_once()
        notify_kwargs = notification_service.notify_task_state_change.call_args.kwargs
        assert notify_kwargs["priority"] == NotificationPriority.HIGH
        assert notify_kwargs["state_transition_event_id"] == mock_event.event_id
        assert notify_kwargs["event_type"] == "WORKER_ERROR"
        assert notify_kwargs["payload"]["task_title"] == "测试任务"
        assert notify_kwargs["payload"]["error_class"] == "RuntimeError"

    async def test_notification_service_none_only_emits_event(self) -> None:
        mock_event = _fake_event("evt-err-2", EventType.WORKER_ERROR, "task-err-2")
        task_service = MagicMock()
        task_service.append_structured_event = AsyncMock(return_value=mock_event)

        result = await audit_worker_error(
            task_service,
            task_id="task-err-2",
            agent_runtime_id="runtime-err-2",
            error_class="ValueError",
            error_summary="bad value",
            notification_service=None,
        )

        assert result is mock_event
        task_service.append_structured_event.assert_awaited_once()

    async def test_notify_failure_does_not_raise(self) -> None:
        mock_event = _fake_event("evt-err-3", EventType.WORKER_ERROR, "task-err-3")
        task_service = MagicMock()
        task_service.append_structured_event = AsyncMock(return_value=mock_event)
        notification_service = MagicMock()
        notification_service.notify_task_state_change = AsyncMock(
            side_effect=RuntimeError("notify boom")
        )

        result = await audit_worker_error(
            task_service,
            task_id="task-err-3",
            agent_runtime_id="runtime-err-3",
            error_class="TimeoutError",
            error_summary="slow",
            notification_service=notification_service,
        )

        assert result is mock_event

    async def test_emit_failure_skips_notify(self) -> None:
        task_service = MagicMock()
        task_service.append_structured_event = AsyncMock(
            side_effect=RuntimeError("emit boom")
        )
        notification_service = MagicMock()
        notification_service.notify_task_state_change = AsyncMock()

        result = await audit_worker_error(
            task_service,
            task_id="task-err-4",
            agent_runtime_id="runtime-err-4",
            error_class="RuntimeError",
            error_summary="boom",
            notification_service=notification_service,
        )

        assert result is None
        notification_service.notify_task_state_change.assert_not_awaited()

    async def test_long_summary_payload_validation(self) -> None:
        """error_summary > 200 字符时由 Pydantic schema 拒绝；helper 内部已 [:200] 兜底。"""
        mock_event = _fake_event("evt-err-5", EventType.WORKER_ERROR, "task-err-5")
        task_service = MagicMock()
        task_service.append_structured_event = AsyncMock(return_value=mock_event)

        long_text = "x" * 500
        result = await audit_worker_error(
            task_service,
            task_id="task-err-5",
            agent_runtime_id="runtime-err-5",
            error_class="ValueError",
            error_summary=long_text,
        )

        # helper 内部应该截断 (error_summary[:200])
        assert result is mock_event
        emit_kwargs = task_service.append_structured_event.call_args.kwargs
        assert len(emit_kwargs["payload"]["error_summary"]) == 200
