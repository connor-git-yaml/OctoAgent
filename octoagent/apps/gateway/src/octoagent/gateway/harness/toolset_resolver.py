"""toolset_resolver.py：ToolsetResolver + toolsets.yaml 读取（Feature 084 Phase 1）。

基于 toolsets.yaml 的声明式 toolset 配置，实现 entrypoint 粒度的工具集过滤。

FR 覆盖：FR-1.3（entrypoints 动态过滤）
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
import yaml
from pydantic import BaseModel, field_validator

from .tool_registry import ToolEntry, ToolRegistry

log = structlog.get_logger()


class ToolsetConfig(BaseModel):
    """单个 toolset 的配置。"""

    entrypoints: list[str]
    """此 toolset 适用的入口点列表，如 ["web", "agent_runtime"]。"""

    tools: list[str]
    """工具名称列表，如 ["user_profile.update", "delegate_task"]。"""

    includes: list[str] = []
    """继承的其他 toolset 名称（含 includes 递归展开）。"""

    blocked: list[str] = []
    """在此 toolset 中被屏蔽的工具名称（覆盖 includes 引入的工具）。"""

    @field_validator("entrypoints", mode="before")
    @classmethod
    def normalize_entrypoints(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            return [v]
        return list(v)


def load_toolsets(yaml_path: Path) -> dict[str, ToolsetConfig]:
    """读取 toolsets.yaml，返回 toolset 名称到 ToolsetConfig 的映射。

    Args:
        yaml_path: toolsets.yaml 文件路径。

    Returns:
        dict[toolset_name, ToolsetConfig]。

    Raises:
        FileNotFoundError: yaml_path 不存在时。
        ValueError: YAML 格式错误时。
    """
    if not yaml_path.exists():
        raise FileNotFoundError(f"toolsets.yaml 不存在：{yaml_path}")

    try:
        content = yaml_path.read_text(encoding="utf-8")
        raw = yaml.safe_load(content)
    except Exception as exc:
        raise ValueError(f"toolsets.yaml 解析失败：{exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError("toolsets.yaml 顶层必须是 dict")

    toolsets_raw = raw.get("toolsets", raw)  # 支持有无 "toolsets:" 顶层 key 两种格式
    if not isinstance(toolsets_raw, dict):
        raise ValueError("toolsets.yaml 中 toolsets 必须是 dict")

    result: dict[str, ToolsetConfig] = {}
    for name, config_raw in toolsets_raw.items():
        if not isinstance(config_raw, dict):
            log.warning("toolset_config_invalid", toolset_name=name)
            continue
        try:
            result[name] = ToolsetConfig.model_validate(config_raw)
        except Exception as exc:
            log.warning("toolset_config_parse_error", toolset_name=name, error=str(exc))

    log.debug("toolsets_loaded", count=len(result), toolset_names=list(result.keys()))
    return result


def _resolve_toolset_tools(
    toolset_name: str,
    toolsets: dict[str, ToolsetConfig],
    *,
    _visited: set[str] | None = None,
) -> set[str]:
    """递归解析 toolset 的全部工具（含 includes 展开 + blocked 过滤）。

    Args:
        toolset_name: 要解析的 toolset 名称。
        toolsets: 全部 toolset 配置映射。
        _visited: 已访问的 toolset 集合（防止循环引用）。

    Returns:
        最终工具名称集合。
    """
    if _visited is None:
        _visited = set()
    if toolset_name in _visited:
        log.warning("toolset_circular_include", toolset_name=toolset_name)
        return set()
    _visited.add(toolset_name)

    config = toolsets.get(toolset_name)
    if config is None:
        log.warning("toolset_not_found", toolset_name=toolset_name)
        return set()

    # 先展开 includes
    tool_set: set[str] = set()
    for included_name in config.includes:
        tool_set |= _resolve_toolset_tools(included_name, toolsets, _visited=_visited)

    # 再加自身 tools
    tool_set |= set(config.tools)

    # 最后减去 blocked
    tool_set -= set(config.blocked)

    return tool_set


def resolve_for_entrypoint(
    registry: ToolRegistry,
    entrypoint: str,
    *,
    toolsets: dict[str, ToolsetConfig] | None = None,
    yaml_path: Path | None = None,
) -> list[ToolEntry]:
    """返回指定入口点可见的工具列表，结合 ToolRegistry entrypoints 和 toolsets 配置。

    过滤逻辑：
    1. 从 ToolRegistry 中取所有 entrypoints 包含 entrypoint 的工具
    2. 如果提供了 toolsets，进一步按 toolset.entrypoints 过滤

    Args:
        registry: ToolRegistry 实例。
        entrypoint: 目标入口点，如 "web"、"agent_runtime"。
        toolsets: 可选的 toolset 配置映射（已解析）。
        yaml_path: 可选的 toolsets.yaml 路径（未提供 toolsets 时尝试加载）。

    Returns:
        在指定入口点可见的 ToolEntry 列表。
    """
    # 第一层过滤：ToolRegistry.list_for_entrypoint
    candidates = registry.list_for_entrypoint(entrypoint)

    if toolsets is None and yaml_path is not None:
        try:
            toolsets = load_toolsets(yaml_path)
        except Exception as exc:
            log.warning("toolset_yaml_load_failed", error=str(exc))
            toolsets = None

    if toolsets is None:
        return candidates

    # 第二层过滤：从 toolsets 中计算 entrypoint 可见的工具名集合
    visible_tool_names: set[str] = set()
    for toolset_name, config in toolsets.items():
        if entrypoint in config.entrypoints:
            visible_tool_names |= _resolve_toolset_tools(toolset_name, toolsets)

    if not visible_tool_names:
        # toolsets 没有覆盖此 entrypoint，退回到 registry 级别的过滤结果
        return candidates

    return [e for e in candidates if e.name in visible_tool_names]
