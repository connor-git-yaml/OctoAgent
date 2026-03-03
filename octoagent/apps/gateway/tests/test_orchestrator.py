"""Feature 008: Orchestrator 控制平面测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from octoagent.core.models import RiskLevel, WorkerExecutionStatus
from octoagent.core.models.message import NormalizedMessage
from octoagent.core.store import create_store_group
from octoagent.gateway.services.llm_service import LLMService
from octoagent.gateway.services.orchestrator import OrchestratorService
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.gateway.services.task_service import TaskService
from octoagent.policy.approval_manager import ApprovalManager
from octoagent.policy.models import ApprovalDecision, ApprovalRequest
from octoagent.tooling.models import SideEffectLevel


async def _build_context(tmp_path: Path, approval_manager: ApprovalManager | None = None):
    store_group = await create_store_group(
        str(tmp_path / "orchestrator.db"),
        str(tmp_path / "artifacts"),
    )
    sse_hub = SSEHub()
    llm_service = LLMService()
    task_service = TaskService(store_group, sse_hub)
    orchestrator = OrchestratorService(
        store_group=store_group,
        sse_hub=sse_hub,
        llm_service=llm_service,
        approval_manager=approval_manager,
    )
    return store_group, task_service, orchestrator


class TestOrchestrator:
    async def test_dispatch_success_writes_control_plane_events(
        self, tmp_path: Path
    ) -> None:
        store_group, task_service, orchestrator = await _build_context(tmp_path)

        msg = NormalizedMessage(text="hello orchestrator", idempotency_key="f008-orch-001")
        task_id, created = await task_service.create_task(msg)
        assert created is True

        result = await orchestrator.dispatch(task_id=task_id, user_text=msg.text)
        assert result.status == WorkerExecutionStatus.SUCCEEDED
        assert result.retryable is False

        task = await task_service.get_task(task_id)
        assert task is not None
        assert task.status == "SUCCEEDED"

        events = await store_group.event_store.get_events_for_task(task_id)
        event_types = [event.type for event in events]
        assert "ORCH_DECISION" in event_types
        assert "WORKER_DISPATCHED" in event_types
        assert "WORKER_RETURNED" in event_types

        await store_group.conn.close()

    async def test_routing_hop_guard_fails_before_dispatch(
        self, tmp_path: Path
    ) -> None:
        store_group, task_service, orchestrator = await _build_context(tmp_path)

        msg = NormalizedMessage(text="hop guard", idempotency_key="f008-orch-002")
        task_id, created = await task_service.create_task(msg)
        assert created is True

        result = await orchestrator.dispatch(
            task_id=task_id,
            user_text=msg.text,
            hop_count=3,
            max_hops=3,
        )
        assert result.status == WorkerExecutionStatus.FAILED
        assert result.retryable is False
        assert result.error_type == "OrchestratorRoutingError"

        events = await store_group.event_store.get_events_for_task(task_id)
        event_types = [event.type for event in events]
        assert "ORCH_DECISION" in event_types
        assert "WORKER_DISPATCHED" not in event_types

        task = await task_service.get_task(task_id)
        assert task is not None
        assert task.status == "FAILED"

        await store_group.conn.close()

    async def test_high_risk_task_denied_without_approval(
        self, tmp_path: Path
    ) -> None:
        store_group, task_service, orchestrator = await _build_context(tmp_path)

        msg = NormalizedMessage(text="high risk", idempotency_key="f008-orch-003")
        task_id, created = await task_service.create_task(msg)
        assert created is True

        await store_group.conn.execute(
            "UPDATE tasks SET risk_level = ? WHERE task_id = ?",
            (RiskLevel.HIGH.value, task_id),
        )
        await store_group.conn.commit()

        result = await orchestrator.dispatch(task_id=task_id, user_text=msg.text)
        assert result.status == WorkerExecutionStatus.FAILED
        assert result.retryable is False
        assert result.error_type == "PolicyGateDenied"

        events = await store_group.event_store.get_events_for_task(task_id)
        event_types = [event.type for event in events]
        assert "ORCH_DECISION" in event_types
        assert "WORKER_DISPATCHED" not in event_types

        decision_events = [event for event in events if event.type == "ORCH_DECISION"]
        assert decision_events
        assert decision_events[-1].payload["gate_decision"] == "deny"

        task = await task_service.get_task(task_id)
        assert task is not None
        assert task.status == "REJECTED"

        await store_group.conn.close()

    async def test_high_risk_task_allowed_with_valid_approval_id(
        self, tmp_path: Path
    ) -> None:
        store_group = await create_store_group(
            str(tmp_path / "orchestrator-approved.db"),
            str(tmp_path / "artifacts-approved"),
        )
        sse_hub = SSEHub()
        llm_service = LLMService()
        approval_manager = ApprovalManager(event_store=store_group.event_store)
        task_service = TaskService(store_group, sse_hub)
        orchestrator = OrchestratorService(
            store_group=store_group,
            sse_hub=sse_hub,
            llm_service=llm_service,
            approval_manager=approval_manager,
        )

        msg = NormalizedMessage(text="high risk approved", idempotency_key="f008-orch-005")
        task_id, created = await task_service.create_task(msg)
        assert created is True

        await store_group.conn.execute(
            "UPDATE tasks SET risk_level = ? WHERE task_id = ?",
            (RiskLevel.HIGH.value, task_id),
        )
        await store_group.conn.commit()

        approval_id = "f008-approval-001"
        await approval_manager.register(
            ApprovalRequest(
                approval_id=approval_id,
                task_id=task_id,
                tool_name="orchestrator_dispatch",
                tool_args_summary="dispatch high risk task",
                risk_explanation="high risk task requires approval",
                policy_label="orchestrator.high_risk",
                side_effect_level=SideEffectLevel.IRREVERSIBLE,
                expires_at=datetime.now(UTC) + timedelta(seconds=120),
            )
        )
        await approval_manager.resolve(
            approval_id=approval_id,
            decision=ApprovalDecision.ALLOW_ONCE,
            resolved_by="user:web",
        )

        result = await orchestrator.dispatch(
            task_id=task_id,
            user_text=msg.text,
            metadata={"approval_id": approval_id},
        )
        assert result.status == WorkerExecutionStatus.SUCCEEDED
        assert result.retryable is False
        assert approval_manager.consume_allow_once(approval_id) is False

        await store_group.conn.close()

    async def test_missing_worker_capability_returns_non_retryable_failure(
        self, tmp_path: Path
    ) -> None:
        store_group, task_service, orchestrator = await _build_context(tmp_path)

        msg = NormalizedMessage(text="missing worker", idempotency_key="f008-orch-004")
        task_id, created = await task_service.create_task(msg)
        assert created is True

        result = await orchestrator.dispatch(
            task_id=task_id,
            user_text=msg.text,
            worker_capability="capability.not.exists",
        )
        assert result.status == WorkerExecutionStatus.FAILED
        assert result.retryable is False
        assert result.error_type == "WorkerNotFound"

        events = await store_group.event_store.get_events_for_task(task_id)
        event_types = [event.type for event in events]
        assert "ORCH_DECISION" in event_types
        assert "WORKER_DISPATCHED" not in event_types
        assert "WORKER_RETURNED" in event_types

        task = await task_service.get_task(task_id)
        assert task is not None
        assert task.status == "FAILED"

        await store_group.conn.close()
