"""Feature 008: Orchestrator 控制平面测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from octoagent.core.models import (
    A2AConversation,
    A2AConversationStatus,
    AgentDecision,
    AgentDecisionMode,
    AgentProfile,
    AgentProfileOriginKind,
    AgentProfileStatus,
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
    WorkerResult,
)
from octoagent.core.models.message import NormalizedMessage
from octoagent.core.store import create_store_group
from octoagent.gateway.services.capability_pack import CapabilityPackService
from octoagent.gateway.services.delegation_plane import DelegationPlaneService
from octoagent.gateway.services.execution_context import get_current_execution_context
from octoagent.gateway.services.llm_service import LLMService
from octoagent.gateway.services.orchestrator import OrchestratorService
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.gateway.services.task_service import TaskService
from octoagent.policy.approval_manager import ApprovalManager
from octoagent.policy.models import ApprovalDecision, ApprovalRequest
from octoagent.provider import ModelCallResult, TokenUsage
from octoagent.tooling import ToolBroker
from octoagent.tooling.models import SideEffectLevel

from apps.gateway.tests.runtime_service_fixtures import runtime_service_fixture


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
    task_service = TaskService(store_group, sse_hub, storage_only=True)
    orchestrator = OrchestratorService(
        store_group=store_group,
        sse_hub=sse_hub,
        runtime_services=runtime_service_fixture(resolved_llm_service).bundle,
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
            content = "Agent 综合答复：深圳今天大致晴，约 21°C，基本不用担心下雨。"
        else:
            content = "Agent 常规答复。"
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


class _SingleLoopLLMService:
    supports_single_loop_executor = True
    supports_agent_decision_phase = True
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


class _ExecutionContextCapturingLLMService(_SingleLoopLLMService):
    def __init__(self) -> None:
        super().__init__()
        self.context_snapshots: list[dict[str, str]] = []

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
        metadata_dict = dict(metadata or {})
        if (
            metadata_dict.get("owner_execution_mode") == "worker_self"
            and not str(metadata_dict.get("decision_phase", "")).strip()
        ):
            ctx = get_current_execution_context()
            self.context_snapshots.append(
                {
                    "task_id": ctx.task_id,
                    "session_id": ctx.session_id,
                    "worker_id": ctx.worker_id,
                    "backend": ctx.backend,
                    "runtime_kind": ctx.runtime_kind,
                }
            )
        return await super().call(
            prompt_or_messages,
            model_alias=model_alias,
            task_id=task_id,
            trace_id=trace_id,
            metadata=metadata,
            worker_capability=worker_capability,
            tool_profile=tool_profile,
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
    await store_group.project_store.save_selector_state(
        ProjectSelectorState(
            selector_id="selector-web",
            surface="web",
            active_project_id="project-default",
            source="tests",
        )
    )
    await store_group.conn.commit()

    sse_hub = SSEHub()
    task_service = TaskService(store_group, sse_hub, storage_only=True)
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
        runtime_services=runtime_service_fixture(resolved_llm_service).bundle,
        delegation_plane=delegation_plane,
    )
    return store_group, task_service, orchestrator


class _ForbiddenModelCallService:
    def __init__(self) -> None:
        self.calls = 0

    async def call(self, *args, **kwargs) -> ModelCallResult:
        del args, kwargs
        self.calls += 1
        raise AssertionError("deterministic inline reply must not call the configured model")


class _GraphStartStub:
    def __init__(self, *, result: str = "", error: Exception | None = None) -> None:
        self._result = result
        self._error = error
        self.calls: list[dict[str, object]] = []

    async def execute(self, **kwargs) -> str:
        self.calls.append(dict(kwargs))
        if self._error is not None:
            raise self._error
        return self._result


def _force_routing_decision(monkeypatch, orchestrator, decision: AgentDecision) -> None:
    async def resolve(_request):
        return decision, {}

    monkeypatch.setattr(orchestrator, "_resolve_routing_decision", resolve)


async def _assert_inline_event_contract(store_group, *, task_id: str, expected_content: str) -> str:
    events = await store_group.event_store.get_events_for_task(task_id)
    event_types = [item.type for item in events]
    assert event_types.count("MODEL_CALL_STARTED") == 1
    assert event_types.count("MODEL_CALL_COMPLETED") == 1
    assert event_types.count("ARTIFACT_CREATED") == 1
    assert not {"CONTEXT_COMPACTED", "MEMORY_EXTRACTION_COMPLETED"}.intersection(event_types)
    completed = next(item for item in events if item.type == "MODEL_CALL_COMPLETED")
    assert completed.payload == {
        "model_alias": "main",
        "response_summary": expected_content,
        "duration_ms": 1,
        "token_usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
        "artifact_ref": completed.payload["artifact_ref"],
        "model_name": "agent-inline",
        "provider": "inline",
        "cost_usd": 0.0,
        "cost_unavailable": False,
        "is_fallback": False,
    }
    checkpoint_events = [item for item in events if item.type == "CHECKPOINT_SAVED"]
    assert [item.payload["node_id"] for item in checkpoint_events] == [
        "state_running",
        "model_call_started",
        "response_persisted",
        "task_succeeded",
    ]
    task = await store_group.task_store.get_task(task_id)
    assert task is not None
    assert task.status == TaskStatus.SUCCEEDED
    assert task.pointers.latest_checkpoint_id == checkpoint_events[-1].payload["checkpoint_id"]
    return str(completed.payload["artifact_ref"])


async def _assert_inline_artifact_contract(
    store_group, *, task_id: str, artifact_ref: str, expected_content: str
) -> None:
    artifacts = await store_group.artifact_store.list_artifacts_for_task(task_id)
    assert sorted(item.name for item in artifacts) == [
        "llm-request-context",
        "llm-response",
    ]
    response_artifacts = [item for item in artifacts if item.name == "llm-response"]
    assert len(response_artifacts) == 1
    response_artifact = response_artifacts[0]
    assert response_artifact.artifact_id == artifact_ref
    assert response_artifact.description == "LLM 响应内容"
    assert response_artifact.size == len(expected_content.encode("utf-8"))
    content = await store_group.artifact_store.get_artifact_content(response_artifact.artifact_id)
    assert content == expected_content.encode("utf-8")


async def _assert_inline_session_contract(
    store_group,
    *,
    task_id: str,
    user_text: str,
    expected_content: str,
    response_artifact_id: str,
) -> None:
    frames = await store_group.agent_context_store.list_context_frames(
        task_id=task_id,
        limit=10,
    )
    assert len(frames) == 1
    frame = frames[0]
    assert frame.agent_runtime_id
    assert frame.agent_session_id
    assert frame.recall_frame_id
    session_context = await store_group.agent_context_store.get_session_context(frame.session_id)
    assert session_context is not None
    assert task_id in session_context.task_ids
    assert session_context.last_recall_frame_id == frame.recall_frame_id

    session = await store_group.agent_context_store.get_agent_session(frame.agent_session_id)
    assert session is not None
    assert session.recent_transcript[-2:] == [
        {"role": "user", "content": user_text, "task_id": task_id},
        {"role": "assistant", "content": expected_content, "task_id": task_id},
    ]
    assert session.metadata["latest_model_reply_preview"] == expected_content
    turns = await store_group.agent_context_store.list_agent_session_turns(
        agent_session_id=frame.agent_session_id,
        limit=10,
    )
    assert [item.kind.value for item in turns[-2:]] == [
        "user_message",
        "assistant_message",
    ]
    assert turns[-1].artifact_ref == response_artifact_id


async def _assert_exact_inline_result(
    store_group,
    *,
    task_id: str,
    user_text: str,
    expected_content: str,
) -> None:
    artifact_ref = await _assert_inline_event_contract(
        store_group,
        task_id=task_id,
        expected_content=expected_content,
    )
    await _assert_inline_artifact_contract(
        store_group,
        task_id=task_id,
        artifact_ref=artifact_ref,
        expected_content=expected_content,
    )
    await _assert_inline_session_contract(
        store_group,
        task_id=task_id,
        user_text=user_text,
        expected_content=expected_content,
        response_artifact_id=artifact_ref,
    )


async def test_non_direct_reply_persists_exact_precomputed_result_without_model_call(
    tmp_path: Path,
    monkeypatch,
) -> None:
    model = _ForbiddenModelCallService()
    store_group, task_service, orchestrator = await _build_context(
        tmp_path,
        llm_service=model,
    )
    expected = "请补充要查询的城市。"
    decision = AgentDecision(
        mode=AgentDecisionMode.ASK_ONCE,
        category="weather_location",
        reply_prompt=expected,
    )
    _force_routing_decision(monkeypatch, orchestrator, decision)

    try:
        message = NormalizedMessage(
            text="今天天气怎么样？",
            idempotency_key="f151-s080-inline-reply",
        )
        task_id, created = await task_service.create_task(message)
        assert created is True

        result = await orchestrator.dispatch(task_id=task_id, user_text=message.text)

        assert result.status == TaskStatus.SUCCEEDED
        assert result.summary == "agent_clarification:weather_location"
        assert model.calls == 0
        await _assert_exact_inline_result(
            store_group,
            task_id=task_id,
            user_text=message.text,
            expected_content=expected,
        )
    finally:
        await store_group.close()


async def test_graph_start_exception_uses_exact_deterministic_inline_fallback_without_model_call(
    tmp_path: Path,
    monkeypatch,
) -> None:
    model = _ForbiddenModelCallService()
    store_group, task_service, orchestrator = await _build_context(
        tmp_path,
        llm_service=model,
    )
    graph = _GraphStartStub(error=RuntimeError("graph unavailable"))
    orchestrator._graph_pipeline_tool = graph
    expected = "工作流暂时无法启动，请稍后再试。"
    decision = AgentDecision(
        mode=AgentDecisionMode.DELEGATE_GRAPH,
        category="graph_start",
        reply_prompt=expected,
        pipeline_id="daily-briefing",
        pipeline_params={"region": "cn"},
    )
    _force_routing_decision(monkeypatch, orchestrator, decision)

    try:
        message = NormalizedMessage(
            text="运行日报工作流",
            idempotency_key="f151-s080-graph-exception",
        )
        task_id, created = await task_service.create_task(message)
        assert created is True

        result = await orchestrator.dispatch(task_id=task_id, user_text=message.text)

        assert result.status == TaskStatus.SUCCEEDED
        assert graph.calls == [
            {
                "action": "start",
                "pipeline_id": "daily-briefing",
                "params": {"region": "cn"},
                "task_id": task_id,
            }
        ]
        assert model.calls == 0
        await _assert_exact_inline_result(
            store_group,
            task_id=task_id,
            user_text=message.text,
            expected_content=expected,
        )
    finally:
        await store_group.close()


async def test_graph_start_error_result_uses_exact_deterministic_inline_fallback_without_model_call(
    tmp_path: Path,
    monkeypatch,
) -> None:
    model = _ForbiddenModelCallService()
    store_group, task_service, orchestrator = await _build_context(
        tmp_path,
        llm_service=model,
    )
    graph = _GraphStartStub(result="Error: pipeline disabled")
    orchestrator._graph_pipeline_tool = graph
    expected = "该工作流当前不可用，请稍后再试。"
    decision = AgentDecision(
        mode=AgentDecisionMode.DELEGATE_GRAPH,
        category="graph_start",
        reply_prompt=expected,
        pipeline_id="disabled-flow",
    )
    _force_routing_decision(monkeypatch, orchestrator, decision)

    try:
        message = NormalizedMessage(
            text="运行停用的工作流",
            idempotency_key="f151-s080-graph-error",
        )
        task_id, created = await task_service.create_task(message)
        assert created is True

        result = await orchestrator.dispatch(task_id=task_id, user_text=message.text)

        assert result.status == TaskStatus.SUCCEEDED
        assert graph.calls == [
            {
                "action": "start",
                "pipeline_id": "disabled-flow",
                "params": {},
                "task_id": task_id,
            }
        ]
        assert model.calls == 0
        await _assert_exact_inline_result(
            store_group,
            task_id=task_id,
            user_text=message.text,
            expected_content=expected,
        )
    finally:
        await store_group.close()


class TestOrchestrator:
    async def test_dispatch_success_writes_control_plane_events(self, tmp_path: Path) -> None:
        store_group, task_service, orchestrator = await _build_context(tmp_path)

        msg = NormalizedMessage(text="hello orchestrator", idempotency_key="f008-orch-001")
        task_id, created = await task_service.create_task(msg)
        assert created is True

        result = await orchestrator.dispatch(task_id=task_id, user_text=msg.text)
        assert result.status == TaskStatus.SUCCEEDED
        assert result.retryable is False

        task = await task_service.get_task(task_id)
        assert task is not None
        assert task.status == "SUCCEEDED"

        events = await store_group.event_store.get_events_for_task(task_id)
        event_types = [event.type for event in events]
        assert "ORCH_DECISION" in event_types
        # Feature 064 Phase 1: Main Agent Direct Execution 路径不经过 Worker 派发，
        # 因此不产生 A2A/Worker 事件，但保留 MODEL_CALL 和 ARTIFACT 事件。
        assert "MODEL_CALL_STARTED" in event_types
        assert "MODEL_CALL_COMPLETED" in event_types
        assert "ARTIFACT_CREATED" in event_types

        await store_group.close()

    async def test_dispatch_owner_self_worker_session_executes_without_delegation(
        self,
        tmp_path: Path,
    ) -> None:
        llm_service = _SingleLoopLLMService()
        store_group, task_service, orchestrator = await _build_freshness_context(
            tmp_path,
            llm_service=llm_service,
        )

        try:
            worker_profile = AgentProfile(
                profile_id="worker-profile-finance-root",
                project_id="project-default",
                name="Finance Root Agent",
                model_alias="cheap",
                tool_profile="standard",
                default_tool_groups=["project", "filesystem", "terminal"],
                selected_tools=["filesystem.list_dir", "filesystem.read_text"],
                runtime_kinds=["worker", "subagent"],
                status=AgentProfileStatus.ACTIVE,
                origin_kind=AgentProfileOriginKind.CUSTOM,
                draft_revision=1,
                active_revision=1,
            )
            await _save_worker_with_mirror(store_group.agent_context_store, worker_profile)

            msg = NormalizedMessage(
                text="请总结当前项目 README 的一句话定位。",
                idempotency_key="f071-owner-self-worker",
            )
            task_id, created = await task_service.create_task(msg)
            assert created is True

            result = await orchestrator.dispatch(
                task_id=task_id,
                user_text=msg.text,
                metadata={
                    "session_owner_profile_id": worker_profile.profile_id,
                    "agent_profile_id": worker_profile.profile_id,
                },
            )

            assert result.status == TaskStatus.SUCCEEDED
            assert result.worker_id == worker_profile.profile_id
            assert result.summary == "owner_self_worker:general"
            assert len(llm_service.calls) >= 1
            call = llm_service.calls[-1]
            assert call["model_alias"] == "cheap"
            assert call["worker_capability"] == "llm_generation"
            metadata = dict(call["metadata"])
            assert metadata["owner_execution_mode"] == "worker_self"
            assert metadata["turn_executor_kind"] == "worker"
            assert metadata["session_owner_profile_id"] == worker_profile.profile_id
            assert metadata["agent_profile_id"] == worker_profile.profile_id
            assert metadata.get("delegation_target_profile_id", "") == ""
            assert metadata.get("single_loop_executor") is not True

            sessions = await store_group.agent_context_store.list_agent_sessions(
                project_id="project-default",
                kind=AgentSessionKind.DIRECT_WORKER,
                limit=10,
            )
            assert sessions
            runtime = await store_group.agent_context_store.get_agent_runtime(
                sessions[0].agent_runtime_id
            )
            assert runtime is not None
            assert runtime.role is AgentRuntimeRole.WORKER
            assert runtime.agent_profile_id == worker_profile.profile_id
        finally:
            await store_group.close()

    async def test_dispatch_owner_self_worker_session_binds_execution_context(
        self,
        tmp_path: Path,
    ) -> None:
        llm_service = _ExecutionContextCapturingLLMService()
        store_group, task_service, orchestrator = await _build_freshness_context(
            tmp_path,
            llm_service=llm_service,
        )

        try:
            worker_profile = AgentProfile(
                profile_id="worker-profile-context-root",
                project_id="project-default",
                name="Context Root Agent",
                model_alias="cheap",
                tool_profile="standard",
                default_tool_groups=["project", "filesystem", "terminal"],
                selected_tools=["filesystem.list_dir", "filesystem.read_text"],
                runtime_kinds=["worker", "subagent"],
                status=AgentProfileStatus.ACTIVE,
                origin_kind=AgentProfileOriginKind.CUSTOM,
                draft_revision=1,
                active_revision=1,
            )
            await _save_worker_with_mirror(store_group.agent_context_store, worker_profile)

            msg = NormalizedMessage(
                text="请读取当前 worker 的 execution context。",
                idempotency_key="f071-owner-self-worker-context",
            )
            task_id, created = await task_service.create_task(msg)
            assert created is True

            result = await orchestrator.dispatch(
                task_id=task_id,
                user_text=msg.text,
                metadata={
                    "session_owner_profile_id": worker_profile.profile_id,
                    "agent_profile_id": worker_profile.profile_id,
                },
            )

            assert result.status == TaskStatus.SUCCEEDED
            assert llm_service.context_snapshots
            snapshot = llm_service.context_snapshots[-1]
            assert snapshot["task_id"] == task_id
            assert snapshot["worker_id"] == worker_profile.profile_id
            assert snapshot["backend"] == "inline"
            assert snapshot["runtime_kind"] == "worker"
            assert snapshot["session_id"]
        finally:
            await store_group.close()

    async def test_dispatch_prepared_roundtrips_through_a2a_and_restores_runtime_context(
        self, tmp_path: Path
    ) -> None:
        store_group = await create_store_group(
            str(tmp_path / "orchestrator-a2a.db"),
            str(tmp_path / "artifacts-a2a"),
        )
        sse_hub = SSEHub()
        task_service = TaskService(store_group, sse_hub, storage_only=True)

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
                    status=TaskStatus.SUCCEEDED,
                    retryable=False,
                    summary="captured",
                    tool_profile=envelope.tool_profile,
                )

        orchestrator = OrchestratorService(
            store_group=store_group,
            sse_hub=sse_hub,
            runtime_services=runtime_service_fixture(LLMService()).bundle,
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
                    tool_profile="minimal",
                    work_id="work-a2a",
                ).model_dump_json(),
            },
        )

        result = await orchestrator.dispatch_prepared(envelope)
        assert result.status == TaskStatus.SUCCEEDED
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
        assert (
            captured.runtime_context.metadata["agent_session_id"]
            == captured.metadata["agent_session_id"]
        )

        await store_group.close()

    async def test_routing_hop_guard_fails_before_dispatch(self, tmp_path: Path) -> None:
        store_group, task_service, orchestrator = await _build_context(tmp_path)

        msg = NormalizedMessage(text="hop guard", idempotency_key="f008-orch-002")
        task_id, created = await task_service.create_task(msg)
        assert created is True

        # Feature 064 Phase 1: 使用 parent_task_id 标记为子任务，
        # 绕过 Main Agent Direct Execution 路径，确保请求走 Worker Dispatch
        # 路径以触发 hop guard 检查。
        result = await orchestrator.dispatch(
            task_id=task_id,
            user_text=msg.text,
            hop_count=3,
            max_hops=3,
            metadata={"parent_task_id": "parent-hop-guard"},
        )
        assert result.status == TaskStatus.FAILED
        assert result.retryable is False
        assert result.error_type == "OrchestratorRoutingError"

        events = await store_group.event_store.get_events_for_task(task_id)
        event_types = [event.type for event in events]
        assert "ORCH_DECISION" in event_types
        assert "WORKER_DISPATCHED" not in event_types

        task = await task_service.get_task(task_id)
        assert task is not None
        assert task.status == "FAILED"

        await store_group.close()

    async def test_high_risk_task_denied_without_approval(self, tmp_path: Path) -> None:
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
        assert result.status == TaskStatus.FAILED
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

        await store_group.close()

    async def test_high_risk_task_allowed_with_valid_approval_id(self, tmp_path: Path) -> None:
        store_group = await create_store_group(
            str(tmp_path / "orchestrator-approved.db"),
            str(tmp_path / "artifacts-approved"),
        )
        sse_hub = SSEHub()
        llm_service = LLMService()
        approval_manager = ApprovalManager(event_store=store_group.event_store)
        task_service = TaskService(store_group, sse_hub, storage_only=True)
        orchestrator = OrchestratorService(
            store_group=store_group,
            sse_hub=sse_hub,
            runtime_services=runtime_service_fixture(llm_service).bundle,
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
        assert result.status == TaskStatus.SUCCEEDED
        assert result.retryable is False
        assert approval_manager.consume_allow_once(approval_id) is False

        await store_group.close()

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
        assert result.status == TaskStatus.FAILED
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

        await store_group.close()

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
            source_agent="agent://main.agent",
            target_agent="agent://worker.active",
            status=A2AConversationStatus.ACTIVE,
        )
        waiting_conversation = A2AConversation(
            a2a_conversation_id="conv-waiting",
            task_id=task_id,
            work_id="work-waiting",
            source_agent="agent://main.agent",
            target_agent="agent://worker.waiting",
            status=A2AConversationStatus.WAITING_INPUT,
        )
        completed_conversation = A2AConversation(
            a2a_conversation_id="conv-completed",
            task_id=task_id,
            work_id="work-completed",
            source_agent="agent://main.agent",
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

        await store_group.close()

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
            assert result.status == TaskStatus.SUCCEEDED
            # Feature 064 Phase 1: Main Agent Direct Execution 路径处理，
            # worker_id 为 main.agent
            assert result.worker_id == "main.agent"
            assert result.summary != "agent_clarification:work_priority_context"
        finally:
            await store_group.close()

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
            assert result.status == TaskStatus.SUCCEEDED
            # Feature 064 Phase 1: Main Agent Direct Execution 路径处理天气查询
            assert result.worker_id == "main.agent"
            assert result.summary != "agent_clarification:weather_location"

            events = await store_group.event_store.get_events_for_task(task_id)
            assert [event.type for event in events].count("MODEL_CALL_COMPLETED") == 1
        finally:
            await store_group.close()

    async def test_single_loop_executor_bypasses_agent_decision_preflight(
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
            assert result.status == TaskStatus.SUCCEEDED
            # Feature 064 Phase 1: Main Agent Direct Execution 路径处理
            assert result.worker_id == "main.agent"

            assert len(llm_service.calls) == 1
            metadata = llm_service.calls[0]["metadata"]
            assert isinstance(metadata, dict)
            # Main Agent Direct Execution 设置 agent_execution_mode=direct
            assert metadata.get("agent_execution_mode") == "direct"
            assert "decision_phase" not in metadata

            artifacts = await store_group.artifact_store.list_artifacts_for_task(task_id)
            artifact_names = {item.name for item in artifacts}
            assert "agent-decision-request" not in artifact_names
            assert "agent-decision-response" not in artifact_names
        finally:
            await store_group.close()

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
            assert result.status == TaskStatus.SUCCEEDED
            assert result.worker_id == "worker.llm.default"

            assert len(llm_service.calls) == 1
            metadata = llm_service.calls[0]["metadata"]
            assert isinstance(metadata, dict)
            # F100 Phase E1: metadata["single_loop_executor"] / "single_loop_executor_mode" 已移除
            # 改为验证 runtime_context.delegation_mode == "main_inline"（单一事实源）
            assert "single_loop_executor" not in metadata
            assert "single_loop_executor_mode" not in metadata
            runtime_context = llm_service.calls[0].get("runtime_context")
            if runtime_context is not None:
                assert runtime_context.delegation_mode == "main_inline"
            # F100 Final review HIGH-1 验证：patched runtime_context 同步覆盖
            # metadata["runtime_context_json"]。
            # （避免 LLMService 通过 runtime_context_from_metadata 读到 stale unspecified）
            assert "runtime_context_json" in metadata
            from octoagent.gateway.services.runtime_control import decode_runtime_context

            decoded = decode_runtime_context(metadata["runtime_context_json"])
            assert decoded is not None
            assert decoded.delegation_mode == "main_inline"
            assert metadata["selected_worker_type"] == "research"
            assert "decision_phase" not in metadata
        finally:
            await store_group.close()

    async def test_single_loop_executor_supports_requested_agent_profile_id(
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
                metadata={"requested_agent_profile_id": "singleton:research"},
            )
            assert result.status == TaskStatus.SUCCEEDED
            assert result.worker_id == "worker.llm.default"

            assert len(llm_service.calls) == 1
            metadata = llm_service.calls[0]["metadata"]
            assert isinstance(metadata, dict)
            # F100 Phase E1: metadata flag 已移除；改读 runtime_context
            assert "single_loop_executor" not in metadata
            assert "single_loop_executor_mode" not in metadata
            runtime_context = llm_service.calls[0].get("runtime_context")
            if runtime_context is not None:
                assert runtime_context.delegation_mode == "main_inline"
            assert metadata["selected_worker_type"] == "research"
            assert metadata["requested_worker_type"] == "research"
            assert metadata["requested_agent_profile_id"] == "singleton:research"
            assert metadata["requested_worker_type_source"] == "delegation_target_profile_id"
            assert "decision_phase" not in metadata
        finally:
            await store_group.close()

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
                    agent_runtime_id="runtime-main",
                    role=AgentRuntimeRole.MAIN,
                    status=AgentRuntimeStatus.ACTIVE,
                )
            )
            agent_session = AgentSession(
                agent_session_id="agent-session-transcript",
                agent_runtime_id="runtime-main",
                kind=AgentSessionKind.MAIN_BOOTSTRAP,
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
                    agent_runtime_id="runtime-main",
                    agent_session_id=agent_session.agent_session_id,
                )
            )
            await store_group.conn.commit()

            block = await orchestrator._build_main_recent_conversation_block(task_id=task_id)

            assert "conversation_source: agent_session_transcript" in block
            assert "今天天气怎么样？" in block
            assert "我还缺少城市 / 区县信息。" in block
            assert "用户正在补充天气地点。" in block
        finally:
            await store_group.close()


# ── F117 测试辅助（worker 镜像播种）────────────────────────────────────
# 运行时统一读 agent_profiles(kind=worker) 镜像；生产中镜像由 publish/_sync 写。本 helper
# 把 worker 配置 AgentProfile 写成镜像（kind=worker + source_* 标记）反映生产状态。
# W4-3：WorkerProfile 类已删，入参直接是 AgentProfile（不再 save_worker_profile）。
async def _save_worker_with_mirror(store, wp: AgentProfile):
    await store.save_agent_profile(
        wp.model_copy(
            update={
                "kind": "worker",
                "persona_summary": wp.summary,
                "version": max(int(wp.active_revision or 0), int(wp.draft_revision or 0), 1),
                "metadata": {
                    **dict(wp.metadata),
                    "source_kind": "worker_profile_mirror",
                    "source_worker_profile_id": wp.profile_id,
                },
            }
        )
    )
    return wp
