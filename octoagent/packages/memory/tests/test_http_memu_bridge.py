"""HttpMemUBridge 单元测试。"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
from octoagent.memory import (
    HttpMemUBridge,
    MemoryAccessPolicy,
    MemoryBackendState,
    MemoryLayer,
    MemoryPartition,
)
from pydantic import SecretStr


class TestHttpMemUBridge:
    async def test_status_and_search_include_project_headers(self) -> None:
        now = datetime.now(UTC)
        seen: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((request.method, request.url.path))
            assert request.headers["X-OctoAgent-Project-ID"] == "project-alpha"
            assert request.headers["X-OctoAgent-Workspace-ID"] == "workspace-primary"
            assert request.headers["X-OctoAgent-Bridge-Binding"].endswith("/memu.primary")
            assert request.headers["Authorization"] == "Bearer memu-secret"
            if request.url.path == "/health":
                return httpx.Response(
                    200,
                    json={
                        "status": {
                            "backend_id": "memu",
                            "state": "healthy",
                            "active_backend": "memu",
                            "index_health": {"documents": 12},
                            "last_success_at": now.isoformat(),
                        }
                    },
                )
            payload = json.loads(request.content.decode("utf-8"))
            assert payload["scope_id"] == "memory/project-alpha"
            assert payload["query"] == "running"
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "record_id": "memu-hit-1",
                            "layer": "sor",
                            "scope_id": "memory/project-alpha",
                            "partition": "work",
                            "summary": "running",
                            "created_at": now.isoformat(),
                            "evidence_refs": [
                                {"ref_id": "artifact-1", "ref_type": "artifact"}
                            ],
                        }
                    ]
                },
            )

        transport = httpx.MockTransport(handler)
        bridge = HttpMemUBridge(
            base_url="https://memu.example",
            api_key=SecretStr("memu-secret"),
            project_id="project-alpha",
            workspace_id="workspace-primary",
            project_binding="project-alpha/workspace-primary/memu.primary",
            client_factory=lambda: httpx.AsyncClient(transport=transport),
        )

        status = await bridge.get_status()
        hits = await bridge.search(
            "memory/project-alpha",
            query="running",
            policy=MemoryAccessPolicy(),
        )

        assert status.state is MemoryBackendState.HEALTHY
        assert status.project_binding == "project-alpha/workspace-primary/memu.primary"
        assert status.index_health["documents"] == 12
        assert hits[0].record_id == "memu-hit-1"
        assert hits[0].layer is MemoryLayer.SOR
        assert hits[0].partition is MemoryPartition.WORK
        assert seen == [("GET", "/health"), ("POST", "/memory/search")]

    async def test_status_returns_unavailable_when_transport_fails(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("bridge offline", request=request)

        transport = httpx.MockTransport(handler)
        bridge = HttpMemUBridge(
            base_url="https://memu.example",
            project_binding="project-alpha/project/memu.primary",
            client_factory=lambda: httpx.AsyncClient(transport=transport),
        )

        status = await bridge.get_status()

        assert status.state is MemoryBackendState.UNAVAILABLE
        assert status.active_backend == "sqlite-metadata"
        assert status.failure_code == "MEMU_STATUS_REQUEST_FAILED"
        assert "bridge offline" in status.message

        with pytest.raises(httpx.ConnectError):
            await bridge.search("memory/project-alpha", query="running")
