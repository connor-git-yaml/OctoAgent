"""ArtifactStore SQLite + 文件系统实现 -- 对齐 data-model.md §3

主表 artifacts 为 INSERT-only 元数据存储（小文件 inline / 大文件 storage_ref）。
F104 文件工作台 v0.1 新增 versionable append：仅当 `put_artifact(..., versionable=True,
logical_file_id=...)` 时，在自包含事务内同步写一行 artifact_versions 历史副本
（小文件存内容独立副本、大文件存指针），用于"上一版 vs 当前版"diff。
默认 versionable=False 路径行为零变更（不碰版本表、不开写锁）。
"""

import asyncio
import contextlib
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import aiosqlite
import structlog

from ..config import ARTIFACT_INLINE_THRESHOLD
from ..models.artifact import Artifact, ArtifactPart
from ..models.artifact_version import (
    ArtifactVersionContent,
    ArtifactVersionMeta,
    LogicalFileSummary,
)
from ..models.enums import ActorType, EventType
from ..models.event import Event
from ..models.payloads import ArtifactVersionAppendFailedPayload

if TYPE_CHECKING:
    from .event_store import SqliteEventStore

log = structlog.get_logger()

# versionable append 时版本号 UNIQUE 冲突的 SAVEPOINT 重试上限
MAX_VERSION_RETRY = 3


def compute_hash_and_size(content: bytes) -> tuple[str, int]:
    """计算 SHA-256 hash 和内容大小

    Args:
        content: 原始内容字节

    Returns:
        (sha256_hex, size_bytes) 元组
    """
    return hashlib.sha256(content).hexdigest(), len(content)


def is_utf8_inline_safe(content: bytes) -> bool:
    """判断内容是否可无损以内联 UTF-8 形式存储。"""

    try:
        decoded = content.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return decoded.encode("utf-8") == content


class SqliteArtifactStore:
    """ArtifactStore 的 SQLite + 文件系统实现"""

    def __init__(
        self,
        conn: aiosqlite.Connection,
        artifacts_dir: Path,
        *,
        versionable_conn: aiosqlite.Connection | None = None,
        event_store: "SqliteEventStore | None" = None,
        write_lock: asyncio.Lock | None = None,
    ) -> None:
        self._conn = conn
        self._artifacts_dir = artifacts_dir
        # F104：versionable=True 写专用独立写连接（autocommit + 手动 BEGIN IMMEDIATE）。
        # 事务边界与主连接 _conn 彻底隔离——versionable 写的 commit/rollback 不会卷入
        # 主连接上并发的默认 versionable=False 写（FR-004/FR-021）。
        # 为空时（未注入独立连接）退化到主连接（兼容直接构造 store 的旧测试，
        # 但此时 mixed-writer 隔离不成立——生产/StoreGroup 路径必注入独立连接）。
        self._versionable_conn = versionable_conn if versionable_conn is not None else conn
        # F104 修复 3：记录 versionable 写是否真正隔离（独立物理连接 != 主连接）。
        # 未注入独立连接时退化到主连接，此时 mixed-writer 隔离不成立——put_artifact
        # versionable=True 入口将显式 raise，不静默退化到污染主连接事务边界的路径。
        self._versionable_isolated = (
            versionable_conn is not None and versionable_conn is not conn
        )
        # F104：注入的 event_store 用于 versionable append 失败时 emit durable 事件
        self._event_store = event_store
        # F104：串行化 versionable 写事务（aiosqlite 单连接不能并发事务），防同连接交错。
        # F107 FR-W1-2c：StoreGroup 注入共享锁，与 behavior_version_store 共用单一锁——同一
        # versionable_conn 上两把独立锁各 BEGIN IMMEDIATE 会 "transaction within transaction"。
        self._write_lock = write_lock if write_lock is not None else asyncio.Lock()

    def _process_content(
        self, artifact: Artifact, content: bytes | None, *, exclusive: bool = False
    ) -> None:
        """处理 content：计算 hash/size 并按阈值决定 inline 或文件系统存储。

        就地修改 artifact（hash/size/storage_ref/parts）。
        exclusive=True（versionable 路径）：大文件用 O_EXCL 原子独占创建，已存在则 raise
        FileExistsError（防 TOCTTOU + 跨 writer 覆盖既有，Codex Phase1 high#2）；
        exclusive=False（默认路径）：write_bytes 覆盖语义，与历史等价（0 regression）。
        """
        if content is None:
            return
        hash_hex, size = compute_hash_and_size(content)
        artifact.hash = hash_hex
        artifact.size = size

        if size >= ARTIFACT_INLINE_THRESHOLD or not is_utf8_inline_safe(content):
            # 大文件：写入文件系统
            file_path = self._get_artifact_path(artifact.task_id, artifact.artifact_id)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            if exclusive:
                # O_EXCL 原子独占创建：已存在直接 raise FileExistsError（不覆盖既有内容）
                fd = os.open(file_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                try:
                    os.write(fd, content)
                finally:
                    os.close(fd)
            else:
                file_path.write_bytes(content)
            artifact.storage_ref = str(file_path)
            # 更新 parts 中的 uri
            if artifact.parts:
                artifact.parts[0].uri = str(file_path)
                artifact.parts[0].content = None
        else:
            # 小文件：inline 存储在 parts.content
            if artifact.parts:
                artifact.parts[0].content = content.decode("utf-8")
                artifact.parts[0].uri = None

    async def _insert_artifact_row(
        self,
        artifact: Artifact,
        conn: aiosqlite.Connection | None = None,
    ) -> None:
        """写入 artifacts 主表元数据行（不 commit，由调用方/事务管理）。

        conn：默认 None 用主连接 _conn（versionable=False 默认路径，0 regression）；
        versionable=True 路径显式传 versionable_conn，使主表 INSERT 也走独立写连接。
        """
        target_conn = conn if conn is not None else self._conn
        parts_json = json.dumps(
            [p.model_dump() for p in artifact.parts],
            ensure_ascii=False,
        )
        await target_conn.execute(
            """
            INSERT INTO artifacts (artifact_id, task_id, ts, name, description,
                                   parts, storage_ref, size, hash, version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact.artifact_id,
                artifact.task_id,
                artifact.ts.isoformat(),
                artifact.name,
                artifact.description,
                parts_json,
                artifact.storage_ref,
                artifact.size,
                artifact.hash,
                artifact.version,
            ),
        )

    async def put_artifact(
        self,
        artifact: Artifact,
        content: bytes | None = None,
        *,
        versionable: bool = False,
        logical_file_id: str | None = None,
    ) -> None:
        """存储 Artifact（元数据写 SQLite + 大文件写文件系统）。

        如果 content 不为 None 且大小 >= ARTIFACT_INLINE_THRESHOLD，
        或者内容不是可无损 round-trip 的 UTF-8，则写入文件系统并设置 storage_ref。
        其余小文本 inline 存储在 parts.content 中。

        F104：
        - versionable=False（默认）：现状路径，主表 INSERT 走主连接 _conn 由调用方 commit，
          **完全不碰版本表 / 不碰独立写连接**（0 regression，FR-004）。
        - versionable=True：独立写连接 _versionable_conn 上的自包含事务——`_write_lock`
          串行化 + 手动 `BEGIN IMMEDIATE`（autocommit 连接显式拿写锁）+ SAVEPOINT 重试，
          在同一事务内写主表 + 版本副本并自 commit（FR-021 原子）；commit/rollback 边界与
          主连接隔离，不影响主连接上并发的默认写。`logical_file_id` 必须非空（无 name 回退，
          SD-1）。失败自 rollback 并 emit durable ARTIFACT_VERSION_APPEND_FAILED（走主连接
          event_store，不受 versionable_conn rollback 影响）。
        """
        # 分支 A — versionable=False（默认）：行为零变更，调用方 commit，不碰版本表
        if not versionable:
            self._process_content(artifact, content)
            await self._insert_artifact_row(artifact)
            return

        # 分支 B — versionable=True：自包含事务 + 版本号原子分配
        # F104 修复 3：versionable 写必须有独立隔离写连接，否则会污染主连接事务边界
        # （mixed-writer）。未注入独立连接（如 watchdog 直接构造 StoreGroup 不做 versionable
        # 写的退化路径）显式拒绝，不静默退化。生产 create_store_group 始终注入独立连接。
        if not self._versionable_isolated:
            raise RuntimeError(
                "versionable 写需独立隔离连接（StoreGroup 未注入 versionable_conn）"
            )
        # 先校验（任何 INSERT 之前）：logical_file_id 必须非空，无 name 回退
        if not logical_file_id:
            raise ValueError(
                "versionable=True 时 logical_file_id 必须非空（无 name 回退，SD-1）"
            )

        from ulid import ULID

        attempt = 0
        async with self._write_lock:
            # [F104 方案 B] 独立写连接事务必须从干净状态开始：本连接专供 versionable 写，
            # 正常情况不应有脏事务（每次都自 commit/rollback 收尾）。防御性检查。
            if self._versionable_conn.in_transaction:
                raise RuntimeError(
                    "versionable 独立写连接存在未收尾事务："
                    "BEGIN IMMEDIATE 不能在已开事务的连接上重入。"
                )
            # [Codex Phase1 medium] 文件写移入 _write_lock 内（防同 artifact_id 并发污染）；
            # 记录本次新写的大文件路径，失败时仅清理本次新建（不动既有）。
            written_file_path: Path | None = None
            if content is not None and (
                len(content) >= ARTIFACT_INLINE_THRESHOLD
                or not is_utf8_inline_safe(content)
            ):
                written_file_path = self._get_artifact_path(
                    artifact.task_id, artifact.artifact_id
                )
            owns_file = False
            try:
                # 文件写 + 主表/版本 INSERT 全在 try 内：
                # 任何失败 rollback + 清理本次独占创建的文件。
                # [Codex Phase1 high#2] versionable 大文件 O_EXCL 原子独占创建
                # （已存在 raise FileExistsError，不覆盖既有/防 TOCTTOU）；
                # 仅 O_EXCL 成功后标记 owns_file，失败清理只删本次独占创建的。
                self._process_content(artifact, content, exclusive=True)
                if written_file_path is not None:
                    owns_file = True
                # 版本行存储分支：大文件存指针（content=None），小文件存内容独立副本
                if artifact.storage_ref:
                    ver_storage_kind = "storage_ref"
                    ver_content: str | None = None
                    ver_storage_ref: str | None = artifact.storage_ref
                else:
                    ver_storage_kind = "inline"
                    ver_content = (
                        artifact.parts[0].content
                        if artifact.parts and artifact.parts[0].content is not None
                        else ""
                    )
                    ver_storage_ref = None
                # [F104 方案 B] 独立写连接 autocommit + 手动 BEGIN IMMEDIATE 显式拿 SQLite
                # 写锁（phase-1-recon #4 实测可行）。主表 + 版本 INSERT 全在此事务内，
                # commit/rollback 仅作用于本连接，与主连接彻底隔离（busy_timeout 5s 兜底）。
                await self._versionable_conn.execute("BEGIN IMMEDIATE")
                # 主表 INSERT 走独立写连接（与版本 INSERT 同事务原子提交）
                await self._insert_artifact_row(artifact, conn=self._versionable_conn)
                for attempt in range(MAX_VERSION_RETRY):
                    await self._versionable_conn.execute("SAVEPOINT sp_ver")
                    cursor = await self._versionable_conn.execute(
                        """
                        SELECT COALESCE(MAX(version_no), 0) + 1
                        FROM artifact_versions
                        WHERE task_id = ? AND logical_file_id = ?
                        """,
                        (artifact.task_id, logical_file_id),
                    )
                    row = await cursor.fetchone()
                    next_no = int(row[0]) if row else 1
                    try:
                        await self._versionable_conn.execute(
                            """
                            INSERT INTO artifact_versions (
                                version_id, task_id, logical_file_id, version_no,
                                artifact_id, ts, storage_kind, content, storage_ref,
                                size, hash
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                str(ULID()),
                                artifact.task_id,
                                logical_file_id,
                                next_no,
                                artifact.artifact_id,
                                artifact.ts.isoformat(),
                                ver_storage_kind,
                                ver_content,
                                ver_storage_ref,
                                artifact.size,
                                artifact.hash,
                            ),
                        )
                        await self._versionable_conn.execute("RELEASE sp_ver")
                        break
                    except aiosqlite.IntegrityError:
                        # UNIQUE(task_id, logical_file_id, version_no) 冲突
                        # → 撤版本 INSERT、保留主表行重试
                        await self._versionable_conn.execute("ROLLBACK TO sp_ver")
                        if attempt == MAX_VERSION_RETRY - 1:
                            raise
                await self._versionable_conn.commit()
            except Exception as exc:
                # 失败自 rollback（撤主表+版本，独立写连接不留脏事务，主连接不受影响）
                await self._versionable_conn.rollback()
                # [Codex Phase1 high#2] 仅清理本次 O_EXCL 独占创建的文件（owns_file）——O_EXCL 失败
                # （文件已存在=别的 writer）时 owns_file=False，不误删他人文件。
                if owns_file and written_file_path is not None:
                    with contextlib.suppress(OSError):
                        written_file_path.unlink(missing_ok=True)
                await self._emit_version_append_failed(
                    task_id=artifact.task_id,
                    logical_file_id=logical_file_id,
                    exc=exc,
                    attempt=attempt + 1,
                )
                raise

    async def _emit_version_append_failed(
        self,
        *,
        task_id: str,
        logical_file_id: str,
        exc: Exception,
        attempt: int,
    ) -> None:
        """versionable append 失败的 best-effort 双轨信号（事务外，rollback 之后调用）。

        ①structlog.warning（best-effort local log；经 logging_config 仅挂 StreamHandler，
          输出进程 stderr，无独立文件/审计 sink，可见性取决于环境是否持久化进程流——
          独立文件 sink 超 v0.1 范围）；
        ②event_store.append_event_committed 在 **versionable 独立写连接** 上提交——
          versionable 失败已 rollback，该连接处于干净状态；不走主连接 commit，避免把
          调用方在主连接上未提交的默认 versionable=False 写一并提前提交（调用方后续
          rollback 失效，mixed-writer 转移到失败路径，F104 Codex finding 修复 2）。
          失败事件仍 durable（versionable_conn 独立 commit）+ 不影响主连接事务。
        """
        reason = f"{type(exc).__name__}: {exc}"
        log.warning(
            "artifact_version_append_failed",
            task_id=task_id,
            logical_file_id=logical_file_id,
            reason=reason,
            attempt=attempt,
        )
        if self._event_store is None:
            return
        try:
            from ulid import ULID

            payload = ArtifactVersionAppendFailedPayload(
                task_id=task_id,
                logical_file_id=logical_file_id,
                reason=reason,
                attempt=attempt,
            )
            next_seq = await self._event_store.get_next_task_seq(task_id)
            failed_event = Event(
                event_id=str(ULID()),
                task_id=task_id,
                task_seq=next_seq,
                ts=datetime.now(UTC),
                type=EventType.ARTIFACT_VERSION_APPEND_FAILED,
                actor=ActorType.SYSTEM,
                payload=payload.model_dump(),
                trace_id=f"trace-{task_id}",
            )
            await self._event_store.append_event_committed(
                failed_event, conn=self._versionable_conn
            )
        except Exception as emit_exc:
            # 失败事件 emit 本身失败不再向上传播（structlog 已记录主失败），仅记录降级
            log.warning(
                "artifact_version_append_failed_event_emit_failed",
                task_id=task_id,
                logical_file_id=logical_file_id,
                error_type=type(emit_exc).__name__,
            )

    async def get_artifact(
        self, artifact_id: str, *, task: str | None = None
    ) -> Artifact | None:
        """根据 artifact_id 查询 Artifact 元数据。

        F126 项3：``task=None`` 时按 id 查（内部信任调用，零变更）；``task`` 非 None 时
        SQL 层附加 ``AND task_id = ?`` 物理隔离（纵深防御，跨 task 查询返回 None）。
        """
        if task is None:
            cursor = await self._conn.execute(
                "SELECT * FROM artifacts WHERE artifact_id = ?",
                (artifact_id,),
            )
        else:
            cursor = await self._conn.execute(
                "SELECT * FROM artifacts WHERE artifact_id = ? AND task_id = ?",
                (artifact_id, task),
            )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_artifact(row)

    async def list_artifacts_for_task(self, task_id: str) -> list[Artifact]:
        """查询指定任务的所有 Artifact"""
        cursor = await self._conn.execute(
            "SELECT * FROM artifacts WHERE task_id = ? ORDER BY ts ASC",
            (task_id,),
        )
        rows = await cursor.fetchall()
        return [self._row_to_artifact(row) for row in rows]

    async def get_artifact_content(
        self, artifact_id: str, *, task: str | None = None
    ) -> bytes | None:
        """获取 Artifact 内容

        - inline 内容：从 parts.content 返回
        - 文件内容：从 storage_ref 路径读取

        F126 项3：``task`` 透传给 ``get_artifact`` 做 task 隔离（None = 不过滤）。
        """
        artifact = await self.get_artifact(artifact_id, task=task)
        if artifact is None:
            return None

        # 优先从文件系统读取
        if artifact.storage_ref:
            file_path = self._resolve_storage_ref(artifact.storage_ref)
            if file_path is not None and file_path.exists() and file_path.is_file():
                return file_path.read_bytes()

        # 从 inline content 返回
        for part in artifact.parts:
            if part.content is not None:
                return part.content.encode("utf-8")

        return None

    def _get_artifact_path(self, task_id: str, artifact_id: str) -> Path:
        """获取 Artifact 文件存储路径"""
        return self._artifacts_dir / task_id / artifact_id

    def _resolve_storage_ref(self, storage_ref: str) -> Path | None:
        """解析并校验 storage_ref，拒绝 artifacts_dir 之外的路径。"""
        base_dir = self._artifacts_dir.resolve()
        candidate = Path(storage_ref)
        if not candidate.is_absolute():
            candidate = (base_dir / candidate).resolve()
        else:
            candidate = candidate.resolve()

        try:
            candidate.relative_to(base_dir)
        except ValueError:
            return None

        return candidate

    @staticmethod
    def _row_to_artifact(row: aiosqlite.Row) -> Artifact:
        """将数据库行转换为 Artifact 模型"""
        parts_data = json.loads(row[5]) if row[5] else []
        parts = [ArtifactPart(**p) for p in parts_data]
        return Artifact(
            artifact_id=row[0],
            task_id=row[1],
            ts=datetime.fromisoformat(row[2]),
            name=row[3],
            description=row[4],
            parts=parts,
            storage_ref=row[6],
            size=row[7],
            hash=row[8],
            version=row[9],
        )

    async def collect_storage_refs_for_tasks(self, task_ids: list[str]) -> list[str]:
        """收集指定 tasks 的 artifact storage_ref（事务后文件清理用）。"""
        if not task_ids:
            return []
        placeholders = ",".join("?" * len(task_ids))
        cursor = await self._conn.execute(
            "SELECT storage_ref FROM artifacts "
            f"WHERE task_id IN ({placeholders}) "
            "AND storage_ref IS NOT NULL AND storage_ref != ''",
            tuple(task_ids),
        )
        rows = await cursor.fetchall()
        return [row[0] for row in rows if row[0]]

    async def delete_artifacts_by_task_ids(self, task_ids: list[str]) -> int:
        """按 task_id 批量删除 artifact 元数据（不自动提交）。"""
        if not task_ids:
            return 0
        placeholders = ",".join("?" * len(task_ids))
        await self._conn.execute(
            f"DELETE FROM artifacts WHERE task_id IN ({placeholders})",
            tuple(task_ids),
        )
        cursor = await self._conn.execute("SELECT changes()")
        row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def delete_artifact_versions_by_task_ids(self, task_ids: list[str]) -> int:
        """F104：按 task_id 批量删除 artifact_versions 历史行（不自动提交）。

        append-only 版本表的唯一删除例外（CL-3 级联，数据归属非篡改，FR-005）；
        由 session_delete 在级联事务内、commit 前调用。
        """
        if not task_ids:
            return 0
        placeholders = ",".join("?" * len(task_ids))
        await self._conn.execute(
            f"DELETE FROM artifact_versions WHERE task_id IN ({placeholders})",
            tuple(task_ids),
        )
        cursor = await self._conn.execute("SELECT changes()")
        row = await cursor.fetchone()
        return int(row[0]) if row else 0

    # ------------------------------------------------------------------
    # F104 Phase 2：版本查询方法（FR-006~FR-010）
    # ------------------------------------------------------------------

    async def list_versions(
        self, task_id: str, logical_file_id: str
    ) -> list[ArtifactVersionMeta]:
        """FR-006：按逻辑文件 key 取版本列表（版本号 + 元信息，不含大内容）。

        ORDER BY version_no DESC, ts DESC（ts 兜底排序）。
        """
        cursor = await self._conn.execute(
            """
            SELECT version_no, ts, size, hash, storage_kind
            FROM artifact_versions
            WHERE task_id = ? AND logical_file_id = ?
            ORDER BY version_no DESC, ts DESC
            """,
            (task_id, logical_file_id),
        )
        rows = await cursor.fetchall()
        return [
            ArtifactVersionMeta(
                version_no=int(row[0]),
                ts=row[1],
                size=int(row[2]) if row[2] is not None else 0,
                hash=row[3] or "",
                storage_kind=row[4],
            )
            for row in rows
        ]

    async def get_current_and_previous(
        self,
        task_id: str,
        logical_file_id: str,
        max_content_bytes: int | None = None,
    ) -> tuple[ArtifactVersionContent | None, ArtifactVersionContent | None]:
        """FR-007：取当前版（MAX version_no）与上一版（次大）内容。

        inline → 读 content 列；storage_ref → 读文件，文件不存在/被清理 →
        availability='unavailable' 占位（FR-010），不抛异常。
        < 2 版本 → previous=None。

        max_content_bytes：读前 oversize 拦截阈值（FR-019/SC-005）。两阶段懒加载：
        第一阶段只取元数据（不含 content 列），按 size + storage_kind 判定；
        - inline size>max：oversize=True + content=None，**不执行 content 列查询**；
        - inline size<=max：第二阶段懒加载 content 列；
        - storage_ref：先做文件存在检查（FR-010 优先于 oversize），不存在 → unavailable，
          存在 + size>max → oversize=True 跳过 read_bytes，存在 + size<=max → 读取内容。
        """
        # 第一阶段：只取元数据，不取 content 列（避免超大 inline content 被拉进 Python）。
        cursor = await self._conn.execute(
            """
            SELECT version_id, version_no, storage_kind, storage_ref, size, hash
            FROM artifact_versions
            WHERE task_id = ? AND logical_file_id = ?
            ORDER BY version_no DESC, ts DESC
            LIMIT 2
            """,
            (task_id, logical_file_id),
        )
        rows = await cursor.fetchall()
        if not rows:
            return None, None
        current = await self._meta_row_to_version_content(
            rows[0], max_content_bytes=max_content_bytes
        )
        previous = (
            await self._meta_row_to_version_content(
                rows[1], max_content_bytes=max_content_bytes
            )
            if len(rows) >= 2
            else None
        )
        return current, previous

    async def _load_inline_content(self, version_id: str) -> str | None:
        """第二阶段懒加载：按 version_id 单独查询 inline content 列。"""
        cursor = await self._conn.execute(
            "SELECT content FROM artifact_versions WHERE version_id = ?",
            (version_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return row[0]

    async def _meta_row_to_version_content(
        self,
        row: aiosqlite.Row,
        max_content_bytes: int | None = None,
    ) -> ArtifactVersionContent:
        """将第一阶段元数据行（不含 content）转换为 ArtifactVersionContent，含 FR-010 占位逻辑。

        三态判定优先级（不互相混淆）：
        - storage_ref：**先做文件存在检查**（FR-010），文件不存在/非法 → content=None +
          availability='unavailable' + oversize=False（FR-010 优先于 oversize）；
          文件存在 + size>max → oversize=True 跳过 read_bytes；
          文件存在 + size<=max → read_bytes → decode（UTF-8 成功 available+content /
          非 UTF-8 available+content=None=binary，FR-018）。
        - inline：size>max → oversize=True + content=None（不执行 content 列查询）；
          size<=max → 第二阶段懒加载 content 列，availability='available'。
        """
        version_id = row[0]
        version_no = int(row[1])
        storage_kind = row[2]
        storage_ref = row[3]
        size = int(row[4]) if row[4] is not None else 0
        hash_hex = row[5] or ""

        content: str | None = None
        availability = "available"
        oversize = False

        if storage_kind == "storage_ref":
            # FR-010 优先于 oversize：先做文件存在检查，已被清理的超大文件应报 unavailable。
            file_path = (
                self._resolve_storage_ref(storage_ref) if storage_ref else None
            )
            if file_path is None or not file_path.exists() or not file_path.is_file():
                # 文件不存在/非法 → 不可用占位（FR-010），oversize=False
                content = None
                availability = "unavailable"
            else:
                # 文件存在：read_bytes 前 stat 实际大小，用 max(DB size, 实际 size) 判 oversize。
                # 避免陈旧 DB size（文件写入后被替换/扩大、TOCTOU）按低 size 绕过读前拦截
                # 全量读入超大文件（Codex Phase2 round3 medium，FR-019/SC-005）。
                try:
                    actual_size = file_path.stat().st_size
                except OSError:
                    # stat 失败（文件刚被删/IO 异常）→ 不可用占位（FR-010），不抛异常
                    content = None
                    availability = "unavailable"
                else:
                    effective_size = max(size, actual_size)
                    if (
                        max_content_bytes is not None
                        and effective_size > max_content_bytes
                    ):
                        # 记录或实际大小超阈 → 读前拦截，跳过 read_bytes（FR-019/SC-005）
                        content = None
                        availability = "available"
                        oversize = True
                    else:
                        try:
                            raw = file_path.read_bytes()
                        except OSError:
                            # 文件读 IO 失败 → 内容不可用占位（FR-010），不抛异常
                            content = None
                            availability = "unavailable"
                        else:
                            try:
                                content = raw.decode("utf-8")
                                availability = "available"
                            except UnicodeDecodeError:
                                # 非 UTF-8（二进制大文件）→ 文件可用但无文本，
                                # content=None + availability='available'，
                                # 上层据此预判 binary（FR-018）。
                                content = None
                                availability = "available"
        else:
            # inline：读前 oversize 拦截用 size 元数据短路（FR-019/SC-005）。
            if max_content_bytes is not None and size > max_content_bytes:
                # 超大 → 不执行 content 列查询，content=None + oversize=True
                content = None
                availability = "available"
                oversize = True
            else:
                # 未超阈值 → 第二阶段懒加载 content 列
                content = await self._load_inline_content(version_id)
                availability = "available"

        return ArtifactVersionContent(
            version_no=version_no,
            content=content,
            storage_kind=storage_kind,
            availability=availability,
            oversize=oversize,
            size=size,
            hash=hash_hex,
        )

    async def list_versionable_files_for_task(
        self, task_id: str
    ) -> list[LogicalFileSummary]:
        """FR-008 第二级：列出该 task 下 version count >= 2 的逻辑文件（SD-4 过滤）。

        单版本逻辑文件不返回（SC-006 完全隐藏）。按 logical_file_id 升序稳定排序。
        """
        cursor = await self._conn.execute(
            """
            SELECT logical_file_id, COUNT(*) AS version_count
            FROM artifact_versions
            WHERE task_id = ?
            GROUP BY logical_file_id
            HAVING COUNT(*) >= 2
            ORDER BY logical_file_id ASC
            """,
            (task_id,),
        )
        rows = await cursor.fetchall()
        return [
            LogicalFileSummary(
                logical_file_id=row[0],
                version_count=int(row[1]),
            )
            for row in rows
        ]

    async def list_tasks_with_versionable_files(self) -> list[str]:
        """FR-008 第一级（SD-7 两级导航第一级）：列出有 >=2 版本逻辑文件的 task_id 清单。

        只返回至少含一个 version count >= 2 逻辑文件的 task（DISTINCT）。
        """
        cursor = await self._conn.execute(
            """
            SELECT DISTINCT task_id
            FROM (
                SELECT task_id, logical_file_id
                FROM artifact_versions
                GROUP BY task_id, logical_file_id
                HAVING COUNT(*) >= 2
            )
            ORDER BY task_id ASC
            """
        )
        rows = await cursor.fetchall()
        return [row[0] for row in rows]
