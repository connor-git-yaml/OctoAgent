"""Hook 实现模块

Feature 070: 权限 Hook（ApprovalOverrideHook、PresetBeforeHook）已删除，
权限检查统一由 ToolBroker 内联 check_permission() 完成。
仅保留非权限类 Hook（LargeOutputHandler、EventGenerationHook）。
"""

from __future__ import annotations

from ..hooks_legacy import EventGenerationHook, LargeOutputHandler

__all__ = [
    "EventGenerationHook",
    "LargeOutputHandler",
]
