"""Session 级联删除 -- 在 SQLite 事务内原子删除 session 关联的所有数据。"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from . import StoreGroup

logger = structlog.get_logger()

ACTIVE_TASK_STATUSES = frozenset({"RUNNING", "WAITING_INPUT", "WAITING_APPROVAL"})


class SessionDeleteBlockedError(RuntimeError):
    """Session 下存在活跃任务，不允许删除。"""

    def __init__(self, active_task_ids: list[str]) -> None:
        self.active_task_ids = active_task_ids
        super().__init__(
            f"Session 有 {len(active_task_ids)} 个活跃任务: {active_task_ids}"
        )


async def delete_session_cascade(
    stores: StoreGroup,
    session_id: str,
    task_ids: list[str],
    agent_session_ids: list[str],
) -> dict[str, int]:
    """事务内级联删除 session 所有关联数据。

    删除顺序（FK 依赖从叶到根）：
      recall_frames → context_frames → agent_session_turns
      → a2a → works(+pipeline) → side_effect_ledger → checkpoints → task_jobs
      → artifacts → events → tasks → agent_sessions → session_context_states

    Args:
        stores: StoreGroup，共享同一个 conn
        session_id: projected session id
        task_ids: 属于此 session 的所有 task id
        agent_session_ids: 属于此 session 的所有 agent_session id

    Returns:
        各表删除行数的 dict
    """
    conn = stores.conn
    stats: dict[str, int] = {}

    # 事务前收集 artifact 文件引用
    storage_refs = await stores.artifact_store.collect_storage_refs_for_tasks(task_ids)

    try:
        stats["recall_frames"] = (
            await stores.agent_context_store.delete_recall_frames_by_agent_session_ids(
                agent_session_ids
            )
        )
        stats["context_frames"] = (
            await stores.agent_context_store.delete_context_frames_by_session_id(
                session_id
            )
        )
        stats["agent_session_turns"] = (
            await stores.agent_context_store.delete_agent_session_turns_by_session_ids(
                agent_session_ids
            )
        )
        stats["a2a"] = await stores.a2a_store.delete_by_task_ids(task_ids)
        stats["works"] = await stores.work_store.delete_by_task_ids(task_ids)
        stats["side_effect_ledger"] = (
            await stores.side_effect_ledger_store.delete_by_task_ids(task_ids)
        )
        stats["checkpoints"] = (
            await stores.checkpoint_store.delete_checkpoints_by_task_ids(task_ids)
        )
        stats["task_jobs"] = await stores.task_job_store.delete_jobs_by_task_ids(
            task_ids
        )
        stats["artifacts"] = (
            await stores.artifact_store.delete_artifacts_by_task_ids(task_ids)
        )
        stats["events"] = await stores.event_store.delete_events_by_task_ids(task_ids)
        stats["tasks"] = await stores.task_store.delete_tasks(task_ids)
        stats["agent_sessions"] = (
            await stores.agent_context_store.delete_agent_sessions_by_ids(
                agent_session_ids
            )
        )
        await stores.agent_context_store.delete_session_context(session_id)
        stats["session_context"] = 1

        await conn.commit()
    except Exception:
        await conn.rollback()
        raise

    # 事务提交后 best-effort 清理 artifact 文件
    cleaned = 0
    for ref in storage_refs:
        try:
            p = Path(ref)
            if p.exists():
                p.unlink()
                cleaned += 1
        except OSError:
            logger.warning("artifact_file_cleanup_failed", storage_ref=ref)
    stats["files_cleaned"] = cleaned

    logger.info(
        "session_deleted",
        session_id=session_id,
        tasks=len(task_ids),
        agent_sessions=len(agent_session_ids),
        stats=stats,
    )

    return stats
