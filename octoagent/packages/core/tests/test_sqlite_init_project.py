"""Feature 025: sqlite_init project schema 测试。"""

from pathlib import Path

import aiosqlite
from octoagent.core.store.sqlite_init import init_db


async def test_init_db_creates_project_tables(tmp_path: Path) -> None:
    conn = await aiosqlite.connect(str(tmp_path / "project.db"))
    try:
        await init_db(conn)
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] for row in await cursor.fetchall()}
        assert "projects" in tables
        assert "workspaces" in tables
        assert "project_bindings" in tables
        assert "project_migration_runs" in tables
    finally:
        await conn.close()
