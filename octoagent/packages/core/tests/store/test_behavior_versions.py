"""F107 W1-A：behavior_versions 表 + SqliteBehaviorVersionStore 测试。

覆盖：
- record-after 版本号单调唯一 / 首版 baseline 捕获 / 任意两版 / 当前版+上一版 / 列文件。
- 未注入隔离连接时 record raise（不静默污染主连接）。
- 共用写锁：behavior ∥ artifact 并发写不触发 "transaction within transaction"（FR-W1-2c）。
- 默认路径 0 regression（不 record 时版本表 0 行）。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio
from octoagent.core.models import Artifact, ArtifactPart, PartType, RequesterInfo, Task
from octoagent.core.models.behavior_version import BehaviorFileKey
from octoagent.core.store import create_store_group
from octoagent.core.store.behavior_version_store import SqliteBehaviorVersionStore
from octoagent.core.store.connection import apply_write_connection_pragmas
from octoagent.core.store.sqlite_init import init_db
from octoagent.core.store.task_store import SqliteTaskStore


def _key(
    file_id: str = "USER.md",
    *,
    scope: str = "system_shared",
    agent: str = "",
    project: str = "",
) -> BehaviorFileKey:
    return BehaviorFileKey(
        scope=scope, agent_slug=agent, project_slug=project, file_id=file_id
    )


@pytest_asyncio.fixture
async def bstore_env(tmp_path: Path):
    """behavior 版本 store + 独立 versionable_conn（复刻生产双连接 + 共享写锁注入）。"""
    db_path = str(tmp_path / "test.db")
    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
    await init_db(conn)
    versionable_conn = await aiosqlite.connect(db_path, isolation_level=None)
    versionable_conn.row_factory = aiosqlite.Row
    await apply_write_connection_pragmas(versionable_conn)
    lock = asyncio.Lock()
    store = SqliteBehaviorVersionStore(
        conn, versionable_conn=versionable_conn, write_lock=lock
    )
    yield store, conn
    await conn.close()
    await versionable_conn.close()


@pytest.mark.asyncio
async def test_record_after_monotonic(bstore_env):
    """record-after：连续记录 → 版本号单调递增且唯一。"""
    store, conn = bstore_env
    key = _key()
    assert await store.record_version(key, "A") == 1
    assert await store.record_version(key, "B") == 2
    assert await store.record_version(key, "C") == 3
    metas = await store.list_versions(key)
    assert [m.version_no for m in metas] == [3, 2, 1]  # DESC


@pytest.mark.asyncio
async def test_first_record_with_baseline(bstore_env):
    """首版 baseline：首次记录且有 baseline_content → 先记 baseline(v1) 再记新内容(v2)。"""
    store, conn = bstore_env
    key = _key()
    # 首次写入：盘上旧内容 "OLD" 作 baseline，新内容 "NEW"
    last = await store.record_version(key, "NEW", baseline_content="OLD")
    assert last == 2
    v1, v2 = await store.get_two_versions(key, 1, 2)
    assert v1 is not None and v1.content == "OLD"
    assert v2 is not None and v2.content == "NEW"
    # 后续写入不再插 baseline（已有版本）
    assert await store.record_version(key, "NEWER", baseline_content="IGNORED") == 3
    metas = await store.list_versions(key)
    assert len(metas) == 3


@pytest.mark.asyncio
async def test_baseline_skipped_when_none(bstore_env):
    """无 baseline_content（agent 新建文件，盘上无旧内容）→ 首版即 v1。"""
    store, _ = bstore_env
    key = _key("KNOWLEDGE.md", scope="project_shared", project="demo")
    assert await store.record_version(key, "created") == 1
    metas = await store.list_versions(key)
    assert len(metas) == 1 and metas[0].version_no == 1


@pytest.mark.asyncio
async def test_get_two_versions_arbitrary(bstore_env):
    """任意两版本（非相邻）对比。"""
    store, _ = bstore_env
    key = _key()
    for c in ("A", "B", "C", "D"):
        await store.record_version(key, c)
    v1, v4 = await store.get_two_versions(key, 1, 4)
    assert v1.content == "A" and v4.content == "D"
    missing, v2 = await store.get_two_versions(key, 99, 2)
    assert missing is None and v2.content == "B"


@pytest.mark.asyncio
async def test_get_latest_two(bstore_env):
    """当前版 + 上一版；单版本时上一版 None（首版无对比）。"""
    store, _ = bstore_env
    key = _key()
    await store.record_version(key, "only")
    cur, prev = await store.get_latest_two(key)
    assert cur.content == "only" and prev is None
    await store.record_version(key, "second")
    cur, prev = await store.get_latest_two(key)
    assert cur.content == "second" and prev.content == "only"


@pytest.mark.asyncio
async def test_scope_discriminator_no_collision(bstore_env):
    """同 file_id 不同 scope 是不同逻辑文件（key 含 scope discriminator）。"""
    store, _ = bstore_env
    sys_key = _key("USER.md", scope="system_shared")
    proj_key = _key("USER.md", scope="project_shared", project="demo")
    await store.record_version(sys_key, "sys")
    await store.record_version(proj_key, "proj")
    assert len(await store.list_versions(sys_key)) == 1
    assert len(await store.list_versions(proj_key)) == 1
    cur_sys, _ = await store.get_latest_two(sys_key)
    assert cur_sys.content == "sys"


@pytest.mark.asyncio
async def test_list_versioned_files(bstore_env):
    """列有版本历史的 behavior 文件 + scope 过滤。"""
    store, _ = bstore_env
    await store.record_version(_key("USER.md"), "u")
    await store.record_version(_key("AGENTS.md"), "a")
    await store.record_version(_key("IDENTITY.md", scope="agent_private", agent="octo"), "i")
    all_files = await store.list_versioned_behavior_files()
    assert {f.file_id for f in all_files} == {"USER.md", "AGENTS.md", "IDENTITY.md"}
    sys_only = await store.list_versioned_behavior_files(scope="system_shared")
    assert {f.file_id for f in sys_only} == {"USER.md", "AGENTS.md"}


@pytest.mark.asyncio
async def test_record_requires_isolated_conn(tmp_path: Path):
    """未注入独立 versionable_conn（退化主连接）→ record raise，不静默污染主连接。"""
    db_path = str(tmp_path / "test.db")
    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
    await init_db(conn)
    store = SqliteBehaviorVersionStore(conn)  # 无 versionable_conn → 退化
    with pytest.raises(RuntimeError, match="独立隔离连接"):
        await store.record_version(_key(), "x")
    await conn.close()


@pytest.mark.asyncio
async def test_empty_file_id_raises(bstore_env):
    store, _ = bstore_env
    with pytest.raises(ValueError, match="file_id 必须非空"):
        await store.record_version(_key(file_id=""), "x")


@pytest.mark.asyncio
async def test_shared_lock_behavior_artifact_concurrent(tmp_path: Path):
    """FR-W1-2c：behavior ∥ artifact versionable 写并发不触发 "transaction within transaction"。

    StoreGroup 给两 store 注入同一共享写锁 + 同一 versionable_conn；并发 gather 应全成功。
    """
    sg = await create_store_group(
        str(tmp_path / "test.db"), str(tmp_path / "artifacts")
    )
    # artifact versionable 写需先有 task（外键）
    task_store: SqliteTaskStore = sg.task_store
    now = datetime.now(UTC)
    await task_store.create_task(
        Task(
            task_id="01JTEST_BV_ARTIFACT_000001",
            created_at=now,
            updated_at=now,
            title="并发测试",
            requester=RequesterInfo(channel="web", sender_id="owner"),
        )
    )
    await sg.conn.commit()

    def _artifact(i: int) -> Artifact:
        return Artifact(
            artifact_id=f"01JTEST_BV_ART_{i:020d}",
            task_id="01JTEST_BV_ARTIFACT_000001",
            name="doc",
            ts=datetime.now(UTC),
            parts=[ArtifactPart(type=PartType.TEXT, content=f"art{i}")],
        )

    async def write_behavior() -> None:
        for c in ("A", "B", "C"):
            await sg.behavior_version_store.record_version(_key(), c)

    async def write_artifact() -> None:
        for i in range(3):
            await sg.artifact_store.put_artifact(
                _artifact(i), versionable=True, logical_file_id="progress-note:s1"
            )

    # 并发：共享锁串行化两条 versionable 写，不应抛 "transaction within transaction"
    await asyncio.gather(write_behavior(), write_artifact())

    assert len(await sg.behavior_version_store.list_versions(_key())) == 3
    await sg.close()
