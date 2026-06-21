"""F107 W1-B/W1-E：behavior 版本 key 派生 + record_behavior_version helper 测试。

- behavior_version_key_for：scope 路由 + 按 scope 归零无关字段（同物理文件 → 唯一 key，MED-4）。
- behavior_version_key_from_path：从实际 resolved 路径派生写 key（Opus H1）；写 key 与读 key 一致。
- record_behavior_version：path 派生 key + record-after + baseline + best-effort 事件 + 不阻断。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from octoagent.core.behavior_workspace import (
    behavior_version_key_for,
    behavior_version_key_from_path,
)
from octoagent.core.models import RequesterInfo, Task
from octoagent.core.store import create_store_group
from octoagent.gateway.services.behavior_versioning import record_behavior_version

# ---- key 派生（MED-4） ----


def test_key_routing_system_shared():
    k = behavior_version_key_for("USER.md", agent_slug="octo", project_slug="demo")
    # SHARED → system_shared，agent_slug/project_slug 归零（全局唯一文件，不受上下文噪声裂解）
    assert k.scope == "system_shared" and k.agent_slug == "" and k.project_slug == ""
    assert k.file_id == "USER.md"


def test_key_routing_agent_private():
    k = behavior_version_key_for("IDENTITY.md", agent_slug="octo", project_slug="demo")
    assert k.scope == "agent_private" and k.agent_slug == "octo" and k.project_slug == ""


def test_key_routing_project_shared():
    k = behavior_version_key_for("PROJECT.md", agent_slug="octo", project_slug="demo")
    assert k.scope == "project_shared" and k.project_slug == "demo" and k.agent_slug == ""


def test_key_same_file_one_key_regardless_of_context():
    """MED-4：同一物理文件（USER.md）在不同 agent/project 上下文 → 同一 key（不裂解）。"""
    a = behavior_version_key_for("USER.md", agent_slug="a1", project_slug="p1")
    b = behavior_version_key_for("USER.md", agent_slug="a2", project_slug="p2")
    assert a == b


def test_key_unknown_file_raises():
    with pytest.raises(ValueError, match="未知的 file_id"):
        behavior_version_key_for("NOPE.md")


# ---- path 派生 key（Opus H1：写 key 从实际路径，与前端读 key 一致） ----


def test_path_key_system_shared(tmp_path: Path):
    resolved = tmp_path / "behavior" / "system" / "USER.md"
    k = behavior_version_key_from_path(tmp_path, resolved)
    assert k.scope == "system_shared" and k.file_id == "USER.md"
    assert k == behavior_version_key_for("USER.md")  # 写==读


def test_path_key_agent_private_matches_read_key(tmp_path: Path):
    """H1 核心：自定义 Worker 的 AGENT_PRIVATE 文件，写 key（路径派生）== 读 key。

    前端 deriveAgentSlug 从 behavior/agents/<slug>/ 取 slug → behavior_version_key_for(
    file_id, agent_slug=<slug>)；写侧从同一路径段派生 → 逐字一致，历史命中（不再空）。
    """
    resolved = tmp_path / "behavior" / "agents" / "research-bot" / "IDENTITY.md"
    write_key = behavior_version_key_from_path(tmp_path, resolved)
    read_key = behavior_version_key_for("IDENTITY.md", agent_slug="research-bot")
    assert write_key == read_key
    assert write_key.scope == "agent_private"
    assert write_key.agent_slug == "research-bot"


def test_path_key_project_shared(tmp_path: Path):
    resolved = tmp_path / "projects" / "demo" / "behavior" / "PROJECT.md"
    k = behavior_version_key_from_path(tmp_path, resolved)
    assert k.scope == "project_shared" and k.project_slug == "demo"
    assert k == behavior_version_key_for("PROJECT.md", project_slug="demo")


# ---- record_behavior_version helper（path 派生签名） ----


@pytest_asyncio.fixture
async def sg_env(tmp_path: Path):
    sg = await create_store_group(str(tmp_path / "t.db"), str(tmp_path / "artifacts"))
    now = datetime.now(UTC)
    await sg.task_store.create_task(
        Task(
            task_id="01JTEST_CAP_0000000000001",
            created_at=now,
            updated_at=now,
            title="capture",
            requester=RequesterInfo(channel="web", sender_id="owner"),
        )
    )
    await sg.conn.commit()
    yield sg
    await sg.close()


async def _count_events(sg, event_type: str) -> int:
    cur = await sg.conn.execute(
        "SELECT COUNT(*) FROM events WHERE type = ?", (event_type,)
    )
    row = await cur.fetchone()
    return int(row[0]) if row else 0


@pytest.mark.asyncio
async def test_record_helper_records_with_baseline_and_event(sg_env, tmp_path: Path):
    """首次记录（有 baseline）→ 2 版 + emit BEHAVIOR_VERSION_RECORDED。"""
    sg = sg_env
    await record_behavior_version(
        stores=sg,
        project_root=tmp_path,
        resolved_path=tmp_path / "behavior" / "system" / "USER.md",
        new_content="NEW",
        old_content="OLD",
        task_id="01JTEST_CAP_0000000000001",
        source="llm_tool",
    )
    key = behavior_version_key_for("USER.md")
    metas = await sg.behavior_version_store.list_versions(key)
    assert [m.version_no for m in metas] == [2, 1]
    v1, v2 = await sg.behavior_version_store.get_two_versions(key, 1, 2)
    assert v1.content == "OLD" and v2.content == "NEW"
    assert await _count_events(sg, "BEHAVIOR_VERSION_RECORDED") == 1


@pytest.mark.asyncio
async def test_record_helper_no_task_records_but_skips_event(sg_env, tmp_path: Path):
    """无 task_id（control_plane 路径）→ 版本仍记录，事件跳过。"""
    sg = sg_env
    await record_behavior_version(
        stores=sg,
        project_root=tmp_path,
        resolved_path=tmp_path / "behavior" / "system" / "AGENTS.md",
        new_content="x",
        old_content=None,
        task_id="",
        source="control_plane",
    )
    key = behavior_version_key_for("AGENTS.md")
    assert len(await sg.behavior_version_store.list_versions(key)) == 1
    assert await _count_events(sg, "BEHAVIOR_VERSION_RECORDED") == 0


@pytest.mark.asyncio
async def test_record_helper_baseline_skipped_when_unchanged(sg_env, tmp_path: Path):
    """Opus L2：首版 baseline == 新内容 → 不重复插 v1==v2，只记 1 版。"""
    sg = sg_env
    await record_behavior_version(
        stores=sg,
        project_root=tmp_path,
        resolved_path=tmp_path / "behavior" / "system" / "TOOLS.md",
        new_content="same",
        old_content="same",
        task_id="",
        source="llm_tool",
    )
    key = behavior_version_key_for("TOOLS.md")
    assert len(await sg.behavior_version_store.list_versions(key)) == 1


@pytest.mark.asyncio
async def test_record_helper_best_effort_no_raise(tmp_path: Path):
    """store 不存在（None）→ 不抛（best-effort，写已成功）。"""

    class _NoStore:
        behavior_version_store = None

    await record_behavior_version(
        stores=_NoStore(),
        project_root=tmp_path,
        resolved_path=tmp_path / "behavior" / "system" / "USER.md",
        new_content="x",
        old_content=None,
        task_id="",
        source="llm_tool",
    )
