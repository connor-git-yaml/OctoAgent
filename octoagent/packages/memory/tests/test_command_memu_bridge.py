"""CommandMemUBridge 单元测试。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from octoagent.memory import (
    CommandMemUBridge,
    MemoryAccessPolicy,
    MemoryBackendState,
    MemoryLayer,
    MemoryPartition,
)


def _write_bridge_script(path: Path) -> None:
    path.write_text(
        """
import json
import os
import sys

action = sys.argv[-1]
payload = json.loads(sys.stdin.read() or "{}")

if action == "health":
    print(json.dumps({
        "status": {
            "backend_id": "memu",
            "state": "healthy",
            "active_backend": "memu",
            "project_binding": os.environ.get("OCTOAGENT_BRIDGE_BINDING", ""),
            "index_health": {"driver": "command"},
        }
    }))
elif action == "query":
    print(json.dumps({
        "items": [
            {
                "record_id": "memu-hit-1",
                "layer": "sor",
                "scope_id": payload["scope_id"],
                "partition": "work",
                "summary": payload.get("query", ""),
                "created_at": "2026-03-14T00:00:00+00:00",
            }
        ]
    }))
else:
    print(json.dumps({"result": {"backend_state": "healthy"}}))
""".strip(),
        encoding="utf-8",
    )


class TestCommandMemUBridge:
    async def test_status_and_search_use_local_command(self, tmp_path: Path) -> None:
        script_path = tmp_path / "memu_bridge.py"
        _write_bridge_script(script_path)

        bridge = CommandMemUBridge(
            command=f"{sys.executable} {script_path}",
            cwd=str(tmp_path),
            project_id="project-alpha",
            workspace_id="workspace-primary",
            project_binding="project-alpha/workspace-primary/octoagent.yaml",
        )

        status = await bridge.get_status()
        hits = await bridge.search(
            "memory/project-alpha",
            query="running",
            policy=MemoryAccessPolicy(),
        )

        assert status.state is MemoryBackendState.HEALTHY
        assert status.project_binding == "project-alpha/workspace-primary/octoagent.yaml"
        assert status.index_health["driver"] == "command"
        assert hits[0].record_id == "memu-hit-1"
        assert hits[0].layer is MemoryLayer.SOR
        assert hits[0].partition is MemoryPartition.WORK

    async def test_status_returns_unavailable_when_command_fails(self, tmp_path: Path) -> None:
        script_path = tmp_path / "memu_bridge_fail.py"
        script_path.write_text(
            "import sys\nsys.stderr.write('bridge offline')\nsys.exit(2)\n",
            encoding="utf-8",
        )

        bridge = CommandMemUBridge(
            command=f"{sys.executable} {script_path}",
            cwd=str(tmp_path),
            project_binding="project-alpha/workspace-primary/octoagent.yaml",
        )

        status = await bridge.get_status()

        assert status.state is MemoryBackendState.UNAVAILABLE
        assert status.active_backend == "sqlite-metadata"
        assert status.failure_code == "MEMU_STATUS_COMMAND_FAILED"
        assert "bridge offline" in status.message

        with pytest.raises(RuntimeError):
            await bridge.search("memory/project-alpha", query="running")
