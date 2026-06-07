"""F104 Phase 5 T5.1：versionable append 并发 / 串行化不变量测试。

F104 方案 B：versionable 写走**独立写连接**（autocommit + 手动 BEGIN IMMEDIATE），
事务边界与主连接彻底隔离；versionable 写之间由 `_write_lock` 串行化（单连接不能并发事务），
版本号由 SAVEPOINT 重试 + UNIQUE 约束保证连续唯一。

覆盖：
- ① `asyncio.gather` N 次 put_artifact(versionable=True) 同 (task_id, logical_file_id)
  → 断言版本号连续 1..N 无重复（`_write_lock` + SAVEPOINT + UNIQUE 三重防线）。
- ② 串行化不变量：versionable 写在 `_write_lock` 下严格串行，版本号连续无空洞。
- ③ mixed-writer 事务边界隔离（versionable 独立写连接 vs 主连接默认写）：双向必过——
  versionable rollback 不卷走主连接已提交默认写；versionable commit 不提前提交主连接
  随后的未提交默认写（FR-004/FR-021）。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio
from octoagent.core.models import Artifact, ArtifactPart, PartType, RequesterInfo, Task
from octoagent.core.store.artifact_store import SqliteArtifactStore
from octoagent.core.store.connection import apply_write_connection_pragmas
from octoagent.core.store.sqlite_init import init_db
from octoagent.core.store.task_store import SqliteTaskStore
from ulid import ULID

TASK_ID = "01JTEST_CONC_0000000000001"


async def _make_task(conn: aiosqlite.Connection, task_id: str = TASK_ID) -> None:
    task_store = SqliteTaskStore(conn)
    now = datetime.now(UTC)
    task = Task(
        task_id=task_id,
        created_at=now,
        updated_at=now,
        title="并发版本测试",
        requester=RequesterInfo(channel="web", sender_id="owner"),
    )
    await task_store.create_task(task)
    await conn.commit()


@pytest_asyncio.fixture
async def store_env(tmp_path: Path):
    # F104 方案 B：versionable 写走独立写连接（autocommit + 手动 BEGIN IMMEDIATE），
    # 复刻生产 create_store_group 双连接拓扑。
    db_path = str(tmp_path / "test.db")
    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
    await init_db(conn)
    versionable_conn = await aiosqlite.connect(db_path, isolation_level=None)
    versionable_conn.row_factory = aiosqlite.Row
    await apply_write_connection_pragmas(versionable_conn)
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    await _make_task(conn)
    artifact_store = SqliteArtifactStore(
        conn, artifacts_dir, versionable_conn=versionable_conn
    )
    yield artifact_store, conn, artifacts_dir, tmp_path
    await conn.close()
    await versionable_conn.close()


def _make_artifact(content: str, *, name: str = "doc") -> Artifact:
    return Artifact(
        artifact_id=str(ULID()),
        task_id=TASK_ID,
        ts=datetime.now(UTC),
        name=name,
        parts=[ArtifactPart(type=PartType.TEXT, content=content)],
    )


async def _version_nos(conn: aiosqlite.Connection, lfid: str) -> list[int]:
    cursor = await conn.execute(
        "SELECT version_no FROM artifact_versions "
        "WHERE task_id = ? AND logical_file_id = ? ORDER BY version_no",
        (TASK_ID, lfid),
    )
    rows = await cursor.fetchall()
    return [int(r[0]) for r in rows]


class TestConcurrentVersionAppend:
    async def test_gather_no_duplicate_version_no(self, store_env):
        """① asyncio.gather N 次同 key versionable append → 版本号连续 1..N 无重复。

        `_write_lock`（asyncio.Lock）串行化 + SAVEPOINT 重试 + UNIQUE 约束三重防线：
        即便 gather 同时发起 N 个协程，最终版本号必为 1..N 连续唯一序列。
        """
        store, conn, _, _ = store_env
        lfid = "progress-note:concurrent_a"
        n = 12

        async def _write(i: int) -> None:
            art = _make_artifact(f"并发内容 {i}")
            await store.put_artifact(
                art, f"并发内容 {i}".encode(), versionable=True, logical_file_id=lfid
            )

        await asyncio.gather(*[_write(i) for i in range(n)])

        nos = await _version_nos(conn, lfid)
        # 连续 1..N 无重复无空洞（核心不变量）
        assert nos == list(range(1, n + 1))
        assert len(set(nos)) == n

    async def test_serialized_writes_no_gaps(self, store_env):
        """② 串行化不变量：高并发 gather 下版本号仍连续无空洞（写锁串行化生效）。

        与 ① 区别：用更高并发 + 不同内容 size（inline / storage_ref 混合）压测，
        断言无论 inline 还是大文件路径，串行化都保持版本号连续。
        """
        store, conn, _, _ = store_env
        lfid = "progress-note:concurrent_b"
        n = 16

        async def _write(i: int) -> None:
            # 偶数 inline 小文件，奇数大文件（storage_ref 分支）交替压测两条路径串行化
            content = (f"内容{i}" if i % 2 == 0 else "大" * 5000) + f"#{i}"
            art = _make_artifact(content)
            await store.put_artifact(
                art, content.encode(), versionable=True, logical_file_id=lfid
            )

        await asyncio.gather(*[_write(i) for i in range(n)])

        nos = await _version_nos(conn, lfid)
        assert nos == list(range(1, n + 1))

    async def test_distinct_logical_files_independent_version_sequences(self, store_env):
        """不同 logical_file_id 并发各自版本序列独立（互不串号）。"""
        store, conn, _, _ = store_env
        lf_a = "progress-note:multi_lf_a"
        lf_b = "progress-note:multi_lf_b"

        async def _write(lfid: str, i: int) -> None:
            art = _make_artifact(f"{lfid}-{i}")
            await store.put_artifact(
                art, f"{lfid}-{i}".encode(), versionable=True, logical_file_id=lfid
            )

        tasks = []
        for i in range(6):
            tasks.append(_write(lf_a, i))
            tasks.append(_write(lf_b, i))
        await asyncio.gather(*tasks)

        assert await _version_nos(conn, lf_a) == [1, 2, 3, 4, 5, 6]
        assert await _version_nos(conn, lf_b) == [1, 2, 3, 4, 5, 6]

    async def test_mixed_writer_isolation(self, store_env):
        """③ mixed-writer 事务边界隔离（F104 方案 B：versionable 独立写连接）。

        versionable 写走独立写连接（autocommit + 手动 BEGIN IMMEDIATE），其 commit/rollback
        仅作用于自身，与主连接 conn 上的默认 versionable=False 写彻底隔离。两方向验证：

        方向① versionable 写**失败 rollback** 不影响主连接已提交的默认写
              （旧共享连接缺陷：versionable rollback 会卷走主连接同事务里的默认写）。
        方向② versionable 写**成功 commit** 不会提前提交主连接上随后才发生的未提交默认写
              （旧共享连接缺陷：versionable commit 会把主连接里堆积的未提交默认写一起 flush）。

        两连接通过 SQLite 写锁串行化（busy_timeout 5s 兜底，无死锁：主连接读 / versionable
        写不互等），且 WAL 跨连接可见性保证主连接读得到 versionable commit 的行。
        """
        store, conn, _, _ = store_env
        versionable_conn = store._versionable_conn
        lfid = "progress-note:mixed_c"

        # ── 方向① versionable rollback 不影响主连接已提交默认写 ──
        # 默认写（versionable=False，主连接）+ 调用方 commit。
        default_art = _make_artifact("默认已提交")
        await store.put_artifact(default_art, "默认已提交".encode())
        await conn.commit()
        # versionable 写人为失败 → 独立写连接 rollback。
        # 用 SAVEPOINT 重试耗尽触发 rollback：monkeypatch 版本 INSERT 持续 IntegrityError。
        orig_execute = versionable_conn.execute

        async def _fail_version_insert(sql, *args, **kwargs):
            if isinstance(sql, str) and "INSERT INTO artifact_versions" in sql:
                raise aiosqlite.IntegrityError("forced fail for rollback test")
            return await orig_execute(sql, *args, **kwargs)

        versionable_conn.execute = _fail_version_insert  # type: ignore[method-assign]
        fail_art = _make_artifact("版本写失败")
        with pytest.raises(aiosqlite.IntegrityError):
            await store.put_artifact(
                fail_art, "版本写失败".encode(), versionable=True, logical_file_id=lfid
            )
        versionable_conn.execute = orig_execute  # type: ignore[method-assign]

        # 断言：主连接已提交默认写仍在（versionable rollback 没卷走它）；版本表 / 失败 artifact 无残留。
        assert await store.get_artifact(default_art.artifact_id) is not None
        assert await store.get_artifact(fail_art.artifact_id) is None
        assert await _version_nos(conn, lfid) == []
        assert not conn.in_transaction
        assert not versionable_conn.in_transaction

        # ── 方向② versionable commit 不提前提交主连接随后的未提交默认写 ──
        # versionable 写成功 commit（独立写连接），WAL 跨连接对主连接可见。
        ver_art = _make_artifact("版本写成功")
        await store.put_artifact(
            ver_art, "版本写成功".encode(), versionable=True, logical_file_id=lfid
        )
        assert await _version_nos(conn, lfid) == [1]
        assert await store.get_artifact(ver_art.artifact_id) is not None

        # 主连接随后开未提交默认写 → 主连接 rollback 可独立撤销
        # （versionable 早先 commit 没有把这条尚不存在的默认写一起提交）。
        pending_art = _make_artifact("默认未提交可撤销")
        await store.put_artifact(pending_art, "默认未提交可撤销".encode())
        await conn.rollback()
        assert await store.get_artifact(pending_art.artifact_id) is None
        # versionable commit 的行不受主连接 rollback 影响（隔离双向成立）。
        assert await store.get_artifact(ver_art.artifact_id) is not None
        assert await _version_nos(conn, lfid) == [1]
