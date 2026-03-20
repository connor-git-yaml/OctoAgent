"""AliasRegistry -- 运行时 alias 注册表

对齐 data-model.md SS2.3/SS2.4 + contracts/provider-api.md SS3。
管理“配置 alias + legacy 语义 alias”的统一解析。
"""

from collections.abc import Iterable

import structlog
from pydantic import BaseModel, Field

log = structlog.get_logger()

# MVP 默认 alias 配置（对齐 data-model.md SS2.4）
DEFAULT_ALIASES: list["AliasConfig"] = []  # 延迟初始化，避免前向引用

DEFAULT_RUNTIME_ALIASES = {"cheap", "main", "fallback"}


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
            name="compaction",
            category="cheap",
            runtime_group="cheap",
            description="上下文压缩（推荐轻量模型如 haiku / gpt-4o-mini）",
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
    """Alias 注册表。

    设计原则：
    - `octoagent.yaml.model_aliases` 是运行时 alias 的主事实源
    - legacy 语义 alias（如 planner / summarizer）仅做兼容映射
    - 如果显式配置 alias 与 legacy 名称冲突，永远优先使用显式配置
    """

    def __init__(
        self,
        aliases: list[AliasConfig] | None = None,
        *,
        runtime_aliases: Iterable[str] | None = None,
        default_runtime_alias: str = "main",
    ) -> None:
        """初始化注册表

        Args:
            aliases: legacy 语义 alias 配置列表，None 时使用 MVP 默认配置
            runtime_aliases: 当前运行时允许直接透传的 alias 集合；通常来自
                `octoagent.yaml.model_aliases.keys()`
            default_runtime_alias: 未命中时的安全默认 alias
        """
        alias_list = aliases if aliases is not None else _get_default_aliases()
        # 按 name 建立索引，去重
        self._aliases: dict[str, AliasConfig] = {}
        for alias in alias_list:
            self._aliases[alias.name] = alias
        normalized_runtime_aliases = {
            str(alias).strip()
            for alias in (runtime_aliases or DEFAULT_RUNTIME_ALIASES)
            if str(alias).strip()
        }
        self._runtime_aliases = normalized_runtime_aliases or set(DEFAULT_RUNTIME_ALIASES)
        self._default_runtime_alias = default_runtime_alias.strip() or "main"

    @classmethod
    def from_runtime_aliases(
        cls,
        runtime_aliases: Iterable[str],
        *,
        aliases: list[AliasConfig] | None = None,
        default_runtime_alias: str = "main",
    ) -> "AliasRegistry":
        """从配置驱动的 runtime alias 集合构造注册表。"""
        normalized = {
            str(alias).strip()
            for alias in runtime_aliases
            if str(alias).strip()
        }
        if default_runtime_alias.strip():
            normalized.add(default_runtime_alias.strip())
        return cls(
            aliases=aliases,
            runtime_aliases=normalized,
            default_runtime_alias=default_runtime_alias,
        )

    def resolve(self, alias: str) -> str:
        """将 alias 解析为最终运行时 alias（Proxy model_name）

        解析顺序：
            1. 如果 alias 已在 runtime alias 集合中，直接透传
            2. 如果 alias 是 legacy 语义 alias，则解析到其 runtime_group，
               但只有目标 alias 确实可用时才采用
            3. 否则回退到安全默认值（通常是 `main`）

        这样可保证：
            - `octoagent.yaml` 中显式配置的 alias 永远优先
            - legacy planner/summarizer 仍可兼容 main/cheap 语义
            - 未知 alias 不会再意外覆盖显式配置
        """
        normalized_alias = alias.strip()
        if not normalized_alias:
            return self._default_runtime_alias

        if normalized_alias in self._runtime_aliases:
            return normalized_alias

        legacy_alias = self._aliases.get(normalized_alias)
        if legacy_alias is not None:
            runtime_group = legacy_alias.runtime_group.strip()
            if runtime_group in self._runtime_aliases:
                return runtime_group

        log.warning(
            "unknown_alias_fallback_to_default",
            alias=normalized_alias,
            fallback_alias=self._default_runtime_alias,
            runtime_aliases=sorted(self._runtime_aliases),
        )
        return self._default_runtime_alias

    def has_runtime_alias(self, alias: str) -> bool:
        """判断 alias 是否存在于当前运行时 alias 集。"""
        return alias.strip() in self._runtime_aliases

    def list_runtime_aliases(self) -> list[str]:
        """列出当前运行时可直接消费的 alias。"""
        return sorted(self._runtime_aliases)

    def get_alias(self, alias: str) -> AliasConfig | None:
        """按名称查询单个 legacy 语义 alias 配置"""
        return self._aliases.get(alias)

    def get_aliases_by_category(self, category: str) -> list[AliasConfig]:
        """按 category 查询 legacy 语义 alias 列表"""
        return [a for a in self._aliases.values() if a.category == category]

    def get_aliases_by_runtime_group(self, group: str) -> list[AliasConfig]:
        """按运行时 group 查询 legacy 语义 alias 列表"""
        return [a for a in self._aliases.values() if a.runtime_group == group]

    def list_all(self) -> list[AliasConfig]:
        """列出所有 legacy 语义 alias（按 name 排序）"""
        return sorted(self._aliases.values(), key=lambda a: a.name)
