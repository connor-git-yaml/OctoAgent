"""Feature 033: TaskService 上下文连续性接线测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from octoagent.core.models import (
    AgentProfile,
    AgentProfileScope,
    BootstrapSession,
    BootstrapSessionStatus,
    OwnerOverlayScope,
    OwnerProfile,
    OwnerProfileOverlay,
    Project,
    ProjectBinding,
    ProjectBindingType,
    SessionContextState,
    Workspace,
)
from octoagent.core.models.message import NormalizedMessage
from octoagent.core.store import create_store_group
from octoagent.gateway.services.agent_context import build_scope_aware_session_id
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.gateway.services.task_service import TaskService
from octoagent.memory import MemoryLayer, MemoryPartition, MemorySearchHit, MemoryService
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

    async def fake_search_memory(self, *, scope_id, query=None, policy=None, limit=10):
        memory_calls.append(
            {
                "scope_id": scope_id,
                "query": query,
                "limit": limit,
                "policy": policy.model_dump() if policy is not None else {},
            }
        )
        return [
            MemorySearchHit(
                record_id="memory-1",
                layer=MemoryLayer.SOR,
                scope_id=scope_id,
                partition=MemoryPartition.WORK,
                summary="长期记忆指出 Alpha 项目必须保持需求上下文连续。",
                subject_key="alpha-constraint",
                metadata={"source": "test"},
                created_at=datetime.now(tz=UTC),
            )
        ]

    monkeypatch.setattr(MemoryService, "search_memory", fake_search_memory)

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
    )

    assert len(memory_calls) == 2
    assert {item["scope_id"] for item in memory_calls} == {
        "chat:web:thread-alpha",
        "memory/project-alpha",
    }

    prompt = llm_service.calls[0]["prompt_or_messages"]
    assert isinstance(prompt, list)
    joined = "\n".join(str(item.get("content", "")) for item in prompt)
    assert "你负责 Alpha 项目的需求连续性与交付推进" in joined
    assert "Alpha Agent" in joined
    assert "之前已经确认 Alpha 的关键约束和当前里程碑" in joined
    assert "长期记忆指出 Alpha 项目必须保持需求上下文连续" in joined
    assert "请继续推进 Alpha 的方案拆解" in joined

    frames = await store_group.agent_context_store.list_context_frames(task_id=task_id, limit=5)
    assert len(frames) == 1
    frame = frames[0]
    assert frame.agent_profile_id == "agent-profile-alpha"
    assert frame.recent_summary == "之前已经确认 Alpha 的关键约束和当前里程碑。"
    assert frame.memory_hits[0]["record_id"] == "memory-1"

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
    assert (
        "session_id: surface:web|scope:chat:web:thread-alpha|"
        "project:project-alpha|workspace:workspace-alpha|thread:thread-alpha"
        in request_text
    )
    assert "之前已经确认 Alpha 的关键约束和当前里程碑" in request_text
    assert "长期记忆指出 Alpha 项目必须保持需求上下文连续" in request_text
    assert final_tokens > history_tokens

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

    async def fake_search_memory(self, *, scope_id, query=None, policy=None, limit=10):
        return [
            MemorySearchHit(
                record_id=f"memory-{index}",
                layer=MemoryLayer.SOR,
                scope_id=scope_id,
                partition=MemoryPartition.WORK,
                summary=("记忆摘要。" * 120) + str(index),
                subject_key=f"subject-{index}",
                metadata={"source": "budget-test"},
                created_at=datetime.now(tz=UTC),
            )
            for index in range(4)
        ]

    monkeypatch.setattr(MemoryService, "search_memory", fake_search_memory)

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
