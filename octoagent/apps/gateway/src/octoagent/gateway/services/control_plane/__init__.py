"""Control Plane 服务包。

原始 control_plane.py 已拆分为多个 domain service。
外部代码通过此包导入 ControlPlaneService 和 ControlPlaneActionError。

ControlPlaneActionError 统一定义在 _base.py，_legacy.py 中的副本仅向后兼容。
"""

from ._base import ControlPlaneActionError
from ._legacy import ControlPlaneService

__all__ = ["ControlPlaneActionError", "ControlPlaneService"]
