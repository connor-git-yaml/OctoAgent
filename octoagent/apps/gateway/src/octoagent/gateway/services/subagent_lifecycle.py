"""Feature 059: Subagent 生命周期管理。

Worker 按需创建/销毁临时 Subagent。
Subagent 共享 Worker 的 Project，用完回收。
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog
from octoagent.core.models import (
    AgentRuntime,
    AgentRuntimeRole,
    AgentRuntimeStatus,
    AgentSession,
    AgentSessionKind,
    AgentSessionStatus,
)
from octoagent.core.store import StoreGroup
from ulid import ULID

log = structlog.get_logger(__name__)


async def spawn_subagent(
    *,
    store_group: StoreGroup,
    parent_worker_runtime_id: str,
    name: str = "",
    persona_summary: str = "",
) -> tuple[AgentRuntime, AgentSession]:
    """为指定 Worker 创建一个临时 Subagent Runtime + Session。

    Subagent 共享 Worker 的 project_id 和 workspace_id。
    """
    # 查找 parent Worker 的 Runtime
    parent_runtime = await store_group.agent_context_store.get_agent_runtime(
        parent_worker_runtime_id
    )
    if parent_runtime is None:
        raise ValueError(f"Parent worker runtime 不存在: {parent_worker_runtime_id}")

    now = datetime.now(tz=UTC)
    runtime_id = f"subagent-{str(ULID())}"
    effective_name = name or f"Subagent of {parent_runtime.name or parent_worker_runtime_id}"

    # 创建轻量 AgentRuntime
    runtime = AgentRuntime(
        agent_runtime_id=runtime_id,
        project_id=parent_runtime.project_id,
        workspace_id=parent_runtime.workspace_id,
        agent_profile_id=parent_runtime.agent_profile_id,
        worker_profile_id=parent_runtime.worker_profile_id,
        role=AgentRuntimeRole.WORKER,  # 复用 worker role，通过 metadata 标记为 subagent
        name=effective_name,
        persona_summary=persona_summary,
        status=AgentRuntimeStatus.ACTIVE,
        metadata={"is_subagent": True, "parent_worker_runtime_id": parent_worker_runtime_id},
        created_at=now,
        updated_at=now,
    )
    await store_group.agent_context_store.save_agent_runtime(runtime)

    # 创建 SUBAGENT_INTERNAL session
    session_id = f"session-subagent-{str(ULID())}"
    session = AgentSession(
        agent_session_id=session_id,
        agent_runtime_id=runtime_id,
        kind=AgentSessionKind.SUBAGENT_INTERNAL,
        status=AgentSessionStatus.ACTIVE,
        project_id=parent_runtime.project_id,
        workspace_id=parent_runtime.workspace_id,
        parent_worker_runtime_id=parent_worker_runtime_id,
        created_at=now,
        updated_at=now,
    )
    await store_group.agent_context_store.save_agent_session(session)

    await store_group.conn.commit()

    log.info(
        "subagent_spawned",
        subagent_runtime_id=runtime_id,
        parent_worker_runtime_id=parent_worker_runtime_id,
        project_id=parent_runtime.project_id,
        session_id=session_id,
    )

    return runtime, session


async def kill_subagent(
    *,
    store_group: StoreGroup,
    subagent_runtime_id: str,
) -> bool:
    """关闭 Subagent 的 Session 并归档 Runtime。

    返回 True 表示成功清理，False 表示 runtime 不存在。
    """
    runtime = await store_group.agent_context_store.get_agent_runtime(subagent_runtime_id)
    if runtime is None:
        return False

    now = datetime.now(tz=UTC)

    # 关闭所有关联的 subagent session
    subagent_sessions = await store_group.agent_context_store.list_subagent_sessions(
        runtime.metadata.get("parent_worker_runtime_id", subagent_runtime_id),
        status=AgentSessionStatus.ACTIVE,
    )
    for session in subagent_sessions:
        if session.agent_runtime_id == subagent_runtime_id:
            await store_group.agent_context_store.save_agent_session(
                session.model_copy(
                    update={
                        "status": AgentSessionStatus.CLOSED,
                        "closed_at": now,
                        "updated_at": now,
                    }
                )
            )

    # 归档 runtime
    await store_group.agent_context_store.save_agent_runtime(
        runtime.model_copy(
            update={
                "status": AgentRuntimeStatus.ARCHIVED,
                "archived_at": now,
                "updated_at": now,
            }
        )
    )

    await store_group.conn.commit()

    log.info(
        "subagent_killed",
        subagent_runtime_id=subagent_runtime_id,
        parent_worker_runtime_id=runtime.metadata.get("parent_worker_runtime_id"),
    )

    return True


async def list_active_subagents(
    *,
    store_group: StoreGroup,
    parent_worker_runtime_id: str,
) -> list[AgentRuntime]:
    """列出指定 Worker 的所有活跃 Subagent。"""
    sessions = await store_group.agent_context_store.list_subagent_sessions(
        parent_worker_runtime_id,
        status=AgentSessionStatus.ACTIVE,
    )
    if not sessions:
        return []

    runtime_ids = {s.agent_runtime_id for s in sessions}
    runtimes: list[AgentRuntime] = []
    for runtime_id in runtime_ids:
        runtime = await store_group.agent_context_store.get_agent_runtime(runtime_id)
        if runtime is not None and runtime.status == AgentRuntimeStatus.ACTIVE:
            runtimes.append(runtime)

    return runtimes
