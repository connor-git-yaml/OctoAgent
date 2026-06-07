"""F104 Phase 2 T2.5：Files API endpoint 集成测。

覆盖：
- 4 endpoint 响应结构正确
- /logical-files 只返回 version>=2（SD-4/FR-012）
- front-door 鉴权生效（bearer 模式无 token → 401）
- 主响应字段不含 artifact_id/storage_ref/hash（SC-004/FR-017）
- logical_file_id 含 ':' 走 query 参数解析正确
- logical_file_id 含 '/'（progress-note:phase/1）走 query 参数解析正确（Codex Phase2 medium）
- storage_ref 文件被删 → diff endpoint availability='unavailable' 不 500（FR-010）
- 超大 storage_ref 文件 → 读前拦截 oversize=True + content=None，响应体不含大内容
  （Codex Phase2 high / FR-019/SC-005）
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from octoagent.core.models import Artifact, ArtifactPart, PartType, RequesterInfo, Task
from ulid import ULID

TASK_ID = "01JFILES_TASK_0000000000001"
LFID_MULTI = "progress-note:step-3"  # 含 ':' + '-'
LFID_SINGLE = "progress-note:step-9"
LFID_BIG = "progress-note:big-file"
LFID_SLASH = "progress-note:phase/1"  # 含 '/'（step_id 未禁斜杠，Codex Phase2 medium）
LFID_OVERSIZE = "progress-note:oversize-file"  # > 256KB，触发读前 oversize 拦截

# diff 读前拦截阈值（与 routes/files.py DIFF_OVERSIZE_THRESHOLD 一致）。
_OVERSIZE_THRESHOLD = 256 * 1024
# oversize 内容特征串：用于断言响应体不含完整大内容。
_OVERSIZE_MARKER = "OVERSIZE_CONTENT_MARKER"


def _configure_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OCTOAGENT_DB_PATH", str(tmp_path / "data" / "sqlite" / "test.db"))
    monkeypatch.setenv("OCTOAGENT_ARTIFACTS_DIR", str(tmp_path / "data" / "artifacts"))
    monkeypatch.setenv("OCTOAGENT_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("OCTOAGENT_LLM_MODE", "echo")
    monkeypatch.setenv("LOGFIRE_SEND_TO_LOGFIRE", "false")


@pytest_asyncio.fixture
async def files_app(tmp_path: Path, monkeypatch):
    _configure_env(tmp_path, monkeypatch)
    from octoagent.gateway.main import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        await _seed_versions(app)
        yield app


@pytest_asyncio.fixture
async def files_client(files_app) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=files_app),
        base_url="http://test",
    ) as client:
        yield client


async def _seed_versions(app) -> None:
    """写入：多版本逻辑文件 + 单版本逻辑文件 + 大文件多版本。"""
    store_group = app.state.store_group
    now = datetime.now(UTC)
    task = Task(
        task_id=TASK_ID,
        created_at=now,
        updated_at=now,
        title="文件版本测试任务",
        requester=RequesterInfo(channel="web", sender_id="owner"),
    )
    await store_group.task_store.create_task(task)
    await store_group.conn.commit()

    artifact_store = store_group.artifact_store

    async def _put(lfid: str, content: str) -> None:
        art = Artifact(
            artifact_id=str(ULID()),
            task_id=TASK_ID,
            ts=datetime.now(UTC),
            name="doc",
            parts=[ArtifactPart(type=PartType.TEXT, content=content)],
        )
        await artifact_store.put_artifact(
            art, content.encode(), versionable=True, logical_file_id=lfid
        )

    # 多版本（2 版）
    await _put(LFID_MULTI, "第一行\n旧内容")
    await _put(LFID_MULTI, "第一行\n新内容")
    # 单版本（应被过滤）
    await _put(LFID_SINGLE, "只有一版")
    # 大文件多版本（storage_ref 路径）
    await _put(LFID_BIG, "大" * 3000)
    await _put(LFID_BIG, "大" * 3001)
    # 含 '/' 的 logical_file_id 多版本（query 参数路由解析，Codex Phase2 medium）
    await _put(LFID_SLASH, "斜杠键\n旧")
    await _put(LFID_SLASH, "斜杠键\n新")
    # 超大文件多版本：每版 > 256KB，触发读前 oversize 拦截（Codex Phase2 high）
    _oversize_text = _OVERSIZE_MARKER * (_OVERSIZE_THRESHOLD // len(_OVERSIZE_MARKER) + 16)
    assert len(_oversize_text.encode("utf-8")) > _OVERSIZE_THRESHOLD
    await _put(LFID_OVERSIZE, _oversize_text + "v1")
    await _put(LFID_OVERSIZE, _oversize_text + "v2")


class TestFilesEndpoints:
    async def test_list_tasks(self, files_client):
        resp = await files_client.get("/api/files/tasks")
        assert resp.status_code == 200
        data = resp.json()
        task_ids = {t["task_id"] for t in data["tasks"]}
        assert TASK_ID in task_ids
        item = next(t for t in data["tasks"] if t["task_id"] == TASK_ID)
        assert item["title"] == "文件版本测试任务"

    async def test_list_logical_files_filters_single_version(self, files_client):
        """SD-4/FR-012：只返回 version>=2，单版本被过滤。"""
        resp = await files_client.get(f"/api/files/tasks/{TASK_ID}/logical-files")
        assert resp.status_code == 200
        files = resp.json()["files"]
        lfids = {f["logical_file_id"] for f in files}
        assert LFID_MULTI in lfids
        assert LFID_BIG in lfids
        assert LFID_SINGLE not in lfids  # 单版本完全隐藏（SC-006）
        # 友好命名 + version_count
        multi = next(f for f in files if f["logical_file_id"] == LFID_MULTI)
        assert multi["display_name"] == "进度笔记"
        assert multi["version_count"] == 2

    async def test_logical_files_no_technical_fields(self, files_client):
        """SC-004/FR-017：主响应不含 artifact_id/storage_ref/hash。"""
        resp = await files_client.get(f"/api/files/tasks/{TASK_ID}/logical-files")
        body = resp.text
        assert "artifact_id" not in body
        assert "storage_ref" not in body
        assert "hash" not in body

    async def test_diff_contains_current_and_previous(self, files_client):
        """FR-007/FR-013：diff 返回 current + previous 内容；含 ':' query 参数解析正确。"""
        resp = await files_client.get(
            f"/api/files/tasks/{TASK_ID}/diff",
            params={"logical_file_id": LFID_MULTI},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["current"]["version_no"] == 2
        assert data["current"]["content"] == "第一行\n新内容"
        assert data["previous"]["version_no"] == 1
        assert data["previous"]["content"] == "第一行\n旧内容"
        assert data["binary"] is False
        assert data["oversize"] is False

    async def test_diff_no_technical_fields(self, files_client):
        """SC-004/FR-017：diff 主响应不含 artifact_id/storage_ref/hash。"""
        resp = await files_client.get(
            f"/api/files/tasks/{TASK_ID}/diff",
            params={"logical_file_id": LFID_MULTI},
        )
        body = resp.text
        assert "artifact_id" not in body
        assert "storage_ref" not in body
        assert "hash" not in body

    async def test_logical_files_lists_slash_key(self, files_client):
        """Codex Phase2 medium：含 '/' 的 logical_file_id 出现在 /logical-files 列表。"""
        resp = await files_client.get(f"/api/files/tasks/{TASK_ID}/logical-files")
        assert resp.status_code == 200
        lfids = {f["logical_file_id"] for f in resp.json()["files"]}
        assert LFID_SLASH in lfids

    async def test_diff_slash_logical_file_id_via_query(self, files_client):
        """Codex Phase2 medium：含 '/' 的 logical_file_id 走 query 参数能正确匹配 diff。"""
        resp = await files_client.get(
            f"/api/files/tasks/{TASK_ID}/diff",
            params={"logical_file_id": LFID_SLASH},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["current"]["version_no"] == 2
        assert data["current"]["content"] == "斜杠键\n新"
        assert data["previous"]["version_no"] == 1
        assert data["previous"]["content"] == "斜杠键\n旧"

    async def test_versions_slash_logical_file_id_via_query(self, files_client):
        """Codex Phase2 medium：含 '/' 的 logical_file_id 走 query 参数能正确匹配 versions。"""
        resp = await files_client.get(
            f"/api/files/tasks/{TASK_ID}/versions",
            params={"logical_file_id": LFID_SLASH},
        )
        assert resp.status_code == 200
        versions = resp.json()["versions"]
        assert [v["version_no"] for v in versions] == [2, 1]

    async def test_diff_oversize_blocked_before_read(self, files_client):
        """Codex Phase2 high / FR-019/SC-005：超大文件读前拦截。

        oversize=True + content=None，且响应体不含完整大内容（不 read_bytes 超大文件）。
        """
        resp = await files_client.get(
            f"/api/files/tasks/{TASK_ID}/diff",
            params={"logical_file_id": LFID_OVERSIZE},
        )
        assert resp.status_code == 200
        data = resp.json()
        # 读前拦截：内容存在但因超大省略
        assert data["oversize"] is True
        assert data["current"]["oversize"] is True
        assert data["current"]["content"] is None
        assert data["current"]["availability"] == "available"
        assert data["previous"]["oversize"] is True
        assert data["previous"]["content"] is None
        # binary 不应被 oversize 误判（content=None + available 但 oversize 已排除）
        assert data["binary"] is False
        # 响应体不含完整大内容：不含特征串，且体积远小于原内容
        body = resp.text
        assert _OVERSIZE_MARKER not in body
        assert len(body) < _OVERSIZE_THRESHOLD

    async def test_diff_storage_ref_deleted_unavailable_not_500(self, files_app):
        """FR-010：storage_ref 文件被删 → availability='unavailable'，不 500。"""
        # 删除大文件底层文件
        store_group = files_app.state.store_group
        cursor = await store_group.conn.execute(
            "SELECT storage_ref FROM artifact_versions WHERE logical_file_id = ? "
            "AND storage_ref IS NOT NULL",
            (LFID_BIG,),
        )
        rows = await cursor.fetchall()
        for row in rows:
            p = Path(row[0])
            if p.exists():
                p.unlink()

        async with AsyncClient(
            transport=ASGITransport(app=files_app), base_url="http://test"
        ) as client:
            resp = await client.get(
                f"/api/files/tasks/{TASK_ID}/diff",
                params={"logical_file_id": LFID_BIG},
            )
        assert resp.status_code == 200  # 不 500
        data = resp.json()
        assert data["current"]["availability"] == "unavailable"
        assert data["current"]["content"] is None

    async def test_diff_oversize_storage_ref_deleted_unavailable_not_oversize(
        self, files_app
    ):
        """Codex Phase2 re-review high：超大 storage_ref 文件被删 → unavailable 优先于 oversize。

        FR-010 优先于 oversize：文件存在检查在 oversize 拦截之前，已被清理的超大文件
        endpoint 应返回 availability='unavailable' + oversize=False，不误报 available+oversize。
        """
        # 删除超大文件（>256KB → storage_ref）的底层文件
        store_group = files_app.state.store_group
        cursor = await store_group.conn.execute(
            "SELECT storage_ref FROM artifact_versions WHERE logical_file_id = ? "
            "AND storage_ref IS NOT NULL",
            (LFID_OVERSIZE,),
        )
        rows = await cursor.fetchall()
        assert rows  # 确认确实是 storage_ref 路径
        for row in rows:
            p = Path(row[0])
            if p.exists():
                p.unlink()

        async with AsyncClient(
            transport=ASGITransport(app=files_app), base_url="http://test"
        ) as client:
            resp = await client.get(
                f"/api/files/tasks/{TASK_ID}/diff",
                params={"logical_file_id": LFID_OVERSIZE},
            )
        assert resp.status_code == 200
        data = resp.json()
        # FR-010 优先：文件被清理 → unavailable，不误报 oversize
        assert data["current"]["availability"] == "unavailable"
        assert data["current"]["oversize"] is False
        assert data["current"]["content"] is None

    async def test_versions_endpoint_exposes_technical_fields(self, files_client):
        """Advanced /versions endpoint 含技术字段（hash/size/storage_kind）。"""
        resp = await files_client.get(
            f"/api/files/tasks/{TASK_ID}/versions",
            params={"logical_file_id": LFID_MULTI},
        )
        assert resp.status_code == 200
        versions = resp.json()["versions"]
        assert [v["version_no"] for v in versions] == [2, 1]
        assert all(v["hash"] for v in versions)
        assert all(v["storage_kind"] == "inline" for v in versions)


class TestFilesFrontDoorAuth:
    async def test_bearer_mode_without_token_rejected(self, tmp_path, monkeypatch):
        """front-door bearer 模式无 token → 401（路由级鉴权生效）。"""
        _configure_env(tmp_path, monkeypatch)
        monkeypatch.setenv("OCTOAGENT_FRONTDOOR_MODE", "bearer")
        monkeypatch.setenv("OCTOAGENT_FRONTDOOR_TOKEN_ENV", "FILES_TEST_TOKEN")
        monkeypatch.setenv("FILES_TEST_TOKEN", "secret-token-xyz")

        from octoagent.gateway.main import create_app

        app = create_app()
        async with (
            app.router.lifespan_context(app),
            AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client,
        ):
            resp = await client.get("/api/files/tasks")
            assert resp.status_code == 401
            # 带正确 token 放行
            resp2 = await client.get(
                "/api/files/tasks",
                headers={"Authorization": "Bearer secret-token-xyz"},
            )
            assert resp2.status_code == 200
