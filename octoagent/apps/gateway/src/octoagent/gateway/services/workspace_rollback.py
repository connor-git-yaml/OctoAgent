"""F107 W2-C：workspace 回滚 service（durable 请求 + 状态机 + 执行）。

durable `workspace_rollback_requests` 表持久化请求 + 状态（#1，Codex C-HIGH-A）——审批 ApprovalGate
仅在内存（`_pending_handles`），进程重启丢失待批回滚；本表 + 启动 rehydrate 保证 restart-survive。

执行（SD-10，仅文件态）：pre-rollback 快照（可撤销撤销 + 失败恢复点）→ `checkout_paths` →
回滚后快照（记此次回滚为新 commit）→ 状态。注入防御复用 WorkspaceGitStore（hash + path 越界）。
审批门由 W2-D route 经 ApprovalGate 异步接入（本 service 提供状态机 + 执行原语）。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from octoagent.core.models.workspace_git import WorkspaceRollbackRequest

from .workspace_git import WorkspaceGitStore, is_valid_commit_hash

log = structlog.get_logger("workspace.rollback")

_TERMINAL_STATUS = frozenset({"rejected", "executed", "failed", "expired"})


class WorkspaceRollbackService:
    """durable 回滚状态机 + 执行（注入主连接 + WorkspaceGitStore）。"""

    def __init__(self, conn: Any, git_store: WorkspaceGitStore) -> None:
        self._conn = conn
        self._git = git_store

    async def create_request(
        self,
        *,
        project_slug: str,
        worktree: Path | str,
        target_commit: str,
        paths: list[str] | None = None,
    ) -> WorkspaceRollbackRequest | None:
        """创建 durable 回滚请求（status=pending）。非法 commit hash → None（注入防御）。"""
        if not is_valid_commit_hash(target_commit):
            return None
        from ulid import ULID

        now = datetime.now(UTC).isoformat()
        req = WorkspaceRollbackRequest(
            request_id=str(ULID()),
            project_slug=project_slug,
            worktree=str(worktree),
            target_commit=target_commit,
            paths=list(paths or []),
            status="pending",
            created_at=now,
            updated_at=now,
        )
        await self._conn.execute(
            "INSERT INTO workspace_rollback_requests (request_id, project_slug, "
            "worktree, target_commit, paths, status, created_at, updated_at, detail) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                req.request_id,
                req.project_slug,
                req.worktree,
                req.target_commit,
                json.dumps(req.paths),
                req.status,
                req.created_at,
                req.updated_at,
                "",
            ),
        )
        await self._conn.commit()
        return req

    async def get_request(self, request_id: str) -> WorkspaceRollbackRequest | None:
        cur = await self._conn.execute(
            "SELECT * FROM workspace_rollback_requests WHERE request_id = ?",
            (request_id,),
        )
        row = await cur.fetchone()
        return self._row_to_req(row) if row else None

    async def list_by_status(self, status: str) -> list[WorkspaceRollbackRequest]:
        cur = await self._conn.execute(
            "SELECT * FROM workspace_rollback_requests WHERE status = ? "
            "ORDER BY created_at",
            (status,),
        )
        return [self._row_to_req(r) for r in await cur.fetchall()]

    async def _set_status(
        self, request_id: str, status: str, detail: str = ""
    ) -> None:
        await self._conn.execute(
            "UPDATE workspace_rollback_requests SET status = ?, updated_at = ?, "
            "detail = ? WHERE request_id = ?",
            (status, datetime.now(UTC).isoformat(), detail, request_id),
        )
        await self._conn.commit()

    async def reject(self, request_id: str) -> bool:
        """审批拒绝 → status=rejected（0 副作用）。"""
        req = await self.get_request(request_id)
        if req is None or req.status in _TERMINAL_STATUS:
            return False
        await self._set_status(request_id, "rejected")
        return True

    async def approve_and_execute(self, request_id: str) -> bool:
        """审批通过 → 执行回滚（pre-snapshot → checkout → 回滚后 snapshot → executed/failed）。

        幂等安全：重入（crash 后 rehydrate 重跑）对同 commit checkout 是 no-op，快照 git 去重。
        """
        req = await self.get_request(request_id)
        if req is None:
            return False
        # CAS 原子占用 pending/approved → executing，防并发 approve 重复执行（Codex W2-MED-1）。
        # rowcount!=1 表示已被占用/终态 → 不重入。
        cur = await self._conn.execute(
            "UPDATE workspace_rollback_requests SET status='executing', updated_at=? "
            "WHERE request_id=? AND status IN ('pending','approved')",
            (datetime.now(UTC).isoformat(), request_id),
        )
        await self._conn.commit()
        if cur.rowcount != 1:
            return False
        worktree = Path(req.worktree)
        short = req.target_commit[:8]
        try:
            # pre-rollback 快照（可撤销此次撤销 + 失败恢复点，Hermes 模式）
            await self._git.snapshot(worktree, f"before rollback to {short}")
            ok = await self._git.checkout_paths(
                worktree, req.target_commit, req.paths or None
            )
            if not ok:
                await self._set_status(request_id, "failed", "checkout 失败")
                return False
            # 回滚后快照（记此次回滚为新 commit；失败仅 log 非致命）
            await self._git.snapshot(worktree, f"rollback to {short}")
            await self._set_status(request_id, "executed")
            return True
        except Exception as exc:
            await self._set_status(
                request_id, "failed", f"{type(exc).__name__}: {exc}"
            )
            log.warning("workspace_rollback_failed", request_id=request_id, error=str(exc))
            return False

    async def rehydrate(self) -> dict[str, list[WorkspaceRollbackRequest]]:
        """启动恢复（#1）：返回需处理的请求。

        - pending：W2-D 据此重建 ApprovalGate approval（等用户决策）。
        - approved：crash 在 approved→executed 之间 → 调用方应重跑 approve_and_execute（幂等）。
        """
        return {
            "pending": await self.list_by_status("pending"),
            "approved": await self.list_by_status("approved"),
        }

    @staticmethod
    def _row_to_req(row: Any) -> WorkspaceRollbackRequest:
        return WorkspaceRollbackRequest(
            request_id=row["request_id"],
            project_slug=row["project_slug"],
            worktree=row["worktree"],
            target_commit=row["target_commit"],
            paths=json.loads(row["paths"] or "[]"),
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            detail=row["detail"],
        )
