"""Control Plane 服务包。

原始 control_plane.py 已拆分为多个 domain service，
由 ControlPlaneService（_coordinator.py）作为 thin facade 整合对外暴露。

NOTE: RuntimeActivationService / RuntimeActivationError / UpdateTriggerSource / asyncio
     在此处 re-export，以便测试通过 monkeypatch.setattr(control_plane_module, ...) 生效。
     domain service 中使用这些符号时，必须通过 package 模块引用（见 setup_service / mcp_service）。
"""

import asyncio  # noqa: F401 — 测试通过 control_plane_module.asyncio 引用

from octoagent.core.models.update import UpdateTriggerSource  # noqa: F401
from octoagent.provider.auth.environment import detect_environment  # noqa: F401
from octoagent.provider.auth.oauth_flows import run_auth_code_pkce_flow  # noqa: F401
from octoagent.provider.dx.runtime_activation import (  # noqa: F401
    RuntimeActivationError,
    RuntimeActivationService,
)

from ._base import ControlPlaneActionError
from ._coordinator import ControlPlaneService

__all__ = ["ControlPlaneActionError", "ControlPlaneService"]
