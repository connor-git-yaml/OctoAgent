"""ArtifactStore SQLite + 文件系统实现 -- 对齐 data-model.md §3

主表 artifacts 为 INSERT-only 元数据存储（小文件 inline / 大文件 storage_ref）。
F104 文件工作台 v0.1 新增 versionable append：仅当 `put_artifact(..., versionable=True,
logical_file_id=...)` 时，在自包含事务内同步写一行 artifact_versions 历史副本
（小文件存内容独立副本、大文件存指针），用于"上一版 vs 当前版"diff。
默认 versionable=False 路径行为零变更（不碰版本表、不开写锁）。
"""

import asyncio
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
        event_store: "SqliteEventStore | None" = None,
    ) -> None:
        self._conn = conn
        self._artifacts_dir = artifacts_dir
        # F104：注入的 event_store 用于 versionable append 失败时 emit durable 事件
        self._event_store = event_store
        # F104：串行化 versionable 写事务（取代 BEGIN IMMEDIATE 的写锁作用），防 mixed-writer 交错
        self._write_lock = asyncio.Lock()

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

    async def _insert_artifact_row(self, artifact: Artifact) -> None:
        """写入 artifacts 主表元数据行（不 commit，由调用方/事务管理）。"""
        parts_json = json.dumps(
            [p.model_dump() for p in artifact.parts],
            ensure_ascii=False,
        )
        await self._conn.execute(
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
        - versionable=False（默认）：现状路径，主表 INSERT 由调用方 commit，**完全不碰版本表**
          （0 regression，FR-004）。
        - versionable=True：自包含事务——`_write_lock` 串行化 + 隐式事务 + SAVEPOINT 重试，
          在同一事务内写主表 + 版本副本并自 commit（FR-021 原子）；`logical_file_id` 必须非空
          （无 name 回退，SD-1）。失败自 rollback 并 emit durable ARTIFACT_VERSION_APPEND_FAILED。
        """
        # 分支 A — versionable=False（默认）：行为零变更，调用方 commit，不碰版本表
        if not versionable:
            self._process_content(artifact, content)
            await self._insert_artifact_row(artifact)
            return

        # 分支 B — versionable=True：自包含事务 + 版本号原子分配
        # 先校验（任何 INSERT 之前）：logical_file_id 必须非空，无 name 回退
        if not logical_file_id:
            raise ValueError(
                "versionable=True 时 logical_file_id 必须非空（无 name 回退，SD-1）"
            )

        from ulid import ULID

        attempt = 0
        async with self._write_lock:
            # [Codex Phase1 high] 自包含事务必须从干净连接状态开始：共享单连接下，若调用方已有
            # 未提交写入，本路径的 commit/rollback 会波及其事务。显式拒绝，要求调用方先 commit。
            if self._conn.in_transaction:
                raise RuntimeError(
                    "versionable=True 要求调用方无未提交事务："
                    "自包含事务的 commit/rollback 不能影响共享连接上的既有写入。"
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
                # 文件写 + 主表/版本 INSERT 全在 try 内：任何失败 rollback + 清理本次独占创建的文件。
                # [Codex Phase1 high#2] versionable 大文件 O_EXCL 原子独占创建（已存在 raise FileExistsError，
                # 不覆盖既有/防 TOCTTOU）；仅 O_EXCL 成功后标记 owns_file，失败清理只删本次独占创建的。
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
                # 主表 INSERT 自动开隐式事务（isolation_level=''，T1.3 实测），不显式 BEGIN
                await self._insert_artifact_row(artifact)
                for attempt in range(MAX_VERSION_RETRY):
                    await self._conn.execute("SAVEPOINT sp_ver")
                    cursor = await self._conn.execute(
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
                        await self._conn.execute(
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
                        await self._conn.execute("RELEASE sp_ver")
                        break
                    except aiosqlite.IntegrityError:
                        # UNIQUE(task_id, logical_file_id, version_no) 冲突 → 撤版本 INSERT、保留主表行重试
                        await self._conn.execute("ROLLBACK TO sp_ver")
                        if attempt == MAX_VERSION_RETRY - 1:
                            raise
                await self._conn.commit()
            except Exception as exc:
                # 失败自 rollback（撤主表+版本，连接不留脏事务）
                await self._conn.rollback()
                # [Codex Phase1 high#2] 仅清理本次 O_EXCL 独占创建的文件（owns_file）——O_EXCL 失败
                # （文件已存在=别的 writer）时 owns_file=False，不误删他人文件。
                if owns_file and written_file_path is not None:
                    try:
                        written_file_path.unlink(missing_ok=True)
                    except OSError:
                        pass
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
        """versionable append 失败的 durable 双轨信号（事务外，rollback 之后调用）。

        ①structlog.warning（store 层 always durable 日志）；
        ②event_store.append_event_committed（独立提交，不被前面的 rollback 吞）。
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
            await self._event_store.append_event_committed(failed_event)
        except Exception as emit_exc:
            # 失败事件 emit 本身失败不再向上传播（structlog 已记录主失败），仅记录降级
            log.warning(
                "artifact_version_append_failed_event_emit_failed",
                task_id=task_id,
                logical_file_id=logical_file_id,
                error_type=type(emit_exc).__name__,
            )

    async def get_artifact(self, artifact_id: str) -> Artifact | None:
        """根据 artifact_id 查询 Artifact 元数据"""
        cursor = await self._conn.execute(
            "SELECT * FROM artifacts WHERE artifact_id = ?",
            (artifact_id,),
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

    async def get_artifact_content(self, artifact_id: str) -> bytes | None:
        """获取 Artifact 内容

        - inline 内容：从 parts.content 返回
        - 文件内容：从 storage_ref 路径读取
        """
        artifact = await self.get_artifact(artifact_id)
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
            f"SELECT storage_ref FROM artifacts WHERE task_id IN ({placeholders}) AND storage_ref IS NOT NULL AND storage_ref != ''",
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
