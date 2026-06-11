"""F119 e2e_live：F104 文件工作台端到端补全。

集成 review 缺口：F104 只有 store 层单测（独立 conn）+ route 单测（create_app），
缺 e2e_live——bootstrap 后 store_group.artifact_store 真带独立 versionable_conn 吗？
Files API 真能从 app.state.store_group 取版本、主响应真不泄漏技术字段吗？并发不串吗？

设计原则（沿用 F087 范式）：
1. 真跑 OctoHarness 全 11 段 bootstrap → 真文件 tmp DB + 独立 versionable_conn
2. 直调 store_group.artifact_store.put_artifact(versionable=True) 写多版本（progress_note 产物）
3. 手动 include files.router + ASGITransport 调两级导航 + diff（仿 test_e2e_memory_pipeline）
4. 每个 case ≥ 2 独立断言点

AC 绑定（spec §3）：
- AC-104-1 → test_file_workbench_versions_retrievable
- AC-104-2 → test_file_workbench_two_level_navigation
- AC-104-3 → test_file_workbench_diff_no_technical_field_leak
- AC-104-4 → test_file_workbench_concurrent_writes_no_version_clash
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from ulid import ULID


pytestmark = [pytest.mark.e2e_full, pytest.mark.e2e_live]


TASK_ID = "_e2e_f104_file_workbench_task"


@pytest.fixture
async def bootstrapped_harness(octo_harness_e2e: dict[str, Any]) -> dict[str, Any]:
    """真跑 OctoHarness.bootstrap 全 11 段 + 挂 files 路由（仿 test_e2e_memory_pipeline）。"""
    harness = octo_harness_e2e["harness"]
    app = octo_harness_e2e["app"]
    project_root = octo_harness_e2e["project_root"]

    fixtures_root = (
        Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "local-instance"
    )
    if (fixtures_root / "octoagent.yaml.template").exists():
        from apps.gateway.tests.e2e_live.helpers.factories import (
            copy_local_instance_template,
        )

        copy_local_instance_template(fixtures_root, project_root)

    await harness.bootstrap(app)
    harness.commit_to_app(app)

    # 手动挂 files 路由（bootstrap/commit_to_app 不挂 HTTP route；与生产 main.py 一致带
    # front_door dep，loopback/ASGITransport 自动通过）
    from fastapi import Depends

    from octoagent.gateway.deps import require_front_door_access
    from octoagent.gateway.routes import files

    protected = [Depends(require_front_door_access)]
    app.include_router(files.router, tags=["files"], dependencies=protected)

    return {"harness": harness, "app": app, "project_root": project_root}


async def _make_task(store_group: Any, task_id: str = TASK_ID) -> None:
    from octoagent.core.models import RequesterInfo, Task

    now = datetime.now(UTC)
    task = Task(
        task_id=task_id,
        created_at=now,
        updated_at=now,
        title="F104 文件工作台 e2e 任务",
        requester=RequesterInfo(channel="web", sender_id="owner"),
    )
    await store_group.task_store.create_task(task)
    await store_group.conn.commit()


async def _put_version(
    store_group: Any, logical_file_id: str, content: str, *, task_id: str = TASK_ID
) -> None:
    """写一条 versionable artifact（模拟 progress_note 的版本化产物）。"""
    from octoagent.core.models import Artifact, ArtifactPart, PartType

    art = Artifact(
        artifact_id=str(ULID()),
        task_id=task_id,
        ts=datetime.now(UTC),
        name="doc",
        parts=[ArtifactPart(type=PartType.TEXT, content=content)],
    )
    await store_group.artifact_store.put_artifact(
        art, content.encode("utf-8"), versionable=True, logical_file_id=logical_file_id
    )


# ---------------------------------------------------------------------------
# AC-104-1：版本内容可取回（非仅计数器）
# ---------------------------------------------------------------------------


async def test_file_workbench_versions_retrievable(
    bootstrapped_harness: dict[str, Any],
) -> None:
    """AC-104-1：versionable 写 3 版本 → get_current_and_previous 取回真实内容。

    断言（≥ 2 独立点）：
    1. current.content == 第 3 版内容（最新版可取回真实内容，非计数器）
    2. previous.content == 第 2 版内容（上一版可取回真实内容）
    """
    app = bootstrapped_harness["app"]
    store_group = app.state.store_group
    await _make_task(store_group)

    lfid = "progress-note:retrievable"
    contents = ["第一版内容 alpha", "第二版内容 beta", "第三版内容 gamma"]
    for c in contents:
        await _put_version(store_group, lfid, c)

    current, previous = await store_group.artifact_store.get_current_and_previous(
        TASK_ID, lfid
    )

    assert current is not None, "AC-104-1: 当前版应存在"
    assert current.content == contents[2], (
        f"AC-104-1: current.content 应为第 3 版真实内容 {contents[2]!r}，"
        f"实际 {current.content!r}（若为版本号则说明退化为计数器）"
    )
    assert previous is not None, "AC-104-1: 上一版应存在（已写 3 版）"
    assert previous.content == contents[1], (
        f"AC-104-1: previous.content 应为第 2 版真实内容 {contents[1]!r}，"
        f"实际 {previous.content!r}"
    )


# ---------------------------------------------------------------------------
# AC-104-2：Files API 两级导航
# ---------------------------------------------------------------------------


async def test_file_workbench_two_level_navigation(
    bootstrapped_harness: dict[str, Any],
) -> None:
    """AC-104-2：/api/files/tasks 含 task；/logical-files 只返回 version_count≥2。

    断言（≥ 2 独立点）：
    1. GET /api/files/tasks 含本 task_id
    2. GET /api/files/tasks/{id}/logical-files 含多版本 lfid、不含单版本 lfid
    """
    app = bootstrapped_harness["app"]
    store_group = app.state.store_group
    await _make_task(store_group)

    multi_lfid = "progress-note:multi"
    single_lfid = "progress-note:single"
    await _put_version(store_group, multi_lfid, "多版本 v1")
    await _put_version(store_group, multi_lfid, "多版本 v2")
    await _put_version(store_group, single_lfid, "单版本 only")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://e2e-test") as client:
        resp_tasks = await client.get("/api/files/tasks")
        resp_files = await client.get(f"/api/files/tasks/{TASK_ID}/logical-files")

    assert resp_tasks.status_code == 200, (
        f"AC-104-2: /api/files/tasks 应 200，实际 {resp_tasks.status_code}: {resp_tasks.text}"
    )
    task_ids = [t["task_id"] for t in resp_tasks.json().get("tasks", [])]
    assert TASK_ID in task_ids, (
        f"AC-104-2: /api/files/tasks 应含本 task（有多版本文件），实际 {task_ids}"
    )

    assert resp_files.status_code == 200, (
        f"AC-104-2: /logical-files 应 200，实际 {resp_files.status_code}: {resp_files.text}"
    )
    files = resp_files.json().get("files", [])
    lfids = {f["logical_file_id"] for f in files}
    assert multi_lfid in lfids, (
        f"AC-104-2: 多版本逻辑文件 {multi_lfid} 应出现，实际 {lfids}"
    )
    assert single_lfid not in lfids, (
        f"AC-104-2: 单版本逻辑文件 {single_lfid} 不应出现（SD-4 隐藏单版本），实际 {lfids}"
    )


# ---------------------------------------------------------------------------
# AC-104-3：diff 无技术字段泄漏
# ---------------------------------------------------------------------------


async def test_file_workbench_diff_no_technical_field_leak(
    bootstrapped_harness: dict[str, Any],
) -> None:
    """AC-104-3：diff 响应含 current+previous 内容，且不泄漏技术字段。

    断言（≥ 2 独立点）：
    1. current/previous content 正确
    2. 响应 JSON 原文不含 artifact_id / storage_ref / hash（SC-004 无泄漏）
    """
    app = bootstrapped_harness["app"]
    store_group = app.state.store_group
    await _make_task(store_group)

    lfid = "progress-note:diff-target"
    await _put_version(store_group, lfid, "旧版本\n第二行旧")
    await _put_version(store_group, lfid, "新版本\n第二行新")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://e2e-test") as client:
        resp = await client.get(
            f"/api/files/tasks/{TASK_ID}/diff",
            params={"logical_file_id": lfid},
        )

    assert resp.status_code == 200, (
        f"AC-104-3: diff 应 200，实际 {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert body.get("current", {}).get("content") == "新版本\n第二行新", (
        f"AC-104-3: diff current.content 应为最新版，实际 {body.get('current')}"
    )
    assert body.get("previous", {}).get("content") == "旧版本\n第二行旧", (
        f"AC-104-3: diff previous.content 应为上一版，实际 {body.get('previous')}"
    )

    raw = resp.text
    for forbidden in ("artifact_id", "storage_ref", "hash"):
        assert forbidden not in raw, (
            f"AC-104-3: diff 主响应不应泄漏技术字段 {forbidden!r}（SC-004），"
            f"响应原文: {raw[:300]!r}"
        )


# ---------------------------------------------------------------------------
# AC-104-4：并发 versionable 写不串/不丢版本
# ---------------------------------------------------------------------------


async def test_file_workbench_concurrent_writes_no_version_clash(
    bootstrapped_harness: dict[str, Any],
) -> None:
    """AC-104-4：并发 N 个 versionable 写同一 logical_file → 版本号 1..N 连续唯一。

    _write_lock 串行化 + UNIQUE(task_id, logical_file_id, version_no) 约束保证
    并发不串/不丢（连接级写隔离的端到端验证）。

    断言（≥ 2 独立点）：
    1. list_versions 返回 N 条
    2. 版本号集合 == {1..N}（连续唯一，无重复无缺口）
    """
    app = bootstrapped_harness["app"]
    store_group = app.state.store_group
    await _make_task(store_group)

    lfid = "progress-note:concurrent"
    n = 5
    await asyncio.gather(
        *(_put_version(store_group, lfid, f"并发版本 {i}") for i in range(n))
    )

    versions = await store_group.artifact_store.list_versions(TASK_ID, lfid)
    assert len(versions) == n, (
        f"AC-104-4: 并发 {n} 写应产 {n} 版本（不丢），实际 {len(versions)}"
    )
    version_nos = sorted(v.version_no for v in versions)
    assert version_nos == list(range(1, n + 1)), (
        f"AC-104-4: 版本号应为 1..{n} 连续唯一（不串），实际 {version_nos}"
    )
