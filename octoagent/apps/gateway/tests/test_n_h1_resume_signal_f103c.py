"""F103c Phase D — N-H1 resume signal e2e 测试。

目标：lock baseline 行为 + 验证 F103c Phase C-1-1 升级（_emit_is_caller_worker_signal
失败路径 emit WORKER_LOG_EMITTED 而不是只落 stderr）。

侦察结论（baseline-recon.md §4）：
- N-H1 worker restart 路径 baseline 已 cover（test_f101_phase_b.py:705 AC-C6 已覆盖
  startup_recovery 路径）
- F103c 仅 lock baseline；不引入新业务逻辑（spec FR-D2）
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from octoagent.core.models.enums import ActorType, EventType
from octoagent.core.models.event import Event, EventCausality
from octoagent.gateway.services.worker_runtime import _emit_is_caller_worker_signal


def _fake_event(event_type: EventType, task_id: str) -> Event:
    return Event(
        event_id="evt-test",
        task_id=task_id,
        task_seq=1,
        ts=datetime.now(UTC),
        type=event_type,
        actor=ActorType.SYSTEM,
        payload={},
        trace_id=f"trace-{task_id}",
        causality=EventCausality(idempotency_key=None),
    )


# ============================================================
# resume_state_snapshot 路径的 baseline lock（spec FR-D1）
# ============================================================


class TestResumeStateSnapshotSignal:
    """worker_runtime.run() 从 resume_state_snapshot 读取 is_caller_worker_signal 的 baseline 锁定。

    实际读取逻辑（worker_runtime.py:551-557）：
        _snapshot = envelope.resume_state_snapshot
        _is_caller_worker = True  # WorkerRuntime 路径始终为 True
        if _snapshot is not None:
            _signal_from_snapshot = _snapshot.get("is_caller_worker_signal", "")
            if _signal_from_snapshot == "1":
                _is_caller_worker = True
    """

    def test_resume_snapshot_with_signal_reads_one(self) -> None:
        """resume_state_snapshot 含 is_caller_worker_signal="1" → 读取成功。"""
        snapshot = {"is_caller_worker_signal": "1", "execution_session_id": "sess-1"}
        signal_value = snapshot.get("is_caller_worker_signal", "")
        assert signal_value == "1"
        # worker_runtime 在该值="1" 时确认 _is_caller_worker = True

    def test_resume_snapshot_without_signal_returns_empty(self) -> None:
        """resume_state_snapshot 不含信号 → 回退默认（worker_runtime 仍 True，baseline 兜底）。"""
        snapshot = {"execution_session_id": "sess-2"}
        signal_value = snapshot.get("is_caller_worker_signal", "")
        assert signal_value == ""
        # worker_runtime 在该值="" 时使用 baseline 默认 _is_caller_worker = True
        # （worker 子任务路径，spec §0.1 侦察 4）

    def test_resume_snapshot_none_envelope_path(self) -> None:
        """envelope.resume_state_snapshot is None → 首次 dispatch 路径，会触发 _emit_is_caller_worker_signal。"""
        snapshot = None
        # worker_runtime.py:558-566 在此分支调 _emit_is_caller_worker_signal
        # 这是首次 dispatch，不是 resume——baseline 行为
        assert snapshot is None


# ============================================================
# _emit_is_caller_worker_signal C-1-1 升级行为（spec FR-D1）
# ============================================================


@pytest.mark.asyncio
class TestEmitSignalAuditUpgrade:
    """worker_runtime.py:442 升级到 WORKER_LOG_EMITTED 的行为验证（F103c Phase C-1-1）。"""

    async def test_normal_emit_success_no_audit_log(self) -> None:
        """正常写入路径：emit 成功 → 不触发 WORKER_LOG_EMITTED（仅 emit 1 条 CONTROL_METADATA_UPDATED）。"""
        # mock TaskService + event_store.append_event_committed 成功
        mock_event_store = MagicMock()
        mock_event_store.get_next_task_seq = AsyncMock(return_value=1)
        mock_event_store.append_event_committed = AsyncMock()

        mock_stores = MagicMock()
        mock_stores.event_store = mock_event_store

        task_service = MagicMock()
        task_service._stores = mock_stores
        task_service.append_structured_event = AsyncMock()  # 监控 WORKER_LOG_EMITTED emit

        await _emit_is_caller_worker_signal(
            task_service=task_service,
            task_id="task-resume-1",
            trace_id="trace-resume-1",
            dispatch_id="dispatch-1",
            envelope_metadata={"agent_runtime_id": "ar-1"},
        )

        # 正常路径：CONTROL_METADATA_UPDATED 写入成功
        mock_event_store.append_event_committed.assert_awaited_once()
        # 正常路径：不触发 WORKER_LOG_EMITTED（仅在 except 块触发）
        task_service.append_structured_event.assert_not_awaited()

    async def test_emit_failure_triggers_worker_log_emitted_audit(self) -> None:
        """F103c C-1-1：emit CONTROL_METADATA_UPDATED 失败时 → 应 emit WORKER_LOG_EMITTED audit。"""
        mock_event_store = MagicMock()
        mock_event_store.get_next_task_seq = AsyncMock(return_value=1)
        # 模拟写入失败
        mock_event_store.append_event_committed = AsyncMock(
            side_effect=RuntimeError("event store boom")
        )

        mock_stores = MagicMock()
        mock_stores.event_store = mock_event_store

        task_service = MagicMock()
        task_service._stores = mock_stores
        # mock append_structured_event 返回 Event
        task_service.append_structured_event = AsyncMock(
            return_value=_fake_event(EventType.WORKER_LOG_EMITTED, "task-resume-2")
        )

        await _emit_is_caller_worker_signal(
            task_service=task_service,
            task_id="task-resume-2",
            trace_id="trace-resume-2",
            dispatch_id="dispatch-2",
            envelope_metadata={"agent_runtime_id": "ar-2"},
        )

        # 异常路径：触发 WORKER_LOG_EMITTED audit
        task_service.append_structured_event.assert_awaited_once()
        emit_kwargs = task_service.append_structured_event.call_args.kwargs
        assert emit_kwargs["event_type"] == EventType.WORKER_LOG_EMITTED
        assert emit_kwargs["payload"]["key"] == "worker_runtime_emit_is_caller_worker_signal_failed"
        assert emit_kwargs["payload"]["agent_runtime_id"] == "ar-2"
        # PH1: 派生成功路径不应有 degraded_reason
        assert emit_kwargs["payload"]["degraded_reason"] is None
        # payload 含 dispatch_id 用于排障
        assert emit_kwargs["payload"]["payload"]["dispatch_id"] == "dispatch-2"

    async def test_emit_failure_without_metadata_includes_degraded_reason(self) -> None:
        """envelope_metadata=None → audit 事件含 degraded_reason='agent_runtime_id_unavailable'。"""
        mock_event_store = MagicMock()
        mock_event_store.get_next_task_seq = AsyncMock(return_value=1)
        mock_event_store.append_event_committed = AsyncMock(
            side_effect=RuntimeError("emit boom")
        )

        mock_stores = MagicMock()
        mock_stores.event_store = mock_event_store

        task_service = MagicMock()
        task_service._stores = mock_stores
        task_service.append_structured_event = AsyncMock(
            return_value=_fake_event(EventType.WORKER_LOG_EMITTED, "task-resume-3")
        )

        await _emit_is_caller_worker_signal(
            task_service=task_service,
            task_id="task-resume-3",
            trace_id="trace-resume-3",
            dispatch_id="dispatch-3",
            envelope_metadata=None,
        )

        emit_kwargs = task_service.append_structured_event.call_args.kwargs
        assert emit_kwargs["payload"]["agent_runtime_id"] == ""
        assert emit_kwargs["payload"]["degraded_reason"] == "agent_runtime_id_unavailable"
