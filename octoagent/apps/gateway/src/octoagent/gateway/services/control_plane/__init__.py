"""Control Plane 服务包。

原始 control_plane.py 已拆分为多个 domain service。
外部代码通过此包导入 ControlPlaneService 和 ControlPlaneActionError。

Phase 1: 从 _legacy.py（原 control_plane.py）re-export，保持行为完全一致。
后续逐步切换到 _coordinator.py + domain services。
"""

from ._legacy import ControlPlaneActionError, ControlPlaneService

__all__ = ["ControlPlaneActionError", "ControlPlaneService"]
