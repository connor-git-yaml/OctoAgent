"""F145 统一候选审批中心 — 三源 pending 汇总只读端点（badge 计数）。

GET /api/approval-center/summary — 返回三个后台提议源的 pending 计数 + 合计：
- memory 候选（F084 ``observation_candidates``）
- 记忆合并提议（F127 ``consolidation_candidates``）
- 规则精简提议（F111 ``behavior_compact_candidates``）

只读薄壳（spec §4-1）：不触任何审批语义/状态机——promote/accept/reject 仍走各源
既有路由。前端红点 badge 用本端点替代「拉三个完整 list 端点算数」（其中
``GET /api/behavior/compact/candidates`` 每次响应都为全部 pending 候选做盘读 +
difflib unified diff，badge 轮询不该踩这条热路径）。

降级语义（Constitution #6）：``consolidation_candidates`` 表属 memory 子系统
（``init_memory_db`` 建表，gateway 降级启动可能缺席）——该表缺失时计 0 并 log，
不让整个 badge 500；另两表由 core ``sqlite_init`` 保证恒在，查询失败如实 500。
"""

from __future__ import annotations

import sqlite3

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..deps import get_store_group

log = structlog.get_logger(__name__)

router = APIRouter()


class ApprovalCenterSummary(BaseModel):
    """三源 pending 计数汇总（响应 schema）。"""

    memory_pending: int
    consolidation_pending: int
    behavior_compact_pending: int
    total_pending: int


async def _count_pending(conn, table: str) -> int:
    """COUNT 指定候选表的 pending 行（raw SQL，memory_candidates 路由同款范式）。

    ``table`` 仅接受本模块内两个字面量常量（非用户输入，无注入面）。
    """
    async with conn.execute(
        f"SELECT COUNT(*) AS cnt FROM {table} WHERE status = 'pending'"
    ) as cur:
        row = await cur.fetchone()
        return int(row["cnt"]) if row else 0


@router.get("/api/approval-center/summary", response_model=ApprovalCenterSummary)
async def get_approval_center_summary(
    store_group=Depends(get_store_group),
) -> ApprovalCenterSummary:
    """三源 pending 计数（前端「审批」nav 红点 badge 消费）。"""
    conn = store_group.conn

    try:
        memory_pending = await _count_pending(conn, "observation_candidates")
        # F111 表也在 core sqlite_init，复用既有 store 计数方法
        from octoagent.core.models.behavior_compact import (
            BehaviorCompactCandidateStatus,
        )

        behavior_compact_pending = await store_group.behavior_compact_store.count_candidates(
            status=BehaviorCompactCandidateStatus.PENDING
        )
    except Exception as exc:
        log.error(
            "approval_center_summary_failed",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        raise HTTPException(
            status_code=500, detail=f"审批汇总查询失败: {exc}"
        ) from exc

    # consolidation 表属 memory 子系统（init_memory_db），降级启动可能缺席 → 计 0。
    # Codex final P2 闭环：只有「表不存在」这一种预期缺席才降级——DB 锁/连接坏等
    # 真故障必须如实 500，否则 badge 会把仍待审批的合并提议静默藏成 0。
    try:
        consolidation_pending = await _count_pending(conn, "consolidation_candidates")
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc):
            log.error(
                "approval_center_summary_failed",
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise HTTPException(
                status_code=500, detail=f"审批汇总查询失败: {exc}"
            ) from exc
        log.warning(
            "approval_center_consolidation_count_degraded",
            error=str(exc),
            hint="consolidation_candidates 表不存在（memory 子系统未初始化）；计 0 降级",
        )
        consolidation_pending = 0
    except Exception as exc:
        log.error(
            "approval_center_summary_failed",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        raise HTTPException(
            status_code=500, detail=f"审批汇总查询失败: {exc}"
        ) from exc

    return ApprovalCenterSummary(
        memory_pending=memory_pending,
        consolidation_pending=consolidation_pending,
        behavior_compact_pending=behavior_compact_pending,
        total_pending=memory_pending + consolidation_pending + behavior_compact_pending,
    )
