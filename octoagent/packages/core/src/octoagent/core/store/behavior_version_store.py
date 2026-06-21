"""F107 文件工作台 v0.2 W1 -- behavior 文件版本历史 store（SQLite，append-only）。

与 F104 SqliteArtifactStore **同隔离模式**：versionable 独立写连接 + **共用写锁** + 手动
BEGIN IMMEDIATE + SAVEPOINT 重试。behavior 文件恒小 md → 恒 inline（无 storage_ref/文件写/oversize）。

**record-after + 首版 baseline（FR-W1-2b）**：写盘成功后记录新内容为新版；首次记录（key 下无任何
版本）且提供 baseline_content（写盘前的旧盘内容）则先记 baseline 再记新内容——让用户有 v1 可 diff，
且 latest 必被记（无"末版丢失"gap）。

**共用写锁（FR-W1-2c / Codex MED-5）**：与 SqliteArtifactStore 共用同一 asyncio.Lock——
同一 versionable_conn 上两把独立锁各自 BEGIN IMMEDIATE 会触发 "transaction within transaction"。
"""

import asyncio
import contextlib
from datetime import UTC, datetime

import aiosqlite
import structlog

from ..models.behavior_version import (
    BehaviorFileKey,
    BehaviorFileSummary,
    BehaviorVersionContent,
    BehaviorVersionMeta,
)
from .artifact_store import compute_hash_and_size

log = structlog.get_logger()

# version_no UNIQUE 冲突的 SAVEPOINT 重试上限（与 artifact_store 同语义）
MAX_VERSION_RETRY = 3


class SqliteBehaviorVersionStore:
    """behavior_versions 的 SQLite 实现（读走主连接，写走独立隔离写连接）。"""

    def __init__(
        self,
        conn: aiosqlite.Connection,
        *,
        versionable_conn: aiosqlite.Connection | None = None,
        write_lock: asyncio.Lock | None = None,
    ) -> None:
        self._conn = conn  # 读路径（主连接）
        # F104 方案 B 同款：versionable 写专用独立写连接（autocommit + 手动 BEGIN IMMEDIATE），
        # 事务边界与主连接彻底隔离。为空时退化主连接（直接构造的旧测试），但此时 record 入口
        # 显式 raise，不静默污染主连接事务边界。
        self._versionable_conn = (
            versionable_conn if versionable_conn is not None else conn
        )
        self._versionable_isolated = (
            versionable_conn is not None and versionable_conn is not conn
        )
        # FR-W1-2c：与 artifact_store 共用单一写锁（StoreGroup 注入），不新建独立锁。
        self._write_lock = write_lock if write_lock is not None else asyncio.Lock()

    # ---- 写路径 ----

    async def record_version(
        self,
        key: BehaviorFileKey,
        content: str,
        *,
        baseline_content: str | None = None,
    ) -> int:
        """记录一个新版本（record-after）。返回新版本号。

        首次记录（key 下无任何版本）且 baseline_content 非空 → 先记 baseline 为 v1、
        再记 content 为 v2，返回 2；否则记 content 为 next_no 并返回。
        失败自 rollback（独立写连接不留脏事务，主连接不受影响）。
        """
        if not self._versionable_isolated:
            raise RuntimeError(
                "behavior 版本写需独立隔离连接（StoreGroup 未注入 versionable_conn）"
            )
        if not key.file_id:
            raise ValueError("file_id 必须非空")

        async with self._write_lock:
            if self._versionable_conn.in_transaction:
                raise RuntimeError(
                    "versionable 独立写连接存在未收尾事务：BEGIN IMMEDIATE 不能重入。"
                )
            try:
                await self._versionable_conn.execute("BEGIN IMMEDIATE")
                cur = await self._versionable_conn.execute(
                    "SELECT COUNT(*) FROM behavior_versions "
                    "WHERE scope=? AND agent_slug=? AND project_slug=? AND file_id=?",
                    (key.scope, key.agent_slug, key.project_slug, key.file_id),
                )
                row = await cur.fetchone()
                has_any = (int(row[0]) > 0) if row else False
                if not has_any and baseline_content is not None:
                    await self._insert_version(key, baseline_content)
                last_no = await self._insert_version(key, content)
                await self._versionable_conn.commit()
                return last_no
            except Exception as exc:
                await self._versionable_conn.rollback()
                log.warning(
                    "behavior_version_record_failed",
                    scope=key.scope,
                    file_id=key.file_id,
                    reason=f"{type(exc).__name__}: {exc}",
                )
                raise

    async def _insert_version(self, key: BehaviorFileKey, content: str) -> int:
        """在已开事务内 INSERT 一行版本（SAVEPOINT 重试 next_no UNIQUE 冲突）。返回版本号。"""
        from ulid import ULID

        hash_hex, size = compute_hash_and_size(content.encode("utf-8"))
        next_no = 1
        for attempt in range(MAX_VERSION_RETRY):
            await self._versionable_conn.execute("SAVEPOINT sp_bver")
            cur = await self._versionable_conn.execute(
                "SELECT COALESCE(MAX(version_no), 0) + 1 FROM behavior_versions "
                "WHERE scope=? AND agent_slug=? AND project_slug=? AND file_id=?",
                (key.scope, key.agent_slug, key.project_slug, key.file_id),
            )
            r = await cur.fetchone()
            next_no = int(r[0]) if r else 1
            try:
                await self._versionable_conn.execute(
                    "INSERT INTO behavior_versions ("
                    "version_id, scope, agent_slug, project_slug, file_id, "
                    "version_no, ts, content, size, hash) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(ULID()),
                        key.scope,
                        key.agent_slug,
                        key.project_slug,
                        key.file_id,
                        next_no,
                        datetime.now(UTC).isoformat(),
                        content,
                        size,
                        hash_hex,
                    ),
                )
                await self._versionable_conn.execute("RELEASE sp_bver")
                return next_no
            except aiosqlite.IntegrityError:
                await self._versionable_conn.execute("ROLLBACK TO sp_bver")
                if attempt == MAX_VERSION_RETRY - 1:
                    raise
        return next_no

    # ---- 读路径 ----

    async def list_versions(self, key: BehaviorFileKey) -> list[BehaviorVersionMeta]:
        """版本元信息列表，ORDER BY version_no DESC, ts DESC（时间线）。"""
        cur = await self._conn.execute(
            "SELECT version_no, ts, size, hash FROM behavior_versions "
            "WHERE scope=? AND agent_slug=? AND project_slug=? AND file_id=? "
            "ORDER BY version_no DESC, ts DESC",
            (key.scope, key.agent_slug, key.project_slug, key.file_id),
        )
        rows = await cur.fetchall()
        return [
            BehaviorVersionMeta(
                version_no=r["version_no"], ts=r["ts"], size=r["size"], hash=r["hash"]
            )
            for r in rows
        ]

    async def _get_version_content(
        self, key: BehaviorFileKey, version_no: int
    ) -> BehaviorVersionContent | None:
        cur = await self._conn.execute(
            "SELECT version_no, content, size, hash FROM behavior_versions "
            "WHERE scope=? AND agent_slug=? AND project_slug=? AND file_id=? AND version_no=?",
            (key.scope, key.agent_slug, key.project_slug, key.file_id, version_no),
        )
        r = await cur.fetchone()
        if r is None:
            return None
        return BehaviorVersionContent(
            version_no=r["version_no"],
            content=r["content"],
            availability="available",
            size=r["size"],
            hash=r["hash"],
        )

    async def get_two_versions(
        self, key: BehaviorFileKey, version_a: int, version_b: int
    ) -> tuple[BehaviorVersionContent | None, BehaviorVersionContent | None]:
        """任意两版本内容（FR-W1-3 / FR-S-2）。缺失版本返回 None。"""
        a = await self._get_version_content(key, version_a)
        b = await self._get_version_content(key, version_b)
        return a, b

    async def get_latest_two(
        self, key: BehaviorFileKey
    ) -> tuple[BehaviorVersionContent | None, BehaviorVersionContent | None]:
        """(当前版, 上一版)；< 2 版本时上一版为 None（首版无对比，复用 F104 DiffView 分支）。"""
        metas = await self.list_versions(key)
        if not metas:
            return None, None
        current = await self._get_version_content(key, metas[0].version_no)
        previous = (
            await self._get_version_content(key, metas[1].version_no)
            if len(metas) >= 2
            else None
        )
        return current, previous

    async def get_version_content(
        self, key: BehaviorFileKey, version_no: int
    ) -> BehaviorVersionContent | None:
        """单版本内容（恢复流读旧版用，W1-C）。"""
        return await self._get_version_content(key, version_no)

    async def list_versioned_behavior_files(
        self, *, scope: str | None = None
    ) -> list[BehaviorFileSummary]:
        """有版本历史（version_count >= 1）的 behavior 文件清单，服务 Agent 中心入口。"""
        params: list[object] = []
        where = ""
        if scope is not None:
            where = "WHERE scope = ?"
            params.append(scope)
        cur = await self._conn.execute(
            "SELECT scope, agent_slug, project_slug, file_id, COUNT(*) AS vc "
            f"FROM behavior_versions {where} "
            "GROUP BY scope, agent_slug, project_slug, file_id "
            "HAVING COUNT(*) >= 1 "
            "ORDER BY file_id ASC",
            tuple(params),
        )
        rows = await cur.fetchall()
        return [
            BehaviorFileSummary(
                scope=r["scope"],
                agent_slug=r["agent_slug"],
                project_slug=r["project_slug"],
                file_id=r["file_id"],
                version_count=int(r["vc"]),
            )
            for r in rows
        ]
