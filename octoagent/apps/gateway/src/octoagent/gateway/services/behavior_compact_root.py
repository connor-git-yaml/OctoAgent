"""F111 — compact root Task+Work 占位的单一事实源（Codex round5 P3 闭环）。

问题：编排服务（``behavior_compaction.py``，import apscheduler 链）与 REST 路由
（刻意不 import 该链）此前各有一条 ensure 路径——路由降级用通用
``ensure_system_audit_task``（thread_id/status 形态与服务不同），一次降级启动期
请求就会把通用形态 root task 永久落盘，后续健康重启的服务 ensure 复用该行不修形态
→ spawn 的 compact 子任务继承错误 parent thread（audit lineage 歪斜）。

修复：把常量 + ensure 收进本模块（**零 apscheduler 依赖**，只碰 core models/store），
服务与路由共用同一创建路径——两个入口天然产出同一形态，无需修复逻辑。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

import structlog
from octoagent.core.models.delegation import DelegationTargetKind, Work, WorkStatus
from octoagent.core.models.enums import TaskStatus
from octoagent.core.models.task import RequesterInfo
from octoagent.core.models.task import Task as TaskModel

if TYPE_CHECKING:
    from octoagent.core.store.task_store import SqliteTaskStore
    from octoagent.core.store.work_store import SqliteWorkStore

logger = structlog.get_logger(__name__)

#: 合成 compact root Task / Work（spawn_child 真父对象 + events FK 占位；长驻单例，
#: 沿用 F127 范式——**自己的** id，不共用 `_memory_consolidation_root`，handoff §1.3）
BEHAVIOR_COMPACT_ROOT_TASK_ID: Final[str] = "_behavior_compact_root"
BEHAVIOR_COMPACT_ROOT_WORK_ID: Final[str] = "_behavior_compact_root_work"

#: root Task 显式 thread_id（子 thread 命名稳定可识别）
BEHAVIOR_COMPACT_ROOT_THREAD_ID: Final[str] = "_behavior_compact"

#: spawn 标识（审计 spawned_by + root requester sender）
BEHAVIOR_COMPACT_SPAWNED_BY: Final[str] = "behavior_compact"


async def ensure_behavior_compact_root(
    task_store: SqliteTaskStore,
    work_store: SqliteWorkStore,
) -> tuple[TaskModel, Work]:
    """ensure 系统 owned 的 compact root Task+Work 对（幂等，服务/路由共用）。

    形态要点：``status=SUCCEEDED``（系统占位避免被业务逻辑捡起）+
    ``channel="system"``（通用系统任务抑制面覆盖，F127 finding-E）+ 显式
    thread_id（子 thread 命名稳定）。F127 handoff 坑 2：spawn_child 必需
    task+work **成对真对象**。
    """
    now = datetime.now(UTC)

    existing_task = await task_store.get_task(BEHAVIOR_COMPACT_ROOT_TASK_ID)
    if existing_task is None:
        root_task = TaskModel(
            task_id=BEHAVIOR_COMPACT_ROOT_TASK_ID,
            created_at=now,
            updated_at=now,
            status=TaskStatus.SUCCEEDED,  # 系统占位，避免被业务逻辑捡起
            title="F111 行为文件精简根任务占位",
            thread_id=BEHAVIOR_COMPACT_ROOT_THREAD_ID,
            scope_id="",
            requester=RequesterInfo(
                channel="system", sender_id=BEHAVIOR_COMPACT_SPAWNED_BY
            ),
        )
        await task_store.create_task(root_task)
    else:
        root_task = existing_task

    existing_work = await work_store.get_work(BEHAVIOR_COMPACT_ROOT_WORK_ID)
    if existing_work is None:
        root_work = Work(
            work_id=BEHAVIOR_COMPACT_ROOT_WORK_ID,
            task_id=BEHAVIOR_COMPACT_ROOT_TASK_ID,
            title="F111 行为文件精简根 Work",
            status=WorkStatus.CREATED,
            target_kind=DelegationTargetKind.SUBAGENT,
            created_at=now,
            updated_at=now,
        )
        await work_store.save_work(root_work)
    else:
        root_work = existing_work

    # 提交事务（FK 引用立即可见，沿用 F102/F127 ensure commit 范式）
    conn = getattr(task_store, "_conn", None)
    if conn is not None and hasattr(conn, "commit"):
        try:
            await conn.commit()
        except Exception:
            logger.exception("behavior_compact_root_commit_failed")

    return root_task, root_work


__all__ = [
    "BEHAVIOR_COMPACT_ROOT_TASK_ID",
    "BEHAVIOR_COMPACT_ROOT_THREAD_ID",
    "BEHAVIOR_COMPACT_ROOT_WORK_ID",
    "BEHAVIOR_COMPACT_SPAWNED_BY",
    "ensure_behavior_compact_root",
]
