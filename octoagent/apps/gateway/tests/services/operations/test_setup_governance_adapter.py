from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from octoagent.gateway.services.operations.setup_governance_adapter import (
    LocalSetupGovernanceAdapter,
)
from pydantic import ValidationError

_ASYNC_CLIENT = httpx.AsyncClient


def _install_transport(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> None:
    def build_client(*_args: object, **kwargs: object) -> httpx.AsyncClient:
        return _ASYNC_CLIENT(
            timeout=kwargs.get("timeout"),
            transport=httpx.MockTransport(handler),
        )

    monkeypatch.setattr(httpx, "AsyncClient", build_client)


@pytest.mark.asyncio
async def test_real_adapter_handles_health_error_malformed_envelope_and_existing_profile_without_default_write(  # noqa: E501
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = LocalSetupGovernanceAdapter(tmp_path)

    _install_transport(
        monkeypatch,
        lambda _request: httpx.Response(503, json={"status": "unavailable"}),
    )
    with pytest.raises(RuntimeError, match="Gateway 未运行"):
        await adapter.review()

    def malformed_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(200, json={"result": {"message": "missing envelope fields"}})

    _install_transport(monkeypatch, malformed_handler)
    with pytest.raises(ValidationError):
        await adapter.review({"provider": "openrouter"})

    def existing_profile_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        assert request.url.path == "/api/control/resources/agent-profiles"
        return httpx.Response(
            200,
            json={"active_agent_profile": {"name": "existing-profile"}},
        )

    _install_transport(monkeypatch, existing_profile_handler)
    draft = {"config": {"providers": [{"id": "openrouter"}]}}
    prepared = await adapter.prepare_wizard_draft(draft)

    assert prepared == draft
    assert "agent_profile" not in prepared
