"""F108b W7（F118 D8）：typed service registry 的错误语义锁 + 构造期接线测试。

锁定目标：
1. 未注入注册表时，typed accessor 的错误与原 `_get_service` **字节级一致**
   （RuntimeError + ``service '<name>' 未在 service_registry 中注册``）。
2. coordinator 构造后注册表就位、accessor 返回同一实例、`all_services()` 全 9 个。
3. bind_* 经 setter 传播到子 service（替代直捅私有属性，行为等价）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from octoagent.core.store import create_store_group
from octoagent.gateway.services.control_plane import ControlPlaneService
from octoagent.gateway.services.control_plane._base import (
    ControlPlaneContext,
    DomainServiceBase,
)
from octoagent.gateway.services.operations.project_migration import ProjectWorkspaceMigrationService
from octoagent.gateway.services.operations.telegram_pairing import TelegramStateStore
from octoagent.gateway.services.sse_hub import SSEHub


async def _bare_service(tmp_path: Path):
    store_group = await create_store_group(
        str(tmp_path / "gateway.db"),
        str(tmp_path / "artifacts"),
    )
    ctx = ControlPlaneContext(project_root=tmp_path, store_group=store_group)
    return DomainServiceBase(ctx), store_group


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("accessor", "name"),
    [("_agent_domain", "agent"), ("_mcp_domain", "mcp"), ("_setup_domain", "setup")],
)
async def test_accessor_error_byte_identical_to_legacy_get_service(
    tmp_path: Path, accessor: str, name: str
) -> None:
    svc, store_group = await _bare_service(tmp_path)
    try:
        with pytest.raises(RuntimeError) as exc_info:
            getattr(svc, accessor)
        assert str(exc_info.value) == f"service '{name}' 未在 service_registry 中注册"
        assert type(exc_info.value) is RuntimeError
    finally:
        await store_group.close()


@pytest.mark.asyncio
async def test_coordinator_wires_typed_registry_and_setters(tmp_path: Path) -> None:
    store_group = await create_store_group(
        str(tmp_path / "gateway.db"),
        str(tmp_path / "artifacts"),
    )
    try:
        await ProjectWorkspaceMigrationService(
            project_root=tmp_path,
            store_group=store_group,
        ).ensure_default_project()
        control_plane = ControlPlaneService(
            project_root=tmp_path,
            store_group=store_group,
            sse_hub=SSEHub(),
            telegram_state_store=TelegramStateStore(tmp_path),
        )
        services = control_plane._ctx.services
        assert services is not None
        # accessor 与 coordinator 持有的是同一实例
        assert control_plane._setup_service._agent_domain is control_plane._agent_service
        assert control_plane._setup_service._mcp_domain is control_plane._mcp_service
        assert control_plane._mcp_service._setup_domain is control_plane._setup_service
        # 全 9 个、原 dict 插入序
        assert services.all_services() == (
            control_plane._agent_service,
            control_plane._automation_service,
            control_plane._import_service,
            control_plane._mcp_service,
            control_plane._memory_service,
            control_plane._session_service,
            control_plane._setup_service,
            control_plane._work_service,
            control_plane._worker_service,
        )
        # bind_* 经 setter 传播（行为与原直捅私有属性等价）
        sentinel_proxy = object()
        sentinel_installer = object()
        sentinel_scheduler = object()
        control_plane.bind_proxy_manager(sentinel_proxy)
        control_plane.bind_mcp_installer(sentinel_installer)
        control_plane.bind_automation_scheduler(sentinel_scheduler)
        assert control_plane._proxy_manager is sentinel_proxy
        assert control_plane._setup_service._proxy_manager is sentinel_proxy
        assert control_plane._mcp_service._proxy_manager is sentinel_proxy
        assert control_plane._mcp_installer is sentinel_installer
        assert control_plane._mcp_service._mcp_installer is sentinel_installer
        assert control_plane._automation_scheduler is sentinel_scheduler
        assert control_plane._automation_service._automation_scheduler is sentinel_scheduler
    finally:
        await store_group.close()


@pytest.mark.asyncio
async def test_automation_create_accepts_domain_action_via_all_services(
    tmp_path: Path,
) -> None:
    """Codex W7 F1 killing test：automation.create 的 action_id 存在性校验
    经 ``all_services()`` 迭代必须覆盖 **domain action**（非 coordinator inline）。

    回归保护：若 all_services() 行为被误改（漏 service / 返回空），
    domain action 会被误判 AUTOMATION_UNKNOWN_ACTION——本测试拦截。
    """
    from octoagent.core.models import (
        ActionRequestEnvelope,
        ControlPlaneActor,
        ControlPlaneSurface,
    )
    from ulid import ULID

    store_group = await create_store_group(
        str(tmp_path / "gateway.db"),
        str(tmp_path / "artifacts"),
    )
    try:
        await ProjectWorkspaceMigrationService(
            project_root=tmp_path,
            store_group=store_group,
        ).ensure_default_project()
        control_plane = ControlPlaneService(
            project_root=tmp_path,
            store_group=store_group,
            sse_hub=SSEHub(),
            telegram_state_store=TelegramStateStore(tmp_path),
        )

        def _request(action_id: str) -> ActionRequestEnvelope:
            return ActionRequestEnvelope(
                request_id=str(ULID()),
                action_id="automation.create",
                surface=ControlPlaneSurface.WEB,
                actor=ControlPlaneActor(actor_id="user:web", actor_label="Owner"),
                params={
                    "name": "registry-killing-test",
                    "schedule_kind": "cron",
                    "schedule_expr": "0 9 * * *",
                    "action_id": action_id,
                    "action_params": {},
                },
            )

        # domain action（worker_service 注册的路由）必须通过存在性校验并创建成功
        ok = await control_plane.execute_action(_request("behavior.read_file"))
        assert ok.code == "AUTOMATION_CREATED", f"{ok.code}: {ok.message}"
        # 未知 action 仍被拒绝（校验本身没被放空）
        bad = await control_plane.execute_action(_request("no.such_action"))
        assert bad.code == "AUTOMATION_ACTION_INVALID"
    finally:
        await store_group.close()
