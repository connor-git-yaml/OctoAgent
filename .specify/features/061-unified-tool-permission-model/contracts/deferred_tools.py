"""Feature 061 接口契约: DeferredToolEntry + CoreToolSet + ToolSearchResult

对齐 spec FR-015/016/017/018/019/020/021/022/023。
定义 Deferred Tools 懒加载的核心数据结构。

注意: 此文件是接口契约（specification），不是最终实现。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


# ============================================================
# ToolTier 枚举
# ============================================================


class ToolTier(StrEnum):
    """工具层级标记 — 决定工具在初始 context 中的呈现方式

    CORE: 完整 JSON Schema 始终加载到 LLM context
          - 约 10 个高频工具
          - 无首次交互延迟
          - 必须包含 tool_search 自身 (FR-018)

    DEFERRED: 仅暴露 {name, one_line_desc} 列表
              - 通过 tool_search 按需加载完整 schema
              - Context 节省 60%+ (SC-001)
              - MCP 工具默认为 DEFERRED (FR-021)
    """

    CORE = "core"
    DEFERRED = "deferred"


# ============================================================
# Deferred Tools 数据模型
# ============================================================


class DeferredToolEntry(BaseModel):
    """Deferred 工具的精简表示 — 注入 system prompt

    仅包含名称和单行描述，不包含完整 JSON Schema。
    用于构建 Deferred Tools 名称列表，让 LLM 知道有哪些工具可用。
    LLM 通过 tool_search 获取完整定义后才能调用这些工具。

    估算: 每个 entry 约 15 tokens（name + desc），
    39 个 deferred tools ≈ 585 tokens。
    """

    name: str = Field(description="工具名称（如 docker.run）")
    one_line_desc: str = Field(
        max_length=80,
        description="单行描述（≤80 字符，用于 LLM 判断是否需要搜索该工具）",
    )
    tool_group: str = Field(default="", description="工具分组（如 docker, web）")
    side_effect_level: str = Field(default="", description="副作用等级标记")


# ============================================================
# Core Tools 配置
# ============================================================


class CoreToolSet(BaseModel):
    """Core Tools 配置 — 定义始终加载完整 schema 的工具清单

    Core Tools 的选择标准:
    1. 高频使用（覆盖 80% 的常见操作场景）
    2. 首次交互必需（如 tool_search 自身）
    3. 基础操作（读/写/执行）

    清单可通过配置文件覆盖，也可基于 Event Store 中
    工具调用频率统计动态确定。
    """

    tool_names: list[str] = Field(
        description="Core 工具名称列表（至少包含 tool_search）",
        min_length=1,
    )

    @classmethod
    def default(cls) -> CoreToolSet:
        """默认 Core Tools 清单（约 10 个高频工具）

        选择依据:
        - tool_search: 必须为 Core，保证 LLM 能搜索其他工具
        - project.inspect: 项目元信息查询，几乎每次对话都用到
        - filesystem.*: 文件操作是最高频的工具类别
        - terminal.exec: 命令执行，仅次于文件操作
        - memory.*: 记忆检索/搜索，上下文工程核心
        - skills: Skill 发现与加载
        - subagents.spawn: Subagent 创建（Butler/Worker 高频）
        """
        return cls(
            tool_names=[
                "tool_search",
                "project.inspect",
                "filesystem.list_dir",
                "filesystem.read_text",
                "filesystem.write_text",
                "terminal.exec",
                "memory.recall",
                "memory.search",
                "skills",
                "subagents.spawn",
            ]
        )

    def is_core(self, tool_name: str) -> bool:
        """判断工具是否为 Core"""
        return tool_name in self.tool_names

    def classify(self, tool_name: str) -> ToolTier:
        """返回工具的层级"""
        return ToolTier.CORE if self.is_core(tool_name) else ToolTier.DEFERRED


# ============================================================
# tool_search 返回结果
# ============================================================


class ToolSearchHit(BaseModel):
    """单个工具搜索命中 — tool_search 返回的工具完整信息

    包含工具的完整 JSON Schema，使 LLM 在后续步骤中
    能够正确构造工具调用参数。
    """

    tool_name: str = Field(description="工具名称")
    description: str = Field(description="工具完整描述")
    parameters_schema: dict[str, Any] = Field(
        description="完整参数 JSON Schema（reflect_tool_schema 生成）",
    )
    score: float = Field(default=0.0, description="匹配得分（0.0-1.0）")
    side_effect_level: str = Field(default="", description="副作用等级")
    tool_group: str = Field(default="", description="工具分组")
    tags: list[str] = Field(default_factory=list, description="检索标签")


class ToolSearchResult(BaseModel):
    """tool_search 工具的完整返回结果

    包含查询、匹配结果、降级信息。
    LLM 调用 tool_search 后接收此结构化响应。
    """

    query: str = Field(description="原始自然语言查询")
    results: list[ToolSearchHit] = Field(
        default_factory=list,
        description="匹配结果列表（按 score 降序）",
    )
    total_deferred: int = Field(
        default=0,
        description="Deferred Tools 总数（用于 LLM 判断结果覆盖度）",
    )
    is_fallback: bool = Field(
        default=False,
        description="是否为降级模式（ToolIndex 不可用时全量返回名称列表）",
    )
    backend: str = Field(
        default="",
        description="使用的检索后端（in_memory/lancedb）",
    )
    latency_ms: int = Field(
        default=0,
        description="检索延迟（毫秒）",
    )


# ============================================================
# 工具提升/回退追踪
# ============================================================


class ToolPromotionRecord(BaseModel):
    """工具层级变更记录 — 追踪 Deferred→Active 或 Active→Deferred

    用于:
    1. 可观测性（FR-036: Skill 加载导致的工具提升事件）
    2. 引用计数管理（FR-032: Skill 卸载时回退无其他引用的工具）
    """

    tool_name: str = Field(description="工具名称")
    direction: str = Field(description="变更方向: promoted 或 demoted")
    source: str = Field(description="来源: tool_search 或 skill")
    source_id: str = Field(
        default="",
        description="来源 ID（Skill 名称或 tool_search query ID）",
    )
    agent_runtime_id: str = Field(default="", description="Agent 实例 ID")
    agent_session_id: str = Field(default="", description="会话 ID")


class ToolPromotionState(BaseModel):
    """当前 session 的工具提升状态

    维护 tool_name → sources 的引用计数，
    确保 Skill 卸载时正确判断工具是否应回退。
    """

    promoted_tools: dict[str, list[str]] = Field(
        default_factory=dict,
        description="tool_name → 提升来源列表（如 ['skill:coding-agent', 'tool_search:q1']）",
    )

    def promote(self, tool_name: str, source: str) -> bool:
        """提升工具，返回 True 如果是新增提升（之前不在 active 集合中）"""
        sources = self.promoted_tools.setdefault(tool_name, [])
        if source not in sources:
            sources.append(source)
        return len(sources) == 1  # 第一个来源 = 新增提升

    def demote(self, tool_name: str, source: str) -> bool:
        """移除提升来源，返回 True 如果工具应回退到 Deferred"""
        sources = self.promoted_tools.get(tool_name, [])
        if source in sources:
            sources.remove(source)
        if not sources:
            self.promoted_tools.pop(tool_name, None)
            return True  # 无其他来源，应回退
        return False

    def is_promoted(self, tool_name: str) -> bool:
        """判断工具是否处于 Active 状态"""
        return tool_name in self.promoted_tools

    @property
    def active_tool_names(self) -> list[str]:
        """当前所有 Active 状态的工具名称"""
        return list(self.promoted_tools.keys())


# ============================================================
# Deferred Tools system prompt 模板
# ============================================================

DEFERRED_TOOLS_PROMPT_TEMPLATE = """## Available Tools (Deferred)

以下工具可通过 tool_search 搜索后使用。如需使用，请先调用 tool_search 查询。

{deferred_tools_list}

共 {total_count} 个 deferred 工具可用。"""


def format_deferred_tools_list(entries: list[DeferredToolEntry]) -> str:
    """格式化 Deferred Tools 列表为 system prompt 注入文本"""
    if not entries:
        return ""
    lines = [f"- {entry.name}: {entry.one_line_desc}" for entry in entries]
    return DEFERRED_TOOLS_PROMPT_TEMPLATE.format(
        deferred_tools_list="\n".join(lines),
        total_count=len(entries),
    )
