from __future__ import annotations

from pathlib import Path

import pytest

from octoagent.core.models import ControlPlaneActionStatus
from octoagent.core.store import create_store_group
from octoagent.provider.dx.project_migration import ProjectWorkspaceMigrationService
from octoagent.provider.dx.setup_governance_adapter import LocalSetupGovernanceAdapter


@pytest.mark.asyncio
async def test_review_initializes_memory_schema_for_cli_control_plane(tmp_path: Path) -> None:
    db_path = tmp_path / "data" / "sqlite" / "octoagent.db"
    artifacts_dir = tmp_path / "data" / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    store_group = await create_store_group(db_path, artifacts_dir)
    await store_group.conn.close()
    await ProjectWorkspaceMigrationService(tmp_path).ensure_default_project()

    result = await LocalSetupGovernanceAdapter(tmp_path).review({"config": {}})

    assert result.status == ControlPlaneActionStatus.COMPLETED
    assert result.code == "SETUP_REVIEW_READY"

