"""SnapshotStore：冻结快照 + Live State 二分法（Hermes Agent 模式）。

Feature 084 Phase 2 (T022-T025) — D2 断层根治。

核心设计：
- session 启动时把 USER.md / MEMORY.md 读入 _system_prompt_snapshot（冻结副本）
  注入 system prompt，保护 prefix cache
- mid-session 写入只走文件 + live_state，下次 session 才刷新冻结副本
- write_through 用 fcntl.flock + tempfile + os.replace 原子写入
- mtime drift 检测：session 结束时比对，漂移则 WARN 日志（不阻断）
- SnapshotRecord 持久化：每次工具调用写入后落 SQLite，TTL 30 天

参考：_references/opensource/hermes-agent/ MemoryStore 实现
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import fcntl
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiosqlite
import structlog
from pydantic import BaseModel, ConfigDict, Field
from ulid import ULID

log = structlog.get_logger(__name__)


class CharLimitExceeded(Exception):
    """append_entry 写入会超过 char_limit 时抛出（FR-7.6 USER.md 字符上限）。"""

    def __init__(self, actual: int, limit: int, file_path: str) -> None:
        super().__init__(
            f"char_limit_exceeded: {file_path} 写入后 {actual} 字符 > 上限 {limit}"
        )
        self.actual = actual
        self.limit = limit
        self.file_path = file_path


# ---------------------------------------------------------------------------
# SnapshotRecord 模型（FR-2.3）
# ---------------------------------------------------------------------------


class SnapshotRecord(BaseModel):
    """工具调用写入结果的摘要快照，对应 snapshot_records 表（T019）。

    LLM 通过 snapshot.read 工具按 tool_call_id 或最近 N 条查询；
    TTL 30 天到期后由后台任务清理（FR-2.6）。
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: str = Field(description="主键 UUID/ULID")
    tool_call_id: str = Field(description="工具调用 ID（UNIQUE）")
    result_summary: str = Field(description="写入摘要，UTF-8 ≤ 500 字符")
    timestamp: str = Field(description="ISO 8601 创建时间（写入时）")
    ttl_days: int = Field(default=30, description="TTL 天数")
    expires_at: str = Field(description="ISO 8601 过期时间")
    created_at: str | None = Field(default=None, description="DB 插入时间（datetime('now')）")


# ---------------------------------------------------------------------------
# SnapshotStore 主类
# ---------------------------------------------------------------------------


# 单条 result_summary 上限（与 FR-2.3 描述一致）
RESULT_SUMMARY_MAX_LEN = 500


class SnapshotStore:
    """冻结快照 + live state 双层存储（Hermes 模式）。

    职责拆分：
    - _system_prompt_snapshot: session 启动后冻结，整个 session 保持不变
      （注入 system prompt 路径），保护 prefix cache（SC-011）
    - _live_state: 随每次写入更新（user_profile.read 路径读这里）
    - _file_mtimes: session 启动时记录，结束时 diff 检测漂移（FR-2.5 / R1）
    - _locks: per-file asyncio.Lock，防同 session 内 read-modify-write 竞态

    write_through 方法实现 atomic write：
    - fcntl.flock(LOCK_EX) 跨进程互斥（macOS / Linux 标准库）
    - tempfile.mkstemp + os.replace 原子替换（崩溃时不留半文件）
    - 写入成功后调用 update_live_state 更新 live state（不改冻结副本）
    """

    def __init__(self, conn: aiosqlite.Connection | None = None) -> None:
        """构造 SnapshotStore。

        Args:
            conn: SQLite 连接（用于 SnapshotRecord 持久化）；测试场景可为 None，
                  此时 persist_snapshot_record 等 SQLite 路径降级为 in-memory
        """
        self._system_prompt_snapshot: dict[str, str] = {}
        self._live_state: dict[str, str] = {}
        self._file_mtimes: dict[Path, float] = {}
        self._locks: dict[Path, asyncio.Lock] = {}
        self._conn = conn
        self._session_id: str = ""

    # ----- T022: 内存冻结 + live state -----

    async def load_snapshot(self, session_id: str, files: dict[str, Path]) -> None:
        """读取目标文件并冻结快照（session 启动时调用）。

        Args:
            session_id: 当前会话 ID（结束时检查 drift 用）
            files: 文件键 → 路径映射，例如
                {"USER.md": Path("~/.octoagent/behavior/system/USER.md"),
                 "MEMORY.md": Path("~/.octoagent/behavior/system/MEMORY.md")}

        语义：
        - 文件存在：读 utf-8 内容，放入 snapshot + live_state，记录 mtime
        - 文件不存在：键映射为空字符串，mtime 记录为 0.0（占位避免后续 drift FP）
        - 异常（解码失败等）：写 WARN 日志，键映射为空字符串
        """
        self._session_id = session_id
        for key, path in files.items():
            try:
                if path.exists():
                    content = path.read_text(encoding="utf-8")
                    mtime = path.stat().st_mtime
                else:
                    content = ""
                    mtime = 0.0
            except Exception as exc:
                log.warning(
                    "snapshot_load_failed",
                    file_key=key,
                    file_path=str(path),
                    error=str(exc),
                )
                content = ""
                mtime = 0.0
            self._system_prompt_snapshot[key] = content
            self._live_state[key] = content
            self._file_mtimes[path] = mtime
        log.info(
            "snapshot_loaded",
            session_id=session_id,
            files=list(files.keys()),
            sizes={k: len(v) for k, v in self._system_prompt_snapshot.items()},
        )

    def format_for_system_prompt(self) -> dict[str, str]:
        """返回冻结副本（始终不变，供 system prompt 注入）。

        Returns:
            字典副本，key 是文件名（如 "USER.md"），value 是冻结时的全文内容
        """
        return dict(self._system_prompt_snapshot)

    def get_live_state(self, key: str) -> str | None:
        """读取 live state 当前内容（user_profile.read 路径）。

        Args:
            key: 文件键，如 "USER.md"

        Returns:
            当前 live state 内容；不存在则 None
        """
        return self._live_state.get(key)

    def update_live_state(self, key: str, content: str) -> None:
        """更新 live state（不改冻结副本）。

        典型场景：write_through 成功后内部调用，或外部代码同步内存状态。
        """
        self._live_state[key] = content

    # ----- T023: atomic write + fcntl.flock -----

    def _get_lock(self, file_path: Path) -> asyncio.Lock:
        """获取/创建 per-file asyncio.Lock（防同 session 内并发写）。"""
        lock = self._locks.get(file_path)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[file_path] = lock
        return lock

    async def write_through(
        self,
        file_path: Path,
        new_content: str,
        live_state_key: str | None = None,
    ) -> None:
        """原子写入文件 + 同步 live state（FR-2.2）。

        步骤：
        1. asyncio.Lock 防同 session 内并发写
        2. fcntl.flock(LOCK_EX) 防跨进程并发写（advisory lock；macOS / Linux）
        3. tempfile.mkstemp 在同目录创建临时文件
        4. os.replace 原子替换（崩溃时旧文件保持完整）
        5. 调用 update_live_state（key 默认取文件名）

        Args:
            file_path: 目标文件绝对路径
            new_content: 新内容（UTF-8）
            live_state_key: live state 的 key（默认 file_path.name）

        Raises:
            OSError: 写入失败（盘满、权限等）；异常时 flock 自动释放，
                    临时文件被清理（finally 保证），不留孤立 .tmp
        """
        async_lock = self._get_lock(file_path)
        async with async_lock:
            file_path.parent.mkdir(parents=True, exist_ok=True)

            # flock 用一个守护文件（避免对目标文件本身上锁导致 os.replace 复杂化）
            lock_path = file_path.parent / f".{file_path.name}.lock"
            tmp_fd: int | None = None
            tmp_path: str | None = None
            lock_fd: int | None = None
            try:
                # 阻塞式排他锁（短任务可接受；长 IO 可改为 LOCK_EX | LOCK_NB + 重试）
                lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
                fcntl.flock(lock_fd, fcntl.LOCK_EX)

                # 同目录创建临时文件 → 写入 → 原子替换
                tmp_fd, tmp_path = tempfile.mkstemp(
                    suffix=".tmp",
                    prefix=f"{file_path.name}.",
                    dir=str(file_path.parent),
                )
                with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                    f.write(new_content)
                tmp_fd = None  # fdopen 已经接管 close
                os.replace(tmp_path, str(file_path))
                tmp_path = None  # replace 后路径已不存在
            finally:
                # 清理临时文件（写入失败时）
                if tmp_fd is not None:
                    with contextlib.suppress(OSError):
                        os.close(tmp_fd)
                if tmp_path is not None and os.path.exists(tmp_path):
                    with contextlib.suppress(OSError):
                        os.unlink(tmp_path)
                # 释放 flock（lock_fd 关闭时自动释放，但 explicit 更清晰）
                if lock_fd is not None:
                    with contextlib.suppress(OSError):
                        fcntl.flock(lock_fd, fcntl.LOCK_UN)
                        os.close(lock_fd)

            # 写入成功 → 同步 live state + 更新 mtime 记录（避免被自己的写入触发 drift）
            key = live_state_key or file_path.name
            self.update_live_state(key, new_content)
            with contextlib.suppress(OSError):
                self._file_mtimes[file_path] = file_path.stat().st_mtime

    async def append_entry(
        self,
        file_path: Path,
        new_entry: str,
        *,
        entry_separator: str = "\n\n§ ",
        first_entry_prefix: str = "§ ",
        line_terminator: str = "\n",
        char_limit: int | None = None,
        live_state_key: str | None = None,
    ) -> tuple[str, int]:
        """原子 read-modify-write：在同一锁窗口内 read + append + 限额 + write（防 F21 并发回归）。

        旧路径 user_profile.update 把 read / build / write_through 拆开调用，
        write_through 的 lock 只覆盖最后一步；两个并发 add 会先各自 read 同一旧内容，
        随后各自 write 旧内容 + 自己的条目，造成数据丢失。

        本方法把整段 read-modify-write 放进同一 async + flock 临界区。

        Args:
            file_path: 目标文件
            new_entry: 要 append 的新条目（不含分隔符与终止符）
            entry_separator: 已有内容非空时的分隔串
            first_entry_prefix: 内容为空时新条目的前缀
            line_terminator: 末尾换行
            char_limit: 写入后总字符上限，超出则 raise CharLimitExceeded
            live_state_key: live state 的 key（默认 file_path.name）

        Returns:
            (final_content, bytes_written) 元组

        Raises:
            CharLimitExceeded: 写入会超过 char_limit
            OSError: 写入失败
        """
        async_lock = self._get_lock(file_path)
        async with async_lock:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            lock_path = file_path.parent / f".{file_path.name}.lock"
            tmp_fd: int | None = None
            tmp_path: str | None = None
            lock_fd: int | None = None
            try:
                # 1. flock 占据跨进程互斥窗口
                lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
                fcntl.flock(lock_fd, fcntl.LOCK_EX)

                # 2. 在同一窗口内 read（不依赖外部 read 时序）
                if file_path.exists():
                    try:
                        existing = file_path.read_text(encoding="utf-8")
                    except OSError:
                        existing = ""
                else:
                    existing = ""

                # 3. build new_content
                stripped_entry = new_entry.strip()
                if existing.strip():
                    final_content = existing.rstrip("\n") + entry_separator + stripped_entry + line_terminator
                else:
                    final_content = first_entry_prefix + stripped_entry + line_terminator

                # 4. 限额检查（仍在窗口内）
                if char_limit is not None and len(final_content) > char_limit:
                    raise CharLimitExceeded(
                        actual=len(final_content),
                        limit=char_limit,
                        file_path=str(file_path),
                    )

                # 5. atomic write（仍在窗口内）
                tmp_fd, tmp_path = tempfile.mkstemp(
                    suffix=".tmp",
                    prefix=f"{file_path.name}.",
                    dir=str(file_path.parent),
                )
                with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                    f.write(final_content)
                tmp_fd = None
                os.replace(tmp_path, str(file_path))
                tmp_path = None
            finally:
                if tmp_fd is not None:
                    with contextlib.suppress(OSError):
                        os.close(tmp_fd)
                if tmp_path is not None and os.path.exists(tmp_path):
                    with contextlib.suppress(OSError):
                        os.unlink(tmp_path)
                if lock_fd is not None:
                    with contextlib.suppress(OSError):
                        fcntl.flock(lock_fd, fcntl.LOCK_UN)
                        os.close(lock_fd)

            # 6. live state + mtime（持锁结束后 update 是 ok 的，因为本身 in-memory）
            key = live_state_key or file_path.name
            self.update_live_state(key, final_content)
            with contextlib.suppress(OSError):
                self._file_mtimes[file_path] = file_path.stat().st_mtime
            return final_content, len(final_content.encode("utf-8"))

    # ----- T024: mtime drift 检测 -----

    async def check_drift_on_session_end(self) -> list[dict[str, Any]]:
        """检查记录 mtime 与磁盘当前 mtime 是否漂移（FR-2.5）。

        漂移含义：session 启动后某些工具调用绕过 SnapshotStore 直接改了文件，
        或外部进程修改了文件。漂移本身不算错误（手工 vim 修改是合法的），
        但应记录供排查；下一次 session 会自动刷新冻结副本（重启 effect）。

        Returns:
            漂移项列表，元素含 file_path / original_mtime / current_mtime；
            无漂移返回空列表
        """
        drifts: list[dict[str, Any]] = []
        for path, original_mtime in self._file_mtimes.items():
            try:
                current_mtime = path.stat().st_mtime if path.exists() else 0.0
            except OSError:
                current_mtime = 0.0
            if current_mtime != original_mtime:
                drift = {
                    "file_path": str(path),
                    "original_mtime": original_mtime,
                    "current_mtime": current_mtime,
                }
                drifts.append(drift)
                log.warning(
                    "SNAPSHOT_DRIFT_DETECTED",
                    session_id=self._session_id,
                    **drift,
                )
        return drifts

    # ----- T025: SnapshotRecord 持久化 -----

    async def persist_snapshot_record(
        self,
        tool_call_id: str,
        result_summary: str,
        ttl_days: int = 30,
    ) -> SnapshotRecord:
        """写入一条 SnapshotRecord 到 SQLite（FR-2.3）。

        Args:
            tool_call_id: 唯一工具调用 ID（写库时 UNIQUE 约束）
            result_summary: 写入摘要，超 RESULT_SUMMARY_MAX_LEN 时截断 + 添加省略号
            ttl_days: TTL 天数，默认 30

        Returns:
            新建的 SnapshotRecord 模型

        Raises:
            RuntimeError: 当 self._conn 为 None 且无降级路径时
        """
        # 摘要截断（保护 SQLite 与下游 LLM context 大小）
        if len(result_summary) > RESULT_SUMMARY_MAX_LEN:
            result_summary = result_summary[: RESULT_SUMMARY_MAX_LEN - 1] + "…"

        now = datetime.now(timezone.utc)
        expires = now + timedelta(days=max(0, ttl_days))
        record = SnapshotRecord(
            id=str(ULID()),
            tool_call_id=tool_call_id,
            result_summary=result_summary,
            timestamp=now.isoformat(),
            ttl_days=ttl_days,
            expires_at=expires.isoformat(),
        )

        if self._conn is None:
            log.warning(
                "snapshot_record_no_conn",
                tool_call_id=tool_call_id,
                hint="StoreGroup 未注入 SnapshotStore；记录返回但未持久化",
            )
            return record

        await self._conn.execute(
            """
            INSERT INTO snapshot_records
                (id, tool_call_id, result_summary, timestamp, ttl_days, expires_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                record.id,
                record.tool_call_id,
                record.result_summary,
                record.timestamp,
                record.ttl_days,
                record.expires_at,
            ),
        )
        await self._conn.commit()
        return record

    async def get_snapshot_record(self, tool_call_id: str) -> SnapshotRecord | None:
        """按 tool_call_id 查询 SnapshotRecord。"""
        if self._conn is None:
            return None
        async with self._conn.execute(
            """
            SELECT id, tool_call_id, result_summary, timestamp, ttl_days, expires_at, created_at
            FROM snapshot_records WHERE tool_call_id = ?
            """,
            (tool_call_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return SnapshotRecord(
            id=row["id"],
            tool_call_id=row["tool_call_id"],
            result_summary=row["result_summary"],
            timestamp=row["timestamp"],
            ttl_days=row["ttl_days"],
            expires_at=row["expires_at"],
            created_at=row["created_at"],
        )

    async def list_recent_snapshot_records(self, limit: int = 20) -> list[SnapshotRecord]:
        """按 timestamp 倒序返回最近 N 条 SnapshotRecord（snapshot.read 工具用）。"""
        if self._conn is None:
            return []
        async with self._conn.execute(
            """
            SELECT id, tool_call_id, result_summary, timestamp, ttl_days, expires_at, created_at
            FROM snapshot_records ORDER BY timestamp DESC LIMIT ?
            """,
            (max(1, limit),),
        ) as cursor:
            rows = await cursor.fetchall()
        return [
            SnapshotRecord(
                id=row["id"],
                tool_call_id=row["tool_call_id"],
                result_summary=row["result_summary"],
                timestamp=row["timestamp"],
                ttl_days=row["ttl_days"],
                expires_at=row["expires_at"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    async def cleanup_expired_records(self) -> int:
        """删除 expires_at < now 的过期记录（FR-2.6，后台任务调用）。

        Returns:
            删除的行数
        """
        if self._conn is None:
            return 0
        now_iso = datetime.now(timezone.utc).isoformat()
        cursor = await self._conn.execute(
            "DELETE FROM snapshot_records WHERE expires_at < ?",
            (now_iso,),
        )
        await self._conn.commit()
        return cursor.rowcount or 0
