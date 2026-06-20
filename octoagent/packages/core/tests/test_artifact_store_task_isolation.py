"""F126 项3：SqliteArtifactStore task 隔离纵深防御测试（C5）。

覆盖：
- 传 task 命中本 task artifact（get_artifact / get_artifact_content）
- 跨 task 读返回 None（物理隔离，防越权）
- 不传 task（None）= 内部信任调用、零变更（按 id 查到）
"""

from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio
from octoagent.core.models import Artifact, ArtifactPart, PartType, RequesterInfo, Task
from octoagent.core.store.artifact_store import SqliteArtifactStore
from octoagent.core.store.sqlite_init import init_db
from octoagent.core.store.task_store import SqliteTaskStore

pytestmark = pytest.mark.asyncio

_TASK_A = "01JTASKA_0000000000000001"
_TASK_B = "01JTASKB_0000000000000001"
_ART_A = "01JARTA_00000000000000001"
_CONTENT = b"secret-of-task-A" * 8


@pytest_asyncio.fixture
async def store(tmp_path: Path):
    conn = await aiosqlite.connect(str(tmp_path / "iso.db"))
    await init_db(conn)
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    task_store = SqliteTaskStore(conn)
    artifact_store = SqliteArtifactStore(conn, artifacts_dir)
    now = datetime.now(UTC)
    for tid in (_TASK_A, _TASK_B):
        await task_store.create_task(
            Task(
                task_id=tid,
                created_at=now,
                updated_at=now,
                title=f"task {tid}",
                requester=RequesterInfo(channel="web", sender_id="owner"),
            )
        )
    artifact = Artifact(
        artifact_id=_ART_A,
        task_id=_TASK_A,
        ts=now,
        name="a-output",
        parts=[ArtifactPart(type=PartType.TEXT)],
    )
    await artifact_store.put_artifact(artifact, _CONTENT)
    await conn.commit()
    yield artifact_store
    await conn.close()


async def test_same_task_returns_artifact(store):
    art = await store.get_artifact(_ART_A, task=_TASK_A)
    assert art is not None
    content = await store.get_artifact_content(_ART_A, task=_TASK_A)
    assert content == _CONTENT


async def test_cross_task_returns_none(store):
    assert await store.get_artifact(_ART_A, task=_TASK_B) is None
    assert await store.get_artifact_content(_ART_A, task=_TASK_B) is None


async def test_no_task_param_zero_change(store):
    """不传 task（None）= 内部信任调用，按 id 查到（保既有 caller 零变更）。"""
    art = await store.get_artifact(_ART_A)
    assert art is not None
    content = await store.get_artifact_content(_ART_A)
    assert content == _CONTENT
