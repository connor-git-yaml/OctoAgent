"""SkillsTool -- LLM 通过 ToolBroker 调用的 skills tool 实现。

提供 list 和 load 两个 action：
- list: 从 SkillDiscovery 获取所有 Skill 摘要，格式化为文本返回
- load: 验证 Skill 存在 -> 获取完整 content -> 更新 AgentSession.metadata -> 返回确认
- unload: 从 session 中移除指定 Skill（可选增强）

对齐 contracts/skills-api.md 中的 Tool Schema 定义。
"""

from __future__ import annotations

from typing import Any

import structlog

from .discovery import SkillDiscovery

logger = structlog.get_logger(__name__)

# 上下文预算阈值（字符数），超出则警告用户
_CONTEXT_BUDGET_THRESHOLD = 50_000  # 50KB
# session 最多加载的 Skill 数量
_MAX_LOADED_SKILLS = 10


class SkillsTool:
    """skills tool 实现，通过 ToolBroker 注册后供 LLM 调用。

    依赖 SkillDiscovery 提供数据，操作 AgentSession.metadata 存储加载状态。
    """

    def __init__(self, discovery: SkillDiscovery) -> None:
        self._discovery = discovery

    async def execute(
        self,
        *,
        action: str,
        name: str = "",
        session_metadata: dict[str, Any] | None = None,
    ) -> str:
        """执行 skills tool。

        Args:
            action: 操作类型（list / load / unload）
            name: Skill 名称（load/unload 时必填）
            session_metadata: AgentSession.metadata 引用，用于读写 loaded_skill_names

        Returns:
            格式化的文本结果
        """
        if action == "list":
            return self._handle_list()
        elif action == "load":
            return self._handle_load(name, session_metadata)
        elif action == "unload":
            return self._handle_unload(name, session_metadata)
        else:
            return f"Error: unknown action '{action}'. Supported actions: list, load, unload."

    def _handle_list(self) -> str:
        """处理 list action：返回所有可用 Skill 的摘要列表。"""
        items = self._discovery.list_items()
        if not items:
            return "No skills available."

        lines = [f"Available skills ({len(items)}):"]
        for item in items:
            # 截断过长的 description
            desc = item.description
            if len(desc) > 80:
                desc = desc[:77] + "..."
            lines.append(f"- {item.name}: {desc}")

        lines.append("")
        lines.append("Tip: use skills action=load name=<name> to load full instructions.")
        return "\n".join(lines)

    def _handle_load(
        self, name: str, session_metadata: dict[str, Any] | None
    ) -> str:
        """处理 load action：加载指定 Skill 到当前 session。"""
        if not name:
            return "Error: 'name' is required for action=load."

        entry = self._discovery.get(name)
        if entry is None:
            return f"Error: skill not found: '{name}'. Try skills action=list."

        if session_metadata is None:
            session_metadata = {}

        loaded_names: list[str] = session_metadata.get("loaded_skill_names", [])

        # 检查是否已加载（幂等）
        if name in loaded_names:
            return (
                f"Skill '{name}' is already loaded in this session.\n\n"
                f"Content (SKILL.md body):\n{entry.content}"
            )

        # 检查数量上限
        if len(loaded_names) >= _MAX_LOADED_SKILLS:
            return (
                f"Error: maximum {_MAX_LOADED_SKILLS} skills per session. "
                f"Currently loaded: {', '.join(loaded_names)}. "
                "Use skills action=unload name=<name> to free a slot."
            )

        # 上下文预算检查
        current_total = 0
        for n in loaded_names:
            e = self._discovery.get(n)
            if e:
                current_total += len(e.content)
        new_total = current_total + len(entry.content)
        if new_total > _CONTEXT_BUDGET_THRESHOLD:
            return (
                f"Warning: loading '{name}' ({len(entry.content)} chars) would exceed "
                f"context budget ({new_total}/{_CONTEXT_BUDGET_THRESHOLD} chars). "
                "Consider unloading unused skills first."
            )

        # 追加到 loaded_skill_names
        loaded_names.append(name)
        session_metadata["loaded_skill_names"] = loaded_names

        logger.info(
            "skill_loaded",
            skill_name=name,
            source=entry.source,
            loaded_count=len(loaded_names),
        )

        # 构建返回文本
        tags_str = ", ".join(entry.tags) if entry.tags else "(none)"
        return (
            f"Skill: {entry.name}\n"
            f"Version: {entry.version or '(unversioned)'}\n"
            f"Tags: {tags_str}\n"
            f"Description: {entry.description}\n\n"
            f"Content (SKILL.md body):\n{entry.content}\n\n"
            f"Loaded skill '{name}' into current session."
        )

    def _handle_unload(
        self, name: str, session_metadata: dict[str, Any] | None
    ) -> str:
        """处理 unload action：从当前 session 中移除指定 Skill。"""
        if not name:
            return "Error: 'name' is required for action=unload."

        if session_metadata is None:
            return f"Error: skill '{name}' is not loaded in this session."

        loaded_names: list[str] = session_metadata.get("loaded_skill_names", [])
        if name not in loaded_names:
            return f"Error: skill '{name}' is not loaded in this session."

        loaded_names.remove(name)
        session_metadata["loaded_skill_names"] = loaded_names

        logger.info(
            "skill_unloaded",
            skill_name=name,
            loaded_count=len(loaded_names),
        )

        return f"Skill '{name}' unloaded from current session."
