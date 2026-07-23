"""Control Plane 服务包。

原始 control_plane.py 已拆分为多个 domain service，
由 ControlPlaneService（_coordinator.py）作为 thin facade 整合对外暴露。

NOTE: UpdateTriggerSource / asyncio保留在package seam，供既有异步调度测试注入。
"""

import asyncio  # noqa: F401 — 测试通过 control_plane_module.asyncio 引用

from octoagent.core.models.update import UpdateTriggerSource  # noqa: F401
from octoagent.provider.auth.environment import detect_environment  # noqa: F401
from octoagent.provider.auth.oauth_flows import run_auth_code_pkce_flow  # noqa: F401

from ._base import ControlPlaneActionError
from ._coordinator import ControlPlaneService

__all__ = ["ControlPlaneActionError", "ControlPlaneService"]
