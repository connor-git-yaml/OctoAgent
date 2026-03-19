"""Feature 008: Orchestrator 控制平面测试。"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from octoagent.core.models import (
    A2AConversation,
    A2AConversationStatus,
    AgentRuntime,
    AgentRuntimeRole,
    AgentRuntimeStatus,
    AgentSession,
    AgentSessionKind,
    ContextFrame,
    DispatchEnvelope,
    Project,
    ProjectSelectorState,
    RiskLevel,
    RuntimeControlContext,
    SessionContextState,
    TaskStatus,
    WorkerExecutionStatus,
    WorkerResult,
    Workspace,
)
from octoagent.core.models.message import NormalizedMessage
from octoagent.core.store import create_store_group
from octoagent.gateway.services.agent_context import (
    build_agent_runtime_id,
    build_agent_session_id,
    build_scope_aware_session_id,
)
from octoagent.gateway.services.capability_pack import CapabilityPackService
from octoagent.gateway.services.delegation_plane import DelegationPlaneService
from octoagent.gateway.services.llm_service import LLMService
from octoagent.gateway.services.orchestrator import OrchestratorService
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.gateway.services.task_service import TaskService
from octoagent.policy.approval_manager import ApprovalManager
from octoagent.policy.models import ApprovalDecision, ApprovalRequest
from octoagent.provider import ModelCallResult, TokenUsage
from octoagent.tooling import ToolBroker
from octoagent.tooling.models import SideEffectLevel


async def _build_context(
    tmp_path: Path,
    approval_manager: ApprovalManager | None = None,
    *,
    llm_service=None,
):
    store_group = await create_store_group(
        str(tmp_path / "orchestrator.db"),
        str(tmp_path / "artifacts"),
    )
    sse_hub = SSEHub()
    resolved_llm_service = llm_service or LLMService()
    task_service = TaskService(store_group, sse_hub)
    orchestrator = OrchestratorService(
        store_group=store_group,
        sse_hub=sse_hub,
        llm_service=resolved_llm_service,
        approval_manager=approval_manager,
    )
    return store_group, task_service, orchestrator


class _FreshnessLLMService:
    def __init__(self) -> None:
        self.handoff_roles: list[str] = []

    async def call(
        self,
        prompt_or_messages,
        model_alias: str | None = None,
        *,
        task_id: str | None = None,
        trace_id: str | None = None,
        metadata: dict | None = None,
        worker_capability: str | None = None,
        tool_profile: str | None = None,
    ) -> ModelCallResult:
        metadata = metadata or {}
        if isinstance(prompt_or_messages, str):
            joined = prompt_or_messages
        else:
            for item in prompt_or_messages:
                if "ResearchHandoff:" not in str(item.get("content", "")):
                    continue
                role = str(item.get("role", "")).strip()
                if role:
                    self.handoff_roles.append(role)
            joined = "\n\n".join(str(item.get("content", "")) for item in prompt_or_messages)
        if str(metadata.get("selected_worker_type", "")).strip() == "research":
            content = "Research 结论：深圳当前约 21°C，晴，降水概率约 0%。"
        elif "ResearchHandoff:" in joined:
            content = "Butler 综合答复：深圳今天大致晴，约 21°C，基本不用担心下雨。"
        else:
            content = "Butler 常规答复。"
        return ModelCallResult(
            content=content,
            model_alias=model_alias or "main",
            model_name="test-model",
            provider="tests",
            duration_ms=5,
            token_usage=TokenUsage(prompt_tokens=12, completion_tokens=8, total_tokens=20),
            cost_usd=0.0,
            cost_unavailable=False,
            is_fallback=False,
            fallback_reason="",
        )


class _FailingFreshnessLLMService(_FreshnessLLMService):
    async def call(
        self,
        prompt_or_messages,
        model_alias: str | None = None,
        *,
        task_id: str | None = None,
        trace_id: str | None = None,
        metadata: dict | None = None,
        worker_capability: str | None = None,
        tool_profile: str | None = None,
    ) -> ModelCallResult:
        metadata = metadata or {}
        if str(metadata.get("selected_worker_type", "")).strip() == "research":
            raise RuntimeError("web search failed: ConnectError: network down")
        return await super().call(
            prompt_or_messages,
            model_alias=model_alias,
            task_id=task_id,
            trace_id=trace_id,
            metadata=metadata,
            worker_capability=worker_capability,
            tool_profile=tool_profile,
        )


class _SingleLoopLLMService:
    supports_single_loop_executor = True
    supports_butler_decision_phase = True
    supports_recall_planning_phase = True

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def call(
        self,
        prompt_or_messages,
        model_alias: str | None = None,
        *,
        task_id: str | None = None,
        trace_id: str | None = None,
        metadata: dict | None = None,
        worker_capability: str | None = None,
        tool_profile: str | None = None,
    ) -> ModelCallResult:
        self.calls.append(
            {
                "prompt_or_messages": prompt_or_messages,
                "model_alias": model_alias,
                "task_id": task_id,
                "trace_id": trace_id,
                "metadata": dict(metadata or {}),
                "worker_capability": worker_capability,
                "tool_profile": tool_profile,
            }
        )
        return ModelCallResult(
            content="单循环主执行器已直接完成本轮答复。",
            model_alias=model_alias or "main",
            model_name="single-loop-test",
            provider="tests",
            duration_ms=5,
            token_usage=TokenUsage(prompt_tokens=12, completion_tokens=10, total_tokens=22),
            cost_usd=0.0,
            cost_unavailable=False,
            is_fallback=False,
            fallback_reason="",
        )


async def _build_freshness_context(
    tmp_path: Path,
    *,
    llm_service: _FreshnessLLMService | None = None,
):
    store_group = await create_store_group(
        str(tmp_path / "orchestrator-freshness.db"),
        str(tmp_path / "artifacts-freshness"),
    )
    await store_group.project_store.create_project(
        Project(
            project_id="project-default",
            slug="default",
            name="Default Project",
            is_default=True,
        )
    )
    await store_group.project_store.create_workspace(
        Workspace(
            workspace_id="workspace-default",
            project_id="project-default",
            slug="primary",
            name="Primary",
            root_path=str(tmp_path),
        )
    )
    await store_group.project_store.save_selector_state(
        ProjectSelectorState(
            selector_id="selector-web",
            surface="web",
            active_project_id="project-default",
            active_workspace_id="workspace-default",
            source="tests",
        )
    )
    await store_group.conn.commit()

    sse_hub = SSEHub()
    task_service = TaskService(store_group, sse_hub)
    tool_broker = ToolBroker(event_store=store_group.event_store)
    capability_pack = CapabilityPackService(
        project_root=tmp_path,
        store_group=store_group,
        tool_broker=tool_broker,
    )
    delegation_plane = DelegationPlaneService(
        project_root=tmp_path,
        store_group=store_group,
        sse_hub=sse_hub,
        capability_pack=capability_pack,
    )
    await capability_pack.startup()
    resolved_llm_service = llm_service or _FreshnessLLMService()
    orchestrator = OrchestratorService(
        store_group=store_group,
        sse_hub=sse_hub,
        llm_service=resolved_llm_service,
        delegation_plane=delegation_plane,
    )
    return store_group, task_service, orchestrator


async def _build_freshness_failure_context(tmp_path: Path):
    store_group = await create_store_group(
        str(tmp_path / "orchestrator-freshness-failure.db"),
        str(tmp_path / "artifacts-freshness-failure"),
    )
    await store_group.project_store.create_project(
        Project(
            project_id="project-default",
            slug="default",
            name="Default Project",
            is_default=True,
        )
    )
    await store_group.project_store.create_workspace(
        Workspace(
            workspace_id="workspace-default",
            project_id="project-default",
            slug="primary",
            name="Primary",
            root_path=str(tmp_path),
        )
    )
    await store_group.project_store.save_selector_state(
        ProjectSelectorState(
            selector_id="selector-web",
            surface="web",
            active_project_id="project-default",
            active_workspace_id="workspace-default",
            source="tests",
        )
    )
    await store_group.conn.commit()

    sse_hub = SSEHub()
    task_service = TaskService(store_group, sse_hub)
    tool_broker = ToolBroker(event_store=store_group.event_store)
    capability_pack = CapabilityPackService(
        project_root=tmp_path,
        store_group=store_group,
        tool_broker=tool_broker,
    )
    delegation_plane = DelegationPlaneService(
        project_root=tmp_path,
        store_group=store_group,
        sse_hub=sse_hub,
        capability_pack=capability_pack,
    )
    await capability_pack.startup()
    orchestrator = OrchestratorService(
        store_group=store_group,
        sse_hub=sse_hub,
        llm_service=_FailingFreshnessLLMService(),
        delegation_plane=delegation_plane,
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
        # Feature 064 Phase 1: Butler Direct Execution 路径不经过 Worker 派发，
        # 因此不产生 A2A/Worker 事件，但保留 MODEL_CALL 和 ARTIFACT 事件。
        assert "MODEL_CALL_STARTED" in event_types
        assert "MODEL_CALL_COMPLETED" in event_types
        assert "ARTIFACT_CREATED" in event_types

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

        # Feature 064 Phase 1: 使用 parent_task_id 标记为子任务，
        # 绕过 Butler Direct Execution 路径，确保请求走 Worker Dispatch
        # 路径以触发 hop guard 检查。
        result = await orchestrator.dispatch(
            task_id=task_id,
            user_text=msg.text,
            hop_count=3,
            max_hops=3,
            metadata={"parent_task_id": "parent-hop-guard"},
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

    async def test_freshness_query_runs_research_child_then_butler_reply(
        self,
        tmp_path: Path,
    ) -> None:
        store_group, task_service, orchestrator = await _build_freshness_context(tmp_path)

        try:
            msg = NormalizedMessage(
                text="深圳今天天气怎么样？",
                idempotency_key="f041-orch-freshness",
            )
            task_id, created = await task_service.create_task(msg)
            assert created is True

            result = await orchestrator.dispatch(task_id=task_id, user_text=msg.text)
            assert result.status == WorkerExecutionStatus.SUCCEEDED
            assert result.worker_id == "butler.main"

            parent_task = await task_service.get_task(task_id)
            assert parent_task is not None
            assert parent_task.status == TaskStatus.SUCCEEDED

            parent_events = await store_group.event_store.get_events_for_task(task_id)
            parent_event_types = [event.type for event in parent_events]
            assert parent_event_types.count("MODEL_CALL_COMPLETED") == 1

            parent_completion = next(
                event
                for event in reversed(parent_events)
                if event.type == "MODEL_CALL_COMPLETED"
            )
            parent_artifact_id = parent_completion.payload["artifact_ref"]
            parent_content = (
                await store_group.artifact_store.get_artifact_content(parent_artifact_id)
            ).decode("utf-8")
            assert "Butler 综合答复" in parent_content

            parent_works = await store_group.work_store.list_works(task_id=task_id)
            assert len(parent_works) == 1
            parent_work = parent_works[0]
            assert parent_work.selected_worker_type == "general"
            assert parent_work.metadata["delegation_strategy"] == "butler_owned_freshness"
            assert parent_work.metadata["research_tool_profile"] == "standard"

            child_task_id = parent_work.metadata["research_child_task_id"]
            child_task = await task_service.get_task(child_task_id)
            assert child_task is not None
            assert child_task.status == TaskStatus.SUCCEEDED

            child_events = await store_group.event_store.get_events_for_task(child_task_id)
            child_event_types = [event.type for event in child_events]
            assert "A2A_MESSAGE_SENT" in child_event_types
            assert "A2A_MESSAGE_RECEIVED" in child_event_types
            assert child_event_types.count("MODEL_CALL_COMPLETED") == 1

            child_completion = next(
                event
                for event in reversed(child_events)
                if event.type == "MODEL_CALL_COMPLETED"
            )
            child_artifact_id = child_completion.payload["artifact_ref"]
            child_content = (
                await store_group.artifact_store.get_artifact_content(child_artifact_id)
            ).decode("utf-8")
            assert "Research 结论" in child_content

            child_works = await store_group.work_store.list_works(task_id=child_task_id)
            assert len(child_works) == 1
            child_work = child_works[0]
            assert child_work.parent_work_id == parent_work.work_id

            conversation = await store_group.a2a_store.get_conversation_for_work(child_work.work_id)
            assert conversation is not None
            expected_runtime_id = build_agent_runtime_id(
                role=AgentRuntimeRole.BUTLER,
                project_id="project-default",
                workspace_id="workspace-default",
                agent_profile_id=parent_work.agent_profile_id,
                worker_profile_id="",
                worker_capability="",
            )
            expected_session_id = build_agent_session_id(
                agent_runtime_id=expected_runtime_id,
                kind=AgentSessionKind.BUTLER_MAIN,
                legacy_session_id=build_scope_aware_session_id(
                    parent_task,
                    project_id="project-default",
                    workspace_id="workspace-default",
                ),
                work_id="",
                task_id=task_id,
            )
            assert conversation.source_agent_runtime_id == expected_runtime_id
            assert conversation.source_agent_session_id == expected_session_id
            assert conversation.target_agent_session_id
            assert parent_work.metadata["research_a2a_message_count"] == conversation.message_count
            assert (
                parent_work.metadata["research_a2a_conversation_id"]
                == conversation.a2a_conversation_id
            )
            assert (
                parent_work.metadata["research_butler_agent_session_id"]
                == conversation.source_agent_session_id
            )
            assert (
                parent_work.metadata["research_worker_agent_session_id"]
                == conversation.target_agent_session_id
            )
        finally:
            await store_group.conn.close()

    async def test_under_specified_work_priority_request_no_longer_uses_compat_fallback(
        self,
        tmp_path: Path,
    ) -> None:
        store_group, task_service, orchestrator = await _build_context(tmp_path)

        try:
            msg = NormalizedMessage(
                text="帮我把今天下午的工作拆成 3 个优先级，并给我一个先做什么后做什么的顺序。",
                idempotency_key="f049-orch-clarify-work-priority",
            )
            task_id, created = await task_service.create_task(msg)
            assert created is True

            result = await orchestrator.dispatch(task_id=task_id, user_text=msg.text)
            assert result.status == WorkerExecutionStatus.SUCCEEDED
            # Feature 064 Phase 1: Butler Direct Execution 路径处理，
            # worker_id 为 butler.main
            assert result.worker_id == "butler.main"
            assert result.summary != "butler_clarification:work_priority_context"
        finally:
            await store_group.conn.close()

    async def test_weather_without_delegation_plane_uses_single_loop_executor(
        self,
        tmp_path: Path,
    ) -> None:
        store_group, task_service, orchestrator = await _build_context(tmp_path)

        try:
            msg = NormalizedMessage(
                text="今天天气怎么样？",
                idempotency_key="f049-orch-weather-without-plane",
            )
            task_id, created = await task_service.create_task(msg)
            assert created is True

            result = await orchestrator.dispatch(task_id=task_id, user_text=msg.text)
            assert result.status == WorkerExecutionStatus.SUCCEEDED
            # Feature 064 Phase 1: Butler Direct Execution 路径处理天气查询
            assert result.worker_id == "butler.main"
            assert result.summary != "butler_clarification:weather_location"

            events = await store_group.event_store.get_events_for_task(task_id)
            assert [event.type for event in events].count("MODEL_CALL_COMPLETED") == 1
        finally:
            await store_group.conn.close()

    async def test_single_loop_executor_bypasses_butler_decision_preflight(
        self,
        tmp_path: Path,
    ) -> None:
        llm_service = _SingleLoopLLMService()
        store_group, task_service, orchestrator = await _build_context(
            tmp_path,
            llm_service=llm_service,
        )

        try:
            msg = NormalizedMessage(
                text="今天天气怎么样？",
                idempotency_key="f051-single-loop-orchestrator-001",
            )
            task_id, created = await task_service.create_task(msg)
            assert created is True

            result = await orchestrator.dispatch(task_id=task_id, user_text=msg.text)
            assert result.status == WorkerExecutionStatus.SUCCEEDED
            # Feature 064 Phase 1: Butler Direct Execution 路径处理
            assert result.worker_id == "butler.main"

            assert len(llm_service.calls) == 1
            metadata = llm_service.calls[0]["metadata"]
            assert isinstance(metadata, dict)
            # Butler Direct Execution 设置 butler_execution_mode=direct
            assert metadata.get("butler_execution_mode") == "direct"
            assert "decision_phase" not in metadata

            artifacts = await store_group.artifact_store.list_artifacts_for_task(task_id)
            artifact_names = {item.name for item in artifacts}
            assert "butler-decision-request" not in artifact_names
            assert "butler-decision-response" not in artifact_names
        finally:
            await store_group.conn.close()

    async def test_single_loop_executor_supports_explicit_research_worker_lens(
        self,
        tmp_path: Path,
    ) -> None:
        llm_service = _SingleLoopLLMService()
        store_group, task_service, orchestrator = await _build_context(
            tmp_path,
            llm_service=llm_service,
        )

        try:
            msg = NormalizedMessage(
                text="查一下 Alpha 最近的公开资料并汇总关键变化",
                idempotency_key="f051-single-loop-research-001",
            )
            task_id, created = await task_service.create_task(msg)
            assert created is True

            result = await orchestrator.dispatch(
                task_id=task_id,
                user_text=msg.text,
                metadata={"requested_worker_type": "research"},
            )
            assert result.status == WorkerExecutionStatus.SUCCEEDED
            assert result.worker_id == "worker.llm.default"

            assert len(llm_service.calls) == 1
            metadata = llm_service.calls[0]["metadata"]
            assert isinstance(metadata, dict)
            assert metadata["single_loop_executor"] is True
            assert metadata["selected_worker_type"] == "research"
            assert metadata["single_loop_executor_mode"] == "butler_research"
            assert "decision_phase" not in metadata
        finally:
            await store_group.conn.close()

    async def test_single_loop_executor_supports_requested_worker_profile_id(
        self,
        tmp_path: Path,
    ) -> None:
        llm_service = _SingleLoopLLMService()
        store_group, task_service, orchestrator = await _build_context(
            tmp_path,
            llm_service=llm_service,
        )

        try:
            msg = NormalizedMessage(
                text="查一下 Alpha 最近的公开资料并汇总关键变化",
                idempotency_key="f053-single-loop-research-profile-001",
            )
            task_id, created = await task_service.create_task(msg)
            assert created is True

            result = await orchestrator.dispatch(
                task_id=task_id,
                user_text=msg.text,
                metadata={"requested_worker_profile_id": "singleton:research"},
            )
            assert result.status == WorkerExecutionStatus.SUCCEEDED
            assert result.worker_id == "worker.llm.default"

            assert len(llm_service.calls) == 1
            metadata = llm_service.calls[0]["metadata"]
            assert isinstance(metadata, dict)
            assert metadata["single_loop_executor"] is True
            assert metadata["selected_worker_type"] == "research"
            assert metadata["requested_worker_type"] == "research"
            assert metadata["requested_worker_profile_id"] == "singleton:research"
            assert metadata["single_loop_executor_mode"] == "butler_research"
            assert metadata["requested_worker_type_source"] == "requested_worker_profile_id"
            assert "decision_phase" not in metadata
        finally:
            await store_group.conn.close()

    async def test_recent_conversation_prefers_agent_session_transcript(
        self,
        tmp_path: Path,
    ) -> None:
        store_group, task_service, orchestrator = await _build_context(tmp_path)

        try:
            msg = NormalizedMessage(
                text="深圳",
                idempotency_key="f051-session-transcript",
            )
            task_id, created = await task_service.create_task(msg)
            assert created is True

            await store_group.agent_context_store.save_agent_runtime(
                AgentRuntime(
                    agent_runtime_id="runtime-butler",
                    role=AgentRuntimeRole.BUTLER,
                    status=AgentRuntimeStatus.ACTIVE,
                )
            )
            agent_session = AgentSession(
                agent_session_id="agent-session-transcript",
                agent_runtime_id="runtime-butler",
                kind=AgentSessionKind.BUTLER_MAIN,
                metadata={
                    "recent_transcript": [
                        {"role": "user", "content": "今天天气怎么样？", "task_id": "task-old-1"},
                        {
                            "role": "assistant",
                            "content": "我还缺少城市 / 区县信息。",
                            "task_id": "task-old-1",
                        },
                        {"role": "user", "content": "深圳", "task_id": task_id},
                    ],
                    "latest_model_reply_summary": "我还缺少城市 / 区县信息。",
                },
            )
            await store_group.agent_context_store.save_agent_session(agent_session)
            await store_group.agent_context_store.save_session_context(
                SessionContextState(
                    session_id="session-transcript",
                    agent_session_id=agent_session.agent_session_id,
                    thread_id="thread-transcript",
                    task_ids=[task_id],
                    recent_turn_refs=["missing-task-ref"],
                    rolling_summary="用户正在补充天气地点。",
                )
            )
            await store_group.agent_context_store.save_context_frame(
                ContextFrame(
                    context_frame_id="frame-transcript",
                    task_id=task_id,
                    session_id="session-transcript",
                    agent_runtime_id="runtime-butler",
                    agent_session_id=agent_session.agent_session_id,
                )
            )
            await store_group.conn.commit()

            block = await orchestrator._build_butler_recent_conversation_block(task_id=task_id)

            assert "conversation_source: agent_session_transcript" in block
            assert "今天天气怎么样？" in block
            assert "我还缺少城市 / 区县信息。" in block
            assert "用户正在补充天气地点。" in block
        finally:
            await store_group.conn.close()

    async def test_freshness_weather_without_location_clarifies_before_delegation(
        self,
        tmp_path: Path,
    ) -> None:
        store_group, task_service, orchestrator = await _build_freshness_context(tmp_path)

        try:
            msg = NormalizedMessage(
                text="今天天气怎么样？",
                idempotency_key="f041-orch-freshness-no-location",
            )
            task_id, created = await task_service.create_task(msg)
            assert created is True

            result = await orchestrator.dispatch(task_id=task_id, user_text=msg.text)
            assert result.status == WorkerExecutionStatus.SUCCEEDED
            assert result.worker_id == "butler.main"
            assert result.summary == "butler_freshness_location_clarified"

            task = await task_service.get_task(task_id)
            assert task is not None
            assert task.status == TaskStatus.SUCCEEDED

            events = await store_group.event_store.get_events_for_task(task_id)
            assert [event.type for event in events].count("MODEL_CALL_COMPLETED") == 1
            assert "A2A_MESSAGE_SENT" not in [event.type for event in events]
            completion = next(
                event for event in reversed(events) if event.type == "MODEL_CALL_COMPLETED"
            )
            content = (
                await store_group.artifact_store.get_artifact_content(
                    completion.payload["artifact_ref"]
                )
            ).decode("utf-8")
            assert "缺少**城市 / 区县**信息" in content
            assert "受治理的实时查询链路" in content

            works = await store_group.work_store.list_works(task_id=task_id)
            assert len(works) == 1
            work = works[0]
            assert work.metadata["freshness_resolution"] == "location_required"
            assert work.metadata["clarification_needed"] == "weather_location"
            assert work.metadata["delegation_strategy"] == "butler_owned_freshness"

            conversations = await store_group.a2a_store.list_conversations(task_id=task_id)
            assert conversations == []
        finally:
            await store_group.conn.close()

    async def test_freshness_weather_location_followup_resumes_research_chain(
        self,
        tmp_path: Path,
    ) -> None:
        store_group, task_service, orchestrator = await _build_freshness_context(tmp_path)

        try:
            msg = NormalizedMessage(
                text="今天天气怎么样？",
                idempotency_key="f041-orch-freshness-followup-location",
            )
            task_id, created = await task_service.create_task(msg)
            assert created is True

            first_result = await orchestrator.dispatch(task_id=task_id, user_text=msg.text)
            assert first_result.summary == "butler_freshness_location_clarified"

            append_event = await task_service.append_user_message(task_id, "深圳")
            assert append_event.type == "USER_MESSAGE"

            second_result = await orchestrator.dispatch(task_id=task_id, user_text="深圳")
            assert second_result.status == WorkerExecutionStatus.SUCCEEDED
            assert second_result.worker_id == "butler.main"
            assert second_result.summary == "butler_freshness_synthesized"

            events = await store_group.event_store.get_events_for_task(task_id)
            completions = [event for event in events if event.type == "MODEL_CALL_COMPLETED"]
            assert len(completions) == 2
            latest_content = (
                await store_group.artifact_store.get_artifact_content(
                    completions[-1].payload["artifact_ref"]
                )
            ).decode("utf-8")
            assert "Butler 综合答复" in latest_content

            works = await store_group.work_store.list_works(task_id=task_id)
            assert len(works) == 2
            latest_work = works[0]
            assert latest_work.metadata["delegation_strategy"] == "butler_owned_freshness"
            assert latest_work.metadata["freshness_followup_mode"] == "weather_location"
            assert latest_work.metadata["research_child_task_id"]
        finally:
            await store_group.conn.close()

    async def test_freshness_explicit_websearch_without_location_stays_best_effort(
        self,
        tmp_path: Path,
    ) -> None:
        store_group, task_service, orchestrator = await _build_freshness_context(tmp_path)

        try:
            msg = NormalizedMessage(
                text="今天天气怎么样？",
                idempotency_key="f041-orch-freshness-explicit-websearch-no-location",
            )
            task_id, created = await task_service.create_task(msg)
            assert created is True

            first_result = await orchestrator.dispatch(task_id=task_id, user_text=msg.text)
            assert first_result.summary == "butler_freshness_location_clarified"

            followup = "你直接去 Websearch 今天天气怎么样"
            append_event = await task_service.append_user_message(task_id, followup)
            assert append_event.type == "USER_MESSAGE"

            second_result = await orchestrator.dispatch(task_id=task_id, user_text=followup)
            assert second_result.status == WorkerExecutionStatus.SUCCEEDED
            assert second_result.worker_id == "butler.main"
            assert second_result.summary == "butler_freshness_best_effort"

            events = await store_group.event_store.get_events_for_task(task_id)
            completions = [event for event in events if event.type == "MODEL_CALL_COMPLETED"]
            assert len(completions) == 2
            latest_content = (
                await store_group.artifact_store.get_artifact_content(
                    completions[-1].payload["artifact_ref"]
                )
            ).decode("utf-8")
            assert "缺少**城市 / 区县**信息" in latest_content
            assert "不能假装已经查到了正确城市" in latest_content

            works = await store_group.work_store.list_works(task_id=task_id)
            assert len(works) == 2
            latest_work = works[0]
            assert latest_work.metadata["delegation_strategy"] == "butler_owned_freshness"
            assert latest_work.metadata["freshness_resolution"] == "location_missing_best_effort"
        finally:
            await store_group.conn.close()

    async def test_freshness_handoff_is_not_injected_as_system_message(
        self,
        tmp_path: Path,
    ) -> None:
        llm_service = _FreshnessLLMService()
        store_group, task_service, orchestrator = await _build_freshness_context(
            tmp_path,
            llm_service=llm_service,
        )

        try:
            msg = NormalizedMessage(
                text="深圳今天天气怎么样？",
                idempotency_key="f041-orch-freshness-handoff-role",
            )
            task_id, created = await task_service.create_task(msg)
            assert created is True

            result = await orchestrator.dispatch(task_id=task_id, user_text=msg.text)
            assert result.status == WorkerExecutionStatus.SUCCEEDED
            assert llm_service.handoff_roles == ["assistant"]
        finally:
            await store_group.conn.close()

    async def test_freshness_backend_unavailable_returns_environment_limited_reply(
        self,
        tmp_path: Path,
    ) -> None:
        store_group, task_service, orchestrator = await _build_freshness_failure_context(tmp_path)

        try:
            msg = NormalizedMessage(
                text="深圳今天天气怎么样？",
                idempotency_key="f041-orch-freshness-backend-unavailable",
            )
            task_id, created = await task_service.create_task(msg)
            assert created is True

            result = await orchestrator.dispatch(task_id=task_id, user_text=msg.text)
            assert result.status == WorkerExecutionStatus.SUCCEEDED
            assert result.worker_id == "butler.main"
            assert result.summary == "butler_freshness_backend_explained"

            parent_task = await task_service.get_task(task_id)
            assert parent_task is not None
            assert parent_task.status == TaskStatus.SUCCEEDED

            parent_events = await store_group.event_store.get_events_for_task(task_id)
            completion = next(
                event
                for event in reversed(parent_events)
                if event.type == "MODEL_CALL_COMPLETED"
            )
            content = (
                await store_group.artifact_store.get_artifact_content(
                    completion.payload["artifact_ref"]
                )
            ).decode("utf-8")
            assert "web/browser 后端暂时不可用" in content
            assert "不代表系统整体没有实时查询能力" in content

            works = await store_group.work_store.list_works(task_id=task_id)
            assert len(works) == 1
            work = works[0]
            assert work.metadata["freshness_resolution"] == "backend_unavailable"
            assert "web search failed" in str(work.metadata["freshness_degraded_reason"])

            child_task_id = work.metadata["research_child_task_id"]
            child_events = await store_group.event_store.get_events_for_task(child_task_id)
            assert "A2A_MESSAGE_SENT" in [event.type for event in child_events]
            assert "A2A_MESSAGE_RECEIVED" in [event.type for event in child_events]
            failure = next(
                event for event in reversed(child_events) if event.type == "MODEL_CALL_FAILED"
            )
            assert "web search failed" in failure.payload["error_message"]
        finally:
            await store_group.conn.close()
