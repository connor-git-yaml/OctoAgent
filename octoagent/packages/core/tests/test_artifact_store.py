"""ArtifactStore 单元测试 -- T049

测试内容：
1. inline 文本存取（< 4KB）
2. 大文件存取（>= 4KB）
3. hash 完整性校验
"""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest_asyncio
from octoagent.core.models import Artifact, ArtifactPart, PartType, RequesterInfo, Task
from octoagent.core.store.artifact_store import SqliteArtifactStore, compute_hash_and_size
from octoagent.core.store.sqlite_init import init_db
from octoagent.core.store.task_store import SqliteTaskStore


@pytest_asyncio.fixture
async def stores(tmp_path: Path):
    """提供已初始化的 ArtifactStore"""
    conn = await aiosqlite.connect(str(tmp_path / "test.db"))
    await init_db(conn)
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    task_store = SqliteTaskStore(conn)
    artifact_store = SqliteArtifactStore(conn, artifacts_dir)

    # 创建测试任务
    now = datetime.now(UTC)
    task = Task(
        task_id="01JTEST_ART_00000000000001",
        created_at=now,
        updated_at=now,
        title="Artifact 测试",
        requester=RequesterInfo(channel="web", sender_id="owner"),
    )
    await task_store.create_task(task)
    await conn.commit()

    yield artifact_store, conn, artifacts_dir
    await conn.close()


class TestArtifactStore:
    """ArtifactStore 测试"""

    async def test_inline_text_store_and_retrieve(self, stores):
        """小文本 inline 存储和检索"""
        artifact_store, conn, _ = stores
        now = datetime.now(UTC)

        content = b"Hello, OctoAgent!"
        artifact = Artifact(
            artifact_id="01JART_INLINE_000000000001",
            task_id="01JTEST_ART_00000000000001",
            ts=now,
            name="small-text",
            parts=[ArtifactPart(type=PartType.TEXT)],
        )
        await artifact_store.put_artifact(artifact, content)
        await conn.commit()

        # 检索
        retrieved = await artifact_store.get_artifact("01JART_INLINE_000000000001")
        assert retrieved is not None
        assert retrieved.name == "small-text"
        assert retrieved.size == len(content)
        assert retrieved.hash == hashlib.sha256(content).hexdigest()
        assert retrieved.storage_ref is None  # inline 不需要文件引用
        assert retrieved.parts[0].content == "Hello, OctoAgent!"

    async def test_large_file_store_and_retrieve(self, stores):
        """大文件存储和检索"""
        artifact_store, conn, artifacts_dir = stores
        now = datetime.now(UTC)

        # 生成 > 4KB 的内容
        content = b"x" * 5000
        artifact = Artifact(
            artifact_id="01JART_LARGE_000000000001",
            task_id="01JTEST_ART_00000000000001",
            ts=now,
            name="large-output",
            parts=[ArtifactPart(type=PartType.FILE)],
        )
        await artifact_store.put_artifact(artifact, content)
        await conn.commit()

        # 检索
        retrieved = await artifact_store.get_artifact("01JART_LARGE_000000000001")
        assert retrieved is not None
        assert retrieved.size == 5000
        assert retrieved.storage_ref is not None
        assert retrieved.hash == hashlib.sha256(content).hexdigest()

        # 文件内容检索
        retrieved_content = await artifact_store.get_artifact_content(
            "01JART_LARGE_000000000001"
        )
        assert retrieved_content == content

    async def test_list_artifacts_for_task(self, stores):
        """按 task_id 查询 Artifact 列表"""
        artifact_store, conn, _ = stores
        now = datetime.now(UTC)

        for i in range(3):
            artifact = Artifact(
                artifact_id=f"01JART_LIST_{i:020d}",
                task_id="01JTEST_ART_00000000000001",
                ts=now,
                name=f"artifact-{i}",
                parts=[ArtifactPart(type=PartType.TEXT)],
            )
            await artifact_store.put_artifact(artifact, f"content {i}".encode())
            await conn.commit()

        artifacts = await artifact_store.list_artifacts_for_task(
            "01JTEST_ART_00000000000001"
        )
        assert len(artifacts) == 3

    async def test_hash_integrity(self, stores):
        """hash 完整性校验"""
        content = b"test content for hash"
        expected_hash, expected_size = compute_hash_and_size(content)

        assert expected_size == len(content)
        assert expected_hash == hashlib.sha256(content).hexdigest()

    async def test_get_nonexistent_artifact(self, stores):
        """查询不存在的 Artifact 返回 None"""
        artifact_store, _, _ = stores
        result = await artifact_store.get_artifact("nonexistent")
        assert result is None

    async def test_get_content_nonexistent(self, stores):
        """获取不存在的 Artifact 内容返回 None"""
        artifact_store, _, _ = stores
        result = await artifact_store.get_artifact_content("nonexistent")
        assert result is None
