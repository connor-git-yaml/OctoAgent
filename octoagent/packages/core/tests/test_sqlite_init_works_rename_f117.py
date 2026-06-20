"""F117 W3：works 三列 worker→agent 数据保全防御 RENAME 测试。

覆盖 sqlite_init `_migrate_legacy_tables` 的 works 列改名四态中两个数据相关态：
- 老实例（仅老列 + 数据）→ RENAME COLUMN 改名，**数据保全**（真实例升级关键路径）。
- 坏半迁移态（老列 + 新列同存）→ 老列数据 backfill 进新列 + DROP 老列，防孤儿（Codex MED）。

fresh / 已迁移态由全量 init_db 路径隐式覆盖（works 直接建新名 → RENAME 跳过）。
"""

from pathlib import Path

import aiosqlite
from octoagent.core.store.sqlite_init import (
    _migrate_legacy_tables,
    _table_columns,
    init_db,
)

_RENAME_PAIRS = (
    ("requested_agent_profile_id", "requested_worker_profile_id"),
    ("requested_agent_profile_version", "requested_worker_profile_version"),
    ("effective_profile_snapshot_id", "effective_worker_snapshot_id"),
)


async def test_works_rename_old_only_preserves_data(tmp_path: Path) -> None:
    """老实例（仅老列 + 数据）→ RENAME 改名且数据守恒（真实例升级路径）。"""
    conn = await aiosqlite.connect(str(tmp_path / "works-old.db"))
    try:
        await init_db(conn)
        # 模拟 W3 前实例：把新列名改回老列名。
        for _new, _old in _RENAME_PAIRS:
            await conn.execute(f"ALTER TABLE works RENAME COLUMN {_new} TO {_old}")
        await conn.execute(
            "INSERT INTO works (work_id, task_id, created_at, updated_at, "
            "requested_worker_profile_id, requested_worker_profile_version, "
            "effective_worker_snapshot_id) "
            "VALUES ('w1', 't1', '2026-06-20T00:00:00Z', '2026-06-20T00:00:00Z', "
            "'profile-X', 7, 'snap-Y')"
        )
        await conn.commit()

        # 再跑迁移 → 老列 RENAME 成新列，数据保全。
        await _migrate_legacy_tables(conn)

        cols = await _table_columns(conn, "works")
        for _new, _old in _RENAME_PAIRS:
            assert _new in cols, f"{_new} 应已建"
            assert _old not in cols, f"{_old} 应已改名移除"
        cursor = await conn.execute(
            "SELECT requested_agent_profile_id, requested_agent_profile_version, "
            "effective_profile_snapshot_id FROM works WHERE work_id='w1'"
        )
        row = await cursor.fetchone()
        assert row == ("profile-X", 7, "snap-Y"), "RENAME 必须保留老列数据"
    finally:
        await conn.close()


async def test_works_rename_both_present_backfills_and_drops_old(tmp_path: Path) -> None:
    """坏半迁移态（老列 + 新列同存）→ 老列数据 backfill 进新列 + DROP 老列（Codex MED）。"""
    conn = await aiosqlite.connect(str(tmp_path / "works-both.db"))
    try:
        await init_db(conn)  # works 已是新列名
        # 模拟坏半迁移态：ADD 老列（仅 id 列）+ 老列有数据、新列留默认空。
        await conn.execute(
            "ALTER TABLE works ADD COLUMN requested_worker_profile_id TEXT NOT NULL DEFAULT ''"
        )
        await conn.execute(
            "INSERT INTO works (work_id, task_id, created_at, updated_at, "
            "requested_worker_profile_id, requested_agent_profile_id) "
            "VALUES ('w2', 't2', '2026-06-20T00:00:00Z', '2026-06-20T00:00:00Z', "
            "'old-authoritative', '')"
        )
        await conn.commit()

        await _migrate_legacy_tables(conn)

        cols = await _table_columns(conn, "works")
        assert "requested_worker_profile_id" not in cols, "坏半迁移态老列应被 DROP"
        assert "requested_agent_profile_id" in cols
        cursor = await conn.execute(
            "SELECT requested_agent_profile_id FROM works WHERE work_id='w2'"
        )
        row = await cursor.fetchone()
        assert row[0] == "old-authoritative", "新列必须 backfill 自老列权威数据，防孤儿"
    finally:
        await conn.close()
