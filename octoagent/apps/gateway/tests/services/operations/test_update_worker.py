from __future__ import annotations

from pathlib import Path

import pytest
from octoagent.gateway.services.operations import update_worker


@pytest.mark.asyncio
async def test_update_worker_executes_requested_attempt_with_injected_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Path, str]] = []

    class RecordingUpdateService:
        def __init__(self, project_root: Path) -> None:
            self._project_root = project_root

        async def execute_attempt(self, attempt_id: str) -> None:
            calls.append((self._project_root, attempt_id))

    monkeypatch.setattr(update_worker, "UpdateService", RecordingUpdateService)
    await update_worker._run(tmp_path, "attempt-123")
    assert calls == [(tmp_path, "attempt-123")]

    class FailingUpdateService:
        def __init__(self, _project_root: Path) -> None:
            pass

        async def execute_attempt(self, _attempt_id: str) -> None:
            raise RuntimeError("worker execution failed")

    monkeypatch.setattr(update_worker, "UpdateService", FailingUpdateService)
    with pytest.raises(RuntimeError, match="worker execution failed"):
        await update_worker._run(tmp_path, "attempt-failed")
