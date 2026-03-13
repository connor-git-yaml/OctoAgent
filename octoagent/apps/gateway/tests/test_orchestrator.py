"""Feature 008: Orchestrator 控制平面测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from octoagent.core.models import (
    A2AConversation,
    A2AConversationStatus,
    DispatchEnvelope,
    RiskLevel,
    RuntimeControlContext,
    WorkerExecutionStatus,
    WorkerResult,
)
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
        assert "A2A_MESSAGE_SENT" in event_types
        assert "A2A_MESSAGE_RECEIVED" in event_types
        assert "WORKER_DISPATCHED" in event_types
        assert "WORKER_RETURNED" in event_types
        conversations = await store_group.a2a_store.list_conversations(task_id=task_id)
        assert len(conversations) == 1
        messages = await store_group.a2a_store.list_messages(
            a2a_conversation_id=conversations[0].a2a_conversation_id
        )
        assert [item.message_type for item in messages] == ["TASK", "HEARTBEAT", "RESULT"]

        await store_group.conn.close()

    async def test_dispatch_prepared_roundtrips_through_a2a_and_restores_runtime_context(
        self, tmp_path: Path
    ) -> None:
        store_group = await create_store_group(
            str(tmp_path / "orchestrator-a2a.db"),
            str(tmp_path / "artifacts-a2a"),
        )
        sse_hub = SSEHub()
        task_service = TaskService(store_group, sse_hub)

        seen: dict[str, DispatchEnvelope] = {}

        class _CaptureWorker:
            worker_id = "worker.capture"
            capability = "llm_generation"

            async def handle(self, envelope: DispatchEnvelope) -> WorkerResult:
                seen["envelope"] = envelope
                return WorkerResult(
                    dispatch_id=envelope.dispatch_id,
                    task_id=envelope.task_id,
                    worker_id=self.worker_id,
                    status=WorkerExecutionStatus.SUCCEEDED,
                    retryable=False,
                    summary="captured",
                    tool_profile=envelope.tool_profile,
                )

        orchestrator = OrchestratorService(
            store_group=store_group,
            sse_hub=sse_hub,
            llm_service=LLMService(),
            workers={"llm_generation": _CaptureWorker()},
        )

        msg = NormalizedMessage(text="capture a2a", idempotency_key="f008-orch-a2a")
        task_id, created = await task_service.create_task(msg)
        assert created is True

        envelope = DispatchEnvelope(
            dispatch_id="dispatch-a2a",
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            contract_version="1.0",
            route_reason="worker_type=general",
            worker_capability="llm_generation",
            hop_count=1,
            max_hops=3,
            user_text=msg.text,
            model_alias="main",
            tool_profile="minimal",
            runtime_context=RuntimeControlContext(
                task_id=task_id,
                trace_id=f"trace-{task_id}",
                session_id="session-a2a",
                project_id="project-default",
                workspace_id="workspace-default",
                tool_profile="minimal",
                work_id="work-a2a",
            ),
            metadata={
                "work_id": "work-a2a",
                "runtime_context_json": RuntimeControlContext(
                    task_id=task_id,
                    trace_id=f"trace-{task_id}",
                    session_id="session-a2a",
                    project_id="project-default",
                    workspace_id="workspace-default",
                    tool_profile="minimal",
                    work_id="work-a2a",
                ).model_dump_json(),
            },
        )

        result = await orchestrator.dispatch_prepared(envelope)
        assert result.status == WorkerExecutionStatus.SUCCEEDED
        captured = seen["envelope"]
        conversation = await store_group.a2a_store.get_conversation_for_work("work-a2a")
        assert conversation is not None
        assert captured.metadata["a2a_message_id"] == "dispatch-a2a"
        assert captured.metadata["a2a_context_id"] == conversation.a2a_conversation_id
        assert captured.metadata["a2a_to_agent"] == "agent://worker.capture"
        assert captured.metadata["a2a_conversation_id"] == conversation.a2a_conversation_id
        assert captured.metadata["source_agent_session_id"]
        assert captured.metadata["agent_session_id"]
        assert captured.runtime_context is not None
        assert captured.runtime_context.session_id == "session-a2a"
        assert captured.runtime_context.metadata["agent_session_id"] == captured.metadata[
            "agent_session_id"
        ]

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

    async def test_record_cancel_marks_all_active_a2a_conversations_for_task(
        self, tmp_path: Path
    ) -> None:
        store_group, task_service, orchestrator = await _build_context(tmp_path)

        msg = NormalizedMessage(text="cancel all a2a", idempotency_key="f008-orch-cancel-all")
        task_id, created = await task_service.create_task(msg)
        assert created is True

        active_conversation = A2AConversation(
            a2a_conversation_id="conv-active",
            task_id=task_id,
            work_id="work-active",
            source_agent="agent://butler.main",
            target_agent="agent://worker.active",
            status=A2AConversationStatus.ACTIVE,
        )
        waiting_conversation = A2AConversation(
            a2a_conversation_id="conv-waiting",
            task_id=task_id,
            work_id="work-waiting",
            source_agent="agent://butler.main",
            target_agent="agent://worker.waiting",
            status=A2AConversationStatus.WAITING_INPUT,
        )
        completed_conversation = A2AConversation(
            a2a_conversation_id="conv-completed",
            task_id=task_id,
            work_id="work-completed",
            source_agent="agent://butler.main",
            target_agent="agent://worker.completed",
            status=A2AConversationStatus.COMPLETED,
        )
        await store_group.a2a_store.save_conversation(active_conversation)
        await store_group.a2a_store.save_conversation(waiting_conversation)
        await store_group.a2a_store.save_conversation(completed_conversation)
        await store_group.conn.commit()

        await orchestrator.record_cancel(task_id=task_id, reason="user_cancelled_all")

        conversations = await store_group.a2a_store.list_conversations(task_id=task_id, limit=None)
        statuses = {conversation.work_id: conversation.status for conversation in conversations}
        assert statuses == {
            "work-active": A2AConversationStatus.CANCELLED,
            "work-waiting": A2AConversationStatus.CANCELLED,
            "work-completed": A2AConversationStatus.COMPLETED,
        }

        active_messages = await store_group.a2a_store.list_messages(
            a2a_conversation_id=active_conversation.a2a_conversation_id
        )
        waiting_messages = await store_group.a2a_store.list_messages(
            a2a_conversation_id=waiting_conversation.a2a_conversation_id
        )
        completed_messages = await store_group.a2a_store.list_messages(
            a2a_conversation_id=completed_conversation.a2a_conversation_id
        )
        assert [message.message_type for message in active_messages] == ["CANCEL"]
        assert [message.message_type for message in waiting_messages] == ["CANCEL"]
        assert completed_messages == []

        await store_group.conn.close()
