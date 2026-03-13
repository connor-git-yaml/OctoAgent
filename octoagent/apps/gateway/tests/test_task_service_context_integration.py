"""Feature 033: TaskService 上下文连续性接线测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from octoagent.core.models import (
    AgentProfile,
    AgentProfileScope,
    BootstrapSession,
    BootstrapSessionStatus,
    EventType,
    OwnerOverlayScope,
    OwnerProfile,
    OwnerProfileOverlay,
    Project,
    ProjectBinding,
    ProjectBindingType,
    ProjectSelectorState,
    RuntimeControlContext,
    SessionContextState,
    Workspace,
)
from octoagent.core.models.message import NormalizedMessage
from octoagent.core.store import create_store_group
from octoagent.gateway.services.agent_context import (
    build_ambient_runtime_facts,
    build_scope_aware_session_id,
)
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.gateway.services.task_service import TaskService
from octoagent.memory import (
    MemoryBackendState,
    MemoryBackendStatus,
    MemoryLayer,
    MemoryPartition,
    MemoryRecallHit,
    MemoryRecallHookTrace,
    MemoryRecallPostFilterMode,
    MemoryRecallRerankMode,
    MemoryRecallResult,
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

    assert len(memory_calls) == 1
    assert set(memory_calls[0]["scope_ids"]) == {
        "chat:web:thread-alpha",
        "memory/project-alpha",
    }
    assert memory_calls[0]["hook_options"]["post_filter_mode"] == "keyword_overlap"
    assert memory_calls[0]["hook_options"]["rerank_mode"] == "heuristic"

    prompt = llm_service.calls[0]["prompt_or_messages"]
    assert isinstance(prompt, list)
    joined = "\n".join(str(item.get("content", "")) for item in prompt)
    assert "你负责 Alpha 项目的需求连续性与交付推进" in joined
    assert "Alpha Agent" in joined
    assert "AmbientRuntime:" in joined
    assert "timezone: UTC" in joined
    assert "current_weekday_local:" in joined
    assert "之前已经确认 Alpha 的关键约束和当前里程碑" in joined
    assert "长期记忆指出 Alpha 项目必须保持需求上下文连续" in joined
    assert "memory://memory/project-alpha/sor/alpha-constraint" in joined
    assert "请继续推进 Alpha 的方案拆解" in joined

    frames = await store_group.agent_context_store.list_context_frames(task_id=task_id, limit=5)
    assert len(frames) == 1
    frame = frames[0]
    assert frame.agent_profile_id == "agent-profile-alpha"
    assert frame.recent_summary == "之前已经确认 Alpha 的关键约束和当前里程碑。"
    assert frame.memory_hits[0]["record_id"] == "memory-1"
    assert frame.memory_hits[0]["citation"] == "memory://memory/project-alpha/sor/alpha-constraint"
    assert frame.budget["memory_recall"]["backend"] == "sqlite"
    assert frame.budget["memory_recall"]["expanded_queries"] == [
        "请继续推进 Alpha 的方案拆解",
        "Alpha 方案拆解",
    ]
    assert frame.budget["memory_recall"]["hit_count"] == 1
    assert frame.budget["memory_recall"]["delivered_hit_count"] == 1
    assert frame.budget["memory_recall"]["hook_trace"]["post_filter_mode"] == "keyword_overlap"

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
    assert "resolve_request_kind: chat" in request_text
    assert (
        "session_id: surface:web|scope:chat:web:thread-alpha|"
        "project:project-alpha|workspace:workspace-alpha|thread:thread-alpha" in request_text
    )
    assert "AmbientRuntime:" in request_text
    assert "timezone: UTC" in request_text
    assert "之前已经确认 Alpha 的关键约束和当前里程碑" in request_text
    assert "长期记忆指出 Alpha 项目必须保持需求上下文连续" in request_text
    assert final_tokens > history_tokens

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


async def test_task_service_persists_delayed_recall_as_durable_artifacts_and_events(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store_group = await create_store_group(
        str(tmp_path / "f038-delayed-recall.db"),
        str(tmp_path / "artifacts"),
    )
    await _seed_project_context(store_group)

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
    assert frame.budget["final_prompt_tokens"] <= 550
    assert frame.budget["history_tokens"] < frame.budget["final_prompt_tokens"]
    assert "context_budget_trimmed" in frame.degraded_reason
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
