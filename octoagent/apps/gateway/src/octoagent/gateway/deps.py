"""依赖注入模块 -- 通过 FastAPI Depends 注入 Store 实例

Store 实例通过 app.state 管理，在 lifespan 中初始化/清理。
"""

from fastapi import Request
from octoagent.core.store import StoreGroup


def get_store_group(request: Request) -> StoreGroup:
    """从 app.state 获取 StoreGroup 实例"""
    return request.app.state.store_group


def get_sse_hub(request: Request):
    """从 app.state 获取 SSEHub 实例"""
    return request.app.state.sse_hub
