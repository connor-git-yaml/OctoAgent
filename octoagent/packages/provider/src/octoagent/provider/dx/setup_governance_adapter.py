"""本地 CLI 适配 canonical setup.review / setup.apply。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from octoagent.core.models import (
    ActionRequestEnvelope,
    ActionResultEnvelope,
    ControlPlaneActor,
    ControlPlaneSurface,
)
from octoagent.core.store import create_store_group
from octoagent.memory.store import init_memory_db
from octoagent.tooling import ToolBroker
from ulid import ULID

# 延迟导入 gateway 服务——打断 provider ↔ gateway 循环依赖。
# 这些 import 只在 _open_control_plane() 运行时才需要。
_CapabilityPackService = None
_ControlPlaneService = None


def _ensure_gateway_imports() -> tuple[type, type]:
    global _CapabilityPackService, _ControlPlaneService
    if _CapabilityPackService is None:
        from octoagent.gateway.services.capability_pack import CapabilityPackService
        from octoagent.gateway.services.control_plane import ControlPlaneService
        _CapabilityPackService = CapabilityPackService
        _ControlPlaneService = ControlPlaneService
    return _CapabilityPackService, _ControlPlaneService

from .update_service import UpdateService
from .update_status_store import UpdateStatusStore
from .wizard_session import DEFAULT_SETUP_AGENT_PROFILE


class LocalSetupGovernanceAdapter:
    """在 CLI 里复用 gateway control-plane 的 setup 语义。"""

    def __init__(self, project_root: Path) -> None:
        self._project_root = project_root.resolve()

    @asynccontextmanager
    async def _open_control_plane(self) -> AsyncIterator[Any]:
        CapabilityPackSvc, ControlPlaneSvc = _ensure_gateway_imports()
        db_path = self._project_root / "data" / "sqlite" / "octoagent.db"
        artifacts_dir = self._project_root / "data" / "artifacts"
        store_group = await create_store_group(db_path, artifacts_dir)
        await init_memory_db(store_group.conn)
        tool_broker = ToolBroker(
            event_store=store_group.event_store,
            artifact_store=store_group.artifact_store,
        )
        capability_pack = CapabilityPackSvc(
            project_root=self._project_root,
            store_group=store_group,
            tool_broker=tool_broker,
        )
        await capability_pack.startup()
        await capability_pack.refresh()
        control_plane = ControlPlaneSvc(
            project_root=self._project_root,
            store_group=store_group,
            capability_pack_service=capability_pack,
            update_status_store=UpdateStatusStore(self._project_root),
            update_service=UpdateService(self._project_root),
        )
        try:
            yield control_plane
        finally:
            await store_group.conn.close()

    async def review(self, draft: Mapping[str, Any] | None = None) -> ActionResultEnvelope:
        async with self._open_control_plane() as control_plane:
            return await control_plane.execute_action(
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

        async with self._open_control_plane() as control_plane:
            _, selected_project, _, _ = await control_plane._resolve_selection()
            agent_profiles = await control_plane.get_agent_profiles_document()
            active_agent_profile = control_plane._resolve_active_agent_profile_payload(
                agent_profiles=agent_profiles,
                selected_project=selected_project,
            )
        if isinstance(active_agent_profile, Mapping) and str(
            active_agent_profile.get("name", "")
        ).strip():
            return prepared

        prepared["agent_profile"] = dict(DEFAULT_SETUP_AGENT_PROFILE)
        return prepared

    async def apply(self, draft: Mapping[str, Any]) -> ActionResultEnvelope:
        async with self._open_control_plane() as control_plane:
            return await control_plane.execute_action(
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
        async with self._open_control_plane() as control_plane:
            return await control_plane.execute_action(
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
        async with self._open_control_plane() as control_plane:
            return await control_plane.execute_action(
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
