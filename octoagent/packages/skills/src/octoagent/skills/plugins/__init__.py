"""F106 User Plugin Loader —— 用户/社区插件装载子系统。

在 SkillDiscovery 之上升级为 plugin_registry：发现 + 校验 + 信任审批 + 注册 +
（Phase C）热重载 + git。两类 plugin：
- declarative（skill 指令 + KNOWLEDGE 知识，数据，自由装载）
- code-capable（含 .py/.so 等可执行制品，启用须用户显式审批 + code-hash 绑定）

信任模型见 spec §0.2 / §0.3（残余风险）。本包 Phase A（declarative spine）+
Phase B（code-capable 审批 + 专用 loader）。Phase C（watchdog + git）后续。
"""

from __future__ import annotations

from .manifest import (
    PLUGIN_BEHAVIOR_ALLOWLIST,
    PluginCapability,
    PluginManifest,
    PluginProvides,
    PluginRecord,
    PluginRejectedReason,
    PluginState,
)

__all__ = [
    "PLUGIN_BEHAVIOR_ALLOWLIST",
    "PluginCapability",
    "PluginManifest",
    "PluginProvides",
    "PluginRecord",
    "PluginRejectedReason",
    "PluginState",
]
