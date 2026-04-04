"""Feature 033: Agent context continuity 集成测试。"""

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
)
from octoagent.core.models.message import NormalizedMessage
from octoagent.core.store import create_store_group
from octoagent.gateway.services.agent_context import build_scope_aware_session_id
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.gateway.services.task_service import TaskService
from octoagent.memory import MemoryLayer, MemoryPartition, MemorySearchHit, MemoryService
from octoagent.provider.models import ModelCallResult, TokenUsage


class RecordingLLMService:
    """记录每次真实主模型输入。"""

    def __init__(self) -> None:
        self.calls: list[list[dict[str, str]]] = []

    async def call(self, prompt_or_messages, model_alias: str | None = None, **kwargs):
        assert isinstance(prompt_or_messages, list)
        self.calls.append(prompt_or_messages)
        return ModelCallResult(
            content="continuity-ok",
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


async def _seed_project(
    store_group,
    *,
    project_id: str,
    slug: str,
    scope_id: str,
    memory_scope_id: str,
    persona_summary: str,
    rolling_summary: str,
    assistant_name: str,
    is_default: bool = False,
    session_id: str | None = None,
    session_thread_id: str | None = None,
) -> None:
    workspace_id = f"workspace-{slug}"
    agent_profile_id = f"agent-profile-{slug}"
    owner_overlay_id = f"owner-overlay-{slug}"
    bootstrap_id = f"bootstrap-{slug}"
    project = Project(
        project_id=project_id,
        slug=slug,
        name=f"{slug.title()} Project",
        description=f"{slug.title()} project context",
        is_default=is_default,
        default_agent_profile_id=agent_profile_id,
    )
    await store_group.project_store.save_project(project)
    await store_group.project_store.create_binding(
        ProjectBinding(
            binding_id=f"binding-scope-{slug}",
            project_id=project_id,
            binding_type=ProjectBindingType.SCOPE,
            binding_key=scope_id,
            binding_value=scope_id,
            source="tests",
            migration_run_id=f"run-{slug}",
        )
    )
    await store_group.project_store.create_binding(
        ProjectBinding(
            binding_id=f"binding-memory-{slug}",
            project_id=project_id,
            binding_type=ProjectBindingType.MEMORY_SCOPE,
            binding_key=memory_scope_id,
            binding_value=memory_scope_id,
            source="tests",
            migration_run_id=f"run-{slug}",
        )
    )
    await store_group.agent_context_store.save_agent_profile(
        AgentProfile(
            profile_id=agent_profile_id,
            scope=AgentProfileScope.PROJECT,
            project_id=project_id,
            name=assistant_name,
            persona_summary=persona_summary,
            instruction_overlays=[f"必须保持 {slug} project 连续性。"],
        )
    )
    await store_group.agent_context_store.save_owner_profile(
        OwnerProfile(
            owner_profile_id="owner-profile-default",
            display_name="Connor",
            working_style="保持事实连续。",
        )
    )
    await store_group.agent_context_store.save_owner_overlay(
        OwnerProfileOverlay(
            owner_overlay_id=owner_overlay_id,
            owner_profile_id="owner-profile-default",
            scope=OwnerOverlayScope.PROJECT,
            project_id=project_id,
            assistant_identity_overrides={"assistant_name": assistant_name},
        )
    )
    await store_group.agent_context_store.save_bootstrap_session(
        BootstrapSession(
            bootstrap_id=bootstrap_id,
            project_id=project_id,
            owner_profile_id="owner-profile-default",
            owner_overlay_id=owner_overlay_id,
            agent_profile_id=agent_profile_id,
            status=BootstrapSessionStatus.COMPLETED,
            current_step="done",
            answers={"assistant_identity": assistant_name},
        )
    )
    await store_group.agent_context_store.save_session_context(
        SessionContextState(
            session_id=session_id or scope_id.split(":")[-1],
            thread_id=session_thread_id or scope_id.split(":")[-1],
            project_id=project_id,
            task_ids=["legacy-task"],
            recent_turn_refs=["legacy-task"],
            rolling_summary=rolling_summary,
            last_context_frame_id=f"context-frame-{slug}-legacy",
        )
    )


async def test_f033_context_survives_restart(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "f033-restart.db"
    artifacts_dir = tmp_path / "artifacts"

    async def fake_search_memory(self, *, scope_id, query=None, policy=None, limit=10):
        return [
            MemorySearchHit(
                record_id=f"memory-{scope_id}",
                layer=MemoryLayer.SOR,
                scope_id=scope_id,
                partition=MemoryPartition.WORK,
                summary="长期记忆：Alpha 的历史约束不能丢。",
                subject_key="alpha-history",
                metadata={},
                created_at=datetime.now(tz=UTC),
            )
        ]

    monkeypatch.setattr(MemoryService, "search_memory", fake_search_memory)

    store_group_1 = await create_store_group(str(db_path), str(artifacts_dir))
    await _seed_project(
        store_group_1,
        project_id="project-alpha",
        slug="alpha",
        scope_id="chat:web:thread-alpha",
        memory_scope_id="memory/project-alpha",
        persona_summary="你负责 Alpha 项目的连续上下文与交付推进。",
        rolling_summary="之前已经确认 Alpha 的关键约束。",
        assistant_name="Alpha Agent",
        is_default=True,
    )
    await store_group_1.conn.commit()

    service_1 = TaskService(store_group_1, SSEHub())
    llm_1 = RecordingLLMService()
    message = NormalizedMessage(
        channel="web",
        thread_id="thread-alpha",
        scope_id="chat:web:thread-alpha",
        text="第一轮：记录 Alpha 的约束",
        idempotency_key="f033-restart-001",
    )
    task_id, created = await service_1.create_task(message)
    assert created is True
    await service_1.process_task_with_llm(
        task_id=task_id,
        user_text=message.text,
        llm_service=llm_1,
    )
    await service_1.append_user_message(task_id, "第二轮：继续沿用上一轮约束")
    await service_1.process_task_with_llm(
        task_id=task_id,
        user_text="第二轮：继续沿用上一轮约束",
        llm_service=llm_1,
    )
    await store_group_1.conn.close()

    store_group_2 = await create_store_group(str(db_path), str(artifacts_dir))
    service_2 = TaskService(store_group_2, SSEHub())
    llm_2 = RecordingLLMService()
    await service_2.append_user_message(task_id, "第三轮：重启后继续，不要丢上下文")
    await service_2.process_task_with_llm(
        task_id=task_id,
        user_text="第三轮：重启后继续，不要丢上下文",
        llm_service=llm_2,
    )

    joined = "\n".join(item.get("content", "") for item in llm_2.calls[0])
    assert "你负责 Alpha 项目的连续上下文与交付推进" in joined
    assert "之前已经确认 Alpha 的关键约束" in joined
    assert "第一轮：记录 Alpha 的约束" in joined
    assert "第二轮：继续沿用上一轮约束" in joined
    assert "长期记忆：Alpha 的历史约束不能丢" in joined

    task = await store_group_2.task_store.get_task(task_id)
    assert task is not None
    session_state = await store_group_2.agent_context_store.get_session_context(
        build_scope_aware_session_id(
            task,
            project_id="project-alpha",
        )
    )
    assert session_state is not None
    assert session_state.rolling_summary
    assert session_state.last_context_frame_id

    await store_group_2.conn.close()


async def test_f033_project_context_does_not_leak_across_projects(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store_group = await create_store_group(
        str(tmp_path / "f033-isolation.db"),
        str(tmp_path / "artifacts"),
    )
    await _seed_project(
        store_group,
        project_id="project-alpha",
        slug="alpha",
        scope_id="chat:web:thread-alpha",
        memory_scope_id="memory/project-alpha",
        persona_summary="你负责 Alpha 项目的需求连续性。",
        rolling_summary="Alpha summary",
        assistant_name="Alpha Agent",
        is_default=True,
        session_id=(
            "surface:web|scope:chat:web:thread-alpha|project:project-alpha|workspace:workspace-alpha|thread:shared-thread"
        ),
        session_thread_id="shared-thread",
    )
    await _seed_project(
        store_group,
        project_id="project-beta",
        slug="beta",
        scope_id="chat:web:thread-beta",
        memory_scope_id="memory/project-beta",
        persona_summary="你负责 Beta 项目的实验推进。",
        rolling_summary="Beta summary",
        assistant_name="Beta Agent",
        session_id=(
            "surface:web|scope:chat:web:thread-beta|project:project-beta|workspace:workspace-beta|thread:shared-thread"
        ),
        session_thread_id="shared-thread",
    )
    await store_group.conn.commit()

    async def fake_search_memory(self, *, scope_id, query=None, policy=None, limit=10):
        label = "Alpha" if "alpha" in scope_id else "Beta"
        return [
            MemorySearchHit(
                record_id=f"memory-{label.lower()}",
                layer=MemoryLayer.SOR,
                scope_id=scope_id,
                partition=MemoryPartition.WORK,
                summary=f"{label} memory only",
                subject_key=f"{label.lower()}-subject",
                metadata={},
                created_at=datetime.now(tz=UTC),
            )
        ]

    monkeypatch.setattr(MemoryService, "search_memory", fake_search_memory)

    service = TaskService(store_group, SSEHub())
    llm = RecordingLLMService()

    task_alpha, created_alpha = await service.create_task(
        NormalizedMessage(
            channel="web",
            thread_id="shared-thread",
            scope_id="chat:web:thread-alpha",
            text="推进 Alpha",
            idempotency_key="f033-isolation-alpha",
        )
    )
    assert created_alpha is True
    await service.process_task_with_llm(task_id=task_alpha, user_text="推进 Alpha", llm_service=llm)

    task_beta, created_beta = await service.create_task(
        NormalizedMessage(
            channel="web",
            thread_id="shared-thread",
            scope_id="chat:web:thread-beta",
            text="推进 Beta",
            idempotency_key="f033-isolation-beta",
        )
    )
    assert created_beta is True
    await service.process_task_with_llm(task_id=task_beta, user_text="推进 Beta", llm_service=llm)

    alpha_prompt = "\n".join(item.get("content", "") for item in llm.calls[0])
    beta_prompt = "\n".join(item.get("content", "") for item in llm.calls[1])

    assert "Alpha Agent" in alpha_prompt
    assert "Alpha summary" in alpha_prompt
    assert "Alpha memory only" in alpha_prompt
    assert "Beta Agent" not in alpha_prompt
    assert "Beta memory only" not in alpha_prompt

    assert "Beta Agent" in beta_prompt
    assert "Beta summary" in beta_prompt
    assert "Beta memory only" in beta_prompt
    assert "Alpha Agent" not in beta_prompt
    assert "Alpha memory only" not in beta_prompt

    task_alpha_model = await store_group.task_store.get_task(task_alpha)
    task_beta_model = await store_group.task_store.get_task(task_beta)
    assert task_alpha_model is not None
    assert task_beta_model is not None
    alpha_state = await store_group.agent_context_store.get_session_context(
        build_scope_aware_session_id(
            task_alpha_model,
            project_id="project-alpha",
        )
    )
    beta_state = await store_group.agent_context_store.get_session_context(
        build_scope_aware_session_id(
            task_beta_model,
            project_id="project-beta",
        )
    )
    assert alpha_state is not None
    assert beta_state is not None
    assert alpha_state.session_id != beta_state.session_id

    await store_group.conn.close()
