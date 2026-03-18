"""依赖注入模块 -- 通过 FastAPI Depends 注入 Store 实例

Store 实例通过 app.state 管理，在 lifespan 中初始化/清理。
"""

import os
from pathlib import Path

from fastapi import Depends, Request
from octoagent.core.store import StoreGroup
from octoagent.policy.approval_manager import ApprovalManager

from .services.frontdoor_auth import FrontDoorGuard
from .services.task_scope import TaskScopeGuard


def get_store_group(request: Request) -> StoreGroup:
    """从 app.state 获取 StoreGroup 实例"""
    return request.app.state.store_group


def get_sse_hub(request: Request):
    """从 app.state 获取 SSEHub 实例"""
    return request.app.state.sse_hub


def get_approval_manager(request: Request) -> ApprovalManager:
    """从 app.state 获取 ApprovalManager 实例

    Feature 006: PolicyEngine 在 lifespan 中初始化，
    ApprovalManager 通过 PolicyEngine 获取。
    """
    return request.app.state.approval_manager


def get_approval_override_repo(request: Request):
    """从 app.state 获取 ApprovalOverrideRepository 实例

    Feature 061 T-016: 供审批覆盖管理 API 使用。
    """
    return request.app.state.approval_override_repo


def get_approval_override_cache(request: Request):
    """从 app.state 获取 ApprovalOverride 内存缓存实例

    Feature 061 T-016: 删除覆盖时同步清除内存缓存。
    """
    return request.app.state.approval_override_cache


def get_execution_console_service(request: Request):
    """从 app.state 获取 ExecutionConsoleService。"""
    return request.app.state.execution_console


def get_control_plane_service(request: Request):
    """从 app.state 获取 ControlPlaneService。"""
    return request.app.state.control_plane_service


def get_front_door_guard(request: Request):
    """从 app.state 获取 FrontDoorGuard。"""
    guard = getattr(request.app.state, "front_door_guard", None)
    if guard is None:
        project_root = getattr(
            request.app.state,
            "project_root",
            Path(os.environ.get("OCTOAGENT_PROJECT_ROOT", str(Path.cwd()))),
        )
        guard = FrontDoorGuard(Path(project_root))
        request.app.state.front_door_guard = guard
    return guard


async def require_front_door_access(
    request: Request,
    guard=Depends(get_front_door_guard),
) -> None:
    """统一校验 owner-facing API 的 front-door 访问边界。"""
    await guard.authorize(request)


def get_skill_discovery(request: Request):
    """从 app.state 获取 SkillDiscovery 实例。

    Feature 057: SkillDiscovery 在 CapabilityPackService 初始化时创建，
    挂载到 app.state.skill_discovery 供路由依赖注入使用。
    """
    return request.app.state.skill_discovery


def get_task_scope_guard(request: Request, store_group=Depends(get_store_group)) -> TaskScopeGuard:
    """获取 raw task routes 的 project/workspace 视图隔离守卫。"""
    project_root = getattr(
        request.app.state,
        "project_root",
        Path(os.environ.get("OCTOAGENT_PROJECT_ROOT", str(Path.cwd()))),
    )
    return TaskScopeGuard(Path(project_root), store_group)
