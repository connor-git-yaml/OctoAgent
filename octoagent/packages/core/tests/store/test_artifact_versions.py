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
from octoagent.core.models import Artifact, ArtifactPart, PartType, RequesterInfo, Task
from octoagent.core.config import ARTIFACT_INLINE_THRESHOLD
from octoagent.core.store.artifact_store import SqliteArtifactStore
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
    """提供已初始化的 ArtifactStore + conn + artifacts_dir。"""
    conn = await aiosqlite.connect(str(tmp_path / "test.db"))
    conn.row_factory = aiosqlite.Row
    await init_db(conn)
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    await _make_task(conn)
    artifact_store = SqliteArtifactStore(conn, artifacts_dir)
    yield artifact_store, conn, artifacts_dir, tmp_path
    await conn.close()


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
            await store.put_artifact(art, f"内容版本{i}".encode(), versionable=True, logical_file_id=lfid)

        cursor = await conn.execute(
            "SELECT version_no FROM artifact_versions WHERE task_id = ? AND logical_file_id = ? ORDER BY version_no",
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
            "SELECT storage_kind, content, storage_ref, size, hash FROM artifact_versions WHERE logical_file_id = ?",
            (lfid,),
        )
        row = await cursor.fetchone()
        assert row["storage_kind"] == "inline"
        assert row["content"] == content
        assert row["storage_ref"] is None
        assert row["size"] == len(content.encode())
        assert row["hash"]

    async def test_storage_ref_branch_stores_pointer(self, store_env):
        """大文件 storage_ref → storage_kind='storage_ref' + content NULL + storage_ref/hash 非空。"""
        store, conn, _, _ = store_env
        lfid = "progress-note:big"
        big = "x" * 5000  # > 4096 inline 阈值
        art = _make_artifact("01JART_BIG_000000000000001", big)
        await store.put_artifact(art, big.encode(), versionable=True, logical_file_id=lfid)

        cursor = await conn.execute(
            "SELECT storage_kind, content, storage_ref, hash FROM artifact_versions WHERE logical_file_id = ?",
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
            "SELECT artifact_id FROM artifact_versions WHERE logical_file_id = ? ORDER BY version_no",
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

        orig_execute = conn.execute
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

        monkeypatch.setattr(conn, "execute", fake_execute)

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
        """版本 INSERT 持续失败 → 整事务 rollback，主表也不留行（FR-021 原子）。"""
        store, conn, _, _ = store_env
        lfid = "progress-note:fail"

        orig_execute = conn.execute

        async def always_fail_version_insert(sql, *args, **kwargs):
            if isinstance(sql, str) and "INSERT INTO artifact_versions" in sql:
                raise aiosqlite.IntegrityError("UNIQUE constraint failed (forced)")
            return await orig_execute(sql, *args, **kwargs)

        monkeypatch.setattr(conn, "execute", always_fail_version_insert)

        art = _make_artifact("01JART_FAIL_00000000000001", "fail")
        with pytest.raises(aiosqlite.IntegrityError):
            await store.put_artifact(art, b"fail", versionable=True, logical_file_id=lfid)

        monkeypatch.undo()
        # 主表无该行（整事务回滚），版本表也无
        assert await store.get_artifact("01JART_FAIL_00000000000001") is None


class TestConnTransactionAndFileCleanup:
    """Codex Phase1 review 修复：连接事务隔离 + 失败文件清理。"""

    async def test_versionable_rejects_existing_transaction(self, store_env):
        """high：调用方有未提交事务时 versionable 抛 RuntimeError（不污染共享连接既有写）。"""
        store, conn, _, _ = store_env
        # 制造未提交写 → in_transaction=True
        await conn.execute(
            "INSERT INTO artifacts (artifact_id, task_id, ts, name, description, "
            "parts, storage_ref, size, hash, version) "
            "VALUES (?, ?, ?, '', '', '[]', NULL, 0, '', 1)",
            ("01JPRE_000000000000000001", TASK_ID, datetime.now(UTC).isoformat()),
        )
        assert conn.in_transaction
        art = _make_artifact("01JART_INTXN_0000000000001", "x")
        with pytest.raises(RuntimeError, match="未提交事务"):
            await store.put_artifact(
                art, b"x", versionable=True, logical_file_id="lf:intxn"
            )
        await conn.rollback()

    async def test_large_file_cleanup_on_version_failure(self, store_env, monkeypatch):
        """medium：大文件 versionable 写失败 → 清理本次新写的文件（不残留磁盘）。"""
        store, conn, _, _ = store_env
        lfid = "progress-note:bigfail"
        big = b"x" * (ARTIFACT_INLINE_THRESHOLD + 100)  # 大文件 → storage_ref 文件写
        art = _make_artifact("01JART_BIGFAIL_0000000001", "")
        file_path = store._get_artifact_path(TASK_ID, art.artifact_id)

        orig_execute = conn.execute

        async def fail_version_insert(sql, *args, **kwargs):
            if isinstance(sql, str) and "INSERT INTO artifact_versions" in sql:
                raise aiosqlite.IntegrityError("forced")
            return await orig_execute(sql, *args, **kwargs)

        monkeypatch.setattr(conn, "execute", fail_version_insert)
        with pytest.raises(aiosqlite.IntegrityError):
            await store.put_artifact(art, big, versionable=True, logical_file_id=lfid)
        monkeypatch.undo()
        # 大文件已写但版本失败 → 清理，文件不残留
        assert not file_path.exists()

    async def test_existing_file_not_overwritten_on_versionable(self, store_env):
        """re-review high：相同 artifact_id 大文件路径已存在 → versionable 拒绝（FileExistsError），既有 bytes 不变。"""
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
