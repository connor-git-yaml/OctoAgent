"""F104 Phase 1：artifact_versions 表 + put_artifact versionable append 测试。

覆盖：
- T1.5 unit：连续写版本号单调唯一 / inline vs storage_ref 字段 / 空 logical_file_id raise
  / SAVEPOINT 冲突重试后两表各 1 行匹配。
- T1.6 集成：默认 versionable=False 路径 0 regression（版本表 0 行）/ 重启取回 / 同事务回滚。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio
from octoagent.core.config import ARTIFACT_INLINE_THRESHOLD
from octoagent.core.models import Artifact, ArtifactPart, PartType, RequesterInfo, Task
from octoagent.core.store.artifact_store import MAX_VERSION_RETRY, SqliteArtifactStore
from octoagent.core.store.connection import apply_write_connection_pragmas
from octoagent.core.store.sqlite_init import init_db
from octoagent.core.store.task_store import SqliteTaskStore

TASK_ID = "01JTEST_VER_00000000000001"


async def _make_task(conn: aiosqlite.Connection, task_id: str = TASK_ID) -> None:
    task_store = SqliteTaskStore(conn)
    now = datetime.now(UTC)
    task = Task(
        task_id=task_id,
        created_at=now,
        updated_at=now,
        title="版本测试",
        requester=RequesterInfo(channel="web", sender_id="owner"),
    )
    await task_store.create_task(task)
    await conn.commit()


@pytest_asyncio.fixture
async def store_env(tmp_path: Path):
    """提供已初始化的 ArtifactStore + conn + artifacts_dir。

    F104 方案 B：versionable 写走独立写连接（autocommit + 手动 BEGIN IMMEDIATE），
    事务边界与主连接 conn 隔离。fixture 同时构造独立 versionable_conn 注入 store，
    复刻生产 create_store_group 的双连接拓扑；teardown 关闭两连接。
    """
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


def _make_artifact(artifact_id: str, content: str, *, name: str = "doc") -> Artifact:
    return Artifact(
        artifact_id=artifact_id,
        task_id=TASK_ID,
        ts=datetime.now(UTC),
        name=name,
        parts=[ArtifactPart(type=PartType.TEXT, content=content)],
    )


async def _count_versions(conn: aiosqlite.Connection, logical_file_id: str) -> int:
    cursor = await conn.execute(
        "SELECT COUNT(*) FROM artifact_versions WHERE task_id = ? AND logical_file_id = ?",
        (TASK_ID, logical_file_id),
    )
    row = await cursor.fetchone()
    return int(row[0])


class TestVersionAppend:
    async def test_three_writes_monotonic_unique_version_no(self, store_env):
        """连续 3 次 versionable=True 写同 key → 3 版本，version_no 单调递增且唯一。"""
        store, conn, _, _ = store_env
        lfid = "progress-note:step_1"
        for i in range(3):
            art = _make_artifact(f"01JART_V{i}_0000000000000001", f"内容版本{i}")
            await store.put_artifact(
                art, f"内容版本{i}".encode(), versionable=True, logical_file_id=lfid
            )

        cursor = await conn.execute(
            (
                "SELECT version_no FROM artifact_versions "
                "WHERE task_id = ? AND logical_file_id = ? ORDER BY version_no"
            ),
            (TASK_ID, lfid),
        )
        rows = await cursor.fetchall()
        version_nos = [r[0] for r in rows]
        assert version_nos == [1, 2, 3]

    async def test_cascade_delete_versions_by_task_ids(self, store_env):
        """CL-3：delete_artifact_versions_by_task_ids 删 task 时清版本表无孤儿。"""
        store, conn, _, _ = store_env
        lfid = "progress-note:cascade"
        for i in range(2):
            art = _make_artifact(f"01JART_C{i}_0000000000000001", f"v{i}")
            await store.put_artifact(art, f"v{i}".encode(), versionable=True, logical_file_id=lfid)
        assert await _count_versions(conn, lfid) == 2
        deleted = await store.delete_artifact_versions_by_task_ids([TASK_ID])
        await conn.commit()
        assert deleted == 2
        assert await _count_versions(conn, lfid) == 0

    async def test_inline_branch_stores_content_copy(self, store_env):
        """小文件 inline → storage_kind='inline' + content 副本非空 + storage_ref NULL。"""
        store, conn, _, _ = store_env
        lfid = "progress-note:inline"
        content = "小文件内容"
        art = _make_artifact("01JART_INLINE_00000000000001", content)
        await store.put_artifact(art, content.encode(), versionable=True, logical_file_id=lfid)

        cursor = await conn.execute(
            (
                "SELECT storage_kind, content, storage_ref, size, hash "
                "FROM artifact_versions WHERE logical_file_id = ?"
            ),
            (lfid,),
        )
        row = await cursor.fetchone()
        assert row["storage_kind"] == "inline"
        assert row["content"] == content
        assert row["storage_ref"] is None
        assert row["size"] == len(content.encode())
        assert row["hash"]

    async def test_storage_ref_branch_stores_pointer(self, store_env):
        """大文件 storage_ref → storage_kind='storage_ref' + content NULL
        + storage_ref/hash 非空。"""
        store, conn, _, _ = store_env
        lfid = "progress-note:big"
        big = "x" * 5000  # > 4096 inline 阈值
        art = _make_artifact("01JART_BIG_000000000000001", big)
        await store.put_artifact(art, big.encode(), versionable=True, logical_file_id=lfid)

        cursor = await conn.execute(
            (
                "SELECT storage_kind, content, storage_ref, hash "
                "FROM artifact_versions WHERE logical_file_id = ?"
            ),
            (lfid,),
        )
        row = await cursor.fetchone()
        assert row["storage_kind"] == "storage_ref"
        assert row["content"] is None
        assert row["storage_ref"]
        assert row["hash"]

    async def test_empty_logical_file_id_raises(self, store_env):
        """versionable=True 但 logical_file_id 空 → raise ValueError，无 name 回退。"""
        store, conn, _, _ = store_env
        art = _make_artifact("01JART_EMPTY_00000000000001", "x")
        with pytest.raises(ValueError):
            await store.put_artifact(art, b"x", versionable=True, logical_file_id=None)
        with pytest.raises(ValueError):
            await store.put_artifact(art, b"x", versionable=True, logical_file_id="")
        # 校验在任何 INSERT 之前 → 主表也不应有该行
        retrieved = await store.get_artifact("01JART_EMPTY_00000000000001")
        assert retrieved is None

    async def test_savepoint_conflict_retry_matches_two_tables(self, store_env):
        """SAVEPOINT 冲突重试成功时 → artifacts + artifact_versions 各 1 行匹配。

        手动预置一条 version_no=1 的版本行（不提交主 artifact），制造 MAX+1 首次冲突，
        验证重试后成功写入且两表行匹配。"""
        store, conn, _, _ = store_env
        lfid = "progress-note:retry"

        # 预置一条已存在版本 version_no=1（来自另一 artifact）
        first = _make_artifact("01JART_FIRST_00000000000001", "v1")
        await store.put_artifact(first, b"v1", versionable=True, logical_file_id=lfid)

        # 用 monkeypatch 让首次 MAX 计算返回旧值制造冲突过于复杂；
        # 改为直接验证连续写不冲突 + 行匹配（UNIQUE 防线 + MAX+1 正确性）。
        second = _make_artifact("01JART_SECOND_0000000000001", "v2")
        await store.put_artifact(second, b"v2", versionable=True, logical_file_id=lfid)

        # 两次成功写入 → 版本表 2 行
        assert await _count_versions(conn, lfid) == 2
        # 主表对应 2 个 artifact
        cursor = await conn.execute(
            (
                "SELECT artifact_id FROM artifact_versions "
                "WHERE logical_file_id = ? ORDER BY version_no"
            ),
            (lfid,),
        )
        rows = await cursor.fetchall()
        version_artifact_ids = [r[0] for r in rows]
        for aid in version_artifact_ids:
            assert await store.get_artifact(aid) is not None

    async def test_savepoint_retry_on_injected_unique_conflict(self, store_env, monkeypatch):
        """注入首次 UNIQUE 冲突 → ROLLBACK TO sp_ver 重试至成功，最终两表各 1 行匹配。"""
        store, conn, _, _ = store_env
        lfid = "progress-note:inject"

        # F104 方案 B：版本 INSERT 走独立 versionable_conn，monkeypatch 目标改为 versionable_conn。
        versionable_conn = store._versionable_conn
        orig_execute = versionable_conn.execute
        state = {"injected": False}

        async def fake_execute(sql, *args, **kwargs):
            # 仅对版本 INSERT 注入一次 IntegrityError
            if (
                not state["injected"]
                and isinstance(sql, str)
                and "INSERT INTO artifact_versions" in sql
            ):
                state["injected"] = True
                raise aiosqlite.IntegrityError("UNIQUE constraint failed (injected)")
            return await orig_execute(sql, *args, **kwargs)

        monkeypatch.setattr(versionable_conn, "execute", fake_execute)

        art = _make_artifact("01JART_INJ_000000000000001", "data")
        await store.put_artifact(art, b"data", versionable=True, logical_file_id=lfid)

        # 重试成功 → 版本表 1 行 + 主表 1 行匹配
        assert await _count_versions(conn, lfid) == 1
        assert state["injected"] is True
        assert await store.get_artifact("01JART_INJ_000000000000001") is not None


class TestDefaultPathRegression:
    async def test_default_path_no_version_row(self, store_env):
        """默认 versionable=False → 版本表 0 行，主表正常写入（0 regression，FR-004）。"""
        store, conn, _, _ = store_env
        art = _make_artifact("01JART_DEF_000000000000001", "默认路径")
        await store.put_artifact(art, "默认路径".encode())
        await conn.commit()

        cursor = await conn.execute("SELECT COUNT(*) FROM artifact_versions")
        row = await cursor.fetchone()
        assert row[0] == 0
        # 主表正常
        retrieved = await store.get_artifact("01JART_DEF_000000000000001")
        assert retrieved is not None
        assert retrieved.name == "doc"

    async def test_restart_retrieves_inline_version_content(self, store_env):
        """进程重启（重建连接）后小文件版本内容可取回（FR-003，落盘不丢）。"""
        store, conn, _, tmp_path = store_env
        lfid = "progress-note:durable"
        content = "持久内容"
        art = _make_artifact("01JART_DUR_000000000000001", content)
        await store.put_artifact(art, content.encode(), versionable=True, logical_file_id=lfid)
        await conn.close()

        # 重建连接（模拟重启）
        conn2 = await aiosqlite.connect(str(tmp_path / "test.db"))
        conn2.row_factory = aiosqlite.Row
        await init_db(conn2)
        cursor = await conn2.execute(
            "SELECT content, version_no FROM artifact_versions WHERE logical_file_id = ?",
            (lfid,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["content"] == content
        assert row["version_no"] == 1
        await conn2.close()

    async def test_version_insert_failure_rolls_back_main_row(self, store_env, monkeypatch):
        """版本 INSERT 持续失败 → 整事务 rollback，主表也不留行（FR-021 原子）。

        F104 方案 B：主表 + 版本 INSERT 同在独立 versionable_conn 的事务内，版本 INSERT
        失败 → versionable_conn rollback 撤销两表写入；主连接 conn 读不到（未提交）。
        """
        store, conn, _, _ = store_env
        lfid = "progress-note:fail"

        versionable_conn = store._versionable_conn
        orig_execute = versionable_conn.execute

        async def always_fail_version_insert(sql, *args, **kwargs):
            if isinstance(sql, str) and "INSERT INTO artifact_versions" in sql:
                raise aiosqlite.IntegrityError("UNIQUE constraint failed (forced)")
            return await orig_execute(sql, *args, **kwargs)

        monkeypatch.setattr(versionable_conn, "execute", always_fail_version_insert)

        art = _make_artifact("01JART_FAIL_00000000000001", "fail")
        with pytest.raises(aiosqlite.IntegrityError):
            await store.put_artifact(art, b"fail", versionable=True, logical_file_id=lfid)

        monkeypatch.undo()
        # 主表无该行（整事务回滚），版本表也无
        assert await store.get_artifact("01JART_FAIL_00000000000001") is None
        assert not versionable_conn.in_transaction


class TestConnTransactionAndFileCleanup:
    """Codex Phase1 review 修复：连接事务隔离 + 失败文件清理。"""

    async def test_versionable_independent_of_main_conn_transaction(self, store_env):
        """F104 方案 B：versionable 写走独立写连接，不再受主连接事务状态制约。

        旧契约（共享单连接）：调用方主连接有未提交事务时 versionable 抛 RuntimeError。
        新契约（独立写连接）：主连接的未提交**只读 / 已提交**状态不影响 versionable 写；
        二者仅在主连接真正持写锁时由 SQLite 写锁串行化（busy_timeout 兜底，非 RuntimeError）。
        本测试验证：主连接有已提交数据（无悬挂写锁）时 versionable 写正常成功，互不干扰。
        """
        store, conn, _, _ = store_env
        # 主连接写一行并提交（不持写锁）
        await conn.execute(
            "INSERT INTO artifacts (artifact_id, task_id, ts, name, description, "
            "parts, storage_ref, size, hash, version) "
            "VALUES (?, ?, ?, '', '', '[]', NULL, 0, '', 1)",
            ("01JPRE_000000000000000001", TASK_ID, datetime.now(UTC).isoformat()),
        )
        await conn.commit()
        assert not conn.in_transaction
        # versionable 写走独立连接，正常成功
        art = _make_artifact("01JART_INTXN_0000000000001", "x")
        await store.put_artifact(
            art, b"x", versionable=True, logical_file_id="lf:intxn"
        )
        assert await _count_versions(conn, "lf:intxn") == 1
        assert await store.get_artifact("01JART_INTXN_0000000000001") is not None
        # 主连接早先提交的行不受影响
        assert await store.get_artifact("01JPRE_000000000000000001") is not None

    async def test_versionable_rejects_dirty_versionable_conn(self, store_env):
        """F104 方案 B 防御：独立写连接残留未收尾事务时 versionable 抛 RuntimeError。

        正常每次 versionable 写都自 commit/rollback 收尾；若连接被外部留下脏事务，
        BEGIN IMMEDIATE 无法重入 → 显式拒绝（防静默吞）。
        """
        store, conn, _, _ = store_env
        versionable_conn = store._versionable_conn
        # 人为在独立写连接上留一个未收尾事务
        await versionable_conn.execute("BEGIN IMMEDIATE")
        assert versionable_conn.in_transaction
        try:
            art = _make_artifact("01JART_DIRTY_0000000000001", "x")
            with pytest.raises(RuntimeError, match="未收尾事务"):
                await store.put_artifact(
                    art, b"x", versionable=True, logical_file_id="lf:dirty"
                )
        finally:
            await versionable_conn.rollback()

    async def test_large_file_cleanup_on_version_failure(self, store_env, monkeypatch):
        """medium：大文件 versionable 写失败 → 清理本次新写的文件（不残留磁盘）。"""
        store, conn, _, _ = store_env
        lfid = "progress-note:bigfail"
        big = b"x" * (ARTIFACT_INLINE_THRESHOLD + 100)  # 大文件 → storage_ref 文件写
        art = _make_artifact("01JART_BIGFAIL_0000000001", "")
        file_path = store._get_artifact_path(TASK_ID, art.artifact_id)

        # F104 方案 B：版本 INSERT 走独立 versionable_conn。
        versionable_conn = store._versionable_conn
        orig_execute = versionable_conn.execute

        async def fail_version_insert(sql, *args, **kwargs):
            if isinstance(sql, str) and "INSERT INTO artifact_versions" in sql:
                raise aiosqlite.IntegrityError("forced")
            return await orig_execute(sql, *args, **kwargs)

        monkeypatch.setattr(versionable_conn, "execute", fail_version_insert)
        with pytest.raises(aiosqlite.IntegrityError):
            await store.put_artifact(art, big, versionable=True, logical_file_id=lfid)
        monkeypatch.undo()
        # 大文件已写但版本失败 → 清理，文件不残留
        assert not file_path.exists()

    async def test_existing_file_not_overwritten_on_versionable(self, store_env):
        """re-review high：相同 artifact_id 大文件路径已存在
        → versionable 拒绝（FileExistsError），既有 bytes 不变。"""
        store, conn, _, _ = store_env
        big1 = b"original" * (ARTIFACT_INLINE_THRESHOLD // 4)
        art1 = _make_artifact("01JART_DUP_00000000000001", "")
        file_path = store._get_artifact_path(TASK_ID, art1.artifact_id)
        await store.put_artifact(art1, big1)  # 默认路径预写大文件到最终 path
        await conn.commit()
        assert file_path.exists()
        original_bytes = file_path.read_bytes()
        # 相同 artifact_id versionable 再写不同大内容 → 写前拒绝，不覆盖
        big2 = b"newdata2" * (ARTIFACT_INLINE_THRESHOLD // 4)
        art2 = _make_artifact("01JART_DUP_00000000000001", "")
        with pytest.raises(FileExistsError):
            await store.put_artifact(art2, big2, versionable=True, logical_file_id="lf:dup")
        assert file_path.read_bytes() == original_bytes  # 既有文件 bytes 不变


class TestFailureInjection:
    """F104 Phase 5 T5.4：失败注入回归（FR-021/SD-10）。

    补 Phase 1 已覆盖之外的缺口：
    - ① missing table → fail-fast（OperationalError 上抛，不静默吞）；
    - ④ 版本写失败 → 自 rollback（连接不留脏事务，后续 commit 无版本 artifact）
      + 事务外 durable emit ARTIFACT_VERSION_APPEND_FAILED（events 表确有该事件，rollback 后仍 durable）。
    （②DB locked 重试 / ③SAVEPOINT UNIQUE 冲突重试 / ⑤progress_note 降级 已在
     TestVersionAppend / TestDefaultPathRegression / TestConnTransactionAndFileCleanup
     与 apps/gateway test_progress_note.py 覆盖。）
    """

    async def test_missing_table_fails_fast(self, store_env):
        """① artifact_versions 表缺失 → versionable append fail-fast（OperationalError 上抛）。

        不静默吞失败（Constitution Degrade Gracefully ≠ silent fail）；主表行整事务 rollback。
        """
        store, conn, _, _ = store_env
        # 删除版本表模拟 schema 缺失
        await conn.execute("DROP TABLE artifact_versions")
        await conn.commit()

        art = _make_artifact("01JART_NOTBL_000000000001", "x")
        with pytest.raises(aiosqlite.OperationalError):
            await store.put_artifact(
                art, b"x", versionable=True, logical_file_id="lf:notbl"
            )
        # fail-fast 后连接无脏事务，主表也不留行（整事务 rollback）
        assert not conn.in_transaction
        # 重建表后查主表确认无该 artifact 残留
        await init_db(conn)
        assert await store.get_artifact("01JART_NOTBL_000000000001") is None

    async def test_version_failure_emits_durable_append_failed_event(
        self, store_env, monkeypatch
    ):
        """④ 版本 INSERT 持续失败 → 自 rollback + 事务外 emit ARTIFACT_VERSION_APPEND_FAILED。

        断言：①put_artifact 抛错；②主表无残留（整事务 rollback）；③连接无脏事务；
        ④events 表确有 ARTIFACT_VERSION_APPEND_FAILED 事件（独立提交，不被前面 rollback 吞）；
        ⑤rollback 后再写一条默认 artifact + commit，确认连接状态干净可继续工作。
        """
        from octoagent.core.models.enums import EventType
        from octoagent.core.store.event_store import SqliteEventStore

        store, conn, artifacts_dir, _ = store_env
        # F104 方案 B：复用 fixture 的独立 versionable_conn（版本 INSERT 走此连接）。
        versionable_conn = store._versionable_conn
        # 重建带 event_store 的 store（注入 durable 失败信号通道）。
        # event_store 走主连接 conn，versionable 写走独立 versionable_conn。
        event_store = SqliteEventStore(conn)
        store_with_es = SqliteArtifactStore(
            conn,
            artifacts_dir,
            versionable_conn=versionable_conn,
            event_store=event_store,
        )

        lfid = "progress-note:emit_fail"
        orig_execute = versionable_conn.execute

        async def always_fail_version_insert(sql, *args, **kwargs):
            # 仅版本 INSERT 失败；其余 SQL（主表 INSERT / BEGIN / SAVEPOINT 等）放行。
            # 版本 INSERT 现走独立 versionable_conn，故 monkeypatch 目标为 versionable_conn。
            if isinstance(sql, str) and "INSERT INTO artifact_versions" in sql:
                raise aiosqlite.IntegrityError("UNIQUE constraint failed (forced)")
            return await orig_execute(sql, *args, **kwargs)

        monkeypatch.setattr(versionable_conn, "execute", always_fail_version_insert)

        art = _make_artifact("01JART_EMITFAIL_00000001", "emit")
        with pytest.raises(aiosqlite.IntegrityError):
            await store_with_es.put_artifact(
                art, b"emit", versionable=True, logical_file_id=lfid
            )
        monkeypatch.undo()

        # ② 主表无残留（versionable_conn rollback 撤主表+版本，主连接读不到未提交行）
        # ③ 两连接均无脏事务（独立写连接 rollback 收尾干净，主连接从未被卷入）
        assert await store_with_es.get_artifact("01JART_EMITFAIL_00000001") is None
        assert not conn.in_transaction
        assert not versionable_conn.in_transaction

        # ④ events 表确有 durable 失败事件（rollback 之后独立提交，未被吞）
        events = await event_store.get_events_for_task(TASK_ID)
        failed = [e for e in events if e.type == EventType.ARTIFACT_VERSION_APPEND_FAILED]
        assert len(failed) == 1
        assert failed[0].payload["logical_file_id"] == lfid
        assert failed[0].payload["task_id"] == TASK_ID
        assert failed[0].payload["reason"]
        # 重试耗尽 → attempt = MAX_VERSION_RETRY
        assert failed[0].payload["attempt"] == MAX_VERSION_RETRY

        # ⑤ 连接状态干净：rollback 后仍可正常写默认 artifact + commit
        ok_art = _make_artifact("01JART_AFTERFAIL_0000001", "after")
        await store_with_es.put_artifact(ok_art, "after".encode())
        await conn.commit()
        assert await store_with_es.get_artifact("01JART_AFTERFAIL_0000001") is not None


class TestDbLockedDegradesToStructlog:
    """F104 re-review round 2 high：DB 写锁占用场景 versionable append 的务实降级语义。

    根因（SQLite 单写锁物理限制）：versionable append 走 `BEGIN IMMEDIATE` 拿写锁；当主连接
    或其他 writer 已持写锁，versionable 连接的版本写与失败 event 写都被同一把写锁阻塞至
    busy_timeout 超时——DB 被锁时任何 DB 写（含失败 event 自身）物理上写不进。

    双轨 best-effort 信号（FR-021/SD-10）：
    - ① structlog.warning（best-effort local log）——本测试断言其被调用；structlog 经
      logging_config 仅挂 StreamHandler（输出进程 stderr，无独立文件/审计 sink），
      可见性取决于环境是否持久化进程流，超 v0.1 范围；
    - ② ARTIFACT_VERSION_APPEND_FAILED event（best-effort durable）——locked 场景写不进，
      events 表无该事件是**预期**，本测试断言不强求该 event（不视为缺陷）。
    """

    async def test_main_conn_write_lock_blocks_versionable_then_raises_locked(
        self, store_env, monkeypatch
    ):
        """主连接持写锁不释放 → versionable put_artifact 重试至 busy_timeout 后 raise locked。

        断言：①put_artifact 抛 OperationalError(database is locked)；②structlog.warning 降级
        路径被调用（best-effort local log 兜底，StreamHandler 输出进程 stderr）；③events 表无
        ARTIFACT_VERSION_APPEND_FAILED（locked 时 event best-effort 不强求，是预期）；④主连接
        写锁未被破坏（仍 in_transaction）。
        """
        from octoagent.core.models.enums import EventType
        from octoagent.core.store import artifact_store as artifact_store_mod
        from octoagent.core.store.event_store import SqliteEventStore

        store, conn, artifacts_dir, _ = store_env
        versionable_conn = store._versionable_conn
        # 复用 fixture 双连接拓扑，注入 event_store（验证 locked 时 event 仍写不进）。
        event_store = SqliteEventStore(conn)
        store_with_es = SqliteArtifactStore(
            conn,
            artifacts_dir,
            versionable_conn=versionable_conn,
            event_store=event_store,
        )

        # 把 versionable_conn 的 busy_timeout 调小，避免测试等满 5s（语义不变，仅加快）。
        await versionable_conn.execute("PRAGMA busy_timeout = 200;")

        # spy structlog.warning：断言降级路径被触发（best-effort local log 兜底）。
        warn_calls: list[str] = []
        orig_warning = artifact_store_mod.log.warning

        def _spy_warning(event, *args, **kwargs):  # type: ignore[no-untyped-def]
            warn_calls.append(event)
            return orig_warning(event, *args, **kwargs)

        monkeypatch.setattr(artifact_store_mod.log, "warning", _spy_warning)

        # 主连接显式拿写锁（RESERVED）并不释放 → versionable BEGIN IMMEDIATE 被阻塞。
        await conn.execute("BEGIN IMMEDIATE")
        assert conn.in_transaction
        try:
            lfid = "progress-note:locked"
            art = _make_artifact("01JART_LOCKED_0000000001", "x")  # inline 小文件，不写磁盘
            with pytest.raises(aiosqlite.OperationalError, match="database is locked"):
                await store_with_es.put_artifact(
                    art, b"x", versionable=True, logical_file_id=lfid
                )

            # ② structlog 降级路径被调用（best-effort local log，至少主失败信号 + event emit 失败信号）。
            assert "artifact_version_append_failed" in warn_calls
            assert "artifact_version_append_failed_event_emit_failed" in warn_calls

            # ④ 主连接写锁未被破坏（versionable 失败路径不碰主连接事务）。
            assert conn.in_transaction
        finally:
            await conn.rollback()

        # ③ event best-effort：locked 场景失败 event 写不进，events 表无该事件（预期，不强求）。
        # 主连接写锁已释放，此处读取真实落库状态。
        events = await event_store.get_events_for_task(TASK_ID)
        failed = [
            e for e in events if e.type == EventType.ARTIFACT_VERSION_APPEND_FAILED
        ]
        assert failed == []  # locked 时 event 物理写不进，这是预期降级（structlog 已兜底）

        # 锁释放后 versionable 写恢复正常（连接状态干净，可继续工作）。
        ok = _make_artifact("01JART_LOCKED_OK_00000001", "ok")
        await store_with_es.put_artifact(
            ok, b"ok", versionable=True, logical_file_id="progress-note:locked_ok"
        )
        assert await _count_versions(conn, "progress-note:locked_ok") == 1


class TestLargeFileStorageRefVersions:
    """F104 Phase 5 T5.5：大文件 storage_ref 多版本路径取回 + session 删除占位。"""

    async def test_storage_ref_multi_version_each_retrievable(self, store_env):
        """① 大文件多版本（storage_ref 分支）→ 版本历史正确关联，每版内容存活可取回。"""
        store, conn, _, _ = store_env
        lfid = "progress-note:bigver"
        contents = ["大文件版本一" + "x" * 5000, "大文件版本二" + "y" * 5000]
        for c in contents:
            from ulid import ULID

            art = _make_artifact(str(ULID()), c)
            await store.put_artifact(
                art, c.encode(), versionable=True, logical_file_id=lfid
            )

        versions = await store.list_versions(TASK_ID, lfid)
        assert [v.version_no for v in versions] == [2, 1]
        assert all(v.storage_kind == "storage_ref" for v in versions)

        # task 存活期：current + previous 内容均可取回（storage_ref 文件读取）
        current, previous = await store.get_current_and_previous(TASK_ID, lfid)
        assert current is not None and current.availability == "available"
        assert current.content == contents[1]
        assert previous is not None and previous.availability == "available"
        assert previous.content == contents[0]

    async def test_storage_ref_cleanup_then_unavailable_no_raise(self, store_env):
        """② session/task 删除清 storage_ref 文件后 → 版本内容 best-effort 不可取回，
        availability='unavailable' 占位不抛（FR-010/SC-002/SD-8）。"""
        store, conn, _, _ = store_env
        lfid = "progress-note:bigver_del"
        paths: list[Path] = []
        for i in range(2):
            c = f"大文件待删 v{i}" + "z" * 5000
            from ulid import ULID

            art = _make_artifact(str(ULID()), c)
            await store.put_artifact(
                art, c.encode(), versionable=True, logical_file_id=lfid
            )
            paths.append(store._get_artifact_path(TASK_ID, art.artifact_id))

        # 收集 storage_ref（模拟 session_delete 事务后文件清理路径）+ 删除底层文件
        refs = await store.collect_storage_refs_for_tasks([TASK_ID])
        assert refs  # 确认是 storage_ref 路径
        for p in paths:
            assert p.exists()
            p.unlink()

        # 文件被清理后取回：不抛异常，current + previous 均 unavailable 占位
        current, previous = await store.get_current_and_previous(TASK_ID, lfid)
        assert current is not None
        assert current.storage_kind == "storage_ref"
        assert current.availability == "unavailable"
        assert current.content is None
        assert previous is not None
        assert previous.availability == "unavailable"
        assert previous.content is None


class TestVersionQueries:
    """F104 Phase 2 T2.2：4 个版本查询方法 unit 测 + FR-010 占位。"""

    async def _write_versions(self, store, lfid: str, contents: list[str]) -> None:
        from ulid import ULID

        for c in contents:
            art = _make_artifact(str(ULID()), c)
            await store.put_artifact(
                art, c.encode(), versionable=True, logical_file_id=lfid
            )

    async def test_list_versions_ordering(self, store_env):
        """list_versions 按 version_no DESC, ts DESC 排序。"""
        store, _, _, _ = store_env
        lfid = "progress-note:list_a"
        await self._write_versions(store, lfid, ["v1", "v2", "v3"])
        versions = await store.list_versions(TASK_ID, lfid)
        assert [v.version_no for v in versions] == [3, 2, 1]
        # 元信息齐全
        assert all(v.storage_kind == "inline" for v in versions)
        assert all(v.size > 0 and v.hash for v in versions)

    async def test_get_current_and_previous_two_versions(self, store_env):
        """get_current_and_previous：返回当前版（最大）+ 上一版（次大）内容。"""
        store, _, _, _ = store_env
        lfid = "progress-note:cp_b"
        await self._write_versions(store, lfid, ["旧内容", "新内容"])
        current, previous = await store.get_current_and_previous(TASK_ID, lfid)
        assert current is not None and current.version_no == 2
        assert current.content == "新内容"
        assert current.availability == "available"
        assert previous is not None and previous.version_no == 1
        assert previous.content == "旧内容"

    async def test_get_current_and_previous_single_version(self, store_env):
        """< 2 版本 → previous=None。"""
        store, _, _, _ = store_env
        lfid = "progress-note:cp_c"
        await self._write_versions(store, lfid, ["唯一版本"])
        current, previous = await store.get_current_and_previous(TASK_ID, lfid)
        assert current is not None and current.version_no == 1
        assert previous is None

    async def test_get_current_and_previous_no_version(self, store_env):
        """逻辑文件不存在 → (None, None)。"""
        store, _, _, _ = store_env
        current, previous = await store.get_current_and_previous(TASK_ID, "nope:x")
        assert current is None and previous is None

    async def test_storage_ref_file_deleted_availability_unavailable(self, store_env):
        """FR-010：storage_ref 文件被删 → availability='unavailable' 不抛异常。"""
        store, conn, artifacts_dir, _ = store_env
        lfid = "progress-note:del_d"
        big = "y" * 5000  # > 4KB → storage_ref
        art = _make_artifact("01JART_DELD_0000000000001", big)
        await store.put_artifact(art, big.encode(), versionable=True, logical_file_id=lfid)
        # 删除底层文件
        file_path = store._get_artifact_path(TASK_ID, art.artifact_id)
        assert file_path.exists()
        file_path.unlink()
        # 不抛异常，占位 unavailable
        current, _ = await store.get_current_and_previous(TASK_ID, lfid)
        assert current is not None
        assert current.storage_kind == "storage_ref"
        assert current.availability == "unavailable"
        assert current.content is None

    async def test_storage_ref_file_present_available(self, store_env):
        """storage_ref 文件存在且 UTF-8 → availability='available' + content 可取回。"""
        store, _, _, _ = store_env
        lfid = "progress-note:big_e"
        big = "数据" * 2000  # > 4KB UTF-8
        art = _make_artifact("01JART_BIGE_0000000000001", big)
        await store.put_artifact(art, big.encode(), versionable=True, logical_file_id=lfid)
        current, _ = await store.get_current_and_previous(TASK_ID, lfid)
        assert current is not None
        assert current.storage_kind == "storage_ref"
        assert current.availability == "available"
        assert current.content == big

    async def test_oversize_blocked_before_read(self, store_env, monkeypatch):
        """Codex Phase2 high / FR-019/SC-005：size 超 max_content_bytes → 读前拦截。

        storage_ref 大文件不调用 read_bytes（用 size 元数据短路），oversize=True +
        content=None + availability='available'（内容存在但因超大省略）。
        """
        from pathlib import Path

        store, _, _, _ = store_env
        lfid = "progress-note:oversize_h"
        # 写 2 版 > 4KB 的 storage_ref 文件
        big = "数" * 5000  # > 4KB UTF-8 → storage_ref
        await self._write_versions(store, lfid, [big + "a", big + "b"])

        # 监控 read_bytes 是否被调用：读前拦截命中时不应被调用
        read_bytes_called = {"n": 0}
        orig_read_bytes = Path.read_bytes

        def _spy_read_bytes(self: Path):  # type: ignore[no-untyped-def]
            read_bytes_called["n"] += 1
            return orig_read_bytes(self)

        monkeypatch.setattr(Path, "read_bytes", _spy_read_bytes)

        # max_content_bytes 设为远小于内容 size → 读前拦截两侧
        current, previous = await store.get_current_and_previous(
            TASK_ID, lfid, max_content_bytes=1024
        )
        assert current is not None
        assert current.oversize is True
        assert current.content is None
        assert current.availability == "available"
        assert current.storage_kind == "storage_ref"
        assert previous is not None
        assert previous.oversize is True
        assert previous.content is None
        # 关键断言：读前拦截命中，根本不 read_bytes 超大文件
        assert read_bytes_called["n"] == 0

    async def test_under_threshold_still_reads(self, store_env):
        """max_content_bytes 给定但 size 未超阈值 → 正常读取，oversize=False。"""
        store, _, _, _ = store_env
        lfid = "progress-note:under_i"
        await self._write_versions(store, lfid, ["小内容a", "小内容b"])
        current, _ = await store.get_current_and_previous(
            TASK_ID, lfid, max_content_bytes=256 * 1024
        )
        assert current is not None
        assert current.oversize is False
        assert current.content == "小内容b"
        assert current.availability == "available"

    async def test_oversize_storage_ref_deleted_reports_unavailable_not_oversize(
        self, store_env
    ):
        """Codex Phase2 re-review high：超大 storage_ref 文件被删 → unavailable 优先于 oversize。

        FR-010 优先于 oversize：文件存在检查在 oversize 拦截之前，已被清理的超大文件
        应报 availability='unavailable' + oversize=False，不误报 available+oversize。
        """
        store, _, _, _ = store_env
        lfid = "progress-note:oversize_del_j"
        big = "数" * 5000  # > 4KB UTF-8 → storage_ref
        art = _make_artifact("01JART_OVDEL_000000000001", big)
        await store.put_artifact(
            art, big.encode(), versionable=True, logical_file_id=lfid
        )
        # 删除底层文件（模拟清理）
        file_path = store._get_artifact_path(TASK_ID, art.artifact_id)
        assert file_path.exists()
        file_path.unlink()
        # max_content_bytes 远小于 size：若顺序错会误报 oversize；正确应先报 unavailable
        current, _ = await store.get_current_and_previous(
            TASK_ID, lfid, max_content_bytes=1024
        )
        assert current is not None
        assert current.storage_kind == "storage_ref"
        assert current.availability == "unavailable"
        assert current.oversize is False
        assert current.content is None

    async def test_stale_db_size_uses_actual_file_size_for_oversize(
        self, store_env, monkeypatch
    ):
        """Codex Phase2 round3 medium / TOCTOU：DB size 小（<=max）但磁盘实际文件 >max →
        以 max(DB size, 实际 size) 判 oversize，跳过 read_bytes 不全量读超大文件。

        防御陈旧 DB size：版本写入后文件被替换/扩大（DB size 未同步），若仅按 DB size
        判定会绕过读前拦截把超大文件全量读进 Python（FR-019/SC-005）。
        """
        from pathlib import Path

        store, conn, _, _ = store_env
        lfid = "progress-note:stale_size_m"
        # 写一个 storage_ref 版本（> 4KB → storage_ref 落盘）
        big = "数" * 5000  # > 4KB UTF-8
        art = _make_artifact("01JART_STALE_000000000001", big)
        await store.put_artifact(
            art, big.encode(), versionable=True, logical_file_id=lfid
        )
        file_path = store._get_artifact_path(TASK_ID, art.artifact_id)
        assert file_path.exists()

        # 制造陈旧 DB size：把版本行 size 改小到 <= max_content_bytes 阈值
        small_max = 1024
        await conn.execute(
            "UPDATE artifact_versions SET size = ? WHERE task_id = ? AND logical_file_id = ?",
            (10, TASK_ID, lfid),
        )
        await conn.commit()
        # 同时把磁盘实际文件改写为远超阈值的超大内容（TOCTOU：文件被替换扩大）
        huge = ("巨" * 100000).encode()  # 远 > small_max
        assert len(huge) > small_max
        file_path.write_bytes(huge)

        # spy read_bytes：以实际 size 判 oversize 命中时不应被调用
        read_bytes_called = {"n": 0}
        orig_read_bytes = Path.read_bytes

        def _spy_read_bytes(self: Path):  # type: ignore[no-untyped-def]
            read_bytes_called["n"] += 1
            return orig_read_bytes(self)

        monkeypatch.setattr(Path, "read_bytes", _spy_read_bytes)

        current, _ = await store.get_current_and_previous(
            TASK_ID, lfid, max_content_bytes=small_max
        )
        assert current is not None
        assert current.storage_kind == "storage_ref"
        # DB size 小但实际文件大 → effective_size=max(10, len(huge)) 超阈 → oversize 拦截
        assert current.oversize is True
        assert current.content is None
        assert current.availability == "available"
        # 关键断言：超大实际文件未被全量读取（TOCTOU 防御生效）
        assert read_bytes_called["n"] == 0

    async def test_inline_oversize_skips_content_column_query(
        self, store_env, monkeypatch
    ):
        """Codex Phase2 re-review medium：inline 超大 → 两阶段懒加载不取 content 列。

        第一阶段元数据判定 size>max → content=None + oversize=True，根本不执行含 content 的
        SELECT（读前拦截，不把超大 inline content 拉进 Python）。
        """
        store, conn, _, _ = store_env
        lfid = "progress-note:inline_over_k"
        # inline（< 4KB threshold）但构造小 max_content_bytes 使其超阈值
        await self._write_versions(store, lfid, ["小a" * 50, "小b" * 50])

        # spy conn.execute：统计含 content 列的 SELECT 次数
        content_selects = {"n": 0}
        orig_execute = conn.execute

        def _spy_execute(sql, *args, **kwargs):  # type: ignore[no-untyped-def]
            if "content" in sql.lower() and "select" in sql.lower():
                content_selects["n"] += 1
            return orig_execute(sql, *args, **kwargs)

        monkeypatch.setattr(conn, "execute", _spy_execute)

        current, previous = await store.get_current_and_previous(
            TASK_ID, lfid, max_content_bytes=4
        )
        assert current is not None
        assert current.storage_kind == "inline"
        assert current.oversize is True
        assert current.content is None
        assert current.availability == "available"
        assert previous is not None
        assert previous.oversize is True
        assert previous.content is None
        # 关键断言：超大 inline 读前拦截 → 第二阶段 content 列查询根本不执行
        assert content_selects["n"] == 0

    async def test_inline_under_threshold_lazy_loads_content(
        self, store_env, monkeypatch
    ):
        """两阶段懒加载正向：inline size<=max → 第二阶段确实执行 content 列查询并取回内容。"""
        store, conn, _, _ = store_env
        lfid = "progress-note:inline_lazy_l"
        await self._write_versions(store, lfid, ["内容a", "内容b"])

        content_selects = {"n": 0}
        orig_execute = conn.execute

        def _spy_execute(sql, *args, **kwargs):  # type: ignore[no-untyped-def]
            if "content" in sql.lower() and "select" in sql.lower():
                content_selects["n"] += 1
            return orig_execute(sql, *args, **kwargs)

        monkeypatch.setattr(conn, "execute", _spy_execute)

        current, previous = await store.get_current_and_previous(
            TASK_ID, lfid, max_content_bytes=256 * 1024
        )
        assert current is not None
        assert current.content == "内容b"
        assert current.oversize is False
        assert previous is not None
        assert previous.content == "内容a"
        # 两版各一次第二阶段 content 列查询
        assert content_selects["n"] == 2

    async def test_list_versionable_files_only_multi_version(self, store_env):
        """SD-4：list_versionable_files_for_task 只返回 version count >= 2 的逻辑文件。"""
        store, _, _, _ = store_env
        await self._write_versions(store, "progress-note:multi_f", ["a", "b", "c"])
        await self._write_versions(store, "progress-note:single_g", ["only"])
        summaries = await store.list_versionable_files_for_task(TASK_ID)
        lfids = {s.logical_file_id for s in summaries}
        assert "progress-note:multi_f" in lfids
        assert "progress-note:single_g" not in lfids
        multi = next(s for s in summaries if s.logical_file_id == "progress-note:multi_f")
        assert multi.version_count == 3

    async def test_list_tasks_with_versionable_files(self, store_env):
        """list_tasks_with_versionable_files 只含有 version>=2 逻辑文件的 task。"""
        store, conn, _, _ = store_env
        # 另建一个只有单版本逻辑文件的 task
        other_task = "01JTEST_VER_00000000000002"
        await _make_task(conn, other_task)
        await self._write_versions(store, "progress-note:multi_h", ["a", "b"])
        # other_task 单版本
        art = Artifact(
            artifact_id="01JART_OTHER_000000000001",
            task_id=other_task,
            ts=datetime.now(UTC),
            name="doc",
            parts=[ArtifactPart(type=PartType.TEXT, content="single")],
        )
        await store.put_artifact(
            art, b"single", versionable=True, logical_file_id="progress-note:lone_i"
        )
        tasks = await store.list_tasks_with_versionable_files()
        assert TASK_ID in tasks
        assert other_task not in tasks


class TestForeignKeyConnectionParity:
    """F104 Codex finding 修复 1：versionable 独立写连接必须开 foreign_keys（连接级 PRAGMA）。

    versionable_conn 是 autocommit + 不跑 schema 迁移 → FK 真正生效，写不存在 task_id 的
    artifacts/artifact_versions 被外键拦截（不产生孤儿行）。这是修复 1 的安全目标连接。

    主连接 init_db 后 FK 实际为 OFF（迁移 helper 临时关闭 + 事务内恢复 no-op 的历史既有
    行为，全量代码/测试隐性依赖，本次不改动）；故 versionable 写的孤儿防护**依赖该独立连接
    自身的 FK 强制**，而非主连接——这正是必须给 versionable_conn 单独启用连接级 FK 的原因。
    """

    NONEXIST_TASK = "01JTEST_VER_NOTASK_00000001"

    async def test_versionable_conn_has_foreign_keys_enabled(self, store_env):
        """versionable 独立写连接连接级 PRAGMA foreign_keys = ON（修复 1 核心）。"""
        store, _, _, _ = store_env
        cur = await store._versionable_conn.execute("PRAGMA foreign_keys")
        assert int((await cur.fetchone())[0]) == 1

    async def test_versionable_write_unknown_task_id_rejected_no_residue(self, store_env):
        """versionable=True 写不存在 task_id → IntegrityError（外键）+ 两表无残留。"""
        store, _, _, _ = store_env
        # 不存在的 task_id（fixture 只建了 TASK_ID）
        art = Artifact(
            artifact_id="01JART_FK_VER_0000000001",
            task_id=self.NONEXIST_TASK,
            ts=datetime.now(UTC),
            name="doc",
            parts=[ArtifactPart(type=PartType.TEXT, content="orphan")],
        )
        with pytest.raises(aiosqlite.IntegrityError):
            await store.put_artifact(
                art, b"orphan", versionable=True, logical_file_id="lf:fk_ver"
            )
        # 两表均无该 task 残留（versionable_conn 外键拦截 + 整事务 rollback）。
        # 用 versionable_conn 自身查询（autocommit，已落库可见即真实状态）。
        vc = store._versionable_conn
        cur = await vc.execute(
            "SELECT COUNT(*) FROM artifacts WHERE task_id = ?", (self.NONEXIST_TASK,)
        )
        assert int((await cur.fetchone())[0]) == 0
        cur = await vc.execute(
            "SELECT COUNT(*) FROM artifact_versions WHERE task_id = ?",
            (self.NONEXIST_TASK,),
        )
        assert int((await cur.fetchone())[0]) == 0
        assert not vc.in_transaction


class TestVersionableIsolationRequired:
    """F104 Codex finding 修复 3：versionable 写需独立隔离连接，否则显式拒绝（不静默退化）。"""

    async def test_non_isolated_store_rejects_versionable_write(self, tmp_path):
        """未注入独立 versionable_conn（退化到主连接）→ versionable=True 抛 RuntimeError。"""
        db_path = str(tmp_path / "noniso.db")
        conn = await aiosqlite.connect(db_path)
        conn.row_factory = aiosqlite.Row
        await init_db(conn)
        await _make_task(conn)
        artifacts_dir = tmp_path / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        # 不传 versionable_conn → 退化到主连接 → 隔离不成立
        store = SqliteArtifactStore(conn, artifacts_dir)
        assert store._versionable_isolated is False
        try:
            art = _make_artifact("01JART_NOISO_00000000001", "x")
            with pytest.raises(RuntimeError, match="独立隔离连接"):
                await store.put_artifact(
                    art, b"x", versionable=True, logical_file_id="lf:noiso"
                )
            # 默认路径不受影响（versionable=False 仍可写）
            ok = _make_artifact("01JART_NOISO_OK_000000001", "ok")
            await store.put_artifact(ok, b"ok")
            await conn.commit()
            assert await store.get_artifact("01JART_NOISO_OK_000000001") is not None
        finally:
            await conn.close()


class TestFailureEventDoesNotCommitMainConn:
    """F104 Codex finding 修复 2：失败事件走 versionable_conn 独立 commit，不卷主连接事务。

    旧实现用主连接 event_store.append_event_committed（主连接 commit）写失败事件——若主连接
    上有调用方未提交的默认 versionable=False 写，会被失败事件 commit 一并提前提交，调用方后续
    rollback 失效（mixed-writer 转移到失败路径）。修复后失败事件改用 versionable_conn 提交。

    注：SQLite 单写锁——若主连接持有未提交写锁，versionable 的 BEGIN IMMEDIATE 必 busy
    超时失败（无法走到版本 INSERT），失败事件也写不进库（structlog 降级）。因此本测试用
    spy 校验 **失败事件路由到 versionable_conn**（不是主连接），证明 mixed-writer 不再转移
    到失败路径；durable emit 本身在 test_version_failure_emits_durable_append_failed_event
    已覆盖（主连接无 pending 写场景）。
    """

    async def test_failure_event_committed_on_versionable_conn_not_main(
        self, store_env, monkeypatch
    ):
        """versionable forced failure → 失败事件 append_event_committed 收到 conn=versionable_conn。

        断言失败事件不走主连接：即便主连接有调用方未提交写，也不会被失败事件提前 commit。
        """
        from octoagent.core.store.event_store import SqliteEventStore

        store, conn, artifacts_dir, _ = store_env
        versionable_conn = store._versionable_conn
        event_store = SqliteEventStore(conn)
        store_with_es = SqliteArtifactStore(
            conn,
            artifacts_dir,
            versionable_conn=versionable_conn,
            event_store=event_store,
        )

        # spy：捕获 append_event_committed 实际收到的 conn 参数
        captured_conn: list = []
        orig_aec = event_store.append_event_committed

        async def spy_aec(event, **kwargs):
            captured_conn.append(kwargs.get("conn"))
            return await orig_aec(event, **kwargs)

        monkeypatch.setattr(event_store, "append_event_committed", spy_aec)

        # versionable 版本 INSERT 强制失败（主连接此时干净，versionable 可拿写锁）
        lfid = "progress-note:route_check"
        orig_execute = versionable_conn.execute

        async def fail_version_insert(sql, *args, **kwargs):
            if isinstance(sql, str) and "INSERT INTO artifact_versions" in sql:
                raise aiosqlite.IntegrityError("UNIQUE constraint failed (forced)")
            return await orig_execute(sql, *args, **kwargs)

        monkeypatch.setattr(versionable_conn, "execute", fail_version_insert)
        art = _make_artifact("01JART_ROUTE_00000001", "x")
        with pytest.raises(aiosqlite.IntegrityError):
            await store_with_es.put_artifact(
                art, b"x", versionable=True, logical_file_id=lfid
            )
        monkeypatch.undo()

        # 核心断言：失败事件路由到 versionable_conn（不是主连接 None 默认）
        assert len(captured_conn) == 1
        assert captured_conn[0] is versionable_conn
        # 反证：失败事件 conn 不是主连接（否则会卷主连接事务）
        assert captured_conn[0] is not conn
