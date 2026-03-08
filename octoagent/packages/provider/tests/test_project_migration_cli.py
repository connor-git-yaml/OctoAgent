from __future__ import annotations

import asyncio
from pathlib import Path

from click.testing import CliRunner
from octoagent.core.store import create_store_group
from octoagent.provider.dx.cli import main


def _db_path(project_root: Path) -> Path:
    return project_root / "data" / "sqlite" / "octoagent.db"


def _artifacts_dir(project_root: Path) -> Path:
    return project_root / "data" / "artifacts"


def test_config_migrate_dry_run_does_not_persist_records(tmp_path: Path) -> None:
    runner = CliRunner()

    result = runner.invoke(
        main,
        ["config", "migrate", "--dry-run"],
        env={"OCTOAGENT_PROJECT_ROOT": str(tmp_path)},
    )

    assert result.exit_code == 0
    assert "Project Migration Plan" in result.output
    assert "created_project=True" in result.output

    async def _assert_no_project() -> None:
        store_group = await create_store_group(str(_db_path(tmp_path)), _artifacts_dir(tmp_path))
        try:
            assert await store_group.project_store.get_default_project() is None
        finally:
            await store_group.conn.close()

    asyncio.run(_assert_no_project())


def test_config_migrate_apply_then_rollback_latest(tmp_path: Path) -> None:
    runner = CliRunner()
    env = {"OCTOAGENT_PROJECT_ROOT": str(tmp_path)}

    applied = runner.invoke(
        main,
        ["config", "migrate", "--yes"],
        env=env,
    )
    assert applied.exit_code == 0
    assert "Project Migration Apply" in applied.output

    async def _assert_project_exists() -> None:
        store_group = await create_store_group(str(_db_path(tmp_path)), _artifacts_dir(tmp_path))
        try:
            assert await store_group.project_store.get_default_project() is not None
        finally:
            await store_group.conn.close()

    asyncio.run(_assert_project_exists())

    rolled_back = runner.invoke(
        main,
        ["config", "migrate", "--rollback", "latest", "--yes"],
        env=env,
    )
    assert rolled_back.exit_code == 0
    assert "Project Migration Rollback" in rolled_back.output

    async def _assert_project_removed() -> None:
        store_group = await create_store_group(str(_db_path(tmp_path)), _artifacts_dir(tmp_path))
        try:
            assert await store_group.project_store.get_default_project() is None
        finally:
            await store_group.conn.close()

    asyncio.run(_assert_project_removed())
