"""CLI setup 适配器 — 通过 HTTP 调用已运行的 gateway control-plane API。

Feature A2 修复：消除 provider/dx → apps/gateway 的反向依赖��
CLI（octo setup）不再在进程内实例化 gateway 服务栈，
改为调用已运行 gateway 的 REST API。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import structlog

from octoagent.core.models import (
    ActionRequestEnvelope,
    ActionResultEnvelope,
    ControlPlaneActor,
    ControlPlaneSurface,
)
from ulid import ULID

from .wizard_session import DEFAULT_SETUP_AGENT_PROFILE

logger = structlog.get_logger(__name__)

# gateway 默认地址
_DEFAULT_GATEWAY_URL = "http://127.0.0.1:8000"


class RemoteControlPlaneClient:
    """通过 HTTP 调用 gateway 的 control-plane API。

    替代原来在 CLI 进程内实例化 CapabilityPackService + ControlPlaneService 的方式。
    """

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    async def execute_action(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        """调用 POST /api/control/actions。"""
        import httpx

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self._base_url}/api/control/actions",
                json=request.model_dump(mode="json"),
            )
            resp.raise_for_status()
            return ActionResultEnvelope.model_validate(resp.json())

    async def get_agent_profiles(self) -> dict[str, Any]:
        """调用 GET /api/control/resources/agent-profiles。"""
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{self._base_url}/api/control/resources/agent-profiles")
            resp.raise_for_status()
            return resp.json()

    async def get_project_selector(self) -> dict[str, Any]:
        """调用 GET /api/control/resources/project-selector。"""
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{self._base_url}/api/control/resources/project-selector")
            resp.raise_for_status()
            return resp.json()

    async def health_check(self) -> bool:
        """检查 gateway 是否可达。"""
        import httpx

        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(f"{self._base_url}/health")
                return resp.status_code == 200
        except (httpx.ConnectError, httpx.TimeoutException):
            return False


class LocalSetupGovernanceAdapter:
    """CLI setup 适配器。通过 HTTP 调用已运行的 gateway。"""

    def __init__(self, project_root: Path) -> None:
        self._project_root = project_root.resolve()

    def _resolve_gateway_url(self) -> str:
        """从环境变量或默认值解析 gateway 地址。"""
        return os.environ.get("OCTOAGENT_GATEWAY_URL", _DEFAULT_GATEWAY_URL)

    async def _get_client(self) -> RemoteControlPlaneClient:
        """获取 gateway 客户端，gateway 未运行时抛异常。"""
        url = self._resolve_gateway_url()
        client = RemoteControlPlaneClient(url)
        if not await client.health_check():
            raise RuntimeError(
                f"Gateway 未运行（{url}）。请先运行 octo-start 启动服务，再执行 setup。"
            )
        return client

    async def review(self, draft: Mapping[str, Any] | None = None) -> ActionResultEnvelope:
        client = await self._get_client()
        return await client.execute_action(
            ActionRequestEnvelope(
                request_id=str(ULID()),
                action_id="setup.review",
                params={"draft": dict(draft or {})},
                surface=ControlPlaneSurface.CLI,
                actor=ControlPlaneActor(
                    actor_id="user:cli",
                    actor_label="CLI",
                ),
            )
        )

    async def prepare_wizard_draft(self, draft: Mapping[str, Any]) -> dict[str, Any]:
        prepared = dict(draft)
        if isinstance(prepared.get("agent_profile"), Mapping) and prepared["agent_profile"]:
            return prepared

        client = await self._get_client()
        # 通过公开 REST API 获取 agent profiles（替代原来的私有方法调用）
        profiles_doc = await client.get_agent_profiles()
        active_profile = profiles_doc.get("active_agent_profile")
        if isinstance(active_profile, Mapping) and str(
            active_profile.get("name", "")
        ).strip():
            return prepared

        prepared["agent_profile"] = dict(DEFAULT_SETUP_AGENT_PROFILE)
        return prepared

    async def apply(self, draft: Mapping[str, Any]) -> ActionResultEnvelope:
        client = await self._get_client()
        return await client.execute_action(
            ActionRequestEnvelope(
                request_id=str(ULID()),
                action_id="setup.apply",
                params={"draft": dict(draft)},
                surface=ControlPlaneSurface.CLI,
                actor=ControlPlaneActor(
                    actor_id="user:cli",
                    actor_label="CLI",
                ),
            )
        )

    async def quick_connect(self, draft: Mapping[str, Any]) -> ActionResultEnvelope:
        client = await self._get_client()
        return await client.execute_action(
            ActionRequestEnvelope(
                request_id=str(ULID()),
                action_id="setup.quick_connect",
                params={"draft": dict(draft)},
                surface=ControlPlaneSurface.CLI,
                actor=ControlPlaneActor(
                    actor_id="user:cli",
                    actor_label="CLI",
                ),
            )
        )

    async def connect_openai_codex_oauth(
        self,
        *,
        env_name: str = "OPENAI_API_KEY",
        profile_name: str = "openai-codex-default",
    ) -> ActionResultEnvelope:
        client = await self._get_client()
        return await client.execute_action(
            ActionRequestEnvelope(
                request_id=str(ULID()),
                action_id="provider.oauth.openai_codex",
                params={
                    "env_name": env_name,
                    "profile_name": profile_name,
                },
                surface=ControlPlaneSurface.CLI,
                actor=ControlPlaneActor(
                    actor_id="user:cli",
                    actor_label="CLI",
                ),
            )
        )
