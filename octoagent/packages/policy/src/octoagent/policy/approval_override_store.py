"""ApprovalOverride 持久化 + 内存缓存 — Feature 061 T-011, T-012

对齐 contracts/approval_override.py。
- ApprovalOverrideRepository: SQLite 表 approval_overrides 的 CRUD
- ApprovalOverrideCache: 内存 O(1) 查询缓存
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import aiosqlite
import structlog

from .models import ApprovalOverride

logger = structlog.get_logger(__name__)


# ============================================================
# T-012: 内存缓存
# ============================================================


class ApprovalOverrideCache:
    """ApprovalOverride 内存缓存 — O(1) 查询

    运行时每次工具调用前由 ApprovalOverrideHook 查询。
    key: (agent_runtime_id, tool_name)
    """

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str], bool] = {}

    def has(self, agent_runtime_id: str, tool_name: str) -> bool:
        """检查缓存中是否存在 always 覆盖"""
        return self._cache.get((agent_runtime_id, tool_name), False)

    def set(self, agent_runtime_id: str, tool_name: str) -> None:
        """设置缓存条目"""
        self._cache[(agent_runtime_id, tool_name)] = True

    def remove(self, agent_runtime_id: str, tool_name: str) -> None:
        """移除缓存条目"""
        self._cache.pop((agent_runtime_id, tool_name), None)

    def load_from_records(self, records: list[ApprovalOverride]) -> None:
        """从 Repository 记录批量加载缓存"""
        for record in records:
            self._cache[(record.agent_runtime_id, record.tool_name)] = True

    def clear_agent(self, agent_runtime_id: str) -> None:
        """清除指定 Agent 的所有缓存条目"""
        keys_to_remove = [
            key for key in self._cache if key[0] == agent_runtime_id
        ]
        for key in keys_to_remove:
            del self._cache[key]

    def clear_tool(self, tool_name: str) -> None:
        """清除指定工具的所有缓存条目"""
        keys_to_remove = [
            key for key in self._cache if key[1] == tool_name
        ]
        for key in keys_to_remove:
            del self._cache[key]

    def list_for_agent(self, agent_runtime_id: str) -> list[str]:
        """列出指定 Agent 的所有 always 授权工具名"""
        return [
            tool_name
            for (rid, tool_name) in self._cache
            if rid == agent_runtime_id
        ]

    @property
    def size(self) -> int:
        """缓存条目总数"""
        return len(self._cache)


# ============================================================
# T-011: SQLite 持久化 Repository
# ============================================================


class ApprovalOverrideRepository:
    """ApprovalOverride SQLite 持久化仓库

    基于 approval_overrides 表（T-007 DDL 已就绪）。
    所有写入操作同步更新内存缓存和 Event Store。
    """

    def __init__(
        self,
        conn: aiosqlite.Connection,
        *,
        cache: ApprovalOverrideCache | None = None,
        event_store: Any | None = None,
    ) -> None:
        self._conn = conn
        self._cache = cache
        self._event_store = event_store

    async def save_override(
        self,
        agent_runtime_id: str,
        tool_name: str,
    ) -> ApprovalOverride:
        """保存 always 覆盖记录（INSERT OR REPLACE 幂等语义）"""
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            """
            INSERT OR REPLACE INTO approval_overrides
                (agent_runtime_id, tool_name, decision, created_at)
            VALUES (?, ?, 'always', ?)
            """,
            (agent_runtime_id, tool_name, now),
        )
        await self._conn.commit()

        override = ApprovalOverride(
            agent_runtime_id=agent_runtime_id,
            tool_name=tool_name,
            decision="always",
            created_at=now,
        )

        # 同步更新内存缓存
        if self._cache is not None:
            self._cache.set(agent_runtime_id, tool_name)

        # 记录事件
        await self._emit_event(
            "APPROVAL_OVERRIDE_CREATED",
            agent_runtime_id=agent_runtime_id,
            tool_name=tool_name,
        )

        logger.info(
            "approval_override_saved",
            agent_runtime_id=agent_runtime_id,
            tool_name=tool_name,
        )
        return override

    async def remove_override(
        self,
        agent_runtime_id: str,
        tool_name: str,
    ) -> bool:
        """移除单条 always 覆盖记录"""
        await self._conn.execute(
            """
            DELETE FROM approval_overrides
            WHERE agent_runtime_id = ? AND tool_name = ?
            """,
            (agent_runtime_id, tool_name),
        )
        cursor = await self._conn.execute("SELECT changes()")
        row = await cursor.fetchone()
        changed = int(row[0]) if row else 0
        await self._conn.commit()

        if changed > 0:
            # 同步更新内存缓存
            if self._cache is not None:
                self._cache.remove(agent_runtime_id, tool_name)

            await self._emit_event(
                "APPROVAL_OVERRIDE_REMOVED",
                agent_runtime_id=agent_runtime_id,
                tool_name=tool_name,
            )

            logger.info(
                "approval_override_removed",
                agent_runtime_id=agent_runtime_id,
                tool_name=tool_name,
            )
        return changed > 0

    async def has_override(
        self,
        agent_runtime_id: str,
        tool_name: str,
    ) -> bool:
        """检查是否存在 always 覆盖（SQLite 兜底查询）"""
        cursor = await self._conn.execute(
            """
            SELECT 1 FROM approval_overrides
            WHERE agent_runtime_id = ? AND tool_name = ?
            LIMIT 1
            """,
            (agent_runtime_id, tool_name),
        )
        row = await cursor.fetchone()
        return row is not None

    async def load_overrides(
        self,
        agent_runtime_id: str,
    ) -> list[ApprovalOverride]:
        """加载指定 Agent 实例的所有 always 覆盖"""
        cursor = await self._conn.execute(
            """
            SELECT id, agent_runtime_id, tool_name, decision, created_at
            FROM approval_overrides
            WHERE agent_runtime_id = ?
            ORDER BY created_at ASC
            """,
            (agent_runtime_id,),
        )
        rows = await cursor.fetchall()
        return [self._row_to_override(row) for row in rows]

    async def load_all_overrides(self) -> list[ApprovalOverride]:
        """加载所有 always 覆盖记录"""
        cursor = await self._conn.execute(
            """
            SELECT id, agent_runtime_id, tool_name, decision, created_at
            FROM approval_overrides
            ORDER BY created_at ASC
            """
        )
        rows = await cursor.fetchall()
        return [self._row_to_override(row) for row in rows]

    async def remove_overrides_for_tool(self, tool_name: str) -> int:
        """移除指定工具的所有 always 覆盖"""
        await self._conn.execute(
            "DELETE FROM approval_overrides WHERE tool_name = ?",
            (tool_name,),
        )
        cursor = await self._conn.execute("SELECT changes()")
        row = await cursor.fetchone()
        changed = int(row[0]) if row else 0
        await self._conn.commit()

        if changed > 0 and self._cache is not None:
            self._cache.clear_tool(tool_name)

        logger.info(
            "approval_overrides_removed_for_tool",
            tool_name=tool_name,
            count=changed,
        )
        return changed

    async def remove_overrides_for_agent(self, agent_runtime_id: str) -> int:
        """移除指定 Agent 实例的所有 always 覆盖"""
        await self._conn.execute(
            "DELETE FROM approval_overrides WHERE agent_runtime_id = ?",
            (agent_runtime_id,),
        )
        cursor = await self._conn.execute("SELECT changes()")
        row = await cursor.fetchone()
        changed = int(row[0]) if row else 0
        await self._conn.commit()

        if changed > 0 and self._cache is not None:
            self._cache.clear_agent(agent_runtime_id)

        logger.info(
            "approval_overrides_removed_for_agent",
            agent_runtime_id=agent_runtime_id,
            count=changed,
        )
        return changed

    # ---- 内部辅助 ----

    @staticmethod
    def _row_to_override(row: tuple) -> ApprovalOverride:
        """将数据库行转换为 ApprovalOverride 模型"""
        return ApprovalOverride(
            id=row[0],
            agent_runtime_id=row[1],
            tool_name=row[2],
            decision=row[3],
            created_at=row[4],
        )

    async def _emit_event(
        self,
        event_type: str,
        *,
        agent_runtime_id: str,
        tool_name: str,
    ) -> None:
        """生成 Event Store 事件"""
        if self._event_store is None:
            return
        try:
            from octoagent.core.models.event import Event

            event = Event.create(
                task_id=f"approval-override-{agent_runtime_id}",
                event_type=event_type,
                payload={
                    "agent_runtime_id": agent_runtime_id,
                    "tool_name": tool_name,
                },
            )
            await self._event_store.append_event(event)
        except Exception:
            logger.warning(
                "approval_override_event_emit_failed",
                event_type=event_type,
                agent_runtime_id=agent_runtime_id,
                tool_name=tool_name,
                exc_info=True,
            )
