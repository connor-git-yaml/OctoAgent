"""TaskStore SQLite 实现 -- 对齐 data-model.md §3

tasks 表是 events 的物化视图（projection）。
所有状态更新必须通过事件触发，此处仅提供数据库操作。
"""

import json
from datetime import datetime

import aiosqlite

from ..models.enums import TaskStatus
from ..models.task import RequesterInfo, Task, TaskPointers

#: 系统内部占位 Task 的 requester.channel 值——这些 Task（F102 _daily_routine_audit /
#: F127 记忆巩固 root + 后台巩固 child）由系统后台流程合成，仅作 event_store FK 占位 /
#: spawn parent，不是用户发起的真实任务。用户可见的 task 列表（/api/tasks）与 daily
#: routine 统计必须用 ``exclude_internal=True`` 把它们排除（H1 + UI 普通用户原则：不让用户
#: 在任务列表/日报看到系统后台占位）。child 巩固 Task 继承 parent root 的 channel="system"
#: （capability_pack._launch_child_task 透传 parent.requester.channel），故按 channel 过滤
#: 同时覆盖 root + 全部后代，无需逐个 id 枚举。
SYSTEM_INTERNAL_TASK_CHANNEL: str = "system"


class SqliteTaskStore:
    """TaskStore 的 SQLite 实现"""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn
        # 防御性设置 row_factory：_row_to_task 走 name-based access（row["requester"]
        # 等），若连接由调用方 raw 打开没设过，拿到的就是 raw tuple，字符串索引
        # 会直接踩 TypeError。这里强制 idempotent 设置一次，任何 caller
        # （tests / 旧 fixture / 集成脚本）都不再需要显式记得配置。
        if getattr(self._conn, "row_factory", None) is not aiosqlite.Row:
            self._conn.row_factory = aiosqlite.Row

    async def create_task(self, task: Task) -> None:
        """创建任务记录"""
        await self._conn.execute(
            """
            INSERT INTO tasks (task_id, created_at, updated_at, status, title,
                               thread_id, scope_id, requester, risk_level, pointers,
                               trace_id, parent_task_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task.task_id,
                task.created_at.isoformat(),
                task.updated_at.isoformat(),
                task.status.value,
                task.title,
                task.thread_id,
                task.scope_id,
                task.requester.model_dump_json(),
                task.risk_level.value,
                task.pointers.model_dump_json(),
                task.trace_id,
                task.parent_task_id,
            ),
        )

    async def get_task(self, task_id: str) -> Task | None:
        """根据 task_id 查询任务"""
        cursor = await self._conn.execute(
            "SELECT * FROM tasks WHERE task_id = ?",
            (task_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_task(row)

    async def list_tasks(
        self, status: str | None = None, *, exclude_internal: bool = False
    ) -> list[Task]:
        """查询任务列表，支持按状态筛选，按 created_at 倒序。

        Args:
            status: 可选状态过滤。
            exclude_internal: True 时排除系统内部占位 Task（requester.channel=="system"，
                如 F102 _daily_routine_audit / F127 巩固 root+child）。**默认 False 保持忠实
                accessor**（与 list_works 不过滤同范式）；仅用户可见消费方（/api/tasks）显式开启。
        """
        if status:
            cursor = await self._conn.execute(
                "SELECT * FROM tasks WHERE status = ? ORDER BY created_at DESC",
                (status,),
            )
        else:
            cursor = await self._conn.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC"
            )
        rows = await cursor.fetchall()
        tasks = [self._row_to_task(row) for row in rows]
        if exclude_internal:
            tasks = [
                t
                for t in tasks
                if t.requester.channel != SYSTEM_INTERNAL_TASK_CHANNEL
            ]
        return tasks

    async def list_tasks_by_statuses(self, statuses: list[TaskStatus]) -> list[Task]:
        """按状态集合批量查询任务（Feature 011 spec WARNING 3）

        单次原子 IN (?) 查询，避免多次串行查询导致的竞态窗口。
        保持原 list_tasks 接口向下兼容（此方法为新增，不修改已有方法）。

        Args:
            statuses: 目标状态列表（空列表返回空结果）

        Returns:
            匹配的任务列表，按 created_at 倒序排列
        """
        if not statuses:
            return []

        placeholders = ",".join("?" * len(statuses))
        status_values = [s.value for s in statuses]

        cursor = await self._conn.execute(
            f"SELECT * FROM tasks WHERE status IN ({placeholders}) ORDER BY created_at DESC",
            tuple(status_values),
        )
        rows = await cursor.fetchall()
        return [self._row_to_task(row) for row in rows]

    async def list_child_tasks(self, parent_task_id: str) -> list[Task]:
        """查询指定父任务的所有子任务（Feature 064），按 created_at 正序"""
        cursor = await self._conn.execute(
            "SELECT * FROM tasks WHERE parent_task_id = ? ORDER BY created_at ASC",
            (parent_task_id,),
        )
        rows = await cursor.fetchall()
        return [self._row_to_task(row) for row in rows]

    async def list_tasks_in_time_range(
        self,
        start: datetime,
        end: datetime,
        statuses: list[TaskStatus] | None = None,
        *,
        exclude_internal: bool = False,
    ) -> list[Task]:
        """查询 created_at 在 [start, end) 范围内的 task 列表（F102 FR-T1）。

        D3 决策：F102 范围内新增此 API 解决 daily routine 跨任务时间窗 N+1 问题；
        复用现有 idx_tasks_created_at 索引（store/sqlite_init.py:32）。

        时区语义（spec SD-10）：start / end MUST 是 UTC-aware datetime；
        tasks.created_at 列以 UTC ISO 8601 形式持久化（``Task.created_at.isoformat()``）；
        调用方负责本地时区 → UTC 转换。NaiveDatetime 输入会 raise ValueError。

        Args:
            start: 查询窗起点（UTC-aware datetime, 含）
            end: 查询窗终点（UTC-aware datetime, 不含）
            statuses: 可选状态过滤；None 时返回时间窗内全部 task
            exclude_internal: True 时排除系统内部占位 Task（requester.channel=="system"）。
                **默认 False 保持忠实 accessor**；daily routine 统计显式开启，避免把 F102
                audit 占位 / F127 巩固 root+child（深夜触发可能落在统计窗内）计入用户 worker
                数与日报。

        Returns:
            按 created_at 倒序排列的 Task 列表（与 list_tasks 保持一致）

        Raises:
            ValueError: start 或 end 为 NaiveDatetime（无 tzinfo）时

        Codex Phase B review H2 修复：
            带非 UTC tzinfo（如 +08:00）的 aware datetime 不能直接 isoformat() 与 UTC
            字符串字典序比较——会跨时区错位。修复：内部统一 astimezone(UTC) 后再
            isoformat()。spec SD-10 要求"UTC-aware"，本实现接受任何 tz-aware 但
            归一化到 UTC 比较。
        """
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError(
                "list_tasks_in_time_range requires tz-aware datetimes; "
                "naive datetimes are ambiguous and rejected (spec SD-10)."
            )

        # Codex H2 修复：归一化到 UTC（避免 +08:00 vs +00:00 字符串字典序错位）
        from datetime import UTC as _UTC

        start_iso = start.astimezone(_UTC).isoformat()
        end_iso = end.astimezone(_UTC).isoformat()

        if statuses is None:
            cursor = await self._conn.execute(
                "SELECT * FROM tasks WHERE created_at >= ? AND created_at < ? "
                "ORDER BY created_at DESC",
                (start_iso, end_iso),
            )
        else:
            if not statuses:
                return []
            placeholders = ",".join("?" * len(statuses))
            status_values = [s.value for s in statuses]
            cursor = await self._conn.execute(
                f"SELECT * FROM tasks WHERE created_at >= ? AND created_at < ? "
                f"AND status IN ({placeholders}) ORDER BY created_at DESC",
                (start_iso, end_iso, *status_values),
            )
        rows = await cursor.fetchall()
        tasks = [self._row_to_task(row) for row in rows]
        if exclude_internal:
            tasks = [
                t
                for t in tasks
                if t.requester.channel != SYSTEM_INTERNAL_TASK_CHANNEL
            ]
        return tasks

    async def update_task_status(
        self,
        task_id: str,
        status: str,
        updated_at: str,
        latest_event_id: str,
    ) -> None:
        """更新任务状态（仅通过事件触发调用）"""
        await self._conn.execute(
            """
            UPDATE tasks
            SET status = ?, updated_at = ?,
                pointers = json_set(pointers, '$.latest_event_id', ?)
            WHERE task_id = ?
            """,
            (status, updated_at, latest_event_id, task_id),
        )

    @staticmethod
    def _load_json(value: str | None, default: object) -> object:
        """防御性 JSON 解析：空值或格式错误时返回 default。"""
        if not value:
            return default
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return default

    @classmethod
    def _row_to_task(cls, row: aiosqlite.Row) -> Task:
        """将数据库行转换为 Task 模型（name-based 列访问）。"""
        requester_data = cls._load_json(row["requester"], {})
        pointers_data = cls._load_json(row["pointers"], {})
        return Task(
            task_id=row["task_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            status=row["status"],
            title=row["title"],
            thread_id=row["thread_id"],
            scope_id=row["scope_id"],
            requester=RequesterInfo(**requester_data),
            risk_level=row["risk_level"],
            pointers=TaskPointers(**pointers_data),
            trace_id=row["trace_id"] if "trace_id" in row.keys() else "",
            parent_task_id=row["parent_task_id"] if "parent_task_id" in row.keys() else None,
        )

    async def delete_tasks(self, task_ids: list[str]) -> int:
        """按 task_id 批量删除 tasks（不自动提交）。"""
        if not task_ids:
            return 0
        placeholders = ",".join("?" * len(task_ids))
        await self._conn.execute(
            f"DELETE FROM tasks WHERE task_id IN ({placeholders})",
            tuple(task_ids),
        )
        cursor = await self._conn.execute("SELECT changes()")
        row = await cursor.fetchone()
        return int(row[0]) if row else 0
