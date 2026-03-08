"""依赖注入模块 -- 通过 FastAPI Depends 注入 Store 实例

Store 实例通过 app.state 管理，在 lifespan 中初始化/清理。
"""

from fastapi import Request
from octoagent.core.store import StoreGroup
from octoagent.policy.approval_manager import ApprovalManager


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


def get_execution_console_service(request: Request):
    """从 app.state 获取 ExecutionConsoleService。"""
    return request.app.state.execution_console


def get_control_plane_service(request: Request):
    """从 app.state 获取 ControlPlaneService。"""
    return request.app.state.control_plane_service
