"""US-6 Artifact 完整集成测试 -- T057

测试内容：
1. inline 文本存取
2. 大文件存取
3. hash 完整性校验
4. task_id 检索
"""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest_asyncio
from octoagent.core.models import Artifact, ArtifactPart, PartType, RequesterInfo, Task
from octoagent.core.store.artifact_store import SqliteArtifactStore
from octoagent.core.store.sqlite_init import init_db
from octoagent.core.store.task_store import SqliteTaskStore


@pytest_asyncio.fixture
async def stores(tmp_path: Path):
    conn = await aiosqlite.connect(str(tmp_path / "test.db"))
    await init_db(conn)
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    task_store = SqliteTaskStore(conn)
    artifact_store = SqliteArtifactStore(conn, artifacts_dir)

    now = datetime.now(UTC)
    task = Task(
        task_id="01JTEST_US6_00000000000001",
        created_at=now,
        updated_at=now,
        title="US-6 测试",
        requester=RequesterInfo(channel="web", sender_id="owner"),
    )
    await task_store.create_task(task)
    await conn.commit()

    yield artifact_store, conn, artifacts_dir
    await conn.close()


class TestUS6Artifact:
    """US-6: 产物存储与检索"""

    async def test_inline_round_trip(self, stores):
        """inline 文本往返"""
        store, conn, _ = stores
        content = b"Short text"
        now = datetime.now(UTC)
        artifact = Artifact(
            artifact_id="01JART_US6_INLINE_00000001",
            task_id="01JTEST_US6_00000000000001",
            ts=now,
            name="inline-test",
            parts=[ArtifactPart(type=PartType.TEXT)],
        )
        await store.put_artifact(artifact, content)
        await conn.commit()

        retrieved = await store.get_artifact_content("01JART_US6_INLINE_00000001")
        assert retrieved == content

    async def test_large_file_round_trip(self, stores):
        """大文件往返"""
        store, conn, _ = stores
        content = b"A" * 8192
        now = datetime.now(UTC)
        artifact = Artifact(
            artifact_id="01JART_US6_LARGE_000000001",
            task_id="01JTEST_US6_00000000000001",
            ts=now,
            name="large-test",
            parts=[ArtifactPart(type=PartType.FILE)],
        )
        await store.put_artifact(artifact, content)
        await conn.commit()

        retrieved = await store.get_artifact_content("01JART_US6_LARGE_000000001")
        assert retrieved == content

    async def test_hash_and_size_correct(self, stores):
        """hash 和 size 正确"""
        store, conn, _ = stores
        content = b"hash verification content"
        now = datetime.now(UTC)
        artifact = Artifact(
            artifact_id="01JART_US6_HASH_0000000001",
            task_id="01JTEST_US6_00000000000001",
            ts=now,
            name="hash-test",
            parts=[ArtifactPart(type=PartType.TEXT)],
        )
        await store.put_artifact(artifact, content)
        await conn.commit()

        meta = await store.get_artifact("01JART_US6_HASH_0000000001")
        assert meta.size == len(content)
        assert meta.hash == hashlib.sha256(content).hexdigest()

    async def test_list_by_task_id(self, stores):
        """按 task_id 检索"""
        store, conn, _ = stores
        now = datetime.now(UTC)
        for i in range(3):
            a = Artifact(
                artifact_id=f"01JART_US6_LIST_{i:015d}",
                task_id="01JTEST_US6_00000000000001",
                ts=now,
                name=f"item-{i}",
                parts=[ArtifactPart(type=PartType.TEXT)],
            )
            await store.put_artifact(a, f"content {i}".encode())
            await conn.commit()

        result = await store.list_artifacts_for_task("01JTEST_US6_00000000000001")
        assert len(result) == 3
