"""Feature 061 接口契约: ApprovalOverride 模型 + ApprovalOverrideRepository 接口

对齐 spec FR-009/010/011/012/013/014。
对齐 CLR-001 决策: 方案 A — SQLite 表 approval_overrides。
对齐 CLR-002: always 授权绑定到 agent_runtime_id，Agent 实例间隔离。

注意: 此文件是接口契约（specification），不是最终实现。
最终代码位于 packages/policy/src/octoagent/policy/。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel, Field


# ============================================================
# 数据模型
# ============================================================


class ApprovalOverride(BaseModel):
    """审批覆盖记录 — 持久化用户 always 授权决策

    每条记录表示用户对某个 Agent 实例的某个工具
    选择了 "always"（永久允许）。

    作用域: Agent 实例级（agent_runtime_id）
    - 不同 Agent 实例之间的 always 授权互相独立 (CLR-002)
    - Worker A 的提权决策不会扩散到 Worker B

    持久化: SQLite 表 approval_overrides (CLR-001)
    - 跨进程重启后仍然有效 (FR-011, SC-005)
    - 进程启动时批量加载到内存缓存

    生命周期:
    - 创建: 用户在审批中选择 always
    - 查询: ApprovalOverrideHook 在每次工具调用前检查
    - 删除: 用户通过 Web UI 管理界面手动撤销（或工具被移除时自动清理）
    """

    id: int | None = Field(
        default=None,
        description="自增主键（SQLite 自动生成，内存中可为 None）",
    )
    agent_runtime_id: str = Field(
        description="Agent 实例 ID（Butler/Worker/Subagent 的 runtime ID）",
    )
    tool_name: str = Field(
        description="工具名称（如 docker.run, terminal.exec）",
    )
    decision: str = Field(
        default="always",
        description="授权决策（当前仅支持 always）",
    )
    created_at: str = Field(
        description="创建时间 ISO 格式（UTC）",
    )

    @classmethod
    def create(cls, agent_runtime_id: str, tool_name: str) -> ApprovalOverride:
        """工厂方法: 创建新的 always 覆盖记录"""
        return cls(
            agent_runtime_id=agent_runtime_id,
            tool_name=tool_name,
            decision="always",
            created_at=datetime.now(UTC).isoformat(),
        )


# ============================================================
# Repository Protocol
# ============================================================


class ApprovalOverrideRepository(Protocol):
    """ApprovalOverride 持久化仓库接口

    实现层使用 SQLite 表 approval_overrides:
    ```sql
    CREATE TABLE IF NOT EXISTS approval_overrides (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        agent_runtime_id TEXT NOT NULL,
        tool_name TEXT NOT NULL,
        decision TEXT NOT NULL DEFAULT 'always',
        created_at TEXT NOT NULL,
        UNIQUE(agent_runtime_id, tool_name)
    );
    ```

    设计要点:
    - save_override: INSERT OR REPLACE 语义（幂等）
    - load_overrides: 进程启动时批量加载到内存缓存
    - has_override: 运行时检查由内存缓存完成，此方法为兜底
    """

    async def save_override(
        self,
        agent_runtime_id: str,
        tool_name: str,
    ) -> ApprovalOverride:
        """保存 always 覆盖记录

        行为:
        - 如果 (agent_runtime_id, tool_name) 已存在，更新 created_at
        - 如果不存在，插入新记录
        - 同时写入 Event Store（APPROVAL_OVERRIDE_CREATED 事件）

        Args:
            agent_runtime_id: Agent 实例 ID
            tool_name: 工具名称

        Returns:
            保存后的 ApprovalOverride 记录
        """
        ...

    async def remove_override(
        self,
        agent_runtime_id: str,
        tool_name: str,
    ) -> bool:
        """移除 always 覆盖记录

        行为:
        - 如果记录存在，删除并返回 True
        - 如果记录不存在，返回 False
        - 同时写入 Event Store（APPROVAL_OVERRIDE_REMOVED 事件）

        Args:
            agent_runtime_id: Agent 实例 ID
            tool_name: 工具名称

        Returns:
            True 如果成功删除，False 如果记录不存在
        """
        ...

    async def has_override(
        self,
        agent_runtime_id: str,
        tool_name: str,
    ) -> bool:
        """检查是否存在 always 覆盖

        注意: 运行时高频调用应使用内存缓存，
        此方法为缓存未命中时的兜底查询。

        Args:
            agent_runtime_id: Agent 实例 ID
            tool_name: 工具名称

        Returns:
            True 如果存在 always 覆盖
        """
        ...

    async def load_overrides(
        self,
        agent_runtime_id: str,
    ) -> list[ApprovalOverride]:
        """加载指定 Agent 实例的所有 always 覆盖

        用途: 进程启动时批量加载到内存缓存。

        Args:
            agent_runtime_id: Agent 实例 ID

        Returns:
            该 Agent 的所有 ApprovalOverride 记录
        """
        ...

    async def load_all_overrides(self) -> list[ApprovalOverride]:
        """加载所有 always 覆盖记录

        用途:
        - 进程启动时全量加载（所有 Agent 实例）
        - Web UI 管理界面展示

        Returns:
            全量 ApprovalOverride 记录列表
        """
        ...

    async def remove_overrides_for_tool(
        self,
        tool_name: str,
    ) -> int:
        """移除指定工具的所有 always 覆盖

        用途: 工具从系统中移除时，清理所有相关的 always 授权。
        （Edge Case: always 授权的工具被移除）

        Args:
            tool_name: 工具名称

        Returns:
            删除的记录数
        """
        ...

    async def remove_overrides_for_agent(
        self,
        agent_runtime_id: str,
    ) -> int:
        """移除指定 Agent 实例的所有 always 覆盖

        用途: Agent 实例被销毁时，清理所有相关的 always 授权。

        Args:
            agent_runtime_id: Agent 实例 ID

        Returns:
            删除的记录数
        """
        ...


# ============================================================
# 内存缓存接口
# ============================================================


class ApprovalOverrideCache:
    """ApprovalOverride 内存缓存

    运行时 O(1) 查询，避免每次工具调用都查 SQLite。

    设计要点:
    - 进程启动时从 Repository 批量加载
    - save_override 时同步更新缓存
    - remove_override 时同步清除缓存
    - key: (agent_runtime_id, tool_name) → True
    """

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str], bool] = {}

    def has(self, agent_runtime_id: str, tool_name: str) -> bool:
        """检查缓存中是否存在 always 覆盖

        Args:
            agent_runtime_id: Agent 实例 ID
            tool_name: 工具名称

        Returns:
            True 如果缓存命中
        """
        return self._cache.get((agent_runtime_id, tool_name), False)

    def set(self, agent_runtime_id: str, tool_name: str) -> None:
        """设置缓存条目"""
        self._cache[(agent_runtime_id, tool_name)] = True

    def remove(self, agent_runtime_id: str, tool_name: str) -> None:
        """移除缓存条目"""
        self._cache.pop((agent_runtime_id, tool_name), None)

    def load_from_records(self, records: list[ApprovalOverride]) -> None:
        """从 Repository 记录批量加载缓存

        Args:
            records: ApprovalOverride 记录列表
        """
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

    @property
    def size(self) -> int:
        """缓存条目总数"""
        return len(self._cache)

    def list_for_agent(self, agent_runtime_id: str) -> list[str]:
        """列出指定 Agent 的所有 always 授权工具名

        Args:
            agent_runtime_id: Agent 实例 ID

        Returns:
            工具名称列表
        """
        return [
            tool_name
            for (rid, tool_name) in self._cache
            if rid == agent_runtime_id
        ]


# ============================================================
# 审批决策扩展（与现有 ApprovalDecision 对齐）
# ============================================================


class ApprovalDecisionExt:
    """审批决策扩展常量

    扩展现有 ApprovalDecision(ALLOW_ONCE/ALLOW_ALWAYS/DENY)，
    增加 Feature 061 所需的语义。

    对齐 FR-010: 三种响应
    - approve (ALLOW_ONCE): 本次允许，下次仍触发审批
    - always (ALLOW_ALWAYS): 本次允许 + 持久化到 approval_overrides
    - deny (DENY): 本次拒绝，不永久封禁

    对齐 FR-012: deny 仅作用于本次调用
    - LLM 后续仍可再次尝试该工具（但会再次触发 ask）
    """

    APPROVE = "allow_once"
    ALWAYS = "allow_always"
    DENY = "deny"


# ============================================================
# DDL（供 migration 参考）
# ============================================================

APPROVAL_OVERRIDES_DDL = """
-- Feature 061: 审批覆盖持久化表
-- 存储用户 "always" 授权决策，绑定到 Agent 实例
CREATE TABLE IF NOT EXISTS approval_overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_runtime_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    decision TEXT NOT NULL DEFAULT 'always',
    created_at TEXT NOT NULL,
    -- Agent 实例 + 工具名唯一约束
    UNIQUE(agent_runtime_id, tool_name)
);

-- 按 Agent 实例查询索引（进程启动时批量加载）
CREATE INDEX IF NOT EXISTS idx_overrides_agent
    ON approval_overrides(agent_runtime_id);

-- 按工具名查询索引（工具移除时批量清理）
CREATE INDEX IF NOT EXISTS idx_overrides_tool
    ON approval_overrides(tool_name);
"""
