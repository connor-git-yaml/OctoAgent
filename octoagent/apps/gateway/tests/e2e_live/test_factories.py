"""F087 P2 T-P2-12 helpers/factories 自身单测。"""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

from octoagent.core.store import create_store_group
from octoagent.core.store.sqlite_init import init_db

from apps.gateway.tests.e2e_live.helpers.factories import (
    _build_real_user_profile_handler,
    _ensure_audit_task,
    _insert_turn_events,
    copy_local_instance_template,
)


pytestmark = [pytest.mark.e2e_live]


@pytest_asyncio.fixture
async def store_group(tmp_path: Path):
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    sg = await create_store_group(
        db_path=str(tmp_path / "test.db"),
        artifacts_dir=str(artifacts_dir),
    )
    yield sg
    await sg.conn.close()


@pytest_asyncio.fixture
async def db_conn(tmp_path: Path):
    db_path = str(tmp_path / "fac.db")
    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
    await init_db(conn)
    yield conn
    await conn.close()


async def test_ensure_audit_task_idempotent(store_group) -> None:
    """同一 task_id 重复 ensure 不抛错。"""
    await _ensure_audit_task(store_group, "_test_audit_t1")
    await _ensure_audit_task(store_group, "_test_audit_t1")  # 第二次无副作用
    t = await store_group.task_store.get_task("_test_audit_t1")
    assert t is not None


async def test_insert_turn_events_writes_rows(db_conn) -> None:
    turns = [{"payload": {"text": "hi"}}, {"payload": {"text": "yo"}}]
    await _insert_turn_events(db_conn, turns)
    cursor = await db_conn.execute(
        "SELECT COUNT(*) FROM events WHERE task_id='_e2e_turns_task'",
    )
    row = await cursor.fetchone()
    assert row[0] >= 2


async def test_build_real_user_profile_handler(store_group, tmp_path: Path) -> None:
    handler, snap_store, user_md = await _build_real_user_profile_handler(
        store_group, tmp_path,
    )
    assert callable(handler)
    assert snap_store is not None
    assert user_md.exists()


def test_copy_local_instance_template(tmp_path: Path) -> None:
    """copy_local_instance_template 从仓库 fixture 模板复制到 tmp dst。"""
    # 真实 template root（仓库内）
    # __file__ → apps/gateway/tests/e2e_live/test_factories.py
    # parents[4] → octoagent/
    repo_root = Path(__file__).resolve().parents[4]
    template_root = repo_root / "tests" / "fixtures" / "local-instance"
    if not template_root.exists():
        pytest.skip(f"template root not found: {template_root}")

    dst = tmp_path / "dst_instance"
    copy_local_instance_template(template_root, dst)
    # 至少 USER.md 应被复制（去掉 .template 后缀）
    assert (dst / "behavior" / "system" / "USER.md").exists()
