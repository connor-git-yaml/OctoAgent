"""@tool_contract 装饰器 -- Feature 004 Tool Contract

对齐 spec FR-001/002, contracts/tooling-api.md §10。
将工具元数据附加到函数对象上，Schema Reflection 时自动提取。
side_effect_level 为必填（无默认值），强制声明。
"""

from __future__ import annotations

from typing import Any, TypeVar

from .models import SideEffectLevel, ToolTier

F = TypeVar("F", bound=Any)


def tool_contract(
    *,
    side_effect_level: SideEffectLevel,
    tool_group: str,
    tier: ToolTier = ToolTier.DEFERRED,
    tags: list[str] | None = None,
    worker_types: list[str] | None = None,
    manifest_ref: str = "",
    metadata: dict[str, Any] | None = None,
    name: str | None = None,
    version: str = "1.0.0",
    timeout_seconds: float | None = None,
    output_truncate_threshold: int | None = None,
) -> Any:
    """工具契约声明装饰器 -- 对齐 spec FR-001/002

    将工具元数据附加到函数对象上，Schema Reflection 时自动提取。
    side_effect_level 为必填（无默认值），强制声明。

    Args:
        side_effect_level: 副作用等级（必填）
        tool_group: 逻辑分组（如 "system", "filesystem"）
        tier: Feature 061 工具层级（CORE/DEFERRED），默认 DEFERRED
        tags: ToolIndex 检索标签
        worker_types: 推荐 worker type
        manifest_ref: 声明来源引用
        metadata: 扩展元数据
        name: 工具名称，默认取 func.__name__
        version: 工具版本号，默认 "1.0.0"
        timeout_seconds: 声明式超时（秒），None 表示不超时
        output_truncate_threshold: 工具级输出裁切阈值（字符数）
    """
    def decorator(func: F) -> F:
        func._tool_meta = {  # type: ignore[attr-defined]
            "side_effect_level": side_effect_level,
            "tool_group": tool_group,
            "tier": tier,
            "tags": list(tags or []),
            "worker_types": list(worker_types or []),
            "manifest_ref": manifest_ref,
            "metadata": dict(metadata or {}),
            "name": name or func.__name__,
            "version": version,
            "timeout_seconds": timeout_seconds,
            "output_truncate_threshold": output_truncate_threshold,
        }
        return func

    return decorator
