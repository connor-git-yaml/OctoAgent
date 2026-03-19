"""Feature 033: TaskService 上下文连续性接线测试。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import MethodType

from octoagent.core.models import (
    ActorType,
    AgentProfile,
    AgentProfileScope,
    AgentRuntime,
    AgentRuntimeRole,
    AgentSession,
    AgentSessionKind,
    AgentSessionTurn,
    AgentSessionTurnKind,
    BootstrapSession,
    BootstrapSessionStatus,
    EventType,
    MemoryNamespaceKind,
    OwnerOverlayScope,
    OwnerProfile,
    OwnerProfileOverlay,
    Project,
    ProjectBinding,
    ProjectBindingType,
    ProjectSelectorState,
    RuntimeControlContext,
    SessionContextState,
    WorkerProfile,
    WorkerProfileOriginKind,
    WorkerProfileStatus,
    Workspace,
)
from octoagent.core.models.message import NormalizedMessage
from octoagent.core.store import create_store_group
from octoagent.gateway.services.agent_context import (
    AgentContextService,
    build_agent_runtime_id,
    build_agent_session_id,
    build_ambient_runtime_facts,
    build_private_memory_scope_ids,
    build_scope_aware_session_id,
)
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.gateway.services.task_service import TaskService
from octoagent.memory import (
    MemoryAccessPolicy,
    MemoryBackendState,
    MemoryBackendStatus,
    MemoryEvidenceProjection,
    MemoryEvidenceQuery,
    MemoryLayer,
    MemoryPartition,
    MemoryRecallHit,
    MemoryRecallHookTrace,
    MemoryRecallPostFilterMode,
    MemoryRecallRerankMode,
    MemoryRecallResult,
    MemorySearchHit,
    MemorySearchOptions,
    MemoryService,
)
from octoagent.provider.models import ModelCallResult, TokenUsage


class RecordingLLMService:
    """记录真实 LLM 输入。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def call(self, prompt_or_messages, model_alias: str | None = None, **kwargs):
        self.calls.append(
            {
                "prompt_or_messages": prompt_or_messages,
                "model_alias": model_alias,
                **kwargs,
            }
        )
        return ModelCallResult(
            content="已结合上下文生成回答",
            model_alias=model_alias or "main",
            model_name="mock-model",
            provider="mock",
            duration_ms=5,
            token_usage=TokenUsage(prompt_tokens=10, completion_tokens=10, total_tokens=20),
            cost_usd=0.0,
            cost_unavailable=False,
            is_fallback=False,
            fallback_reason="",
        )


class PlannerAwareLLMService(RecordingLLMService):
    supports_recall_planning_phase = True

    async def call(self, prompt_or_messages, model_alias: str | None = None, **kwargs):
        self.calls.append(
            {
                "prompt_or_messages": prompt_or_messages,
                "model_alias": model_alias,
                **kwargs,
            }
        )
        text = ""
        if isinstance(prompt_or_messages, list):
            text = "\n".join(str(item.get("content", "")) for item in prompt_or_messages)
        else:
            text = str(prompt_or_messages)
        content = (
            json.dumps(
                {
                    "mode": "recall",
                    "query": "Alpha continuity constraints milestone plan",
                    "rationale": "当前请求依赖长期约束和连续上下文，先 recall 更稳。",
                    "subject_hint": "alpha-constraint",
                    "focus_terms": ["Alpha", "连续性", "里程碑"],
                    "allow_vault": False,
                    "limit": 3,
                },
                ensure_ascii=False,
            )
            if "RecallPlanningContext:" in text
            else "已结合 recall 证据生成回答"
        )
        return ModelCallResult(
            content=content,
            model_alias=model_alias or "main",
            model_name="mock-model",
            provider="mock",
            duration_ms=5,
            token_usage=TokenUsage(prompt_tokens=10, completion_tokens=10, total_tokens=20),
            cost_usd=0.0,
            cost_unavailable=False,
            is_fallback=False,
            fallback_reason="",
        )


def test_build_ambient_runtime_facts_formats_local_datetime_and_fallbacks() -> None:
    facts, degraded_reasons = build_ambient_runtime_facts(
        owner_profile=OwnerProfile(
            owner_profile_id="owner-profile-test",
            timezone="Asia/Shanghai",
            locale="zh-CN",
        ),
        surface="web",
        now=datetime(2026, 3, 12, 1, 2, 3, tzinfo=UTC),
    )
    assert facts["current_datetime_local"] == "2026-03-12 09:02:03"
    assert facts["current_weekday_local"] == "星期四"
    assert facts["timezone"] == "Asia/Shanghai"
    assert facts["surface"] == "web"
    assert degraded_reasons == []

    fallback_facts, fallback_reasons = build_ambient_runtime_facts(
        owner_profile=OwnerProfile(
            owner_profile_id="owner-profile-fallback",
            timezone="Invalid/Timezone",
            locale="",
        ),
        surface="chat",
        now=datetime(2026, 3, 12, 1, 2, 3, tzinfo=UTC),
    )
    assert fallback_facts["timezone"] == "UTC"
    assert fallback_facts["locale"] == "zh-CN"
    assert "owner_timezone_invalid" in fallback_reasons
    assert "owner_locale_missing" in fallback_reasons


async def _seed_project_context(store_group) -> None:
    project = Project(
        project_id="project-alpha",
        slug="alpha",
        name="Alpha Project",
        description="Alpha 项目要求保持严格的需求连续性。",
        is_default=True,
        default_agent_profile_id="agent-profile-alpha",
    )
    workspace = Workspace(
        workspace_id="workspace-alpha",
        project_id=project.project_id,
        slug="primary",
        name="Alpha Workspace",
        root_path="/tmp/alpha",
    )
    await store_group.project_store.save_project(project)
    await store_group.project_store.create_workspace(workspace)
    await store_group.project_store.create_binding(
        ProjectBinding(
            binding_id="binding-scope-alpha",
            project_id=project.project_id,
            workspace_id=workspace.workspace_id,
            binding_type=ProjectBindingType.SCOPE,
            binding_key="chat:web:thread-alpha",
            binding_value="chat:web:thread-alpha",
            source="tests",
            migration_run_id="run-alpha",
        )
    )
    await store_group.project_store.create_binding(
        ProjectBinding(
            binding_id="binding-memory-alpha",
            project_id=project.project_id,
            workspace_id=workspace.workspace_id,
            binding_type=ProjectBindingType.MEMORY_SCOPE,
            binding_key="memory/project-alpha",
            binding_value="memory/project-alpha",
            source="tests",
            migration_run_id="run-alpha",
        )
    )
    await store_group.agent_context_store.save_agent_profile(
        AgentProfile(
            profile_id="agent-profile-alpha",
            scope=AgentProfileScope.PROJECT,
            project_id=project.project_id,
            name="Alpha Agent",
            persona_summary="你负责 Alpha 项目的需求连续性与交付推进。",
            instruction_overlays=["回答前必须对齐当前 project 的长期约束。"],
        )
    )
    await store_group.agent_context_store.save_owner_profile(
        OwnerProfile(
            owner_profile_id="owner-profile-default",
            display_name="Connor",
            preferred_address="你",
            working_style="先给结论，再给关键证据。",
            interaction_preferences=["避免丢失上一轮已经确认的事实。"],
        )
    )
    await store_group.agent_context_store.save_owner_overlay(
        OwnerProfileOverlay(
            owner_overlay_id="owner-overlay-alpha",
            owner_profile_id="owner-profile-default",
            scope=OwnerOverlayScope.PROJECT,
            project_id=project.project_id,
            assistant_identity_overrides={"assistant_name": "Alpha Agent"},
            working_style_override="聚焦 Alpha 项目的里程碑推进。",
        )
    )
    await store_group.agent_context_store.save_bootstrap_session(
        BootstrapSession(
            bootstrap_id="bootstrap-alpha",
            project_id=project.project_id,
            workspace_id=workspace.workspace_id,
            owner_profile_id="owner-profile-default",
            owner_overlay_id="owner-overlay-alpha",
            agent_profile_id="agent-profile-alpha",
            status=BootstrapSessionStatus.COMPLETED,
            current_step="done",
            answers={
                "assistant_identity": "Alpha Agent",
                "interaction_preference": "direct",
            },
        )
    )
    await store_group.agent_context_store.save_session_context(
        SessionContextState(
            session_id="thread-alpha",
            thread_id="thread-alpha",
            project_id=project.project_id,
            workspace_id=workspace.workspace_id,
            task_ids=["legacy-task"],
            recent_turn_refs=["legacy-task"],
            recent_artifact_refs=["artifact-legacy"],
            rolling_summary="之前已经确认 Alpha 的关键约束和当前里程碑。",
            last_context_frame_id="context-frame-legacy",
        )
    )
    await store_group.conn.commit()


async def test_agent_context_backfills_bootstrap_templates_and_routes(
    tmp_path: Path,
) -> None:
    store_group = await create_store_group(
        str(tmp_path / "f055-bootstrap.db"),
        str(tmp_path / "artifacts"),
    )
    await _seed_project_context(store_group)
    await store_group.agent_context_store.save_worker_profile(
        WorkerProfile(
            profile_id="singleton:research",
            scope=AgentProfileScope.PROJECT,
            project_id="project-alpha",
            name="Research Root Agent",
            summary="负责研究与外部资料核实。",
            tool_profile="standard",
            status=WorkerProfileStatus.ACTIVE,
            origin_kind=WorkerProfileOriginKind.BUILTIN,
            active_revision=1,
        )
    )

    service = AgentContextService(store_group, project_root=tmp_path)
    project = await store_group.project_store.get_project("project-alpha")
    workspace = await store_group.project_store.get_workspace("workspace-alpha")
    assert project is not None
    assert workspace is not None

    owner_profile = await service._ensure_owner_profile()
    agent_profile = await service._ensure_agent_profile(project)
    owner_overlay = await service._ensure_owner_overlay(
        owner_profile=owner_profile,
        project=project,
        workspace=workspace,
    )
    bootstrap = await service._ensure_bootstrap_session(
        project=project,
        workspace=workspace,
        owner_profile=owner_profile,
        owner_overlay=owner_overlay,
        agent_profile=agent_profile,
        surface="chat",
    )
    mirrored = await service._ensure_agent_profile_from_worker_profile("singleton:research")

    assert owner_overlay is not None
    assert mirrored is not None
    assert "behavior:system:AGENTS.md" in agent_profile.bootstrap_template_ids
    assert "behavior:agent:IDENTITY.md" in agent_profile.bootstrap_template_ids
    assert "behavior:project:PROJECT.md" in agent_profile.bootstrap_template_ids
    assert "behavior:project:PROJECT.md" in owner_overlay.bootstrap_template_ids
    assert "behavior:project_agent:TOOLS.md" in mirrored.bootstrap_template_ids
    assert bootstrap.steps == [
        "owner_identity",
        "assistant_identity",
        "assistant_personality",
        "locale_and_location",
        "memory_preferences",
        "secret_routing",
    ]
    assert bootstrap.metadata["project_path_manifest_required"] is True
    routes = {
        item["step"]: item["route"]
        for item in bootstrap.metadata.get("questionnaire", [])
    }
    assert routes["owner_identity"] == "memory"
    assert routes["assistant_personality"] == "behavior:SOUL.md"
    assert routes["secret_routing"] == "secrets"
    assert (
        bootstrap.metadata["storage_boundary_hints"]["facts_store"] == "MemoryService"
    )
    assert "behavior:project:instructions/README.md" in bootstrap.metadata[
        "bootstrap_template_ids"
    ]


async def test_task_service_injects_profile_bootstrap_recent_and_memory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store_group = await create_store_group(
        str(tmp_path / "f033-context.db"),
        str(tmp_path / "artifacts"),
    )
    await _seed_project_context(store_group)

    memory_calls: list[dict[str, object]] = []

    async def fake_recall_memory(
        self,
        *,
        scope_ids,
        query,
        policy=None,
        per_scope_limit=3,
        max_hits=4,
        hook_options=None,
    ):
        memory_calls.append(
            {
                "scope_ids": list(scope_ids),
                "query": query,
                "per_scope_limit": per_scope_limit,
                "max_hits": max_hits,
                "policy": policy.model_dump() if policy is not None else {},
                "hook_options": (
                    hook_options.model_dump(mode="json") if hook_options is not None else {}
                ),
            }
        )
        return MemoryRecallResult(
            query=query,
            expanded_queries=[query, "Alpha 方案拆解"],
            scope_ids=list(scope_ids),
            hits=[
                MemoryRecallHit(
                    record_id="memory-1",
                    layer=MemoryLayer.SOR,
                    scope_id="memory/project-alpha",
                    partition=MemoryPartition.WORK,
                    summary="长期记忆指出 Alpha 项目必须保持需求上下文连续。",
                    subject_key="alpha-constraint",
                    search_query="Alpha 方案拆解",
                    citation="memory://memory/project-alpha/sor/alpha-constraint",
                    content_preview="Alpha 项目要求保持需求上下文连续。",
                    metadata={"source": "test"},
                    created_at=datetime.now(tz=UTC),
                )
            ],
            backend_status=MemoryBackendStatus(
                backend_id="sqlite",
                active_backend="sqlite",
                state=MemoryBackendState.HEALTHY,
            ),
            hook_trace=MemoryRecallHookTrace(
                post_filter_mode=MemoryRecallPostFilterMode.KEYWORD_OVERLAP,
                rerank_mode=MemoryRecallRerankMode.HEURISTIC,
                focus_terms=["Alpha", "方案拆解"],
                candidate_count=1,
                filtered_count=0,
                delivered_count=1,
            ),
        )

    monkeypatch.setattr(MemoryService, "recall_memory", fake_recall_memory)

    service = TaskService(store_group, SSEHub())
    llm_service = RecordingLLMService()
    message = NormalizedMessage(
        channel="web",
        thread_id="thread-alpha",
        scope_id="chat:web:thread-alpha",
        text="请继续推进 Alpha 的方案拆解",
        idempotency_key="f033-context-001",
    )
    task_id, created = await service.create_task(message)
    assert created is True

    await service.process_task_with_llm(
        task_id=task_id,
        user_text=message.text,
        llm_service=llm_service,
        dispatch_metadata=await service.get_latest_user_metadata(task_id),
    )

    assert memory_calls == []

    prompt = llm_service.calls[0]["prompt_or_messages"]
    assert isinstance(prompt, list)
    joined = "\n".join(str(item.get("content", "")) for item in prompt)
    # persona_summary 已不再直接注入 prompt；AgentProfile 块只包含 name 和 instruction_overlays
    assert "Alpha Agent" in joined
    assert "AmbientRuntime:" in joined
    assert "timezone: UTC" in joined
    assert "current_weekday_local:" in joined
    assert "之前已经确认 Alpha 的关键约束和当前里程碑" in joined
    assert "MemoryRuntime:" in joined
    assert "mode: hint_first" in joined
    assert "MemoryRecallHints:" in joined
    assert "当前未预取详细命中" in joined
    assert "memory.recall / memory.search / memory.read" in joined
    assert "请继续推进 Alpha 的方案拆解" in joined

    frames = await store_group.agent_context_store.list_context_frames(task_id=task_id, limit=5)
    assert len(frames) == 1
    frame = frames[0]
    assert frame.agent_profile_id == "agent-profile-alpha"
    assert frame.recent_summary == "之前已经确认 Alpha 的关键约束和当前里程碑。"
    assert frame.memory_hits == []
    assert str(frame.budget["memory_recall"]["backend"]).startswith("memu")
    assert frame.budget["memory_recall"]["query"] == "请继续推进 Alpha 的方案拆解"
    assert frame.budget["memory_recall"]["expanded_queries"] == []
    assert frame.budget["memory_recall"]["hit_count"] == 0
    assert frame.budget["memory_recall"]["delivered_hit_count"] == 0
    assert frame.budget["memory_recall"]["hook_trace"] == {}
    assert frame.budget["memory_recall"]["prefetch_mode"] == "agent_led_hint_first"
    assert frame.budget["memory_recall"]["agent_led_recall_expected"] is True
    assert frame.budget["memory_recall"]["hint_reason"] == "butler_agent_led_recall"
    assert frame.budget["memory_recall"]["available_tools"] == [
        "memory.search",
        "memory.recall",
        "memory.read",
    ]
    assert "delayed_recall" not in frame.budget
    assert frame.agent_runtime_id
    assert frame.agent_session_id
    assert frame.recall_frame_id
    assert len(frame.memory_namespace_ids) == 2

    state = await store_group.agent_context_store.get_session_context(frame.session_id)
    assert state is not None
    assert state.agent_runtime_id == frame.agent_runtime_id
    assert state.agent_session_id == frame.agent_session_id
    assert state.last_recall_frame_id == frame.recall_frame_id

    runtime = await store_group.agent_context_store.get_agent_runtime(frame.agent_runtime_id)
    assert runtime is not None
    assert runtime.role is AgentRuntimeRole.BUTLER
    assert runtime.name == "Alpha Agent"

    agent_session = await store_group.agent_context_store.get_agent_session(frame.agent_session_id)
    assert agent_session is not None
    assert agent_session.kind is AgentSessionKind.BUTLER_MAIN
    assert agent_session.legacy_session_id == frame.session_id
    assert agent_session.last_recall_frame_id == frame.recall_frame_id
    assert agent_session.rolling_summary
    assert agent_session.metadata["latest_model_reply_summary"]
    assert agent_session.metadata["latest_model_reply_preview"] == "已结合上下文生成回答"
    assert agent_session.recent_transcript[-2:] == [
        {
            "role": "user",
            "content": "请继续推进 Alpha 的方案拆解",
            "task_id": task_id,
        },
        {
            "role": "assistant",
            "content": "已结合上下文生成回答",
            "task_id": task_id,
        },
    ]
    assert agent_session.metadata["recent_transcript"][-2:] == [
        {
            "role": "user",
            "content": "请继续推进 Alpha 的方案拆解",
            "task_id": task_id,
        },
        {
            "role": "assistant",
            "content": "已结合上下文生成回答",
            "task_id": task_id,
        },
    ]
    session_turns = await store_group.agent_context_store.list_agent_session_turns(
        agent_session_id=frame.agent_session_id,
        limit=10,
    )
    assert [item.kind for item in session_turns[-2:]] == [
        AgentSessionTurnKind.USER_MESSAGE,
        AgentSessionTurnKind.ASSISTANT_MESSAGE,
    ]
    assert session_turns[-1].artifact_ref

    namespaces = await store_group.agent_context_store.list_memory_namespaces(
        project_id="project-alpha",
    )
    assert {item.kind for item in namespaces} == {
        MemoryNamespaceKind.PROJECT_SHARED,
        MemoryNamespaceKind.BUTLER_PRIVATE,
    }
    expected_scope_ids = {
        scope_id
        for namespace in namespaces
        for scope_id in namespace.memory_scope_ids
    }
    butler_private_namespace = next(
        item for item in namespaces if item.kind is MemoryNamespaceKind.BUTLER_PRIVATE
    )
    assert len(butler_private_namespace.memory_scope_ids) == 2
    assert set(frame.budget["memory_recall"]["scope_ids"]) == expected_scope_ids

    recalls = await store_group.agent_context_store.list_recall_frames(task_id=task_id, limit=5)
    assert len(recalls) == 1
    recall = recalls[0]
    assert recall.recall_frame_id == frame.recall_frame_id
    assert recall.agent_runtime_id == frame.agent_runtime_id
    assert recall.agent_session_id == frame.agent_session_id
    assert set(recall.memory_namespace_ids) == set(frame.memory_namespace_ids)
    assert recall.memory_hits == []

    artifacts = await store_group.artifact_store.list_artifacts_for_task(task_id)
    request_artifact = next(item for item in artifacts if item.name == "llm-request-context")
    request_content = await store_group.artifact_store.get_artifact_content(
        request_artifact.artifact_id
    )
    assert request_content is not None
    request_text = request_content.decode("utf-8")
    history_tokens = int(
        next(
            line.split(": ", 1)[1]
            for line in request_text.splitlines()
            if line.startswith("history_tokens: ")
        )
    )
    final_tokens = int(
        next(
            line.split(": ", 1)[1]
            for line in request_text.splitlines()
            if line.startswith("final_tokens: ")
        )
    )
    assert "agent_profile_id: agent-profile-alpha" in request_text
    assert f"agent_runtime_id: {frame.agent_runtime_id}" in request_text
    assert f"agent_session_id: {frame.agent_session_id}" in request_text
    assert f"recall_frame_id: {frame.recall_frame_id}" in request_text
    assert "resolve_request_kind: chat" in request_text
    assert (
        "session_id: surface:web|scope:chat:web:thread-alpha|"
        "project:project-alpha|workspace:workspace-alpha|thread:thread-alpha" in request_text
    )
    assert "AmbientRuntime:" in request_text
    assert "timezone: UTC" in request_text
    assert "之前已经确认 Alpha 的关键约束和当前里程碑" in request_text
    assert "MemoryRuntime:" in request_text
    assert "MemoryRecallHints:" in request_text
    assert "当前未预取详细命中" in request_text
    assert final_tokens > history_tokens

    await store_group.conn.close()


async def test_task_service_agent_led_recall_uses_model_planned_query(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store_group = await create_store_group(
        str(tmp_path / "f051-agent-led-recall.db"),
        str(tmp_path / "artifacts"),
    )
    await _seed_project_context(store_group)
    await store_group.agent_context_store.save_agent_profile(
        AgentProfile(
            profile_id="agent-profile-alpha",
            scope=AgentProfileScope.PROJECT,
            project_id="project-alpha",
            name="Alpha Agent",
            persona_summary="你负责 Alpha 项目的需求连续性与交付推进。",
            instruction_overlays=["回答前必须对齐当前 project 的长期约束。"],
            context_budget_policy={
                "memory_recall": {
                    "planner_enabled": True,
                }
            },
        )
    )
    await store_group.conn.commit()

    memory_calls: list[dict[str, object]] = []

    async def fake_recall_memory(
        self,
        *,
        scope_ids,
        query,
        policy=None,
        per_scope_limit=3,
        max_hits=4,
        hook_options=None,
    ):
        memory_calls.append(
            {
                "scope_ids": list(scope_ids),
                "query": query,
                "policy": policy.model_dump(mode="json") if policy is not None else {},
                "hook_options": (
                    hook_options.model_dump(mode="json") if hook_options is not None else {}
                ),
            }
        )
        return MemoryRecallResult(
            query=query,
            expanded_queries=[query, "Alpha continuity constraints"],
            scope_ids=list(scope_ids),
            hits=[
                MemoryRecallHit(
                    record_id="memory-plan-1",
                    layer=MemoryLayer.SOR,
                    scope_id="memory/project-alpha",
                    partition=MemoryPartition.WORK,
                    summary="长期记忆指出 Alpha 项目必须保持需求上下文连续。",
                    subject_key="alpha-constraint",
                    search_query=query,
                    citation="memory://memory/project-alpha/sor/alpha-constraint",
                    content_preview="Alpha 项目要求保持需求上下文连续。",
                    metadata={"source": "planned-recall-test"},
                    created_at=datetime.now(tz=UTC),
                )
            ],
            backend_status=MemoryBackendStatus(
                backend_id="sqlite",
                active_backend="sqlite",
                state=MemoryBackendState.HEALTHY,
            ),
            degraded_reasons=[],
            hook_trace=MemoryRecallHookTrace(
                post_filter_mode=MemoryRecallPostFilterMode.KEYWORD_OVERLAP,
                rerank_mode=MemoryRecallRerankMode.HEURISTIC,
                focus_terms=["Alpha", "连续性", "里程碑"],
                candidate_count=1,
                filtered_count=0,
                delivered_count=1,
            ),
        )

    monkeypatch.setattr(MemoryService, "recall_memory", fake_recall_memory)

    service = TaskService(store_group, SSEHub())
    llm_service = PlannerAwareLLMService()
    message = NormalizedMessage(
        channel="web",
        thread_id="thread-alpha",
        scope_id="chat:web:thread-alpha",
        text="请继续推进 Alpha 的方案拆解",
        idempotency_key="f051-agent-led-recall-001",
    )
    task_id, created = await service.create_task(message)
    assert created is True

    await service.process_task_with_llm(
        task_id=task_id,
        user_text=message.text,
        llm_service=llm_service,
        dispatch_metadata=await service.get_latest_user_metadata(task_id),
    )

    assert len(llm_service.calls) == 2
    planner_prompt = llm_service.calls[0]["prompt_or_messages"]
    final_prompt = llm_service.calls[1]["prompt_or_messages"]
    assert isinstance(planner_prompt, list)
    assert isinstance(final_prompt, list)
    planner_joined = "\n".join(str(item.get("content", "")) for item in planner_prompt)
    final_joined = "\n".join(str(item.get("content", "")) for item in final_prompt)
    assert "RecallPlanningContext:" in planner_joined
    assert "memory_scope_ids:" in planner_joined
    assert "MemoryRecallHints:" in final_joined
    assert "长期记忆指出 Alpha 项目必须保持需求上下文连续" in final_joined
    assert "memory://memory/project-alpha/sor/alpha-constraint" in final_joined

    assert len(memory_calls) == 1
    assert memory_calls[0]["query"] == "Alpha continuity constraints milestone plan"
    assert memory_calls[0]["policy"]["allow_vault"] is False
    assert memory_calls[0]["hook_options"]["focus_terms"] == ["Alpha", "连续性", "里程碑"]

    frames = await store_group.agent_context_store.list_context_frames(task_id=task_id, limit=5)
    assert len(frames) == 1
    frame = frames[0]
    assert frame.memory_hits[0]["record_id"] == "memory-plan-1"
    assert frame.budget["memory_recall"]["agent_led_recall_executed"] is True
    assert frame.budget["memory_recall"]["recall_plan"]["query"] == (
        "Alpha continuity constraints milestone plan"
    )
    assert frame.budget["memory_recall"]["recall_plan"]["metadata"]["plan_source"] == "model"
    assert frame.budget["memory_recall"]["recall_evidence_bundle"]["executed"] is True
    assert (
        frame.budget["memory_recall"]["recall_evidence_bundle"]["citations"][0]
        == "memory://memory/project-alpha/sor/alpha-constraint"
    )

    artifacts = await store_group.artifact_store.list_artifacts_for_task(task_id)
    artifact_names = {item.name for item in artifacts}
    assert "memory-recall-plan-request" in artifact_names
    assert "memory-recall-plan-response" in artifact_names

    await store_group.conn.close()



async def test_task_service_precomputed_recall_plan_skips_auxiliary_planner_phase(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store_group = await create_store_group(
        str(tmp_path / "f051-precomputed-recall-plan.db"),
        str(tmp_path / "artifacts"),
    )
    await _seed_project_context(store_group)

    memory_calls: list[dict[str, object]] = []

    async def fake_recall_memory(
        self,
        *,
        query,
        scope_ids,
        policy,
        limit=None,
        hook_options=None,
        per_scope_limit=None,
        max_hits=None,
        **kwargs,
    ):
        memory_calls.append(
            {
                "query": query,
                "scope_ids": list(scope_ids),
                "policy": policy.model_dump(mode="json"),
                "limit": limit,
                "per_scope_limit": per_scope_limit,
                "max_hits": max_hits,
                "extra_kwargs": kwargs,
                "hook_options": (
                    hook_options.model_dump(mode="json")
                    if hook_options is not None
                    else None
                ),
            }
        )
        return MemoryRecallResult(
            query=query,
            scope_ids=list(scope_ids),
            hits=[
                MemoryRecallHit(
                    record_id="memory-precomputed-1",
                    layer=MemoryLayer.SOR,
                    scope_id="memory/project-alpha",
                    partition=MemoryPartition.WORK,
                    summary="预计算 recall 命中了 Alpha continuity 约束。",
                    subject_key="alpha-precomputed",
                    citation="memory://memory/project-alpha/sor/alpha-precomputed",
                    preview="Alpha 约束要求先对齐 continuity 再回答。",
                    metadata={"query_source": "precomputed"},
                    created_at=datetime.now(tz=UTC),
                )
            ],
            backend_status=MemoryBackendStatus(
                backend_id="sqlite",
                active_backend="sqlite",
                state=MemoryBackendState.HEALTHY,
            ),
            degraded_reasons=[],
            hook_trace=MemoryRecallHookTrace(
                post_filter_mode=MemoryRecallPostFilterMode.KEYWORD_OVERLAP,
                rerank_mode=MemoryRecallRerankMode.HEURISTIC,
                focus_terms=["Alpha", "continuity"],
                candidate_count=1,
                filtered_count=0,
                delivered_count=1,
            ),
        )

    monkeypatch.setattr(MemoryService, "recall_memory", fake_recall_memory)

    service = TaskService(store_group, SSEHub())
    llm_service = PlannerAwareLLMService()
    message = NormalizedMessage(
        channel="web",
        thread_id="thread-alpha",
        scope_id="chat:web:thread-alpha",
        text="请继续推进 Alpha 的方案拆解",
        idempotency_key="f051-agent-led-recall-precomputed-001",
    )
    task_id, created = await service.create_task(message)
    assert created is True

    await service.process_task_with_llm(
        task_id=task_id,
        user_text=message.text,
        llm_service=llm_service,
        dispatch_metadata={
            **(await service.get_latest_user_metadata(task_id)),
            "precomputed_recall_plan": {
                "mode": "recall",
                "query": "Alpha continuity constraints milestone plan",
                "rationale": "Butler loop 已经判断这轮需要先 recall。",
                "subject_hint": "alpha-precomputed",
                "focus_terms": ["Alpha", "continuity"],
                "allow_vault": False,
                "limit": 3,
            },
            "precomputed_recall_plan_source": "butler_loop_plan",
            "precomputed_recall_plan_request_artifact_ref": "artifact-precomputed-request",
            "precomputed_recall_plan_response_artifact_ref": "artifact-precomputed-response",
        },
    )

    assert len(llm_service.calls) == 1
    final_prompt = llm_service.calls[0]["prompt_or_messages"]
    assert isinstance(final_prompt, list)
    final_joined = "\n".join(str(item.get("content", "")) for item in final_prompt)
    assert "MemoryRecallHints:" in final_joined
    assert "alpha-precomputed" in final_joined

    assert len(memory_calls) == 1
    assert memory_calls[0]["query"] == "Alpha continuity constraints milestone plan"

    frames = await store_group.agent_context_store.list_context_frames(task_id=task_id, limit=5)
    assert len(frames) == 1
    frame = frames[0]
    assert frame.budget["memory_recall"]["recall_plan"]["metadata"]["plan_source"] == (
        "butler_loop_plan"
    )

    artifacts = await store_group.artifact_store.list_artifacts_for_task(task_id)
    artifact_names = {item.name for item in artifacts}
    assert "memory-recall-plan-request" not in artifact_names
    assert "memory-recall-plan-response" not in artifact_names

    await store_group.conn.close()


async def test_task_service_single_loop_executor_skips_auxiliary_recall_planner_phase(
    tmp_path: Path,
) -> None:
    store_group = await create_store_group(
        str(tmp_path / "f051-single-loop-recall-plan.db"),
        str(tmp_path / "artifacts"),
    )
    await _seed_project_context(store_group)

    service = TaskService(store_group, SSEHub())
    llm_service = PlannerAwareLLMService()
    message = NormalizedMessage(
        channel="web",
        thread_id="thread-alpha",
        scope_id="chat:web:thread-alpha",
        text="请继续推进 Alpha 的方案拆解",
        idempotency_key="f051-single-loop-recall-plan-001",
    )
    task_id, created = await service.create_task(message)
    assert created is True

    await service.process_task_with_llm(
        task_id=task_id,
        user_text=message.text,
        llm_service=llm_service,
        dispatch_metadata={
            **(await service.get_latest_user_metadata(task_id)),
            "single_loop_executor": True,
            "selected_worker_type": "general",
            "selected_tools_json": "[]",
        },
    )

    assert len(llm_service.calls) == 1
    final_call = llm_service.calls[0]
    assert final_call["metadata"]["single_loop_executor"] is True
    joined = "\n".join(
        str(item.get("content", ""))
        for item in final_call["prompt_or_messages"]
        if isinstance(item, dict)
    )
    assert "RecallPlanningContext:" not in joined

    artifacts = await store_group.artifact_store.list_artifacts_for_task(task_id)
    artifact_names = {item.name for item in artifacts}
    assert "memory-recall-plan-request" not in artifact_names
    assert "memory-recall-plan-response" not in artifact_names

    await store_group.conn.close()


async def test_agent_session_replay_projection_pairs_tool_turns_and_drops_orphans(
    tmp_path: Path,
) -> None:
    store_group = await create_store_group(
        str(tmp_path / "f051-session-replay.db"),
        str(tmp_path / "artifacts"),
    )

    session_id = "agent-session-replay-001"
    runtime_id = "agent-runtime-replay-001"
    await store_group.agent_context_store.save_agent_runtime(
        AgentRuntime(
            agent_runtime_id=runtime_id,
            role=AgentRuntimeRole.BUTLER,
            project_id="project-default",
            workspace_id="workspace-default",
        )
    )
    await store_group.agent_context_store.save_agent_session(
        AgentSession(
            agent_session_id=session_id,
            agent_runtime_id=runtime_id,
            kind=AgentSessionKind.BUTLER_MAIN,
            rolling_summary="之前已经确认过 Alpha continuity 的长期约束。",
            metadata={},
        )
    )
    turns = [
        AgentSessionTurn(
            agent_session_turn_id="turn-001",
            agent_session_id=session_id,
            task_id="task-001",
            turn_seq=1,
            kind=AgentSessionTurnKind.USER_MESSAGE,
            role="user",
            summary="请继续推进 Alpha 方案。",
        ),
        AgentSessionTurn(
            agent_session_turn_id="turn-002",
            agent_session_id=session_id,
            task_id="task-001",
            turn_seq=2,
            kind=AgentSessionTurnKind.TOOL_CALL,
            role="assistant",
            tool_name="web.search",
            summary='web.search({"q":"Alpha continuity"})',
        ),
        AgentSessionTurn(
            agent_session_turn_id="turn-003",
            agent_session_id=session_id,
            task_id="task-001",
            turn_seq=3,
            kind=AgentSessionTurnKind.TOOL_RESULT,
            role="tool",
            tool_name="web.search",
            summary="命中 2 条 Alpha continuity 相关结果。",
        ),
        AgentSessionTurn(
            agent_session_turn_id="turn-004",
            agent_session_id=session_id,
            task_id="task-001",
            turn_seq=4,
            kind=AgentSessionTurnKind.TOOL_RESULT,
            role="tool",
            tool_name="browser.snapshot",
            summary="孤立的 snapshot 结果。",
        ),
        AgentSessionTurn(
            agent_session_turn_id="turn-005",
            agent_session_id=session_id,
            task_id="task-001",
            turn_seq=5,
            kind=AgentSessionTurnKind.CONTEXT_SUMMARY,
            role="system",
            summary="Alpha continuity 约束要求先对齐上下文再回答。",
        ),
        AgentSessionTurn(
            agent_session_turn_id="turn-006",
            agent_session_id=session_id,
            task_id="task-001",
            turn_seq=6,
            kind=AgentSessionTurnKind.ASSISTANT_MESSAGE,
            role="assistant",
            summary="我已经基于 continuity 约束继续推进。",
        ),
        AgentSessionTurn(
            agent_session_turn_id="turn-007",
            agent_session_id=session_id,
            task_id="task-001",
            turn_seq=7,
            kind=AgentSessionTurnKind.TOOL_CALL,
            role="assistant",
            tool_name="memory.search",
            summary='memory.search({"query":"Alpha"})',
        ),
    ]
    for turn in turns:
        await store_group.agent_context_store.save_agent_session_turn(turn)
    await store_group.conn.commit()

    projection = await AgentContextService(store_group).build_agent_session_replay_projection(
        agent_session_id=session_id
    )
    assert projection.source == "agent_session_turn_store"
    assert projection.transcript_entries[-2:] == [
        {
            "role": "user",
            "content": "请继续推进 Alpha 方案。",
            "task_id": "task-001",
        },
        {
            "role": "assistant",
            "content": "我已经基于 continuity 约束继续推进。",
            "task_id": "task-001",
        },
    ]
    assert projection.tool_exchange_lines == [
        "- web.search: 命中 2 条 Alpha continuity 相关结果。",
        "- browser.snapshot: 孤立的 snapshot 结果。",
    ]
    assert projection.latest_context_summary == "Alpha continuity 约束要求先对齐上下文再回答。"
    assert projection.dropped_orphan_tool_calls == 1
    assert projection.dropped_orphan_tool_results == 1

    await store_group.conn.close()


async def test_task_service_worker_context_defaults_to_private_namespace_hint_first_recall(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store_group = await create_store_group(
        str(tmp_path / "f038-worker-private.db"),
        str(tmp_path / "artifacts"),
    )
    await _seed_project_context(store_group)
    worker_profile = WorkerProfile(
        profile_id="worker-profile-alpha-research",
        scope=AgentProfileScope.PROJECT,
        project_id="project-alpha",
        name="Alpha Research Worker",
        summary="负责处理需要检索与调研的任务。",
        model_alias="main",
        tool_profile="standard",
        default_tool_groups=["network"],
        selected_tools=["web.search"],
        runtime_kinds=["worker"],
        status=WorkerProfileStatus.ACTIVE,
        origin_kind=WorkerProfileOriginKind.CUSTOM,
        draft_revision=1,
        active_revision=1,
    )
    await store_group.agent_context_store.save_worker_profile(worker_profile)
    await store_group.conn.commit()

    memory_calls: list[dict[str, object]] = []
    worker_runtime_id = build_agent_runtime_id(
        role=AgentRuntimeRole.WORKER,
        project_id="project-alpha",
        workspace_id="workspace-alpha",
        agent_profile_id=worker_profile.profile_id,
        worker_profile_id=worker_profile.profile_id,
        worker_capability="research",
    )
    worker_agent_session_id = build_agent_session_id(
        agent_runtime_id=worker_runtime_id,
        kind=AgentSessionKind.WORKER_INTERNAL,
        legacy_session_id="worker-thread-alpha",
        work_id="work-alpha-1",
        task_id="worker-task-alpha",
    )
    worker_private_scope_ids = build_private_memory_scope_ids(
        kind=MemoryNamespaceKind.WORKER_PRIVATE,
        agent_runtime_id=worker_runtime_id,
        agent_session_id=worker_agent_session_id,
    )

    async def fake_recall_memory(
        self,
        *,
        scope_ids,
        query,
        policy=None,
        per_scope_limit=3,
        max_hits=4,
        hook_options=None,
    ):
        memory_calls.append({"scope_ids": list(scope_ids), "query": query})
        return MemoryRecallResult(
            query=query,
            expanded_queries=[query],
            scope_ids=list(scope_ids),
            hits=[
                MemoryRecallHit(
                    record_id="memory-worker-1",
                    layer=MemoryLayer.FRAGMENT,
                    scope_id=worker_private_scope_ids[0],
                    partition=MemoryPartition.WORK,
                    summary="Worker 私有记忆记录了上次调研偏好与检索策略。",
                    subject_key="worker-research-preference",
                    search_query=query,
                    citation=f"memory://{worker_private_scope_ids[0]}/fragment/worker-research-preference",
                    content_preview="优先先查官网和权威资料，再给 Butler 汇总。",
                    metadata={"source": "worker-private-test"},
                    created_at=datetime.now(tz=UTC),
                )
            ],
            backend_status=MemoryBackendStatus(
                backend_id="sqlite",
                active_backend="sqlite",
                state=MemoryBackendState.HEALTHY,
            ),
            degraded_reasons=[],
        )

    monkeypatch.setattr(MemoryService, "recall_memory", fake_recall_memory)

    service = TaskService(store_group, SSEHub())
    llm_service = RecordingLLMService()
    message = NormalizedMessage(
        channel="web",
        thread_id="thread-alpha",
        scope_id="chat:web:thread-alpha",
        text="继续处理 Alpha 的官网调研任务",
        idempotency_key="f038-worker-private-001",
    )
    task_id, created = await service.create_task(message)
    assert created is True

    runtime_context = RuntimeControlContext(
        task_id=task_id,
        trace_id=f"trace-{task_id}",
        surface="web",
        scope_id="chat:web:thread-alpha",
        thread_id="thread-alpha",
        session_id="worker-thread-alpha",
        project_id="project-alpha",
        workspace_id="workspace-alpha",
        work_id="work-alpha-1",
        agent_profile_id=worker_profile.profile_id,
        worker_capability="research",
        metadata={
            "agent_runtime_id": worker_runtime_id,
            "agent_session_id": worker_agent_session_id,
            "parent_agent_session_id": "butler-session-alpha",
        },
    )

    await service.process_task_with_llm(
        task_id=task_id,
        user_text=message.text,
        llm_service=llm_service,
        worker_capability="research",
        runtime_context=runtime_context,
        dispatch_metadata={
            **(await service.get_latest_user_metadata(task_id)),
            "requested_worker_profile_id": worker_profile.profile_id,
            "parent_agent_session_id": "butler-session-alpha",
            "work_id": "work-alpha-1",
        },
    )

    assert memory_calls == []

    frames = await store_group.agent_context_store.list_context_frames(task_id=task_id, limit=5)
    assert len(frames) == 1
    frame = frames[0]
    assert frame.memory_hits == []
    assert frame.budget["memory_recall"]["recall_owner_role"] == AgentRuntimeRole.WORKER.value
    assert any(
        item["namespace_kind"] == MemoryNamespaceKind.WORKER_PRIVATE.value
        for item in frame.budget["memory_recall"]["scope_entries"]
    )
    assert frame.budget["memory_recall"]["prefetch_mode"] == "hint_first"
    assert frame.budget["memory_recall"]["agent_led_recall_expected"] is True
    assert frame.budget["memory_recall"]["available_tools"] == [
        "memory.search",
        "memory.recall",
        "memory.read",
    ]

    runtime = await store_group.agent_context_store.get_agent_runtime(frame.agent_runtime_id)
    assert runtime is not None
    assert runtime.role is AgentRuntimeRole.WORKER
    assert runtime.agent_profile_id == worker_profile.profile_id
    assert runtime.worker_profile_id == worker_profile.profile_id

    agent_session = await store_group.agent_context_store.get_agent_session(frame.agent_session_id)
    assert agent_session is not None
    assert agent_session.kind is AgentSessionKind.WORKER_INTERNAL
    assert agent_session.work_id == "work-alpha-1"
    assert agent_session.recent_transcript[-2:] == [
        {
            "role": "user",
            "content": "继续处理 Alpha 的官网调研任务",
            "task_id": task_id,
        },
        {
            "role": "assistant",
            "content": "已结合上下文生成回答",
            "task_id": task_id,
        },
    ]
    assert agent_session.metadata["recent_transcript"][-2:] == [
        {
            "role": "user",
            "content": "继续处理 Alpha 的官网调研任务",
            "task_id": task_id,
        },
        {
            "role": "assistant",
            "content": "已结合上下文生成回答",
            "task_id": task_id,
        },
    ]
    session_turns = await store_group.agent_context_store.list_agent_session_turns(
        agent_session_id=frame.agent_session_id,
        limit=10,
    )
    assert [item.kind for item in session_turns[-2:]] == [
        AgentSessionTurnKind.USER_MESSAGE,
        AgentSessionTurnKind.ASSISTANT_MESSAGE,
    ]

    namespaces = await store_group.agent_context_store.list_memory_namespaces(
        project_id="project-alpha",
        agent_runtime_id=frame.agent_runtime_id,
    )
    assert {item.kind for item in namespaces} == {
        MemoryNamespaceKind.PROJECT_SHARED,
        MemoryNamespaceKind.WORKER_PRIVATE,
    }
    worker_private_namespace = next(
        item for item in namespaces if item.kind is MemoryNamespaceKind.WORKER_PRIVATE
    )
    assert worker_private_namespace.memory_scope_ids == worker_private_scope_ids

    prompt = llm_service.calls[0]["prompt_or_messages"]
    assert isinstance(prompt, list)
    joined = "\n".join(str(item.get("content", "")) for item in prompt)
    assert "MemoryRuntime:" in joined
    assert "mode: hint_first" in joined
    assert "MemoryRecallHints:" in joined
    assert "当前未预取详细命中" in joined
    assert "memory.recall / memory.search / memory.read" in joined

    await store_group.conn.close()


async def test_task_service_worker_context_enables_planned_recall_by_default(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store_group = await create_store_group(
        str(tmp_path / "f051-worker-planned-recall.db"),
        str(tmp_path / "artifacts"),
    )
    await _seed_project_context(store_group)
    worker_profile = WorkerProfile(
        profile_id="worker-profile-alpha-research",
        scope=AgentProfileScope.PROJECT,
        project_id="project-alpha",
        name="Alpha Research Worker",
        summary="负责处理需要检索与调研的任务。",
        model_alias="main",
        tool_profile="standard",
        default_tool_groups=["network"],
        selected_tools=["web.search"],
        runtime_kinds=["worker"],
        status=WorkerProfileStatus.ACTIVE,
        origin_kind=WorkerProfileOriginKind.CUSTOM,
        draft_revision=1,
        active_revision=1,
    )
    await store_group.agent_context_store.save_worker_profile(worker_profile)
    await store_group.conn.commit()

    memory_calls: list[dict[str, object]] = []
    worker_runtime_id = build_agent_runtime_id(
        role=AgentRuntimeRole.WORKER,
        project_id="project-alpha",
        workspace_id="workspace-alpha",
        agent_profile_id=worker_profile.profile_id,
        worker_profile_id=worker_profile.profile_id,
        worker_capability="research",
    )
    worker_agent_session_id = build_agent_session_id(
        agent_runtime_id=worker_runtime_id,
        kind=AgentSessionKind.WORKER_INTERNAL,
        legacy_session_id="worker-thread-alpha",
        work_id="work-alpha-2",
        task_id="worker-task-alpha-2",
    )
    worker_private_scope_ids = build_private_memory_scope_ids(
        kind=MemoryNamespaceKind.WORKER_PRIVATE,
        agent_runtime_id=worker_runtime_id,
        agent_session_id=worker_agent_session_id,
    )

    async def fake_recall_memory(
        self,
        *,
        scope_ids,
        query,
        policy=None,
        per_scope_limit=3,
        max_hits=4,
        hook_options=None,
    ):
        memory_calls.append(
            {
                "scope_ids": list(scope_ids),
                "query": query,
                "policy": policy.model_dump(mode="json") if policy is not None else {},
                "hook_options": (
                    hook_options.model_dump(mode="json") if hook_options is not None else {}
                ),
            }
        )
        return MemoryRecallResult(
            query=query,
            expanded_queries=[query],
            scope_ids=list(scope_ids),
            hits=[
                MemoryRecallHit(
                    record_id="memory-worker-plan-1",
                    layer=MemoryLayer.FRAGMENT,
                    scope_id=worker_private_scope_ids[0],
                    partition=MemoryPartition.WORK,
                    summary="Worker 私有记忆保留了上次 Alpha 调研的有效检索策略。",
                    subject_key="worker-research-preference",
                    search_query=query,
                    citation=f"memory://{worker_private_scope_ids[0]}/fragment/worker-research-preference",
                    content_preview="优先先查官网和权威资料，再给 Butler 汇总。",
                    metadata={"source": "worker-planned-test"},
                    created_at=datetime.now(tz=UTC),
                )
            ],
            backend_status=MemoryBackendStatus(
                backend_id="sqlite",
                active_backend="sqlite",
                state=MemoryBackendState.HEALTHY,
            ),
            degraded_reasons=[],
            hook_trace=MemoryRecallHookTrace(
                post_filter_mode=MemoryRecallPostFilterMode.KEYWORD_OVERLAP,
                rerank_mode=MemoryRecallRerankMode.HEURISTIC,
                focus_terms=["Alpha", "连续性", "里程碑"],
                candidate_count=1,
                filtered_count=0,
                delivered_count=1,
            ),
        )

    monkeypatch.setattr(MemoryService, "recall_memory", fake_recall_memory)

    service = TaskService(store_group, SSEHub())
    llm_service = PlannerAwareLLMService()
    message = NormalizedMessage(
        channel="web",
        thread_id="thread-alpha",
        scope_id="chat:web:thread-alpha",
        text="继续处理 Alpha 的官网调研任务",
        idempotency_key="f051-worker-planned-recall-001",
    )
    task_id, created = await service.create_task(message)
    assert created is True

    runtime_context = RuntimeControlContext(
        task_id=task_id,
        trace_id=f"trace-{task_id}",
        surface="web",
        scope_id="chat:web:thread-alpha",
        thread_id="thread-alpha",
        session_id="worker-thread-alpha",
        project_id="project-alpha",
        workspace_id="workspace-alpha",
        work_id="work-alpha-2",
        agent_profile_id=worker_profile.profile_id,
        worker_capability="research",
        metadata={
            "agent_runtime_id": worker_runtime_id,
            "agent_session_id": worker_agent_session_id,
            "parent_agent_session_id": "butler-session-alpha",
        },
    )

    await service.process_task_with_llm(
        task_id=task_id,
        user_text=message.text,
        llm_service=llm_service,
        worker_capability="research",
        runtime_context=runtime_context,
        dispatch_metadata={
            **(await service.get_latest_user_metadata(task_id)),
            "requested_worker_profile_id": worker_profile.profile_id,
            "parent_agent_session_id": "butler-session-alpha",
            "work_id": "work-alpha-2",
        },
    )

    assert len(llm_service.calls) == 2
    planner_prompt = llm_service.calls[0]["prompt_or_messages"]
    final_prompt = llm_service.calls[1]["prompt_or_messages"]
    assert isinstance(planner_prompt, list)
    assert isinstance(final_prompt, list)
    planner_joined = "\n".join(str(item.get("content", "")) for item in planner_prompt)
    final_joined = "\n".join(str(item.get("content", "")) for item in final_prompt)
    assert "RecallPlanningContext:" in planner_joined
    assert "mode: hint_first" in final_joined
    assert "Worker 私有记忆保留了上次 Alpha 调研的有效检索策略" in final_joined

    assert len(memory_calls) == 1
    assert memory_calls[0]["query"] == "Alpha continuity constraints milestone plan"
    assert memory_calls[0]["policy"]["allow_vault"] is False
    assert memory_calls[0]["hook_options"]["focus_terms"] == ["Alpha", "连续性", "里程碑"]

    frames = await store_group.agent_context_store.list_context_frames(task_id=task_id, limit=5)
    assert len(frames) == 1
    frame = frames[0]
    assert frame.budget["memory_recall"]["prefetch_mode"] == "hint_first"
    assert frame.budget["memory_recall"]["agent_led_recall_executed"] is True
    assert frame.budget["memory_recall"]["recall_plan"]["query"] == (
        "Alpha continuity constraints milestone plan"
    )
    assert frame.memory_hits[0]["record_id"] == "memory-worker-plan-1"

    await store_group.conn.close()


async def test_task_service_worker_context_respects_explicit_detailed_prefetch_override(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store_group = await create_store_group(
        str(tmp_path / "f051-worker-detailed-prefetch.db"),
        str(tmp_path / "artifacts"),
    )
    await _seed_project_context(store_group)
    worker_profile = WorkerProfile(
        profile_id="worker-profile-alpha-research",
        scope=AgentProfileScope.PROJECT,
        project_id="project-alpha",
        name="Alpha Research Worker",
        summary="负责处理需要检索与调研的任务。",
        model_alias="main",
        tool_profile="standard",
        default_tool_groups=["network"],
        selected_tools=["web.search"],
        runtime_kinds=["worker"],
        status=WorkerProfileStatus.ACTIVE,
        origin_kind=WorkerProfileOriginKind.CUSTOM,
        draft_revision=1,
        active_revision=1,
    )
    await store_group.agent_context_store.save_worker_profile(worker_profile)
    await store_group.agent_context_store.save_agent_profile(
        AgentProfile(
            profile_id=worker_profile.profile_id,
            scope=AgentProfileScope.PROJECT,
            project_id="project-alpha",
            name=worker_profile.name,
            persona_summary="显式覆盖成 detailed prefetch。",
            context_budget_policy={
                "memory_recall": {
                    "prefetch_mode": "detailed_prefetch",
                    "planner_enabled": False,
                }
            },
            metadata={"source_kind": "worker_profile_mirror"},
        )
    )
    await store_group.conn.commit()

    memory_calls: list[dict[str, object]] = []
    worker_runtime_id = build_agent_runtime_id(
        role=AgentRuntimeRole.WORKER,
        project_id="project-alpha",
        workspace_id="workspace-alpha",
        agent_profile_id=worker_profile.profile_id,
        worker_profile_id=worker_profile.profile_id,
        worker_capability="research",
    )
    worker_agent_session_id = build_agent_session_id(
        agent_runtime_id=worker_runtime_id,
        kind=AgentSessionKind.WORKER_INTERNAL,
        legacy_session_id="worker-thread-alpha",
        work_id="work-alpha-2",
        task_id="worker-task-alpha-2",
    )
    worker_private_scope_ids = build_private_memory_scope_ids(
        kind=MemoryNamespaceKind.WORKER_PRIVATE,
        agent_runtime_id=worker_runtime_id,
        agent_session_id=worker_agent_session_id,
    )

    async def fake_recall_memory(
        self,
        *,
        scope_ids,
        query,
        policy=None,
        per_scope_limit=3,
        max_hits=4,
        hook_options=None,
    ):
        _ = policy, per_scope_limit, max_hits, hook_options
        memory_calls.append({"scope_ids": list(scope_ids), "query": query})
        return MemoryRecallResult(
            query=query,
            expanded_queries=[query],
            scope_ids=list(scope_ids),
            hits=[
                MemoryRecallHit(
                    record_id="memory-worker-override-1",
                    layer=MemoryLayer.FRAGMENT,
                    scope_id=worker_private_scope_ids[0],
                    partition=MemoryPartition.WORK,
                    summary="显式覆盖后重新回到 detailed prefetch。",
                    subject_key="worker-research-override",
                    search_query=query,
                    citation=f"memory://{worker_private_scope_ids[0]}/fragment/worker-research-override",
                    content_preview="这次会在 prompt 里直接注入 recall。",
                    metadata={"source": "worker-override-test"},
                    created_at=datetime.now(tz=UTC),
                )
            ],
            backend_status=MemoryBackendStatus(
                backend_id="sqlite",
                active_backend="sqlite",
                state=MemoryBackendState.HEALTHY,
            ),
            degraded_reasons=[],
        )

    monkeypatch.setattr(MemoryService, "recall_memory", fake_recall_memory)

    service = TaskService(store_group, SSEHub())
    llm_service = RecordingLLMService()
    message = NormalizedMessage(
        channel="web",
        thread_id="thread-alpha",
        scope_id="chat:web:thread-alpha",
        text="继续处理 Alpha 的官网调研任务",
        idempotency_key="f051-worker-detailed-prefetch-001",
    )
    task_id, created = await service.create_task(message)
    assert created is True

    runtime_context = RuntimeControlContext(
        task_id=task_id,
        trace_id=f"trace-{task_id}",
        surface="web",
        scope_id="chat:web:thread-alpha",
        thread_id="thread-alpha",
        session_id="worker-thread-alpha",
        project_id="project-alpha",
        workspace_id="workspace-alpha",
        work_id="work-alpha-2",
        agent_profile_id=worker_profile.profile_id,
        worker_capability="research",
        metadata={
            "agent_runtime_id": worker_runtime_id,
            "agent_session_id": worker_agent_session_id,
            "parent_agent_session_id": "butler-session-alpha",
        },
    )

    await service.process_task_with_llm(
        task_id=task_id,
        user_text=message.text,
        llm_service=llm_service,
        worker_capability="research",
        runtime_context=runtime_context,
        dispatch_metadata={
            **(await service.get_latest_user_metadata(task_id)),
            "requested_worker_profile_id": worker_profile.profile_id,
            "parent_agent_session_id": "butler-session-alpha",
            "work_id": "work-alpha-2",
        },
    )

    assert len(memory_calls) == 1
    assert set(worker_private_scope_ids).issubset(set(memory_calls[0]["scope_ids"]))

    frames = await store_group.agent_context_store.list_context_frames(task_id=task_id, limit=5)
    assert len(frames) == 1
    frame = frames[0]
    assert frame.budget["memory_recall"]["prefetch_mode"] == "detailed_prefetch"
    assert frame.memory_hits[0]["record_id"] == "memory-worker-override-1"

    prompt = llm_service.calls[0]["prompt_or_messages"]
    assert isinstance(prompt, list)
    joined = "\n".join(str(item.get("content", "")) for item in prompt)
    assert "mode: detailed_prefetch" in joined
    assert "MemoryRecall:" in joined
    assert "显式覆盖后重新回到 detailed prefetch" in joined

    await store_group.conn.close()


async def test_task_service_worker_private_writeback_surfaces_runtime_memory_hints_across_sessions(
    tmp_path: Path,
) -> None:
    store_group = await create_store_group(
        str(tmp_path / "f038-worker-writeback.db"),
        str(tmp_path / "artifacts"),
    )
    await _seed_project_context(store_group)
    worker_profile = WorkerProfile(
        profile_id="worker-profile-alpha-research",
        scope=AgentProfileScope.PROJECT,
        project_id="project-alpha",
        name="Alpha Research Worker",
        summary="负责处理需要检索与调研的任务。",
        model_alias="main",
        tool_profile="standard",
        default_tool_groups=["network"],
        selected_tools=["web.search"],
        runtime_kinds=["worker"],
        status=WorkerProfileStatus.ACTIVE,
        origin_kind=WorkerProfileOriginKind.CUSTOM,
        draft_revision=1,
        active_revision=1,
    )
    await store_group.agent_context_store.save_worker_profile(worker_profile)
    await store_group.conn.commit()

    worker_runtime_id = build_agent_runtime_id(
        role=AgentRuntimeRole.WORKER,
        project_id="project-alpha",
        workspace_id="workspace-alpha",
        agent_profile_id=worker_profile.profile_id,
        worker_profile_id=worker_profile.profile_id,
        worker_capability="research",
    )
    service = TaskService(store_group, SSEHub())
    llm_service = RecordingLLMService()

    async def run_worker_turn(
        *,
        work_id: str,
        agent_session_id: str,
        text: str,
        idempotency_key: str,
    ):
        message = NormalizedMessage(
            channel="web",
            thread_id="thread-alpha",
            scope_id="chat:web:thread-alpha",
            text=text,
            idempotency_key=idempotency_key,
        )
        task_id, created = await service.create_task(message)
        assert created is True
        runtime_context = RuntimeControlContext(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            surface="web",
            scope_id="chat:web:thread-alpha",
            thread_id="thread-alpha",
            session_id="worker-thread-alpha",
            project_id="project-alpha",
            workspace_id="workspace-alpha",
            work_id=work_id,
            agent_profile_id=worker_profile.profile_id,
            worker_capability="research",
            metadata={
                "agent_runtime_id": worker_runtime_id,
                "agent_session_id": agent_session_id,
                "parent_agent_session_id": "butler-session-alpha",
            },
        )
        await service.process_task_with_llm(
            task_id=task_id,
            user_text=message.text,
            llm_service=llm_service,
            worker_capability="research",
            runtime_context=runtime_context,
            dispatch_metadata={
                **(await service.get_latest_user_metadata(task_id)),
                "requested_worker_profile_id": worker_profile.profile_id,
                "parent_agent_session_id": "butler-session-alpha",
                "work_id": work_id,
            },
        )
        frames = await store_group.agent_context_store.list_context_frames(task_id=task_id, limit=5)
        assert len(frames) == 1
        return task_id, frames[0]

    first_agent_session_id = build_agent_session_id(
        agent_runtime_id=worker_runtime_id,
        kind=AgentSessionKind.WORKER_INTERNAL,
        legacy_session_id="worker-thread-alpha",
        work_id="work-alpha-1",
        task_id="worker-task-alpha-1",
    )
    first_private_scope_ids = build_private_memory_scope_ids(
        kind=MemoryNamespaceKind.WORKER_PRIVATE,
        agent_runtime_id=worker_runtime_id,
        agent_session_id=first_agent_session_id,
    )
    _first_task_id, first_frame = await run_worker_turn(
        work_id="work-alpha-1",
        agent_session_id=first_agent_session_id,
        text="请记住 alpha-official-root 这个官网调研线索，后续继续跟进。",
        idempotency_key="f038-worker-writeback-001",
    )

    writeback = first_frame.budget["private_memory_writeback"]
    # private_memory_writeback 现在使用 PROJECT_SHARED namespace（而非 WORKER_PRIVATE）
    assert writeback["namespace_kind"] == MemoryNamespaceKind.PROJECT_SHARED.value
    # scope_id 由 PROJECT_SHARED namespace 的 memory_scope_ids 决定（通常为 memory/project-{id}）
    actual_writeback_scope_id = writeback["scope_id"]
    assert actual_writeback_scope_id  # 确保非空
    assert writeback["scope_kind"]    # 确保有值（namespace_primary / runtime_private 等）
    assert writeback["fragment_refs"]
    assert any(
        ref["ref_type"] == "memory_maintenance_run" and ref["ref_id"] == writeback["run_id"]
        for ref in first_frame.source_refs
    )

    first_session = await store_group.agent_context_store.get_agent_session(first_frame.agent_session_id)
    assert first_session is not None
    # session metadata key 已改名为 last_memory_writeback_run_id / last_memory_scope_id
    assert first_session.metadata["last_memory_writeback_run_id"] == writeback["run_id"]
    assert first_session.metadata["last_memory_scope_id"] == actual_writeback_scope_id

    cursor = await store_group.conn.execute(
        """
        SELECT scope_id, content
        FROM memory_fragments
        WHERE scope_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (actual_writeback_scope_id,),
    )
    fragment_row = await cursor.fetchone()
    assert fragment_row is not None
    assert fragment_row[0] == actual_writeback_scope_id
    assert "alpha-official-root" in fragment_row[1]

    second_agent_session_id = build_agent_session_id(
        agent_runtime_id=worker_runtime_id,
        kind=AgentSessionKind.WORKER_INTERNAL,
        legacy_session_id="worker-thread-alpha",
        work_id="work-alpha-2",
        task_id="worker-task-alpha-2",
    )
    _second_task_id, second_frame = await run_worker_turn(
        work_id="work-alpha-2",
        agent_session_id=second_agent_session_id,
        text="继续处理 alpha-official-root 的官网调研，并给 Butler 一个更新。",
        idempotency_key="f038-worker-writeback-002",
    )

    assert second_frame.agent_session_id == second_agent_session_id
    assert second_frame.memory_hits == []
    assert second_frame.budget["memory_recall"]["recall_owner_role"] == AgentRuntimeRole.WORKER.value
    assert second_frame.budget["memory_recall"]["prefetch_mode"] == "hint_first"
    assert any(
        entry["scope_id"] == first_private_scope_ids[1]
        and entry["scope_kind"] == "runtime_private"
        and entry["namespace_kind"] == MemoryNamespaceKind.WORKER_PRIVATE.value
        for entry in second_frame.budget["memory_recall"]["scope_entries"]
    )

    prompt = llm_service.calls[-1]["prompt_or_messages"]
    assert isinstance(prompt, list)
    joined = "\n".join(str(item.get("content", "")) for item in prompt)
    assert "MemoryRuntime:" in joined
    assert "mode: hint_first" in joined
    assert "MemoryRecallHints:" in joined
    assert "当前未预取详细命中" in joined

    await store_group.conn.close()


async def test_task_service_worker_tool_writeback_commits_sor_and_surfaces_runtime_memory_hints(
    tmp_path: Path,
) -> None:
    store_group = await create_store_group(
        str(tmp_path / "f038-worker-tool-writeback.db"),
        str(tmp_path / "artifacts"),
    )
    await _seed_project_context(store_group)
    worker_profile = WorkerProfile(
        profile_id="worker-profile-alpha-research",
        scope=AgentProfileScope.PROJECT,
        project_id="project-alpha",
        name="Alpha Research Worker",
        summary="负责处理需要检索与调研的任务。",
        model_alias="main",
        tool_profile="standard",
        default_tool_groups=["network"],
        selected_tools=["web.search"],
        runtime_kinds=["worker"],
        status=WorkerProfileStatus.ACTIVE,
        origin_kind=WorkerProfileOriginKind.CUSTOM,
        draft_revision=1,
        active_revision=1,
    )
    await store_group.agent_context_store.save_worker_profile(worker_profile)
    await store_group.conn.commit()

    worker_runtime_id = build_agent_runtime_id(
        role=AgentRuntimeRole.WORKER,
        project_id="project-alpha",
        workspace_id="workspace-alpha",
        agent_profile_id=worker_profile.profile_id,
        worker_profile_id=worker_profile.profile_id,
        worker_capability="research",
    )
    service = TaskService(store_group, SSEHub())

    class ToolAwareLLMService:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []
            self.tool_event_ids: list[str] = []
            self.ignored_tool_event_ids: list[str] = []

        async def call(self, prompt_or_messages, model_alias: str | None = None, **kwargs):
            self.calls.append(
                {
                    "prompt_or_messages": prompt_or_messages,
                    "model_alias": model_alias,
                    **kwargs,
                }
            )
            task_id = str(kwargs["task_id"])
            trace_id = str(kwargs["trace_id"])
            metadata = dict(kwargs.get("metadata", {}))
            agent_runtime_id = str(metadata.get("agent_runtime_id", "")).strip()
            agent_session_id = str(metadata.get("agent_session_id", "")).strip()
            work_id = str(metadata.get("work_id", "")).strip()
            ignored_artifact = await service.create_text_artifact(
                task_id=task_id,
                name="tool_output:web.search:ignored",
                description="其他 worker session 的 web.search 完整输出",
                content="这条结果属于别的 worker session，不应该写回当前 private memory。",
                trace_id=trace_id,
                session_id="worker-session-ignored",
                source="tool_output:web.search",
            )
            ignored_event = await service.append_structured_event(
                task_id=task_id,
                event_type=EventType.TOOL_CALL_COMPLETED,
                actor=ActorType.TOOL,
                payload={
                    "tool_name": "web.search",
                    "duration_ms": 17,
                    "output_summary": "忽略：这条结果来自别的 worker session。",
                    "agent_runtime_id": agent_runtime_id,
                    "agent_session_id": "worker-session-ignored",
                    "work_id": "work-ignored",
                    "truncated": False,
                    "artifact_ref": ignored_artifact.artifact_id,
                },
                trace_id=trace_id,
            )
            self.ignored_tool_event_ids.append(ignored_event.event_id)
            artifact = await service.create_text_artifact(
                task_id=task_id,
                name="tool_output:web.search",
                description="web.search 完整输出",
                content="找到 agent-zero-playbook 的官方文档入口与相关官网线索。",
                trace_id=trace_id,
                session_id=agent_session_id,
                source="tool_output:web.search",
            )
            event = await service.append_structured_event(
                task_id=task_id,
                event_type=EventType.TOOL_CALL_COMPLETED,
                actor=ActorType.TOOL,
                payload={
                    "tool_name": "web.search",
                    "duration_ms": 18,
                    "output_summary": "找到 agent-zero-playbook 的官方文档入口与官网线索。",
                    "agent_runtime_id": agent_runtime_id,
                    "agent_session_id": agent_session_id,
                    "work_id": work_id,
                    "truncated": False,
                    "artifact_ref": artifact.artifact_id,
                },
                trace_id=trace_id,
            )
            self.tool_event_ids.append(event.event_id)
            return ModelCallResult(
                content="已完成官网检索，并整理关键入口给 Butler。",
                model_alias=model_alias or "main",
                model_name="mock-model",
                provider="mock",
                duration_ms=6,
                token_usage=TokenUsage(
                    prompt_tokens=10,
                    completion_tokens=12,
                    total_tokens=22,
                ),
                cost_usd=0.0,
                cost_unavailable=False,
                is_fallback=False,
                fallback_reason="",
            )

    async def run_worker_turn(
        *,
        work_id: str,
        agent_session_id: str,
        text: str,
        idempotency_key: str,
        llm_service,
    ):
        message = NormalizedMessage(
            channel="web",
            thread_id="thread-alpha",
            scope_id="chat:web:thread-alpha",
            text=text,
            idempotency_key=idempotency_key,
        )
        task_id, created = await service.create_task(message)
        assert created is True
        runtime_context = RuntimeControlContext(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            surface="web",
            scope_id="chat:web:thread-alpha",
            thread_id="thread-alpha",
            session_id="worker-thread-alpha",
            project_id="project-alpha",
            workspace_id="workspace-alpha",
            work_id=work_id,
            agent_profile_id=worker_profile.profile_id,
            worker_capability="research",
            metadata={
                "agent_runtime_id": worker_runtime_id,
                "agent_session_id": agent_session_id,
                "parent_agent_session_id": "butler-session-alpha",
            },
        )
        await service.process_task_with_llm(
            task_id=task_id,
            user_text=message.text,
            llm_service=llm_service,
            worker_capability="research",
            runtime_context=runtime_context,
            dispatch_metadata={
                **(await service.get_latest_user_metadata(task_id)),
                "requested_worker_profile_id": worker_profile.profile_id,
                "parent_agent_session_id": "butler-session-alpha",
                "work_id": work_id,
            },
        )
        frames = await store_group.agent_context_store.list_context_frames(task_id=task_id, limit=5)
        assert len(frames) == 1
        return task_id, frames[0]

    first_agent_session_id = build_agent_session_id(
        agent_runtime_id=worker_runtime_id,
        kind=AgentSessionKind.WORKER_INTERNAL,
        legacy_session_id="worker-thread-alpha",
        work_id="work-alpha-tool-1",
        task_id="worker-tool-task-1",
    )
    first_private_scope_ids = build_private_memory_scope_ids(
        kind=MemoryNamespaceKind.WORKER_PRIVATE,
        agent_runtime_id=worker_runtime_id,
        agent_session_id=first_agent_session_id,
    )
    tool_llm_service = ToolAwareLLMService()
    _first_task_id, first_frame = await run_worker_turn(
        work_id="work-alpha-tool-1",
        agent_session_id=first_agent_session_id,
        text="请先检索 agent-zero-playbook 的官网线索并记住。",
        idempotency_key="f038-worker-tool-writeback-001",
        llm_service=tool_llm_service,
    )

    tool_writeback = first_frame.budget["private_tool_writeback"]
    assert tool_writeback["status"] == "completed"
    assert tool_writeback["scope_id"] == first_private_scope_ids[1]
    assert tool_writeback["scope_kind"] == "runtime_private"
    assert tool_writeback["committed_count"] == 1
    assert tool_writeback["tool_names"] == ["web.search"]
    assert tool_llm_service.calls[0]["metadata"]["agent_runtime_id"] == worker_runtime_id
    assert tool_llm_service.calls[0]["metadata"]["agent_session_id"] == first_agent_session_id
    assert tool_llm_service.calls[0]["metadata"]["work_id"] == "work-alpha-tool-1"
    assert tool_llm_service.tool_event_ids[0] in tool_writeback["event_ids"]
    assert tool_llm_service.ignored_tool_event_ids[0] not in tool_writeback["event_ids"]
    assert any(ref["ref_type"] == "memory_sor" for ref in first_frame.source_refs)

    cursor = await store_group.conn.execute(
        """
        SELECT subject_key, content
        FROM memory_sor
        WHERE scope_id = ?
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (first_private_scope_ids[1],),
    )
    sor_row = await cursor.fetchone()
    assert sor_row is not None
    assert "worker_tool:web.search" in sor_row[0]
    assert "agent-zero-playbook" in sor_row[1]

    second_agent_session_id = build_agent_session_id(
        agent_runtime_id=worker_runtime_id,
        kind=AgentSessionKind.WORKER_INTERNAL,
        legacy_session_id="worker-thread-alpha",
        work_id="work-alpha-tool-2",
        task_id="worker-tool-task-2",
    )
    second_llm_service = RecordingLLMService()
    _second_task_id, second_frame = await run_worker_turn(
        work_id="work-alpha-tool-2",
        agent_session_id=second_agent_session_id,
        text="agent-zero-playbook",
        idempotency_key="f038-worker-tool-writeback-002",
        llm_service=second_llm_service,
    )

    assert second_frame.memory_hits == []
    assert second_frame.budget["memory_recall"]["prefetch_mode"] == "hint_first"
    assert any(
        entry["scope_id"] == first_private_scope_ids[1]
        and entry["scope_kind"] == "runtime_private"
        and entry["namespace_kind"] == MemoryNamespaceKind.WORKER_PRIVATE.value
        for entry in second_frame.budget["memory_recall"]["scope_entries"]
    )
    prompt = second_llm_service.calls[0]["prompt_or_messages"]
    assert isinstance(prompt, list)
    joined = "\n".join(str(item.get("content", "")) for item in prompt)
    assert "MemoryRuntime:" in joined
    assert "mode: hint_first" in joined
    assert "MemoryRecallHints:" in joined
    assert "memory.recall / memory.search / memory.read" in joined

    await store_group.conn.close()


async def test_task_service_prompt_context_only_exposes_sanitized_control_metadata(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store_group = await create_store_group(
        str(tmp_path / "f043-context.db"),
        str(tmp_path / "artifacts"),
    )
    await _seed_project_context(store_group)

    async def fake_recall_memory(
        self,
        *,
        scope_ids,
        query,
        policy=None,
        per_scope_limit=3,
        max_hits=4,
        hook_options=None,
    ):
        return MemoryRecallResult(
            query=query,
            expanded_queries=[query],
            scope_ids=list(scope_ids),
            hits=[],
            backend_status=MemoryBackendStatus(
                backend_id="sqlite",
                active_backend="sqlite",
                state=MemoryBackendState.HEALTHY,
            ),
            degraded_reasons=[],
        )

    monkeypatch.setattr(MemoryService, "recall_memory", fake_recall_memory)

    service = TaskService(store_group, SSEHub())
    llm_service = RecordingLLMService()
    message = NormalizedMessage(
        channel="web",
        thread_id="thread-alpha",
        scope_id="chat:web:thread-alpha",
        text="请继续推进 Alpha 的方案拆解",
        metadata={
            "agent_profile_id": "attacker-profile",
            "approval_token": "secret-token-123",
        },
        control_metadata={
            "agent_profile_id": "agent-profile-alpha",
            "requested_worker_profile_id": "agent-profile-alpha",
            "target_kind": "worker",
        },
        idempotency_key="f043-context-sanitize-001",
    )
    task_id, created = await service.create_task(message)
    assert created is True

    await service.process_task_with_llm(
        task_id=task_id,
        user_text=message.text,
        llm_service=llm_service,
        dispatch_metadata=await service.get_latest_user_metadata(task_id),
    )

    prompt = llm_service.calls[0]["prompt_or_messages"]
    assert isinstance(prompt, list)
    joined = "\n".join(str(item.get("content", "")) for item in prompt)
    assert "control_metadata_summary" in joined
    assert "agent-profile-alpha" in joined
    assert "attacker-profile" not in joined
    assert "secret-token-123" not in joined

    artifacts = await store_group.artifact_store.list_artifacts_for_task(task_id)
    request_artifact = next(item for item in artifacts if item.name == "llm-request-context")
    request_content = await store_group.artifact_store.get_artifact_content(
        request_artifact.artifact_id
    )
    assert request_content is not None
    request_text = request_content.decode("utf-8")
    assert "control_metadata_summary" in request_text
    assert "attacker-profile" not in request_text
    assert "secret-token-123" not in request_text

    await store_group.conn.close()


async def test_task_service_injects_runtime_hints_block_into_prompt_and_request_snapshot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store_group = await create_store_group(
        str(tmp_path / "f049-runtime-hints.db"),
        str(tmp_path / "artifacts"),
    )
    await _seed_project_context(store_group)

    async def fake_recall_memory(
        self,
        *,
        scope_ids,
        query,
        policy=None,
        per_scope_limit=3,
        max_hits=4,
        hook_options=None,
    ):
        return MemoryRecallResult(
            query=query,
            expanded_queries=[query],
            scope_ids=list(scope_ids),
            hits=[],
            backend_status=MemoryBackendStatus(
                backend_id="sqlite",
                active_backend="sqlite",
                state=MemoryBackendState.HEALTHY,
            ),
            degraded_reasons=[],
        )

    monkeypatch.setattr(MemoryService, "recall_memory", fake_recall_memory)

    service = TaskService(store_group, SSEHub())
    llm_service = RecordingLLMService()
    message = NormalizedMessage(
        channel="web",
        thread_id="thread-alpha",
        scope_id="chat:web:thread-alpha",
        text="深圳",
        idempotency_key="f049-runtime-hints-001",
    )
    task_id, created = await service.create_task(message)
    assert created is True

    await service.process_task_with_llm(
        task_id=task_id,
        user_text=message.text,
        llm_service=llm_service,
        dispatch_metadata={
            "clarification_category": "weather_location",
            "clarification_source_text": "今天天气怎么样？",
            "requested_worker_type": "research",
            "freshness_followup_location_text": "深圳",
        },
    )

    prompt = llm_service.calls[0]["prompt_or_messages"]
    assert isinstance(prompt, list)
    joined = "\n".join(str(item.get("content", "")) for item in prompt)
    assert "RuntimeHints:" in joined
    assert "current_user_text: 深圳" in joined
    assert "can_delegate_research: True" in joined
    assert "effective_location_hint: 深圳" in joined
    assert "recent_clarification_category: weather_location" in joined

    artifacts = await store_group.artifact_store.list_artifacts_for_task(task_id)
    request_artifact = next(item for item in artifacts if item.name == "llm-request-context")
    request_content = await store_group.artifact_store.get_artifact_content(
        request_artifact.artifact_id
    )
    assert request_content is not None
    request_text = request_content.decode("utf-8")
    assert "RuntimeHints:" in request_text
    assert "effective_location_hint: 深圳" in request_text

    await store_group.conn.close()


async def test_task_service_persists_delayed_recall_as_durable_artifacts_and_events(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store_group = await create_store_group(
        str(tmp_path / "f038-delayed-recall.db"),
        str(tmp_path / "artifacts"),
    )
    await _seed_project_context(store_group)
    await store_group.agent_context_store.save_agent_profile(
        AgentProfile(
            profile_id="agent-profile-alpha",
            scope=AgentProfileScope.PROJECT,
            project_id="project-alpha",
            name="Alpha Agent",
            persona_summary="你负责 Alpha 项目的需求连续性与交付推进。",
            instruction_overlays=["回答前必须对齐当前 project 的长期约束。"],
            context_budget_policy={
                "memory_recall": {
                    "prefetch_mode": "detailed_prefetch",
                }
            },
        )
    )
    await store_group.conn.commit()

    recall_calls: list[dict[str, object]] = []

    async def fake_recall_memory(
        self,
        *,
        scope_ids,
        query,
        policy=None,
        per_scope_limit=3,
        max_hits=4,
        hook_options=None,
    ):
        recall_calls.append(
            {
                "scope_ids": list(scope_ids),
                "query": query,
                "per_scope_limit": per_scope_limit,
                "max_hits": max_hits,
                "hook_options": (
                    hook_options.model_dump(mode="json") if hook_options is not None else {}
                ),
            }
        )
        if len(recall_calls) == 1:
            return MemoryRecallResult(
                query=query,
                expanded_queries=[query],
                scope_ids=list(scope_ids),
                hits=[
                    MemoryRecallHit(
                        record_id="memory-initial-1",
                        layer=MemoryLayer.SOR,
                        scope_id="memory/project-alpha",
                        partition=MemoryPartition.WORK,
                        summary="初始 recall 命中了一条关键约束。",
                        subject_key="alpha-initial",
                        search_query=query,
                        citation="memory://memory/project-alpha/sor/alpha-initial",
                        content_preview="Alpha 约束需要 durable delayed recall。",
                        metadata={"source": "delayed-initial"},
                        created_at=datetime.now(tz=UTC),
                    )
                ],
                backend_status=MemoryBackendStatus(
                    backend_id="sqlite",
                    active_backend="sqlite",
                    state=MemoryBackendState.DEGRADED,
                    pending_replay_count=2,
                ),
                degraded_reasons=["memory_sync_backlog"],
                hook_trace=MemoryRecallHookTrace(
                    post_filter_mode=MemoryRecallPostFilterMode.KEYWORD_OVERLAP,
                    rerank_mode=MemoryRecallRerankMode.HEURISTIC,
                    focus_terms=["Alpha", "delayed", "recall"],
                    candidate_count=1,
                    filtered_count=0,
                    delivered_count=1,
                ),
            )

        return MemoryRecallResult(
            query=query,
            expanded_queries=[query, "Alpha delayed recall"],
            scope_ids=list(scope_ids),
            hits=[
                MemoryRecallHit(
                    record_id="memory-delayed-1",
                    layer=MemoryLayer.SOR,
                    scope_id="memory/project-alpha",
                    partition=MemoryPartition.WORK,
                    summary="Delayed recall 返回了更完整的 Alpha 上下文。",
                    subject_key="alpha-delayed-1",
                    search_query="Alpha delayed recall",
                    citation="memory://memory/project-alpha/sor/alpha-delayed-1",
                    content_preview="Delayed recall hit 1",
                    metadata={"source": "delayed-result"},
                    created_at=datetime.now(tz=UTC),
                ),
                MemoryRecallHit(
                    record_id="memory-delayed-2",
                    layer=MemoryLayer.SOR,
                    scope_id="memory/project-alpha",
                    partition=MemoryPartition.WORK,
                    summary="Delayed recall 带回第二条补充事实。",
                    subject_key="alpha-delayed-2",
                    search_query="Alpha delayed recall",
                    citation="memory://memory/project-alpha/sor/alpha-delayed-2",
                    content_preview="Delayed recall hit 2",
                    metadata={"source": "delayed-result"},
                    created_at=datetime.now(tz=UTC),
                ),
            ],
            backend_status=MemoryBackendStatus(
                backend_id="sqlite",
                active_backend="sqlite",
                state=MemoryBackendState.HEALTHY,
                pending_replay_count=0,
            ),
            degraded_reasons=[],
            hook_trace=MemoryRecallHookTrace(
                post_filter_mode=MemoryRecallPostFilterMode.KEYWORD_OVERLAP,
                rerank_mode=MemoryRecallRerankMode.HEURISTIC,
                focus_terms=["Alpha", "delayed", "recall"],
                candidate_count=2,
                filtered_count=0,
                delivered_count=2,
            ),
        )

    monkeypatch.setattr(MemoryService, "recall_memory", fake_recall_memory)

    service = TaskService(store_group, SSEHub())
    llm_service = RecordingLLMService()
    message = NormalizedMessage(
        channel="web",
        thread_id="thread-alpha",
        scope_id="chat:web:thread-alpha",
        text="请继续推进 Alpha 的 delayed recall 验证",
        idempotency_key="f038-delayed-recall-001",
    )
    task_id, created = await service.create_task(message)
    assert created is True

    await service.process_task_with_llm(
        task_id=task_id,
        user_text=message.text,
        llm_service=llm_service,
    )

    assert len(recall_calls) == 2
    assert recall_calls[1]["max_hits"] >= 8
    assert recall_calls[0]["hook_options"]["post_filter_mode"] == "keyword_overlap"
    assert recall_calls[1]["hook_options"]["rerank_mode"] == "heuristic"

    events = await store_group.event_store.get_events_for_task(task_id)
    assert any(event.type is EventType.MEMORY_RECALL_SCHEDULED for event in events)
    assert any(event.type is EventType.MEMORY_RECALL_COMPLETED for event in events)

    artifacts = await store_group.artifact_store.list_artifacts_for_task(task_id)
    delayed_request = next(item for item in artifacts if item.name == "delayed-recall-request")
    delayed_result = next(item for item in artifacts if item.name == "delayed-recall-result")
    delayed_request_text = await store_group.artifact_store.get_artifact_content(
        delayed_request.artifact_id
    )
    delayed_result_text = await store_group.artifact_store.get_artifact_content(
        delayed_result.artifact_id
    )
    assert delayed_request_text is not None
    assert delayed_result_text is not None
    assert "memory_sync_backlog" in delayed_request_text.decode("utf-8")
    assert "memory-delayed-2" in delayed_result_text.decode("utf-8")

    frames = await store_group.agent_context_store.list_context_frames(task_id=task_id, limit=5)
    assert len(frames) == 1
    delayed_recall = frames[0].budget["delayed_recall"]
    assert delayed_recall["status"] == "completed"
    assert delayed_recall["request_artifact_ref"] == delayed_request.artifact_id
    assert delayed_recall["result_artifact_ref"] == delayed_result.artifact_id
    assert delayed_recall["hit_count"] == 2
    assert delayed_recall["backend_state"] == "healthy"

    await store_group.conn.close()


async def test_task_service_migrates_legacy_session_and_trims_prompt_budget(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("OCTOAGENT_CONTEXT_MAX_INPUT_TOKENS", "550")
    store_group = await create_store_group(
        str(tmp_path / "f033-context-budget.db"),
        str(tmp_path / "artifacts"),
    )
    await _seed_project_context(store_group)

    long_summary = "Alpha 历史约束。" * 220
    await store_group.agent_context_store.save_session_context(
        SessionContextState(
            session_id="thread-alpha",
            thread_id="thread-alpha",
            project_id="project-alpha",
            workspace_id="workspace-alpha",
            task_ids=["legacy-task"],
            recent_turn_refs=["legacy-task"],
            rolling_summary=long_summary,
            last_context_frame_id="context-frame-legacy",
        )
    )
    await store_group.conn.commit()

    async def fake_recall_memory(
        self,
        *,
        scope_ids,
        query,
        policy=None,
        per_scope_limit=3,
        max_hits=4,
        hook_options=None,
    ):
        return MemoryRecallResult(
            query=query,
            expanded_queries=[query, "Alpha 预算裁剪"],
            scope_ids=list(scope_ids),
            hits=[
                MemoryRecallHit(
                    record_id=f"memory-{index}",
                    layer=MemoryLayer.SOR,
                    scope_id=scope_ids[0],
                    partition=MemoryPartition.WORK,
                    summary=("记忆摘要。" * 120) + str(index),
                    subject_key=f"subject-{index}",
                    search_query="Alpha 预算裁剪",
                    citation=f"memory://{scope_ids[0]}/sor/subject-{index}",
                    content_preview=("记忆正文。" * 60) + str(index),
                    metadata={"source": "budget-test"},
                    created_at=datetime.now(tz=UTC),
                )
                for index in range(4)
            ],
            backend_status=MemoryBackendStatus(
                backend_id="sqlite",
                active_backend="sqlite",
                state=MemoryBackendState.HEALTHY,
            ),
            hook_trace=MemoryRecallHookTrace(
                post_filter_mode=MemoryRecallPostFilterMode.KEYWORD_OVERLAP,
                rerank_mode=MemoryRecallRerankMode.HEURISTIC,
                focus_terms=["Alpha", "预算裁剪"],
                candidate_count=4,
                filtered_count=0,
                delivered_count=4,
            ),
        )

    monkeypatch.setattr(MemoryService, "recall_memory", fake_recall_memory)

    service = TaskService(store_group, SSEHub())
    llm_service = RecordingLLMService()
    message = NormalizedMessage(
        channel="web",
        thread_id="thread-alpha",
        scope_id="chat:web:thread-alpha",
        text="请继续推进 Alpha 的方案拆解",
        idempotency_key="f033-context-budget-001",
    )
    task_id, created = await service.create_task(message)
    assert created is True

    await service.process_task_with_llm(
        task_id=task_id,
        user_text=message.text,
        llm_service=llm_service,
    )

    task = await store_group.task_store.get_task(task_id)
    assert task is not None
    session_id = build_scope_aware_session_id(
        task,
        project_id="project-alpha",
        workspace_id="workspace-alpha",
    )
    migrated_state = await store_group.agent_context_store.get_session_context(session_id)
    legacy_state = await store_group.agent_context_store.get_session_context("thread-alpha")
    assert migrated_state is not None
    assert legacy_state is None

    frames = await store_group.agent_context_store.list_context_frames(task_id=task_id, limit=5)
    assert len(frames) == 1
    frame = frames[0]
    assert frame.budget["final_prompt_tokens"] > 550
    assert frame.budget["history_tokens"] < frame.budget["final_prompt_tokens"]
    assert "context_budget_trimmed" in frame.degraded_reason
    assert "context_budget_exceeded" in frame.degraded_reason
    assert len(frame.memory_hits) < 4 or len(frame.recent_summary) < len(long_summary)

    await store_group.conn.close()


async def test_task_service_prefers_frozen_runtime_context_over_live_selector(
    tmp_path: Path,
) -> None:
    store_group = await create_store_group(
        str(tmp_path / "f037-runtime-context.db"),
        str(tmp_path / "artifacts"),
    )
    await _seed_project_context(store_group)
    await store_group.project_store.save_project(
        Project(
            project_id="project-beta",
            slug="beta",
            name="Beta Project",
            description="Beta 项目只关注临时实验。",
        )
    )
    await store_group.project_store.create_workspace(
        Workspace(
            workspace_id="workspace-beta",
            project_id="project-beta",
            slug="lab",
            name="Beta Workspace",
            root_path="/tmp/beta",
        )
    )
    await store_group.project_store.save_selector_state(
        ProjectSelectorState(
            selector_id="selector-web",
            surface="web",
            active_project_id="project-alpha",
            active_workspace_id="workspace-alpha",
            source="tests",
        )
    )
    await store_group.conn.commit()

    service = TaskService(store_group, SSEHub())
    llm_service = RecordingLLMService()
    message = NormalizedMessage(
        channel="web",
        thread_id="thread-runtime-drift",
        text="请继续推进默认 project 的方案拆解",
        idempotency_key="f037-runtime-context-001",
    )
    task_id, created = await service.create_task(message)
    assert created is True
    task = await store_group.task_store.get_task(task_id)
    assert task is not None

    frozen_context = RuntimeControlContext(
        task_id=task_id,
        trace_id=f"trace-{task_id}",
        contract_version="1.0",
        surface="web",
        scope_id=task.scope_id,
        thread_id=task.thread_id,
        session_id=build_scope_aware_session_id(
            task,
            project_id="project-alpha",
            workspace_id="workspace-alpha",
        ),
        project_id="project-alpha",
        workspace_id="workspace-alpha",
        hop_count=1,
        max_hops=3,
        worker_capability="llm_generation",
        route_reason="frozen_runtime_context_test",
        model_alias="main",
        tool_profile="standard",
    )

    await store_group.project_store.save_selector_state(
        ProjectSelectorState(
            selector_id="selector-web",
            surface="web",
            active_project_id="project-beta",
            active_workspace_id="workspace-beta",
            source="tests",
        )
    )
    await store_group.conn.commit()

    await service.process_task_with_llm(
        task_id=task_id,
        user_text=message.text,
        llm_service=llm_service,
        runtime_context=frozen_context,
    )

    prompt = llm_service.calls[0]["prompt_or_messages"]
    assert isinstance(prompt, list)
    joined = "\n".join(str(item.get("content", "")) for item in prompt)
    assert "Alpha 项目要求保持严格的需求连续性" in joined
    assert "Beta 项目只关注临时实验" not in joined

    frames = await store_group.agent_context_store.list_context_frames(task_id=task_id, limit=5)
    assert len(frames) == 1
    assert frames[0].project_id == "project-alpha"
    assert frames[0].workspace_id == "workspace-alpha"
    assert any(
        ref["ref_type"] == "runtime_context" for ref in frames[0].source_refs
    )

    await store_group.conn.close()
