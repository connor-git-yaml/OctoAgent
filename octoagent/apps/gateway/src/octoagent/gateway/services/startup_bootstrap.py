"""Feature 056: startup 阶段补齐默认 agent profile 和 bootstrap session。

在 Gateway lifespan 中，ensure_default_project() 之后调用。
把原本 lazy init 的 agent_profile 和 bootstrap_session 提前到 startup，
确保 clean install 后前端和 control plane 能立即发现它们。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import structlog
from octoagent.core.behavior_workspace import (
    build_behavior_bootstrap_template_ids,
    materialize_agent_behavior_files,
    resolve_behavior_agent_slug,
)
from ulid import ULID
from octoagent.core.models.agent_context import resolve_permission_preset
from octoagent.core.models import (
    AgentProfile,
    AgentProfileScope,
    AgentRuntime,
    AgentRuntimeRole,
    AgentRuntimeStatus,
    AgentSession,
    AgentSessionKind,
    AgentSessionStatus,
    OwnerProfile,
    Project,
)
from octoagent.core.store import StoreGroup

log = structlog.get_logger(__name__)

DEFAULT_OWNER_PROFILE_ID = "owner-profile-default"


async def ensure_startup_records(
    *,
    store_group: StoreGroup,
    project_root: Path,
) -> None:
    """在 startup 阶段补齐默认 owner profile、agent profile 和 bootstrap session。"""
    project = await store_group.project_store.get_project("project-default")
    if project is None:
        return

    owner_profile = await _ensure_owner_profile(store_group)
    agent_profile = await _ensure_agent_profile(store_group, project)
    # F084 Phase 4 T067/T068：bootstrap_session 状态机已退役，不再创建 bootstrap_session 记录。
    # bootstrap 完成状态通过 owner_profile.bootstrap_completed 和 USER.md 实质填充判断。

    # 回填 default project 的 primary_agent_id（如果尚未设置）
    await _backfill_primary_agent_id(store_group, project)

    # 确保主 Agent 有 AgentRuntime + AgentSession（多 Session 侧边栏的前置条件）
    await ensure_main_runtime_and_session(store_group, project, agent_profile)

    await store_group.conn.commit()

    # agent profile 确定后，用正确的 slug 补齐 agent-private 行为文件
    agent_slug = resolve_behavior_agent_slug(agent_profile)
    materialize_agent_behavior_files(
        project_root,
        agent_slug=agent_slug,
        agent_name=agent_profile.name,
        is_worker_profile=False,
    )

    log.info(
        "startup_records_ensured",
        agent_profile_id=agent_profile.profile_id,
        owner_profile_id=owner_profile.owner_profile_id,
    )


async def _ensure_owner_profile(store_group: StoreGroup) -> OwnerProfile:
    """确保默认 owner profile 存在。

    Feature 082 P0：所有偏好字段（display_name / locale / timezone /
    preferred_address）都显式赋空——表示"未引导"。Bootstrap 完成时由
    ProfileGenerator 回填，Agent system prompt 层 fallback 适当称呼。
    """
    existing = await store_group.agent_context_store.get_owner_profile(DEFAULT_OWNER_PROFILE_ID)
    if existing is not None:
        return existing

    profile = OwnerProfile(
        owner_profile_id=DEFAULT_OWNER_PROFILE_ID,
        display_name="",
        preferred_address="",  # Feature 082 P0：显式空，不再用伪默认 "你"
        locale="",
        timezone="",
        metadata={},
    )
    await store_group.agent_context_store.save_owner_profile(profile)
    return profile


async def _backfill_permission_preset(
    store_group: StoreGroup,
    profile: AgentProfile,
) -> AgentProfile:
    """已有 Agent profile 若缺少 permission_preset，自动补为 full 并持久化。"""
    meta = profile.metadata or {}
    if meta.get("permission_preset"):
        return profile
    updated = profile.model_copy(
        update={
            "metadata": {**meta, "permission_preset": "full"},
            "updated_at": datetime.now(tz=UTC),
        }
    )
    await store_group.agent_context_store.save_agent_profile(updated)
    return updated


async def _ensure_agent_profile(
    store_group: StoreGroup,
    project: Project,
) -> AgentProfile:
    """确保默认 agent profile 存在。"""
    bootstrap_template_ids = build_behavior_bootstrap_template_ids(
        include_agent_private=True,
        include_project_shared=True,
        include_project_agent=False,
    )

    canonical_profile_id = f"agent-profile-{project.project_id}"
    project_default_profile_id = str(project.default_agent_profile_id or "").strip()

    # 如果 project 上已经指定了 default_agent_profile_id，先查它
    if project_default_profile_id:
        existing = await store_group.agent_context_store.get_agent_profile(
            project_default_profile_id
        )
        if existing is not None:
            existing = await _backfill_permission_preset(store_group, existing)
            return existing

    existing = await store_group.agent_context_store.get_agent_profile(canonical_profile_id)
    if existing is not None:
        existing = await _backfill_permission_preset(store_group, existing)
        if project_default_profile_id != existing.profile_id:
            await store_group.project_store.save_project(
                project.model_copy(
                    update={
                        "default_agent_profile_id": existing.profile_id,
                        "updated_at": datetime.now(tz=UTC),
                    }
                )
            )
        return existing

    profile = AgentProfile(
        profile_id=canonical_profile_id,
        scope=AgentProfileScope.PROJECT,
        project_id=project.project_id,
        name=project.name,
        persona_summary="",
        instruction_overlays=[
            "优先遵守 project/profile/bootstrap 约束，再回答当前用户问题。",
            "在上下文不足时显式说明 degraded reason，但继续给出可执行帮助。",
            "遇到缺关键信息的问题时，优先补最关键的 1-2 个条件，不要先给伪完整答案。",
            "遇到今天、最新、天气、官网等依赖实时外部事实的问题时，"
            "先判断是否缺关键参数，并优先通过受治理 worker/tool 路径完成查询。",
        ],
        tool_profile="standard",
        model_alias="main",
        bootstrap_template_ids=bootstrap_template_ids,
        metadata={"permission_preset": "full"},
    )
    await store_group.agent_context_store.save_agent_profile(profile)

    # 回写 project 的 default_agent_profile_id（包含脏旧值自愈）
    if project_default_profile_id != profile.profile_id:
        await store_group.project_store.save_project(
            project.model_copy(
                update={
                    "default_agent_profile_id": profile.profile_id,
                    "updated_at": datetime.now(tz=UTC),
                }
            )
        )

    return profile



async def _backfill_primary_agent_id(
    store_group: StoreGroup,
    project: Project,
) -> None:
    """回填 Project 的 primary_agent_id（主 AgentRuntime）。"""
    if project.primary_agent_id:
        return

    # 查找该 Project 的主 AgentRuntime
    runtimes = await store_group.agent_context_store.list_agent_runtimes(
        role=AgentRuntimeRole.MAIN,
        project_id=project.project_id,
    )
    if not runtimes:
        return

    main_runtime = runtimes[0]
    await store_group.project_store.set_primary_agent(
        project.project_id, main_runtime.agent_runtime_id
    )
    log.info(
        "backfill_primary_agent_id",
        project_id=project.project_id,
        primary_agent_id=main_runtime.agent_runtime_id,
    )


async def ensure_main_runtime_and_session(
    store_group: StoreGroup,
    project: Project,
    agent_profile: AgentProfile,
) -> None:
    """确保主 Agent 在 project-default 上有 AgentRuntime + AgentSession。

    多 Session 侧边栏需要 agent_sessions 行才能展示会话列表。
    """
    # 查找或创建主 Agent Runtime
    runtimes = await store_group.agent_context_store.list_agent_runtimes(
        role=AgentRuntimeRole.MAIN,
        project_id=project.project_id,
    )
    if runtimes:
        main_runtime = runtimes[0]
    else:
        runtime_id = f"runtime-{str(ULID())}"
        main_runtime = AgentRuntime(
            agent_runtime_id=runtime_id,
            project_id=project.project_id,
            agent_profile_id=agent_profile.profile_id,
            role=AgentRuntimeRole.MAIN,
            name=agent_profile.name,
            persona_summary="",
            status=AgentRuntimeStatus.ACTIVE,
            permission_preset=resolve_permission_preset(agent_profile),
        )
        await store_group.agent_context_store.save_agent_runtime(main_runtime)
        # 同步回填 primary_agent_id
        await store_group.project_store.set_primary_agent(
            project.project_id, main_runtime.agent_runtime_id
        )
        log.info("main_runtime_created", runtime_id=runtime_id, project_id=project.project_id)

    # 查找或创建活跃 AgentSession
    existing_session = await store_group.agent_context_store.get_active_session_for_project(
        project.project_id,
        kind=AgentSessionKind.MAIN_BOOTSTRAP,
    )
    if existing_session is None:
        session_id = f"session-{str(ULID())}"
        session = AgentSession(
            agent_session_id=session_id,
            agent_runtime_id=main_runtime.agent_runtime_id,
            kind=AgentSessionKind.MAIN_BOOTSTRAP,
            status=AgentSessionStatus.ACTIVE,
            project_id=project.project_id,
            surface="web",
            thread_id=session_id,
            legacy_session_id=session_id,
        )
        await store_group.agent_context_store.save_agent_session(session)
        log.info("main_session_created", session_id=session_id, project_id=project.project_id)
    else:
        needs_backfill = (
            existing_session.surface not in {"web", "chat"}
            or not str(existing_session.thread_id or "").strip()
            or not str(existing_session.legacy_session_id or "").strip()
        )
        if needs_backfill:
            session_anchor = str(existing_session.thread_id or existing_session.agent_session_id).strip()
            await store_group.agent_context_store.save_agent_session(
                existing_session.model_copy(
                    update={
                        "surface": "web",
                        "thread_id": session_anchor,
                        "legacy_session_id": session_anchor,
                        "updated_at": datetime.now(tz=UTC),
                    }
                )
            )


async def ensure_default_project_agent_profile(
    store_group: StoreGroup,
    project: Project,
) -> AgentProfile | None:
    """解析 default project 的 canonical agent profile，并自愈脏 default_agent_profile_id。"""
    project_default_profile_id = str(project.default_agent_profile_id or "").strip()
    canonical_profile_id = f"agent-profile-{project.project_id}"
    candidate_ids: list[str] = []
    if project_default_profile_id:
        candidate_ids.append(project_default_profile_id)
    if canonical_profile_id not in candidate_ids:
        candidate_ids.append(canonical_profile_id)

    for candidate_id in candidate_ids:
        profile = await store_group.agent_context_store.get_agent_profile(candidate_id)
        if profile is None:
            continue
        if project_default_profile_id != profile.profile_id:
            await store_group.project_store.save_project(
                project.model_copy(
                    update={
                        "default_agent_profile_id": profile.profile_id,
                        "updated_at": datetime.now(tz=UTC),
                    }
                )
            )
        return profile
    return None


