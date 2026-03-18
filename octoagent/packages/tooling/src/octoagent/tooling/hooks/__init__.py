"""Hook 实现模块

包含内置 Hook 和 Feature 061 新增的权限检查 Hook。
向后兼容：原 hooks.py 中的 LargeOutputHandler / EventGenerationHook 仍可从此处导入。
"""

from __future__ import annotations

# 原有内置 Hook（保持向后兼容导入路径）
from ..hooks_legacy import EventGenerationHook, LargeOutputHandler

# Feature 061: 权限检查 Hook
from .approval_override_hook import ApprovalOverrideHook
from .preset_hook import PresetBeforeHook

__all__ = [
    "EventGenerationHook",
    "LargeOutputHandler",
    "ApprovalOverrideHook",
    "PresetBeforeHook",
]
