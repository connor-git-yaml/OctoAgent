"""AliasRegistry -- 语义 alias 注册表

对齐 data-model.md SS2.3/SS2.4 + contracts/provider-api.md SS3。
管理语义 alias -> category -> runtime_group 双层映射。
"""

import structlog
from pydantic import BaseModel, Field

log = structlog.get_logger()

# MVP 默认 alias 配置（对齐 data-model.md SS2.4）
DEFAULT_ALIASES: list["AliasConfig"] = []  # 延迟初始化，避免前向引用

# 已知运行时 group 名称
KNOWN_RUNTIME_GROUPS = {"cheap", "main", "fallback"}


class AliasConfig(BaseModel):
    """单个语义 alias 的配置

    双层映射：
    - category: 成本归因维度（cheap/main/fallback）
    - runtime_group: Proxy model_name 维度（cheap/main/fallback）

    MVP 阶段 category 与 runtime_group 一一对齐。
    """

    name: str = Field(description="语义 alias 名称（如 router, planner）")
    description: str = Field(default="", description="alias 用途描述")
    category: str = Field(
        default="main",
        description="成本归因分类：cheap / main / fallback",
    )
    runtime_group: str = Field(
        default="main",
        description="运行时 group（对应 Proxy model_name）：cheap / main / fallback",
    )


def _get_default_aliases() -> list[AliasConfig]:
    """获取 MVP 默认 alias 配置"""
    return [
        AliasConfig(
            name="router",
            category="cheap",
            runtime_group="cheap",
            description="路由决策（轻量）",
        ),
        AliasConfig(
            name="extractor",
            category="cheap",
            runtime_group="cheap",
            description="信息提取（轻量）",
        ),
        AliasConfig(
            name="summarizer",
            category="cheap",
            runtime_group="cheap",
            description="摘要生成（轻量）",
        ),
        AliasConfig(
            name="planner",
            category="main",
            runtime_group="main",
            description="规划推理（主力）",
        ),
        AliasConfig(
            name="executor",
            category="main",
            runtime_group="main",
            description="执行生成（主力）",
        ),
        AliasConfig(
            name="fallback",
            category="fallback",
            runtime_group="fallback",
            description="降级备选",
        ),
    ]


class AliasRegistry:
    """Alias 注册表 -- 管理语义 alias -> category -> runtime_group 映射

    MVP：启动时从配置加载，运行期间不变。
    查询接口供 LiteLLMClient 和后续 Feature 使用。
    """

    def __init__(self, aliases: list[AliasConfig] | None = None) -> None:
        """初始化注册表

        Args:
            aliases: alias 配置列表，None 时使用 MVP 默认配置
        """
        alias_list = aliases if aliases is not None else _get_default_aliases()
        # 按 name 建立索引，去重
        self._aliases: dict[str, AliasConfig] = {}
        for alias in alias_list:
            self._aliases[alias.name] = alias

    def resolve(self, alias: str) -> str:
        """将语义 alias 解析为运行时 group（Proxy model_name）

        映射链: 语义 alias -> AliasConfig -> runtime_group

        行为规则:
            1. 如果 alias 在注册表中 -> 返回对应 runtime_group
            2. 如果 alias 不在注册表中但是已知运行时 group（cheap/main/fallback）
               -> 直接返回 alias（透传）
            3. 如果都不匹配 -> 返回 "main"（安全默认值）
               并记录 warning 日志
        """
        # 规则 1: 注册表内的语义 alias
        if alias in self._aliases:
            return self._aliases[alias].runtime_group

        # 规则 2: 已知运行时 group 直接透传
        if alias in KNOWN_RUNTIME_GROUPS:
            return alias

        # 规则 3: 未知 alias 降级到 main
        log.warning("unknown_alias_fallback_to_main", alias=alias)
        return "main"

    def get_alias(self, alias: str) -> AliasConfig | None:
        """按名称查询单个 alias 配置"""
        return self._aliases.get(alias)

    def get_aliases_by_category(self, category: str) -> list[AliasConfig]:
        """按 category 查询 alias 列表"""
        return [a for a in self._aliases.values() if a.category == category]

    def get_aliases_by_runtime_group(self, group: str) -> list[AliasConfig]:
        """按运行时 group 查询语义 alias 列表"""
        return [a for a in self._aliases.values() if a.runtime_group == group]

    def list_all(self) -> list[AliasConfig]:
        """列出所有已注册的 alias（按 name 排序）"""
        return sorted(self._aliases.values(), key=lambda a: a.name)
